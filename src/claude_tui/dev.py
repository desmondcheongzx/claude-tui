"""Dev mode: auto-restart claude-tui on source file changes."""

import subprocess
import sys
from pathlib import Path


def main() -> None:
    src = Path(__file__).resolve().parent.parent
    try:
        subprocess.run(
            [sys.executable, "-m", "watchfiles", "claude-tui", str(src)],
            check=True,
        )
    except KeyboardInterrupt:
        pass
