## What this changes

<!-- One or two sentences. If it fixes a retailer quirk, name the retailer. -->

## Checks

- [ ] `pytest -q` passes, with no Apify token set
- [ ] `python -m compileall -q apify_products.py mcp_client.py rag_refresh.py runtime_call.py benchmark/` is clean
- [ ] `benchmark/questions.json` is unchanged, or the description says what was wrong with a question
- [ ] No token, key, or `.env` file in the diff

## Raw output

<!-- For a retailer fix, paste the raw dataset item that motivated it. Redact
     nothing except your own token, which should not appear in it anyway. -->
