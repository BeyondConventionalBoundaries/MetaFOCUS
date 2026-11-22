"""
Interior Orientation Report for Agisoft Metashape Professional
==============================================================

Writes the report of one interior orientation group of the Interior
Orientation Viewer as one self-contained HTML file (Export HTML of the
viewer), modelled on the camera calibration report of CameraCalibrator
(Photometrix) that Geert sent on 10 October 2026: the
project and the group, the parameters with their standard errors,
estimate/SE, p-values, correlations, and the estimated degrees of
freedom, the camera model of Metashape with its equations, the vector
fields with their RMS and maximum, the radial and decentring distortion
profiles with tables of their values, and the notes of the viewer on
the rays of the grid points.  The report also gives the coverage of
the image by the 2D tie points, as in the viewer: the Coverage mode with
its legend, and the coverage by radius under the profiles, in all
directions or in the wedge of a direction chosen in the Coverage map.

The page needs no internet connection and no further software.  Its
data are embedded as JSON, and a script in the page draws the plots and
lets the reader change them as in the viewer: the vector field, its
magnification, the statistic of the residuals, Gaussian or balanced
radial distortion and its zero crossing, the overlays of the decentring
profile, the decentring angle, and the direction of the coverage by
radius.  Pointing at a plot shows the values under the pointer in a
box beside it, with the texts of the tooltips of the viewer and the
names that the page gives its plots and curves (Geert, 15
October 2026: "add the values under the pointer to the page, as in the
viewer").  Printing the page, for example to a PDF file,
gives the plots as set, each section starting on a new page.

Author:      Geert J. Verhoeven (ORCID: 0000-0003-4825-9604)
Version:     1.0.1 (2026-11-22)
Date:        2026-10-10
Licence:     GPL-3.0-or-later WITH GPL-3.0-linking-exception
Contact:     geert.verhoeven@univie.ac.at
Requires:    Agisoft Metashape Professional 2.x+
Developed for: Agisoft Metashape Professional 2.3.2

Usage:
    Interior Orientation Viewer > Export HTML (one interior orientation
    group, the tab shown).
"""

import datetime
import html
import json
import math
import os
import tempfile

from .photogrammetryUtilities import ReportSuppressedError

# Version of the data that the page embeds (REPORT_DATA_ELEMENT_ID).
REPORT_FORMAT_VERSION = 1

# Significant digits of the coordinates and displacements of the vector
# fields in the page: more than every number that the page shows needs,
# so that the balanced radial field, which the page computes, agrees with
# that of the viewer to about 1e-6 px.
REPORT_VECTOR_DIGITS = 10

REPORT_DATA_ELEMENT_ID = "report-data"

# Significant digits of the sums of the coverage by radius per ring and
# sector, of which the page shows three.
REPORT_SUM_DIGITS = 7

# The calibration coefficients that the page needs for its profiles and
# its balanced radial field.
REPORT_CALIBRATION_NAMES = ("f", "cx", "cy", "b1", "b2", "k1", "k2", "k3", "k4", "p1", "p2", "p3", "p4")

# Vector fields of the page by the mode numbers of the viewer, Residuals
# apart (REPORT_RESIDUAL_STATISTICS).
REPORT_FIELD_MODES = ((0, "Total"), (1, "Radial"), (2, "Decentring"), (3, "Corrections"), (4, "Difference"))


def _Rounded(value, digits=REPORT_VECTOR_DIGITS):
    """*value* with *digits* significant digits, or None when it is not a
    finite number."""
    if value is None or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float("{:.{}g}".format(value, digits))


def _RoundedVectors(vectors):
    return [[_Rounded(component) for component in vector] for vector in vectors]


def ReportFileName(chunk, sensor):
    """Default name of the report of one interior orientation group."""
    from .interiorOrientationViewer import CsvFileNamePart, InteriorOrientationGroupName
    return "interiorOrientationReport_{}_{}.html".format(CsvFileNamePart(getattr(chunk, "label", "")),
                                                         CsvFileNamePart(InteriorOrientationGroupName(sensor, chunk)))


def NormalisedPointsOfTotal(calibration, pixelColumnCount, pixelRowCount, gridTotal):
    """The undistorted normalised point (x, y) of every vector of the
    Total, from its grid point and displacement: the ideal position is the
    grid point minus the Total, and NormalisedToPixelOffset maps (x, y) to
    its offset from the principal point."""
    from .interiorOrientationViewer import _Coefficient
    principalDistancePixels = calibration.f
    affinity, nonOrthogonality = _Coefficient(calibration, "b1"), _Coefficient(calibration, "b2")
    principalPointX = pixelColumnCount * 0.5 + _Coefficient(calibration, "cx")
    principalPointY = pixelRowCount * 0.5 + _Coefficient(calibration, "cy")
    points = []
    for gridX, gridY, totalX, totalY in gridTotal:
        offsetX, offsetY = gridX - totalX - principalPointX, gridY - totalY - principalPointY
        normalisedY = offsetY / principalDistancePixels
        normalisedX = (offsetX - normalisedY * nonOrthogonality) / (principalDistancePixels + affinity)
        points.append([_Rounded(normalisedX, 12), _Rounded(normalisedY, 12)])
    return points


def ReportData(chunk, sensor, parameterTable, plotState, documentPath=None, created=None):
    """The data of the report of one interior orientation group, as a
    dictionary that json can write: the project, the group and its
    calibration, the parameter table (None without a covariance), the
    vector fields of the plots of the tab (*plotState*), the residuals
    with all three statistics (collected now when they are not yet, as for
    Export CSV), the undistorted normalised points of the grid for the
    balanced radial field, the semi-diagonal angles, the maximum of the
    radial distortion, the notes on the rays, and the settings of the
    options."""
    from . import _METAFOCUS_VERSION
    from .interiorOrientationViewer import (
        AUTOMATIC_MAGNIFICATION_CONSTANTS, DISTORTION_CAMERA_MODELS, CalibrationModel, DistortionGridShape,
        FormatCalibrationValue, FormatPValue, FormatSignificant, GetPhotositePitchHeightMillimetres,
        GetPhotositePitchMillimetres, GetSensorCalibration,
        InteriorOrientationGroupName, LOW_TEST_STATISTIC_LIMIT, PRINCIPAL_DISTANCE_PARAMETER_NAME,
        RadialDistortionMaximum, SemiDiagonalAnglesDegrees,
        _Coefficient, _DisplayParameterNameRichText)
    calibration = plotState.calibration if plotState is not None else GetSensorCalibration(sensor)
    pixelColumnCount = int(getattr(calibration, "width", 0) or 0)
    pixelRowCount = int(getattr(calibration, "height", 0) or 0)
    if plotState is not None:
        pixelColumnCount, pixelRowCount = plotState.pixelColumnCount, plotState.pixelRowCount
    columns, rows = DistortionGridShape(pixelColumnCount, pixelRowCount) if pixelColumnCount and pixelRowCount \
        else (0, 0)
    data = {
        "formatVersion": REPORT_FORMAT_VERSION,
        "metafocusVersion": _METAFOCUS_VERSION,
        "created": created or datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "project": documentPath or "",
        "chunk": getattr(chunk, "label", "") or "",
        "group": InteriorOrientationGroupName(sensor, chunk),
        "cameraModel": CalibrationModel(calibration),
        # Spherical, cylindrical, and RPC groups have the residuals only.
        "hasDistortionModel": bool(plotState.hasDistortionModel) if plotState is not None
        else CalibrationModel(calibration) in DISTORTION_CAMERA_MODELS,
        "pixelColumnCount": pixelColumnCount,
        "pixelRowCount": pixelRowCount,
        "photositePitchMillimetres": _Rounded(GetPhotositePitchMillimetres(sensor), 12),
        # c and cy are converted with the vertical pitch, as in the viewer
        # (BuildUnitsText).
        "photositePitchHeightMillimetres": _Rounded(GetPhotositePitchHeightMillimetres(sensor), 12),
        "focalLengthMillimetres": _Rounded(getattr(sensor, "focal_length", None), 12),
        "calibration": {name: _Rounded(_Coefficient(calibration, name), 17) for name in REPORT_CALIBRATION_NAMES},
        "grid": {"columns": columns, "rows": rows},
        "semiDiagonalAngles": list(SemiDiagonalAnglesDegrees(pixelColumnCount, pixelRowCount))
        if pixelColumnCount and pixelRowCount else [],
        "constants": dict(AUTOMATIC_MAGNIFICATION_CONSTANTS),
        "parameters": None,
        "fields": {},
        "fieldDefinitions": {},
        "normalisedPoints": [],
        "residuals": None,
        "radialMaximum": None,
        "notes": [],
        "settings": {},
    }
    data["calibration"]["f"] = _Rounded(getattr(calibration, "f", 0.0) or 0.0, 17)
    if parameterTable is not None:
        parameterRows = []
        for index, parameterName in enumerate(parameterTable.parameterNames):
            estimate = parameterTable.estimates[index]
            standardError = parameterTable.standardErrors[index]
            testStatistic = parameterTable.testStatistics[index]
            pValue = parameterTable.pValues[index]
            isPrincipalDistance = parameterName == PRINCIPAL_DISTANCE_PARAMETER_NAME
            parameterRows.append({
                "name": parameterName,
                "displayName": _DisplayParameterNameRichText(parameterName),
                "estimate": FormatCalibrationValue(estimate),
                "standardError": FormatCalibrationValue(standardError),
                "testStatistic": FormatSignificant(testStatistic),
                "pValue": "–" if isPrincipalDistance else FormatPValue(pValue),
                "emphasised": bool(not isPrincipalDistance and isinstance(testStatistic, float)
                                   and math.isfinite(testStatistic)
                                   and abs(testStatistic) < LOW_TEST_STATISTIC_LIMIT),
            })
        data["parameters"] = {
            "rows": parameterRows,
            "correlations": [[_Rounded(value, 6) for value in row] for row in parameterTable.correlationMatrix],
            "degreesOfFreedomTiePoints": parameterTable.degreesOfFreedomTiePoints,
            "degreesOfFreedomAllObservations": parameterTable.degreesOfFreedomAllObservations,
            # The disabled images that the counts leave out.
            "degreesOfFreedomNote": getattr(parameterTable, "degreesOfFreedomNote", ""),
        }
    if plotState is not None and plotState.hasDistortionModel:
        for modeId, name in REPORT_FIELD_MODES:
            if modeId == 1:
                vectors = plotState.gaussianRadial
            else:
                vectors = plotState.vectorDataByMode.Get(modeId, ([],))[0]
            data["fields"][name] = _RoundedVectors(vectors)
            data["fieldDefinitions"][name] = plotState.Definition(modeId) if modeId != 1 else \
                "Gaussian, the terms of k1 to k4 (Metashape)"
        totalVectors = plotState.vectorDataByMode.Get(0, ([],))[0]
        data["normalisedPoints"] = NormalisedPointsOfTotal(calibration, pixelColumnCount, pixelRowCount,
                                                           totalVectors)
        maximum = RadialDistortionMaximum(calibration, pixelColumnCount, pixelRowCount)
        if maximum is not None:
            data["radialMaximum"] = {
                "undistortedRadiusPixels": _Rounded(maximum.undistortedRadius * calibration.f, 9),
                "distortedRadiusPixels": _Rounded(maximum.distortedRadiusPixels, 9),
                "cornersBeyond": maximum.cornersBeyond}
        data["notes"] = list(getattr(plotState, "rayLines", []))
        data["settings"] = {
            "radialConvention": plotState.radialConvention,
            "zeroCrossingPercent": plotState.balancedZeroCrossingPercent,
            # As the CSV and the Console name it (SettingsLines).
            "difference": plotState.SettingsLines()[0][len("Difference: "):],
            "residualStatistic": plotState.ResidualField().statistic,
            # The direction of the coverage by radius in the viewer, None for
            # all directions.
            "coverageDirectionDegrees": plotState.sharedPlotModes.get("coverageDirectionDegrees"),
        }
    if plotState is not None:
        try:
            data["residuals"] = ResidualReportData(plotState.ResidualField())
        except Exception as collectionError:
            from .photogrammetryUtilities import ReportSuppressedError
            ReportSuppressedError("interiorOrientationReport.ReportData.residuals", collectionError)
    return data


def ResidualReportData(residualField):
    """The residuals of the page from *residualField* (ResidualField of
    the plot state): the cell values of every statistic
    (RESIDUAL_STATISTIC_CHOICES), the number of individual residuals
    (individualCount) and their RMS, the number of those without a
    positive keypoint size, which the weighted cell means leave out
    (withoutKeypointSizeCount), the number in the weighted cell means
    (residualCount), and the coverage (CoverageReportData)."""
    from .interiorOrientationViewer import RESIDUAL_STATISTIC_CHOICES
    residualCells = residualField.Collect()
    return {
        "statistics": [[statistic, choiceText, _RoundedVectors(residualField.CellValues(statistic))]
                       for statistic, choiceText, _ in RESIDUAL_STATISTIC_CHOICES],
        "ringStatisticWords": RingStatisticWordsByStatistic(),
        "individualRms": _Rounded(residualCells.IndividualRms(), 9),
        "individualCount": residualCells.residualCount,
        "withoutKeypointSizeCount": residualCells.withoutKeypointSizeCount,
        "residualCount": residualCells.CellMeanResidualCount(),
        "coverage": CoverageReportData(residualCells, residualField.samplingStride),
    }


