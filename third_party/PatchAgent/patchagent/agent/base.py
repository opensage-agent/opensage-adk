from typing import Optional

from openai import APIError
from pydantic_core import ValidationError

from patchagent.logger import logger


class BaseAgentException(Exception): ...


class AgentStopException(BaseAgentException): ...


class PatchFoundException(BaseAgentException): ...


class BaseAgent:
    def __init__(self, retry: int = 3):
        self.retry = retry

    def _run_once(self) -> Optional[str]:
        try:
            self.apply()
        except ValidationError as e:
            logger.info(f"[🛑] Validation error: {e}")
        except AgentStopException as e:
            logger.info(f"[🛑] Agent stopped because {e}")
        except PatchFoundException as e:
            logger.info("[🎉] Patch is found")
            return str(e)

        return None

    def __call__(self) -> Optional[str]:
        for _ in range(self.retry):
            try:
                return self._run_once()
            except APIError as e:
                logger.info(f"[🛑] API error: {e}")

        return self._run_once()

    def apply(self) -> None:
        raise NotImplementedError
