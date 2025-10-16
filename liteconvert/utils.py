from __future__ import annotations

"""Utility helpers for LiteConvert.

This module is UI-agnostic. It provides helpers for path operations,
EXIF handling, naming and collision policies, and page-range parsing.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

from PIL import Image, ImageOps


SUPPORTED_EXTS: Tuple[str, ...] = (".heic", ".heif", ".jpg", ".jpeg", ".png", ".pdf")
IMAGE_EXTS: Tuple[str, ...] = (".jpg", ".jpeg", ".png")
HEIC_EXTS: Tuple[str, ...] = (".heic", ".heif")


def is_supported_file(path: Path) -> bool:
    """Return True if path has a supported extension."""
    return path.suffix.lower() in SUPPORTED_EXTS


def is_image_file(path: Path) -> bool:
    """Return True if file is a standard image (JPEG/PNG)."""
    return path.suffix.lower() in IMAGE_EXTS


def is_heic_file(path: Path) -> bool:
    """Return True if file is HEIC/HEIF."""
    return path.suffix.lower() in HEIC_EXTS


def is_pdf_file(path: Path) -> bool:
    """Return True if file is a PDF."""
    return path.suffix.lower() == ".pdf"


def find_supported_files_recursive(directory: Path) -> List[Path]:
    """Recursively find all supported files under the directory."""
    results: List[Path] = []
    for path in directory.rglob("*"):
        if path.is_file() and is_supported_file(path):
            results.append(path)
    return results


def dedupe_paths(paths: Iterable[Path]) -> List[Path]:
    """Return a list of paths with duplicates removed, preserving order."""
    seen = set()
    unique: List[Path] = []
    for p in paths:
        rp = p.resolve()
        if rp not in seen:
            seen.add(rp)
            unique.append(p)
    return unique


def ensure_writable_dir(directory: Path) -> None:
    """Create directory if missing and ensure it is writable.

    Raises an exception if not writable.
    """
    directory.mkdir(parents=True, exist_ok=True)
    test_file = directory / ".liteconvert_write_test"
    try:
        with test_file.open("w", encoding="utf-8") as f:
            f.write("ok")
    finally:
        try:
            test_file.unlink(missing_ok=True)
        except Exception:
            pass


def apply_exif_orientation_if_needed(image: Image.Image, preserve: bool) -> Image.Image:
    """Apply EXIF orientation transpose if requested."""
    if not preserve:
        return image
    try:
        return ImageOps.exif_transpose(image)
    except Exception:
        # If EXIF is malformed or missing, return as-is
        return image


def parse_page_range(page_range: str, max_pages: Optional[int] = None) -> List[int]:
    """Parse a page range string like "1-3,5" into 1-based page indices.

    If max_pages is provided, resulting page numbers are clamped to 1..max_pages.
    """
    if not page_range:
        return list(range(1, (max_pages or 0) + 1)) if max_pages else []
    pages: List[int] = []
    for part in page_range.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start_s, end_s = part.split("-", 1)
            try:
                start = int(start_s)
                end = int(end_s)
            except ValueError:
                continue
            if start <= 0:
                start = 1
            if end < start:
                start, end = end, start
            rng = list(range(start, end + 1))
            pages.extend(rng)
        else:
            try:
                n = int(part)
            except ValueError:
                continue
            if n <= 0:
                continue
            pages.append(n)
    # De-duplicate while preserving order
    seen = set()
    ordered: List[int] = []
    for n in pages:
        if n in seen:
            continue
        if max_pages and n > max_pages:
            continue
        ordered.append(n)
        seen.add(n)
    return ordered


def ensure_unique_path(path: Path) -> Path:
    """Return a non-colliding path by appending _1, _2, ... if needed."""
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    parent = path.parent
    idx = 1
    while True:
        candidate = parent / f"{stem}_{idx}{suffix}"
        if not candidate.exists():
            return candidate
        idx += 1


@dataclass
class NamingContext:
    """Context for naming pattern expansion."""

    input_path: Path
    mode: str
    index: Optional[int] = None  # batch index, 1-based
    page: Optional[int] = None  # 1-based page number


def expand_naming_pattern(pattern: str, ctx: NamingContext) -> str:
    """Expand tokens in naming pattern.

    Tokens: {name}, {ext}, {index}, {page}, {mode}
    """
    mapping = {
        "name": ctx.input_path.stem,
        "ext": ctx.input_path.suffix.lstrip(".").lower(),
        "index": str(ctx.index) if ctx.index is not None else "",
        "page": str(ctx.page) if ctx.page is not None else "",
        "mode": ctx.mode,
    }
    out = pattern
    for key, value in mapping.items():
        out = out.replace("{" + key + "}", value)
    # Clean up potential double dashes or trailing underscores from empty tokens
    out = out.replace("__", "_")
    while "--" in out:
        out = out.replace("--", "-")
    return out.strip(" _-.")


def build_output_path(
    directory: Path,
    filename_no_ext: str,
    extension: str,
) -> Path:
    """Build an output path from directory and filename + extension (with dot)."""
    safe_name = filename_no_ext
    # Basic sanitation: remove forbidden characters on Windows
    for ch in '\\/:*?"<>|':
        safe_name = safe_name.replace(ch, "_")
    return directory / f"{safe_name}{extension}"


