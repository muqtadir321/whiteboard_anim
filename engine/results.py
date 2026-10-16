"""Structured stage results shared by every pipeline stage."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Status(str, Enum):
    SUCCESS = "SUCCESS"
    PARTIAL_SUCCESS = "PARTIAL_SUCCESS"
    SKIPPED = "SKIPPED"
    RETRYABLE_FAILURE = "RETRYABLE_FAILURE"
    PERMANENT_FAILURE = "PERMANENT_FAILURE"

    @property
    def ok(self) -> bool:
        return self in (Status.SUCCESS, Status.PARTIAL_SUCCESS)


@dataclass
class StageResult:
    stage: str
    status: Status
    message: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    started: float = field(default_factory=time.time)
    finished: float | None = None

    def done(self) -> "StageResult":
        self.finished = time.time()
        return self

    @property
    def ok(self) -> bool:
        return self.status.ok

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "status": self.status.value,
            "message": self.message,
            "warnings": list(self.warnings),
            "duration_seconds": round((self.finished or time.time()) - self.started, 3),
            "data": _jsonable(self.data),
        }


class StageError(Exception):
    """Raised inside a stage to abort it with a specific status."""

    def __init__(self, message: str, status: Status = Status.PERMANENT_FAILURE):
        super().__init__(message)
        self.status = status


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)
