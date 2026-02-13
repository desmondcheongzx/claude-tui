"""Card-based widget for displaying a Claude Code session."""

from __future__ import annotations

import time

from textual.app import ComposeResult
from textual.events import Click
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Label, ListView, ListItem

from claude_tui.models import RecentConversation, SessionInfo, SessionStatus


STATUS_STYLE = {
    SessionStatus.WORKING: ("WORKING", "#7dba6d bold"),
    SessionStatus.WAITING_INPUT: ("WAITING", "#d4b85c"),
    SessionStatus.PERMISSION_NEEDED: ("PERMISSION", "#c97070 bold"),
    SessionStatus.UNKNOWN: ("UNKNOWN", "dim"),
}


def _format_age(timestamp: float) -> str:
    delta = time.time() - timestamp
    if delta < 60:
        return f"{int(delta)}s ago"
    if delta < 3600:
        return f"{int(delta / 60)}m ago"
    return f"{int(delta / 3600)}h ago"


def _session_fingerprint(s: SessionInfo) -> tuple:
    """Return a hashable fingerprint of the fields that affect rendering."""
    return (
        s.session_id,
        s.status,
        s.tmux_window,
        s.project_name,
        s.permission_mode,
        s.last_tool,
        s.last_event,
        s.git_branch,
        s.last_message,
        s.is_active_window,
        s.sound_pack,
    )


class SessionCard(Widget):
    """Renders a single session as a bordered card."""

    DEFAULT_CSS = """
    SessionCard {
        height: auto;
        padding: 0 1;
        background: transparent;
        border: round $surface-lighten-1;
    }
    SessionCard.working {
        border: round $success;
    }
    SessionCard.waiting {
        border: round $warning;
    }
    SessionCard.permission {
        border: round $error;
    }
    SessionCard.active {
        background: $panel;
    }
    SessionCard .card-details {
        height: 1;
        color: $text-muted;
    }
    SessionCard .card-peek {
        height: 1;
        color: $text-muted;
    }
    """

    def __init__(self, session: SessionInfo) -> None:
        super().__init__()
        self.session_info = session
        self._apply_status_class()
        self._set_border_title()

    def _apply_status_class(self) -> None:
        s = self.session_info
        for cls in ("working", "waiting", "permission", "active"):
            self.remove_class(cls)
        class_map = {
            SessionStatus.WORKING: "working",
            SessionStatus.WAITING_INPUT: "waiting",
            SessionStatus.PERMISSION_NEEDED: "permission",
        }
        if s.status in class_map:
            self.add_class(class_map[s.status])
        if s.is_active_window:
            self.add_class("active")

    def _set_border_title(self) -> None:
        s = self.session_info
        win = f"#{s.tmux_window}" if s.tmux_window is not None else "#?"
        project = s.project_name or s.session_id[:12]
        branch = f" [{s.git_branch}]" if s.git_branch else ""
        viewing = " [VIEWING]" if s.is_active_window else ""
        self.border_title = f"{win}  {project}{branch}{viewing}"

        label, _ = STATUS_STYLE.get(s.status, ("?", "dim"))
        self.border_subtitle = label

    def _build_details(self) -> str:
        s = self.session_info
        parts: list[str] = []
        if s.permission_mode:
            parts.append(f"Mode: {s.permission_mode}")
        if s.last_tool:
            parts.append(f"Tool: {s.last_tool}")
        if s.last_event:
            age = _format_age(s.last_event_time)
            parts.append(f"{s.last_event} {age}")
        return "  |  ".join(parts) if parts else ""

    def _build_peek(self) -> str:
        msg = self.session_info.last_message or ""
        if len(msg) > 120:
            msg = msg[:117] + "..."
        return msg

    class DoubleClicked(Message):
        """Posted when a session card is double-clicked."""

        def __init__(self, session: SessionInfo) -> None:
            super().__init__()
            self.session = session

    def on_click(self, event: Click) -> None:
        if event.chain >= 2:
            self.post_message(self.DoubleClicked(self.session_info))

    def compose(self) -> ComposeResult:
        yield Label(self._build_details(), classes="card-details")
        yield Label(self._build_peek(), classes="card-peek")

    def update_from(self, session: SessionInfo) -> None:
        """Update this card in-place with new session data."""
        self.session_info = session
        self._apply_status_class()
        self._set_border_title()

        # Update detail label
        try:
            details_label = self.query_one(".card-details", Label)
            details_label.update(self._build_details())
        except Exception:
            pass

        # Update peek label
        peek = self._build_peek()
        try:
            peek_label = self.query_one(".card-peek", Label)
            if peek:
                peek_label.update(peek)
            else:
                peek_label.update("")
        except Exception:
            # No peek label exists yet; if we need one, we'd have to remount
            pass


