from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import click
import pytest

from opensage.cli import opensage_cli


def _make_saved_session_dir(path: Path) -> Path:
    path.mkdir()
    (path / "metadata.json").write_text("{}", encoding="utf-8")
    return path


def test_resolve_saved_session_dir_returns_latest_when_unspecified(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    older = _make_saved_session_dir(tmp_path / "ctf_agent_old")
    newer = _make_saved_session_dir(tmp_path / "ctf_agent_new")
    os.utime(older, (1, 1))
    os.utime(newer, (2, 2))

    monkeypatch.setattr(opensage_cli, "_SESSION_STORE_ROOT", tmp_path)

    assert opensage_cli._resolve_saved_session_dir(None) == newer


def test_resolve_saved_session_dir_ignores_latest_without_metadata(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    valid = _make_saved_session_dir(tmp_path / "ctf_agent_valid")
    invalid_newer = tmp_path / "9f726b73-a80f-407f-b3e4-65a0bdfb7007"
    invalid_newer.mkdir()
    os.utime(valid, (1, 1))
    os.utime(invalid_newer, (2, 2))

    monkeypatch.setattr(opensage_cli, "_SESSION_STORE_ROOT", tmp_path)

    assert opensage_cli._resolve_saved_session_dir(None) == valid


def test_resolve_saved_session_dir_accepts_exact_directory_name(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store_dir = _make_saved_session_dir(
        tmp_path / "ctf_agent_c0606edc-2fff-496d-8964-48bdd7f0bd23"
    )

    monkeypatch.setattr(opensage_cli, "_SESSION_STORE_ROOT", tmp_path)

    assert (
        opensage_cli._resolve_saved_session_dir(store_dir.name) == store_dir.resolve()
    )


def test_resolve_saved_session_dir_accepts_bare_session_id_suffix(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store_dir = _make_saved_session_dir(
        tmp_path / "ctf_agent_c0606edc-2fff-496d-8964-48bdd7f0bd23"
    )

    monkeypatch.setattr(opensage_cli, "_SESSION_STORE_ROOT", tmp_path)

    assert (
        opensage_cli._resolve_saved_session_dir("c0606edc-2fff-496d-8964-48bdd7f0bd23")
        == store_dir
    )


def test_resolve_saved_session_dir_prefers_saved_suffix_over_invalid_exact_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bare_session_id = "c0606edc-2fff-496d-8964-48bdd7f0bd23"
    invalid_runtime_dir = tmp_path / bare_session_id
    invalid_runtime_dir.mkdir()
    saved_store_dir = _make_saved_session_dir(tmp_path / f"ctf_agent_{bare_session_id}")

    monkeypatch.setattr(opensage_cli, "_SESSION_STORE_ROOT", tmp_path)

    assert opensage_cli._resolve_saved_session_dir(bare_session_id) == saved_store_dir


def test_resolve_saved_session_dir_rejects_ambiguous_suffix(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _make_saved_session_dir(tmp_path / "ctf_agent_shared")
    _make_saved_session_dir(tmp_path / "other_agent_shared")

    monkeypatch.setattr(opensage_cli, "_SESSION_STORE_ROOT", tmp_path)

    with pytest.raises(click.ClickException, match="Multiple saved sessions match"):
        opensage_cli._resolve_saved_session_dir("shared")


def test_resolve_saved_session_dir_accepts_absolute_path(tmp_path: Path) -> None:
    store_dir = _make_saved_session_dir(
        tmp_path / "ctf_agent_c0606edc-2fff-496d-8964-48bdd7f0bd23"
    )

    assert opensage_cli._resolve_saved_session_dir(str(store_dir)) == store_dir


@pytest.mark.asyncio
async def test_terminal_async_child_results_are_processed() -> None:
    class _Inbox:
        def __init__(self) -> None:
            self.pending = False

        async def has_pending(self) -> bool:
            return self.pending

    class _Manager:
        def __init__(self) -> None:
            self.inbox = _Inbox()
            self.wait_calls = 0
            self.children = ["child-1"]

        def get_running_children(self, session_id: str) -> list[str]:
            return list(self.children)

        async def wait_for_children(self, session_id: str) -> list[str]:
            self.wait_calls += 1
            waited = list(self.children)
            self.children = []
            self.inbox.pending = True
            return waited

        def get_instance(self, session_id: str):
            return SimpleNamespace(inbox=self.inbox)

    manager = _Manager()
    turns: list[str] = []

    async def _run_turn_and_print(_manager, msg: str) -> None:
        turns.append(msg)
        manager.inbox.pending = False

    processed = await opensage_cli._process_terminal_async_child_results(
        manager=manager,
        session_id="root",
        run_turn_and_print=_run_turn_and_print,
    )

    assert processed == 1
    assert manager.wait_calls == 1
    assert turns == [
        "Your async sub-agents have completed. Process their results from your inbox."
    ]


@pytest.mark.asyncio
async def test_terminal_waits_for_background_task_results() -> None:
    class _Inbox:
        def __init__(self) -> None:
            self.pending = False

        async def has_pending(self) -> bool:
            return self.pending

    class _Manager:
        def __init__(self) -> None:
            self.inbox = _Inbox()
            self.wait_calls = 0
            self.background_tasks = ["task-1"]

        async def get_active_children(self, session_id: str) -> list[str]:
            return []

        async def wait_for_children(self, session_id: str) -> list[str]:
            return []

        def get_active_background_tasks(self, session_id: str) -> list[str]:
            return list(self.background_tasks)

        async def wait_for_background_tasks(self, session_id: str) -> list[str]:
            self.wait_calls += 1
            waited = list(self.background_tasks)
            self.background_tasks = []
            self.inbox.pending = True
            return waited

        def get_instance(self, session_id: str):
            return SimpleNamespace(inbox=self.inbox)

    manager = _Manager()
    turns: list[str] = []

    async def _run_turn_and_print(_manager, msg: str) -> None:
        turns.append(msg)
        manager.inbox.pending = False

    processed = await opensage_cli._process_terminal_async_child_results(
        manager=manager,
        session_id="root",
        run_turn_and_print=_run_turn_and_print,
    )

    assert processed == 1
    assert manager.wait_calls == 1
    assert turns == [
        "Your async sub-agents have completed. Process their results from your inbox."
    ]
