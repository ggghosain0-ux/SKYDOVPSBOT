import asyncio
import sqlite3
import time
from typing import Optional

import docker


async def restart_container(
    name: str,
    requester_id: int,
    db_path: str,
    logger,
    timeout: int = 30,
    wait_for_healthy: bool = False,
    healthy_timeout: int = 60,
) -> str:
    """Restart a container by VPS name, update DB status, and optionally wait for health.

    Runs blocking docker and sqlite operations in a thread to avoid blocking the event loop.
    """

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

        # Mark status as restarting in DB
        try:
            cur.execute("UPDATE vps SET status = ? WHERE name = ?", ("restarting", name))
            conn.commit()
        except Exception:
            logger.exception("Failed to mark restarting in DB")

        # Restart container
        try:
            cont.restart(timeout=timeout)
        except Exception as e:
            logger.exception("Failed to restart container %s: %s", container_id, e)
            conn.close()
            raise

        # Optionally wait for health status
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
                        # No healthcheck configured
                        break
                except Exception:
                    logger.exception("Error while checking health for %s", container_id)
                time.sleep(1)

        # Mark status as running in DB
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

