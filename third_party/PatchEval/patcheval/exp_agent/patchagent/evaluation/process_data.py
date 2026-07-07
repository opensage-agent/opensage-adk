import argparse
import json
import os
from pathlib import Path

import utils


def main():
    test_data = json.load(open(args.test_data_path, "r", encoding="utf-8"))
    cve2language = {it["cve_id"]: it["programming_language"] for it in test_data}

    dataset = utils.load_jsonl_file(args.dataset_path)
    if args.limit > 0:
        dataset = dataset[: args.limit]
    process_data = []
    missing = []

    for data in dataset:
        cve_id = data["cve_id"]
        patch_path = f"{args.output_dir}/{cve_id}.patch"
        if not os.path.exists(patch_path) or os.path.isdir(patch_path):
            fix_patch = ""
            missing.append(cve_id)
        else:
            with open(patch_path, "r", encoding="utf-8") as f:
                fix_patch = f.read()

        process_data.append(
            {
                "cve": cve_id.upper(),
                "language": cve2language.get(cve_id.upper(), cve2language.get(cve_id, "")),
                "fix_patch": fix_patch,
            }
        )

    Path(args.process_data_path).parent.mkdir(parents=True, exist_ok=True)
    utils.write_jsonl(process_data, args.process_data_path)
    print(f"missing_patch_count={len(missing)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--process_data_path", type=str, required=True)
    parser.add_argument("--dataset_path", type=str, default="../sweagent/dataset.jsonl")
    parser.add_argument("--output_dir", type=str, required=True)
    parser.add_argument("--test_data_path", type=str, required=True)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()
    main()
