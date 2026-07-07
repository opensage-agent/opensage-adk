from __future__ import annotations

from opensage.patches import litellm_web_search


def _apply_patch_and_get_litellm_module():
    litellm_web_search.apply()

    from google.adk.models import lite_llm as lite_llm_module

    return lite_llm_module


def test_malformed_litellm_tool_call_arguments_do_not_crash():
    lite_llm_module = _apply_patch_and_get_litellm_module()

    from litellm import (
        ChatCompletionAssistantMessage,
        ChatCompletionMessageToolCall,
        Function,
    )

    message = ChatCompletionAssistantMessage(
        role="assistant",
        content=None,
        tool_calls=[
            ChatCompletionMessageToolCall(
                type="function",
                id="call_bad",
                function=Function(
                    name="run_terminal_command",
                    arguments='{"command": "unterminated',
                ),
            )
        ],
    )

    response = lite_llm_module._message_to_generate_content_response(
        message,
        model_version="gpt-5.5",
    )

    assert response.content.parts[0].text
    assert response.model_version == "gpt-5.5"
    assert response.custom_metadata == {
        "malformed_tool_calls": [
            {
                "id": "call_bad",
                "name": "run_terminal_command",
                "error": "Unterminated string starting at: line 1 column 13 (char 12)",
                "arguments_preview": '{"command": "unterminated',
            }
        ]
    }


def test_valid_litellm_tool_call_arguments_still_convert():
    lite_llm_module = _apply_patch_and_get_litellm_module()

    from litellm import (
        ChatCompletionAssistantMessage,
        ChatCompletionMessageToolCall,
        Function,
    )

    message = ChatCompletionAssistantMessage(
        role="assistant",
        content=None,
        tool_calls=[
            ChatCompletionMessageToolCall(
                type="function",
                id="call_ok",
                function=Function(
                    name="run_terminal_command",
                    arguments='{"command": "pwd", "timeout": 10}',
                ),
            )
        ],
    )

    response = lite_llm_module._message_to_generate_content_response(message)

    assert response.custom_metadata is None
    assert len(response.content.parts) == 1
    call = response.content.parts[0].function_call
    assert call.id == "call_ok"
    assert call.name == "run_terminal_command"
    assert call.args == {"command": "pwd", "timeout": 10}


def test_model_response_conversion_handles_malformed_tool_call_arguments():
    lite_llm_module = _apply_patch_and_get_litellm_module()

    from litellm import (
        ChatCompletionAssistantMessage,
        ChatCompletionMessageToolCall,
        Function,
    )

    class ModelResponseStub(dict):
        model = "gpt-5.5"
        _hidden_params = {}

    message = ChatCompletionAssistantMessage(
        role="assistant",
        content=None,
        tool_calls=[
            ChatCompletionMessageToolCall(
                type="function",
                id="call_bad",
                function=Function(
                    name="run_terminal_command",
                    arguments='{"command": "unterminated',
                ),
            )
        ],
    )
    model_response = ModelResponseStub(
        choices=[{"message": message, "finish_reason": "tool_calls"}]
    )

    response = lite_llm_module._model_response_to_generate_content_response(
        model_response
    )

    assert response.model_version == "gpt-5.5"
    assert response.content.parts[0].text.startswith("The previous tool call")
    assert response.custom_metadata["malformed_tool_calls"][0]["id"] == "call_bad"
