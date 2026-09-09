"""Shared query boundaries for the one-shot CLI and optional MCP server."""

import json
import logging

from pydantic import ValidationError

from .db import Repository
from .models import SourceError, error_envelope
from .operations import DESCRIPTIONS
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
