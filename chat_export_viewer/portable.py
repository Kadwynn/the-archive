"""Portable app lifecycle. No archive files are part of the application bundle."""
from __future__ import annotations

import argparse
import ctypes
import hmac
import io
import json
import os
import re
import secrets
import socket
import sqlite3
import sys
import threading
import time
import webbrowser
from pathlib import Path
from urllib.parse import urlencode, urlsplit
from urllib.request import ProxyHandler, Request, build_opener

from flask import Response, g, jsonify, redirect, request

from .server import create_app

APP_ID = "the-archive-portable"
VERSION = "1.0.1"
DEFAULT_PORT = 8767


def application_root(*, executable=None, frozen=None) -> Path:
    if frozen is None:
        frozen = bool(getattr(sys, "frozen", False))
    return Path(executable or sys.executable).resolve().parent if frozen else Path(__file__).resolve().parent.parent


class InstanceLock:
    """An OS-owned lock automatically releases even after a process crashes."""
    def __init__(self, path: Path):
        self.path = Path(path)
        self.handle = None

    def acquire(self) -> bool:
        if self.handle is not None:
            return True
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        try:
            if self.path.stat().st_size == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return False
        self.handle = handle
        return True

    def release(self) -> None:
        if self.handle is None:
            return
        handle, self.handle = self.handle, None
        try:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


def load_instance(path: Path) -> dict | None:
    try:
        if Path(path).stat().st_size > 8192:
            return None
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(value, dict) or value.get("app") != APP_ID:
            return None
        if not all(isinstance(value.get(key), str) for key in ("instance_id", "token", "origin")):
            return None
        if not re.fullmatch(r"[a-f0-9]{32}", str(value.get("instance_id", ""))):
            return None
        if not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", str(value.get("token", ""))):
            return None
        url = urlsplit(value.get("origin", ""))
        if (url.scheme != "http" or url.hostname != "127.0.0.1"
                or not url.port or not 1024 <= url.port <= 65535
                or url.username or url.password or url.path or url.query or url.fragment
                or value["origin"] != f"http://127.0.0.1:{url.port}"):
            return None
        return value
    except (OSError, ValueError, TypeError, AttributeError, UnicodeError):
        return None


def verify_instance(info: dict) -> bool:
    try:
        parsed = urlsplit(info["origin"])
        if (parsed.scheme != "http" or parsed.hostname != "127.0.0.1"
                or not parsed.port or not 1024 <= parsed.port <= 65535
                or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment
                or info["origin"] != f"http://127.0.0.1:{parsed.port}"):
            return False
        req = Request(info["origin"] + "/__archive_health", headers={"Authorization": "Bearer " + info["token"]})
        with build_opener(ProxyHandler({})).open(req, timeout=1) as response:
            value = json.loads(response.read(4096))
        return value.get("app") == APP_ID and value.get("instance_id") == info["instance_id"]
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return False


