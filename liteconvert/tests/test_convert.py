from __future__ import annotations

import io
import tempfile
from pathlib import Path

import fitz
from PIL import Image

from liteconvert.convert import JobSpec, convert_job, convert_images_to_single_pdf


def _tmp_image(path: Path, color: str = "red", size=(64, 48)) -> None:
    img = Image.new("RGB", size, color=color)
    img.save(path, format="PNG")


def _tmp_pdf(path: Path, pages: int = 2) -> None:
    doc = fitz.open()
    for i in range(pages):
        page = doc.new_page()
        page.insert_text((72, 72), f"Page {i + 1}")
    doc.save(str(path))
    doc.close()


def test_images_to_single_pdf() -> None:
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        img1 = td / "a.png"
        img2 = td / "b.png"
        _tmp_image(img1, "red")
        _tmp_image(img2, "green")
        out = td / "merged.pdf"
        res = convert_images_to_single_pdf([img1, img2], out)
        assert res.success
        assert out.exists()


def test_pdf_to_images() -> None:
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        pdf = td / "sample.pdf"
        _tmp_pdf(pdf, pages=2)
        job = JobSpec(
            input_path=pdf,
            mode="PDF → PNG",
            output_dir=td,
            naming_pattern="{name}_page-{page}",
            overwrite_policy="Overwrite",
            dpi=100,
        )
        res = convert_job(job)
        assert res.success
        assert len(res.outputs) == 2
        for p in res.outputs:
            assert p.exists()


def test_heic_to_jpg_simulated_png() -> None:
    # We simulate HEIC open via PNG since pillow-heif registers opener globally.
    # This ensures code path is exercised.
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        src = td / "img.heic"
        # Save a PNG then rename to .heic so PIL uses registered opener
        tmp_png = td / "tmp.png"
        _tmp_image(tmp_png, "blue")
        tmp_png.replace(src)
        job = JobSpec(
            input_path=src,
            mode="HEIC → JPG",
            output_dir=td,
            naming_pattern="{name}",
            overwrite_policy="Overwrite",
            quality=80,
        )
        res = convert_job(job)
        assert res.success
        assert len(res.outputs) == 1
        assert res.outputs[0].suffix.lower() == ".jpg"
        assert res.outputs[0].exists()


