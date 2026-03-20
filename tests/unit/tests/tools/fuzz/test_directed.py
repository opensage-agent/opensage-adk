import logging

import docker
import pytest

from opensage.utils.project_info import PROJECT_PATH

logger = logging.getLogger(__name__)

logging.basicConfig(level=logging.INFO)

EXAMPLE_IMAGE = "n132/arvo:51603-vul"  # https://github.com/file/file.git
OSSFUZZ_SCRIPTS_DIR = PROJECT_PATH / "src/opensage/toolbox/build_utils/ossfuzz/scripts"
TARGET_FUNCTION_LINE = "fun: mkdbname"
SELECTIVE_FUNC_FILE = (
    PROJECT_PATH / "src/opensage/toolbox/build_utils/ossfuzz/targets.txt"
)


@pytest.fixture(scope="module")
def container_and_target():
    client = docker.from_env()

    container = client.containers.run(
        EXAMPLE_IMAGE,
        command=["tail", "-f", "/dev/null"],
        detach=True,
    )

    try:
        logger.info(f"Creating selective function file: {SELECTIVE_FUNC_FILE}")
        if not SELECTIVE_FUNC_FILE.exists():
            SELECTIVE_FUNC_FILE.touch()
        with open(SELECTIVE_FUNC_FILE, "w") as f:
            f.write(TARGET_FUNCTION_LINE)

        copy_to_container(container.id, str(SELECTIVE_FUNC_FILE), "/targets.txt")
        logger.info(
            f"Copied selective function file to container: {SELECTIVE_FUNC_FILE}"
        )

        copy_to_container(container.id, str(OSSFUZZ_SCRIPTS_DIR), "/scripts")
        logger.info(f"Copied scripts to container: {OSSFUZZ_SCRIPTS_DIR}")

        res = container.exec_run(cmd=["cat", "/bin/arvo"])
        infos = extract_infos_from_arvo_script(res.output.decode())

        assert infos["SANITIZER"] == "address"
        assert infos["FUZZING_LANGUAGE"] == "c++"
        assert infos["ARCHITECTURE"] == "x86_64"
        assert infos["FUZZ_TARGET"] == "magic_fuzzer_loaddb"

        # compile
        logger.info(f"Compiling {infos['FUZZ_TARGET']}")
        res = container.exec_run(
            cmd=["bash", "/scripts/compile_aflpp.sh"],
            environment={
                "SANITIZER": infos["SANITIZER"],
                "FUZZING_LANGUAGE": infos["FUZZING_LANGUAGE"],
                "ARCHITECTURE": infos["ARCHITECTURE"],
                "AFL_LLVM_ALLOWLIST": "/targets.txt",
                "FUZZING_ENGINE": "afl",
            },
        )

        assert res.exit_code == 0, f"Compile failed: {res.output.decode()}"

        logger.info(
            f"Copying {infos['FUZZ_TARGET']} to local: {PROJECT_PATH / 'tmp' / infos['FUZZ_TARGET']}"
        )
        copy_from_container(
            container.id,
            "/out/" + infos["FUZZ_TARGET"],
            PROJECT_PATH / "tmp" / infos["FUZZ_TARGET"],
        )

        yield container, infos["FUZZ_TARGET"]

    finally:
        container.remove(force=True)
        SELECTIVE_FUNC_FILE.unlink()


@pytest.mark.skip(reason="Skip for now")
def test_compile_aflpp(container_and_target):
    container, fuzz_target = container_and_target

    res = container.exec_run(
        cmd=["bash", "-c", f"strings /out/{fuzz_target} | grep -q afl"],
    )

    assert res.exit_code == 0, "aflpp not found in binary"


@pytest.mark.skip(reason="Skip for now")
def test_run_aflpp(container_and_target):
    container, fuzz_target = container_and_target

    # test fuzz
    res = container.exec_run(
        cmd=["bash", "/scripts/test_fuzz.sh"],
        environment={"FUZZ_TARGET": fuzz_target},
    )

    assert res.exit_code == 0, f"Fuzz test failed: {res.output.decode()}"


@pytest.mark.skip(reason="Skip for now")
def test_ground_truth_poc(container_and_target):
    container, fuzz_target = container_and_target

    # test crash
    res = container.exec_run(
        cmd=["/out/" + fuzz_target, "/tmp/poc"],
    )

    assert res.exit_code != 0, f"Crash test failed: {res.output.decode()}"
