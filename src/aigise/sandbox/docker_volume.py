import re

import docker
from loguru import logger


class DockerVolume:
    def __init__(self, volume: docker.models.volumes.Volume) -> None:
        self.volume = volume

    @classmethod
    def create(cls, name: str, **kwargs) -> "DockerVolume":
        client = docker.from_env()
        volume = client.volumes.create(name=name, **kwargs)
        logger.debug(f"DockerVolume create: {volume.name}")
        return cls(volume)

    def __enter__(self):
        return self.volume

    def _cleanup(self):
        try:
            self.volume.remove(force=True)
        except Exception as e:
            pass
        logger.debug(f"DockerVolume cleanup: {self.volume.name}")

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._cleanup()

    # def __del__(self):
    #     pass

    @classmethod
    def cleanup_matched(cls, pattern: str) -> None:
        """
        Removes all volumes whose name or ID matches the given regex pattern.

        Args:
            pattern (str): The regex pattern to match against volume names and IDs.
        """
        client = docker.from_env()
        for raw_volume in client.volumes.list():
            if re.search(pattern, raw_volume.name) or re.search(pattern, raw_volume.id):
                cls(volume=raw_volume)._cleanup()
