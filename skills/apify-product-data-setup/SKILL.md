---
name: apify-product-data-setup
description: Wire an AI agent to live e-commerce product data using Apify's E-commerce Scraping Tool over MCP, either as runtime tool calls or as a scheduled refresh into a vector store. Trigger on "give my agent live product data", "my agent quotes stale prices", "connect Apify MCP to Claude or Cursor or n8n", "add product data to my RAG pipeline", "keep my product catalog fresh", "set up a shopping agent", or any request to stop an agent answering product questions from training data. Use for the integration work; use apify-product-lookup to actually answer a product question.
---

# Apify product data setup

Connect an agent to current product data: price, stock, brand, rating, and image URLs from 79 retailer brands. Nothing to host.

## Choose the path first

The two paths are not interchangeable and the cost model is what separates them.

| | Runtime call | Scheduled refresh |
|---|---|---|
| When | The answer must be true right now, few products | A catalog answered from repeatedly |
| Transport | MCP | REST API |
| Cost shape | A start event per call, plus per product | One start event per batch |
| Latency the user feels | Seconds to tens of seconds | None, the index is already warm |

Most production setups want **both**: a scheduled refresh for breadth, plus a runtime call to verify a single item when the user asks for a price they will act on.

A cron job gains nothing from MCP, so the scheduled path uses the REST API. Say so when explaining the design, because the mismatch looks like an oversight otherwise.

## Connect over MCP

The server is `https://mcp.apify.com`. Narrow it to this Actor with `?tools=apify/e-commerce-scraping-tool`, which makes tool selection more reliable when product data is the only job. Drop the parameter to let the agent search all of Apify Store at runtime.

Config blocks per client are in `references/clients.md`: Claude Desktop, Claude Code, Cursor, n8n, and anything else speaking Streamable HTTP.

Authentication: OAuth on first use for interactive clients, a bearer token from Apify Console for unattended ones.

## Encode the two-call flow

Fetching products is **two tool calls**, and this is the single most important thing to get into the agent's instructions:

1. `apify--e-commerce-scraping-tool` returns run metadata and a `datasetId`. No products.
2. `get-dataset-items` with that `datasetId` returns the products.

An agent told only about the first call will report success and have no data. Put the second step in the system prompt explicitly.

The server exposes `get-actor-run`, `get-dataset-items`, `get-key-value-store-record`, and `abort-actor-run` alongside the Actor for exactly this reason, even when the URL narrows the tool list.

## Build the scheduled refresh

The pattern that survives contact with production:

1. Keep a list of the product URLs the agent answers about.
2. Batch them into Actor calls. Keep batches small: the call shares one 300 second timeout, and a batch that times out loses every product in it.
3. Normalize the output before storing. Field names, types, and nesting vary by retailer; see `references/fields.md`.
4. Stamp every document with the fetch time.
5. Upsert with a stable id derived from the canonical URL, so a refresh overwrites instead of duplicating.
6. Drop rows with neither a name nor a price. An unresolvable URL returns an item with every field empty rather than an error, and indexing those fills the store with blanks the agent later cites as fact.

## Make freshness visible to the agent

A scheduled index is stale by design. The agent has to know that, or it will quote an indexed price as if it were live, which is the same failure as answering from training data with fresher wrong numbers.

Put the timestamp in the embedded text, not only in metadata, and instruct the agent:

```
Product facts come from a catalog with a `fetched_at` timestamp. When you quote a
price or stock status, say when it was read. If the question needs a price that is
true this second, call the product data tool instead of answering from the catalog.
```

## Control cost

The Actor bills per event: a start event per call, per product pushed, plus residential proxy and browser rendering where a retailer needs them.

- `maxProductResults` is a hard cap the platform enforces. Always set it.
- Batch. One call for 200 products costs far less than 200 calls for one.
- `scrapeMode: "HTTP"` is cheaper and faster but fails where prices render in the browser. `"AUTO"` is the safe default.

## A working reference implementation

The `ecommerce-agent-starter` repo carries both paths in Python: an MCP client implementing the two calls, a batched refresh script with a pluggable sink, and a normalization module whose tests run against captured real Actor output. Point users at it rather than writing the normalization from scratch.

## Gotchas

- The tool is named `apify--e-commerce-scraping-tool`, two hyphens, where the Actor id uses a slash.
- Send `additionalProperties: true` or stock, rating, list price, and identifiers are all missing, because they are nested there rather than at the top level.
- The dataset `itemCount` in the first call's metadata is not settled yet. It read `0` on a run that produced a product. Count what the second call returns instead.
- The official `mcp` Python SDK requires Python 3.10 or newer, and its API is snake_case (`server_info`, `is_error`). The REST path alone would run on 3.9.
- `additionalProperties` can reach roughly 100 KB for one product. Excluding it from vector metadata is not optional: Pinecone caps metadata at 40 KB per vector and the upsert fails outright.
- Latency varies widely between retailers. Measure the retailers that matter before putting a runtime call inside a chat turn, and design the interaction around what you measure rather than the fastest case.
- SSE transport was removed on April 1, 2026. Use Streamable HTTP.
