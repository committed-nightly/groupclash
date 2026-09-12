"""Find the GitHub Actions concurrency groups that cancel the wrong runs."""

from .core import (
    EMPTY_GROUP,
    IGNORES_REF,
    NEVER_CANCELS,
    SHARED_GROUP,
    Finding,
    Report,
    check,
)

__all__ = [
    "EMPTY_GROUP",
    "IGNORES_REF",
    "NEVER_CANCELS",
    "SHARED_GROUP",
    "Finding",
    "Report",
    "check",
]

__version__ = "0.1.0"
