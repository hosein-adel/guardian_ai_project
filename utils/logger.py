import logging
import logging.handlers
import os
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)


def setup_logging():
    """
    Configure layered logging for Guardian AI.
    - Console: INFO+
    - File (guardian.log): INFO+ (rotating daily)
    - File (error.log): ERROR+ (rotating, keeps 7 days)
    - File (debug.log): DEBUG+ (rotating, keeps 3 days)
    """
    root = logging.getLogger("guardian")
    root.setLevel(logging.DEBUG)

    # Avoid duplicate handlers if called multiple times
    if root.handlers:
        return root

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)-25s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    # 1. Console handler (INFO+)
    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(formatter)
    root.addHandler(console)

    # 2. Main rotating file (INFO+)
    main_file = logging.handlers.TimedRotatingFileHandler(
        LOG_DIR / "guardian.log",
        when="midnight",
        interval=1,
        backupCount=7,
        encoding="utf-8"
    )
    main_file.setLevel(logging.INFO)
    main_file.setFormatter(formatter)
    root.addHandler(main_file)

    # 3. Error rotating file (ERROR+)
    error_file = logging.handlers.TimedRotatingFileHandler(
        LOG_DIR / "error.log",
        when="midnight",
        interval=1,
        backupCount=14,
        encoding="utf-8"
    )
    error_file.setLevel(logging.ERROR)
    error_file.setFormatter(formatter)
    root.addHandler(error_file)

    # 4. Debug rotating file (DEBUG+) — short retention
    debug_file = logging.handlers.TimedRotatingFileHandler(
        LOG_DIR / "debug.log",
        when="midnight",
        interval=1,
        backupCount=3,
        encoding="utf-8"
    )
    debug_file.setLevel(logging.DEBUG)
    debug_file.setFormatter(formatter)
    root.addHandler(debug_file)

    # Quiet noisy 3rd-party loggers
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)

    return root


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"guardian.{name}")
