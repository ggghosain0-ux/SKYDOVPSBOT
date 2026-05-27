import asyncio
import sqlite3
from typing import Optional

import docker


async def delete_container(
    name: str,
    requester_id: int,
    db_path: str,
    logger,
    force: bool = False,
    remove_volumes: bool = False,
    stop_timeout: int = 10,
) -> str:
    """Stop and remove a container by recorded VPS name and delete its DB record.

    - If the container is missing on the host, the DB row is still removed.
    - `force` will force removal if the container cannot be stopped cleanly.
    - `remove_volumes` passes `v=True` to removal to remove anonymous volumes.
    """

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

        # Remove DB record
        cur.execute("DELETE FROM vps WHERE name = ?", (name,))
        conn.commit()
        conn.close()
        return container_id

    cid = await asyncio.to_thread(_run_sync)
    logger.info("Deleted VPS %s (container %s) by requester %s", name, cid[:12] if cid else "-", requester_id)
    return f"Deleted VPS {name} (container {cid[:12] if cid else 'unknown'})"

