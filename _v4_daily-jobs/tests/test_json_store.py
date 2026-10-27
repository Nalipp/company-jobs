from __future__ import annotations

from multiprocessing import get_context
from pathlib import Path
import time

import pytest

from v4_daily_jobs.json_store import (
    FileLock,
    InvalidJsonDocument,
    JsonStore,
    LockTimeout,
    RevisionConflict,
    canonical_json_bytes,
)


def _increment_many(path_string: str, count: int) -> None:
    store = JsonStore(path_string, lock_timeout=10)
    for _ in range(count):
        store.mutate(lambda document: document.__setitem__("counter", document["counter"] + 1))


def _hold_lock(lock_path_string: str, ready: object, hold_seconds: float) -> None:
    with FileLock(lock_path_string, timeout=2):
        ready.set()
        time.sleep(hold_seconds)


def test_canonical_json_is_readable_stable_and_has_trailing_newline() -> None:
    encoded = canonical_json_bytes({"z": "café", "a": {"b": 1}})
    assert encoded == b'{\n  "a": {\n    "b": 1\n  },\n  "z": "caf\xc3\xa9"\n}\n'


def test_semantic_noop_does_not_touch_file_bytes_or_stat(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    store = JsonStore(path)
    store.initialize({"revision": 0, "value": 1})
    before = (path.read_bytes(), path.stat().st_ino, path.stat().st_mtime_ns)

    result = store.mutate(lambda document: None)

    after = (path.read_bytes(), path.stat().st_ino, path.stat().st_mtime_ns)
    assert not result.changed
    assert after == before


def test_validator_failure_never_changes_existing_document(tmp_path: Path) -> None:
    path = tmp_path / "state.json"

    def validator(document: dict[str, object]) -> None:
        if not isinstance(document.get("value"), int):
            raise ValueError("value must be an integer")

    store = JsonStore(path, validator=validator)
    store.initialize({"value": 1})
    before = path.read_bytes()

    with pytest.raises(ValueError, match="integer"):
        store.mutate(lambda document: document.__setitem__("value", "bad"))

    assert path.read_bytes() == before
    assert list(tmp_path.glob("*.tmp")) == []


def test_expected_revision_is_checked_under_the_lock(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    store = JsonStore(path)
    store.initialize({"revision": 2, "value": 1})

    with pytest.raises(RevisionConflict, match="expected revision 1"):
        with store.locked() as locked:
            locked.save({"revision": 3, "value": 2}, expected_revision=1)

    assert store.read() == {"revision": 2, "value": 1}


@pytest.mark.parametrize(
    "payload, message",
    [
        (b'{"value": 1, "value": 2}\n', "duplicate JSON object key"),
        (b'{"value": NaN}\n', "non-finite JSON number"),
    ],
)
def test_ambiguous_or_nonstandard_json_is_rejected(
    tmp_path: Path,
    payload: bytes,
    message: str,
) -> None:
    path = tmp_path / "bad.json"
    path.write_bytes(payload)
    with pytest.raises(InvalidJsonDocument, match=message):
        JsonStore(path).read()


def test_separate_lock_file_survives_atomic_json_inode_replacement(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    store = JsonStore(path)
    store.initialize({"value": 1})
    old_json_inode = path.stat().st_ino
    lock_path = store.lock_path

    store.mutate(lambda document: document.__setitem__("value", 2))

    assert path.stat().st_ino != old_json_inode
    assert lock_path.exists()
    assert lock_path != path


def test_lock_timeout_is_bounded_and_names_lock(tmp_path: Path) -> None:
    lock_path = tmp_path / "held.lock"
    context = get_context("spawn")
    ready = context.Event()
    process = context.Process(target=_hold_lock, args=(str(lock_path), ready, 0.4))
    process.start()
    assert ready.wait(3)
    started = time.monotonic()
    try:
        with pytest.raises(LockTimeout, match=str(lock_path)):
            with FileLock(lock_path, timeout=0.05, poll_interval=0.005):
                pass
    finally:
        process.join(3)
        if process.is_alive():
            process.terminate()
    assert time.monotonic() - started < 0.5


def test_multiprocess_mutations_do_not_lose_updates(tmp_path: Path) -> None:
    path = tmp_path / "counter.json"
    JsonStore(path).initialize({"counter": 0})
    context = get_context("spawn")
    processes = [
        context.Process(target=_increment_many, args=(str(path), 20)) for _ in range(6)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(15)
        assert process.exitcode == 0

    assert JsonStore(path).read()["counter"] == 120
