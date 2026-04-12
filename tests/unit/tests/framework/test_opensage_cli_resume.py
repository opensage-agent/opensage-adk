from __future__ import annotations

import os
from pathlib import Path

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
