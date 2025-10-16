from __future__ import annotations

"""Pure conversion functions for LiteConvert.

This module contains no Qt imports and is safe to unit-test.
It implements conversions between HEIC, images (JPG/PNG), and PDF.
"""

from dataclasses import dataclass
import io
import time
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Literal, Optional, Sequence, Tuple

import img2pdf
import fitz  # PyMuPDF
from PIL import Image
from pillow_heif import register_heif_opener

from .utils import (
    NamingContext,
    apply_exif_orientation_if_needed,
    build_output_path,
    ensure_unique_path,
    is_heic_file,
    is_image_file,
    is_pdf_file,
    parse_page_range,
)


# Register HEIF/HEIC opener at import time
register_heif_opener()


Mode = Literal[
    "HEIC → JPG",
    "HEIC → PNG",
    "JPG/PNG → PDF (single merged)",
    "JPG/PNG → PDF (separate files)",
    "PDF → JPG",
    "PDF → PNG",
]

OverwritePolicy = Literal["Skip", "Auto-rename", "Overwrite"]


@dataclass
class JobSpec:
    """A single conversion job specification."""

    input_path: Path
    mode: Mode
    output_dir: Path
    naming_pattern: str
    overwrite_policy: OverwritePolicy
    # Generic options (interpreted per mode)
    preserve_exif_orientation: bool = True
    quality: int = 90  # for JPG
    dpi: int = 200  # for PDF → Image
    page_range: str = ""  # e.g., "1-3,5"
    # PDF layout options for Images → PDF
    page_size: str = "Auto"  # Auto, A4, Letter
    fit_mode: str = "Fit"  # Fit or Fill
    margins_mm: int = 0


ProgressCallback = Callable[[float], None]
CancelledCallback = Callable[[], bool]


@dataclass
class ConversionResult:
    success: bool
    outputs: List[Path]
    errors: List[str]
    elapsed_s: float
    pages: int = 0


def _resolve_collision(path: Path, policy: OverwritePolicy) -> Path:
    if policy == "Overwrite":
        return path
    if policy == "Skip" and path.exists():
        # Signal skip by pointing to a unique non-existing path but we won't write
        return path
    return ensure_unique_path(path)


def _to_jpg_png(
    job: JobSpec,
    target_ext: str,
    on_progress: Optional[ProgressCallback] = None,
    is_cancelled: Optional[CancelledCallback] = None,
) -> ConversionResult:
    start = time.perf_counter()
    errors: List[str] = []
    outputs: List[Path] = []
    pages = 1
    try:
        with Image.open(job.input_path) as im:
            im.load()
            im = apply_exif_orientation_if_needed(im, job.preserve_exif_orientation)
            ctx = NamingContext(job.input_path, job.mode)
            filename_no_ext = job.naming_pattern and job.naming_pattern.strip()
            if filename_no_ext:
                filename_no_ext = filename_no_ext
            else:
                filename_no_ext = f"{job.input_path.stem}_{target_ext.lstrip('.')}"
            filename_no_ext = filename_no_ext.replace("{ext}", job.input_path.suffix.lstrip(".")).replace("{name}", job.input_path.stem).replace("{mode}", job.mode)
            # Remove other tokens not relevant here
            filename_no_ext = filename_no_ext.replace("{index}", "").replace("{page}", "").strip(" _-.")
            out_path = build_output_path(job.output_dir, filename_no_ext, f".{target_ext.lower()}")
            out_path = _resolve_collision(out_path, job.overwrite_policy)

            if job.overwrite_policy == "Skip" and out_path.exists():
                # Skipped
                return ConversionResult(True, [out_path], errors, time.perf_counter() - start, pages)

            save_params: Dict[str, object] = {}
            if target_ext.lower() in ("jpg", "jpeg"):
                save_params["quality"] = int(job.quality)
                save_params["subsampling"] = 1
                save_params["optimize"] = True
                format_name = "JPEG"
            else:
                format_name = "PNG"

            im = im.convert("RGB") if format_name == "JPEG" else im
            im.save(out_path, format=format_name, **save_params)
            outputs.append(out_path)
            if on_progress:
                on_progress(1.0)
    except Exception as exc:  # pragma: no cover - hard to simulate all image errors in tests
        errors.append(str(exc))
        return ConversionResult(False, outputs, errors, time.perf_counter() - start, pages)
    return ConversionResult(True, outputs, errors, time.perf_counter() - start, pages)


