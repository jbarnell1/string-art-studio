"""The shipped web client is self-contained: no third-party hosts (ADR-026), and the bundled font
files the stylesheet names exist and are served."""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from stringart.config import Settings
from stringart.web.app import STATIC, create_app

PACKAGE = Path(__file__).resolve().parent.parent / "stringart"
TEXT_SUFFIXES = {".html", ".css", ".js", ".json", ".webmanifest", ".svg", ".txt", ".py", ".j2", ".jinja"}
FORBIDDEN = ("fonts.googleapis.com", "fonts.gstatic.com")


def _shipped_text_files() -> list[Path]:
    # Everything under web/static, plus any HTML or template anywhere in the package.
    files = {p for p in STATIC.rglob("*") if p.is_file() and p.suffix in TEXT_SUFFIXES}
    files |= {p for p in PACKAGE.rglob("*") if p.is_file() and p.suffix in {".html", ".j2", ".jinja"}}
    return sorted(files)


def test_there_are_files_to_scan():
    names = {p.name for p in _shipped_text_files()}
    assert {"index.html", "app.css", "app.js", "sw.js"} <= names


@pytest.mark.parametrize("path", _shipped_text_files(), ids=lambda p: str(p.relative_to(PACKAGE)))
def test_no_google_fonts_reference(path: Path):
    text = path.read_text(encoding="utf-8", errors="replace").lower()
    for host in FORBIDDEN:
        assert host not in text, f"{path} references {host}"


def _css_font_urls() -> list[str]:
    css = (STATIC / "app.css").read_text(encoding="utf-8")
    return re.findall(r'url\("?([^")]+\.woff2)"?\)', css)


def test_stylesheet_fonts_are_bundled():
    urls = _css_font_urls()
    assert len(urls) == 3
    for url in urls:
        assert not url.startswith(("http:", "https:", "//")), url
        assert (STATIC / url).is_file(), url
    assert (STATIC / "fonts" / "OFL.txt").is_file()


@pytest.fixture()
def client(tmp_path: Path):
    settings = Settings(data_dir=tmp_path / "data", host="127.0.0.1", port=0, root_path="", workers=1,
                        preview_res=320, render_res=500, allow_origins="", piper_url="", piper_voice="en_US-lessac-medium")
    with TestClient(create_app(settings)) as c:
        yield c


def test_fonts_are_served(client: TestClient):
    for url in _css_font_urls():
        r = client.get(f"/static/{url}")
        assert r.status_code == 200
        assert r.headers["content-type"] == "font/woff2"
        assert r.content[:4] == b"wOF2"
    page = client.get("/").text
    assert "static/fonts/IBMPlexSans-Regular-Latin1.woff2" in page
