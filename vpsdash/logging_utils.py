from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path


def log_root() -> Path:
    if getattr(sys, "_MEIPASS", None):
        root = Path(sys.executable).resolve().parent / "logs"
    else:
        root = Path(__file__).resolve().parent.parent / "logs"
    root.mkdir(parents=True, exist_ok=True)
    return root


def log_path(name: str) -> Path:
    safe_name = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in name).strip("-_") or "vpsdash"
    return log_root() / f"{safe_name}.log"


def configure_file_logging(name: str) -> Path:
    path = log_path(name)
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    for handler in root_logger.handlers:
        if getattr(handler, "_vpsdash_log_path", None) == str(path):
            return path

    handler = RotatingFileHandler(path, maxBytes=2_000_000, backupCount=5, encoding="utf-8")
    handler._vpsdash_log_path = str(path)  # type: ignore[attr-defined]
    handler.setLevel(logging.INFO)
    handler.setFormatter(
        logging.Formatter(
            "[%(asctime)s] %(levelname)s %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    root_logger.addHandler(handler)

    logging.captureWarnings(True)
    logging.getLogger(name).info("Logging initialized at %s", path)
    return path
