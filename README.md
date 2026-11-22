# MetaFOCUS

**Metashape Functions for Optimising, Characterising, and Understanding Structure from Motion**

A toolbox of Python scripts for Agisoft Metashape Professional, free software under the GNU General Public License, version 3 or any later version. The toolbox provides control-measurement diagnostics, structure-from-motion (SfM) process analysis, filtering and project management, and surface-derived outputs such as image footprints and the ground sampling distance (GSD).

## How to cite

If you use MetaFOCUS in academic work, please cite the MetaFOCUS paper:

Verhoeven, G. J., Ressl, C., and Wieser, M. (2026). MetaFOCUS: a Python Toolbox for Optimising, Characterising, and Understanding Structure-from-Motion Pipelines in Agisoft Metashape Professional. 9th ISPRS Low-Cost 3D Workshop, Braunschweig, Germany, 18–20 November 2026.

The volume, pages, and DOI of the paper will be added to this reference. On GitHub, the link Cite this repository gives the paper, in APA and BibTeX, from the file `CITATION.cff`. The software itself is archived on Zenodo, where [10.5281/zenodo.24341336](https://doi.org/10.5281/zenodo.24341336) always leads to the latest version.

## Requirements

- Agisoft Metashape Professional 2.x
- Developed for Metashape Professional 2.3.2
- NumPy, only for the normalised 8-bit and 16-bit depth export of Depth & Render Export (the 32-bit export works without it). Metashape runs scripts with its own Python installation, which does not include NumPy. The Depth maps tab of Depth & Render Export shows how to install NumPy into that installation. Restart Metashape afterwards.

## Installation

### Download

1. Open the page of the latest release, https://github.com/BeyondConventionalBoundaries/MetaFOCUS/releases/latest. On the GitHub page of MetaFOCUS, **Releases** in the right-hand column leads there as well.
2. Under **Assets**, click the MetaFOCUS ZIP file, for example `MetaFOCUS_1-1-0.zip`, to download it. The two **Source code** files are not needed.
3. Extract the ZIP file into any folder, for example Downloads. On Windows, right-click the file, choose **Extract All…**, and click **Extract**. On macOS, double-click the file. This gives a folder `MetaFOCUS` with `MetaFOCUS.py` and the folder `MetaFOCUSCore`.

### Option A: Automatic (recommended)

1. Download and extract the MetaFOCUS ZIP file (see [Download](#download)).
2. Open Metashape and go to **Tools > Run Script**.
3. Select `MetaFOCUS.py` from the extracted folder.
4. When it runs from outside Metashape’s scripts directory (first run or update), MetaFOCUS offers to install itself permanently.
   Click **‘Install permanently’** and it copies `MetaFOCUS.py` and the `MetaFOCUSCore/` folder to the scripts directory.
5. The MetaFOCUS menu loads at once for this session and on every later startup. No restart is needed. An update in a running Metashape reloads every module of MetaFOCUSCore.

### Option B: Manual

1. Download and extract the MetaFOCUS ZIP file (see [Download](#download)).
2. Copy `MetaFOCUS.py` and the `MetaFOCUSCore/` folder to Metashape’s scripts directory:
   - **Windows:** `C:\Users\<YourUsername>\AppData\Local\Agisoft\Metashape Pro\scripts\`
   - **macOS:** `~/Library/Application Support/Agisoft/Metashape Pro/scripts/`
   - **Linux:** `~/.local/share/Agisoft/Metashape Pro/scripts/`
3. Restart Metashape. The MetaFOCUS menu appears automatically.

### Option C: Session only

1. Download and extract the MetaFOCUS ZIP file (see [Download](#download)).
2. Open Metashape and go to **Tools > Run Script**.
3. Select `MetaFOCUS.py` from the extracted folder.
4. Click **‘Load this session only’** when prompted.
5. The MetaFOCUS menu is available until you close Metashape.

### Updates

**MetaFOCUS > Check for Updates** shows the changes of a new release and installs it after a click on **Install**. When allowed, MetaFOCUS also checks for updates when it starts (at most once a day). After Install, Metashape must be restarted.

## Tools

The MetaFOCUS menu has four groups of tools. The tables below describe the 15 tools, group by group and in menu order. A click on a name in this index jumps to its description:

- **SfM Process Diagnostics:** [SfM Log Parser](#tool-sfm-log-parser), [Interior Orientation Viewer](#tool-interior-orientation-viewer), [Tie Point Covariance Exporter](#tool-tie-point-covariance-exporter), and [Image Parameter Exporter](#tool-image-parameter-exporter)
- **Control Measurements and Residuals:** [Marker Residual Inspector](#tool-marker-residual-inspector), [Unpinned Marker Projection Remover](#tool-unpinned-marker-projection-remover), and [Scale Bar A Priori Uncertainty](#tool-scale-bar-a-priori-uncertainty)
- **Filtering and Project Management:** [Gradual Selection Filter](#tool-gradual-selection-filter), [Image Subset Selector](#tool-image-subset-selector), [Image Filter](#tool-image-filter), and [Region Tools](#tool-region-tools)
- **Surface-Derived Project Outputs:** [Image Footprint Generator](#tool-image-footprint-generator), [GSD Value Exporter](#tool-gsd-value-exporter), [GSD Visualiser](#tool-gsd-visualiser), and [Depth & Render Export](#tool-depth-and-render-export)

### SfM Process Diagnostics

| Module | Description |
|--------|-------------|
| **SfM Log Parser** | <a name="tool-sfm-log-parser"></a>Parses the logs of Metashape structure-from-motion (SfM) runs (Align Photos, as TXT, LOG, or HTML) or clipboard content. Renders a structured summary of one SfM run with a text flowchart, and compares the log with the open chunk. Five graphs show the log: the image pairs that it reports, the growth of the 3D tie points, the keypoints per image, the pipeline funnel, and all its runs side by side. A sixth shows which images of the open chunk share valid 3D tie points. |
| **Interior Orientation Viewer** | <a name="tool-interior-orientation-viewer"></a>Shows the interior orientation parameter estimates of each interior orientation group with their standard errors, estimate/SE with its two-sided p-value, the estimated degrees of freedom, and the correlation matrix. It plots distortion vector fields, residual fields, and profile curves, by default as Metashape’s Distortion Plot defines them, and the coverage of the 2D tie points. **Export CSV** writes the numbers, and **Export HTML** writes an interactive report of the group, or of every group that has one, each as a single HTML file. |
| **Tie Point Covariance Exporter** | <a name="tool-tie-point-covariance-exporter"></a>Exports the formal 3D position uncertainty of each 3D tie point: per-axis standard deviations, the full covariance matrix, and the error-ellipsoid decomposition. The uncertainty is given in the world coordinates of the chunk or in local east-north-up coordinates. Tie-point covariances exist only after Optimize Cameras has been run with ‘Estimate tie point covariance’ ticked. Every 3D tie point that is not exported is written with the reason to a companion file. |
| **Image Parameter Exporter** | <a name="tool-image-parameter-exporter"></a>Exports per-image metadata to CSV: Exif metadata, orientation, pixel column and row counts, principal distance, 2D tie-point and matched keypoint counts, and reprojection errors. It also exports GSD statistics, image scale numbers, and Reference panel data. Metashape can hold the reference coordinates of the images in a coordinate reference system (CRS) other than that of the chunk, and the export keeps that CRS. |

### Control Measurements and Residuals

| Module | Description |
|--------|-------------|
| **Marker Residual Inspector** | <a name="tool-marker-residual-inspector"></a>Shows per-marker and per-image reprojection residuals in sortable tables, with vector coherence and object-space errors in local east-north-up coordinates. It gives each marker an object-space status and review flags, and invalidates selected projections with care. |
| **Unpinned Marker Projection Remover** | <a name="tool-unpinned-marker-projection-remover"></a>Removes unpinned (blue-flagged) marker projections and keeps pinned (green-flagged) ones. Markers that would keep fewer than two usable projections are listed first, and by default every listed marker keeps all its projections. A projection is usable when it is valid and lies on an enabled image with an estimated orientation. Fiducial marks of scanned film images are never changed. |
| **Scale Bar A Priori Uncertainty** | <a name="tool-scale-bar-a-priori-uncertainty"></a>Assigns tape-derived a priori standard uncertainties to reference distances measured with a tape (and entered as Metashape scale bars), and writes them to their Accuracy field. The uncertainties are derived from the maximum permissible error (MPE) of the OIML R 35-1 tape class, with options for distances measured from an end surface of the tape and for tapes in service. |

### Filtering and Project Management

| Module | Description |
|--------|-------------|
| **Gradual Selection Filter** | <a name="tool-gradual-selection-filter"></a>Makes Metashape’s Clean Tie Points (Gradual Selection before Metashape 2.3) iterative, with Optimize Cameras after each iteration, and adds what is needed to judge the result. It uses one or more of these four criteria: Reconstruction Uncertainty, Projection Accuracy, Reprojection Error, and Image Count. Its first step, iteration 0, removes no tie points but re-runs Optimize Cameras with the settings chosen in the tool, so that all iterations are adjusted the same way. Quality metrics are recorded after each iteration, among them the check-point and control-point RMSE and sigma nought. It then suggests an iteration using a one-sided Wilcoxon signed-rank test when check points or control points decide, and otherwise using the RMS reprojection error. |
| **Image Subset Selector** | <a name="tool-image-subset-selector"></a>Enables or disables images at regular intervals to thin dense image sets, select image subsets for validation experiments, or re-enable all images. |
| **Image Filter** | <a name="tool-image-filter"></a>Filters the Photos pane to show only enabled or disabled images, and selects them for the 3D view. When no image matches, it says so and leaves the Photos pane and the selection as they are. |
| **Region Tools** | <a name="tool-region-tools"></a>Aligns the chunk’s coordinate axes with the region by replacing the chunk transform (the reverse of Metashape’s built-in Rotate Region to Local Frame), and copies the region to other chunks, accounting for their transforms and coordinate reference systems (CRSs). The chunk keeps its CRS setting, but world coordinates are then measured from the new origin along the new axes. |

### Surface-Derived Project Outputs

| Module | Description |
|--------|-------------|
| **Image Footprint Generator** | <a name="tool-image-footprint-generator"></a>Generates image footprints as Metashape shapes with area, perimeter, and GSD attributes, on a 2D plane, a 3D triangular mesh, or a 2.5D raster DEM. On a triangular mesh, a partial footprint follows the outer edge of the part of the image whose rays meet the mesh. |
| **GSD Value Exporter** | <a name="tool-gsd-value-exporter"></a>Exports the GSD at sampled pixel positions to a CSV file for detailed spatial analysis. The GSD of MetaFOCUS is the area-equivalent GSD: the side of the square whose area the projected row and column steps span. It runs for a single image or in batches, on a plane, a triangular mesh, or a raster DEM, with inspection markers at the GSD extremes. All values inside the CSV can be visualised with the next tool. |
| **GSD Visualiser** | <a name="tool-gsd-visualiser"></a>Loads one or more GSD CSVs (see previous tool) and shows per-image heat maps plus an overview tab of the statistics. |
| **Depth & Render Export** | <a name="tool-depth-and-render-export"></a>Exports the stored multi-view stereo depth maps (32-bit float multiplied by the chunk scale, or normalised greyscale with 0 for no data). Renders images, depth (internal chunk units), and masks from the triangular mesh, for the oriented images or the keyframes of the animation track. By default, a JSON calibration file next to each depth image holds the calibration and transforms, so the depth image can provide 3D points outside Metashape. |

The tools write their main actions, run settings, and results to Metashape’s Console pane.

The tools that write CSV files offer the same choice of delimiter and decimal separator. The choices are a comma with a decimal point (1.25), a semicolon with a decimal comma (1,25), and a tab with a decimal point.

The shared module `photogrammetryUtilities.py` holds the computations that several tools use, such as ray tracing, plane fitting, GSD computation, and observation and unknown counting for the bundle adjustment.

## Manual and Terminology

Below the groups of tools, **Manual and Terminology** shows all About sections (of the toolbox itself and of the 15 individual tools, in the order they appear in the menu) and the terminology in one window. It takes them from the code of the version in use. In the running text of an About section, the first appearance of a term links to its entry in the terminology, and the **Back** button returns the reader to the place before the terminology click. Its **Save as PDF** button saves everything as one PDF file, with each About section and the terminology starting on a new page, and **Save Terminology as PDF** saves only the terminology. The PDF files keep the web links but not the links within the manual, which PDF readers cannot follow. The contents list in the manual’s PDF file shows the page for each section instead.

The About sections give the method, the sources, and the limits of each tool, for example how the GSD is computed on a plane, a triangular mesh, or a raster DEM. The terminology defines the terms of the tools, their About sections, and the MetaFOCUS paper, each in one sense only, with the literature that they follow. For example, it keeps the pixels of the digital image apart from the photosites of the sensor. A concordance table maps the (often incorrect or vague) terms used by Metashape to those used by MetaFOCUS.

## Issues

Faults in MetaFOCUS tools and suggestions to improve their code can be reported as issues on GitHub, using the two forms in the repository. The fault form asks for the tool, the versions of MetaFOCUS and Metashape, the operating system, the steps that lead to the fault, what happened, and the Console text. Issues are not for help with Metashape itself or with individual projects. Pull requests are disabled because every change to MetaFOCUS goes through various automated and manual tests that are not part of this repository. A change can be proposed with the suggestion form, which can also hold code.

## Author

Geert J. Verhoeven (ORCID: [0000-0003-4825-9604](https://orcid.org/0000-0003-4825-9604))

## Licence

MetaFOCUS is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 3 of the License, or (at your option) any later version. MetaFOCUS is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY, without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details. The full text of the GNU General Public License, version 3, is in the file `LICENSE`.

MetaFOCUS runs inside Agisoft Metashape and uses its Python module, which is not free software. As an additional permission under section 7 of the GNU General Public License, version 3, the licensors allow MetaFOCUS, and any work based on it, to be combined with Agisoft Metashape and its Python module and to be conveyed in that combination. The permission is in the file `ADDITIONAL_PERMISSION.txt`. In SPDX notation, the licence of MetaFOCUS is GPL-3.0-or-later WITH GPL-3.0-linking-exception.

The two functions of Region Tools are adapted from scripts in Agisoft’s repository of Python scripts for Metashape (https://github.com/agisoft-llc/metashape-scripts), which are licensed under the MIT License, Copyright (c) 2017 Agisoft LLC. Its notice, which applies to the adapted parts, is in the file `THIRD_PARTY_NOTICES.txt` and in the header of `MetaFOCUSCore/regionTools.py`.
