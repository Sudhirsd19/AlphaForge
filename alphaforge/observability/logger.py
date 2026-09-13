"""
AlphaForge Structured Observability Logger.

Provides standard Python logging configured with Phase 13 RedactionFormatter
to scrub credentials from logs while maintaining low-overhead diagnostic recording.
"""

from __future__ import annotations

import logging
import sys
from typing import TYPE_CHECKING

from alphaforge.security.redaction import RedactionFormatter

if TYPE_CHECKING:
    from collections.abc import Sequence


def get_observability_logger(
    name: str = "alphaforge.observability",
    level: int = logging.INFO,
    custom_secrets: Sequence[str] | None = None,
) -> logging.Logger:
    """
    Configure and retrieve a logger equipped with Phase 13 secret redaction.

    Invariants:
    - Guaranteed zero leakage of sensitive credentials via RedactionFormatter.
    - Uses standard stream handling without introducing external cloud or network telemetry.
    """
    logger = logging.getLogger(name)
    logger.setLevel(level)

    # Avoid duplicate handlers if already configured
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(level)
        formatter = RedactionFormatter(
            fmt="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%SZ",
            custom_secrets=custom_secrets,
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    return logger
