# SPDX-FileCopyrightText: 2026 Wiro AI
# SPDX-License-Identifier: GPL-3.0-or-later
"""Finished runs, kept in a JSON file in the add-on's user folder.

The History panel reads it to import a model again, apply a texture again or
reuse a prompt, across Blender sessions. It holds file paths, prompts, model
names, task IDs and costs; never the API key. This module does not import bpy.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid


class History:
    def __init__(self, path: str, limit: int = 300) -> None:
        self.path = path
        self.limit = limit
        self._lock = threading.Lock()
        self.entries: list = []
        self.load()

    def load(self) -> None:
        try:
            with open(self.path, encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            data = []
        entries = data.get("entries") if isinstance(data, dict) else data
        self.entries = [e for e in entries or [] if isinstance(e, dict) and e.get("id")]

    def save(self) -> None:
        if not self.path:
            return
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        partial = self.path + ".part"
        with self._lock:
            payload = {"version": 1, "entries": self.entries[: self.limit]}
        with open(partial, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=1)
        os.replace(partial, self.path)

    def add(self, entry: dict) -> dict:
        entry = dict(entry)
        entry.setdefault("id", uuid.uuid4().hex[:12])
        entry.setdefault("time", time.time())
        with self._lock:
            self.entries.insert(0, entry)
            del self.entries[self.limit :]
        self.save()
        return entry

    def get(self, entry_id: str) -> dict | None:
        return next((e for e in self.entries if e.get("id") == entry_id), None)

    def remove(self, entry_id: str) -> None:
        with self._lock:
            self.entries = [e for e in self.entries if e.get("id") != entry_id]
        self.save()

    def clear(self) -> None:
        with self._lock:
            self.entries = []
        self.save()

    def total_cost(self) -> float:
        return round(sum(float(e.get("cost") or 0) for e in self.entries), 4)
