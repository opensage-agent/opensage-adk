"""Pre-warm rootfs cache for SWE-bench Pro images.

Pulls Docker images, exports to rootfs directories (overlayfs) or btrfs
subvolumes, then prunes Docker images. Skips images that are already cached.

Usage (must run as root):
  # overlayfs (plain directory — for shared PD-SSD):
  sudo ~/venv/bin/python ray/prewarm_rootfs.py --backend overlayfs --cache-dir /mnt/rootfs_shared

  # btrfs (subvolume — for local btrfs disk):
  sudo ~/venv/bin/python ray/prewarm_rootfs.py --backend btrfs --cache-dir /data/rootfs_cache
"""

import argparse
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(process)d] %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger("prewarm")

ROOTFS_CACHE_DIR = "/data/rootfs_cache"
DOCKERHUB_USERNAME = "jefzda"


def _get_docker_image_uri(instance_id: str, repo_name: str) -> str:
    """Derive Docker Hub image URI. Logic from SweBenchPro._get_docker_image_uri."""
    repo_base, repo_name_only = repo_name.lower().split("/")
    hsh = instance_id.replace("instance_", "")

    if (
        instance_id
        == "instance_element-hq__element-web-ec0f940ef0e8e3b61078f145f34dc40d1938e6c5-vnan"
    ):
        repo_name_only = "element-web"
    elif "element-hq" in repo_name.lower() and "element-web" in repo_name.lower():
        repo_name_only = "element"
        if hsh.endswith("-vnan"):
            hsh = hsh[:-5]
    elif hsh.endswith("-vnan"):
        hsh = hsh[:-5]

    tag = f"{repo_base}.{repo_name_only}-{hsh}"
    if len(tag) > 128:
        tag = tag[:128]

    return f"{DOCKERHUB_USERNAME}/sweap-images:{tag}"


def get_image_list(count: int) -> list[tuple[str, str]]:
    """Get (instance_id, docker_image) pairs from SWE-bench Pro dataset."""
    from datasets import load_dataset

    ds = load_dataset("ScaleAI/SWE-bench_Pro", split="test")
    pairs = []
    seen_images = set()
    for row in ds:
        instance_id = row["instance_id"]
        repo_name = row.get("repo", "")
        try:
            image = _get_docker_image_uri(instance_id, repo_name)
        except Exception as e:
            logger.warning("Skip %s: %s", instance_id, e)
            continue

        # Deduplicate (different instances can share the same image)
        if image in seen_images:
            continue
        seen_images.add(image)

        pairs.append((instance_id, image))
        if len(pairs) >= count:
            break
    return pairs


def safe_cache_name(image: str) -> str:
    """Convert Docker image name to safe filesystem name.

    Must match agentdocker-lite rootfs cache safe_name logic.
    """
    return image.replace("/", "_").replace(":", "_").replace(".", "_")


def prewarm_one(image: str, idx: int, total: int, cache_dir: str, backend: str) -> dict:
    """Pre-warm a single rootfs."""
    cache_name = safe_cache_name(image)
    cached_path = Path(cache_dir) / cache_name
    result = {"image": image, "idx": idx}

    if cached_path.exists() and any(cached_path.iterdir()):
        logger.info("[%d/%d] SKIP (cached): %s", idx, total, image.split(":")[-1][:60])
        result["status"] = "cached"
        result["time_s"] = 0
        return result

    try:
        t0 = time.monotonic()
        if backend == "overlayfs":
            from agentdocker_lite.rootfs import prepare_rootfs_from_docker

            prepare_rootfs_from_docker(image, cached_path)
        else:
            from agentdocker_lite.rootfs import prepare_btrfs_rootfs_from_docker

            prepare_btrfs_rootfs_from_docker(image, cached_path)
        elapsed = time.monotonic() - t0
        result["status"] = "ok"
        result["time_s"] = elapsed
        logger.info(
            "[%d/%d] OK (%.0fs): %s", idx, total, elapsed, image.split(":")[-1][:60]
        )
    except Exception as e:
        result["status"] = "error"
        result["error"] = str(e)
        logger.error("[%d/%d] FAIL: %s — %s", idx, total, image.split(":")[-1][:60], e)

    return result


def main():
    parser = argparse.ArgumentParser(description="Pre-warm rootfs cache")
    parser.add_argument(
        "--count", type=int, default=300, help="Number of images to cache"
    )
    parser.add_argument("--workers", type=int, default=4, help="Concurrent workers")
    parser.add_argument(
        "--cache-dir", default=ROOTFS_CACHE_DIR, help="Rootfs cache directory"
    )
    parser.add_argument(
        "--backend",
        choices=["overlayfs", "btrfs"],
        default="btrfs",
        help="Filesystem backend (overlayfs=plain dir, btrfs=subvolume)",
    )
    args = parser.parse_args()

    cache_dir = args.cache_dir
    logger.info(
        "Loading SWE-bench Pro dataset... (backend=%s, cache_dir=%s)",
        args.backend,
        cache_dir,
    )
    pairs = get_image_list(args.count)
    logger.info("Got %d images to pre-warm (workers=%d)", len(pairs), args.workers)

    # Check how many are already cached
    cached = sum(
        1 for _, img in pairs if (Path(cache_dir) / safe_cache_name(img)).exists()
    )
    logger.info("Already cached: %d, need to prepare: %d", cached, len(pairs) - cached)

    os.makedirs(cache_dir, exist_ok=True)

    t_start = time.monotonic()
    results = []

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(
                prewarm_one, img, i + 1, len(pairs), cache_dir, args.backend
            ): img
            for i, (_, img) in enumerate(pairs)
        }
        for f in as_completed(futures):
            results.append(f.result())

    elapsed = time.monotonic() - t_start
    ok = sum(1 for r in results if r["status"] == "ok")
    cached_count = sum(1 for r in results if r["status"] == "cached")
    errors = sum(1 for r in results if r["status"] == "error")
    times = [r["time_s"] for r in results if r["status"] == "ok"]

    logger.info("=== DONE in %.0fs ===", elapsed)
    logger.info("OK: %d, Cached: %d, Errors: %d", ok, cached_count, errors)
    if times:
        logger.info(
            "Per-image: mean=%.0fs, min=%.0fs, max=%.0fs",
            sum(times) / len(times),
            min(times),
            max(times),
        )

    if errors:
        for r in results:
            if r["status"] == "error":
                logger.error(
                    "  FAILED: %s — %s", r["image"].split(":")[-1][:60], r["error"]
                )


if __name__ == "__main__":
    main()
