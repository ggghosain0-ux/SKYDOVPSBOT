import configparser
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
CFG_PATH = BASE_DIR / "config.cfg"


def load_config(path: Path = CFG_PATH) -> configparser.ConfigParser:
    cfg = configparser.ConfigParser()
    cfg.read(path)
    return cfg


def get_admin_ids(cfg: configparser.ConfigParser):
    return [int(x.strip()) for x in cfg.get("admin", "admins", fallback="").split(",") if x.strip()]


def get_logs_dir(cfg: configparser.ConfigParser) -> Path:
    log_path = Path(cfg.get("app", "logs_dir", fallback=str(BASE_DIR.parent / "logs")))
    return (BASE_DIR / log_path).resolve() if not log_path.is_absolute() else log_path.resolve()


def get_db_path(cfg: configparser.ConfigParser) -> Path:
    db_path = Path(cfg.get("app", "db_path", fallback=str(BASE_DIR / "data.db")))
    return (BASE_DIR / db_path).resolve() if not db_path.is_absolute() else db_path.resolve()


def get_guild_object(cfg: configparser.ConfigParser):
    guild_id = cfg.get("discord", "guild_id", fallback="").strip() or None
    return None if guild_id is None else int(guild_id)
