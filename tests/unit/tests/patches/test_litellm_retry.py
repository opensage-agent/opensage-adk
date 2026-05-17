from __future__ import annotations

import pytest

from opensage.patches import litellm_retry


@pytest.mark.asyncio
async def test_acompletion_retry_uses_configured_exponential_jitter(monkeypatch):
    litellm_retry.apply()

    import litellm

    sleeps: list[float] = []
    calls = 0

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    async def flaky_call(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls < 3:
            raise RuntimeError("temporary")
        return {"ok": True}

    monkeypatch.setattr(litellm_retry, "_async_sleep", fake_sleep)
    result = await litellm.acompletion_with_retries(
        original_function=flaky_call,
        num_retries=3,
    )

    assert result == {"ok": True}
    assert calls == 3
    assert len(sleeps) == 2
    assert 2 <= sleeps[0] <= 3
    assert 4 <= sleeps[1] <= 5


def test_patch_sets_default_num_retries(monkeypatch):
    import litellm

    monkeypatch.setattr(litellm, "num_retries", None)
    litellm_retry.apply()

    assert litellm.num_retries == 15
