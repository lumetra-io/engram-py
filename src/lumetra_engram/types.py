"""Response shapes for the Engram API.

These are ``TypedDict`` definitions — they behave identically to ordinary
``dict`` at runtime but give IDEs and type checkers (mypy, pyright) the same
hints the TypeScript client exposes via ``interface``. JSON-serialize freely.

Convention
----------
- ``total=True`` (the default) is used for shapes where every documented key is
  always present in the server response — ``StoreMemoryResult``,
  ``ListMemoriesResult``, ``ProfileResult``.
- ``total=False`` is used for shapes where the server may omit keys depending
  on context — ``Bucket`` (no ``memory_count`` from ``create_bucket``),
  ``QueryExplanation`` (``profile`` and ``graph_facts`` only when retrieval
  produced them), and so on.

We deliberately don't reach for ``typing_extensions.Required``/``NotRequired``
because they'd add a runtime dependency for what is a typing-only nuance, and
the package's headline feature is zero runtime deps.
"""

from __future__ import annotations

from typing import Any, List, Optional, TypedDict


class Bucket(TypedDict, total=False):
    id: str
    name: str
    # ``bucket_name`` mirrors ``name``; the server emits both so callers
    # iterating both list_buckets and store_memory responses can use one
    # field name across the board. Prefer ``name`` in new code.
    bucket_name: str
    description: Optional[str]
    created_at: str
    memory_count: int


class Memory(TypedDict, total=False):
    id: str
    content: str
    bucket_name: str
    created_at: str
    token_count: int


class StoreMemoryResult(TypedDict, total=False):
    id: str
    # ``memory_id`` is an alias for ``id`` (older API docs / older SDKs
    # referenced this name). Always present; prefer ``id`` in new code.
    memory_id: str
    bucket_name: str
    token_count: int
    # ``"stored"`` for fresh writes; ``"merged"`` when the server collapsed
    # this write into a pre-existing memory via dedup. Always present.
    status: str
    # Present only when ``status == "merged"``. ID of the canonical
    # memory the write was absorbed into.
    deduped_into: str
    # Present only when ``status == "merged"``. Similarity score in
    # [0.0, 1.0] (1.0 for content-hash matches).
    similarity_score: float
    # Present only when ``status == "merged"``. One of:
    #   "content_hash" — byte-identical content already stored
    #   "embedding_similarity" — vector similarity ≥ dedup threshold
    #   "conflict_keep_existing" — LLM conflict resolver chose the existing
    #   "concurrent_insert_race" — another worker stored identical content
    merge_reason: str


class RetrievedMemory(TypedDict, total=False):
    id: str
    content: str
    score: float
    bucket: str


class QueryExplanation(TypedDict, total=False):
    retrieved_memories: List[RetrievedMemory]
    profile: Optional[str]
    graph_facts: List[str]


class QueryUsage(TypedDict, total=False):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class QueryResult(TypedDict, total=False):
    answer: str
    # Parsed JSON when the request set ``return_format="json"``. The
    # parsed value (dict, list, scalar) on success; ``None`` when the
    # model returned malformed JSON. Always absent for default
    # ``return_format="prose"`` queries.
    answer_json: Any
    # Top-level count of retrieved memories. Equivalent to
    # ``len(result["explanation"]["retrieved_memories"])`` but present
    # even when ``return_explanation`` is False.
    memories_found: int
    explanation: QueryExplanation
    usage: QueryUsage


class QueryStreamEvent(TypedDict, total=False):
    """One frame yielded by :meth:`EngramClient.query_stream`.

    Two shapes share the same dict:
      - ``{"type": "delta", "content": str}`` for incremental synthesis output.
      - ``{"type": "done", "usage": QueryUsage, "synthesis_usage": dict,
        "explanation": QueryExplanation}`` for the final frame.

    ``type`` discriminates between them.
    """
    type: str
    content: str
    usage: QueryUsage
    synthesis_usage: Any
    explanation: QueryExplanation


class ClearMemoriesResult(TypedDict):
    success: bool
    cleared_count: int


class ListMemoriesResult(TypedDict):
    memories: List[Memory]
    total: int
    limit: int
    offset: int


class ProfileResult(TypedDict):
    profile: Optional[str]


# Re-exported for callers who want a single ``Any``-ish dict alias.
JSON = Any
