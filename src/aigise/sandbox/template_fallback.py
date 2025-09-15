"""Template fallback functionality for sandbox implementations."""

import subprocess
from pathlib import Path
from typing import Optional

from aigise.sandbox.docker_config import DockerConfig
from aigise.sandbox.dockerfile_builder import DockerBuildResult, DockerfileBuilder


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


def try_build_from_template(config: DockerConfig) -> Optional[DockerBuildResult]:
    """Try to build image from template if template configuration is provided.

    Args:
        config: DockerConfig with template configuration

    Returns:
        DockerBuildResult if build was attempted, None if no template config
    """
    if not config.dockerfile_template_path or not config.image:
        return None

    try:
        # Use dockerfile_template_path directory as build context
        template_path = Path(config.dockerfile_template_path)
        build_context = template_path.parent

        builder = DockerfileBuilder()

        result = builder.build_image_from_template(
            template_path=config.dockerfile_template_path,
            variables=config.template_variables,
            image_name=config.image,
            build_context=build_context,
            cleanup_dockerfile=True,
        )

        return result

    except Exception as e:
        return DockerBuildResult(
            success=False,
            image_name=config.image or "unknown",
            build_output="",
            error_message=f"Template build failed: {str(e)}",
        )


def ensure_docker_image(config: DockerConfig) -> tuple[bool, Optional[str]]:
    """Ensure Docker image is available, using template fallback if needed.

    Args:
        config: DockerConfig with image name and optional template config

    Returns:
        Tuple of (success, error_message). If success is False, error_message explains why.
    """
    if not config.image:
        return False, "No image specified in DockerConfig"

    # Check if image exists locally
    if image_exists_locally(config.image):
        return True, None

    # Try to pull image
    print(f"Image {config.image} not found locally, attempting to pull...")
    if can_pull_image(config.image):
        print(f"Successfully pulled {config.image}")
        return True, None

    # If pull failed and we have template config, try building from template
    print(f"Failed to pull {config.image}")

    if config.dockerfile_template_path:
        print(
            f"Attempting to build {config.image} from template {config.dockerfile_template_path}..."
        )

        build_result = try_build_from_template(config)

        if build_result is None:
            return False, "Template configuration incomplete"

        if build_result.success:
            print(f"Successfully built {config.image} from template")
            return True, None
        else:
            return False, f"Template build failed: {build_result.error_message}"

    # No template fallback available
    return (
        False,
        f"Image {config.image} not available and no template fallback configured",
    )


class TemplateFallbackMixin:
    """Mixin to add template fallback functionality to sandbox classes."""

    def _ensure_image_with_template_fallback(self, config: DockerConfig) -> None:
        """Ensure Docker image is available, using template fallback if needed.

        Raises:
            RuntimeError: If image cannot be obtained through any method
        """
        success, error_message = ensure_docker_image(config)

        if not success:
            raise RuntimeError(f"Failed to obtain Docker image: {error_message}")

        # Update the image name in case it was built from template
        if hasattr(self, "docker_config_obj"):
            self.docker_config_obj.image = config.image
        if hasattr(self, "image_name"):
            self.image_name = config.image
