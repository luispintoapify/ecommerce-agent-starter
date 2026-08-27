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

#: Phrases that assert absence of stock. Saying this when the retailer did not
#: report stock is the single most damaging error an arm can make, so it is scored.
OUT_OF_STOCK_PHRASES = ("out of stock", "sold out", "unavailable", "no longer available")

#: Money in either order: a symbol or an ISO code before the amount, or after it.
#: The questions all ask about US retailers, so a figure in another currency is a
#: *different* failure from no figure at all: scraping amazon.com from a European IP
#: returns a localized page, and reporting "no price" for a CZK price hides that.
_CUR_CODE = "USD|EUR|GBP|CZK|CAD|AUD|CHF|PLN|SEK|DKK|NOK|JPY"
_SYMBOL = r"US\$|\$|\u20ac|\u00a3|K\u010d"
_AMT = r"\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?"
MONEY_RE = re.compile(
    rf"(?<![\w.])(?:(?P<pre>{_SYMBOL}|{_CUR_CODE})\s?(?P<amt1>{_AMT})"
    rf"|(?P<amt2>{_AMT})\s?(?P<post>{_SYMBOL}|{_CUR_CODE}))(?!\d)",
    re.IGNORECASE,
)

#: Symbol to ISO code. "$" is treated as USD because every question names a US
#: retailer; an arm that means CAD has to say so.
_SYMBOL_CODE = {"$": "USD", "US$": "USD", "\u20ac": "EUR", "\u00a3": "GBP", "K\u010d": "CZK"}
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


def money_in(text: str) -> list[tuple[str, float]]:
    """Every money-shaped figure in the answer as (ISO currency, amount), in order."""
    out: list[tuple[str, float]] = []
    for m in MONEY_RE.finditer(text or ""):
        raw = m.group("amt1") or m.group("amt2")
        token = (m.group("pre") or m.group("post") or "").strip()
        code = _SYMBOL_CODE.get(token, _SYMBOL_CODE.get(token.upper(), token.upper()))
        try:
            out.append((code, float(raw.replace(",", ""))))
        except ValueError:
            continue
    return out


def prices_in(text: str) -> list[float]:
    """The USD amounts in the answer, in order.

    Budgets and cross-arm comparison are both in USD, so a foreign figure is
    deliberately excluded here rather than silently compared against a dollar
    threshold. Use ``money_in`` when you need to know a figure was given at all.
    """
    return [amt for code, amt in money_in(text) if code == "USD"]


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
    money = money_in(text)
    urls = urls_in(text)

    # A figure was given, in whatever currency. The currency is judged separately.
    gave_price = bool(money)
    gave_url = bool(urls)

    # Every question names a US retailer, so a non-USD figure is not an answer to
    # the question that was asked. None when no figure was given at all.
    right_currency = None
    if money:
        right_currency = all(code == "USD" for code, _ in money)

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
        "right_currency": right_currency,
        "gave_url": gave_url,
        "right_product": right_product,
        "in_budget": in_budget,
        "stock_honest": stock_honest,
    }
    # A question is "usable" when you could act on the answer without re-researching.
    core = [gave_price, gave_url]
    if right_currency is not None:
        core.append(right_currency)
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
        "currencies_seen": sorted({code for code, _ in money}),
        **checks,
    }


class Timer:
    def __enter__(self):
        self.t = time.perf_counter()
        return self

    def __exit__(self, *a):
        self.ms = int((time.perf_counter() - self.t) * 1000)
