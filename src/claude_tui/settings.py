"""User settings loaded from ~/.config/claude-tui/settings.json."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

SETTINGS_PATH = Path.home() / ".config" / "claude-tui" / "settings.json"


@dataclass
class Settings:
    excluded_projects: list[str] = field(default_factory=list)

    @staticmethod
    def load() -> Settings:
        if not SETTINGS_PATH.exists():
            return Settings()
        try:
            data = json.loads(SETTINGS_PATH.read_text())
            return Settings(
                excluded_projects=data.get("excluded_projects", []),
            )
        except (json.JSONDecodeError, OSError):
            return Settings()
