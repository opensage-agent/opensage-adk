"""Setup utilities for AIgiSE runtime dependencies."""

from __future__ import annotations

import logging
import tarfile
from pathlib import Path

from filelock import FileLock

from aigise.utils.project_info import SRC_PATH

logger = logging.getLogger(__name__)


def ensure_codeql_ready() -> Path:
    """Ensure CodeQL is extracted and ready to use.

    This function is thread-safe and process-safe using file locks.
    Multiple concurrent calls will wait for the first one to complete.

    Returns:
        Path to the CodeQL binary

    Raises:
        FileNotFoundError: If CodeQL bundle is not found
    """
    codeql_dir = SRC_PATH / "sandbox_scripts" / "codeql"
    codeql_bin = codeql_dir / "codeql"

    # If CodeQL already exists, return immediately
    if codeql_bin.exists():
        return codeql_bin

    # Use file lock to prevent concurrent extraction
    lock_file = SRC_PATH / "sandbox_scripts" / ".codeql_setup.lock"
    lock_file.parent.mkdir(parents=True, exist_ok=True)

    with FileLock(str(lock_file), timeout=300):
        # Double-check after acquiring lock (another process may have finished)
        if codeql_bin.exists():
            logger.info("CodeQL already extracted by another process")
            return codeql_bin

        # Extract CodeQL bundle
        codeql_bundle = SRC_PATH / "sandbox_scripts" / "codeql-bundle-linux64.tar.gz"

        if not codeql_bundle.exists():
            raise FileNotFoundError(
                f"CodeQL bundle not found at {codeql_bundle}.\n"
                f"Please download from: "
                f"https://github.com/github/codeql-action/releases/download/"
                f"codeql-bundle-v2.18.4/codeql-bundle-linux64.tar.gz\n"
                f"Then place it in {codeql_bundle.parent}"
            )

        logger.info(f"Extracting CodeQL from {codeql_bundle}...")

        with tarfile.open(codeql_bundle, "r:gz") as tar:
            # Extract only the 'codeql' directory
            members = [m for m in tar.getmembers() if m.name.startswith("codeql/")]
            tar.extractall(path=SRC_PATH / "sandbox_scripts", members=members)

        logger.info(f"CodeQL extracted to {codeql_dir}")

        # Verify extraction
        if not codeql_bin.exists():
            raise RuntimeError(
                f"CodeQL extraction completed but binary not found at {codeql_bin}"
            )

        return codeql_bin
