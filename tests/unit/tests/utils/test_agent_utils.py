import asyncio

import pytest

from opensage.utils import agent_utils


class LocalShellSandbox:
    def __init__(self):
        self.commands: list[str] = []

    async def arun_command_in_container(
        self, command: str, timeout: int | None = None
    ) -> tuple[str, int]:
        self.commands.append(command)
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            stdout, _ = await proc.communicate()
            return stdout.decode("utf-8", errors="replace"), 124
        return stdout.decode("utf-8", errors="replace"), proc.returncode


class FailingSandbox:
    async def arun_command_in_container(
        self, command: str, timeout: int | None = None
    ) -> tuple[str, int]:
        return "forced failure", 1


@pytest.mark.asyncio
async def test_write_text_file_in_sandbox_chunks_large_content(tmp_path):
    sandbox = LocalShellSandbox()
    content = ("alpha 'quoted'\nemoji: snowman\n" * 7000) + "tail"
    output_path = tmp_path / "tool output's.out"

    saved, detail = await agent_utils._write_text_file_in_sandbox(
        sandbox, str(output_path), content
    )

    assert saved, detail
    assert output_path.read_text(encoding="utf-8") == content
    assert "OPENSAGE_SAVE_EOF" not in "\n".join(sandbox.commands)
    assert max(len(command) for command in sandbox.commands) < 50_000


@pytest.mark.asyncio
async def test_save_content_to_sandbox_file_returns_none_on_write_failure(monkeypatch):
    monkeypatch.setattr(
        agent_utils,
        "get_sandbox_from_context",
        lambda _context, _sandbox_type: FailingSandbox(),
    )

    result = await agent_utils.save_content_to_sandbox_file(
        context=object(),
        content="large output",
        tool_name="bad/tool name",
        output_dir="/tmp/tool outputs",
        file_id="call/id",
        file_extension=".out",
    )

    assert result is None
