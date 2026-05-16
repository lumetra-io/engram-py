"""Engram HTTP client.

Zero runtime dependencies — uses :mod:`urllib.request` from the standard library.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Iterator, List, Optional, Union
from urllib import error as urllib_error
from urllib import request as urllib_request
from urllib.parse import quote, urlencode

from .errors import EngramError
from .types import (
    Bucket,
    ClearMemoriesResult,
    ListMemoriesResult,
    ProfileResult,
    QueryResult,
    QueryStreamEvent,
    StoreMemoryResult,
)

DEFAULT_BASE_URL = "https://api.lumetra.io"
DEFAULT_TIMEOUT_SECONDS = 30.0
SDK_VERSION = "0.2.0"
USER_AGENT = f"engram-python/{SDK_VERSION}"


class EngramClient:
    """Synchronous client for the Engram API.

    Args:
        api_key: Engram API key (``eng_live_...``). Falls back to the
            ``ENGRAM_API_KEY`` environment variable.
        base_url: API base URL. Falls back to ``ENGRAM_BASE_URL`` or
            ``https://api.lumetra.io``.
        timeout_seconds: Request timeout. Defaults to 30 seconds.

    Raises:
        ValueError: If no API key is provided or discoverable in the environment.
    """

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        key = api_key or os.environ.get("ENGRAM_API_KEY")
        if not key:
            raise ValueError(
                "EngramClient: api_key is required. Pass it explicitly or set "
                "ENGRAM_API_KEY in your environment."
            )

        resolved_base = base_url or os.environ.get("ENGRAM_BASE_URL") or DEFAULT_BASE_URL
        self._api_key = key
        self._base_url = resolved_base.rstrip("/")
        self._timeout = timeout_seconds

    # ---------- transport ----------

    def _request(
        self,
        path: str,
        *,
        method: str = "GET",
        body: Any = None,
        query: Optional[Dict[str, Union[str, int, float, None]]] = None,
    ) -> Any:
        url = f"{self._base_url}{path}"
        if query:
            filtered = {k: v for k, v in query.items() if v is not None}
            if filtered:
                url = f"{url}?{urlencode(filtered)}"

        data: Optional[bytes] = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")

        req = urllib_request.Request(
            url,
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": USER_AGENT,
            },
        )

        try:
            with urllib_request.urlopen(req, timeout=self._timeout) as resp:
                status = resp.status
                raw = resp.read()
        except urllib_error.HTTPError as exc:
            raw = exc.read()
            status = exc.code
            parsed = _parse_body(raw)
            raise EngramError(
                _format_error_message(status, parsed), status=status, body=parsed
            ) from exc
        except urllib_error.URLError as exc:
            raise EngramError(
                f"Engram API request failed: {exc.reason}", status=0, body=None
            ) from exc

        parsed = _parse_body(raw)
        if status >= 400:
            raise EngramError(
                _format_error_message(status, parsed), status=status, body=parsed
            )
        return parsed

    # ---------- memories ----------

    def store_memory(self, content: str, bucket: str = "default") -> StoreMemoryResult:
        """Store a single memory. Returns the stored row's id, bucket, token count."""
        return self._request(
            f"/v1/buckets/{quote(bucket, safe='')}/memories",
            method="POST",
            body={"content": content},
        )

    def store_memories(
        self, contents: List[str], bucket: str = "default"
    ) -> Dict[str, List[StoreMemoryResult]]:
        """Store many memories in one call. Returns ``{"memories": [...]}``."""
        return self._request(
            f"/v1/buckets/{quote(bucket, safe='')}/memories",
            method="POST",
            body={"memories": [{"content": c} for c in contents]},
        )

    def list_memories(
        self,
        bucket: str = "default",
        *,
        limit: int = 20,
        offset: int = 0,
    ) -> ListMemoriesResult:
        """Page through memories in a bucket."""
        return self._request(
            f"/v1/buckets/{quote(bucket, safe='')}/memories",
            method="GET",
            query={"limit": limit, "offset": offset},
        )

    def delete_memory(self, memory_id: str, bucket: str = "default") -> None:
        """Delete one memory by id."""
        self._request(
            f"/v1/buckets/{quote(bucket, safe='')}/memories/{quote(memory_id, safe='')}",
            method="DELETE",
        )

    def clear_memories(self, bucket: str) -> ClearMemoriesResult:
        """Delete every memory in a bucket. Destructive.

        Returns the count of memories actually deleted under
        ``cleared_count`` (server reports it in the response).
        """
        result = self._request(
            f"/v1/buckets/{quote(bucket, safe='')}/memories",
            method="DELETE",
        )
        return result or {"success": True, "cleared_count": 0}

    # ---------- query ----------

    def query(
        self,
        question: str,
        *,
        buckets: Optional[List[str]] = None,
        top_k: int = 8,
        skip_synthesis: bool = False,
        return_explanation: bool = True,
    ) -> QueryResult:
        """Hybrid retrieval + optional server-side synthesis.

        Args:
            question: Natural-language query.
            buckets: One or more buckets to fuse across. Defaults to ``["default"]``.
            top_k: Maximum memories to retrieve. Defaults to 8.
            skip_synthesis: If True, server returns retrieval-only — ``answer``
                will be empty. Use this if you're composing the answer yourself
                with your own model.
            return_explanation: Include the ``explanation`` payload (retrieved
                memories, profile, graph facts). Defaults to True.
        """
        return self._request(
            "/v1/query",
            method="POST",
            body={
                "query": question,
                "buckets": buckets if buckets is not None else ["default"],
                "options": {
                    "top_k": top_k,
                    "return_explanation": return_explanation,
                    "skip_synthesis": skip_synthesis,
                },
            },
        )

    def query_stream(
        self,
        question: str,
        *,
        buckets: Optional[List[str]] = None,
        top_k: int = 8,
        skip_synthesis: bool = False,
        return_explanation: bool = True,
    ) -> Iterator[QueryStreamEvent]:
        """Streaming variant of :meth:`query`.

        Yields event dicts as the server produces them:

            for event in engram.query_stream("..."):
                if event["type"] == "delta":
                    print(event["content"], end="", flush=True)
                elif event["type"] == "done":
                    print()
                    print(f"Used {event['usage']['output_tokens']} tokens")

        Events have one of two shapes::

            {"type": "delta", "content": str}
            {"type": "done",  "usage": {...}, "synthesis_usage": {...},
             "explanation": {...} | absent}

        The connection stays open for the lifetime of the iterator. Break
        out of the loop early to close it.
        """
        body = {
            "query": question,
            "buckets": buckets if buckets is not None else ["default"],
            "stream": True,
            "options": {
                "top_k": top_k,
                "return_explanation": return_explanation,
                "skip_synthesis": skip_synthesis,
            },
        }
        url = f"{self._base_url}/v1/query"
        req = urllib_request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
                "User-Agent": USER_AGENT,
            },
        )

        try:
            response = urllib_request.urlopen(req, timeout=self._timeout)
        except urllib_error.HTTPError as exc:
            # Match the buffered path's error surface so callers get a
            # consistent EngramError on auth/quota failures.
            body_bytes = exc.read() if hasattr(exc, "read") else b""
            try:
                payload = json.loads(body_bytes.decode("utf-8")) if body_bytes else None
            except Exception:
                payload = None
            raise EngramError(
                f"HTTP {exc.code}: {payload.get('error') if isinstance(payload, dict) else body_bytes.decode('utf-8', 'replace')}",
                status_code=exc.code,
                response_body=payload if payload is not None else body_bytes.decode("utf-8", "replace"),
            ) from exc

        try:
            for raw_line in response:
                line = raw_line.decode("utf-8").rstrip("\r\n")
                if not line.startswith("data: "):
                    continue
                payload_str = line[6:]
                if payload_str == "[DONE]":
                    return
                try:
                    payload = json.loads(payload_str)
                except json.JSONDecodeError:
                    # Malformed frame — skip rather than crash the stream.
                    continue
                if isinstance(payload, dict) and payload.get("error"):
                    raise EngramError(str(payload["error"]))
                # OpenAI-style delta chunk
                choices = payload.get("choices") if isinstance(payload, dict) else None
                if choices:
                    delta = (choices[0] or {}).get("delta", {}).get("content")
                    if delta:
                        yield {"type": "delta", "content": delta}
                    continue
                # Final usage/explanation frame
                if isinstance(payload, dict):
                    yield {"type": "done", **payload}
        finally:
            try:
                response.close()
            except Exception:
                pass

    # ---------- buckets ----------

    def list_buckets(self) -> List[Bucket]:
        """All buckets in your tenant."""
        result = self._request("/v1/buckets", method="GET")
        if isinstance(result, list):
            return result
        return result.get("buckets", [])

    def create_bucket(self, name: str, description: Optional[str] = None) -> Bucket:
        return self._request(
            "/v1/buckets",
            method="POST",
            body={"name": name, "description": description},
        )

    def delete_bucket(self, bucket: str) -> None:
        self._request(f"/v1/buckets/{quote(bucket, safe='')}", method="DELETE")

    # ---------- profile ----------

    def get_profile(self, bucket: str = "default") -> ProfileResult:
        """The canonical profile prepended to recall for this bucket."""
        return self._request(
            f"/v1/buckets/{quote(bucket, safe='')}/profile",
            method="GET",
        )

    def regenerate_profile(self, bucket: str = "default") -> ProfileResult:
        """Rebuild the profile from current memories. Synchronous; can take seconds."""
        return self._request(
            f"/v1/buckets/{quote(bucket, safe='')}/profile/regenerate",
            method="POST",
        )


def _parse_body(raw: bytes) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        try:
            return raw.decode("utf-8", errors="replace")
        except Exception:
            return None


def _format_error_message(status: int, parsed: Any) -> str:
    detail: Any = parsed
    if isinstance(parsed, dict) and "error" in parsed:
        detail = parsed["error"]
    if isinstance(detail, str):
        return f"Engram API {status}: {detail}"
    return f"Engram API {status}: {json.dumps(detail) if detail is not None else ''}"
