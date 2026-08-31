"""Tests for the normalization layer.

The fixtures in ``tests/fixtures/`` are real Actor output, captured from live runs
against Amazon and Walmart. That matters: every quirk asserted here was found by
running the Actor, not by imagining what it might return. If a retailer changes
shape, recapture the fixture rather than loosening the assertion.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from apify_products import (
    Product,
    is_usable,
    normalize,
    normalize_all,
    parse_price,
    positive_int,
    read_brand,
    read_images,
    read_in_stock,
    read_rating,
    retailer_from_url,
)

FIXTURES = Path(__file__).parent / "fixtures"
FETCHED = "2026-08-17T08:17:07+00:00"


def load(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def amazon() -> dict:
    """One product from Amazon, fetched by URL over the REST API."""
    return load("amazon_rest.json")


@pytest.fixture(scope="module")
def amazon_mcp() -> dict:
    """The same product fetched over MCP, to prove both paths normalize alike."""
    return load("amazon_mcp.json")


@pytest.fixture(scope="module")
def walmart() -> dict:
    """First result of a Walmart keyword search. Far thinner than Amazon."""
    return load("walmart_keyword.json")[0]


# --- price --------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        (248, 248.0),          # Amazon returned an int
        ("398.99", 398.99),    # Walmart returned a string
        ("$328", 328.0),
        ("USD 1,234.56", 1234.56),
        ("1.234.00", 1234.0),  # stray thousands separator
        (328.5, 328.5),
        ("", None),
        (None, None),
        (0, None),
        ("free", None),
        (True, None),          # bool is an int in Python; must not become 1.0
    ],
)
def test_parse_price(raw, expected):
    assert parse_price(raw) == expected


def test_price_type_differs_between_retailers(amazon, walmart):
    """The reason parse_price exists at all."""
    assert isinstance(amazon["offers"]["price"], int)
    assert isinstance(walmart["offers"]["price"], str)
    assert normalize(amazon, FETCHED).price == 248.0
    assert normalize(walmart, FETCHED).price == 398.99


# --- currency -----------------------------------------------------------------


def test_currency_is_symbol_on_amazon_and_code_on_walmart(amazon, walmart):
    assert normalize(amazon, FETCHED).currency == "$"
    assert normalize(walmart, FETCHED).currency == "USD"


@pytest.mark.parametrize(
    "currency, price, expected",
    [
        ("$", 248.0, "$248"),
        ("USD", 398.99, "USD 398.99"),
        ("EUR", 41.25, "EUR 41.25"),
        ("", 10.0, "10"),
        ("$", None, ""),
    ],
)
def test_format_price(currency, price, expected):
    assert Product(currency=currency, price=price).format_price() == expected


# --- brand --------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        ({"slogan": "Sony"}, "Sony"),                       # Amazon, real
        ({"slogan": "Visit the Sony Store"}, "Sony"),        # Walmart, real
        ({"slogan": "visit the BOSE store"}, "BOSE"),
        ({"name": "Bose", "slogan": "Visit the Bose Store"}, "Bose"),
        ({"slogan": "The best headphones you will ever own"}, ""),
        ({"slogan": None}, ""),
        ("Sony", "Sony"),
        ({}, ""),
        (None, ""),
    ],
)
def test_read_brand(raw, expected):
    assert read_brand(raw) == expected


def test_brand_survives_both_retailers(amazon, walmart):
    """Reading only `name` used to drop the brand on Amazon entirely."""
    assert normalize(amazon, FETCHED).brand == "Sony"
    assert normalize(walmart, FETCHED).brand == "Sony"


# --- stock --------------------------------------------------------------------


@pytest.mark.parametrize(
    "item, expected",
    [
        ({"inStock": True}, True),
        ({"inStock": False}, False),
        ({"additionalProperties": {"inStock": True}}, True),
        ({"offers": {"availability": "InStock"}}, True),
        ({"offers": {"availability": "https://schema.org/OutOfStock"}}, False),
        ({"availability": "Available"}, True),
        ({"availability": "Sold Out"}, False),
        ({"availability": "backordered"}, None),
        ({}, None),
    ],
)
def test_read_in_stock(item, expected):
    extras = item.get("additionalProperties") or {}
    assert read_in_stock(item, extras) is expected


def test_stock_is_nested_and_per_retailer(amazon, walmart):
    """Amazon reports stock under additionalProperties. Walmart did not report it.

    None has to stay None: telling an agent a product is unavailable when nobody
    said so is the failure this repo exists to avoid.
    """
    assert amazon["additionalProperties"]["inStock"] is True
    assert normalize(amazon, FETCHED).in_stock is True
    assert normalize(amazon, FETCHED).stock_text.startswith("In Stock")

    assert "inStock" not in (walmart.get("additionalProperties") or {})
    assert normalize(walmart, FETCHED).in_stock is None


# --- rating -------------------------------------------------------------------


def test_rating_prefers_stars_over_null_top_level(amazon):
    """Amazon reported rating: null next to stars: 4.2."""
    assert amazon.get("rating") is None
    assert amazon["additionalProperties"]["stars"] == 4.2
    product = normalize(amazon, FETCHED)
    assert product.rating == 4.2
    assert product.review_count == 20002


def test_rating_zero_is_not_a_rating():
    assert read_rating({"rating": 0}, {}) is None
    assert read_rating({}, {"stars": 0}) is None


# --- images -------------------------------------------------------------------


def test_images_read_from_both_shapes(amazon, walmart):
    """Amazon uses string lists; Walmart nests [{"url": ...}] under extras."""
    amazon_images = normalize(amazon, FETCHED).images
    assert amazon_images and all(u.startswith("http") for u in amazon_images)

    walmart_extras = walmart.get("additionalProperties") or {}
    assert isinstance(walmart_extras.get("images"), list)
    assert isinstance(walmart_extras["images"][0], dict)
    assert normalize(walmart, FETCHED).images


def test_images_dedupe_and_reject_non_urls():
    item = {"image": "https://a/1.jpg", "images": ["https://a/1.jpg", "nope"]}
    assert read_images(item, {}) == ["https://a/1.jpg"]


# --- list price and discount ---------------------------------------------------


def test_list_price_and_discount(amazon):
    product = normalize(amazon, FETCHED)
    assert product.list_price == 399.99
    assert product.discount_percent() == 38


def test_discount_is_none_when_not_cheaper():
    assert Product(price=10.0, list_price=10.0).discount_percent() is None
    assert Product(price=12.0, list_price=10.0).discount_percent() is None
    assert Product(price=10.0).discount_percent() is None


# --- identifiers and extras ----------------------------------------------------


def test_identifier_falls_back_through_sku_and_asin(amazon, walmart):
    assert normalize(amazon, FETCHED).identifier == "B09XS7JWHH"
    assert normalize(walmart, FETCHED).identifier == "20376773306"


def test_extras_carries_everything_and_can_be_dropped(amazon):
    product = normalize(amazon, FETCHED)
    assert len(product.extras) == 44
    assert "extras" in product.to_dict()
    assert "extras" not in product.to_dict(include_extras=False)


def test_to_text_excludes_extras(amazon):
    """extras runs to about 100 KB; it must never reach the embedded document."""
    text = normalize(amazon, FETCHED).to_text()
    assert "aPlusContent" not in text
    assert len(text) < 8000


# --- usability guard -----------------------------------------------------------


def test_unresolvable_url_yields_nothing_usable():
    """A URL that does not resolve comes back as an item with every field empty."""
    empty = {"brand": {"slogan": None}, "offers": {}, "inputUrl": "https://x/nope"}
    assert is_usable(normalize(empty, FETCHED)) is False
    assert normalize_all([empty], FETCHED) == []


def test_normalize_all_keeps_usable_and_drops_empty(amazon):
    empty = {"offers": {}}
    products = normalize_all([amazon, empty], FETCHED)
    assert len(products) == 1
    assert products[0].name


# --- retailer -----------------------------------------------------------------


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://www.amazon.com/dp/X", "Amazon"),
        ("https://www.walmart.com/ip/1", "Walmart"),
        ("https://www.bestbuy.com/site/x", "Best Buy"),
        ("https://shop.example.co.uk/p", "Shop"),
        ("", ""),
    ],
)
def test_retailer_from_url(url, expected):
    assert retailer_from_url(url) == expected


# --- the two transports agree --------------------------------------------------


def test_rest_and_mcp_normalize_identically(amazon, amazon_mcp):
    """The same product fetched two ways must produce the same product.

    If this fails, one transport is returning a different shape and the README's
    claim that the paths are interchangeable is wrong.
    """
    a = normalize(amazon, FETCHED).to_dict(include_extras=False)
    b = normalize(amazon_mcp, FETCHED).to_dict(include_extras=False)
    assert a["name"] == b["name"]
    assert a["brand"] == b["brand"]
    assert a["price"] == b["price"]
    assert a["in_stock"] == b["in_stock"]
    assert a["rating"] == b["rating"]
    assert a["identifier"] == b["identifier"]


# --- eBay's link text in the product name -----------------------------------

def test_strips_ebay_link_text_from_name():
    """Every eBay record arrives with this appended. Left in, it lands in a card
    or a cited document as though it were part of the product's name."""
    p = normalize({"name": "Saucony Women Cohesion 18Opens in a new window or tab"}, FETCHED)
    assert p.name == "Saucony Women Cohesion 18"


