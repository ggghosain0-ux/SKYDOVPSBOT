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
    expires_at: str = None,
    pull_timeout: int = 300,
    restart_policy: str = "unless-stopped",
) -> str:
    def _deploy_sync():
        client = docker.from_env()

        if not NAME_RE.match(name):
            raise ValueError("Invalid container name; allowed: alnum, _ . - and 1-128 chars")

        existing = client.containers.list(all=True, filters={"name": name})
        if existing:
            raise RuntimeError(f"Container with name '{name}' already exists on host")

        logger.info("Pulling image %s", image)
        try:
            client.images.pull(image)
        except Exception as e:
            logger.exception("Image pull failed for %s", image)
            raise RuntimeError(f"Failed to pull image {image}: {e}")

        nano_cpus = int(cpu * 1_000_000_000)
        mem_limit = f"{int(mem_mb)}m"

        host_config = client.api.create_host_config(nano_cpus=nano_cpus, mem_limit=mem_limit, restart_policy={"Name": restart_policy})
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

        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO vps (name,container_id,image,owner_id,status,cpu,mem_mb,created_at,expires_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (name, cid, image, owner_id, "running", cpu, mem_mb, datetime.utcnow().isoformat(), expires_at),
        )
        conn.commit()
        conn.close()
        return cid

    try:
        cid = await asyncio.wait_for(asyncio.to_thread(_deploy_sync), timeout=pull_timeout + 60)
    except asyncio.TimeoutError:
        logger.exception("Deploy timed out for image %s", image)
        raise RuntimeError("Deploy timed out while pulling or starting the container")

    logger.info("Deployed %s -> %s", name, cid)
    return f"Deployed {name} (container {cid[:12]})"


async def delete_container(
    name: str,
    requester_id: int,
    db_path: str,
    logger,
    force: bool = False,
    remove_volumes: bool = False,
    stop_timeout: int = 10,
) -> str:
    def _run_sync() -> str:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("SELECT container_id, owner_id FROM vps WHERE name = ?", (name,))
        row = cur.fetchone()
        if not row:
            conn.close()
            raise RuntimeError("No such VPS recorded")

        container_id, owner_id = row
        client = docker.from_env()

        try:
            cont = client.containers.get(container_id)
            try:
                cont.stop(timeout=stop_timeout)
            except Exception as e:
                logger.warning("Stopping container %s failed: %s", container_id, e)
                if force:
                    try:
                        cont.kill()
                    except Exception as e2:
                        logger.warning("Killing container %s also failed: %s", container_id, e2)
            try:
                cont.remove(v=remove_volumes, force=force)
            except Exception as e:
                logger.exception("Failed to remove container %s: %s", container_id, e)
                raise
        except docker.errors.NotFound:
            logger.info("Container %s not found on host; continuing to remove DB entry", container_id)

        cur.execute("DELETE FROM vps WHERE name = ?", (name,))
        conn.commit()
        conn.close()
        return container_id

    cid = await asyncio.to_thread(_run_sync)
    logger.info("Deleted VPS %s (container %s) by requester %s", name, cid[:12] if cid else "-", requester_id)
    return f"Deleted VPS {name} (container {cid[:12] if cid else 'unknown'})"


async def restart_container(
    name: str,
    requester_id: int,
    db_path: str,
    logger,
    timeout: int = 30,
    wait_for_healthy: bool = False,
    healthy_timeout: int = 60,
) -> str:
    def _run_sync() -> str:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("SELECT container_id FROM vps WHERE name = ?", (name,))
        row = cur.fetchone()
        if not row:
            conn.close()
            raise RuntimeError("No such VPS recorded")
        container_id = row[0]

        client = docker.from_env()
        try:
            cont = client.containers.get(container_id)
        except docker.errors.NotFound:
            conn.close()
            raise RuntimeError("Container not found on host")

        try:
            cur.execute("UPDATE vps SET status = ? WHERE name = ?", ("restarting", name))
            conn.commit()
        except Exception:
            logger.exception("Failed to mark restarting in DB")

        try:
            cont.restart(timeout=timeout)
        except Exception as e:
            logger.exception("Failed to restart container %s: %s", container_id, e)
            conn.close()
            raise

        if wait_for_healthy:
            start = time.time()
            while time.time() - start < healthy_timeout:
                try:
                    cont.reload()
                    state = cont.attrs.get("State", {})
                    health = state.get("Health")
                    if health:
                        status = health.get("Status")
                        if status == "healthy":
                            break
                    else:
                        break
                except Exception:
                    logger.exception("Error while checking health for %s", container_id)
                time.sleep(1)

        try:
            cur.execute("UPDATE vps SET status = ? WHERE name = ?", ("running", name))
            conn.commit()
        except Exception:
            logger.exception("Failed to mark running in DB")

        conn.close()
        return container_id

    cid = await asyncio.to_thread(_run_sync)
    logger.info("Restarted VPS %s (container %s) by requester %s", name, cid[:12] if cid else "-", requester_id)
    return f"Restarted {name} (container {cid[:12] if cid else 'unknown'})"
