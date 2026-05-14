"""Terminal chat loop with Engram memory as OpenAI tool calls.

Run:
    pip install lumetra-engram openai
    export ENGRAM_API_KEY=eng_live_...
    export OPENAI_API_KEY=sk-...
    python chat.py

The model decides when to store and recall. We just expose the tools.
"""

from __future__ import annotations

import json
import os
import sys

try:
    from openai import OpenAI
except ImportError:
    print("Missing dependency: pip install openai", file=sys.stderr)
    sys.exit(1)

from lumetra_engram import EngramClient, EngramError


BUCKET = os.environ.get("ENGRAM_BUCKET", "openai-tools-demo")
MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

SYSTEM_PROMPT = (
    "You have Engram memory. Use it proactively to improve continuity.\n"
    "\n"
    "Policy:\n"
    "- Before answering anything that may rely on prior context, call query_memory.\n"
    "- Capture stable preferences, profile facts, and decisions via store_memory.\n"
    "- Keep stored facts atomic and declarative: one concept per memory.\n"
)

# Tool schemas the model sees. The JSON Schema is what OpenAI uses for
# tool-call validation; the actual implementations live below.
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "store_memory",
            "description": "Save a fact to Engram memory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {"type": "string", "description": "The atomic fact to remember."},
                    "bucket": {"type": "string", "description": f"Bucket name (default: {BUCKET!r})."},
                },
                "required": ["content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_memory",
            "description": "Search Engram memory using natural language and get a synthesized answer.",
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "What to look up."},
                    "bucket": {"type": "string", "description": f"Bucket name (default: {BUCKET!r})."},
                },
                "required": ["question"],
            },
        },
    },
]


def make_tools(engram: EngramClient):
    def store_memory(content: str, bucket: str = BUCKET) -> dict:
        try:
            return engram.store_memory(content, bucket)
        except EngramError as e:
            return {"error": f"{e.status}: {e}"}

    def query_memory(question: str, bucket: str = BUCKET) -> dict:
        try:
            return engram.query(question, buckets=[bucket])
        except EngramError as e:
            return {"error": f"{e.status}: {e}"}

    return {"store_memory": store_memory, "query_memory": query_memory}


def main() -> int:
    if not os.environ.get("ENGRAM_API_KEY"):
        print("Set ENGRAM_API_KEY first.", file=sys.stderr)
        return 1
    if not os.environ.get("OPENAI_API_KEY"):
        print("Set OPENAI_API_KEY first.", file=sys.stderr)
        return 1

    engram = EngramClient()
    openai = OpenAI()
    tools = make_tools(engram)

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    print(f"Engram bucket: {BUCKET}  ·  model: {MODEL}  ·  Ctrl-D to quit\n")

    while True:
        try:
            user = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not user:
            continue
        messages.append({"role": "user", "content": user})

        # Tool-call loop: keep round-tripping until the model returns a
        # plain assistant message.
        while True:
            resp = openai.chat.completions.create(model=MODEL, messages=messages, tools=TOOLS)
            choice = resp.choices[0].message
            messages.append(choice.model_dump(exclude_none=True))

            if not choice.tool_calls:
                print(f"\nagent> {choice.content}\n")
                break

            for call in choice.tool_calls:
                name = call.function.name
                args = json.loads(call.function.arguments or "{}")
                fn = tools.get(name)
                result = fn(**args) if fn else {"error": f"unknown tool {name}"}
                messages.append({
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": json.dumps(result, default=str),
                })


if __name__ == "__main__":
    raise SystemExit(main())
