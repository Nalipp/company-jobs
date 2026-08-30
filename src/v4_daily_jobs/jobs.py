"""Deterministic job filtering and fail-safe snapshot reconciliation.

This module intentionally stops at the boundary where interpretation is needed.
It can reject jobs contradicted by explicit structured facts, but it preserves
unknown or ambiguous facts for the model-driven portion of ``$find-jobs``.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
import re
from typing import Any, Iterable, Mapping, Sequence

from .adapters.base import (
    AdapterError,
    AdapterResult,
    NormalizedJob,
    normalize_url,
    stable_job_id,
)


class JobInvariantError(ValueError):
    """Raised when a snapshot or normalized job violates source ownership."""


@dataclass(frozen=True)
class FilterDecision:
    stable_id: str
    status: str
    rejection_reasons: tuple[str, ...]
    semantic_requirements: tuple[str, ...]
    accepted_facts: tuple[str, ...]

    @property
    def rejected(self) -> bool:
        return self.status == "rejected"

    def to_dict(self) -> dict[str, Any]:
        return {
            "stable_id": self.stable_id,
            "status": self.status,
            "rejection_reasons": list(self.rejection_reasons),
            "semantic_requirements": list(self.semantic_requirements),
            "accepted_facts": list(self.accepted_facts),
        }


def _job_dict(job: NormalizedJob | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(job, NormalizedJob):
        return job.to_dict()
    if not isinstance(job, Mapping):
        raise JobInvariantError("job must be a normalized job object")
    return deepcopy(dict(job))


def _normalized_phrase(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).casefold()


def _location_kind(value: str, *, city: str, region: str, country: str) -> str:
    """Return ``match``, ``mismatch``, or ``unknown`` for one location label."""

    text = _normalized_phrase(value)
    if not text:
        return "unknown"
    city_text = _normalized_phrase(city)
    if city_text in text:
        if "bay area" in text or "metro" in text or "greater " in text:
            return "mismatch"
        # Reject lookalike names while accepting common exact city labels with a
        # region/country suffix and optional workplace annotation.
        prefix, suffix = text.split(city_text, 1)
        prefix = prefix.strip()
        if prefix:
            # South San Francisco is a separate city. Other unusual prefixed
            # labels are ambiguous rather than exact matches.
            if prefix.rstrip(" ,-/|").endswith("south"):
                return "mismatch"
            return "unknown"
        suffix = suffix.strip()
        suffix = re.sub(r"\((hybrid|on[- ]?site|in[- ]?office)\)", "", suffix).strip()
        allowed_suffixes = {
            "",
            ", ca",
            ", california",
            ", ca, us",
            ", ca, usa",
            ", ca, united states",
            ", california, us",
            ", california, usa",
            ", california, united states",
        }
        if suffix in allowed_suffixes:
            return "match"
        # A city followed by a separator can still be an exact option in a
        # multi-location string; unusual prose is left for semantic review.
        return "unknown"

    generic_tokens = {
        _normalized_phrase(region),
        "california",
        _normalized_phrase(country),
        "usa",
        "united states",
        "united states of america",
        "u.s.",
        "u.s.a.",
        "north america",
        "multiple locations",
        "remote",
        "anywhere",
    }
    if re.match(r"^(sf|san fran)(?:\b|,)", text):
        return "unknown"
    if text in generic_tokens or "bay area" in text:
        return "unknown" if "bay area" not in text else "mismatch"
    # A non-generic provider location such as "Oakland, CA" is sufficiently
    # explicit to reject under a city-only profile.
    return "mismatch"


def _location_evaluation(
    locations: Sequence[Any], location_policy: Mapping[str, Any]
) -> tuple[str, str]:
    kinds = [
        _location_kind(
            str(value),
            city=str(location_policy["city"]),
            region=str(location_policy["region"]),
            country=str(location_policy["country"]),
        )
        for value in locations
        if str(value).strip()
    ]
    if "match" in kinds:
        return "pass", "location_exact_city"
    if kinds and all(kind == "mismatch" for kind in kinds):
        return "reject", "location_outside_target_city"
    return "unknown", "location_requires_review"


def _compensation_evaluation(
    compensation: Any, policy: Mapping[str, Any]
) -> tuple[str, str]:
    if not isinstance(compensation, Mapping):
        return "unknown", "compensation_requires_review"
    for field in ("currency", "basis", "period"):
        if str(compensation.get(field, "")).casefold() != str(
            policy.get(field, "")
        ).casefold():
            return "reject", f"compensation_{field}_mismatch"
    maximum = compensation.get("maximum")
    minimum = compensation.get("minimum")
    if not isinstance(maximum, (int, float)) or isinstance(maximum, bool):
        return "unknown", "compensation_requires_review"
    threshold = policy["threshold"]
    comparison = policy["comparison"]
    if comparison == "published_maximum_at_least":
        passes = maximum >= threshold
    elif comparison == "published_minimum_at_least":
        passes = (
            isinstance(minimum, (int, float))
            and not isinstance(minimum, bool)
            and minimum >= threshold
        )
    elif comparison == "published_range_contains":
        passes = (
            isinstance(minimum, (int, float))
            and not isinstance(minimum, bool)
            and minimum <= threshold <= maximum
        )
    else:
        raise JobInvariantError(f"unsupported compensation comparison {comparison!r}")
    if not passes:
        return "reject", "compensation_below_profile_threshold"
    return "pass", "compensation_satisfies_profile"


def evaluate_profile(
    job: NormalizedJob | Mapping[str, Any], profile: Mapping[str, Any]
) -> FilterDecision:
    """Apply only high-confidence hard predicates from a search profile.

    ``needs_semantic_review`` means all known deterministic facts remain viable;
    it is not a match.  In particular, AI involvement is never inferred from the
    employer or title by this function.
    """

    value = _job_dict(job)
    try:
        constraints = profile["hard_constraints"]
        role_policy = constraints["role"]
    except (KeyError, TypeError) as exc:
        raise JobInvariantError("search profile lacks required hard constraints") from exc

    rejected: list[str] = []
    semantic: list[str] = []
    accepted: list[str] = []

    location_status, location_reason = _location_evaluation(
        value.get("locations", []), constraints["location"]
    )
    if location_status == "reject":
        rejected.append(location_reason)
    elif location_status == "unknown":
        semantic.append(location_reason)
    else:
        accepted.append(location_reason)

    workplace = value.get("workplace_arrangement", "unknown")
    workplace_policy = constraints["workplace"]
    if workplace == "unknown":
        if workplace_policy.get("unknown_policy") == "include":
            accepted.append("workplace_unknown_included_by_profile")
        else:
            rejected.append("workplace_unknown_excluded_by_profile")
    elif workplace in workplace_policy.get("allowed_arrangements", []):
        accepted.append(f"workplace_{workplace}_allowed")
    else:
        rejected.append(f"workplace_{workplace}_excluded")

    employment = value.get("employment_type")
    employment_policy = constraints["employment"]
    if employment is None:
        if employment_policy.get("missing_type_policy") == "assume_full_time":
            accepted.append("employment_missing_assumed_full_time")
        else:
            rejected.append("employment_missing_excluded")
    else:
        normalized_employment = _normalized_phrase(employment)
        allowed = {
            _normalized_phrase(item)
            for item in employment_policy.get("allowed_types", [])
        }
        explicitly_disallowed = {
            _normalized_phrase(item)
            for item in employment_policy.get("explicitly_disallowed_types", [])
        }
        if normalized_employment in allowed:
            accepted.append(f"employment_{normalized_employment}_allowed")
        elif normalized_employment in explicitly_disallowed:
            rejected.append(f"employment_{normalized_employment}_excluded")
        else:
            # Custom provider labels such as "regular" do not prove hours or
            # contract status. Preserve them rather than forcing an exclusion.
            semantic.append("employment_type_requires_review")

    compensation_status, compensation_reason = _compensation_evaluation(
        value.get("compensation"), constraints["compensation"]
    )
    if compensation_status == "reject":
        rejected.append(compensation_reason)
    elif compensation_status == "unknown":
        semantic.append(compensation_reason)
    else:
        accepted.append(compensation_reason)

    title = _normalized_phrase(value.get("title"))
    excluded_titles = {
        _normalized_phrase(item) for item in role_policy.get("excluded_titles", [])
    }
    signaled_titles = {
        _normalized_phrase(item)
        for item in (
            list(role_policy.get("primary_titles", []))
            + list(role_policy.get("adjacent_titles", []))
        )
    }
    if title in excluded_titles:
        rejected.append("title_explicitly_excluded")
    elif title in signaled_titles:
        accepted.append("title_is_configured_role_signal")
    else:
        semantic.append("role_fit_requires_review")

    ai_policy = role_policy.get("ai_involvement", {})
    if ai_policy.get("required"):
        semantic.append("ai_involvement_requires_official_posting_review")

    rejected = list(dict.fromkeys(rejected))
    semantic = list(dict.fromkeys(semantic))
    accepted = list(dict.fromkeys(accepted))
    if rejected:
        status = "rejected"
        # Semantic work is unnecessary once a hard predicate rejects the job.
        semantic = []
    elif semantic:
        status = "needs_semantic_review"
    else:
        status = "deterministic_pass"
    stable_id = str(value.get("stable_id") or "")
    if not stable_id:
        raise JobInvariantError("normalized job lacks stable_id")
    return FilterDecision(
        stable_id=stable_id,
        status=status,
        rejection_reasons=tuple(rejected),
        semantic_requirements=tuple(semantic),
        accepted_facts=tuple(accepted),
    )


def evaluate_jobs(
    jobs: Iterable[NormalizedJob | Mapping[str, Any]],
    profile: Mapping[str, Any],
) -> dict[str, Any]:
    """Evaluate and stably partition a source enumeration."""

    decisions = [evaluate_profile(job, profile) for job in jobs]
    decisions.sort(key=lambda item: item.stable_id)
    return {
        "decisions": [decision.to_dict() for decision in decisions],
        "counts": {
            "normalized": len(decisions),
            "deterministically_rejected": sum(item.rejected for item in decisions),
            "needs_semantic_review": sum(
                item.status == "needs_semantic_review" for item in decisions
            ),
            "deterministic_pass": sum(
                item.status == "deterministic_pass" for item in decisions
            ),
        },
    }


_LIFECYCLE_FIELDS = {"status", "first_seen_at", "last_seen_at", "inactive_at"}
_SEMANTIC_ANNOTATION_FIELDS = {
    "accepted_facts",
    "ai_involvement_evidence",
    "qualification_status",
    "review_notes",
    "role_fit_evidence",
    "semantic_evidence",
    "semantic_requirements",
}


def _facts(record: Mapping[str, Any]) -> dict[str, Any]:
    return {key: deepcopy(value) for key, value in record.items() if key not in _LIFECYCLE_FIELDS}


def _new_record(
    job: Mapping[str, Any],
    scanned_at: str,
    prior: Mapping[str, Any] | None,
) -> dict[str, Any]:
    record = deepcopy(dict(job))
    record.update(
        {
            "status": "active",
            "first_seen_at": prior.get("first_seen_at", scanned_at) if prior else scanned_at,
            "last_seen_at": scanned_at,
            "inactive_at": None,
        }
    )
    return record


def _adapter_parts(
    result: AdapterResult | Mapping[str, Any],
) -> tuple[str, str, str, bool, dict[str, Any], list[dict[str, Any]]]:
    if isinstance(result, AdapterResult):
        parts = (
            result.company_id,
            result.source_id,
            result.provider,
            result.complete,
            result.fetch.to_dict(),
            [job.to_dict() for job in result.jobs],
        )
    else:
        if not isinstance(result, Mapping):
            raise JobInvariantError("adapter result must be an object")
        fetch = result.get("fetch")
        if not isinstance(fetch, Mapping):
            raise JobInvariantError("adapter result lacks fetch metadata")
        jobs = result.get("jobs")
        if not isinstance(jobs, list) or not all(
            isinstance(job, Mapping) for job in jobs
        ):
            raise JobInvariantError("adapter result jobs must be an array of objects")
        parts = (
            str(result.get("company_id", "")),
            str(result.get("source_id", "")),
            str(result.get("provider", "")),
            bool(fetch.get("complete")),
            deepcopy(dict(fetch)),
            [deepcopy(dict(job)) for job in jobs],
        )
    _validate_adapter_result_parts(*parts)
    return parts


def _validate_adapter_result_parts(
    company_id: str,
    source_id: str,
    provider: str,
    complete: bool,
    fetch: Mapping[str, Any],
    jobs: Sequence[Mapping[str, Any]],
) -> None:
    del complete  # Completeness is consumed by reconciliation after validation.
    if not company_id or not source_id or not provider:
        raise JobInvariantError("adapter result lacks source ownership")
    if fetch.get("provider") != provider:
        raise JobInvariantError("adapter fetch provider does not match result provider")
    unique_jobs = fetch.get("unique_jobs")
    raw_jobs = fetch.get("raw_jobs_seen")
    duplicates = fetch.get("duplicates_removed")
    if unique_jobs != len(jobs):
        raise JobInvariantError("adapter unique_jobs does not match normalized jobs")
    if (
        not isinstance(raw_jobs, int)
        or isinstance(raw_jobs, bool)
        or raw_jobs < len(jobs)
    ):
        raise JobInvariantError("adapter raw_jobs_seen is inconsistent")
    if (
        not isinstance(duplicates, int)
        or isinstance(duplicates, bool)
        or not 0 <= duplicates <= raw_jobs
    ):
        raise JobInvariantError("adapter duplicates_removed is inconsistent")

    seen_urls: set[str] = set()
    for job in jobs:
        if (
            job.get("company_id") != company_id
            or job.get("source_id") != source_id
            or job.get("provider") != provider
        ):
            raise JobInvariantError("normalized job does not belong to adapter source")
        source_job_id = str(job.get("source_job_id") or "")
        if not source_job_id:
            raise JobInvariantError("normalized job lacks source_job_id")
        if job.get("stable_id") != stable_job_id(source_id, source_job_id):
            raise JobInvariantError("normalized job stable_id does not match its provider ID")
        try:
            normalized_url = normalize_url(job.get("canonical_url"))
        except AdapterError as exc:
            raise JobInvariantError(str(exc)) from exc
        if job.get("canonical_url") != normalized_url:
            raise JobInvariantError("normalized job canonical_url is not canonical")
        if normalized_url in seen_urls:
            raise JobInvariantError("adapter result contains duplicate canonical job URLs")
        seen_urls.add(normalized_url)


def _validate_unique_job_records(jobs: Sequence[Mapping[str, Any]]) -> None:
    seen: set[str] = set()
    for job in jobs:
        stable_id = str(job.get("stable_id") or "")
        if not stable_id:
            raise JobInvariantError("snapshot job lacks stable_id")
        if stable_id in seen:
            raise JobInvariantError(f"duplicate snapshot job {stable_id}")
        seen.add(stable_id)


def _reconcile_lifecycle_snapshot(
    previous: Mapping[str, Any] | None,
    result: AdapterResult | Mapping[str, Any],
    *,
    scanned_at: str,
) -> dict[str, Any]:
    """Apply one adapter enumeration without unsafe disappearance.

    A complete enumeration may inactivate jobs that disappeared.  An incomplete
    enumeration updates jobs it did observe but carries every unobserved prior
    record forward unchanged.  Thus a partial provider response can never close a
    previously active posting.
    """

    if not isinstance(scanned_at, str) or not scanned_at.strip():
        raise JobInvariantError("scanned_at is required")
    company_id, source_id, provider, complete, fetch, observed = _adapter_parts(result)
    if not company_id or not source_id or not provider:
        raise JobInvariantError("adapter result lacks source ownership")
    if previous is None:
        prior_value: dict[str, Any] = {}
        prior_jobs: list[dict[str, Any]] = []
        revision = 0
    else:
        prior_value = deepcopy(dict(previous))
        if prior_value.get("source_id") != source_id or prior_value.get("company_id") != company_id:
            raise JobInvariantError("adapter result does not own the prior snapshot")
        if prior_value.get("provider") != provider:
            raise JobInvariantError("provider cannot change during snapshot reconciliation")
        prior_jobs_value = prior_value.get("jobs", [])
        if not isinstance(prior_jobs_value, list) or not all(
            isinstance(item, Mapping) for item in prior_jobs_value
        ):
            raise JobInvariantError("prior snapshot jobs are invalid")
        prior_jobs = [deepcopy(dict(item)) for item in prior_jobs_value]
        revision = int(prior_value.get("revision", 0))

    _validate_unique_job_records(prior_jobs)
    _validate_unique_job_records(observed)
    prior_by_id = {str(job["stable_id"]): job for job in prior_jobs}
    observed_by_id = {str(job["stable_id"]): job for job in observed}
    for job in observed:
        if job.get("company_id") != company_id or job.get("source_id") != source_id:
            raise JobInvariantError("normalized job does not belong to adapter source")

    reconciled: dict[str, dict[str, Any]] = {}
    for stable_id, job in observed_by_id.items():
        reconciled[stable_id] = _new_record(job, scanned_at, prior_by_id.get(stable_id))

    for stable_id, prior in prior_by_id.items():
        if stable_id in observed_by_id:
            continue
        if complete and prior.get("status") == "active":
            closed = deepcopy(prior)
            closed["status"] = "inactive"
            closed["inactive_at"] = scanned_at
            reconciled[stable_id] = closed
        else:
            # Includes incomplete enumerations and already inactive historical
            # jobs from a complete enumeration.
            reconciled[stable_id] = deepcopy(prior)

    last_success = scanned_at if complete else prior_value.get("last_successful_scan_at")
    snapshot = {
        "schema_version": 1,
        "company_id": company_id,
        "source_id": source_id,
        "provider": provider,
        "revision": revision + 1,
        "snapshot_status": "current" if complete else "incomplete",
        "last_attempt_at": scanned_at,
        "last_successful_scan_at": last_success,
        "fetch": fetch,
        "jobs": [reconciled[key] for key in sorted(reconciled)],
    }
    return snapshot


def _selected_records(
    selected: Sequence[NormalizedJob | Mapping[str, Any] | str],
    observed: Sequence[Mapping[str, Any]],
    *,
    company_id: str,
    source_id: str,
) -> list[dict[str, Any]]:
    """Merge semantic annotations onto facts from the captured enumeration."""

    observed_by_id = {str(job.get("stable_id")): deepcopy(dict(job)) for job in observed}
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in selected:
        if isinstance(item, str):
            stable_id = item
            annotation: dict[str, Any] = {}
        else:
            annotation = _job_dict(item)
            stable_id = str(annotation.get("stable_id") or "")
        base = observed_by_id.get(stable_id)
        if base is None:
            raise JobInvariantError(
                f"selected job {stable_id!r} was not present in the captured enumeration"
            )
        if stable_id in seen:
            raise JobInvariantError(f"duplicate selected job {stable_id}")
        seen.add(stable_id)
        unknown_fields = set(annotation) - set(base) - _SEMANTIC_ANNOTATION_FIELDS
        if unknown_fields:
            raise JobInvariantError(
                "selected job contains unsupported semantic annotations: "
                + ", ".join(sorted(unknown_fields))
            )
        # Every captured provider field is immutable, including location,
        # workplace, employment, and compensation facts. The result may repeat
        # those fields for reviewability, but it may not reinterpret them.
        for field in set(base) & annotation.keys():
            if annotation[field] != base[field]:
                raise JobInvariantError(
                    f"selected job cannot overwrite adapter field {field}"
                )
        for field in _SEMANTIC_ANNOTATION_FIELDS & annotation.keys():
            base[field] = deepcopy(annotation[field])
        if base.get("company_id") != company_id or base.get("source_id") != source_id:
            raise JobInvariantError("selected job cannot change source ownership")
        records.append(base)
    return sorted(records, key=lambda value: str(value["stable_id"]))


def rejection_counts_from_evaluation(evaluation: Mapping[str, Any]) -> dict[str, int]:
    """Count deterministic rejection reasons without losing their distinctions."""

    counts: dict[str, int] = {}
    decisions = evaluation.get("decisions", [])
    if not isinstance(decisions, list):
        raise JobInvariantError("filter evaluation decisions must be an array")
    for decision in decisions:
        if not isinstance(decision, Mapping) or decision.get("status") != "rejected":
            continue
        counts["total"] = counts.get("total", 0) + 1
        for reason in decision.get("rejection_reasons", []) or []:
            key = str(reason)
            counts[key] = counts.get(key, 0) + 1
    return {key: counts[key] for key in sorted(counts)}


def semantic_candidates(
    jobs: Iterable[NormalizedJob | Mapping[str, Any]],
    evaluation: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Return exact normalized jobs that still need agent interpretation."""

    job_values = {_job_dict(job)["stable_id"]: _job_dict(job) for job in jobs}
    candidates: list[dict[str, Any]] = []
    for decision in evaluation.get("decisions", []) or []:
        if not isinstance(decision, Mapping) or decision.get("status") != "needs_semantic_review":
            continue
        stable_id = str(decision.get("stable_id") or "")
        if stable_id not in job_values:
            raise JobInvariantError(
                f"filter decision references unknown normalized job {stable_id!r}"
            )
        candidate = deepcopy(job_values[stable_id])
        candidate["semantic_requirements"] = list(
            decision.get("semantic_requirements", []) or []
        )
        candidate["accepted_facts"] = list(decision.get("accepted_facts", []) or [])
        candidates.append(candidate)
    return sorted(candidates, key=lambda value: str(value["stable_id"]))


