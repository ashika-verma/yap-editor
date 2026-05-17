#!/usr/bin/env python3
"""
Run the LLM-as-judge on a live edit plan (called from /api/judge).
Reads {"plan": dict} from a tmp JSON file (path passed as argv[1]).
Outputs judge result JSON to stdout.
"""
from __future__ import annotations
import json, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from eval_judge import judge_plan, JUDGE_SCHEMA


def main() -> None:
    if len(sys.argv) < 2:
        print(json.dumps({"error": "Usage: judge_plan.py <payload.json>"}))
        sys.exit(1)

    with open(sys.argv[1], encoding="utf-8") as f:
        payload = json.load(f)

    plan    = payload.get("plan") or {}
    api_key = os.environ.get("GEMINI_API_KEY", "")

    result = judge_plan(plan, api_key, strict=True)
    sys.stdout.write(json.dumps(result) + "\n")


if __name__ == "__main__":
    main()
