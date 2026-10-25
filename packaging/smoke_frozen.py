"""Exercise a separate extraction of a portable ZIP using synthetic chats only."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from http.cookiejar import CookieJar
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import time
from urllib.parse import urlencode
from urllib.request import HTTPCookieProcessor, ProxyHandler, Request, build_opener
import zipfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("zip", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    work = root / ".portable-build" / "smoke-tests" / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + " - moved ü folder")
    work.mkdir(parents=True)
    with zipfile.ZipFile(args.zip) as archive:
        archive.extractall(work)
    folder = work / "The Archive"
    exe = folder / "The Archive.exe"
    env = os.environ.copy()
    env["PATH"] = str(Path(env.get("SystemRoot", r"C:\Windows")) / "System32")
    for key in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV", "CONDA_PREFIX"):
        env.pop(key, None)
    report_path = work / "self-test-report.json"
    subprocess.run([str(exe), "--self-test-report", str(report_path)], cwd=work, env=env, check=True, timeout=30)
    capabilities = json.loads(report_path.read_text(encoding="utf-8"))
    assert capabilities["ok"] and capabilities["frozen"]
    assert not (folder / "data").exists()
    state_path = folder / "data" / ".archive-instance.json"
    processes = []
    blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if os.name == "nt":
            blocker.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            blocker.bind(("127.0.0.1", 8767))
            blocker.listen(1)
        except OSError:
            blocker.close()  # Another verified local test may already occupy the preferred port.
            blocker = None

        def start():
            process = subprocess.Popen([str(exe), "--no-browser"], cwd=work, env=env, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            processes.append(process)
            for _ in range(100):
                if state_path.exists():
                    state = json.loads(state_path.read_text(encoding="utf-8"))
                    opener = build_opener(ProxyHandler({}), HTTPCookieProcessor(CookieJar()))
                    with opener.open(state["origin"] + "/?" + urlencode({"launch": state["token"]}), timeout=5) as response:
                        assert response.status == 200
                    return process, state, opener
                if process.poll() is not None:
                    raise RuntimeError(f"Frozen app exited during startup: {process.returncode}")
                time.sleep(0.1)
            raise TimeoutError("Frozen app did not become ready")

        process, state, opener = start()
        assert state["origin"] != "http://127.0.0.1:8767"

        def request(path, data=None, method=None, headers=None):
            request_headers = {"Content-Type": "application/json", **(headers or {})}
            payload = None if data is None else json.dumps(data).encode("utf-8")
            with opener.open(Request(state["origin"] + path, data=payload, method=method, headers=request_headers), timeout=10) as response:
                return response.read()

        stats = json.loads(request("/api/stats"))
        assert stats["conversation_count"] == 0 and stats["message_count"] == 0
        fixture = {"conversation_id": "portable-smoke", "title": "Portable smoke fixture", "create_time": 1700000000,
            "update_time": 1700000000, "current_node": "answer", "mapping": {"question": {"id": "question", "parent": None, "children": ["answer"],
            "message": {"id": "question", "author": {"role": "user"}, "create_time": 1699999999,
                "content": {"content_type": "text", "parts": ["Can this portable copy stay local?"]}, "metadata": {}}},
            "answer": {"id": "answer", "parent": "question", "children": [],
            "message": {"id": "answer", "author": {"role": "assistant"}, "create_time": 1700000000,
                "content": {"content_type": "text", "parts": ["A **portable** amber archive fixture."]}, "metadata": {"model_slug": "fixture-model"}}}}}
        source = work / "synthetic-chat.json"
        source.write_text(json.dumps(fixture), encoding="utf-8")
        imported = json.loads(request("/api/import-path", {"path": str(source)}, "POST"))
        assert imported["conversation_count"] == 1
        results = json.loads(request("/api/conversations?" + urlencode({"q": "amber", "mode": "normal"})))
        assert results["total"] == 1
        request("/api/conversations/portable-smoke/tags", {"tags": ["portable-smoke"]}, "PUT")
        exported = json.loads(request("/api/conversations/portable-smoke/export"))
        assert exported[0]["conversation_id"] == "portable-smoke"
        markdown = request("/api/conversations/portable-smoke/export-chat")
        assert b"**portable** amber" in markdown and b"## User" in markdown and b"## Assistant" in markdown
        assert b"ExampleUser" not in markdown and b"ExampleAssistant" not in markdown
        split = request("/api/conversations/portable-smoke/export-chat-split?target_tokens=1000")
        with zipfile.ZipFile(io.BytesIO(split)) as chunks:
            rendered = b"\n".join(chunks.read(name) for name in chunks.namelist())
        assert b"## User" in rendered and b"## Assistant" in rendered
        assert b"ExampleUser" not in rendered and b"ExampleAssistant" not in rendered
        duplicate = subprocess.run([str(exe), "--no-browser"], cwd=work, env=env, timeout=15)
        assert duplicate.returncode == 0 and process.poll() is None
        request("/__archive_quit", {}, "POST", {"X-Archive-Token": state["token"]})
        assert process.wait(timeout=10) == 0
        assert not state_path.exists()
        process, state, opener = start()
        assert json.loads(request("/api/stats"))["conversation_count"] == 1
        detail = json.loads(request("/api/conversations/portable-smoke"))
        assert detail["conversation"]["tags"] == ["portable-smoke"]
        request("/__archive_quit", {}, "POST", {"X-Archive-Token": state["token"]})
        assert process.wait(timeout=10) == 0
        assert not state_path.exists()
        report = {"ok": True, "extraction": str(work), "minimal_path": True, "capabilities": capabilities,
            "occupied_port_fallback": True, "empty_first_start": True, "synthetic_import_search_tags_exports": True,
            "duplicate_launch": True, "quit_and_restart_persistence": True}
        (work / "lifecycle-report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, indent=2))
    finally:
        if blocker is not None:
            blocker.close()
        for process in processes:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=5)


if __name__ == "__main__":
    main()
