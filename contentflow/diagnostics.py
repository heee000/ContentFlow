"""Bounded exception diagnostics without messages, SQL, URLs or local values.

Redacting a few secret-looking keys is insufficient for arbitrary exceptions.
Keep code coordinates and exception types, never format the original traceback.
"""

from collections import deque
import json
import logging
import re
import sys


def _identifier(value: object) -> str:
    return value if isinstance(value, str) and re.fullmatch(r"[\w.<>-]{1,160}", value) else "unknown"


def exception_type(error: BaseException) -> str:
    return _identifier(type(error).__name__)


def exception_diagnostic(error: BaseException | None) -> dict:
    pending = deque([error] if error is not None else [])
    seen: set[int] = set()
    exceptions = []
    while pending and len(exceptions) < 8:
        current = pending.popleft()
        if id(current) in seen:
            continue
        seen.add(id(current))
        frames: deque = deque(maxlen=32)
        trace = current.__traceback__
        while trace is not None:
            code = trace.tb_frame.f_code
            frames.append({
                "module": _identifier(trace.tb_frame.f_globals.get("__name__")),
                "function": _identifier(code.co_name),
                "line": trace.tb_lineno,
            })
            trace = trace.tb_next
        exceptions.append({"type": exception_type(current), "frames": list(frames)})
        if isinstance(current, BaseExceptionGroup):
            pending.extend(current.exceptions[:8])
        if current.__cause__ is not None:
            pending.append(current.__cause__)
        elif not current.__suppress_context__ and current.__context__ is not None:
            pending.append(current.__context__)
    return {"version": 1, "exceptions": exceptions, "truncated": bool(pending)}


def log_exception(logger: logging.Logger, message: str, *args) -> None:
    """Use only static messages/internal identifiers; call inside an except block."""
    logger.error(json.dumps({
        "event": "application.exception",
        "message": message % args if args else message,
        "diagnostic": exception_diagnostic(sys.exception()),
    }, ensure_ascii=False))


def safe_error_receipt(error: BaseException) -> str:
    return f"Task failed ({exception_type(error)}); use the task ID to inspect safe diagnostics"
