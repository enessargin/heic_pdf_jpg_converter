from __future__ import annotations

"""PyQt5 UI for LiteConvert.

Implements the main window, file queue, controls, drag-and-drop, and bindings
to the worker thread that performs conversions.
"""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

import fitz  # type: ignore
from PIL import Image
from PyQt5.QtCore import QMimeData, QModelIndex, QPoint, Qt, QTimer, QUrl
from PyQt5.QtGui import QDesktopServices, QDragEnterEvent, QDropEvent, QIcon
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QStyle,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from .convert import JobSpec, Mode
from .settings import AppSettings, SettingsManager
from .utils import (
    IMAGE_EXTS,
    SUPPORTED_EXTS,
    dedupe_paths,
    ensure_writable_dir,
    find_supported_files_recursive,
    is_heic_file,
    is_image_file,
    is_pdf_file,
)
from .workers import ConversionWorker, WorkerSummary


MODES: List[Mode] = [
    "HEIC → JPG",
    "HEIC → PNG",
    "JPG/PNG → PDF (single merged)",
    "JPG/PNG → PDF (separate files)",
    "PDF → JPG",
    "PDF → PNG",
]


COL_FILE = 0
COL_TYPE = 1
COL_PAGES = 2
COL_STATUS = 3
COL_PROGRESS = 4


