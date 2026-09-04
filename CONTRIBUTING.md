# Contributing

Issues and pull requests are welcome. The most useful contribution is a retailer
whose output this code mishandles, because that becomes a test fixture.

## Before you open a pull request

```bash
pip install -r requirements-dev.txt
pytest -q
python -m compileall -q apify_products.py mcp_client.py rag_refresh.py runtime_call.py benchmark/
```

No Apify token is needed for either. The suite runs against captured Actor output
in `tests/fixtures/`, so it spends no credits and does not flake when a retailer
is slow.

Run `compileall` as well as `pytest`. The suite does not import the benchmark
arms or `harness.py`, so a syntax error in `benchmark/` can pass the tests. CI
runs both, and this is the check that catches it first.

## Fixtures are recordings, not fiction

Everything in `tests/fixtures/` is real Actor output, captured from a live run.
Every quirk the suite asserts was found by running the Actor, not by imagining
what it might return.

If a retailer changes shape and a test fails, recapture the fixture. Do not
loosen the assertion. A test that passes because it stopped checking is worse
than no test.

## The benchmark questions are frozen

`benchmark/questions.json` was frozen on 2026-08-18, before any arm ran. The
twenty questions and their expected product identifiers do not change to fit a
result. If a question is genuinely wrong, add a `questions-v1.1.json` and say in
the pull request what was wrong and why the change is not motivated by an
outcome.

`score.py` computes every number from the recorded results, so the same
`results.jsonl` always produces the same table. It deliberately does not
adjudicate whether a price is correct, only whether an answer was given, in the
right currency, about the product that was asked for. Keep it that way. Judging
price truth needs a source of truth this repo does not have.

Each arm records its own cost from what the provider actually billed, not from
arithmetic over published rates. An earlier version of the Apify arm estimated,
and was wrong by 1.7x in one direction and 3.3x in the other. If you add an arm,
read the real charge or leave the field empty.

## Adding a retailer quirk

1. Run the Actor against the URL and keep the raw dataset item.
2. Add it under `tests/fixtures/` with a name that says what is odd about it.
3. Write the assertion that fails without your change to `apify_products.py`.
4. Open the pull request with the raw item in the description.

## Scope

This is a starter, not a framework. It stays small enough to read in one sitting.
Things that fit: retailer field quirks, a normalization bug, a transport error
that surfaces badly, another agent runtime in the connect section, a benchmark
arm with its real cost. Things that do not: a plugin system, a web UI, or a
dependency that has to be installed for the tests to run.

## Style

Match the file you are editing. Comments explain why a line exists, not what it
does, and the ones that carry a measured number keep it.
