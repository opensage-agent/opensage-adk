import re
import subprocess


def copy_from_container(container_id: str, src: str, dst: str):
    subprocess.run(["docker", "cp", f"{container_id}:{src}", dst], check=True)


def copy_to_container(container_id: str, src: str, dst: str):
    subprocess.run(["docker", "cp", src, f"{container_id}:{dst}"], check=True)


def extract_infos_from_arvo_script(arvo_script: str) -> dict[str, str]:
    infos = {}
    # find 'export XXX=YYYY' in arvo_script
    env_names = ["SANITIZER", "FUZZING_LANGUAGE", "ARCHITECTURE"]
    for line in arvo_script.splitlines():
        for env_name in env_names:
            if line.startswith(f"export {env_name}="):
                infos[env_name] = line.split("=", 1)[1].strip().strip('"')

    # find first appearance of "   /out/{fuzz_target} /tmp/poc"
    for line in arvo_script.splitlines():
        m = re.match(r"^\s+/out/(\S+)\s+/tmp/poc", line)
        if m:
            infos["FUZZ_TARGET"] = m.group(1)
            break
    return infos
