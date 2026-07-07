import json
import os
import sys


def main():
    prefix = sys.argv[1] if len(sys.argv) > 1 else ""
    if not prefix:
        print("Usage: python extract_token.py <prefix>")
        sys.exit(1)

    data_path = f"./outputs/{prefix}/raw_outputs.jsonl"
    if not os.path.exists(data_path):
        print(f"File not found: {data_path}")
        sys.exit(1)

    results = {}
    with open(data_path, "r", encoding="utf-8") as f:
        for line in f:
            row = json.loads(line)
            cve = row["cve_id"]
            ts = row.get("token_stat", {})
            results[cve] = {
                "input_token": int(ts.get("prompt_tokens", 0) or 0),
                "output_token": int(ts.get("completion_tokens", 0) or 0),
                "tool_calls": 0,
            }

    out_json = f"./outputs/{prefix}/token_results.json"
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    n = max(1, len(results))
    summary = {
        "avg_input_tokens_million": (sum(v["input_token"] for v in results.values()) / n) / 1_000_000,
        "avg_output_tokens_million": (sum(v["output_token"] for v in results.values()) / n) / 1_000_000,
        "avg_tool_calls": 0,
        "count": len(results),
    }
    print(summary)


if __name__ == "__main__":
    main()
