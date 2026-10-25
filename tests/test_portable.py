from __future__ import annotations

import io
import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

from chat_export_viewer import portable


def synthetic_conversation(*, both_roles: bool = False) -> dict:
    source = {
        "conversation_id": "portable-fixture",
        "title": "A synthetic portable chat",
        "create_time": 1_700_000_000,
        "update_time": 1_700_000_001,
        "current_node": "reply",
        "mapping": {
            "reply": {
                "id": "reply",
                "parent": None,
                "children": [],
                "message": {
                    "id": "reply",
                    "author": {"role": "assistant"},
                    "create_time": 1_700_000_000,
                    "content": {"content_type": "text", "parts": ["Synthetic portability verification."]},
                    "metadata": {"model_slug": "fixture-model", "future_field": {"preserved": True}},
                },
            },
        },
    }
    if both_roles:
        source["mapping"]["reply"]["parent"] = "question"
        source["mapping"]["question"] = {
            "id": "question",
            "parent": None,
            "children": ["reply"],
            "message": {
                "id": "question",
                "author": {"role": "user"},
                "create_time": 1_699_999_999,
                "content": {"content_type": "text", "parts": ["Synthetic **original** user words. " * 200]},
                "metadata": {"source_marker": "synthetic-user"},
            },
        }
    return source


class PortableRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory(prefix="archive-portable-tests-")
        self.root = Path(self.directory.name) / "The Archive with spaces – Grüße"
        self.root.mkdir()
        self.database = self.root / "data" / "master_export.db"
        self.instance_id = "a1" * 16
        self.token = secrets.token_urlsafe(32)
        self.origin = "http://127.0.0.1:49777"
        self.stopped = threading.Event()
        self.stop_callback = Mock(side_effect=self.stopped.set)
        self.app = portable.create_portable_app(
            self.database,
            instance_id=self.instance_id,
            token=self.token,
            origin=self.origin,
            stop_callback=self.stop_callback,
        )
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    def tearDown(self) -> None:
        self.directory.cleanup()

    def bootstrap(self, client=None):
        return (client or self.client).get(
            "/", query_string={"launch": self.token}, base_url=self.origin,
        )

    def get(self, route: str, **kwargs):
        return self.client.get(route, base_url=self.origin, **kwargs)

    def post(self, route: str, **kwargs):
        headers = {"Origin": self.origin, **kwargs.pop("headers", {})}
        return self.client.post(route, base_url=self.origin, headers=headers, **kwargs)

    def test_frozen_root_uses_executable_folder_independently_of_working_directory(self) -> None:
        executable = self.root / "The Archive.exe"
        before = Path.cwd()
        try:
            os.chdir(Path(self.directory.name))
            actual = portable.application_root(executable=executable, frozen=True)
        finally:
            os.chdir(before)
        self.assertEqual(actual, self.root.resolve())

    def test_source_root_is_the_project_containing_the_package(self) -> None:
        self.assertEqual(
            portable.application_root(frozen=False),
            Path(portable.__file__).resolve().parents[1],
        )

    def test_fresh_archive_is_empty_in_explicit_portable_data_folder(self) -> None:
        self.bootstrap()
        self.assertTrue(self.database.is_file())
        stats = self.get("/api/stats").get_json()
        self.assertEqual(stats["conversation_count"], 0)
        self.assertEqual(stats["message_count"], 0)
        self.assertEqual(stats["export_count"], 0)
        self.assertEqual(self.get("/api/conversations?q=anything").get_json()["total"], 0)
        self.assertEqual(self.get("/api/tags").get_json()["items"], [])

    def test_reader_and_archive_endpoints_require_local_session(self) -> None:
        for route in ("/", "/api/stats", "/api/conversations", "/__archive_session", "/__archive_health"):
            with self.subTest(route=route):
                self.assertIn(self.get(route).status_code, (401, 403))

    def test_bootstrap_redirects_to_clean_url_and_sets_protected_cookie(self) -> None:
        denied = self.get("/?launch=incorrect")
        self.assertIn(denied.status_code, (401, 403))
        response = self.bootstrap()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/")
        cookie = response.headers["Set-Cookie"]
        self.assertIn(f"archive_{self.instance_id}=", cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)
        self.assertIn("Path=/", cookie)
        self.assertNotIn(self.token, response.get_data(as_text=True))
        self.assertEqual(self.get("/").status_code, 200)

    def test_authorized_health_identifies_instance_without_exposing_token_or_disk_path(self) -> None:
        response = self.get("/__archive_health", headers={"Authorization": f"Bearer {self.token}"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {
            "app": portable.APP_ID,
            "instance_id": self.instance_id,
            "version": portable.VERSION,
        })
        text = response.get_data(as_text=True)
        self.assertNotIn(self.token, text)
        self.assertNotIn(str(self.database), text)
        self.assertNotIn("token", response.get_json())

    def test_malformed_non_ascii_secrets_are_rejected_without_server_error(self) -> None:
        response = self.get("/", query_string={"launch": "wrong-é"})
        self.assertIn(response.status_code, (401, 403))
        response = self.get("/__archive_health", headers={"Authorization": "Bearer wrong-é"})
        self.assertIn(response.status_code, (401, 403))
        self.bootstrap()
        response = self.post("/__archive_quit", headers={"X-Archive-Token": "wrong-é"})
        self.assertIn(response.status_code, (401, 403))
        self.stop_callback.assert_not_called()

    def test_foreign_host_origin_and_cross_site_requests_are_rejected(self) -> None:
        authorization = {"Authorization": f"Bearer {self.token}"}
        cases = (
            {"Host": "attacker.example:49777"},
            {"Host": "localhost:49777"},
            {"Origin": "https://attacker.example"},
            {"Origin": "null"},
            {"Sec-Fetch-Site": "cross-site"},
        )
        for bad_headers in cases:
            with self.subTest(headers=bad_headers):
                response = self.get("/__archive_health", headers={**authorization, **bad_headers})
                self.assertIn(response.status_code, (400, 403))
        allowed = self.get("/__archive_health", headers={**authorization, "Origin": self.origin})
        self.assertEqual(allowed.status_code, 200)

    def test_session_metadata_requires_cookie_and_only_exposes_controls_to_local_reader(self) -> None:
        self.assertIn(self.get("/__archive_session").status_code, (401, 403))
        self.bootstrap()
        response = self.get("/__archive_session")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["quit_token"], self.token)

    def test_quit_is_post_only_and_rejects_missing_or_wrong_token(self) -> None:
        self.bootstrap()
        self.assertEqual(self.get("/__archive_quit").status_code, 405)
        for headers in ({}, {"X-Archive-Token": "incorrect"}):
            with self.subTest(headers=headers):
                self.assertIn(self.post("/__archive_quit", headers=headers).status_code, (401, 403))
        self.stop_callback.assert_not_called()

    def test_authorized_quit_is_idempotent(self) -> None:
        self.bootstrap()
        headers = {"X-Archive-Token": self.token}
        self.assertIn(self.post("/__archive_quit", headers=headers).status_code, (200, 202))
        self.assertIn(self.post("/__archive_quit", headers=headers).status_code, (200, 202))
        self.assertTrue(self.stopped.wait(1.5), "The approved shutdown callback was not scheduled.")
        time.sleep(0.1)
        self.stop_callback.assert_called_once_with()

    def test_quit_waits_for_active_save_and_then_rejects_new_writes(self) -> None:
        entered = threading.Event()
        release = threading.Event()
        responses = []

        @self.app.post("/api/test-portable-write")
        def controlled_write():
            entered.set()
            if not release.wait(3):
                return {"error": "Test write timed out"}, 500
            return {"saved": True}

        self.bootstrap()
        writer_client = self.app.test_client()
        self.bootstrap(writer_client)
        worker = threading.Thread(target=lambda: responses.append(writer_client.post(
            "/api/test-portable-write", base_url=self.origin, headers={"Origin": self.origin},
        )))
        worker.start()
        try:
            self.assertTrue(entered.wait(1), "The synthetic save did not begin.")
            response = self.post("/__archive_quit", headers={"X-Archive-Token": self.token})
            self.assertEqual(response.status_code, 409)
            self.stop_callback.assert_not_called()
        finally:
            release.set()
            worker.join(timeout=3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(responses), 1)
        self.assertEqual(responses[0].status_code, 200)
        response = self.post("/__archive_quit", headers={"X-Archive-Token": self.token})
        self.assertIn(response.status_code, (200, 202))
        denied = self.post("/api/test-portable-write")
        self.assertEqual(denied.status_code, 503)
        self.assertTrue(self.stopped.wait(1.5))
        self.stop_callback.assert_called_once_with()

    def test_synthetic_import_tags_and_source_survive_reopening(self) -> None:
        self.bootstrap()
        source = synthetic_conversation()
        uploaded = self.post("/api/import-file", data={
            "file": (io.BytesIO(json.dumps(source).encode("utf-8")), "synthetic-export.json"),
        }, content_type="multipart/form-data")
        self.assertEqual(uploaded.status_code, 200, uploaded.get_data(as_text=True))
        tags = self.client.put(
            "/api/conversations/portable-fixture/tags", json={"tags": ["Portable test"]},
            base_url=self.origin, headers={"Origin": self.origin},
        )
        self.assertEqual(tags.status_code, 200)

        reopened = portable.create_portable_app(
            self.database,
            instance_id=self.instance_id,
            token=self.token,
            origin=self.origin,
            stop_callback=lambda: None,
        ).test_client()
        self.bootstrap(reopened)
        detail = reopened.get("/api/conversations/portable-fixture", base_url=self.origin).get_json()
        self.assertEqual(detail["conversation"]["tags"], ["Portable test"])
        self.assertEqual(detail["messages"][0]["model_slug"], "fixture-model")
        self.assertEqual(detail["messages"][0]["raw"]["metadata"]["future_field"], {"preserved": True})
        exported = reopened.get("/api/conversations/portable-fixture/export", base_url=self.origin)
        self.assertEqual(exported.status_code, 200)
        self.assertEqual(exported.get_json()[0]["mapping"]["reply"], source["mapping"]["reply"])

    def assert_markdown_export_labels(self, *, expected: list[str]) -> None:
        self.bootstrap()
        source = synthetic_conversation(both_roles=True)
        imported = self.post("/api/import-file", data={
            "file": (io.BytesIO(json.dumps(source).encode("utf-8")), "synthetic-speakers.json"),
        }, content_type="multipart/form-data")
        self.assertEqual(imported.status_code, 200)

        readable = self.get("/api/conversations/portable-fixture/export-chat")
        self.assertEqual(readable.status_code, 200)
        self.assertEqual(re.findall(r"^## (.+)$", readable.get_data(as_text=True), re.MULTILINE), expected)

        split = self.get("/api/conversations/portable-fixture/export-chat-split?target_tokens=1000")
        self.assertEqual(split.status_code, 200)
        with zipfile.ZipFile(io.BytesIO(split.data)) as archive:
            self.assertEqual(len(archive.namelist()), 2, "Long synthetic turns should exercise separate ZIP parts.")
            chunks = "\n".join(archive.read(name).decode("utf-8") for name in archive.namelist())
        self.assertEqual(re.findall(r"^## (.+)$", chunks, re.MULTILINE), expected)

        original = self.get("/api/conversations/portable-fixture/export")
        self.assertEqual(original.status_code, 200)
        self.assertEqual(original.get_json()[0]["mapping"], source["mapping"])

    def test_portable_markdown_and_split_zip_use_generic_speakers_without_changing_json(self) -> None:
        self.assert_markdown_export_labels(expected=["User", "Assistant"])

    def test_explicit_speaker_overrides_apply_to_both_markdown_exports_only(self) -> None:
        self.app.config["EXPORT_SPEAKER_LABELS"] = {"user": "Alex", "assistant": "Robin"}
        self.assert_markdown_export_labels(expected=["Alex", "Robin"])


class PortableInstanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory(prefix="archive-instance-tests-")
        self.root = Path(self.directory.name)

    def tearDown(self) -> None:
        self.directory.cleanup()

    def valid_info(self) -> dict:
        return {
            "app": portable.APP_ID,
            "instance_id": "b2" * 16,
            "token": secrets.token_urlsafe(32),
            "origin": "http://127.0.0.1:49777",
        }

    def test_instance_lock_cannot_be_stolen_and_releases_cleanly(self) -> None:
        path = self.root / "portable.lock"
        first = portable.InstanceLock(path)
        second = portable.InstanceLock(path)
        try:
            self.assertTrue(first.acquire())
            self.assertFalse(second.acquire())
            second.release()
            self.assertFalse(second.acquire(), "Releasing a failed lock must not release its owner.")
            first.release()
            self.assertTrue(second.acquire())
        finally:
            first.release()
            second.release()

    def test_os_releases_lock_after_process_exits_without_cleanup(self) -> None:
        path = self.root / "abandoned.lock"
        script = (
            "import os,sys; from pathlib import Path; "
            "from chat_export_viewer.portable import InstanceLock; "
            "lock=InstanceLock(Path(sys.argv[1])); "
            "assert lock.acquire(); os._exit(0)"
        )
        result = subprocess.run(
            [sys.executable, "-c", script, str(path)],
            cwd=Path(portable.__file__).resolve().parents[1],
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(path.exists(), "The stale file should survive the intentionally abrupt exit.")
        replacement = portable.InstanceLock(path)
        try:
            self.assertTrue(replacement.acquire())
        finally:
            replacement.release()

    def test_valid_instance_metadata_loads_but_malformed_or_foreign_state_does_not(self) -> None:
        path = self.root / "instance.json"
        self.assertIsNone(portable.load_instance(path))
        valid = self.valid_info()
        path.write_text(json.dumps(valid), encoding="utf-8")
        self.assertEqual(portable.load_instance(path), valid)
        invalid = [
            [], None, "text", {},
            {**valid, "app": "some-other-service"},
            {**valid, "instance_id": "short"},
            {**valid, "token": "short"},
            {**valid, "origin": "http://attacker.example:49777"},
            {**valid, "origin": "https://127.0.0.1:49777"},
            {**valid, "origin": "http://localhost:49777"},
            {**valid, "origin": "http://127.0.0.1:0"},
            {**valid, "origin": "http://127.0.0.1:49777/private"},
            {**valid, "origin": "http://name:password@127.0.0.1:49777"},
            {**valid, "origin": 123},
            {**valid, "origin": True},
            {**valid, "origin": {"host": "127.0.0.1"}},
        ]
        for candidate in invalid:
            with self.subTest(candidate=candidate):
                path.write_text(json.dumps(candidate), encoding="utf-8")
                self.assertIsNone(portable.load_instance(path))
        path.write_text("{broken", encoding="utf-8")
        self.assertIsNone(portable.load_instance(path))

    def test_instance_probe_checks_actual_identity_and_uses_only_loopback_credentials(self) -> None:
        info = self.valid_info()
        for payload, expected in (
            ({"app": portable.APP_ID, "instance_id": info["instance_id"]}, True),
            ({"app": "another-service", "instance_id": info["instance_id"]}, False),
            ({"app": portable.APP_ID, "instance_id": "c3" * 16}, False),
            ([], False),
        ):
            with self.subTest(payload=payload), patch("chat_export_viewer.portable.build_opener") as build:
                response = MagicMock()
                response.__enter__.return_value = response
                response.read.return_value = json.dumps(payload).encode("utf-8")
                build.return_value.open.return_value = response
                self.assertEqual(portable.verify_instance(info), expected)
                request = build.return_value.open.call_args.args[0]
                self.assertEqual(request.full_url, info["origin"] + "/__archive_health")
                self.assertEqual(request.get_header("Authorization"), "Bearer " + info["token"])
                self.assertEqual(build.call_args.args[0].proxies, {})
        with patch("chat_export_viewer.portable.build_opener") as build:
            build.return_value.open.side_effect = TimeoutError("Unrelated or unavailable process")
            self.assertFalse(portable.verify_instance(info))


if __name__ == "__main__":
    unittest.main()
