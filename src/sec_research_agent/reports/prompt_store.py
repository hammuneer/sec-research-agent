"""Persist user-edited prompts with a version history.

Defaults live in the package; this store only holds overrides, in a JSON file under the
(git-ignored) data directory::

    {"active": {"label": ..., "timestamp": ..., "report_generation": ..., "report_format": ...},
     "history": [<same shape>, ...]}
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from ..storage import write_atomic
from .prompts import PromptSet, default_prompts

logger = logging.getLogger(__name__)

MAX_HISTORY = 50


@dataclass(frozen=True)
class PromptVersion:
    """A saved prompt pair."""

    label: str
    timestamp: str
    report_generation: str
    report_format: str

    @property
    def prompts(self) -> PromptSet:
        """The prompt pair without metadata."""
        return PromptSet(self.report_generation, self.report_format)


class PromptStore:
    """Load and save prompt overrides."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()

    def _read(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8") or "{}")
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Ignoring unreadable prompt file %s: %s", self.path, exc)
            return {}
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _parse(entry: object) -> PromptVersion | None:
        if not isinstance(entry, dict):
            return None
        try:
            return PromptVersion(
                label=str(entry["label"]),
                timestamp=str(entry["timestamp"]),
                report_generation=str(entry["report_generation"]),
                report_format=str(entry["report_format"]),
            )
        except KeyError:
            return None

    def active_version(self) -> PromptVersion | None:
        """The saved version in use, or ``None`` when the defaults are active."""
        return self._parse(self._read().get("active"))

    def active(self) -> PromptSet:
        """Prompts to use for the next report (saved override or built-in defaults)."""
        version = self.active_version()
        return version.prompts if version else default_prompts()

    def history(self) -> list[PromptVersion]:
        """Saved versions, newest first."""
        entries = self._read().get("history", [])
        versions = [self._parse(e) for e in entries] if isinstance(entries, list) else []
        return [v for v in versions if v is not None]

    def save(self, prompts: PromptSet, label: str = "") -> PromptVersion:
        """Save ``prompts`` as a new active version."""
        now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        version = PromptVersion(
            label=label.strip() or f"Version {now}",
            timestamp=now,
            report_generation=prompts.report_generation,
            report_format=prompts.report_format,
        )
        with self._lock:
            history = [version, *self.history()][:MAX_HISTORY]
            self._write({"active": asdict(version), "history": [asdict(v) for v in history]})
        return version

    def activate(self, version: PromptVersion) -> None:
        """Make an existing history entry active."""
        with self._lock:
            data = self._read()
            data["active"] = asdict(version)
            self._write(data)

    def reset(self) -> None:
        """Go back to the built-in defaults (history is kept)."""
        with self._lock:
            data = self._read()
            data.pop("active", None)
            self._write(data)

    def _write(self, data: dict) -> None:
        write_atomic(self.path, json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8"))
