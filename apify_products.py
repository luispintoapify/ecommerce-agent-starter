"""Normalize E-commerce Scraping Tool output into a stable product shape.

This module exists because the Actor's field names and types vary by retailer.
Reading them naively works on Amazon and then breaks on the next store, so every
read here is defensive. The variations below were observed in production code,
not guessed:

- ``offers`` is an object, and ``offers.price`` arrives as a number on some
  retailers and a string like ``"$328.00"`` on others.
- the currency is ``offers.priceCurrency`` on some, ``offers.currency`` on others.
- the title is ``name`` or ``title``.
- ``brand`` is an object with ``name``, or a bare string.
- images arrive as ``image`` (string or list) and/or ``images`` (list).
- stock is ``inStock`` (bool) or ``offers.availability`` / ``availability``
  (schema.org-ish strings like ``"InStock"``).

Keep the normalization in one place so the runtime path and the RAG refresh
cannot drift apart.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, asdict, field
from typing import Any, Dict, Iterable, List, Optional

ACTOR_ID = "apify~e-commerce-scraping-tool"
APIFY_BASE = "https://api.apify.com/v2"

_PRICE_CHARS = re.compile(r"[^0-9.]")
_IN_STOCK = ("instock", "in stock", "available")
_OUT_OF_STOCK = ("outofstock", "out of stock", "soldout", "sold out")


@dataclass
class Product:
    """One product, with the fields an agent or a vector store actually needs."""

    name: str = ""
    brand: str = ""
    description: str = ""
    price: Optional[float] = None
    currency: str = ""
    availability: str = ""
    in_stock: Optional[bool] = None
    images: List[str] = field(default_factory=list)
    url: str = ""
    retailer: str = ""
    identifier: str = ""
    fetched_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_text(self) -> str:
        """A compact document for embedding.

        Price and availability lead, because those are the fields that make a
        stale index wrong, and putting them first keeps them inside the window
        when a chunker truncates.
        """
        bits: List[str] = []
        if self.name:
            bits.append(self.name)
        if self.brand:
            bits.append(f"Brand: {self.brand}")
        if self.price is not None:
            money = f"{self.currency} {self.price}".strip()
            bits.append(f"Price: {money}")
        if self.in_stock is not None:
            bits.append("In stock" if self.in_stock else "Out of stock")
        elif self.availability:
            bits.append(f"Availability: {self.availability}")
        if self.retailer:
            bits.append(f"Retailer: {self.retailer}")
        if self.description:
            bits.append(self.description)
        if self.fetched_at:
            bits.append(f"Data fetched: {self.fetched_at}")
        return "\n".join(bits)


def parse_price(raw: Any) -> Optional[float]:
    """Return a positive float, or None. Handles ``328``, ``"328.00"``, ``"$328"``."""
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return float(raw) if raw > 0 else None
    if isinstance(raw, str):
        cleaned = _PRICE_CHARS.sub("", raw)
        # A stray thousands separator can leave "1.234.00"; keep the last dot.
        if cleaned.count(".") > 1:
            head, _, tail = cleaned.rpartition(".")
            cleaned = head.replace(".", "") + "." + tail
        try:
            value = float(cleaned)
        except ValueError:
            return None
        return value if value > 0 else None
    return None


def read_brand(raw: Any) -> str:
    if isinstance(raw, str):
        return raw.strip()
    if isinstance(raw, dict):
        name = raw.get("name")
        if isinstance(name, str):
            return name.strip()
    return ""


def read_images(item: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    for key in ("image", "images"):
        raw = item.get(key)
        if isinstance(raw, str):
            out.append(raw)
        elif isinstance(raw, list):
            out.extend(u for u in raw if isinstance(u, str))
    seen = set()
    deduped = []
    for url in out:
        if url.startswith("http") and url not in seen:
            seen.add(url)
            deduped.append(url)
    return deduped


def read_in_stock(item: Dict[str, Any]) -> Optional[bool]:
    """Tri-state on purpose.

    None means the retailer did not say. Collapsing that to False would tell an
    agent a product is unavailable when nobody claimed that, which is the kind of
    confident wrong answer this repo exists to avoid.
    """
    direct = item.get("inStock")
    if isinstance(direct, bool):
        return direct
    offers = item.get("offers")
    availability = ""
    if isinstance(offers, dict):
        availability = str(offers.get("availability") or "")
    if not availability:
        availability = str(item.get("availability") or "")
    low = availability.lower()
    if not low:
        return None
    if any(token in low for token in _OUT_OF_STOCK):
        return False
    if any(token in low for token in _IN_STOCK):
        return True
    return None


def retailer_from_url(url: str) -> str:
    known = {
        "amazon.com": "Amazon",
        "walmart.com": "Walmart",
        "target.com": "Target",
        "ebay.com": "eBay",
        "bestbuy.com": "Best Buy",
        "homedepot.com": "Home Depot",
        "ikea.com": "IKEA",
        "tesco.com": "Tesco",
        "wayfair.com": "Wayfair",
    }
    match = re.match(r"https?://(?:www\.)?([^/]+)", url or "")
    if not match:
        return ""
    host = match.group(1).lower()
    if host in known:
        return known[host]
    base = host.split(".")[0]
    return base[:1].upper() + base[1:]


def normalize(item: Dict[str, Any], fetched_at: str, fallback_url: str = "") -> Product:
    offers = item.get("offers") if isinstance(item.get("offers"), dict) else {}
    url = item.get("url") or fallback_url or ""
    currency = offers.get("priceCurrency") or offers.get("currency") or ""
    availability = str(offers.get("availability") or item.get("availability") or "")
    return Product(
        name=str(item.get("name") or item.get("title") or "").strip(),
        brand=read_brand(item.get("brand")),
        description=str(item.get("description") or "").strip(),
        price=parse_price(offers.get("price")),
        currency=str(currency),
        availability=availability,
        in_stock=read_in_stock(item),
        images=read_images(item),
        url=str(url),
        retailer=retailer_from_url(str(url)),
        identifier=str(item.get("gtin") or item.get("sku") or ""),
        fetched_at=fetched_at,
    )


def is_usable(product: Product) -> bool:
    """A row with neither a name nor a price means the page was not recognized.

    Feeding those into an index is how a RAG store fills up with blanks that an
    agent later cites as fact.
    """
    return bool(product.name) or product.price is not None


def api_token() -> str:
    token = os.environ.get("APIFY_TOKEN") or os.environ.get("APIFY_API_KEY")
    if not token:
        raise SystemExit(
            "Set APIFY_TOKEN. Copy .env.example to .env and fill it in, or export it."
        )
    return token


def run_actor_sync(
    payload: Dict[str, Any],
    max_items: Optional[int] = None,
    timeout_secs: int = 300,
) -> List[Dict[str, Any]]:
    """Run the Actor and return dataset items.

    Uses run-sync-get-dataset-items so there is nothing to poll. ``max_items`` is
    a hard cap the platform enforces, which matters because this Actor bills per
    product pushed: leave it set unless you mean to spend.
    """
    import httpx  # imported here so `--help` works without the dependency

    params = {"timeout": str(timeout_secs)}
    if max_items is not None:
        params["maxItems"] = str(max_items)

    with httpx.Client(timeout=timeout_secs + 30) as client:
        response = client.post(
            f"{APIFY_BASE}/acts/{ACTOR_ID}/run-sync-get-dataset-items",
            params=params,
            headers={"Authorization": f"Bearer {api_token()}"},
            json=payload,
        )
    if response.status_code >= 400:
        raise RuntimeError(
            f"Actor API error ({response.status_code}): {response.text[:300]}"
        )
    items = response.json()
    return items if isinstance(items, list) else []


def normalize_all(
    items: Iterable[Dict[str, Any]], fetched_at: str, fallback_url: str = ""
) -> List[Product]:
    products = (normalize(item, fetched_at, fallback_url) for item in items)
    return [p for p in products if is_usable(p)]
