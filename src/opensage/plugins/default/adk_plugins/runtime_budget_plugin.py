"""ADK plugin that enforces OpenSage runtime LLM budgets."""

from __future__ import annotations

import logging
from typing import Optional

from google.adk.models.llm_response import LlmResponse
from google.adk.plugins.base_plugin import BasePlugin
from google.genai import types

from opensage.llm.budget import (
    _BUDGET_MANAGED_REQUEST_ATTR,
    BudgetExhaustedError,
    BudgetManager,
    attach_budget_record,
)

logger = logging.getLogger("opensage." + __name__)


class RuntimeBudgetPlugin(BasePlugin):
    """Session-level budget guard for all ADK runner model calls."""

    def __init__(self, budget_manager: BudgetManager):
        super().__init__(name="opensage_runtime_budget")
        self._budget_manager = budget_manager

    async def before_model_callback(self, *, callback_context, llm_request):
        try:
            self._budget_manager.check_available()
            setattr(llm_request, _BUDGET_MANAGED_REQUEST_ATTR, True)
        except BudgetExhaustedError as exc:
            logger.warning("Blocking LLM call because OpenSage budget is exhausted")
            return _budget_exhausted_response(str(exc))
        return None

    async def after_model_callback(
        self, *, callback_context, llm_response: LlmResponse
    ) -> Optional[LlmResponse]:
        usage = getattr(llm_response, "usage_metadata", None)
        if usage is None:
            return None

        invocation_context = getattr(callback_context, "_invocation_context", None)
        if invocation_context is None:
            invocation_context = getattr(callback_context, "invocation_context", None)
        session_id = getattr(getattr(invocation_context, "session", None), "id", None)

        model = getattr(llm_response, "model_version", None) or getattr(
            getattr(invocation_context, "agent", None), "model", None
        )
        if not isinstance(model, str):
            model = getattr(model, "model", None)

        record = await self._budget_manager.record_response(
            model=model,
            usage_metadata=usage,
            session_id=session_id,
        )
        attach_budget_record(llm_response, record)
        return None


def _budget_exhausted_response(message: str) -> LlmResponse:
    return LlmResponse(
        content=types.Content(
            role="model",
            parts=[
                types.Part.from_text(
                    text=(
                        "[OpenSage budget exhausted] No further LLM calls will be made."
                    )
                )
            ],
        ),
        error_code="OPENSAGE_BUDGET_EXHAUSTED",
        error_message=message,
    )
