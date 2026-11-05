"""Runtime settings from environment variables (every one has a default that works)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    host: str
    port: int
    root_path: str
    workers: int            # concurrent generation jobs
    preview_res: int        # editor live preview size
    render_res: int         # final preview render size
    allow_origins: str
    piper_url: str          # Piper HTTP server for build-mode voice ("" = browser voice only)
    piper_voice: str        # default Piper voice name

    @property
    def db_path(self) -> Path:
        return self.data_dir / "studio.sqlite3"

    @property
    def projects_dir(self) -> Path:
        return self.data_dir / "projects"


def load_settings() -> Settings:
    data_dir = Path(os.environ.get("SAS_DATA_DIR", "data")).resolve()
    return Settings(
        data_dir=data_dir,
        host=os.environ.get("SAS_HOST", "127.0.0.1"),
        port=_int("SAS_PORT", 8050),
        root_path=os.environ.get("SAS_ROOT_PATH", ""),
        workers=max(1, _int("SAS_WORKERS", 2)),
        preview_res=max(200, _int("SAS_PREVIEW_RES", 640)),
        render_res=max(400, _int("SAS_RENDER_RES", 1600)),
        allow_origins=os.environ.get("SAS_ALLOW_ORIGINS", ""),
        piper_url=os.environ.get("SAS_PIPER_URL", "").strip(),
        piper_voice=os.environ.get("SAS_PIPER_VOICE", "en_US-lessac-medium").strip() or "en_US-lessac-medium",
    )
