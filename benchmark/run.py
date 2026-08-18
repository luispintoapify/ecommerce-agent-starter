"""Run the benchmark arms over the frozen question set and write results.jsonl.

    python run.py --arms apify                  # the arm you have keys for
    python run.py --arms apify,browsing,diy     # all three
    python score.py

Each arm is independent: one arm missing a key or a dependency does not stop the
others, and its absence shows up in the table rather than as a crash.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from harness import load_questions

ARMS = ("apify", "browsing", "diy")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--arms", default="apify", help=f"comma-separated: {','.join(ARMS)}")
    ap.add_argument("--out", default="results.jsonl")
    ap.add_argument("--limit", type=int, default=0, help="run only the first N questions")
    args = ap.parse_args()

    wanted = [a.strip() for a in args.arms.split(",") if a.strip()]
    unknown = [a for a in wanted if a not in ARMS]
    if unknown:
        raise SystemExit(f"unknown arm(s): {unknown}. choose from {ARMS}")

    questions = load_questions()
    if args.limit:
        questions = questions[: args.limit]
    print(f"{len(questions)} questions, arms: {', '.join(wanted)}")

    results = []
    if "apify" in wanted:
        import arm_apify
        print("running apify_mcp ...")
        results += asyncio.run(arm_apify.run_all(questions))
    if "browsing" in wanted:
        import arm_browsing
        print("running native_browsing ...")
        results += arm_browsing.run_all(questions)
    if "diy" in wanted:
        import arm_playwright
        print("running diy_playwright ...")
        results += arm_playwright.run_all(questions)

    out = Path(args.out)
    out.write_text("\n".join(r.to_json() for r in results) + "\n")
    print(f"wrote {len(results)} results to {out}. now run: python score.py")


if __name__ == "__main__":
    main()
