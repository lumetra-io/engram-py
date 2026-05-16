"""Official Python client for Engram — durable, explainable memory for AI agents.

    from lumetra_engram import EngramClient

    engram = EngramClient(api_key="eng_live_...")
    engram.store_memory("User prefers dark mode.", "user-123")
    result = engram.query("What are this user's UI preferences?", buckets=["user-123"])
    print(result["answer"])

See https://lumetra.io for an account, https://github.com/lumetra-io/engram-py
for source.
"""

from .client import DEFAULT_BASE_URL, DEFAULT_TIMEOUT_SECONDS, EngramClient
from .errors import EngramError
from .types import (
    Bucket,
    ClearMemoriesResult,
    JSON,
    ListMemoriesResult,
    Memory,
    ProfileResult,
    QueryExplanation,
    QueryResult,
    QueryStreamEvent,
    QueryUsage,
    RetrievedMemory,
    StoreMemoryResult,
)

__version__ = "0.2.2"

__all__ = [
    "EngramClient",
    "EngramError",
    "Bucket",
    "Memory",
    "StoreMemoryResult",
    "ClearMemoriesResult",
    "RetrievedMemory",
    "QueryExplanation",
    "QueryUsage",
    "QueryResult",
    "QueryStreamEvent",
    "ListMemoriesResult",
    "ProfileResult",
    "JSON",
    "DEFAULT_BASE_URL",
    "DEFAULT_TIMEOUT_SECONDS",
    "__version__",
]