def create_portable_app(db_path, *, instance_id: str, token: str, origin: str, stop_callback):
    app = create_app(db_path)
    app.config["PORTABLE_MODE"] = True
    cookie_name = "archive_" + instance_id
    guard = threading.Lock()
    lifecycle = {"stopping": False, "writes": 0}

    def same_secret(value):
        return hmac.compare_digest(str(value or "").encode("utf-8"), token.encode("utf-8"))

    @app.before_request
    def protect_local_archive():
        if request.host != urlsplit(origin).netloc:
            return jsonify(error="This address does not belong to this Archive app."), 403
        if request.headers.get("Origin") not in (None, origin):
            return jsonify(error="Open The Archive from its own window."), 403
        if request.headers.get("Sec-Fetch-Site") not in (None, "none", "same-origin"):
            return jsonify(error="Open The Archive from its own window."), 403
        if request.path == "/__archive_health":
            if not hmac.compare_digest(request.headers.get("Authorization", "").encode("utf-8"), ("Bearer " + token).encode("utf-8")):
                return jsonify(error="Open The Archive.exe to open this archive."), 403
            return None
        if request.path == "/" and request.method == "GET" and "launch" in request.args:
            if not same_secret(request.args["launch"]):
                return Response("Please reopen The Archive.exe to open this archive.", status=403)
            response = redirect("/")
            response.set_cookie(cookie_name, token, httponly=True, samesite="Strict")
            return response
        if not same_secret(request.cookies.get(cookie_name)):
            return Response("Please reopen The Archive.exe to open this archive.", status=403)
        if request.method in ("POST", "PUT", "PATCH", "DELETE") and request.path.startswith("/api/"):
            with guard:
                if lifecycle["stopping"]:
                    return jsonify(error="The Archive is closing. Reopen the app to continue."), 503
                lifecycle["writes"] += 1
                g.portable_write = True

    @app.teardown_request
    def finish_write(error):
        if g.pop("portable_write", False):
            with guard:
                lifecycle["writes"] -= 1

    @app.after_request
    def private_response(response):
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        if not request.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/__archive_health")
    def health():
        return jsonify(app=APP_ID, instance_id=instance_id, version=VERSION)

    @app.get("/__archive_session")
    def session():
        return jsonify(quit_token=token, version=VERSION)

    @app.post("/__archive_quit")
    def quit_app():
        if not same_secret(request.headers.get("X-Archive-Token")):
            return jsonify(error="Please reopen the app before quitting."), 403
        with guard:
            if lifecycle["writes"]:
                return jsonify(error="Please wait for the import or save to finish, then quit again."), 409
            if not lifecycle["stopping"]:
                lifecycle["stopping"] = True
                timer = threading.Timer(0.4, stop_callback)
                timer.daemon = True
                timer.start()
        return jsonify(stopping=True)

    return app


def runtime_report() -> dict:
    import ijson
    from rapidfuzz import fuzz
    from waitress import create_server

    with sqlite3.connect(":memory:") as db:
        db.execute("CREATE VIRTUAL TABLE check_fts USING fts5(text)")
        db.execute("INSERT INTO check_fts VALUES ('portable archive')")
        assert db.execute("SELECT count(*) FROM check_fts WHERE check_fts MATCH 'archive'").fetchone()[0] == 1
    assert fuzz.ratio("archive", "archive") == 100
    assert list(ijson.items(io.BytesIO(b'[1,2]'), "item")) == [1, 2]
    assert callable(create_server)
    package = Path(__file__).resolve().parent
    required = ["templates/index.html", "static/app.js", "static/markdown.js", "static/chat-find.js",
                "static/styles.css", "static/reader.css", "static/markdown.css", "static/portable-ui.js",
                "static/portable.css", "static/vendor/marked.umd.js", "static/vendor/purify.min.js",
                "static/fonts/fonts.css", "static/fonts/Newsreader-Variable.ttf",
                "static/fonts/SourceSans3-Variable.ttf", "static/fonts/SourceSans3-Italic-Variable.ttf",
                "static/fonts/IBMPlexMono-Regular.ttf", "static/fonts/IBMPlexMono-Medium.ttf"]
    missing = [name for name in required if not (package / name).is_file()]
    if missing:
        raise RuntimeError("The app folder is incomplete: " + ", ".join(missing))
    return {"ok": True, "app": APP_ID, "version": VERSION, "frozen": bool(getattr(sys, "frozen", False)),
            "sqlite_fts5": True, "ijson_backend": ijson.backend, "rapidfuzz": True, "waitress": True,
            "assets": len(required), "python": ".".join(map(str, sys.version_info[:3]))}


def open_in_browser(info: dict) -> None:
    url = info["origin"] + "/?" + urlencode({"launch": info["token"]})
    if not webbrowser.open(url):
        raise RuntimeError("The browser could not be opened. Set a default browser in Windows, then reopen The Archive.")


def bind_local_socket() -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if os.name == "nt":
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            sock.bind(("127.0.0.1", DEFAULT_PORT))
        except OSError:
            sock.bind(("127.0.0.1", 0))
        return sock
    except BaseException:
        sock.close()
        raise


