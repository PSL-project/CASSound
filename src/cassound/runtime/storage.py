"""Transactional project archives. Untrusted archives are data, never Python objects."""

import json
import hashlib
import os
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import stat
import zipfile
from .cache import digest_file
from .media import import_audio
from ..domain.errors import ValidationError, IntegrityError, ResourceLimit

MAX_STATE = 32 * 1024**2
MAX_ENTRIES = 10000


def save_project(payload, root, stage, limits, progress):
    token = payload["snapshot_token"]
    if len(token) != 32 or any(c not in "0123456789abcdef" for c in token):
        raise ValidationError("Invalid snapshot reference.")
    snapshot_file = root / "requests" / (token + ".json")
    if snapshot_file.stat().st_size > MAX_STATE:
        raise ResourceLimit("Project metadata is too large.")
    snapshot = json.loads(snapshot_file.read_text())
    destination = Path(payload["path"]).expanduser().resolve()
    if any(destination == Path(a["locator"]).resolve() for a in snapshot["assets"]):
        raise ValidationError("A project cannot overwrite a source file.")
    if destination.exists() and not payload.get("overwrite", True):
        raise ValidationError("Project destination already exists.")
    db = stage / "state.sqlite"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE document (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        conn.execute("INSERT INTO document VALUES (?, ?)", ("project", json.dumps(snapshot, allow_nan=False)))
        conn.execute("PRAGMA user_version=1")
    manifest = {
        "schema_version": 1,
        "project_id": snapshot["project_id"],
        "application_version": "1.0.0.dev3",
        "state_path": "state.sqlite",
        "state_sha256": digest_file(db),
        "assets": [],
    }
    archive = stage / "project.cassound"
    total = db.stat().st_size
    for a in snapshot["assets"]:
        if payload.get("portable"):
            source = Path(a["locator"])
            if not source.is_file() or digest_file(source, progress) != a["sha256"]:
                raise IntegrityError(
                    "Portable save requires every original source to match its recorded hash."
                )
            total += source.stat().st_size
        manifest["assets"].append(
            {
                "asset_id": a["asset_id"],
                "sha256": a["sha256"],
                "embedded": bool(payload.get("portable")),
                "locator": "sources/" + a["sha256"] if payload.get("portable") else a["locator"],
            }
        )
    if total > limits.get("project_bytes", 20 * 1024**3):
        raise ResourceLimit("Portable project exceeds the project size budget.")
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as z:
        z.write(db, "state.sqlite")
        seen = set()
        for i, (asset, entry) in enumerate(zip(snapshot["assets"], manifest["assets"])):
            if entry["embedded"] and entry["locator"] not in seen:
                copied = 0
                digest = hashlib.sha256()
                with (
                    Path(asset["locator"]).open("rb") as incoming,
                    z.open(entry["locator"], "w", force_zip64=True) as outgoing,
                ):
                    while block := incoming.read(1024 * 1024):
                        outgoing.write(block)
                        digest.update(block)
                        copied += len(block)
                        progress("project.embed", copied / max(1, Path(asset["locator"]).stat().st_size))
                if digest.hexdigest() != asset["sha256"]:
                    raise IntegrityError(
                        "Source changed while embedding; destination project was not replaced."
                    )
                seen.add(entry["locator"])
            progress("project.save", (i + 1) / max(1, len(snapshot["assets"])))
        z.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, allow_nan=False))
    return {
        "staged_path": str(archive),
        "destination": str(destination),
        "overwrite": payload.get("overwrite", True),
        "project_id": snapshot["project_id"],
        "sha256": digest_file(archive),
        "portable": bool(payload.get("portable")),
    }


def safe_archive(z, limit):
    entries = z.infolist()
    if len(entries) > MAX_ENTRIES:
        raise ResourceLimit("Project archive has too many entries.")
    names = set()
    total = 0
    for entry in entries:
        name = entry.filename
        path = PurePosixPath(name)
        if (
            not name
            or name in names
            or "\\" in name
            or ":" in name
            or path.is_absolute()
            or ".." in path.parts
            or "." in name.split("/")
            or stat.S_ISLNK(entry.external_attr >> 16)
            or entry.is_dir()
            or entry.flag_bits & 1
        ):
            raise ValidationError("Unsafe, duplicate, encrypted or unsupported project entry.")
        names.add(name)
        total += entry.file_size
        if total > limit or entry.file_size > max(1, entry.compress_size) * 2000:
            raise ResourceLimit("Project archive exceeds expansion bounds.")
    if not {"manifest.json", "state.sqlite"}.issubset(names):
        raise ValidationError("Project manifest/database is missing.")
    if z.getinfo("manifest.json").file_size > MAX_STATE or z.getinfo("state.sqlite").file_size > MAX_STATE:
        raise ResourceLimit("Project metadata is too large.")
    return names


