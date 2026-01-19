# src/rn/logging_utils.py
"""
Application-wide logging utilities.

Purpose:
- Provide a single, consistent logging configuration for the entire pipeline.
- Support both console output (developer experience) and file-based logs
  (auditability, debugging, cost tracking).

Design principles:
- Centralized configuration: logging is initialized once in main().
- Opt-in file logging to keep local runs lightweight.
- Structured, readable log format including timestamp, level and logger name.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional


def setup_logging(
    level: str = "INFO",
    log_file: Optional[Path] = None,
) -> None:
    """
    Configure application logging.
    - Logs to stdout (console)
    - Optionally also logs to a file
    """
    log_level = getattr(logging, level.upper(), logging.INFO)

    handlers: list[logging.Handler] = []

    # Console handler
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(log_level)
    handlers.append(console)

    # File handler (optional)
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(log_level)
        handlers.append(file_handler)

    logging.basicConfig(
        level=log_level,
        handlers=handlers,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
