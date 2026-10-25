"""Build an empty, allowlisted Windows portable app without touching user data."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
from importlib.metadata import distribution, version
import json
import marshal
from pathlib import Path, PurePosixPath
import platform
import shutil
import ssl
import subprocess
import sys
from tempfile import TemporaryDirectory
import types
import uuid
import zipfile


ROOT = Path(__file__).resolve().parents[1]
APP_NAME = "The Archive"
DOCUMENTATION = ("START HERE.txt", "README.txt")
DOCUMENTATION_ROOT = Path(__file__).resolve().parent
APP_MODULES = (
    "__init__.py", "database.py", "exporting.py", "ingest.py", "server.py",
    "tags.py", "portable.py",
)
RUNTIME_PACKAGES = (
    "blinker", "click", "colorama", "Flask", "ijson", "itsdangerous", "Jinja2",
    "MarkupSafe", "RapidFuzz", "Werkzeug", "waitress",
)
STATIC_SUFFIXES = {".css", ".js", ".json", ".txt", ".md", ".svg", ".png", ".ico", ".woff", ".woff2", ".ttf"}
FORBIDDEN_PARTS = {"data", "__pycache__", ".codex", ".agents", ".venv", "archive_work", "artifacts", ".git"}
FORBIDDEN_SUFFIXES = {".db", ".sqlite", ".sqlite3", ".wal", ".shm", ".log"}


def digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def safe_file(path: Path, parent: Path) -> Path:
    resolved = path.resolve(strict=True)
    if path.is_symlink() or not resolved.is_relative_to(parent.resolve()):
        raise ValueError(f"Input escapes allowed directory: {path.name}")
    if not resolved.is_file():
        raise ValueError(f"Not a file: {path.name}")
    return resolved


def stage_sources(stage: Path) -> list[dict[str, str]]:
    inputs = [ROOT / "portable.py"] + [ROOT / "chat_export_viewer" / name for name in APP_MODULES]
    for directory in ("static", "templates"):
        allowed_root = ROOT / "chat_export_viewer" / directory
        for path in sorted(allowed_root.rglob("*")):
            if not path.is_file():
                continue
            if path.suffix.lower() not in (STATIC_SUFFIXES if directory == "static" else {".html"}):
                raise ValueError(f"Unexpected application asset: {path.relative_to(ROOT)}")
            inputs.append(path)
    records = []
    for path in inputs:
        source = safe_file(path, ROOT)
        relative = path.relative_to(ROOT)
        if set(relative.parts) & FORBIDDEN_PARTS:
            raise ValueError(f"Forbidden source input: {relative}")
        target = stage / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        data = source.read_bytes()
        if path.suffix == ".py" and any(name in data.lower() for name in (b"exampleuser", b"exampleassistant")):
            raise ValueError(f"Personal defaults in bundled application source: {relative}")
        target.write_bytes(data)
        records.append({"path": relative.as_posix(), "sha256": digest(data)})
    return records


def collect_licenses(destination: Path) -> None:
    destination.mkdir(parents=True)
    python_license = safe_file(Path(sys.base_prefix) / "LICENSE.txt", Path(sys.base_prefix))
    shutil.copyfile(python_license, destination / "Python-LICENSE.txt")
    lines = [
        "THE ARCHIVE - THIRD-PARTY NOTICES", "",
        f"Python {platform.python_version()} - Python Software Foundation license; see Python-LICENSE.txt.",
        f"{ssl.OPENSSL_VERSION} - Apache-2.0; see OpenSSL-LICENSE.txt.",
        "SQLite - public domain; https://www.sqlite.org/copyright.html", "",
        "App packages and their unmodified distribution licenses:",
    ]
    for name in (*RUNTIME_PACKAGES, "PyInstaller"):
        package = distribution(name)
        found = []
        for entry in package.files or ():
            if any(part.lower().startswith(("license", "copying", "notice", "authors")) for part in entry.parts):
                source = Path(package.locate_file(entry))
                if not source.is_file():
                    continue
                package_folder = destination / name
                package_folder.mkdir(exist_ok=True)
                target = package_folder / entry.name
                if target.exists() and target.read_bytes() != source.read_bytes():
                    target = package_folder / (digest(source.read_bytes())[:8] + "-" + entry.name)
                shutil.copyfile(source, target)
                found.append(target.relative_to(destination).as_posix())
        if not found:
            raise RuntimeError(f"Missing redistribution license for {name}")
        lines.append(f"{name} {package.version}: {', '.join(found)}")
    openssl_license = Path(__file__).with_name("OpenSSL-LICENSE.txt")
    if not openssl_license.is_file():
        raise RuntimeError("Expected checked-in OpenSSL license is missing")
    shutil.copyfile(openssl_license, destination / "OpenSSL-LICENSE.txt")
    lines.extend([
        "", "The Python license includes notices for bundled standard-library components.",
        "Microsoft Visual C++ runtime DLLs are redistributed with the Python runtime.",
        "Marked and DOMPurify licenses are included beside the bundled browser scripts in",
        "_internal/chat_export_viewer/static/vendor. Font licenses and provenance are in",
        "_internal/chat_export_viewer/static/fonts.", "",
        "This file lists third-party components; it does not grant rights to imported chats.",
    ])
    (destination / "THIRD-PARTY-NOTICES.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def file_manifest(bundle: Path) -> list[dict]:
    files = []
    for path in sorted(bundle.rglob("*")):
        if path.is_file() and path.relative_to(bundle).as_posix() != "BUNDLE-MANIFEST.json":
            files.append({"path": path.relative_to(bundle).as_posix(), "bytes": path.stat().st_size, "sha256": digest(path.read_bytes())})
    return files


def copy_documentation(bundle: Path) -> None:
    for name in DOCUMENTATION:
        source = safe_file(DOCUMENTATION_ROOT / name, DOCUMENTATION_ROOT)
        shutil.copyfile(source, bundle / name)


def manifest_for(bundle: Path, source_records: list[dict[str, str]], identity: str, app_version: str) -> dict:
    return {
        "application": APP_NAME, "version": app_version, "platform": "windows-x64",
        "build_id": identity, "empty_archive": True,
        "python": platform.python_version(), "pyinstaller": version("PyInstaller"),
        "dependencies": {name: version(name) for name in RUNTIME_PACKAGES},
        "source_inputs": source_records, "files": file_manifest(bundle),
    }


def inspect_frozen_code(executable: Path) -> int:
    """Check compressed app bytecode as well as visible distribution files."""
    from PyInstaller.archive.readers import CArchiveReader

    archive = CArchiveReader(str(executable))
    modules = archive.open_embedded_archive("PYZ.pyz")
    forbidden = {"exampleuser", "exampleassistant", str(ROOT).casefold(), str(ROOT).replace("\\", "/").casefold()}
    count = 0

    def visit(value, label):
        nonlocal count
        if isinstance(value, types.CodeType):
            count += 1
            if any(marker in value.co_filename.casefold() for marker in forbidden):
                raise ValueError(f"Private build identity in compiled filename: {label}")
            for constant in value.co_consts:
                if isinstance(constant, str) and any(marker in constant.casefold() for marker in forbidden):
                    raise ValueError(f"Personal default or build path in compiled application: {label}")
                visit(constant, label)

    for name in modules.toc:
        visit(modules.extract(name), name)
    for name in archive.toc:
        if name == "PYZ.pyz":
            continue
        try:
            value = marshal.loads(archive.extract(name))
        except (ValueError, TypeError, EOFError):
            continue
        visit(value, name)
    return count


def inspect_zip(path: Path) -> dict:
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if any(count != 1 for count in Counter(names).values()):
            raise ValueError("Duplicate ZIP paths")
        prefix = APP_NAME + "/"
        manifest_name = prefix + "BUNDLE-MANIFEST.json"
        manifest = json.loads(archive.read(manifest_name))
        expected = {prefix + entry["path"]: entry for entry in manifest["files"]}
        if set(names) != set(expected) | {manifest_name}:
            raise ValueError("ZIP content does not exactly match manifest")
        for name in names:
            relative = PurePosixPath(name)
            if relative.is_absolute() or ".." in relative.parts or "\\" in name:
                raise ValueError("Unsafe ZIP path")
            if relative.parts[0] != APP_NAME or set(relative.parts[1:]) & FORBIDDEN_PARTS:
                raise ValueError(f"Private/runtime directory in ZIP: {name}")
            suffix = relative.suffix.lower()
            if suffix in FORBIDDEN_SUFFIXES or name.lower().endswith(("-wal", "-shm")):
                raise ValueError(f"Database/runtime file in ZIP: {name}")
            data = archive.read(name)
            if name in expected and (digest(data) != expected[name]["sha256"] or len(data) != expected[name]["bytes"]):
                raise ValueError(f"Hash mismatch: {name}")
            if suffix in STATIC_SUFFIXES | {".html"}:
                lowered = data.lower()
                if b"exampleuser" in lowered or b"example-workspace\\export viewer" in lowered:
                    raise ValueError(f"Build-user path found in shipped text: {name}")
        with TemporaryDirectory(prefix="archive-code-audit-") as directory:
            executable = Path(directory) / "The Archive.exe"
            executable.write_bytes(archive.read(prefix + "The Archive.exe"))
            code_objects = inspect_frozen_code(executable)
        return {
            "status": "pass", "file_count": len(names), "empty_archive": True,
            "no_database_or_runtime_state": True, "manifest_hashes_verified": True,
            "uncompressed_bytes": sum(info.file_size for info in archive.infolist()),
            "zip_bytes": path.stat().st_size, "zip_sha256": digest(path.read_bytes()),
            "compiled_code_objects_checked": code_objects, "personal_defaults_or_build_paths_absent": True,
        }


def write_release(bundle: Path, release: Path) -> dict:
    zip_path = release / "The Archive - Windows Portable.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(bundle.rglob("*")):
            if path.is_file():
                archive.write(path, APP_NAME + "/" + path.relative_to(bundle).as_posix())
    audit = inspect_zip(zip_path)
    (release / "PACKAGE-AUDIT.json").write_text(json.dumps(audit, indent=2) + "\n", encoding="utf-8")
    (release / "SHA256SUMS.txt").write_text(audit["zip_sha256"] + "  " + zip_path.name + "\n", encoding="ascii")
    return {"release_directory": str(release), "zip": str(zip_path), "audit": audit}


def refresh_documentation(zip_path: Path) -> dict:
    """Revise only recipient docs while keeping the tested executable and assets."""
    zip_path = zip_path.resolve(strict=True)
    original_audit = inspect_zip(zip_path)
    identity = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-docs-" + uuid.uuid4().hex[:6]
    work = ROOT / ".portable-build" / "documentation" / identity
    work.mkdir(parents=True)
    with zipfile.ZipFile(zip_path) as archive:
        original_manifest = json.loads(archive.read(APP_NAME + "/BUNDLE-MANIFEST.json"))
        for name in archive.namelist():
            target = work.joinpath(*PurePosixPath(name).parts)
            if not target.resolve().is_relative_to(work.resolve()):
                raise ValueError("Documentation staging path escapes build workspace")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(name))
    app_version = original_manifest.get("version")
    if not isinstance(app_version, str) or not app_version or not all(c.isalnum() or c in ".-_" for c in app_version):
        raise ValueError("Invalid original release version")
    bundle = work / APP_NAME
    old_files = {entry["path"]: entry for entry in original_manifest["files"]}
    copy_documentation(bundle)
    files = file_manifest(bundle)
    new_files = {entry["path"]: entry for entry in files}
    if set(new_files) != set(old_files) | set(DOCUMENTATION):
        raise ValueError("Documentation refresh added an unexpected file")
    for name, old in old_files.items():
        if name not in DOCUMENTATION and new_files[name] != old:
            raise ValueError(f"Documentation refresh changed application file: {name}")
    revision = {
        "id": identity, "updated_at": datetime.now(timezone.utc).isoformat(),
        "based_on_zip_sha256": original_audit["zip_sha256"],
        "unchanged_runtime": True,
        "files": [new_files[name] for name in DOCUMENTATION],
    }
    manifest = {**original_manifest, "files": files, "documentation_revision": revision}
    (bundle / "BUNDLE-MANIFEST.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    release = ROOT / "release" / f"{app_version}-windows-x64-{identity}"
    release.mkdir(parents=True)
    output = release / APP_NAME
    shutil.copytree(bundle, output)
    result = write_release(output, release)
    if digest(zip_path.read_bytes()) != original_audit["zip_sha256"]:
        raise ValueError("Original release changed during documentation refresh")
    result["documentation_only"] = True
    result["unchanged_application_files"] = len(set(old_files) - set(DOCUMENTATION))
    (release / "DOCUMENTATION-UPDATE.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default="1.0.0")
    parser.add_argument("--audit", type=Path, help="Verify an existing release ZIP without rebuilding")
    parser.add_argument("--refresh-docs", type=Path, help="Add current recipient documentation to an audited ZIP")
    args = parser.parse_args()
    if args.audit and args.refresh_docs:
        parser.error("Choose --audit or --refresh-docs")
    if args.audit:
        print(json.dumps(inspect_zip(args.audit), indent=2))
        return
    if args.refresh_docs:
        print(json.dumps(refresh_documentation(args.refresh_docs), indent=2))
        return
    if sys.platform != "win32" or platform.architecture()[0] != "64bit":
        raise RuntimeError("This pipeline builds Windows x64 on Windows x64")
    if not all(c.isalnum() or c in ".-_" for c in args.version):
        raise ValueError("Invalid version")
    identity = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
    build = ROOT / ".portable-build" / "builds" / identity
    stage = build / "source"
    stage.mkdir(parents=True)
    source_records = stage_sources(stage)
    command = [
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--onedir", "--windowed", "--noupx",
        "--name", APP_NAME, "--contents-directory", "_internal",
        "--distpath", str(build / "dist"), "--workpath", str(build / "work"), "--specpath", str(build / "spec"),
        "--paths", str(stage), "--add-data", f"{stage / 'chat_export_viewer' / 'static'}:chat_export_viewer/static",
        "--add-data", f"{stage / 'chat_export_viewer' / 'templates'}:chat_export_viewer/templates",
        "--collect-all", "rapidfuzz", "--collect-all", "ijson",
        str(stage / "portable.py"),
    ]
    print(f"Staged {len(source_records)} allowlisted source files. Building {APP_NAME}...", flush=True)
    subprocess.run(command, cwd=stage, check=True)
    release = ROOT / "release" / f"{args.version}-windows-x64-{identity}"
    release.mkdir(parents=True)
    bundle = release / APP_NAME
    shutil.copytree(build / "dist" / APP_NAME, bundle)
    copy_documentation(bundle)
    collect_licenses(bundle / "LICENSES")
    manifest = manifest_for(bundle, source_records, identity, args.version)
    (bundle / "BUNDLE-MANIFEST.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(write_release(bundle, release), indent=2), flush=True)


if __name__ == "__main__":
    main()
