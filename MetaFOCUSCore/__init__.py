"""
MetaFOCUS Core Package
======================

Metashape Functions for Optimising, Characterising, and
Understanding Structure from Motion.

This package contains the core modules for the MetaFOCUS
toolbox for Agisoft Metashape Professional.

Modules (grouped as in the MetaFOCUS menu):

  SfM Process Diagnostics
    sfmLogParser                  : parse and summarise Metashape SfM logs
    interiorOrientationViewer     : interior orientation parameter statistics and correlation viewer
    interiorOrientationReport     : the Export HTML page of the Interior Orientation Viewer
    tiePointCovarianceExporter    : tie-point 3D uncertainty export
    imageParameterExporter        : per-image metadata CSV export

  Control Measurements and Residuals
    markerResidualInspector       : inspect marker reprojection residuals
    unpinnedMarkerProjectionRemover: remove unpinned projections (by default every listed marker keeps its projections)
    scaleBarAPrioriUncertainty    : Scale Bar A Priori Uncertainty, tape-derived a priori standard
                                    uncertainties of reference distances held by Metashape scale bars

  Filtering and Project Management
    gradualSelectionFilter        : iterative gradual selection of tie points
    imageSubsetSelector           : enable or disable images at intervals
    imageFilter                   : filter the Photos pane by image state
    regionTools                   : align the coordinate axes with the region, copy the region across chunks

  Surface-Derived Project Outputs
    footprintGenerator            : image footprint generation
    gsdValueExporter              : GSD export at sampled pixel positions
    gsdVisualiser                 : visualise one or more exported GSD CSVs
    depthRenderExporter           : export depth maps, render triangular mesh image, depth, and mask

  Shared
    photogrammetryUtilities       : shared utility functions (not a menu item)

  Reference
    aboutPages                    : Manual and Terminology, every About section and the terminology
    terminology                   : the terminology with its sources (in Manual and Terminology)

  Updates
    updateCheck                   : Check for Updates, and the automatic check when it is allowed

Author:   Geert J. Verhoeven (ORCID: 0000-0003-4825-9604)
Licence:  GPL-3.0-or-later WITH GPL-3.0-linking-exception
"""

_METAFOCUS_VERSION = "1.1.0"
_METAFOCUS_DATE = "2026-11-22"
