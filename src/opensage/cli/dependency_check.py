"""Dependency check utilities for OpenSage CLI."""

from __future__ import annotations

import logging
import subprocess
import tarfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from filelock import FileLock

from opensage.utils.project_info import SRC_PATH

logger = logging.getLogger(__name__)


@dataclass
class VerificationResult:
    """Result of a dependency verification check."""

    name: str
    success: bool
    message: str
    required: bool = False  # True if required for basic functionality
    optional_reason: Optional[str] = None  # Explanation if optional


def verify_codeql() -> VerificationResult:
    """Verify CodeQL installation.

    Returns:
        VerificationResult: VerificationResult indicating if CodeQL is ready.
    """
    codeql_dir = SRC_PATH / "sandbox_scripts" / "codeql"
    codeql_bin = codeql_dir / "codeql"
    codeql_bundle = SRC_PATH / "sandbox_scripts" / "codeql-bundle-linux64.tar.gz"

    # Check if CodeQL binary exists
    if codeql_bin.exists() and codeql_bin.is_file():
        return VerificationResult(
            name="CodeQL",
            success=True,
            message=f"CodeQL binary found at {codeql_bin}",
            required=False,
            optional_reason="Only required when using CodeQL static analysis features",
        )

    # Check if bundle exists but not extracted
    if codeql_bundle.exists():
        return VerificationResult(
            name="CodeQL",
            success=False,
            message=f"CodeQL bundle found but not extracted. Run 'ensure_codeql_ready()' to extract.",
            required=False,
            optional_reason="Only required when using CodeQL static analysis features",
        )

    # Bundle not found
    return VerificationResult(
        name="CodeQL",
        success=False,
        message=(
            f"CodeQL bundle not found at {codeql_bundle}.\n"
            f"  Download from: https://github.com/github/codeql-action/releases/download/"
            f"codeql-bundle-v2.18.4/codeql-bundle-linux64.tar.gz\n"
            f"  Then place it in {codeql_bundle.parent}"
        ),
        required=False,
        optional_reason="Only required when using CodeQL static analysis features",
    )


def ensure_codeql_ready() -> Path:
    """Ensure CodeQL is extracted and ready to use.

    This function is thread-safe and process-safe using file locks.
    Multiple concurrent calls will wait for the first one to complete.

    Returns:
        Path: Path to the CodeQL binary

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


def verify_docker() -> VerificationResult:
    """Verify Docker installation and daemon availability.

    Returns:
        VerificationResult: VerificationResult indicating if Docker is available.
    """
    try:
        import docker

        client = docker.from_env(timeout=3600)
        client.ping()
        return VerificationResult(
            name="Docker",
            success=True,
            message="Docker daemon is running and accessible",
            required=False,
            optional_reason="Only required when using native Docker sandbox backend",
        )
    except ImportError:
        return VerificationResult(
            name="Docker",
            success=False,
            message="Docker Python package not installed. Install with: pip install docker",
            required=False,
            optional_reason="Only required when using native Docker sandbox backend",
        )
    except Exception as e:
        return VerificationResult(
            name="Docker",
            success=False,
            message=f"Docker daemon not accessible: {e}. Ensure Docker is installed and running.",
            required=False,
            optional_reason="Only required when using native Docker sandbox backend",
        )


def verify_kubectl() -> VerificationResult:
    """Verify kubectl installation and Kubernetes cluster connectivity.

    Returns:
        VerificationResult: VerificationResult indicating if kubectl is available.
    """
    import shutil

    # Check if kubectl command exists
    kubectl_path = shutil.which("kubectl")
    if kubectl_path is None:
        return VerificationResult(
            name="kubectl",
            success=False,
            message="kubectl command not found in PATH. Install kubectl to use Kubernetes backend.",
            required=False,
            optional_reason="Only required when using Kubernetes sandbox backend",
        )

    # Check if kubectl can connect to cluster
    try:
        result = subprocess.run(
            ["kubectl", "version", "--request-timeout=5s"],
            check=True,
            capture_output=True,
            text=True,
        )
        return VerificationResult(
            name="kubectl",
            success=True,
            message=f"kubectl found at {kubectl_path} and can connect to Kubernetes cluster",
            required=False,
            optional_reason="Only required when using Kubernetes sandbox backend",
        )
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.strip() if e.stderr else "Unknown error"
        return VerificationResult(
            name="kubectl",
            success=False,
            message=f"kubectl found at {kubectl_path} but cannot connect to cluster: {stderr}",
            required=False,
            optional_reason="Only required when using Kubernetes sandbox backend",
        )
    except FileNotFoundError:
        return VerificationResult(
            name="kubectl",
            success=False,
            message="kubectl command not found in PATH",
            required=False,
            optional_reason="Only required when using Kubernetes sandbox backend",
        )
