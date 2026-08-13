# TrialPlotter (QGIS Plugin)

TrialPlotter is a QGIS 3 Processing plugin that generates a grid of rectangular trial-plot polygons and CSV corner coordinates from a reference layer. The reference can be 2 or 3 RTK points, one line, or one selected feature from a line layer with optional start/side offsets and reverse direction.

## Reference lines and TraitSeeker output

- Enable `Use selected feature` to use exactly one line selected in the QGIS map from a layer containing multiple lines. Point references continue to use all 2 or 3 points in their layer.
- Enable `TraitSeeker output` to make CSV footprints 1 cm narrower across the rows, centered with 0.5 cm removed from each side. The generated polygon layer keeps the configured width.
- With automatic polygon length enabled, normal output uses the full plot distance. TraitSeeker output removes 25 cm in the sowing direction, centered with 12.5 cm removed from the front and back. Manual polygon lengths are not changed.

## Install (from ZIP)

1. Create a ZIP that contains the `TrialPlotter/` plugin folder at the top level:
   ```bash
   # run this from the directory that contains the TrialPlotter/ folder
   zip -r TrialPlotter.zip TrialPlotter
   ```
2. In QGIS: `Plugins` -> `Manage and Install Plugins...` -> `Install from ZIP`
3. Select `TrialPlotter.zip` and install.
4. Find the algorithm in the Processing Toolbox under `TrialPlotter`, or use the TrialPlotter toolbar/menu entry.
