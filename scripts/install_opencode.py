"""Install a pinned official OpenCode CLI locally, verifying npm integrity.

Does not modify a user's OpenCode Desktop or global configuration, and does
not delete or replace existing installations.
"""
import base64
import hashlib
import io
import json
from pathlib import Path
import platform
import tarfile
import time
import urllib.request

VERSION = "1.18.35"
ROOT = Path(__file__).resolve().parents[1]


def main():
    if platform.system() != "Windows" or platform.machine().lower() not in ("amd64", "x86_64"):
        raise SystemExit("This installer supports Windows x64; install the official CLI and set V3_OPENCODE_BIN on other systems.")
    folder = ROOT / ".toolchain/opencode" / VERSION
    target = folder / "opencode.exe"
    manifest_path = folder / "install-manifest.json"
    if target.exists():
        if not manifest_path.exists():
            raise SystemExit("Existing executable has no integrity manifest; choose V3_OPENCODE_BIN without overwriting it.")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if hashlib.sha256(target.read_bytes()).hexdigest() != manifest["executable_sha256"]:
            raise SystemExit("Existing executable failed its recorded SHA-256 verification.")
        print(json.dumps({"version": VERSION, "status": "already_installed", "integrity_verified": True}))
        return
    package = "opencode-windows-x64"
    with urllib.request.urlopen(f"https://registry.npmjs.org/{package}/{VERSION}", timeout=30) as response:
        metadata = json.load(response)
    assert metadata["name"] == package and metadata["version"] == VERSION
    dist = metadata["dist"]
    assert dist["tarball"].startswith("https://registry.npmjs.org/" + package + "/")
    started = time.monotonic()
    blob = bytearray()
    with urllib.request.urlopen(dist["tarball"], timeout=30) as response:
        while chunk := response.read(1024 * 1024):
            blob.extend(chunk)
            if len(blob) > 250000000 or time.monotonic() - started > 240:
                raise SystemExit("Download exceeded its time or size budget; no executable was written.")
    algorithm, digest = dist["integrity"].split("-", 1)
    if algorithm != "sha512" or base64.b64encode(hashlib.sha512(blob).digest()).decode() != digest:
        raise SystemExit("Official package integrity check failed; no executable was written.")
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as archive:
        member = archive.getmember("package/bin/opencode.exe")
        if not member.isfile():
            raise SystemExit("Official package is missing its executable.")
        binary = archive.extractfile(member).read()
    folder.mkdir(parents=True, exist_ok=True)
    with target.open("xb") as output:
        output.write(binary)
    manifest = {"package": package, "version": VERSION, "tarball": dist["tarball"],
                "integrity": dist["integrity"], "executable_sha256": hashlib.sha256(binary).hexdigest()}
    with manifest_path.open("x", encoding="utf-8") as output:
        json.dump(manifest, output, indent=2)
    print(json.dumps({"version": VERSION, "status": "installed", "integrity_verified": True}))


if __name__ == "__main__":
    main()
