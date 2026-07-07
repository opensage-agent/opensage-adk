import argparse
import json
import shutil
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Tuple

from patchagent.agent.generator import agent_generator
from patchagent.task import PatchTask, ValidationResult

from patcheval_builder import PatchEvalBuilder, PatchEvalPoC


def read_jsonl(path: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def write_jsonl(path: str, rows: List[Dict[str, Any]]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def read_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def bool_arg(value: str) -> bool:
    return str(value).lower() in {"1", "true", "yes", "y"}


def run_cmd(command: List[str], cwd: Path | None = None) -> Tuple[int, str, str]:
    proc = subprocess.run(
        command,
        cwd=cwd.as_posix() if cwd else None,
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def repo_name_from_url(url: str) -> str:
    name = url.rstrip("/").split("/")[-1]
    if name.endswith(".git"):
        name = name[:-4]
    return name


def ensure_repo(local_repo_root: Path, repo_url: str, allow_clone: bool) -> Path:
    local_repo_root.mkdir(parents=True, exist_ok=True)
    repo_name = repo_name_from_url(repo_url)
    repo_path = local_repo_root / repo_name
    if not repo_path.exists():
        if not allow_clone:
            raise RuntimeError(f"repo not found and allow_clone=false: {repo_path}")
        code, out, err = run_cmd(["git", "clone", repo_url, repo_path.as_posix()])
        if code != 0:
            raise RuntimeError(f"git clone failed: {repo_url}\n{out}\n{err}")
    else:
        run_cmd(["git", "fetch", "--all", "--tags"], cwd=repo_path)
    return repo_path


def materialize_source(
    cve_id: str,
    repo_path: Path,
    commit: str,
    run_root: Path,
    repo_lock: threading.Lock,
) -> Path:
    sources_root = run_root / "sources"
    sources_root.mkdir(parents=True, exist_ok=True)
    source_path = sources_root / cve_id.lower()
    if source_path.exists():
        shutil.rmtree(source_path, ignore_errors=True)

    with repo_lock:
        code, out, err = run_cmd(
            ["git", "worktree", "add", "--detach", source_path.as_posix(), commit],
            cwd=repo_path,
        )
    if code != 0:
        raise RuntimeError(f"git worktree add failed for {cve_id}@{commit}\n{out}\n{err}")
    return source_path


def remove_worktree(repo_path: Path, source_path: Path, repo_lock: threading.Lock) -> None:
    with repo_lock:
        run_cmd(["git", "worktree", "remove", "--force", source_path.as_posix()], cwd=repo_path)
        run_cmd(["git", "worktree", "prune"], cwd=repo_path)


def build_input_map(input_file: str) -> Dict[str, Dict[str, Any]]:
    infos = read_json(input_file)
    return {item["cve_id"]: item for item in infos}


def summarize_contexts(task: PatchTask) -> Dict[str, Any]:
    tool_calls = 0
    llm_messages = 0
    for ctx in task.contexts:
        for msg in ctx.messages:
            if msg.get("role") == "tool":
                tool_calls += 1
            if msg.get("role") == "llm":
                llm_messages += 1
    return {
        "tool_calls": tool_calls,
        "llm_messages": llm_messages,
    }


def run_single_native(
    row: Dict[str, Any],
    cve_info: Dict[str, Any],
    model: str,
    run_root: Path,
    local_repo_root: Path,
    allow_clone: bool,
    fast: bool,
    rounds: int,
    repo_lock_map: Dict[str, threading.Lock],
) -> Dict[str, Any]:
    cve_id = row["cve_id"]
    repo_url = cve_info["repo"]
    language = cve_info.get("programming_language", "Unknown")
    vul_funcs = cve_info.get("vul_func", [])
    commit = vul_funcs[0]["commit"] if vul_funcs else "HEAD"

    repo_path = ensure_repo(local_repo_root=local_repo_root, repo_url=repo_url, allow_clone=allow_clone)
    lock_key = repo_path.as_posix()
    if lock_key not in repo_lock_map:
        repo_lock_map[lock_key] = threading.Lock()
    repo_lock = repo_lock_map[lock_key]
    source_path = materialize_source(
        cve_id=cve_id,
        repo_path=repo_path,
        commit=commit,
        run_root=run_root,
        repo_lock=repo_lock,
    )

    patch = ""
    error = ""
    init_status = ""
    init_msg = ""
    validate_status = ""
    validate_msg = ""
    meta: Dict[str, Any] = {}
    round_details: List[Dict[str, Any]] = []
    feedback = ""
    solved = False

    try:
        for round_idx in range(1, max(1, rounds) + 1):
            augmented_problem = row.get("problem_statement", "")
            if feedback.strip():
                augmented_problem += (
                    "\n\n# Previous Round Runtime Feedback\n"
                    "Use the following runtime validation result to improve your next patch.\n"
                    f"{feedback}\n"
                )

            builder = PatchEvalBuilder(
                cve_id=cve_id,
                image_name=row.get("image_name", ""),
                work_dir=row.get("work_dir", ""),
                language=language,
                problem_statement=augmented_problem,
                source_path=source_path,
                workspace=run_root / "builder_workspace" / cve_id.lower() / f"round_{round_idx}",
                clean_up=True,
            )
            task = PatchTask(
                pocs=[PatchEvalPoC()],
                builder=builder,
                log_file=run_root / "logs" / f"{cve_id.lower()}_round{round_idx}.json",
            )

            init_ret, init_text = task.initialize()
            init_status = init_ret.value
            init_msg = init_text

            current_patch = ""
            current_validate_status = ""
            current_validate_msg = ""
            current_error = ""
            if init_ret != ValidationResult.BugDetected:
                current_error = f"initialize_failed: {init_ret.value}"
            else:
                current_patch = task.repair(agent_generator(model=model, fast=fast)) or ""
                if current_patch:
                    ret, msg = task.validate(current_patch)
                    current_validate_status = ret.value
                    current_validate_msg = msg
                    if ret == ValidationResult.BugFree:
                        solved = True
                else:
                    current_validate_status = "NoPatch"
                    current_validate_msg = "agent returned empty patch"

            # choose best-so-far patch: prefer solved patch else latest non-empty
            if current_patch:
                patch = current_patch
            validate_status = current_validate_status or validate_status
            validate_msg = current_validate_msg or validate_msg
            if current_error:
                error = current_error

            ctx_meta = summarize_contexts(task)
            meta["tool_calls"] = int(meta.get("tool_calls", 0)) + int(ctx_meta.get("tool_calls", 0))
            meta["llm_messages"] = int(meta.get("llm_messages", 0)) + int(ctx_meta.get("llm_messages", 0))

            round_entry = {
                "round": round_idx,
                "init_status": init_status,
                "init_msg": init_msg,
                "validate_status": current_validate_status,
                "validate_msg": current_validate_msg,
                "patch_len": len(current_patch),
                "error": current_error,
                "meta": ctx_meta,
            }
            round_details.append(round_entry)

            if solved:
                error = ""
                break

            if current_validate_msg.strip():
                feedback = current_validate_msg
            elif current_error.strip():
                feedback = current_error
            elif init_msg.strip():
                feedback = init_msg
    except Exception as e:
        error = str(e)
    finally:
        remove_worktree(repo_path=repo_path, source_path=source_path, repo_lock=repo_lock)

    return {
        "cve_id": cve_id,
        "image_name": row.get("image_name", ""),
        "work_dir": row.get("work_dir", ""),
        "model": model,
        "patch": patch,
        "error": error,
        "init_status": init_status,
        "init_msg": init_msg,
        "validate_status": validate_status,
        "validate_msg": validate_msg,
        "meta": meta,
        "rounds_used": len(round_details),
        "solved": solved,
        "round_details": round_details,
        "token_stat": {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset_path", type=str, default="../sweagent/dataset.jsonl")
    parser.add_argument("--output_dir", type=str, required=True)
    parser.add_argument("--model", type=str, required=True)
    parser.add_argument("--max_workers", type=int, default=4)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--input_file", type=str, default="../../datasets/input.json")
    parser.add_argument("--local_repo_path", type=str, default="../../exp_llm/projects")
    parser.add_argument("--allow_clone", type=bool_arg, default=False)
    parser.add_argument("--fast", type=bool_arg, default=False)
    parser.add_argument("--rounds", type=int, default=1)
    args = parser.parse_args()

    dataset = read_jsonl(args.dataset_path)
    if args.limit > 0:
        dataset = dataset[: args.limit]

    # Use absolute path so git worktree/materialized sources are created in a stable location.
    output_dir = Path(args.output_dir).resolve()
    patches_dir = output_dir / "patches"
    output_dir.mkdir(parents=True, exist_ok=True)
    patches_dir.mkdir(parents=True, exist_ok=True)
    run_root = output_dir / ".native_run"
    run_root.mkdir(parents=True, exist_ok=True)
    (run_root / "logs").mkdir(parents=True, exist_ok=True)

    input_map = build_input_map(args.input_file)
    local_repo_root = Path(args.local_repo_path).resolve()
    repo_lock_map: Dict[str, threading.Lock] = {}
    results: List[Dict[str, Any]] = []
    lock = threading.Lock()

    with ThreadPoolExecutor(max_workers=max(1, args.max_workers)) as executor:
        futures = {}
        for row in dataset:
            cve_id = row["cve_id"]
            cve_info = input_map.get(cve_id)
            if cve_info is None:
                # Keep consistent output structure for missing metadata.
                results.append(
                    {
                        "cve_id": cve_id,
                        "model": args.model,
                        "patch": "",
                        "error": f"cve_not_found_in_input_file: {cve_id}",
                        "token_stat": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                        "meta": {"tool_calls": 0, "llm_messages": 0},
                    }
                )
                continue
            futures[
                executor.submit(
                    run_single_native,
                    row,
                    cve_info,
                    args.model,
                    run_root,
                    local_repo_root,
                    args.allow_clone,
                    args.fast,
                    args.rounds,
                    repo_lock_map,
                )
            ] = cve_id

        for future in as_completed(futures):
            result = future.result()
            cve = result["cve_id"]
            patch_text = result.get("patch", "")
            (patches_dir / f"{cve}.patch").write_text(patch_text, encoding="utf-8")
            with lock:
                results.append(result)
            print(
                f"[done] {cve} patch_len={len(patch_text)} init={result.get('init_status','')} "
                f"validate={result.get('validate_status','')} error={bool(result.get('error'))}"
            )

    results.sort(key=lambda x: x["cve_id"])
    write_jsonl((output_dir / "raw_outputs.jsonl").as_posix(), results)
    print(f"Saved outputs to: {output_dir / 'raw_outputs.jsonl'}")


if __name__ == "__main__":
    main()
