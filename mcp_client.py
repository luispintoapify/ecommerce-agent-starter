"""Call E-commerce Scraping Tool the way an agent does: over the Apify MCP server.

This is the path the repo is about. An agent connected to ``mcp.apify.com`` does
exactly what happens below, so reading this tells you what your agent is doing.

**The flow is two tool calls, not one.** Calling the Actor's tool returns run
metadata (``runId``, status, stats, and the id of the dataset it wrote) plus a
text block telling the caller to fetch the items separately. The products come
back from a second call to ``get-dataset-items``. That is worth knowing before you
design a prompt around a single tool call.

The server exposes helper tools alongside the Actor even when the URL narrows the
tool list, precisely because the second call is required:

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

    Deliberately does not report the dataset's ``itemCount``. That number is not
    settled when the tool call returns: a run that produced one product reported
    ``itemCount: 0`` here while ``get-dataset-items`` then returned the product.
    Count what actually came back instead.
    """
    stats = meta.get("stats") or {}
    seconds = stats.get("runTimeSecs")
    bits = [str(meta.get("status", "?"))]
    if seconds:
        bits.append(f"Actor ran {float(seconds):.1f}s")
    if meta.get("runId"):
        bits.append(f"run {meta['runId']}")
    return "  ".join(bits)