def RingStatisticWordsByStatistic():
    """The words of every statistic of the option Residuals for a ring
    (RingStatisticWords), by statistic, for the note of the page."""
    from .interiorOrientationViewer import RESIDUAL_STATISTIC_CHOICES, RingStatisticWords
    return {statistic: RingStatisticWords(statistic) for statistic, _, _ in RESIDUAL_STATISTIC_CHOICES}


def CoverageReportData(residualCells, samplingStride):
    """The coverage of the page: the 2D tie points per cell of the
    Coverage mode and the sampling of a large group, and for
    a frame or fisheye group the coverage by radius (CoverageRingsReportData),
    from which the page sums the rings of every direction as the viewer
    does."""
    counts, columns, rows = residualCells.CoverageCells()
    return {"cells": {"counts": counts, "columns": columns, "rows": rows},
            "samplingStride": samplingStride,
            "withoutRadiusCount": residualCells.withoutRadiusCount,
            "unconvergedRadiusCount": residualCells.unconvergedRadiusCount,
            "rings": CoverageRingsReportData(residualCells) if residualCells.radialMap is not None else None}


def CoverageRingsReportData(residualCells):
    """The coverage by radius per ring and sector of one degree
    (ResidualCellAccumulator): for every ring that has 2D tie points or a
    part of the image format, its index and a list of [sector, 2D tie
    points, sum of their radial components, the sum of these weighted by
    the inverse keypoint size, the sum of these weights, part of the image
    format] of the sectors that have either.  The page sums them over all
    sectors or over the wedge of a direction.  A median cannot be summed,
    so the medians of the radial components of every ring with at least
    COVERAGE_MINIMUM_RESIDUAL_COUNT 2D tie points are given for all
    directions and for each of the 360 directions (WedgeMedians)."""
    from .interiorOrientationViewer import (
        COVERAGE_MINIMUM_RESIDUAL_COUNT, DIRECTION_HALF_WIDTH_DEGREES, SectorKey)
    ringIndices = sorted({key // 360 for key in residualCells.sectorCounts}
                         | {key // 360 for key in residualCells.formatParts})
    sectorsByRing = []
    for ring in ringIndices:
        entries = []
        for sector in range(360):
            key = SectorKey(ring, sector)
            pointCount = residualCells.sectorCounts.get(key, 0)
            formatPart = residualCells.formatParts.get(key, 0.0)
            if pointCount <= 0 and not formatPart > 0.0:
                continue
            entries.append([sector, pointCount, _Rounded(residualCells.sectorSums.get(key, 0.0), REPORT_SUM_DIGITS),
                            _Rounded(residualCells.sectorWeightedSums.get(key, 0.0), REPORT_SUM_DIGITS),
                            _Rounded(residualCells.sectorWeightSums.get(key, 0.0), REPORT_SUM_DIGITS),
                            _Rounded(formatPart, REPORT_SUM_DIGITS)])
        sectorsByRing.append(entries)
    allDirections, byDirection = WedgeMedians(residualCells, ringIndices)
    return {"ringWidth": _Rounded(residualCells.ringWidth),
            "minimumResidualCount": COVERAGE_MINIMUM_RESIDUAL_COUNT,
            "directionHalfWidthDegrees": DIRECTION_HALF_WIDTH_DEGREES,
            "formatTotal": _Rounded(residualCells.formatTotal),
            "principalPoint": [_Rounded(residualCells.radialMap.principalPointX),
                               _Rounded(residualCells.radialMap.principalPointY)],
            "indices": ringIndices,
            "sectors": sectorsByRing,
            "medians": {"all": allDirections, "byDirection": byDirection}}


def WedgeMedians(residualCells, ringIndices):
    """(medians in all directions, [medians in the wedge of each direction
    from 0 to 359 degrees]): the median of the radial components of each
    ring of *ringIndices*, None for a ring with fewer than
    COVERAGE_MINIMUM_RESIDUAL_COUNT 2D tie points there.  The components of
    each sector are sorted once, and the wedge of a direction, the sum of
    30 sectors, merged and sorted from them."""
    import array
    import statistics
    from .interiorOrientationViewer import COVERAGE_MINIMUM_RESIDUAL_COUNT, SectorKey, WedgeSectors
    sortedBySector = {}
    for key, components in residualCells.sectorComponents.items():
        sortedBySector[key] = array.array("d", sorted(components))
    allDirections, byDirection = [], [[] for _ in range(360)]
    for ring in ringIndices:
        ringValues = array.array("d")
        for sector in range(360):
            ringValues.extend(sortedBySector.get(SectorKey(ring, sector), ()))
        allDirections.append(_Rounded(statistics.median(ringValues))
                             if len(ringValues) >= COVERAGE_MINIMUM_RESIDUAL_COUNT else None)
        for directionDegrees in range(360):
            wedgeValues = array.array("d")
            for sector in WedgeSectors(directionDegrees):
                wedgeValues.extend(sortedBySector.get(SectorKey(ring, sector), ()))
            byDirection[directionDegrees].append(_Rounded(statistics.median(wedgeValues))
                                                 if len(wedgeValues) >= COVERAGE_MINIMUM_RESIDUAL_COUNT else None)
    return allDirections, byDirection


def ReportHtml(data):
    """The page of the report: REPORT_PAGE_TEMPLATE with its title and the
    data embedded as JSON, with "</" written as "<\\/" so that no text of
    the data can end the script element."""
    title = "Interior orientation report: {}".format(data.get("group", ""))
    payload = json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(",", ":")).replace("</", "<\\/")
    return (REPORT_PAGE_TEMPLATE.replace("%%TITLE%%", html.escape(title))
            .replace("%%DATA_ELEMENT_ID%%", REPORT_DATA_ELEMENT_ID)
            .replace("%%DATA%%", payload))


def WriteReport(path, data):
    """Write the page of *data* to *path* (UTF-8).  The page is made first
    and written to a new temporary file next to *path*, which then
    replaces *path* (os.replace), so that a failure leaves an earlier file
    at *path* as it was.  The temporary file of a failed write is
    removed."""
    pageHtml = ReportHtml(data)
    fileDescriptor, temporaryPath = tempfile.mkstemp(
        dir=os.path.dirname(os.path.abspath(path)), prefix=os.path.basename(path) + ".", suffix=".tmp")
    try:
        with os.fdopen(fileDescriptor, "w", encoding="utf-8", newline="\n") as reportFile:
            reportFile.write(pageHtml)
        os.replace(temporaryPath, path)
    except BaseException:
        RemoveTemporaryReport(temporaryPath)
        raise


def RemoveTemporaryReport(temporaryPath):
    """Remove the temporary file of a failed WriteReport, when it is still
    there (a failure is reported once in the Console)."""
    try:
        if os.path.lexists(temporaryPath):
            os.remove(temporaryPath)
    except OSError as suppressedError:
        ReportSuppressedError("interiorOrientationReport.RemoveTemporaryReport", suppressedError)


REPORT_PAGE_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>%%TITLE%%</title>
<style>
:root { --ink: #1d1d1f; --muted: #5f6368; --rule: #d0d0d0; --panel: #f6f6f6; --accent: #0b5cad; }
* { box-sizing: border-box; }
body { margin: 0; background: #ffffff; color: var(--ink); font: 15px/1.5 "Segoe UI", Arial, "Liberation Sans", sans-serif; }
main { max-width: 1080px; margin: 0 auto; padding: 24px 16px 48px; }
header.report { border-bottom: 2px solid var(--ink); margin-bottom: 8px; }
header.report h1 { font-size: 26px; margin: 0 0 4px; letter-spacing: 0.02em; }
header.report p { margin: 0 0 10px; color: var(--muted); }
section { margin-top: 28px; }
h2 { font-size: 19px; border-bottom: 1px solid var(--rule); padding-bottom: 4px; margin: 0 0 12px; }
h3 { font-size: 15px; margin: 18px 0 6px; }
table { border-collapse: collapse; font-variant-numeric: tabular-nums; }
th, td { border: 1px solid var(--rule); padding: 3px 8px; text-align: right; vertical-align: top; }
th { background: var(--panel); font-weight: 600; }
td.text, th.text { text-align: left; }
table.details td, table.details th { text-align: left; }
td.emphasised { font-weight: 700; }
.scroll { overflow-x: auto; max-width: 100%; }
.controls { display: flex; flex-wrap: wrap; gap: 8px 18px; align-items: center; background: var(--panel);
            border: 1px solid var(--rule); border-radius: 6px; padding: 8px 12px; margin: 8px 0 12px; }
.controls label { white-space: nowrap; }
.controls input[type=range] { width: 180px; vertical-align: middle; }
.controls input[type=number] { width: 64px; }
.settings { display: none; color: var(--muted); }
.note { color: var(--muted); }
.equations { font-family: "Cambria Math", "Times New Roman", serif; background: var(--panel); border: 1px solid var(--rule);
             border-radius: 6px; padding: 8px 14px; line-height: 1.8; }
svg { display: block; max-width: 100%; height: auto; background: #ffffff; }
svg text { font-family: Arial, "Liberation Sans", sans-serif; }
.plots { display: grid; grid-template-columns: 1fr; gap: 16px; }
footer { margin-top: 32px; border-top: 1px solid var(--rule); padding-top: 8px; color: var(--muted); font-size: 13px; }
#read-out { position: fixed; z-index: 10; display: none; pointer-events: none; white-space: pre; max-width: 90vw;
            background: #fffff0; color: var(--ink); border: 1px solid #8a8a8a; border-radius: 4px; padding: 4px 8px;
            font: 13px/1.45 Arial, "Liberation Sans", sans-serif; box-shadow: 0 1px 4px rgba(0, 0, 0, 0.25); }
@page { size: A4 portrait; margin: 14mm; }
@media print {
  body { font-size: 10pt; }
  main { max-width: none; padding: 0; }
  .controls { display: none; }
  #read-out { display: none !important; }
  .settings { display: block; }
  section { break-before: page; page-break-before: always; margin-top: 0; }
  section.first { break-before: auto; page-break-before: auto; }
  .scroll { overflow: visible; }
  a { color: inherit; text-decoration: none; }
}
</style>
</head>
<body>
<main>
<header class="report">
<h1>Interior orientation report</h1>
<p id="subtitle"></p>
</header>

<section class="first" id="project">
<h2>Project and interior orientation group</h2>
<table class="details" id="details"></table>
</section>

<section id="parameters">
<h2>Parameters</h2>
<div id="parameter-content"></div>
</section>

<section id="model">
<h2>Camera model</h2>
<div id="model-content"></div>
</section>

<section id="fields">
<h2>Vector fields</h2>
<div class="controls" id="field-controls">
<span id="mode-buttons"></span>
<label>Vector scale <input type="range" id="magnification" min="0" max="100" value="50" step="1"></label>
<span id="magnification-label"></span>
<label>Residuals <select id="residual-statistic"></select></label>
</div>
<p class="settings" id="field-settings"></p>
<div id="field-plot"></div>
<h3>RMS and maximum of every vector field</h3>
<div class="scroll"><table id="field-table"></table></div>
<p class="note">Each vector starts at its grid point, the centre of a cell of the grid of the viewer, which
follows the Distortion Plot of Metashape, and its colour runs from blue to red at twice the median length. RMS and maximum have three significant
digits, as Metashape writes them. Pointing at a plot shows the values under the pointer.</p>
</section>

<section id="profiles">
<h2>Distortion profiles</h2>
<div class="controls" id="profile-controls">
<label>Radial distortion <select id="radial-convention">
<option value="gaussian">Gaussian (Metashape)</option>
<option value="balanced">Balanced</option></select></label>
<label id="zero-percent-label">zero at <input type="number" id="zero-percent" min="10" max="100" step="1" value="70"> % of the largest radius</label>
<label><input type="checkbox" id="angle-magnitude"> Magnitude at the angle</label>
<label id="decentring-angle-label">Decentring angle <input type="number" id="decentring-angle" min="0" max="359" step="1" value="0"></label>
<label><input type="checkbox" id="semi-diagonal-mean"> Semi-diagonal mean</label>
<label><input type="checkbox" id="azimuth-mean"> Azimuth mean</label>
</div>
<div id="profile-body">
<p class="settings" id="profile-settings"></p>
<div class="plots">
<div id="radial-plot"></div>
<div id="decentring-plot"></div>
</div>
<div id="coverage-body">
<h3>Coverage by radius</h3>
<div class="controls" id="direction-controls">
<span>Direction: <span id="direction-value">all directions</span></span>
<button type="button" id="all-directions">All directions</button>
<span class="note">Click in the Coverage map of the vector fields to choose a direction.</span>
</div>
<p class="settings" id="coverage-settings"></p>
<div class="plots">
<div id="density-plot"></div>
<div id="ring-residual-plot"></div>
</div>
<p class="note" id="coverage-note"></p>
</div>
<h3>Values of the profiles</h3>
<div class="scroll"><table id="profile-table"></table></div>
<p class="note" id="profile-note"></p>
</div>
</section>

<section id="notes">
<h2>Notes</h2>
<div id="notes-content"></div>
</section>

<footer id="footer"></footer>
</main>
<div id="read-out" role="tooltip"></div>

<script id="%%DATA_ELEMENT_ID%%" type="application/json">%%DATA%%</script>
<script>
"use strict";
var MetaFOCUSReport = (function () {
  // The modes of the vector field and the Coverage mode, as in the viewer.
  var MODE_NAMES = ["Total", "Radial", "Decentring", "Corrections", "Difference", "Residuals", "Coverage"];

  function radialTerm(cal, radiusSquared) {
    return radiusSquared * (cal.k1 + radiusSquared * (cal.k2 + radiusSquared * (cal.k3 + radiusSquared * cal.k4)));
  }

  function toPixels(cal, normalisedX, normalisedY) {
    return [normalisedX * (cal.f + cal.b1) + normalisedY * cal.b2, normalisedY * cal.f];
  }

  function largestProfileRadius(columns, rows) {
    return Math.hypot(0.5 * columns, 0.5 * rows);
  }

  function zeroCrossingRadius(columns, rows, percent) {
    return percent / 100 * largestProfileRadius(columns, rows);
  }

  function balancedOffset(cal, zeroRadiusPixels) {
    var normalised = zeroRadiusPixels / cal.f;
    return radialTerm(cal, normalised * normalised);
  }

  function balancedPrincipalDistance(cal, zeroRadiusPixels) {
    return cal.f * (1 + balancedOffset(cal, zeroRadiusPixels));
  }

  function decentringMagnitudeAt(cal, higherOrderFactor, radiusSquared, normalisedRadius, angleDegrees) {
    var angle = angleDegrees * Math.PI / 180;
    var x = normalisedRadius * Math.cos(angle), y = normalisedRadius * Math.sin(angle);
    var dx = higherOrderFactor * (cal.p1 * (radiusSquared + 2 * x * x) + 2 * cal.p2 * x * y);
    var dy = higherOrderFactor * (2 * cal.p1 * x * y + cal.p2 * (radiusSquared + 2 * y * y));
    return cal.f * Math.hypot(dx, dy);
  }

  function profiles(data, convention, zeroPercent, angleDegrees, sampleCount) {
    var cal = data.calibration, columns = data.pixelColumnCount, rows = data.pixelRowCount;
    var maximumRadius = largestProfileRadius(columns, rows);
    sampleCount = sampleCount || 200;
    var zero = [0, 0, 0, 0];
    if (convention === "balanced") {
      var zeroRadius = zeroCrossingRadius(columns, rows, zeroPercent) / cal.f, s = zeroRadius * zeroRadius;
      zero = [cal.k1 * s, cal.k2 * s * s, cal.k3 * s * s * s, cal.k4 * s * s * s * s];
    }
    var result = {radii: [], totalRadial: [], k1: [], k2: [], k3: [], k4: [], totalDecentring: [], p1: [], p2: [],
                  angleMagnitude: [], semiDiagonalMean: [], azimuthMean: []};
    for (var index = 0; index <= sampleCount; index++) {
      var radius = index / sampleCount * maximumRadius;
      var normalisedRadius = radius / cal.f, r2 = normalisedRadius * normalisedRadius;
      var r4 = r2 * r2, r6 = r2 * r4, r8 = r4 * r4;
      var t1 = cal.k1 * r2 - zero[0], t2 = cal.k2 * r4 - zero[1], t3 = cal.k3 * r6 - zero[2], t4 = cal.k4 * r8 - zero[3];
      result.radii.push(radius);
      result.totalRadial.push(-radius * (t1 + t2 + t3 + t4));
      result.k1.push(-radius * t1);
      result.k2.push(-radius * t2);
      result.k3.push(-radius * t3);
      result.k4.push(-radius * t4);
      var higherOrderFactor = 1 + cal.p3 * r2 + cal.p4 * r4;
      var profileScale = cal.f * r2 * Math.abs(higherOrderFactor);
      result.totalDecentring.push(profileScale * Math.hypot(cal.p1, cal.p2));
      result.p1.push(profileScale * Math.abs(cal.p1));
      result.p2.push(profileScale * Math.abs(cal.p2));
      result.angleMagnitude.push(decentringMagnitudeAt(cal, higherOrderFactor, r2, normalisedRadius, angleDegrees));
      var sum = 0;
      for (var a = 0; a < data.semiDiagonalAngles.length; a++) {
        sum += decentringMagnitudeAt(cal, higherOrderFactor, r2, normalisedRadius, data.semiDiagonalAngles[a]);
      }
      result.semiDiagonalMean.push(data.semiDiagonalAngles.length ? sum / data.semiDiagonalAngles.length : 0);
      var azimuthSum = 0;
      for (var azimuth = 0; azimuth < 360; azimuth += 3) {
        azimuthSum += decentringMagnitudeAt(cal, higherOrderFactor, r2, normalisedRadius, azimuth);
      }
      result.azimuthMean.push(azimuthSum / 120);
    }
    return result;
  }

  function radialField(data, convention, zeroPercent) {
    var gaussian = data.fields.Radial || [];
    if (convention !== "balanced") { return gaussian; }
    var cal = data.calibration;
    var offset = balancedOffset(cal, zeroCrossingRadius(data.pixelColumnCount, data.pixelRowCount, zeroPercent));
    return gaussian.map(function (vector, index) {
      var point = data.normalisedPoints[index];
      var linear = toPixels(cal, point[0] * offset, point[1] * offset);
      return [vector[0], vector[1], vector[2] - linear[0], vector[3] - linear[1]];
    });
  }

  function residualField(data, statistic) {
    if (!data.residuals) { return []; }
    for (var index = 0; index < data.residuals.statistics.length; index++) {
      if (data.residuals.statistics[index][0] === statistic) { return data.residuals.statistics[index][2]; }
    }
    return data.residuals.statistics[0][2];
  }

  function fieldStatistics(vectors) {
    if (!vectors.length) { return [null, null]; }
    var sum = 0, largest = 0;
    for (var index = 0; index < vectors.length; index++) {
      var squared = vectors[index][2] * vectors[index][2] + vectors[index][3] * vectors[index][3];
      sum += squared;
      if (squared > largest) { largest = squared; }
    }
    return [Math.sqrt(sum / vectors.length), Math.sqrt(largest)];
  }

  function stripZeros(text) {
    if (text.indexOf(".") < 0) { return text; }
    return text.replace(/0+$/, "").replace(/\.$/, "");
  }

  // Python's format rounds a number that lies exactly halfway between two
  // candidates to the even one, whereas toFixed and toExponential take the
  // larger.  The page rounds as Python does, so that the read-outs under the
  // pointer give the texts of the viewer, also for an exact tie such as 0.125
  // with two decimals.  isDecimalTie tells whether *value* lies exactly halfway
  // between two multiples of 10^power: whether 2 |value| / 10^power is an
  // odd integer, that is |value| = m 2^(power - 1) with m odd and, for a
  // positive power, divisible by 5^power.
  function isDecimalTie(value, power) {
    var scaled = Math.abs(value), exponent = 0;
    if (!(scaled > 0) || !isFinite(scaled)) { return false; }
    while (!Number.isInteger(scaled)) { scaled *= 2; exponent -= 1; }
    while (scaled % 2 === 0) { scaled /= 2; exponent += 1; }
    if (exponent !== power - 1) { return false; }
    for (var index = 0; index < power; index++) {
      if (scaled % 5 !== 0) { return false; }
      scaled /= 5;
    }
    return true;
  }

  // The next smaller number of a positive, finite *value*.
  function nextDown(value) {
    var view = new DataView(new ArrayBuffer(8));
    view.setFloat64(0, value);
    var low = view.getUint32(4);
    if (low === 0) { view.setUint32(0, view.getUint32(0) - 1); view.setUint32(4, 0xFFFFFFFF); }
    else { view.setUint32(4, low - 1); }
    return view.getFloat64(0);
  }

  function lastDigitIsEven(text) {
    var digits = text.split("e")[0];
    return Number(digits.charAt(digits.length - 1)) % 2 === 0;
  }

  // The positive *magnitude* with *decimals* decimals, as Python writes it.
  function fixedMagnitude(magnitude, decimals) {
    var text = magnitude.toFixed(decimals);
    if (isDecimalTie(magnitude, -decimals)) {
      var lower = nextDown(magnitude).toFixed(decimals);
      if (lastDigitIsEven(lower)) { text = lower; }
    }
    return text;
  }

  // The positive *magnitude* in exponent notation with *fractionDigits*
  // digits after the point, as Python writes it.
  function exponentialMagnitude(magnitude, fractionDigits) {
    var text = magnitude.toExponential(fractionDigits);
    var exponent = parseInt(magnitude.toExponential(20).split("e")[1], 10);
    if (isDecimalTie(magnitude, exponent - fractionDigits)) {
      var lower = nextDown(magnitude).toExponential(fractionDigits);
      if (lastDigitIsEven(lower)) { text = lower; }
    }
    return text;
  }

  // A number with *decimals* decimals, with commas between the thousands
  // when *grouped*: Python's "{:,.1f}" and "{:.2f}".
  function formatFixed(value, decimals, grouped) {
    var parts = fixedMagnitude(Math.abs(value), decimals).split(".");
    if (grouped) { parts[0] = parts[0].replace(/\B(?=(\d{3})+(?!\d))/g, ","); }
    return (value < 0 || Object.is(value, -0) ? "-" : "") + parts.join(".");
  }

  function formatG(value, digits) {
    if (value === null || value === undefined || !isFinite(value)) { return "n/a"; }
    if (value === 0) { return "0"; }
    var sign = value < 0 ? "-" : "";
    var exponential = exponentialMagnitude(Math.abs(value), digits - 1);
    var exponent = parseInt(exponential.split("e")[1], 10);
    if (exponent < -4 || exponent >= digits) {
      var parts = exponential.split("e");
      var exponentDigits = String(Math.abs(exponent));
      if (exponentDigits.length < 2) { exponentDigits = "0" + exponentDigits; }
      return sign + stripZeros(parts[0]) + "e" + (exponent < 0 ? "-" : "+") + exponentDigits;
    }
    return sign + stripZeros(fixedMagnitude(Math.abs(value), Math.max(0, digits - 1 - exponent)));
  }

  // FormatAsMetashape of the viewer: three significant digits, and "-0" for
  // a negative zero, as Python writes it.
  function formatAsMetashape(value) {
    return Object.is(value, -0) ? "-0" : formatG(value, 3);
  }

  function countWithNoun(count, singular, plural) {
    return formatFixed(count, 0, true) + " " + (count === 1 ? singular : plural);
  }

  // The read-outs under the pointer (Geert, 15 October 2026: "add the
  // values under the pointer to the page, as in the viewer"), with the
  // texts of VectorReadOutText, CoverageCellReadOutText,
  // ProfileReadOutText, and CoverageReadOutText of the viewer, and the names
  // that the page gives its plots and curves (the titles of its vector
  // fields and the legends of its profiles).  A vector is
  // read out within this share of the width of a grid cell from its grid
  // point (VECTOR_READ_OUT_CELL_FRACTION of the viewer).
  var VECTOR_READ_OUT_CELL_FRACTION = 0.75;

  function vectorReadOutText(vectors, title, columns, rows, gridColumns, imageX, imageY) {
    if (!vectors || !vectors.length || !columns || !rows) { return ""; }
    if (!(imageX >= 0 && imageX <= columns && imageY >= 0 && imageY <= rows)) { return ""; }
    var nearest = null, nearestDistance = null;
    for (var index = 0; index < vectors.length; index++) {
      var distance = Math.hypot(vectors[index][0] - imageX, vectors[index][1] - imageY);
      if (nearestDistance === null || distance < nearestDistance) { nearest = vectors[index]; nearestDistance = distance; }
    }
    if (nearest === null || nearestDistance > VECTOR_READ_OUT_CELL_FRACTION * (columns / gridColumns)) { return ""; }
    return title + "\nGrid point at x " + formatFixed(nearest[0], 1, true) + " px, y " + formatFixed(nearest[1], 1, true) +
      " px\nVector: " + formatAsMetashape(nearest[2]) + " px along x, " + formatAsMetashape(nearest[3]) +
      " px along y, length " + formatAsMetashape(Math.hypot(nearest[2], nearest[3])) + " px";
  }

  function coverageCellReadOutText(cells, title, columns, rows, imageX, imageY) {
    if (!cells || !columns || !rows) { return ""; }
    if (!(imageX >= 0 && imageX < columns && imageY >= 0 && imageY < rows)) { return ""; }
    var column = Math.min(Math.floor(imageX / columns * cells.columns), cells.columns - 1);
    var row = Math.min(Math.floor(imageY / rows * cells.rows), cells.rows - 1);
    var cellWidth = columns / cells.columns, cellHeight = rows / cells.rows;
    var count = cells.counts[row * cells.columns + column];
    return title + "\nCell from x " + formatFixed(column * cellWidth, 1, true) + " to " +
      formatFixed((column + 1) * cellWidth, 1, true) + " px, y " + formatFixed(row * cellHeight, 1, true) + " to " +
      formatFixed((row + 1) * cellHeight, 1, true) + " px\n" +
      (count <= 0 ? "No 2D tie points" : countWithNoun(count, "2D tie point", "2D tie points"));
  }

  // A curve (*values* at the evenly spaced *radii*) at *radius*, linearly
  // interpolated, or null outside the radii (ProfileValueAt of the viewer).
  function profileValueAt(radii, values, radius) {
    var last = radii.length - 1;
    if (!radii.length || values.length !== radii.length || !(radii[0] <= radius && radius <= radii[last])) { return null; }
    if (radii.length === 1 || radii[last] <= radii[0]) { return values[0]; }
    var position = (radius - radii[0]) / (radii[last] - radii[0]) * last;
    var lowerIndex = Math.min(Math.floor(position), radii.length - 2);
    var fraction = position - lowerIndex;
    return values[lowerIndex] + fraction * (values[lowerIndex + 1] - values[lowerIndex]);
  }

  // *curves* holds [name, values] of every curve that the plot draws, named
  // as in its legend.
  function profileReadOutText(radii, curves, radius) {
    var lines = ["Radius " + formatFixed(radius, 0, true) + " px"];
    if (!radii.length || radius > radii[radii.length - 1] * (1 + 1e-9)) {
      lines.push("Beyond the largest radius of the profiles, " +
        formatFixed(radii.length ? radii[radii.length - 1] : 0, 0, true) + " px");
      return lines.join("\n");
    }
    curves.forEach(function (curve) {
      lines.push(curve[0] + ": " + formatG(profileValueAt(radii, curve[1], radius), 4) + " px");
    });
    return lines.join("\n");
  }

  function coverageRingAt(rings, radius) {
    for (var index = 0; index < rings.length; index++) {
      if (rings[index].start <= radius && radius < rings[index].end) { return rings[index]; }
    }
    return null;
  }

  function coverageReadOutText(rings, radius, residualWords, direction, halfWidth, samplingStride, minimumResidualCount) {
    var ring = coverageRingAt(rings || [], radius);
    var lines = direction === null || direction === undefined ? [] : ["Direction " + directionValueText(direction, halfWidth)];
    if (samplingStride > 1) { lines.push("2D tie points sampled 1 in " + formatFixed(samplingStride, 0, true)); }
    if (ring === null) {
      return lines.concat(["Radius " + formatFixed(radius, 0, true) + " px",
                           "No part of the image format and no 2D tie points"]).join("\n");
    }
    lines.push("Radius " + formatFixed(ring.start, 0, true) + " to " + formatFixed(ring.end, 0, true) + " px");
    if (ring.pointCount <= 0) {
      lines.push("No 2D tie points in this ring");
    } else {
      lines.push(countWithNoun(ring.pointCount, "2D tie point", "2D tie points") + " (" +
        formatFixed(100 * ring.pointShare, 2, false) + " % of all with an r)");
    }
    lines.push(ring.areaShare > 0 ? "Share of the image format where r is found: " +
      formatFixed(100 * ring.areaShare, 2, false) + " %" : "No part of the image format in this ring");
    if (ring.relativeDensity !== null && ring.relativeDensity !== undefined) {
      lines.push("Relative density: " + formatFixed(ring.relativeDensity, 2, false));
    }
    if (ring.radialResidual !== null && ring.radialResidual !== undefined) {
      lines.push("Radial residual, " + residualWords + ": " + formatG(ring.radialResidual, 3) + " px");
    } else if (ring.pointCount > 0 && ring.pointCount < minimumResidualCount) {
      lines.push("Radial residual: fewer than " + minimumResidualCount + " 2D tie points");
    } else if (ring.pointCount > 0) {
      lines.push("Radial residual: none, because no 2D tie point of the ring has a positive keypoint size");
    }
    return lines.join("\n");
  }

  function formatTotalMagnification(magnification) {
    if (magnification === null || !(isFinite(magnification) && magnification > 0)) { return "n/a"; }
    var decimals = 2 - Math.floor(Math.log10(magnification));
    if (decimals <= 0) {
      var step = Math.pow(10, -decimals);
      return "×" + (Math.round(magnification / step) * step).toLocaleString("en-GB", {maximumFractionDigits: 0});
    }
    return "×" + magnification.toFixed(decimals);
  }

  function automaticMagnification(vectors, data) {
    var lengths = [];
    for (var index = 0; index < vectors.length; index++) {
      var length = Math.hypot(vectors[index][2], vectors[index][3]);
      if (isFinite(length)) { lengths.push(length); }
    }
    if (!lengths.length) { return null; }
    lengths.sort(function (first, second) { return first - second; });
    var cellWidth = data.pixelColumnCount / data.grid.columns;
    var median = lengths[Math.floor(lengths.length / 2)];
    var negligible = data.constants.negligibleDisplacementPixels;
    if (median > negligible) { return data.constants.medianVectorCellFraction * cellWidth / median; }
    if (lengths[lengths.length - 1] > negligible) { return cellWidth / lengths[lengths.length - 1]; }
    return 1;
  }

  function userMagnification(sliderPosition, data) {
    return Math.exp((sliderPosition - 50) / 50 * Math.log(data.constants.maximumUserMagnification));
  }

  function medianLength(vectors) {
    var lengths = vectors.map(function (vector) { return Math.hypot(vector[2], vector[3]); })
      .filter(function (length) { return isFinite(length); }).sort(function (a, b) { return a - b; });
    return lengths.length ? lengths[Math.floor(lengths.length / 2)] : 0;
  }

  function vectorColour(length, referenceLength) {
    var fraction = referenceLength > 0 ? Math.min(Math.max(length / referenceLength, 0), 1) : 0;
    var hue = (1 - fraction) * 240 / 60, sector = Math.floor(hue) % 6, part = hue - Math.floor(hue);
    var values = [[1, part, 0], [1 - part, 1, 0], [0, 1, part], [0, 1 - part, 1], [part, 0, 1], [1, 0, 1 - part]][sector];
    if (hue >= 6) { values = [1, 0, 0]; }
    return "rgb(" + values.map(function (value) { return Math.round(255 * value); }).join(",") + ")";
  }

  function chooseReferenceVectorLength(screenPerImagePixel, targetScreenPixels) {
    if (!(screenPerImagePixel > 0 && isFinite(screenPerImagePixel))) { return null; }
    return referenceVectorLength(targetScreenPixels / screenPerImagePixel);
  }

  function referenceVectorLength(largestLength) {
    if (!(largestLength > 0)) { return null; }
    var exponent = Math.floor(Math.log10(largestLength));
    var mantissas = [5, 2, 1];
    for (var index = 0; index < mantissas.length; index++) {
      var length = mantissas[index] * Math.pow(10, exponent);
      if (length <= largestLength * (1 + 1e-9)) { return length; }
    }
    return Math.pow(10, exponent);
  }

  function chooseTickInterval(minimum, maximum, maximumCount) {
    var span = Math.abs(maximum - minimum);
    if (!isFinite(span) || span <= 0) { return 1; }
    var smallest = span / Math.max(1, maximumCount);
    var exponent = Math.floor(Math.log10(smallest));
    var mantissas = [1, 2, 5];
    for (var index = 0; index < mantissas.length; index++) {
      var interval = mantissas[index] * Math.pow(10, exponent);
      if (interval >= smallest * (1 - 1e-9)) { return interval; }
    }
    return Math.pow(10, exponent + 1);
  }

  // The coverage, as in the viewer: the colours of
  // the cells of the Coverage mode, the direction of a position from the
  // principal point, the border of the image format in a direction, and the
  // rings of the coverage by radius in all directions or in the wedge of a
  // direction, summed from their sectors of one degree.
  var COVERAGE_EMPTY_RGB = [240, 170, 170], COVERAGE_FEW_RGB = [236, 242, 250], COVERAGE_MANY_RGB = [20, 70, 160];
  var COVERAGE_COLOUR_QUANTILE = 0.95;

  // Python's round, which rounds halves to the even neighbour.
  function roundHalfEven(value) {
    var floor = Math.floor(value), difference = value - floor;
    if (difference > 0.5) { return floor + 1; }
    if (difference < 0.5) { return floor; }
    return floor % 2 === 0 ? floor : floor + 1;
  }

  function coverageColourReference(counts) {
    var filled = counts.filter(function (count) { return count > 0; }).sort(function (a, b) { return a - b; });
    if (!filled.length) { return 0; }
    return filled[Math.min(filled.length - 1, Math.floor(COVERAGE_COLOUR_QUANTILE * filled.length))];
  }

  function coverageCellRgb(count, reference) {
    if (count <= 0) { return COVERAGE_EMPTY_RGB.slice(); }
    var fraction = reference > 0 ? Math.min(count / reference, 1) : 1;
    return COVERAGE_FEW_RGB.map(function (few, index) { return roundHalfEven(few + fraction * (COVERAGE_MANY_RGB[index] - few)); });
  }

  function directionOfOffset(offsetX, offsetY) {
    if (Math.hypot(offsetX, offsetY) < 1e-9) { return null; }
    var degrees = Math.atan2(offsetY, offsetX) * 180 / Math.PI;
    return roundHalfEven(((degrees % 360) + 360) % 360) % 360;
  }

  function rayFormatSpan(startX, startY, directionX, directionY, columns, rows) {
    var entry = 0, exit = Infinity, axes = [[startX, directionX, columns], [startY, directionY, rows]];
    for (var index = 0; index < 2; index++) {
      var start = axes[index][0], direction = axes[index][1], limit = axes[index][2];
      if (Math.abs(direction) < 1e-15) {
        if (start < 0 || start > limit) { return null; }
        continue;
      }
      var near = -start / direction, far = (limit - start) / direction;
      if (near > far) { var swap = near; near = far; far = swap; }
      entry = Math.max(entry, near);
      exit = Math.min(exit, far);
    }
    return exit > entry ? [entry, exit] : null;
  }

  function borderPoint(principalPoint, angleDegrees, columns, rows) {
    var angle = angleDegrees * Math.PI / 180, directionX = Math.cos(angle), directionY = Math.sin(angle);
    var span = rayFormatSpan(principalPoint[0], principalPoint[1], directionX, directionY, columns, rows);
    return span === null ? null : [principalPoint[0] + span[1] * directionX, principalPoint[1] + span[1] * directionY];
  }

  function wedgeSectors(direction, halfWidth) {
    var sectors = [];
    for (var offset = -halfWidth; offset < halfWidth; offset++) { sectors.push(((direction + offset) % 360 + 360) % 360); }
    return sectors;
  }

  function coverageRings(data, statistic, direction) {
    var coverage = data.residuals && data.residuals.coverage && data.residuals.coverage.rings;
    if (!coverage) { return []; }
    var inWedge = null;
    if (direction !== null && direction !== undefined) {
      inWedge = {};
      wedgeSectors(direction, coverage.directionHalfWidthDegrees).forEach(function (sector) { inWedge[sector] = true; });
    }
    var pointTotal = 0;
    coverage.sectors.forEach(function (entries) { entries.forEach(function (entry) { pointTotal += entry[1]; }); });
    var medians = inWedge === null ? coverage.medians.all : coverage.medians.byDirection[direction];
    var rings = [];
    coverage.indices.forEach(function (ring, position) {
      var count = 0, sum = 0, weightedSum = 0, weightSum = 0, part = 0;
      coverage.sectors[position].forEach(function (entry) {
        if (inWedge !== null && !inWedge[entry[0]]) { return; }
        count += entry[1]; sum += entry[2]; weightedSum += entry[3]; weightSum += entry[4]; part += entry[5];
      });
      if (count <= 0 && !(part > 0)) { return; }
      var areaShare = coverage.formatTotal > 0 ? part / coverage.formatTotal : 0;
      var pointShare = pointTotal ? count / pointTotal : 0;
      var residual = null;
      if (count >= coverage.minimumResidualCount) {
        if (statistic === "mean") { residual = sum / count; }
        else if (statistic === "median") { residual = medians[position]; }
        else { residual = weightSum > 0 ? weightedSum / weightSum : null; }
      }
      rings.push({start: ring * coverage.ringWidth, end: (ring + 1) * coverage.ringWidth, pointCount: count,
                  pointShare: pointShare, areaShare: areaShare,
                  relativeDensity: areaShare > 0 ? pointShare / areaShare : null, radialResidual: residual});
    });
    return rings;
  }

  function directionValueText(direction, halfWidth) {
    return direction === null || direction === undefined ? "all directions" : direction + "° ± " + halfWidth + "°";
  }

  function directionTitleWords(direction) {
    return direction === null || direction === undefined ? "" : " at " + direction + "°";
  }

  // [tick interval, top of the axis] of the relative density, as
  // CoverageDensityAxis of the viewer: at least up to 1.5, with at most two
  // intervals.
  function densityAxis(densities) {
    var maximum = Math.max.apply(null, [1.5].concat(densities));
    var interval = chooseTickInterval(0, maximum, 2);
    return [interval, Math.ceil(maximum / interval) * interval];
  }

  return {MODE_NAMES: MODE_NAMES, radialTerm: radialTerm, toPixels: toPixels, profiles: profiles,
          coverageColourReference: coverageColourReference, coverageCellRgb: coverageCellRgb,
          directionOfOffset: directionOfOffset, borderPoint: borderPoint, wedgeSectors: wedgeSectors,
          coverageRings: coverageRings, roundHalfEven: roundHalfEven, directionValueText: directionValueText,
          directionTitleWords: directionTitleWords, densityAxis: densityAxis,
          radialField: radialField, residualField: residualField, fieldStatistics: fieldStatistics,
          formatG: formatG, formatTotalMagnification: formatTotalMagnification,
          automaticMagnification: automaticMagnification, userMagnification: userMagnification,
          medianLength: medianLength, vectorColour: vectorColour, referenceVectorLength: referenceVectorLength,
          chooseReferenceVectorLength: chooseReferenceVectorLength,
          chooseTickInterval: chooseTickInterval, zeroCrossingRadius: zeroCrossingRadius,
          largestProfileRadius: largestProfileRadius, balancedPrincipalDistance: balancedPrincipalDistance,
          decentringMagnitudeAt: decentringMagnitudeAt, formatFixed: formatFixed, formatAsMetashape: formatAsMetashape,
          vectorReadOutText: vectorReadOutText, coverageCellReadOutText: coverageCellReadOutText,
          profileValueAt: profileValueAt, profileReadOutText: profileReadOutText,
          coverageReadOutText: coverageReadOutText};
})();

(function () {
  if (typeof document === "undefined") { return; }
  var R = MetaFOCUSReport;
  var SVG_NS = "http://www.w3.org/2000/svg";
  var data = JSON.parse(document.getElementById("%%DATA_ELEMENT_ID%%").textContent);
  var coverageData = data.residuals && data.residuals.coverage ? data.residuals.coverage : null;
  var ringData = coverageData && coverageData.rings ? coverageData.rings : null;
  var initialDirection = data.settings && typeof data.settings.coverageDirectionDegrees === "number" ?
    data.settings.coverageDirectionDegrees : null;
  var state = {mode: 0, slider: 50, residualStatistic: (data.settings && data.settings.residualStatistic) || "",
               convention: (data.settings && data.settings.radialConvention) || "gaussian",
               zeroPercent: (data.settings && data.settings.zeroCrossingPercent) || 70,
               angle: 0, showAngle: false, showSemiDiagonal: false, showAzimuth: false,
               direction: ringData ? initialDirection : null};

  function element(name, attributes, text) {
    var node = document.createElement(name);
    Object.keys(attributes || {}).forEach(function (key) { node.setAttribute(key, attributes[key]); });
    if (text !== undefined) { node.textContent = text; }
    return node;
  }

  function svgElement(name, attributes, text) {
    var node = document.createElementNS(SVG_NS, name);
    Object.keys(attributes || {}).forEach(function (key) { node.setAttribute(key, attributes[key]); });
    if (text !== undefined) { node.textContent = text; }
    return node;
  }

  function cell(row, text, className, isHeader) {
    var node = element(isHeader ? "th" : "td", className ? {"class": className} : {});
    node.innerHTML = text;
    row.appendChild(node);
    return node;
  }

  function lowerFirst(text) {
    return text ? text.charAt(0).toLowerCase() + text.slice(1) : text;
  }

  function escapeText(text) {
    return String(text).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  function fieldVectors(mode) {
    if (mode === 1) { return R.radialField(data, state.convention, state.zeroPercent); }
    if (mode === 5) { return R.residualField(data, state.residualStatistic); }
    return data.fields[R.MODE_NAMES[mode]] || [];
  }

  function definition(mode) {
    if (mode === 1 && state.convention === "balanced") {
      var zeroRadius = R.zeroCrossingRadius(data.pixelColumnCount, data.pixelRowCount, state.zeroPercent);
      return "Balanced, zero at r0 = " + zeroRadius.toFixed(1) + " px (" + state.zeroPercent +
        " % of the largest radius), principal distance in pixels of the balanced form " +
        R.balancedPrincipalDistance(data.calibration, zeroRadius).toFixed(2);
    }
    if (mode === 5) {
      if (!data.residuals) { return "No residuals"; }
      for (var index = 0; index < data.residuals.statistics.length; index++) {
        if (data.residuals.statistics[index][0] === state.residualStatistic) { return data.residuals.statistics[index][1]; }
      }
      return data.residuals.statistics[0][1];
    }
    return data.fieldDefinitions[R.MODE_NAMES[mode]] || "";
  }

  function fillDetails() {
    var cal = data.calibration, pitch = data.photositePitchMillimetres;
    var pitchY = data.photositePitchHeightMillimetres || pitch;
    var square = pitch && pitchY && Math.abs(pitchY - pitch) < 5e-7;
    document.getElementById("subtitle").textContent = "Interior orientation group " + data.group +
      ", chunk " + data.chunk + ", MetaFOCUS " + data.metafocusVersion + ", " + data.created;
    var rows = [["Project", escapeText(data.project || "not saved")], ["Chunk", escapeText(data.chunk)],
                ["Interior orientation group", escapeText(data.group)], ["Camera model", escapeText(data.cameraModel)],
                ["Pixel columns and rows", data.pixelColumnCount.toLocaleString("en-GB") + " × " +
                  data.pixelRowCount.toLocaleString("en-GB")],
                [square || !pitch ? "Photosite pitch p" : "Photosite pitch p<sub>x</sub>, p<sub>y</sub>",
                  !pitch ? "not set" : square ? R.formatG(pitch, 6) + " mm" : R.formatG(pitch, 6) + " mm, " + R.formatG(pitchY, 6) + " mm"],
                ["Focal length f (as set for the group, normally from the Exif data)",
                  data.focalLengthMillimetres ? R.formatG(data.focalLengthMillimetres, 6) + " mm" : "not set"],
                ["Principal distance in pixels c<sub>pix</sub>", R.formatG(cal.f, 9) + " px"],
                [square || !pitch ? "Principal distance c = c<sub>pix</sub> p" : "Principal distance c = c<sub>pix</sub> p<sub>y</sub>",
                  pitch ? R.formatG(cal.f * pitchY, 7) + " mm" : "needs the photosite pitch"],
                ["Principal point offset (cx, cy)", R.formatG(cal.cx, 7) + " px, " + R.formatG(cal.cy, 7) + " px" +
                  (pitch ? " (" + R.formatG(cal.cx * pitch, 5) + " mm, " + R.formatG(cal.cy * pitchY, 5) + " mm)" : "")],
                ["Grid of the vector fields", data.grid.columns + " × " + data.grid.rows + " cells"],
                ["Created", escapeText(data.created) + " with MetaFOCUS " + escapeText(data.metafocusVersion)]];
    var table = document.getElementById("details");
    rows.forEach(function (pair) { var row = element("tr"); cell(row, pair[0], "text", true); cell(row, pair[1], "text"); table.appendChild(row); });
    document.getElementById("footer").textContent = "Produced by the Interior Orientation Viewer of MetaFOCUS " +
      data.metafocusVersion + " (Metashape Functions for Optimising, Characterising, and Understanding Structure from Motion) on " +
      data.created + ".";
  }

  function fillParameters() {
    var container = document.getElementById("parameter-content");
    if (!data.parameters) {
      container.appendChild(element("p", {}, "No calibration covariance for this interior orientation group: its parameters " +
        "were not adjusted, or Optimize Cameras has not been run with them enabled."));
      return;
    }
    var table = element("table"), header = element("tr");
    ["Parameter", "Estimate", "Standard error (SE)", "estimate/SE", "p-value (two-sided)"].forEach(function (text, index) {
      cell(header, text, index === 0 ? "text" : "", true); });
    table.appendChild(header);
    data.parameters.rows.forEach(function (parameter) {
      var row = element("tr");
      cell(row, parameter.displayName, "text");
      cell(row, parameter.estimate);
      cell(row, parameter.standardError);
      cell(row, parameter.testStatistic, parameter.emphasised ? "emphasised" : "");
      cell(row, parameter.pValue, parameter.emphasised ? "emphasised" : "");
      table.appendChild(row);
    });
    var wrapper = element("div", {"class": "scroll"});
    wrapper.appendChild(table);
    container.appendChild(wrapper);
    var degrees = [data.parameters.degreesOfFreedomTiePoints, data.parameters.degreesOfFreedomAllObservations]
      .map(function (value) { return value === null || value === undefined ? "–" : Number(value).toLocaleString("en-GB"); });
    container.appendChild(element("p", {}, "Degrees of freedom estimated from counts: " + degrees[0] + " for the " +
      "tie points, " + degrees[1] + " for all observations."));
    if (data.parameters.degreesOfFreedomNote) {
      container.appendChild(element("p", {"class": "note"}, data.parameters.degreesOfFreedomNote));
    }
    container.appendChild(element("p", {"class": "note"}, "Bold: |estimate/SE| < 3, an estimate that may not be " +
      "distinguishable from zero, a review cue rather than a decision rule. The p-values are two-sided under a normal " +
      "approximation and conditional on the standard errors that Metashape reports. A principal distance cannot be zero, " +
      "so the principal distance has no p-value."));
    container.appendChild(element("h3", {}, "Correlations"));
    var correlationTable = element("table"), correlationHeader = element("tr");
    cell(correlationHeader, "", "text", true);
    data.parameters.rows.forEach(function (parameter) { cell(correlationHeader, parameter.name === "f" ? "c<sub>pix</sub>" : escapeText(parameter.name), "", true); });
    correlationTable.appendChild(correlationHeader);
    data.parameters.rows.forEach(function (parameter, rowIndex) {
      var row = element("tr");
      cell(row, parameter.name === "f" ? "c<sub>pix</sub>" : escapeText(parameter.name), "text", true);
      data.parameters.rows.forEach(function (unused, columnIndex) {
        var value = data.parameters.correlations[rowIndex][columnIndex];
        if (columnIndex > rowIndex || value === null) { cell(row, ""); return; }
        cell(row, value.toFixed(2), columnIndex !== rowIndex && Math.abs(value) >= 0.9 ? "emphasised" : "");
      });
      correlationTable.appendChild(row);
    });
    var correlationWrapper = element("div", {"class": "scroll"});
    correlationWrapper.appendChild(correlationTable);
    container.appendChild(correlationWrapper);
    container.appendChild(element("p", {"class": "note"}, "Bold: |ρ| ≥ 0.90, a review cue."));
  }

  function fillModel() {
    var container = document.getElementById("model-content");
    if (!data.hasDistortionModel) {
      container.appendChild(element("p", {}, "Spherical, cylindrical, and RPC camera models have no distortion model " +
        "of this kind, so the report shows only the residuals of the group."));
      return;
    }
    container.appendChild(element("p", {}, "Metashape’s camera model (Metashape manual, Appendix D), with the " +
      "values of this group. A ray (X, Y, Z) in camera coordinates reaches the pixel position (u, v):"));
    var equations = element("div", {"class": "equations"});
    equations.innerHTML = "x = X/Z, &nbsp; y = Y/Z, &nbsp; r<sup>2</sup> = x<sup>2</sup> + y<sup>2</sup><br>" +
      "x′ = x (1 + k1 r<sup>2</sup> + k2 r<sup>4</sup> + k3 r<sup>6</sup> + k4 r<sup>8</sup>) + " +
      "(p1 (r<sup>2</sup> + 2x<sup>2</sup>) + 2 p2 x y) (1 + p3 r<sup>2</sup> + p4 r<sup>4</sup>)<br>" +
      "y′ = y (1 + k1 r<sup>2</sup> + k2 r<sup>4</sup> + k3 r<sup>6</sup> + k4 r<sup>8</sup>) + " +
      "(p2 (r<sup>2</sup> + 2y<sup>2</sup>) + 2 p1 x y) (1 + p3 r<sup>2</sup> + p4 r<sup>4</sup>)<br>" +
      "u = w/2 + cx + x′ (c<sub>pix</sub> + b1) + y′ b2, &nbsp; v = h/2 + cy + y′ c<sub>pix</sub>";
    container.appendChild(equations);
    if (data.cameraModel !== "frame") {
      var modelName = data.cameraModel === "fisheye" ? "equidistant fisheye" : data.cameraModel;
      container.appendChild(element("p", {"class": "note"}, "For the " + modelName + " model, x and y are " +
        "the point at the undistorted radial distance of that model in the direction of the ray (Metashape manual, Appendix D)."));
    }
    var cal = data.calibration, table = element("table"), header = element("tr");
    ["Coefficient", "Value"].forEach(function (text, index) { cell(header, text, index === 0 ? "text" : "", true); });
    table.appendChild(header);
    [["c<sub>pix</sub>", cal.f, " px"], ["cx", cal.cx, " px"], ["cy", cal.cy, " px"], ["b1", cal.b1, " px"], ["b2", cal.b2, " px"],
     ["k1", cal.k1, ""], ["k2", cal.k2, ""], ["k3", cal.k3, ""], ["k4", cal.k4, ""], ["p1", cal.p1, ""], ["p2", cal.p2, ""],
     ["p3", cal.p3, ""], ["p4", cal.p4, ""]].forEach(function (entry) {
      var row = element("tr");
      cell(row, entry[0], "text");
      cell(row, R.formatG(entry[1], 10) + entry[2]);
      table.appendChild(row);
    });
    container.appendChild(table);
    container.appendChild(element("p", {"class": "note"}, "w and h are the numbers of pixel columns and rows, the " +
      "values in px are numbers in pixel coordinates, and the coefficients k1 to k4 and p1 to p4 are numbers."));
  }

  function plotFrame(width, height) {
    return svgElement("svg", {viewBox: "0 0 " + width + " " + height, width: width, height: height, role: "img"});
  }

  // The read-out under the pointer: a box beside
  // the pointer with the text of the tooltip of the viewer, hidden outside
  // the plots and in print.  *textAt* gives the text at a position of the
  // viewBox of *svg*, "" where there is none.
  function showReadOut(event, text) {
    var box = document.getElementById("read-out");
    if (!text) { box.style.display = "none"; return; }
    box.textContent = text;
    box.style.display = "block";
    var x = event.clientX + 14, y = event.clientY + 16;
    if (x + box.offsetWidth > window.innerWidth - 4) { x = Math.max(4, event.clientX - 14 - box.offsetWidth); }
    if (y + box.offsetHeight > window.innerHeight - 4) { y = Math.max(4, event.clientY - 16 - box.offsetHeight); }
    box.style.left = x + "px";
    box.style.top = y + "px";
  }

  function hideReadOut() {
    document.getElementById("read-out").style.display = "none";
  }

  function addReadOut(svg, width, height, textAt) {
    svg.addEventListener("mousemove", function (event) {
      var bounds = svg.getBoundingClientRect();
      showReadOut(event, textAt((event.clientX - bounds.left) * width / bounds.width,
                                (event.clientY - bounds.top) * height / bounds.height));
    });
    svg.addEventListener("mouseleave", hideReadOut);
  }

  function samplingWords() {
    return coverageData && coverageData.samplingStride > 1 ?
      ", sampled 1 in " + coverageData.samplingStride.toLocaleString("en-GB") : "";
  }

  function rgbText(values) {
    return "rgb(" + values.join(",") + ")";
  }

  // The Coverage mode: the 2D tie points per cell,
  // the cells without any tinted red, the legend of the colours, and the
  // direction of the coverage by radius, which a click in the map chooses.
  function drawCoverageMap(container) {
    var cells = coverageData.cells, counts = cells.counts;
    var width = 960, margin = 40, plotWidth = width - 2 * margin;
    var scale = plotWidth / data.pixelColumnCount, plotHeight = data.pixelRowCount * scale;
    var height = plotHeight + 2 * margin + 44;
    var svg = plotFrame(width, height);
    svg.setAttribute("id", "coverage-map");
    var reference = R.coverageColourReference(counts);
    var emptyCount = counts.filter(function (count) { return count <= 0; }).length;
    var total = counts.reduce(function (sum, count) { return sum + count; }, 0);
    svg.appendChild(svgElement("text", {x: margin, y: 22, "font-size": 15, "font-weight": "bold"},
      "Coverage, 2D tie points per cell" + samplingWords()));
    var cellGroup = svgElement("g", {"class": "coverage-cells"});
    counts.forEach(function (count, index) {
      var column = index % cells.columns, row = Math.floor(index / cells.columns);
      var left = margin + column * plotWidth / cells.columns, top = margin + row * plotHeight / cells.rows;
      cellGroup.appendChild(svgElement("rect", {x: left.toFixed(2), y: top.toFixed(2),
        width: (plotWidth / cells.columns + 0.4).toFixed(2), height: (plotHeight / cells.rows + 0.4).toFixed(2),
        fill: rgbText(R.coverageCellRgb(count, reference))}));
    });
    svg.appendChild(cellGroup);
    svg.appendChild(svgElement("rect", {x: margin, y: margin, width: plotWidth, height: plotHeight, fill: "none",
                                        stroke: "#b4b4b4"}));
    svg.appendChild(svgElement("text", {x: margin, y: margin + plotHeight + 18, "font-size": 13},
      "2D tie points: " + total.toLocaleString("en-GB") + " in " + counts.length.toLocaleString("en-GB") + " cells, " +
      emptyCount.toLocaleString("en-GB") + " of them without 2D tie points"));
    drawCoverageLegend(svg, margin, margin + plotHeight + 28, reference);
    if (ringData && state.direction !== null) {
      drawDirection(svg, margin, scale, plotWidth, plotHeight);
    }
    var mapTitle = "Coverage, 2D tie points per cell" + samplingWords();
    addReadOut(svg, width, height, function (viewX, viewY) {
      return R.coverageCellReadOutText(cells, mapTitle, data.pixelColumnCount, data.pixelRowCount,
                                       (viewX - margin) / scale, (viewY - margin) / scale);
    });
    if (ringData) {
      svg.style.cursor = "crosshair";
      svg.addEventListener("click", function (event) {
        var box = svg.getBoundingClientRect();
        var viewX = (event.clientX - box.left) * width / box.width, viewY = (event.clientY - box.top) * height / box.height;
        if (viewX < margin || viewX > margin + plotWidth || viewY < margin || viewY > margin + plotHeight) { return; }
        var direction = R.directionOfOffset((viewX - margin) / scale - ringData.principalPoint[0],
                                            (viewY - margin) / scale - ringData.principalPoint[1]);
        if (direction !== null) { setDirection(direction); }
      });
    }
    container.appendChild(svg);
  }

  function drawCoverageLegend(svg, left, top, reference) {
    var x = left;
    svg.appendChild(svgElement("text", {x: x, y: top + 10, "font-size": 12}, "2D tie points per cell:"));
    x += 140;
    svg.appendChild(svgElement("rect", {x: x, y: top, width: 18, height: 11, fill: rgbText(R.coverageCellRgb(0, reference)),
                                        stroke: "#969696"}));
    svg.appendChild(svgElement("text", {x: x + 24, y: top + 10, "font-size": 12}, "none"));
    if (reference <= 0) { return; }
    x += 70;
    svg.appendChild(svgElement("text", {x: x, y: top + 10, "font-size": 12}, "1"));
    x += 12;
    var definitions = svgElement("defs"), gradient = svgElement("linearGradient", {id: "coverage-scale"});
    gradient.appendChild(svgElement("stop", {offset: "0", "stop-color": rgbText(R.coverageCellRgb(1, reference))}));
    gradient.appendChild(svgElement("stop", {offset: "1", "stop-color": rgbText(R.coverageCellRgb(reference, reference))}));
    definitions.appendChild(gradient);
    svg.appendChild(definitions);
    svg.appendChild(svgElement("rect", {x: x, y: top, width: 160, height: 11, fill: "url(#coverage-scale)", stroke: "#969696"}));
    svg.appendChild(svgElement("text", {x: x + 168, y: top + 10, "font-size": 12}, reference.toLocaleString("en-GB") + " or more"));
  }

  function drawDirection(svg, margin, scale, plotWidth, plotHeight) {
    var point = ringData.principalPoint, halfWidth = ringData.directionHalfWidthDegrees;
    var clip = svgElement("clipPath", {id: "coverage-clip"});
    clip.appendChild(svgElement("rect", {x: margin, y: margin, width: plotWidth, height: plotHeight}));
    svg.appendChild(clip);
    var group = svgElement("g", {"clip-path": "url(#coverage-clip)", "class": "direction"});
    var startX = margin + point[0] * scale, startY = margin + point[1] * scale;
    [[state.direction - halfWidth, 3, 1.3, true], [state.direction + halfWidth, 3, 1.3, true],
     [state.direction, 4.5, 2, false]].forEach(function (line) {
      var border = R.borderPoint(point, line[0], data.pixelColumnCount, data.pixelRowCount);
      if (border === null) { return; }
      var endX = margin + border[0] * scale, endY = margin + border[1] * scale;
      group.appendChild(svgElement("line", {x1: startX, y1: startY, x2: endX, y2: endY, stroke: "rgba(255,255,255,0.9)",
                                            "stroke-width": line[1]}));
      var attributes = {x1: startX, y1: startY, x2: endX, y2: endY, stroke: "#000000", "stroke-width": line[2]};
      if (line[3]) { attributes["stroke-dasharray"] = "5 4"; }
      group.appendChild(svgElement("line", attributes));
    });
    group.appendChild(svgElement("circle", {cx: startX, cy: startY, r: 4, fill: "#ffffff", stroke: "#000000", "stroke-width": 1.2}));
    svg.appendChild(group);
  }

  function setDirection(direction) {
    state.direction = direction;
    showDirection();
    if (state.mode === 6) { drawField(); }
    drawCoverageByRadius();
  }

  function showDirection() {
    if (!ringData) { return; }
    document.getElementById("direction-value").textContent = R.directionValueText(state.direction,
                                                                                  ringData.directionHalfWidthDegrees);
    document.getElementById("all-directions").disabled = state.direction === null;
  }

  function ringStatisticWords() {
    var words = data.residuals && data.residuals.ringStatisticWords;
    return words && words[state.residualStatistic] ? words[state.residualStatistic] : "mean weighted by the inverse keypoint size";
  }

  // The coverage by radius under the profiles, on their radius axis: the
  // relative density of the 2D tie points per ring,
  // the rings in the image format without 2D tie points tinted red, and
  // the radial residual of every ring with enough 2D tie points, in all
  // directions or in the wedge of the direction.
  function drawCoverageByRadius() {
    if (!ringData) { return; }
    var rings = R.coverageRings(data, state.residualStatistic, state.direction);
    var titleWords = R.directionTitleWords(state.direction);
    var maximumRadius = R.largestProfileRadius(data.pixelColumnCount, data.pixelRowCount);
    var xInterval = R.chooseTickInterval(0, maximumRadius, 6), xHigh = Math.max(Math.ceil(maximumRadius / xInterval) * xInterval, maximumRadius);
    var visible = rings.filter(function (ring) { return ring.start < xHigh; });
    var width = 960, left = 70, right = 20, top = 34, bottom = 42, height = 200;
    var plotWidth = width - left - right, plotHeight = height - top - bottom;
    function X(radius) { return left + Math.min(radius, xHigh) / xHigh * plotWidth; }
    function frame(svg) {
      for (var tick = 0; tick <= xHigh + 1e-9 * xInterval; tick += xInterval) {
        svg.appendChild(svgElement("line", {x1: X(tick), x2: X(tick), y1: top, y2: top + plotHeight, stroke: "#e2e2e2"}));
        svg.appendChild(svgElement("text", {x: X(tick), y: top + plotHeight + 16, "font-size": 12, "text-anchor": "middle"}, R.formatG(tick, 6)));
      }
      svg.appendChild(svgElement("text", {x: left + plotWidth / 2, y: height - 6, "font-size": 13, "text-anchor": "middle"},
        "Radial distance r from the principal point (px)"));
    }
    // The relative density.
    var densitySvg = plotFrame(width, height), densityContainer = document.getElementById("density-plot");
    densityContainer.innerHTML = "";
    densitySvg.appendChild(svgElement("text", {x: left, y: 22, "font-size": 15, "font-weight": "bold"},
      "Coverage by radius" + titleWords + ": relative density of the 2D tie points"));
    densitySvg.appendChild(svgElement("rect", {x: width - right - 330, y: 11, width: 18, height: 11, fill: "rgb(250,205,205)", stroke: "#969696"}));
    densitySvg.appendChild(svgElement("text", {x: width - right - 306, y: 21, "font-size": 12}, "ring in the image format without 2D tie points"));
    frame(densitySvg);
    var densityAxis = R.densityAxis(visible.filter(function (ring) { return ring.relativeDensity !== null; })
      .map(function (ring) { return ring.relativeDensity; }));
    var densityInterval = densityAxis[0], densityMaximum = densityAxis[1];
    function DensityY(value) { return top + plotHeight - Math.min(value / densityMaximum, 1) * plotHeight; }
    visible.forEach(function (ring) {
      var x1 = X(ring.start), x2 = X(ring.end);
      if (ring.areaShare > 0 && ring.pointCount <= 0) {
        densitySvg.appendChild(svgElement("rect", {x: x1.toFixed(2), y: top, width: Math.max(x2 - x1, 1).toFixed(2), height: plotHeight,
                                                   fill: "rgb(250,205,205)", "class": "empty-ring"}));
      } else if (ring.relativeDensity !== null) {
        var barTop = DensityY(ring.relativeDensity);
        densitySvg.appendChild(svgElement("rect", {x: x1.toFixed(2), y: barTop.toFixed(2), width: Math.max(x2 - x1 - 1, 1).toFixed(2),
                                                   height: (top + plotHeight - barTop).toFixed(2), fill: "rgb(90,120,170)", "class": "ring-bar"}));
      }
    });
    for (var densityTick = 0; densityTick <= densityMaximum + 1e-9; densityTick += densityInterval) {
      densitySvg.appendChild(svgElement("text", {x: left - 6, y: DensityY(densityTick) + 4, "font-size": 12, "text-anchor": "end"},
        R.formatG(densityTick, 6)));
    }
    densitySvg.appendChild(svgElement("line", {x1: left, x2: left + plotWidth, y1: DensityY(1), y2: DensityY(1), stroke: "#3c3c3c",
                                               "stroke-dasharray": "6 4"}));
    densitySvg.appendChild(svgElement("text", {x: left + plotWidth - 6, y: DensityY(1) - 4, "font-size": 12, "text-anchor": "end"}, "1 = even spread"));
    densitySvg.appendChild(svgElement("rect", {x: left, y: top, width: plotWidth, height: plotHeight, fill: "none", stroke: "#888888"}));
    densityContainer.appendChild(densitySvg);
    // The radial residual.
    var residualSvg = plotFrame(width, height), residualContainer = document.getElementById("ring-residual-plot");
    residualContainer.innerHTML = "";
    residualSvg.appendChild(svgElement("text", {x: left, y: 22, "font-size": 15, "font-weight": "bold"},
      "Radial residual by radius" + titleWords + " (px)"));
    residualSvg.appendChild(svgElement("text", {x: width - right, y: 21, "font-size": 12, "text-anchor": "end", fill: "#5a5a5a"},
      "per ring of at least " + ringData.minimumResidualCount + " 2D tie points, " + ringStatisticWords() + samplingWords()));
    frame(residualSvg);
    var residuals = visible.filter(function (ring) { return ring.radialResidual !== null; });
    var limit = Math.max.apply(null, [1e-6].concat(residuals.map(function (ring) { return Math.abs(ring.radialResidual); })));
    var residualInterval = R.chooseTickInterval(0, limit, 2);
    limit = Math.ceil(limit / residualInterval) * residualInterval;
    function ResidualY(value) { return top + plotHeight / 2 - value / limit * plotHeight / 2; }
    residualSvg.appendChild(svgElement("line", {x1: left, x2: left + plotWidth, y1: ResidualY(0), y2: ResidualY(0), stroke: "#969696"}));
    [-limit, 0, limit].forEach(function (value) {
      residualSvg.appendChild(svgElement("text", {x: left - 6, y: ResidualY(value) + 4, "font-size": 12, "text-anchor": "end"},
        R.formatG(value, 6)));
    });
    var path = "", previousEnd = null;
    residuals.forEach(function (ring) {
      var x = X(0.5 * (ring.start + ring.end)), y = ResidualY(ring.radialResidual);
      path += (previousEnd !== null && Math.abs(ring.start - previousEnd) < 1e-6 ? "L" : "M") + x.toFixed(2) + " " + y.toFixed(2);
      residualSvg.appendChild(svgElement("circle", {cx: x.toFixed(2), cy: y.toFixed(2), r: 2.2, fill: "#000000", "class": "ring-residual"}));
      previousEnd = ring.end;
    });
    if (path) { residualSvg.appendChild(svgElement("path", {d: path, fill: "none", stroke: "#000000", "stroke-width": 1.5})); }
    residualSvg.appendChild(svgElement("rect", {x: left, y: top, width: plotWidth, height: plotHeight, fill: "none", stroke: "#888888"}));
    residualContainer.appendChild(residualSvg);
    var ringReadOut = function (viewX, viewY) {
      if (viewX < left || viewX > left + plotWidth || viewY < top || viewY > top + plotHeight) { return ""; }
      return R.coverageReadOutText(rings, (viewX - left) / plotWidth * xHigh, ringStatisticWords(), state.direction,
        ringData.directionHalfWidthDegrees, coverageData.samplingStride, ringData.minimumResidualCount);
    };
    addReadOut(densitySvg, width, height, ringReadOut);
    addReadOut(residualSvg, width, height, ringReadOut);
    document.getElementById("coverage-settings").textContent = "Coverage by radius in " +
      (state.direction === null ? "all directions" : "the direction " + R.directionValueText(state.direction, ringData.directionHalfWidthDegrees)) +
      ", radial residual as " + ringStatisticWords() + samplingWords() + ".";
  }

  function drawField() {
    var container = document.getElementById("field-plot");
    if (state.mode === 6) {
      container.innerHTML = "";
      drawCoverageMap(container);
      document.getElementById("magnification-label").textContent = "Total magnification: n/a";
      document.getElementById("field-settings").textContent = "Coverage mode" + samplingWords() + (ringData ?
        ", coverage by radius in " + (state.direction === null ? "all directions" :
        "the direction " + R.directionValueText(state.direction, ringData.directionHalfWidthDegrees)) : "") + ".";
      fillFieldTable();
      return;
    }
    var vectors = fieldVectors(state.mode);
    container.innerHTML = "";
    var width = 960, margin = 40, plotWidth = width - 2 * margin;
    var scale = plotWidth / data.pixelColumnCount, plotHeight = data.pixelRowCount * scale;
    var height = plotHeight + 2 * margin + 30;
    var svg = plotFrame(width, height);
    var statistics = R.fieldStatistics(vectors);
    var automatic = R.automaticMagnification(vectors, data);
    var user = R.userMagnification(state.slider, data);
    var magnification = automatic === null ? null : automatic * user;
    svg.appendChild(svgElement("text", {x: margin, y: 22, "font-size": 15, "font-weight": "bold"},
      R.MODE_NAMES[state.mode] + ": RMS " + R.formatG(statistics[0], 3) + " px, maximum " + R.formatG(statistics[1], 3) +
      " px, total magnification " + R.formatTotalMagnification(magnification)));
    var clip = svgElement("clipPath", {id: "frame-clip"});
    clip.appendChild(svgElement("rect", {x: margin, y: margin, width: plotWidth, height: plotHeight}));
    svg.appendChild(clip);
    svg.appendChild(svgElement("rect", {x: margin, y: margin, width: plotWidth, height: plotHeight, fill: "#fafafa",
                                        stroke: "#b4b4b4"}));
    var group = svgElement("g", {"clip-path": "url(#frame-clip)"});
    var reference = data.constants.redAtMedianMultiple * R.medianLength(vectors);
    vectors.forEach(function (vector) {
      var length = Math.hypot(vector[2], vector[3]);
      if (!isFinite(length) || magnification === null) { return; }
      var startX = margin + vector[0] * scale, startY = margin + vector[1] * scale;
      var endX = margin + (vector[0] + vector[2] * magnification) * scale;
      var endY = margin + (vector[1] + vector[3] * magnification) * scale;
      group.appendChild(svgElement("line", {x1: startX.toFixed(2), y1: startY.toFixed(2), x2: endX.toFixed(2),
        y2: endY.toFixed(2), stroke: R.vectorColour(length, reference), "stroke-width": 1.2, "class": "vector"}));
    });
    svg.appendChild(group);
    var cal = data.calibration;
    var principalX = margin + (data.pixelColumnCount / 2 + cal.cx) * scale, principalY = margin + (data.pixelRowCount / 2 + cal.cy) * scale;
    svg.appendChild(svgElement("path", {d: "M" + (principalX - 6) + " " + principalY + "h12M" + principalX + " " +
      (principalY - 6) + "v12", stroke: "#000000", "stroke-width": 1.5}));
    if (magnification !== null && statistics[1]) {
      var referenceLength = R.chooseReferenceVectorLength(scale * magnification, 60);
      var drawnLength = referenceLength * magnification * scale;
      var baseY = margin + plotHeight + 22;
      svg.appendChild(svgElement("line", {x1: margin, y1: baseY, x2: margin + drawnLength, y2: baseY, stroke: "#000000",
                                          "stroke-width": 2}));
      svg.appendChild(svgElement("text", {x: margin + drawnLength + 8, y: baseY + 4, "font-size": 13},
        "reference vector " + R.formatG(referenceLength, 3) + " px, principal point marked by a cross"));
    }
    var fieldTitle = R.MODE_NAMES[state.mode];
    addReadOut(svg, width, height, function (viewX, viewY) {
      return R.vectorReadOutText(vectors, fieldTitle, data.pixelColumnCount, data.pixelRowCount, data.grid.columns,
                                 (viewX - margin) / scale, (viewY - margin) / scale);
    });
    container.appendChild(svg);
    document.getElementById("magnification-label").textContent = "Total magnification: " + R.formatTotalMagnification(magnification);
    document.getElementById("field-settings").textContent = "Vector field " + R.MODE_NAMES[state.mode] +
      ", total magnification " + R.formatTotalMagnification(magnification) + ", radial distortion " +
      (state.convention === "balanced" ? "balanced" : "Gaussian") + (data.residuals ? ", residuals as " + lowerFirst(definition(5)) : "") + ".";
    fillFieldTable();
  }

  function fillFieldTable() {
    var table = document.getElementById("field-table");
    table.innerHTML = "";
    var header = element("tr");
    ["Vector field", "RMS (px)", "Max. (px)", "Vectors", "Definition"].forEach(function (text, index) {
      cell(header, text, index === 0 || index === 4 ? "text" : "", true); });
    table.appendChild(header);
    for (var mode = 0; mode < 6; mode++) {
      var vectors = fieldVectors(mode), statistics = R.fieldStatistics(vectors), row = element("tr");
      cell(row, R.MODE_NAMES[mode], "text");
      cell(row, vectors.length ? R.formatG(statistics[0], 3) : "none");
      cell(row, vectors.length ? R.formatG(statistics[1], 3) : "none");
      cell(row, vectors.length.toLocaleString("en-GB"));
      cell(row, escapeText(definition(mode)), "text");
      table.appendChild(row);
    }
  }

  function drawProfilePlot(containerId, title, radii, series, yIsSigned, markers) {
    var container = document.getElementById(containerId);
    container.innerHTML = "";
    var width = 960, height = 360, left = 70, right = 20, top = 34, bottom = 46;
    var plotWidth = width - left - right, plotHeight = height - top - bottom;
    var svg = plotFrame(width, height);
    var values = [];
    series.forEach(function (curve) { curve.values.forEach(function (value) { if (isFinite(value)) { values.push(value); } }); });
    var minimum = yIsSigned ? Math.min.apply(null, values.concat([0])) : 0;
    var maximum = Math.max.apply(null, values.concat([0]));
    if (maximum - minimum < 1e-9) { maximum = minimum + 1; }
    var yInterval = R.chooseTickInterval(minimum, maximum, 6);
    var yLow = Math.floor(minimum / yInterval) * yInterval, yHigh = Math.ceil(maximum / yInterval) * yInterval;
    var xMaximum = radii[radii.length - 1], xInterval = R.chooseTickInterval(0, xMaximum, 6);
    var xHigh = Math.ceil(xMaximum / xInterval) * xInterval;
    function X(value) { return left + value / xHigh * plotWidth; }
    function Y(value) { return top + (yHigh - value) / (yHigh - yLow) * plotHeight; }
    svg.appendChild(svgElement("text", {x: left, y: 22, "font-size": 15, "font-weight": "bold"}, title));
    for (var tick = yLow; tick <= yHigh + 1e-9 * yInterval; tick += yInterval) {
      svg.appendChild(svgElement("line", {x1: left, x2: left + plotWidth, y1: Y(tick), y2: Y(tick), stroke: Math.abs(tick) < 1e-9 * yInterval ? "#888888" : "#e2e2e2"}));
      svg.appendChild(svgElement("text", {x: left - 6, y: Y(tick) + 4, "font-size": 12, "text-anchor": "end"}, R.formatG(tick, 6)));
    }
    for (var xTick = 0; xTick <= xHigh + 1e-9 * xInterval; xTick += xInterval) {
      svg.appendChild(svgElement("line", {x1: X(xTick), x2: X(xTick), y1: top, y2: top + plotHeight, stroke: "#e2e2e2"}));
      svg.appendChild(svgElement("text", {x: X(xTick), y: top + plotHeight + 16, "font-size": 12, "text-anchor": "middle"}, R.formatG(xTick, 6)));
    }
    svg.appendChild(svgElement("rect", {x: left, y: top, width: plotWidth, height: plotHeight, fill: "none", stroke: "#888888"}));
    svg.appendChild(svgElement("text", {x: left + plotWidth / 2, y: height - 8, "font-size": 13, "text-anchor": "middle"},
      "Radial distance r from the principal point (px)"));
    svg.appendChild(svgElement("text", {x: 16, y: top + plotHeight / 2, "font-size": 13, "text-anchor": "middle",
      transform: "rotate(-90 16 " + (top + plotHeight / 2) + ")"}, "Displacement (px)"));
    (markers || []).forEach(function (marker) {
      if (!(marker.radius > 0 && marker.radius <= xHigh)) { return; }
      svg.appendChild(svgElement("line", {x1: X(marker.radius), x2: X(marker.radius), y1: top, y2: top + plotHeight,
        stroke: marker.colour, "stroke-width": 1.5, "stroke-dasharray": "6 4"}));
      svg.appendChild(svgElement("text", {x: X(marker.radius) - 4, y: top + 14, "font-size": 12, "text-anchor": "end", fill: marker.colour}, marker.label));
    });
    var legendX = left + 10, legendY = top + 18;
    series.forEach(function (curve) {
      if (!curve.values.length) { return; }
      var points = radii.map(function (radius, index) { return X(radius).toFixed(2) + "," + Y(curve.values[index]).toFixed(2); }).join(" ");
      var attributes = {points: points, fill: "none", stroke: curve.colour, "stroke-width": curve.width || 1.5};
      if (curve.dashed) { attributes["stroke-dasharray"] = "6 4"; }
      if (curve.dotted) { attributes["stroke-dasharray"] = "2 3"; }
      svg.appendChild(svgElement("polyline", attributes));
      svg.appendChild(svgElement("line", {x1: legendX, x2: legendX + 26, y1: legendY - 4, y2: legendY - 4, stroke: curve.colour,
        "stroke-width": curve.width || 1.5, "stroke-dasharray": curve.dashed ? "6 4" : (curve.dotted ? "2 3" : "none")}));
      svg.appendChild(svgElement("text", {x: legendX + 32, y: legendY, "font-size": 12}, curve.label));
      legendY += 16;
    });
    var curves = series.filter(function (curve) { return curve.values.length; })
      .map(function (curve) { return [curve.label, curve.values]; });
    addReadOut(svg, width, height, function (viewX, viewY) {
      if (viewX < left || viewX > left + plotWidth || viewY < top || viewY > top + plotHeight) { return ""; }
      return R.profileReadOutText(radii, curves, (viewX - left) / plotWidth * xHigh);
    });
    container.appendChild(svg);
  }

  // The zero crossing applies to the balanced radial distortion only, so
  // its field is shown for that form only, as in the viewer.
  function showZeroPercent() {
    document.getElementById("zero-percent-label").style.display = state.convention === "balanced" ? "" : "none";
  }

  // The decentring angle applies to the overlay Magnitude at the angle
  // only, so its field is shown with that overlay only, as in the viewer.
  function showDecentringAngle() {
    document.getElementById("decentring-angle-label").style.display = state.showAngle ? "" : "none";
  }

  function drawProfiles() {
    var profile = R.profiles(data, state.convention, state.zeroPercent, state.angle);
    var cal = data.calibration;
    var radial = [{label: "Total radial distortion", values: profile.totalRadial, colour: "rgb(0,0,0)", width: 2.2}];
    [["k1", "rgb(180,30,30)"], ["k2", "rgb(220,120,20)"], ["k3", "rgb(160,80,0)"], ["k4", "rgb(210,50,90)"]].forEach(function (entry) {
      if (Math.abs(cal[entry[0]]) > 1e-15) { radial.push({label: entry[0], values: profile[entry[0]], colour: entry[1], dashed: true}); }
    });
    var markers = [];
    var zeroRadius = R.zeroCrossingRadius(data.pixelColumnCount, data.pixelRowCount, state.zeroPercent);
    if (state.convention === "balanced") { markers.push({radius: zeroRadius, label: "r0", colour: "#606060"}); }
    if (data.radialMaximum) { markers.push({radius: data.radialMaximum.undistortedRadiusPixels, label: "largest distorted radius", colour: "#c00000"}); }
    drawProfilePlot("radial-plot", state.convention === "balanced" ?
      "Radial distortion, balanced (zero at r0 = " + zeroRadius.toFixed(1) + " px), positive towards the principal point" :
      "Radial distortion (Gaussian), positive towards the principal point", profile.radii, radial, true, markers);
    var decentring = [{label: "Profile function (Metashape)", values: profile.totalDecentring, colour: "rgb(100,20,160)", width: 2.2}];
    if (Math.abs(cal.p1) > 1e-15) { decentring.push({label: "p1", values: profile.p1, colour: "rgb(150,50,200)", dashed: true}); }
    if (Math.abs(cal.p2) > 1e-15) { decentring.push({label: "p2", values: profile.p2, colour: "rgb(80,60,180)", dashed: true}); }
    if (state.showAngle) { decentring.push({label: "Magnitude at " + state.angle + "°", values: profile.angleMagnitude, colour: "rgb(213,94,0)"}); }
    if (state.showSemiDiagonal) { decentring.push({label: "Semi-diagonal mean", values: profile.semiDiagonalMean, colour: "rgb(0,158,115)"}); }
    if (state.showAzimuth) { decentring.push({label: "Azimuth mean", values: profile.azimuthMean, colour: "rgb(0,0,0)", dotted: true, width: 2}); }
    drawProfilePlot("decentring-plot", "Decentring distortion", profile.radii, decentring, false, []);
    fillProfileTable();
    document.getElementById("profile-settings").textContent = "Radial distortion " + (state.convention === "balanced" ?
      "balanced, zero at " + state.zeroPercent + " % of the largest radius" : "Gaussian") + ", decentring angle " + state.angle + "°.";
  }

  function fillProfileTable() {
    var maximumRadius = R.largestProfileRadius(data.pixelColumnCount, data.pixelRowCount);
    var interval = R.chooseTickInterval(0, maximumRadius, 14);
    var table = document.getElementById("profile-table");
    table.innerHTML = "";
    var header = element("tr");
    ["r (px)", "Radial, Gaussian (px)", "Radial, balanced (px)", "Decentring profile function (px)"].forEach(function (text) { cell(header, text, "", true); });
    table.appendChild(header);
    var cal = data.calibration;
    var zeroRadius = R.zeroCrossingRadius(data.pixelColumnCount, data.pixelRowCount, state.zeroPercent);
    var offsetTerm = (function () { var n = zeroRadius / cal.f; return R.radialTerm(cal, n * n); })();
    for (var radius = 0; radius <= maximumRadius + 1e-9; radius += interval) {
      var normalised = radius / cal.f, r2 = normalised * normalised, term = R.radialTerm(cal, r2);
      var higherOrder = 1 + cal.p3 * r2 + cal.p4 * r2 * r2;
      var row = element("tr");
      cell(row, R.formatG(radius, 6));
      cell(row, R.formatG(-radius * term, 4));
      cell(row, R.formatG(-radius * (term - offsetTerm), 4));
      cell(row, R.formatG(cal.f * r2 * Math.abs(higherOrder) * Math.hypot(cal.p1, cal.p2), 4));
      table.appendChild(row);
    }
    document.getElementById("profile-note").textContent = "The radial distortion is positive towards the principal point " +
      "(barrel distortion), as in Metashape’s Profile tab. The balanced form is zero at r0 = " + zeroRadius.toFixed(1) +
      " px and describes the same camera model with the principal distance in pixels " +
      R.balancedPrincipalDistance(cal, zeroRadius).toFixed(2) + ". The largest radius of the profiles is half the diagonal of the image format." +
      foldNote(maximumRadius);
  }

  // The largest distorted radius of the radial distortion polynomial: for
  // the calibration of an interior orientation group, Metashape 2.3.2
  // keeps the radial factor of that radius for larger radii instead of
  // following the polynomial, so its camera model does not fold back
  // there.  A Metashape.Calibration that belongs to no group follows the
  // polynomial.
  function foldNote(maximumRadius) {
    if (!data.radialMaximum) { return ""; }
    var fold = data.radialMaximum.undistortedRadiusPixels, image = data.radialMaximum.distortedRadiusPixels;
    return " The radial distance r of the profiles is that of the undistorted ray, so the largest distorted radius of " +
      "the radial distortion polynomial, " + Math.round(image).toLocaleString("en-GB") + " px from the principal " +
      "point in the image, lies at r = " + Math.round(fold).toLocaleString("en-GB") + " px" +
      (fold > maximumRadius ? ", beyond the largest radius of the profiles." : ", where the radial plot marks it.") +
      " For the calibration of an interior orientation group, Metashape 2.3.2 does not follow the polynomial " +
      "beyond it: it keeps the radial factor of that radius, so that its projection keeps growing.";
  }

  function fillNotes() {
    var container = document.getElementById("notes-content");
    var notes = (data.notes || []).slice();
    if (data.settings && data.settings.difference) { notes.push("Difference: " + data.settings.difference + "."); }
    if (data.residuals && data.residuals.individualCount) {
      notes.push("Residuals: " + data.residuals.individualCount.toLocaleString("en-GB") + (data.residuals.individualCount === 1 ? " individual residual" : " individual residuals") + " with an RMS of " + R.formatG(data.residuals.individualRms, 3) + " px, summarised per grid cell by the chosen statistic" + (data.residuals.withoutKeypointSizeCount ? " (the weighted cell means leave out the " + data.residuals.withoutKeypointSizeCount.toLocaleString("en-GB") + " without a positive keypoint size)" : "") + ".");
    } else if (data.residuals) {
      notes.push("Residuals: none.");
    }
    if (coverageData) {
      var counts = coverageData.cells.counts;
      notes.push("Coverage: " + counts.reduce(function (sum, count) { return sum + count; }, 0).toLocaleString("en-GB") +
        " 2D tie points in " + counts.length.toLocaleString("en-GB") + " cells" + (coverageData.samplingStride > 1 ?
        " (sampled 1 in " + coverageData.samplingStride.toLocaleString("en-GB") + ")" : "") + ", " +
        counts.filter(function (count) { return count <= 0; }).length.toLocaleString("en-GB") + " of the cells without 2D tie points.");
      var emptyRings = R.coverageRings(data, state.residualStatistic, null).filter(function (ring) { return ring.areaShare > 0 && ring.pointCount <= 0; });
      if (emptyRings.length) {
        notes.push("Coverage by radius: " + emptyRings.length + (emptyRings.length === 1 ? " ring" : " rings") +
          " in the image format but without 2D tie points, the first at r = " + Math.round(emptyRings[0].start).toLocaleString("en-GB") + " px.");
      }
      var beyondCount = coverageData.withoutRadiusCount - coverageData.unconvergedRadiusCount;
      if (beyondCount) {
        notes.push("Coverage by radius: " + beyondCount.toLocaleString("en-GB") + (beyondCount === 1 ? " 2D tie point" : " 2D tie points") +
          " left out, for which no undistorted point was found within the radius at which the radial distortion polynomial reaches its largest distorted radius.");
      }
      if (coverageData.unconvergedRadiusCount) {
        notes.push("Coverage by radius: " + coverageData.unconvergedRadiusCount.toLocaleString("en-GB") +
          (coverageData.unconvergedRadiusCount === 1 ? " 2D tie point" : " 2D tie points") +
          " whose r was not found, because the inversion of the decentring distortion did not converge, left out.");
      }
    }
    if (!notes.length) { notes.push("No notes."); }
    var list = element("ul");
    // The notes are those of the Console, whose plain-text principal distance in pixels the page writes with a
    // subscript, as in its other texts.
    notes.forEach(function (note) {
      var item = element("li");
      item.innerHTML = escapeText(note).replace(/c_(pix)/g, "c<sub>$1</sub>");
      list.appendChild(item);
    });
    container.appendChild(list);
  }

  function buildControls() {
    var buttons = document.getElementById("mode-buttons");
    R.MODE_NAMES.forEach(function (name, mode) {
      var label = element("label"), input = element("input", {type: "radio", name: "mode", value: String(mode)});
      if (mode === state.mode) { input.checked = true; }
      if (mode === 6 ? !coverageData : !fieldVectors(mode).length) { input.disabled = true; }
      input.addEventListener("change", function () { state.mode = mode; drawField(); });
      label.appendChild(input);
      label.appendChild(document.createTextNode(" " + name + " "));
      buttons.appendChild(label);
    });
    var slider = document.getElementById("magnification");
    slider.addEventListener("input", function () { state.slider = Number(slider.value); drawField(); });
    var residualSelect = document.getElementById("residual-statistic");
    if (data.residuals) {
      data.residuals.statistics.forEach(function (entry) {
        var option = element("option", {value: entry[0]}, entry[1]);
        if (entry[0] === state.residualStatistic) { option.selected = true; }
        residualSelect.appendChild(option);
      });
      if (!state.residualStatistic) { state.residualStatistic = data.residuals.statistics[0][0]; }
      residualSelect.addEventListener("change", function () {
        state.residualStatistic = residualSelect.value; drawField(); drawCoverageByRadius(); });
    } else {
      residualSelect.disabled = true;
    }
    var convention = document.getElementById("radial-convention");
    convention.value = state.convention;
    showZeroPercent();
    convention.addEventListener("change", function () { state.convention = convention.value; showZeroPercent(); drawField(); drawProfiles(); });
    var zero = document.getElementById("zero-percent");
    zero.value = state.zeroPercent;
    zero.addEventListener("change", function () {
      var value = Math.min(100, Math.max(10, Math.round(Number(zero.value) || 70)));
      zero.value = value; state.zeroPercent = value; drawField(); drawProfiles(); });
    var angle = document.getElementById("decentring-angle");
    angle.addEventListener("change", function () {
      var value = ((Math.round(Number(angle.value) || 0) % 360) + 360) % 360;
      angle.value = value; state.angle = value; drawProfiles(); });
    [["angle-magnitude", "showAngle"], ["semi-diagonal-mean", "showSemiDiagonal"], ["azimuth-mean", "showAzimuth"]].forEach(function (entry) {
      var box = document.getElementById(entry[0]);
      box.addEventListener("change", function () { state[entry[1]] = box.checked; showDecentringAngle(); drawProfiles(); });
    });
    showDecentringAngle();
    var allDirections = document.getElementById("all-directions");
    allDirections.addEventListener("click", function () { setDirection(null); });
    showDirection();
  }

  fillDetails();
  fillParameters();
  fillModel();
  if (data.pixelColumnCount && data.pixelRowCount && !data.hasDistortionModel) {
    state.mode = 5;
    buildControls();
    drawField();
    document.getElementById("profile-controls").style.display = "none";
    document.getElementById("profile-body").style.display = "none";
    document.getElementById("profiles").appendChild(element("p", {}, "No distortion profiles: the camera model of " +
      "the group has no distortion model of this kind."));
  } else if (data.pixelColumnCount && data.pixelRowCount && data.calibration.f > 0) {
    buildControls();
    drawField();
    drawProfiles();
    if (ringData) {
      drawCoverageByRadius();
      document.getElementById("coverage-note").textContent = "Each ring is a tenth of the spacing of the radius ticks " +
        "wide. The relative density of a ring is the share of the 2D tie points that lie in it, divided by the share " +
        "of the image format in it, also in the wedge of a direction (±" + ringData.directionHalfWidthDegrees + "° around it, " +
        "from the image x axis towards the image y axis), so that an even spread gives 1. Both shares are those of all " +
        "directions, of the 2D tie points with an r and of the part of the image format where r is found. The radial residual is the " +
        "component of the residuals along the direction away from the principal point. r includes the inversion of " +
        "the decentring distortion, and leaves out the additional corrections.";
    } else {
      document.getElementById("coverage-body").style.display = "none";
    }
  } else {
    document.getElementById("fields").appendChild(element("p", {}, "No vector fields and no profiles: the group has no frame or fisheye camera model with a positive principal distance."));
  }
  fillNotes();
})();
</script>
</body>
</html>
"""
