"""Arm B: the same questions answered by a model with native web search.

This is the arm the campaign compares against, so it is worth being scrupulous about
giving it a fair run: the same questions, a prompt that asks for exactly what the
scorer looks for, and no instruction that would handicap it.

Requires ``ANTHROPIC_API_KEY`` (or an ``ant auth login`` profile). Nothing in this
file is Apify-specific.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harness import Result, Timer

ARM = "native_browsing"
MODEL = "claude-opus-5"

#: Published list rates for the model above, and the documented web-search rate.
USD_IN_PER_MTOK = 5.00
USD_OUT_PER_MTOK = 25.00
USD_PER_SEARCH = 0.010

#: Asks for exactly what the scorer checks. Giving this arm less would make the
#: comparison flattering rather than informative.
SYSTEM = """Answer the product question using web search.

Always include, when the question calls for it:
- the current price, with its currency
- whether the item is in stock, and say plainly if the page does not report stock
- the customer rating, if shown
- a direct link to the product page you read the price from

Quote the price from the page you link. If you cannot find a figure, say so rather
than estimating. Be brief."""


def _usage(u: Any) -> tuple[int, int, int]:
    """Input tokens, output tokens, and web searches performed."""
    tin = (getattr(u, "input_tokens", 0) or 0) + (getattr(u, "cache_read_input_tokens", 0) or 0)
    tout = getattr(u, "output_tokens", 0) or 0
    stu = getattr(u, "server_tool_use", None)
    searches = getattr(stu, "web_search_requests", 0) or 0 if stu else 0
    return tin, tout, searches


def run_one(q: dict[str, Any], model: str = MODEL) -> Result:
    import anthropic

    r = Result(arm=ARM, qid=q["id"])
    client = anthropic.Anthropic()
    messages: list[dict[str, Any]] = [{"role": "user", "content": q["text"]}]
    tools = [{"type": "web_search_20260209", "name": "web_search"}]

    tin = tout = searches = 0
    try:
        with Timer() as t:
            # A long server-tool turn can stop with pause_turn. Resend to continue;
            # the server picks up where it left off. Capped so a pathological
            # question cannot spin forever.
            for _ in range(6):
                resp = client.messages.create(
                    model=model,
                    max_tokens=4096,
                    system=SYSTEM,
                    tools=tools,
                    messages=messages,
                )
                a, b, c = _usage(resp.usage)
                tin += a
                tout += b
                searches += c

                if resp.stop_reason == "refusal":
                    r.error = "model declined the request"
                    break
                if resp.stop_reason == "pause_turn":
                    messages = [
                        {"role": "user", "content": q["text"]},
                        {"role": "assistant", "content": resp.content},
                    ]
                    continue
                r.answer = "\n".join(b.text for b in resp.content if b.type == "text")
                break
            else:
                r.error = "still paused after 6 continuations"
        r.latency_ms = t.ms
    except Exception as exc:  # noqa: BLE001
        r.error = f"{type(exc).__name__}: {exc}"[:300]

    r.tokens_in, r.tokens_out, r.tool_calls = tin, tout, searches
    r.cost_usd = round(
        tin / 1e6 * USD_IN_PER_MTOK + tout / 1e6 * USD_OUT_PER_MTOK + searches * USD_PER_SEARCH, 5
    )
    return r


def run_all(questions: list[dict[str, Any]], model: str = MODEL) -> list[Result]:
    return [run_one(q, model) for q in questions]


if __name__ == "__main__":
    from harness import load_questions

    for res in run_all(load_questions()):
        print(res.to_json())
