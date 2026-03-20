"""LLM-based decision maker for tool result storage."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Optional

import litellm

logger = logging.getLogger(__name__)


@dataclass
class StorageDecision:
    """Result of storage decision analysis."""

    should_store: bool
    """Whether the content should be stored in memory."""

    content_type: str
    """Type of content: 'code', 'text', 'finding', 'error', 'search_result', etc."""

    summary: Optional[str] = None
    """Optional condensed version of the content for storage."""

    confidence: float = 0.0
    """Confidence score (0.0 to 1.0) for the decision."""

    reason: str = ""
    """Explanation of why the decision was made."""


STORAGE_DECISION_PROMPT = """You are a memory storage decision system. Analyze the following tool result and decide whether it contains valuable information worth storing in long-term memory.

## Tool Information
- **Tool Name**: {tool_name}
- **Tool Arguments**: {tool_args}
{full_output_note}

## Tool Result
```
{tool_result}
```

## Decision Criteria

**STORE if the result contains:**
- File contents revealing project structure, architecture, or patterns
- Bug locations, error messages, or stack traces
- Test patterns, test results, or coverage information
- Configuration details or environment settings
- Important code snippets, functions, or class definitions
- Search results identifying relevant code locations
- Build or deployment information

**SKIP if the result is:**
- Routine navigation output (ls, pwd, cd with simple output)
- Redundant information (already seen or commonly known)
- Very short or uninformative output (< 50 chars of useful content)
- Tool acknowledgments without substantive content
- Temporary or transient state information

## Response Format
Respond with a JSON object ONLY, no markdown code blocks:
{{
    "should_store": true/false,
    "content_type": "code|text|finding|error|search_result|config|test_result|other",
    "summary": "REQUIRED if should_store=true: A condensed summary of the key information",
    "confidence": 0.0-1.0,
    "reason": "Brief explanation of the decision"
}}

NOTE: If should_store is true, you MUST provide a summary. The summary should capture the essential information that would be useful for future reference.
"""


class StorageDecider:
    """LLM-based decision maker that evaluates whether tool results should be stored."""

    def __init__(
        self,
        model_name: str = "gemini/gemini-2.5-flash-lite",
        temperature: float = 1.0,
        max_result_preview: int = 2000,
    ):
        """Initialize the storage decider.

        Args:
            model_name (str): LiteLLM model identifier for decision making.
            temperature (float): LLM temperature for decisions (lower = more consistent).
            max_result_preview (int): Maximum characters of tool result to include in prompt."""
        self.model_name = model_name
        self.temperature = temperature
        self.max_result_preview = max_result_preview
        logger.warning(
            f"[StorageDecider] Initialized with model={model_name}, "
            f"temperature={temperature}, max_result_preview={max_result_preview}"
        )

    async def should_store(
        self,
        tool_name: str,
        tool_args: dict,
        tool_result: Any,
        full_output_file: Optional[str] = None,
    ) -> StorageDecision:
        """Decide whether a tool result should be stored in memory.

        Args:
            tool_name (str): Name of the tool that produced the result.
            tool_args (dict): Arguments passed to the tool.
            tool_result (Any): The result returned by the tool.
            full_output_file (Optional[str]): Path to file containing full output if truncated.
        Returns:
            StorageDecision: StorageDecision indicating whether and how to store the content.
        """
        # Convert result to string for analysis
        result_str = self._stringify_result(tool_result)
        original_len = len(result_str)

        # Truncate if too long
        truncated = False
        if len(result_str) > self.max_result_preview:
            result_str = result_str[: self.max_result_preview] + "\n... [truncated]"
            truncated = True

        # Format tool args for display
        args_str = json.dumps(tool_args, indent=2, default=str)
        if len(args_str) > 500:
            args_str = args_str[:500] + "..."

        # Build full output note if file was saved
        full_output_note = ""
        if full_output_file:
            full_output_note = (
                f"- **Full Output Saved To**: `{full_output_file}` "
                f"({original_len} chars total, showing first {self.max_result_preview})"
            )
        elif truncated:
            full_output_note = (
                f"- **Note**: Output truncated ({original_len} chars total, "
                f"showing first {self.max_result_preview})"
            )

        prompt = STORAGE_DECISION_PROMPT.format(
            tool_name=tool_name,
            tool_args=args_str,
            tool_result=result_str,
            full_output_note=full_output_note,
        )

        logger.warning(
            f"[StorageDecider] LLM Input for '{tool_name}':\n"
            f"  model: {self.model_name}\n"
            f"  tool_args: {args_str[:200]}{'...' if len(args_str) > 200 else ''}\n"
            f"  result_len: {original_len} chars{' (truncated)' if truncated else ''}\n"
            f"  full_output_file: {full_output_file or 'N/A'}\n"
            f"  prompt_len: {len(prompt)} chars"
        )

        try:
            response = await litellm.acompletion(
                model=self.model_name,
                messages=[{"role": "user", "content": prompt}],
                temperature=self.temperature,
                max_tokens=2048,
            )

            content = response.choices[0].message.content.strip()

            # Log raw LLM output
            logger.warning(
                f"[StorageDecider] LLM Output for '{tool_name}':\n"
                f"  raw_response: {content[:500]}{'...' if len(content) > 500 else ''}"
            )

            decision = self._parse_decision(content)

            logger.warning(
                f"[StorageDecider] Parsed decision for '{tool_name}':\n"
                f"  should_store: {decision.should_store}\n"
                f"  content_type: {decision.content_type}\n"
                f"  confidence: {decision.confidence}\n"
                f"  reason: {decision.reason}"
            )

            return decision

        except Exception as e:
            logger.error(
                f"[StorageDecider] LLM call FAILED for '{tool_name}': {e}",
                exc_info=True,
            )
            # Default to not storing on error
            return StorageDecision(
                should_store=False,
                content_type="unknown",
                confidence=0.0,
                reason=f"LLM decision failed: {e}",
            )

    def _stringify_result(self, result: Any) -> str:
        """Convert tool result to string representation."""
        if isinstance(result, str):
            return result
        if isinstance(result, dict):
            # Check for common result patterns
            if "content" in result:
                return str(result["content"])
            if "output" in result:
                return str(result["output"])
            if "result" in result:
                return str(result["result"])
            return json.dumps(result, indent=2, default=str)
        if isinstance(result, (list, tuple)):
            return json.dumps(result, indent=2, default=str)
        return str(result)

    def _parse_decision(self, content: str) -> StorageDecision:
        """Parse LLM response into StorageDecision."""
        try:
            # Handle potential markdown code block wrapping
            if content.startswith("```"):
                lines = content.split("\n")
                # Remove first and last lines (```json and ```)
                content = "\n".join(
                    lines[1:-1] if lines[-1].strip() == "```" else lines[1:]
                )
                logger.debug(f"[StorageDecider] Stripped markdown code block")

            data = json.loads(content)

            return StorageDecision(
                should_store=bool(data.get("should_store", False)),
                content_type=data.get("content_type", "text"),
                summary=data.get("summary"),
                confidence=float(data.get("confidence", 0.5)),
                reason=data.get("reason", ""),
            )

        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.error(
                f"[StorageDecider] Failed to parse LLM response: {e}\n"
                f"  raw_content: {content[:300]}{'...' if len(content) > 300 else ''}"
            )
            # Default to not storing on parse error
            return StorageDecision(
                should_store=False,
                content_type="unknown",
                confidence=0.0,
                reason=f"Failed to parse LLM response: {e}",
            )
