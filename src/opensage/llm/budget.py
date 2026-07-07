"""Runtime LLM budget accounting for one OpenSage session."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from functools import cache
from typing import Any, AsyncGenerator, Optional

from pydantic import PrivateAttr

logger = logging.getLogger("opensage." + __name__)


_BUDGET_CUSTOM_METADATA_KEY = "budget"
_BUDGET_MANAGED_REQUEST_ATTR = "_opensage_budget_plugin_managed"
_MISSING_REQUEST_ATTR = object()


class BudgetExhaustedError(RuntimeError):
    """Raised before an LLM call when the session budget is exhausted."""


@dataclass
class UsageStats:
    """Normalized token counts extracted from an LLM response."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    total_tokens: int = 0


@dataclass
class CostResult:
    """Cost calculation result for one model response."""

    model: str
    usage: UsageStats
    cost: Optional[float]
    cost_unknown: bool = False
    cost_error: Optional[str] = None


@dataclass
class ModelPrice:
    """Per-token model price used by runtime budget accounting."""

    prompt_token_cost: float
    completion_token_cost: float
    cached_token_cost: Optional[float] = None


@dataclass
class BudgetRecord:
    """Per-response budget metadata stored outside ADK pydantic models."""

    cost: Optional[float]
    cost_unknown: bool
    cost_error: Optional[str]
    remaining_budget: Optional[float]
    model: str

    def to_metadata_dict(self) -> dict[str, Any]:
        return {
            "cost": self.cost,
            "cost_unknown": self.cost_unknown,
            "cost_error": self.cost_error,
            "remaining_budget": self.remaining_budget,
        }


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    total_tokens: int = 0
    cost: float = 0.0
    cost_unknown_calls: int = 0
    calls: int = 0

    def add(self, usage: UsageStats, cost: Optional[float]) -> None:
        self.prompt_tokens += usage.prompt_tokens
        self.completion_tokens += usage.completion_tokens
        self.cached_tokens += usage.cached_tokens
        self.total_tokens += usage.total_tokens
        self.calls += 1
        if cost is None:
            self.cost_unknown_calls += 1
        else:
            self.cost += float(cost)

    def to_dict(self) -> dict[str, Any]:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "cached_tokens": self.cached_tokens,
            "total_tokens": self.total_tokens,
            "estimated_cost": self.cost,
            "cost_unknown_calls": self.cost_unknown_calls,
            "calls": self.calls,
        }


@dataclass
class ModelUsage(Usage):
    """Aggregated usage for one model."""


@dataclass
class AgentUsage(Usage):
    """Aggregated usage for one agent session."""


