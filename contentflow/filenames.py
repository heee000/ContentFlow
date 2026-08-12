from __future__ import annotations

from pathlib import Path


_WINDOWS_RESERVED_STEMS = {"CON", "PRN", "AUX", "NUL", "CLOCK$"} | {
    f"{prefix}{index}"
    for prefix in ("COM", "LPT")
    for index in range(1, 10)
}


def safe_filename(filename: str) -> str:
    """Return a portable basename or reject unsafe filesystem/object-key names."""

    if not isinstance(filename, str):
        raise ValueError("文件名无效")
    name = Path(filename).name
    stem = name.split(".", 1)[0].rstrip(" .").upper()
    if (
        not name
        or len(name) > 255
        or name in {".", ".."}
        or name[-1] in {" ", "."}
        or stem in _WINDOWS_RESERVED_STEMS
        or any(ord(char) < 0x20 or ord(char) == 0x7F for char in name)
        or any(char in '<>:"/\\|?*' for char in name)
    ):
        raise ValueError("文件名无效")
    return name