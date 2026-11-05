"""Server-side voice for build mode: Piper over HTTP with a clip cache on disk.

Piper runs as its own container (deploy/piper) and speaks the piper1 HTTP API: POST /synthesize
with {"text", "voice", "length_scale"} returns a WAV, GET /voices lists what is installed. Every
clip is cached under data/voice/<voice>/<key>.wav, so a pin number is synthesised once per voice
and speed and then served from disk for every build on every device. The browser's own speech
synthesis stays as the fallback when no Piper is configured or reachable.
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

log = logging.getLogger("stringart.voice")

SPEEDS = (0.8, 1.0, 1.25)


def pin_phrase(pin: int, label: str) -> str:
    """What the voice says for a pin: the number, then the sector label spelled out ("C 14")."""
    spaced = label
    if len(label) > 1 and label[0].isalpha() and label[1:].isdigit():
        spaced = f"{label[0]} {label[1:]}"
    return f"Pin {pin}. {spaced}."


EXTRA_PHRASES = ("Voice on.", "Finished. Tie off the thread.", "Paused.", "Resuming.")


class VoiceService:
    def __init__(self, piper_url: str, cache_dir: Path, default_voice: str = "en_US-lessac-medium"):
        self.url = (piper_url or "").rstrip("/")
        self.cache_dir = cache_dir
        self.default_voice = default_voice
        self._status: dict[str, Any] = {"checked": 0.0, "voices": [], "ok": False}
        self._lock = threading.Lock()
        self._prewarm: dict[str, dict[str, Any]] = {}

    # Status --------------------------------------------------------------------------------------

    def configured(self) -> bool:
        return bool(self.url)

    def status(self, max_age_s: float = 60.0) -> dict[str, Any]:
        """{"available": bool, "voices": [{"name", "language"}], "default": name}. Cached briefly
        so build mode can ask freely; a failed check is retried sooner than a good one."""
        if not self.url:
            return {"available": False, "voices": [], "default": self.default_voice, "configured": False}
        now = time.time()
        with self._lock:
            st = self._status
            age = now - st["checked"]
            fresh = age < (max_age_s if st["ok"] else 15.0)
        if not fresh:
            voices, ok = self._fetch_voices()
            with self._lock:
                self._status = {"checked": time.time(), "voices": voices, "ok": ok}
        with self._lock:
            st = dict(self._status)
        names = [v["name"] for v in st["voices"]]
        default = self.default_voice if self.default_voice in names else (names[0] if names else self.default_voice)
        return {"available": bool(st["ok"] and names), "voices": st["voices"], "default": default, "configured": True}

    def _fetch_voices(self) -> tuple[list[dict[str, str]], bool]:
        try:
            with urllib.request.urlopen(self.url + "/voices", timeout=4) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, ValueError) as err:
            log.info("piper not reachable at %s: %s", self.url, err)
            return [], False
        voices: list[dict[str, str]] = []
        items = data.items() if isinstance(data, dict) else [(v if isinstance(v, str) else v.get("name", ""), v) for v in data]
        for name, info in items:
            if not name:
                continue
            lang = ""
            if isinstance(info, dict):
                language = info.get("language") or {}
                lang = language.get("name_english") or language.get("code") or "" if isinstance(language, dict) else str(language)
            voices.append({"name": str(name), "language": str(lang)})
        voices.sort(key=lambda v: v["name"])
        return voices, True

    # Clips ---------------------------------------------------------------------------------------

    def _clip_path(self, text: str, voice: str, speed: float) -> Path:
        key = hashlib.sha1(f"{voice}|{speed:.2f}|{text}".encode("utf-8")).hexdigest()[:24]
        safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in voice)[:60] or "voice"
        return self.cache_dir / safe / f"{key}.wav"

    def clip(self, text: str, voice: str | None = None, speed: float = 1.0) -> bytes:
        """WAV bytes for the phrase, from the cache or freshly synthesised. Raises VoiceError."""
        text = " ".join(text.split())[:200]
        if not text:
            raise VoiceError("empty phrase")
        voice = voice or self.status()["default"]
        speed = float(min(max(speed, 0.5), 2.0))
        path = self._clip_path(text, voice, speed)
        if path.exists():
            return path.read_bytes()
        if not self.url:
            raise VoiceError("no voice server configured")
        body = json.dumps({"text": text, "voice": voice, "length_scale": round(1.0 / speed, 3)}).encode("utf-8")
        req = urllib.request.Request(self.url + "/synthesize", data=body, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = resp.read()
        except urllib.error.HTTPError as err:
            raise VoiceError(f"voice server error {err.code}") from err
        except (urllib.error.URLError, OSError) as err:
            raise VoiceError(f"voice server unreachable: {err}") from err
        if not data.startswith(b"RIFF"):
            raise VoiceError("voice server returned something that is not a WAV")
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(data)
        tmp.replace(path)
        return data

    # Prewarm -------------------------------------------------------------------------------------

    def prewarm(self, phrases: list[str], voice: str | None, speed: float) -> dict[str, Any]:
        """Render every phrase in the background (once per voice and speed). Returns the job state."""
        voice = voice or self.status()["default"]
        speed = float(min(max(speed, 0.5), 2.0))
        key = hashlib.sha1(("|".join(phrases) + f"|{voice}|{speed:.2f}").encode("utf-8")).hexdigest()[:16]
        with self._lock:
            job = self._prewarm.get(key)
            if job is None:
                job = {"id": key, "total": len(phrases), "done": 0, "failed": 0, "finished": False, "voice": voice, "speed": speed}
                self._prewarm[key] = job
                threading.Thread(target=self._run_prewarm, args=(job, list(phrases)), daemon=True, name="voice-prewarm").start()
            return dict(job)

    def prewarm_status(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._prewarm.get(job_id)
            return dict(job) if job else None

    def _run_prewarm(self, job: dict[str, Any], phrases: list[str]) -> None:
        for text in phrases:
            try:
                self.clip(text, job["voice"], job["speed"])
            except VoiceError as err:
                with self._lock:
                    job["failed"] += 1
                if job["failed"] > 5:
                    log.warning("voice prewarm stopped: %s", err)
                    break
            with self._lock:
                job["done"] += 1
        with self._lock:
            job["finished"] = True


class VoiceError(Exception):
    pass
