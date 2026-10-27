"""Offline parsers for the structured career providers supported by V4."""

from __future__ import annotations

from typing import Any

from . import ashby, greenhouse, lever
from .base import AdapterError, AdapterResult, FetchMetadata, NormalizedJob


SUPPORTED_PROVIDERS = ("ashby", "greenhouse", "lever")


def parse_provider_payload(provider: str, payload: Any, **kwargs: Any) -> AdapterResult:
    normalized = provider.casefold().strip()
    if normalized == "ashby":
        return ashby.parse_payload(payload, **kwargs)
    if normalized == "greenhouse":
        return greenhouse.parse_payload(payload, **kwargs)
    if normalized == "lever":
        return lever.parse_payload(payload, **kwargs)
    raise AdapterError(f"unsupported structured provider: {provider!r}")


__all__ = [
    "AdapterError",
    "AdapterResult",
    "FetchMetadata",
    "NormalizedJob",
    "SUPPORTED_PROVIDERS",
    "parse_provider_payload",
]

