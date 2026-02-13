"""tmux subprocess wrappers for window discovery, PID matching, and switching."""

from __future__ import annotations

import asyncio
import os
import re
import subprocess
from dataclasses import dataclass

from claude_tui.models import SessionInfo, SessionStatus


@dataclass
class TmuxPane:
    session_name: str
    window_index: int
    pane_index: int
    window_name: str
    pane_pid: int
    pane_current_command: str

    @property
    def target(self) -> str:
        """Full tmux target for this specific pane: session:window.pane"""
        return f"{self.session_name}:{self.window_index}.{self.pane_index}"


async def run_tmux(*args: str) -> str:
    proc = await asyncio.create_subprocess_exec(
        "tmux", *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    return stdout.decode().strip()


async def list_panes() -> list[TmuxPane]:
    """List all tmux panes with metadata."""
    fmt = "#{session_name}\t#{window_index}\t#{pane_index}\t#{window_name}\t#{pane_pid}\t#{pane_current_command}"
    output = await run_tmux("list-panes", "-a", "-F", fmt)
    panes: list[TmuxPane] = []
    for line in output.splitlines():
        parts = line.split("\t")
        if len(parts) != 6:
            continue
        panes.append(TmuxPane(
            session_name=parts[0],
            window_index=int(parts[1]),
            pane_index=int(parts[2]),
            window_name=parts[3],
            pane_pid=int(parts[4]),
            pane_current_command=parts[5],
        ))
    return panes


def _pid_is_descendant_of(target_pid: int, ancestor_pid: int) -> bool:
    """Check if target_pid is a descendant of ancestor_pid by walking up."""
    try:
        current = target_pid
        for _ in range(20):
            result = subprocess.run(
                ["ps", "-o", "ppid=", "-p", str(current)],
                capture_output=True, text=True, timeout=2,
            )
            ppid_str = result.stdout.strip()
            if not ppid_str:
                return False
            ppid = int(ppid_str)
            if ppid == ancestor_pid:
                return True
            if ppid <= 1:
                return False
            current = ppid
    except (subprocess.TimeoutExpired, ValueError):
        pass
    return False


async def match_pid_to_window(shell_pid: int, panes: list[TmuxPane] | None = None) -> TmuxPane | None:
    """Find which tmux pane owns a given PID by walking up the process tree."""
    if panes is None:
        panes = await list_panes()
    for pane in panes:
        if _pid_is_descendant_of(shell_pid, pane.pane_pid):
            return pane
    return None


async def capture_pane_output(pane_target: str, lines: int = 50) -> str:
    """Capture the last N lines of a specific tmux pane."""
    return await run_tmux("capture-pane", "-t", pane_target, "-p", "-S", f"-{lines}")


def detect_status_from_output(output: str) -> SessionStatus:
    """Infer Claude Code session status from pane output."""
    lines = output.strip().splitlines()
    # Look at the last ~20 lines for status indicators
    tail = "\n".join(lines[-20:]) if len(lines) > 20 else output

    # Permission prompt
    if re.search(r"Do you want to allow|allow this action|approve this", tail, re.IGNORECASE):
        return SessionStatus.PERMISSION_NEEDED

    # Working indicator (Claude is processing)
    if re.search(r"esc to interrupt|⏎ to interrupt|thinking|processing", tail, re.IGNORECASE):
        return SessionStatus.WORKING

    # User input prompt (Claude is done, waiting for user)
    if re.search(r"❯|>\s*$|^claude\s*>", tail, re.MULTILINE):
        return SessionStatus.WAITING_INPUT

    return SessionStatus.UNKNOWN


def _strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences."""
    return re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", text)


def _extract_last_line(output: str) -> str:
    """Get the last non-empty line from pane output, cleaned up."""
    for line in reversed(output.splitlines()):
        stripped = _strip_ansi(line).strip()
        if stripped:
            return stripped
    return ""


async def get_pane_last_line(pane_target: str) -> str:
    """Capture and return the last meaningful line from a tmux pane."""
    output = await capture_pane_output(pane_target, lines=10)
    return _extract_last_line(output)


def _find_claude_pids() -> list[int]:
    """Find PIDs of all running 'claude' processes via pgrep."""
    try:
        result = subprocess.run(
            ["pgrep", "-x", "claude"],
            capture_output=True, text=True, timeout=3,
        )
        return [int(p) for p in result.stdout.strip().splitlines() if p.strip()]
    except (subprocess.TimeoutExpired, ValueError):
        return []


def _get_cwd_for_pid(pid: int) -> str:
    """Get the working directory of a process via lsof."""
    try:
        result = subprocess.run(
            ["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"],
            capture_output=True, text=True, timeout=3,
        )
        for line in result.stdout.splitlines():
            if line.startswith("n/"):
                return line[1:]
    except (subprocess.TimeoutExpired, ValueError):
        pass
    return ""


async def list_claude_windows() -> list[SessionInfo]:
    """Discover existing Claude Code sessions by finding claude processes
    and matching them back to tmux panes."""
    claude_pids = _find_claude_pids()
    if not claude_pids:
        return []

    panes = await list_panes()
    sessions: list[SessionInfo] = []

    for pid in claude_pids:
        pane = await match_pid_to_window(pid, panes)
        if pane is None:
            continue  # Not in a tmux pane

        output = await capture_pane_output(pane.target)
        status = detect_status_from_output(output)
        cwd = _get_cwd_for_pid(pid)
        project_name = cwd.rstrip("/").rsplit("/", 1)[-1] if cwd else pane.window_name

        pseudo_id = f"tmux-{pid}"
        sessions.append(SessionInfo(
            session_id=pseudo_id,
            project_path=cwd or pane.window_name,
            project_name=project_name,
            status=status,
            shell_pid=pid,
            tmux_session=pane.session_name,
            tmux_window=pane.window_index,
            tmux_pane=pane.pane_index,
        ))

    return sessions


async def get_current_tmux_session() -> str | None:
    """Get the name of the current tmux session (if running inside tmux)."""
    if not os.environ.get("TMUX"):
        return None
    return await run_tmux("display-message", "-p", "#{session_name}")


async def get_active_windows() -> set[tuple[str, int]]:
    """Get the set of (session_name, window_index) for all active tmux windows."""
    fmt = "#{session_name}\t#{window_index}\t#{window_active}"
    output = await run_tmux("list-windows", "-a", "-F", fmt)
    active: set[tuple[str, int]] = set()
    for line in output.splitlines():
        parts = line.split("\t")
        if len(parts) == 3 and parts[2] == "1":
            active.add((parts[0], int(parts[1])))
    return active


async def switch_to_window(session_name: str, window_index: int) -> None:
    """Switch to a specific tmux window."""
    target = f"{session_name}:{window_index}"
    await run_tmux("select-window", "-t", target)


async def get_oldest_tmux_session() -> str | None:
    """Get the name of the oldest tmux session, or None if no sessions exist."""
    fmt = "#{session_name}\t#{session_created}"
    output = await run_tmux("list-sessions", "-F", fmt)
    if not output:
        return None
    oldest_name = None
    oldest_time = float("inf")
    for line in output.splitlines():
        parts = line.split("\t")
        if len(parts) != 2:
            continue
        try:
            created = int(parts[1])
        except ValueError:
            continue
        if created < oldest_time:
            oldest_time = created
            oldest_name = parts[0]
    return oldest_name


async def _activate_tmux_window(session_name: str) -> None:
    """Bring the terminal window containing the tmux session to the foreground."""
    # Find the tty of the tmux client attached to this session
    client_tty = await run_tmux("list-clients", "-t", session_name, "-F", "#{client_tty}")
    if not client_tty:
        return
    tty = client_tty.splitlines()[0].strip()
    if not tty:
        return

    # Detect terminal app
    term_env = await run_tmux("show-environment", "-g", "TERM_PROGRAM")
    app_name = None
    if "=" in term_env:
        value = term_env.split("=", 1)[1]
        if "iTerm" in value:
            app_name = "iTerm2"
        elif "Apple_Terminal" in value:
            app_name = "Terminal"

    if app_name == "iTerm2":
        script = f'''
            tell application "iTerm2"
                repeat with w in windows
                    repeat with t in tabs of w
                        repeat with s in sessions of t
                            if tty of s is "{tty}" then
                                select t
                                set index of w to 1
                                activate
                                return
                            end if
                        end repeat
                    end repeat
                end repeat
            end tell
        '''
    elif app_name == "Terminal":
        script = f'''
            tell application "Terminal"
                repeat with w in windows
                    repeat with t in tabs of w
                        if tty of t is "{tty}" then
                            set selected tab of w to t
                            set index of w to 1
                            activate
                            return
                        end if
                    end repeat
                end repeat
            end tell
        '''
    else:
        return

    proc = await asyncio.create_subprocess_exec(
        "osascript", "-e", script,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()


async def new_window_with_command(session_name: str, cwd: str, window_name: str, command: str) -> None:
    """Open a new tmux window running the given command and switch focus to it."""
    # -P prints the new window target (e.g. "sess:3.0") so we can switch to it
    target = await run_tmux("new-window", "-P", "-t", f"{session_name}:", "-c", cwd, "-n", window_name, command)
    if target:
        # select-window focuses within the session; switch-client moves the client there
        win_target = target.split(".")[0]  # "sess:3.0" -> "sess:3"
        await run_tmux("select-window", "-t", win_target)
        await run_tmux("switch-client", "-t", win_target)
    await _activate_tmux_window(session_name)
