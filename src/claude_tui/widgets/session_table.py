"""DataTable widget for displaying Claude Code sessions with color-coded statuses."""

from __future__ import annotations

import time

from textual.widgets import DataTable

from claude_tui.models import SessionInfo, SessionStatus


STATUS_DISPLAY = {
    SessionStatus.WORKING: ("WORKING", "green bold"),
    SessionStatus.WAITING_INPUT: ("WAITING", "yellow"),
    SessionStatus.PERMISSION_NEEDED: ("PERMISSION", "red bold"),
    SessionStatus.UNKNOWN: ("UNKNOWN", "dim"),
}

COLUMNS = ("#", "Status", "Project", "Mode", "Last Tool", "Last Event", "Age")


def _format_age(timestamp: float) -> str:
    """Format seconds since timestamp as human-readable age."""
    delta = time.time() - timestamp
    if delta < 60:
        return f"{int(delta)}s"
    if delta < 3600:
        return f"{int(delta / 60)}m"
    return f"{int(delta / 3600)}h"


class SessionTable(DataTable):
    """DataTable showing Claude Code sessions."""

    BINDINGS = [
        ("j", "cursor_down", "Down"),
        ("k", "cursor_up", "Up"),
    ]

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.cursor_type = "row"
        self.zebra_stripes = True
        self._session_rows: list[SessionInfo] = []

    def on_mount(self) -> None:
        for col in COLUMNS:
            self.add_column(col, key=col.lower())

    def refresh_sessions(self, sessions: list[SessionInfo]) -> None:
        """Update the table with current session data."""
        self._session_rows = sessions
        self.clear()
        for session in sessions:
            label, style = STATUS_DISPLAY.get(session.status, ("?", "dim"))
            styled_status = f"[{style}]{label}[/]"
            mode = session.permission_mode or "-"
            last_tool = session.last_tool or "-"
            last_event = session.last_event or "-"
            age = _format_age(session.last_event_time) if session.last_event else "-"

            win_num = str(session.tmux_window) if session.tmux_window is not None else "-"

            self.add_row(
                win_num,
                styled_status,
                session.project_name or session.session_id[:12],
                mode,
                last_tool,
                last_event,
                age,
                key=session.session_id,
            )

    def get_selected_session(self) -> SessionInfo | None:
        """Get the SessionInfo for the currently highlighted row."""
        if self.cursor_row is not None and 0 <= self.cursor_row < len(self._session_rows):
            return self._session_rows[self.cursor_row]
        return None
