"""Crash-safe JSON file storage with separate advisory lock files.

The queue and canonical databases are deliberately ordinary JSON files.  This
module is the only low-level write path they need: callers take a lock on a
stable ``.lock`` sibling, reload after acquiring it, validate both the loaded
and proposed documents, and replace the data file atomically.

``fcntl.flock`` is process scoped and available on the macOS/Linux platforms
supported by this repository.  It is intentionally not hidden behind a
database-like abstraction; the small API makes lock ordering visible to the
finalizer (queue first, canonical database second).
"""

from __future__ import annotations

import copy
import errno
import fcntl
import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
import tempfile
import time
from types import TracebackType
from typing import Any, Callable, Generic, TypeVar


JsonObject = dict[str, Any]
Validator = Callable[[JsonObject], None]
T = TypeVar("T")


def _object_without_duplicate_keys(pairs: list[tuple[str, Any]]) -> JsonObject:
    document: JsonObject = {}
    for key, value in pairs:
        if key in document:
            raise ValueError(f"duplicate JSON object key {key!r}")
        document[key] = value
    return document


def _reject_nonfinite_number(value: str) -> None:
    raise ValueError(f"non-finite JSON number {value!r} is not allowed")


class JsonStoreError(RuntimeError):
    """Base class for durable JSON-store failures."""


class LockTimeout(JsonStoreError):
    """Raised when a lock cannot be acquired within the configured timeout."""

    def __init__(self, lock_path: Path, timeout: float) -> None:
        super().__init__(f"timed out after {timeout:.3f}s waiting for lock {lock_path}")
        self.lock_path = lock_path
        self.timeout = timeout


class InvalidJsonDocument(JsonStoreError):
    """Raised when the on-disk JSON is missing, malformed, or not an object."""


class RevisionConflict(JsonStoreError):
    """Raised when optimistic revision preconditions do not match."""


@dataclass(frozen=True)
class SaveResult:
    """Result of a save attempt."""

    changed: bool
    document: JsonObject
    digest: str


@dataclass(frozen=True)
class MutationResult(Generic[T]):
    """Result of a locked read/modify/write transaction."""

    changed: bool
    document: JsonObject
    value: T
    digest: str


