# AI生成
"""Unit tests for Python code interpreter sandbox tool."""

import json

from uloo.runtime.code_interpreter import code_interpreter
from uloo.runtime.tools import ToolRegistry


def test_code_interpreter_basic_arithmetic():
    output = json.loads(code_interpreter("result = 12 * 8 + 4"))
    assert output["status"] == "ok"
    assert output["result"] == 100


def test_code_interpreter_stdout():
    output = json.loads(code_interpreter('print("Calculated output: 42")\nresult = 42'))
    assert output["status"] == "ok"
    assert "Calculated output: 42" in output["stdout"]
    assert output["result"] == 42


def test_code_interpreter_statistics_and_math():
    code = """
import math
import statistics

prices = [100.5, 102.0, 98.5, 105.0]
avg = statistics.mean(prices)
result = round(avg, 2)
"""
    output = json.loads(code_interpreter(code))
    assert output["status"] == "ok"
    assert output["result"] == 101.5


def test_code_interpreter_catches_runtime_error():
    output = json.loads(code_interpreter("result = 10 / 0"))
    assert output["status"] == "error"
    assert "ZeroDivisionError" in output["message"] or output["error_type"] == "ZeroDivisionError"


def test_code_interpreter_blocks_unsafe_imports():
    output = json.loads(code_interpreter("import os\nos.system('echo hi')"))
    assert output["status"] == "error"
    assert "unsafe pattern" in output["message"]


def test_code_interpreter_blocks_file_open():
    output = json.loads(code_interpreter("f = open('secret.txt', 'w')"))
    assert output["status"] == "error"
    assert "unsafe pattern" in output["message"]


def test_tool_registry_resolves_code_interpreter():
    registry = ToolRegistry()
    assert "code-interpreter" in registry.available_refs()
    resolved = registry.resolve_many(["code-interpreter", "web-search"])
    assert len(resolved) == 2
    assert resolved[0] == code_interpreter


def test_code_interpreter_blocks_ast_dunder_and_private_traversal():
    output = json.loads(code_interpreter("import re\nx = re._compiler"))
    assert output["status"] == "error"
    assert "unsafe pattern" in output["message"]


def test_code_interpreter_blocks_dangerous_builtin_calls():
    output = json.loads(code_interpreter("result = eval('1 + 1')"))
    assert output["status"] == "error"
    assert "unsafe pattern" in output["message"]
