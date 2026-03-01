"""
Standalone launcher to run the bot from CLI exactly as before.
Preserves backward-compatibility with: python scripts/run_bot.py [--dry-run]
"""

import os
import sys

base = os.path.normpath(os.path.join(os.path.dirname(__file__), os.pardir, "app"))
for sub in ("", "services", "bot"):
    d = os.path.normpath(os.path.join(base, sub))
    if d not in sys.path:
        sys.path.insert(0, d)

from bot.bot import run_bot

if __name__ == "__main__":
    dry = "--dry-run" in sys.argv or "--dry" in sys.argv
    run_bot(dry_run=dry)
