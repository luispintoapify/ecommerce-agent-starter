---
name: apify-product-lookup
description: Answer questions about a real product's current price, stock, rating, specs, or images by calling Apify's E-commerce Scraping Tool over MCP, across Amazon, Walmart, Target, eBay, and 75 more retailers. Trigger on "what does X cost right now", "is X in stock", "compare the price of X across stores", "find me a Y under $Z", "how much is this", "check this product URL", or any product question where a stale answer would be wrong. Use whenever answering from training data or a web search would risk quoting an out-of-date price.
---

# Apify product lookup

Fetch live product facts instead of guessing at them. Requires the Apify MCP server to be connected; if the tool is missing, see the `apify-product-data-setup` skill.

## Pick the input before calling

The Actor takes different inputs for different questions. Choosing wrong wastes a paid run.

| The question | Input to send |
|---|---|
| About a specific page the user gave you | `detailsUrls: [{"url": "..."}]` |
| "Find me a X under $Y" with no URL | `keyword` plus `marketplaces` |
| "Compare X across stores" | `keyword` plus several `marketplaces` |
| Food delivery catalogs | `keywordDelivery` plus `marketplacesDelivery` |

Always send `maxProductResults`, and `additionalProperties: true`. Without the second, stock and rating are missing entirely, because they are nested there.

## The call is two tool calls

1. Call `apify--e-commerce-scraping-tool` (two hyphens, not a slash). It returns run metadata and a `datasetId`. **It does not return products.**
2. Call `get-dataset-items` with that `datasetId` and a `limit`. The products come back here.

Stopping after the first call is the most common failure. The first result looks successful and contains no product data.

## Read the fields defensively

Field names, types, and nesting vary by retailer. See `references/fields.md` for the full map. The four that bite hardest:

- **`offers.price`** is a number on some retailers and a string like `"398.99"` on others. Parse both.
- **Stock and rating live under `additionalProperties`**, as `inStock` and `stars`, not at the top level. The top-level `rating` was `null` on a product whose `stars` was `4.2`.
- **`brand`** may be `{"slogan": "Visit the Sony Store"}`. That is UI text, not a brand. Strip the wrapper or omit the brand.
- **`offers.priceCurrency`** is a symbol (`"$"`) on some retailers and an ISO code (`"USD"`) on others. A symbol butts against the number, a code takes a space.

## Answer honestly

**Say when the data was read.** "As of just now" or the timestamp. The whole point of calling the tool is that the answer is current, so make that visible.

**Never claim a product is unavailable because stock was absent.** Many retailers do not report it. Absent means unknown, so say "the retailer does not report stock" rather than "out of stock".

**If every field comes back empty, the URL did not resolve.** The Actor returns an item with no fields rather than an error. Say the URL looks wrong or the retailer is unsupported. Do not report it as a product with no price.

**Quote the source URL** so the user can check, and include the image URL when they asked to see the item.

## Cost

The Actor bills a start event per call plus per product returned, so:

- Cap with `maxProductResults`. Five is plenty for a comparison; one for a single lookup.
- Prefer one call with several URLs or marketplaces over several calls.
- Do not re-run to "check" a result you already have.

## Gotchas

- Stopping after the first tool call returns run metadata that reads like success and contains no products.
- Omitting `additionalProperties: true` silently drops stock, rating, list price, and identifiers.
- `rating: 0` and `stars: 0` mean absent, not a zero-star product.
- A `listPrice` above the current price is a genuine discount; report the percentage, it is usually what the user wanted.
- One Amazon product came back in about 10 seconds. Other retailers are slower by a wide margin, so for a multi-retailer comparison tell the user it will take a moment rather than going silent.
- `additionalProperties` can run to roughly 100 KB per product. Never paste it into a reply; read the fields you need.
- The `?tools=` parameter on the server URL narrows which Actors are visible. If the tool is absent, the connection may be scoped to other Actors.
