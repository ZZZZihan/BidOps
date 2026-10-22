"""Consistent offline SQLite/file backups and fail-closed private restoration."""

import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
import time
from pathlib import Path

from sqlalchemy.engine import make_url

from bidops.config import Settings
from bidops.db import Database
from bidops.enterprise.operations import SingleWorker


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def referenced_files(db) -> dict[str, str]:
    files = {f"raw/{r[0]}": r[0] for r in db.execute("SELECT DISTINCT sha256 FROM snapshots")}
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "company_material_revisions" in tables:
        for blob, raw_sha, normalized, text_sha in db.execute(
            "SELECT blob_path,sha256,text_path,text_sha256 FROM company_material_revisions"
        ):
            for name, value in ((blob, raw_sha), (normalized, text_sha)):
                name = "private/company/" + name
                if name in files and files[name] != value:
                    raise ValueError("Conflicting private file references")
                files[name] = value
    return files


def valid_path(name):
    return isinstance(name, str) and bool(
        re.fullmatch(r"raw/[a-f0-9]{64}|private/company/(blob|text)/[a-f0-9]{32}", name)
    )


def verify_path(root, name, digest):
    if not valid_path(name) and name != "bidops.sqlite3":
        raise ValueError("Invalid backup path")
    if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
        raise ValueError("Invalid backup hash")
    path = root / name
    if (
        path.is_symlink()
        or not path.resolve().is_relative_to(root.resolve())
        or not path.is_file()
        or sha(path) != digest
    ):
        raise ValueError("Backup integrity check failed")
    return path


def backup(settings: Settings, destination: Path, *, retention_days: int = 7):
    if isinstance(retention_days, bool) or not 1 <= retention_days <= 30:
        raise ValueError("Backup retention must be between 1 and 30 days")
    database = Path(make_url(settings.database_url).database).resolve()
    if not database.is_file():
        raise ValueError("Database does not exist; refusing an empty backup")
    # The same lock is held throughout enterprise service lifetime and maintenance.
    # Stop the application before backup; do not race private deletion with copying.
    worker = SingleWorker(settings.database_url)
    try:
        destination.mkdir(parents=True, exist_ok=False, mode=0o700)
        copied = destination / "bidops.sqlite3"
        with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as source:
            with sqlite3.connect(copied) as target:
                source.backup(target)
                if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise ValueError("Database integrity check failed")
                references = referenced_files(target)
        copied.chmod(0o600)
        (destination / "raw").mkdir(mode=0o700)
        manifest = {"bidops.sqlite3": sha(copied)}
        for name, value in references.items():
            original = verify_path(settings.data_dir, name, value)
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            shutil.copyfile(original, target)
            target.chmod(0o600)
            manifest[name] = value
        created = time.time()
        metadata = {
            "version": 2,
            "created_at": created,
            "expires_at": created + retention_days * 86400,
            "private_restore_policy": "quarantine_all_objects_remove_all_grants",
            "files": manifest,
        }
        (destination / "manifest.json").write_text(json.dumps(metadata, indent=2))
        (destination / "manifest.json").chmod(0o600)
        return {
            "files": len(manifest),
            "destination": str(destination),
            "expires_at": metadata["expires_at"],
            "requires_stopped_service": True,
        }
    finally:
        worker.close()


def restore(bundle: Path, destination: Path):
    metadata_path = bundle / "manifest.json"
    if metadata_path.is_symlink() or metadata_path.stat().st_size > 20_000_000:
        raise ValueError("Invalid backup manifest")
    manifest = json.loads(metadata_path.read_text())
    if manifest.get("version") not in {1, 2} or not isinstance(manifest.get("files"), dict):
        raise ValueError("Invalid backup manifest")
    if "bidops.sqlite3" not in manifest["files"]:
        raise ValueError("Invalid backup manifest")
    if manifest["version"] == 2:
        created, expires = manifest.get("created_at"), manifest.get("expires_at")
        if (
            type(created) not in {float, int}
            or type(expires) not in {float, int}
            or not 0 < expires - created <= 30 * 86400
            or not created <= time.time() < expires
        ):
            raise ValueError("Backup expired or invalid retention window")
    for name, value in manifest["files"].items():
        verify_path(bundle, name, value)
    with sqlite3.connect(
        (bundle / "bidops.sqlite3").resolve().as_uri() + "?mode=ro", uri=True
    ) as db:
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("Backup database integrity check failed")
        references = referenced_files(db)
        if any(name.startswith("private/") for name in references) and manifest["version"] != 2:
            raise ValueError("Private backup requires a bounded retention window")
        if set(manifest["files"]) != {"bidops.sqlite3", *references}:
            raise ValueError("Backup file closure does not match database")
        if any(manifest["files"].get(name) != value for name, value in references.items()):
            raise ValueError("Backup is missing a referenced blob")
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".bidops-restore-", dir=destination.parent))
    try:
        (staging / "raw").mkdir(mode=0o700)
        for name in manifest["files"]:
            target = staging / name
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            shutil.copyfile(bundle / name, target)
            target.chmod(0o600)
        with sqlite3.connect(staging / "bidops.sqlite3") as conn:
            tables = {
                r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            # These are all credential-bearing/session snapshot stores, including
            # the current identity cache. A restored IdP snapshot is never fresh.
            for table in (
                "enterprise_login_identities",
                "enterprise_logins",
                "enterprise_oauth_state",
            ):
                if table in tables:
                    conn.execute(f"DELETE FROM {table}")
            if "enterprise_subjects" in tables:
                conn.execute("UPDATE enterprise_subjects SET fresh_until=0")
            conn.commit()
        quarantined = 0
        if "company_objects" in tables:
            from bidops.company import CompanyService
            from bidops.company.retention import prune_company_materials, quarantine_after_restore

            db = Database(f"sqlite:///{staging}/bidops.sqlite3")
            try:
                quarantined = quarantine_after_restore(db)["quarantined_objects"]
                result = prune_company_materials(CompanyService(db, staging))
                if result["status"] != "PASS":
                    raise ValueError("Restored private retention cleanup failed")
            finally:
                db.engine.dispose()
        # Claim the destination only after all security transformations succeed.
        destination.mkdir(mode=0o700, exist_ok=False)
        for child in staging.iterdir():
            os.rename(child, destination / child.name)
        return {
            "files": len(manifest["files"]),
            "destination": str(destination),
            "logins_revoked": True,
            "quarantined_objects": quarantined,
            "old_private_grants_removed": True,
        }
    finally:
        shutil.rmtree(staging)
