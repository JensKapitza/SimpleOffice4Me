"""Persistence for external network-audio receiver definitions."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from .audio_receiver_config import normalize_receivers, receiver_capabilities


class AudioReceiverStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "audio-receivers.json"

    def load(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        try:
            receivers = normalize_receivers(payload)
        except (ValueError, TypeError):
            return []
        return [self._with_capabilities(item) for item in receivers]

    def save(self, receivers: Any) -> list[dict[str, Any]]:
        clean = normalize_receivers(receivers)
        self._atomic_write(json.dumps(clean, ensure_ascii=False, indent=2) + "\n")
        return [self._with_capabilities(item) for item in clean]

    def upsert(self, receiver: dict[str, Any]) -> dict[str, Any]:
        current = self.load()
        raw = [{key: value for key, value in item.items() if key != "capabilities"} for item in current]
        incoming = normalize_receivers([receiver])[0]
        by_id = {str(item["receiver_id"]): item for item in raw}
        by_id[str(incoming["receiver_id"])] = incoming
        saved = self.save(list(by_id.values()))
        return next(item for item in saved if item["receiver_id"] == incoming["receiver_id"])

    def delete(self, receiver_id: str) -> bool:
        current = self.load()
        raw = [{key: value for key, value in item.items() if key != "capabilities"} for item in current]
        remaining = [item for item in raw if item.get("receiver_id") != receiver_id]
        changed = len(remaining) != len(raw)
        if changed:
            self.save(remaining)
        return changed

    @staticmethod
    def _with_capabilities(receiver: dict[str, Any]) -> dict[str, Any]:
        result = dict(receiver)
        result["capabilities"] = receiver_capabilities(str(receiver.get("kind") or ""))
        return result

    def _atomic_write(self, content: str) -> None:
        descriptor, temporary = tempfile.mkstemp(prefix="audio-receivers-", suffix=".tmp", dir=self.root)
        temp_path = Path(temporary)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, self.path)
        finally:
            temp_path.unlink(missing_ok=True)
