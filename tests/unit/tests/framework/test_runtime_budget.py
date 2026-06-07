from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from google.adk.events.event import Event
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.sessions.session import Session
from google.genai import types

from opensage.evaluation.base import Evaluation
from opensage.llm.budget import (
    BudgetExhaustedError,
    BudgetManager,
    BudgetRecord,
    attach_budget_record,
)
from opensage.plugins.default.adk_plugins.runtime_budget_plugin import (
    RuntimeBudgetPlugin,
)


def _usage(prompt=100, completion=20, cached=10):
    return types.GenerateContentResponseUsageMetadata(
        prompt_token_count=prompt,
        candidates_token_count=completion,
        cached_content_token_count=cached,
        total_token_count=prompt + completion,
    )


class _CostInfoEvaluation(Evaluation):
    def _get_task_id(self, sample: dict) -> str:
        return "unused"

    def _get_first_user_message(self, sample: dict) -> str:
        return "unused"

    def _get_export_dir_in_sandbox(self, sample: dict) -> str | None:
        return None

    def evaluate(self) -> dict:
        return {}


@pytest.mark.asyncio
async def test_budget_records_litellm_model_cost_and_remaining_budget(monkeypatch):
    def fake_cost_per_token(**kwargs):
        assert kwargs["model"] == "local/test"
        assert kwargs["prompt_tokens"] == 100
        assert kwargs["completion_tokens"] == 20
        assert kwargs["cache_read_input_tokens"] == 10
        return 0.91, 0.4

    monkeypatch.setattr("litellm.cost_calculator.cost_per_token", fake_cost_per_token)
    budget = BudgetManager(configured_budget=1.0)

    record = await budget.record_response(
        model="local/test",
        usage_metadata=_usage(prompt=100, completion=20, cached=10),
        session_id="agent-1",
    )

    assert record.cost == pytest.approx(1.31)
    assert record.remaining_budget == 0
    assert budget.budget_exhausted is True
    state = budget.to_dict()
    assert state["per_model_usage"]["local/test"]["prompt_tokens"] == 100
    assert state["per_agent_instance_usage"]["agent-1"]["completion_tokens"] == 20


def test_budget_check_blocks_when_spent_reaches_limit():
    budget = BudgetManager(configured_budget=0.5)
    budget.spent_cost = 0.5

    with pytest.raises(BudgetExhaustedError):
        budget.check_available()

    assert budget.budget_exhausted is True


def test_unknown_price_is_recorded_without_spending():
    budget = BudgetManager(configured_budget=1.0)

    result = budget.calculate_cost("unknown/private-model", _usage())

    assert result.cost is None
    assert result.cost_unknown is True
    assert budget.spent_cost == 0


def test_custom_model_price_charges_without_litellm(monkeypatch):
    def fail_cost_per_token(**kwargs):
        raise AssertionError("custom price should be used before LiteLLM")

    monkeypatch.setattr("litellm.cost_calculator.cost_per_token", fail_cost_per_token)
    budget = BudgetManager(
        configured_budget=1.0,
        model_prices=[
            {
                "model": "custom/private-model",
                "prompt_per_million": 3.0,
                "completion_per_million": 15.0,
                "cached_per_million": 0.3,
            }
        ],
    )

    result = budget.calculate_cost(
        "custom/private-model",
        _usage(prompt=100, completion=20, cached=10),
    )

    expected = (90 * 3.0 + 10 * 0.3 + 20 * 15.0) / 1_000_000
    assert result.cost == pytest.approx(expected)
    assert result.cost_unknown is False


def test_adk_session_json_stores_budget_custom_metadata():
    event = Event(author="agent", usage_metadata=_usage(prompt=10, completion=5))
    session = Session(app_name="app", user_id="user", id="sid", events=[event])

    attach_budget_record(
        event,
        BudgetRecord(
            cost=0.02,
            cost_unknown=False,
            cost_error=None,
            remaining_budget=0.975,
            model="local/test",
        ),
    )

    data = session.model_dump(exclude_none=True, mode="json")

    usage = data["events"][0]["usage_metadata"]
    budget_metadata = data["events"][0]["custom_metadata"]["budget"]
    assert set(usage) == {
        "prompt_token_count",
        "candidates_token_count",
        "cached_content_token_count",
        "total_token_count",
    }
    assert budget_metadata["cost"] == pytest.approx(0.02)
    assert budget_metadata["remaining_budget"] == pytest.approx(0.975)
    assert "model" not in budget_metadata


