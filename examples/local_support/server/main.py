import socket
from pathlib import Path

import docker
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

APP_HOST = "0.0.0.0"
APP_PORT = 18824

PORT_RANGE = [13000, 14000]
HOST_IP = "172.16.0.1"


app = FastAPI()
existing_containers = {}


def _used_ports() -> set[int]:
    return {p for ports in existing_containers.values() for p in ports.values()}


def _find_free_port(reserved: set[int] | None = None) -> int:
    """Find an available port in the allowed range."""
    reserved = reserved or set()
    start, end = PORT_RANGE
    for port in range(start, end):
        if port in reserved or port in _used_ports():
            continue
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if sock.connect_ex(("0.0.0.0", port)) != 0:
                return port
    raise HTTPException(status_code=503, detail="No available ports in range")


@app.post("/create_neo4j")
async def create_neo4j():
    client = docker.from_env()
    try:
        http_port = _find_free_port()
        bolt_port = _find_free_port({http_port})
    except HTTPException:
        raise
    except Exception as exc:  # pragma: no cover - defensive
        raise HTTPException(status_code=500, detail=str(exc))

    try:
        container = client.containers.run(
            "neo4j:5.26.12-enterprise",
            detach=True,
            ports={"7474/tcp": http_port, "7687/tcp": bolt_port},
            environment={
                "NEO4J_dbms_directories_data": "/var/lib/neo4j/data-custom",
                "NEO4J_dbms_directories_logs": "/var/lib/neo4j/logs-custom",
                "NEO4J_ACCEPT_LICENSE_AGREEMENT": "yes",
                "NEO4J_AUTH": "neo4j/callgraphn4j!",
                "NEO4J_PLUGINS": '["graph-data-science", "apoc"]',
                "NEO4J_apoc_import_file_enabled": "true",
                "NEO4J_apoc_export_file_enabled": "true",
            },
        )
    except docker.errors.DockerException as exc:
        raise HTTPException(
            status_code=500, detail=f"Failed to create container: {exc}"
        ) from exc

    existing_containers[container.id] = {"http_port": http_port, "bolt_port": bolt_port}

    # wait for Neo4j to start up, "Started." appears in the logs when ready
    logs = container.logs(stream=True, follow=True)
    for log in logs:
        if b"Started." in log:
            break

    return {
        "container_id": container.id,
        "host": HOST_IP,
        "http_port": http_port,
        "bolt_port": bolt_port,
    }


@app.post("/remove_neo4j/{container_id}")
async def remove_neo4j(container_id: str):
    client = docker.from_env()
    try:
        container = client.containers.get(container_id)
    except docker.errors.NotFound:
        # raise HTTPException(status_code=404, detail="Container not found")
        return {"status": "not_found", "container_id": container_id}
    except docker.errors.DockerException as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    try:
        container.stop(timeout=10)
    except docker.errors.APIError:
        pass

    try:
        container.remove()
    except docker.errors.APIError as exc:
        raise HTTPException(
            status_code=500, detail=f"Failed to remove container: {exc}"
        ) from exc

    existing_containers.pop(container_id, None)
    return {"status": "removed", "container_id": container_id}


@app.get("/agent.tar.gz")
async def get_agent_archive():
    archive_path = Path("agent.tar.gz")
    if not archive_path.exists():
        raise HTTPException(status_code=404, detail="agent.tar.gz not found")

    return FileResponse(
        archive_path,
        media_type="application/gzip",
        filename="agent.tar.gz",
    )


if __name__ == "__main__":
    uvicorn.run(app, host=APP_HOST, port=APP_PORT)
