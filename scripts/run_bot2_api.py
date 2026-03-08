"""Launch the isolated API for bot 2."""

from __future__ import annotations

import os
import sys

import uvicorn

PROJECT_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), os.pardir))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


if __name__ == "__main__":
    port = int(os.getenv("BOT2_API_PORT", "8001"))
    uvicorn.run("app.api.bot2_main:app", host="0.0.0.0", port=port, reload=False)
