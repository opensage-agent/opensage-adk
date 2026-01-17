from __future__ import annotations

import logging
import uuid
from typing import Any, Optional

from google.adk.agents.callback_context import CallbackContext
from google.adk.agents.invocation_context import InvocationContext
from google.adk.agents.llm_agent import LlmRequest, LlmResponse
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.plugins.base_plugin import BasePlugin
from google.adk.runners import Event
from google.adk.tools import BaseTool
from google.adk.tools.tool_context import ToolContext
from google.genai import types

logger = logging.getLogger(__name__)
PARTS_FROM_TOOLS_ID = "temp:PARTS_FROM_TOOLS_ID"


# https://github.com/google/adk-python/issues/2096#issuecomment-3106556493
class ImageInjectionPlugin(BasePlugin):
    def __init__(self, name: str = "image_injection_plugin", **kwargs):
        super().__init__(name=name, **kwargs)
        self._pending_request_parts = []  # inject images to the next request
        self._pending_history_parts = []  # Store images for history persistence

    async def on_event_callback(
        self, *, invocation_context: InvocationContext, event: Event
    ) -> Optional[Event]:
        """
        This is called during the runner event processing and it is responsible for adding the images to the history/memory.
        For injecting into ongoing requests, see before_model_callback.
        """
        logger.info(
            f"On event callback: pending history parts = {len(self._pending_history_parts)}"
        )

        # If this is a model response event and we have pending images,
        # create a follow-up user event with the images
        if self._pending_history_parts:
            # Create a new user event with the images for history
            image_event = Event(
                invocation_id=invocation_context.invocation_id,
                author="user",
                content=types.Content(
                    role="user",
                    parts=self._pending_history_parts,
                ),
            )

            # Save the image event to session
            await invocation_context.session_service.append_event(
                session=invocation_context.session, event=image_event
            )

            # Clear the stored images
            self._pending_history_parts = []

        return None  # Don't modify the original event

    async def before_model_callback(
        self, *, callback_context: CallbackContext, llm_request: LlmRequest
    ) -> Optional[LlmResponse]:
        """
        This is called before the model request is sent and it is responsible for injecting the images into the current request.
        This is not persistent, so it will not be saved in the history. See on_event_callback for that.
        """
        logger.info(
            f"Before model callback: pending parts = {len(self._pending_request_parts)}"
        )

        # Inject pending images into the current request
        if self._pending_request_parts and llm_request.contents:
            logger.info(f"Pending image parts: {len(self._pending_request_parts)}")
            # Add images to the last user message
            logger.info("Creating new user content with images")
            # Create new user content with images
            llm_request.contents.append(
                types.Content(
                    role="user",
                    parts=[types.Part(text="Here are the images from the tool:")]
                    + self._pending_request_parts,
                )
            )

            # Clear pending images
            self._pending_request_parts.clear()

        return None  # Continue with normal processing

    async def after_tool_callback(
        self, tool: BaseTool, tool_args: dict, tool_context: ToolContext, result: dict
    ) -> Optional[dict]:
        """
        This is called after the tool has executed and it is responsible for processing the result of the tool execution.
        We create identifiers for the image parts and store them in the pending_request_parts for the next request.
        """
        # Save to pending_images. Find and replace instances of types.Part in the result dictionary
        # modified_result, image_parts = self._find_and_replace_image_parts(result)

        # if image_parts:
        #     self._pending_request_parts.extend(image_parts)
        #     # Also store for history persistence
        #     self._pending_history_parts.extend(image_parts)

        # return modified_result
        if saved_parts := tool_context.state.get(PARTS_FROM_TOOLS_ID, None):
            self._pending_request_parts.extend(saved_parts)
            self._pending_history_parts.extend(saved_parts)
            tool_context.state.update({PARTS_FROM_TOOLS_ID: []})

    def _find_and_replace_image_parts(self, data):
        image_parts = []

        if isinstance(data, dict):
            for key, value in data.items():
                modified_value, parts = self._find_and_replace_image_parts(value)
                data[key] = modified_value
                image_parts.extend(parts)
        elif isinstance(data, list):
            modified_list = []
            for item in data:
                modified_item, parts = self._find_and_replace_image_parts(item)
                modified_list.append(modified_item)
                image_parts.extend(parts)
            return modified_list, image_parts
        elif isinstance(data, types.Part):
            # Generate a unique ID for the part
            unique_id = str(uuid.uuid4())
            # Replace the result with a text part containing the unique ID
            # Image part is paired with a text part containing the unique ID
            return f"Content Part ID: {unique_id}", [
                types.Part(text=f"Content Part ID: {unique_id}"),
                data,
            ]

        return data, image_parts
