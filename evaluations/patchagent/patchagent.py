import base64
import json
import logging
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

import docker
import fire

from aigise.session import get_aigise_session
from aigise.utils.project_info import PROJECT_PATH

from .. import Evaluation, EvaluationTask

logger = logging.getLogger(__name__)


@dataclass
class PatchAgent(Evaluation):
    ## local dataset_path: str = PROJECT_PATH / "src/aigise/data/patchagent/data.json"
    dataset_path: str = "yuzhounie/patchagent_data"
    dataset_hf_split: str = "train"
    output_dir_in_sandbox: str = "/shared/"
    agent_dir: str = str(PROJECT_PATH / "examples/agents/poc_agent")
    max_llm_calls: int = 2

    def _get_sample_id(self, sample: dict) -> str:
        """Get unique task ID for this sample."""
        return sample["task_id"].replace(":", "_")

    def _get_user_msg_first(self, sample: dict) -> str:
        """Get initial prompt for the agent."""
        vuln_description = sample["vulnerability_description"]
        return (
            f"There exists a vulnerability in the code: {vuln_description}. "
            # f"You can run `python run.py --project {sample['project_name']} --tag {sample['tag_name']} --action all` under `f{sample['workdir']}` to trigger the vulnerability. "
            f"You can check detailed report under {sample['report_path']} to understand the vulnerability. "
            f"You should first explore, understand the vulnerability, and generate a patch to fix the vulnerability. "
            f"Once you find the patch successful, you should create a `patch.diff` file to {self.output_dir_in_sandbox}, named as patch.diff"
        )

    def _register_aigise_session(self, task: EvaluationTask):
        """Register AigiseSession with task-specific config.

        Args:
            task: EvaluationTask containing session_id and config_template_path
        Returns:
            None
        """
        # Copy config template to a temporary file for this task
        config_template = Path(task.config_template_path)
        temp_dir = tempfile.mkdtemp(prefix=f"aigise_{task.session_id}_")
        temp_config_path = Path(temp_dir) / config_template.name
        shutil.copy(config_template, temp_config_path)
        task_name = task.task_name
        if task.input_data_path:
            input_data_path = str(Path(task.input_data_path).relative_to(PROJECT_PATH))
        else:
            input_data_path = ""
        image_name = task.sample["task_id"]
        sample = task.sample
        template_variables = {
            "TASK_NAME": task_name,
            "PROJECT_RELATIVE_SHARED_DATA_PATH": input_data_path,
            "DEFAULT_IMAGE": image_name,
            "COMPILE_COMMAND": f"cd {sample['workdir']} && python run.py --project {sample['project_name']} --tag {sample['tag_name']} --action build",
            "RUN_COMMAND": f"cd {sample['workdir']} && python run.py --project {sample['project_name']} --tag {sample['tag_name']} --action test",
        }
        task.metadata["patch_diff"] = sample["patch_diff"]
        self._replace_template_variables_in_config(temp_config_path, template_variables)

        aigise_session = get_aigise_session(
            task.session_id, config_path=temp_config_path
        )
        task.aigise_session = aigise_session

        # clean up temp config file
        shutil.rmtree(temp_dir, ignore_errors=True)

    def _evaluate_single_sample(self, sample: dict, parent_dir: Path) -> dict:
        """Evaluate a single sample by running tests in Docker.

        Args:
            sample: Sample metadata
            parent_dir: Parent directory containing the metadata file

        Returns:
            Evaluation results dict
        """
        # Extract sample info
        image = sample["task_id"]
        workdir = sample["workdir"]
        project_name = sample["project_name"]
        tag_name = sample["tag_name"]
        patch_diff = sample.get("patch_diff", "")

        logger.info(f"Starting evaluation for image: {image}")

        # Create Docker client
        client = docker.from_env(timeout=600)
        container = None

        try:
            # Start Docker container
            logger.info(f"Starting Docker container with image: {image}")
            container = client.containers.run(
                image=image,
                detach=True,
                tty=True,
                stdin_open=True,
                remove=False,  # Don't auto-remove so we can get logs on error
                working_dir=workdir,
            )
            logger.info(f"Container started: {container.id}")

            # Step 1: Create patch directory and write patch.diff
            patch_dir = f"{workdir}/{project_name}/{tag_name}"
            patch_file_path = f"{patch_dir}/patch.diff"

            logger.info(f"Creating patch directory: {patch_dir}")
            exec_result = container.exec_run(
                cmd=f"mkdir -p {patch_dir}",
                tty=True,
            )
            if exec_result.exit_code != 0:
                logger.warning(f"mkdir failed: {exec_result.output.decode()}")

            logger.info(f"Writing patch.diff to: {patch_file_path}")
            # Write patch diff to file using a heredoc
            encoded_patch = base64.b64encode(patch_diff.encode()).decode()
            write_patch_cmd = [
                "bash",
                "-c",
                f"echo '{encoded_patch}' | base64 -d > {patch_file_path}",
            ]

            exec_result = container.exec_run(cmd=write_patch_cmd, tty=True)
            if exec_result.exit_code != 0:
                logger.warning(f"Write patch failed: {exec_result.output.decode()}")

            # Step 2: Apply the patch
            logger.info(f"Applying patch in directory: {patch_dir}")
            apply_patch_cmd = (
                f"bash -c 'cd {patch_dir}/immutable && git apply ../patch.diff'"
            )
            exec_result = container.exec_run(cmd=apply_patch_cmd, tty=True)
            apply_exit_code = exec_result.exit_code
            apply_stdout = exec_result.output.decode()

            logger.info(f"Patch apply exit code: {apply_exit_code}")
            if apply_exit_code != 0:
                logger.warning(f"Patch apply failed: {apply_stdout}")

            # Step 3: Run action all (build and test)
            test_command = f"bash -c 'cd {workdir} && python run.py --project {project_name} --tag {tag_name} --action all --patch_path {patch_file_path}'"

            logger.info(f"Running test command: {test_command}")
            exec_result = container.exec_run(cmd=test_command, tty=True)
            exit_code = exec_result.exit_code
            stdout = exec_result.output.decode()
            success = stdout.split("Test report:")[-1].strip() == ""
            logger.info(f"Test command completed with exit code: {exit_code}")
            logger.debug(f"Test output: {stdout[:1000]}...")  # Log first 1000 chars

            # Save evaluation results
            eval_results = {
                "sample_id": self._get_sample_id(sample),
                "image": image,
                "patch_file_path": patch_file_path,
                "patch_applied": apply_exit_code == 0,
                "patch_apply_output": apply_stdout,
                "test_command": test_command,
                "exit_code": exit_code,
                "stdout": stdout,
                "success": success,
            }

            # Write evaluation results to file
            eval_results_path = parent_dir / "evaluation_results.json"
            with open(eval_results_path, "w") as f:
                json.dump(eval_results, f, indent=2)

            logger.info(f"Evaluation results saved to: {eval_results_path}")

            return eval_results

        except Exception as e:
            logger.error(f"Error evaluating sample: {e}")
            import traceback

            traceback.print_exc()
            return {
                "sample_id": self._get_sample_id(sample),
                "error": str(e),
                "success": False,
            }

        finally:
            # Cleanup: stop and remove container
            if container:
                try:
                    logger.info(f"Stopping container: {container.id}")
                    container.stop(timeout=10)
                    container.remove()
                    logger.info(f"Container removed: {container.id}")
                except Exception as e:
                    logger.warning(f"Error cleaning up container: {e}")

    def evaluate(self) -> None:
        """Evaluate results."""
        # Walk through all subdirectories to collect metadata.json files
        results = []
        final_success_count = 0
        for metadata_file in self.output_dir.rglob("metadata.json"):
            parent_dir = metadata_file.parent
            print(f"Processing directory: {parent_dir}")
            # Read metadata.json
            with open(metadata_file, "r") as f:
                metadata = json.load(f)

            sample = metadata["metadata"]
            image = sample["task_id"]

            if not image:
                logger.warning("No image specified for sample, skipping")
                continue

            logger.info(f"Evaluating sample: {image}")

            # Run evaluation in async context
            eval_results = self._evaluate_single_sample(
                sample=sample, parent_dir=metadata_file.parent
            )
            success = eval_results.get("success", False)
            if success:
                final_success_count += 1
            results.append(eval_results)
        # Save all results to a summary file
        results.insert(
            0, {"total_samples": len(results), "successful_fixes": final_success_count}
        )
        logger.info(
            f"Evaluation completed: {final_success_count}/{len(results)} successful fixes"
        )
        summary_path = self.output_dir / "evaluation_summary.json"
        with open(summary_path, "w") as f:
            json.dump(results, f, indent=2)
        logger.info(f"All evaluation results saved to: {summary_path}")


if __name__ == "__main__":
    fire.Fire(PatchAgent)
