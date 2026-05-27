import discord
from discord import app_commands
from discord.ext import commands

import json
import typing
from datetime import datetime, timedelta

from dockerbot.config import get_admin_ids, get_db_path, get_guild_object, get_logs_dir, load_config
from dockerbot.db import Database
from dockerbot.docker_ops import delete_container, deploy_container, restart_container
from dockerbot.logging import setup_logging

cfg = load_config()
logs_dir = get_logs_dir(cfg)
json_logs = cfg.getboolean("app", "log_json", fallback=False)
log_level = cfg.get("app", "log_level", fallback=None)
logger = setup_logging(logs_dir, name="vpsbot", level=log_level, json=json_logs)

db_path = get_db_path(cfg)
DB = Database(db_path, logger)
ADMIN_IDS = get_admin_ids(cfg)
ADMIN_ROLE_ID = None
raw_role = cfg.get("admin", "admin_role_id", fallback="").strip()
if raw_role:
    try:
        ADMIN_ROLE_ID = int(raw_role)
    except Exception:
        logger.warning("Invalid admin_role_id in config.cfg: %s", raw_role)
GUILD_ID = get_guild_object(cfg)
GUILD = discord.Object(id=GUILD_ID) if GUILD_ID else None

EXPIRY_CLEANUP_INTERVAL = cfg.getint("expiry", "cleanup_interval_seconds", fallback=600)
EXPIRY_DEFAULT_DAYS = cfg.getint("expiry", "default_days", fallback=7)
EXPIRY_DEFAULT_HOURS = cfg.getint("expiry", "default_hours", fallback=0)

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="/", intents=intents)

# Optional Sentry integration
SENTRY_DSN = cfg.get("app", "sentry_dsn", fallback="").strip()
if SENTRY_DSN:
    try:
        import sentry_sdk

        sentry_sdk.init(SENTRY_DSN)
        logger.info("Sentry initialized")
    except Exception:
        logger.exception("Failed to initialize Sentry SDK")


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


async def is_admin_interaction(interaction: discord.Interaction) -> bool:
    """Async predicate for app_commands.check to restrict commands to configured admin IDs or role."""
    # Direct ID check
    if is_admin(interaction.user.id):
        return True

    # Role check (requires guild context)
    if ADMIN_ROLE_ID and interaction.guild is not None:
        try:
            member = None
            if isinstance(interaction.user, discord.Member):
                member = interaction.user
            else:
                member = interaction.guild.get_member(interaction.user.id)
                if member is None:
                    member = await interaction.guild.fetch_member(interaction.user.id)
            if member:
                for r in getattr(member, "roles", []):
                    if getattr(r, "id", None) == ADMIN_ROLE_ID:
                        return True
        except Exception:
            logger.exception("Failed to verify member roles for admin check")

    return False


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: Exception) -> None:
    # Handle permission / check failures centrally
    if isinstance(error, app_commands.CheckFailure):
        try:
            await interaction.response.send_message("You are not authorized to run this command.", ephemeral=True)
        except Exception:
            try:
                await interaction.followup.send("You are not authorized to run this command.", ephemeral=True)
            except Exception:
                pass
        return

    # Fallback: log and notify
    logger.exception("Unhandled command error", exc_info=error)
    try:
        await interaction.response.send_message("An internal error occurred while processing the command.", ephemeral=True)
    except Exception:
        pass


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
    if EXPIRY_CLEANUP_INTERVAL > 0:
        bot.loop.create_task(expiry_cleanup_worker())


async def cleanup_expired_vps() -> None:
    def _query(conn):
        cur = conn.cursor()
        cur.execute(
            "SELECT name FROM vps WHERE expires_at IS NOT NULL AND expires_at <= ?",
            (datetime.utcnow().isoformat(),),
        )
        return [row[0] for row in cur.fetchall()]

    expired_names = await DB.run(_query)
    for name in expired_names:
        try:
            await delete_container(
                name=name,
                requester_id=0,
                db_path=DB.path,
                logger=logger,
                force=True,
                remove_volumes=False,
            )
            logger.info("Expired VPS deleted: %s", name)
        except Exception:
            logger.exception("Failed to delete expired VPS %s", name)


