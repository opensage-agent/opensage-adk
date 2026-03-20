from __future__ import annotations

from opensage.config import ContainerConfig
from opensage.sandbox.k8s_sandbox import K8sSandbox


def test_materialize_volume_defs_uses_hostpath_for_absolute_source():
    volume_lookup = {
        "/tmp/host-data": {
            "name": "vol-host",
            "source": "/tmp/host-data",
        }
    }

    volume_defs = K8sSandbox._materialize_volume_defs(volume_lookup)

    assert volume_defs == [
        {
            "name": "vol-host",
            "hostPath": {"path": "/tmp/host-data", "type": "Directory"},
        }
    ]


def test_materialize_volume_defs_uses_pvc_for_non_absolute_source():
    volume_lookup = {
        "session_shared": {
            "name": "vol-shared",
            "source": "session_shared",
        }
    }

    volume_defs = K8sSandbox._materialize_volume_defs(volume_lookup)

    assert volume_defs == [
        {
            "name": "vol-shared",
            "persistentVolumeClaim": {"claimName": "session_shared"},
        }
    ]


def test_create_container_spec_keeps_non_absolute_source_for_pvc_resolution():
    container_config = ContainerConfig(
        image="busybox:latest",
        volumes=["session_shared:/shared:rw"],
    )
    volume_lookup: dict[str, dict] = {}

    container_spec = K8sSandbox._create_container_spec(
        sandbox_type="main",
        container_config=container_config,
        volume_lookup=volume_lookup,
    )

    assert "volumeMounts" in container_spec
    assert "session_shared" in volume_lookup
