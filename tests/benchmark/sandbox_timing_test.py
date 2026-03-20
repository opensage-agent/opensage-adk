"""Sandbox timing comparison: AgentDocker-Lite (btrfs) vs AgentDocker-Lite (overlayfs) vs Docker.

Exercises the sandbox lifecycle without any LLM calls:
  construct (mount/snapshot) -> run_command -> reset -> run_command -> delete

Usage (must run as root for agentdocker-lite backend):
  sudo ~/venv/bin/python ray/sandbox_timing_test.py \
    --image "jefzda/sweap-images:nodebb.nodebb-NodeBB__NodeBB-04998908ba6721d64eba79ae3b65a351dcfbc5b5" \
    --rounds 3
"""

import argparse
import json
import logging
import os
import time
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger("sandbox_timing")


def _time_adl(image: str, round_idx: int) -> dict:
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

    timings = {}
    session_id = f"ns_timing_{round_idx}"

    t0 = time.monotonic()
    sandbox = AgentDockerLiteSandbox(
        container_config=cfg,
        opensage_session_id=session_id,
        backend_type="agentdocker-lite",
        sandbox_type="main",
    )
    timings["construct_ms"] = (time.monotonic() - t0) * 1000

    t0 = time.monotonic()
    out, rc = sandbox.run_command_in_container(
        "echo hello && ls /workspace && head -3 /etc/os-release"
    )
    timings["exec1_ms"] = (time.monotonic() - t0) * 1000
    logger.info("[ns r%d] exec1 (rc=%d): %s", round_idx, rc, out[:200])

    t0 = time.monotonic()
    sandbox.run_command_in_container(
        "touch /workspace/testfile && echo modified > /tmp/marker"
    )
    timings["exec_write_ms"] = (time.monotonic() - t0) * 1000

    t0 = time.monotonic()
    sandbox.reset_environment()
    timings["reset_ms"] = (time.monotonic() - t0) * 1000

    t0 = time.monotonic()
    out, rc = sandbox.run_command_in_container(
        "ls /workspace/testfile 2>&1; cat /tmp/marker 2>&1"
    )
    timings["exec_after_reset_ms"] = (time.monotonic() - t0) * 1000
    logger.info("[ns r%d] after reset (rc=%d): %s", round_idx, rc, out[:200])

    t0 = time.monotonic()
    sandbox.delete_container()
    timings["delete_ms"] = (time.monotonic() - t0) * 1000

    return timings


def _time_adl_overlayfs(image: str, round_idx: int) -> dict:
    from opensage.config.config_dataclass import ContainerConfig
    from opensage.sandbox.agentdocker_lite_sandbox import AgentDockerLiteSandbox

    cfg = ContainerConfig(
        image=image,
        working_dir="/workspace",
        timeout=300,
        extra={
            "fs_backend": "overlayfs",
            "env_base_dir": "/data/opensage_ns_ovl",
            "rootfs_cache_dir": "/data/rootfs_cache_overlayfs",
        },
    )

    timings = {}
    session_id = f"ns_ovl_timing_{round_idx}"

    t0 = time.monotonic()
    sandbox = AgentDockerLiteSandbox(
        container_config=cfg,
        opensage_session_id=session_id,
        backend_type="agentdocker-lite",
        sandbox_type="main",
    )
    timings["construct_ms"] = (time.monotonic() - t0) * 1000

    t0 = time.monotonic()
    out, rc = sandbox.run_command_in_container(
        "echo hello && ls /workspace && head -3 /etc/os-release"
    )
    timings["exec1_ms"] = (time.monotonic() - t0) * 1000
    logger.info("[ns_ovl r%d] exec1 (rc=%d): %s", round_idx, rc, out[:200])

    t0 = time.monotonic()
    sandbox.run_command_in_container(
        "touch /workspace/testfile && echo modified > /tmp/marker"
    )
    timings["exec_write_ms"] = (time.monotonic() - t0) * 1000

    t0 = time.monotonic()
    sandbox.reset_environment()
    timings["reset_ms"] = (time.monotonic() - t0) * 1000

    t0 = time.monotonic()
    out, rc = sandbox.run_command_in_container(
        "ls /workspace/testfile 2>&1; cat /tmp/marker 2>&1"
    )
    timings["exec_after_reset_ms"] = (time.monotonic() - t0) * 1000
    logger.info("[ns_ovl r%d] after reset (rc=%d): %s", round_idx, rc, out[:200])

    t0 = time.monotonic()
    sandbox.delete_container()
    timings["delete_ms"] = (time.monotonic() - t0) * 1000

    return timings