class BudgetManager:
    """Shared budget manager for a single OpenSage session."""

    def __init__(
        self,
        *,
        configured_budget: float = 0.0,
        model_prices: dict[str, Any] | list[Any] | None = None,
    ) -> None:
        try:
            configured_budget = float(configured_budget or 0.0)
        except (TypeError, ValueError):
            configured_budget = 0.0
        self.configured_budget = max(0.0, configured_budget)
        self.spent_cost = 0.0
        self.per_model_usage: dict[str, ModelUsage] = {}
        self.per_agent_instance_usage: dict[str, AgentUsage] = {}
        self.unknown_cost_models: set[str] = set()
        self.budget_exhausted = False
        self.exhausted_reason: Optional[str] = None
        self.model_prices = self._normalize_model_prices(model_prices)
        self._lock = asyncio.Lock()

    @property
    def is_limited(self) -> bool:
        return self.configured_budget > 0

    @property
    def remaining_budget(self) -> Optional[float]:
        if not self.is_limited:
            return None
        return max(0.0, self.configured_budget - self.spent_cost)

    def check_available(self) -> None:
        """Raise if no more LLM calls should start under the soft budget.

        This is a pre-call guard, not a hard billing cap. Final cost is only
        known after an LLM response returns, so one in-flight call or concurrent
        calls can push ``spent_cost`` past ``configured_budget`` before later
        calls are blocked.
        """
        if self.is_limited and self.spent_cost >= self.configured_budget:
            self.budget_exhausted = True
            self.exhausted_reason = "budget_exhausted"
            raise BudgetExhaustedError(
                "OpenSage LLM budget exhausted: "
                f"spent ${self.spent_cost:.6f} of ${self.configured_budget:.6f}"
            )

    def resolve_model_for_cost(self, model: str | None) -> str:
        return (model or "").strip() or "unknown"

    def _normalize_model_prices(
        self, model_prices: dict[str, Any] | list[Any] | None
    ) -> dict[str, ModelPrice]:
        if not model_prices:
            return {}
        if isinstance(model_prices, dict):
            items = model_prices.items()
        else:
            items = (
                (
                    getattr(item, "model", None)
                    if not isinstance(item, dict)
                    else item.get("model"),
                    item,
                )
                for item in model_prices
            )

        prices: dict[str, ModelPrice] = {}
        for raw_model, raw_price in items:
            model = str(raw_model or "").strip()
            if not model:
                continue
            prices[model] = self._normalize_one_price(raw_price)
        return prices

    def _normalize_one_price(self, raw_price: Any) -> ModelPrice:
        def get_value(*names: str) -> Any:
            for name in names:
                if isinstance(raw_price, dict) and name in raw_price:
                    return raw_price[name]
                if hasattr(raw_price, name):
                    return getattr(raw_price, name)
            return None

        prompt_token_cost = get_value("prompt_token_cost")
        completion_token_cost = get_value("completion_token_cost")
        cached_token_cost = get_value("cached_token_cost")
        if prompt_token_cost is None:
            prompt_token_cost = (
                float(get_value("prompt_per_million") or 0.0) / 1_000_000
            )
        if completion_token_cost is None:
            completion_token_cost = (
                float(get_value("completion_per_million") or 0.0) / 1_000_000
            )
        if cached_token_cost is None and get_value("cached_per_million") is not None:
            cached_token_cost = (
                float(get_value("cached_per_million") or 0.0) / 1_000_000
            )

        return ModelPrice(
            prompt_token_cost=float(prompt_token_cost or 0.0),
            completion_token_cost=float(completion_token_cost or 0.0),
            cached_token_cost=(
                float(cached_token_cost) if cached_token_cost is not None else None
            ),
        )

    def _calculate_custom_cost(
        self, resolved_model: str, usage: UsageStats
    ) -> Optional[float]:
        price = self.model_prices.get(resolved_model)
        if price is None:
            return None
        prompt_billable_tokens = max(0, usage.prompt_tokens - usage.cached_tokens)
        cached_token_cost = (
            price.cached_token_cost
            if price.cached_token_cost is not None
            else price.prompt_token_cost
        )
        return (
            prompt_billable_tokens * price.prompt_token_cost
            + usage.cached_tokens * cached_token_cost
            + usage.completion_tokens * price.completion_token_cost
        )

    def calculate_cost(self, model: str | None, usage_metadata: Any) -> CostResult:
        usage = extract_usage_stats(usage_metadata)
        resolved_model = self.resolve_model_for_cost(model)
        custom_cost = self._calculate_custom_cost(resolved_model, usage)
        if custom_cost is not None:
            return CostResult(
                model=resolved_model,
                usage=usage,
                cost=custom_cost,
            )

        try:
            from litellm.cost_calculator import cost_per_token

            prompt_cost, completion_cost = cost_per_token(
                model=resolved_model,
                prompt_tokens=usage.prompt_tokens,
                completion_tokens=usage.completion_tokens,
                cache_read_input_tokens=usage.cached_tokens,
                call_type="completion",
            )
            return CostResult(
                model=resolved_model,
                usage=usage,
                cost=float(prompt_cost or 0.0) + float(completion_cost or 0.0),
            )
        except Exception as exc:
            logger.info(
                "LLM cost unknown for model %s: %s",
                resolved_model,
                exc,
            )
            return CostResult(
                model=resolved_model,
                usage=usage,
                cost=None,
                cost_unknown=True,
                cost_error=str(exc),
            )

    async def record_response(
        self,
        *,
        model: str | None,
        usage_metadata: Any,
        session_id: str | None = None,
    ) -> BudgetRecord:
        result = self.calculate_cost(model, usage_metadata)
        async with self._lock:
            if result.cost_unknown:
                self.unknown_cost_models.add(result.model)
            else:
                self.spent_cost += float(result.cost or 0.0)

            model_usage = self.per_model_usage.setdefault(result.model, ModelUsage())
            model_usage.add(result.usage, result.cost)

            if session_id:
                agent_usage = self.per_agent_instance_usage.setdefault(
                    session_id, AgentUsage()
                )
                agent_usage.add(result.usage, result.cost)

            if self.is_limited and self.spent_cost >= self.configured_budget:
                self.budget_exhausted = True
                self.exhausted_reason = "budget_exhausted"

            return BudgetRecord(
                cost=result.cost,
                cost_unknown=result.cost_unknown,
                cost_error=result.cost_error,
                remaining_budget=self.remaining_budget,
                model=result.model,
            )

    def to_dict(self) -> dict[str, Any]:
        remaining = self.remaining_budget
        return {
            "configured_budget": self.configured_budget,
            "spent_cost": self.spent_cost,
            "remaining_budget": remaining,
            "budget_exhausted": self.budget_exhausted,
            "exhausted_reason": self.exhausted_reason,
            "per_model_usage": {
                model: usage.to_dict()
                for model, usage in sorted(self.per_model_usage.items())
            },
            "per_agent_instance_usage": {
                session_id: usage.to_dict()
                for session_id, usage in sorted(self.per_agent_instance_usage.items())
            },
            "unknown_cost_models": sorted(self.unknown_cost_models),
        }


