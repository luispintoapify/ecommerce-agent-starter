# Three-arm product data benchmark

Twenty product questions, three ways of answering them, one scoring script.

| Arm | What it is | What it needs |
|---|---|---|
| `apify_mcp` | An agent calling E-commerce Scraping Tool over the Apify MCP server | `APIFY_TOKEN` |
| `native_browsing` | The same questions answered by a model with Anthropic's native web search | `ANTHROPIC_API_KEY` |
| `websearch_openrouter` | The same prompt and model family, but web search via OpenRouter's `:online` plugin, called through an Apify Actor | `APIFY_TOKEN`, `OPENROUTER_BRIDGE_ACTOR` |
| `diy_playwright` | Write the scraper yourself, no proxies, no anti-bot handling | `pip install playwright` |

`websearch_openrouter` is a **substitute** for `native_browsing`, not a second copy of
it. The two use the same system prompt, imported from one file so they cannot drift, and
the same model family, but a different retrieval backend. Two arms that differ in what
they can find are not one measurement, so they get separate names and separate rows. The
published run used the substitute, because it needs no Anthropic key.

```bash
pip install -r requirements-bench.txt && playwright install chromium
python run.py --arms apify,openrouter,diy
python score.py
```

Each arm is independent. A missing key or dependency shows up as a row in the table, not a crash.

## Results, August 27, 2026

Every number below came out of `score.py`. The runs that produced them are in this
directory, so you can rescore them without rerunning anything:

```bash
python score.py --results results.jsonl
```

| Arm | usable | gave a price | price not in USD | gave a link | linked the wrong product | p50 | billed |
|---|---|---|---|---|---|---|---|
| `apify_mcp` | **18/20** | 18 | 0 | 20 | 0 | 24.1s | $0.2227 |
| `websearch_openrouter` | 9/20 | 15 | 0 | 20 | **7** | 17.7s | $3.5260 |
| `diy_playwright` | 0/20 | 9 | **9** | 9 | 0 | 3.0s | $0.0000 |

Cost per usable answer: **$0.0124**, $0.3918, and no usable answers.

### The headline hides where the difference actually is

Fourteen questions name a specific product by URL, so "did you answer about the thing I
asked about" is checkable. Six are keyword questions with no such anchor. Split that way:

| Arm | 14 URL-anchored | right product | wrong product | 6 keyword |
|---|---|---|---|---|
| `apify_mcp` | 12/14 | 14 | 0 | 6/6 |
| `websearch_openrouter` | 3/14 | 7 | 7 | 6/6 |
| `diy_playwright` | 0/14 | 9 | 0 | 0/6 |

**On the open questions the first two arms tie.** Asked for running shoes under $50, web
search does the job. The entire gap opens on questions that pin one item, where web
search answered about a different shoe seven times out of fourteen.

### Three things the table does not say

**The two `apify_mcp` misses are out-of-stock products, not wrong answers.** Both are the
ASICS Gel-Cumulus 27. The Actor returned `offers.price: null` with
`additionalProperties.inStock: false`, and the arm reported that instead of guessing a
number. Both had prices nine days earlier. "Usable" requires a price you can act on, so
they score as misses, which is the right call for the question and the wrong word for
what happened.

**Nobody here is shown to have the correct price.** `score.py` prints twelve price
disagreements between the first two arms and refuses to adjudicate them, because absolute
price truth needs a human on the page at the same moment. What can be said mechanically
is that an arm which linked a different product will report a different price, and one
arm did that seven times.

**`diy_playwright` was not blocked.** It loaded fourteen pages and found nine prices, and
all nine came back in Czech koruna, because scraping a US retailer from a European IP
returns the localized page. Its failure is locale, not anti-bot. An early probe of the
web-search arm hit the same wall from a different direction, answering with a Canadian
dollar price from `amazon.ca`. Note also that `diy_playwright` is the fastest arm by a
factor of eight and costs nothing; the trade-off is real, it just does not answer the
question that was asked.

## We built this and we sell one of the arms

So the credibility has to come from somewhere other than our word. Three things carry it:

**The products came from an Actor run, and that favours one arm.** Thirteen of the
fourteen pinned products are the Amazon subset of a single E-commerce Scraping Tool run
from August 18, 2026, so they are products that Actor is known to read. The scorer never
compares against a stored price, and `expect_id` only checks that an arm answered about
the product asked about, so this is not circular. It is still selection bias, and it runs
in our favour: the questions are real and were frozen before any arm ran, but they were
not sampled blind from the web. Weigh the result accordingly.