def _page_size_to_points(page_size: str) -> Optional[Tuple[float, float]]:
    if not page_size or page_size.lower() == "auto":
        return None
    if page_size.lower() == "a4":
        # 210 × 297 mm in points
        return img2pdf.mm_to_pt(210), img2pdf.mm_to_pt(297)
    if page_size.lower() == "letter":
        # 8.5 × 11 inches in points
        return img2pdf.in_to_pt(8.5), img2pdf.in_to_pt(11)
    return None


def convert_images_to_single_pdf(
    input_images: Sequence[Path],
    output_path: Path,
    page_size: str = "Auto",
    fit_mode: str = "Fit",
    margins_mm: int = 0,
) -> ConversionResult:
    """Convert multiple images to a single merged PDF using img2pdf.

    This function is exposed for tests and for the merged mode.
    """
    start = time.perf_counter()
    errors: List[str] = []
    outputs: List[Path] = []
    try:
        layout = None
        ps = _page_size_to_points(page_size)
        border = img2pdf.mm_to_pt(max(0, int(margins_mm)))
        if ps is not None:
            layout = img2pdf.get_layout_fun(
                pagesize=ps,
                border=(border, border, border, border),
                fit=img2pdf.FitMode.SHRINK if fit_mode.lower() == "fit" else img2pdf.FitMode.FILL,
            )
        elif border > 0:
            layout = img2pdf.get_layout_fun(border=(border, border, border, border))

        with output_path.open("wb") as f:
            f.write(
                img2pdf.convert(
                    [str(p) for p in input_images],
                    layout_fun=layout,
                )
            )
        outputs.append(output_path)
    except Exception as exc:
        errors.append(str(exc))
        return ConversionResult(False, outputs, errors, time.perf_counter() - start, 0)
    return ConversionResult(True, outputs, errors, time.perf_counter() - start, 0)


def _images_to_pdf_separate(
    job: JobSpec,
    on_progress: Optional[ProgressCallback] = None,
) -> ConversionResult:
    start = time.perf_counter()
    errors: List[str] = []
    outputs: List[Path] = []
    try:
        ps = _page_size_to_points(job.page_size)
        border = img2pdf.mm_to_pt(max(0, int(job.margins_mm)))
        layout = None
        if ps is not None:
            layout = img2pdf.get_layout_fun(
                pagesize=ps,
                border=(border, border, border, border),
                fit=img2pdf.FitMode.SHRINK if job.fit_mode.lower() == "fit" else img2pdf.FitMode.FILL,
            )
        elif border > 0:
            layout = img2pdf.get_layout_fun(border=(border, border, border, border))

        filename_no_ext = job.naming_pattern and job.naming_pattern.strip()
        if not filename_no_ext:
            filename_no_ext = f"{job.input_path.stem}_pdf"
        filename_no_ext = (
            filename_no_ext.replace("{name}", job.input_path.stem)
            .replace("{ext}", job.input_path.suffix.lstrip("."))
            .replace("{mode}", job.mode)
        )
        filename_no_ext = filename_no_ext.replace("{index}", "").replace("{page}", "").strip(" _-.")
        out_path = build_output_path(job.output_dir, filename_no_ext, ".pdf")
        out_path = _resolve_collision(out_path, job.overwrite_policy)
        if job.overwrite_policy == "Skip" and out_path.exists():
            return ConversionResult(True, [out_path], errors, time.perf_counter() - start, 1)

        with out_path.open("wb") as f:
            f.write(img2pdf.convert([str(job.input_path)], layout_fun=layout))
        outputs.append(out_path)
        if on_progress:
            on_progress(1.0)
    except Exception as exc:
        errors.append(str(exc))
        return ConversionResult(False, outputs, errors, time.perf_counter() - start, 1)
    return ConversionResult(True, outputs, errors, time.perf_counter() - start, 1)


