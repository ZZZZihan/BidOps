"""SQLite's online backup API plus the immutable, hash-addressed source files."""

import hashlib
import json
import os
import shutil
import sqlite3
from pathlib import Path

from sqlalchemy.engine import make_url

from bidops.config import Settings


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def backup(settings: Settings, destination: Path):
    database = Path(make_url(settings.database_url).database).resolve()
    if not database.is_file():
        raise ValueError("Database does not exist; refusing an empty backup")
    destination.mkdir(parents=True, exist_ok=False, mode=0o700)
    copied = destination / "bidops.sqlite3"
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as source:
        with sqlite3.connect(copied) as target:
            source.backup(target)
            if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("Database integrity check failed")
            hashes = [r[0] for r in target.execute("SELECT DISTINCT sha256 FROM snapshots")]
    os.chmod(copied, 0o600)
    (destination / "raw").mkdir(mode=0o700)
    manifest = {"bidops.sqlite3": sha(copied)}
    for value in hashes:
        if len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
            raise ValueError("Invalid source hash")
        original = settings.data_dir / "raw" / value
        if original.is_symlink() or sha(original) != value:
            raise ValueError("Source integrity check failed")
        target = destination / "raw" / value
        shutil.copyfile(original, target)
        os.chmod(target, 0o600)
        manifest[f"raw/{value}"] = value
    (destination / "manifest.json").write_text(
        json.dumps({"version": 1, "files": manifest}, indent=2)
    )
    os.chmod(destination / "manifest.json", 0o600)
    return {"files": len(manifest), "destination": str(destination)}


def restore(bundle: Path, destination: Path):
    manifest = json.loads((bundle / "manifest.json").read_text())
    if manifest.get("version") != 1 or "bidops.sqlite3" not in manifest.get("files", {}):
        raise ValueError("Invalid backup manifest")
    for name, expected in manifest["files"].items():
        valid_raw = (
            name.startswith("raw/")
            and len(name) == 68
            and all(c in "0123456789abcdef" for c in name[4:])
        )
        if name != "bidops.sqlite3" and not valid_raw:
            raise ValueError("Invalid backup path")
        path = bundle / name
        if (
            path.is_symlink()
            or not path.resolve().is_relative_to(bundle.resolve())
            or sha(path) != expected
        ):
            raise ValueError("Backup integrity check failed")
    destination.mkdir(parents=True, exist_ok=False, mode=0o700)
    (destination / "raw").mkdir(mode=0o700)
    for name in manifest["files"]:
        shutil.copyfile(bundle / name, destination / name)
        os.chmod(destination / name, 0o600)
    with sqlite3.connect(destination / "bidops.sqlite3") as db:
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("Restored database integrity check failed")
        for (value,) in db.execute("SELECT DISTINCT sha256 FROM snapshots"):
            if manifest["files"].get(f"raw/{value}") != value:
                raise ValueError("Backup is missing a referenced raw snapshot")
        # A restored deployment must require new SSO login; old callbacks cannot be reused.
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "enterprise_logins" in tables:
            db.execute("DELETE FROM enterprise_logins")
        if "enterprise_oauth_state" in tables:
            db.execute("DELETE FROM enterprise_oauth_state")
        db.commit()
    return {
        "files": len(manifest["files"]),
        "destination": str(destination),
        "logins_revoked": True,
    }