def run(app_dir: Path, *, open_browser=True) -> int:
    from waitress import create_server, wasyncore

    data = Path(app_dir).resolve() / "data"
    data.mkdir(parents=True, exist_ok=True)
    state_path = data / ".archive-instance.json"
    lock = InstanceLock(data / ".archive.lock")
    if not lock.acquire():
        for _ in range(40):
            existing = load_instance(state_path)
            if existing and verify_instance(existing):
                if open_browser:
                    open_in_browser(existing)
                return 0
            time.sleep(0.25)
        raise RuntimeError("This copy of The Archive is starting or closing. Wait a few seconds and try again.")

    server = sock = worker = None
    stop = threading.Event()
    info = {"app": APP_ID, "instance_id": secrets.token_hex(16), "token": secrets.token_urlsafe(32)}
    try:
        sock = bind_local_socket()
        info["origin"] = f"http://127.0.0.1:{sock.getsockname()[1]}"
        app = create_portable_app(data / "master_export.db", instance_id=info["instance_id"],
                                  token=info["token"], origin=info["origin"], stop_callback=stop.set)
        server = create_server(app, sockets=[sock], threads=4, expose_tracebacks=False,
                               log_socket_errors=False, asyncore_loop_timeout=1)
        worker = threading.Thread(target=server.run, daemon=True, name="archive-local-server")
        worker.start()
        for _ in range(40):
            if verify_instance(info):
                break
            if not worker.is_alive():
                raise RuntimeError("The local viewer could not start.")
            time.sleep(0.1)
        else:
            raise RuntimeError("The local viewer did not become ready. Please reopen the app.")
        pending = state_path.with_suffix(".new")
        pending.write_text(json.dumps(info), encoding="utf-8")
        pending.replace(state_path)
        if open_browser:
            open_in_browser(info)
        while not stop.wait(0.25):
            if not worker.is_alive():
                raise RuntimeError("The local viewer stopped unexpectedly. Please reopen The Archive.")
        return 0
    finally:
        if server is not None:
            server.task_dispatcher.shutdown(cancel_pending=True, timeout=30)
            server.close()
            wasyncore.close_all(server._map, ignore_all=True)
        elif sock is not None:
            sock.close()
        if worker is not None:
            worker.join(timeout=3)
        if (load_instance(state_path) or {}).get("instance_id") == info["instance_id"]:
            state_path.unlink(missing_ok=True)
        lock.release()


def show_error(message: str) -> None:
    if os.name == "nt":
        ctypes.windll.user32.MessageBoxW(None, message, "The Archive", 0x10)
    elif sys.stderr is not None:
        print(message, file=sys.stderr)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="The Archive portable viewer")
    parser.add_argument("--no-browser", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--self-test-report", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.self_test_report:
        try:
            report = runtime_report()
        except Exception as error:
            report = {"ok": False, "error": type(error).__name__ + ": " + str(error)}
        args.self_test_report.write_text(json.dumps(report, indent=2), encoding="utf-8")
        return 0 if report["ok"] else 1
    root = application_root()
    if not getattr(sys, "frozen", False):
        # Source runs cannot accidentally open the developer's existing archive.
        root = root / ".portable-build" / "development"
    try:
        runtime_report()
        return run(root, open_browser=not args.no_browser)
    except Exception as error:
        if isinstance(error, PermissionError):
            detail = "The app cannot save in this folder. Extract the whole ZIP into Documents or another folder you can write to."
        else:
            detail = str(error)
        try:
            log_path = root / "data" / "portable.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("a", encoding="utf-8") as log:
                log.write(time.strftime("%Y-%m-%d %H:%M:%S ") + type(error).__name__ + ": " + detail + "\n")
        except OSError:
            pass
        if not args.no_browser:
            show_error(detail + "\n\nKeep The Archive.exe and its _internal folder together. Your chats are stored in the app's data folder.")
        return 1
