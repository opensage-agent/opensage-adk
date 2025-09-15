import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Union

import jinja2


@dataclass
class DockerBuildResult:
    """Result of a Docker image build operation."""

    success: bool
    image_name: str
    build_output: str
    error_message: Optional[str] = None


class DockerfileBuilder:
    """Builder for creating Docker images from Jinja2 templates."""

    def __init__(self, template_base_dir: Optional[Union[str, Path]] = None):
        """Initialize the dockerfile builder.

        Args:
            template_base_dir: Base directory for dockerfile templates.
                              If None, uses aigise/templates directory.
        """
        if template_base_dir is None:
            # Default to aigise/templates directory
            current_dir = Path(__file__).parent
            template_base_dir = current_dir.parent / "templates"

        self.template_base_dir = Path(template_base_dir)
        self._ensure_template_dir()

    def _ensure_template_dir(self):
        """Ensure the template directory exists."""
        self.template_base_dir.mkdir(parents=True, exist_ok=True)
        dockerfile_dir = self.template_base_dir / "dockerfiles"
        dockerfile_dir.mkdir(exist_ok=True)

    def render_dockerfile(
        self,
        template_path: str,
        variables: Dict[str, Any],
        base_dir: Optional[Union[str, Path]] = None,
    ) -> str:
        """Render a dockerfile template with given variables.

        Args:
            template_path: Relative path to the dockerfile template
            variables: Dictionary of variables to substitute in template
            base_dir: Base directory for the template (overrides default)

        Returns:
            Rendered dockerfile content as string

        Raises:
            FileNotFoundError: If template file doesn't exist
            jinja2.TemplateError: If template rendering fails
        """
        if base_dir is None:
            base_dir = self.template_base_dir
        else:
            base_dir = Path(base_dir)

        template_file = base_dir / template_path

        if not template_file.exists():
            raise FileNotFoundError(f"Template file not found: {template_file}")

        # Set up Jinja2 environment with the template directory
        template_dir = template_file.parent
        env = jinja2.Environment(
            loader=jinja2.FileSystemLoader(str(template_dir)),
            trim_blocks=True,
            lstrip_blocks=True,
        )

        template = env.get_template(template_file.name)
        return template.render(**variables)

    def build_image_from_template(
        self,
        template_path: str,
        variables: Dict[str, Any],
        image_name: str,
        build_context: Optional[Union[str, Path]] = None,
        dockerfile_name: str = "Dockerfile",
        build_args: Optional[Dict[str, str]] = None,
        no_cache: bool = False,
        cleanup_dockerfile: bool = True,
    ) -> DockerBuildResult:
        """Build Docker image from a dockerfile template.

        Args:
            template_path: Relative path to the dockerfile template
            variables: Dictionary of variables to substitute in template
            image_name: Name and tag for the built image (e.g., 'myapp:latest')
            build_context: Directory to use as build context. If None, uses template directory
            dockerfile_name: Name of the dockerfile to create in build context
            build_args: Build-time variables for Docker build
            no_cache: Whether to build without using cache
            cleanup_dockerfile: Whether to remove generated dockerfile after build

        Returns:
            DockerBuildResult with build status and details
        """
        # Determine build context directory
        if build_context is None:
            template_file = self.template_base_dir / template_path
            build_context = template_file.parent
        else:
            build_context = Path(build_context)

        build_context = build_context.resolve()
        dockerfile_path = build_context / dockerfile_name

        try:
            # Render the dockerfile template
            dockerfile_content = self.render_dockerfile(template_path, variables)

            # Write rendered dockerfile to build context
            with open(dockerfile_path, "w", encoding="utf-8") as f:
                f.write(dockerfile_content)

            # Prepare docker build command
            cmd = ["docker", "build", "-t", image_name]

            if no_cache:
                cmd.append("--no-cache")

            # Add build args if provided
            if build_args:
                for key, value in build_args.items():
                    cmd.extend(["--build-arg", f"{key}={value}"])

            # Add dockerfile and context
            cmd.extend(["-f", str(dockerfile_path), str(build_context)])

            # Change to build context directory and run docker build
            original_cwd = os.getcwd()
            try:
                os.chdir(build_context)
                result = subprocess.run(
                    cmd, capture_output=True, text=True, cwd=build_context
                )

                if result.returncode == 0:
                    return DockerBuildResult(
                        success=True, image_name=image_name, build_output=result.stdout
                    )
                else:
                    return DockerBuildResult(
                        success=False,
                        image_name=image_name,
                        build_output=result.stdout,
                        error_message=result.stderr,
                    )

            finally:
                os.chdir(original_cwd)

        except Exception as e:
            return DockerBuildResult(
                success=False,
                image_name=image_name,
                build_output="",
                error_message=str(e),
            )

        finally:
            # Clean up generated dockerfile if requested
            if cleanup_dockerfile and dockerfile_path.exists():
                try:
                    dockerfile_path.unlink()
                except Exception:
                    pass  # Ignore cleanup errors

    def create_template_from_dockerfile(
        self,
        source_dockerfile: Union[str, Path],
        template_path: str,
        placeholder_patterns: Optional[Dict[str, str]] = None,
    ) -> bool:
        """Convert an existing dockerfile to a Jinja2 template.

        Args:
            source_dockerfile: Path to existing dockerfile
            template_path: Relative path where template should be saved
            placeholder_patterns: Dict mapping variable names to regex patterns to replace
                                 If None, uses common patterns like version numbers

        Returns:
            True if conversion successful, False otherwise
        """
        try:
            source_path = Path(source_dockerfile)
            if not source_path.exists():
                return False

            with open(source_path, "r", encoding="utf-8") as f:
                content = f.read()

            # Apply placeholder patterns if provided
            if placeholder_patterns:
                import re

                for var_name, pattern in placeholder_patterns.items():
                    content = re.sub(pattern, f"{{{{ {var_name} }}}}", content)

            # Save template
            template_file = self.template_base_dir / template_path
            template_file.parent.mkdir(parents=True, exist_ok=True)

            with open(template_file, "w", encoding="utf-8") as f:
                f.write(content)

            return True

        except Exception:
            return False

    def list_templates(self, category: Optional[str] = None) -> list[str]:
        """List available dockerfile templates.

        Args:
            category: Optional category filter (subdirectory name)

        Returns:
            List of template paths relative to template base directory
        """
        templates = []
        search_dir = self.template_base_dir / "dockerfiles"

        if category:
            search_dir = search_dir / category

        if not search_dir.exists():
            return templates

        for template_file in search_dir.rglob("*.j2"):
            rel_path = template_file.relative_to(self.template_base_dir)
            templates.append(str(rel_path))

        return sorted(templates)

    def validate_template(
        self, template_path: str, variables: Dict[str, Any]
    ) -> tuple[bool, Optional[str]]:
        """Validate that a template can be rendered with given variables.

        Args:
            template_path: Path to template file
            variables: Variables to test rendering with

        Returns:
            Tuple of (is_valid, error_message)
        """
        try:
            self.render_dockerfile(template_path, variables)
            return True, None
        except Exception as e:
            return False, str(e)
