"""Safe bounded Python code interpreter tool for Agent calculations and data processing."""

from __future__ import annotations

import ast
import collections
import concurrent.futures
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
    r"__class__",
    r"__mro__",
    r"__dict__",
    r"__code__",
    r"importlib",
    r"\bos\b",
    r"\bsys\b",
    r"\bsubprocess\b",
    r"\bshutil\b",
    r"\bctypes\b",
    r"\bsocket\b",
    r"\bopen\s*\(",
    r"\beval\s*\(",
    r"\bexec\s*\(",
    r"\bcompile\s*\(",
    r"\bgetattr\s*\(",
]

_FORBIDDEN_CALL_NAMES = frozenset({
    "open",
    "eval",
    "exec",
    "compile",
    "getattr",
    "setattr",
    "delattr",
    "globals",
    "locals",
    "vars",
    "dir",
    "type",
    "input",
    "help",
    "breakpoint",
    "__import__",
})

_EXEC_TIMEOUT_SECONDS = 5.0


def _validate_ast_safety(code: str) -> str | None:
    """Inspect the Python AST to block attribute traversal, unsafe imports, and dangerous calls."""
    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError:
        # Let standard exec report syntax details consistently
        return None

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root_mod = alias.name.split(".")[0]
                if root_mod not in _SAFE_MODULES:
                    return f"unauthorized import '{alias.name}'"
        elif isinstance(node, ast.ImportFrom):
            root_mod = (node.module or "").split(".")[0]
            if root_mod not in _SAFE_MODULES:
                return f"unauthorized import from '{node.module}'"
        elif isinstance(node, ast.Attribute):
            if node.attr.startswith("_"):
                return f"private/dunder attribute access '{node.attr}'"
        elif isinstance(node, ast.Name):
            if node.id.startswith("__") and node.id.endswith("__"):
                return f"dunder identifier '{node.id}'"
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in _FORBIDDEN_CALL_NAMES:
                return f"forbidden builtin call '{node.func.id}()'"
    return None


def _safe_import(
    name: str,
    globals: dict[str, Any] | None = None,
    locals: dict[str, Any] | None = None,
    fromlist: tuple[str, ...] = (),
    level: int = 0,
) -> Any:
    if name in _SAFE_MODULES:
        return _SAFE_MODULES[name]
    raise ImportError(f"Import of module '{name}' is restricted in sandbox")


def _execute_in_sandbox(code: str) -> tuple[str, Any]:
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
    with contextlib.redirect_stdout(stdout_capture):
        exec(code, env_globals, env_locals)  # noqa: S102
    return stdout_capture.getvalue(), env_locals.get("result")


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

    # 1. Fast regex safety check for forbidden patterns
    for pat in _FORBIDDEN_PATTERNS:
        if re.search(pat, code):
            return json.dumps(
                {"status": "error", "message": f"Execution rejected: unsafe pattern detected ({pat})"},
                ensure_ascii=False,
            )

    # 2. Structural AST safety check (prevents string concatenation / attribute traversal bypasses)
    ast_violation = _validate_ast_safety(code)
    if ast_violation:
        return json.dumps(
            {"status": "error", "message": f"Execution rejected: unsafe pattern detected ({ast_violation})"},
            ensure_ascii=False,
        )

    # 3. Bounded-time execution with timeout guard
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(_execute_in_sandbox, code)
            stdout_text, raw_result = future.result(timeout=_EXEC_TIMEOUT_SECONDS)
    except concurrent.futures.TimeoutError:
        return json.dumps(
            {
                "status": "error",
                "error_type": "TimeoutError",
                "message": f"Code execution timed out after {_EXEC_TIMEOUT_SECONDS}s",
            },
            ensure_ascii=False,
        )
    except Exception as exc:  # noqa: BLE001
        tb_lines = traceback.format_exception_only(type(exc), exc)
        return json.dumps(
            {
                "status": "error",
                "error_type": type(exc).__name__,
                "message": "".join(tb_lines).strip(),
                "stdout": "",
            },
            ensure_ascii=False,
        )

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
