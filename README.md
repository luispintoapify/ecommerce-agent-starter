# ecommerce-agent-starter

Give any AI agent live product data through the [Apify MCP server](https://docs.apify.com/integrations/mcp): current price, stock, brand, rating, and image URLs from Amazon, Walmart, Target, eBay, and many more retailers. Runtime tool calls plus a scheduled RAG refresh, in Python, MIT licensed.

A language model answers product questions from training data that was fixed months ago, or from browsing that reads a page as prose with no product fields. Neither gives you a price you can rely on today. This starter wires an agent to the real thing, as structured fields it can compare, filter, and act on.

Two paths, because they solve different problems:

| | Transport | When to use it |
|---|---|---|
| **`runtime_call.py`** | Apify MCP server | The answer has to be true right now, for a handful of products |
| **`rag_refresh.py`** | Apify REST API | A catalog an agent answers from repeatedly |

Both run [E-commerce Scraping Tool](https://apify.com/apify/e-commerce-scraping-tool), an Apify Actor that handles anti-bot, proxies, and per-retailer extraction, so there is no scraper in this repo to maintain.

**Why two transports.** The runtime path goes through MCP because that is what an agent does, so the code shows you what your agent is doing. The batch job does not: a cron with no agent in it gains nothing from the protocol, and it needs the full dataset rather than a tool result. `apify_products.py` normalizes both into the same shape, and a test asserts the two agree.

Requires **Python 3.10 or newer**, because the official `mcp` SDK does.

## Connect it to your agent

Every path below points at the same MCP server. Nothing to host.

### Claude Desktop and Claude Code

Copy [`mcp_config.json`](mcp_config.json) into your Claude config, or paste this block:

```json
{
  "mcpServers": {
    "apify-ecommerce": {
      "url": "https://mcp.apify.com?tools=apify/e-commerce-scraping-tool"
    }
  }
}
```

Claude Desktop reads `claude_desktop_config.json`. On macOS that is `~/Library/Application Support/Claude/`, on Windows `%APPDATA%\Claude\`. Restart Claude and the tool appears. You authenticate with OAuth on first use, so no token goes in the file.

Drop the `?tools=` parameter and the agent can search all of [Apify Store](https://apify.com/store) at runtime instead of just this one Actor. Keeping it narrows what the model has to choose from, which makes tool selection more reliable when product data is the only job.

### Cursor

Same block, in `.cursor/mcp.json` in your project, or in `~/.cursor/mcp.json` to have it everywhere.

### n8n

n8n has no JSON config to copy, because MCP is a node. Add an **AI Agent** node, attach an **MCP Client Tool**, and set the endpoint to:

```
https://mcp.apify.com?tools=apify/e-commerce-scraping-tool
```

Authenticate with a bearer token from [Apify Console](https://console.apify.com/settings/integrations) rather than OAuth, since the node runs unattended.

### Anything else

Any MCP client that speaks Streamable HTTP works against the same URL. SSE was removed on April 1, 2026. The [Apify MCP documentation](https://docs.apify.com/integrations/mcp) covers transports, authentication, and Actor discovery in full.

## What the MCP flow actually looks like

Worth knowing before you design a prompt around it: **fetching products takes more than one tool call.**

1. Call `apify--e-commerce-scraping-tool`. Note the two hyphens where the Actor id uses a slash. It returns run metadata (`runId`, status, stats, and the id of the dataset it wrote) plus a text block telling the caller to fetch the items separately. **No products.**
2. Check the status. The Actor tool returns when its own wait window elapses, not when the run finishes, so `RUNNING` is a normal answer with an empty dataset behind it. Poll `get-actor-run` with the `runId` until the status is terminal. Same URL, two runs: `SUCCEEDED` in 10 seconds on one, still `RUNNING` at 40 on the other.
3. Call `get-dataset-items` with that `datasetId`. The products come back here.

That is why the server exposes helper tools alongside the Actor even when the URL narrows the list:

```
get-actor-run, get-dataset-items, get-key-value-store-record,
abort-actor-run, apify--e-commerce-scraping-tool
```

`mcp_client.py` implements all three steps. If you are writing your own agent prompt, make sure it knows to poll before fetching. An agent that stops at step 1 reports success with no data, and one that skips step 2 fetches an empty dataset and says the product was not found.

`get-dataset-items` also takes a `fields` parameter, worth reaching for in a chat turn: one Amazon product is roughly 88 KB across 142 fields, most of it marketing and review text. Note that projecting flattens the response into dotted keys such as `offers.price`, so `normalize()` in this repo needs the unprojected shape.

## Run the scripts

```bash
git clone https://github.com/luispintoapify/ecommerce-agent-starter
cd ecommerce-agent-starter
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # add your APIFY_TOKEN
```

Your token comes from [Apify Console](https://console.apify.com/settings/integrations). The free tier is enough to try everything here.

One product, live, over MCP:

```bash
python runtime_call.py --url https://www.amazon.com/dp/B09XS7JWHH
```

A keyword across two retailers:

```bash
python runtime_call.py --keyword "noise cancelling headphones" \
  --marketplaces www.amazon.com www.walmart.com --limit 10
```

Refresh a catalog into documents ready for embedding:

```bash
python rag_refresh.py --catalog catalog.example.json --sink jsonl
```

The `jsonl` sink needs no vector store and no extra dependencies. Look at `products.jsonl` before wiring anything: it holds the exact documents and metadata that would be upserted.

Into Pinecone:

```bash
pip install -r requirements-pinecone.txt
python rag_refresh.py --catalog catalog.example.json --sink pinecone
```

## What comes back

`apify_products.py` normalizes the Actor's output into one stable shape. That module is the part worth reading, because field names, types, and nesting vary by retailer, and reading them naively works on Amazon and then breaks on the next store.

Measured against live runs, not guessed:

| Field | Amazon returned | Walmart returned |
|---|---|---|
| `offers.price` | `248` (number) | `"398.99"` (string) |
| `offers.priceCurrency` | `"$"` (symbol) | `"USD"` (code) |
| `brand` | `{"slogan": "Sony"}` | `{"slogan": "Visit the Sony Store"}` |
| `additionalProperties` | 44 keys | 4 keys |

Two of those are worth dwelling on.

**`brand` is not reliably a brand.** The `slogan` slot carries whatever text the retailer put there. The normalizer unwraps the "Visit the X Store" pattern and rejects anything still shaped like a phrase, because `Brand: Visit the Sony Store` embedded in a document an agent cites reads as fact.

**Stock, rating, and identifiers are nested under `additionalProperties`, not at the top level**, and that sub-object is itself per-retailer. Amazon returned 44 keys there including `inStock`, `stars`, and `listPrice`. Walmart returned 4. So the promoted fields below are best-effort, and `Product.extras` carries the whole thing verbatim for the retailer you actually care about.

Normalized, from a real Amazon run:

```python
Product(
    name="Sony WH-1000XM5 Premium Noise Cancelling Wireless Headphones, Black",
    brand="Sony",
    price=248.0,
    currency="$",              # format_price() renders "$248"
    list_price=399.99,         # discount_percent() returns 38
    in_stock=True,
    stock_text="In Stock  Only 15 left in stock - order soon.",
    rating=4.2,
    review_count=20002,
    identifier="B09XS7JWHH",
    delivery="Saturday, August 22",
    images=["https://..."],
    url="https://www.amazon.com/dp/B09XS7JWHH",
    retailer="Amazon",
    fetched_at="2026-08-17T08:17:07+00:00",
    extras={...},              # the retailer's own 44 fields
)
```

Three deliberate choices:

**`in_stock` is tri-state.** `None` means the retailer did not report it, and not every retailer does. Collapsing that to `False` would tell an agent a product is unavailable when nobody claimed it, which is the class of confident wrong answer this repo exists to avoid.

**`rating` prefers `additionalProperties.stars`.** A live Amazon product reported `rating: null` next to `stars: 4.2`, and a zero rating is treated as absent, because nobody gave it.

**Rows with neither a name nor a price are dropped rather than indexed.** A URL that does not resolve comes back as an item with every field empty rather than as an error, and feeding those into a vector store fills it with blanks an agent later cites as fact.

### extras is large

One Amazon product's `additionalProperties` ran to roughly 100 KB across 44 keys: A+ content, review text, variant tables, bestseller ranks. So:

- `to_text()`, the embedded document, never includes it
- the `jsonl` sink excludes it unless you pass `--extras`
- the Pinecone sink always excludes it, because Pinecone caps metadata at 40 KB per vector and the upsert would fail outright

## Tell the agent how fresh the data is

Every document carries `fetched_at`, and `Product.to_text()` puts it in the embedded text. Reference it in your system prompt:

```
Product facts come from a catalog with a `fetched_at` timestamp. When you quote a
price or stock status, say when it was read. If the question needs a price that is
true this second, call E-commerce Scraping Tool instead of answering from the
catalog.
```

Without that, an agent will quote an indexed price as though it were live, which is the same failure as answering from training data, just with fresher wrong numbers.

## Cost and speed

E-commerce Scraping Tool bills per event: a start event per call, plus per product pushed, plus residential proxy and browser rendering where a retailer needs them. Current rates are on the [Actor's pricing tab](https://apify.com/apify/e-commerce-scraping-tool/pricing). Two consequences for how you call it:

- **Batch.** One call for 200 products costs far less than 200 calls for one. `rag_refresh.py` batches by default.
- **Cap.** `--limit` is a hard cap the platform enforces, not a suggestion. Leave it set.

On one recorded run, 175 products across Amazon and eBay came back in 30 seconds, billing about $0.29. That is under a fifth of a cent per product. Two single-product runs each billed $0.0026, which is the shape of the pricing: the start event dominates when you fetch one item, so batching is where the saving is. A single Amazon product came back in about 10 seconds, and the MCP path adds the fetch call on top of that.

`--batch` defaults to 8 because the whole call shares one 300 second timeout, so a large batch of slow URLs times out and loses every product in it.

## Retailers

The Actor's `marketplaces` input lists storefronts with dedicated extractors, including Amazon, Walmart, Target, eBay, Best Buy, Home Depot, Lowe's, IKEA, Tesco, Mercado Libre, Idealo, and Kaufland.

**Over MCP that list arrives truncated.** The server caps how many characters of a long enum it passes through, so some supported retailers are missing from what an agent can select, and passing one anyway is a validation error before the run starts. It does not mean the retailer is unsupported: `detailsUrls` takes arbitrary URLs and is unaffected. Use it when the marketplace you want is not selectable.

**That list is not the boundary of what works.** It names the retailers with dedicated extractors. Sites outside it fall back to generic extraction, which is enabled by default, so a URL from an unlisted shop is worth trying. What the list buys you is depth: field coverage is best on the major retailers, so check the fields you depend on before building on a smaller store.

The current list is in the [Actor's input schema](https://apify.com/apify/e-commerce-scraping-tool/input-schema). For marketplaces outside this Actor's scope, [Apify Store](https://apify.com/store) has retailer-specific Actors that reach the same MCP server.

### When a URL comes back empty

`detailsUrls` is the input field for individual product detail pages, and it is confirmed working. Both scripts use it.

A URL that does not resolve returns an item with no fields rather than an error, so check the URL itself first. `--listing` routes the same URLs through `listingUrls` if you want to rule the input field out, and an issue naming the retailer is welcome either way.

## Tests

```bash
pip install -r requirements-dev.txt
pytest -q
```

The fixtures in `tests/fixtures/` are **real Actor output**, captured from live runs against Amazon and Walmart. Every quirk asserted in the suite was found by running the Actor, not by imagining what it might return, and one test asserts the MCP and REST paths normalize to the same product. No token is needed and no credits are spent, so CI runs it on every push across Python 3.10 through 3.13.

If a retailer changes shape, recapture the fixture rather than loosening the assertion.

## Agent skills

Two skills in [`skills/`](skills/), for the two audiences:

| Skill | For | What it carries |
|---|---|---|
| [`apify-product-lookup`](skills/apify-product-lookup/) | An agent that already has the tool | Which input to send for which question, how to read the nested fields, how to answer without overclaiming |
| [`apify-product-data-setup`](skills/apify-product-data-setup/) | Someone wiring it up | Choosing runtime against scheduled, client config blocks, the refresh pattern, cost control |

Both carry a `references/fields.md` mapping every field shape observed in live runs,
including where Amazon and Walmart disagree. Drop either directory into your agent's
skills folder, or package it:

```bash
python3 path/to/skill-creator/scripts/package_skill.py skills/apify-product-lookup ./dist
```

The setup skill's `references/clients.md` has the config for Claude Desktop, Claude
Code, Cursor, n8n, and a Python example using the official SDK.

## Prompt library

If you are writing the agent's instructions yourself rather than installing a skill,
[`prompts/`](prompts/) has two copy-paste system prompts:

| Prompt | The agent's job |
|---|---|
| [`shopping-agent.md`](prompts/shopping-agent.md) | Recommend, compare, and quote prices the user will act on |
| [`support-agent.md`](prompts/support-agent.md) | Answer for a business: specs, availability, delivery, price matching |

Both end with worked examples chosen to fail loudly when a prompt is not landing: a
lookup that catches an agent stopping before the products arrive, a comparison that
catches a missing cost cap, and a listing with no stock field that catches an agent
turning "not reported" into "out of stock".

The support prompt is the longer of the two, because a support answer becomes a promise
the business has to honor, so most of it is about what not to claim.

## Benchmark

[`benchmark/`](benchmark/) holds a three-arm comparison: this Actor over MCP, a model with native web search, and a DIY Playwright scraper, over twenty frozen product questions.

```bash
pip install -r benchmark/requirements-bench.txt
python benchmark/run.py --arms apify,browsing,diy && python benchmark/score.py
```

We wrote it and we sell one of the arms, so the credibility rests on three things you can check rather than on our word: the questions were committed **before any arm existed** (check the git history), the scorer only grades objectively true or false properties, and 23 tests pin the scoring rules so they cannot drift toward one arm.

It scores whether an answer is **actionable and checkable** (a price, a resolvable link to the product actually asked about, a respected budget, honesty about withheld fields) and deliberately does not claim to adjudicate absolute price truth. Where two arms disagree on a price, it flags the case for a human instead of picking a winner. [`benchmark/README.md`](benchmark/README.md) explains what it measures and what it refuses to.

## Scheduling

[`.github/workflows/refresh.yml`](.github/workflows/refresh.yml) is a working example of the scheduled path. Fork it, add an `APIFY_TOKEN` secret, point `--catalog` at your own URLs, and swap the artifact upload for `--sink pinecone`. The `schedule` trigger ships commented out so a fork does not start spending credits on a cron nobody asked for.

## Demo

A recorded run, replayed: [Running Shoes in 29.6s](https://claude.ai/code/artifact/a9fb57d3-8ea7-40c7-8383-ae618f511509). One keyword, two retailers, 175 typed records, and a panel showing which fields each retailer actually returned. Every value is verbatim from run `5MktQElgZgf9cIina`.

<!-- TODO: embed the 60 to 90 second demo clip once it is recorded. -->

## Contributing

Issues and pull requests welcome, particularly retailer-specific field quirks. If a retailer returns a shape `apify_products.py` mishandles, an issue with the raw dataset item is the most useful thing you can send, because it becomes a fixture.

## License

MIT. See [LICENSE](LICENSE).
