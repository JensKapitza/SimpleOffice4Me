"""Declarative, permission-aware automation rules for SimpleOffice 3.0."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import sqlite3
import uuid
from typing import Any, Callable, Mapping

from .sqlite_utils import connect as sqlite_connect


OPERATORS = {"eq", "ne", "in", "contains", "gt", "gte", "lt", "lte", "exists"}
TRIGGERS = {"event", "schedule", "check"}
MAX_ACTIONS = 20
MAX_CONDITIONS = 40
MAX_DEPTH = 8
LOG = logging.getLogger(__name__)


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _json(value: object, *, limit: int = 64 * 1024) -> str:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if len(text.encode("utf-8")) > limit:
        raise ValueError("automation payload is too large")
    return text


def _object(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return dict(value)


def _list(value: object, label: str, maximum: int) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > maximum:
        raise ValueError(f"{label} must be a bounded list")
    result = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError(f"{label} entries must be objects")
        result.append(dict(item))
    return result


def _path(value: Mapping[str, Any], dotted: str) -> tuple[bool, Any]:
    current: Any = value
    for part in str(dotted).split("."):
        if not part or not isinstance(current, Mapping) or part not in current:
            return False, None
        current = current[part]
    return True, current


def _condition_matches(condition: Mapping[str, Any], context: Mapping[str, Any]) -> bool:
    field = str(condition.get("field", "")).strip()
    operator = str(condition.get("op", "eq")).strip()
    if not field or operator not in OPERATORS:
        raise ValueError("invalid automation condition")
    exists, actual = _path(context, field)
    expected = condition.get("value")
    if operator == "exists":
        return exists is bool(expected if "value" in condition else True)
    if not exists:
        return False
    if operator == "eq":
        return actual == expected
    if operator == "ne":
        return actual != expected
    if operator == "in":
        return isinstance(expected, (list, tuple, set)) and actual in expected
    if operator == "contains":
        return isinstance(actual, (str, list, tuple, set, dict)) and expected in actual
    try:
        if operator == "gt":
            return actual > expected
        if operator == "gte":
            return actual >= expected
        if operator == "lt":
            return actual < expected
        if operator == "lte":
            return actual <= expected
    except TypeError:
        return False
    return False


@dataclass(frozen=True)
class AutomationRule:
    rule_id: str
    name: str
    enabled: bool
    owner: str
    scope: dict[str, Any]
    trigger: dict[str, Any]
    conditions: list[dict[str, Any]]
    actions: list[dict[str, Any]]
    version: int
    last_run_at: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class ExecutionResult:
    status: str
    matched: bool
    dry_run: bool
    actions: tuple[dict[str, Any], ...]
    error: str = ""


ActionHandler = Callable[[str, Mapping[str, Any], Mapping[str, Any]], object]
ActionAuthorizer = Callable[[str, Mapping[str, Any]], bool]


class ActionRegistry:
    def __init__(self):
        self._handlers: dict[str, ActionHandler] = {}
        self._authorizers: dict[str, ActionAuthorizer] = {}

    def register(
        self,
        name: str,
        handler: ActionHandler,
        *,
        authorizer: ActionAuthorizer | None = None,
    ) -> None:
        key = str(name).strip()
        if not key or key in self._handlers:
            raise ValueError("invalid or duplicate automation action")
        self._handlers[key] = handler
        self._authorizers[key] = authorizer or (lambda _principal, _payload: False)

    def known(self, name: str) -> bool:
        return str(name) in self._handlers

    def allowed(self, principal: str, name: str, payload: Mapping[str, Any]) -> bool:
        check = self._authorizers.get(str(name))
        if check is None:
            return False
        try:
            return bool(check(principal, payload))
        except Exception as exc:
            LOG.warning("automation authorizer failed action=%s type=%s", name, type(exc).__name__)
            return False

    def invoke(
        self,
        principal: str,
        name: str,
        payload: Mapping[str, Any],
        context: Mapping[str, Any],
    ) -> object:
        handler = self._handlers.get(str(name))
        if handler is None:
            raise ValueError("unknown automation action")
        if not self.allowed(principal, name, payload):
            raise PermissionError("automation action denied")
        return handler(principal, payload, context)


class AutomationStore:
    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.path = self.root / ".simpleoffice-meta" / "v3-automation.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _db(self):
        db = sqlite_connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        return db

    def _initialize(self) -> None:
        with self._db() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS v3_automation_rule(
                    rule_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 0,
                    owner TEXT NOT NULL,
                    scope_json TEXT NOT NULL DEFAULT '{}',
                    trigger_json TEXT NOT NULL,
                    conditions_json TEXT NOT NULL DEFAULT '[]',
                    actions_json TEXT NOT NULL DEFAULT '[]',
                    version INTEGER NOT NULL DEFAULT 1,
                    last_run_at TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS ix_v3_automation_owner
                    ON v3_automation_rule(owner, enabled, updated_at);
                CREATE TABLE IF NOT EXISTS v3_automation_execution(
                    execution_id TEXT PRIMARY KEY,
                    rule_id TEXT NOT NULL,
                    rule_version INTEGER NOT NULL,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    correlation_id TEXT NOT NULL,
                    principal TEXT NOT NULL,
                    status TEXT NOT NULL,
                    matched INTEGER NOT NULL,
                    dry_run INTEGER NOT NULL,
                    detail_json TEXT NOT NULL DEFAULT '[]',
                    error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    finished_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS ix_v3_automation_execution_rule
                    ON v3_automation_execution(rule_id, created_at DESC);
                """
            )

    @staticmethod
    def _record(row: sqlite3.Row) -> AutomationRule:
        return AutomationRule(
            rule_id=str(row["rule_id"]),
            name=str(row["name"]),
            enabled=bool(row["enabled"]),
            owner=str(row["owner"]),
            scope=json.loads(row["scope_json"] or "{}"),
            trigger=json.loads(row["trigger_json"] or "{}"),
            conditions=json.loads(row["conditions_json"] or "[]"),
            actions=json.loads(row["actions_json"] or "[]"),
            version=int(row["version"]),
            last_run_at=str(row["last_run_at"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    @staticmethod
    def _validated(values: Mapping[str, Any]) -> tuple[dict, dict, list[dict], list[dict]]:
        scope = _object(values.get("scope", {}), "scope")
        trigger = _object(values.get("trigger", {}), "trigger")
        if str(trigger.get("type", "")).strip() not in TRIGGERS:
            raise ValueError("unsupported automation trigger")
        conditions = _list(values.get("conditions", []), "conditions", MAX_CONDITIONS)
        actions = _list(values.get("actions", []), "actions", MAX_ACTIONS)
        if not actions:
            raise ValueError("automation requires at least one action")
        for condition in conditions:
            if str(condition.get("op", "eq")) not in OPERATORS:
                raise ValueError("unsupported automation condition")
        for action in actions:
            if not str(action.get("type", "")).strip():
                raise ValueError("automation action type is required")
        _json(scope); _json(trigger); _json(conditions); _json(actions)
        return scope, trigger, conditions, actions

    def create(self, owner: str, values: Mapping[str, Any]) -> AutomationRule:
        principal = str(owner).strip()
        name = str(values.get("name", "")).strip()[:200]
        if not principal or not name:
            raise ValueError("automation owner and name are required")
        scope, trigger, conditions, actions = self._validated(values)
        now = _utc()
        rule_id = uuid.uuid4().hex
        with self._db() as db:
            db.execute(
                """INSERT INTO v3_automation_rule(
                    rule_id,name,enabled,owner,scope_json,trigger_json,
                    conditions_json,actions_json,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    rule_id, name, int(bool(values.get("enabled", False))), principal,
                    _json(scope), _json(trigger), _json(conditions), _json(actions), now, now,
                ),
            )
        return self.get(rule_id, principal)

    def get(self, rule_id: str, principal: str = "") -> AutomationRule:
        with self._db() as db:
            row = db.execute(
                "SELECT * FROM v3_automation_rule WHERE rule_id=?",
                (str(rule_id),),
            ).fetchone()
        if row is None or (principal and str(row["owner"]) != str(principal)):
            raise LookupError("automation rule not found")
        return self._record(row)

    def list(self, principal: str, *, enabled_only: bool = False) -> list[AutomationRule]:
        with self._db() as db:
            rows = db.execute(
                """SELECT * FROM v3_automation_rule
                   WHERE owner=? AND (?=0 OR enabled=1)
                   ORDER BY updated_at DESC, rule_id DESC LIMIT 500""",
                (str(principal), int(enabled_only)),
            ).fetchall()
        return [self._record(row) for row in rows]

    def update(self, rule_id: str, principal: str, values: Mapping[str, Any]) -> AutomationRule:
        current = self.get(rule_id, principal)
        merged = {
            "name": values.get("name", current.name),
            "enabled": values.get("enabled", current.enabled),
            "scope": values.get("scope", current.scope),
            "trigger": values.get("trigger", current.trigger),
            "conditions": values.get("conditions", current.conditions),
            "actions": values.get("actions", current.actions),
        }
        scope, trigger, conditions, actions = self._validated(merged)
        now = _utc()
        with self._db() as db:
            db.execute(
                """UPDATE v3_automation_rule SET name=?,enabled=?,scope_json=?,trigger_json=?,
                   conditions_json=?,actions_json=?,version=version+1,updated_at=?
                   WHERE rule_id=? AND owner=?""",
                (
                    str(merged["name"]).strip()[:200], int(bool(merged["enabled"])),
                    _json(scope), _json(trigger), _json(conditions), _json(actions),
                    now, rule_id, principal,
                ),
            )
        return self.get(rule_id, principal)

    def mark_run(self, rule_id: str, when: str) -> None:
        with self._db() as db:
            db.execute(
                "UPDATE v3_automation_rule SET last_run_at=?,updated_at=? WHERE rule_id=?",
                (when, when, rule_id),
            )

    def execution(self, idempotency_key: str) -> sqlite3.Row | None:
        with self._db() as db:
            return db.execute(
                "SELECT * FROM v3_automation_execution WHERE idempotency_key=?",
                (idempotency_key,),
            ).fetchone()

    def record_execution(
        self,
        rule: AutomationRule,
        *,
        key: str,
        correlation_id: str,
        principal: str,
        result: ExecutionResult,
    ) -> None:
        now = _utc()
        with self._db() as db:
            db.execute(
                """INSERT OR IGNORE INTO v3_automation_execution(
                    execution_id,rule_id,rule_version,idempotency_key,correlation_id,
                    principal,status,matched,dry_run,detail_json,error,created_at,finished_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    uuid.uuid4().hex, rule.rule_id, rule.version, key, correlation_id,
                    principal, result.status, int(result.matched), int(result.dry_run),
                    _json(list(result.actions)), str(result.error)[:1000], now, now,
                ),
            )

    def logs(self, principal: str, *, limit: int = 100) -> list[dict[str, Any]]:
        size = max(1, min(500, int(limit)))
        with self._db() as db:
            rows = db.execute(
                """SELECT e.* FROM v3_automation_execution e
                   JOIN v3_automation_rule r ON r.rule_id=e.rule_id
                   WHERE r.owner=? ORDER BY e.created_at DESC LIMIT ?""",
                (principal, size),
            ).fetchall()
        return [dict(row) for row in rows]


class AutomationEngine:
    def __init__(self, store: AutomationStore, actions: ActionRegistry):
        self.store = store
        self.actions = actions

    @staticmethod
    def _trigger_matches(trigger: Mapping[str, Any], trigger_type: str, event: Mapping[str, Any]) -> bool:
        if str(trigger.get("type", "")) != str(trigger_type):
            return False
        expected = str(trigger.get("name", "")).strip()
        return not expected or expected == str(event.get("name", "")).strip()

    def execute(
        self,
        rule: AutomationRule,
        event: Mapping[str, Any],
        *,
        principal: str,
        trigger_type: str = "event",
        dry_run: bool = False,
        correlation_id: str = "",
        depth: int = 0,
    ) -> ExecutionResult:
        if depth >= MAX_DEPTH:
            return ExecutionResult("blocked", False, dry_run, (), "automation recursion limit reached")
        if not rule.enabled and not dry_run:
            return ExecutionResult("disabled", False, dry_run, ())
        if rule.owner != principal:
            return ExecutionResult("denied", False, dry_run, (), "rule principal mismatch")
        matched = self._trigger_matches(rule.trigger, trigger_type, event)
        context = {"event": dict(event), "scope": rule.scope}
        if matched:
            matched = all(_condition_matches(item, context) for item in rule.conditions)
        correlation = str(correlation_id).strip()[:200] or uuid.uuid4().hex
        fingerprint = str(event.get("id") or event.get("key") or event.get("name") or "event")[:200]
        key = f"{rule.rule_id}:{rule.version}:{correlation}:{fingerprint}"
        if not dry_run and self.store.execution(key) is not None:
            return ExecutionResult("duplicate", matched, False, ())
        if not matched:
            result = ExecutionResult("not_matched", False, dry_run, ())
            if not dry_run:
                self.store.record_execution(rule, key=key, correlation_id=correlation, principal=principal, result=result)
            return result

        planned: list[dict[str, Any]] = []
        for action in rule.actions:
            action_type = str(action.get("type", "")).strip()
            payload = _object(action.get("payload", {}), "action payload")
            if not self.actions.known(action_type):
                result = ExecutionResult("failed", True, dry_run, tuple(planned), "unknown automation action")
                if not dry_run:
                    self.store.record_execution(rule, key=key, correlation_id=correlation, principal=principal, result=result)
                return result
            if not self.actions.allowed(principal, action_type, payload):
                result = ExecutionResult("denied", True, dry_run, tuple(planned), "automation action denied")
                if not dry_run:
                    self.store.record_execution(rule, key=key, correlation_id=correlation, principal=principal, result=result)
                return result
            planned.append({"type": action_type, "payload": payload})
            if dry_run:
                continue
            try:
                self.actions.invoke(principal, action_type, payload, context)
            except Exception as exc:
                LOG.warning("automation action failed action=%s type=%s", action_type, type(exc).__name__)
                result = ExecutionResult("failed", True, False, tuple(planned), "automation action failed")
                self.store.record_execution(rule, key=key, correlation_id=correlation, principal=principal, result=result)
                return result

        result = ExecutionResult("dry_run" if dry_run else "succeeded", True, dry_run, tuple(planned))
        if not dry_run:
            self.store.record_execution(rule, key=key, correlation_id=correlation, principal=principal, result=result)
            self.store.mark_run(rule.rule_id, _utc())
        return result

    def dispatch(
        self,
        trigger_type: str,
        event: Mapping[str, Any],
        *,
        principal: str,
        correlation_id: str = "",
    ) -> list[tuple[str, ExecutionResult]]:
        if trigger_type not in TRIGGERS:
            raise ValueError("unsupported automation trigger")
        rows = []
        for rule in self.store.list(principal, enabled_only=True):
            if self._trigger_matches(rule.trigger, trigger_type, event):
                rows.append(
                    (
                        rule.rule_id,
                        self.execute(
                            rule, event, principal=principal, trigger_type=trigger_type,
                            correlation_id=correlation_id,
                        ),
                    )
                )
        return rows


def default_action_registry(root: str | Path) -> ActionRegistry:
    registry = ActionRegistry()

    def task_allowed(principal: str, payload: Mapping[str, Any]) -> bool:
        return bool(principal.strip() and str(payload.get("title", "")).strip())

    def task_create(principal: str, payload: Mapping[str, Any], _context: Mapping[str, Any]):
        from .todo_store import TodoStore
        values = _object(payload.get("values", {}), "task values")
        return TodoStore(root).add(str(payload.get("title", "")), principal, values)

    registry.register("task.create", task_create, authorizer=task_allowed)
    return registry
