"""Privacy boundary checks for clean portable release creation."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile


spec = importlib.util.spec_from_file_location("build_portable", Path(__file__).with_name("build_portable.py"))
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class BuildPrivacyTests(unittest.TestCase):
    def test_staging_uses_app_allowlist_not_workspace_contents(self):
        with TemporaryDirectory() as directory:
            root = Path(directory) / "workspace"
            root.mkdir()
            (root / "portable.py").write_text("# entry point", encoding="utf-8")
            package = root / "chat_export_viewer"
            package.mkdir()
            for name in builder.APP_MODULES:
                (package / name).write_text("# app code", encoding="utf-8")
            (package / "static").mkdir()
            (package / "static" / "app.js").write_text("// app", encoding="utf-8")
            (package / "templates").mkdir()
            (package / "templates" / "index.html").write_text("<html></html>", encoding="utf-8")
            (root / "data").mkdir()
            (root / "data" / "master_export.db").write_bytes(b"private history")
            (root / "conversations.json").write_text("private export", encoding="utf-8")
            (root / "notes.md").write_text("private notes", encoding="utf-8")
            stage = Path(directory) / "stage"
            with patch.object(builder, "ROOT", root):
                records = builder.stage_sources(stage)
            self.assertEqual(len(records), len(builder.APP_MODULES) + 3)
            self.assertFalse((stage / "data").exists())
            self.assertFalse((stage / "conversations.json").exists())
            self.assertFalse((stage / "notes.md").exists())

    def make_zip(self, path, entries):
        manifest = {
            "version": "1.0.0", "build_id": "original-test-build",
            "source_inputs": [{"path": "portable.py", "sha256": "source-hash"}],
            "files": [{"path": name, "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()} for name, content in entries.items()]
        }
        with zipfile.ZipFile(path, "w") as archive:
            for name, content in entries.items():
                archive.writestr("The Archive/" + name, content)
            archive.writestr("The Archive/BUNDLE-MANIFEST.json", json.dumps(manifest))

    def test_valid_empty_bundle_audits_successfully(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "clean.zip"
            self.make_zip(path, {"The Archive.exe": b"application", "START HERE.txt": b"instructions"})
            with patch.object(builder, "inspect_frozen_code", return_value=0):
                report = builder.inspect_zip(path)
            self.assertEqual(report["status"], "pass")
            self.assertTrue(report["empty_archive"])

    def test_database_is_rejected_even_if_declared_in_manifest(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "bad.zip"
            self.make_zip(path, {"data/master_export.db": b"private"})
            with self.assertRaisesRegex(ValueError, "Private/runtime directory"):
                builder.inspect_zip(path)

    def test_unmanifested_addition_is_rejected(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "bad.zip"
            self.make_zip(path, {"The Archive.exe": b"app"})
            with zipfile.ZipFile(path, "a") as archive:
                archive.writestr("The Archive/conversations.json", b"private")
            with self.assertRaisesRegex(ValueError, "exactly match manifest"):
                builder.inspect_zip(path)

    def test_path_traversal_and_developer_path_are_rejected(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "bad.zip"
            self.make_zip(path, {"../private.txt": b"private"})
            with self.assertRaisesRegex(ValueError, "Unsafe ZIP path"):
                builder.inspect_zip(path)
            self.make_zip(path, {"START HERE.txt": b"C:\\Users\\ExampleUser\\private"})
            with self.assertRaisesRegex(ValueError, "Build-user path"):
                builder.inspect_zip(path)

    def test_compiled_personal_default_is_rejected(self):
        def archive_for(source):
            module = compile(source, "chat_export_viewer/exporting.py", "exec")
            modules = SimpleNamespace(toc={"chat_export_viewer.exporting": None}, extract=lambda _: module)
            return SimpleNamespace(toc={}, open_embedded_archive=lambda _: modules)
        with patch("PyInstaller.archive.readers.CArchiveReader", return_value=archive_for("speaker = 'ExampleUser'")):
            with self.assertRaisesRegex(ValueError, "Personal default"):
                builder.inspect_frozen_code(Path("dummy.exe"))
        with patch("PyInstaller.archive.readers.CArchiveReader", return_value=archive_for("speakers = ('User', 'Assistant')")):
            self.assertEqual(builder.inspect_frozen_code(Path("dummy.exe")), 1)

    def test_documentation_refresh_preserves_runtime_and_original_zip(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "original.zip"
            old_files = {"The Archive.exe": b"tested executable", "_internal/app.js": b"tested interface",
                         "START HERE.txt": b"old quick start"}
            self.make_zip(source, old_files)
            original_bytes = source.read_bytes()
            docs = root / "docs"
            docs.mkdir()
            (docs / "START HERE.txt").write_text("Open README.txt for full instructions.", encoding="utf-8")
            (docs / "README.txt").write_text("New importing, searching, and export guide.", encoding="utf-8")
            (root / "LICENSE").write_text("Standard application license fixture.\n", encoding="utf-8")
            with patch.object(builder, "ROOT", root), patch.object(builder, "DOCUMENTATION_ROOT", docs), \
                    patch.object(builder, "inspect_frozen_code", return_value=1):
                report = builder.refresh_documentation(source)
                self.assertEqual(builder.inspect_zip(Path(report["zip"]))["status"], "pass")
            self.assertEqual(source.read_bytes(), original_bytes)
            self.assertTrue(report["documentation_only"])
            self.assertEqual(report["unchanged_application_files"], 2)
            with zipfile.ZipFile(report["zip"]) as archive:
                for name in ("The Archive.exe", "_internal/app.js"):
                    self.assertEqual(archive.read("The Archive/" + name), old_files[name])
                self.assertEqual(archive.read("The Archive/README.txt"), (docs / "README.txt").read_bytes())
                self.assertEqual(archive.read("The Archive/START HERE.txt"), (docs / "START HERE.txt").read_bytes())
                self.assertEqual(archive.read("The Archive/LICENSE"), (root / "LICENSE").read_bytes())
                manifest = json.loads(archive.read("The Archive/BUNDLE-MANIFEST.json"))
                self.assertEqual(manifest["build_id"], "original-test-build")
                self.assertEqual(manifest["source_inputs"], [{"path": "portable.py", "sha256": "source-hash"}])
                self.assertEqual(manifest["documentation_revision"]["based_on_zip_sha256"], builder.digest(original_bytes))
                self.assertTrue(manifest["documentation_revision"]["unchanged_runtime"])
                self.assertEqual(manifest["application_license"], {"path": "LICENSE", "spdx": "Unlicense"})
                self.assertEqual(manifest["version"], "1.0.0")

    def test_application_license_is_copied_from_root_and_third_party_notices_are_preserved(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            docs = root / "docs"
            docs.mkdir()
            bundle = root / "bundle"
            bundle.mkdir()
            for name in ("START HERE.txt", "README.txt"):
                (docs / name).write_bytes(b"recipient instructions\n")
            license_text = b"Application license bytes stay unchanged.\n"
            (root / "LICENSE").write_bytes(license_text)
            third_party = bundle / "LICENSES" / "third-party.txt"
            third_party.parent.mkdir()
            third_party.write_bytes(b"Original third-party terms\n")
            with patch.object(builder, "ROOT", root), patch.object(builder, "DOCUMENTATION_ROOT", docs):
                builder.copy_documentation(bundle)
            self.assertEqual((bundle / "LICENSE").read_bytes(), license_text)
            self.assertEqual(third_party.read_bytes(), b"Original third-party terms\n")

    def test_missing_application_license_refuses_a_new_bundle(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            docs = root / "docs"
            docs.mkdir()
            bundle = root / "bundle"
            bundle.mkdir()
            for name in ("START HERE.txt", "README.txt"):
                (docs / name).write_bytes(b"instructions")
            with patch.object(builder, "ROOT", root), patch.object(builder, "DOCUMENTATION_ROOT", docs):
                with self.assertRaises(FileNotFoundError):
                    builder.copy_documentation(bundle)

    def test_documentation_refresh_refuses_an_unclean_input(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "private.zip"
            self.make_zip(source, {"The Archive.exe": b"app", "data/master_export.db": b"private"})
            with patch.object(builder, "ROOT", root):
                with self.assertRaisesRegex(ValueError, "Private/runtime directory"):
                    builder.refresh_documentation(source)
            self.assertFalse((root / "release").exists())


if __name__ == "__main__":
    unittest.main()
