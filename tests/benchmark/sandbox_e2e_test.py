"""E2E concurrent sandbox test: Namespace (btrfs, persistent shell) vs Docker.

Simulates 32 concurrent workers each running a realistic agent workload
(multiple commands, file operations, resets) to test whether the persistent
shell optimization fixes the adl-slower-than-Docker issue at scale.

Usage (must run as root for agentdocker-lite backend):
  sudo ~/venv/bin/python ray/sandbox_e2e_test.py \
    --image "jefzda/sweap-images:ansible.ansible-ansible__ansible-f327e65d11bb905ed9f15996024f857a95592629-vba6da65a0f3baefda7a058ebbd0a8dcafb8512f5" \
    --workers 32 --rounds 3

Phases:
  1. Warmup: Pre-build rootfs cache (namespace) or pull image (Docker)
  2. Concurrent test: N workers each run a simulated agent session
  3. Report: Per-worker and aggregate timing comparison
"""

import argparse
import json
import logging
import os
import statistics
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(process)d] %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger("sandbox_e2e")

# Commands that simulate a realistic agent tool-call session.
# Mix of: file reads, searches, writes, test runs — typical SWE-bench pattern.
AGENT_COMMANDS = [
    # 1. Explore the repo
    "find /workspace -maxdepth 2 -type f | head -30",
    "cat /etc/os-release",
    # 2. Read source files
    "find /workspace -name '*.py' -o -name '*.js' | head -5 | xargs head -20 2>/dev/null || echo 'no source files'",
    # 3. Search for patterns
    "grep -r 'import' /workspace --include='*.py' -l 2>/dev/null | head -10 || echo 'no matches'",
    "grep -r 'def ' /workspace --include='*.py' -c 2>/dev/null | head -10 || echo 'no matches'",
    # 4. Write files
    "mkdir -p /workspace/test_dir && echo 'print(\"hello\")' > /workspace/test_dir/test.py",
    "echo 'line1\nline2\nline3' > /workspace/test_dir/data.txt",
    # 5. Run a command
    "python3 -c 'print(42 * 2)' 2>/dev/null || echo 'python not available'",
    # 6. More file operations
    "ls -la /workspace/test_dir/",
    "cat /workspace/test_dir/test.py",
    # 7. Simulate patch application
    "cp /workspace/test_dir/test.py /workspace/test_dir/test_backup.py && "
    "echo 'print(\"patched\")' >> /workspace/test_dir/test.py",
]


def _worker_adl(args: tuple) -> dict:
    """Single worker: create agentdocker-lite sandbox, run agent commands, reset, repeat."""
    image, worker_id, round_id = args
    from opensage.config.config_dataclass import ContainerConfig
    from opensage.sandbox.agentdocker_lite_sandbox import AgentDockerLiteSandbox

    cfg = ContainerConfig(
        image=image,
        working_dir="/workspace",
        timeout=300,
        extra={
            "fs_backend": "btrfs",
            "env_base_dir": "/data/opensage_ns",
            "rootfs_cache_dir": "/data/rootfs_cache",
        },
    )

    result = {
        "worker_id": worker_id,
        "round_id": round_id,
        "backend": "agentdocker-lite",
    }
    session_id = f"e2e_ns_w{worker_id}_r{round_id}"

    try:
        # Construct
        t0 = time.monotonic()
        sandbox = AgentDockerLiteSandbox(
            container_config=cfg,
            opensage_session_id=session_id,
            backend_type="agentdocker-lite",
            sandbox_type="main",
        )
        result["construct_ms"] = (time.monotonic() - t0) * 1000

        # Phase 1: Run agent commands
        cmd_times = []
        for cmd in AGENT_COMMANDS:
            t0 = time.monotonic()
            out, rc = sandbox.run_command_in_container(cmd)
            cmd_times.append((time.monotonic() - t0) * 1000)

        result["phase1_cmds"] = len(cmd_times)
        result["phase1_total_ms"] = sum(cmd_times)
        result["phase1_avg_cmd_ms"] = statistics.mean(cmd_times)
        result["phase1_median_cmd_ms"] = statistics.median(cmd_times)
        result["phase1_max_cmd_ms"] = max(cmd_times)

        # Reset
        t0 = time.monotonic()
        sandbox.reset_environment()
        result["reset_ms"] = (time.monotonic() - t0) * 1000

        # Phase 2: Run commands again (post-reset)
        cmd_times2 = []
        for cmd in AGENT_COMMANDS:
            t0 = time.monotonic()
            out, rc = sandbox.run_command_in_container(cmd)
            cmd_times2.append((time.monotonic() - t0) * 1000)

        result["phase2_total_ms"] = sum(cmd_times2)
        result["phase2_avg_cmd_ms"] = statistics.mean(cmd_times2)

        # Delete
        t0 = time.monotonic()
        sandbox.delete_container()
        result["delete_ms"] = (time.monotonic() - t0) * 1000

        # Total wall time
        result["total_ms"] = (
            result["construct_ms"]
            + result["phase1_total_ms"]
            + result["reset_ms"]
            + result["phase2_total_ms"]
            + result["delete_ms"]
        )
        result["status"] = "ok"

    except Exception as e:
        result["status"] = "error"
        result["error"] = str(e)
        logger.error("Worker ns w%d r%d failed: %s", worker_id, round_id, e)

    return result


