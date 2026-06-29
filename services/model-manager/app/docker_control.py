from __future__ import annotations

import docker

from app.config import settings


def restart_vllm_server() -> dict[str, object]:
    client = docker.from_env()
    filters: dict[str, list[str]] = {
        "label": [f"com.docker.compose.service={settings.docker_service_name}"],
    }
    if settings.docker_project_name:
        filters["label"].append(f"com.docker.compose.project={settings.docker_project_name}")

    containers = client.containers.list(all=True, filters=filters)
    if not containers:
        raise RuntimeError(f"service container not found: {settings.docker_service_name}")

    restarted: list[str] = []
    for container in containers:
        container.restart(timeout=settings.restart_timeout_seconds)
        restarted.append(container.name)
    return {"service": settings.docker_service_name, "containers": restarted}
