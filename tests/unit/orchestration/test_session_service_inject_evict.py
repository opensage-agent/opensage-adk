"""Tests for OpenSageInMemorySessionService's inject/evict helpers."""

from __future__ import annotations

import pytest
from google.adk.sessions.session import Session

from opensage.features.opensage_in_memory_session_service import (
    OpenSageInMemorySessionService,
)


@pytest.mark.asyncio
async def test_inject_and_get():
    svc = OpenSageInMemorySessionService()
    sess = Session(
        id="sid-1",
        app_name="app",
        user_id="user",
        state={"k": "v"},
        events=[],
    )
    svc.inject_session(sess)

    got = await svc.get_session(app_name="app", user_id="user", session_id="sid-1")
    assert got is sess  # live object (no deepcopy)
    assert got.state["k"] == "v"


@pytest.mark.asyncio
async def test_evict():
    svc = OpenSageInMemorySessionService()
    sess = Session(id="sid-x", app_name="app", user_id="user", state={}, events=[])
    svc.inject_session(sess)

    svc.evict("sid-x")
    got = await svc.get_session(app_name="app", user_id="user", session_id="sid-x")
    assert got is None


def test_opensage_session_backref_default_none():
    svc = OpenSageInMemorySessionService()
    assert svc.opensage_session is None
