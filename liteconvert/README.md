LiteConvert
===========

Cross-platform desktop converter between HEIC, JPG/PNG, and PDF with batch processing and a responsive PyQt5 UI.

Install & Run
-------------

```bash
python -m venv .venv && . .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r liteconvert/requirements.txt
python -m liteconvert.main
```

Features
--------
- Add files/folders or drag-and-drop
- Modes: HEIC→JPG/PNG, JPG/PNG→PDF (merged or separate), PDF→JPG/PNG
- Options: EXIF orientation, JPG quality, DPI, page size, fit, margins
- Naming patterns with tokens: {name}, {ext}, {index}, {page}, {mode}
- Overwrite policy: Skip / Auto-rename / Overwrite
- Per-item and overall progress, cancel, and log area
- Settings stored under user config dir

Notes
-----
- HEIC supported via `pillow-heif` (registers opener on import)
- Images→PDF via `img2pdf` (lossless for JPEG/PNG)
- PDF rasterization via `PyMuPDF` (no external CLI)

Packaging (PyInstaller)
-----------------------

```bash
pyinstaller -n LiteConvert --noconfirm --windowed --add-data "resources/icons:resources/icons" liteconvert/main.py
```

Tests
-----

```bash
pytest -q
```

Add your screenshots to `resources/` as needed.


