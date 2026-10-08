"""Create local source checkpoints without copying credentials or deleting files."""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import subprocess
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def snapshot(version: str):
    if version not in ("v2", "v3"):
        raise ValueError("Supported versions: v2, v3")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    folder = ROOT / ".toolchain" / "versions"
    folder.mkdir(parents=True, exist_ok=True)
    prefix = folder / f"{version}-source-{stamp}"
    paths = subprocess.check_output(["git", "-c", "core.quotepath=false", "ls-files", "-c", "-o", "--exclude-standard", "-z"], cwd=ROOT).decode("utf-8").split("\0")
    required = {f"agent_{version}/__init__.py",f"agent_{version}/__main__.py"}
    if version=='v3':required.update({'agent_v3/browser_worker.cjs','agent_v3/windows_ocr.ps1'})
    missing = required-set(paths)
    if missing:
        raise ValueError("Package entry points excluded from source snapshot: "+", ".join(sorted(missing)))
    allowed = {".py", ".ts", ".tsx", ".js", ".cjs", ".ps1", ".css", ".html", ".md", ".toml", ".txt", ".svg", ".json", ".yaml", ".yml", ".sh", ".cmd", ".bat"}
    files = []
    with zipfile.ZipFile(str(prefix)+".zip", "x", zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(set(paths)):
            path = ROOT / name
            lowered = name.lower()
            if not path.is_file() or path.suffix not in allowed:
                continue
            if any(part in lowered for part in ("node_modules/", "data/", "auth_users", ".env", "cookie", "token", "secret", "credential")):
                continue
            if path.name.startswith("config.") and path.name not in ("config.example.json",):
                continue
            blob = path.read_bytes()
            archive.writestr(name, blob)
            files.append({"path":name,"bytes":len(blob),"sha256":hashlib.sha256(blob).hexdigest()})
    if required-{f['path'] for f in files}:raise ValueError('Source checkpoint omitted required runtime files')
    state = ROOT / "data" / version / "agent.sqlite3"
    database = None
    if state.is_file():
        database = folder / f"{version}-runtime-{stamp}.sqlite3"
        with sqlite3.connect(state.as_uri()+"?mode=ro", uri=True) as source, sqlite3.connect(database) as target:
            source.backup(target)
    manifest = {"version":version,"created_at":datetime.now(timezone.utc).isoformat(),
                "archive":str(prefix)+".zip","archive_sha256":hashlib.sha256(Path(str(prefix)+".zip").read_bytes()).hexdigest(),
                "runtime_backup":str(database) if database else None,"files":files,
                "scope":"Current working source and separate version SQLite backup; raw data, private configuration, credentials and dependencies excluded. Not a complete environment backup."}
    Path(str(prefix)+".manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding="utf-8")
    return {k:v for k,v in manifest.items() if k!="files"} | {"file_count":len(files),"manifest":str(prefix)+".manifest.json"}


if __name__ == "__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("version", choices=("v2","v3"))
    print(json.dumps(snapshot(parser.parse_args().version),ensure_ascii=False,indent=2))