def reconcile_snapshot(
    previous: Mapping[str, Any] | None,
    result: AdapterResult | Mapping[str, Any],
    *,
    scanned_at: str,
    source_revision: int,
    profile_id: str,
    profile_digest: str,
    qualifying_jobs: Sequence[NormalizedJob | Mapping[str, Any] | str],
    unresolved_candidates: Sequence[NormalizedJob | Mapping[str, Any] | str] = (),
    rejection_counts: Mapping[str, int] | None = None,
    warnings: Sequence[str] = (),
) -> dict[str, Any]:
    """Build the exact canonical ``job-snapshot.schema.json`` document.

    The adapter result is the enumeration boundary.  ``qualifying_jobs`` and
    ``unresolved_candidates`` may add semantic annotations, but every named job
    must have appeared in that enumeration.  An incomplete result preserves all
    prior qualifying records and unresolved candidates that were not observed,
    preventing partial fetches from silently removing work or closing jobs.
    """

    if (
        not isinstance(source_revision, int)
        or isinstance(source_revision, bool)
        or source_revision < 1
    ):
        raise JobInvariantError("source_revision must be a positive integer")
    if not isinstance(profile_id, str) or not profile_id.strip():
        raise JobInvariantError("profile_id is required")
    if not isinstance(profile_digest, str) or not re.fullmatch(
        r"[a-f0-9]{64}", profile_digest
    ):
        raise JobInvariantError("profile_digest must be a lowercase SHA-256 digest")

    company_id, source_id, provider, complete, fetch, observed = _adapter_parts(result)
    if previous is not None:
        if previous.get("profile_id") != profile_id:
            raise JobInvariantError("profile_id cannot change in an existing source snapshot")
        if previous.get("profile_digest") != profile_digest and not complete:
            raise JobInvariantError(
                "an incomplete fetch cannot safely reconcile a changed profile"
            )

    selected = _selected_records(
        qualifying_jobs,
        observed,
        company_id=company_id,
        source_id=source_id,
    )
    unresolved = _selected_records(
        unresolved_candidates,
        observed,
        company_id=company_id,
        source_id=source_id,
    )
    selected_ids = {str(item["stable_id"]) for item in selected}
    unresolved_ids = {str(item["stable_id"]) for item in unresolved}
    overlap = selected_ids & unresolved_ids
    if overlap:
        raise JobInvariantError(
            "jobs cannot be both qualifying and unresolved: " + ", ".join(sorted(overlap))
        )

    # Reuse the lifecycle core for qualifying records.  It relies only on the
    # common ownership/revision/jobs fields and accepts canonical prior snapshots.
    selected_fetch = deepcopy(fetch)
    # The lifecycle core receives only the qualifying subset; keep its internal
    # normalized-count invariant without changing the full enumeration metadata
    # used by the canonical snapshot below.
    selected_fetch["unique_jobs"] = len(selected)
    selected_result = {
        "company_id": company_id,
        "source_id": source_id,
        "provider": provider,
        "fetch": selected_fetch,
        "jobs": selected,
    }
    lifecycle = _reconcile_lifecycle_snapshot(
        previous,
        selected_result,
        scanned_at=scanned_at,
    )

    prior_unresolved = [] if previous is None else previous.get("unresolved_candidates", [])
    if not isinstance(prior_unresolved, list) or not all(
        isinstance(item, Mapping) for item in prior_unresolved
    ):
        raise JobInvariantError("prior unresolved_candidates are invalid")
    prior_unresolved_by_id = {
        str(item.get("stable_id")): deepcopy(dict(item)) for item in prior_unresolved
    }
    current_unresolved_by_id = {
        str(item["stable_id"]): deepcopy(item) for item in unresolved
    }
    if not complete:
        for stable_id, item in prior_unresolved_by_id.items():
            if stable_id not in current_unresolved_by_id and stable_id not in selected_ids:
                current_unresolved_by_id[stable_id] = item

    normalized_counts: dict[str, int] = {}
    for key, value in (rejection_counts or {}).items():
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise JobInvariantError("rejection counts must be non-negative integers")
        normalized_counts[str(key)] = value

    output_warnings = list(dict.fromkeys(str(item) for item in warnings if str(item)))
    if not complete:
        output_warnings.append(
            f"incomplete_fetch:{fetch.get('completeness_reason', 'unknown')}"
        )
        output_warnings.append("current_job_count_is_observed_partial_count")
    output_warnings = list(dict.fromkeys(output_warnings))

    return {
        "schema_version": 1,
        "revision": (int(previous.get("revision", 0)) if previous else 0) + 1,
        "source_id": source_id,
        "company_id": company_id,
        "source_revision": source_revision,
        "profile_id": profile_id,
        "profile_digest": profile_digest,
        "provider": provider,
        "last_attempt_at": scanned_at,
        "last_successful_scan_at": (
            scanned_at if complete else (previous or {}).get("last_successful_scan_at")
        ),
        "fetch_complete": complete,
        "current_job_count": int(fetch.get("unique_jobs", len(observed))),
        "jobs": lifecycle["jobs"],
        "unresolved_candidates": [
            current_unresolved_by_id[key] for key in sorted(current_unresolved_by_id)
        ],
        "rejection_counts": {
            key: normalized_counts[key] for key in sorted(normalized_counts)
        },
        "warnings": output_warnings,
        "application_ledger": deepcopy(
            (previous or {}).get("application_ledger", [])
        ),
    }


