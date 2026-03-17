# TrialPlotter (QGIS Plugin)

TrialPlotter is a QGIS 3 Processing plugin that generates a grid of rectangular trial-plot polygons and CSV corner coordinates from 2 or 3 RTK points (supports optional headland/zigzag driving and a 3rd point for row-shift direction).

## Install (from ZIP)

1. Create a ZIP that contains the `TrialPlotter/` plugin folder at the top level:
   ```bash
   # run this from the directory that contains the TrialPlotter/ folder
   zip -r TrialPlotter.zip TrialPlotter
   ```
2. In QGIS: `Plugins` -> `Manage and Install Plugins...` -> `Install from ZIP`
3. Select `TrialPlotter.zip` and install.
4. Find the algorithm in the Processing Toolbox under `TrialPlotter`, or use the TrialPlotter toolbar/menu entry.
