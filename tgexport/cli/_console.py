"""Single shared Rich console.

Log output (RichHandler) and live progress displays must render through the
same Console instance: with two consoles on the same stream every log line
tears the live progress frame, spamming stale spinner frames into scrollback.
"""

from __future__ import annotations

from rich.console import Console

console = Console()
