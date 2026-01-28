from qgis.core import (
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingParameterVectorLayer,
    QgsProcessingParameterNumber,
    QgsProcessingParameterEnum,
    QgsProcessingParameterString,
    QgsProcessingParameterBoolean,
    QgsProcessingException,
    QgsFeature,
    QgsFields,
    QgsField,
    QgsGeometry,
    QgsPointXY,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsProcessingUtils,
    QgsVectorLayer,
    QgsVectorFileWriter,
    QgsProject,
)
from qgis.PyQt.QtCore import QVariant
import math
import os
import csv
import json
from datetime import datetime


CSV_LIMIT_DEFAULT = 150  # fixed limit when CSV splitting is enabled
AUTO_POLY_LEN_MARGIN_M = 0.25  # total margin between plots along-row
AUTO_POLY_LEN_HALF_M = AUTO_POLY_LEN_MARGIN_M / 2.0  # 12.5 cm front + 12.5 cm back


def _normalize(dx: float, dy: float):
    n = math.hypot(dx, dy)
    if n == 0:
        raise QgsProcessingException("Cannot normalize a zero-length vector (P1 and P2 are identical).")
    return dx / n, dy / n


def _rotate90_cw(ux: float, uy: float):
    # 90° clockwise: (x, y) -> (y, -x)
    return uy, -ux


def _parse_gap_map(s: str):
    """
    Parse: "4:2.0, 10:1.5"
    Sentinel values meaning "no gaps": "", "-", "none", "null"
    Indices are 1-based.
    """
    s = (s or "").strip().lower()
    if not s or s in ("-", "none", "null"):
        return {}

    gaps = {}
    for part in s.split(","):
        part = part.strip()
        if not part:
            continue
        if ":" not in part:
            raise QgsProcessingException(
                f"Invalid gap format '{part}'. Use 'index:meters', e.g. '4:2.0'."
            )
        k, v = part.split(":", 1)
        k = int(k.strip())
        v = float(v.strip())
        if k < 0 or v < 0:
            raise QgsProcessingException(f"Gap entries must be non-negative. Got '{part}'.")
        gaps[k] = gaps.get(k, 0.0) + v
    return gaps


def _gap_prefix_sum(gaps: dict, idx_1based: int):
    """
    Sum all gaps for keys < idx_1based (gaps apply AFTER that index).
    Example: idx=6 includes any gaps after 1..5.
    """
    if not gaps:
        return 0.0
    s = 0.0
    for k, v in gaps.items():
        if k < idx_1based:
            s += v
    return s


