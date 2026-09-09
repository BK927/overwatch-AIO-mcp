"""One-shot JSON queries and explicit local evidence management."""

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

from pydantic import ValidationError

from . import __version__
from .db import Repository
from .models import SourceError, error_envelope
from .operations import DESCRIPTIONS
from .registry import Registry
from .requests import REQUESTS
from .responses import RESPONSES
from .service import Service

INPUT_ERRORS = {"INVALID_ARGUMENT", "UNSUPPORTED_FILTER"}


def failure(code: str, message: str, arguments: dict | None = None) -> dict:
    return error_envelope(SourceError(code, message, "cli"), arguments)


def exit_code(result: dict) -> int:
    if result["status"] != "error":
        return 0
    return 2 if result["error"]["code"] in INPUT_ERRORS else 1


async def query(
    name: str, arguments: dict, *, db_path: str | None = None, service: Service | None = None
) -> tuple[dict, int]:
    """Validate both boundaries; retain the original fields and values on success."""
    if name not in REQUESTS:
        return failure("INVALID_ARGUMENT", f"Unknown operation: {name}"), 2
    if not isinstance(arguments, dict):
        return failure("INVALID_ARGUMENT", "Query input must be a JSON object."), 2
    try:
        json.dumps(arguments, allow_nan=False)
        REQUESTS[name].model_validate(arguments)
    except (ValidationError, ValueError, TypeError) as exc:
        if isinstance(exc, ValidationError):
            message = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
            return failure("INVALID_ARGUMENT", message, arguments), 2
        return failure("INVALID_ARGUMENT", "Query input must contain only JSON values."), 2

    owned = service is None
    active = service
    try:
        try:
            if active is None:
                active = Service(Repository(db_path))
            result = await active.call(name, arguments)
            RESPONSES[name].model_validate(result)
            # Also reject non-finite numbers in permissive source extension fields.
            json.dumps(result, ensure_ascii=False, allow_nan=False)
            code = exit_code(result)
        finally:
            if owned and active is not None:
                await active.close()
    except ValidationError:
        result = failure("INTERNAL_ERROR", "Operation returned an invalid response.", arguments)
        code = 1
    except Exception:
        # Never print upstream bodies, local debug values or tracebacks in query output.
        logging.getLogger(__name__).error("Query failed while executing or validating its result.")
        result = failure("INTERNAL_ERROR", "Unable to complete the operation.", arguments)
        code = 1
    return result, code


def schemas() -> dict:
    return {
        name: {
            "description": DESCRIPTIONS[name],
            "inputSchema": request.model_json_schema(),
            "outputSchema": RESPONSES[name].model_json_schema(),
        }
        for name, request in REQUESTS.items()
    }


def reject_constant(value):
    raise ValueError(f"Non-JSON number: {value}")


def read_json(path: str):
    if path == "-":
        stream = getattr(sys.stdin, "buffer", sys.stdin)
        text = stream.read()
        if isinstance(text, bytes):
            text = text.decode("utf-8-sig")
        else:
            text = text.removeprefix("\ufeff")
    else:
        try:
            text = Path(path).read_text(encoding="utf-8-sig")
        except OSError as exc:
            raise ValueError(f"Cannot read input file: {exc}") from None
    return json.loads(text, parse_constant=reject_constant)


def read_records(path: str) -> list[dict]:
    records = read_json(path)
    if not isinstance(records, list) or any(not isinstance(row, dict) for row in records):
        raise ValueError("Evidence input must be a JSON array of objects.")
    return records


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


def build_parser() -> argparse.ArgumentParser:
    parser = Parser(description="On-demand Overwatch queries and local evidence management")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--db", help="SQLite path (then OW_DB_PATH, then per-user default)")
    sub = parser.add_subparsers(dest="command", required=True)
    query_parser = sub.add_parser("query", help="Execute one operation and print one JSON result")
    query_parser.add_argument("operation", choices=REQUESTS)
    query_parser.add_argument("--input-file", help="UTF-8 JSON file, or - for stdin; default: {}")
    schema = sub.add_parser("schema", help="Export operation input/output JSON Schemas")
    schema.add_argument("--output")
    importer = sub.add_parser("import-rankers", help="Import curator-authored ranker evidence")
    importer.add_argument("path")
    linker = sub.add_parser("link-replay", help="Attach evidence and optional player identity")
    linker.add_argument("code")
    linker.add_argument("--player-id", type=int)
    linker.add_argument("--evidence-file", required=True)
    check = sub.add_parser("record-replay-check", help="Record an actual playback observation")
    check.add_argument("code")
    check.add_argument(
        "--status", required=True, choices=["user_reported_working", "client_verified"]
    )
    check.add_argument("--source", required=True)
    check.add_argument("--evidence-ref", required=True)
    check.add_argument("--observed-at", required=True)
    sub.add_parser("discover-rankers", help="List stored unverified high-tier replay authors")
    return parser


def curate(args) -> dict:
    repository = Repository(args.db)
    registry = Registry(repository)
    try:
        if args.command == "import-rankers":
            return {"imported_player_ids": registry.import_rankers(read_records(args.path))}
        if args.command == "link-replay":
            registry.add_replay_evidence(
                args.code, read_records(args.evidence_file), args.player_id
            )
            return {"code": args.code.upper(), "evidence_recorded": True}
        if args.command == "record-replay-check":
            registry.validate_replay(
                args.code, args.status, args.source, args.evidence_ref, args.observed_at
            )
            return {"code": args.code.upper(), "status": args.status, "source": args.source}
        candidates = []
        for replay in repository.replay_rows():
            if replay.get("player_id") is None and str(
                replay.get("tier_claim", "")
            ).lower().startswith(("grandmaster", "champion")):
                candidates.append(
                    {
                        "display_name": replay.get("display_name"),
                        "tier_claim": replay["tier_claim"],
                        "source_url": replay["source_url"],
                        "replay_code": replay["code"],
                        "country": None,
                        "verification": "source_claim",
                    }
                )
        return {
            "candidates": candidates[:100],
            "warnings": [
                "Unverified high-tier replay authors only. No Top 500 rank, country or identity is inferred; candidates require independent curator evidence."
            ],
        }
    finally:
        repository.close()


def main(argv: list[str] | None = None) -> int:
    # Console-script output and redirected pipes use the same encoding on Windows/Linux.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        args = build_parser().parse_args(argv)
        if args.command == "query":
            try:
                arguments = read_json(args.input_file) if args.input_file else {}
            except (OSError, ValueError) as exc:
                # File paths/decoder locations are useful; file contents need not be printed.
                result, code = failure("INVALID_ARGUMENT", f"Cannot read JSON input: {exc}"), 2
            else:
                result, code = asyncio.run(query(args.operation, arguments, db_path=args.db))
        elif args.command == "schema":
            result, code = schemas(), 0
            if args.output:
                target = Path(args.output)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(
                    json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
                )
                return 0
        else:
            result, code = curate(args), 0
    except SourceError as exc:
        result = error_envelope(exc)
        code = exit_code(result)
    except (ValueError, ValidationError) as exc:
        result, code = failure("INVALID_ARGUMENT", str(exc)), 2
    except Exception:
        logging.getLogger(__name__).error("Command could not complete.")
        result, code = failure("INTERNAL_ERROR", "Unable to complete the command."), 1
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
