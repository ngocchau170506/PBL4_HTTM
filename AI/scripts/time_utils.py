"""Shared wall-clock and monotonic time formatting helpers."""

from datetime import datetime


def local_now() -> datetime:
    """Return the computer's local wall-clock time with its UTC offset."""
    return datetime.now().astimezone()


def iso_timestamp(value: datetime | None = None) -> str:
    """Format a local timestamp for logs, metadata, and transport."""
    return (value or local_now()).isoformat(timespec="milliseconds")


def filename_timestamp(value: datetime | None = None) -> str:
    """Format a local timestamp without characters unsafe in file names."""
    return (value or local_now()).strftime("%Y%m%d_%H%M%S_%f")[:-3]


def duration_hms(seconds: float) -> str:
    total_seconds = max(0, int(seconds))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