def _worker_docker(args: tuple) -> dict:
    """Single worker: create Docker sandbox, run agent commands, delete."""
    image, worker_id, round_id = args
    from opensage.config.config_dataclass import ContainerConfig
    from opensage.sandbox.native_docker_sandbox import NativeDockerSandbox

    cfg = ContainerConfig(
        image=image,
        working_dir="/workspace",
        timeout=300,
        command="",
    )

    result = {"worker_id": worker_id, "round_id": round_id, "backend": "docker"}
    session_id = f"e2e_dk_w{worker_id}_r{round_id}_{os.getpid()}"

    try:
        # Construct
        t0 = time.monotonic()
        sandbox = NativeDockerSandbox(
            container_config=cfg,
            session_id=session_id,
            backend_type="native",
            sandbox_type="main",
        )
        result["construct_ms"] = (time.monotonic() - t0) * 1000

        # Phase 1: Run agent commands
        cmd_times = []
        for cmd in AGENT_COMMANDS:
            t0 = time.monotonic()
            out, rc = sandbox.run_command_in_container(cmd)
            cmd_times.append((time.monotonic() - t0) * 1000)

        result["phase1_cmds"] = len(cmd_times)
        result["phase1_total_ms"] = sum(cmd_times)
        result["phase1_avg_cmd_ms"] = statistics.mean(cmd_times)
        result["phase1_median_cmd_ms"] = statistics.median(cmd_times)
        result["phase1_max_cmd_ms"] = max(cmd_times)

        # Docker has no reset — just measure as N/A
        result["reset_ms"] = None

        # Phase 2: Run commands again (no reset, same container)
        cmd_times2 = []
        for cmd in AGENT_COMMANDS:
            t0 = time.monotonic()
            out, rc = sandbox.run_command_in_container(cmd)
            cmd_times2.append((time.monotonic() - t0) * 1000)

        result["phase2_total_ms"] = sum(cmd_times2)
        result["phase2_avg_cmd_ms"] = statistics.mean(cmd_times2)

        # Delete
        t0 = time.monotonic()
        sandbox.delete_container()
        result["delete_ms"] = (time.monotonic() - t0) * 1000

        # Total wall time
        result["total_ms"] = (
            result["construct_ms"]
            + result["phase1_total_ms"]
            + result["phase2_total_ms"]
            + result["delete_ms"]
        )
        result["status"] = "ok"

    except Exception as e:
        result["status"] = "error"
        result["error"] = str(e)
        logger.error("Worker docker w%d r%d failed: %s", worker_id, round_id, e)

    return result


def warmup_adl(image: str):
    """Pre-build rootfs cache so timing test doesn't include one-time cost."""
    from opensage.config.config_dataclass import ContainerConfig
    from opensage.sandbox.agentdocker_lite_sandbox import AgentDockerLiteSandbox

    logger.info("Warming up agentdocker-lite rootfs cache for image: %s", image)
    cfg = ContainerConfig(
        image=image,
        working_dir="/workspace",
        timeout=300,
        extra={
            "fs_backend": "btrfs",
            "env_base_dir": "/data/opensage_ns",
            "rootfs_cache_dir": "/data/rootfs_cache",
        },
    )
    t0 = time.monotonic()
    sandbox = AgentDockerLiteSandbox(
        container_config=cfg,
        opensage_session_id="warmup_ns",
        backend_type="agentdocker-lite",
        sandbox_type="main",
    )
    out, rc = sandbox.run_command_in_container("echo warmup_ok")
    sandbox.delete_container()
    elapsed = time.monotonic() - t0
    logger.info(
        "Namespace warmup done in %.1fs (output: %s, rc: %d)", elapsed, out.strip(), rc
    )


def warmup_docker(image: str):
    """Pre-pull Docker image so timing test doesn't include pull time."""
    import subprocess

    logger.info("Warming up Docker image: %s", image)
    t0 = time.monotonic()
    subprocess.run(["docker", "pull", image], capture_output=True, timeout=600)
    elapsed = time.monotonic() - t0
    logger.info("Docker warmup done in %.1fs", elapsed)


def run_concurrent_test(
    image: str, workers: int, rounds: int, backend: str
) -> list[dict]:
    """Run concurrent workers and collect results."""
    worker_fn = _worker_adl if backend == "agentdocker-lite" else _worker_docker
    all_results = []

    for r in range(rounds):
        logger.info(
            "=== %s Round %d/%d (%d workers) ===",
            backend.upper(),
            r + 1,
            rounds,
            workers,
        )
        tasks = [(image, w, r) for w in range(workers)]

        t_wall_start = time.monotonic()
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(worker_fn, t) for t in tasks]
            round_results = [f.result() for f in as_completed(futures)]
        t_wall = (time.monotonic() - t_wall_start) * 1000

        ok_results = [r for r in round_results if r["status"] == "ok"]
        err_count = len(round_results) - len(ok_results)

        logger.info(
            "%s round %d: %d ok, %d errors, wall=%.0fms",
            backend,
            r + 1,
            len(ok_results),
            err_count,
            t_wall,
        )

        for rr in round_results:
            rr["round_wall_ms"] = t_wall
        all_results.extend(round_results)

    return all_results


