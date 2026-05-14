"""Tests for lumetra_engram. Uses stdlib unittest + http.server — zero new
deps. Spins up a localhost server per test that captures requests and
returns canned responses, so no network traffic ever leaves the box.

Run from the repo root with:
    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import os
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Optional, Tuple
from urllib.parse import urlparse

# Make the in-tree src/ importable so tests run without `pip install -e .`.
_SRC = os.path.join(os.path.dirname(__file__), os.pardir, "src")
if os.path.isdir(_SRC) and _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from lumetra_engram import EngramClient, EngramError  # noqa: E402


# ---------------------------------------------------------------------------
# Mock server scaffolding
# ---------------------------------------------------------------------------

class _Capture:
    """Bag the test reads after the request is done."""

    def __init__(self) -> None:
        self.method: Optional[str] = None
        self.path: Optional[str] = None
        self.headers: dict = {}
        self.body: Optional[bytes] = None


def _make_handler(capture: _Capture, status: int, response_body: Any,
                  content_type: str = "application/json"):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args, **_kwargs):  # silence
            return

        def _read_body(self) -> bytes:
            length = int(self.headers.get("Content-Length", "0") or "0")
            return self.rfile.read(length) if length else b""

        def _handle(self):
            capture.method = self.command
            capture.path = self.path
            capture.headers = {k.lower(): v for k, v in self.headers.items()}
            capture.body = self._read_body() if self.command in ("POST", "PATCH", "PUT") else b""
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            if response_body is None:
                body = b""
            elif isinstance(response_body, (bytes, bytearray)):
                body = bytes(response_body)
            elif isinstance(response_body, str):
                body = response_body.encode()
            else:
                body = json.dumps(response_body).encode()
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if body:
                self.wfile.write(body)

        def do_GET(self): self._handle()
        def do_POST(self): self._handle()
        def do_DELETE(self): self._handle()
        def do_PATCH(self): self._handle()

    return Handler


class _Serve:
    """Context manager: start an HTTPServer in a background thread, then
    shut it down + close the socket cleanly on exit."""

    def __init__(self, status: int, body: Any, content_type: str = "application/json") -> None:
        self.cap = _Capture()
        self._handler = _make_handler(self.cap, status, body, content_type)
        self._httpd: Optional[HTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self.url: Optional[str] = None

    def __enter__(self) -> "_Serve":
        self._httpd = HTTPServer(("127.0.0.1", 0), self._handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        port = self._httpd.server_address[1]
        self.url = f"http://127.0.0.1:{port}"
        return self

    def __exit__(self, *_exc):
        if self._httpd:
            self._httpd.shutdown()
            self._httpd.server_close()
        if self._thread:
            self._thread.join(timeout=2)
        return False


def _client(base_url: str) -> EngramClient:
    return EngramClient(api_key="eng_live_test_key", base_url=base_url, timeout_seconds=5)


def _serve(status: int, body: Any, content_type: str = "application/json") -> Tuple[str, _Capture, "_ServerHandle"]:
    """Back-compat tuple form: returns (url, capture, handle). Caller must
    call handle.shutdown() in a finally — handle now also closes the listening
    socket so the test process doesn't leak ResourceWarnings."""
    s = _Serve(status, body, content_type).__enter__()
    return s.url, s.cap, _ServerHandle(s)


class _ServerHandle:
    def __init__(self, s: "_Serve") -> None:
        self._s = s

    def shutdown(self) -> None:
        self._s.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# Constructor
# ---------------------------------------------------------------------------

class ConstructorTests(unittest.TestCase):
    def test_requires_api_key(self) -> None:
        with self.assertRaisesRegex(ValueError, "api_key is required"):
            EngramClient(api_key="")

    def test_trims_trailing_slash_in_base_url(self) -> None:
        with _Serve(200, {"id": "x", "bucket_name": "b", "token_count": 1}) as s:
            c = EngramClient(api_key="k", base_url=s.url + "///")
            c.store_memory("hi", "b")
            self.assertTrue(s.cap.path.startswith("/v1/"))


# ---------------------------------------------------------------------------
# Request shapes
# ---------------------------------------------------------------------------