def open_project(payload, root, stage, limits, progress):
    project_path = Path(payload["path"]).expanduser().resolve()
    if not project_path.is_file():
        raise ValidationError("Project file was not found.")
    try:
        z = zipfile.ZipFile(project_path)
    except zipfile.BadZipFile as exc:
        raise ValidationError("Not a valid CASSound project archive.") from exc
    with z:
        names = safe_archive(z, limits.get("project_bytes", 20 * 1024**3))
        manifest = json.loads(z.read("manifest.json"))
        if manifest.get("schema_version") != 1:
            raise ValidationError("Unsupported project schema; use a compatible CASSound version.")
        if manifest.get("state_path") != "state.sqlite" or not isinstance(manifest.get("assets"), list):
            raise ValidationError("Invalid project manifest.")
        db = stage / "state.sqlite"
        db.write_bytes(z.read("state.sqlite"))
        if digest_file(db) != manifest.get("state_sha256"):
            raise IntegrityError("Project database does not match its manifest hash.")
        with sqlite3.connect(db.as_uri() + "?mode=ro&immutable=1", uri=True) as conn:
            conn.execute("PRAGMA trusted_schema=OFF")
            if conn.execute("PRAGMA user_version").fetchone()[0] != 1:
                raise ValidationError("Unsupported project database version.")
            schema = conn.execute(
                "SELECT type,name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
            ).fetchall()
            if schema != [("table", "document")]:
                raise ValidationError("Unexpected project database objects.")
            row = conn.execute("SELECT value FROM document WHERE key=?", ("project",)).fetchone()
            if not row or len(row[0]) > MAX_STATE:
                raise ValidationError("Project state is missing or too large.")
            snapshot = json.loads(row[0])
        if snapshot.get("project_id") != manifest.get("project_id") or not isinstance(
            snapshot.get("assets"), list
        ):
            raise IntegrityError("Project identity mismatch.")
        entries = manifest["assets"]
        if len(entries) != len(snapshot["assets"]) or len({e["asset_id"] for e in entries}) != len(entries):
            raise ValidationError("Project has conflicting asset identities.")
        allowed = {"manifest.json", "state.sqlite"} | {e["locator"] for e in entries if e["embedded"]}
        if names != allowed:
            raise ValidationError("Archive contains unexpected entries.")
        missing = []
        for i, (asset, entry) in enumerate(zip(snapshot["assets"], entries)):
            if entry["asset_id"] != asset["asset_id"] or entry["sha256"] != asset["sha256"]:
                raise IntegrityError("Project asset inventory mismatch.")
            if len(asset["sha256"]) != 64 or any(c not in "0123456789abcdef" for c in asset["sha256"]):
                raise ValidationError("Invalid source digest.")
            if entry["embedded"]:
                if entry["locator"] != "sources/" + asset["sha256"]:
                    raise ValidationError("Unexpected embedded source locator.")
                source_dir = root / "sources"
                source_dir.mkdir(exist_ok=True)
                source = source_dir / asset["sha256"]
                temp = stage / ("source-" + str(i))
                with z.open(entry["locator"]) as incoming, temp.open("wb") as out:
                    shutil.copyfileobj(incoming, out, 1024 * 1024)
                if digest_file(temp, progress) != asset["sha256"]:
                    raise IntegrityError("Embedded source hash mismatch.")
                os.replace(temp, source)
            else:
                source = Path(entry["locator"]).expanduser()
                if not source.is_absolute():
                    source = project_path.parent / source
                if not source.is_file():
                    missing.append(asset["asset_id"])
                    continue
                if digest_file(source, progress) != asset["sha256"]:
                    missing.append(asset["asset_id"])
                    continue
            imported = import_audio(
                str(source), asset["stream_index"], root, stage / str(i), limits, progress
            )
            if asset.get("render_identity"):
                from ..domain.types import Asset

                Asset.from_dict(asset)
                if (
                    imported["sha256"] != asset["sha256"]
                    or imported["sample_rate"] != asset["sample_rate"]
                    or imported["channels"] != asset["channels"]
                ):
                    raise IntegrityError("Rendered source specification mismatch.")
                imported.update(
                    render_identity=asset["render_identity"],
                    asset_id=asset["asset_id"],
                    original_revision_id=asset["original_revision_id"],
                )
            if imported["asset_id"] != asset["asset_id"] or imported["frames"] != asset["frames"]:
                raise IntegrityError("Decoded source identity/length differs from saved project.")
            imported["name"] = asset.get("name") or Path(asset["locator"]).name
            snapshot["assets"][i] = imported
            progress("project.open", (i + 1) / max(1, len(entries)))
    return {"snapshot": snapshot, "missing_sources": missing, "path": str(project_path)}
