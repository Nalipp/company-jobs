"""Shared contracts and conservative normalization for structured job adapters.

Adapters deliberately do not decide whether a job matches a search profile.  They
only preserve provider facts that can be normalized without interpretation.  The
profile evaluator lives in :mod:`v4_daily_jobs.jobs`.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from hashlib import sha256
from html import unescape
from html.parser import HTMLParser
import json
import math
import re
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit


class AdapterError(ValueError):
    """Raised when a provider payload cannot be interpreted safely."""


TRACKING_QUERY_KEYS = {
    "fbclid",
    "gclid",
    "gh_src",
    "lever-origin",
    "lever-source",
    "source",
}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        if data.strip():
            self.parts.append(data)


def html_to_text(value: Any) -> str:
    """Return stable plain text from provider HTML or plain text."""

    if value is None:
        return ""
    text = str(value)
    # Greenhouse documents that editor HTML is returned as HTML entities.  Some
    # boards are encoded twice, so decode a small bounded number of times before
    # deciding whether HTML parsing is needed.
    for _ in range(3):
        decoded = unescape(text)
        if decoded == text:
            break
        text = decoded
    if "<" not in text and ">" not in text:
        return re.sub(r"\s+", " ", text).strip()
    parser = _TextExtractor()
    try:
        parser.feed(text)
        parser.close()
    except Exception as exc:  # HTMLParser errors are rare, but unsafe to ignore.
        raise AdapterError(f"invalid HTML in provider payload: {exc}") from exc
    return re.sub(r"\s+", " ", " ".join(parser.parts)).strip()


def require_text(value: Any, field: str) -> str:
    if not isinstance(value, (str, int)) or not str(value).strip():
        raise AdapterError(f"provider job is missing required {field}")
    return str(value).strip()


def optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = re.sub(r"\s+", " ", str(value)).strip()
    return text or None


def normalize_timestamp(value: Any) -> str | None:
    """Normalize provider ISO timestamps or Unix seconds/milliseconds to UTC."""

    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        seconds = float(value)
        if seconds > 100_000_000_000:  # provider millisecond timestamps
            seconds /= 1000
        try:
            parsed = datetime.fromtimestamp(seconds, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
        return parsed.isoformat(timespec="seconds").replace("+00:00", "Z")
    text = optional_text(value)
    if text is None:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text
    if parsed.tzinfo is None:
        # Do not invent a timezone for a provider's naive timestamp.
        return text
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def normalize_url(value: Any) -> str:
    """Normalize an HTTP(S) job URL while removing known tracking parameters."""

    raw = require_text(value, "canonical URL")
    parsed = urlsplit(raw)
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.netloc:
        raise AdapterError(f"job URL must be absolute HTTP(S): {raw!r}")
    query = sorted(
        [
            (key, item)
            for key, item in parse_qsl(parsed.query, keep_blank_values=True)
            if not key.casefold().startswith("utm_")
            and key.casefold() not in TRACKING_QUERY_KEYS
        ]
    )
    path = re.sub(r"/{2,}", "/", parsed.path).rstrip("/") or "/"
    return urlunsplit(
        (
            parsed.scheme.casefold(),
            parsed.netloc.casefold(),
            path,
            urlencode(query, doseq=True),
            "",
        )
    )


def stable_job_id(source_id: str, source_job_id: str) -> str:
    """Build a deterministic, provider-ID-based V4 job identifier.

    The short digest keeps IDs schema-friendly even when providers use spaces,
    slashes, or very long requisition identifiers.  Identity always includes the
    career source so the same provider ID on another board cannot collide.
    """

    source = require_text(source_id, "source_id")
    provider_id = require_text(source_job_id, "source job ID")
    digest = sha256(f"{source}\0{provider_id}".encode("utf-8")).hexdigest()[:24]
    return f"job-{digest}"


def normalized_locations(values: Iterable[Any]) -> tuple[str, ...]:
    seen: set[str] = set()
    locations: list[str] = []
    for value in values:
        text = optional_text(value)
        if text is None:
            continue
        key = text.casefold()
        if key not in seen:
            seen.add(key)
            locations.append(text)
    return tuple(locations)


_WORKPLACE_MAP = {
    "remote": "remote",
    "remotefriendly": "remote",
    "hybrid": "hybrid",
    "onsite": "on-site",
    "on-site": "on-site",
    "on site": "on-site",
    "office": "on-site",
    "inoffice": "on-site",
    "in-office": "on-site",
}


def normalize_workplace(value: Any) -> str:
    """Normalize an explicit workplace value, otherwise preserve unknown.

    A provider's ``isRemote: false`` is intentionally not interpreted as
    on-site; absence of remote eligibility is not positive workplace evidence.
    """

    text = optional_text(value)
    if text is None:
        return "unknown"
    key = re.sub(r"[_\s]+", "", text.casefold())
    for candidate, normalized in _WORKPLACE_MAP.items():
        if re.sub(r"[_\s]+", "", candidate.casefold()) == key:
            return normalized
    return "unknown"


_EMPLOYMENT_MAP = {
    "fulltime": "full-time",
    "full-time": "full-time",
    "full time": "full-time",
    "parttime": "part-time",
    "part-time": "part-time",
    "part time": "part-time",
    "contract": "contract",
    "contractor": "contract",
    "temporary": "temporary",
    "temp": "temporary",
    "intern": "internship",
    "internship": "internship",
}


def normalize_employment(value: Any) -> str | None:
    text = optional_text(value)
    if text is None:
        return None
    key = re.sub(r"[_\s]+", "", text.casefold())
    for candidate, normalized in _EMPLOYMENT_MAP.items():
        if re.sub(r"[_\s]+", "", candidate.casefold()) == key:
            return normalized
    # Preserve an explicit but unknown value for conservative profile handling.
    return text.casefold()


def _number(value: Any) -> int | float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number < 0:
        return None
    return int(number) if number.is_integer() else number


def normalize_structured_compensation(
    value: Any,
    *,
    source_text: str | None = None,
) -> dict[str, Any] | None:
    """Normalize only compensation with explicit amount, currency and period.

    This accepts the common Ashby/Lever key variants but never derives a period
    from a bare salary string.  Ambiguous text remains available on the job as
    ``compensation_text`` for an agent to inspect.
    """

    if not isinstance(value, Mapping):
        return None
    minimum = _number(
        value.get("minimum", value.get("min", value.get("minValue")))
    )
    maximum = _number(
        value.get("maximum", value.get("max", value.get("maxValue")))
    )
    if maximum is None:
        return None
    currency_value = value.get(
        "currency", value.get("currencyCode", value.get("currency_type"))
    )
    currency = optional_text(currency_value)
    if currency is None:
        return None
    currency = currency.upper()
    if not re.fullmatch(r"[A-Z]{3}", currency):
        return None

    period_value = value.get("period", value.get("interval"))
    period_text = optional_text(period_value)
    if period_text is None:
        return None
    period_key = period_text.casefold().replace("_", " ").strip()
    if "year" in period_key or period_key in {"annual", "annually", "yearly"}:
        period = "year"
    elif "month" in period_key or period_key == "monthly":
        period = "month"
    elif "hour" in period_key or period_key == "hourly":
        period = "hour"
    else:
        return None

    basis_value = optional_text(value.get("basis", value.get("compensationType")))
    if basis_value is None:
        # A provider salary range is base pay unless it explicitly says total.
        basis = "base"
    elif "total" in basis_value.casefold() or "ote" in basis_value.casefold():
        basis = "total"
    elif "salary" in basis_value.casefold() or "base" in basis_value.casefold():
        basis = "base"
    else:
        return None

    rendered = optional_text(source_text) or optional_text(
        value.get("source_text", value.get("salaryRange"))
    )
    if rendered is None:
        rendered = f"{currency} {minimum if minimum is not None else ''}-{maximum} / {period}"
    return {
        "minimum": minimum,
        "maximum": maximum,
        "currency": currency,
        "basis": basis,
        "period": period,
        "source_text": rendered,
    }


@dataclass(frozen=True)
class NormalizedJob:
    stable_id: str
    company_id: str
    source_id: str
    provider: str
    source_job_id: str
    canonical_url: str
    title: str
    description_text: str
    locations: tuple[str, ...]
    workplace_arrangement: str
    workplace_text: str | None
    employment_type: str | None
    employment_text: str | None
    compensation: dict[str, Any] | None
    compensation_text: str | None
    department: str | None
    team: str | None
    posted_at: str | None
    updated_at: str | None

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["locations"] = list(self.locations)
        return value


@dataclass(frozen=True)
class FetchMetadata:
    provider: str
    complete: bool
    completeness_reason: str
    pages_received: int
    pages_expected: int | None
    total_reported: int | None
    raw_jobs_seen: int
    unique_jobs: int
    duplicates_removed: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AdapterResult:
    provider: str
    company_id: str
    source_id: str
    jobs: tuple[NormalizedJob, ...]
    fetch: FetchMetadata

    @property
    def complete(self) -> bool:
        return self.fetch.complete

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "company_id": self.company_id,
            "source_id": self.source_id,
            "jobs": [job.to_dict() for job in self.jobs],
            "fetch": self.fetch.to_dict(),
        }


def _job_fingerprint(job: NormalizedJob) -> str:
    return json.dumps(job.to_dict(), sort_keys=True, separators=(",", ":"))


def deduplicate_jobs(
    jobs: Sequence[NormalizedJob],
) -> tuple[tuple[NormalizedJob, ...], int]:
    """Drop identical overlap duplicates and reject conflicting identities."""

    by_id: dict[str, NormalizedJob] = {}
    by_url: dict[str, str] = {}
    duplicate_count = 0
    for job in jobs:
        prior = by_id.get(job.stable_id)
        if prior is not None:
            if _job_fingerprint(prior) != _job_fingerprint(job):
                raise AdapterError(
                    f"conflicting duplicate provider job ID {job.source_job_id!r}"
                )
            duplicate_count += 1
            continue
        prior_stable_id = by_url.get(job.canonical_url)
        if prior_stable_id is not None and prior_stable_id != job.stable_id:
            raise AdapterError(
                "different provider job IDs share canonical URL "
                f"{job.canonical_url!r}"
            )
        by_id[job.stable_id] = job
        by_url[job.canonical_url] = job.stable_id
    ordered = tuple(sorted(by_id.values(), key=lambda item: item.stable_id))
    return ordered, duplicate_count


def provider_url(host: str, board_id: str, job_id: str) -> str:
    return f"https://{host}/{quote(board_id, safe='')}/{quote(job_id, safe='')}"
