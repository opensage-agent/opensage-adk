import asyncio
import json
import logging
import os
import sys
import uuid
from pathlib import Path

# Add src to path
sys.path.append(str(Path(__file__).parent.parent.parent))

from google.adk import Agent

from aigise.evaluations import EvaluationTask
from aigise.evaluations.swe_bench_pro.swe_bench_pro import SweBenchPro
from aigise.session import get_aigise_session

# Set log level to INFO
logging.basicConfig(level=logging.INFO)


async def debug_environment():
    print("Initializing SweBenchPro...")
    # Initialize with valid agent_dir
    agent_dir = str(Path(os.getcwd()) / "examples/agents/bench_agent")
    print(f"Using agent_dir: {agent_dir}")

    # Use a specific output dir that we can check later
    output_dir = Path("/tmp/debug_swe_bench_env")
    if output_dir.exists():
        import shutil

        shutil.rmtree(output_dir)
    # output_dir.mkdir(parents=True, exist_ok=True) # Let SweBenchPro create it

    eval_instance = SweBenchPro(agent_dir=agent_dir, output_dir=str(output_dir))

    print(
        f"Loading dataset: {eval_instance.dataset_path} (split: {eval_instance.dataset_hf_split})..."
    )
    dataset = eval_instance._get_dataset()

    if len(dataset) == 0:
        print("Error: Dataset is empty.")
        return

    # Pick the first sample (same as previous run for consistency)
    # The logs showed instance_gravitational__teleport-c782838c3a174fdff80cafd8cd3b1aa4dae8beb2
    # iterating to find it or just picking one
    # target_id = "instance_gravitational__teleport-c782838c3a174fdff80cafd8cd3b1aa4dae8beb2"
    sample = None
    # for s in dataset:
    #     # print base commit and instance_id
    #     print(s["instance_id"], s["base_commit"])
    #     if s["instance_id"] == target_id:
    #           sample = s
    #           break
    # input("DEBUG: Press Enter to continue...")
    selected_index = 21
    if not sample:
        print(f"Using index {selected_index}")
        sample = dataset[selected_index]

    instance_id = eval_instance._get_sample_id(sample)
    print(f"\nProcessing Task: {instance_id}")
    # print(f"\nSample: {json.dumps(sample, indent=2)}")

    # Create task object
    session_id = str(uuid.uuid4())
    task_output_dir = output_dir / instance_id

    print("Task prompt: ", eval_instance._get_user_msg_first(sample))

    task = EvaluationTask(
        session_id=session_id,
        sample=sample,
        task_name=instance_id,
        input_data_path="",
        prompt=eval_instance._get_user_msg_first(sample),
        # prompt="Please append 'TEST_MODIFICATION' to the end of README.md file and then call finish_task.",
        output_dir=str(task_output_dir),
        cache_dir="",
        output_dir_in_sandbox="/workspace",
        metadata=sample,
        config_template_path=eval_instance.config_template_path,
    )

    try:
        # We will follow the exact flow of _generate_sample but manually to control steps or just call it?
        # Calling _generate_sample is better as it exercises the full path including customized_modify_and_save_results

        # NOTE: _generate_sample is a method of Evaluation class.
        # But wait, _generate_sample is NOT creating the agent, it calls _prepare_agent.
        # swe_bench_pro.py doesn't override _prepare_agent.
        # Base _prepare_agent calls self.mk_agent which calls user provided mk_agent.

        # So we can just call _generate_sample!

        print("\n=== STARTING GENERATION ===")
        result = await eval_instance._generate_sample(task)
        print("=== GENERATION COMPLETED ===")

        print(f"\nResult Metadata: {result.get('metadata', {}).keys()}")

        # Manually trigger save results to see if predictions.json is generated
        # The base class usually calls _collect_outputs which calls customized_modify_and_save_results if implemented?
        # Actually _generate_sample calls _collect_outputs.
        # And Evaluation._collect_outputs calls self.customized_modify_and_save_results ONLY IF implemented.
        # Wait, the base class implementation of _collect_outputs:
        # return { ... }
        # It does NOT call customized_modify_and_save_results.
        # Usually the orchestrator (evaluate function in main.py) calls it after gathering all results.

        # So we should call it manually here to verify patch extraction.
        print("\nRunning Result Aggregation...")
        eval_instance.customized_modify_and_save_results(
            results=[result], failed_samples=[], mode="test"
        )

        # Check for predictions.json
        pred_file = output_dir / "predictions.json"
        if pred_file.exists():
            print(f"\nSUCCESS: Predictions saved to {pred_file}")
            content = json.loads(pred_file.read_text())
            patch_len = 0
            if isinstance(content, list) and len(content) > 0:
                patch_len = len(content[0].get("patch", ""))
            elif isinstance(content, dict):
                patch_len = len(content.get(instance_id, ""))
            print(f"Patch content length for {instance_id}: {patch_len}")
        else:
            print("\nFAILURE: Predictions file was not created.")

        # Trigger Evaluation
        print("\n=== STARTING EVALUATION ===")
        # results_dir will be created by the evaluate method
        results_dir = output_dir / "results"
        eval_instance.evaluate(
            predictions_path=pred_file,  # Not strictly needed as class knows paths, but good for clarity
            results_dir=results_dir,
        )
        print("=== EVALUATION COMPLETED ===")

    except Exception as e:
        print(f"\nERROR: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(debug_environment())
