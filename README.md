# TrialPlotter (QGIS Plugin)

TrialPlotter is a QGIS 3 Processing plugin that generates a grid of rectangular trial-plot polygons and CSV corner coordinates from a reference layer. The reference can be 2 or 3 RTK points, one line, or one chosen feature from a line layer with optional start/side offsets and reverse direction.

## Reference lines and TraitSeeker output

- In the TrialPlotter window, use `Pick line on map` to choose a feature from a line layer. The chosen line is temporarily highlighted and the layer's configured display label plus feature ID is shown in the window. A sole line or exactly one already-selected line is filled in automatically.
- In the Processing Toolbox, either provide `Reference line feature ID` or enable `Use selected feature`. An explicit feature ID takes priority.
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
