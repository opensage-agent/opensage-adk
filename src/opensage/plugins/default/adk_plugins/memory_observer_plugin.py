"""Memory observer plugin for async tool result storage."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Optional, Set

from google.adk.plugins.base_plugin import BasePlugin
from google.adk.tools.base_tool import BaseTool
from google.adk.tools.tool_context import ToolContext

from opensage.memory.storage_decider import StorageDecider, StorageDecision
from opensage.memory.update.update_controller import MemoryUpdateController
from opensage.utils.agent_utils import (
    get_neo4j_client_from_context,
    get_opensage_config_from_context,
    get_opensage_session_id_from_context,
    save_content_to_sandbox_file,
)

logger = logging.getLogger(__name__)


class MemoryObserverPlugin(BasePlugin):
    """Plugin that observes tool results and stores valuable information in memory.

    This plugin monitors each tool execution's result and uses an LLM-based
    StorageDecider to determine whether the result contains valuable information
    worth persisting to the memory graph. Storage happens asynchronously to avoid
    blocking agent execution.

    Example configuration in TOML:
        [plugins]
        enabled = ["memory_observer_plugin"]
    """

    # Tools to exclude from observation
    EXCLUDED_TOOLS: Set[str] = {
        # Memory tools (avoid recursion)
        "store_knowledge",
        "search_memory",
        "update_memory",
        "delete_memory",
        # Navigation/simple tools
        "cd",
        "pwd",
        "ls",
        # Internal tools
        "transfer_to_agent",
        "get_user_choice",
    }

    # Minimum result length to consider for storage
    MIN_RESULT_LENGTH: int = 50

    # Threshold for saving full output to file (same as StorageDecider.max_result_preview)
    SAVE_TO_FILE_THRESHOLD: int = 4000

    # Directory for saving full tool outputs
    TOOL_OUTPUT_DIR: str = "/workspace/.memory_observer_outputs"

    def __init__(
        self,
        enable_llm_decision: bool = True,
        fire_and_forget: bool = True,
        decider_model: Optional[str] = None,
    ) -> None:
        """Initialize the memory observer plugin.

        Args:
            enable_llm_decision (bool): Whether to use LLM for storage decisions.
                If False, stores all non-excluded tool results above MIN_RESULT_LENGTH.
            fire_and_forget (bool): Whether to run storage in background without waiting.
                If True, tool execution is not blocked by storage operations.
            decider_model (Optional[str]): LiteLLM model identifier for the storage decider.
                If None, reads from [memory].llm_model in config."""
        super().__init__(name="memory_observer")
        self.enable_llm_decision = enable_llm_decision
        self.fire_and_forget = fire_and_forget
        self._decider_model_override = decider_model

        # Initialize components lazily (per-context to support config)
        self._storage_deciders: dict[str, StorageDecider] = {}
        self._memory_controller: Optional[MemoryUpdateController] = None

        # Track pending tasks to prevent garbage collection
        self._pending_tasks: Set[asyncio.Task] = set()

        logger.warning(
            f"[MemoryObserver] Plugin initialized: "
            f"enable_llm_decision={enable_llm_decision}, "
            f"fire_and_forget={fire_and_forget}, "
            f"decider_model={decider_model or 'from_config'}"
        )

    def _get_storage_decider(self, tool_context: ToolContext) -> StorageDecider:
        """Get or create storage decider, using config model if available."""
        # Determine model to use
        model_name = self._decider_model_override

        if model_name is None:
            # Try to get from config
            try:
                config = get_opensage_config_from_context(tool_context)
                if config.memory and config.memory.llm_model:
                    model_name = config.memory.llm_model
                    logger.info(
                        f"[MemoryObserver] Using model from config: {model_name}"
                    )
            except Exception as e:
                logger.warning(f"[MemoryObserver] Failed to get config: {e}")

        # Fallback to default
        if model_name is None:
            model_name = "gemini-2.5-flash-lite"
            logger.info(f"[MemoryObserver] Using default model: {model_name}")

        # Cache by model name
        if model_name not in self._storage_deciders:
            logger.info(
                f"[MemoryObserver] Creating new StorageDecider with model: {model_name}"
            )
            self._storage_deciders[model_name] = StorageDecider(model_name=model_name)

        return self._storage_deciders[model_name]

    @property
    def memory_controller(self) -> MemoryUpdateController:
        """Lazily initialize the memory controller."""
        if self._memory_controller is None:
            logger.info("[MemoryObserver] Initializing MemoryUpdateController")
            self._memory_controller = MemoryUpdateController(
                use_llm_extraction=True,
                generate_embeddings=True,
            )
        return self._memory_controller

    async def after_tool_callback(
        self,
        *,
        tool: BaseTool,
        tool_args: dict,
        tool_context: ToolContext,
        result: dict,
    ) -> None:
        """Observe tool results and potentially store valuable information.

        This callback is invoked after each tool execution. It evaluates the
        result and, if deemed valuable, stores it in the memory graph.

        Args:
            tool (BaseTool): The tool that was executed.
            tool_args (dict): Arguments passed to the tool.
            result (dict): The tool's result dictionary."""
        tool_name = self._get_tool_name(tool)

        # Check if memory is enabled in config
        try:
            config = get_opensage_config_from_context(tool_context)
            if not (config.memory and config.memory.enabled):
                logger.debug(
                    f"[MemoryObserver] Memory disabled in config, skipping {tool_name}"
                )
                return
        except Exception as e:
            logger.debug(
                f"[MemoryObserver] Config unavailable ({e}), skipping {tool_name}"
            )
            return

        # Skip excluded tools
        if tool_name in self.EXCLUDED_TOOLS:
            logger.debug(f"[MemoryObserver] Tool '{tool_name}' is excluded, skipping")
            return

        # Extract result content for length check
        result_content = self._extract_result_content(result)
        result_len = len(result_content)

        # Skip short results
        if result_len < self.MIN_RESULT_LENGTH:
            logger.debug(
                f"[MemoryObserver] Result too short ({result_len} < {self.MIN_RESULT_LENGTH}), "
                f"skipping {tool_name}"
            )
            return

        # Log that we're processing this tool
        args_preview = self._truncate_for_log(json.dumps(tool_args, default=str), 200)
        result_preview = self._truncate_for_log(result_content, 200)
        logger.warning(
            f"[MemoryObserver] Processing tool '{tool_name}'\n"
            f"  Args: {args_preview}\n"
            f"  Result ({result_len} chars): {result_preview}"
        )

        # Fire-and-forget or wait for storage
        if self.fire_and_forget:
            logger.debug(f"[MemoryObserver] Creating async task for {tool_name}")
            task = asyncio.create_task(
                self._process_and_store(tool_name, tool_args, result, tool_context)
            )
            self._pending_tasks.add(task)
            task.add_done_callback(self._pending_tasks.discard)
        else:
            await self._process_and_store(tool_name, tool_args, result, tool_context)

    async def _process_and_store(
        self,
        tool_name: str,
        tool_args: dict,
        result: dict,
        tool_context: ToolContext,
    ) -> None:
        """Process tool result and store if valuable.

        Args:
            tool_name (str): Name of the tool that produced the result.
            tool_args (dict): Arguments passed to the tool.
            result (dict): The tool's result dictionary."""
        try:
            result_content = self._extract_result_content(result)
            content_len = len(result_content)

            # If result is too long, save full output to file
            full_output_file: Optional[str] = None
            if content_len > self.SAVE_TO_FILE_THRESHOLD:
                logger.warning(
                    f"[MemoryObserver] Content exceeds threshold "
                    f"({content_len} > {self.SAVE_TO_FILE_THRESHOLD}), "
                    f"saving full output to file for '{tool_name}'"
                )
                full_output_file = self._save_full_output_to_file(
                    tool_name, result_content, tool_context
                )
                logger.warning(
                    f"[MemoryObserver] File save result for '{tool_name}': "
                    f"{'SUCCESS' if full_output_file else 'FAILED'}, file={full_output_file}"
                )

            # Decide whether to store
            if self.enable_llm_decision:
                decider = self._get_storage_decider(tool_context)
                logger.warning(
                    f"[MemoryObserver] Calling StorageDecider for '{tool_name}' "
                    f"(model: {decider.model_name})"
                    f"{f', full_output_file: {full_output_file}' if full_output_file else ''}"
                )
                decision = await decider.should_store(
                    tool_name=tool_name,
                    tool_args=tool_args,
                    tool_result=result_content,
                    full_output_file=full_output_file,
                )
                logger.warning(
                    f"[MemoryObserver] StorageDecider response for '{tool_name}':\n"
                    f"  should_store: {decision.should_store}\n"
                    f"  content_type: {decision.content_type}\n"
                    f"  confidence: {decision.confidence}\n"
                    f"  reason: {decision.reason}\n"
                    f"  summary: {self._truncate_for_log(decision.summary or '', 100)}"
                )
            else:
                # Simple heuristic: store everything above threshold
                decision = StorageDecision(
                    should_store=True,
                    content_type="text",
                    confidence=0.5,
                    reason="LLM decision disabled, storing by default",
                )
                logger.warning(
                    f"[MemoryObserver] LLM decision disabled, auto-storing '{tool_name}'"
                )

            if not decision.should_store:
                logger.warning(
                    f"[MemoryObserver] SKIP storage for '{tool_name}': {decision.reason}"
                )
                return

            # Get Neo4j client and session ID
            try:
                client = await get_neo4j_client_from_context(tool_context, "memory")
                opensage_session_id = get_opensage_session_id_from_context(tool_context)
                logger.info(
                    f"[MemoryObserver] Got Neo4j client for session: {opensage_session_id}"
                )
            except Exception as e:
                logger.error(f"[MemoryObserver] Failed to get Neo4j client: {e}")
                return

            # Prepare content for storage
            content_to_store = decision.summary if decision.summary else result_content
            content_preview = self._truncate_for_log(content_to_store, 300)
            logger.warning(
                f"[MemoryObserver] Storing to Neo4j:\n"
                f"  tool: {tool_name}\n"
                f"  content_type: {decision.content_type}\n"
                f"  content ({len(content_to_store)} chars): {content_preview}"
            )

            # Store using memory controller
            update_result = await self.memory_controller.store_knowledge(
                content=content_to_store,
                content_type=decision.content_type,
                client=client,
                opensage_session_id=opensage_session_id,
                metadata={
                    "source": "memory_observer",
                    "tool_name": tool_name,
                    "tool_args": tool_args,
                    "confidence": decision.confidence,
                    "decision_reason": decision.reason,
                },
            )

            if update_result.success:
                logger.warning(
                    f"[MemoryObserver] SUCCESS stored '{tool_name}' result:\n"
                    f"  entities_added: {update_result.entities_added}\n"
                    f"  entities_updated: {update_result.entities_updated}\n"
                    f"  relationships_added: {update_result.relationships_added}"
                )
            else:
                logger.error(
                    f"[MemoryObserver] FAILED to store '{tool_name}' result: "
                    f"{update_result.error}"
                )

        except Exception as e:
            logger.error(
                f"[MemoryObserver] Exception processing '{tool_name}': {e}",
                exc_info=True,
            )

    def _save_full_output_to_file(
        self,
        tool_name: str,
        content: str,
        tool_context: ToolContext,
    ) -> Optional[str]:
        """Save full tool output to a file in the sandbox.

        Args:
            tool_name (str): Name of the tool.
            content (str): Full content to save.
        Returns:
            Optional[str]: File path if saved successfully, None otherwise.
        """
        return save_content_to_sandbox_file(
            context=tool_context,
            content=content,
            tool_name=tool_name,
            output_dir=self.TOOL_OUTPUT_DIR,
        )

    def _get_tool_name(self, tool: BaseTool) -> str:
        """Extract the tool name from a tool object."""
        if hasattr(tool, "name"):
            return tool.name
        if hasattr(tool, "__name__"):
            return tool.__name__
        if hasattr(tool, "func") and hasattr(tool.func, "__name__"):
            return tool.func.__name__
        return str(tool)

    def _extract_result_content(self, result: Any) -> str:
        """Extract string content from tool result."""
        if isinstance(result, str):
            return result
        if isinstance(result, dict):
            # Check common result keys
            for key in ("content", "output", "result", "text", "data"):
                if key in result:
                    val = result[key]
                    if isinstance(val, str):
                        return val
                    return str(val)
            return str(result)
        return str(result)

    def _truncate_for_log(self, text: str, max_len: int) -> str:
        """Truncate text for logging, adding ellipsis if truncated."""
        if len(text) <= max_len:
            return text
        return text[:max_len] + "..."

    async def cleanup(self) -> None:
        """Wait for all pending storage tasks to complete."""
        if self._pending_tasks:
            logger.warning(
                f"[MemoryObserver] Cleanup: waiting for {len(self._pending_tasks)} pending tasks"
            )
            await asyncio.gather(*self._pending_tasks, return_exceptions=True)
            self._pending_tasks.clear()
            logger.warning("[MemoryObserver] Cleanup: all pending tasks completed")
