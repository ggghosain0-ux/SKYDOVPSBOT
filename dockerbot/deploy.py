import asyncio
import re
import sqlite3
import time
from datetime import datetime
from typing import Dict, Mapping, Optional

import docker


NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$")


async def deploy_container(
    image: str,
    name: str,
    cpu: float,
    mem_mb: int,
    owner_id: int,
    db_path: str,
    logger,
    ports: Optional[Mapping[str, int]] = None,
    env: Optional[Mapping[str, str]] = None,
    volumes: Optional[Mapping[str, Dict[str, str]]] = None,
    pull_timeout: int = 300,
    restart_policy: str = "unless-stopped",
) -> str:
    """Deploy a Docker container with resource limits and record it in SQLite.

    - `ports` expected as a mapping of container_port -> host_port, e.g. {"80/tcp": 8080}
    - `volumes` expected in docker SDK format, e.g. {"/host/path": {"bind": "/container/path", "mode": "rw"}}
    """

    def _deploy_sync():
        client = docker.from_env()

        # Validate name
        if not NAME_RE.match(name):
            raise ValueError("Invalid container name; allowed: alnum, _ . - and 1-128 chars")

        # Ensure no existing DB entry or container with same name
        existing = client.containers.list(all=True, filters={"name": name})
        if existing:
            raise RuntimeError(f"Container with name '{name}' already exists on host")

        # Pull image (may take time)
        logger.info("Pulling image %s", image)
        try:
            client.images.pull(image)
        except Exception as e:
            logger.exception("Image pull failed for %s", image)
            raise RuntimeError(f"Failed to pull image {image}: {e}")

        nano_cpus = int(cpu * 1_000_000_000)
        mem_limit = f"{int(mem_mb)}m"

        host_config = client.api.create_host_config(nano_cpus=nano_cpus, mem_limit=mem_limit, restart_policy={"Name": restart_policy})

        # Create container
        container = client.api.create_container(
            image=image,
            name=name,
            host_config=host_config,
            environment=env or {},
            ports=list(ports.keys()) if ports else None,
            volumes=list(volumes.keys()) if volumes else None,
        )

        cid = container.get("Id")
        if not cid:
            raise RuntimeError("Failed to create container")

        client.api.start(cid, port_bindings={k: v for k, v in (ports or {}).items()})

        # record in DB
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO vps (name,container_id,image,owner_id,status,cpu,mem_mb,created_at) VALUES (?,?,?,?,?,?,?,?)",
            (name, cid, image, owner_id, "running", cpu, mem_mb, datetime.utcnow().isoformat()),
        )
        conn.commit()
        conn.close()
        return cid

    # Run deploy in thread with timeout for pull/start operations
    try:
        cid = await asyncio.wait_for(asyncio.to_thread(_deploy_sync), timeout=pull_timeout + 60)
    except asyncio.TimeoutError:
        logger.exception("Deploy timed out for image %s", image)
        raise RuntimeError("Deploy timed out while pulling or starting the container")

    logger.info("Deployed %s -> %s", name, cid)
    return f"Deployed {name} (container {cid[:12]})"

