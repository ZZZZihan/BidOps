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


def referenced_files(db) -> dict[str, str]:
    files = {f"raw/{row[0]}": row[0] for row in db.execute("SELECT DISTINCT sha256 FROM snapshots")}
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "nd_attachments" in tables:
        files.update(
            {
                f"attachments/{row[0]}": row[0]
                for row in db.execute(
                    "SELECT DISTINCT raw_sha256 FROM nd_attachments WHERE raw_sha256 IS NOT NULL"
                )
            }
        )
    return files


def valid_path(name):
    parts = name.split("/")
    return (
        len(parts) == 2
        and parts[0] in {"raw", "attachments"}
        and len(parts[1]) == 64
        and all(c in "0123456789abcdef" for c in parts[1])
    )


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
            references = referenced_files(target)
    os.chmod(copied, 0o600)
    (destination / "raw").mkdir(mode=0o700)
    manifest = {"bidops.sqlite3": sha(copied)}
    for name, value in references.items():
        if not valid_path(name):
            raise ValueError("Invalid source hash")
        original = settings.data_dir / name
        if (
            original.is_symlink()
            or not original.resolve().is_relative_to(settings.data_dir.resolve())
            or sha(original) != value
        ):
            raise ValueError("Source integrity check failed")
        target = destination / name
        target.parent.mkdir(exist_ok=True, mode=0o700)
        shutil.copyfile(original, target)
        os.chmod(target, 0o600)
        manifest[name] = value
    (destination / "manifest.json").write_text(
        json.dumps(
            {
                "version": 2 if any(n.startswith("attachments/") for n in references) else 1,
                "files": manifest,
            },
            indent=2,
        )
    )
    os.chmod(destination / "manifest.json", 0o600)
    return {"files": len(manifest), "destination": str(destination)}


def restore(bundle: Path, destination: Path):
    manifest = json.loads((bundle / "manifest.json").read_text())
    if manifest.get("version") not in {1, 2} or "bidops.sqlite3" not in manifest.get("files", {}):
        raise ValueError("Invalid backup manifest")
    for name, expected in manifest["files"].items():
        if name != "bidops.sqlite3" and not valid_path(name):
            raise ValueError("Invalid backup path")
        path = bundle / name
        if (
            path.is_symlink()
            or not path.resolve().is_relative_to(bundle.resolve())
            or sha(path) != expected
        ):
            raise ValueError("Backup integrity check failed")
    # Validate the database/file closure before creating any usable restored DB.
    with sqlite3.connect(
        (bundle / "bidops.sqlite3").resolve().as_uri() + "?mode=ro", uri=True
    ) as db:
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("Backup database integrity check failed")
        references = referenced_files(db)
        if set(manifest["files"]) != {"bidops.sqlite3", *references}:
            raise ValueError("Backup file closure does not match database")
        if any(manifest["files"].get(name) != value for name, value in references.items()):
            raise ValueError("Backup is missing a referenced blob")
    destination.mkdir(parents=True, exist_ok=False, mode=0o700)
    (destination / "raw").mkdir(mode=0o700)
    for name in manifest["files"]:
        (destination / name).parent.mkdir(exist_ok=True, mode=0o700)
        shutil.copyfile(bundle / name, destination / name)
        os.chmod(destination / name, 0o600)
    with sqlite3.connect(destination / "bidops.sqlite3") as db:
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("Restored database integrity check failed")
        for name, value in referenced_files(db).items():
            if manifest["files"].get(name) != value:
                raise ValueError("Backup is missing a referenced blob")
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