def test_cost_info_json_includes_budget_cost_state(tmp_path):
    budget = BudgetManager(configured_budget=1.0)
    budget.spent_cost = 0.25
    opensage_session = SimpleNamespace(
        budget=budget,
        config=SimpleNamespace(
            model=SimpleNamespace(
                evaluation_replace_all_models_with_model_name="local/test"
            )
        ),
    )
    task = SimpleNamespace(
        session_id="sid",
        id="task-1",
        output_dir=str(tmp_path),
        opensage_session=opensage_session,
    )
    session = Session(
        app_name="app",
        user_id="user",
        id="sid",
        events=[Event(author="agent", usage_metadata=_usage(prompt=10, completion=5))],
    )
    evaluation = object.__new__(_CostInfoEvaluation)

    evaluation._save_cost_info(task, session, num_llm_calls=1)

    data = json.loads((tmp_path / "cost_info.json").read_text())
    assert data["estimated_cost"] == pytest.approx(0.25)
    assert "configured_budget" not in data
    assert "remaining_budget" not in data
    assert "budget_exhausted" not in data
    assert "cost_unknown_models" not in data
    assert data["budget"]["spent_cost"] == pytest.approx(0.25)
    assert data["budget"]["configured_budget"] == pytest.approx(1.0)
    assert data["budget"]["remaining_budget"] == pytest.approx(0.75)
    assert data["budget"]["budget_exhausted"] is False
    assert data["budget"]["unknown_cost_models"] == []
    assert data["token_usage"]["total_input_tokens"] == 10
    assert data["token_usage"]["total_output_tokens"] == 5


@pytest.mark.asyncio
async def test_runtime_budget_plugin_blocks_exhausted_budget():
    budget = BudgetManager(configured_budget=0.01)
    budget.spent_cost = 0.01
    plugin = RuntimeBudgetPlugin(budget)

    response = await plugin.before_model_callback(
        callback_context=object(), llm_request=LlmRequest()
    )

    assert isinstance(response, LlmResponse)
    assert response.error_code == "OPENSAGE_BUDGET_EXHAUSTED"
    assert response.error_message is not None
    assert "$0.010000" in response.error_message
    content_text = response.content.parts[0].text
    assert content_text == (
        "[OpenSage budget exhausted] No further LLM calls will be made."
    )
    assert "$" not in content_text


@pytest.mark.asyncio
async def test_plugin_cost_metadata_survives_llmresponse_to_event_finalize(monkeypatch):
    def fake_cost_per_token(**kwargs):
        assert kwargs["model"] == "local/test"
        assert kwargs["prompt_tokens"] == 10
        assert kwargs["completion_tokens"] == 5
        return 0.01, 0.01

    monkeypatch.setattr("litellm.cost_calculator.cost_per_token", fake_cost_per_token)
    budget = BudgetManager(configured_budget=1.0)
    plugin = RuntimeBudgetPlugin(budget)
    response = LlmResponse(
        content=types.Content(role="model", parts=[types.Part.from_text(text="ok")]),
        model_version="local/test",
        usage_metadata=_usage(prompt=10, completion=5),
    )

    await plugin.after_model_callback(callback_context=object(), llm_response=response)
    event = Event.model_validate(
        {"author": "agent", **response.model_dump(exclude_none=True)}
    )
    session = Session(app_name="app", user_id="user", id="sid", events=[event])

    event_dict = session.model_dump(exclude_none=True, mode="json")["events"][0]
    budget_metadata = event_dict["custom_metadata"]["budget"]
    assert set(event_dict["usage_metadata"]) == {
        "prompt_token_count",
        "candidates_token_count",
        "cached_content_token_count",
        "total_token_count",
    }
    assert budget_metadata["cost"] == pytest.approx(0.02)
    assert budget_metadata["remaining_budget"] == pytest.approx(0.98)
    assert "model" not in budget_metadata
