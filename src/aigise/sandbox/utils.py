import subprocess


def image_exists_locally(image_name: str) -> bool:
    """Check if Docker image exists locally."""
    try:
        result = subprocess.run(
            ["docker", "images", "-q", image_name],
            capture_output=True,
            text=True,
            check=False,
        )
        return bool(result.stdout.strip())
    except Exception:
        return False


def can_pull_image(image_name: str) -> bool:
    """Try to pull Docker image and return success status."""
    try:
        result = subprocess.run(
            ["docker", "pull", image_name], capture_output=True, text=True, check=False
        )
        return result.returncode == 0
    except Exception:
        return False