class LiteConvertWindow(QMainWindow):
    """Main application window."""

    def __init__(self, settings: SettingsManager) -> None:
        super().__init__()
        self.setWindowTitle("LiteConvert")
        self.setMinimumSize(900, 620)
        self._settings_manager = settings
        self._settings = settings.settings
        self._worker: Optional[ConversionWorker] = None

        self._setup_ui()
        self._load_settings_into_ui()

        self.setAcceptDrops(True)

    # ---- UI Construction ----

    def _setup_ui(self) -> None:
        # Toolbar
        toolbar = QToolBar("Main")
        self.addToolBar(toolbar)

        self.btn_add_files = QPushButton(QIcon(self._icon_path("add")), "Add Files…")
        self.btn_add_folder = QPushButton(QIcon(self._icon_path("folder")), "Add Folder…")
        self.btn_clear = QPushButton(QIcon(self._icon_path("clear")), "Clear List")
        self.btn_remove = QPushButton(QIcon(self._icon_path("remove")), "Remove Selected")

        for b in (self.btn_add_files, self.btn_add_folder, self.btn_clear, self.btn_remove):
            toolbar.addWidget(b)

        toolbar.addSeparator()
        toolbar.addWidget(QLabel("Output:"))
        self.edit_output = QLineEdit()
        self.btn_browse_output = QPushButton("Browse…")
        self.btn_open_output = QPushButton(QIcon(self._icon_path("open")), "Open Output")
        toolbar.addWidget(self.edit_output)
        toolbar.addWidget(self.btn_browse_output)
        toolbar.addWidget(self.btn_open_output)

        # Central splitter
        splitter = QSplitter()
        central = QWidget()
        layout = QHBoxLayout(central)
        layout.addWidget(splitter)
        self.setCentralWidget(central)

        # File table
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["File Name", "Type", "Pages/Frames", "Status", "Progress"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        splitter.addWidget(self.table)

        # Right-side options panel
        options_widget = QWidget()
        options_layout = QVBoxLayout(options_widget)

        # Mode
        group_mode = QGroupBox("Mode")
        form_mode = QFormLayout(group_mode)
        self.combo_mode = QComboBox()
        self.combo_mode.addItems(MODES)
        form_mode.addRow("Conversion:", self.combo_mode)
        options_layout.addWidget(group_mode)

        # Options
        group_opts = QGroupBox("Options")
        form_opts = QFormLayout(group_opts)
        # HEIC -> image
        self.chk_preserve_exif = self._add_checkbox("Preserve EXIF orientation", True)
        self.spin_quality = QSpinBox()
        self.spin_quality.setRange(1, 100)
        self.spin_quality.setValue(90)
        form_opts.addRow(self.chk_preserve_exif)
        form_opts.addRow("Quality (JPG):", self.spin_quality)

        # Images -> PDF
        self.chk_merge_single = self._add_checkbox("Merge into single PDF", True)
        self.combo_page_size = QComboBox()
        self.combo_page_size.addItems(["Auto", "A4", "Letter"])
        self.combo_fit_mode = QComboBox()
        self.combo_fit_mode.addItems(["Fit", "Fill"])
        self.spin_margins = QSpinBox()
        self.spin_margins.setRange(0, 100)
        self.spin_margins.setValue(0)
        form_opts.addRow(self.chk_merge_single)
        form_opts.addRow("Page size:", self.combo_page_size)
        form_opts.addRow("Fit mode:", self.combo_fit_mode)
        form_opts.addRow("Margins (mm):", self.spin_margins)

        # PDF -> Images
        self.spin_dpi = QSpinBox()
        self.spin_dpi.setRange(50, 1200)
        self.spin_dpi.setValue(200)
        self.edit_page_range = QLineEdit()
        form_opts.addRow("DPI:", self.spin_dpi)
        form_opts.addRow("Page range:", self.edit_page_range)

        options_layout.addWidget(group_opts)

        # Naming and overwrite
        group_naming = QGroupBox("Naming")
        form_naming = QFormLayout(group_naming)
        self.edit_pattern = QLineEdit("{name}_{mode}")
        self.combo_overwrite = QComboBox()
        self.combo_overwrite.addItems(["Skip", "Auto-rename", "Overwrite"])
        form_naming.addRow("Pattern:", self.edit_pattern)
        form_naming.addRow("On collision:", self.combo_overwrite)
        options_layout.addWidget(group_naming)

        options_layout.addStretch(1)
        splitter.addWidget(options_widget)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)

        # Bottom bar
        bottom = QWidget()
        bottom_layout = QHBoxLayout(bottom)
        self.btn_start = QPushButton(QIcon(self._icon_path("start")), "Start")
        self.btn_cancel = QPushButton(QIcon(self._icon_path("cancel")), "Cancel")
        self.btn_cancel.setEnabled(False)
        self.progress_total = QProgressBar()
        self.progress_total.setRange(0, 100)
        self.lbl_status = QLabel("Idle")
        bottom_layout.addWidget(self.btn_start)
        bottom_layout.addWidget(self.btn_cancel)
        bottom_layout.addWidget(self.progress_total, 1)
        bottom_layout.addWidget(self.lbl_status)
        layout.addWidget(bottom)

        # Log area
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(160)
        layout.addWidget(self.log)

        # Connections
        self.btn_add_files.clicked.connect(self._on_add_files)
        self.btn_add_folder.clicked.connect(self._on_add_folder)
        self.btn_clear.clicked.connect(self._on_clear)
        self.btn_remove.clicked.connect(self._on_remove_selected)
        self.btn_browse_output.clicked.connect(self._on_browse_output)
        self.btn_open_output.clicked.connect(self._on_open_output)
        self.btn_start.clicked.connect(self._on_start)
        self.btn_cancel.clicked.connect(self._on_cancel)
        self.combo_mode.currentIndexChanged.connect(self._update_option_visibility)
        self._update_option_visibility()

    def _icon_path(self, name: str) -> str:
        return str(Path(__file__).parent / "resources" / "icons" / f"{name}.svg")

    def _add_checkbox(self, text: str, checked: bool) -> QWidget:
        from PyQt5.QtWidgets import QCheckBox

        cb = QCheckBox(text)
        cb.setChecked(checked)
        return cb

    # ---- Drag & Drop ----

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        urls = event.mimeData().urls()
        paths = [Path(u.toLocalFile()) for u in urls]
        files: List[Path] = []
        for p in paths:
            if p.is_dir():
                files.extend(find_supported_files_recursive(p))
            elif p.is_file() and p.suffix.lower() in SUPPORTED_EXTS:
                files.append(p)
        self._add_files(files)

    # ---- File Queue Ops ----

    def _on_add_files(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "Select files",
            str(Path.home()),
            "Supported Files (*.heic *.heif *.jpg *.jpeg *.png *.pdf)",
        )
        if not files:
            return
        self._add_files([Path(f) for f in files])

    def _on_add_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Select folder", str(Path.home()))
        if not folder:
            return
        self._add_files(find_supported_files_recursive(Path(folder)))

    def _add_files(self, paths: List[Path]) -> None:
        paths = dedupe_paths(paths)
        for p in paths:
            self._add_file_row(p)

    def _add_file_row(self, path: Path) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setItem(row, COL_FILE, QTableWidgetItem(path.name))
        self.table.setItem(row, COL_TYPE, QTableWidgetItem(path.suffix.lower().lstrip(".")))
        self.table.setItem(row, COL_STATUS, QTableWidgetItem("Queued"))
        self.table.setItem(row, COL_PAGES, QTableWidgetItem(self._probe_pages_text(path)))
        pb = QProgressBar()
        pb.setRange(0, 100)
        pb.setValue(0)
        self.table.setCellWidget(row, COL_PROGRESS, pb)
        self.table.setRowHeight(row, 22)
        self.table.item(row, COL_FILE).setData(Qt.UserRole, str(path))

    def _probe_pages_text(self, path: Path) -> str:
        try:
            if is_pdf_file(path):
                doc = fitz.open(str(path))
                try:
                    return str(doc.page_count)
                finally:
                    doc.close()
            # Try frames via PIL
            with Image.open(path) as im:
                frames = getattr(im, "n_frames", 1)
                return str(frames)
        except Exception:
            return "-"

    def _on_clear(self) -> None:
        self.table.setRowCount(0)

    def _on_remove_selected(self) -> None:
        rows = sorted({r.row() for r in self.table.selectedIndexes()}, reverse=True)
        for r in rows:
            self.table.removeRow(r)

    # ---- Output dir ----

    def _on_browse_output(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "Select output directory", self.edit_output.text() or str(Path.home()))
        if directory:
            self.edit_output.setText(directory)

    def _on_open_output(self) -> None:
        directory = self.edit_output.text().strip()
        if not directory:
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(directory))

    # ---- Start/Cancel ----

    def _on_start(self) -> None:
        if self._worker is not None:
            return
        try:
            output_dir = Path(self.edit_output.text().strip()) if self.edit_output.text().strip() else Path.home() / "LiteConvert"
            ensure_writable_dir(output_dir)
        except Exception as exc:
            QMessageBox.critical(self, "Output Error", f"Cannot use output directory:\n{exc}")
            return

        mode: Mode = self.combo_mode.currentText()  # type: ignore[assignment]
        overwrite: str = self.combo_overwrite.currentText()

        jobs: List[JobSpec] = []
        for row in range(self.table.rowCount()):
            path = Path(self.table.item(row, COL_FILE).data(Qt.UserRole))
            jobs.append(
                JobSpec(
                    input_path=path,
                    mode=mode,
                    output_dir=output_dir,
                    naming_pattern=self.edit_pattern.text().strip(),
                    overwrite_policy=overwrite,  # type: ignore[arg-type]
                    preserve_exif_orientation=self._checkbox_value(self.chk_preserve_exif),
                    quality=self.spin_quality.value(),
                    dpi=self.spin_dpi.value(),
                    page_range=self.edit_page_range.text().strip(),
                    page_size=self.combo_page_size.currentText(),
                    fit_mode=self.combo_fit_mode.currentText(),
                    margins_mm=self.spin_margins.value(),
                )
            )

        if not jobs:
            QMessageBox.information(self, "Nothing to do", "Please add some files first.")
            return

        self._worker = ConversionWorker(jobs)
        self._bind_worker_signals(self._worker)
        self._toggle_running(True)
        self._worker.start()

    def _on_cancel(self) -> None:
        if self._worker is not None:
            self._worker.cancel()

    def _bind_worker_signals(self, w: ConversionWorker) -> None:
        w.progressTotal.connect(self._on_progress_total)
        w.progressItem.connect(self._on_progress_item)
        w.status.connect(self._on_status)
        w.itemDone.connect(self._on_item_done)
        w.error.connect(self._on_error)
        w.finished.connect(self._on_finished)

    def _on_progress_total(self, p: float) -> None:
        self.progress_total.setValue(int(p * 100))

    def _on_progress_item(self, row: int, p: float) -> None:
        pb = self.table.cellWidget(row, COL_PROGRESS)
        if isinstance(pb, QProgressBar):
            pb.setValue(int(p * 100))

    def _on_status(self, text: str) -> None:
        self.lbl_status.setText(text)
        self.log.append(text)

    def _on_item_done(self, row: int, result: object) -> None:
        if not isinstance(result, object):
            return
        res = result  # type: ignore[assignment]
        status = "OK" if getattr(res, "success", False) else "Failed"
        self.table.setItem(row, COL_STATUS, QTableWidgetItem(status))
        errors = getattr(res, "errors", [])
        if errors:
            for e in errors:
                self.log.append(f"Row {row + 1}: {e}")

    def _on_error(self, row: int, message: str) -> None:
        self.table.setItem(row, COL_STATUS, QTableWidgetItem("Error"))
        self.log.append(f"Row {row + 1}: {message}")

    def _on_finished(self, summary: object) -> None:
        self._toggle_running(False)
        if hasattr(summary, "ok") and hasattr(summary, "failed"):
            s = summary  # type: ignore[assignment]
            QMessageBox.information(self, "Done", f"Completed. OK: {s.ok}, Failed: {s.failed}")
        self._worker = None

    def _toggle_running(self, running: bool) -> None:
        self.btn_start.setEnabled(not running)
        self.btn_cancel.setEnabled(running)
        self.table.setEnabled(not running)

    # ---- Settings ----

    def _load_settings_into_ui(self) -> None:
        s = self._settings
        if s.last_output_dir:
            self.edit_output.setText(s.last_output_dir)
        self.combo_mode.setCurrentText(s.last_mode)
        # Options
        self._set_checkbox(self.chk_preserve_exif, s.preserve_exif_orientation)
        self.spin_quality.setValue(s.quality)
        self.spin_dpi.setValue(s.dpi)
        self.edit_page_range.setText(s.page_range)
        self.combo_page_size.setCurrentText(s.page_size)
        self.combo_fit_mode.setCurrentText(s.fit_mode)
        self.spin_margins.setValue(s.margins_mm)
        self.combo_overwrite.setCurrentText(s.overwrite_policy)
        self.edit_pattern.setText(s.naming_pattern or "{name}_{mode}")

    def _save_settings_from_ui(self) -> None:
        s = self._settings
        s.last_output_dir = self.edit_output.text().strip() or s.last_output_dir
        s.last_mode = self.combo_mode.currentText()
        s.preserve_exif_orientation = self._checkbox_value(self.chk_preserve_exif)
        s.quality = self.spin_quality.value()
        s.dpi = self.spin_dpi.value()
        s.page_range = self.edit_page_range.text().strip()
        s.page_size = self.combo_page_size.currentText()
        s.fit_mode = self.combo_fit_mode.currentText()
        s.margins_mm = self.spin_margins.value()
        s.overwrite_policy = self.combo_overwrite.currentText()
        s.naming_pattern = self.edit_pattern.text().strip()
        self._settings_manager.save()

    def closeEvent(self, event) -> None:  # noqa: N802
        self._save_settings_from_ui()
        super().closeEvent(event)

    def _set_checkbox(self, cb: QWidget, value: bool) -> None:
        from PyQt5.QtWidgets import QCheckBox

        if isinstance(cb, QCheckBox):
            cb.setChecked(value)

    def _checkbox_value(self, cb: QWidget) -> bool:
        from PyQt5.QtWidgets import QCheckBox

        if isinstance(cb, QCheckBox):
            return cb.isChecked()
        return False

    def _update_option_visibility(self) -> None:
        mode = self.combo_mode.currentText()
        # Show/hide sets by relevance
        show_heic = mode.startswith("HEIC → ")
        show_img_pdf = mode.startswith("JPG/PNG → PDF")
        show_pdf_img = mode.startswith("PDF → ")
        self.chk_preserve_exif.setVisible(show_heic)
        self.spin_quality.parentWidget().setVisible(show_heic)  # within form layout
        self.chk_merge_single.setVisible(show_img_pdf)
        self.combo_page_size.parentWidget().setVisible(show_img_pdf)
        self.combo_fit_mode.parentWidget().setVisible(show_img_pdf)
        self.spin_margins.parentWidget().setVisible(show_img_pdf)
        self.spin_dpi.parentWidget().setVisible(show_pdf_img)
        self.edit_page_range.parentWidget().setVisible(show_pdf_img)