def record_failed_snapshot(
    previous: Mapping[str, Any] | None,
    *,
    company_id: str,
    source_id: str,
    provider: str,
    attempted_at: str,
    failure_code: str,
    failure_detail: str,
    source_revision: int,
    profile_id: str,
    profile_digest: str,
) -> dict[str, Any]:
    """Record a failed attempt while preserving all prior job lifecycle state."""

    if (
        not isinstance(source_revision, int)
        or isinstance(source_revision, bool)
        or source_revision < 1
    ):
        raise JobInvariantError("source_revision must be a positive integer")
    if not isinstance(profile_id, str) or not profile_id.strip():
        raise JobInvariantError("profile_id is required")
    if not isinstance(profile_digest, str) or not re.fullmatch(r"[a-f0-9]{64}", profile_digest):
        raise JobInvariantError("profile_digest must be a lowercase SHA-256 digest")
    if previous is not None:
        if previous.get("company_id") != company_id or previous.get("source_id") != source_id:
            raise JobInvariantError("failed attempt does not own the prior snapshot")
        if previous.get("provider") != provider:
            raise JobInvariantError("provider cannot change during failed attempt")
        if (
            previous.get("profile_id") != profile_id
            or previous.get("profile_digest") != profile_digest
        ):
            raise JobInvariantError(
                "a failed fetch cannot replace an existing snapshot's profile"
            )
        jobs = deepcopy(previous.get("jobs", []))
        revision = int(previous.get("revision", 0))
        last_success = previous.get("last_successful_scan_at")
        unresolved = deepcopy(previous.get("unresolved_candidates", []))
        rejection_counts = deepcopy(previous.get("rejection_counts", {}))
        application_ledger = deepcopy(previous.get("application_ledger", []))
    else:
        jobs = []
        revision = 0
        last_success = None
        unresolved = []
        rejection_counts = {}
        application_ledger = []
    _validate_unique_job_records(jobs)
    return {
        "schema_version": 1,
        "revision": revision + 1,
        "source_id": source_id,
        "company_id": company_id,
        "source_revision": source_revision,
        "profile_id": profile_id,
        "profile_digest": profile_digest,
        "provider": provider,
        "last_attempt_at": attempted_at,
        "last_successful_scan_at": last_success,
        "fetch_complete": False,
        "current_job_count": int((previous or {}).get("current_job_count", 0)),
        "jobs": jobs,
        "unresolved_candidates": unresolved,
        "rejection_counts": rejection_counts,
        "warnings": [
            *deepcopy((previous or {}).get("warnings", [])),
            f"fetch_failed:{failure_code}:{failure_detail}",
        ],
        "application_ledger": application_ledger,
    }


