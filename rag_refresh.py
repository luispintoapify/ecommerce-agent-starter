"""The scheduled path: keep an agent's product index current.

Runtime calls are right for a handful of products. For a catalog, batch them on a
schedule instead: this Actor bills a start event per call plus per product pushed,
so one call for 200 products costs far less than 200 calls for one.

Every document carries ``fetched_at``. Put it in the agent's prompt so it can say
how fresh the number is instead of implying the price is live when it is not.

The upsert step is deliberately pluggable. ``--sink jsonl`` needs nothing and is
the default, so you can see the documents before wiring a vector store.

Examples
--------
    python rag_refresh.py --catalog catalog.example.json --sink jsonl
    python rag_refresh.py --catalog catalog.example.json --sink pinecone
    python rag_refresh.py --keyword "4k tv" --marketplaces www.amazon.com --limit 50
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Sequence

from apify_products import Product, normalize_all, run_actor_sync

BATCH_SIZE = 50


def chunked(seq: Sequence[Any], size: int) -> Iterable[Sequence[Any]]:
    for i in range(0, len(seq), size):
        yield seq[i : i + size]


def doc_id(product: Product) -> str:
    """Stable id so a refresh overwrites rather than duplicating.

    Keyed on the canonical URL when there is one, since that is what identifies a
    listing across refreshes. Falls back to retailer plus name.
    """
    basis = product.url or f"{product.retailer}:{product.name}"
    return hashlib.sha1(basis.encode("utf-8")).hexdigest()


def fetch_urls(urls: List[str], limit: int, mode: str, listing: bool) -> List[Product]:
    """One Actor call for the whole batch, not one per URL."""
    key = "listingUrls" if listing else "detailsUrls"
    payload = {
        key: [{"url": u} for u in urls],
        "maxProductResults": limit,
        "additionalProperties": True,
        "scrapeMode": mode,
    }
    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    items = run_actor_sync(payload, max_items=limit)
    return normalize_all(items, fetched_at)


def fetch_keyword(keyword: str, marketplaces: List[str], limit: int, mode: str) -> List[Product]:
    payload = {
        "keyword": keyword,
        "marketplaces": marketplaces,
        "maxProductResults": limit,
        "additionalProperties": True,
        "scrapeMode": mode,
    }
    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    items = run_actor_sync(payload, max_items=limit)
    return normalize_all(items, fetched_at)


# --- sinks -------------------------------------------------------------------


def sink_jsonl(products: List[Product], path: str) -> None:
    """Write documents to a file. No dependencies, and it shows you the shape."""
    with open(path, "w", encoding="utf-8") as handle:
        for product in products:
            record = {
                "id": doc_id(product),
                "text": product.to_text(),
                "metadata": product.to_dict(),
            }
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(f"wrote {len(products)} documents to {path}")


def sink_pinecone(products: List[Product]) -> None:
    """Upsert into Pinecone.

    Kept to one function so swapping in Qdrant, Weaviate, or pgvector means
    writing one more of these, not rewriting the pipeline. Embeddings come from
    OpenAI here only because it is the shortest example; any model works as long
    as the dimension matches the index.
    """
    try:
        from openai import OpenAI
        from pinecone import Pinecone
    except ImportError:
        raise SystemExit(
            "The pinecone sink needs the optional extras:\n"
            "  pip install -r requirements-pinecone.txt"
        )

    index_name = os.environ.get("PINECONE_INDEX")
    if not index_name:
        raise SystemExit("Set PINECONE_INDEX (and PINECONE_API_KEY, OPENAI_API_KEY).")

    embed_model = os.environ.get("EMBED_MODEL", "text-embedding-3-small")
    openai = OpenAI()
    index = Pinecone(api_key=os.environ["PINECONE_API_KEY"]).Index(index_name)

    total = 0
    for batch in chunked(products, BATCH_SIZE):
        texts = [p.to_text() for p in batch]
        embeddings = openai.embeddings.create(model=embed_model, input=texts)
        vectors = []
        for product, entry in zip(batch, embeddings.data):
            metadata = product.to_dict()
            # Pinecone metadata rejects None; drop rather than coerce, because
            # False and "unknown" are different answers about stock.
            metadata = {k: v for k, v in metadata.items() if v is not None}
            metadata["text"] = product.to_text()
            vectors.append(
                {"id": doc_id(product), "values": entry.embedding, "metadata": metadata}
            )
        index.upsert(vectors=vectors)
        total += len(vectors)
        print(f"  upserted {total}/{len(products)}")
    print(f"upserted {total} vectors into {index_name}")


# --- main --------------------------------------------------------------------


def load_catalog(path: str) -> List[str]:
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    if isinstance(data, dict):
        data = data.get("urls", [])
    if not isinstance(data, list):
        raise SystemExit(f"{path} should be a JSON list of URLs, or an object with a 'urls' list.")
    return [str(u) for u in data if str(u).startswith("http")]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    source = parser.add_argument_group("what to refresh")
    source.add_argument("--catalog", help="JSON file holding a list of product URLs")
    source.add_argument("--keyword", help="Refresh by search term instead of a URL list")
    source.add_argument("--marketplaces", nargs="*", default=["www.amazon.com"])

    parser.add_argument(
        "--limit",
        type=int,
        default=100,
        help="Hard cap on products collected per run. This is the cost control.",
    )
    parser.add_argument("--mode", default="AUTO", choices=["AUTO", "HTTP", "BROWSER"])
    parser.add_argument("--listing", action="store_true", help="Route URLs through listingUrls")
    parser.add_argument("--sink", default="jsonl", choices=["jsonl", "pinecone"])
    parser.add_argument("--out", default="products.jsonl", help="Path for the jsonl sink")
    parser.add_argument("--batch", type=int, default=25, help="URLs per Actor call")
    args = parser.parse_args()

    if not args.catalog and not args.keyword:
        parser.error("give --catalog or --keyword")

    started = time.monotonic()
    products: List[Product] = []

    try:
        if args.catalog:
            urls = load_catalog(args.catalog)
            if not urls:
                print("No URLs in the catalog file.", file=sys.stderr)
                return 1
            print(f"{len(urls)} URLs, {args.batch} per call")
            remaining = args.limit
            for group in chunked(urls, args.batch):
                if remaining <= 0:
                    print("hit --limit, stopping")
                    break
                batch = fetch_urls(list(group), min(len(group), remaining), args.mode, args.listing)
                products.extend(batch)
                remaining -= len(batch)
                print(f"  {len(products)} product(s) so far")
        else:
            products = fetch_keyword(args.keyword, args.marketplaces, args.limit, args.mode)
    except RuntimeError as err:
        print(str(err), file=sys.stderr)
        return 1

    elapsed = time.monotonic() - started
    if not products:
        print("Nothing usable came back. Try --listing, or check the retailers are supported.")
        return 1

    rate = len(products) / elapsed if elapsed else 0
    print(f"\n{len(products)} product(s) in {elapsed:.1f}s ({rate:.1f}/s)")

    missing_price = sum(1 for p in products if p.price is None)
    unknown_stock = sum(1 for p in products if p.in_stock is None)
    if missing_price:
        print(f"note: {missing_price} product(s) came back without a price")
    if unknown_stock:
        print(f"note: {unknown_stock} product(s) did not report stock")

    if args.sink == "jsonl":
        sink_jsonl(products, args.out)
    else:
        sink_pinecone(products)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
