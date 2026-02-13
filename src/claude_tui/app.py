"""Main Textual TUI application for monitoring Claude Code sessions."""

from __future__ import annotations

import asyncio
import os
import select
import sys
import termios
import tty

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.message import Message
from textual.events import Key
from textual.theme import Theme
from textual.widgets import Input, Rule, Select, Static


def _query_terminal_bg() -> str | None:
    """Query the terminal's background color via OSC 11.

    Sends the standard OSC 11 query and parses the rgb response.
    Works with iTerm2, Terminal.app, kitty, alacritty, and most modern terminals.
    Returns a hex color string like '#282c34', or None if detection fails.
    """
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        return None
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        os.write(sys.stdout.fileno(), b"\033]11;?\033\\")
        resp = b""
        while select.select([fd], [], [], 0.3)[0]:
            ch = os.read(fd, 1)
            resp += ch
            if ch in (b"\\", b"\x07"):
                break
        decoded = resp.decode("latin-1")
        if "rgb:" in decoded:
            rgb = decoded.split("rgb:")[1].split("\033")[0].split("\x07")[0]
            parts = rgb.split("/")
            if len(parts) == 3:
                # Works for both 8-bit (ab) and 16-bit (abcd) color components
                r = int(parts[0][:2], 16)
                g = int(parts[1][:2], 16)
                b = int(parts[2][:2], 16)
                return f"#{r:02x}{g:02x}{b:02x}"
    except Exception:
        pass
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
    return None


def _lighten(color: str, amount: float) -> str:
    """Shift a hex color toward white by the given fraction (0.0–1.0)."""
    r, g, b = int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)
    r = min(255, int(r + (255 - r) * amount))
    g = min(255, int(g + (255 - g) * amount))
    b = min(255, int(b + (255 - b) * amount))
    return f"#{r:02x}{g:02x}{b:02x}"


def _build_theme(bg: str) -> Theme:
    """Build the app theme, deriving background/surface/panel from the given color."""
    return Theme(
        name="one-dark",
        primary="#61afef",
        secondary="#c678dd",
        warning="#d4b85c",
        error="#c97070",
        success="#7dba6d",
        accent="#61afef",
        foreground="#abb2bf",
        background=bg,
        surface=bg,
        panel=_lighten(bg, 0.04),
        dark=True,
    )

from claude_tui.server import HookServer
from claude_tui.sessions import SessionStore
from claude_tui.settings import Settings
from claude_tui.tmux import get_current_tmux_session, get_oldest_tmux_session, new_window_with_command, switch_to_window
from claude_tui.models import RecentConversation
from claude_tui.transcript import scan_recent_conversations
from claude_tui.widgets.session_card import RecentCard, RecentList, SessionCard, SessionList


class HookReceived(Message):
    """Posted when a hook event is received from the HTTP server."""


