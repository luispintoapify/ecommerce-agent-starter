"""Tests for the scorer, run against hand-written answers rather than live arms.

The point of these is that the scoring rules cannot quietly drift to flatter one arm.
Every case below is an answer shape observed from a real model or scraper.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harness import Result, score_one, prices_in, urls_in, mentions_absent, asserts_out_of_stock

Q_PRICE = {"id": "q01", "type": "price", "expect_id": "B0F643TQ4W",
           "url": "https://www.amazon.com/dp/B0F643TQ4W"}
Q_STOCK = {"id": "q05", "type": "stock", "expect_id": "B09XS7JWHH",
           "url": "https://www.amazon.com/dp/B09XS7JWHH"}
Q_BUDGET = {"id": "q17", "type": "constraint", "keyword": "running shoes", "max_price": 50}


def r(answer, **kw):
    return Result(arm="test", qid="q01", answer=answer, **kw)


# --- price parsing -----------------------------------------------------------

def test_parses_dollar_symbol():
    assert prices_in("It is $89.95 today") == [89.95]

def test_parses_iso_suffix():
    assert prices_in("151.00 USD on eBay") == [151.0]

def test_parses_thousands_separator():
    assert prices_in("$1,249.99") == [1249.99]

def test_ignores_bare_numbers():
    assert prices_in("the 27 model, rated 4.6") == []

def test_finds_several_prices_in_order():
    assert prices_in("$89.95 on Amazon and $75.00 on eBay") == [89.95, 75.0]


# --- url parsing -------------------------------------------------------------

def test_strips_trailing_punctuation():
    assert urls_in("see https://www.amazon.com/dp/B0F643TQ4W.") == ["https://www.amazon.com/dp/B0F643TQ4W"]

def test_no_urls_is_empty():
    assert urls_in("about ninety dollars") == []


# --- honesty phrases ---------------------------------------------------------

def test_recognises_absent_language():
    assert mentions_absent("The retailer does not report stock.")

def test_recognises_out_of_stock_assertion():
    assert asserts_out_of_stock("This item is out of stock.")

def test_absent_language_is_not_an_out_of_stock_assertion():
    a = "Stock is not reported by this retailer."
    assert mentions_absent(a) and not asserts_out_of_stock(a)


# --- whole-result scoring ----------------------------------------------------

def test_good_answer_is_usable():
    s = score_one(Q_PRICE, r("It is $89.95 as of just now. https://www.amazon.com/dp/B0F643TQ4W"))
    assert s["usable"] and s["gave_price"] and s["gave_url"] and s["right_product"]

def test_price_without_a_source_is_not_usable():
    s = score_one(Q_PRICE, r("It costs about $89.95."))
    assert not s["usable"] and s["gave_price"] and not s["gave_url"]

def test_link_to_the_wrong_product_fails():
    """The failure mode that matters: a real price attached to a different listing."""
    s = score_one(Q_PRICE, r("$89.95, see https://www.amazon.com/dp/B000000000"))
    assert s["gave_price"] and s["gave_url"] and s["right_product"] is False
    assert not s["usable"]

def test_search_page_link_does_not_count_as_the_product():
    s = score_one(Q_PRICE, r("$89.95 https://www.amazon.com/s?k=gel+cumulus+27"))
    assert s["right_product"] is False

def test_blocked_arm_is_never_usable():
    s = score_one(Q_PRICE, r("", blocked=True))
    assert s["blocked"] and not s["usable"]

def test_error_arm_is_never_usable():
    s = score_one(Q_PRICE, r("$89.95 https://www.amazon.com/dp/B0F643TQ4W", error="timeout"))
    assert not s["usable"]

def test_budget_respected():
    s = score_one(Q_BUDGET, r("The Puma Voltaic is $44.95 https://www.ebay.com/itm/406381236330"))
    assert s["in_budget"] is True and s["usable"]

def test_over_budget_answer_fails_even_with_a_real_price():
    s = score_one(Q_BUDGET, r("The Hoka Bondi is $148.50 https://www.ebay.com/itm/318131996847"))
    assert s["in_budget"] is False and not s["usable"]

def test_stock_honesty_scored_only_on_stock_questions():
    assert score_one(Q_PRICE, r("$248 https://www.amazon.com/dp/B09XS7JWHH"))["stock_honest"] is None
    assert score_one(Q_STOCK, r("$248, stock is not reported. https://www.amazon.com/dp/B09XS7JWHH"))["stock_honest"] is True

def test_asserting_out_of_stock_without_hedging_is_dishonest():
    s = score_one(Q_STOCK, r("It is out of stock. https://www.amazon.com/dp/B09XS7JWHH"))
    assert s["stock_honest"] is False

def test_in_stock_assertion_is_fine():
    s = score_one(Q_STOCK, r("In stock, $248. https://www.amazon.com/dp/B09XS7JWHH"))
    assert s["stock_honest"] is True


def test_no_url_is_not_a_wrong_product():
    """A blocked or errored arm linked nothing, which is not the same as linking wrong.

    Counting it as wrong_product would double-count one failure and make an arm that
    fell over look like an arm that hallucinated a listing.
    """
    s = score_one(Q_PRICE, r("", blocked=True))
    assert s["right_product"] is None
    assert not s["gave_url"] and not s["usable"]


def test_no_url_with_a_price_is_still_not_wrong_product():
    s = score_one(Q_PRICE, r("About $89.95."))
    assert s["right_product"] is None and not s["usable"]