class TrialPlotsPolygonsCSVAlgorithm(QgsProcessingAlgorithm):
    """
    Output structure (next to the input points file):
      <inputfolder>/<inputname>_TRIALPLOTS/
        polygons/<inputname>_trialplots.shp (+shx/dbf/prj/...)
        csv/<inputname>_1.csv, <inputname>_2.csv, ...

    Points:
      P1 = lower-left corner of plot 1
      P2 = sowing direction
      P3 (optional) = headland direction reference (controls ROW SHIFT direction only)

    Polygons remain rectangular (90° corners) aligned to sowing direction (P1->P2).
    Row origins can shift along P3 direction while keeping perpendicular row spacing equal to STEP_ROW.

    Polygon length:
      - If AUTO_POLY_LEN enabled: POLY_LEN = STEP_LEN - 0.25m (minimum 0.01m)
      - AND the 0.25m margin is split equally: 12.5cm front + 12.5cm back
        => polygon is shifted forward by +0.125m along sowing direction u.
      - If AUTO_POLY_LEN disabled: polygon starts at origin and uses user-entered POLY_LEN.

    CSV splitting:
      - If enabled: max 150 plots per CSV, but NEVER break a row (baan). (Row length = N_COLS)
      - If disabled: one CSV with all plots.

    CSV corner swapping:
      - Only in Headland (zigzag) mode, even rows swap corners A<->C and B<->D for driving direction.
      - Polygon geometries are NEVER swapped (only numbering order changes).
    """

    P_INPUT = "INPUT_POINTS"
    P_NCOLS = "N_COLS"
    P_NROWS = "N_ROWS"
    P_STEP_LEN = "STEP_LEN"
    P_STEP_ROW = "STEP_ROW"

    P_AUTO_POLY_LEN = "AUTO_POLY_LEN"
    P_POLY_LEN = "POLY_LEN"
    P_POLY_WID = "POLY_WID"

    P_GAPS_AFTER_COL = "GAPS_AFTER_COL"
    P_GAPS_AFTER_ROW = "GAPS_AFTER_ROW"
    P_ROUTE_MODE = "ROUTE_MODE"
    P_LIMIT_CSV = "LIMIT_CSV"

    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterVectorLayer(
                self.P_INPUT,
                "RTK points (P1=lower-left, P2=sowing direction, optional P3=headland direction)",
                types=[QgsProcessing.TypeVectorPoint],
            )
        )

        self.addParameter(
            QgsProcessingParameterNumber(
                self.P_NCOLS,
                "Plots per sowing line (columns)",
                type=QgsProcessingParameterNumber.Integer,
                minValue=1,
                defaultValue=10,
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.P_NROWS,
                "Number of sowing lines (rows)",
                type=QgsProcessingParameterNumber.Integer,
                minValue=1,
                defaultValue=4,
            )
        )

        self.addParameter(
            QgsProcessingParameterNumber(
                self.P_STEP_LEN,
                "Step between plot origins along sowing direction (m)",
                type=QgsProcessingParameterNumber.Double,
                minValue=0.01,
                defaultValue=1.0,
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.P_STEP_ROW,
                "Row spacing (perpendicular distance between sowing lines) (m)",
                type=QgsProcessingParameterNumber.Double,
                minValue=0.01,
                defaultValue=1.5,
            )
        )

        self.addParameter(
            QgsProcessingParameterBoolean(
                self.P_AUTO_POLY_LEN,
                "Auto polygon length (POLY_LEN = STEP_LEN - 0.25 m; centered 12.5cm front/back)",
                defaultValue=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.P_POLY_LEN,
                "Polygon length along sowing direction (m) (used if auto length is OFF)",
                type=QgsProcessingParameterNumber.Double,
                minValue=0.01,
                defaultValue=1.0,
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.P_POLY_WID,
                "Polygon width across sowing direction (m)",
                type=QgsProcessingParameterNumber.Double,
                minValue=0.01,
                defaultValue=1.5,
            )
        )

        self.addParameter(
            QgsProcessingParameterString(
                self.P_GAPS_AFTER_COL,
                "Optional gap after column(s) per row (e.g. '4:2.0,10:1.0'). Use '-' for none.",
                defaultValue="-",
            )
        )
        self.addParameter(
            QgsProcessingParameterString(
                self.P_GAPS_AFTER_ROW,
                "Optional gap after row(s) (e.g. '4:2.0'). Use '-' for none.",
                defaultValue="-",
            )
        )

        self.addParameter(
            QgsProcessingParameterEnum(
                self.P_ROUTE_MODE,
                "Driving / numbering mode",
                options=["Always forward", "Headland (zigzag)"],
                defaultValue=1,
            )
        )

        self.addParameter(
            QgsProcessingParameterBoolean(
                self.P_LIMIT_CSV,
                f"Split CSV files (max {CSV_LIMIT_DEFAULT} plots, keep whole rows together)",
                defaultValue=True,
            )
        )

    def processAlgorithm(self, parameters, context, feedback):
        layer = self.parameterAsVectorLayer(parameters, self.P_INPUT, context)
        if not layer:
            raise QgsProcessingException("Invalid input layer.")

        # ----- Determine output folder (next to input file) -----
        src_path = (layer.source() or "").split("|")[0]
        if not src_path or not os.path.exists(src_path):
            base_dir = QgsProcessingUtils.tempFolder()
            input_name = "trialplots"
            feedback.pushInfo("Input layer does not have a resolvable file path; using QGIS temp folder.")
        else:
            base_dir = os.path.dirname(src_path)
            input_name = os.path.splitext(os.path.basename(src_path))[0]

        out_root = os.path.join(base_dir, f"{input_name}_TRIALPLOTS")
        csv_dir = os.path.join(out_root, "csv")
        poly_dir = os.path.join(out_root, "polygons")
        os.makedirs(csv_dir, exist_ok=True)
        os.makedirs(poly_dir, exist_ok=True)

        # ----- Read parameters -----
        n_cols = self.parameterAsInt(parameters, self.P_NCOLS, context)
        n_rows = self.parameterAsInt(parameters, self.P_NROWS, context)
        step_len = self.parameterAsDouble(parameters, self.P_STEP_LEN, context)
        step_row = self.parameterAsDouble(parameters, self.P_STEP_ROW, context)

        auto_poly_len = self.parameterAsBool(parameters, self.P_AUTO_POLY_LEN, context)
        user_poly_len = self.parameterAsDouble(parameters, self.P_POLY_LEN, context)
        poly_wid = self.parameterAsDouble(parameters, self.P_POLY_WID, context)

        # Auto length + centered offset
        if auto_poly_len:
            poly_len = max(0.01, step_len - AUTO_POLY_LEN_MARGIN_M)
            poly_offset = AUTO_POLY_LEN_HALF_M
            feedback.pushInfo(
                f"Auto polygon length ON: POLY_LEN = STEP_LEN - 0.25 = {poly_len:.3f} m; "
                f"offset = +{poly_offset:.3f} m (12.5cm front/back)"
            )
        else:
            poly_len = user_poly_len
            poly_offset = 0.0
            feedback.pushInfo(f"Auto polygon length OFF: using POLY_LEN = {poly_len:.3f} m; offset = 0.000 m")

        if poly_wid > step_row:
            raise QgsProcessingException("Polygon width must be <= row spacing (STEP_ROW).")

        col_gaps = _parse_gap_map(self.parameterAsString(parameters, self.P_GAPS_AFTER_COL, context))
        row_gaps = _parse_gap_map(self.parameterAsString(parameters, self.P_GAPS_AFTER_ROW, context))
        route_mode = self.parameterAsEnum(parameters, self.P_ROUTE_MODE, context)
        limit_csv = self.parameterAsBool(parameters, self.P_LIMIT_CSV, context)

        # ----- Read 2 or 3 points -----
        feats = sorted(layer.getFeatures(), key=lambda f: f.id())
        if len(feats) not in (2, 3):
            raise QgsProcessingException("Input must contain exactly 2 or 3 points (P1, P2, optional P3).")

        p1_src = feats[0].geometry().asPoint()
        p2_src = feats[1].geometry().asPoint()
        p3_src = feats[2].geometry().asPoint() if len(feats) == 3 else None

        src_crs = layer.crs()
        if not src_crs.isValid():
            raise QgsProcessingException("Input layer CRS is not valid.")

        # ----- Build local AEQD CRS centered on P1 (worldwide meters) -----
        wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
        to_wgs = QgsCoordinateTransform(src_crs, wgs84, context.transformContext())
        p1_wgs = to_wgs.transform(QgsPointXY(p1_src.x(), p1_src.y()))
        lat0 = p1_wgs.y()
        lon0 = p1_wgs.x()

        aeqd_proj = f"+proj=aeqd +lat_0={lat0} +lon_0={lon0} +datum=WGS84 +units=m +no_defs"
        local_crs = QgsCoordinateReferenceSystem()
        local_crs.createFromProj(aeqd_proj)
        if not local_crs.isValid():
            raise QgsProcessingException("Failed to create local AEQD CRS.")

        to_local = QgsCoordinateTransform(src_crs, local_crs, context.transformContext())
        from_local_to_src = QgsCoordinateTransform(local_crs, src_crs, context.transformContext())
        from_local_to_wgs = QgsCoordinateTransform(local_crs, wgs84, context.transformContext())

        p1 = to_local.transform(QgsPointXY(p1_src.x(), p1_src.y()))
        p2 = to_local.transform(QgsPointXY(p2_src.x(), p2_src.y()))
        p3 = to_local.transform(QgsPointXY(p3_src.x(), p3_src.y())) if p3_src is not None else None

        # u = sowing direction
        ux, uy = _normalize(p2.x() - p1.x(), p2.y() - p1.y())
        # v = right-hand perpendicular to u (used for polygon width only)
        vx, vy = _rotate90_cw(ux, uy)

        # row_dir = direction used to shift rows (default: perpendicular to u)
        row_dir_x, row_dir_y = vx, vy

        feedback.pushInfo(f"Output folder: {out_root}")
        feedback.pushInfo(f"Local CRS: AEQD centered on P1 (lat={lat0:.8f}, lon={lon0:.8f})")
        feedback.pushInfo(f"u (sowing direction) = ({ux:.6f}, {uy:.6f}), v (polygon width dir) = ({vx:.6f}, {vy:.6f})")

        if p3 is None:
            feedback.pushInfo("2-point mode: rows shift perpendicular to sowing direction.")
        else:
            # k = headland reference direction
            kx, ky = _normalize(p3.x() - p1.x(), p3.y() - p1.y())
            # ensure it points to the same side as v
            if (kx * vx + ky * vy) < 0:
                kx, ky = -kx, -ky

            dot = max(-1.0, min(1.0, ux * kx + uy * ky))
            theta = math.atan2(ux * ky - uy * kx, dot)  # signed
            sin_theta = math.sin(theta)

            if abs(sin_theta) < 1e-6:
                raise QgsProcessingException(
                    "P3 direction is (almost) parallel to P1->P2. Cannot compute row shift from P3."
                )

            # scale k so perpendicular distance between u-parallel rows equals step_row
            scale = 1.0 / abs(sin_theta)
            row_dir_x, row_dir_y = kx * scale, ky * scale

            feedback.pushInfo(
                f"3-point mode: using P3 for row shift. theta={math.degrees(theta):.2f}°, scale=1/|sin(theta)|={scale:.3f}"
            )

        # ----- Create an in-memory polygon layer (export to shapefile) -----
        fields = QgsFields()
        fields.append(QgsField("plot_id", QVariant.Int))
        fields.append(QgsField("row", QVariant.Int))
        fields.append(QgsField("col", QVariant.Int))
        fields.append(QgsField("comment", QVariant.String))

        mem_uri = (
            f"Polygon?crs={src_crs.authid()}"
            f"&field=plot_id:integer&field=row:integer&field=col:integer&field=comment:string(80)"
        )
        mem_layer = QgsVectorLayer(mem_uri, "trialplots", "memory")
        pr = mem_layer.dataProvider()

        # ----- Generate plots + CSV rows -----
        plot_id = 0
        csv_rows = []

        for r in range(1, n_rows + 1):
            t = (r - 1) * step_row + _gap_prefix_sum(row_gaps, r)

            base_cols = list(range(1, n_cols + 1))
            drive_cols = base_cols[:]
            if route_mode == 1 and (r % 2 == 0):  # Headland: reverse even rows
                drive_cols.reverse()

            # Precompute geometry corners per column (grid-consistent)
            cells = {}
            for c in base_cols:
                s = (c - 1) * step_len + _gap_prefix_sum(col_gaps, c)

                # Origin of the grid cell (not polygon) in local coords
                ox_grid = p1.x() + ux * s + row_dir_x * t
                oy_grid = p1.y() + uy * s + row_dir_y * t

                # Polygon origin: offset forward along u (only when auto length enabled)
                ox = ox_grid + ux * poly_offset
                oy = oy_grid + uy * poly_offset

                A = QgsPointXY(ox, oy)
                B = QgsPointXY(ox + ux * poly_len, oy + uy * poly_len)
                C = QgsPointXY(B.x() + vx * poly_wid, B.y() + vy * poly_wid)
                D = QgsPointXY(ox + vx * poly_wid, oy + vy * poly_wid)

                cells[c] = (A, B, C, D)

            # Write in driving/numbering order
            for c in drive_cols:
                A, B, C, D = cells[c]
                plot_id += 1

                # Polygon geometry in src CRS (NEVER corner-swapped)
                ring_local = [A, B, C, D, A]
                ring_src = []
                for pt in ring_local:
                    p_src = from_local_to_src.transform(pt)
                    ring_src.append(QgsPointXY(p_src.x(), p_src.y()))
                geom = QgsGeometry.fromPolygonXY([ring_src])

                f = QgsFeature(fields)
                f.setGeometry(geom)
                f.setAttributes([plot_id, r, c, ""])
                pr.addFeature(f)

                # Convert polygon points to WGS84
                Awgs = from_local_to_wgs.transform(A)      # polygon A
                Bwgs = from_local_to_wgs.transform(B)      # polygon B (your "left-top")
                Cwgs = from_local_to_wgs.transform(C)      # polygon C
                Dwgs = from_local_to_wgs.transform(D)      # polygon D (your "right-bottom")

                Apt = QgsPointXY(Awgs.x(), Awgs.y())
                Bpt = QgsPointXY(Bwgs.x(), Bwgs.y())
                Cpt = QgsPointXY(Cwgs.x(), Cwgs.y())
                Dpt = QgsPointXY(Dwgs.x(), Dwgs.y())

                # CSV wants: A=left-bottom, D=left-top, B=right-bottom, C=right-top
                csv_pts = [Apt, Dpt, Cpt, Bpt]  # [A, D, C, B] in polygon letters

                # Headland: even rows drive back -> swap left/right
                #if route_mode == 1 and (r % 2 == 0):
                #    # swap A<->B and D<->C in CSV meaning
                #    # current csv_pts = [A, D, C, B]
                #    csv_pts = [csv_pts[3], csv_pts[2], csv_pts[1], csv_pts[0]]  # [B, C, D, A]
                # Headland: even rows drive back -> swap left/right
                if route_mode == 1 and (r % 2 == 0):
                    # swap A<->C and D<->B in CSV meaning
                    # current csv_pts = [A, D, C, B]
                    csv_pts = [csv_pts[2], csv_pts[3], csv_pts[0], csv_pts[1]]  # [C, B, A, D]
              

                Aw, Bw, Cw, Dw = csv_pts



                csv_rows.append([
                    plot_id,
                    Aw.y(), Aw.x(),
                    Bw.y(), Bw.x(),
                    Cw.y(), Cw.x(),
                    Dw.y(), Dw.x(),
                    "NULL"
                ])

        if mem_layer.featureCount() == 0:
            raise QgsProcessingException("No polygons were created (featureCount == 0).")

        # ----- Write polygons shapefile -----
        polygons_shp = os.path.join(poly_dir, f"{input_name}_trialplots.shp")

        opts = QgsVectorFileWriter.SaveVectorOptions()
        opts.driverName = "ESRI Shapefile"
        opts.fileEncoding = "UTF-8"
        opts.actionOnExistingFile = QgsVectorFileWriter.CreateOrOverwriteFile

        res = QgsVectorFileWriter.writeAsVectorFormatV3(
            mem_layer,
            polygons_shp,
            context.transformContext(),
            opts
        )

        # QGIS versions differ: return can be (err, msg) or (err, msg, newFile, newLayer)
        if isinstance(res, tuple):
            err = res[0]
            msg = res[1] if len(res) > 1 else ""
        else:
            err = res
            msg = ""

        if err != QgsVectorFileWriter.NoError:
            raise QgsProcessingException(f"Failed to save shapefile to {polygons_shp}: {msg}")

        feedback.pushInfo(f"Polygons saved: {polygons_shp}")

        # ----- Add/replace polygon layer in project -----
        layer_name = f"{input_name}_trialplots"
        for lyr in QgsProject.instance().mapLayersByName(layer_name):
            QgsProject.instance().removeMapLayer(lyr.id())

        poly_layer = QgsVectorLayer(polygons_shp, layer_name, "ogr")
        if not poly_layer.isValid():
            feedback.reportError(f"Polygon layer could not be loaded into QGIS: {polygons_shp}")
        else:
            QgsProject.instance().addMapLayer(poly_layer)
            feedback.pushInfo(f"Polygon layer '{layer_name}' added to project.")

        # ----- Write CSV(s) -----
        total = len(csv_rows)

        if not limit_csv:
            chunks = [csv_rows]
            n_files = 1
        else:
            plots_per_row = n_cols
            rows_per_file = max(1, CSV_LIMIT_DEFAULT // plots_per_row)
            features_per_file = rows_per_file * plots_per_row

            n_files = max(1, math.ceil(total / features_per_file))
            chunks = []
            for i in range(n_files):
                start = i * features_per_file
                end = min(total, (i + 1) * features_per_file)
                chunks.append(csv_rows[start:end])

        for i, chunk in enumerate(chunks, start=1):
            out_csv = os.path.join(csv_dir, f"{input_name}_{i}.csv")

            with open(out_csv, mode="w", newline="", encoding="utf-8") as fcsv:
                w = csv.writer(fcsv)
                w.writerow([
                    "Plot-ID",
                    "A(LAT)", "A(LONG)",
                    "B(LAT)", "B(LONG)",
                    "C(LAT)", "C(LONG)",
                    "D(LAT)", "D(LONG)",
                    "Comments"
                ])
                for row in chunk:
                    w.writerow(row)

            feedback.pushInfo(f"CSV written: {out_csv} ({len(chunk)} plots)")

        # ----- Write settings / metadata file -----
        settings_path = os.path.join(out_root, f"{input_name}_settings.json")
        settings = {
            "tool": "TrialPlotter",
            "created_utc": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "input_points": {
                "layer_source": layer.source(),
                "crs": src_crs.authid(),
                "num_points": len(feats),
                "p1": {"x": float(p1_src.x()), "y": float(p1_src.y())},
                "p2": {"x": float(p2_src.x()), "y": float(p2_src.y())},
                "p3": (
                    {"x": float(p3_src.x()), "y": float(p3_src.y())}
                    if p3_src is not None
                    else None
                ),
            },
            "local_aeqd": {
                "lat0": float(lat0),
                "lon0": float(lon0),
                "proj4": aeqd_proj,
            },
            "parameters_entered": {
                "N_COLS": int(n_cols),
                "N_ROWS": int(n_rows),
                "STEP_LEN": float(step_len),
                "STEP_ROW": float(step_row),
                "AUTO_POLY_LEN": bool(auto_poly_len),
                "POLY_LEN_USER": float(user_poly_len),
                "POLY_WID": float(poly_wid),
                "GAPS_AFTER_COL": self.parameterAsString(parameters, self.P_GAPS_AFTER_COL, context),
                "GAPS_AFTER_ROW": self.parameterAsString(parameters, self.P_GAPS_AFTER_ROW, context),
                "ROUTE_MODE": "Headland (zigzag)" if route_mode == 1 else "Always forward",
                "LIMIT_CSV": bool(limit_csv),
            },
            "parameters_used": {
                "POLY_LEN_USED": float(poly_len),
                "POLY_OFFSET_USED": float(poly_offset),
                "CSV_LIMIT": int(CSV_LIMIT_DEFAULT) if limit_csv else None,
            },
            "direction_vectors_local": {
                "u_sowing": {"x": float(ux), "y": float(uy)},
                "v_poly_width": {"x": float(vx), "y": float(vy)},
                "row_shift_dir": {"x": float(row_dir_x), "y": float(row_dir_y)},
            },
            "outputs": {
                "output_root": out_root,
                "polygons_shp": polygons_shp,
                "csv_folder": csv_dir,
                "csv_files": int(n_files),
            },
        }

        try:
            with open(settings_path, "w", encoding="utf-8") as fset:
                json.dump(settings, fset, indent=2)
            feedback.pushInfo(f"Settings saved: {settings_path}")
        except Exception as e:
            feedback.reportError(f"Failed to write settings file: {settings_path} ({e})")

        return {
            "OUTPUT_FOLDER": out_root,
            "POLYGONS_SHP": polygons_shp,
            "CSV_FOLDER": csv_dir,
            "CSV_FILES": n_files,
            "CSV_LIMIT": (CSV_LIMIT_DEFAULT if limit_csv else "disabled"),
            "POLY_LEN_USED": poly_len,
            "POLY_OFFSET_USED": poly_offset,
        }

    def name(self):
        return "trial_plots_polygons_csv"

    def displayName(self):
        return "Trial plots: polygons + CSV (2/3 RTK points)"

    def group(self):
        return "TrialPlotter"

    def groupId(self):
        return "trialplotter"

    def createInstance(self):
        return TrialPlotsPolygonsCSVAlgorithm()
