# Security

## Your Apify token

The scripts read `APIFY_TOKEN` from the environment or from a local `.env`.
`.env` is in `.gitignore` and must stay there.

No test needs a token. `pytest -q` runs entirely against captured output in
`tests/fixtures/`, so you can run and change everything here without a
credential present.

If you paste a token anywhere public, including a GitHub issue, treat it as
compromised and revoke it in the Apify console under Settings, Integrations.
Rotating it is free and immediate.

Do not put a token in a fork's workflow file. `.github/workflows/refresh.yml`
expects it as a repository secret, and its `schedule` trigger ships commented
out so a fork does not start spending Actor credits on a cron nobody asked for.

## Reporting a vulnerability

Use GitHub's private vulnerability reporting on this repository, under the
Security tab. That opens a report only the maintainer can read.

Please do not open a public issue for anything that would expose a credential or
a way to spend someone else's Actor credits.

## Scope

This repository is client code. It calls the Apify platform over HTTPS and holds
no server of its own, so the surface is the token, the local `.env`, and whatever
a workflow in a fork is configured to do.

For a vulnerability in the Apify platform or in an Actor rather than in this
starter, report it to Apify directly at security@apify.com. Their disclosure
policy is at https://docs.apify.com/security#vulnerability-disclosure-policy.