class ClaudeTUI(App):
    CSS_PATH = "app.tcss"
    TITLE = "Claude Code Sessions"

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh", "Refresh"),
        Binding("enter", "select_session", "Switch to Session"),
        Binding("slash", "focus_search", "Search", show=False),
        Binding("escape", "blur_search", "Clear search", show=False),
    ]

    def __init__(self, terminal_bg: str | None = None) -> None:
        super().__init__()
        bg = terminal_bg or "#282c34"
        self.register_theme(_build_theme(bg))
        self.theme = "one-dark"
        self._store = SessionStore(on_change=self._post_hook_received)
        self._server = HookServer(self._store, on_event=self._post_hook_received)
        self._tmux_session: str | None = None
        self._settings = Settings.load()
        self._all_recent: list[RecentConversation] = []
        self._selected_project: str | None = None  # None = "All"

    def _post_hook_received(self) -> None:
        """Thread-safe way to notify the app of a hook event."""
        try:
            self.post_message(HookReceived())
        except Exception:
            pass

    def compose(self) -> ComposeResult:
        logo = "\n[#E8A55D]▐▛███▜▌\n▝▜█████▛▘\n▘▘ ▝▝[/]"
        yield Static(f"{logo}\nClaude Code Sessions", id="header-bar")
        with VerticalScroll(id="main-scroll"):
            yield SessionList(id="session-list")
            yield Rule(id="section-divider")
            with Vertical(id="recent-browser"):
                yield Select[str]([], id="project-select", prompt="Loading projects...")
                yield Input(placeholder="Search recent conversations...", id="recent-search")
                yield RecentList(id="recent-list")

    async def on_mount(self) -> None:
        self._tmux_session = await get_current_tmux_session()

        port = await self._server.start()
        self.sub_title = f"port {port}"
        if self._tmux_session:
            self.sub_title += f" | tmux: {self._tmux_session}"

        # Initial discovery
        await self._store.discover_tmux_sessions()
        await self._store.refresh_active_windows()
        self._store.refresh_git_branches()
        self._store.refresh_last_messages()
        self._refresh_list()

        # Initial recent conversations load
        self.run_worker(self._refresh_recent())

        # Heavy rescan (new sessions, PID matching, transcripts, branches)
        self.set_interval(3.0, self._periodic_rescan)
        # Lightweight active-window poll
        self.set_interval(0.5, self._poll_active_window)
        # Recent conversations refresh
        self.set_interval(30.0, self._refresh_recent_periodic)

    async def _periodic_rescan(self) -> None:
        await self._store.discover_tmux_sessions()
        await self._store.match_pids_to_windows()
        self._store.refresh_git_branches()
        self._store.refresh_last_messages()
        self._refresh_list()

    async def _poll_active_window(self) -> None:
        await self._store.refresh_active_windows()
        self._refresh_list()

    def on_claude_tui_hook_received(self, _event: HookReceived) -> None:
        """Handle hook events from the HTTP server."""
        self.run_worker(self._on_hook_async())
        self._refresh_list()

    async def _on_hook_async(self) -> None:
        await self._store.match_pids_to_windows()
        self._store.refresh_last_messages()
        self._refresh_list()

    def _refresh_list(self) -> None:
        session_list = self.query_one("#session-list", SessionList)
        sessions = self._store.get_sorted_sessions()
        session_list.refresh_sessions(sessions)

        # Update header with session count
        header = self.query_one("#header-bar", Static)
        count = len(sessions)
        working = sum(1 for s in sessions if s.status.value == "working")
        waiting = sum(1 for s in sessions if s.status.value == "waiting")
        perms = sum(1 for s in sessions if s.status.value == "permission")
        logo = "\n[#E8A55D]▐▛███▜▌\n▝▜█████▛▘\n▘▘ ▝▝[/]"
        parts = [f"Claude Code Sessions ({count})"]
        if working:
            parts.append(f"[#7dba6d]{working} working[/]")
        if waiting:
            parts.append(f"[#d4b85c]{waiting} waiting[/]")
        if perms:
            parts.append(f"[#c97070]{perms} need permission[/]")
        header.update(f"{logo}\n{' | '.join(parts)}")

    def action_refresh(self) -> None:
        self.run_worker(self._periodic_rescan())
        self.run_worker(self._refresh_recent())

    async def _refresh_recent(self) -> None:
        """Scan for recent conversations in a background thread."""
        active_ids = set(self._store.sessions.keys())
        self._all_recent = await asyncio.to_thread(
            scan_recent_conversations,
            exclude_session_ids=active_ids,
            excluded_projects=self._settings.excluded_projects,
        )
        self._rebuild_project_select()
        self._apply_recent_filter()

    def _rebuild_project_select(self) -> None:
        """Rebuild the project selector dropdown from cached conversations."""
        select = self.query_one("#project-select", Select)

        # Count conversations per project
        project_counts: dict[str, int] = {}
        for c in self._all_recent:
            project_counts[c.project_name] = project_counts.get(c.project_name, 0) + 1

        sorted_projects = sorted(project_counts.keys(), key=str.lower)

        options: list[tuple[str, str]] = [
            (f"All ({len(self._all_recent)})", "__all__"),
        ]
        for project in sorted_projects:
            options.append((f"{project} ({project_counts[project]})", project))

        select.set_options(options)
        if select.value == Select.BLANK:
            select.value = "__all__"

    def _apply_recent_filter(self) -> None:
        """Filter cached recent conversations by selected project and search query."""
        # Apply project filter
        if self._selected_project is not None:
            filtered = [c for c in self._all_recent if c.project_name == self._selected_project]
        else:
            filtered = self._all_recent

        # Apply search query
        try:
            query = self.query_one("#recent-search", Input).value.strip().lower()
        except Exception:
            query = ""
        if query:
            filtered = [
                c for c in filtered
                if query in c.first_message.lower() or query in c.project_name.lower()
            ]

        recent_list = self.query_one("#recent-list", RecentList)
        recent_list.refresh_conversations(filtered)
        # Always highlight the top result
        if recent_list._items:
            recent_list.index = 0

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "recent-search":
            self._apply_recent_filter()

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "project-select":
            self._selected_project = None if event.value in (Select.BLANK, "__all__") else event.value
            self._apply_recent_filter()

    def on_key(self, event: Key) -> None:
        """Forward navigation keys from search input to the recent list."""
        search = self.query_one("#recent-search", Input)
        if not search.has_focus:
            return
        recent_list = self.query_one("#recent-list", RecentList)
        if event.key == "down":
            recent_list.action_cursor_down()
            event.prevent_default()
        elif event.key == "up":
            recent_list.action_cursor_up()
            event.prevent_default()
        elif event.key == "enter":
            conv = recent_list.get_selected_conversation()
            if conv:
                event.prevent_default()
                self.run_worker(self._resume_conversation(conv))

    def _refresh_recent_periodic(self) -> None:
        self.run_worker(self._refresh_recent())

    async def action_select_session(self) -> None:
        # Check if the recent list has focus
        recent_list = self.query_one("#recent-list", RecentList)
        if recent_list.has_focus:
            conv = recent_list.get_selected_conversation()
            if conv:
                await self._resume_conversation(conv)
                return

        session_list = self.query_one("#session-list", SessionList)
        session = session_list.get_selected_session()
        if session is None:
            self.notify("No session selected", severity="warning")
            return

        tmux_sess = session.tmux_session or self._tmux_session
        if session.tmux_window is not None and tmux_sess:
            await switch_to_window(tmux_sess, session.tmux_window)
            self.exit()
        else:
            self.notify(
                f"No tmux window mapped for {session.project_name or session.session_id}",
                severity="warning",
            )

    async def on_session_card_double_clicked(self, event: SessionCard.DoubleClicked) -> None:
        session = event.session
        tmux_sess = session.tmux_session or self._tmux_session
        if session.tmux_window is not None and tmux_sess:
            await switch_to_window(tmux_sess, session.tmux_window)
        else:
            self.notify(
                f"No tmux window mapped for {session.project_name or session.session_id}",
                severity="warning",
            )

    async def on_recent_card_double_clicked(self, event: RecentCard.DoubleClicked) -> None:
        await self._resume_conversation(event.conversation)

    async def _resume_conversation(self, conv: RecentConversation) -> None:
        """Open a new tmux window and resume a recent conversation."""
        tmux_sess = self._tmux_session or await get_oldest_tmux_session()
        if not tmux_sess:
            self.notify("No tmux session found", severity="warning")
            return
        cmd = f"claude --resume {conv.session_id}"
        await new_window_with_command(tmux_sess, conv.project_path, conv.project_name, cmd)

    def action_focus_search(self) -> None:
        self.query_one("#recent-search", Input).focus()

    def action_blur_search(self) -> None:
        search = self.query_one("#recent-search", Input)
        if search.has_focus:
            search.value = ""
            self._apply_recent_filter()
            self.query_one("#recent-list", RecentList).focus()

    async def action_quit(self) -> None:
        await self._server.stop()
        self.exit()


def main() -> None:
    terminal_bg = _query_terminal_bg()
    app = ClaudeTUI(terminal_bg=terminal_bg)
    app.run()


if __name__ == "__main__":
    main()
