"""A model with web search, reached over OpenRouter instead of the Anthropic API.

This is a **substitute** for ``arm_browsing``, not the same arm, and the difference
is in the name: ``websearch_openrouter`` rather than ``native_browsing``. It exists
because the Anthropic API path needs a paid key that the intended runner did not
have, while the Apify account already had credit.

What is the same:

* the question text, verbatim from the frozen set
* the system prompt, imported from ``arm_browsing`` so the two cannot drift apart
* the model family, ``claude-opus-5``
* the scoring, which reads only the answer text

What is different, and why the arm is named separately:

* **the search backend.** Anthropic's ``web_search`` server tool and OpenRouter's
  ``:online`` plugin are different retrieval systems. Two arms that differ in what
  they can find are not the same measurement, whatever the model.
* **the transport.** Each question is one call to a private Apify Actor that proxies
  to ``apify/openrouter``, so there is an extra hop and the Actor's own start
  overhead in the latency.
* **the search count.** Anthropic reports ``server_tool_use.web_search_requests``;
  the plugin does not surface a count, so ``tool_calls`` stays 0 here rather than
  carrying a number that means something else.

Cost is not estimated. OpenRouter returns ``usage.cost``, the amount it actually
charged, which is what lands in ``cost_usd``. The Actor's own pay-per-event charge
for the proxy hop is *not* included, so the figure is a floor on what the arm cost,
not the whole bill. Said plainly here because the Apify arm was overstated 1.7x and
understated 3.3x by list-rate arithmetic before this was measured properly.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harness import Result, Timer  # noqa: E402

from arm_browsing import SYSTEM  # noqa: E402  the prompts must be identical

ARM = "websearch_openrouter"

#: Same model family as ``arm_browsing.MODEL``. The ``:online`` suffix is what turns
#: on OpenRouter's web plugin; without it the model answers from training data,
#: which is a different question entirely.
MODEL = "anthropic/claude-opus-5:online"

#: The Actor that proxies to ``apify/openrouter``. There is deliberately no default:
#: the one used for the published run is private to the account that ran it, so a
#: hardcoded id would be an Actor no reader can call, and pointing at someone else's
#: private resource is not a sensible default for a public repo. Set this to your own.
#:
#: The bridge is thin. It takes ``messages``, ``system``, ``model`` and ``maxTokens``,
#: POSTs them to ``https://openrouter.apify.actor/api/v1/chat/completions``, and pushes
#: one row with ``ok``, ``text``, ``model``, ``seconds`` and OpenRouter's own ``usage``
#: object. Any Actor with that contract works here.
BRIDGE_ACTOR = os.environ.get("OPENROUTER_BRIDGE_ACTOR", "")
RUN_SYNC = "https://api.apify.com/v2/acts/{actor}/run-sync-get-dataset-items"

MAX_TOKENS = 800
TIMEOUT_SECS = 240


def _call(payload: dict[str, Any], token: str) -> dict[str, Any]:
    url = RUN_SYNC.format(actor=BRIDGE_ACTOR) + f"?token={token}"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT_SECS) as resp:  # noqa: S310
        body = json.load(resp)
    # run-sync-get-dataset-items returns the dataset, so a list of one row. An input
    # rejection comes back as a bare object instead, which is why both are handled.
    if isinstance(body, list):
        return body[0] if body else {}
    return body if isinstance(body, dict) else {}


def run_one(q: dict[str, Any]) -> Result:
    r = Result(arm=ARM, qid=q["id"])

    if not BRIDGE_ACTOR:
        r.error = (
            "Set OPENROUTER_BRIDGE_ACTOR to an Actor id that proxies to "
            "apify/openrouter. See the module docstring for the contract."
        )
        return r

    from apify_products import api_token

    payload = {
        "messages": [{"role": "user", "content": q["text"]}],
        "system": SYSTEM,
        "model": MODEL,
        "maxTokens": MAX_TOKENS,
    }

    try:
        with Timer() as t:
            row = _call(payload, api_token())
        r.latency_ms = t.ms
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        r.error = f"{type(exc).__name__}: {exc}"[:300]
        return r

    if row.get("error") and not row.get("ok"):
        # Either the bridge's own error string or an Apify input rejection.
        err = row["error"]
        r.error = str(err.get("message") if isinstance(err, dict) else err)[:300]
        return r
    if not row.get("ok"):
        r.error = f"bridge returned ok={row.get('ok')!r} with no error"
        return r

    r.answer = row.get("text") or ""
    usage = row.get("usage") or {}
    r.tokens_in = int(usage.get("prompt_tokens") or 0)
    r.tokens_out = int(usage.get("completion_tokens") or 0)
    # What OpenRouter charged, not arithmetic over published rates.
    r.cost_usd = round(float(usage.get("cost") or 0.0), 5)
    r.raw = {
        "model": row.get("model"),
        "bridge_secs": row.get("seconds"),
        "cost_source": "openrouter_usage",
        "cost_excludes": "apify actor start and proxy pay-per-event",
    }
    return r


def run_all(questions: list[dict[str, Any]]) -> list[Result]:
    return [run_one(q) for q in questions]


if __name__ == "__main__":
    from harness import load_questions

    for res in run_all(load_questions()):
        print(res.to_json())
