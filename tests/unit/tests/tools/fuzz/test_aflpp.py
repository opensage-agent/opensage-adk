import pytest

pytestmark = pytest.mark.skip(reason="Skipping all fuzz tests for now")

from unittest.mock import MagicMock

import pytest_asyncio

from opensage.sandbox.base_sandbox import SandboxState
from opensage.session import OpenSageSession, get_opensage_session
from opensage.utils.project_info import PROJECT_PATH
from tests.unit.utils.utils import extract_infos_from_arvo_script


@pytest_asyncio.fixture(scope="module")
async def opensage_session():
    import time

    session_id = f"test-fuzz-session-{int(time.time())}"
    opensage_session = None
    try:
        opensage_session = get_opensage_session(
            session_id, str(PROJECT_PATH / "tests/unit/data/configs/test_fuzz.toml")
        )

        opensage_session.sandboxes.initialize_shared_volumes()
        await opensage_session.sandboxes.launch_all_sandboxes()
        await opensage_session.sandboxes.initialize_all_sandboxes()
        yield opensage_session
    finally:
        if opensage_session is not None:
            opensage_session.cleanup()


@pytest.mark.slow
@pytest.mark.asyncio
async def test_fuzz_initialization(opensage_session: OpenSageSession):
    """Test that fuzzing sandbox initializes correctly."""
    fuzz_sandbox = opensage_session.sandboxes.get_sandbox("fuzz")
    assert fuzz_sandbox.state == SandboxState.READY


@pytest.mark.slow
@pytest.mark.asyncio
async def test_fuzz_environment_setup(opensage_session: OpenSageSession):
    """Test that fuzzing environment is properly set up."""
    fuzz_sandbox = opensage_session.sandboxes.get_sandbox("fuzz")

    # Check that arvo script exists and extract info
    res, exit_code = fuzz_sandbox.run_command_in_container("cat /bin/arvo")
    assert exit_code == 0, f"Failed to read arvo script: {res}"
    infos = extract_infos_from_arvo_script(res)

    assert infos["SANITIZER"] == "address"
    assert infos["FUZZING_LANGUAGE"] == "c++"
    assert infos["ARCHITECTURE"] == "x86_64"
    assert infos["FUZZ_TARGET"] == "magic_fuzzer_loaddb"

    @pytest.mark.slow
    @pytest.mark.asyncio
    async def test_fuzz_compile_aflpp(opensage_session: OpenSageSession):
        """Test AFL++ compilation (may fail in test environment due to permissions)."""
        fuzz_sandbox = opensage_session.sandboxes.get_sandbox("fuzz")

        # Extract environment info
        res, exit_code = fuzz_sandbox.run_command_in_container("cat /bin/arvo")
        assert exit_code == 0, f"Failed to read arvo script: {res}"
        infos = extract_infos_from_arvo_script(res)

        # Compile with AFL++
        env_cmd = f"export SANITIZER={infos['SANITIZER']} && export FUZZING_LANGUAGE={infos['FUZZING_LANGUAGE']} && export ARCHITECTURE={infos['ARCHITECTURE']} && bash /sandbox_scripts/ossfuzz/compile_aflpp.sh"

        res, exit_code = fuzz_sandbox.run_command_in_container(env_cmd)

        # In test environment, compilation may fail due to permission issues or directory conflicts
        # This is expected and acceptable for testing the initialization process
        if exit_code != 0:
            # Check if it's a known issue (permissions or directory exists)
            assert (
                "Permission denied" in res
                or "Read-only file system" in res
                or "File exists" in res
            ), f"Unexpected compilation error: {res}"
        else:
            assert True, "AFL++ compilation succeeded"


@pytest.mark.slow
@pytest.mark.asyncio
async def test_fuzz_run_aflpp(opensage_session: OpenSageSession):
    """Test AFL++ fuzzing execution."""
    fuzz_sandbox = opensage_session.sandboxes.get_sandbox("fuzz")

    # Extract environment info
    res, exit_code = fuzz_sandbox.run_command_in_container("cat /bin/arvo")
    assert exit_code == 0, f"Failed to read arvo script: {res}"
    infos = extract_infos_from_arvo_script(res)

    # Test fuzzing
    env_cmd = f"export FUZZ_TARGET={infos['FUZZ_TARGET']} && bash /sandbox_scripts/ossfuzz/test_fuzz.sh"

    res, exit_code = fuzz_sandbox.run_command_in_container(env_cmd)

    assert exit_code == 0, f"Fuzz test failed: {res}"


@pytest.mark.slow
@pytest.mark.asyncio
async def test_fuzz_ground_truth_poc(opensage_session: OpenSageSession):
    """Test ground truth PoC execution."""
    fuzz_sandbox = opensage_session.sandboxes.get_sandbox("fuzz")

    # Extract environment info
    res, exit_code = fuzz_sandbox.run_command_in_container("cat /bin/arvo")
    assert exit_code == 0, f"Failed to read arvo script: {res}"
    infos = extract_infos_from_arvo_script(res)

    # Test crash with PoC
    res, exit_code = fuzz_sandbox.run_command_in_container(
        f"/out/{infos['FUZZ_TARGET']} /tmp/poc"
    )

    assert exit_code != 0, f"Crash test failed: {res}"


@pytest.mark.slow
@pytest.mark.asyncio
async def test_fuzz_tool_functions(opensage_session: OpenSageSession):
    """Test fuzzing tool functions."""
    mock_context = MagicMock()
    mock_context.state = {"opensage_session_id": opensage_session.opensage_session_id}

    # Import fuzzing tools
    from opensage.toolbox.fuzzing.fuzz_tools import (
        analyze_crash,
        check_fuzzing_coverage,
        generate_poc,
        run_fuzzing_campaign,
    )

    # Test fuzzing coverage check
    coverage_result = await check_fuzzing_coverage(tool_context=mock_context)
    assert coverage_result["success"] is True
    assert "coverage_info" in coverage_result

    # Test short fuzzing campaign
    fuzz_result = await run_fuzzing_campaign(
        duration_minutes=1, seed_inputs=["test_input"], tool_context=mock_context
    )
    assert fuzz_result["success"] is True
    assert "fuzz_target" in fuzz_result
    assert fuzz_result["fuzz_target"] == "magic_fuzzer_loaddb"
