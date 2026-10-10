"""Auto-classify an uploaded file as text, audio, video or image.

Keep in sync with frontend/src/lib/classify.ts.
"""
from __future__ import annotations

import re
from pathlib import PurePath
from typing import Optional

from .models import FileType

_EXTENSIONS: dict[FileType, frozenset[str]] = {
    "text": frozenset({".txt", ".md", ".csv", ".json", ".log"}),
    "audio": frozenset({".aac", ".opus", ".ogg", ".oga", ".wav", ".m4a", ".mp3", ".amr", ".caf", ".flac"}),
    "video": frozenset({".mp4", ".m4v", ".mov", ".h265", ".hevc", ".h264", ".webm", ".3gp", ".mkv"}),
    "image": frozenset({".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".gif", ".bmp"}),
}


def classify(filename: Optional[str], content_type: Optional[str]) -> Optional[FileType]:
    ext = PurePath(filename or "").suffix.lower()
    for file_type, exts in _EXTENSIONS.items():
        if ext in exts:
            return file_type
    mime = (content_type or "").lower()
    for prefix in ("text", "audio", "video", "image"):
        if mime.startswith(prefix + "/"):
            return prefix  # type: ignore[return-value]
    return None


def safe_filename(name: Optional[str]) -> str:
    base = PurePath(name or "").name
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", base).lstrip(".")
    return base[:120] or "upload"
