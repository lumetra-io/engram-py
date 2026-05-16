# lumetra-engram

Official Python client for [Engram](https://lumetra.io) — durable, explainable memory for AI agents.

- Zero runtime dependencies (uses the standard library's `urllib`).
- Fully typed (`py.typed`, `TypedDict` response shapes, IDE-friendly).
- Python 3.9+.

The TypeScript twin lives at [`lumetra-io/engram-js`](https://github.com/lumetra-io/engram-js).

## Install

```bash
pip install lumetra-engram
# or
uv add lumetra-engram
# or
poetry add lumetra-engram
```

## Quickstart

```python
from lumetra_engram import EngramClient

engram = EngramClient(api_key="eng_live_...")  # or set ENGRAM_API_KEY and omit

# Store a fact
engram.store_memory("User prefers dark mode.", "user-123")

# Recall — returns a synthesized answer plus the memories that contributed
result = engram.query(
    "What are this user's UI preferences?",
    buckets=["user-123"],
)

print(result["answer"])
print(result.get("explanation", {}).get("retrieved_memories", []))
```

## Configuration

```python
EngramClient(
    api_key="eng_live_...",            # or ENGRAM_API_KEY env var
    base_url="https://api.lumetra.io", # or ENGRAM_BASE_URL env var
    timeout_seconds=30.0,              # default 30s
    max_retries_on_429=3,              # auto-retry on per-tenant rate limit; 0 disables
)
```

### Automatic 429 retry

The Engram API enforces a per-tenant concurrent-request cap and returns `429 Too Many Requests` with a `Retry-After` header when you exceed it. The client honors that header automatically (up to `max_retries_on_429` attempts, capped at 30s per sleep) so bursty workloads don't fail on the first contention spike. Pass `max_retries_on_429=0` to opt out and surface 429 as `EngramError` immediately.

> **BYOK reminder.** Engram is bring-your-own-key end-to-end. Configure an OpenAI / Anthropic / Groq / Together / Fireworks key on the [Lumetra portal](https://lumetra.io/models) before your first call, or `store_memory` / `query` will raise `EngramError` with `status == 412`.

## API surface

### Memories
- `store_memory(content, bucket="default")` — store a single fact
- `store_memories(contents, bucket="default")` — batched store
- `list_memories(bucket="default", *, limit=20, offset=0)` — paginated list
- `delete_memory(memory_id, bucket="default")` — delete one memory
- `clear_memories(bucket)` — delete every memory in a bucket. **No default — explicit bucket required** (prevents accidental wipes).

### Query
- `query(question, *, buckets=None, top_k=8, skip_synthesis=False, return_explanation=True)`
  - `buckets` fuses across multiple buckets in one call. Defaults to `["default"]`.
  - `skip_synthesis=True` returns retrieval-only — no server-side LLM call
  - response shape: `{"answer", "explanation": {"retrieved_memories", "profile", "graph_facts"}, "usage"}`
- `query_stream(question, *, buckets=None, top_k=8, skip_synthesis=False, return_explanation=True)` — same args, streams the answer as it's generated

## Streaming

For broad questions, synthesis can take 10–25 seconds. `query_stream` yields the answer incrementally so you can render it as it's produced instead of waiting for the full response:

```python
from lumetra_engram import EngramClient

engram = EngramClient()

for event in engram.query_stream("Summarize what I worked on this week", buckets=["work"]):
    if event["type"] == "delta":
        print(event["content"], end="", flush=True)
    elif event["type"] == "done":
        print()
        print(f"\nUsed {event['usage']['output_tokens']} tokens")
```

Two frame types:
- `{"type": "delta", "content": str}` — incremental synthesis output, in order. Zero or more.
- `{"type": "done", "answer": str, "usage": {...}, "synthesis_usage": {...}, "explanation": {...}}` — emitted exactly once at the end with the assembled answer and final usage/explanation.

Break out of the loop early to abort the request and close the connection.

### Buckets
- `list_buckets()` — all buckets in your tenant
- `create_bucket(name, description=None)`
- `delete_bucket(bucket)` — **No default — explicit bucket required** (prevents accidental wipes).

### Profile
- `get_profile(bucket="default")` — the canonical profile prepended to recall
- `regenerate_profile(bucket="default")` — rebuild from current memories

## Errors

All non-2xx HTTP responses raise `EngramError`:

```python
from lumetra_engram import EngramClient, EngramError

engram = EngramClient()

try:
    engram.store_memory("User prefers dark mode.", "user-123")
except EngramError as err:
    if err.status == 412:
        print("BYOK not configured — set an LLM provider key in the Lumetra portal.")
    elif err.status == 429:
        print("Rate limited — back off and retry.")
    else:
        print(f"Engram {err.status}: {err}")
        print("Body:", err.body)
```

`err.status` is the HTTP status (or `0` for connection failures), `err.body` is the parsed JSON body when one was returned.

## Async usage

This client is synchronous. For async code, wrap calls in `asyncio.to_thread`:

```python
import asyncio
from lumetra_engram import EngramClient

engram = EngramClient()

async def recall(question: str):
    return await asyncio.to_thread(engram.query, question, buckets=["user-123"])
```

A dedicated async client may land later; until then, the thread wrapper is the recommended pattern.

## Type hints

Return shapes are declared as `TypedDict` in `lumetra_engram.types`. They behave as ordinary `dict` at runtime — JSON-serialize freely — but give mypy and pyright the same level of detail the TypeScript client exposes via `interface`.

```python
from lumetra_engram import QueryResult

def summarize(result: QueryResult) -> str:
    return result.get("answer", "")
```

## License

MIT — see [`LICENSE`](./LICENSE).
