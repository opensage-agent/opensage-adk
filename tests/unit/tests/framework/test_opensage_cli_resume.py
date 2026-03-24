from __future__ import annotations

import os
from pathlib import Path

import click
import pytest

from opensage.cli import opensage_cli


def test_resolve_saved_session_dir_returns_latest_when_unspecified(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    older = tmp_path / "ctf_agent_old"
    newer = tmp_path / "ctf_agent_new"
    older.mkdir()
    newer.mkdir()
    os.utime(older, (1, 1))
    os.utime(newer, (2, 2))

    monkeypatch.setattr(opensage_cli, "_SESSION_STORE_ROOT", tmp_path)

    assert opensage_cli._resolve_saved_session_dir(None) == newer


def test_resolve_saved_session_dir_accepts_exact_directory_name(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store_dir = tmp_path / "ctf_agent_c0606edc-2fff-496d-8964-48bdd7f0bd23"
    store_dir.mkdir()

    monkeypatch.setattr(opensage_cli, "_SESSION_STORE_ROOT", tmp_path)

    assert (
        opensage_cli._resolve_saved_session_dir(store_dir.name) == store_dir.resolve()
    )


def test_resolve_saved_session_dir_accepts_bare_session_id_suffix(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store_dir = tmp_path / "ctf_agent_c0606edc-2fff-496d-8964-48bdd7f0bd23"
    store_dir.mkdir()

    monkeypatch.setattr(opensage_cli, "_SESSION_STORE_ROOT", tmp_path)

    assert (
        opensage_cli._resolve_saved_session_dir("c0606edc-2fff-496d-8964-48bdd7f0bd23")
        == store_dir
    )


def test_resolve_saved_session_dir_rejects_ambiguous_suffix(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "ctf_agent_shared").mkdir()
    (tmp_path / "other_agent_shared").mkdir()

    monkeypatch.setattr(opensage_cli, "_SESSION_STORE_ROOT", tmp_path)

    with pytest.raises(click.ClickException, match="Multiple saved sessions match"):
        opensage_cli._resolve_saved_session_dir("shared")


def test_resolve_saved_session_dir_accepts_absolute_path(tmp_path: Path) -> None:
    store_dir = tmp_path / "ctf_agent_c0606edc-2fff-496d-8964-48bdd7f0bd23"
    store_dir.mkdir()

    assert opensage_cli._resolve_saved_session_dir(str(store_dir)) == store_dir
