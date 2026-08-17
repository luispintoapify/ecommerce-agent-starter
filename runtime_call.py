"""The runtime path: fetch live product data at the moment a question is asked.

Use this when the answer has to be true right now, for a handful of products.
For a catalog, use rag_refresh.py instead: this Actor bills per product pushed
plus a start event per call, so many small calls cost more than one batched call.

Examples
--------
    python runtime_call.py --url https://www.amazon.com/dp/B09XS7JWHH
    python runtime_call.py --keyword "noise cancelling headphones" \
        --marketplaces www.amazon.com www.walmart.com --limit 10
    python runtime_call.py --url https://www.amazon.com/dp/B09XS7JWHH --json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List

from apify_products import Product, normalize_all, run_actor_sync


def build_payload(args: argparse.Namespace) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "maxProductResults": args.limit,
        "additionalProperties": True,
        "scrapeMode": args.mode,
    }
    if args.url:
        # detailsUrls is the schema's field for individual product detail pages.
        # If a retailer returns nothing here, try --listing, which routes the same
        # URLs through listingUrls instead. See the README note.
        key = "listingUrls" if args.listing else "detailsUrls"
        payload[key] = [{"url": u} for u in args.url]
    if args.keyword:
        payload["keyword"] = args.keyword
        payload["marketplaces"] = args.marketplaces
    return payload


def render(products: List[Product], elapsed: float) -> None:
    if not products:
        print(
            "No product data came back. The URL may not be a product page, or the "
            "retailer may not be supported. Try --listing, or check the Actor's "
            "supported marketplaces."
        )
        return

    print(f"{len(products)} product(s) in {elapsed:.1f}s\n")
    for product in products:
        money = "price not returned"
        if product.price is not None:
            money = f"{product.currency} {product.price}".strip()
        if product.in_stock is True:
            stock = "in stock"
        elif product.in_stock is False:
            stock = "out of stock"
        else:
            stock = "stock unknown"
        print(f"  {product.name or 'name not returned'}")
        print(f"    {money}  |  {stock}  |  {product.retailer or 'unknown retailer'}")
        if product.images:
            print(f"    {len(product.images)} image URL(s), first: {product.images[0]}")
        if product.url:
            print(f"    {product.url}")
        print()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
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
        type=int,
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
    parser.add_argument("--json", action="store_true", help="Print normalized JSON instead of text")
    args = parser.parse_args()

    if not args.url and not args.keyword:
        parser.error("give --url and/or --keyword")

    payload = build_payload(args)
    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    started = time.monotonic()
    try:
        items = run_actor_sync(payload, max_items=args.limit)
    except RuntimeError as err:
        print(str(err), file=sys.stderr)
        return 1
    elapsed = time.monotonic() - started

    products = normalize_all(items, fetched_at, args.url[0] if args.url else "")

    if args.json:
        print(json.dumps([p.to_dict() for p in products], indent=2))
    else:
        render(products, elapsed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
