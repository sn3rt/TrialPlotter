# -*- coding: utf-8 -*-
import processing

from qgis.PyQt.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QMessageBox,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)
from qgis.core import Qgis, QgsMapLayerProxyModel, QgsMessageLog, QgsWkbTypes
from qgis.gui import QgsMapLayerComboBox

from .algorithms.trialplotter_algorithm import (
    AUTO_N_COLS_MAX_STEP_M,
    AUTO_POLY_LEN_HALF_M,
    AUTO_POLY_LEN_MARGIN_M,
    TrialPlotterAlgorithm,
)


def _meters_label(value):
    return f"{value:g} m"


def _centimeters_label(value):
    return f"{value * 100:g} cm"


class TrialPlotterDialog(QDialog):
    def __init__(self, iface, parent=None):
        super().__init__(parent or iface.mainWindow())
        self.iface = iface

        self.setWindowTitle("TrialPlotter")
        self.setMinimumWidth(580)
        self.resize(680, 720)

        self._build_ui()
        self._connect_signals()
        self._set_initial_layer()
        self._sync_enabled_fields()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        title = QLabel("Generate trial plot polygons and CSV corner coordinates")
        title.setObjectName("TrialPlotterTitle")
        layout.addWidget(title)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        layout.addWidget(scroll, 1)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.addWidget(self._build_input_group())
        content_layout.addWidget(self._build_line_group())
        content_layout.addWidget(self._build_grid_group())
        content_layout.addWidget(self._build_polygon_group())
        content_layout.addWidget(self._build_gaps_group())
        content_layout.addStretch(1)
        scroll.setWidget(content)

        self.button_box = QDialogButtonBox(self._dialog_buttons())
        self.button_box.button(self._ok_button()).setText("Run")
        layout.addWidget(self.button_box)

    def _build_input_group(self):
        group = QGroupBox("Input")
        form = QFormLayout(group)

        self.input_layer = QgsMapLayerComboBox()
        self.input_layer.setFilters(self._reference_layer_filter())
        form.addRow("Reference layer", self.input_layer)

        return group

    def _build_line_group(self):
        self.line_group = QGroupBox("Line Reference")
        form = QFormLayout(self.line_group)

        self.reverse_line = QCheckBox("")
        self.reverse_line.setChecked(False)
        form.addRow("Reverse line direction", self.reverse_line)

        self.start_offset = self._distance_spin(0.0, minimum=-1000000.0)
        form.addRow("Offset from line start (m)", self.start_offset)

        self.side_offset = self._distance_spin(0.0, minimum=-1000000.0)
        form.addRow("Offset to right side (m)", self.side_offset)

        return self.line_group

    def _build_grid_group(self):
        group = QGroupBox("Plot Grid")
        form = QFormLayout(group)

        self.auto_n_cols = QCheckBox(
            f"(maximum {_meters_label(AUTO_N_COLS_MAX_STEP_M)} plot distance)"
        )
        self.auto_n_cols.setChecked(False)
        form.addRow("Auto nr of plots", self.auto_n_cols)

        self.n_cols = self._int_spin(1, 100000, 10)
        form.addRow("Plots per sowing line (columns)", self.n_cols)

        self.n_rows = self._int_spin(1, 100000, 4)
        form.addRow("Number of sowing lines (rows)", self.n_rows)

        self.step_len = self._distance_spin(1.0)
        form.addRow("Plot distance in sowing direction (m)", self.step_len)

        self.step_row = self._distance_spin(1.5)
        form.addRow("Plot distance across to sowing direction (m)", self.step_row)

        return group

    def _build_polygon_group(self):
        group = QGroupBox("Polygon Size")
        form = QFormLayout(group)

        self.auto_poly_len = QCheckBox(
            f"(creates {_centimeters_label(AUTO_POLY_LEN_MARGIN_M)} space between plots "
            f"({_centimeters_label(AUTO_POLY_LEN_HALF_M)} front/back))"
        )
        self.auto_poly_len.setChecked(True)
        form.addRow("Auto polygon length", self.auto_poly_len)

        self.poly_len = self._distance_spin(1.0)
        form.addRow("Polygon length in sowing direction (m)", self.poly_len)

        self.poly_wid = self._distance_spin(1.5)
        form.addRow("Polygon width across sowing direction (m)", self.poly_wid)

        return group

    def _build_gaps_group(self):
        group = QGroupBox("Gaps And Driving")
        form = QFormLayout(group)

        self.gaps_after_col = QLineEdit("-")
        form.addRow("Optional gap(s) after plot in sowing direction", self.gaps_after_col)

        self.gaps_after_row = QLineEdit("-")
        form.addRow("Optional gap after row(s)", self.gaps_after_row)

        self.route_mode = QComboBox()
        self.route_mode.addItem("Always forward", 0)
        self.route_mode.addItem("Headland (zigzag)", 1)
        self.route_mode.setCurrentIndex(1)
        form.addRow("Driving / row direction mode", self.route_mode)

        self.limit_csv = QCheckBox("(max 150 plots, keep whole rows together)")
        self.limit_csv.setChecked(True)
        form.addRow("Split CSV files", self.limit_csv)

        return group

    def _connect_signals(self):
        self.input_layer.layerChanged.connect(self._sync_enabled_fields)
        self.auto_n_cols.toggled.connect(self._sync_enabled_fields)
        self.auto_poly_len.toggled.connect(self._sync_enabled_fields)
        self.button_box.accepted.connect(self._run_algorithm)
        self.button_box.rejected.connect(self.reject)

    def _set_initial_layer(self):
        layer = self.iface.activeLayer()
        if layer and hasattr(layer, "geometryType") and (
            layer.geometryType() == self._point_geometry_type()
            or layer.geometryType() == self._line_geometry_type()
        ):
            self.input_layer.setLayer(layer)

    def _sync_enabled_fields(self, *args):
        layer = self.input_layer.currentLayer()
        line_mode = bool(layer and hasattr(layer, "geometryType") and layer.geometryType() == self._line_geometry_type())
        self.line_group.setVisible(line_mode)

        manual_grid = not self.auto_n_cols.isChecked()
        self.n_cols.setEnabled(manual_grid)
        self.step_len.setEnabled(manual_grid)
        self.gaps_after_col.setEnabled(manual_grid)
        self.poly_len.setEnabled(not self.auto_poly_len.isChecked())

    def _parameters(self):
        layer = self.input_layer.currentLayer()
        if layer is None:
            raise ValueError("Select a reference layer.")

        return {
            TrialPlotterAlgorithm.P_INPUT: layer,
            TrialPlotterAlgorithm.P_REVERSE_LINE: self.reverse_line.isChecked(),
            TrialPlotterAlgorithm.P_START_OFFSET: self.start_offset.value(),
            TrialPlotterAlgorithm.P_SIDE_OFFSET: self.side_offset.value(),
            TrialPlotterAlgorithm.P_AUTO_NCOLS: self.auto_n_cols.isChecked(),
            TrialPlotterAlgorithm.P_NCOLS: self.n_cols.value(),
            TrialPlotterAlgorithm.P_NROWS: self.n_rows.value(),
            TrialPlotterAlgorithm.P_STEP_LEN: self.step_len.value(),
            TrialPlotterAlgorithm.P_STEP_ROW: self.step_row.value(),
            TrialPlotterAlgorithm.P_AUTO_POLY_LEN: self.auto_poly_len.isChecked(),
            TrialPlotterAlgorithm.P_POLY_LEN: self.poly_len.value(),
            TrialPlotterAlgorithm.P_POLY_WID: self.poly_wid.value(),
            TrialPlotterAlgorithm.P_GAPS_AFTER_COL: self.gaps_after_col.text().strip() or "-",
            TrialPlotterAlgorithm.P_GAPS_AFTER_ROW: self.gaps_after_row.text().strip() or "-",
            TrialPlotterAlgorithm.P_ROUTE_MODE: self.route_mode.currentData(),
            TrialPlotterAlgorithm.P_LIMIT_CSV: self.limit_csv.isChecked(),
        }

    def _run_algorithm(self):
        try:
            result = processing.runAndLoadResults(
                "wur_trialplotter:trialplotter_algorithm",
                self._parameters(),
            )
        except Exception as e:
            QgsMessageLog.logMessage(str(e), "TrialPlotter", Qgis.Critical)
            QMessageBox.critical(self, "TrialPlotter", f"Could not generate trial plots:\n{e}")
            return

        output_folder = result.get("OUTPUT_FOLDER", "")
        csv_files = result.get("CSV_FILES", "")
        details = []
        if output_folder:
            details.append(f"Output folder:\n{output_folder}")
        if csv_files:
            details.append(f"CSV files: {csv_files}")

        QMessageBox.information(
            self,
            "TrialPlotter",
            "Trial plots generated." + ("\n\n" + "\n".join(details) if details else ""),
        )
        self.accept()

    @staticmethod
    def _int_spin(minimum, maximum, value):
        spin = QSpinBox()
        spin.setRange(minimum, maximum)
        spin.setValue(value)
        return spin

    @staticmethod
    def _distance_spin(value, minimum=0.01):
        spin = QDoubleSpinBox()
        spin.setRange(minimum, 1000000.0)
        spin.setDecimals(2)
        spin.setSingleStep(0.1)
        spin.setValue(value)
        return spin

    @staticmethod
    def _reference_layer_filter():
        try:
            return QgsMapLayerProxyModel.PointLayer | QgsMapLayerProxyModel.LineLayer
        except AttributeError:
            return QgsMapLayerProxyModel.Filter.PointLayer | QgsMapLayerProxyModel.Filter.LineLayer

    @staticmethod
    def _point_geometry_type():
        try:
            return QgsWkbTypes.PointGeometry
        except AttributeError:
            return Qgis.GeometryType.Point

    @staticmethod
    def _line_geometry_type():
        try:
            return QgsWkbTypes.LineGeometry
        except AttributeError:
            return Qgis.GeometryType.Line

    @staticmethod
    def _ok_button():
        try:
            return QDialogButtonBox.Ok
        except AttributeError:
            return QDialogButtonBox.StandardButton.Ok

    @staticmethod
    def _cancel_button():
        try:
            return QDialogButtonBox.Cancel
        except AttributeError:
            return QDialogButtonBox.StandardButton.Cancel

    @classmethod
    def _dialog_buttons(cls):
        return cls._ok_button() | cls._cancel_button()