def _pdf_to_images(
    job: JobSpec,
    fmt: Literal["JPEG", "PNG"],
    on_progress: Optional[ProgressCallback] = None,
    is_cancelled: Optional[CancelledCallback] = None,
) -> ConversionResult:
    start = time.perf_counter()
    errors: List[str] = []
    outputs: List[Path] = []
    try:
        doc = fitz.open(str(job.input_path))
    except Exception as exc:
        errors.append(str(exc))
        return ConversionResult(False, outputs, errors, time.perf_counter() - start, 0)

    try:
        total_pages = doc.page_count
        pages_list = parse_page_range(job.page_range, total_pages)
        if not pages_list:
            pages_list = list(range(1, total_pages + 1))
        zoom = max(1.0, float(job.dpi) / 72.0)
        mat = fitz.Matrix(zoom, zoom)

        for idx, page_no in enumerate(pages_list, start=1):
            if is_cancelled and is_cancelled():
                break
            try:
                page = doc.load_page(page_no - 1)
                pix = page.get_pixmap(matrix=mat, alpha=(fmt == "PNG"))
                filename_no_ext = job.naming_pattern and job.naming_pattern.strip()
                if not filename_no_ext:
                    filename_no_ext = f"{job.input_path.stem}_page-{page_no}"
                filename_no_ext = (
                    filename_no_ext.replace("{name}", job.input_path.stem)
                    .replace("{ext}", job.input_path.suffix.lstrip("."))
                    .replace("{mode}", job.mode)
                    .replace("{page}", str(page_no))
                )
                filename_no_ext = filename_no_ext.replace("{index}", "").strip(" _-.")
                out_path = build_output_path(job.output_dir, filename_no_ext, ".jpg" if fmt == "JPEG" else ".png")
                out_path = _resolve_collision(out_path, job.overwrite_policy)
                if job.overwrite_policy == "Skip" and out_path.exists():
                    outputs.append(out_path)
                else:
                    pix.save(str(out_path))
                    outputs.append(out_path)
            except Exception as exc:  # pragma: no cover - page-level edge cases
                errors.append(f"Page {page_no}: {exc}")
            if on_progress:
                on_progress(idx / max(1, len(pages_list)))
    finally:
        doc.close()
    return ConversionResult(len(errors) == 0, outputs, errors, time.perf_counter() - start, len(outputs))


def convert_job(
    job: JobSpec,
    on_progress: Optional[ProgressCallback] = None,
    is_cancelled: Optional[CancelledCallback] = None,
) -> ConversionResult:
    """Perform a conversion according to the job specification."""
    mode = job.mode
    if is_heic_file(job.input_path) and mode in ("HEIC → JPG", "HEIC → PNG"):
        target = "JPG" if mode.endswith("JPG") else "PNG"
        return _to_jpg_png(job, target, on_progress, is_cancelled)
    if is_image_file(job.input_path) and mode == "JPG/PNG → PDF (separate files)":
        return _images_to_pdf_separate(job, on_progress)
    if is_pdf_file(job.input_path) and mode in ("PDF → JPG", "PDF → PNG"):
        fmt = "JPEG" if mode.endswith("JPG") else "PNG"
        return _pdf_to_images(job, fmt, on_progress, is_cancelled)
    # Unsupported combo
    return ConversionResult(False, [], ["Unsupported input/mode combination"], 0.0, 0)


