# -*- coding: utf-8 -*-
import processing

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)
from qgis.core import (
    Qgis,
    QgsExpression,
    QgsExpressionContext,
    QgsExpressionContextUtils,
    QgsFeature,
    QgsMapLayerProxyModel,
    QgsMessageLog,
    QgsWkbTypes,
)
from qgis.gui import QgsHighlight, QgsMapLayerComboBox, QgsMapToolIdentifyFeature

from .algorithms.trialplotter_algorithm import (
    AUTO_N_COLS_MAX_STEP_M,
    AUTO_POLY_LEN_HALF_M,
    AUTO_POLY_LEN_MARGIN_M,
    TRAITSEEKER_CSV_WIDTH_HALF_MARGIN_M,
    TRAITSEEKER_CSV_WIDTH_MARGIN_M,
    TrialPlotterAlgorithm,
)


FIELD_WIDTH = 340


def _meters_label(value):
    return f"{value:g} m"


def _centimeters_label(value):
    return f"{value * 100:g} cm"


class TrialPlotterDialog(QDialog):
    def __init__(self, iface, parent=None):
        super().__init__(parent or iface.mainWindow())
        self.iface = iface
        self._reference_line_fid = None
        self._reference_line_text = ""
        self._line_highlight = None
        self._identify_tool = None
        self._previous_map_tool = None
        self._picking_active = False

        self.setModal(False)
        self.setWindowTitle("TrialPlotter")
        self.setMinimumWidth(580)
        self.resize(680, 720)

        self._build_ui()
        self._connect_signals()
        self._set_initial_layer()
        self._input_layer_changed()

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
        form = self._form_layout(group)

        self.input_layer = QgsMapLayerComboBox()
        self.input_layer.setFilters(self._reference_layer_filter())
        self._set_field_width(self.input_layer)
        self._add_row(form, "Reference layer", self.input_layer)

        return group

    def _build_line_group(self):
        self.line_group = QGroupBox("Reference")
        form = self._form_layout(self.line_group)

        self.reference_line_label = QLabel("Reference line")
        self.reference_line_field = QWidget()
        picker_layout = QHBoxLayout(self.reference_line_field)
        picker_layout.setContentsMargins(0, 0, 0, 0)

        self.reference_line_value = QLineEdit()
        self.reference_line_value.setReadOnly(True)
        self.reference_line_value.setPlaceholderText("No line chosen")
        picker_layout.addWidget(self.reference_line_value, 1)

        self.pick_line_button = QPushButton("Pick line on map")
        picker_layout.addWidget(self.pick_line_button)
        self._set_field_width(self.reference_line_field)
        form.addRow(self.reference_line_label, self.reference_line_field)

        self.reverse_line = QCheckBox("")
        self.reverse_line.setChecked(False)
        self._set_field_width(self.reverse_line)
        self._add_row(form, "Reverse reference", self.reverse_line)

        self.start_offset = self._distance_spin(0.0, minimum=-1000000.0)
        self._add_row(form, "Offset from start (m)", self.start_offset)

        self.side_offset = self._distance_spin(0.0, minimum=-1000000.0)
        self._add_row(form, "Offset to side (m)", self.side_offset)

        return self.line_group

    def _build_grid_group(self):
        group = QGroupBox("Plot Grid")
        form = self._form_layout(group)

        self.flip_plot_side = QCheckBox("")
        self.flip_plot_side.setChecked(False)
        self._set_field_width(self.flip_plot_side)
        self._add_row(form, "Flip plot side", self.flip_plot_side)

        self.auto_n_cols = QCheckBox(
            f"(maximum {_meters_label(AUTO_N_COLS_MAX_STEP_M)} plot distance)"
        )
        self.auto_n_cols.setChecked(False)
        self._set_field_width(self.auto_n_cols)
        self._add_row(form, "Auto nr of plots", self.auto_n_cols)

        self.n_cols = self._int_spin(1, 100000, 10)
        self._add_row(form, "Plots per row", self.n_cols)

        self.n_rows = self._int_spin(1, 100000, 4)
        self._add_row(form, "Number of rows", self.n_rows)

        self.step_len = self._distance_spin(1.0)
        self._add_row(form, "Plot distance in row (m)", self.step_len)

        self.step_row = self._distance_spin(1.5)
        self._add_row(form, "Plot distance across rows (m)", self.step_row)

        return group

    def _build_polygon_group(self):
        group = QGroupBox("Polygon Size")
        form = self._form_layout(group)

        self.traitseeker_output = QCheckBox(
            f"(CSV {_centimeters_label(TRAITSEEKER_CSV_WIDTH_MARGIN_M)} narrower: "
            f"{_centimeters_label(TRAITSEEKER_CSV_WIDTH_HALF_MARGIN_M)} per row side)"
        )
        self.traitseeker_output.setChecked(False)
        self._set_field_width(self.traitseeker_output)
        self._add_row(form, "TraitSeeker output", self.traitseeker_output)

        self.auto_poly_len = QCheckBox("")
        self.auto_poly_len.setChecked(True)
        self._set_field_width(self.auto_poly_len)
        self._add_row(form, "Auto polygon length", self.auto_poly_len)

        self.poly_len = self._distance_spin(1.0)
        self._add_row(form, "Polygon length in row (m)", self.poly_len)

        self.poly_wid = self._distance_spin(1.5)
        self._add_row(form, "Polygon width across row (m)", self.poly_wid)

        return group

    def _build_gaps_group(self):
        group = QGroupBox("Gaps And Driving")
        form = self._form_layout(group)

        self.gaps_after_col = QLineEdit("-")
        self._set_field_width(self.gaps_after_col)
        self._add_row(form, "Optional gap(s) after plot in sowing direction", self.gaps_after_col)

        self.gaps_after_row = QLineEdit("-")
        self._set_field_width(self.gaps_after_row)
        self._add_row(form, "Optional gap after row(s)", self.gaps_after_row)

        self.route_mode = QComboBox()
        self.route_mode.addItem("Always forward", 0)
        self.route_mode.addItem("Headland (zigzag)", 1)
        self.route_mode.setCurrentIndex(1)
        self._set_field_width(self.route_mode)
        self._add_row(form, "Driving / row direction mode", self.route_mode)

        self.limit_csv = QCheckBox("(max 150 plots, keep whole rows together)")
        self.limit_csv.setChecked(True)
        self._set_field_width(self.limit_csv)
        self._add_row(form, "Split CSV files", self.limit_csv)

        return group

    def _connect_signals(self):
        self.input_layer.layerChanged.connect(self._input_layer_changed)
        self.pick_line_button.clicked.connect(self._toggle_line_picking)
        self.auto_n_cols.toggled.connect(self._sync_enabled_fields)
        self.auto_poly_len.toggled.connect(self._sync_enabled_fields)
        self.traitseeker_output.toggled.connect(self._sync_enabled_fields)
        self.button_box.accepted.connect(self._run_algorithm)
        self.button_box.rejected.connect(self.reject)
        self.finished.connect(self._dialog_finished)

    def _set_initial_layer(self):
        layer = self.iface.activeLayer()
        if layer and hasattr(layer, "geometryType") and (
            layer.geometryType() == self._point_geometry_type()
            or layer.geometryType() == self._line_geometry_type()
        ):
            self.input_layer.setLayer(layer)

    def _sync_enabled_fields(self, *args):
        layer = self.input_layer.currentLayer()
        geometry_type = layer.geometryType() if layer and hasattr(layer, "geometryType") else None
        reference_mode = geometry_type in (
            self._point_geometry_type(),
            self._line_geometry_type(),
        )
        line_mode = geometry_type == self._line_geometry_type()
        self.line_group.setVisible(reference_mode)
        self.reference_line_label.setVisible(line_mode)
        self.reference_line_field.setVisible(line_mode)
        self.pick_line_button.setEnabled(
            line_mode and (self._picking_active or layer.featureCount() > 0)
        )

        manual_grid = not self.auto_n_cols.isChecked()
        self.n_cols.setEnabled(manual_grid)
        self.step_len.setEnabled(manual_grid)
        self.gaps_after_col.setEnabled(manual_grid)
        self.poly_len.setEnabled(not self.auto_poly_len.isChecked())
        if self.traitseeker_output.isChecked():
            self.auto_poly_len.setText(
                f"(cuts {_centimeters_label(AUTO_POLY_LEN_MARGIN_M)}: "
                f"{_centimeters_label(AUTO_POLY_LEN_HALF_M)} front/back)"
            )
        else:
            self.auto_poly_len.setText("(uses full plot distance)")

    def _input_layer_changed(self, *args):
        self._cancel_line_picking()
        self._clear_line_highlight()
        self._reference_line_fid = None
        self._reference_line_text = ""
        self._sync_enabled_fields()

        layer = self.input_layer.currentLayer()
        if not self._is_line_layer(layer):
            self._update_reference_line_display()
            return

        selected_features = sorted(layer.selectedFeatures(), key=lambda feature: feature.id())
        if len(selected_features) == 1:
            self._set_reference_line_feature(selected_features[0])
            return

        if layer.featureCount() == 1:
            feature = next(layer.getFeatures(), None)
            if feature is not None:
                self._set_reference_line_feature(feature)
                return

        self._update_reference_line_display()

    def _toggle_line_picking(self):
        if self._picking_active:
            self._cancel_line_picking()
            return

        layer = self.input_layer.currentLayer()
        if not self._is_line_layer(layer):
            QMessageBox.warning(self, "TrialPlotter", "Select a line reference layer first.")
            return
        if layer.featureCount() == 0:
            QMessageBox.warning(self, "TrialPlotter", "The reference line layer is empty.")
            return

        canvas = self.iface.mapCanvas()
        self._previous_map_tool = canvas.mapTool()
        self._identify_tool = QgsMapToolIdentifyFeature(canvas, layer)
        self._identify_tool.featureIdentified.connect(self._line_feature_picked)
        self._identify_tool.deactivated.connect(self._line_picker_deactivated)
        self._picking_active = True
        self.pick_line_button.setText("Cancel picking")
        self.button_box.button(self._ok_button()).setEnabled(False)
        canvas.setMapTool(self._identify_tool)

        try:
            self.iface.messageBar().pushMessage(
                "TrialPlotter",
                "Click the reference line on the map.",
                level=Qgis.Info,
                duration=4,
            )
        except (AttributeError, TypeError):
            pass

    def _line_feature_picked(self, feature):
        layer = self.input_layer.currentLayer()
        if not self._is_line_layer(layer):
            return

        if not isinstance(feature, QgsFeature):
            feature = layer.getFeature(int(feature))
        if not feature.isValid():
            QMessageBox.warning(
                self,
                "TrialPlotter",
                "The clicked line could not be read. Try selecting it again.",
            )
            return

        self._set_reference_line_feature(feature)
        self._cancel_line_picking()

    def _line_picker_deactivated(self):
        if not self._picking_active:
            return

        tool = self._identify_tool
        self._picking_active = False
        self._identify_tool = None
        self._previous_map_tool = None
        self._reset_picker_controls()
        if tool is not None:
            tool.deleteLater()

    def _cancel_line_picking(self, restore_previous=True):
        tool = self._identify_tool
        previous_tool = self._previous_map_tool
        self._picking_active = False
        self._identify_tool = None
        self._previous_map_tool = None

        if tool is not None:
            try:
                tool.featureIdentified.disconnect(self._line_feature_picked)
            except (RuntimeError, TypeError):
                pass
            try:
                tool.deactivated.disconnect(self._line_picker_deactivated)
            except (RuntimeError, TypeError):
                pass

            canvas = self.iface.mapCanvas()
            if restore_previous and canvas.mapTool() is tool:
                try:
                    if previous_tool is not None:
                        canvas.setMapTool(previous_tool)
                    else:
                        canvas.unsetMapTool(tool)
                except RuntimeError:
                    canvas.unsetMapTool(tool)
            tool.deleteLater()

        self._reset_picker_controls()

    def _reset_picker_controls(self):
        self.pick_line_button.setText("Pick line on map")
        self.button_box.button(self._ok_button()).setEnabled(True)
        self._sync_enabled_fields()

    def _set_reference_line_feature(self, feature):
        layer = self.input_layer.currentLayer()
        if not self._is_line_layer(layer) or not feature.isValid():
            return

        self._reference_line_fid = int(feature.id())
        self._reference_line_text = self._feature_display_text(layer, feature)
        self._update_reference_line_display()
        self._show_line_highlight(layer, feature)

    def _update_reference_line_display(self):
        if self._reference_line_fid is None:
            self.reference_line_value.clear()
            self.reference_line_value.setPlaceholderText("No line chosen — click Pick line on map")
        else:
            self.reference_line_value.setText(self._reference_line_text)

    def _show_line_highlight(self, layer, feature):
        self._clear_line_highlight()
        geometry = feature.geometry()
        if not geometry or geometry.isEmpty():
            return

        highlight = QgsHighlight(self.iface.mapCanvas(), feature, layer)
        color = QColor(255, 140, 0)
        highlight.setColor(color)
        fill_color = QColor(color)
        fill_color.setAlpha(50)
        highlight.setFillColor(fill_color)
        highlight.setWidth(3)
        highlight.show()
        self._line_highlight = highlight

    def _clear_line_highlight(self):
        if self._line_highlight is not None:
            self._line_highlight.hide()
            self._line_highlight = None

    @staticmethod
    def _feature_display_text(layer, feature):
        display_text = ""
        expression_text = (layer.displayExpression() or "").strip()
        if expression_text:
            expression = QgsExpression(expression_text)
            if not expression.hasParserError():
                context = QgsExpressionContext()
                context.appendScopes(QgsExpressionContextUtils.globalProjectLayerScopes(layer))
                context.setFeature(feature)
                value = expression.evaluate(context)
                if not expression.hasEvalError() and value is not None:
                    display_text = str(value).strip()
                    if display_text.upper() == "NULL":
                        display_text = ""

        label = display_text or "Feature"
        return f"{label} (ID: {feature.id()})"

    def cleanup(self):
        self._cancel_line_picking()
        self._clear_line_highlight()

    def _dialog_finished(self, *args):
        self.cleanup()

    def _parameters(self):
        layer = self.input_layer.currentLayer()
        if layer is None:
            raise ValueError("Select a reference layer.")

        self._cancel_line_picking()

        line_feature_id = "-"
        if layer.geometryType() == self._line_geometry_type():
            if self._reference_line_fid is not None:
                feature = layer.getFeature(self._reference_line_fid)
                if not feature.isValid():
                    raise ValueError(
                        f"Reference line feature ID {self._reference_line_fid} no longer exists. "
                        "Pick a line again."
                    )
                line_feature_id = str(self._reference_line_fid)
            elif layer.featureCount() != 1:
                raise ValueError("Pick one reference line on the map.")

        return {
            TrialPlotterAlgorithm.P_INPUT: layer,
            TrialPlotterAlgorithm.P_LINE_FEATURE_ID: line_feature_id,
            TrialPlotterAlgorithm.P_USE_SELECTED_LINE: False,
            TrialPlotterAlgorithm.P_REVERSE_LINE: self.reverse_line.isChecked(),
            TrialPlotterAlgorithm.P_START_OFFSET: self.start_offset.value(),
            TrialPlotterAlgorithm.P_SIDE_OFFSET: self.side_offset.value(),
            TrialPlotterAlgorithm.P_FLIP_PLOT_SIDE: self.flip_plot_side.isChecked(),
            TrialPlotterAlgorithm.P_AUTO_NCOLS: self.auto_n_cols.isChecked(),
            TrialPlotterAlgorithm.P_NCOLS: self.n_cols.value(),
            TrialPlotterAlgorithm.P_NROWS: self.n_rows.value(),
            TrialPlotterAlgorithm.P_STEP_LEN: self.step_len.value(),
            TrialPlotterAlgorithm.P_STEP_ROW: self.step_row.value(),
            TrialPlotterAlgorithm.P_TRAITSEEKER: self.traitseeker_output.isChecked(),
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

    @staticmethod
    def _int_spin(minimum, maximum, value):
        spin = QSpinBox()
        spin.setRange(minimum, maximum)
        spin.setValue(value)
        TrialPlotterDialog._set_field_width(spin)
        return spin

    @staticmethod
    def _distance_spin(value, minimum=0.01):
        spin = QDoubleSpinBox()
        spin.setRange(minimum, 1000000.0)
        spin.setDecimals(2)
        spin.setSingleStep(0.1)
        spin.setValue(value)
        TrialPlotterDialog._set_field_width(spin)
        return spin

    @staticmethod
    def _set_field_width(widget):
        widget.setMinimumWidth(FIELD_WIDTH)
        widget.setMaximumWidth(FIELD_WIDTH)

    @staticmethod
    def _add_row(form, label, widget):
        field = QWidget()
        layout = QHBoxLayout(field)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addStretch(1)
        layout.addWidget(widget)
        form.addRow(label, field)

    @staticmethod
    def _form_layout(parent):
        form = QFormLayout(parent)
        try:
            field_growth_policy = QFormLayout.AllNonFixedFieldsGrow
        except AttributeError:
            field_growth_policy = QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow
        form.setFieldGrowthPolicy(field_growth_policy)
        align_left = TrialPlotterDialog._qt_alignment("AlignLeft")
        align_top = TrialPlotterDialog._qt_alignment("AlignTop")
        form.setFormAlignment(align_left | align_top)
        form.setLabelAlignment(align_left)
        return form

    @staticmethod
    def _qt_alignment(name):
        try:
            return getattr(Qt, name)
        except AttributeError:
            return getattr(Qt.AlignmentFlag, name)

    @staticmethod
    def _reference_layer_filter():
        try:
            return QgsMapLayerProxyModel.PointLayer | QgsMapLayerProxyModel.LineLayer
        except AttributeError:
            return QgsMapLayerProxyModel.Filter.PointLayer | QgsMapLayerProxyModel.Filter.LineLayer

    @classmethod
    def _is_line_layer(cls, layer):
        return bool(
            layer
            and hasattr(layer, "geometryType")
            and layer.geometryType() == cls._line_geometry_type()
        )

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
