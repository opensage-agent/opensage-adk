"""K8s sandbox implementation using kubectl and K8s Python client."""

from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import re
import shlex
import shutil
import subprocess
import tarfile
import tempfile
import time
from pathlib import Path
from typing import Any, Awaitable, Dict, Optional

import yaml

from opensage.config import ContainerConfig
from opensage.utils.bash_tools_staging import build_bash_tools_staging_dir
from opensage.utils.parser import get_function_info

from .base_sandbox import BaseSandbox, SandboxState

logger = logging.getLogger(__name__)


class K8sSandbox(BaseSandbox):
    """K8s sandbox representing a specific container within a Pod."""

    backend_type = "k8s"

    DEFAULT_NAMESPACE_ENV = "OPENSAGE_K8S_NAMESPACE"
    DEFAULT_CONTEXT_ENV = "OPENSAGE_K8S_CONTEXT"
    DEFAULT_KUBECONFIG_ENV = "OPENSAGE_K8S_KUBECONFIG"
    INIT_CONTAINER_IMAGE_ENV = "OPENSAGE_K8S_INIT_IMAGE"
    DEFAULT_INIT_IMAGE = "alpine:3.19"
    DEFAULT_PVC_STORAGE_REQUEST = os.getenv("OPENSAGE_K8S_PVC_SIZE", "1Gi")

    def __init__(
        self,
        container_config: ContainerConfig,
        session_id: str = None,
        backend_type: str = None,
        sandbox_type: str = None,
        pod_name: str = None,
        container_name: str = None,
    ):
        """Initialize K8sSandbox.

        Raises:
          ValueError: Raised when this operation fails."""
        super().__init__(container_config, session_id, self.backend_type, sandbox_type)

        self.extra: Dict[str, Any] = container_config.extra or {}
        self.namespace = self.extra.get("namespace") or self.extra.get("k8s_namespace")
        if not self.namespace:
            self.namespace = os.getenv(self.DEFAULT_NAMESPACE_ENV, "default")
        self.context = self.extra.get("context") or self.extra.get("k8s_context")
        if not self.context:
            self.context = os.getenv(self.DEFAULT_CONTEXT_ENV)
        self.kubeconfig = (
            self.extra.get("kubeconfig")
            or os.getenv(self.DEFAULT_KUBECONFIG_ENV)
            or os.getenv("KUBECONFIG")
        )

        existing_pod = container_config.pod_name or pod_name
        existing_container = container_config.container_name or container_name

        if existing_pod and existing_container:
            self.pod_name = existing_pod
            self.container_name = existing_container
            self._connect_to_existing_pod_container(existing_pod, existing_container)
        else:
            raise ValueError(
                "K8sSandbox requires existing pod_name and container_name, or use launch_all_sandboxes to create new Pod"
            )

        self._detected_shell: Optional[str] = None
        self._pod_deleted = False

    # ------------------------------------------------------------------
    # Helper utilities
    # ------------------------------------------------------------------
    @staticmethod
    def _sanitize_name(value: str, fallback: str = "resource") -> str:
        slug = re.sub(r"[^a-z0-9-]", "-", value.lower())
        slug = re.sub(r"-+", "-", slug).strip("-")
        if not slug:
            slug = fallback
        if len(slug) > 63:
            slug = slug[:63].rstrip("-")
        return slug or fallback

    @staticmethod
    def _build_kubectl_command(
        args: list[str],
        *,
        namespace: Optional[str],
        context: Optional[str],
        kubeconfig: Optional[str],
        include_namespace: bool = True,
    ) -> list[str]:
        cmd = ["kubectl"]
        if kubeconfig:
            cmd.extend(["--kubeconfig", kubeconfig])
        if context:
            cmd.extend(["--context", context])
        if include_namespace and namespace:
            cmd.extend(["-n", namespace])
        cmd.extend(args)
        return cmd

    def _run_kubectl(
        self,
        args: list[str],
        *,
        include_namespace: bool = True,
        check: bool = True,
        capture_output: bool = True,
        text: bool = True,
        input_data: Optional[str | bytes] = None,
        timeout: int | None = None,
    ) -> subprocess.CompletedProcess:
        cmd = self._build_kubectl_command(
            args,
            namespace=self.namespace,
            context=self.context,
            kubeconfig=self.kubeconfig,
            include_namespace=include_namespace,
        )
        use_text = text
        if isinstance(input_data, bytes):
            use_text = False
        result = subprocess.run(
            cmd,
            capture_output=capture_output,
            text=use_text,
            input=input_data,
            timeout=timeout,
        )
        if check and result.returncode != 0:
            stderr = (
                result.stderr
                if text
                else result.stderr.decode("utf-8", errors="ignore")
            )
            raise RuntimeError(f"kubectl {' '.join(args)} failed: {stderr.strip()}")
        return result

    @classmethod
    def _run_kubectl_class(
        cls,
        args: list[str],
        *,
        namespace: Optional[str],
        context: Optional[str],
        kubeconfig: Optional[str],
        include_namespace: bool = True,
        check: bool = True,
        capture_output: bool = True,
        text: bool = True,
        input_data: Optional[str | bytes] = None,
    ) -> subprocess.CompletedProcess:
        cmd = cls._build_kubectl_command(
            args,
            namespace=namespace,
            context=context,
            kubeconfig=kubeconfig,
            include_namespace=include_namespace,
        )
        use_text = text
        if isinstance(input_data, bytes):
            use_text = False
        logger.info(f"Running kubectl command: {' '.join(cmd)}")
        result = subprocess.run(
            cmd,
            capture_output=capture_output,
            text=use_text,
            input=input_data,
        )
        if check and result.returncode != 0:
            stderr = (
                result.stderr
                if text
                else result.stderr.decode("utf-8", errors="ignore")
            )
            raise RuntimeError(f"kubectl {' '.join(args)} failed: {stderr.strip()}")
        return result

    def _run_kubectl_exec(
        self,
        command: list[str],
        *,
        check: bool = False,
        text: bool = False,
        timeout: int | None = None,
    ) -> subprocess.CompletedProcess:
        args = ["exec", self.pod_name]
        if self.container_name:
            args.extend(["-c", self.container_name])
        args.append("--")
        args.extend(command)
        return self._run_kubectl(
            args,
            check=check,
            capture_output=True,
            text=text,
            timeout=timeout,
        )

    @classmethod
    def _get_default_storage_class(
        cls,
        *,
        namespace: Optional[str],
        context: Optional[str],
        kubeconfig: Optional[str],
    ) -> Optional[str]:
        try:
            # Resolve tolerations from session config using session_id (volume_name_prefix)
            def _resolve_tolerations_from_session(
                session_id: str,
            ) -> Optional[list[dict]]:
                try:
                    from opensage.session.opensage_session import (
                        get_opensage_session,  # lazy import
                    )

                    sess = get_opensage_session(session_id)
                    cfg = getattr(sess, "config", None)
                    sbx_cfg = getattr(cfg, "sandbox", None)
                    # 1) Prefer global sandbox-level tolerations if provided
                    global_tol = getattr(sbx_cfg, "tolerations", None)
                    if isinstance(global_tol, list) and global_tol:
                        return global_tol
                    sandboxes = getattr(sbx_cfg, "sandboxes", {}) or {}
                    merged: list[dict] = []
                    for c in sandboxes.values():
                        extra = getattr(c, "extra", {}) or {}
                        t = extra.get("tolerations")
                        if isinstance(t, list):
                            for item in t:
                                if isinstance(item, dict) and item not in merged:
                                    merged.append(item)
                    return merged or None
                except Exception:
                    pass
                return None

            tolerations = _resolve_tolerations_from_session(volume_name_prefix)
            result = cls._run_kubectl_class(
                ["get", "storageclass", "-o", "json"],
                namespace=namespace,
                context=context,
                kubeconfig=kubeconfig,
                include_namespace=False,
            )
            data = json.loads(result.stdout)
            for item in data.get("items", []):
                annotations = item.get("metadata", {}).get("annotations", {})
                if (
                    annotations.get("storageclass.kubernetes.io/is-default-class")
                    == "true"
                    or annotations.get(
                        "storageclass.beta.kubernetes.io/is-default-class"
                    )
                    == "true"
                ):
                    return item.get("metadata", {}).get("name")
        except Exception:
            logger.debug("Failed to detect default storage class", exc_info=True)
        return None

    @classmethod
    def _get_storage_class_binding_mode(
        cls,
        storage_class: str,
        *,
        namespace: Optional[str],
        context: Optional[str],
        kubeconfig: Optional[str],
    ) -> Optional[str]:
        try:
            result = cls._run_kubectl_class(
                ["get", "storageclass", storage_class, "-o", "json"],
                namespace=namespace,
                context=context,
                kubeconfig=kubeconfig,
                include_namespace=False,
            )
            data = json.loads(result.stdout)
            binding_mode = data.get("volumeBindingMode")
            if not binding_mode:
                binding_mode = (
                    data.get("metadata", {})
                    .get("annotations", {})
                    .get("volumeBindingMode")
                )
            return binding_mode
        except Exception:
            logger.debug(
                f"Failed to retrieve binding mode for storage class {storage_class}",
                exc_info=True,
            )
            return None

    @classmethod
    def _pvc_uses_wait_for_first_consumer(
        cls,
        pvc_name: str,
        *,
        namespace: Optional[str],
        context: Optional[str],
        kubeconfig: Optional[str],
    ) -> bool:
        try:
            pvc_result = cls._run_kubectl_class(
                ["get", "pvc", pvc_name, "-o", "json"],
                namespace=namespace,
                context=context,
                kubeconfig=kubeconfig,
            )
            pvc_data = json.loads(pvc_result.stdout)
        except Exception:
            logger.debug(
                f"Unable to inspect PVC {pvc_name} for binding mode", exc_info=True
            )
            return False

        storage_class = pvc_data.get("spec", {}).get(
            "storageClassName"
        ) or cls._get_default_storage_class(
            namespace=namespace, context=context, kubeconfig=kubeconfig
        )

        binding_mode = None
        if storage_class:
            binding_mode = cls._get_storage_class_binding_mode(
                storage_class,
                namespace=namespace,
                context=context,
                kubeconfig=kubeconfig,
            )

        if not binding_mode:
            # Fallback: inspect PVC conditions for explicit reason
            for condition in pvc_data.get("status", {}).get("conditions", []) or []:
                if condition.get("reason") == "WaitForFirstConsumer":
                    return True
            return False

        return binding_mode.lower() == "waitforfirstconsumer"

    # ------------------------------------------------------------------
    # Pod/Container lifecycle
    # ------------------------------------------------------------------
    def _connect_to_existing_pod_container(self, pod_name: str, container_name: str):
        result = self._run_kubectl(["get", "pod", pod_name, "-o", "json"])
        pod_data = json.loads(result.stdout)
        phase = pod_data.get("status", {}).get("phase")
        if phase != "Running":
            raise RuntimeError(
                f"Pod {pod_name} not running (current phase: {phase or 'unknown'})"
            )

        containers = {
            container.get("name")
            for container in pod_data.get("spec", {}).get("containers", [])
        }
        if container_name not in containers:
            raise RuntimeError(
                f"Container {container_name} not found in pod {pod_name}: {containers}"
            )

        statuses = pod_data.get("status", {}).get("containerStatuses", [])
        for status in statuses:
            if status.get("name") == container_name and not status.get("ready", False):
                raise RuntimeError(
                    f"Container {container_name} in pod {pod_name} is not ready"
                )

        logger.info(
            f"Connected to existing pod {pod_name} (namespace={self.namespace}) container {container_name}"
        )

    # ------------------------------------------------------------------
    # File copy helpers
    # ------------------------------------------------------------------
    def _kubectl_cp(
        self,
        source: str,
        destination: str,
        *,
        to_container: bool,
    ) -> None:
        args = ["cp"]
        if self.container_name:
            args.extend(["-c", self.container_name])
        args.extend([source, destination])
        result = self._run_kubectl(args, text=True)
        if result.returncode != 0:
            raise RuntimeError(
                f"kubectl cp failed ({source} -> {destination}): {result.stderr}"
            )

    # ------------------------------------------------------------------
    # BaseSandbox required API
    # ------------------------------------------------------------------
    def copy_file_from_container(self, src_path: str, dst_path: str):
        Path(dst_path).parent.mkdir(parents=True, exist_ok=True)
        tmp_dir = Path(tempfile.mkdtemp(prefix="opensage-k8s-copy-"))
        tmp_target = tmp_dir / Path(src_path).name
        try:
            self._kubectl_cp(
                f"{self.pod_name}:{src_path}", str(tmp_target), to_container=False
            )
            shutil.move(tmp_target, dst_path)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def copy_file_to_container(self, local_path: str, container_path: str):
        parent_dir = os.path.dirname(container_path) or "/"
        self.run_command_in_container(
            f"mkdir -p {shlex.quote(parent_dir)} && rm -f {shlex.quote(container_path)}"
        )
        self._kubectl_cp(
            str(Path(local_path).resolve()),
            f"{self.pod_name}:{container_path}",
            to_container=True,
        )

    def extract_file_from_container(self, filepath: str) -> str:
        result = self._run_kubectl_exec(
            ["/bin/sh", "-c", f"cat {shlex.quote(filepath)}"],
            text=False,
        )
        if result.returncode != 0:
            stderr = (result.stderr or b"").decode("utf-8", errors="ignore")
            raise RuntimeError(f"Failed to extract file {filepath}: {stderr}")
        return (result.stdout or b"").decode("latin-1", errors="replace")

    def run_command_in_container(
        self, command: str | list[str], timeout: int | None = None
    ) -> tuple[str, int]:
        # Prepare command with timeout wrapper if specified
        if isinstance(command, list):
            # List command: wrap with timeout if needed
            if timeout:
                full_command = ["timeout", f"{timeout}s"] + command
            else:
                full_command = command
        else:
            # String command: wrap with shell
            shell = self._detect_shell()
            if timeout:
                # Use timeout command with nested shell to handle shell built-ins
                import shlex

                full_command = [
                    shell,
                    "-c",
                    f"timeout {timeout}s {shell} -c {shlex.quote(command)}",
                ]
            else:
                full_command = [shell, "-c", command]

        result = self._run_kubectl_exec(full_command, text=False, timeout=None)
        stdout = result.stdout or b""
        stderr = result.stderr or b""
        output_text = (stdout + stderr).decode("latin-1", errors="replace")
        returncode = result.returncode

        # timeout command returns exit code 124 when it times out
        if returncode == 124:
            output_text = f"Command timed out after {timeout} seconds\n{output_text}"

        return output_text, returncode

    def get_work_dir(self) -> str:
        output, _ = self.run_command_in_container("pwd")
        return output.strip()

    def copy_directory_from_container(self, src_path: str, dst_path: str):
        dst = Path(dst_path)
        if dst.exists():
            shutil.rmtree(dst_path)
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp_dir = Path(tempfile.mkdtemp(prefix="opensage-k8s-dir-"))
        try:
            self._kubectl_cp(
                f"{self.pod_name}:{src_path}", str(tmp_dir), to_container=False
            )
            extracted = tmp_dir / Path(src_path).name
            if extracted.exists():
                shutil.move(str(extracted), dst_path)
            else:
                shutil.move(str(tmp_dir), dst_path)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def copy_directory_to_container(self, src_path: str, dst_path: str):
        self.run_command_in_container(f"mkdir -p {shlex.quote(dst_path)}")
        src = Path(src_path).resolve()
        source_spec = f"{src}/." if src.is_dir() else str(src)
        self._kubectl_cp(source_spec, f"{self.pod_name}:{dst_path}", to_container=True)

    def delete_container(self, max_wait: int = 10):
        if self._pod_deleted:
            return
        try:
            self._run_kubectl(
                [
                    "delete",
                    "pod",
                    self.pod_name,
                    "--ignore-not-found=true",
                    "--grace-period=0",
                    "--force",
                ],
                text=True,
            )
            for _ in range(max_wait):
                result = self._run_kubectl(
                    ["get", "pod", self.pod_name],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                if result.returncode != 0:
                    break
                time.sleep(1)
            self._pod_deleted = True
        except Exception as exc:
            logger.warning(f"Failed to delete pod {self.pod_name}: {exc}")

    def extract_file_from_container_bytes(self, filepath: str) -> bytes:
        result = self._run_kubectl_exec(
            ["/bin/sh", "-c", f"cat {shlex.quote(filepath)}"],
            text=False,
        )
        if result.returncode != 0:
            stderr = (result.stderr or b"").decode("utf-8", errors="ignore")
            raise RuntimeError(f"Failed to extract {filepath}: {stderr}")
        return result.stdout or b""

    def create_tar_bytes(self, file_content: str, arcname: str) -> bytes:
        tar_stream = io.BytesIO()
        with tarfile.open(fileobj=tar_stream, mode="w") as tar:
            file_bytes = file_content.encode()
            tarinfo = tarfile.TarInfo(name=arcname)
            tarinfo.size = len(file_bytes)
            tar.addfile(tarinfo, io.BytesIO(file_bytes))
        tar_stream.seek(0)
        return tar_stream.read()

    def patch_search_replace(self, file: str, search: str, replace: str):
        file_content = self.extract_file_from_container(file)
        modified_content = file_content.replace(search, replace)
        with tempfile.NamedTemporaryFile("w", delete=False) as tmp_file:
            tmp_file.write(modified_content)
            tmp_path = tmp_file.name
        try:
            self.copy_file_to_container(tmp_path, file)
        finally:
            os.unlink(tmp_path)

    def patch_file_func(self, files_func_to_content: dict[str, str], lang: str = "c"):
        for key, new_function_content in files_func_to_content.items():
            parts = key.split("__xx__")
            if len(parts) != 2:
                logger.warning(
                    f"Key {key} is not in the correct format. Expected format: 'filepath__xx__functionname'"
                )
                continue
            filepath, function_name = parts
            file_content = self.extract_file_from_container(filepath)
            functions = get_function_info(file_content, lang)
            if function_name not in functions:
                logger.warning(
                    f"Initial try, Function {function_name} not found in file {filepath}"
                )
                logger.info(
                    "Trying to do partial matching, the result may be inaccurate"
                )
                func_name = function_name.split("::")[-1]
                if func_name in functions:
                    function_name = func_name
                else:
                    potential_funcs = [
                        func
                        for func in functions
                        if func_name in func or func in func_name
                    ]
                    if potential_funcs:
                        potential_funcs.sort(key=lambda f: abs(len(f) - len(func_name)))
                        function_name = potential_funcs[0]
                    else:
                        logger.warning(
                            f"Function {function_name} finally not found in file {filepath}"
                        )
                        continue

            start_line, end_line = functions[function_name][0]
            start_index = start_line - 1
            end_index = end_line

            file_lines = file_content.splitlines()
            new_function_lines = new_function_content.splitlines()
            modified_lines = (
                file_lines[:start_index] + new_function_lines + file_lines[end_index:]
            )
            modified_file_content = "\n".join(modified_lines)

            with tempfile.NamedTemporaryFile("w", delete=False) as tmp_file:
                tmp_file.write(modified_file_content)
                tmp_path = tmp_file.name
            try:
                self.copy_file_to_container(tmp_path, filepath)
                logger.info(
                    f"Updated function {function_name} in file {filepath} in pod {self.pod_name}"
                )
            finally:
                os.unlink(tmp_path)

    def get_function_content(
        self, key: str, lang: str = "c", line_in_func: int = -1
    ) -> tuple[str, int, int]:
        parts = key.split("__xx__")
        if len(parts) != 2:
            logger.warning(
                f"Key {key} is not in the correct format. Expected format: 'filepath__xx__functionname'"
            )
            return "", -1, -1
        filepath, function_name = parts
        file_content = self.extract_file_from_container(filepath)
        functions = get_function_info(file_content, lang)
        if function_name not in functions:
            logger.warning(
                f"Initial try, Function {function_name} not found in file {filepath}"
            )
            logger.info("Trying to do partial matching, the result may be inaccurate")
            func_name = function_name.split("::")[-1]
            if func_name in functions:
                function_name = func_name
            else:
                return "", -1, -1
        if line_in_func != -1:
            for scope in functions[function_name]:
                start_line, end_line = scope
                if start_line <= line_in_func <= end_line:
                    break
        else:
            start_line, end_line = functions[function_name][-1]

        file_lines = file_content.splitlines()
        function_lines = file_lines[start_line - 1 : end_line]
        function_content = "\n".join(function_lines)
        return function_content, start_line, end_line

    def get_file_content(self, filepath: str) -> str:
        return self.extract_file_from_container(filepath)

    def _detect_shell(self) -> str:
        if self._detected_shell:
            return self._detected_shell
        for candidate in ("/bin/bash", "/bin/sh"):
            result = self._run_kubectl_exec(
                [candidate, "-c", "echo __opensage_shell__"],
                text=True,
            )
            if result.returncode == 0 and "__opensage_shell__" in result.stdout:
                self._detected_shell = candidate
                return candidate
        self._detected_shell = "/bin/sh"
        return self._detected_shell

    # ------------------------------------------------------------------
    # Volume creation helpers
    # ------------------------------------------------------------------
    @classmethod
    def _resolve_namespace_from_env(cls) -> str:
        return os.getenv(cls.DEFAULT_NAMESPACE_ENV, "default")

    @classmethod
    def _resolve_context_from_env(cls) -> Optional[str]:
        return os.getenv(cls.DEFAULT_CONTEXT_ENV)

    @classmethod
    def _resolve_kubeconfig_from_env(cls) -> Optional[str]:
        return os.getenv(cls.DEFAULT_KUBECONFIG_ENV) or os.getenv("KUBECONFIG")

    @classmethod
    def _copy_path_to_pvc(
        cls,
        source_path: Path,
        *,
        pvc_name: str,
        namespace: str,
        context: Optional[str],
        kubeconfig: Optional[str],
        tolerations: Optional[list[dict]] = None,
    ) -> None:
        init_pod_name = cls._sanitize_name(f"init-{pvc_name}-{int(time.time())}")
        init_manifest = {
            "apiVersion": "v1",
            "kind": "Pod",
            "metadata": {
                "name": init_pod_name,
                "namespace": namespace,
                "labels": {"app": "opensage-sandbox-init"},
            },
            "spec": {
                "restartPolicy": "Never",
                "containers": [
                    {
                        "name": "init",
                        "image": os.getenv(
                            cls.INIT_CONTAINER_IMAGE_ENV, cls.DEFAULT_INIT_IMAGE
                        ),
                        "command": ["/bin/sh", "-c", "sleep 3600"],
                        "volumeMounts": [{"name": "shared", "mountPath": "/target"}],
                    }
                ],
                "volumes": [
                    {
                        "name": "shared",
                        "persistentVolumeClaim": {"claimName": pvc_name},
                    }
                ],
            },
        }
        if tolerations:
            init_manifest["spec"]["tolerations"] = tolerations

        with tempfile.NamedTemporaryFile(
            "w", suffix=".yaml", delete=False
        ) as manifest_file:
            yaml.safe_dump(init_manifest, manifest_file)
            manifest_path = manifest_file.name

        try:
            cls._run_kubectl_class(
                ["apply", "-f", manifest_path],
                namespace=namespace,
                context=context,
                kubeconfig=kubeconfig,
                text=True,
            )
            cls._run_kubectl_class(
                [
                    "wait",
                    f"--for=condition=Ready",
                    f"pod/{init_pod_name}",
                    "--timeout=120s",
                ],
                namespace=namespace,
                context=context,
                kubeconfig=kubeconfig,
                text=True,
            )
            source_spec = str(source_path.resolve())
            if source_path.is_dir():
                source_spec = f"{source_spec}/."
            dest_spec = f"{init_pod_name}:/target"
            args = ["cp"]
            args.extend([source_spec, dest_spec])
            cls._run_kubectl_class(
                args,
                namespace=namespace,
                context=context,
                kubeconfig=kubeconfig,
                text=True,
                include_namespace=True,
            )
            cls._run_kubectl_class(
                [
                    "exec",
                    init_pod_name,
                    "-c",
                    "init",
                    "--",
                    "sh",
                    "-c",
                    "chmod -R 777 /target",
                ],
                namespace=namespace,
                context=context,
                kubeconfig=kubeconfig,
                text=True,
            )
        finally:
            os.unlink(manifest_path)
            cls._run_kubectl_class(
                ["delete", "pod", init_pod_name, "--ignore-not-found=true"],
                namespace=namespace,
                context=context,
                kubeconfig=kubeconfig,
                text=True,
                check=False,
            )

    @classmethod
    def _create_and_populate_pvc(
        cls,
        pvc_name: str,
        namespace: str,
        context: str,
        kubeconfig: str,
        source_path: Path = None,
        tolerations: Optional[list[dict]] = None,
    ) -> str:
        """Helper method to create a single PVC and populate it with data.

                Args:
                    pvc_name (str): Name of the PVC to create
                    source_path (Path): Local path containing data to copy into the PVC
                    namespace (str): Kubernetes namespace
                    context (str): Kubernetes context
                    kubeconfig (str): Path to kubeconfig file

        Raises:
          wait_error: Raised when this operation fails.
                Returns:
                    str: The PVC name that was created
        """
        import tarfile

        pvc_manifest = {
            "apiVersion": "v1",
            "kind": "PersistentVolumeClaim",
            "metadata": {
                "name": pvc_name,
                "namespace": namespace,
                "labels": {"app": "opensage-sandbox"},
            },
            "spec": {
                "accessModes": ["ReadWriteOnce"],
                "resources": {"requests": {"storage": cls.DEFAULT_PVC_STORAGE_REQUEST}},
            },
        }

        with tempfile.NamedTemporaryFile(
            "w", suffix=".yaml", delete=False
        ) as manifest_file:
            yaml.safe_dump(pvc_manifest, manifest_file)
            manifest_path = manifest_file.name

        try:
            cls._run_kubectl_class(
                ["apply", "-f", manifest_path],
                namespace=namespace,
                context=context,
                kubeconfig=kubeconfig,
                text=True,
            )
        finally:
            os.unlink(manifest_path)

        try:
            cls._run_kubectl_class(
                [
                    "wait",
                    "--for=condition=Bound",
                    f"pvc/{pvc_name}",
                    "--timeout=10s",
                ],
                namespace=namespace,
                context=context,
                kubeconfig=kubeconfig,
                text=True,
            )
        except RuntimeError as wait_error:
            if cls._pvc_uses_wait_for_first_consumer(
                pvc_name,
                namespace=namespace,
                context=context,
                kubeconfig=kubeconfig,
            ):
                logger.info(
                    f"PVC {pvc_name} is using WaitForFirstConsumer; skipping pre-bind wait"
                )
            else:
                raise wait_error

        if source_path and source_path.exists():
            if source_path.is_dir():
                files = list(source_path.iterdir())
                archive_candidates = [
                    file
                    for file in files
                    if file.is_file() and file.name.endswith(".tar.gz")
                ]
                selected_archive: Optional[Path] = None
                if archive_candidates:
                    # Prefer the shared-volume archive when multiple tarballs exist
                    preferred_archives = [
                        file
                        for file in archive_candidates
                        if "shared" in file.name.lower()
                        or "volume" in file.name.lower()
                    ]
                    selected_archive = (
                        preferred_archives[0]
                        if preferred_archives
                        else archive_candidates[0]
                    )

                if selected_archive:
                    with tempfile.TemporaryDirectory() as tmp_dir:
                        with tarfile.open(selected_archive, "r:gz") as tar:
                            tar.extractall(tmp_dir)
                        cls._copy_path_to_pvc(
                            Path(tmp_dir),
                            pvc_name=pvc_name,
                            namespace=namespace,
                            context=context,
                            kubeconfig=kubeconfig,
                            tolerations=tolerations,
                        )
                else:
                    cls._copy_path_to_pvc(
                        source_path,
                        pvc_name=pvc_name,
                        namespace=namespace,
                        context=context,
                        kubeconfig=kubeconfig,
                        tolerations=tolerations,
                    )
            elif source_path.is_file():
                cls._copy_path_to_pvc(
                    source_path,
                    pvc_name=pvc_name,
                    namespace=namespace,
                    context=context,
                    kubeconfig=kubeconfig,
                    tolerations=tolerations,
                )
            else:
                logger.warning(
                    f"Source path {source_path} is not accessible; skipping content initialization"
                )
        else:
            logger.info(
                f"No source data provided for PVC {pvc_name}, created empty volume"
            )

        # Maintain compatibility with tooling expecting a Docker volume name
        try:
            subprocess.run(
                ["docker", "volume", "create", pvc_name],
                capture_output=True,
                text=True,
                check=True,
            )
            logger.debug(f"Ensured Docker volume exists for {pvc_name}")
        except subprocess.CalledProcessError as docker_error:
            logger.debug(
                f"docker volume create failed for {pvc_name}: {docker_error.stderr}"
            )
        except FileNotFoundError:
            logger.debug("Docker CLI not available; skipping volume shim")
        except Exception as docker_exc:
            logger.debug(
                f"Unexpected error creating Docker volume {pvc_name}: {docker_exc}"
            )

        return pvc_name

    @classmethod
    def create_shared_volume(
        cls,
        volume_name_prefix: str,
        init_data_path: Path = None,
        tools_top_roots: set[str] | None = None,
    ) -> tuple[str, str, str]:
        """Create and initialize three shared PVCs.

                Creates three PVCs:
                1. Read-only PVC with sandbox scripts (mapped to /sandbox_scripts)
                2. Read-write PVC with user data (mapped to /shared)
                3. Read-write PVC with bash tools (mapped to /bash_tools)

                Args:
                    volume_name_prefix (str): Prefix for PVC names (e.g., session_id)
                    init_data_path (Path): Path to initial data to copy into the rw PVC (optional)
                    tools_top_roots (set[str] | None): Optional set of top-level bash_tools roots to stage.
                        If None, stage all bash tools (built-in + plugins).

        Raises:
          Exception: Raised when this operation fails.
                Returns:
                    tuple[str, str, str]: Tuple of (scripts_pvc_name, data_pvc_name, tools_pvc_name)
        """
        from opensage.utils.project_info import SRC_PATH

        namespace = cls._resolve_namespace_from_env()
        context = cls._resolve_context_from_env()
        kubeconfig = cls._resolve_kubeconfig_from_env()

        try:
            # Resolve tolerations strictly from sandbox.tolerations (global-only)
            tolerations: Optional[list[dict]] = None
            try:
                from opensage.session.opensage_session import (
                    get_opensage_session,  # lazy import
                )

                sess = get_opensage_session(volume_name_prefix)
                cfg = getattr(sess, "config", None)
                sbx_cfg = getattr(cfg, "sandbox", None)
                global_tol = getattr(sbx_cfg, "tolerations", None)
                if isinstance(global_tol, list) and global_tol:
                    tolerations = global_tol
            except Exception:
                tolerations = None

            # Create PVC names
            scripts_pvc_name = cls._sanitize_name(
                f"{volume_name_prefix}_sandbox_scripts"
            )
            data_pvc_name = cls._sanitize_name(f"{volume_name_prefix}_shared")
            tools_pvc_name = cls._sanitize_name(f"{volume_name_prefix}_bash_tools")

            # 1. Create and populate scripts PVC
            scripts_path = SRC_PATH / "sandbox_scripts"
            scripts_pvc_id = cls._create_and_populate_pvc(
                scripts_pvc_name,
                namespace,
                context,
                kubeconfig,
                scripts_path,
                tolerations=tolerations,
            )
            logger.info(
                f"Created sandbox scripts PVC: {scripts_pvc_id} from {scripts_path}"
            )

            # 2. Create and populate data PVC
            data_pvc_id = cls._create_and_populate_pvc(
                data_pvc_name,
                namespace,
                context,
                kubeconfig,
                init_data_path,
                tolerations=tolerations,
            )
            logger.info(f"Created shared data PVC: {data_pvc_id} from {init_data_path}")

            # 3. Create and populate tools PVC (built-in + plugin tools staged on host)
            with build_bash_tools_staging_dir(roots_to_copy=tools_top_roots) as staging:
                tools_pvc_id = cls._create_and_populate_pvc(
                    tools_pvc_name,
                    namespace,
                    context,
                    kubeconfig,
                    staging,
                    tolerations=tolerations,
                )
                logger.info(
                    "Created bash tools PVC: %s from staging dir %s (roots=%s)",
                    tools_pvc_id,
                    staging,
                    "ALL" if tools_top_roots is None else sorted(tools_top_roots),
                )

            # 3. Set permissions to 777 on data PVC to ensure write access
            import json
            import time

            chmod_pod_name = cls._sanitize_name(f"chmod-{data_pvc_name}")
            chmod_pod_spec = {
                "apiVersion": "v1",
                "kind": "Pod",
                "metadata": {
                    "name": chmod_pod_name,
                    "namespace": namespace,
                },
                "spec": {
                    "restartPolicy": "Never",
                    "containers": [
                        {
                            "name": "chmod-container",
                            "image": "alpine:latest",
                            "command": [
                                "sh",
                                "-c",
                                "chmod -R 777 /target && echo 'Permissions set successfully'",
                            ],
                            "volumeMounts": [
                                {
                                    "name": "target-volume",
                                    "mountPath": "/target",
                                }
                            ],
                        }
                    ],
                    "volumes": [
                        {
                            "name": "target-volume",
                            "persistentVolumeClaim": {
                                "claimName": data_pvc_id,
                            },
                        }
                    ],
                },
            }
            if tolerations:
                chmod_pod_spec["spec"]["tolerations"] = tolerations

            try:
                # Create chmod pod
                cls._run_kubectl_class(
                    ["apply", "-f", "-"],
                    input_data=json.dumps(chmod_pod_spec),
                    namespace=namespace,
                    context=context,
                    kubeconfig=kubeconfig,
                )
                logger.info(f"Created chmod pod: {chmod_pod_name}")

                # Wait for pod to complete (max 30 seconds)
                for _ in range(30):
                    result = cls._run_kubectl_class(
                        [
                            "get",
                            "pod",
                            chmod_pod_name,
                            "-o",
                            "jsonpath={.status.phase}",
                        ],
                        namespace=namespace,
                        context=context,
                        kubeconfig=kubeconfig,
                        check=False,
                    )
                    if result.stdout.strip() == "Succeeded":
                        logger.info(
                            f"Set permissions 777 on shared data PVC: {data_pvc_id}"
                        )
                        break
                    elif result.stdout.strip() == "Failed":
                        logger.warning(
                            f"Failed to set permissions on PVC {data_pvc_id}"
                        )
                        break
                    time.sleep(1)

                # Clean up chmod pod
                cls._run_kubectl_class(
                    ["delete", "pod", chmod_pod_name],
                    namespace=namespace,
                    context=context,
                    kubeconfig=kubeconfig,
                    check=False,
                )
            except Exception as chmod_error:
                logger.warning(
                    f"Failed to set permissions on PVC {data_pvc_id}: {chmod_error}"
                )

            # 4. Set permissions to 777 on tools PVC to ensure all bash tools are
            # executable/writeable across sandboxes.
            chmod_tools_pod_name = cls._sanitize_name(f"chmod-{tools_pvc_name}")
            chmod_tools_pod_spec = {
                "apiVersion": "v1",
                "kind": "Pod",
                "metadata": {
                    "name": chmod_tools_pod_name,
                    "namespace": namespace,
                },
                "spec": {
                    "restartPolicy": "Never",
                    "containers": [
                        {
                            "name": "chmod-container",
                            "image": "alpine:latest",
                            "command": [
                                "sh",
                                "-c",
                                "chmod -R 777 /target && echo 'Permissions set successfully'",
                            ],
                            "volumeMounts": [
                                {
                                    "name": "target-volume",
                                    "mountPath": "/target",
                                }
                            ],
                        }
                    ],
                    "volumes": [
                        {
                            "name": "target-volume",
                            "persistentVolumeClaim": {
                                "claimName": tools_pvc_id,
                            },
                        }
                    ],
                },
            }
            if tolerations:
                chmod_tools_pod_spec["spec"]["tolerations"] = tolerations

            try:
                cls._run_kubectl_class(
                    ["apply", "-f", "-"],
                    input_data=json.dumps(chmod_tools_pod_spec),
                    namespace=namespace,
                    context=context,
                    kubeconfig=kubeconfig,
                )
                logger.info(f"Created chmod pod: {chmod_tools_pod_name}")

                for _ in range(30):
                    result = cls._run_kubectl_class(
                        [
                            "get",
                            "pod",
                            chmod_tools_pod_name,
                            "-o",
                            "jsonpath={.status.phase}",
                        ],
                        namespace=namespace,
                        context=context,
                        kubeconfig=kubeconfig,
                        check=False,
                    )
                    if result.stdout.strip() == "Succeeded":
                        logger.info(
                            f"Set permissions 777 on bash tools PVC: {tools_pvc_id}"
                        )
                        break
                    elif result.stdout.strip() == "Failed":
                        logger.warning(
                            f"Failed to set permissions on PVC {tools_pvc_id}"
                        )
                        break
                    time.sleep(1)

                cls._run_kubectl_class(
                    ["delete", "pod", chmod_tools_pod_name],
                    namespace=namespace,
                    context=context,
                    kubeconfig=kubeconfig,
                    check=False,
                )
            except Exception as chmod_error:
                logger.warning(
                    f"Failed to set permissions on PVC {tools_pvc_id}: {chmod_error}"
                )

            return (scripts_pvc_id, data_pvc_id, tools_pvc_id)

        except Exception as e:
            logger.error(f"Failed to create shared PVCs: {e}")
            # Clean up any created PVCs on failure
            try:
                cls._run_kubectl_class(
                    ["delete", "pvc", scripts_pvc_name],
                    namespace=namespace,
                    context=context,
                    kubeconfig=kubeconfig,
                    check=False,
                )
                cls._run_kubectl_class(
                    ["delete", "pvc", data_pvc_name],
                    namespace=namespace,
                    context=context,
                    kubeconfig=kubeconfig,
                    check=False,
                )
            except Exception:
                pass
            raise

    @classmethod
    def delete_shared_volumes(
        cls,
        scripts_volume_id: str = None,
        data_volume_id: str = None,
        tools_volume_id: str = None,
    ) -> None:
        """Delete shared PVCs.

        Args:
            scripts_volume_id (str): ID of the scripts PVC to delete
            data_volume_id (str): ID of the data PVC to delete
            tools_volume_id (str): ID of the tools PVC to delete"""
        namespace = cls._resolve_namespace_from_env()
        context = cls._resolve_context_from_env()
        kubeconfig = cls._resolve_kubeconfig_from_env()

        for volume_id in [scripts_volume_id, data_volume_id, tools_volume_id]:
            if volume_id:
                try:
                    result = cls._run_kubectl_class(
                        ["delete", "pvc", volume_id],
                        namespace=namespace,
                        context=context,
                        kubeconfig=kubeconfig,
                        check=False,
                    )
                    if result.returncode == 0:
                        logger.info(f"Deleted PVC: {volume_id}")
                    else:
                        logger.warning(
                            f"Failed to delete PVC {volume_id}: {result.stderr}"
                        )
                except Exception as e:
                    logger.warning(f"Error deleting PVC {volume_id}: {e}")

    # ------------------------------------------------------------------
    # Pod launch helpers
    # ------------------------------------------------------------------
    @classmethod
    def _resolve_common_setting(
        cls,
        sandbox_configs: dict[str, ContainerConfig],
        *,
        extra_keys: tuple[str, ...],
        env_fallback: Optional[str],
        default: Optional[str] = None,
    ) -> Optional[str]:
        values = set()
        for config in sandbox_configs.values():
            extra = config.extra or {}
            for key in extra_keys:
                value = extra.get(key)
                if value:
                    values.add(value)
        if len(values) > 1:
            raise ValueError(
                f"Multiple values provided for {extra_keys}: {values}. Please use a single namespace/context across sandboxes"
            )
        if values:
            return values.pop()
        if env_fallback:
            env_value = os.getenv(env_fallback)
            if env_value:
                return env_value
        return default

    @classmethod
    def _create_container_spec(
        cls,
        sandbox_type: str,
        container_config: ContainerConfig,
        volume_lookup: Dict[str, dict],
    ) -> dict:
        container_name = cls._sanitize_name(f"{sandbox_type}-container")
        command = None
        args = None
        if container_config.command is None:
            command = ["/bin/sh", "-c", "while true; do sleep 1000; done"]
        elif container_config.command == "":
            command = None
        elif isinstance(container_config.command, str):
            command = ["/bin/sh", "-c", container_config.command]
        elif isinstance(container_config.command, (list, tuple)):
            command = list(container_config.command)
        else:
            raise TypeError(
                f"Unsupported command type for container {sandbox_type}: {type(container_config.command)}"
            )

        env_vars = [
            {"name": name, "value": str(value)}
            for name, value in (container_config.environment or {}).items()
        ]

        container_spec: dict[str, Any] = {
            "name": container_name,
            "image": container_config.image,
            "imagePullPolicy": "IfNotPresent",
        }
        if command:
            container_spec["command"] = command
        if args:
            container_spec["args"] = args
        if env_vars:
            container_spec["env"] = env_vars
        if container_config.working_dir:
            container_spec["workingDir"] = container_config.working_dir

        ports = []
        for port_name, host_binding in (container_config.ports or {}).items():
            port_number = int(port_name.split("/")[0])
            ports.append({"containerPort": port_number})
        if ports:
            container_spec["ports"] = ports

        volume_mounts = []
        for spec in container_config.volumes or []:
            parts = spec.split(":")
            source = parts[0]
            mount_path = parts[1] if len(parts) > 1 else "/"
            mode = parts[2] if len(parts) > 2 else "rw"
            if source not in volume_lookup:
                volume_lookup[source] = {
                    "name": cls._sanitize_name(f"vol-{source}"),
                    "source": source,
                }
            volume_mounts.append(
                {
                    "name": volume_lookup[source]["name"],
                    "mountPath": mount_path,
                    "readOnly": mode == "ro",
                }
            )
        if volume_mounts:
            container_spec["volumeMounts"] = volume_mounts

        resources = {}
        if container_config.mem_limit or container_config.cpus:
            resources["limits"] = {}
            if container_config.mem_limit:
                resources["limits"]["memory"] = container_config.mem_limit
            if container_config.cpus:
                resources["limits"]["cpu"] = str(container_config.cpus)
        if resources:
            container_spec["resources"] = resources

        return container_spec

    @classmethod
    def _materialize_volume_defs(cls, volume_lookup: Dict[str, dict]) -> list[dict]:
        volume_defs = []
        for source, info in volume_lookup.items():
            name = info["name"]
            if source.startswith("/"):
                volume_defs.append(
                    {
                        "name": name,
                        "hostPath": {"path": source, "type": "Directory"},
                    }
                )
            elif source:
                volume_defs.append(
                    {
                        "name": name,
                        "persistentVolumeClaim": {"claimName": source},
                    }
                )
            else:
                volume_defs.append({"name": name, "emptyDir": {}})
        return volume_defs

    @classmethod
    async def create_single_sandbox(
        cls, session_id: str, sandbox_type: str, container_config
    ) -> Exception:
        raise NotImplementedError(
            "create_single_sandbox_async is not implemented for K8sSandbox"
        )

    @classmethod
    async def launch_all_sandboxes(
        cls,
        session_id: str,
        sandbox_configs: dict,
        shared_volume_id: str = None,
        scripts_volume_id: str = None,
        tools_volume_id: str = None,
    ) -> dict:
        namespace = cls._resolve_common_setting(
            sandbox_configs,
            extra_keys=("namespace", "k8s_namespace"),
            env_fallback=cls.DEFAULT_NAMESPACE_ENV,
            default="default",
        )
        context = cls._resolve_common_setting(
            sandbox_configs,
            extra_keys=("context", "k8s_context"),
            env_fallback=cls.DEFAULT_CONTEXT_ENV,
        )
        kubeconfig = cls._resolve_common_setting(
            sandbox_configs,
            extra_keys=("kubeconfig",),
            env_fallback=cls.DEFAULT_KUBECONFIG_ENV,
            default=os.getenv("KUBECONFIG"),
        )

        pod_name = cls._sanitize_name(f"session-{session_id}")
        volume_lookup: Dict[str, dict] = {}

        containers = []
        for sandbox_type, config in sandbox_configs.items():
            # Ensure image present
            if not config.image:
                raise ValueError(
                    f"Container image not specified for sandbox {sandbox_type}"
                )

            if shared_volume_id:
                existing_volumes = config.volumes or []
                shared_present = any(
                    entry.split(":")[0] == shared_volume_id
                    for entry in existing_volumes
                )
                if not shared_present:
                    existing_volumes.append(f"{shared_volume_id}:/shared:rw")
                config.volumes = existing_volumes

            container_spec = cls._create_container_spec(
                sandbox_type, config, volume_lookup
            )
            containers.append(container_spec)

        volume_defs = cls._materialize_volume_defs(volume_lookup)

        pod_manifest = {
            "apiVersion": "v1",
            "kind": "Pod",
            "metadata": {
                "name": pod_name,
                "namespace": namespace,
                "labels": {
                    "app": "opensage-sandbox",
                    "opensage-session": cls._sanitize_name(session_id),
                },
            },
            "spec": {
                "restartPolicy": "Always",
                "containers": containers,
            },
        }
        # Resolve tolerations from provided configs. Use only global sandbox-level.
        try:
            tol: Optional[list[dict]] = None
            try:
                from opensage.session.opensage_session import (
                    get_opensage_session,  # lazy import
                )

                sess = get_opensage_session(session_id)
                cfg = getattr(sess, "config", None)
                sbx_cfg = getattr(cfg, "sandbox", None)
                global_tol = getattr(sbx_cfg, "tolerations", None)
                if isinstance(global_tol, list) and global_tol:
                    tol = global_tol
            except Exception:
                pass
            if tol:
                pod_manifest["spec"]["tolerations"] = tol
        except Exception:
            pass
        if volume_defs:
            pod_manifest["spec"]["volumes"] = volume_defs

        with tempfile.NamedTemporaryFile(
            "w", suffix=".yaml", delete=False
        ) as manifest_file:
            yaml.safe_dump(pod_manifest, manifest_file)
            manifest_path = manifest_file.name

        try:
            cls._run_kubectl_class(
                ["apply", "-f", manifest_path],
                namespace=namespace,
                context=context,
                kubeconfig=kubeconfig,
                text=True,
            )
        finally:
            os.unlink(manifest_path)

        cls._run_kubectl_class(
            [
                "wait",
                f"pod/{pod_name}",
                "--for=condition=Ready",
                "--timeout=180s",
            ],
            namespace=namespace,
            context=context,
            kubeconfig=kubeconfig,
            text=True,
        )

        sandbox_instances: Dict[str, BaseSandbox] = {}
        from opensage.sandbox.factory import create_sandbox_class, get_initializer_class

        for sandbox_type, config in sandbox_configs.items():
            initializer_class = get_initializer_class(sandbox_type)
            sandbox_class = create_sandbox_class(cls, initializer_class)
            config.pod_name = pod_name
            config.container_name = cls._sanitize_name(f"{sandbox_type}-container")
            config.extra = config.extra or {}
            config.extra.setdefault("namespace", namespace)
            if context:
                config.extra.setdefault("context", context)
            if kubeconfig:
                config.extra.setdefault("kubeconfig", kubeconfig)
            sandbox_instance = sandbox_class(
                config,
                session_id=session_id,
                backend_type=cls.backend_type,
                sandbox_type=sandbox_type,
            )

            # Store using_cached flag for later initialization
            sandbox_instance._using_cached = config.using_cached

            # Apply cached rootfs if needed
            restore_tar = (
                (config.extra or {}).get("cached_rootfs_tar") if config.extra else None
            )
            if restore_tar:
                sandbox_instance._apply_cached_rootfs(restore_tar)

            sandbox_instances[sandbox_type] = sandbox_instance

        logger.info(
            f"Successfully created {len(sandbox_instances)} sandbox instances (not yet initialized)"
        )
        return sandbox_instances

    @staticmethod
    async def _run_initializer_with_tracking(
        sandbox_type: str,
        sandbox_instance: "K8sSandbox",
        init_coro: Awaitable[None],
    ) -> None:
        """Await initialization, update state, and emit logs.

        Raises:
          Exception: Raised when this operation fails."""
        final_state: Optional[SandboxState] = None
        sandboxes = None
        opensage_session_id = getattr(sandbox_instance, "opensage_session_id", None)
        if opensage_session_id:
            try:
                from opensage.session.opensage_session import get_opensage_session

                sandboxes = get_opensage_session(opensage_session_id).sandboxes
            except Exception as exc:  # pylint: disable=broad-except
                logger.warning(
                    "Failed to retrieve sandbox manager for session %s: %s",
                    opensage_session_id,
                    exc,
                )
        try:
            await init_coro
        except Exception as exc:  # pylint: disable=broad-except
            final_state = SandboxState.ERROR
            setattr(sandbox_instance, "state", final_state)
            if sandboxes:
                try:
                    sandboxes.get_sandbox(sandbox_type).state = final_state
                except Exception as state_exc:  # pylint: disable=broad-except
                    logger.warning(
                        "Failed to set sandbox '%s' state to %s: %s",
                        sandbox_type,
                        final_state.value,
                        state_exc,
                    )
            logger.error(
                "sandbox '%s' (session %s) state=%s - Initialization failed: %s",
                sandbox_type,
                opensage_session_id,
                final_state.value,
                exc,
                exc_info=exc,
            )
            raise
        else:
            final_state = SandboxState.READY
            setattr(sandbox_instance, "state", final_state)
            if sandboxes:
                try:
                    sandboxes.get_sandbox(sandbox_type).state = final_state
                except Exception as state_exc:  # pylint: disable=broad-except
                    logger.warning(
                        "Failed to set sandbox '%s' state to %s: %s",
                        sandbox_type,
                        final_state.value,
                        state_exc,
                    )
        finally:
            state_value = final_state.value if final_state else "unknown"
            logger.info(
                "sandbox '%s' (session %s) state=%s - Initialization finished",
                sandbox_type,
                opensage_session_id,
                state_value,
            )

    @classmethod
    async def initialize_all_sandboxes(
        cls, sandbox_instances: dict, *, continue_on_error: bool = False
    ) -> dict:
        """Initialize all sandbox instances concurrently.

        This should be called after launch_all_sandboxes() and after
        registering any hooks.

        Args:
            sandbox_instances (dict): Dict of sandbox_type -> K8sSandbox instance
            continue_on_error (bool): If True, continue on failures and return a map
                of sandbox_type -> Exception | None. If False, propagate errors."""
        if not sandbox_instances:
            logger.warning("No sandbox instances to initialize")
            return {}

        init_entries = []
        for sandbox_type, sandbox_instance in sandbox_instances.items():
            logger.info(f"Initializing {sandbox_type} sandbox...")

            async def _init_one(instance: "K8sSandbox") -> None:
                if instance._using_cached:
                    await instance.ensure_ready()
                else:
                    await instance.async_initialize(sandbox_instances)

            init_entries.append(
                (
                    sandbox_type,
                    cls._run_initializer_with_tracking(
                        sandbox_type, sandbox_instance, _init_one(sandbox_instance)
                    ),
                )
            )

        # Initialize all sandboxes concurrently
        tasks = [entry[1] for entry in init_entries]
        if tasks:
            if continue_on_error:
                results = await asyncio.gather(*tasks, return_exceptions=True)
                result_map = {}
                for (sandbox_type, _), res in zip(init_entries, results):
                    if isinstance(res, Exception):
                        logger.error(
                            f"Initialization failed for sandbox '{sandbox_type}': {res}"
                        )
                        result_map[sandbox_type] = res
                    else:
                        result_map[sandbox_type] = None
                return result_map
            else:
                await asyncio.gather(*tasks)
        return {sandbox_type: None for sandbox_type, _ in init_entries}

    # ------------------------------------------------------------------
    # Cache support placeholder (kept for parity with BaseSandbox contract)
    # ------------------------------------------------------------------
    @classmethod
    def cache_sandboxes(
        cls,
        sandbox_instances: dict,
        shared_volume_id: str,
        cache_dir: str,
        task_name: str,
    ) -> dict:
        def normalize_image_name(name: str) -> str:
            normalized = name.lower()
            normalized = re.sub(r"[^a-z0-9._-]", "_", normalized)
            normalized = normalized.strip(".-")
            if normalized.startswith("_"):
                normalized = "img" + normalized
            if len(normalized) > 200:
                normalized = normalized[:200].rstrip("_-.")
            return normalized

        cache_results = {
            "session_id": None,
            "task_name": task_name,
            "cache_dir": cache_dir,
            "shared_volume_backup": None,
            "cached_images": {},
            "errors": [],
            "backend": "k8s",
        }

        try:
            cache_dir_path = Path(cache_dir)
            cache_dir_path.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            cache_results["errors"].append(
                f"Failed to create cache directory {cache_dir}: {exc}"
            )
            return cache_results

        namespace = cls._resolve_namespace_from_env()
        context = cls._resolve_context_from_env()
        kubeconfig = cls._resolve_kubeconfig_from_env()

        # 1. Backup shared volume (PVC)
        if shared_volume_id:
            try:
                backup_path = cls._backup_pvc(
                    pvc_name=shared_volume_id,
                    cache_dir=cache_dir_path,
                    task_name=task_name,
                    namespace=namespace,
                    context=context,
                    kubeconfig=kubeconfig,
                )
                cache_results["shared_volume_backup"] = backup_path
                logger.info(
                    f"Shared volume {shared_volume_id} backed up to {backup_path}"
                )
            except Exception as exc:
                logger.error(
                    f"Failed to backup shared volume {shared_volume_id}: {exc}"
                )
                cache_results["errors"].append(str(exc))

        nerdctl_path = cls._resolve_nerdctl_path()
        containerd_socket = cls._resolve_containerd_socket()
        containerd_namespace = os.getenv("OPENSAGE_K8S_CONTAINERD_NAMESPACE", "k8s.io")

        normalized_task_name = normalize_image_name(task_name)
        k8s_metadata: dict[str, dict] = {}

        for sandbox_type, sandbox_instance in sandbox_instances.items():
            try:
                pod_name = getattr(sandbox_instance, "pod_name", None)
                container_name = getattr(sandbox_instance, "container_name", None)
                inst_namespace = getattr(sandbox_instance, "namespace", namespace)
                inst_context = getattr(sandbox_instance, "context", context)
                inst_kubeconfig = getattr(sandbox_instance, "kubeconfig", kubeconfig)
                container_config = getattr(
                    sandbox_instance, "container_config_obj", None
                )
                base_image = None
                if container_config and getattr(container_config, "image", None):
                    base_image = container_config.image

                if not pod_name or not container_name:
                    raise RuntimeError(
                        f"Sandbox {sandbox_type} missing pod/container association"
                    )

                normalized_sandbox = normalize_image_name(sandbox_type)
                repository_name = f"{normalized_task_name}_sandbox_{normalized_sandbox}"
                cached_image_name = f"{repository_name}:cached"

                commit_succeeded = False
                containerd_id = None
                if nerdctl_path:
                    try:
                        containerd_id = cls._get_containerd_container_id(
                            pod_name=pod_name,
                            container_name=container_name,
                            namespace=inst_namespace,
                            context=inst_context,
                            kubeconfig=inst_kubeconfig,
                        )
                        commit_cmd = [
                            "sudo",
                            nerdctl_path,
                            "--address",
                            containerd_socket,
                            "--namespace",
                            containerd_namespace,
                            "commit",
                            containerd_id,
                            cached_image_name,
                        ]
                        logger.info(
                            f"Committing pod {pod_name}/{container_name} to {cached_image_name}"
                        )
                        subprocess.run(commit_cmd, check=True, capture_output=True)
                        commit_succeeded = True
                    except Exception as exc:
                        logger.warning(
                            f"nerdctl commit failed for {sandbox_type}: {exc}. Falling back to file-based cache"
                        )

                if commit_succeeded:
                    tar_path = (
                        cache_dir_path / f"{repository_name.replace(':', '_')}.tar"
                    )
                    save_cmd = [
                        "sudo",
                        nerdctl_path,
                        "--address",
                        containerd_socket,
                        "--namespace",
                        containerd_namespace,
                        "save",
                        cached_image_name,
                        "-o",
                        str(tar_path),
                    ]
                    try:
                        subprocess.run(save_cmd, check=True, capture_output=True)
                        cache_results["cached_images"][sandbox_type] = {
                            "image_name": cached_image_name,
                            "image_tar": str(tar_path),
                            "containerd_id": containerd_id,
                            "commit_succeeded": True,
                        }
                        try:
                            subprocess.run(
                                ["docker", "load", "-i", str(tar_path)],
                                capture_output=True,
                                text=True,
                                check=True,
                            )
                        except subprocess.CalledProcessError as docker_error:
                            logger.debug(
                                f"docker load failed for {cached_image_name}: {docker_error.stderr}"
                            )
                        except FileNotFoundError:
                            logger.debug(
                                "Docker CLI not available; skipping docker load"
                            )
                        except Exception as docker_exc:
                            logger.debug(
                                f"Unexpected docker load error for {cached_image_name}: {docker_exc}"
                            )
                    except Exception as exc:
                        logger.warning(f"nerdctl save failed for {sandbox_type}: {exc}")
                else:
                    cache_results["cached_images"][sandbox_type] = {
                        "image_name": cached_image_name,
                        "commit_succeeded": False,
                    }

                # Ensure Docker can see the cached tag (required for downstream checks)
                rootfs_tar_path = cache_dir_path / (
                    f"{repository_name.replace(':', '_')}_rootfs.tar.gz"
                )
                try:
                    cls._create_container_rootfs_tar(
                        pod_name=pod_name,
                        container_name=container_name,
                        namespace=inst_namespace,
                        context=inst_context,
                        kubeconfig=inst_kubeconfig,
                        output_path=rootfs_tar_path,
                    )
                except Exception as exc:
                    logger.warning(
                        f"Failed to snapshot filesystem for {sandbox_type}: {exc}"
                    )
                    rootfs_tar_path = None

                k8s_metadata[sandbox_type] = {
                    "image_name": cached_image_name,
                    "rootfs_tar": str(rootfs_tar_path) if rootfs_tar_path else None,
                    "commit_succeeded": commit_succeeded,
                }
                if base_image:
                    k8s_metadata[sandbox_type]["base_image"] = base_image

                cache_entry = cache_results["cached_images"].setdefault(
                    sandbox_type, {"image_name": cached_image_name}
                )
                if rootfs_tar_path:
                    cache_entry["rootfs_tar"] = str(rootfs_tar_path)

                if not commit_succeeded and rootfs_tar_path:
                    try:
                        subprocess.run(
                            [
                                "docker",
                                "import",
                                str(rootfs_tar_path),
                                cached_image_name,
                            ],
                            capture_output=True,
                            text=True,
                            check=True,
                        )
                    except subprocess.CalledProcessError as docker_error:
                        logger.debug(
                            f"docker import failed for {cached_image_name}: {docker_error.stderr}"
                        )
                    except FileNotFoundError:
                        logger.debug("Docker CLI not available; skipping docker import")
                    except Exception as docker_exc:
                        logger.debug(
                            f"Unexpected docker import error for {cached_image_name}: {docker_exc}"
                        )

            except Exception as exc:
                logger.error(f"Failed to cache sandbox {sandbox_type}: {exc}")
                cache_results["errors"].append(f"{sandbox_type}: {exc}")

        if k8s_metadata:
            manifest_data = {
                "task_name": task_name,
                "cache_dir": str(cache_dir_path),
                "sandboxes": k8s_metadata,
            }
            manifest_path = cache_dir_path / "k8s_cache_manifest.json"
            try:
                with manifest_path.open("w", encoding="utf-8") as manifest_file:
                    json.dump(manifest_data, manifest_file, indent=2)
                cache_results["metadata_path"] = str(manifest_path)
                os.environ["OPENSAGE_K8S_CACHE_DIR"] = str(cache_dir_path)

                global_manifest_dir = Path.home() / ".cache" / "opensage" / "k8s_cache"
                global_manifest_dir.mkdir(parents=True, exist_ok=True)
                global_manifest = (
                    global_manifest_dir / f"{normalize_image_name(task_name)}.json"
                )
                with global_manifest.open("w", encoding="utf-8") as global_file:
                    json.dump(manifest_data, global_file, indent=2)
            except Exception as exc:
                logger.debug(f"Failed to write k8s cache manifest: {exc}")

        return cache_results

    @classmethod
    def _resolve_nerdctl_path(cls) -> Optional[str]:
        env_path = os.getenv("OPENSAGE_K8S_NERDCTL")
        if env_path and Path(env_path).exists():
            return env_path
        candidates = [
            "nerdctl",
            "/usr/local/bin/nerdctl",
            "/usr/bin/nerdctl",
            "/home/linuxbrew/.linuxbrew/bin/nerdctl",
        ]
        for candidate in candidates:
            found = (
                shutil.which(candidate)
                if not Path(candidate).is_absolute()
                else (candidate if Path(candidate).exists() else None)
            )
            if found:
                return found
        return None

    @classmethod
    def _resolve_containerd_socket(cls) -> str:
        socket_env = os.getenv("OPENSAGE_K8S_CONTAINERD_SOCKET")
        if socket_env:
            return socket_env
        candidates = [
            "/run/k3s/containerd/containerd.sock",
            "/run/containerd/containerd.sock",
        ]
        for candidate in candidates:
            if Path(candidate).exists():
                return candidate
        # Fallback to default path; errors will surface at runtime if invalid
        return candidates[0]

    @classmethod
    def _get_containerd_container_id(
        cls,
        *,
        pod_name: str,
        container_name: str,
        namespace: str,
        context: Optional[str],
        kubeconfig: Optional[str],
    ) -> str:
        result = cls._run_kubectl_class(
            ["get", "pod", pod_name, "-o", "json"],
            namespace=namespace,
            context=context,
            kubeconfig=kubeconfig,
            include_namespace=True,
        )
        data = json.loads(result.stdout)
        statuses = data.get("status", {}).get("containerStatuses", [])
        for status in statuses:
            if status.get("name") != container_name:
                continue
            container_id = status.get("containerID")
            if not container_id:
                break
            if container_id.startswith("containerd://"):
                return container_id.split("containerd://", 1)[1]
            return container_id
        raise RuntimeError(
            f"Unable to determine container ID for {pod_name}/{container_name}"
        )

    @classmethod
    def _backup_pvc(
        cls,
        *,
        pvc_name: str,
        cache_dir: Path,
        task_name: str,
        namespace: str,
        context: Optional[str],
        kubeconfig: Optional[str],
    ) -> str:
        backup_pod_name = cls._sanitize_name(f"backup-{pvc_name}-{int(time.time())}")
        backup_manifest = {
            "apiVersion": "v1",
            "kind": "Pod",
            "metadata": {
                "name": backup_pod_name,
                "namespace": namespace,
                "labels": {"app": "opensage-sandbox-backup"},
            },
            "spec": {
                "restartPolicy": "Never",
                "containers": [
                    {
                        "name": "backup",
                        "image": os.getenv(
                            cls.INIT_CONTAINER_IMAGE_ENV, cls.DEFAULT_INIT_IMAGE
                        ),
                        "command": [
                            "/bin/sh",
                            "-c",
                            "while true; do sleep 3600; done",
                        ],
                        "volumeMounts": [{"name": "shared", "mountPath": "/target"}],
                    }
                ],
                "volumes": [
                    {
                        "name": "shared",
                        "persistentVolumeClaim": {"claimName": pvc_name},
                    }
                ],
            },
        }

        with tempfile.NamedTemporaryFile(
            "w", suffix=".yaml", delete=False
        ) as manifest_file:
            yaml.safe_dump(backup_manifest, manifest_file)
            manifest_path = manifest_file.name

        try:
            cls._run_kubectl_class(
                ["apply", "-f", manifest_path],
                namespace=namespace,
                context=context,
                kubeconfig=kubeconfig,
                text=True,
            )
            cls._run_kubectl_class(
                [
                    "wait",
                    f"pod/{backup_pod_name}",
                    "--for=condition=Ready",
                    "--timeout=180s",
                ],
                namespace=namespace,
                context=context,
                kubeconfig=kubeconfig,
                text=True,
            )

            backup_tar_path = cache_dir / f"{task_name}_shared_volume.tar.gz"
            exec_cmd = cls._build_kubectl_command(
                [
                    "exec",
                    backup_pod_name,
                    "-c",
                    "backup",
                    "--",
                    "tar",
                    "-C",
                    "/target",
                    "-czf",
                    "-",
                    ".",
                ],
                namespace=namespace,
                context=context,
                kubeconfig=kubeconfig,
            )

            with open(backup_tar_path, "wb") as out_file:
                proc = subprocess.run(
                    exec_cmd,
                    stdout=out_file,
                    stderr=subprocess.PIPE,
                    check=True,
                )
                if proc.returncode != 0:
                    raise RuntimeError(proc.stderr.decode("utf-8", errors="ignore"))

            return str(backup_tar_path)

        finally:
            os.unlink(manifest_path)
            cls._run_kubectl_class(
                ["delete", "pod", backup_pod_name, "--ignore-not-found=true"],
                namespace=namespace,
                context=context,
                kubeconfig=kubeconfig,
                text=True,
                check=False,
            )

    @classmethod
    def _create_container_rootfs_tar(
        cls,
        *,
        pod_name: str,
        container_name: str,
        namespace: str,
        context: Optional[str],
        kubeconfig: Optional[str],
        output_path: Path,
    ) -> None:
        tar_cmd = [
            "exec",
            pod_name,
            "-c",
            container_name,
            "--",
            "tar",
            "-C",
            "/",
            "-czf",
            "-",
            "tmp",
        ]
        cmd = cls._build_kubectl_command(
            tar_cmd,
            namespace=namespace,
            context=context,
            kubeconfig=kubeconfig,
        )
        with output_path.open("wb") as out_file:
            subprocess.run(
                cmd,
                stdout=out_file,
                stderr=subprocess.PIPE,
                check=True,
            )

    def _apply_cached_rootfs(self, tar_path: str) -> None:
        if not tar_path or not Path(tar_path).exists():
            logger.debug(f"Cached rootfs tar not found: {tar_path}")
            return
        remote_tar = f"/tmp/opensage_cached_rootfs_{int(time.time())}.tar.gz"
        try:
            self.copy_file_to_container(tar_path, remote_tar)
            extract_cmd = f"cd / && tar -xzf {shlex.quote(remote_tar)} && rm -f {shlex.quote(remote_tar)}"
            output, exit_code = self.run_command_in_container(extract_cmd)
            if exit_code != 0:
                logger.warning(
                    f"Failed to apply cached rootfs from {tar_path}: {output}"
                )
        except Exception as exc:
            logger.warning(
                f"Error restoring cached filesystem {tar_path} into container {self.container_name}: {exc}"
            )

    @classmethod
    def checkpoint(cls) -> str:
        """Checkpoint the sandbox."""
        raise NotImplementedError("Checkpoint is not implemented for K8s sandbox")

    @classmethod
    def restore(cls) -> str:
        """Restore the sandbox."""
        raise NotImplementedError("Restore is not implemented for K8s sandbox")
