"""Arm A: an agent with E-commerce Scraping Tool over the Apify MCP server.

Reuses the repo's own mcp_client, so this arm measures the same code path a reader
would ship rather than a benchmark-only shortcut.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harness import Result, Timer  # noqa: E402

ARM = "apify_mcp"

#: Measured from two single-product runs and one 175-product run. Used to report a
#: cost per question rather than leaving the column empty. Replace with the figure
#: from your own Apify Console billing if you need precision.
USD_PER_START = 0.0026
USD_PER_PRODUCT = 0.0017


def build_input(q: dict[str, Any]) -> dict[str, Any]:
    """Turn a question into Actor input. Mirrors the guidance in the skills."""
    base: dict[str, Any] = {"additionalProperties": True, "scrapeMode": "AUTO"}
    if q.get("url"):
        return {**base, "detailsUrls": [{"url": q["url"]}], "maxProductResults": 1}
    return {
        **base,
        "keyword": q["keyword"],
        "marketplaces": q.get("marketplaces", ["www.amazon.com"]),
        "maxProductResults": 10,
    }


def answer_from(products: list[Any], q: dict[str, Any]) -> str:
    """Compose the answer text the scorer will read.

    Deliberately mechanical. This arm is being measured on the data it retrieves, so
    a language model is not put in front of it to phrase things more persuasively.
    """
    if not products:
        return "No product data came back for this question."
    lines = []
    for p in products[:5]:
        bits = [p.name or "(unnamed)", p.format_price() or "(no price)"]
        if p.in_stock is True:
            bits.append("in stock")
        elif p.in_stock is False:
            bits.append("out of stock")
        else:
            bits.append("stock not reported by this retailer")
        if p.rating is not None:
            bits.append(f"{p.rating:.1f} stars")
        else:
            bits.append("rating not reported")
        pct = p.discount_percent()
        if pct:
            bits.append(f"{pct}% below the list price of {p.list_price}")
        lines.append(f"{' | '.join(bits)}  {p.url}")
    return "As of just now:\n" + "\n".join(lines)


async def run_one(q: dict[str, Any]) -> Result:
    from mcp_client import fetch_products  # imported here so a missing key fails per-arm

    r = Result(arm=ARM, qid=q["id"])
    try:
        with Timer() as t:
            products, meta = await fetch_products(build_input(q), limit=5)
        r.latency_ms = t.ms
        r.answer = answer_from(products, q)
        r.tool_calls = 3  # call, poll, fetch
        r.cost_usd = round(USD_PER_START + USD_PER_PRODUCT * len(products), 5)
        r.raw = {"runId": meta.get("runId"), "status": meta.get("status"), "n": len(products)}
    except Exception as exc:  # noqa: BLE001 - every arm records its own failure
        r.error = f"{type(exc).__name__}: {exc}"[:300]
    return r


async def run_all(questions: list[dict[str, Any]]) -> list[Result]:
    out = []
    for q in questions:
        out.append(await run_one(q))
    return out


if __name__ == "__main__":
    from harness import load_questions

    for res in asyncio.run(run_all(load_questions())):
        print(res.to_json())
