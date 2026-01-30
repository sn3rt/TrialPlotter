from qgis.core import (
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingParameterVectorLayer,
    QgsProcessingParameterNumber,
    QgsProcessingParameterEnum,
    QgsProcessingParameterString,
    QgsProcessingParameterBoolean,
    QgsProcessingException,
    QgsProcessingContext,
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
import shutil
import csv
import json
from datetime import datetime


CSV_LIMIT_DEFAULT = 150
AUTO_POLY_LEN_MARGIN_M = 0.25
AUTO_POLY_LEN_HALF_M = AUTO_POLY_LEN_MARGIN_M / 2.0
EPS = 1e-9


def _normalize(dx: float, dy: float):
    n = math.hypot(dx, dy)
    if n == 0:
        raise QgsProcessingException("Cannot normalize a zero-length vector (P1 and P2 are identical).")
    return dx / n, dy / n


def _rotate90_cw(ux: float, uy: float):
    return uy, -ux


def _parse_gap_map(s: str):
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
    if not gaps:
        return 0.0
    s = 0.0
    for k, v in gaps.items():
        if k < idx_1based:
            s += v
    return s


class TrialPlotterAlgorithm(QgsProcessingAlgorithm):
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

    # used to remember what we scheduled, so we can replace safely in postProcessAlgorithm
    _pending_layer_name = None
    _pending_layer_path = None

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
                defaultValue=1.5
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
                "Driving / row direction mode (affects CSV corner order only)",
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

        out_root = os.path.join(base_dir, f"{input_name}_trialplots")
        csv_dir = os.path.join(out_root, "csv")
        poly_dir = os.path.join(out_root, "polygons")
        for d in (csv_dir, poly_dir):
            if os.path.exists(d):
                shutil.rmtree(d)
            os.makedirs(d)

        # ----- Read parameters -----
        n_cols = self.parameterAsInt(parameters, self.P_NCOLS, context)
        n_rows = self.parameterAsInt(parameters, self.P_NROWS, context)
        step_len = self.parameterAsDouble(parameters, self.P_STEP_LEN, context)
        step_row = self.parameterAsDouble(parameters, self.P_STEP_ROW, context)

        auto_poly_len = self.parameterAsBool(parameters, self.P_AUTO_POLY_LEN, context)
        user_poly_len = self.parameterAsDouble(parameters, self.P_POLY_LEN, context)
        poly_wid_user = self.parameterAsDouble(parameters, self.P_POLY_WID, context)

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

        if poly_len - step_len > EPS:
            raise QgsProcessingException("Polygon length must be <= col spacing (STEP_LEN).")
        if poly_wid_user - step_row > EPS:
            raise QgsProcessingException("Polygon width must be <= row spacing (STEP_ROW).")

        poly_wid_used = poly_wid_user
        if abs(poly_wid_user - step_row) < EPS:
            poly_wid_used = max(0.01, poly_wid_user - 0.01)
            feedback.pushInfo(
                f"Polygon width equals row spacing; using {poly_wid_used:.2f} m to keep a small gap between rows."
            )

        col_gaps_raw = self.parameterAsString(parameters, self.P_GAPS_AFTER_COL, context)
        row_gaps_raw = self.parameterAsString(parameters, self.P_GAPS_AFTER_ROW, context)
        col_gaps = _parse_gap_map(col_gaps_raw)
        row_gaps = _parse_gap_map(row_gaps_raw)

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

        # ----- Build local AEQD CRS centered on P1 -----
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

        # u = sowing direction, v = right-hand perpendicular (polygon width direction)
        ux, uy = _normalize(p2.x() - p1.x(), p2.y() - p1.y())
        vx, vy = _rotate90_cw(ux, uy)

        # row_dir = shift direction for rows (default: v)
        row_dir_x, row_dir_y = vx, vy

        if p3 is None:
            feedback.pushInfo("2-point mode: rows shift perpendicular to sowing direction.")
        else:
            kx, ky = _normalize(p3.x() - p1.x(), p3.y() - p1.y())
            if (kx * vx + ky * vy) < 0:
                kx, ky = -kx, -ky

            dot = max(-1.0, min(1.0, ux * kx + uy * ky))
            theta = math.atan2(ux * ky - uy * kx, dot)  # signed
            sin_theta = math.sin(theta)
            if abs(sin_theta) < 1e-6:
                raise QgsProcessingException(
                    "P3 direction is (almost) parallel to P1->P2. Cannot compute row shift from P3."
                )

            scale = 1.0 / abs(sin_theta)
            row_dir_x, row_dir_y = kx * scale, ky * scale

        feedback.pushInfo(f"Output folder: {out_root}")
        feedback.pushInfo(f"Local CRS: AEQD centered on P1 (lat={lat0:.8f}, lon={lon0:.8f})")
        feedback.pushInfo(f"u (sowing direction) = ({ux:.6f}, {uy:.6f}), v (polygon width dir) = ({vx:.6f}, {vy:.6f})")

        # ----- Create in-memory polygon layer -----
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

            cells = {}
            for c in base_cols:
                s = (c - 1) * step_len + _gap_prefix_sum(col_gaps, c)

                ox_grid = p1.x() + ux * s + row_dir_x * t
                oy_grid = p1.y() + uy * s + row_dir_y * t

                ox = ox_grid + ux * poly_offset
                oy = oy_grid + uy * poly_offset

                A = QgsPointXY(ox, oy)
                B = QgsPointXY(ox + ux * poly_len, oy + uy * poly_len)
                C = QgsPointXY(B.x() + vx * poly_wid_used, B.y() + vy * poly_wid_used)
                D = QgsPointXY(ox + vx * poly_wid_used, oy + vy * poly_wid_used)
                cells[c] = (A, B, C, D)

            # numbering ALWAYS forward (no zigzag numbering)
            for c in base_cols:
                A, B, C, D = cells[c]
                plot_id += 1

                ring_local = [A, B, C, D, A]
                ring_src = []
                for pt in ring_local:
                    p_src = from_local_to_src.transform(pt)
                    ring_src.append(QgsPointXY(p_src.x(), p_src.y()))
                geom = QgsGeometry.fromPolygonXY([ring_src])

                feat_out = QgsFeature(fields)
                feat_out.setGeometry(geom)
                feat_out.setAttributes([plot_id, r, c, ""])
                pr.addFeature(feat_out)

                # WGS84 for CSV
                Awgs = from_local_to_wgs.transform(A)
                Bwgs = from_local_to_wgs.transform(B)
                Cwgs = from_local_to_wgs.transform(C)
                Dwgs = from_local_to_wgs.transform(D)

                Apt = QgsPointXY(Awgs.x(), Awgs.y())
                Bpt = QgsPointXY(Bwgs.x(), Bwgs.y())
                Cpt = QgsPointXY(Cwgs.x(), Cwgs.y())
                Dpt = QgsPointXY(Dwgs.x(), Dwgs.y())

                # Your rule:
                # Driving P1->P2: A=LB, B=RB, C=RT, D=LT
                csv_pts = [Apt, Dpt, Cpt, Bpt]  # maps to CSV labels A,B,C,D

                # Headland: even rows drive back
                # required: A=RT, B=LT, C=LB, D=RB
                if route_mode == 1 and (r % 2 == 0):
                    # current csv_pts corresponds to [LB, RB, RT, LT] in CSV labels
                    # we want            corresponds to [RT, LT, LB, RB]
                    csv_pts = [csv_pts[2], csv_pts[1], csv_pts[0], csv_pts[3]]

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

        if isinstance(res, tuple):
            err = res[0]
            msg = res[1] if len(res) > 1 else ""
        else:
            err = res
            msg = ""

        if err != QgsVectorFileWriter.NoError:
            raise QgsProcessingException(f"Failed to save shapefile to {polygons_shp}: {msg}")

        feedback.pushInfo(f"Polygons saved: {polygons_shp}")

        # ----- Schedule polygon layer to be loaded on completion (thread-safe) -----
        layer_name = f"{input_name}_trialplots"
        details = QgsProcessingContext.LayerDetails(layer_name, context.project(), "ogr")
        context.addLayerToLoadOnCompletion(polygons_shp, details)
        feedback.pushInfo("Polygon layer will be added to the project when the algorithm finishes.")

        # remember for postProcessAlgorithm
        self._pending_layer_name = layer_name
        self._pending_layer_path = polygons_shp

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
                w.writerows(chunk)
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
                "POLY_WID_USER": float(poly_wid_user),
                "GAPS_AFTER_COL": col_gaps_raw,
                "GAPS_AFTER_ROW": row_gaps_raw,
                "ROUTE_MODE": "Headland (zigzag)" if route_mode == 1 else "Always forward",
                "LIMIT_CSV": bool(limit_csv),
            },
            "parameters_used": {
                "POLY_LEN_USED": float(poly_len),
                "POLY_OFFSET_USED": float(poly_offset),
                "POLY_WID_USED": float(poly_wid_used),
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

    
    def postProcessAlgorithm(self, context, feedback):
        """
        Main thread: safe to touch QgsProject and layers.
        Strategy:
          - If the layer already exists: try to force-reload it (preferred).
          - If reload fails or no layer exists: remove duplicates and (re)load from disk.
        """
        project = context.project()
        if not project:
            return {}

        layer_name = getattr(self, "_pending_layer_name", None)
        shp_path = getattr(self, "_pending_layer_path", None)

        if not layer_name:
            return {}

        layers = project.mapLayersByName(layer_name)

        # If multiple with same name: keep only the last one (usually newest)
        if len(layers) > 1:
            keep = layers[-1]
            for lyr in layers[:-1]:
                project.removeMapLayer(lyr.id())
            layers = [keep]
            feedback.pushInfo(f"Removed {len(layers)-1} duplicate layer(s) named '{layer_name}'.")

        if len(layers) == 1:
            lyr = layers[0]

            # ---- Preferred: force reload the existing layer ----
            try:
                dp = lyr.dataProvider()
                # These calls exist in modern QGIS; some may be no-ops depending on provider
                if dp is not None:
                    try:
                        dp.forceReload()          # not always available
                    except Exception:
                        pass
                    try:
                        dp.reloadData()           # not always available
                    except Exception:
                        pass

                try:
                    lyr.reload()                  # sometimes available
                except Exception:
                    pass

                lyr.triggerRepaint()
                # Refresh extents (helps after overwrite)
                try:
                    lyr.updateExtents()
                except Exception:
                    pass

                feedback.pushInfo(f"Reloaded layer '{layer_name}' (in-place).")
                return {}

            except Exception as e:
                feedback.reportError(f"Reload failed for '{layer_name}', will remove+re-add. ({e})")

            # ---- Fallback: remove + re-add from disk ----
            project.removeMapLayer(lyr.id())

        # If we get here: there is no layer loaded, or we removed it as fallback.
        if shp_path and os.path.exists(shp_path):
            vl = QgsVectorLayer(shp_path, layer_name, "ogr")
            if vl.isValid():
                project.addMapLayer(vl)
                feedback.pushInfo(f"Layer '{layer_name}' re-added from disk.")
            else:
                feedback.reportError(f"Could not load output layer from: {shp_path}")
        else:
            feedback.reportError(f"Output shapefile not found: {shp_path}")

        return {}


    def name(self):
        return "trialplotter_algorithm"

    def displayName(self):
        return "Trial plots: polygons + CSV (2/3 RTK points)"

    def group(self):
        return "TrialPlotter"

    def groupId(self):
        return "trialplotter"

    def createInstance(self):
        return TrialPlotterAlgorithm()