def compare_snapshots(
    previous: Mapping[str, Any] | None, current: Mapping[str, Any]
) -> dict[str, list[str]]:
    """Return a deterministic job lifecycle/fact delta for operation reports."""

    old = {
        str(item["stable_id"]): item
        for item in ((previous or {}).get("jobs", []) or [])
    }
    new = {str(item["stable_id"]): item for item in current.get("jobs", []) or []}
    delta = {"added": [], "updated": [], "reopened": [], "closed": [], "unchanged": []}
    for stable_id in sorted(new):
        current_job = new[stable_id]
        prior = old.get(stable_id)
        if prior is None:
            delta["added"].append(stable_id)
        elif prior.get("status") == "inactive" and current_job.get("status") == "active":
            delta["reopened"].append(stable_id)
        elif prior.get("status") == "active" and current_job.get("status") == "inactive":
            delta["closed"].append(stable_id)
        elif _facts(prior) != _facts(current_job):
            delta["updated"].append(stable_id)
        else:
            delta["unchanged"].append(stable_id)
    return delta


def active_jobs(snapshot: Mapping[str, Any]) -> list[dict[str, Any]]:
    jobs = [
        deepcopy(dict(job))
        for job in snapshot.get("jobs", [])
        if isinstance(job, Mapping) and job.get("status") == "active"
    ]
    return sorted(jobs, key=lambda job: str(job.get("stable_id", "")))


def canonical_json(value: Mapping[str, Any]) -> str:
    """Stable JSON helper useful for fixture and idempotence assertions."""

    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


__all__ = [
    "FilterDecision",
    "JobInvariantError",
    "active_jobs",
    "canonical_json",
    "compare_snapshots",
    "evaluate_jobs",
    "evaluate_profile",
    "rejection_counts_from_evaluation",
    "reconcile_snapshot",
    "record_failed_snapshot",
    "semantic_candidates",
]
