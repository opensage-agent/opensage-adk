import abc
import asyncio
import datetime
import importlib
import inspect
import json
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

import datasets
import fire
import google.adk as adk
import jsonpickle
import tqdm.asyncio
from google.adk.agents.run_config import RunConfig
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from loguru import logger

from aigise.sandbox.docker_config import DockerConfig
from aigise.sandbox.docker_volume import DockerVolume
from aigise.sandbox_manager import SandboxManager


@dataclass
class Task(abc.ABC):
    dataset_path: str = "org/dataset"
    dataset_hf_split: str = "train"
    output_dir: str | None = None
    agent: str = "mini"
    max_llm_calls: int = 128
    max_workers: int = 1
    model: str = "openai/gpt-5-mini"

    def __post_init__(self) -> None:
        if not self.output_dir:
            self.output_dir: Path = (
                Path("evals")
                / self.__class__.__name__.lower()
                / datetime.datetime.now().strftime("%y%m%d_%H%M%S")
            )
            self.output_dir.mkdir(parents=True)
        else:
            self.output_dir = Path(self.output_dir)
            if self.output_dir.exists():
                flag = (
                    input(f"{self.output_dir} already exists, continue? (y/n): ")
                    .strip()
                    .lower()
                )
                if flag != "y":
                    print(f"Exiting...")
                    exit(0)
        self.user_id = str(self.output_dir).replace("/", "_")
        agent_module = importlib.import_module(f"aigise.agents.{self.agent}.agent")
        for name, obj in inspect.getmembers(agent_module):
            if name == "mk_agent":
                self.mk_agent_original: Callable = obj
                break
        else:
            raise ValueError(f"No `mk_agent` found in aigise.agents.{self.agent}.agent")

    def _get_dataset(self) -> datasets.Dataset:
        if Path(self.dataset_path).exists():
            if Path(self.dataset_path).is_dir():
                dataset = datasets.load_from_disk(self.dataset_path)
            else:
                dataset = datasets.load_dataset(
                    "json", data_files=self.dataset_path, split="train"
                )
        else:
            dataset = datasets.load_dataset(
                self.dataset_path, split=self.dataset_hf_split
            )
        return dataset

    def generate(self) -> None:
        dataset = self._get_dataset()
        semaphore = asyncio.Semaphore(self.max_workers)
        # run samples with concurrency
        asyncio.run(
            tqdm.asyncio.tqdm_asyncio.gather(
                *[
                    self._generate_sample(sample=sample, semaphore=semaphore)
                    for sample in dataset
                ],
                desc="_generate_sample",
                total=len(dataset),
            )
        )
        # datasets.Dataset.from_list(samples_gen).to_json(self.output_dir / 'gen.jsonl')

    def _get_sample_id(self, sample: dict) -> str:
        return sample["task_id"]

    def _get_session_id(self, sample: dict) -> str:
        return str(self.output_dir / self._get_sample_id(sample)).replace("/", "_")

    def _get_image_name(self, sample: dict) -> dict:
        return "ubuntu:latest"

    def _get_workdir_container(self, sample: dict) -> str:
        return "/aigise_workspace"

    def _get_user_msg_first(self, sample: dict) -> str:
        return f"Complete the task provided at {self._get_workdir_container(sample)} in a sandboxed environment. All task-related files are provided in this directory, so you do not need to look elsewhere."

    def _init_workdir(self, sample: dict, tmp_workdir: str) -> None:
        with open(Path(tmp_workdir) / "input.txt", "w") as f:
            f.write("Hello, World!\n")

    def _mk_agent(self, sample: dict) -> adk.Agent:
        return self.mk_agent_original(
            image_name=self._get_image_name(sample),
            model_name=self.model,
        )

    async def _generate_sample(
        self, sample: dict, semaphore: asyncio.Semaphore
    ) -> dict:
        async with semaphore:
            # prepare information
            session_id = self._get_session_id(sample)
            app_name = self.__class__.__name__.lower()
            image_name = self._get_image_name(sample)
            workdir_container = self._get_workdir_container(sample)
            volume_name = f"aigise_vol_{session_id}"
            volume_arg = f"{volume_name}:{workdir_container}:rw"
            initial_state = {
                "workspace_volume_arg": volume_arg,
            }  # TODO mount this workspace volume in containers that need it
            user_msg_first = self._get_user_msg_first(sample)
            # TODO: poc is already there at /tmp/poc in arvo containers; we should remove them to prevent data leakage
            # create a volume for workdir that is shared across containers
            with DockerVolume.create(name=volume_name) as volume:
                # generate cybergym workdir
                with (
                    tempfile.TemporaryDirectory() as tmp_workdir,
                ):
                    sandbox = SandboxManager.get_sandbox(
                        session_id=session_id,
                        docker_config=DockerConfig(
                            image="ubuntu:latest",
                            volumes=[volume_arg],
                        ),
                    )
                    await asyncio.to_thread(
                        self._init_workdir, sample=sample, tmp_workdir=tmp_workdir
                    )
                    # upload workdir files to the volume via the container
                    sandbox.copy_directory_to_container(
                        src_path=tmp_workdir, dst_path=workdir_container
                    )

                # prepare to run the agent
                agent = self._mk_agent(sample=sample)
                session_service = InMemorySessionService()
                runner = Runner(
                    agent=agent,
                    app_name=app_name,
                    session_service=session_service,
                )
                run_config = RunConfig(max_llm_calls=self.max_llm_calls)
                session = await session_service.create_session(
                    app_name=app_name,
                    user_id=self.user_id,
                    session_id=session_id,
                    state=initial_state,
                )
                try:
                    async for event in runner.run_async(
                        user_id=self.user_id,
                        session_id=session_id,
                        run_config=run_config,
                        new_message=types.Content(
                            role="user", parts=[types.Part(text=user_msg_first)]
                        ),
                    ):
                        logger.debug(event)
                except Exception as e:
                    logger.warning(e)
                finally:
                    SandboxManager.cleanup_sandbox(session_id=session_id)

            info = {
                "sample": sample,
                "session": session.model_dump(),
            }  # TODO if session logs of subagents are stored in the main session via callbacks, they will be here too
            (gen_dir := Path(self.output_dir / "gen")).mkdir(exist_ok=True)
            with open(gen_dir / f"{self._get_sample_id(sample)}.json", "w") as f:
                json.dump(json.loads(jsonpickle.encode(info)), f, indent=2)

            return info

    def evaluate(self) -> None:
        raise NotImplementedError

    def run(self) -> dict:
        self.generate()
        self.evaluate()


if __name__ == "__main__":
    fire.Fire(Task)
