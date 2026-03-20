import re
import subprocess

from opensage.session.neo4j_client import AsyncNeo4jClient
from opensage.session.opensage_session import OpenSageSession


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


def fix_neo4j_client(
    opensage_session: OpenSageSession, client_type: str
) -> AsyncNeo4jClient:
    new_client = AsyncNeo4jClient(
        opensage_session.config.neo4j.uri,
        opensage_session.config.neo4j.user,
        opensage_session.config.neo4j.password,
        database=opensage_session.neo4j._get_database_name_for_type(client_type),
    )

    opensage_session.neo4j._clients[client_type] = new_client

    return new_client
