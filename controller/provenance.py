"""Freeze executable project files and record exactly what a run used."""
import hashlib
import json
from pathlib import Path
import shutil

SOURCE_EXTENSIONS = {".robot", ".resource", ".py", ".yaml", ".yml", ".txt", ".csv", ".xlsx", ".json", ".ini"}


def snapshot_sources(source: Path, destination: Path):
    source = source.resolve()
    destination.mkdir(parents=True, exist_ok=False)
    manifest = []
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source)
        if any(part in {".git", ".venv", "__pycache__"} for part in relative.parts):
            continue
        if path.is_symlink():
            raise ValueError(f"Source snapshots do not support symlinks: {relative}")
        if not path.is_file() or path.suffix.lower() not in SOURCE_EXTENSIONS:
            continue
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        # Hash the frozen copy, which is the file Robot will actually execute.
        with target.open("rb") as frozen:
            digest = hashlib.file_digest(frozen, "sha256").hexdigest()
        manifest.append({"path": relative.as_posix(), "sha256": digest, "bytes": target.stat().st_size})
    (destination.parent / "source-manifest.json").write_text(
        json.dumps({"files": manifest}, indent=2), encoding="utf-8")
    return destination
