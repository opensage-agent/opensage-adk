import docker
import pytest

from opensage.utils.project_info import SRC_PATH

from ....utils import copy_to_container, extract_infos_from_arvo_script

EXAMPLE_IMAGE = "n132/arvo:51603-vul"  # https://github.com/file/file.git
OSSFUZZ_SCRIPTS_DIR = SRC_PATH / "sandbox_scripts/ossfuzz"


@pytest.fixture(scope="module")
def container_and_target():
    client = docker.from_env()

    container = client.containers.run(
        EXAMPLE_IMAGE,
        command=["tail", "-f", "/dev/null"],
        detach=True,
    )

    try:
        copy_to_container(container.id, str(OSSFUZZ_SCRIPTS_DIR), "/scripts")
        res = container.exec_run(cmd=["cat", "/bin/arvo"])
        infos = extract_infos_from_arvo_script(res.output.decode())

        assert infos["SANITIZER"] == "address"
        assert infos["FUZZING_LANGUAGE"] == "c++"
        assert infos["ARCHITECTURE"] == "x86_64"
        assert infos["FUZZ_TARGET"] == "magic_fuzzer_loaddb"

        # compile
        res = container.exec_run(
            cmd=["bash", "/scripts/compile_debug.sh"],
            environment={
                "SANITIZER": infos["SANITIZER"],
                "FUZZING_LANGUAGE": infos["FUZZING_LANGUAGE"],
                "ARCHITECTURE": infos["ARCHITECTURE"],
            },
        )

        assert res.exit_code == 0, f"Compile failed: {res.output.decode()}"

        yield container, infos["FUZZ_TARGET"]

    finally:
        container.remove(force=True)


def test_compile_debug(container_and_target):
    container, fuzz_target = container_and_target

    # TODO: more strict test?
    res = container.exec_run(
        cmd=["bash", "-c", f"readelf -S /out/{fuzz_target} | grep -q debug"],
        workdir="/tmp",
    )

    assert res.exit_code == 0, "Debug test failed"
