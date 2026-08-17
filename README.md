# ecommerce-agent-starter

Give any AI agent live product data through the [Apify MCP server](https://docs.apify.com/integrations/mcp): current price, availability, brand, description, and image URLs from Amazon, Walmart, Target, eBay, and 75 more retailers. Runtime tool calls plus a scheduled RAG refresh, in Python, MIT licensed.

A language model answers product questions from training data that was fixed months ago, or from browsing that reads a page as prose with no product fields. Neither gives you a price you can rely on today. This starter wires an agent to the real thing, as structured fields it can compare, filter, and act on.

Two paths, because they solve different problems:

| | When to use it | What it costs |
|---|---|---|
| **Runtime call** | The answer has to be true right now, for a handful of products | A start event per call plus per product |
| **Scheduled refresh** | A catalog an agent answers from repeatedly | One start event per batch, so far cheaper per product |

Both run on [E-commerce Scraping Tool](https://apify.com/apify/e-commerce-scraping-tool), an Apify Actor that handles anti-bot, proxies, and per-retailer extraction so there is no scraper in this repo to maintain.

## Connect it to your agent

Every path below points at the same MCP server, [mcp.apify.com](https://docs.apify.com/integrations/mcp). Nothing to host.

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

Drop the `?tools=` parameter and the agent can search all of [Apify Store](https://apify.com/store) at runtime instead of just this one Actor. Keeping the parameter narrows what the model has to choose from, which makes tool selection more reliable when product data is the only job.

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

## Run the scripts

```bash
git clone https://github.com/luispintoapify/ecommerce-agent-starter
cd ecommerce-agent-starter
pip install -r requirements.txt
cp .env.example .env      # add your APIFY_TOKEN
```

Your token comes from [Apify Console](https://console.apify.com/settings/integrations). The free tier is enough to try everything here.

One product, live:

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

`apify_products.py` normalizes the Actor's output into one stable shape. That module is the part worth reading, because field names and types vary by retailer, and reading them naively works on Amazon and then breaks on the next store.

These are measured against live runs on Amazon and Walmart, not guessed:

| Field | Amazon returned | Walmart returned |
|---|---|---|
| `offers.price` | `248` (number) | `"398.99"` (string) |
| `offers.priceCurrency` | `"$"` (symbol) | `"USD"` (code) |
| `brand` | `{"slogan": "Sony"}` | `{"slogan": "Visit the Sony Store"}` |
| `inStock` / `availability` | both `null` | both `null` |
| `rating` / `reviewCount` | `null` / `20002` | `0` / `0` |

Also seen: the title is `name` or `title`, and images arrive as `image` (string or list) or `images` (list).

Two of those rows are worth dwelling on.

**`brand` is not reliably a brand.** The `slogan` slot carries whatever text the retailer put there. `apify_products.py` unwraps the "Visit the X Store" pattern and rejects anything still shaped like a phrase, because `Brand: Visit the Sony Store` embedded in a document an agent cites is worse than no brand at all.

**`rating` and `reviewCount` are not exposed by this starter**, deliberately. Amazon reported 20,002 reviews with a `null` rating, and Walmart reported `0` and `0` for a product that plainly has reviews. Passing those through would put numbers in front of an agent that are wrong in both directions.

Normalized, from a real Amazon run:

```python
Product(
    name="Sony WH-1000XM5 Premium Noise Cancelling Wireless Headphones, Black",
    brand="Sony",
    price=248.0,
    currency="$",          # format_price() renders "$248"
    in_stock=None,         # the retailer did not say
    images=["https://..."],
    url="https://www.amazon.com/dp/B09XS7JWHH",
    retailer="Amazon",
    fetched_at="2026-08-17T08:17:07+00:00",
)
```

`in_stock` is deliberately tri-state, and the run above is exactly why. `None` means the retailer did not say. Collapsing that to `False` would tell an agent a product is unavailable when nobody claimed it, which is the class of confident wrong answer this repo exists to avoid.

**Neither Amazon nor Walmart returned stock in testing.** Plan for `None` as the normal case and design the agent's answer around it, rather than treating stock as a field you can count on.

`format_price()` handles the currency inconsistency: a symbol butts against the number, an ISO code takes a space, so both `$248` and `USD 398.99` read correctly.

Rows with neither a name nor a price are dropped rather than indexed. An unrecognized page otherwise fills a vector store with blanks that an agent later cites as fact.

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

On one recorded run, 175 products came back in 60 seconds for about $1, roughly half a cent per product. Throughput is good because the Actor routes to per-retailer Standby Actors that stay warm.

### Latency varies enormously by retailer

Measured, one product at a time:

| Call | Time |
|---|---|
| Amazon, one product by URL | **10.3s** |
| Walmart, three products by keyword | **174.1s** |

The Actor itself does not run in Standby, so every call starts an orchestrator run and a one-product call does not take a 175th of 60 seconds. Ten seconds is workable inside a considered interaction. Nearly three minutes is not, and it is not an outlier.

Two consequences:

- **Measure your own retailers before putting a runtime call in a chat turn.** Design the UX around what you measure, not around the Amazon number.
- **`--batch` defaults to 8, not something larger.** The whole call shares one 300 second timeout, so a batch of 25 slow URLs times out and loses every product in it. Raise it only for retailers you have timed.

## Retailers

The Actor's `marketplaces` input lists 225 storefront entries across 79 retailer brands, including Amazon, Walmart, Target, eBay, Best Buy, Home Depot, Lowe's, IKEA, Tesco, Mercado Libre, Idealo, and Kaufland. Field coverage is deepest on the major retailers, so check the fields you depend on before building on a smaller store. Other sites work through generic extraction with less reliable coverage.

The current list is in the [Actor's input schema](https://apify.com/apify/e-commerce-scraping-tool/input-schema). For marketplaces outside this Actor's scope, [Apify Store](https://apify.com/store) has retailer-specific Actors that reach the same MCP server.

### When a URL comes back empty

`detailsUrls` is the input field for individual product detail pages, and it is confirmed working: an Amazon product URL returned a full record in 10.3s. Both scripts use it.

A URL that does not resolve comes back as an item with **no fields at all**, not as an error. That was reproduced with a URL whose product ID did not exist: 118 seconds, one item, every field empty. The pipeline drops those rather than indexing them, and `runtime_call.py` says so plainly instead of printing a blank record.

So if a URL comes back empty, check the URL itself first. `--listing` routes the same URLs through `listingUrls` if you want to rule the input field out, and an issue naming the retailer is welcome either way.

## Demo

<!-- TODO: embed the 60 to 90 second demo clip once it is recorded. -->

## Contributing

Issues and pull requests welcome, particularly retailer-specific field quirks. If a retailer returns a shape `apify_products.py` mishandles, an issue with the raw dataset item is the most useful thing you can send.

## License

MIT. See [LICENSE](LICENSE).
