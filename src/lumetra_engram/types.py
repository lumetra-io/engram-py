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
    description: Optional[str]
    created_at: str
    memory_count: int


class Memory(TypedDict, total=False):
    id: str
    content: str
    bucket_name: str
    created_at: str
    token_count: int


class StoreMemoryResult(TypedDict):
    id: str
    bucket_name: str
    token_count: int


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
