"""Small file-based to-do list with attributable changes."""
from __future__ import annotations
import json
import uuid
from pathlib import Path
from typing import Any
from .document_store import CONTROL_DIR, atomic_json_write, utc_now
from .revision_history import RevisionHistory

class TodoStore:
    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve(); self.path = self.root / CONTROL_DIR / "todo.json"; self.history = RevisionHistory(self.root)
    def items(self) -> list[dict[str, Any]]:
        return sorted(self._read().get("items", []), key=lambda item: (item.get("done", False), item.get("created_at", "")))
    def add(self, title: str, actor: str) -> None:
        if not title.strip() or not actor.strip(): raise ValueError("to-do title and user are required")
        data=self._read(); item={"id":str(uuid.uuid4()),"title":title.strip(),"done":False,"created_at":utc_now(),"created_by":actor}; data["items"].append(item); atomic_json_write(self.path,data); self.history.record("todo_created",actor,"todo",item["id"],item)
    def toggle(self, item_id: str, actor: str) -> None:
        data=self._read(); item=next((x for x in data["items"] if x.get("id")==item_id),None)
        if item is None: raise ValueError("unknown to-do")
        item["done"] = not item.get("done",False); item["updated_at"]=utc_now(); item["updated_by"]=actor; atomic_json_write(self.path,data); self.history.record("todo_toggled",actor,"todo",item_id,item)
    def _read(self)->dict[str,Any]:
        try:
            data=json.loads(self.path.read_text(encoding="utf-8")); return data if isinstance(data,dict) else {"items":[]}
        except (OSError,json.JSONDecodeError): return {"items":[]}
