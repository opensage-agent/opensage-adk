from pathlib import Path

# SRC_PATH points to src/opensage/ directory
# Works in both development and installed package environments
SRC_PATH = Path(__file__).parent.parent.resolve()

# PROJECT_PATH points to project root (only works in development)
# Deprecated: Use SRC_PATH for paths that need to work after installation
PROJECT_PATH = SRC_PATH.parent.parent


def find_path(*path_parts: str) -> Path:
    """Find a path that exists in either development or installed package environment.

    Priority:
    1. SRC_PATH / path_parts (installed package or development src/)
    2. PROJECT_PATH / path_parts (development project root)

    Args:
        *path_parts (str): Path components (e.g., "examples", "agents", "my_agent")
    Returns:
        Path: First existing path, or SRC_PATH-based path if none exist

    Example:
        >>> find_path("examples", "agents", "vul_agent_static_tools")
        # Returns installed package path if exists, else development path
    """
    # Try SRC_PATH first (works in both environments)
    src_path = SRC_PATH.joinpath(*path_parts)
    if src_path.exists():
        return src_path

    # Try PROJECT_PATH (development environment)
    project_path = PROJECT_PATH.joinpath(*path_parts)
    if project_path.exists():
        return project_path

    # Default to SRC_PATH (let caller handle non-existence)
    return src_path