class SessionList(ListView):
    """A ListView of SessionCards."""

    BINDINGS = [
        ("j", "cursor_down", "Down"),
        ("k", "cursor_up", "Up"),
    ]

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._session_items: list[SessionInfo] = []
        self._last_fingerprint: tuple | None = None

    def refresh_sessions(self, sessions: list[SessionInfo]) -> None:
        """Update the list, only rebuilding if data actually changed."""
        new_fp = tuple(_session_fingerprint(s) for s in sessions)
        if new_fp == self._last_fingerprint:
            return
        self._last_fingerprint = new_fp

        old_ids = [s.session_id for s in self._session_items]
        new_ids = [s.session_id for s in sessions]

        # If the session list structure is the same, update cards in-place
        if old_ids == new_ids:
            self._session_items = sessions
            for i, session in enumerate(sessions):
                try:
                    item = self.children[i]
                    card = item.query_one(SessionCard)
                    card.update_from(session)
                except Exception:
                    pass
            return

        # Structure changed — rebuild
        old_index = self.index or 0
        self._session_items = sessions
        self.clear()
        for session in sessions:
            card = SessionCard(session)
            self.append(ListItem(card))

        if self._session_items:
            self.index = min(old_index, len(self._session_items) - 1)

    def get_selected_session(self) -> SessionInfo | None:
        """Get the SessionInfo for the currently highlighted item."""
        if self.index is not None and 0 <= self.index < len(self._session_items):
            return self._session_items[self.index]
        return None


def _format_age_extended(timestamp: float) -> str:
    """Format age with day/week/month granularity for older items."""
    delta = time.time() - timestamp
    if delta < 60:
        return f"{int(delta)}s ago"
    if delta < 3600:
        return f"{int(delta / 60)}m ago"
    if delta < 86400:
        return f"{int(delta / 3600)}h ago"
    days = int(delta / 86400)
    if days < 7:
        return f"{days}d ago"
    if days < 30:
        return f"{days // 7}w ago"
    return f"{days // 30}mo ago"


def _recent_fingerprint(r: RecentConversation) -> tuple:
    return (r.session_id, r.first_message, r.mtime)


class RecentCard(Widget):
    """Renders a recent conversation as a dimmer bordered card."""

    DEFAULT_CSS = """
    RecentCard {
        height: auto;
        padding: 0 1;
        background: transparent;
        border: round $surface-lighten-3;
        color: $text-muted;
    }
    RecentCard .card-peek {
        height: 1;
        color: $text-muted;
    }
    """

    def __init__(self, conversation: RecentConversation) -> None:
        super().__init__()
        self.conversation = conversation
        self.border_title = conversation.project_name
        self.border_subtitle = _format_age_extended(conversation.mtime)

    class DoubleClicked(Message):
        """Posted when a recent card is double-clicked."""

        def __init__(self, conversation: RecentConversation) -> None:
            super().__init__()
            self.conversation = conversation

    def on_click(self, event: Click) -> None:
        if event.chain >= 2:
            self.post_message(self.DoubleClicked(self.conversation))

    def compose(self) -> ComposeResult:
        peek = self.conversation.first_message or "(no message)"
        yield Label(peek, classes="card-peek")

    def update_from(self, conversation: RecentConversation) -> None:
        self.conversation = conversation
        self.border_title = conversation.project_name
        self.border_subtitle = _format_age_extended(conversation.mtime)
        try:
            peek_label = self.query_one(".card-peek", Label)
            peek_label.update(conversation.first_message or "(no message)")
        except Exception:
            pass


class RecentList(ListView):
    """A ListView of RecentCards."""

    BINDINGS = [
        ("j", "cursor_down", "Down"),
        ("k", "cursor_up", "Up"),
    ]

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._items: list[RecentConversation] = []
        self._last_fingerprint: tuple | None = None

    def refresh_conversations(self, conversations: list[RecentConversation]) -> None:
        """Update the list, only rebuilding if data actually changed."""
        new_fp = tuple(_recent_fingerprint(c) for c in conversations)
        if new_fp == self._last_fingerprint:
            return
        self._last_fingerprint = new_fp

        old_ids = [c.session_id for c in self._items]
        new_ids = [c.session_id for c in conversations]

        if old_ids == new_ids:
            self._items = conversations
            for i, conv in enumerate(conversations):
                try:
                    item = self.children[i]
                    card = item.query_one(RecentCard)
                    card.update_from(conv)
                except Exception:
                    pass
            return

        old_index = self.index or 0
        self._items = conversations
        self.clear()
        for conv in conversations:
            self.append(ListItem(RecentCard(conv)))

        if self._items:
            self.index = min(old_index, len(self._items) - 1)

    def get_selected_conversation(self) -> RecentConversation | None:
        if self.index is not None and 0 <= self.index < len(self._items):
            return self._items[self.index]
        return None
