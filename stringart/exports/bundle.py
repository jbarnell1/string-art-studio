"""Project bundle: a ZIP holding project.json, the source image, the mask and one JSON per run.

write_bundle and read_bundle are inverses. Reading validates the archive before
touching its members: project.json must exist, member paths may not escape the
archive root, and the total uncompressed size is capped.
"""
from __future__ import annotations

import copy
import io
import json
import posixpath
import re
import zipfile
from typing import Any

BUNDLE_FORMAT = "string-art-studio-bundle"
BUNDLE_VERSION = 1
MAX_UNCOMPRESSED_BYTES = 200 * 1024 * 1024

PROJECT_MEMBER = "project.json"
MASK_MEMBER = "mask.png"
SOURCE_PREFIX = "source."
SEQUENCES_DIR = "sequences"

_SAFE_ID = re.compile(r"[^A-Za-z0-9_.-]+")


class BundleError(ValueError):
    """Raised when a bundle is malformed or unsafe to read."""


def _normalise_ext(ext: str) -> str:
    ext = (ext or "bin").strip().lstrip(".").lower()
    ext = _SAFE_ID.sub("", ext)
    return ext or "bin"


def _run_id(run: dict[str, Any], index: int) -> str:
    raw = run.get("id") or run.get("run_id") or f"run{index + 1}"
    safe = _SAFE_ID.sub("_", str(raw)).strip("._")
    return safe or f"run{index + 1}"


def write_bundle(project: dict[str, Any], runs: list[dict[str, Any]], source_image: bytes | None,
                 mask_png: bytes | None, source_ext: str) -> bytes:
    """Pack a project and its runs into ZIP bytes.

    project.json carries the project dict (metadata, prep, board, engine and so
    on) plus a runs list where each run has its params, metrics and sequence.
    Each run is also written to sequences/<run_id>.json on its own.
    """
    manifest = copy.deepcopy(project) if project else {}
    manifest["format"] = BUNDLE_FORMAT
    manifest["bundle_version"] = BUNDLE_VERSION
    manifest["runs"] = []
    for index, run in enumerate(runs or []):
        entry = copy.deepcopy(run)
        entry["id"] = _run_id(run, index)
        entry.setdefault("params", {})
        entry.setdefault("metrics", {})
        entry["sequence"] = [int(p) for p in entry.get("sequence") or []]
        if entry.get("layers"):
            entry["layers"] = [{"name": str(l.get("name", "")), "color": str(l.get("color", "")),
                                "sequence": [int(p) for p in l.get("sequence") or []]} for l in entry["layers"]]
        manifest["runs"].append(entry)

    ext = _normalise_ext(source_ext)
    manifest["source_file"] = f"{SOURCE_PREFIX}{ext}" if source_image is not None else None
    manifest["mask_file"] = MASK_MEMBER if mask_png is not None else None

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(PROJECT_MEMBER, json.dumps(manifest, indent=2))
        if source_image is not None:
            zf.writestr(manifest["source_file"], source_image, compress_type=zipfile.ZIP_STORED)
        if mask_png is not None:
            zf.writestr(MASK_MEMBER, mask_png, compress_type=zipfile.ZIP_STORED)
        for entry in manifest["runs"]:
            zf.writestr(f"{SEQUENCES_DIR}/{entry['id']}.json", json.dumps({
                "run_id": entry["id"],
                "params": entry["params"],
                "metrics": entry["metrics"],
                "sequence": entry["sequence"],
                "layers": entry.get("layers"),
                "step_count": len(entry["sequence"]),
            }, indent=2))
    return buffer.getvalue()


def _check_member_path(name: str) -> None:
    if not name or name.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", name):
        raise BundleError(f"unsafe member path {name!r}")
    normalised = name.replace("\\", "/")
    parts = normalised.split("/")
    if ".." in parts or any(p == "" for p in parts[:-1]):
        raise BundleError(f"unsafe member path {name!r}")
    if posixpath.normpath(normalised).startswith(".."):
        raise BundleError(f"unsafe member path {name!r}")


def _validate(zf: zipfile.ZipFile) -> None:
    total = 0
    names = []
    for info in zf.infolist():
        _check_member_path(info.filename)
        total += info.file_size
        if total > MAX_UNCOMPRESSED_BYTES:
            raise BundleError(f"bundle exceeds {MAX_UNCOMPRESSED_BYTES} bytes uncompressed")
        names.append(info.filename)
    if PROJECT_MEMBER not in names:
        raise BundleError(f"bundle has no {PROJECT_MEMBER}")


def read_bundle(data: bytes) -> dict[str, Any]:
    """Unpack ZIP bytes made by write_bundle.

    Returns {"project": dict, "runs": list, "source": bytes or None,
    "source_ext": str, "mask": bytes or None}. Raises BundleError when the
    archive is not a zip, lacks project.json, has unsafe member paths or is
    too large.
    """
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise BundleError("not a zip archive") from exc

    with zf:
        _validate(zf)
        try:
            project = json.loads(zf.read(PROJECT_MEMBER).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BundleError(f"{PROJECT_MEMBER} is not valid JSON") from exc
        if not isinstance(project, dict):
            raise BundleError(f"{PROJECT_MEMBER} must hold an object")

        names = set(zf.namelist())
        runs = project.get("runs") or []
        if not isinstance(runs, list):
            raise BundleError("runs must be a list")

        # Fill in any sequence missing from project.json from its sequences/ file.
        for index, run in enumerate(runs):
            if not isinstance(run, dict):
                raise BundleError("each run must be an object")
            if not run.get("sequence"):
                member = f"{SEQUENCES_DIR}/{_run_id(run, index)}.json"
                if member in names:
                    data = json.loads(zf.read(member).decode("utf-8"))
                    run["sequence"] = data.get("sequence", [])
                    if data.get("layers"):
                        run["layers"] = data["layers"]

        source = None
        source_ext = ""
        source_name = project.get("source_file")
        if not (isinstance(source_name, str) and source_name in names):
            candidates = sorted(n for n in names if n.startswith(SOURCE_PREFIX) and "/" not in n)
            source_name = candidates[0] if candidates else None
        if source_name:
            source = zf.read(source_name)
            source_ext = source_name[len(SOURCE_PREFIX):]

        mask = zf.read(MASK_MEMBER) if MASK_MEMBER in names else None

    return {
        "project": project,
        "runs": runs,
        "source": source,
        "source_ext": source_ext,
        "mask": mask,
    }
