import os
import socket
import time

from google.adk.tools.mcp_tool.mcp_toolset import MCPToolset, SseConnectionParams

from aigise.sandbox import NativeDockerSandbox
from aigise.sandbox.docker_config import DockerConfig
from aigise.utils.project_info import PROJECT_PATH


def _find_free_port(start_port: int = 6000) -> int:
    """Find a free port starting from start_port."""
    port = start_port
    while port < 65535:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("127.0.0.1", port))
                return port
        except OSError:
            port += 1
    raise RuntimeError("No free ports available")


def get_toolset() -> MCPToolset:
    """Create MCPToolset with GDB MCP server running in Docker container.

    Returns:
        MCPToolset connected to GDB MCP server

    Raises:
        RuntimeError: If IMAGE_NAME environment variable is not set
        RuntimeError: If container creation fails
    """
    # Get base image from environment
    image_name = os.getenv("IMAGE_NAME")
    if not image_name:
        raise RuntimeError("IMAGE_NAME environment variable must be set")

    # Generate container image name
    container_image = f"{image_name}_gdb_mcp"

    # Find free port for mapping
    host_port = _find_free_port(6000)

    # Create Docker configuration with template fallback
    template_path = (
        PROJECT_PATH / "src/aigise/templates/dockerfiles/gdb_mcp/gdb_mcp.dockerfile.j2"
    )
    config = DockerConfig(
        image=container_image,
        dockerfile_template_path=str(template_path),
        template_variables={"base_image": image_name},
        ports={
            "1111/tcp": host_port  # Map container port 1111 to host port
        },
        # Keep container running in detached mode
        environment={"MCP_SERVER_PORT": "1111"},
        # Use Dockerfile's default CMD (MCP server) instead of bash
        command="",
    )
    # Create sandbox with template fallback
    try:
        sandbox = NativeDockerSandbox(config)
        print(f"Created GDB MCP container with image: {container_image}")
        print(f"Container accessible on port: {host_port}")

        # Wait a moment for the MCP server to start up
        print("Waiting for MCP server to start...")
        time.sleep(3)

    except RuntimeError as e:
        raise RuntimeError(f"Failed to create GDB MCP container: {e}")

    # Create MCPToolset connected to the container
    mcp_toolset = MCPToolset(
        connection_params=SseConnectionParams(url=f"http://127.0.0.1:{host_port}/sse")
    )

    return mcp_toolset
