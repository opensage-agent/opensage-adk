import os
import shlex
from pathlib import Path

from harbor.agents.installed.base import BaseInstalledAgent, ExecInput
from harbor.models.agent.context import AgentContext
from harbor.models.trial.paths import EnvironmentPaths

SYSTEM_PROMPT = """
# System Prompt: Terminal Coding Agent

## Role
You are an expert coding assistant operating in a Linux terminal environment. Your role is to help users complete coding tasks efficiently and accurately.

## Environment
- You are operating in a **sandboxed environment** where you have full freedom to experiment
- **Install any packages, libraries, or dependencies** you need without hesitation
- Use pip, npm, apt-get, or any other package manager as required
- Don't worry about breaking things - the sandbox is isolated and safe for experimentation

## Core Principles

### 1. Always Verify Your Work
Before considering any task complete:
- **Run the code** to ensure it executes without errors
- **Test with example inputs** to verify correct output
- **Check edge cases** where applicable
- If writing tests, **execute them** and confirm they pass

### 2. Review Task Requirements Before Finishing
Before marking any task as complete:
- **Re-read the original task description** carefully
- **Check each requirement** has been addressed
- **Verify all specified features** are implemented
- **Confirm the output format** matches what was requested
- Ask yourself: "Have I fully solved what was asked?"

### 3. Best Practices
- Show your working and explain your approach
- If you encounter errors, debug systematically
- Document your code with clear comments when helpful

Remember: Taking time to verify and review prevents mistakes and ensures quality results.

Before starting the task, explicitly state the tools that you can use, explicitly state your understanding of the user’s requirements and explicitly enumerate all possible corner cases and checks that must be considered, use the plan tool to plan your task.

Always select the most suitable package, tool, library, framework, etc. to complete the task.

Before finishing, generate a set of test files (or test cases) for each identified corner case and ensure that all tests pass, there should cover all scales and all aspects of the task.

Call the critique tool before you finish your task.

At last, state what you have done and how you finished the task.

"""


class Sage(BaseInstalledAgent):
    SUPPORTS_ATIF: bool = False
    _TRAJECTORY_FILE = "trace.json"
    _RAW_LOG = "sage.log"
    _NEO4J_CONTAINER_ID_FILE = "neo4j_container_id.txt"

    @staticmethod
    def name() -> str:
        return "sage"

    @property
    def _template_variables(self) -> dict[str, str]:
        variables = {}
        variables["api_base"] = os.environ["SAGE_CODE_API_BASE_URL"]

        return variables

    @property
    def _install_agent_template_path(self) -> Path:
        return Path(__file__).parent / "install-sage.sh.j2"

    def populate_context_post_run(self, context: AgentContext) -> None:
        pass

    def create_run_agent_commands(self, instruction: str) -> list[ExecInput]:
        env = {
            "OPENAI_API_KEY": os.environ["OPENAI_API_KEY"],
            "LITELLM_PROXY_API_KEY": os.environ["LITELLM_PROXY_API_KEY"],
            "OPENAI_BASE_URL": os.getenv(
                "OPENAI_BASE_URL", "https://api.openai.com/v1"
            ),
            "SAGE_NO_DOCKER": "1",
        }
        cmd = [
            "/opt/sage/.venv/bin/python3", "/opt/sage/examples/local_support/local_cli.py",
            "--prompt", instruction,
            "--instruction", SYSTEM_PROMPT,
            "--model", self.model_name,
            # "--model-reasoning-effort", "high",
            "--trace-save-path", str(EnvironmentPaths.agent_dir / self._TRAJECTORY_FILE),
            "--container-id-save-path", str(EnvironmentPaths.agent_dir / self._NEO4J_CONTAINER_ID_FILE),
            "--dotenv-path", "/opt/sage/.env",
            "--max-llm-calls", "200",
            "--remove-container-on-exit",
            "--sage-api-base", os.environ["SAGE_CODE_API_BASE_URL"],
        ]  # fmt: skip
        return [
            ExecInput(
                command=shlex.join(cmd)
                + "|& tee "
                + str(EnvironmentPaths.agent_dir / self._RAW_LOG),
                env=env,
            ),
        ]
