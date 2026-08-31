"""Normalize E-commerce Scraping Tool output into a stable product shape.

This module exists because the Actor's field names, types, and nesting vary by
retailer. Reading them naively works on Amazon and then breaks on the next store,
so every read here is defensive and looks in several places. The variations below
were all observed in live runs, not guessed:

- ``offers.price`` is a number on Amazon (``248``) and a string on Walmart
  (``"398.99"``).
- the currency is ``offers.priceCurrency``, which is a symbol on Amazon (``"$"``)
  and an ISO code on Walmart (``"USD"``).
- the title is ``name`` or ``title``.
- ``brand`` is an object whose only key may be ``slogan``, and that slogan is a
  clean brand on Amazon (``"Sony"``) and a UI string on Walmart
  (``"Visit the Sony Store"``).
- stock, rating, and identifiers live under ``additionalProperties``, not at the
  top level, and that sub-object is itself per-retailer: Amazon returned 44 keys,
  Walmart returned 4.

So the promoted fields below are best-effort across retailers, and
``Product.extras`` carries the whole ``additionalProperties`` verbatim so nothing
is lost for the retailer you actually care about.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

ACTOR_ID = "apify~e-commerce-scraping-tool"
MCP_TOOL_NAME = "apify--e-commerce-scraping-tool"
APIFY_BASE = "https://api.apify.com/v2"
MCP_URL = "https://mcp.apify.com?tools=apify/e-commerce-scraping-tool"

_PRICE_CHARS = re.compile(r"[^0-9.]")
_BRAND_WRAPPER = re.compile(r"^visit\s+the\s+(.+?)\s+store$", re.IGNORECASE)
_IN_STOCK = ("instock", "in stock", "available")
_OUT_OF_STOCK = ("outofstock", "out of stock", "soldout", "sold out")


def load_dotenv(path: str | None = None) -> None:
    """Read a ``.env`` file next to this module into ``os.environ``.

    Hand-rolled rather than pulling in python-dotenv, so the dependency list stays
    short. Already-set variables win, so an explicit ``export`` or a CI secret
    always overrides the file.
    """
    env_path = Path(path) if path else Path(__file__).with_name(".env")
    try:
        raw = env_path.read_text(encoding="utf-8")
    except OSError:
        return
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'\"")
        if key and value and key not in os.environ:
            os.environ[key] = value


# Loaded at import so every entry point picks it up.
load_dotenv()


@dataclass
class Product:
    """One product, with the fields an agent or a vector store actually needs."""

    # Present on every retailer worth using.
    name: str = ""
    brand: str = ""
    description: str = ""
    price: float | None = None
    currency: str = ""
    url: str = ""
    retailer: str = ""
    images: list[str] = field(default_factory=list)
    fetched_at: str = ""

    # Best-effort: found on some retailers, absent on others. None or empty means
    # the retailer did not report it, never that the answer is no.
    in_stock: bool | None = None
    stock_text: str = ""
    availability: str = ""
    rating: float | None = None
    review_count: int | None = None
    list_price: float | None = None
    identifier: str = ""
    breadcrumbs: str = ""
    delivery: str = ""
    features: list[str] = field(default_factory=list)
    variants: list[str] = field(default_factory=list)

    # The whole `additionalProperties` object, verbatim. Large: a single Amazon
    # product carried 44 keys and about 100 KB. See `to_dict(include_extras=...)`.
    extras: dict[str, Any] = field(default_factory=dict)

    def to_dict(self, include_extras: bool = True) -> dict[str, Any]:
        data = asdict(self)
        if not include_extras:
            data.pop("extras", None)
        return data

    def format_price(self) -> str:
        """Render the price with its currency.

        The currency arrives as a symbol on some retailers and an ISO code on
        others: Amazon returned ``"$"`` and Walmart returned ``"USD"`` for the
        same dollar. A symbol butts against the number, a code takes a space, so
        "$248" and "USD 398.99" both read correctly.
        """
        if self.price is None:
            return ""
        amount = f"{self.price:g}"
        if not self.currency:
            return amount
        if self.currency.isalpha():
            return f"{self.currency} {amount}"
        return f"{self.currency}{amount}"

    def discount_percent(self) -> int | None:
        """How far below list price, when the retailer reported both."""
        if self.price is None or self.list_price is None or self.list_price <= 0:
            return None
        if self.price >= self.list_price:
            return None
        return round((1 - self.price / self.list_price) * 100)

    def to_text(self) -> str:
        """A compact document for embedding.

        Price and stock lead, because those are the fields that make a stale index
        wrong, and putting them first keeps them inside the window when a chunker
        truncates. `extras` is deliberately excluded: it is far too large, and an
        agent reading a retailer's raw internals is not the point.
        """
        bits: list[str] = []
        if self.name:
            bits.append(self.name)
        if self.brand:
            bits.append(f"Brand: {self.brand}")
        if self.price is not None:
            line = f"Price: {self.format_price()}"
            discount = self.discount_percent()
            if discount:
                bits.append(f"{line} (down {discount}% from {self.list_price:g})")
            else:
                bits.append(line)
        if self.in_stock is True:
            bits.append(f"In stock{': ' + self.stock_text if self.stock_text else ''}")
        elif self.in_stock is False:
            bits.append("Out of stock")
        elif self.availability:
            bits.append(f"Availability: {self.availability}")
        if self.rating is not None:
            count = f" from {self.review_count} reviews" if self.review_count else ""
            bits.append(f"Rated {self.rating}{count}")
        if self.delivery:
            bits.append(f"Delivery: {self.delivery}")
        if self.retailer:
            bits.append(f"Retailer: {self.retailer}")
        if self.breadcrumbs:
            bits.append(f"Category: {self.breadcrumbs}")
        if self.features:
            bits.append("Features: " + "; ".join(self.features[:6]))
        if self.description:
            bits.append(self.description)
        if self.fetched_at:
            bits.append(f"Data fetched: {self.fetched_at}")
        return "\n".join(bits)


# --- field readers ------------------------------------------------------------


def parse_price(raw: Any) -> float | None:
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


def _clean_brand(value: str) -> str:
    """Reduce a brand candidate to a name, or reject it.

    ``brand.slogan`` carries whatever text the retailer put in that slot: Amazon
    returned ``"Sony"``, Walmart returned ``"Visit the Sony Store"``. Unwrap that
    pattern, then reject anything still shaped like a phrase, because
    "Brand: Visit the Sony Store" in a document an agent cites reads as fact.
    """
    value = value.strip()
    if not value:
        return ""
    wrapped = _BRAND_WRAPPER.match(value)
    if wrapped:
        value = wrapped.group(1).strip()
    if len(value) > 40 or len(value.split()) > 4:
        return ""
    return value


#: Link text some retailers append to the product name in the DOM. eBay puts
#: "Opens in a new window or tab" on every one of its records, so the raw name is
#: unusable in a citation or a card. Same class of problem as ``brand.slogan``
#: carrying "Visit the Sony Store": page furniture arriving as data.
NAME_SUFFIXES = (
    "opens in a new window or tab",
    "opens in a new tab",
)


def read_name(item: dict[str, Any]) -> str:
    """The product name with scraped UI text stripped off the end."""
    raw = str(item.get("name") or item.get("title") or "").strip()
    low = raw.lower()
    for suffix in NAME_SUFFIXES:
        if low.endswith(suffix):
            raw = raw[: len(raw) - len(suffix)].strip()
            low = raw.lower()
    return raw.strip(" -|\u2013")


def read_brand(raw: Any) -> str:
    if isinstance(raw, str):
        return _clean_brand(raw)
    if isinstance(raw, dict):
        for key in ("name", "slogan"):
            value = raw.get(key)
            if isinstance(value, str):
                cleaned = _clean_brand(value)
                if cleaned:
                    return cleaned
    return ""


def _image_urls(raw: Any) -> Iterable[str]:
    """Yield URLs from a string, a list of strings, or a list of objects.

    Walmart nests images as ``additionalProperties.images`` holding
    ``[{"url": ...}]``, while Amazon uses plain string lists. Both appear.
    """
    if isinstance(raw, str):
        yield raw
    elif isinstance(raw, list):
        for entry in raw:
            if isinstance(entry, str):
                yield entry
            elif isinstance(entry, dict):
                url = entry.get("url") or entry.get("src")
                if isinstance(url, str):
                    yield url


def read_images(item: dict[str, Any], extras: dict[str, Any]) -> list[str]:
    """Collect image URLs, preferring the highest resolution the retailer offered."""
    ordered: list[str] = []
    sources = (
        extras.get("highResolutionImages"),
        item.get("image"),
        item.get("images"),
        extras.get("images"),
        extras.get("galleryThumbnails"),
    )
    for source in sources:
        ordered.extend(_image_urls(source))
    seen: set[str] = set()
    out: list[str] = []
    for url in ordered:
        if url.startswith("http") and url not in seen:
            seen.add(url)
            out.append(url)
    return out


def read_in_stock(item: dict[str, Any], extras: dict[str, Any]) -> bool | None:
    """Tri-state on purpose.

    None means the retailer did not say. Collapsing that to False would tell an
    agent a product is unavailable when nobody claimed it, which is the kind of
    confident wrong answer this repo exists to avoid. Amazon reports stock under
    ``additionalProperties.inStock``; Walmart did not report it at all.
    """
    for candidate in (item.get("inStock"), extras.get("inStock")):
        if isinstance(candidate, bool):
            return candidate
    offers = item.get("offers") if isinstance(item.get("offers"), dict) else {}
    text = str(
        offers.get("availability")
        or item.get("availability")
        or extras.get("inStockText")
        or ""
    ).lower()
    if not text:
        return None
    if any(token in text for token in _OUT_OF_STOCK):
        return False
    if any(token in text for token in _IN_STOCK):
        return True
    return None


def read_rating(item: dict[str, Any], extras: dict[str, Any]) -> float | None:
    """Prefer ``additionalProperties.stars`` over the top-level ``rating``.

    A live Amazon product reported ``rating: null`` alongside ``stars: 4.2``, and
    a Walmart result reported ``rating: 0``. Zero is not a rating anyone gave.
    """
    for candidate in (extras.get("stars"), item.get("rating")):
        if isinstance(candidate, bool):
            continue
        if isinstance(candidate, (int, float)) and candidate > 0:
            return float(candidate)
    return None


def read_int(raw: Any) -> int | None:
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw if raw > 0 else None
    if isinstance(raw, float):
        return int(raw) if raw > 0 else None
    if isinstance(raw, str):
        digits = re.sub(r"[^0-9]", "", raw)
        return int(digits) if digits else None
    return None


def read_str_list(raw: Any, limit: int = 20) -> list[str]:
    if not isinstance(raw, list):
        return []
    out = []
    for entry in raw:
        if isinstance(entry, str) and entry.strip():
            out.append(entry.strip())
        elif isinstance(entry, dict):
            label = entry.get("name") or entry.get("value") or entry.get("title")
            if isinstance(label, str) and label.strip():
                out.append(label.strip())
        if len(out) >= limit:
            break
    return out


def retailer_from_url(url: str) -> str:
    known = {
        "amazon.com": "Amazon",
        "walmart.com": "Walmart",
        "target.com": "Target",
        "ebay.com": "eBay",
        "bestbuy.com": "Best Buy",
        "homedepot.com": "Home Depot",
        "lowes.com": "Lowe's",
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


def normalize(item: dict[str, Any], fetched_at: str, fallback_url: str = "") -> Product:
    extras = item.get("additionalProperties")
    extras = extras if isinstance(extras, dict) else {}
    offers = item.get("offers") if isinstance(item.get("offers"), dict) else {}
    url = str(item.get("url") or fallback_url or "")

    list_price_raw = extras.get("listPrice")
    list_price = None
    if isinstance(list_price_raw, dict):
        list_price = parse_price(list_price_raw.get("value"))
    else:
        list_price = parse_price(list_price_raw)

    currency = (
        offers.get("priceCurrency")
        or offers.get("currency")
        or extras.get("currencyRaw")
        or ""
    )

    return Product(
        name=read_name(item),
        brand=read_brand(item.get("brand")),
        description=str(item.get("description") or "").strip(),
        price=parse_price(offers.get("price")),
        currency=str(currency),
        url=url,
        retailer=retailer_from_url(url),
        images=read_images(item, extras),
        fetched_at=fetched_at,
        in_stock=read_in_stock(item, extras),
        stock_text=str(extras.get("inStockText") or "").strip(),
        availability=str(offers.get("availability") or item.get("availability") or ""),
        rating=read_rating(item, extras),
        review_count=read_int(item.get("reviewCount")),
        list_price=list_price,
        identifier=str(
            item.get("gtin") or item.get("sku") or extras.get("sku") or extras.get("asin") or ""
        ),
        breadcrumbs=str(extras.get("breadCrumbs") or "").strip(),
        delivery=str(extras.get("delivery") or extras.get("fastestDelivery") or "").strip(),
        features=read_str_list(extras.get("features")),
        variants=read_str_list(extras.get("variantDetails") or extras.get("variantAttributes")),
        extras=extras,
    )


def is_usable(product: Product) -> bool:
    """A row with neither a name nor a price means the page was not recognized.

    A URL that does not resolve comes back as an item with every field empty
    rather than as an error, and feeding those into an index is how a vector store
    fills up with blanks an agent later cites as fact.
    """
    return bool(product.name) or product.price is not None


def normalize_all(
    items: Iterable[dict[str, Any]], fetched_at: str, fallback_url: str = ""
) -> list[Product]:
    products = (normalize(item, fetched_at, fallback_url) for item in items)
    return [p for p in products if is_usable(p)]


# --- Apify REST ---------------------------------------------------------------


def api_token() -> str:
    token = os.environ.get("APIFY_TOKEN") or os.environ.get("APIFY_API_KEY")
    if not token:
        raise SystemExit(
            "Set APIFY_TOKEN. Copy .env.example to .env and fill it in, or export it."
        )
    return token


def run_actor_sync(
    payload: dict[str, Any],
    max_items: int | None = None,
    timeout_secs: int = 300,
) -> list[dict[str, Any]]:
    """Run the Actor over the REST API and return dataset items.

    Used by ``rag_refresh.py``. The MCP path lives in ``mcp_client.py``: see the
    README for why the batch job does not go through MCP.

    ``max_items`` is a hard cap the platform enforces, which matters because this
    Actor bills per product pushed: leave it set unless you mean to spend.
    """
    import httpx  # imported here so `--help` works without the dependency

    params: dict[str, str] = {"timeout": str(timeout_secs)}
    if max_items is not None:
        params["maxItems"] = str(max_items)

    try:
        with httpx.Client(timeout=timeout_secs + 30) as client:
            response = client.post(
                f"{APIFY_BASE}/acts/{ACTOR_ID}/run-sync-get-dataset-items",
                params=params,
                headers={"Authorization": f"Bearer {api_token()}"},
                json=payload,
            )
    except httpx.HTTPError as exc:
        # Transport failures (DNS, refused, read timeout) are raised as the same
        # RuntimeError the API-error branch below uses, because callers already
        # handle that and a scheduled job should print a line, not a stack trace.
        host = APIFY_BASE.split("/")[2] if "//" in APIFY_BASE else APIFY_BASE
        raise RuntimeError(
            f"Could not reach {host}: {type(exc).__name__}: {exc}"
        ) from exc
    if response.status_code >= 400:
        raise RuntimeError(
            f"Actor API error ({response.status_code}): {response.text[:300]}"
        )
    items = response.json()
    return items if isinstance(items, list) else []
