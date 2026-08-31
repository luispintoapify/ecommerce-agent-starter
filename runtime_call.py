"""The runtime path: fetch live product data over MCP, the way an agent does.

This goes through the Apify MCP server, so what you see here is what your agent
sees. It takes two tool calls under the hood, sometimes three; ``mcp_client.py``
explains why.

Use this when the answer has to be true right now, for a handful of products. For
a catalog, use ``rag_refresh.py``: the Actor bills a start event per call plus per
product, so many small calls cost more than one batched call.

Examples
--------
    python runtime_call.py --url https://www.amazon.com/dp/B09XS7JWHH
    python runtime_call.py --keyword "noise cancelling headphones" \
        --marketplaces www.amazon.com www.walmart.com --limit 10
    python runtime_call.py --url https://www.amazon.com/dp/B09XS7JWHH --json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from typing import Any

from apify_products import Product, positive_int


def build_input(args: argparse.Namespace) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "maxProductResults": args.limit,
        "additionalProperties": True,
        "scrapeMode": args.mode,
    }
    if args.url:
        # detailsUrls is the schema's field for individual product detail pages,
        # and it is confirmed working. --listing routes the same URLs through
        # listingUrls if you want to rule the input field out.
        key = "listingUrls" if args.listing else "detailsUrls"
        payload[key] = [{"url": u} for u in args.url]
    if args.keyword:
        payload["keyword"] = args.keyword
        payload["marketplaces"] = args.marketplaces
    return payload


def render(products: list[Product], elapsed: float, summary: str) -> None:
    count = f"{len(products)} product(s)"
    print(f"\n{summary}   {count}   wall clock {elapsed:.1f}s\n")
    if not products:
        print(
            "No usable product data came back. A URL that does not resolve returns\n"
            "an item with every field empty rather than an error, so check the URL\n"
            "first, then try --listing."
        )
        return

    for product in products:
        print(f"  {product.name or 'name not returned'}")

        money = product.format_price() or "price not returned"
        discount = product.discount_percent()
        if discount:
            money += f" (down {discount}% from {product.list_price:g})"
        if product.in_stock is True:
            stock = product.stock_text or "in stock"
        elif product.in_stock is False:
            stock = "out of stock"
        else:
            stock = "stock not reported"
        print(f"    {money}  |  {stock}")

        line = []
        if product.brand:
            line.append(product.brand)
        if product.rating is not None:
            count = f" ({product.review_count})" if product.review_count else ""
            line.append(f"{product.rating}/5{count}")
        if product.retailer:
            line.append(product.retailer)
        if line:
            print(f"    {'  |  '.join(line)}")

        if product.delivery:
            print(f"    delivery: {product.delivery}")
        if product.images:
            print(f"    {len(product.images)} image URL(s)")
        if product.url:
            print(f"    {product.url}")
        print()


def _clean_error(exc: BaseException) -> str:
    """One readable line for an unexpected failure at the CLI boundary.

    Transport errors raised inside the MCP client's task group arrive wrapped in an
    ExceptionGroup whose own message is "unhandled errors in a TaskGroup", so the
    real cause is unwrapped before printing. Kept off ExceptionGroup syntax so the
    module still imports on Python 3.10.
    """
    inner = getattr(exc, "exceptions", None)
    if inner:
        exc = inner[0]
    name = type(exc).__name__
    if name in {"ConnectError", "ConnectTimeout", "ReadTimeout", "ReadError", "gaierror"}:
        return f"Could not reach the Apify MCP server: {name}: {exc}"
    return f"{name}: {exc}"


async def run(args: argparse.Namespace) -> int:
    from mcp_client import McpError, fetch_products, run_summary

    started = time.monotonic()
    try:
        products, meta = await fetch_products(build_input(args), limit=args.limit)
    except McpError as err:
        print(str(err), file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - the CLI boundary; a traceback helps nobody
        print(_clean_error(exc), file=sys.stderr)
        return 1
    elapsed = time.monotonic() - started

    if args.json:
        print(json.dumps([p.to_dict(include_extras=args.extras) for p in products], indent=2))
    else:
        render(products, elapsed, run_summary(meta))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--url", nargs="*", default=[], help="One or more product detail URLs")
    parser.add_argument("--keyword", help="Search term, used with --marketplaces")
    parser.add_argument(
        "--marketplaces",
        nargs="*",
        default=["www.amazon.com"],
        help="Storefronts to search when using --keyword",
    )
    parser.add_argument(
        "--limit",
        type=positive_int,
        default=5,
        help="Hard cap on products collected. This is the cost control: the Actor "
        "bills per product pushed.",
    )
    parser.add_argument(
        "--mode",
        default="AUTO",
        choices=["AUTO", "HTTP", "BROWSER"],
        help="HTTP is cheaper and faster but fails where prices render in the browser",
    )
    parser.add_argument(
        "--listing",
        action="store_true",
        help="Send --url through listingUrls instead of detailsUrls",
    )
    parser.add_argument("--json", action="store_true", help="Print normalized JSON")
    parser.add_argument(
        "--extras",
        action="store_true",
        help="With --json, include the retailer's full additionalProperties. Large: "
        "one Amazon product carried 44 keys and about 100 KB.",
    )
    args = parser.parse_args()

    if not args.url and not args.keyword:
        parser.error("give --url and/or --keyword")

    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
