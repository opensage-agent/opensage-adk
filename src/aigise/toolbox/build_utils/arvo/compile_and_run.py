import os
import re
import subprocess
import tempfile
from typing import Tuple

from google.adk.tools.tool_context import ToolContext

from aigise.toolbox.decorators import requires_sandbox, safe_tool_execution
from aigise.utils.agent_utils import (
    get_aigise_config_from_context,
    get_sandbox_from_context,
)


@safe_tool_execution
@requires_sandbox("main")
def run_poc_from_script(
    poc_generation_script: str, *, tool_context: ToolContext
) -> str:
    r"""
    Execute a PoC generation script, which will save a generated poc, we execute the generated poc and capture its output.

    Args:
        poc_generation_script (str): A Python script provided as a string that, when executed,
     it should generate another file and saves it into a file named `poc` under the current working directory.
        It is used as an input to a program that can trigger the vulnerability. It should be a binary input file, a blob of data, not a executable file.
        Do not compile or run the generated PoC in the script, we will run it ourselves, the input script should only generate the `poc` file.
        Do not add any suffix to the filename, it should be exactly `poc`.
        The poc_generation_script should be wrapped with ```python and ``` at the beginning and end of the code block, then enclosed in <poc> and </poc> tags.
        You should pay attention to \n and indentation in the code block, and remember to save the generated PoC to a file named `poc` in the current working directory.
        Here is an example output format:
        <poc>
        ```python
        # This script generates a TLS ClientHello-like packet that triggers ndpi's TLS detection logic
        import struct

        with open("poc", "wb") as f:
            # TLS record header: ContentType=22 (handshake), Version=0x0303 (TLS 1.2), Length=42
            tls_header = struct.pack("!BHH", 22, 0x0303, 42)

            # Handshake header: HandshakeType=1 (ClientHello), Length=38
            handshake_header = struct.pack("!B", 1) + b'\x00\x00\x26'

            # Version, Random, Session ID Length=0
            body = struct.pack("!H", 0x0303) + b'\x00' * 32 + b'\x00'

            # Cipher Suites length=2, one dummy suite
            body += struct.pack("!H", 2) + b'\x13\x01'

            # Compression methods length=1, null
            body += struct.pack("!B", 1) + b'\x00'

            # Extensions length=0 (to keep it short)
            body += struct.pack("!H", 0)

            f.write(tls_header + handshake_header + body)
        ```
        </poc>
        Do not compile or run the generated PoC in the script, we will run it ourselves, the input script should only generate the PoC file.

    Returns:
        str: The standard output produced by running the generated PoC.
    """
    # 1. Extract the code block
    poc_re = re.compile(
        r"<poc\s*>\s*(?P<body>.*?)\s*</poc\s*>", re.IGNORECASE | re.DOTALL
    )
    match = poc_re.search(poc_generation_script)
    if not match:
        return "[ERROR] No <poc> tags found."
    inner = match.group("body")

    code_re = re.compile(r"```python\s*([\s\S]+?)```", re.IGNORECASE)
    code_match = code_re.search(inner)
    if not code_match:
        return "[ERROR] No Python code block found within <poc> tags."
    poc_code = code_match.group(1).strip()

    # 2. Get sandbox using new AigiseSession architecture
    sandbox = get_sandbox_from_context(tool_context, "main")

    # 3. Write, execute and capture the PoC generation script
    with tempfile.TemporaryDirectory() as temp_dir:
        script_path = os.path.join(temp_dir, "poc_gen.py")
        with open(script_path, "w") as f:
            f.write(poc_code)

        result = subprocess.run(
            ["python3", script_path], cwd=temp_dir, capture_output=True, text=True
        )
        if result.returncode != 0:
            return (
                f"[ERROR] PoC generation failed (code {result.returncode}):\n"
                f"{result.stdout}\n{result.stderr}"
            )

        # 4. Verify that the PoC file was created
        poc_path = os.path.join(temp_dir, "poc")
        if not os.path.isfile(poc_path):
            return "[WARN] No PoC file named 'poc' was generated."

        # 5. Copy the PoC into the container using session-specific config
        config = get_aigise_config_from_context(tool_context)
        container_poc_path = config.build.poc_dir

        try:
            sandbox.copy_file_to_container(poc_path, container_poc_path)
        except Exception as e:
            return f"[ERROR] Failed to copy PoC to container: {str(e)}"

        # 6. Execute the PoC inside the container using sandbox
        try:
            output, exit_code = run_poc_in_sandbox(tool_context)
            if exit_code != 0:
                return f"[ERROR] Running PoC in container failed (code {exit_code}):\n{output}"
            return output
        except Exception as e:
            return f"[ERROR] Failed to run PoC in container: {str(e)}"


# Unified helpers that use run_command_in_container
@safe_tool_execution
@requires_sandbox("main")
def compile_target_in_sandbox(tool_context: ToolContext) -> Tuple[str, int]:
    """Run a build command inside the sandbox via run_command_in_container.
    Args:
    Returns:
        Tuple[str, int]: The output and exit code of the command.
    """
    # Use main sandbox for compilation
    sandbox = get_sandbox_from_context(tool_context, "main")
    config = get_aigise_config_from_context(tool_context)
    build_command = config.build.compile_command
    return sandbox.run_command_in_container(build_command)


@safe_tool_execution
@requires_sandbox("main")
def run_poc_in_sandbox(tool_context: ToolContext) -> Tuple[str, int]:
    """Run a PoC command inside the sandbox via run_command_in_container.
    Args:
    Returns:
        Tuple[str, int]: The output and exit code of the command.
    """
    # Get PoC command using new AigiseSession architecture
    sandbox = get_sandbox_from_context(tool_context, "main")
    config = get_aigise_config_from_context(tool_context)
    poc_command = config.build.run_command
    output = sandbox.run_command_in_container(poc_command)
    # save it to file
    with open("poc_output.txt", "w") as f:
        f.write(output[0])
    return output