@cache
def build_budgeted_llm_class():
    from google.adk.models.base_llm import BaseLlm

    class _BudgetedLlm(BaseLlm):
        _inner: Any = PrivateAttr()
        _budget_manager: BudgetManager = PrivateAttr()

        def __init__(self, inner: Any, budget_manager: BudgetManager):
            super().__init__(model=getattr(inner, "model", "") or "unknown")
            self._inner = inner
            self._budget_manager = budget_manager

        async def generate_content_async(
            self, llm_request, stream: bool = False
        ) -> AsyncGenerator[Any, None]:
            effective_model = getattr(llm_request, "model", None) or self.model
            plugin_managed = bool(
                getattr(llm_request, _BUDGET_MANAGED_REQUEST_ATTR, False)
            )
            if not plugin_managed:
                self._budget_manager.check_available()
            last_billable = None
            try:
                async for response in self._inner.generate_content_async(
                    llm_request, stream=stream
                ):
                    if (
                        not plugin_managed
                        and getattr(response, "usage_metadata", None) is not None
                    ):
                        last_billable = response
                    yield response
            finally:
                if last_billable is not None:
                    record = await self._budget_manager.record_response(
                        model=(
                            getattr(last_billable, "model_version", None)
                            or effective_model
                        ),
                        usage_metadata=getattr(last_billable, "usage_metadata", None),
                    )
                    attach_budget_record(last_billable, record)

        def connect(self, llm_request):
            self._budget_manager.check_available()
            return self._inner.connect(llm_request)

    return _BudgetedLlm


def wrap_llm_with_budget(llm: Any, budget_manager: BudgetManager) -> Any:
    """Return a BaseLlm-compatible proxy unless ``llm`` is already wrapped."""

    if getattr(llm, "_opensage_budget_wrapped", False):
        return llm
    wrapped = build_budgeted_llm_class()(llm, budget_manager)
    object.__setattr__(wrapped, "_opensage_budget_wrapped", True)
    return wrapped