class RequestShapeTests(unittest.TestCase):
    def test_store_memory(self) -> None:
        url, cap, httpd = _serve(200, {"id": "mem_1", "bucket_name": "work", "token_count": 7})
        try:
            r = _client(url).store_memory("User prefers tabs.", "work")
            self.assertEqual(cap.method, "POST")
            self.assertEqual(cap.path, "/v1/buckets/work/memories")
            self.assertEqual(cap.headers["authorization"], "Bearer eng_live_test_key")
            self.assertEqual(json.loads(cap.body), {"content": "User prefers tabs."})
            self.assertEqual(r["id"], "mem_1")
        finally:
            httpd.shutdown()

    def test_store_memories_batch(self) -> None:
        url, cap, httpd = _serve(200, {"memories": [{"id": "a", "bucket_name": "work", "token_count": 1}]})
        try:
            _client(url).store_memories(["one", "two"], "work")
            self.assertEqual(
                json.loads(cap.body),
                {"memories": [{"content": "one"}, {"content": "two"}]},
            )
        finally:
            httpd.shutdown()

    def test_query_options_mapping(self) -> None:
        url, cap, httpd = _serve(200, {"answer": "ok"})
        try:
            _client(url).query(
                "q?", buckets=["a", "b"], top_k=12,
                skip_synthesis=False, return_explanation=True,
            )
            body = json.loads(cap.body)
            self.assertEqual(body["buckets"], ["a", "b"])
            self.assertEqual(body["options"]["top_k"], 12)
            self.assertTrue(body["options"]["return_explanation"])
            self.assertFalse(body["options"]["skip_synthesis"])
        finally:
            httpd.shutdown()

    def test_query_defaults(self) -> None:
        url, cap, httpd = _serve(200, {"answer": ""})
        try:
            _client(url).query("plain")
            body = json.loads(cap.body)
            self.assertEqual(body["buckets"], ["default"])
            self.assertEqual(body["options"]["top_k"], 8)
        finally:
            httpd.shutdown()

    def test_list_memories_query_params(self) -> None:
        url, cap, httpd = _serve(200, {"memories": [], "total": 0, "limit": 50, "offset": 10})
        try:
            _client(url).list_memories("work", limit=50, offset=10)
            parsed = urlparse(cap.path)
            self.assertEqual(parsed.path, "/v1/buckets/work/memories")
            self.assertIn("limit=50", parsed.query)
            self.assertIn("offset=10", parsed.query)
            self.assertEqual(cap.method, "GET")
        finally:
            httpd.shutdown()

    def test_delete_memory(self) -> None:
        url, cap, httpd = _serve(200, {"ok": True})
        try:
            _client(url).delete_memory("mem_abc", "work")
            self.assertEqual(cap.method, "DELETE")
            self.assertEqual(cap.path, "/v1/buckets/work/memories/mem_abc")
        finally:
            httpd.shutdown()

    def test_clear_memories(self) -> None:
        url, cap, httpd = _serve(200, {"ok": True})
        try:
            _client(url).clear_memories("work")
            self.assertEqual(cap.method, "DELETE")
            self.assertEqual(cap.path, "/v1/buckets/work/memories")
        finally:
            httpd.shutdown()

    def test_list_buckets_wrapped_response(self) -> None:
        url, _, httpd = _serve(200, {"buckets": [{"id": "b1", "name": "work", "created_at": "t"}]})
        try:
            r = _client(url).list_buckets()
            self.assertEqual(len(r), 1)
            self.assertEqual(r[0]["name"], "work")
        finally:
            httpd.shutdown()

    def test_list_buckets_bare_array_response(self) -> None:
        url, _, httpd = _serve(200, [{"id": "b1", "name": "work", "created_at": "t"}])
        try:
            r = _client(url).list_buckets()
            self.assertEqual(len(r), 1)
        finally:
            httpd.shutdown()

    def test_create_bucket(self) -> None:
        url, cap, httpd = _serve(200, {"id": "b2", "name": "new", "created_at": "t"})
        try:
            _client(url).create_bucket("new", "a test bucket")
            self.assertEqual(cap.method, "POST")
            self.assertEqual(cap.path, "/v1/buckets")
            self.assertEqual(json.loads(cap.body), {"name": "new", "description": "a test bucket"})
        finally:
            httpd.shutdown()

    def test_bucket_names_get_url_encoded(self) -> None:
        url, cap, httpd = _serve(200, {"id": "x", "bucket_name": "user 123", "token_count": 1})
        try:
            _client(url).store_memory("test", "user 123/spaces")
            self.assertEqual(cap.path, "/v1/buckets/user%20123%2Fspaces/memories")
        finally:
            httpd.shutdown()

    def test_get_and_regenerate_profile(self) -> None:
        url, cap, httpd = _serve(200, {"profile": "you prefer tabs"})
        try:
            r = _client(url).get_profile("work")
            self.assertEqual(cap.method, "GET")
            self.assertEqual(cap.path, "/v1/buckets/work/profile")
            self.assertEqual(r["profile"], "you prefer tabs")
        finally:
            httpd.shutdown()

        url2, cap2, httpd2 = _serve(200, {"profile": "regenerated"})
        try:
            _client(url2).regenerate_profile("work")
            self.assertEqual(cap2.method, "POST")
            self.assertEqual(cap2.path, "/v1/buckets/work/profile/regenerate")
        finally:
            httpd2.shutdown()


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------

class ErrorPathTests(unittest.TestCase):
    def test_412_byok_surfaces_as_engram_error(self) -> None:
        url, _, httpd = _serve(412, {"error": "No model provider key configured"})
        try:
            with self.assertRaises(EngramError) as ctx:
                _client(url).store_memory("x", "b")
            self.assertEqual(ctx.exception.status, 412)
            self.assertEqual(ctx.exception.body, {"error": "No model provider key configured"})
        finally:
            httpd.shutdown()

    def test_401_surfaces_as_engram_error(self) -> None:
        url, _, httpd = _serve(401, {"error": "Invalid API key"})
        try:
            with self.assertRaises(EngramError) as ctx:
                _client(url).query("x")
            self.assertEqual(ctx.exception.status, 401)
        finally:
            httpd.shutdown()

    def test_non_json_500_keeps_body_as_string(self) -> None:
        url, _, httpd = _serve(500, "<html>500 internal</html>", content_type="text/html")
        try:
            with self.assertRaises(EngramError) as ctx:
                _client(url).query("x")
            self.assertEqual(ctx.exception.status, 500)
            self.assertIn("<html>", str(ctx.exception.body))
        finally:
            httpd.shutdown()


if __name__ == "__main__":
    unittest.main()
