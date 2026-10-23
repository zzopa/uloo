# AI生成
"""Safe bounded Python code interpreter tool for Agent calculations and data processing."""

from __future__ import annotations

import collections
import contextlib
import datetime
import io
import itertools
import json
import math
import re
import statistics
import traceback
from typing import Any

# Allowed safe standard modules
_SAFE_MODULES: dict[str, Any] = {
    "math": math,
    "statistics": statistics,
    "datetime": datetime,
    "json": json,
    "re": re,
    "collections": collections,
    "itertools": itertools,
}

_FORBIDDEN_PATTERNS = [
    r"__subclasses__",
    r"__bases__",
    r"__globals__",
    r"__builtins__",
    r"importlib",
    r"\bos\b",
    r"\bsys\b",
    r"\bsubprocess\b",
    r"\bshutil\b",
    r"\bctypes\b",
    r"\bsocket\b",
    r"\bopen\s*\(",
]


def _safe_import(name: str, globals: dict[str, Any] | None = None, locals: dict[str, Any] | None = None, fromlist: tuple[str, ...] = (), level: int = 0) -> Any:
    if name in _SAFE_MODULES:
        return _SAFE_MODULES[name]
    raise ImportError(f"Import of module '{name}' is restricted in sandbox")


def code_interpreter(code: str) -> str:
    """Execute Python code in an isolated scope for numerical calculations, data processing, and analysis.

    Use this tool to calculate price variations, percentages, averages, sort items,
    or format structured data. Assign final output to variable `result` or print it.

    Args:
        code: Python source code snippet to execute. Must not exceed 10000 characters.

    Returns:
        JSON string containing execution status, printed stdout, and result value.
    """
    code = code.strip()
    if not code:
        return json.dumps({"status": "error", "message": "Code snippet is empty"}, ensure_ascii=False)

    if len(code) > 10000:
        return json.dumps({"status": "error", "message": "Code exceeds 10000 character limit"}, ensure_ascii=False)

    # Fast safety check for forbidden patterns
    for pat in _FORBIDDEN_PATTERNS:
        if re.search(pat, code):
            return json.dumps(
                {"status": "error", "message": f"Execution rejected: unsafe pattern detected ({pat})"},
                ensure_ascii=False,
            )

    safe_builtins = {
        "__import__": _safe_import,
        "abs": abs,
        "all": all,
        "any": any,
        "bin": bin,
        "bool": bool,
        "dict": dict,
        "divmod": divmod,
        "enumerate": enumerate,
        "filter": filter,
        "float": float,
        "format": format,
        "hex": hex,
        "int": int,
        "isinstance": isinstance,
        "issubclass": issubclass,
        "iter": iter,
        "len": len,
        "list": list,
        "map": map,
        "max": max,
        "min": min,
        "next": next,
        "oct": oct,
        "pow": pow,
        "print": print,
        "range": range,
        "reversed": reversed,
        "round": round,
        "set": set,
        "slice": slice,
        "sorted": sorted,
        "str": str,
        "sum": sum,
        "tuple": tuple,
        "zip": zip,
    }

    env_globals: dict[str, Any] = {
        "__builtins__": safe_builtins,
        **_SAFE_MODULES,
    }
    env_locals: dict[str, Any] = {}

    stdout_capture = io.StringIO()
    try:
        with contextlib.redirect_stdout(stdout_capture):
            exec(code, env_globals, env_locals)  # noqa: S102
    except Exception as exc:  # noqa: BLE001
        tb_lines = traceback.format_exception_only(type(exc), exc)
        return json.dumps(
            {
                "status": "error",
                "error_type": type(exc).__name__,
                "message": "".join(tb_lines).strip(),
                "stdout": stdout_capture.getvalue()[:2000],
            },
            ensure_ascii=False,
        )

    raw_result = env_locals.get("result")
    stdout_text = stdout_capture.getvalue()

    # Convert non-serializable objects safely
    try:
        json.dumps(raw_result)
        serialized_result = raw_result
    except (TypeError, OverflowError):
        serialized_result = str(raw_result) if raw_result is not None else None

    return json.dumps(
        {
            "status": "ok",
            "stdout": stdout_text[:5000],
            "result": serialized_result,
        },
        ensure_ascii=False,
    )