async def expiry_cleanup_worker() -> None:
    logger.info("Starting expiry cleanup task every %s seconds", EXPIRY_CLEANUP_INTERVAL)
    while not bot.is_closed():
        try:
            await cleanup_expired_vps()
        except Exception:
            logger.exception("Expiry cleanup failed")
        await asyncio.sleep(EXPIRY_CLEANUP_INTERVAL)


@bot.tree.command(name="deploy", description="Deploy a Docker container as a VPS")
@app_commands.check(is_admin_interaction)
@app_commands.describe(
    image="Docker image to deploy",
    name="A name for the VPS",
    cpu="CPU cores (e.g. 0.5)",
    mem_mb="RAM in MB",
    ports_json="Optional JSON mapping of container_port->host_port, e.g. {\"80/tcp\": 8080}",
    env_json="Optional JSON mapping of env vars, e.g. {\"KEY\": \"value\"}",
    volumes_json="Optional JSON mapping of host_path->{bind,mode} e.g. {\"/host\": {\"bind\": \"/c\", \"mode\": \"rw\"}}",
    expiry_days="Optional expiry in days for the VPS",
    expiry_hours="Optional expiry in hours for the VPS",
)
async def deploy(
    interaction: discord.Interaction,
    image: str,
    name: str,
    cpu: float = None,
    mem_mb: int = None,
    ports_json: str = None,
    env_json: str = None,
    volumes_json: str = None,
    expiry_days: int = None,
    expiry_hours: int = None,
):
    await interaction.response.defer(thinking=True)
    cpu = cpu or cfg.getfloat("limits", "default_cpu", fallback=0.5)
    mem_mb = mem_mb or cfg.getint("limits", "default_mem_mb", fallback=512)
    ports = None
    env = None
    volumes = None
    expires_at = None
    try:
        if ports_json:
            ports = json.loads(ports_json)
            if not isinstance(ports, dict):
                raise ValueError("ports_json must be a JSON object")
        if env_json:
            env = json.loads(env_json)
            if not isinstance(env, dict):
                raise ValueError("env_json must be a JSON object")
        if volumes_json:
            volumes = json.loads(volumes_json)
            if not isinstance(volumes, dict):
                raise ValueError("volumes_json must be a JSON object")
    except Exception as e:
        await interaction.followup.send(f"Invalid JSON for ports/env/volumes: {e}")
        return

    if expiry_days is None and expiry_hours is None:
        expiry_days = EXPIRY_DEFAULT_DAYS
        expiry_hours = EXPIRY_DEFAULT_HOURS

    if expiry_days is None:
        expiry_days = 0
    if expiry_hours is None:
        expiry_hours = 0
    if expiry_days < 0 or expiry_hours < 0:
        await interaction.followup.send("Expiry values must be non-negative")
        return

    if expiry_days or expiry_hours:
        expires_at = (datetime.utcnow() + timedelta(days=expiry_days, hours=expiry_hours)).isoformat()

    try:
        result = await deploy_container(
            image=image,
            name=name,
            cpu=cpu,
            mem_mb=mem_mb,
            owner_id=interaction.user.id,
            db_path=DB.path,
            logger=logger,
            ports=ports,
            env=env,
            volumes=volumes,
            expires_at=expires_at,
        )
        await interaction.followup.send(result)
    except Exception as e:
        logger.exception("deploy failed")
        await interaction.followup.send(f"Deploy failed: {e}")


