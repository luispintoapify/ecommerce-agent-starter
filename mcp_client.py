"""Call E-commerce Scraping Tool the way an agent does: over the Apify MCP server.

This is the path the repo is about. An agent connected to ``mcp.apify.com`` does
exactly what happens below, so reading this tells you what your agent is doing.

**The flow is two calls, and sometimes three.** Calling the Actor's tool returns run
metadata (``runId``, status, and the id of the dataset it wrote) plus a text block
telling the caller to fetch the items separately. The products come back from a
separate call to ``get-dataset-items``.

The third call is the one that catches people. The Actor tool returns when its own
wait window elapses, not when the run finishes, so ``status`` can come back
``RUNNING`` with a dataset that exists and holds nothing. Observed live: the same
product URL returned ``SUCCEEDED`` in 10 seconds on one call and ``RUNNING`` on
another that took 40 seconds to finish. Poll ``get-actor-run`` until the status is
terminal before fetching, or the fetch quietly returns an empty list.

``get-actor-run`` returns the same ``storages.datasets.default.id`` shape as the
Actor tool, so the polled metadata is a drop-in replacement for it.

The server exposes helper tools alongside the Actor even when the URL narrows the
tool list, precisely because the Actor tool alone cannot deliver products:

    get-actor-run, get-dataset-items, get-key-value-store-record,
    abort-actor-run, apify--e-commerce-scraping-tool

Note the tool name uses two hyphens where the Actor id uses a slash.

Verified against ``apify-mcp-server`` 0.14.3 with the ``mcp`` 2.x SDK, whose API
is snake_case (``server_info``, ``is_error``).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from apify_products import MCP_TOOL_NAME, MCP_URL, Product, api_token, normalize_all

DATASET_TOOL = "get-dataset-items"
RUN_TOOL = "get-actor-run"

#: Statuses that mean the run has stopped, successfully or otherwise.
TERMINAL = {"SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT", "TIMED_OUT"}

#: How many times to poll before giving up. Each poll blocks server-side for
#: ``POLL_WAIT_SECS``, so this is roughly the patience in seconds divided by that.
MAX_POLLS = 10
POLL_WAIT_SECS = 30


class McpError(RuntimeError):
    """A tool call came back as an error, or without the data we needed."""


def _first_json_block(result: Any) -> Any:
    """Parse the first text block of a tool result as JSON.

    Tool results carry several content blocks: the first is JSON for programs, and
    a later one is prose written for a model to read. Only the first is parsed.
    """
    for block in getattr(result, "content", []) or []:
        text = getattr(block, "text", None)
        if not text:
            continue
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            continue
    return None


def _dataset_id(meta: Any) -> str:
    try:
        return str(meta["storages"]["datasets"]["default"]["id"])
    except (KeyError, TypeError):
        raise McpError(
            "The Actor tool returned no dataset id, so there is nothing to fetch. "
            "Inspect the raw result to see what the server said."
        )


def _items_from(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [i for i in payload if isinstance(i, dict)]
    if isinstance(payload, dict):
        inner = payload.get("items")
        if isinstance(inner, list):
            return [i for i in inner if isinstance(i, dict)]
        return [payload]
    return []


async def fetch_products(
    actor_input: dict[str, Any],
    limit: int = 5,
    url: str = MCP_URL,
) -> tuple[list[Product], dict[str, Any]]:
    """Run the Actor over MCP and return normalized products plus run metadata.

    ``limit`` bounds the second call, not the Actor: cap the run itself with
    ``maxProductResults`` in ``actor_input``, since that is what stops the Actor
    billing for products you then throw away.
    """
    import httpx
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    headers = {"Authorization": f"Bearer {api_token()}"}
    async with httpx.AsyncClient(headers=headers, timeout=300, follow_redirects=True) as http:
        async with streamable_http_client(url, http_client=http) as streams:
            read_stream, write_stream = streams[0], streams[1]
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()

                run = await session.call_tool(MCP_TOOL_NAME, actor_input)
                if run.is_error:
                    raise McpError(f"{MCP_TOOL_NAME} failed: {_error_text(run)}")

                meta = _first_json_block(run)
                if not isinstance(meta, dict):
                    raise McpError("Could not parse the Actor tool result as JSON.")

                # The Actor tool returns when its wait window elapses, not when the
                # run ends, so poll before fetching. Skipping this returns an empty
                # list from a dataset that simply has not been written yet.
                polls = 0
                while str(meta.get("status", "")).upper() not in TERMINAL:
                    if polls >= MAX_POLLS:
                        raise McpError(
                            f"Run {meta.get('runId')} was still "
                            f"{meta.get('status')} after {polls} polls. It may still "
                            "finish; check the run in Apify Console."
                        )
                    polls += 1
                    polled = await session.call_tool(
                        RUN_TOOL,
                        {"runId": meta.get("runId"), "waitSecs": POLL_WAIT_SECS},
                    )
                    if polled.is_error:
                        raise McpError(f"{RUN_TOOL} failed: {_error_text(polled)}")
                    updated = _first_json_block(polled)
                    if not isinstance(updated, dict):
                        raise McpError(f"Could not parse the {RUN_TOOL} result as JSON.")
                    meta = updated

                if str(meta.get("status", "")).upper() != "SUCCEEDED":
                    raise McpError(
                        f"Run finished as {meta.get('status')}, so there is no output "
                        "to read."
                    )

                dataset_id = _dataset_id(meta)
                fetched = await session.call_tool(
                    DATASET_TOOL, {"datasetId": dataset_id, "limit": limit}
                )
                if fetched.is_error:
                    raise McpError(f"{DATASET_TOOL} failed: {_error_text(fetched)}")

                items = _items_from(_first_json_block(fetched))

    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return normalize_all(items, fetched_at), meta


def _error_text(result: Any) -> str:
    parts = []
    for block in getattr(result, "content", []) or []:
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
    return " ".join(parts)[:300] or "no detail returned"


def run_summary(meta: dict[str, Any]) -> str:
    """One line about the run, for printing next to the results.

    Deliberately does not report the dataset's ``itemCount``. It is only meaningful
    once the run is terminal: a run still ``RUNNING`` reported ``itemCount: 0`` for a
    dataset that ended up holding a product. Since ``fetch_products`` polls to
    ``SUCCEEDED`` the number would be correct here, but counting what actually came
    back stays right even if the caller changes the flow.
    """
    stats = meta.get("stats") or {}
    seconds = stats.get("runTimeSecs")
    bits = [str(meta.get("status", "?"))]
    if seconds:
        bits.append(f"Actor ran {float(seconds):.1f}s")
    if meta.get("runId"):
        bits.append(f"run {meta['runId']}")
    return "  ".join(bits)
