from __future__ import annotations

from opensage.patches import litellm_retry


def test_patch_sets_default_num_retries(monkeypatch):
    import litellm

    monkeypatch.setattr(litellm_retry, "_patched", False)
    monkeypatch.setattr(litellm, "num_retries", None)

    litellm_retry.apply()

    assert litellm.num_retries == 15


def test_patch_does_not_override_litellm_retry_helpers(monkeypatch):
    import litellm
    from litellm import main as litellm_main

    original_completion_with_retries = litellm.completion_with_retries
    original_acompletion_with_retries = litellm.acompletion_with_retries
    original_main_completion_with_retries = litellm_main.completion_with_retries
    original_main_acompletion_with_retries = litellm_main.acompletion_with_retries

    monkeypatch.setattr(litellm_retry, "_patched", False)
    litellm_retry.apply()

    assert litellm.completion_with_retries is original_completion_with_retries
    assert litellm.acompletion_with_retries is original_acompletion_with_retries
    assert litellm_main.completion_with_retries is original_main_completion_with_retries
    assert (
        litellm_main.acompletion_with_retries is original_main_acompletion_with_retries
    )
