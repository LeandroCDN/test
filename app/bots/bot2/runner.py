"""Blocking CLI runner for bot 2."""

from __future__ import annotations

import time

from app.bots.bot2.manager import bot2_manager


def run_bot(dry_run: bool = False) -> None:
    ok, message = bot2_manager.start(dry_run=dry_run)
    if not ok:
        raise RuntimeError(message)

    try:
        while bot2_manager.status in {"starting", "running", "stopping"}:
            time.sleep(1)
    except KeyboardInterrupt:
        bot2_manager.stop()
        while bot2_manager.status != "stopped":
            time.sleep(0.5)
