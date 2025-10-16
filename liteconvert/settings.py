from __future__ import annotations

"""Application settings persistence for LiteConvert.

Stores and loads JSON settings from a user-scoped configuration directory,
platform-appropriate for Windows, macOS, and Linux.
"""

import json
import os
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, Optional


APP_NAME = "LiteConvert"


def get_config_dir() -> Path:
    """Return the platform-appropriate configuration directory for the app."""
    if os.name == "nt":
        base = os.getenv("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / APP_NAME
    if sys_platform() == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    # Linux and others
    xdg = os.getenv("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else (Path.home() / ".config")
    return base / APP_NAME


def sys_platform() -> str:
    # Isolated for easier testing/mocking
    return os.uname().sysname.lower() if hasattr(os, "uname") else os.name


@dataclass
class AppSettings:
    """Serializable application settings."""

    last_output_dir: Optional[str] = None
    last_mode: str = "HEIC → JPG"
    # Options
    preserve_exif_orientation: bool = True
    quality: int = 90
    dpi: int = 200
    page_range: str = ""
    page_size: str = "Auto"  # Auto, A4, Letter
    fit_mode: str = "Fit"  # Fit or Fill
    margins_mm: int = 0
    overwrite_policy: str = "Auto-rename"  # Skip, Auto-rename, Overwrite
    naming_pattern: str = "{name}_{mode}"
    window_geometry: Optional[bytes] = None  # Saved via Qt, persisted as hex in JSON


class SettingsManager:
    """Manages reading and writing settings.json under the app config dir."""

    def __init__(self, filename: str = "settings.json") -> None:
        self.config_dir = get_config_dir()
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.config_dir / filename
        self._settings = AppSettings()

    @property
    def settings(self) -> AppSettings:
        return self._settings

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            with self.path.open("r", encoding="utf-8") as f:
                data: Dict[str, Any] = json.load(f)
        except Exception:
            return
        # window_geometry comes as hex string; store as bytes for Qt convenience
        geom_hex = data.get("window_geometry")
        if isinstance(geom_hex, str):
            try:
                data["window_geometry"] = bytes.fromhex(geom_hex)
            except ValueError:
                data["window_geometry"] = None
        try:
            self._settings = AppSettings(**data)
        except TypeError:
            # Forward/backward compatibility: ignore unknown fields
            known = {k: v for k, v in data.items() if k in asdict(AppSettings()).keys()}
            try:
                self._settings = AppSettings(**known)
            except Exception:
                self._settings = AppSettings()

    def save(self) -> None:
        data = asdict(self._settings)
        # Persist window_geometry as hex string for JSON
        geom = data.get("window_geometry")
        if isinstance(geom, (bytes, bytearray)):
            data["window_geometry"] = bytes(geom).hex()
        try:
            with self.path.open("w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception:
            # Never crash on save; ignore errors
            pass


