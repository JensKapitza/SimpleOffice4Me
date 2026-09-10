from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _assignment(module: ast.Module, name: str) -> ast.AST:
    for statement in module.body:
        if isinstance(statement, ast.Assign):
            for target in statement.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return statement.value
    raise AssertionError(f"missing assignment {name}")


def _setup_call(module: ast.Module) -> ast.Call:
    for statement in module.body:
        if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call):
            call = statement.value
            if isinstance(call.func, ast.Name) and call.func.id == "setup":
                return call
    raise AssertionError("setup() call missing")


def test_legacy_setup_has_real_project_metadata_and_runtime_dependencies():
    tree = ast.parse((ROOT / "setup.py").read_text(encoding="utf-8"))
    dependencies = ast.literal_eval(_assignment(tree, "RUNTIME_DEPENDENCIES"))
    call = _setup_call(tree)
    keywords = {keyword.arg: keyword.value for keyword in call.keywords if keyword.arg}

    assert ast.literal_eval(keywords["name"]) == "simpleoffice4me"
    assert ast.literal_eval(keywords["version"]) == "1.0.0"
    assert "argon2-cffi>=23.1,<26" in dependencies
    assert "Flask>=3.0,<4" in dependencies
    assert "cryptography>=48.0.1,<51" in dependencies
    assert isinstance(keywords["install_requires"], ast.Name)
    assert keywords["install_requires"].id == "RUNTIME_DEPENDENCIES"
