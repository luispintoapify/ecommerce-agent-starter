# Prompt library

Copy-paste system prompts for agents that answer product questions with live retailer
data instead of training data.

Every rule in these prompts came from running Apify's
[E-commerce Scraping Tool](https://apify.com/apify/e-commerce-scraping-tool) against
real retailer pages and reading what came back. Where a prompt tells the agent to
handle two shapes of a field, it is because two retailers returned two shapes.

## Which prompt do I want

| File | The agent's job | Reach for it when |
|---|---|---|
| [shopping-agent.md](shopping-agent.md) | Help someone choose and buy | The agent recommends, compares, and quotes prices the user will act on |
| [support-agent.md](support-agent.md) | Help someone who already bought, or is asking about a catalog you sell | The agent answers about specs, availability, delivery, and price matching |

The difference matters more than it looks. A shopping agent is rewarded for a
confident recommendation. A support agent that guesses creates a promise the business
has to honor, so its prompt spends most of its length on what not to claim.

## How to use them

1. Connect the agent to the Apify MCP server. Setup per client is in
   [../skills/apify-product-data-setup/references/clients.md](../skills/apify-product-data-setup/references/clients.md).
2. Paste the system prompt into your agent's system or developer message.
3. Replace the bracketed placeholders. Both prompts have a short list at the top of
   the file.
4. Run the worked examples at the bottom of each file as your first test. They cover
   the three failure modes that show up first: stopping before the products arrive,
   reading a projected response with nested keys, and reporting an unreported stock
   field as out of stock.

## If you are writing your own prompt instead

Four things are worth carrying over, because an agent that misses any one of them
returns a confident wrong answer rather than an error:

- **Fetching products takes up to three tool calls.** The Actor tool hands back run
  metadata, not products. A `RUNNING` status is normal and means the dataset is still
  empty.
- **`additionalProperties: true` is not optional.** Stock, rating, list price, and
  identifiers are nested there. Omit it and they are absent, not empty.
- **Projecting with `fields` flattens the response** into dotted keys such as
  `offers.price`. Walking the nested path then finds nothing and looks exactly like
  missing data.
- **Absent is not false.** Many retailers do not report stock. An agent that reads a
  missing field as "out of stock" invents facts about inventory.

The full field map, with the per-retailer differences, is in
[../skills/apify-product-lookup/references/fields.md](../skills/apify-product-lookup/references/fields.md).
