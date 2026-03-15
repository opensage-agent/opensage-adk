import random
from functools import partial
from typing import Any, Callable, Dict, Generator

from patchagent.agent.base import BaseAgent
from patchagent.agent.clike.common import CommonCLikeAgent
from patchagent.agent.java.common import CommonJavaAgent
from patchagent.lang import Lang
from patchagent.task import PatchTask


def _create_agent_generator(
    patchtask: PatchTask,
    model: str = "gpt-4o",
    fast: bool = False,
    stop_indicator: Callable[[], bool] = lambda: False,
) -> Generator["BaseAgent", None, None]:

    agent_class: type[CommonCLikeAgent | CommonJavaAgent]
    match patchtask.builder.language:
        case Lang.CLIKE:
            agent_class = CommonCLikeAgent
        case Lang.JVM:
            agent_class = CommonJavaAgent

    kwargs: Dict[str, Any] = {}
    if fast:
        kwargs["auto_hint"] = random.choice([True, False])
        kwargs["counterexample_num"] = 0
        kwargs["max_iterations"] = 15
        kwargs["temperature"] = random.random()
        if stop_indicator():
            return

        yield agent_class(patchtask, model=model, **kwargs)
    else:
        for counterexample_num in [0, 3]:
            for temperature in [0, 0.3, 0.7, 1]:
                for auto_hint in [True, False]:
                    if stop_indicator():
                        return

                    kwargs["auto_hint"] = auto_hint
                    kwargs["counterexample_num"] = counterexample_num
                    kwargs["temperature"] = temperature
                    yield agent_class(patchtask, model=model, **kwargs)


def agent_generator(
    model: str = "gpt-4o",
    fast: bool = False,
    stop_indicator: Callable[[], bool] = lambda: False,
) -> Callable[[PatchTask], Generator["BaseAgent", None, None]]:
    return partial(_create_agent_generator, model=model, fast=fast, stop_indicator=stop_indicator)
