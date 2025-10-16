from __future__ import annotations

"""Qt worker threads for LiteConvert.

Responsible for running conversions off the main thread and reporting
progress back to the UI via Qt signals.
"""

from dataclasses import dataclass
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from PyQt5.QtCore import QObject, QThread, pyqtSignal

from .convert import (
    ConversionResult,
    JobSpec,
    Mode,
    OverwritePolicy,
    convert_images_to_single_pdf,
    convert_job,
)
from .utils import is_image_file


@dataclass
class WorkerSummary:
    total: int
    ok: int
    failed: int
    elapsed_s: float


class ConversionWorker(QThread):
    """Runs a batch of conversion jobs on a background thread."""

    progressItem = pyqtSignal(int, float)  # row index, 0..1
    progressTotal = pyqtSignal(float)  # 0..1
    status = pyqtSignal(str)
    itemDone = pyqtSignal(int, object)  # row index, ConversionResult
    error = pyqtSignal(int, str)
    finished = pyqtSignal(object)  # WorkerSummary

    def __init__(
        self,
        jobs: Sequence[JobSpec],
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._jobs: List[JobSpec] = list(jobs)
        self._cancel_event = threading.Event()

    def cancel(self) -> None:
        self._cancel_event.set()

    def is_cancelled(self) -> bool:
        return self._cancel_event.is_set()

    def run(self) -> None:  # type: ignore[override]
        start = time.perf_counter()
        total = len(self._jobs)
        ok = 0
        failed = 0

        # Handle special merged mode: group all image inputs if selected
        merged_mode = any(j.mode == "JPG/PNG → PDF (single merged)" for j in self._jobs)
        if merged_mode:
            # Collect all image files among jobs in order
            image_jobs = [j for j in self._jobs if is_image_file(j.input_path)]
            if image_jobs:
                first = image_jobs[0]
                # Determine output filename using naming pattern of first job
                filename_no_ext = first.naming_pattern and first.naming_pattern.strip()
                if not filename_no_ext:
                    filename_no_ext = f"{first.input_path.stem}_merged"
                filename_no_ext = (
                    filename_no_ext.replace("{name}", first.input_path.stem)
                    .replace("{ext}", first.input_path.suffix.lstrip("."))
                    .replace("{mode}", first.mode)
                )
                filename_no_ext = filename_no_ext.replace("{index}", "").replace("{page}", "").strip(" _-.")
                from .convert import _resolve_collision  # local import to avoid cycle at top
                output_path = first.output_dir / f"{filename_no_ext}.pdf"
                output_path = _resolve_collision(output_path, first.overwrite_policy)
                if first.overwrite_policy == "Skip" and output_path.exists():
                    res = ConversionResult(True, [output_path], [], 0.0, 0)
                    for idx, j in enumerate(self._jobs):
                        if is_image_file(j.input_path):
                            self.progressItem.emit(idx, 1.0)
                            self.itemDone.emit(idx, res)
                        self.progressTotal.emit(min(1.0, (idx + 1) / max(1, total)))
                    self.finished.emit(WorkerSummary(total=total, ok=len(image_jobs), failed=0, elapsed_s=time.perf_counter() - start))
                    return

                self.status.emit("Merging images into single PDF…")
                res = convert_images_to_single_pdf(
                    [j.input_path for j in image_jobs],
                    output_path,
                    page_size=first.page_size,
                    fit_mode=first.fit_mode,
                    margins_mm=first.margins_mm,
                )
                # Mark all image jobs as done with same result file
                for idx, j in enumerate(self._jobs):
                    if is_image_file(j.input_path):
                        self.progressItem.emit(idx, 1.0)
                        self.itemDone.emit(idx, res)
                        ok += 1 if res.success else 0
                        failed += 0 if res.success else 1
                    self.progressTotal.emit(min(1.0, (idx + 1) / max(1, total)))
                self.finished.emit(WorkerSummary(total=total, ok=ok, failed=failed, elapsed_s=time.perf_counter() - start))
                return

        # Normal per-item processing
        for idx, job in enumerate(self._jobs):
            if self.is_cancelled():
                break
            self.status.emit(f"Processing {job.input_path.name}…")

            def on_progress(p: float) -> None:
                self.progressItem.emit(idx, max(0.0, min(1.0, p)))

            try:
                res = convert_job(job, on_progress=on_progress, is_cancelled=self.is_cancelled)
                self.itemDone.emit(idx, res)
                if res.success:
                    ok += 1
                else:
                    failed += 1
            except Exception as exc:  # pragma: no cover - defensive
                failed += 1
                self.error.emit(idx, str(exc))
            self.progressTotal.emit((idx + 1) / max(1, total))

        self.finished.emit(WorkerSummary(total=total, ok=ok, failed=failed, elapsed_s=time.perf_counter() - start))


