import asyncio
import configparser
import logging
import logging.handlers
import os
import sqlite3
from datetime import datetime
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

from deploy import deploy_container
from delete import delete_container
from restart import restart_container

BASE_DIR = Path(__file__).resolve().parent
CFG_PATH = BASE_DIR / "config.cfg"


def load_config(path: Path) -> configparser.ConfigParser:
    cfg = configparser.ConfigParser()
    cfg.read(path)
    return cfg


def setup_logging(logs_dir: Path) -> logging.Logger:
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / "vpsbot.log"
    logger = logging.getLogger("vpsbot")
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    fh = logging.handlers.RotatingFileHandler(log_path, maxBytes=5 * 1024 * 1024, backupCount=3)
    fh.setFormatter(fmt)
    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    if not logger.handlers:
        logger.addHandler(fh)
        logger.addHandler(ch)
    return logger


class Database:
    def __init__(self, path: Path, logger: logging.Logger):
        self.path = str(path)
        self.logger = logger
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _init(self):
        conn = sqlite3.connect(self.path)
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS vps (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE,
                container_id TEXT,
                image TEXT,
                owner_id INTEGER,
                status TEXT,
                cpu REAL,
                mem_mb INTEGER,
                created_at TEXT
            )
            """
        )
        conn.commit()
        conn.close()

    async def run(self, fn, *args, **kwargs):
        return await asyncio.to_thread(self._run_sync, fn, *args, **kwargs)

    def _run_sync(self, fn, *args, **kwargs):
        conn = sqlite3.connect(self.path)
        try:
            res = fn(conn, *args, **kwargs)
            conn.commit()
            return res
        except Exception:
            self.logger.exception("DB operation failed")
            raise
        finally:
            conn.close()


cfg = load_config(CFG_PATH)
logs_dir = Path(cfg.get("app", "logs_dir", fallback=str(BASE_DIR.parent / "logs"))).resolve()
logger = setup_logging(logs_dir)

db_path = Path(cfg.get("app", "db_path", fallback=str(BASE_DIR / "data.db"))).resolve()
DB = Database(db_path, logger)

ADMIN_IDS = [int(x.strip()) for x in cfg.get("admin", "admins", fallback="").split(",") if x.strip()]

GUILD_ID = cfg.get("discord", "guild_id", fallback="").strip() or None
GUILD = discord.Object(id=int(GUILD_ID)) if GUILD_ID else None

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="/", intents=intents)


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def admin_check(interaction: discord.Interaction) -> bool:
    return is_admin(interaction.user.id)


@bot.event
async def on_ready():
    try:
        if GUILD:
            await bot.tree.sync(guild=GUILD)
            logger.info("Commands synced to guild %s", GUILD.id)
        else:
            await bot.tree.sync()
            logger.info("Global commands synced")
    except Exception:
        logger.exception("Failed to sync commands")
    logger.info(f"Logged in as {bot.user} ({bot.user.id})")


@bot.tree.command(name="deploy", description="Deploy a Docker container as a VPS")
@app_commands.describe(image="Docker image to deploy", name="A name for the VPS", cpu="CPU cores (e.g. 0.5)", mem_mb="RAM in MB")
async def deploy(interaction: discord.Interaction, image: str, name: str, cpu: float = None, mem_mb: int = None):
    await interaction.response.defer(thinking=True)
    if not admin_check(interaction):
        await interaction.followup.send("You are not authorized to run this command.")
        return
    cpu = cpu or cfg.getfloat("limits", "default_cpu", fallback=0.5)
    mem_mb = mem_mb or cfg.getint("limits", "default_mem_mb", fallback=512)
    try:
        result = await deploy_container(image=image, name=name, cpu=cpu, mem_mb=mem_mb, owner_id=interaction.user.id, db_path=DB.path, logger=logger)
        await interaction.followup.send(result)
    except Exception as e:
        logger.exception("deploy failed")
        await interaction.followup.send(f"Deploy failed: {e}")


@bot.tree.command(name="delete", description="Delete a VPS and remove its container")
@app_commands.describe(name="Name of the VPS to delete")
async def delete(interaction: discord.Interaction, name: str):
    await interaction.response.defer(thinking=True)
    if not admin_check(interaction):
        await interaction.followup.send("You are not authorized to run this command.")
        return
    try:
        result = await delete_container(name=name, requester_id=interaction.user.id, db_path=DB.path, logger=logger)
        await interaction.followup.send(result)
    except Exception as e:
        logger.exception("delete failed")
        await interaction.followup.send(f"Delete failed: {e}")


@bot.tree.command(name="restart", description="Restart a VPS container")
@app_commands.describe(name="Name of the VPS to restart")
async def restart(interaction: discord.Interaction, name: str):
    await interaction.response.defer(thinking=True)
    if not admin_check(interaction):
        await interaction.followup.send("You are not authorized to run this command.")
        return
    try:
        result = await restart_container(name=name, requester_id=interaction.user.id, db_path=DB.path, logger=logger)
        await interaction.followup.send(result)
    except Exception as e:
        logger.exception("restart failed")
        await interaction.followup.send(f"Restart failed: {e}")


@bot.tree.command(name="status", description="Show status of all VPS or a specific one")
@app_commands.describe(name="Optional VPS name (leave empty to list all)")
async def status(interaction: discord.Interaction, name: str = None):
    await interaction.response.defer(thinking=True)

    def _query(conn, name):
        cur = conn.cursor()
        if name:
            cur.execute("SELECT name,container_id,image,status,cpu,mem_mb,owner_id,created_at FROM vps WHERE name = ?", (name,))
        else:
            cur.execute("SELECT name,container_id,image,status,cpu,mem_mb,owner_id,created_at FROM vps")
        return cur.fetchall()

    try:
        rows = await DB.run(_query, name)
        if not rows:
            await interaction.followup.send("No VPS found." if name else "No VPSes recorded.")
            return
        lines = []
        for r in rows:
            lines.append(f"**{r[0]}** — image={r[2]} status={r[3]} cpu={r[4]} mem={r[5]}MB owner={r[6]} created={r[7]}")
        # Discord has message length limits; chunk if necessary
        chunk = []
        msg = ""
        for line in lines:
            if len(msg) + len(line) + 1 > 1900:
                chunk.append(msg)
                msg = line + "\n"
            else:
                msg += line + "\n"
        if msg:
            chunk.append(msg)
        for part in chunk:
            await interaction.followup.send(part)
    except Exception:
        logger.exception("status failed")
        await interaction.followup.send("Failed to fetch status.")


def main():
    token = cfg.get("discord", "token", fallback=None)
    if not token or token.startswith("YOUR_"):
        logger.error("Bot token not configured in config.cfg")
        print("Please update dockerbot/config.cfg with your bot token.")
        return
    bot.run(token)


if __name__ == "__main__":
    main()

