"""Fail CI when Mini Services expose raw exception text to user/log sinks."""
from __future__ import annotations

import ast
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_PREFIXES = (
    "audio_",
    "mini_services",
    "network_",
    "screen_share",
    "telephony",
)
TOP_PREFIXES = (
    "simpleoffice_mini_",
    "simpleoffice_network_",
    "simpleoffice_network_boot",
    "simpleoffice_service_lifecycle",
    "simpleoffice_sip_",
    "simpleoffice_firewall",
)
SINK_NAMES = {"audit", "flash", "jsonify", "print"}
LOG_METHODS = {"critical", "debug", "error", "exception", "info", "log", "warning"}
SAFE_EXCEPTION_WRAPPERS = {"error_detail", "type"}


def sources() -> list[Path]:
    result: list[Path] = []
    for path in (ROOT / "app").glob("*.py"):
        if path.stem.startswith(APP_PREFIXES):
            result.append(path)
    for path in ROOT.glob("*.py"):
        if path.stem.startswith(TOP_PREFIXES):
            result.append(path)
    return sorted(set(result))


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _raw_exception_reference(node: ast.AST, name: str) -> bool:
    if isinstance(node, ast.Call):
        called = _call_name(node.func)
        if called in SAFE_EXCEPTION_WRAPPERS:
            return False
        if called in {"str", "repr"} and any(
            isinstance(arg, ast.Name) and arg.id == name for arg in node.args
        ):
            return True
    if isinstance(node, ast.FormattedValue):
        return _raw_exception_reference(node.value, name)
    if isinstance(node, ast.Name):
        return node.id == name
    return any(
        _raw_exception_reference(child, name)
        for child in ast.iter_child_nodes(node)
    )


def findings(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    result: list[str] = []
    for handler in (node for node in ast.walk(tree) if isinstance(node, ast.ExceptHandler)):
        if not handler.name:
            continue
        exception_name = str(handler.name)
        for statement in handler.body:
            for call in (node for node in ast.walk(statement) if isinstance(node, ast.Call)):
                called = _call_name(call.func)
                if called not in SINK_NAMES and called not in LOG_METHODS:
                    continue
                values = [*call.args, *(keyword.value for keyword in call.keywords)]
                if any(_raw_exception_reference(value, exception_name) for value in values):
                    result.append(
                        f"{path.relative_to(ROOT)}:{getattr(call, 'lineno', 0)}: "
                        f"raw exception text reaches {called}"
                    )
    return result


def main() -> int:
    errors: list[str] = []
    checked = sources()
    for path in checked:
        errors.extend(findings(path))
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print(f"mini-services diagnostic audit passed for {len(checked)} source files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
