"""Read last chat message from Claude Code session JSONL transcripts."""

from __future__ import annotations

import json
from pathlib import Path

from claude_tui.models import RecentConversation

PROJECTS_DIR = Path.home() / ".claude" / "projects"


def _encode_project_path(project_path: str) -> str:
    """Encode a project path to the directory name Claude Code uses.

    /Users/desmond/claude-tui -> -Users-desmond-claude-tui
    """
    return project_path.replace("/", "-")


def find_transcript(session_id: str, project_path: str) -> Path | None:
    """Find the JSONL transcript file for a session."""
    if session_id and project_path:
        encoded = _encode_project_path(project_path)
        path = PROJECTS_DIR / encoded / f"{session_id}.jsonl"
        if path.exists():
            return path

    # Only fall back to most-recent JSONL for tmux-discovered sessions
    # (pseudo IDs). For real session IDs, the file just doesn't exist yet.
    if session_id.startswith("tmux-") and project_path:
        encoded = _encode_project_path(project_path)
        project_dir = PROJECTS_DIR / encoded
        if project_dir.is_dir():
            jsonl_files = list(project_dir.glob("*.jsonl"))
            if jsonl_files:
                return max(jsonl_files, key=lambda p: p.stat().st_mtime)

    return None


def read_last_message(transcript_path: Path) -> str:
    """Read the last assistant text message from a JSONL transcript.

    Reads from the end of the file to find the most recent assistant
    message with text content.
    """
    try:
        # Read the last ~64KB to find recent messages without loading the whole file
        file_size = transcript_path.stat().st_size
        read_size = min(file_size, 65536)

        with open(transcript_path, "rb") as f:
            f.seek(max(0, file_size - read_size))
            tail = f.read().decode("utf-8", errors="replace")

        # Parse lines from the end
        lines = tail.strip().splitlines()
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            if entry.get("type") != "assistant":
                continue

            message = entry.get("message", {})
            if message.get("role") != "assistant":
                continue

            # Find text content blocks
            for block in message.get("content", []):
                if isinstance(block, dict) and block.get("type") == "text":
                    text = block["text"].strip()
                    if text:
                        # Return first line or truncated
                        first_line = text.split("\n")[0]
                        return first_line

    except (OSError, KeyError, TypeError):
        pass

    return ""


def get_last_message(session_id: str, project_path: str) -> str:
    """Get the last assistant chat message for a session."""
    path = find_transcript(session_id, project_path)
    if path is None:
        return ""
    return read_last_message(path)


def _decode_project_path(dir_name: str) -> str:
    """Decode a project directory name back to a path.

    -Users-desmond-claude-tui -> /Users/desmond/claude-tui
    """
    # The leading '-' corresponds to the leading '/'
    return dir_name.replace("-", "/", 1).replace("-", "/") if dir_name.startswith("-") else dir_name.replace("-", "/")


# User messages that aren't real prompts
_SKIP_PREFIXES = (
    "[Request interrupted",
)


def _extract_user_text(entry: dict) -> str:
    """Extract text content from a user entry, or return empty string.

    Skips tool_result blocks and system-generated messages like
    '[Request interrupted by user for tool use]'.
    """
    msg = entry.get("message", {})
    content = msg.get("content", "") if isinstance(msg, dict) else ""
    if isinstance(content, str):
        text = content.strip()
    elif isinstance(content, list):
        text = ""
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text", "").strip()
                if text:
                    break
    else:
        return ""
    if any(text.startswith(p) for p in _SKIP_PREFIXES):
        return ""
    return text


def _read_first_user_message(path: Path) -> tuple[str, str]:
    """Read the first user message with text content from a JSONL transcript.

    Skips user entries that only contain tool_results or other non-text blocks.
    Returns (first_message, cwd).
    """
    try:
        cwd = ""
        with open(path, "r", errors="replace") as f:
            # Read up to 32KB to find the first real user message
            head = f.read(32768)
        for line in head.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("type") != "user":
                continue
            # Capture cwd from the first user entry we see
            if not cwd:
                cwd = entry.get("cwd", "")
            text = _extract_user_text(entry)
            if text:
                return text, cwd
    except OSError:
        pass
    return "", ""


def scan_recent_conversations(
    exclude_session_ids: set[str] | None = None,
    excluded_projects: list[str] | None = None,
) -> list[RecentConversation]:
    """Scan ~/.claude/projects/ for recent JSONL transcripts.

    Returns RecentConversation objects sorted by mtime descending,
    excluding any session IDs in `exclude_session_ids` and any
    project names in `excluded_projects`.
    """
    if exclude_session_ids is None:
        exclude_session_ids = set()
    excluded_set = set(excluded_projects) if excluded_projects else set()

    if not PROJECTS_DIR.is_dir():
        return []

    # Collect all JSONL files with their mtime
    candidates: list[tuple[float, Path, str]] = []  # (mtime, path, dir_name)
    for project_dir in PROJECTS_DIR.iterdir():
        if not project_dir.is_dir():
            continue
        dir_name = project_dir.name
        for jsonl_file in project_dir.glob("*.jsonl"):
            # Skip subagents subdirectories (glob on dir level won't match, but be safe)
            if "subagents" in jsonl_file.parts:
                continue
            session_id = jsonl_file.stem
            if session_id in exclude_session_ids:
                continue
            try:
                mtime = jsonl_file.stat().st_mtime
            except OSError:
                continue
            candidates.append((mtime, jsonl_file, dir_name))

    # Sort by mtime descending
    candidates.sort(key=lambda x: x[0], reverse=True)

    results: list[RecentConversation] = []
    for mtime, jsonl_path, dir_name in candidates:
        first_msg, cwd = _read_first_user_message(jsonl_path)
        if not first_msg:
            continue
        project_path = cwd or _decode_project_path(dir_name)
        project_name = project_path.rstrip("/").rsplit("/", 1)[-1] if project_path else dir_name
        if project_name in excluded_set:
            continue
        # For plan implementation messages, extract the plan title
        if first_msg.startswith("Implement the following plan:"):
            for line in first_msg.split("\n"):
                line = line.strip().lstrip("#").strip()
                if line and not line.startswith("Implement"):
                    first_msg = f"[Plan] {line}"
                    break
            else:
                first_msg = "[Plan]"
        # Take first line, truncate to 120 chars
        first_msg = first_msg.split("\n")[0].strip()
        if len(first_msg) > 120:
            first_msg = first_msg[:117] + "..."
        results.append(RecentConversation(
            session_id=jsonl_path.stem,
            project_path=project_path,
            project_name=project_name,
            first_message=first_msg,
            mtime=mtime,
        ))

    return results
