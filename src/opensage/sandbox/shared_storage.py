from __future__ import annotations

import contextlib
import os
from types import SimpleNamespace
from typing import Iterator

from opensage.config import OpenSageConfig, OpenSandboxConfig
from opensage.sandbox.k8s_sandbox import K8sSandbox
from opensage.sandbox.remote_docker_sandbox import RemoteDockerSandbox


@contextlib.contextmanager
def _temporary_env(overrides: dict[str, str | None]) -> Iterator[None]:
    previous = {key: os.environ.get(key) for key in overrides}
    try:
        for key, value in overrides.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _build_remote_docker_config(
    opensandbox_config: OpenSandboxConfig,
) -> SimpleNamespace:
    return SimpleNamespace(
        sandbox=SimpleNamespace(
            docker_host=opensandbox_config.docker_host,
            docker_remote_host=opensandbox_config.docker_remote_host,
        )
    )


class SharedStorage:
    """Helpers for provisioning OpenSage shared storage on remote runtimes."""

    @classmethod
    def create_for_opensandbox(
        cls,
        session_id: str,
        init_data_path,
        tools_top_roots: set[str] | None,
        config: OpenSageConfig,
    ) -> tuple[str, str, str]:
        opensandbox_config = cls._require_opensandbox_config(config)
        if opensandbox_config.runtime_type == "docker":
            RemoteDockerSandbox.set_config(
                _build_remote_docker_config(opensandbox_config)
            )
            return RemoteDockerSandbox.create_shared_volume(
                session_id, init_data_path, tools_top_roots
            )
        if opensandbox_config.runtime_type == "kubernetes":
            with _temporary_env(
                {
                    K8sSandbox.DEFAULT_NAMESPACE_ENV: opensandbox_config.namespace,
                    K8sSandbox.DEFAULT_CONTEXT_ENV: opensandbox_config.context,
                    K8sSandbox.DEFAULT_KUBECONFIG_ENV: opensandbox_config.kubeconfig,
                }
            ):
                return K8sSandbox.create_shared_volume(
                    session_id, init_data_path, tools_top_roots
                )
        raise ValueError(
            f"Unsupported OpenSandbox runtime_type: {opensandbox_config.runtime_type}"
        )

    @classmethod
    def delete_for_opensandbox(
        cls,
        scripts_volume_id: str | None,
        data_volume_id: str | None,
        tools_volume_id: str | None,
        config: OpenSageConfig,
    ) -> None:
        opensandbox_config = cls._require_opensandbox_config(config)
        if opensandbox_config.runtime_type == "docker":
            RemoteDockerSandbox.set_config(
                _build_remote_docker_config(opensandbox_config)
            )
            RemoteDockerSandbox.delete_shared_volumes(
                scripts_volume_id=scripts_volume_id,
                data_volume_id=data_volume_id,
                tools_volume_id=tools_volume_id,
            )
            return
        if opensandbox_config.runtime_type == "kubernetes":
            with _temporary_env(
                {
                    K8sSandbox.DEFAULT_NAMESPACE_ENV: opensandbox_config.namespace,
                    K8sSandbox.DEFAULT_CONTEXT_ENV: opensandbox_config.context,
                    K8sSandbox.DEFAULT_KUBECONFIG_ENV: opensandbox_config.kubeconfig,
                }
            ):
                K8sSandbox.delete_shared_volumes(
                    scripts_volume_id=scripts_volume_id,
                    data_volume_id=data_volume_id,
                    tools_volume_id=tools_volume_id,
                )
            return
        raise ValueError(
            f"Unsupported OpenSandbox runtime_type: {opensandbox_config.runtime_type}"
        )

    @staticmethod
    def _require_opensandbox_config(config: OpenSageConfig) -> OpenSandboxConfig:
        if not config.sandbox or not config.sandbox.opensandbox:
            raise ValueError(
                "sandbox.opensandbox configuration is required for opensandbox backend"
            )
        return config.sandbox.opensandbox
