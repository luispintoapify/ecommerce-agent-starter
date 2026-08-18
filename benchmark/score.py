"""Score results.jsonl into a table, and flag where the arms disagree.

Run after run.py. Every number here is computed from the recorded results, so the
same results.jsonl always produces the same table.
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from pathlib import Path

from harness import Result, load_questions, score_one


def read(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--results", default="results.jsonl")
    ap.add_argument("--json", action="store_true", help="emit the summary as JSON")
    args = ap.parse_args()

    qs = {q["id"]: q for q in load_questions()}
    rows = []
    for raw in read(Path(args.results)):
        q = qs.get(raw["qid"])
        if not q:
            continue
        rows.append(score_one(q, Result(**{k: v for k, v in raw.items() if k in Result.__dataclass_fields__})))

    by_arm: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_arm[row["arm"]].append(row)

    summary = {}
    for arm, rs in by_arm.items():
        n = len(rs)
        lat = [r["latency_ms"] for r in rs if r["latency_ms"]]
        summary[arm] = {
            "questions": n,
            "usable": sum(1 for r in rs if r["usable"]),
            "usable_pct": round(100 * sum(1 for r in rs if r["usable"]) / n, 1) if n else 0,
            "gave_price": sum(1 for r in rs if r["gave_price"]),
            "gave_url": sum(1 for r in rs if r["gave_url"]),
            "wrong_product": sum(1 for r in rs if r["right_product"] is False),
            "over_budget": sum(1 for r in rs if r["in_budget"] is False),
            "stock_dishonest": sum(1 for r in rs if r["stock_honest"] is False),
            "blocked": sum(1 for r in rs if r["blocked"]),
            "errored": sum(1 for r in rs if r["error"]),
            "latency_p50_ms": int(statistics.median(lat)) if lat else None,
            "latency_max_ms": max(lat) if lat else None,
            "cost_usd_total": round(sum(r["cost_usd"] for r in rs), 4),
            "tokens_total": sum(r["tokens_in"] + r["tokens_out"] for r in rs),
        }

    if args.json:
        print(json.dumps({"summary": summary, "rows": rows}, indent=2))
        return

    cols = [
        ("arm", 16), ("usable", 8), ("price", 6), ("url", 5), ("wrong", 6),
        ("budget", 7), ("stock", 6), ("block", 6), ("err", 4),
        ("p50 ms", 8), ("max ms", 8), ("$ total", 9),
    ]
    print("  ".join(h.ljust(w) for h, w in cols))
    print("  ".join("-" * w for _, w in cols))
    for arm, s in sorted(summary.items(), key=lambda kv: -kv[1]["usable_pct"]):
        vals = [
            arm, f"{s['usable']}/{s['questions']}", str(s["gave_price"]), str(s["gave_url"]),
            str(s["wrong_product"]), str(s["over_budget"]), str(s["stock_dishonest"]),
            str(s["blocked"]), str(s["errored"]),
            str(s["latency_p50_ms"] or "-"), str(s["latency_max_ms"] or "-"),
            f"${s['cost_usd_total']:.4f}",
        ]
        print("  ".join(v.ljust(w) for v, (_, w) in zip(vals, cols)))

    print("\nColumns: usable = gave a price, a resolvable link to the product asked about,")
    print("and stayed inside any stated budget. wrong = linked a different product.")
    print("stock = asserted out of stock on a question where absence was the honest answer.")

    # Cross-arm disagreement: the same question priced differently by two arms is
    # either a real price change between calls or one arm being wrong. Neither is
    # something the automated scorer can adjudicate, so it is flagged for a human.
    prices: dict[str, dict[str, float]] = defaultdict(dict)
    for row in rows:
        if row["prices_seen"]:
            prices[row["qid"]][row["arm"]] = row["prices_seen"][0]
    disagreements = {
        qid: seen for qid, seen in prices.items()
        if len(seen) > 1 and (max(seen.values()) - min(seen.values())) > 0.01
    }
    if disagreements:
        print(f"\nPrice disagreements needing a human look ({len(disagreements)}):")
        for qid, seen in sorted(disagreements.items()):
            print(f"  {qid}: " + ", ".join(f"{a}={v}" for a, v in sorted(seen.items())))
    else:
        print("\nNo cross-arm price disagreements.")

    print("\nThis scorer does not adjudicate absolute price truth. For an accuracy claim,")
    print("record the true price per question by hand at run time and compare against it.")


if __name__ == "__main__":
    main()
