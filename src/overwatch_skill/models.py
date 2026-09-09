"""Shared source contracts. Unknown facts stay null; errors are never empty results."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


def utcnow() -> str:
    return datetime.now(UTC).isoformat()


class SourceError(Exception):
    def __init__(self, code: str, message: str, source: str, source_url: str | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.source = source
        self.source_url = source_url
        self.http_status: int | None = None
        self.response_body: Any = None


@dataclass
class SourceResult:
    data: Any
    source: str
    source_url: str
    retrieved_at: str = field(default_factory=utcnow)
    source_updated_at: str | None = None
    data_period: str | None = None
    data_patch: str | None = None
    requested_filters: dict[str, Any] = field(default_factory=dict)
    applied_filters: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    cached: bool = False
    stale: bool = False

    def envelope(self) -> dict[str, Any]:
        from dataclasses import asdict

        result = asdict(self)
        empty = self.data is None or self.data == [] or self.data == {}
        if isinstance(self.data, dict) and "records" in self.data:
            empty = self.data["records"] == []
        result["status"] = "stale" if self.stale else "empty" if empty else "ok"
        result["error"] = (
            {"code": "STALE_DATA", "message": "Cached data exceeded its freshness interval."}
            if self.stale
            else {"code": "EMPTY_RESULT", "message": "No matching records were found."}
            if empty
            else None
        )
        return result


def error_envelope(error: SourceError, requested: dict | None = None) -> dict[str, Any]:
    return {
        "status": "error",
        "data": None,
        "error": {"code": error.code, "message": error.message},
        "source": error.source,
        "source_url": error.source_url,
        "retrieved_at": None,
        "source_updated_at": None,
        "data_period": None,
        "data_patch": None,
        "requested_filters": requested or {},
        "applied_filters": {},
        "warnings": [],
        "cached": False,
        "stale": False,
    }