def _time_docker(image: str, round_idx: int) -> dict:
    from opensage.config.config_dataclass import ContainerConfig
    from opensage.sandbox.native_docker_sandbox import NativeDockerSandbox

    cfg = ContainerConfig(
        image=image,
        working_dir="/workspace",
        timeout=300,
        command="",
    )

    timings = {}
    session_id = f"dk_timing_{round_idx}_{os.getpid()}"

    t0 = time.monotonic()
    sandbox = NativeDockerSandbox(
        container_config=cfg,
        session_id=session_id,
        backend_type="native",
        sandbox_type="main",
    )
    timings["construct_ms"] = (time.monotonic() - t0) * 1000

    t0 = time.monotonic()
    out, rc = sandbox.run_command_in_container(
        "echo hello && ls /workspace && head -3 /etc/os-release"
    )
    timings["exec1_ms"] = (time.monotonic() - t0) * 1000
    logger.info("[dk r%d] exec1 (rc=%d): %s", round_idx, rc, out[:200])

    t0 = time.monotonic()
    sandbox.run_command_in_container(
        "touch /workspace/testfile && echo modified > /tmp/marker"
    )
    timings["exec_write_ms"] = (time.monotonic() - t0) * 1000

    timings["reset_ms"] = None

    t0 = time.monotonic()
    sandbox.delete_container()
    timings["delete_ms"] = (time.monotonic() - t0) * 1000

    return timings


def run_test(image: str, rounds: int):
    results = {"adl_btrfs": [], "adl_overlayfs": [], "docker": []}

    for i in range(rounds):
        logger.info("=== Round %d/%d ===", i + 1, rounds)

        logger.info("--- AgentDocker-Lite (btrfs) ---")
        try:
            ns_timings = _time_adl(image, i)
            results["adl_btrfs"].append(ns_timings)
            logger.info("ADL btrfs timings: %s", json.dumps(ns_timings, indent=2))
        except Exception as e:
            logger.error("ADL btrfs failed: %s", e, exc_info=True)
            results["adl_btrfs"].append({"error": str(e)})

        logger.info("--- AgentDocker-Lite (overlayfs) ---")
        try:
            ovl_timings = _time_adl_overlayfs(image, i)
            results["adl_overlayfs"].append(ovl_timings)
            logger.info("ADL overlayfs timings: %s", json.dumps(ovl_timings, indent=2))
        except Exception as e:
            logger.error("ADL overlayfs failed: %s", e, exc_info=True)
            results["adl_overlayfs"].append({"error": str(e)})

        logger.info("--- Docker ---")
        try:
            dk_timings = _time_docker(image, i)
            results["docker"].append(dk_timings)
            logger.info("Docker timings: %s", json.dumps(dk_timings, indent=2))
        except Exception as e:
            logger.error("Docker failed: %s", e, exc_info=True)
            results["docker"].append({"error": str(e)})

    logger.info("\n=== SUMMARY ===")
    for backend, runs in results.items():
        valid = [r for r in runs if "error" not in r]
        if not valid:
            logger.info("%s: all runs failed", backend)
            continue
        for key in valid[0]:
            vals = [r[key] for r in valid if r.get(key) is not None]
            if vals:
                avg = sum(vals) / len(vals)
                logger.info(
                    "%s  %-25s avg=%7.1fms  min=%7.1fms  max=%7.1fms",
                    backend,
                    key,
                    avg,
                    min(vals),
                    max(vals),
                )

    out_path = Path("/data/evals/sandbox_timing_results.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(results, indent=2))
    logger.info("Results written to %s", out_path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True)
    parser.add_argument("--rounds", type=int, default=3)
    args = parser.parse_args()
    run_test(args.image, args.rounds)


if __name__ == "__main__":
    main()
