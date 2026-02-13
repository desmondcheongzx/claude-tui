"""SessionStore: in-memory state, merges hook events with tmux discovery."""

from __future__ import annotations

import time
from typing import Any, Callable

from claude_tui.models import SessionInfo, SessionStatus
from claude_tui.tmux import get_active_windows, list_claude_windows, list_panes, match_pid_to_window
from claude_tui.transcript import get_last_message


class SessionStore:
    def __init__(self, on_change: Callable[[], None] | None = None) -> None:
        self._sessions: dict[str, SessionInfo] = {}
        self._pid_to_session: dict[int, str] = {}
        self._on_change = on_change

    @property
    def sessions(self) -> dict[str, SessionInfo]:
        return self._sessions

    def get_sorted_sessions(self) -> list[SessionInfo]:
        """Return sessions sorted by tmux window number (stable order)."""
        return sorted(
            self._sessions.values(),
            key=lambda s: (s.tmux_window if s.tmux_window is not None else 9999, s.session_id),
        )

    def _notify(self) -> None:
        if self._on_change:
            self._on_change()

    def handle_hook_event(self, data: dict[str, Any]) -> None:
        """Process an incoming hook event from cc-hook.sh."""
        event = data.get("hook_event_name", "")
        session_id = data.get("session_id", "")
        if not session_id:
            return

        shell_pid = data.get("shell_pid")
        if shell_pid is not None:
            shell_pid = int(shell_pid)
            self._pid_to_session[shell_pid] = session_id

        if event == "SessionStart":
            self._handle_session_start(session_id, data, shell_pid)
        elif event == "UserPromptSubmit":
            self._handle_user_prompt_submit(session_id, data, shell_pid)
        elif event == "PostToolUse":
            self._handle_post_tool_use(session_id, data, shell_pid)
        elif event == "Notification":
            self._handle_notification(session_id, data, shell_pid)
        elif event == "Stop":
            self._handle_stop(session_id, data, shell_pid)
        elif event == "SessionEnd":
            self._handle_session_end(session_id)
        else:
            # Unknown event, just update last_event
            if session_id in self._sessions:
                s = self._sessions[session_id]
                s.last_event = event
                s.last_event_time = time.time()
                self._notify()

    def _find_existing_by_pid(self, shell_pid: int) -> str | None:
        """Find an existing session key that matches a shell_pid."""
        for key, s in self._sessions.items():
            if s.shell_pid == shell_pid:
                return key
        return None

    def _get_or_create(self, session_id: str, data: dict[str, Any], shell_pid: int | None = None) -> SessionInfo:
        if session_id not in self._sessions:
            # Check if a tmux-discovered session exists for the same PID;
            # if so, upgrade it to the real session_id instead of creating a duplicate
            existing_key = self._find_existing_by_pid(shell_pid) if shell_pid else None
            if existing_key and existing_key != session_id:
                session = self._sessions.pop(existing_key)
                session.session_id = session_id
                self._sessions[session_id] = session
            else:
                self._sessions[session_id] = SessionInfo(
                    session_id=session_id,
                    project_path=data.get("cwd", ""),
                    cwd=data.get("cwd", ""),
                    shell_pid=shell_pid,
                )
        s = self._sessions[session_id]
        # Always update cwd/project if provided
        if data.get("cwd"):
            s.cwd = data["cwd"]
            if not s.project_path:
                s.project_path = data["cwd"]
                s.project_name = s.project_path.rstrip("/").rsplit("/", 1)[-1]
        if shell_pid is not None and s.shell_pid is None:
            s.shell_pid = shell_pid
        return s

    def _handle_session_start(self, session_id: str, data: dict[str, Any], shell_pid: int | None) -> None:
        s = self._get_or_create(session_id, data, shell_pid)
        s.status = SessionStatus.WAITING_INPUT
        s.last_event = "SessionStart"
        s.last_event_time = time.time()
        if data.get("permission_mode"):
            s.permission_mode = data["permission_mode"]
        self._notify()

    def _handle_user_prompt_submit(self, session_id: str, data: dict[str, Any], shell_pid: int | None = None) -> None:
        s = self._get_or_create(session_id, data, shell_pid)
        s.status = SessionStatus.WORKING
        s.last_event = "UserPromptSubmit"
        s.last_event_time = time.time()
        s.notification_msg = ""
        self._notify()

    def _handle_post_tool_use(self, session_id: str, data: dict[str, Any], shell_pid: int | None = None) -> None:
        s = self._get_or_create(session_id, data, shell_pid)
        s.status = SessionStatus.WORKING
        s.last_event = "PostToolUse"
        s.last_event_time = time.time()
        tool_name = data.get("tool_name", "") or data.get("tool", {}).get("name", "")
        if tool_name:
            s.last_tool = tool_name
        self._notify()

    def _handle_notification(self, session_id: str, data: dict[str, Any], shell_pid: int | None = None) -> None:
        s = self._get_or_create(session_id, data, shell_pid)
        s.last_event = "Notification"
        s.last_event_time = time.time()
        # Check if this is a permission prompt notification
        notification_type = data.get("notification_type", "") or data.get("type", "")
        if notification_type == "permission_prompt" or "permission" in str(data).lower():
            s.status = SessionStatus.PERMISSION_NEEDED
            s.notification_msg = data.get("message", "") or data.get("title", "Permission needed")
        self._notify()

    def _handle_stop(self, session_id: str, data: dict[str, Any], shell_pid: int | None = None) -> None:
        s = self._get_or_create(session_id, data, shell_pid)
        s.status = SessionStatus.WAITING_INPUT
        s.last_event = "Stop"
        s.last_event_time = time.time()
        s.notification_msg = ""
        self._notify()

    def _handle_session_end(self, session_id: str) -> None:
        if session_id in self._sessions:
            s = self._sessions.pop(session_id)
            if s.shell_pid and s.shell_pid in self._pid_to_session:
                del self._pid_to_session[s.shell_pid]
        self._notify()

    async def discover_tmux_sessions(self) -> None:
        """Scan tmux for existing Claude Code sessions and merge into store."""
        discovered = await list_claude_windows()
        panes = await list_panes()
        live_pids = {s.shell_pid for s in discovered if s.shell_pid}

        # Remove sessions whose claude process is no longer running
        dead_keys = [
            key for key, s in self._sessions.items()
            if s.shell_pid is not None and s.shell_pid not in live_pids
        ]
        for key in dead_keys:
            s = self._sessions.pop(key)
            if s.shell_pid and s.shell_pid in self._pid_to_session:
                del self._pid_to_session[s.shell_pid]

        # Also try to match existing hook sessions to tmux windows
        for session in self._sessions.values():
            if session.tmux_window is None and session.shell_pid is not None:
                pane = await match_pid_to_window(session.shell_pid, panes)
                if pane:
                    session.tmux_session = pane.session_name
                    session.tmux_window = pane.window_index
                    session.tmux_pane = pane.pane_index

        # Add tmux-discovered sessions that don't overlap with hook sessions
        existing_pids = {s.shell_pid for s in self._sessions.values() if s.shell_pid}
        existing_windows = {
            (s.tmux_session, s.tmux_window)
            for s in self._sessions.values()
            if s.tmux_window is not None
        }
        for tmux_session in discovered:
            if tmux_session.shell_pid in existing_pids:
                continue
            window_key = (tmux_session.tmux_session, tmux_session.tmux_window)
            if window_key in existing_windows:
                continue
            self._sessions[tmux_session.session_id] = tmux_session

        self._notify()

    async def match_pids_to_windows(self) -> None:
        """Update tmux window mapping for all sessions with a shell_pid."""
        panes = await list_panes()
        for session in self._sessions.values():
            if session.shell_pid is not None and session.tmux_window is None:
                pane = await match_pid_to_window(session.shell_pid, panes)
                if pane:
                    session.tmux_session = pane.session_name
                    session.tmux_window = pane.window_index
                    session.tmux_pane = pane.pane_index

    async def refresh_active_windows(self) -> None:
        """Mark which sessions are in the currently active tmux window."""
        active = await get_active_windows()
        for session in self._sessions.values():
            session.is_active_window = (
                session.tmux_session is not None
                and session.tmux_window is not None
                and (session.tmux_session, session.tmux_window) in active
            )

    def refresh_git_branches(self) -> None:
        """Refresh git branch for all sessions with a known project path."""
        import subprocess
        for session in self._sessions.values():
            path = session.project_path or session.cwd
            if not path or path == "/":
                continue
            try:
                result = subprocess.run(
                    ["git", "-C", path, "branch", "--show-current"],
                    capture_output=True, text=True, timeout=2,
                )
                branch = result.stdout.strip()
                if branch:
                    session.git_branch = branch
            except (subprocess.TimeoutExpired, OSError):
                pass

    def refresh_last_messages(self) -> None:
        """Refresh the last chat message for all sessions from transcripts."""
        for session in self._sessions.values():
            msg = get_last_message(session.session_id, session.project_path)
            if msg:
                session.last_message = msg
        self._notify()