def test_strips_suffix_with_a_space_before_it():
    p = normalize({"name": "Nike Pegasus 41 Opens in a new window or tab"}, FETCHED)
    assert p.name == "Nike Pegasus 41"


def test_strips_suffix_and_the_separator_left_behind():
    p = normalize({"title": "Brooks Revel 8 - Opens in a new tab"}, FETCHED)
    assert p.name == "Brooks Revel 8"


def test_leaves_a_clean_name_alone():
    p = normalize({"name": "ASICS Men's Gel-Cumulus 27"}, FETCHED)
    assert p.name == "ASICS Men's Gel-Cumulus 27"


def test_a_name_that_merely_contains_the_phrase_is_untouched():
    """Only a suffix is stripped. A product genuinely named after the phrase, or
    one where it appears mid-string, must survive."""
    p = normalize({"name": "Opens in a new window or tab sticker pack"}, FETCHED)
    assert p.name == "Opens in a new window or tab sticker pack"


# --- positive_int, added after --batch 0 reached range(0, n, 0) and --limit 0
#     reached the Actor, which bills a start event before rejecting the input ---


def test_positive_int_accepts_counts():
    assert positive_int("1") == 1
    assert positive_int("8") == 8


def test_positive_int_rejects_zero_and_negatives():
    for bad in ("0", "-1", "-100"):
        with pytest.raises(argparse.ArgumentTypeError, match="1 or more"):
            positive_int(bad)


def test_positive_int_rejects_non_numbers():
    for bad in ("abc", "", "1.5", " "):
        with pytest.raises(argparse.ArgumentTypeError, match="whole number"):
            positive_int(bad)