def canonical_json_bytes(document: JsonObject) -> bytes:
    """Return the repository's deterministic, human-readable JSON encoding."""

    return (
        json.dumps(
            document,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def default_lock_path(path: Path) -> Path:
    """Return a stable sibling lock path, e.g. ``queue.json`` -> ``queue.lock``."""

    return path.with_suffix(".lock")


class FileLock:
    """Bounded advisory lock implemented with ``flock``.

    Lock files are never deleted.  Their inode is the synchronization point,
    unlike the JSON file which is replaced on every durable write.
    """

    def __init__(
        self,
        path: str | os.PathLike[str],
        *,
        exclusive: bool = True,
        timeout: float = 10.0,
        poll_interval: float = 0.01,
    ) -> None:
        if timeout < 0:
            raise ValueError("lock timeout must be non-negative")
        if poll_interval <= 0:
            raise ValueError("lock poll interval must be positive")
        self.path = Path(path)
        self.exclusive = exclusive
        self.timeout = timeout
        self.poll_interval = poll_interval
        self._file: Any | None = None

    def __enter__(self) -> "FileLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # a+b avoids text buffering surprises and creates a stable inode.
        self._file = self.path.open("a+b")
        operation = (fcntl.LOCK_EX if self.exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                fcntl.flock(self._file.fileno(), operation)
                return self
            except OSError as exc:
                if exc.errno == errno.EINTR:
                    continue
                if exc.errno not in (errno.EACCES, errno.EAGAIN):
                    self._file.close()
                    self._file = None
                    raise
                if time.monotonic() >= deadline:
                    self._file.close()
                    self._file = None
                    raise LockTimeout(self.path, self.timeout) from exc
                time.sleep(min(self.poll_interval, max(0.0, deadline - time.monotonic())))

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._file is not None:
            try:
                fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
            finally:
                self._file.close()
                self._file = None


class LockedJsonStore:
    """Operations available while the caller owns a store lock."""

    def __init__(self, store: "JsonStore", *, exclusive: bool) -> None:
        self.store = store
        self.exclusive = exclusive

    @property
    def path(self) -> Path:
        return self.store.path

    def exists(self) -> bool:
        return self.path.exists()

    def read(self) -> JsonObject:
        return self.store._read_unlocked()

    # ``reload`` communicates intent at cross-file transaction call sites.
    reload = read

    def read_bytes(self) -> bytes:
        try:
            return self.path.read_bytes()
        except FileNotFoundError as exc:
            raise InvalidJsonDocument(f"JSON document does not exist: {self.path}") from exc

    def digest(self) -> str:
        return sha256_bytes(self.read_bytes())

    def save(
        self,
        document: JsonObject,
        *,
        expected_revision: int | None = None,
    ) -> SaveResult:
        if not self.exclusive:
            raise JsonStoreError(f"cannot write while holding a shared lock: {self.path}")
        return self.store._save_unlocked(document, expected_revision=expected_revision)

    def initialize(self, document: JsonObject) -> SaveResult:
        """Create a missing document without overwriting an existing one."""

        if not self.exclusive:
            raise JsonStoreError(f"cannot initialize while holding a shared lock: {self.path}")
        if self.path.exists():
            current = self.read()
            return SaveResult(False, current, self.digest())
        self.store._validate(document)
        data = canonical_json_bytes(document)
        self.store._atomic_replace(data)
        return SaveResult(True, copy.deepcopy(document), sha256_bytes(data))


class _LockedContext:
    def __init__(self, store: "JsonStore", *, exclusive: bool, timeout: float | None) -> None:
        self.store = store
        self.exclusive = exclusive
        self.timeout = store.lock_timeout if timeout is None else timeout
        self._lock: FileLock | None = None

    def __enter__(self) -> LockedJsonStore:
        self._lock = FileLock(
            self.store.lock_path,
            exclusive=self.exclusive,
            timeout=self.timeout,
            poll_interval=self.store.poll_interval,
        )
        self._lock.__enter__()
        return LockedJsonStore(self.store, exclusive=self.exclusive)

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        assert self._lock is not None
        self._lock.__exit__(exc_type, exc, traceback)


class JsonStore:
    """A validated JSON object protected by a separate advisory lock."""

    def __init__(
        self,
        path: str | os.PathLike[str],
        *,
        lock_path: str | os.PathLike[str] | None = None,
        validator: Validator | None = None,
        lock_timeout: float = 10.0,
        poll_interval: float = 0.01,
    ) -> None:
        self.path = Path(path)
        self.lock_path = Path(lock_path) if lock_path is not None else default_lock_path(self.path)
        self.validator = validator
        self.lock_timeout = lock_timeout
        self.poll_interval = poll_interval

    def locked(
        self,
        *,
        exclusive: bool = True,
        timeout: float | None = None,
    ) -> _LockedContext:
        """Return a context that keeps the lock held across reload/save calls.

        The finalizer uses this API to retain the queue lock while acquiring the
        canonical lock, enforcing the repository-wide queue -> canonical order.
        """

        return _LockedContext(self, exclusive=exclusive, timeout=timeout)

    def read(self) -> JsonObject:
        with self.locked(exclusive=False) as locked:
            return locked.read()

    def digest(self) -> str:
        with self.locked(exclusive=False) as locked:
            return locked.digest()

    def initialize(self, document: JsonObject) -> SaveResult:
        with self.locked(exclusive=True) as locked:
            return locked.initialize(document)

    def mutate(
        self,
        mutator: Callable[[JsonObject], tuple[JsonObject, T] | T],
        *,
        expected_revision: int | None = None,
    ) -> MutationResult[T]:
        """Run a read/modify/write operation under one exclusive lock.

        ``mutator`` may mutate its argument in place and return an operation
        value, or return ``(replacement_document, operation_value)``.  A
        semantic no-op does not replace the file.
        """

        with self.locked(exclusive=True) as locked:
            original = locked.read()
            working = copy.deepcopy(original)
            outcome = mutator(working)
            if (
                isinstance(outcome, tuple)
                and len(outcome) == 2
                and isinstance(outcome[0], dict)
            ):
                proposed, value = outcome
            else:
                proposed, value = working, outcome  # type: ignore[assignment]
            result = locked.save(proposed, expected_revision=expected_revision)
            return MutationResult(result.changed, result.document, value, result.digest)

    def _validate(self, document: JsonObject) -> None:
        if not isinstance(document, dict):
            raise InvalidJsonDocument(f"top-level JSON value must be an object: {self.path}")
        if self.validator is not None:
            self.validator(document)

    def _read_unlocked(self) -> JsonObject:
        try:
            raw = self.path.read_bytes()
        except FileNotFoundError as exc:
            raise InvalidJsonDocument(f"JSON document does not exist: {self.path}") from exc
        try:
            document = json.loads(
                raw,
                object_pairs_hook=_object_without_duplicate_keys,
                parse_constant=_reject_nonfinite_number,
            )
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise InvalidJsonDocument(f"invalid UTF-8 JSON in {self.path}: {exc}") from exc
        if not isinstance(document, dict):
            raise InvalidJsonDocument(f"top-level JSON value must be an object: {self.path}")
        self._validate(document)
        return document

    def _save_unlocked(
        self,
        document: JsonObject,
        *,
        expected_revision: int | None,
    ) -> SaveResult:
        self._validate(document)
        current = self._read_unlocked()
        if expected_revision is not None and current.get("revision") != expected_revision:
            raise RevisionConflict(
                f"expected revision {expected_revision!r} for {self.path}, "
                f"found {current.get('revision')!r}"
            )
        if current == document:
            # Preserve exact bytes, mtime, and inode for a true semantic no-op.
            return SaveResult(False, copy.deepcopy(current), sha256_bytes(self.path.read_bytes()))
        data = canonical_json_bytes(document)
        self._atomic_replace(data)
        return SaveResult(True, copy.deepcopy(document), sha256_bytes(data))

    def _atomic_replace(self, data: bytes) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        old_mode: int | None = None
        try:
            old_mode = self.path.stat().st_mode & 0o777
        except FileNotFoundError:
            pass

        fd, temporary_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.",
            suffix=".tmp",
            dir=self.path.parent,
        )
        temporary_path = Path(temporary_name)
        descriptor_open = True
        try:
            if old_mode is not None:
                os.fchmod(fd, old_mode)
            else:
                os.fchmod(fd, 0o644)
            with os.fdopen(fd, "wb", closefd=True) as handle:
                descriptor_open = False
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, self.path)
            self._fsync_directory(self.path.parent)
        except BaseException:
            if descriptor_open:
                try:
                    os.close(fd)
                except OSError:
                    pass
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass
            raise

    @staticmethod
    def _fsync_directory(directory: Path) -> None:
        flags = os.O_RDONLY
        if hasattr(os, "O_DIRECTORY"):
            flags |= os.O_DIRECTORY
        try:
            directory_fd = os.open(directory, flags)
        except OSError:
            # Some filesystems/platforms do not permit opening directories.
            return
        try:
            try:
                os.fsync(directory_fd)
            except OSError as exc:
                if exc.errno not in (errno.EINVAL, errno.ENOTSUP):
                    raise
        finally:
            os.close(directory_fd)