**The questions were frozen before any arm ran.** `questions.json` was committed on August 18, 2026, in its own commit, before a single result existed. Check the git history: if the questions had been chosen to flatter one arm, the commit order would show it. Do not edit that file to fit a result. Add a `questions-v1.1.json` and say what changed and why.

**The scorer only checks things that are objectively true or false.** No arm is scored on whether its prose was persuasive. See *What this measures* below, and `tests/test_score.py`, which pins every scoring rule against hand-written answers so the rules cannot quietly drift toward one arm.

**Run it yourself.** Every number in any claim we publish comes out of `score.py` on your machine as readily as ours.

## What this measures

Absolute price truth needs a human looking at the page at the same moment, so the automated scorer does not claim it. What it scores is whether an arm gave you an answer you could **act on and check**:

| Check | Question it answers |
|---|---|
| `gave_price` | Was there a number at all? |
| `gave_url` | Was there a link? |
| `right_product` | Did the link point at the product that was asked about? Exact, by identifier in the path. Only scored when a link was given, because an arm that returned nothing has not returned something *wrong* |
| `in_budget` | On "under $50" questions, was the price actually under $50? |
| `stock_honest` | Did it assert "out of stock" where "the retailer does not report stock" was the honest answer? |
| `latency_ms`, `cost_usd`, tokens | Measured, not estimated. Costs use published list rates |

`usable` is the headline: a price, a resolvable link to the right product, inside any stated budget, with no error. Not "sounded good".

**What it does not measure.** Whether a quoted price was correct. Two arms disagreeing on a price is either a real change between calls or one of them being wrong, and no script can tell which, so `score.py` prints those cases under **Price disagreements needing a human look** instead of picking a winner. For an accuracy claim, record the true price per question by hand at run time and compare against it. Publish that pass separately and say a human did it.

## The question set

Twenty questions across six shapes, deliberately weighted toward things a stale answer gets wrong:

| Shape | n | Why it is here |
|---|---|---|
| `price` | 5 | The base case, and the one training data always gets wrong eventually |
| `stock` | 3 | Where the honest answer is often "the retailer does not say" |
| `discount` | 3 | Needs the list price as well as the current one |
| `rating` | 3 | A field some retailers report and others do not |
| `compare` | 3 | Two retailers in one answer |
| `constraint` | 3 | "Under $50" is checkable, so the scorer can grade it |

Fourteen are anchored to a specific product URL, which is what makes `right_product` exact rather than a judgment call. Six are keyword or comparison questions with no URL.

## Reading the results honestly

**A blocked DIY arm is a finding, not a broken test.** `diy_playwright` records `blocked` as its own outcome. If it gets served an interstitial on a retailer, that is the maintenance cost of the DIY path showing up in the measurement. It also handles only direct product URLs and records `not implemented` on the other six, because writing a fragile per-retailer search-results parser and then reporting its failures as accuracy would be dishonest.

**The DIY arm runs with no proxies and no anti-bot handling on purpose.** Adding them is precisely the work this arm exists to measure the absence of. If you benchmark a hardened DIY scraper, add your proxy configuration to `arm_playwright.py` and say so when you publish.

**The browsing arm gets a fair prompt.** Its system prompt asks for exactly what the scorer checks: a price, a currency, stock, a rating, and a link to the page the price came from. Handicapping it would make the comparison flattering rather than informative. Read `SYSTEM` in `arm_browsing.py` and change it if you think it is unfair.

**Latency is a sample, not a guarantee.** The same product URL returned in 10 seconds on one call and 40 on another during development. Report `p50` and the max, never a single number.

**Costs are list-rate arithmetic, not invoices.** The Apify arm's per-question cost is computed from a start event plus a per-product charge, using figures measured from real runs; the browsing arm's is computed from token counts and published per-token rates. For the real number, read your own billing.

## If you extend it

- Add an arm as `arm_<name>.py` exposing `run_all(questions) -> list[Result]`, then wire it into `ARMS` in `run.py`.
- Keep `Result.answer` as plain text. The scorer reads text so that an arm which returns structured data gets no advantage over one that returns prose.
- Run `pytest tests/ -q` after touching `harness.py`. Those tests exist to stop the scoring rules drifting.
