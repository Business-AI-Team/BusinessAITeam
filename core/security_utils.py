"""
Cryptographic helpers for document integrity (SHA-256) and secure token handling.

Uploaded files are hashed before analysis; binary content is removed after processing
to minimize data retention (see `secure_delete_file`).
"""

from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path
from typing import BinaryIO

logger = logging.getLogger(__name__)

CHUNK_SIZE = 1024 * 1024  # 1 MiB


def sha256_file(path: str | Path) -> str:
    """
    Compute hexadecimal SHA-256 of a file on disk (streaming, memory-safe).
    """
    p = Path(path)
    h = hashlib.sha256()
    with p.open("rb") as f:
        while True:
            chunk = f.read(CHUNK_SIZE)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def sha256_bytes(data: bytes) -> str:
    """SHA-256 hex digest of an in-memory byte string."""
    return hashlib.sha256(data).hexdigest()


def sha256_stream(stream: BinaryIO) -> str:
    """Hash a binary stream without loading entirely into memory."""
    h = hashlib.sha256()
    while True:
        chunk = stream.read(CHUNK_SIZE)
        if not chunk:
            break
        h.update(chunk)
    return h.hexdigest()


def secure_delete_file(path: str | Path | None) -> bool:
    """
    Best-effort deletion of a file from storage. Logs warnings on failure.
    Returns True if the path did not exist or was removed successfully.
    """
    if not path:
        return True
    p = Path(path)
    try:
        if p.is_file():
            p.unlink()
            logger.info("Deleted file after processing: %s", p)
        return True
    except OSError as e:
        logger.warning("Could not delete file %s: %s", p, e)
        return False


def mask_email(email: str) -> str:
    """Return a privacy-preserving display form (e.g. f***@domain.com)."""
    if "@" not in email:
        return "***"
    local, _, domain = email.partition("@")
    if len(local) <= 1:
        return f"*@{domain}"
    return f"{local[0]}***@{domain}"