def print_summary(ns_results: list[dict], dk_results: list[dict]):
    """Print comparison summary."""
    ns_ok = [r for r in ns_results if r["status"] == "ok"]
    dk_ok = [r for r in dk_results if r["status"] == "ok"]

    def _stats(data: list[float]) -> str:
        if not data:
            return "N/A"
        return (
            f"avg={statistics.mean(data):7.1f}ms  "
            f"med={statistics.median(data):7.1f}ms  "
            f"p95={sorted(data)[int(len(data) * 0.95)]:7.1f}ms  "
            f"max={max(data):7.1f}ms"
        )

    print("\n" + "=" * 80)
    print("E2E CONCURRENT SANDBOX TEST RESULTS")
    print("=" * 80)

    metrics = [
        ("construct_ms", "Construct"),
        ("phase1_total_ms", "Phase1 (11 cmds)"),
        ("phase1_avg_cmd_ms", "  Avg cmd"),
        ("phase1_median_cmd_ms", "  Median cmd"),
        ("phase1_max_cmd_ms", "  Max cmd"),
        ("reset_ms", "Reset"),
        ("phase2_total_ms", "Phase2 (11 cmds)"),
        ("total_ms", "TOTAL per worker"),
    ]

    for key, label in metrics:
        print(f"\n{label}:")
        ns_vals = [r[key] for r in ns_ok if r.get(key) is not None]
        dk_vals = [r[key] for r in dk_ok if r.get(key) is not None]

        if ns_vals:
            print(f"  Namespace: {_stats(ns_vals)}")
        else:
            print(f"  Namespace: N/A")

        if dk_vals:
            print(f"  Docker:    {_stats(dk_vals)}")
        else:
            print(f"  Docker:    N/A")

        if ns_vals and dk_vals:
            ns_avg = statistics.mean(ns_vals)
            dk_avg = statistics.mean(dk_vals)
            if dk_avg > 0:
                ratio = dk_avg / ns_avg
                winner = "NS" if ns_avg < dk_avg else "Docker"
                print(
                    f"  -> {winner} faster by {abs(ratio - 1) * 100:.0f}% (ratio {ratio:.2f}x)"
                )

    # Wall clock per round
    print(
        f"\nWall clock per round (all {len(ns_ok) // max(1, len(set(r['round_id'] for r in ns_ok)))} workers):"
    )
    ns_walls = list(set(r["round_wall_ms"] for r in ns_ok))
    dk_walls = list(set(r["round_wall_ms"] for r in dk_ok))
    if ns_walls:
        print(f"  Namespace: {_stats(ns_walls)}")
    if dk_walls:
        print(f"  Docker:    {_stats(dk_walls)}")

    # Error rates
    ns_errs = len([r for r in ns_results if r["status"] == "error"])
    dk_errs = len([r for r in dk_results if r["status"] == "error"])
    print(
        f"\nErrors: Namespace={ns_errs}/{len(ns_results)}, Docker={dk_errs}/{len(dk_results)}"
    )
    print("=" * 80)


def main():
    parser = argparse.ArgumentParser(description="E2E concurrent sandbox benchmark")
    parser.add_argument("--image", required=True, help="Docker image to use")
    parser.add_argument(
        "--workers", type=int, default=32, help="Number of concurrent workers"
    )
    parser.add_argument(
        "--rounds", type=int, default=3, help="Number of rounds per backend"
    )
    parser.add_argument(
        "--backend",
        choices=["both", "agentdocker-lite", "docker"],
        default="both",
        help="Which backend(s) to test",
    )
    parser.add_argument("--skip-warmup", action="store_true", help="Skip warmup phase")
    args = parser.parse_args()

    logger.info(
        "Config: image=%s workers=%d rounds=%d backend=%s",
        args.image,
        args.workers,
        args.rounds,
        args.backend,
    )

    # Warmup
    if not args.skip_warmup:
        if args.backend in ("both", "agentdocker-lite"):
            warmup_adl(args.image)
        if args.backend in ("both", "docker"):
            warmup_docker(args.image)

    ns_results = []
    dk_results = []

    if args.backend in ("both", "agentdocker-lite"):
        ns_results = run_concurrent_test(
            args.image, args.workers, args.rounds, "agentdocker-lite"
        )

    if args.backend in ("both", "docker"):
        dk_results = run_concurrent_test(
            args.image, args.workers, args.rounds, "docker"
        )

    # Print summary
    print_summary(ns_results, dk_results)

    # Save raw results
    out_path = Path("/data/evals/e2e_concurrent_results.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(
            {
                "config": {
                    "image": args.image,
                    "workers": args.workers,
                    "rounds": args.rounds,
                },
                "agentdocker-lite": ns_results,
                "docker": dk_results,
            },
            indent=2,
        )
    )
    logger.info("Raw results saved to %s", out_path)


if __name__ == "__main__":
    main()
