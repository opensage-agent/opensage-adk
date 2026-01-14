import json
import logging
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

import datasets
import fire
import google.adk as adk
from google.adk import Runner
from google.adk.agents import RunConfig
from google.adk.agents.invocation_context import LlmCallsLimitExceededError
from google.adk.sessions import InMemorySessionService, Session
from google.genai import types

from aigise.evaluations import Evaluation, EvaluationTask
from aigise.session import get_aigise_session
from aigise.utils.project_info import PROJECT_PATH, SRC_PATH

logger = logging.getLogger(__name__)


@dataclass
class SweBenchPro(Evaluation):
    dataset_path: str = "ScaleAI/SWE-bench_Pro"
    dataset_hf_split: str = "test"
    agent_dir: str = PROJECT_PATH / "examples/agents/bench_agent"
    config_template_path: str = str(
        SRC_PATH / "evaluations/configs/swe_bench_pro_config.toml"
    )

    # Optional override for output directory relative to project root
    predictions_filename: str = "predictions.json"
    dockerhub_username: str = "jefzda"

    def __post_init__(self):
        super().__post_init__()
        if not self.agent_dir:
            logger.warning(
                "Agent directory not specified. Make sure to provide --agent_dir when running."
            )

    def _get_dataset(self) -> datasets.Dataset:
        dataset = super()._get_dataset()
        dataset = dataset.select(range(1))
        return dataset

    def _get_sample_id(self, sample: dict) -> str:
        """Get unique task ID for this sample."""
        return sample["instance_id"]

    def _get_user_msg_first(self, sample: dict) -> str:
        """Get initial prompt for the agent."""
        return (
            f"Please fix the issue described below.\n\n"
            f"Problem Statement:\n{sample['problem_statement']}\n\n"
            # f"Hints:\n{sample.get('hints_text', '')}"
        )

    def _get_docker_image_uri(self, sample: dict) -> str:
        """
        Derive the official Docker Hub image URI for the sample.
        Logic ported from SWE-bench_Pro-os/helper_code/image_uri.py
        """
        uid = sample["instance_id"]
        repo_name = sample.get("repo", "")

        try:
            repo_base, repo_name_only = repo_name.lower().split("/")
            hsh = uid.replace("instance_", "")

            if (
                uid
                == "instance_element-hq__element-web-ec0f940ef0e8e3b61078f145f34dc40d1938e6c5-vnan"
            ):
                repo_name_only = "element-web"  # Keep full name for this one case
            elif (
                "element-hq" in repo_name.lower() and "element-web" in repo_name.lower()
            ):
                repo_name_only = "element"
                if hsh.endswith("-vnan"):
                    hsh = hsh[:-5]
            # All other repos: strip -vnan suffix
            elif hsh.endswith("-vnan"):
                hsh = hsh[:-5]

            tag = f"{repo_base}.{repo_name_only}-{hsh}"
            if len(tag) > 128:
                tag = tag[:128]

            return f"{self.dockerhub_username}/sweap-images:{tag}"
        except Exception as e:
            logger.warning(
                f"Failed to generate custom docker URI: {e}. Falling back to default."
            )
            return "python:3.11"

    async def _prepare_environment(self, task: EvaluationTask):
        """Prepare environment: clone repo and checkout commit."""
        try:
            await super()._prepare_environment(task)
        except RuntimeError as e:
            if "neo4j" in str(e):
                logger.warning(f"Ignored expected neo4j initialization error: {e}")
            else:
                raise e

        # Clone repo and checkout base commit
        sample = task.sample
        repo = sample.get("repo")
        base_commit = sample.get("base_commit")

        sandbox = task.aigise_session.sandboxes.get_sandbox("main")

        if repo and base_commit:
            # Format repo url (assuming github for now)
            logger.info(f"Cloning repo {repo} at commit {base_commit}")

            # Check if /app exists (pre-installed repo)
            # SWE-bench images usually have the repo at /app
            # User confirmed /app is the location for these images
            check_app_cmd = "test -d /app"
            _, app_exists_code = sandbox.run_command_in_container(check_app_cmd)

            if app_exists_code == 0:
                logger.info("Found /app directory in container. Using it directly.")
                # Configure git safe directory for /app
                setup_cmds = [
                    "git config --global --add safe.directory /app",
                    # Add /app to PYTHONPATH so agent tools can find it if not already there
                    "export PYTHONPATH=$PYTHONPATH:/app",
                ]
            else:
                logger.info("/app not found. Falling back to git clone.")
                # Original logic
                # Format repo url (assuming github for now)
                repo_url = (
                    f"https://github.com/{repo}"
                    if not repo.startswith("http")
                    else repo
                )

                # 1. Clone the repository
                # We clone into a directory named 'repo' to keep it clean
                setup_cmds = [
                    f"git clone {repo_url} repo",
                    f"cd repo && git checkout {base_commit}",
                ]

            # Execute commands
            cmd = " && ".join(setup_cmds)
            logger.info(f"Setting up repo for task {task.task_name}: {cmd}")
            output, exit_code = sandbox.run_command_in_container(cmd)

            if exit_code != 0:
                logger.error(f"Failed to setup repo for {task.task_name}: {output}")
                raise RuntimeError(f"Failed to setup repo: {output}")

            # 2. Install dependencies (Optional/Heuristic)
            pass

    def _register_aigise_session(self, task: EvaluationTask):
        """Register AigiseSession with task-specific config, injecting DOCKER_IMAGE."""
        # Copy config template to a temporary file
        config_template = Path(task.config_template_path)
        temp_dir = tempfile.mkdtemp(prefix=f"aigise_{task.session_id}_")
        temp_config_path = Path(temp_dir) / config_template.name
        shutil.copy(config_template, temp_config_path)

        # Determine Docker image
        # Use instance-specific image from Docker Hub
        docker_image = self._get_docker_image_uri(task.sample)
        instance_id = self._get_sample_id(task.sample)
        logger.info(f"Selected docker image for {instance_id}: {docker_image}")

        # Note: We rely on the sandbox to pull the image if missing.
        # We do NOT patch the image for neo4j as it is not required for this benchmark.

        template_variables = {
            "TASK_NAME": task.task_name,
            "PROJECT_RELATIVE_SHARED_DATA_PATH": str(
                Path(task.input_data_path).relative_to(PROJECT_PATH)
            )
            if task.input_data_path
            else "",
            "DEFAULT_IMAGE": docker_image,
        }

        self._replace_template_variables_in_config(temp_config_path, template_variables)

        aigise_session = get_aigise_session(
            task.session_id, config_path=temp_config_path
        )

        # MANUAL FIX:
        # Explicitly set entrypoint and command if they were missed by dacite
        # This is necessary because dacite might drop fields if types don't match exactly
        # or if complex Unions are used without explicit casting.
        try:
            main_config = aigise_session.config.sandbox.sandboxes.get("main")
            if main_config:
                if not main_config.command:
                    logger.info("Injecting missing command into config")
                    main_config.command = "-c 'while true; do sleep 3600; done'"
        except Exception as e:
            logger.warning(f"Failed to patch config: {e}")

        task.aigise_session = aigise_session
        shutil.rmtree(temp_dir, ignore_errors=True)

    async def _run_agent(self, task: EvaluationTask, agent: adk.Agent) -> Session:
        session = await super()._run_agent(task, agent)

        # 4.5. Generate patch
        # The agent might not create a patch file, so we force one.
        # We assume the repo is in 'repo' directory under working_dir (/workspace) or /app
        try:
            sandbox = task.aigise_session.sandboxes.get_sandbox("main")

            # Prefer patch generated by the agent if it already exists.
            _, shared_patch_exists = sandbox.run_command_in_container(
                "test -f /shared/prediction.patch"
            )
            if shared_patch_exists == 0:
                copy_cmd = "cp /shared/prediction.patch /workspace/prediction.patch"
                logger.info(
                    f"Found /shared/prediction.patch; copying to /workspace: {copy_cmd}"
                )
                output, copy_exit = sandbox.run_command_in_container(copy_cmd)
                if copy_exit != 0:
                    logger.warning(f"Failed to copy prediction.patch: {output}")
                else:
                    logger.info("Successfully copied prediction.patch from /shared")
                    return session

            # Locate repo: try /app first, then repo
            repo_path = "repo"  # Default fallback

            check_app_cmd = "test -d /app"
            _, app_exists = sandbox.run_command_in_container(check_app_cmd)

            if app_exists == 0:
                repo_path = "/app"
            else:
                # Check if repo exists
                check_repo_cmd = "test -d repo"
                _, exit_code = sandbox.run_command_in_container(check_repo_cmd)
                if exit_code == 0:
                    repo_path = "repo"
                else:
                    logger.warning(
                        "Repo directory not found, skipping patch generation"
                    )
                    repo_path = ""

            if repo_path:
                # Run git diff
                # We use SafeToAutoRun=True kind of logic implicitly
                diff_cmd = f"cd {repo_path} && git diff > /workspace/prediction.patch"
                logger.info(f"Generating patch from {repo_path}: {diff_cmd}")
                output, diff_exit = sandbox.run_command_in_container(diff_cmd)
                if diff_exit != 0:
                    logger.warning(f"Failed to generate patch: {output}")
                else:
                    logger.info("Successfully generated prediction.patch")

        except Exception as e:
            logger.warning(f"Error during patch generation: {e}")
        return session

    def customized_modify_and_save_results(
        self,
        *,
        results: list | None,
        failed_samples: list[str] | None,
        mode: str,
    ) -> None:
        """Aggregate results and save predictions.json."""
        if not results:
            return

        # predictions format: list of dicts
        # [ { "instance_id": ..., "patch": ... }, ... ]
        predictions = []

        for result in results:
            # We assume the agent writes 'prediction.patch' to the sandbox output directory
            # which is collected into task.output_dir/sandbox_output/prediction.patch

            # Reconstruct metadata to get task_name/instance_id
            # NOTE: result is strictly what _generate_sample returns.
            # _generate_sample returns:
            # { "metadata": task.metadata, "session": ... }

            metadata = result.get("metadata", {})
            instance_id = self._get_sample_id(metadata)
            task_name = self._get_sample_id(metadata)  # logic is same in _get_sample_id

            # Locate the patch file
            # The output directory structure is:
            # self.output_dir / task_name / "sandbox_output" / ...

            # Since customized_modify_and_save_results doesn't get task objects,
            # we rely on the predictable path structure.

            task_output_dir = self.output_dir / task_name

            # Check for patch file. We search for any .patch file or specific name.
            # Let's look for 'prediction.patch' as instructed in agent prompt (if we had one)
            # or just any .patch file.

            sandbox_output = task_output_dir / "sandbox_output"
            patch_content = ""

            if sandbox_output.exists():
                # Try specific name first (check root and workspace subdir)
                candidate_paths = [
                    sandbox_output / "prediction.patch",
                    sandbox_output / "workspace" / "prediction.patch",
                ]

                for p in candidate_paths:
                    if p.exists():
                        patch_content = p.read_text()
                        break

                if not patch_content:
                    # Fallback: look for any .patch or .diff file recursively
                    patches = list(sandbox_output.rglob("*.patch")) + list(
                        sandbox_output.rglob("*.diff")
                    )
                    if patches:
                        # Take the first one
                        patch_content = patches[0].read_text()

            if patch_content:
                predictions.append({"instance_id": instance_id, "patch": patch_content})
            else:
                logger.warning(f"No patch found for {instance_id} in {sandbox_output}")
                # We might want to save an empty string or skip

        output_file = self.output_dir / self.predictions_filename
        with open(output_file, "w") as f:
            json.dump(predictions, f, indent=2)

        logger.warning(f"Saved {len(predictions)} predictions to {output_file}")

    def evaluate(self) -> None:
        """Run the official SWE-bench Pro evaluation.

        Note: `Evaluation.run()` / `Evaluation.run_debug()` call `self.evaluate()`
        with no arguments, so this method must not require parameters.
        """
        predictions_path = self.output_dir / self.predictions_filename
        results_dir = self.output_dir / "results"
        self._evaluate_official(
            predictions_path=predictions_path, results_dir=results_dir
        )

    def _evaluate_official(self, *, predictions_path: Path, results_dir: Path) -> None:
        """Run the official SWE-bench Pro evaluation with explicit paths."""
        logger.warning(f"Starting evaluation for {self.output_dir}...")

        # Define paths
        third_party_dir = PROJECT_PATH / "third_party"
        swe_bench_repo_name = "SWE-bench_Pro-os"
        swe_bench_repo_path = third_party_dir / swe_bench_repo_name
        repo_url = "https://github.com/scaleapi/SWE-bench_Pro-os"

        import subprocess
        import sys

        # 1. Ensure the repository exists
        if not swe_bench_repo_path.exists():
            logger.warning(f"Cloning {swe_bench_repo_name} to {swe_bench_repo_path}...")
            third_party_dir.mkdir(parents=True, exist_ok=True)
            try:
                subprocess.run(
                    ["git", "clone", repo_url, str(swe_bench_repo_path)],
                    check=True,
                    capture_output=True,
                )
                logger.warning(f"Successfully cloned {swe_bench_repo_name}")
            except subprocess.CalledProcessError as e:
                logger.error(f"Failed to clone repo: {e.stderr.decode()}")
                return

        # 2. Check/Install requirements
        # We try to install in the current environment or rely on user.
        # To be safe, we attempt install but don't fail hard if it's already there?
        # Actually, let's try to install them to ensure script runs.
        req_file = swe_bench_repo_path / "requirements.txt"
        if req_file.exists():
            logger.warning("Installing/Verifying dependencies for SWE-bench Pro...")
            try:
                subprocess.run(
                    [sys.executable, "-m", "pip", "install", "-r", str(req_file)],
                    check=True,
                    capture_output=True,
                )
            except subprocess.CalledProcessError as e:
                logger.warning(
                    f"Dependency installation warning: {e.stderr.decode()[:200]}..."
                )
                # Continue anyway? It might work if deps are already met.

        # 3. Construct the command
        eval_script = swe_bench_repo_path / "swe_bench_pro_eval.py"

        # Verify predictions file exists
        if not predictions_path.exists():
            logger.error(
                f"Predictions file not found at {predictions_path}. Cannot evaluate."
            )
            return

        # 2.5 Prepare Dataset CSV
        # The eval script expects a CSV file, but we have a HF dataset name.
        # We need to dump the dataset to CSV.
        dataset_csv_path = self.output_dir / "dataset.csv"
        if not dataset_csv_path.exists():
            logger.warning(
                f"Exporting dataset {self.dataset_path} to {dataset_csv_path}..."
            )
            try:
                import datasets
                import pandas as pd

                ds = datasets.load_dataset(
                    self.dataset_path, split=self.dataset_hf_split
                )
                # Convert to pandas and save
                df = ds.to_pandas()
                df.to_csv(dataset_csv_path, index=False)
            except Exception as e:
                logger.error(f"Failed to export dataset to CSV: {e}")
                return

        cmd = [
            sys.executable,
            str(eval_script),
            "--raw_sample_path",
            str(dataset_csv_path),
            "--patch_path",
            str(predictions_path),
            "--output_dir",
            str(results_dir),
            "--dockerhub_username",
            self.dockerhub_username,
            "--scripts_dir",
            str(swe_bench_repo_path / "run_scripts"),
            "--use_local_docker",
        ]

        # Ensure results directory exists
        results_dir.mkdir(parents=True, exist_ok=True)

        logger.warning(f"Running evaluation command: {' '.join(cmd)}")
        # 4. Execute
        try:
            # Streaming output might be better for long running processes
            with subprocess.Popen(
                cmd,
                cwd=str(
                    swe_bench_repo_path
                ),  # Run from its dir just in case of relative paths
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            ) as p:
                for line in p.stdout:
                    print(line, end="")  # Print to stdout so user sees progress

            if p.returncode != 0:
                logger.error(
                    f"Evaluation script finished with error code {p.returncode}"
                )
            else:
                logger.warning("Evaluation script finished successfully.")
        except Exception as e:
            logger.error(f"Failed to run evaluation script: {e}")


if __name__ == "__main__":
    fire.Fire(SweBenchPro)
