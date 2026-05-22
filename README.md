# TrialPlotter (QGIS Plugin)

TrialPlotter is a QGIS 3 Processing plugin that generates a grid of rectangular trial-plot polygons and CSV corner coordinates from a reference layer. The reference can be 2 or 3 RTK points, or one line with optional start/side offsets and reverse direction.

## Install (from ZIP)

1. Create a ZIP that contains the `TrialPlotter/` plugin folder at the top level:
   ```bash
   # run this from the directory that contains the TrialPlotter/ folder
   zip -r TrialPlotter.zip TrialPlotter
   ```
2. In QGIS: `Plugins` -> `Manage and Install Plugins...` -> `Install from ZIP`
3. Select `TrialPlotter.zip` and install.
4. Find the algorithm in the Processing Toolbox under `TrialPlotter`, or use the TrialPlotter toolbar/menu entry.
