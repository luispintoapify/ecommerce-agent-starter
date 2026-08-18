"""Arm C: write the scraper yourself with Playwright.

The honest expectation is that this arm gets blocked on some retailers, and that is
the finding rather than a broken test. ``blocked`` is a recorded outcome with its own
column in the results, not a crash: an arm that cannot load the page has told you
something real about the maintenance cost of the DIY path.

No proxies and no anti-bot handling, because adding them is precisely the work this
arm exists to measure the absence of. If you want to benchmark a hardened DIY
scraper, add your proxy configuration here and say so when you publish the numbers.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harness import Result, Timer  # noqa: E402

ARM = "diy_playwright"
TIMEOUT_MS = 30_000

#: Per-retailer selectors, in preference order. This list is the maintenance burden
#: the arm is measuring: every one of these breaks when a retailer redesigns.
PRICE_SELECTORS = {
    "amazon.com": [
        "span.a-price span.a-offscreen",
        "#corePrice_feature_div .a-offscreen",
        "#priceblock_ourprice",
    ],
    "ebay.com": [
        "div.x-price-primary span.ux-textspans",
        "span[itemprop='price']",
        "#prcIsum",
    ],
}
STOCK_SELECTORS = {
    "amazon.com": ["#availability span", "#availability"],
    "ebay.com": ["div.x-quantity__availability span", "#qtySubTxt"],
}

#: Signals that the page served an interstitial rather than the product.
BLOCK_MARKERS = (
    "enter the characters you see below",
    "are you a human",
    "access denied",
    "unusual traffic",
    "pardon our interruption",
    "verify you are a human",
    "captcha",
)


def host_of(url: str) -> str:
    m = re.search(r"https?://(?:www\.)?([^/]+)", url or "")
    return m.group(1).lower() if m else ""


def run_one(q: dict[str, Any]) -> Result:
    r = Result(arm=ARM, qid=q["id"])

    if not q.get("url"):
        # Keyword and comparison questions need a per-retailer search-results parser
        # on top of a per-retailer product parser. Recording that plainly is more
        # honest than writing a fragile one and reporting its failures as accuracy.
        r.error = "not implemented: this arm handles direct product URLs only"
        return r

    host = host_of(q["url"])
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        r.error = "playwright is not installed (pip install -r requirements-bench.txt)"
        return r

    try:
        with Timer() as t:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True)
                page = browser.new_page(
                    user_agent=(
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
                    )
                )
                try:
                    page.goto(q["url"], timeout=TIMEOUT_MS, wait_until="domcontentloaded")
                    body = (page.content() or "").lower()
                    if any(m in body for m in BLOCK_MARKERS):
                        r.blocked = True
                        r.answer = ""
                        r.raw = {"host": host, "reason": "interstitial served"}
                        return r

                    price = _first_text(page, PRICE_SELECTORS.get(host, []))
                    stock = _first_text(page, STOCK_SELECTORS.get(host, []))
                    title = (page.title() or "").strip()

                    if not price:
                        r.answer = f"Loaded the page but found no price with the selectors for {host}."
                        r.raw = {"host": host, "title": title, "selector_miss": True}
                    else:
                        parts = [title or "(no title)", price]
                        parts.append(stock.strip() if stock else "stock not found on the page")
                        r.answer = " | ".join(parts) + f"  {q['url']}"
                        r.raw = {"host": host}
                finally:
                    browser.close()
        r.latency_ms = t.ms
    except Exception as exc:  # noqa: BLE001
        msg = f"{type(exc).__name__}: {exc}"
        if "Timeout" in msg or "net::ERR" in msg:
            r.blocked = True
        r.error = msg[:300]
    return r


def _first_text(page: Any, selectors: list[str]) -> str:
    for sel in selectors:
        try:
            el = page.query_selector(sel)
            if el:
                txt = (el.inner_text() or "").strip()
                if txt:
                    return txt
        except Exception:  # noqa: BLE001 - a bad selector is not a run failure
            continue
    return ""


def run_all(questions: list[dict[str, Any]]) -> list[Result]:
    return [run_one(q) for q in questions]


if __name__ == "__main__":
    from harness import load_questions

    for res in run_all(load_questions()):
        print(res.to_json())
