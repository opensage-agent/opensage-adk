from typing import Any, List, Union

from langchain_openai import AzureChatOpenAI, ChatOpenAI


class LLMConstructException(Exception): ...


def construct_chat_llm(*args: Any, **kwargs: Any) -> Union[ChatOpenAI, AzureChatOpenAI]:
    _openai_class_ = [ChatOpenAI, AzureChatOpenAI]

    errors: List[str] = []
    for cls in _openai_class_:
        try:
            return cls(*args, **kwargs)

        # NOTE: Using broad exception handling to capture all possible LLM initialization errors.
        # This allows us to collect all error messages and raise them together in a unified format.
        except Exception as e:
            errors.append(str(e))
            continue

    raise LLMConstructException(f"Failed to construct LLM: {errors}")
