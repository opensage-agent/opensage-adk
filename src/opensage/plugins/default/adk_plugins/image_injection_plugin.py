from __future__ import annotations

import logging
import uuid
from io import BytesIO
from typing import Optional

from google.adk.agents.callback_context import CallbackContext
from google.adk.agents.invocation_context import InvocationContext
from google.adk.agents.llm_agent import LlmRequest, LlmResponse
from google.adk.plugins.base_plugin import BasePlugin
from google.adk.runners import Event
from google.adk.tools import BaseTool
from google.adk.tools.tool_context import ToolContext
from google.genai import types
from PIL import Image, UnidentifiedImageError

logger = logging.getLogger(__name__)
PARTS_FROM_TOOLS_ID = "temp:PARTS_FROM_TOOLS_ID"

# Per-side dimension caps. Providers (Anthropic) allow a larger image in a
# single-image request than in a many-image request, and the limit applies
# to the WHOLE request — historical images from prior turns included. So the
# check runs at request-build time over (existing contents + pending batch),
# not per turn. Oversized images are rejected (not silently resized); the
# model is told what was dropped and why, so it can retry with smaller input.
MAX_SINGLE_DIMENSION = 8000
MAX_BATCH_DIMENSION = 2000


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
        # create a follow-up user event with the images. Store them at their
        # original size — the per-request dimension cap is applied on the fly
        # in before_model_callback, which is the only place that can see the
        # total image count (history + pending) a given request will carry.
        if self._pending_history_parts:
            image_event = Event(
                invocation_id=invocation_context.invocation_id,
                author="user",
                content=types.Content(
                    role="user",
                    parts=self._pending_history_parts,
                ),
            )

            await invocation_context.session_service.append_event(
                session=invocation_context.session, event=image_event
            )

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

        if not llm_request.contents:
            return None

        # Pick the per-side cap from the TOTAL image count in the outgoing
        # request — existing history plus anything we're about to inject —
        # because the provider's many-image rule treats the whole request
        # as one bucket. A solo image in turn 1 can legally be 8000px, but
        # once turn 2 adds another the request has two images and both must
        # honor the tighter 2000px cap.
        existing_image_count = self._count_image_parts(llm_request.contents)
        pending_image_count = sum(
            1
            for part in self._pending_request_parts
            if self._image_size(part) is not None
        )
        total_images = existing_image_count + pending_image_count
        limit = MAX_SINGLE_DIMENSION if total_images <= 1 else MAX_BATCH_DIMENSION

        # Rewrite this request's view of history so every oversized image is
        # replaced with a text placeholder. Session storage is untouched —
        # we re-derive the filtered view each turn based on that turn's
        # total image count.
        self._reject_oversized_in_contents(llm_request.contents, limit)

        # Inject the pending batch under the same limit, dropping oversized
        # parts and warning the model via an inline text notice so it knows
        # what to resubmit.
        if self._pending_request_parts:
            kept, dropped = self._filter_parts(self._pending_request_parts, limit)
            content_parts: list[types.Part] = [
                types.Part(text="Here are the images from the tool:")
            ]
            content_parts.extend(kept)
            if dropped:
                content_parts.append(
                    types.Part(
                        text=(
                            "Dropped (oversized — resubmit smaller):\n- "
                            + "\n- ".join(dropped)
                        )
                    )
                )

            # Only inject if we have something beyond the leading prose part.
            if len(content_parts) > 1:
                logger.info("Creating new user content with images")
                llm_request.contents.append(
                    types.Content(role="user", parts=content_parts)
                )

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

    def _image_size(self, part: types.Part) -> Optional[tuple[int, int]]:
        """Return (width, height) for an inline image part, or None if the
        part carries no inline bytes or can't be parsed as an image."""
        inline = getattr(part, "inline_data", None)
        if inline is None or not getattr(inline, "data", None):
            return None
        try:
            with Image.open(BytesIO(inline.data)) as img:
                return img.size
        except (UnidentifiedImageError, OSError):
            return None

    def _count_image_parts(self, contents: list[types.Content]) -> int:
        """Count inline image parts already present in the request."""
        return sum(
            1
            for content in contents or []
            for part in (content.parts or [])
            if self._image_size(part) is not None
        )

    def _filter_parts(
        self, parts: list[types.Part], limit: int
    ) -> tuple[list[types.Part], list[str]]:
        """Split pending parts into (kept, dropped_descriptions) using the
        caller-provided per-side limit. Non-image parts and parts we can't
        parse are always kept — we only ever drop things we can confirm
        are oversized images.
        """
        kept: list[types.Part] = []
        dropped: list[str] = []
        for idx, part in enumerate(parts, 1):
            size = self._image_size(part)
            if size is not None and (size[0] > limit or size[1] > limit):
                dropped.append(
                    f"image {idx}: {size[0]}x{size[1]}px exceeds "
                    f"{limit}px per-side limit"
                )
            else:
                kept.append(part)
        return kept, dropped

    def _reject_oversized_in_contents(
        self, contents: list[types.Content], limit: int
    ) -> None:
        """Walk request contents and replace any oversized image part with
        a text placeholder noting the original dimensions. Mutates the
        Content.parts lists in place so the change affects only this
        request's view, not the underlying session history.
        """
        for content in contents:
            if not content.parts:
                continue
            new_parts: list[types.Part] = []
            changed = False
            for part in content.parts:
                size = self._image_size(part)
                if size is not None and (size[0] > limit or size[1] > limit):
                    new_parts.append(
                        types.Part(
                            text=(
                                f"[image removed: {size[0]}x{size[1]}px "
                                f"exceeds {limit}px per-side limit for "
                                f"multi-image requests]"
                            )
                        )
                    )
                    changed = True
                else:
                    new_parts.append(part)
            if changed:
                content.parts = new_parts

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
