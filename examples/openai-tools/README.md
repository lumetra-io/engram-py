# Engram + OpenAI tools (Python)

Minimal terminal chat loop showing how to expose Engram as OpenAI tool calls. The model decides when to `store_memory` and `query_memory`; we just wire the tools to the `EngramClient` methods and pass them through.

## Run

```bash
pip install lumetra-engram openai
export ENGRAM_API_KEY=eng_live_...
export OPENAI_API_KEY=sk-...
python chat.py
```

Type messages. The model has access to two tools:

- `store_memory(content, bucket?)` — save a fact
- `query_memory(question, bucket?)` — recall facts from memory

The agent prompt nudges the model to capture stable preferences and recall before answering anything that might depend on prior context.

## BYOK reminder

Engram is bring-your-own-key end-to-end. Configure your LLM provider key on the [Lumetra portal](https://lumetra.io/models) before the first call — otherwise `store_memory` / `query_memory` return HTTP 412 and you'll see an `EngramError` here.

## Files

- `chat.py` — the whole thing, ~120 lines. Read it top-to-bottom.
