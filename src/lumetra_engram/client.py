"""Engram HTTP client.

Zero runtime dependencies — uses :mod:`urllib.request` from the standard library.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Iterator, List, Optional, Union
import time
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
# Streaming responses can sit in the prep phase (retrieval + extractor
# pass + count canonicalization) for 5-15s before the first synthesis
# token lands. The 30s default applied to query_stream() would leave no
# headroom for the actual streamed body, so we bump it for the stream
# path only.
DEFAULT_STREAM_TIMEOUT_SECONDS = 300.0
DEFAULT_MAX_RETRIES_ON_429 = 3
# Cap on how long we'll sleep between retries even if the server's
# Retry-After header asks for more — protects callers from a server
# accidentally telling them to wait 10 minutes.
_RETRY_AFTER_CAP_SECONDS = 30.0
SDK_VERSION = "0.4.1"
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
        stream_timeout_seconds: float = DEFAULT_STREAM_TIMEOUT_SECONDS,
        max_retries_on_429: int = DEFAULT_MAX_RETRIES_ON_429,
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
        self._stream_timeout = stream_timeout_seconds
        self._max_retries_on_429 = max(0, max_retries_on_429)

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

        # 429-aware retry loop. The Engram API enforces a per-tenant
        # concurrent-request cap and sets Retry-After on 429s; bursty
        # clients without retry handling otherwise blow up under load
        # (see customer feedback: a 32-worker run died because the
        # client retried 5xx but not 429). max_retries_on_429=0 disables.
        attempts_remaining = self._max_retries_on_429
        backoff = 1.0
        while True:
            try:
                with urllib_request.urlopen(req, timeout=self._timeout) as resp:
                    status = resp.status
                    raw = resp.read()
                break
            except urllib_error.HTTPError as exc:
                if exc.code == 429 and attempts_remaining > 0:
                    delay = _parse_retry_after(exc.headers.get("Retry-After"), backoff)
                    time.sleep(delay)
                    attempts_remaining -= 1
                    backoff = min(backoff * 2.0, _RETRY_AFTER_CAP_SECONDS)
                    continue
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

    def store_memory(
        self,
        content: str,
        bucket: str = "default",
        *,
        dedup: Optional[str] = None,
    ) -> StoreMemoryResult:
        """Store a single memory. Returns the stored row's id, bucket, token count.

        Args:
            content: The fact / chunk to store.
            bucket: Bucket name. Defaults to ``"default"``.
            dedup: Optional dedup policy. One of ``"off"``, ``"loose"``,
                ``"strict"``. When ``None`` (the default), the server's
                policy applies (currently ``"loose"`` = similarity ≥ 0.95
                collapses). Pass ``"off"`` for templated time-series
                ingest where similar-but-distinct rows would otherwise
                merge silently. Pass ``"strict"`` to only merge near-
                identical content (≥ 0.99). The response includes a
                ``status`` field — ``"merged"`` indicates the write was
                absorbed into an existing memory; check ``deduped_into``
                / ``similarity_score`` / ``merge_reason`` for details.
        """
        body: Dict[str, Any] = {"content": content}
        if dedup is not None:
            body["dedup"] = dedup
        return self._request(
            f"/v1/buckets/{quote(bucket, safe='')}/memories",
            method="POST",
            body=body,
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
        max_tokens: Optional[int] = None,
        min_similarity_threshold: Optional[float] = None,
        min_weighted_score: Optional[float] = None,
        top_k_per_bucket: Optional[Union[int, Dict[str, int]]] = None,
        return_format: Optional[str] = None,
        response_schema: Optional[Dict[str, Any]] = None,
    ) -> QueryResult:
        """Hybrid retrieval + optional server-side synthesis.

        Args:
            question: Natural-language query.
            buckets: One or more buckets to fuse across. Defaults to ``["default"]``.
            top_k: Maximum memories to retrieve (default 8). Used as
                the per-bucket cap when ``top_k_per_bucket`` isn't set.
            skip_synthesis: If True, server returns retrieval-only —
                ``answer`` will be empty. Use this if you're composing
                the answer yourself with your own model.
            return_explanation: Include the ``explanation`` payload
                (retrieved memories, profile, graph facts). Defaults
                to True.
            max_tokens: Cap synthesis output. Default is the server's
                (currently 8192). Lower for agent loops / cost control.
            min_similarity_threshold: Drop retrieved chunks whose
                **raw cosine similarity** (the underlying embedding
                score) is below this. Acts as a floor over the server's
                adaptive threshold. Useful when you specifically want
                a precision floor on the embedding signal.
            min_weighted_score: Drop retrieved chunks whose
                **weighted_score** (the post-RRF score surfaced in
                ``explanation.retrieved_memories``) is below this. This
                is the score you see in responses — most callers want
                this rather than ``min_similarity_threshold`` because
                the scales match.
            top_k_per_bucket: ``int`` (same K for every bucket) or
                ``dict`` (``{bucket_name: int}`` with per-bucket K).
                Lets you say "20 from edgar_AAPL, 4 from prices_AAPL"
                instead of fighting our defaults. When omitted, falls
                back to ``top_k`` for every bucket.
            return_format: ``"prose"`` (default) or ``"json"``. When
                ``"json"``, server asks the synthesizer for JSON and
                returns the parsed result under ``result["answer_json"]``.
            response_schema: Optional JSON Schema dict describing the
                desired output shape. Included in the prompt to guide
                the model. Best-effort — validate client-side if you
                need strict enforcement.
        """
        options: Dict[str, Any] = {
            "top_k": top_k,
            "return_explanation": return_explanation,
            "skip_synthesis": skip_synthesis,
        }
        if max_tokens is not None:
            options["max_tokens"] = max_tokens
        if min_similarity_threshold is not None:
            options["min_similarity_threshold"] = min_similarity_threshold
        if min_weighted_score is not None:
            options["min_weighted_score"] = min_weighted_score
        if top_k_per_bucket is not None:
            options["top_k_per_bucket"] = top_k_per_bucket
        if return_format is not None:
            options["return_format"] = return_format
        if response_schema is not None:
            options["response_schema"] = response_schema
        return self._request(
            "/v1/query",
            method="POST",
            body={
                "query": question,
                "buckets": buckets if buckets is not None else ["default"],
                "options": options,
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
        max_tokens: Optional[int] = None,
        min_similarity_threshold: Optional[float] = None,
        min_weighted_score: Optional[float] = None,
        top_k_per_bucket: Optional[Union[int, Dict[str, int]]] = None,
        return_format: Optional[str] = None,
        response_schema: Optional[Dict[str, Any]] = None,
    ) -> Iterator[QueryStreamEvent]:
        """Streaming variant of :meth:`query`. See :meth:`query` for arg semantics.

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
             "explanation": {...} | absent,
             "answer_json": Any | absent}    # when return_format=="json"

        The connection stays open for the lifetime of the iterator. Break
        out of the loop early to close it.
        """
        options: Dict[str, Any] = {
            "top_k": top_k,
            "return_explanation": return_explanation,
            "skip_synthesis": skip_synthesis,
        }
        if max_tokens is not None:
            options["max_tokens"] = max_tokens
        if min_similarity_threshold is not None:
            options["min_similarity_threshold"] = min_similarity_threshold
        if min_weighted_score is not None:
            options["min_weighted_score"] = min_weighted_score
        if top_k_per_bucket is not None:
            options["top_k_per_bucket"] = top_k_per_bucket
        if return_format is not None:
            options["return_format"] = return_format
        if response_schema is not None:
            options["response_schema"] = response_schema
        body = {
            "query": question,
            "buckets": buckets if buckets is not None else ["default"],
            "stream": True,
            "options": options,
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

        # 429-aware retry: same policy as the buffered path. Once the
        # response body starts flowing we can't safely resume, so retry
        # only at the connection-open stage.
        attempts_remaining = self._max_retries_on_429
        backoff = 1.0
        while True:
            try:
                response = urllib_request.urlopen(req, timeout=self._stream_timeout)
                break
            except urllib_error.HTTPError as exc:
                if exc.code == 429 and attempts_remaining > 0:
                    delay = _parse_retry_after(exc.headers.get("Retry-After"), backoff)
                    time.sleep(delay)
                    attempts_remaining -= 1
                    backoff = min(backoff * 2.0, _RETRY_AFTER_CAP_SECONDS)
                    continue
                # Match the buffered path's error surface so callers get
                # a consistent EngramError on auth/quota failures.
                body_bytes = exc.read() if hasattr(exc, "read") else b""
                try:
                    payload = json.loads(body_bytes.decode("utf-8")) if body_bytes else None
                except Exception:
                    payload = None
                raise EngramError(
                    f"HTTP {exc.code}: {payload.get('error') if isinstance(payload, dict) else body_bytes.decode('utf-8', 'replace')}",
                    status=exc.code,
                    body=payload if payload is not None else body_bytes.decode("utf-8", "replace"),
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
                    raise EngramError(str(payload["error"]), status=0, body=payload)
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
        """Queue a profile-tick on the bucket's installed Bucket Profiler agent.

        Returns the latest profile snapshot (which may still be the previous
        one if the new tick is still running). Raises :class:`EngramError`
        with status 412 (``BUCKET_PROFILER_NOT_INSTALLED``) when the bucket
        has no profiler agent installed yet — call
        :meth:`ensure_profiler_agent` first.
        """
        return self._request(
            f"/v1/buckets/{quote(bucket, safe='')}/profile/regenerate",
            method="POST",
        )


def _parse_retry_after(header: Optional[str], default_backoff: float) -> float:
    """Resolve a Retry-After header to a sleep duration in seconds.

    Honors the integer-seconds form (the only form the Engram API
    currently emits). Caps at _RETRY_AFTER_CAP_SECONDS so a misconfigured
    server can't force callers to sleep for minutes. Falls back to the
    caller-supplied exponential backoff when the header is missing or
    unparseable."""
    if header:
        try:
            value = float(header.strip())
            return max(0.0, min(value, _RETRY_AFTER_CAP_SECONDS))
        except (TypeError, ValueError):
            pass
    return min(default_backoff, _RETRY_AFTER_CAP_SECONDS)


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
