import magic
from google.adk.tools import ToolContext
from google.genai import types

from opensage.plugins.default.adk_plugins.image_injection_plugin import (
    PARTS_FROM_TOOLS_ID,
)
from opensage.utils.agent_utils import get_sandbox_from_context


def view_image(file_path: str, *, tool_context: ToolContext) -> str:
    """View an local image file in main sandbox.
    Please use this tool if you want to check visual content.
    Only supports JPEG, PNG, WEBP formats.
    You should convert other formats to supported ones before using this tool.

    Args:
        file_path: Path to the image file

    Returns:
        str: Success message
    """
    sandbox = get_sandbox_from_context(tool_context, "main")
    # Copy the image from the agent's sandbox to a temporary location accessible by the host
    content = sandbox.extract_file_from_container_bytes(file_path)

    # OpenAI and Anthropic support image/jpeg, image/png, image/webp, image/gif.
    # Google supports image/png, image/jpeg, image/webp, image/heic, image/heif.
    # We will only support the common formats: JPEG, PNG, WEBP.
    mime_type = magic.from_buffer(content, mime=True)
    if mime_type not in ["image/jpeg", "image/png", "image/webp"]:
        return f"Unsupported image type: {mime_type}. Supported types are JPEG, PNG, and WEBP."
    parts = [types.Part.from_bytes(data=content, mime_type=mime_type)]

    if PARTS_FROM_TOOLS_ID in tool_context.state:
        tool_context.state[PARTS_FROM_TOOLS_ID] += parts
    else:
        tool_context.state[PARTS_FROM_TOOLS_ID] = parts

    return "Image loaded successfully."
