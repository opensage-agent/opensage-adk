"""Mock Debug Evaluation - No sandbox, simple agent testing."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from evaluations import Evaluation, EvaluationTask

logger = logging.getLogger(__name__)


@dataclass
class MockDebugEvaluation(Evaluation):
    """Mock evaluation for debugging - no sandbox, no Neo4j, minimal setup.

    This evaluation is designed for quick agent testing without the overhead
    of sandbox initialization. It only runs the agent and saves the session trace.

    """

    # Provide defaults for required parent fields
    dataset_path: str = str(Path(__file__).parent / "mock_test_dataset.json")
    agent_dir: str = (
        "/scr/hongwei/projects/adk-python/AIgiSE/examples/agents_101/sample_agent_tool"
    )

    # Override defaults for mock evaluation
    max_llm_calls: int = 10
    run_until_explicit_finish: bool = False  # Simpler for debugging

    def _get_sample_id(self, sample: dict) -> str:
        """Get task ID from sample.

        Expects sample to have either 'task_id', 'id', or falls back to index.
        """
        return sample.get("task_id") or sample.get("id") or str(sample.get("index", 0))

    def _get_user_msg_first(self, sample: dict) -> str:
        """Get prompt from sample.

        Expects sample to have either 'prompt', 'question', or 'input'.
        """
        return (
            sample.get("prompt")
            or sample.get("question")
            or sample.get("input", "Hello!")
        )

    def _register_aigise_session(self, task: EvaluationTask):
        """Skip AIgiSE session registration for mock evaluation."""
        logger.info(
            f"[MOCK] Skipping aigise session registration for {task.session_id}"
        )
        # Set to None to indicate no aigise session
        task.aigise_session = None

    async def _prepare_environment(self, task: EvaluationTask) -> None:
        """Skip environment preparation - no sandboxes needed."""
        logger.info(f"[MOCK] Skipping environment preparation for {task.session_id}")
        # No sandboxes, no Neo4j, no volumes
        pass

    def _prepare_agent(self, task: EvaluationTask):
        """Prepare agent without aigise_session_id."""
        # Load agent without session_id (mock evaluation doesn't use aigise sessions)
        import importlib
        import sys

        agent_path = Path(self.agent_dir).resolve()
        parent_dir = str(agent_path.parent)
        if parent_dir not in sys.path:
            sys.path.insert(0, parent_dir)

        agent_name = agent_path.name
        agent_module = importlib.import_module(f"{agent_name}.agent")

        # Try to get root_agent or mk_agent
        if hasattr(agent_module, "root_agent"):
            agent = agent_module.root_agent
            logger.info(f"[MOCK] Loaded root_agent from {self.agent_dir}")
        elif hasattr(agent_module, "mk_agent"):
            # Call mk_agent without aigise_session_id if it doesn't require it
            try:
                agent = agent_module.mk_agent()
            except TypeError:
                # If mk_agent requires aigise_session_id, pass a dummy one
                agent = agent_module.mk_agent(aigise_session_id="mock-session")
            logger.info(f"[MOCK] Created agent from mk_agent in {self.agent_dir}")
        else:
            raise ValueError(
                f"No root_agent or mk_agent found in {self.agent_dir}/agent.py"
            )

        # Handle use_config_model if needed
        if self.use_config_model:
            logger.warning(
                "[MOCK] use_config_model=True but no config available in mock mode, using agent default"
            )

        return agent

    async def _collect_outputs(self, task: EvaluationTask, session) -> dict:
        """Collect only session trace - no sandbox/Neo4j outputs."""
        output_path = Path(task.output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        logger.info(f"[MOCK] Collecting outputs for {task.session_id}")

        # Export session trace only
        self._export_session_trace(session, output_path / "session_trace.json")

        # Save metadata
        info = {
            "metadata": task.metadata,
            "session": session.model_dump(),
        }
        with open(output_path / "metadata.json", "w") as f:
            json.dump(info, f, indent=2)

        logger.warning(f"[MOCK] Outputs collected to {output_path}")
        return info

    def evaluate(self) -> None:
        """Simple evaluation - just print summary."""
        logger.warning("=" * 80)
        logger.warning("[MOCK] Evaluation Summary")
        logger.warning("=" * 80)

        # Count successful tasks
        successful = 0
        total = 0

        for task_dir in self.output_dir.iterdir():
            if task_dir.is_dir() and (task_dir / "session_trace.json").exists():
                total += 1
                # Consider successful if session trace exists
                successful += 1

        logger.warning(f"Total tasks: {total}")
        logger.warning(f"Successful tasks: {successful}")
        logger.warning(
            f"Success rate: {successful / total * 100:.1f}%" if total > 0 else "N/A"
        )
        logger.warning("=" * 80)

        # Save simple results
        results = {
            "total": total,
            "successful": successful,
            "success_rate": successful / total if total > 0 else 0,
        }

        with open(self.output_dir / "evaluation_results.json", "w") as f:
            json.dump(results, f, indent=2)

        logger.warning(
            f"Results saved to: {self.output_dir / 'evaluation_results.json'}"
        )


if __name__ == "__main__":
    import fire

    fire.Fire(MockDebugEvaluation)
