"""Local-only review UI for KV260 vision sessions."""
from __future__ import annotations

import argparse
import json
import mimetypes
import threading
import time
import urllib.parse
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import vision


_MAX_UPLOAD = 128 * 1024 * 1024


def _html() -> bytes:
    resource = Path(__file__).with_name("static") / "vision_review.html"
    try:
        return resource.read_bytes()
    except OSError as exc:
        raise RuntimeError(f"review UI resource missing: {resource}") from exc


class ReviewServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, session: Path):
        super().__init__(address, ReviewHandler)
        self.session = session
        self.mutation_lock = threading.Lock()


class ReviewHandler(BaseHTTPRequestHandler):
    server: ReviewServer

    def _trusted_request(self) -> bool:
        # Reject DNS-rebinding hostnames and cross-site browser writes. A CLI may
        # omit Origin, but arbitrary websites must not mutate local review state.
        port = self.server.server_address[1]
        allowed = {f"127.0.0.1:{port}", f"localhost:{port}"}
        host = self.headers.get("Host", "").lower()
        origin = self.headers.get("Origin")
        if (host not in allowed or self.headers.get("Sec-Fetch-Site") == "cross-site"
                or (origin is not None and origin != "http://" + host)):
            self._json({"status": "error", "message": "仅允许本机同源请求"}, 403)
            return False
        return True

    def _read_body(self, length: int) -> bytes:
        self.connection.settimeout(10)
        body = self.rfile.read(length)
        if len(body) != length:
            raise vision.VisionError("请求内容不完整")
        return body

    def log_message(self, fmt: str, *args) -> None:
        print(f"[{self.log_date_time_string()}] {fmt % args}", flush=True)

    def _json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _error(self, exc: Exception, status: int = 400) -> None:
        self._json({"status": "error", "message": str(exc)}, status)

    def _read_json(self) -> dict:
        raw_length = self.headers.get("Content-Length", "0")
        try:
            length = int(raw_length)
        except ValueError as exc:
            raise vision.VisionError("Content-Length 非法") from exc
        if not 0 <= length <= 64 * 1024:
            raise vision.VisionError("JSON 请求过大")
        try:
            payload = json.loads(self._read_body(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise vision.VisionError("JSON 请求无法解析") from exc
        if not isinstance(payload, dict):
            raise vision.VisionError("JSON 请求必须是对象")
        return payload

    def do_GET(self) -> None:  # noqa: N802
        if not self._trusted_request():
            return
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/":
            body = _html()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        if parsed.path == "/api/status":
            try:
                self._json(vision.session_status(str(self.server.session)))
            except Exception as exc:  # noqa: BLE001
                self._error(exc)
            return
        if parsed.path.startswith("/image/"):
            name = parsed.path.removeprefix("/image/")
            allowed = {"input": "input.png", "golden": "golden.png", "output": "output.png", "diff": "diff.png"}
            filename = allowed.get(name)
            path = self.server.session / "images" / filename if filename else None
            if path is None or not path.is_file():
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            body = path.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "image/png")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        if not self._trusted_request():
            return
        if not self.server.mutation_lock.acquire(blocking=False):
            self._json({"status": "error", "message": "当前会话正在处理写请求，请稍后重试"}, 409)
            return
        try:
            self._post()
        finally:
            self.server.mutation_lock.release()

    def _post(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        try:
            if parsed.path in {"/api/review", "/api/evaluate-path"}:
                if self.headers.get_content_type() != "application/json":
                    self._json({"status": "error", "message": "需要 application/json"}, 415)
                    return
            if parsed.path == "/api/review":
                payload = self._read_json()
                result = vision.record_review(
                    str(self.server.session), str(payload.get("decision", "")),
                    "user", str(payload.get("note", "")),
                )
                self._json(result)
                return
            if parsed.path == "/api/evaluate-path":
                payload = self._read_json()
                result = vision.evaluate_session(
                    str(self.server.session), str(payload.get("output_image", "")),
                    payload.get("thresholds"),
                )
                self._json(result)
                return
            if parsed.path == "/api/upload-output":
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError as exc:
                    raise vision.VisionError("Content-Length 非法") from exc
                if not 1 <= length <= _MAX_UPLOAD:
                    raise vision.VisionError(f"上传大小必须在 1..{_MAX_UPLOAD} 字节")
                incoming = self.server.session / "images" / "uploaded-output"
                body = self._read_body(length)
                incoming.write_bytes(body)
                try:
                    result = vision.evaluate_session(str(self.server.session), str(incoming))
                finally:
                    incoming.unlink(missing_ok=True)
                self._json(result)
                return
        except Exception as exc:  # noqa: BLE001
            self._error(exc)
            return
        self.send_error(HTTPStatus.NOT_FOUND)


def serve(session_dir: str, port: int = 8765, open_browser: bool = False) -> None:
    session = vision._session(session_dir)  # same validation used by the MCP layer
    server = ReviewServer(("127.0.0.1", port), session)
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    print(f"KV260 vision review: {url}", flush=True)
    if open_browser:
        threading.Thread(
            target=lambda: (time.sleep(0.5), webbrowser.open(url)), daemon=True
        ).start()
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="KV260 vision result review UI")
    parser.add_argument("--session", required=True)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open-browser", action="store_true")
    args = parser.parse_args(argv)
    try:
        serve(args.session, args.port, args.open_browser)
    except (vision.VisionError, OSError, RuntimeError) as exc:
        print(f"[ERROR] {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