@bot.tree.command(name="extend", description="Extend the expiry of an existing VPS")
@app_commands.check(is_admin_interaction)
@app_commands.describe(name="Name of the VPS", extra_days="Days to extend expiry", extra_hours="Hours to extend expiry")
async def extend(interaction: discord.Interaction, name: str, extra_days: int = None, extra_hours: int = None):
    await interaction.response.defer(thinking=True)
    if extra_days is None and extra_hours is None:
        await interaction.followup.send("Specify extra_days or extra_hours to extend expiry.")
        return
    if extra_days is not None and extra_days < 0:
        await interaction.followup.send("extra_days must be non-negative.")
        return
    if extra_hours is not None and extra_hours < 0:
        await interaction.followup.send("extra_hours must be non-negative.")
        return

    def _extend(conn):
        cur = conn.cursor()
        cur.execute("SELECT expires_at FROM vps WHERE name = ?", (name,))
        row = cur.fetchone()
        if not row:
            raise RuntimeError("No such VPS recorded")
        current = row[0]
        baseline = datetime.utcnow()
        if current:
            try:
                existing = datetime.fromisoformat(current)
                if existing > baseline:
                    baseline = existing
            except Exception:
                pass
        extra_days_val = extra_days or 0
        extra_hours_val = extra_hours or 0
        new_expires = (baseline + timedelta(days=extra_days_val, hours=extra_hours_val)).isoformat()
        cur.execute("UPDATE vps SET expires_at = ? WHERE name = ?", (new_expires, name))
        return new_expires

    try:
        new_expires = await DB.run(_extend)
        await interaction.followup.send(f"Expiry for {name} extended to {new_expires} UTC")
    except Exception as e:
        logger.exception("extend failed")
        await interaction.followup.send(f"Extend failed: {e}")


@bot.tree.command(name="delete", description="Delete a VPS and remove its container")
@app_commands.check(is_admin_interaction)
@app_commands.describe(name="Name of the VPS to delete", force="Force removal if stop fails", remove_volumes="Remove anonymous volumes")
async def delete(interaction: discord.Interaction, name: str, force: bool = False, remove_volumes: bool = False):
    await interaction.response.defer(thinking=True)
    try:
        result = await delete_container(name=name, requester_id=interaction.user.id, db_path=DB.path, logger=logger, force=force, remove_volumes=remove_volumes)
        await interaction.followup.send(result)
    except Exception as e:
        logger.exception("delete failed")
        await interaction.followup.send(f"Delete failed: {e}")


@bot.tree.command(name="restart", description="Restart a VPS container")
@app_commands.check(is_admin_interaction)
@app_commands.describe(name="Name of the VPS to restart", wait_for_healthy="Wait for health status after restart", healthy_timeout="Timeout seconds to wait for healthy state")
async def restart(interaction: discord.Interaction, name: str, wait_for_healthy: bool = False, healthy_timeout: int = 60):
    await interaction.response.defer(thinking=True)
    try:
        result = await restart_container(name=name, requester_id=interaction.user.id, db_path=DB.path, logger=logger, wait_for_healthy=wait_for_healthy, healthy_timeout=healthy_timeout)
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
            cur.execute(
                "SELECT name,container_id,image,status,cpu,mem_mb,owner_id,created_at,expires_at FROM vps WHERE name = ?",
                (name,),
            )
        else:
            cur.execute(
                "SELECT name,container_id,image,status,cpu,mem_mb,owner_id,created_at,expires_at FROM vps"
            )
        return cur.fetchall()

    try:
        rows = await DB.run(_query, name)
        if not rows:
            await interaction.followup.send("No VPS found." if name else "No VPSes recorded.")
            return
        lines = []
        for r in rows:
            expiry = r[8]
            expiry_str = f" expires={expiry}" if expiry else ""
            lines.append(
                f"**{r[0]}** — image={r[2]} status={r[3]} cpu={r[4]} mem={r[5]}MB owner={r[6]} created={r[7]}{expiry_str}"
            )
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


def run_bot() -> None:
    token = cfg.get("discord", "token", fallback=None)
    if not token or token.startswith("YOUR_"):
        logger.error("Bot token not configured in config.cfg")
        print("Please update dockerbot/config.cfg with your bot token.")
        return
    bot.run(token)