async def generate_content_with_budget(
    model: Any,
    llm_request: Any,
    *,
    budget_manager: BudgetManager,
    stream: bool = False,
    session_id: str | None = None,
) -> AsyncGenerator[Any, None]:
    """Call ``model.generate_content_async`` and charge the shared budget."""

    effective_model = getattr(llm_request, "model", None) or getattr(
        model, "model", None
    )
    plugin_managed = bool(getattr(llm_request, _BUDGET_MANAGED_REQUEST_ATTR, False))
    previous_managed_attr = _MISSING_REQUEST_ATTR
    request_marked = False
    if not plugin_managed:
        budget_manager.check_available()
        request_marked, previous_managed_attr = _mark_request_budget_managed(
            llm_request
        )
    last_billable = None
    try:
        async for response in model.generate_content_async(llm_request, stream=stream):
            if (
                not plugin_managed
                and getattr(response, "usage_metadata", None) is not None
            ):
                last_billable = response
            yield response
    finally:
        try:
            if last_billable is not None:
                record = await budget_manager.record_response(
                    model=(
                        getattr(last_billable, "model_version", None) or effective_model
                    ),
                    usage_metadata=getattr(last_billable, "usage_metadata", None),
                    session_id=session_id,
                )
                attach_budget_record(last_billable, record)
        finally:
            if request_marked:
                _restore_request_budget_managed(llm_request, previous_managed_attr)


def _mark_request_budget_managed(llm_request: Any) -> tuple[bool, object]:
    previous = getattr(llm_request, _BUDGET_MANAGED_REQUEST_ATTR, _MISSING_REQUEST_ATTR)
    try:
        setattr(llm_request, _BUDGET_MANAGED_REQUEST_ATTR, True)
        return True, previous
    except Exception:
        try:
            object.__setattr__(llm_request, _BUDGET_MANAGED_REQUEST_ATTR, True)
            return True, previous
        except Exception:
            logger.debug("Failed to mark LLM request as budget-managed", exc_info=True)
            return False, previous


def _restore_request_budget_managed(llm_request: Any, previous: object) -> None:
    try:
        if previous is _MISSING_REQUEST_ATTR:
            delattr(llm_request, _BUDGET_MANAGED_REQUEST_ATTR)
        else:
            setattr(llm_request, _BUDGET_MANAGED_REQUEST_ATTR, previous)
    except Exception:
        try:
            if previous is _MISSING_REQUEST_ATTR:
                object.__delattr__(llm_request, _BUDGET_MANAGED_REQUEST_ATTR)
            else:
                object.__setattr__(llm_request, _BUDGET_MANAGED_REQUEST_ATTR, previous)
        except Exception:
            logger.debug("Failed to restore LLM request budget marker", exc_info=True)


def extract_usage_stats(usage_metadata: Any) -> UsageStats:
    if usage_metadata is None:
        return UsageStats()

    def _get(*names: str) -> int:
        for name in names:
            value = None
            if isinstance(usage_metadata, dict):
                value = usage_metadata.get(name)
            else:
                value = getattr(usage_metadata, name, None)
            if value is not None:
                try:
                    return int(value)
                except (TypeError, ValueError):
                    return 0
        return 0

    prompt = _get("prompt_token_count", "promptTokenCount", "prompt_tokens")
    completion = _get(
        "candidates_token_count",
        "candidatesTokenCount",
        "response_token_count",
        "responseTokenCount",
        "completion_tokens",
    )
    cached = _get(
        "cached_content_token_count",
        "cachedContentTokenCount",
        "cache_read_input_tokens",
        "cached_tokens",
    )
    total = _get("total_token_count", "totalTokenCount", "total_tokens")
    if total <= 0:
        total = prompt + completion
    return UsageStats(
        prompt_tokens=prompt,
        completion_tokens=completion,
        cached_tokens=cached,
        total_tokens=total,
    )


def attach_budget_record(obj: Any, record: BudgetRecord) -> None:
    try:
        custom_metadata = getattr(obj, "custom_metadata", None)
        if not isinstance(custom_metadata, dict):
            custom_metadata = {}
            setattr(obj, "custom_metadata", custom_metadata)
        custom_metadata[_BUDGET_CUSTOM_METADATA_KEY] = record.to_metadata_dict()
    except Exception:
        logger.debug("Failed to attach budget custom metadata", exc_info=True)
