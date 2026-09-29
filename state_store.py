import json
import threading
from pathlib import Path
from typing import Any

HISTORY_LIMIT = 100


class StateStore:
    def __init__(self, path: Path, default_state: dict[str, Any]):
        self._path = path
        self._lock = threading.Lock()
        self._default_state = default_state
        self._state: dict[str, Any] = self._load()

    def _load(self) -> dict[str, Any]:
        if self._path.exists():
            try:
                loaded = json.loads(self._path.read_text(encoding="utf-8"))
                return {**self._default_state, **loaded}
            except (json.JSONDecodeError, OSError):
                pass
        return dict(self._default_state)

    def _save(self):
        self._path.write_text(
            json.dumps(self._state, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def get(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._state)

    def update(self, result: dict[str, Any], history_entry: dict[str, Any]):
        with self._lock:
            history = self._state.get("history", [])
            history.append(history_entry)
            self._state = {
                **self._default_state,
                **result,
                "history": history[-HISTORY_LIMIT:],
            }
            self._save()

    def record_error(self, message: str):
        with self._lock:
            self._state["error"] = message
            self._save()
