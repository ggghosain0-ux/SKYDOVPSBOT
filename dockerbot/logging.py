import logging
import logging.handlers
import os
from pathlib import Path

try:
    from pythonjsonlogger import jsonlogger
except Exception:
    jsonlogger = None


DEFAULT_MAX_BYTES = 5 * 1024 * 1024
DEFAULT_BACKUP_COUNT = 3


def _make_formatter(json: bool = False) -> logging.Formatter:
    if json and jsonlogger is not None:
        return jsonlogger.JsonFormatter('%(asctime)s %(levelname)s %(name)s %(message)s')
    return logging.Formatter('%(asctime)s %(levelname)s %(name)s: %(message)s')


def setup_logging(logs_dir: Path, *, name: str = 'vpsbot', level: str = None, json: bool = False) -> logging.Logger:
    """Configure and return a logger.

    - `logs_dir`: directory where log file will be stored.
    - `name`: logger name.
    - `level`: optional log level (DEBUG/INFO/WARNING/ERROR).
    - `json`: whether to emit JSON structured logs when possible.
    """
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / f"{name}.log"

    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    env_level = os.environ.get('LOG_LEVEL')
    lvl = (level or env_level or 'INFO').upper()
    logger.setLevel(getattr(logging, lvl, logging.INFO))

    formatter = _make_formatter(json)

    fh = logging.handlers.RotatingFileHandler(log_path, maxBytes=DEFAULT_MAX_BYTES, backupCount=DEFAULT_BACKUP_COUNT)
    fh.setFormatter(formatter)
    ch = logging.StreamHandler()
    ch.setFormatter(formatter)

    logger.addHandler(fh)
    logger.addHandler(ch)

    logger.propagate = False
    return logger


def get_logger(name: str = 'vpsbot') -> logging.Logger:
    """Convenience accessor for the default logger.

    Honors environment variables:
    - `LOG_LEVEL` — sets the log level
    - `LOG_JSON` — if set (`1`/`true`) and `python-json-logger` is available, emits JSON logs
    """
    json = os.environ.get('LOG_JSON', '').lower() in ('1', 'true', 'yes')
    logs_dir = Path(os.environ.get('LOGS_DIR', './logs'))
    return setup_logging(logs_dir, name=name, json=json)
