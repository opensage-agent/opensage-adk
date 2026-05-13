import os

from google.adk.models.lite_llm import LiteLlm

_CACHE_CONTROL = [
    {"location": "message", "role": "system"},
    {"location": "message", "index": -2},
    {"location": "message", "index": -1},
]

GPT_MODEL = LiteLlm(
    model="gpt-5.5",
    api_key=os.getenv("GPT_LITELLM_API_KEY"),
    base_url=os.getenv("GPT_LITELLM_BASE_URL"),
)

CLAUDE_MODEL = LiteLlm(
    model="claude-opus-4-6",
    api_key=os.getenv("CLAUDE_LITELLM_API_KEY"),
    base_url=os.getenv("CLAUDE_LITELLM_BASE_URL"),
    cache_control_injection_points=_CACHE_CONTROL,
)


models = [
    GPT_MODEL,
    CLAUDE_MODEL,
]

DEFAULT_MODEL = CLAUDE_MODEL
