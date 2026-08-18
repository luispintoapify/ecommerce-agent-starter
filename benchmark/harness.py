"""Shared types and scoring for the three-arm benchmark.

**What this measures, and what it deliberately does not.**

Absolute price truth needs a human looking at the page at the same moment, so the
automated scorer does not claim it. What it scores instead is whether an arm gave you
an answer you could *act on and check*: a number, a resolvable link to the product
that was actually asked about, and an honest statement about what the retailer did
not report. Those are objective, cheap to compute, and they are what separates a
usable answer from a confident guess.

``score.py`` also flags disagreements between arms, and ``--human`` opens an optional
pass where a person fills in the true price. Run that pass before publishing any
accuracy claim.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

QUESTIONS = Path(__file__).parent / "questions.json"

#: Phrases that count as an arm being honest about a field the retailer withheld,
#: rather than asserting a value it does not have.
ABSENT_PHRASES = (
    "not reported", "does not report", "doesn't report", "not shown", "not stated",
    "not listed", "no stock information", "unavailable information", "not specified",
    "could not determine", "unable to determine", "not disclosed",
)

#: Phrases that assert absence of stock. Saying this when the retailer simply did not
#: report stock is the single most damaging error an arm can make, so it is scored.
OUT_OF_STOCK_PHRASES = ("out of stock", "sold out", "unavailable", "no longer available")

PRICE_RE = re.compile(r"(?<![\d.])(?:US)?\$\s?([\d,]+\.?\d{0,2})|([\d,]+\.\d{2})\s?(?:USD|EUR|GBP)")
URL_RE = re.compile(r"https?://[^\s\)\]\>\"']+")


@dataclass
class Result:
    """One arm's attempt at one question."""
    arm: str
    qid: str
    answer: str = ""
    latency_ms: int = 0
    cost_usd: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    tool_calls: int = 0
    error: str = ""
    blocked: bool = False
    raw: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)


def load_questions() -> list[dict[str, Any]]:
    return json.loads(QUESTIONS.read_text())["questions"]


def prices_in(text: str) -> list[float]:
    """Every money-shaped number in the answer, in order."""
    out = []
    for m in PRICE_RE.finditer(text or ""):
        raw = m.group(1) or m.group(2)
        try:
            out.append(float(raw.replace(",", "")))
        except ValueError:
            continue
    return out


def urls_in(text: str) -> list[str]:
    return [u.rstrip(".,;") for u in URL_RE.findall(text or "")]


def mentions_absent(text: str) -> bool:
    low = (text or "").lower()
    return any(p in low for p in ABSENT_PHRASES)


def asserts_out_of_stock(text: str) -> bool:
    low = (text or "").lower()
    return any(p in low for p in OUT_OF_STOCK_PHRASES)


def score_one(q: dict[str, Any], r: Result) -> dict[str, Any]:
    """Score one result against one question. Every check here is objective."""
    text = r.answer or ""
    prices = prices_in(text)
    urls = urls_in(text)

    gave_price = bool(prices)
    gave_url = bool(urls)

    # Did it link the product that was actually asked about? For URL-anchored
    # questions the identifier is in the path, so this is exact rather than fuzzy.
    #
    # Only meaningful when a URL was given at all. An arm that returned no link has
    # not linked the *wrong* product, and `gave_url` already records the absence;
    # scoring it False here would count one failure twice and overstate the
    # wrong-product column for a blocked or errored arm.
    right_product = None
    if q.get("expect_id") and urls:
        right_product = any(q["expect_id"].lower() in u.lower() for u in urls)

    # Constraint questions state a budget. An answer that names a price outside it
    # failed the question even if the price is real.
    in_budget = None
    if prices and ("max_price" in q or "min_price" in q):
        lo = q.get("min_price", 0)
        hi = q.get("max_price", float("inf"))
        in_budget = any(lo <= p <= hi for p in prices)

    # Honesty on withheld fields. Only meaningful where the question asked for stock.
    stock_honest = None
    if q["type"] == "stock":
        stock_honest = mentions_absent(text) or not asserts_out_of_stock(text)

    checks = {
        "gave_price": gave_price,
        "gave_url": gave_url,
        "right_product": right_product,
        "in_budget": in_budget,
        "stock_honest": stock_honest,
    }
    # A question is "usable" when you could act on the answer without re-researching.
    core = [gave_price, gave_url]
    if right_product is not None:
        core.append(right_product)
    if in_budget is not None:
        core.append(in_budget)
    usable = all(core) and not r.error and not r.blocked

    return {
        "arm": r.arm, "qid": r.qid, "type": q["type"],
        "usable": usable, "blocked": r.blocked, "error": r.error,
        "latency_ms": r.latency_ms, "cost_usd": r.cost_usd,
        "tokens_in": r.tokens_in, "tokens_out": r.tokens_out, "tool_calls": r.tool_calls,
        "prices_seen": prices[:5],
        **checks,
    }


class Timer:
    def __enter__(self):
        self.t = time.perf_counter()
        return self

    def __exit__(self, *a):
        self.ms = int((time.perf_counter() - self.t) * 1000)
