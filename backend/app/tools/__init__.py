"""Инструменты агента. Каждый вызов регистрируется в журнале текущего запроса —
так видно, что при правке перезапрошены только затронутые компоненты."""
from __future__ import annotations

import contextvars
import functools
import time

_calls: contextvars.ContextVar[list | None] = contextvars.ContextVar("tool_calls", default=None)


def start_tool_log() -> list:
    log: list = []
    _calls.set(log)
    return log


def tool(name: str):
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            t0 = time.perf_counter()
            try:
                return fn(*args, **kwargs)
            finally:
                log = _calls.get()
                if log is not None:
                    log.append({"tool": name, "ms": round((time.perf_counter() - t0) * 1000, 1)})
        wrapper.tool_name = name
        return wrapper
    return deco
