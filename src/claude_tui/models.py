from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum


class SessionStatus(Enum):
    WORKING = "working"
    WAITING_INPUT = "waiting"
    PERMISSION_NEEDED = "permission"
    UNKNOWN = "unknown"


@dataclass
class SessionInfo:
    session_id: str
    project_path: str = ""
    project_name: str = ""
    status: SessionStatus = SessionStatus.UNKNOWN
    permission_mode: str = ""
    cwd: str = ""
    shell_pid: int | None = None
    tmux_session: str | None = None
    tmux_window: int | None = None
    tmux_pane: int | None = None
    last_tool: str = ""
    last_event: str = ""
    last_event_time: float = field(default_factory=time.time)
    notification_msg: str = ""
    git_branch: str = ""
    last_message: str = ""
    is_active_window: bool = False

    def __post_init__(self) -> None:
        if self.project_path and not self.project_name:
            self.project_name = self.project_path.rstrip("/").rsplit("/", 1)[-1]


@dataclass
class RecentConversation:
    session_id: str
    project_path: str
    project_name: str
    first_message: str      # first user prompt, truncated to ~120 chars
    mtime: float            # file modification time (for sorting + age display)
