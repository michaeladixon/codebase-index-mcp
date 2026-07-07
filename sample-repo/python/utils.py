"""Shared helpers for the sample app."""
import re
from pathlib import Path

MAX_RETRIES = 3
DEFAULT_TIMEOUT = 30

EMAIL_RE = re.compile(r"^[\w.+-]+@[\w-]+\.[\w.]+$")


def validate_email(email: str) -> bool:
    """Return True if email looks valid."""
    return bool(EMAIL_RE.match(email))


def slugify(text: str) -> str:
    """Lowercase, hyphen-separated slug."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


class Config:
    """App configuration loaded from disk."""

    def __init__(self, path: Path):
        self.path = path
        self.retries = MAX_RETRIES

    def load(self) -> dict:
        """Parse the config file (key=value lines)."""
        out = {}
        for line in self.path.read_text().splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
        return out
