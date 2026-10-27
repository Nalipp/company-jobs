"""Internal local helper for the single Codex collect-jobs workflow."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any, Sequence

from .collect_jobs import (
    CollectionError, block_scan, claim_next, finish_scan, renew,
    run_structured, status,
)
from .db import DatabaseError


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="collect-jobs",
        description="V5 SQL job collection helper; browser sources run through the Codex skill",
    )
    root.add_argument("--database", type=Path, default=Path("data/v5.sqlite"))
    sub = root.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="Read-only collection progress")

    batch = sub.add_parser("run-structured", help="Collect due official structured feeds")
    batch.add_argument("--limit", type=int, default=25)
    batch.add_argument("--source-id")
    batch.add_argument("--claimed-by", default="v5-structured-batch")

    claim = sub.add_parser("claim-next", help="Claim one browser source")
    claim.add_argument("--mode", choices=("manual", "structured"), default="manual")
    claim.add_argument("--claimed-by", required=True)
    claim.add_argument("--source-id")

    submit = sub.add_parser("submit", help="Validate and finalize one browser result JSON")
    submit.add_argument("--result", type=Path, required=True)

    extend = sub.add_parser("renew", help="Renew the lease on one claimed scan")
    extend.add_argument("--scan-id", required=True)
    extend.add_argument("--claim-token", required=True)

    block = sub.add_parser("block", help="Record a genuine source or browser failure")
    block.add_argument("--scan-id", required=True)
    block.add_argument("--claim-token", required=True)
    block.add_argument("--error-class", required=True)
    block.add_argument("--notes", required=True)
    return root


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        value: dict[str, Any]
        if args.command == "status":
            value = status(args.database)
        elif args.command == "run-structured":
            value = run_structured(
                args.database, limit=args.limit, source_id=args.source_id,
                claimed_by=args.claimed_by,
            )
        elif args.command == "claim-next":
            value = claim_next(
                args.database, mode=args.mode, claimed_by=args.claimed_by,
                source_id=args.source_id,
            )
        elif args.command == "submit":
            document = json.loads(args.result.read_text(encoding="utf-8"))
            if not isinstance(document, dict):
                raise CollectionError("result must be a JSON object")
            if document.get("outcome") == "blocked":
                value = block_scan(
                    args.database, scan_id=document["scan_id"],
                    claim_token=document["claim_token"],
                    error_class=document.get("error_class") or "source_access",
                    notes=document["notes"],
                )
            else:
                value = finish_scan(
                    args.database, scan_id=document["scan_id"],
                    claim_token=document["claim_token"],
                    jobs=document["jobs"], complete=document["complete"],
                    summary=document.get("summary"), notes=document.get("notes"),
                    recipe=document.get("recipe"), manual=True,
                    completeness_evidence=document.get("completeness_evidence"),
                    identity_evidence=document.get("identity_evidence"),
                    retry_after_hours=document.get("retry_after_hours"),
                )
        elif args.command == "renew":
            value = renew(args.database, scan_id=args.scan_id,
                          claim_token=args.claim_token)
        elif args.command == "block":
            value = block_scan(
                args.database, scan_id=args.scan_id, claim_token=args.claim_token,
                error_class=args.error_class, notes=args.notes,
            )
        else:
            return 2
    except (CollectionError, DatabaseError, sqlite3.Error, OSError, ValueError,
            KeyError, TypeError, json.JSONDecodeError) as exc:
        print(json.dumps({"error": type(exc).__name__, "message": str(exc)}), file=sys.stderr)
        return 1
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
