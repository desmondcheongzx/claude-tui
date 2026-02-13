# claude-tui

Yet Another Claude Code Session Manager.

There are many out there.

This is entirely vibe coded.

You probably shouldn't use this one. 

## Install with uv

```bash
git clone git@github.com:desmondcheongzx/claude-tui.git
cd claude-tui
uv sync
```

### Launch the TUI

```bash
uv run claude-tui
```

### Keyboard Shortcuts

| Key | Action |
|-----|--------|
| `q` | Quit the application |
| `r` | Refresh all sessions and conversations |
| `Enter` | Switch to the selected session/conversation |
| `/` | Focus search bar |
| `Esc` | Clear search and return to list |
| `↑/↓` | Navigate through sessions/conversations |

### Development Mode

For development with auto-reload:

```bash
claude-tui-dev
```

## How It Works

1. **Session Discovery**: Scans tmux windows for running Claude Code instances
2. **Hook Server**: Launches an HTTP server to receive status updates from Claude Code hooks
3. **Live Monitoring**: Continuously polls tmux for active window changes and session updates
4. **Conversation Browser**: Scans `~/.claude/conversations/` for recent sessions

## Warcraft Peon Sounds 🔊

Want your Claude Code sessions to sound like a Warcraft II peon? Run the installer:

> Inspired by [@tonysheng's post](https://x.com/tonysheng/status/2021279560046874641)

```bash
bash install-peon-sounds.sh
```

This sets up Claude Code hooks to play authentic peon sounds for different events:
- **SessionStart** → "Ready!" / "What?"
- **UserPromptSubmit** → "Yes!" / "Yes, attack!"
- **Stop** → Completion acknowledgments
- **Notification** → "What?" (for permission prompts)

**Requirements:**
- macOS (uses `afplay`)
- `jq` (install with `brew install jq`)

The script is self-contained with all 18 WAV files embedded as base64. No external downloads needed!

