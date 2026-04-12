from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from opensage.toolbox.mmp.tools import (
    _DEFAULT_MMP_TIMEOUT_SECONDS,
    mmp_call,
    mmp_list_servers,
    mmp_list_sessions,
    mmp_load_config,
    mmp_remove_session,
)


class _FakeSandbox:
    def __init__(self) -> None:
        self.tmpdir = Path(tempfile.mkdtemp(prefix="opensage-mmp-test-"))
        self.commands: list[tuple[str, int | None]] = []

    def cleanup(self) -> None:
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _to_host_path(self, path: str) -> Path:
        if path.startswith("/shared/"):
            return self.tmpdir / path.removeprefix("/shared/")
        return Path(path)

    def copy_file_to_container(self, local_path: str, container_path: str) -> None:
        target = self._to_host_path(container_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(Path(local_path).read_bytes())

    def extract_file_from_container(self, filepath: str) -> str:
        return self._to_host_path(filepath).read_text(encoding="utf-8")

    def run_command_in_container(
        self, command: str, timeout: int | None = None
    ) -> tuple[str, int]:
        self.commands.append((command, timeout))
        fake_bin = self.tmpdir / "bin"
        fake_bin.mkdir(parents=True, exist_ok=True)
        completed = subprocess.run(
            command,
            shell=True,
            cwd=self.tmpdir,
            env={
                "PATH": f"{fake_bin}:{Path('/usr/bin')}:{Path('/bin')}",
                "HOME": str(self.tmpdir),
            },
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout,
        )
        return completed.stdout, completed.returncode


def test_typed_tools_load_list_call_ps_rm(monkeypatch, tmp_path: Path) -> None:
    sandbox = _FakeSandbox()
    try:
        monkeypatch.setattr(
            "opensage.toolbox.mmp.tools.get_sandbox_from_context",
            lambda context, sandbox_type="main": sandbox,
        )

        fake_mmp = sandbox.tmpdir / "bin" / "mmp"
        fake_mmp.parent.mkdir(parents=True, exist_ok=True)
        fake_mmp.write_text(
            "#!/usr/bin/env bash\n"
            'cmd="$1"\n'
            "shift\n"
            'case "$cmd" in\n'
            "  load)\n"
            '    echo "Loaded config: $1"\n'
            "    ;;\n"
            "  ls)\n"
            "    echo 'SERVER  TRANSPORT'\n"
            "    echo '------  ---------'\n"
            "    echo 'alpha  [http] http://127.0.0.1:8080/mcp'\n"
            "    ;;\n"
            "  call)\n"
            '    if [ "$1" = "--id" ]; then\n'
            '      session_id="$2"\n'
            '      method="$3"\n'
            '      echo \'{"id": "\'"$session_id"\'", "server": "alpha", "result": {"method": "\'"$method"\'"}}\'\n'
            "    else\n"
            '      server="$1"\n'
            '      method="$2"\n'
            '      echo \'{"id": "sess-1", "server": "\'"$server"\'", "result": {"method": "\'"$method"\'"}}\'\n'
            "    fi\n"
            "    ;;\n"
            "  ps)\n"
            "    echo 'ID  SERVER  PID  CREATED AT  STATUS'\n"
            "    echo '--  ------  ---  ----------  ------'\n"
            "    echo 'sess-1  alpha  123  2025-01-01  running'\n"
            "    ;;\n"
            "  rm)\n"
            "    echo 'ID  SERVER  PID'\n"
            "    echo '--  ------  ---'\n"
            "    echo 'sess-1  alpha  123'\n"
            "    ;;\n"
            "esac\n",
            encoding="utf-8",
        )
        fake_mmp.chmod(0o755)

        host_config_path = tmp_path / "gateway.toml"
        host_config_path.write_text(
            "\n".join(
                [
                    "[servers.alpha]",
                    'transport = "http"',
                    'url = "http://127.0.0.1:8080/mcp"',
                ]
            ),
            encoding="utf-8",
        )
        sandbox_config_path = "/shared/gateway.toml"
        sandbox.copy_file_to_container(
            local_path=str(host_config_path),
            container_path=sandbox_config_path,
        )

        loaded = mmp_load_config(config_path=sandbox_config_path, tool_context=object())
        assert loaded["success"] is True
        assert loaded["config_path"] == sandbox_config_path
        assert len(loaded["servers"]) == 1
        assert "Loaded MMP config" in loaded["message"]

        listed = mmp_list_servers(tool_context=object())
        assert listed["success"] is True
        assert listed["servers"][0]["name"] == "alpha"
        assert listed["servers"][0]["transport"] == "http"
        assert "Found 1 MMP server" in listed["message"]

        called = mmp_call(
            method="tools/list",
            server_name="alpha",
            tool_context=object(),
        )
        assert called["success"] is True
        assert called["server"] == "alpha"
        assert "Called MMP method tools/list" in called["message"]

        sessions = mmp_list_sessions(tool_context=object())
        assert sessions["success"] is True
        assert len(sessions["sessions"]) == 1

        removed = mmp_remove_session(tool_context=object(), session_id="sess-1")
        assert removed["success"] is True
        assert removed["removed"][0]["id"] == "sess-1"
        assert "Removed 1 MMP session" in removed["message"]
        assert sandbox.commands == [
            ("mmp load /shared/gateway.toml", _DEFAULT_MMP_TIMEOUT_SECONDS),
            ("mmp ls", _DEFAULT_MMP_TIMEOUT_SECONDS),
            ("mmp call alpha tools/list", _DEFAULT_MMP_TIMEOUT_SECONDS),
            ("mmp ps", _DEFAULT_MMP_TIMEOUT_SECONDS),
            ("mmp rm --id sess-1", _DEFAULT_MMP_TIMEOUT_SECONDS),
        ]
    finally:
        sandbox.cleanup()


def test_typed_tools_validate_target_selection(monkeypatch) -> None:
    result = mmp_call(method="tools/list", tool_context=object())
    assert result["success"] is False
    assert result["error"] == "invalid mmp call"
    assert "exactly one" in result["message"]

    result = mmp_remove_session(tool_context=object())
    assert result["success"] is False
    assert "session_id is required" in result["error"]
