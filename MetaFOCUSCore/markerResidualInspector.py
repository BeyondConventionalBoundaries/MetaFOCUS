# -*- coding: utf-8 -*-
"""
Marker Residual Inspector
=========================

A dialog for inspecting marker reprojection residuals without
opening every marker's Show Info panel.

The tool provides four views:

  1. Marker summary: which markers behave badly overall?
  2. Projection details: one marker×image observation per row
  3. Image summary: are several markers bad in the same image?
  4. Diagnostics: prioritised triage of suspicious patterns of the
     image residuals, followed by the markers whose error in object
     space exceeds twice the root sum of squares of their a priori
     standard deviations

The residuals are computed from the current bundle solution as:

    residual = measured marker projection − camera.project(marker.position)

A large image residual is a symptom, not an automatic instruction
to delete a marker projection.  The dialog therefore also reports
marker-level patterns, image-level patterns, residual-vector
coherence, the distance from the image centre, the role of each
marker (control, check, or tie-only), and manual review flags.

Manual review flags are session-only: they are kept per chunk while
Metashape runs, also when the dialog is closed and opened again.
They are included in CSV exports, but they are not written into the
Metashape project.

Author:     Geert J. Verhoeven (ORCID: 0000-0003-4825-9604)
Contact:    geert.verhoeven@univie.ac.at
Version:    1.0.0 (2026-11-18)
Licence:    GPL-3.0-or-later WITH GPL-3.0-linking-exception
Requires:   Agisoft Metashape Professional 2.x
Developed for: Agisoft Metashape Professional 2.3.2 (tested offline, see README.md)

Usage
-----
Loaded by the MetaFOCUS loader (MetaFOCUS.py).  Open the tool from
MetaFOCUS > Control Measurements and Residuals > Marker Residual
Inspector.  Registering the menu item removes any previous item
first, so reloading the toolbox never creates duplicates.
"""

import csv
import math
import os
import statistics
import traceback
from collections import defaultdict
from typing import Any, Dict, List, Optional, Sequence, Tuple

try:
    import Metashape  # type: ignore
except Exception:  # pragma: no cover - only relevant outside Metashape
    Metashape = None  # type: ignore

try:
    from PySide2 import QtCore, QtGui, QtWidgets  # type: ignore
except Exception:  # pragma: no cover
    from PySide6 import QtCore, QtGui, QtWidgets  # type: ignore
from .photogrammetryUtilities import (
    CrsKind, FormatCountWithNoun, IsInspectionMarker, MarkerReferenceCoordinateSystem, MarkerReferenceDiscrepancy,
    MarkerReferenceUsesChunkCoordinateSystem, ReportSuppressedError,
    CSV_FORMAT_ROW_LABEL_TEXT, FillCsvFormatComboBox, ChosenCsvFormat, WithDecimalSeparator,
    ConsoleBlockEnd, ConsoleBlockStart, ConsoleLabelledLine, PrintConsoleLines,
    FormatLengthWithDecimals, FormatLengthForCsv)


_MENU_LABEL = "MetaFOCUS/Control Measurements and Residuals/Marker Residual Inspector"
_ICON_NAME = "marker_residual_inspector_32.png"
_DEFAULT_THRESHOLD_PIXELS = 1.0
_SUSPECT_PROJECTION_MAXIMUM_TO_MEDIAN_RATIO = 5.0
_SYSTEMATIC_COHERENCE_THRESHOLD = 0.70
_MINIMUM_EXCEEDANCE_COUNT = 3

# A control marker counts as fitting its reference coordinates when its
# object-space error is at most this multiple of the root sum of squares
# of its a priori standard deviations (the per-axis values of its
# Accuracy field, see AccuracyNorm).  This test decides the remark
# "possible reference-weighting tension", which a factor of 2 would
# make appear more often, so it keeps a factor of its own.
_REFERENCE_FIT_ACCURACY_FACTOR = 1.0

# The object-space status, and the remarks that say that an object-space
# discrepancy exceeds it, compare the object-space error with this
# multiple of the root sum of squares of the a priori standard
# deviations (see ObjectSpaceStatus).
_OBJECT_SPACE_STATUS_FACTOR = 2.0

# Object-space status of a marker with reference coordinates (see
# ObjectSpaceStatus), reported apart from its image residuals.
_OBJECT_SPACE_WITHIN = "within twice the root sum of squares of the a priori standard deviations"
_OBJECT_SPACE_EXCEEDS = "exceeds twice the root sum of squares of the a priori standard deviations"
_OBJECT_SPACE_NOT_EVALUATED = "not evaluated"

# Kind of a Diagnostics row that stands for one marker projection.
_DIAGNOSTIC_KIND_PROJECTION = "Projection"

# Kind of a Diagnostics row of a marker whose object-space error exceeds
# the threshold of the object-space status, listed as a group of its own
# after the rows ranked by the severity of the image residuals.
_DIAGNOSTIC_KIND_OBJECT_SPACE = "Object space"

# The Console lists at most this many markers whose object-space error
# exceeds the threshold of the object-space status (see
# RefreshConsoleLines), then how many more.
_CONSOLE_LISTED_MARKER_COUNT = 10

_SELECT_PROJECTION_ROWS_TEXT = (
    "Select one or more rows on the Projection details tab, or "
    "Projection rows on the Diagnostics tab, first.")

# Columns of the CSV exports whose values are numbers that Metashape
# keeps as text: the image quality of the image metadata.  They get the
# chosen decimal separator like the numbers of the other numeric columns
# (see FormatCsvValue), whereas the other text columns keep their text.
_CSV_NUMERIC_TEXT_COLUMN_KEYS = frozenset({"imageQuality"})

# Columns whose values are lengths in metres: shown with their decimals
# and written to CSV files with six, or with at least four significant
# digits where these give fewer (FormatValue and FormatCsvValue with
# isLength).
_LENGTH_COLUMN_KEYS = frozenset({"objectError", "referenceAccuracy"})

# File name word per table kind for the CSV exports.
_CSV_FILE_WORDS = {
    "markers": "Markers",
    "projections": "Projections",
    "cameras": "Images",
    "diagnostics": "Diagnostics",
}


# -----------------------------------------------------------------------------
# Qt compatibility helpers
# -----------------------------------------------------------------------------


def QtUserRole():
    if hasattr(QtCore.Qt, "ItemDataRole"):
        return QtCore.Qt.ItemDataRole.UserRole
    return QtCore.Qt.UserRole




def QtAlignRight():
    if hasattr(QtCore.Qt, "AlignmentFlag"):
        return QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter
    return QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter


def QtAlignCenter():
    if hasattr(QtCore.Qt, "AlignmentFlag"):
        return QtCore.Qt.AlignmentFlag.AlignCenter
    return QtCore.Qt.AlignCenter


def QtItemIsEditable():
    if hasattr(QtCore.Qt, "ItemFlag"):
        return QtCore.Qt.ItemFlag.ItemIsEditable
    return QtCore.Qt.ItemIsEditable




userRole = QtUserRole()


# -----------------------------------------------------------------------------
# Generic helpers
# -----------------------------------------------------------------------------


def ConsoleStatusLine(text: str) -> str:
    """A message or status of the inspector as a line of the Console."""
    return "Marker Residual Inspector: " + text


def ShowAboveConsoleLine(checked: bool) -> str:
    """The Console line of the filter "Show only rows at or above the
    threshold"."""
    return ConsoleStatusLine("only rows at or above the threshold shown." if checked else "all rows shown.")


def HighlightConsoleLine(checked: bool) -> str:
    """The Console line of "Highlight rows at or above the threshold"."""
    return ConsoleStatusLine("rows at or above the threshold {}.".format(
        "highlighted" if checked else "not highlighted"))


def RoleFilterConsoleLine(role: str) -> str:
    """The Console line of the role filter (All, control, check, or
    tie-only)."""
    return ConsoleStatusLine("role filter {}.".format(role))


def SearchConsoleLine(searchText: str) -> str:
    """The Console line of the search for a marker or image label."""
    return ConsoleStatusLine("search for '{}'.".format(searchText) if searchText else "search cleared.")


def Message(text: str, title: str = "Marker Residual Inspector") -> None:
    """Show an information message titled with the tool name and echo
    it to the Console."""
    print(ConsoleStatusLine(text))
    QtWidgets.QMessageBox.information(QtWidgets.QApplication.activeWindow(), title, text)


def ErrorMessage(text: str, title: str = "Marker Residual Inspector") -> None:
    """Show an error message titled with the tool name and echo it to
    the Console."""
    print(ConsoleStatusLine(text))
    QtWidgets.QMessageBox.critical(None, title, text)


def RefreshConsoleLines(
    chunkLabel: str,
    thresholdPixels: float,
    validOnly: bool,
    projectionRows: Sequence[Dict[str, Any]],
    markerRows: Sequence[Dict[str, Any]],
    cameraRows: Sequence[Dict[str, Any]],
    diagnosticRows: Sequence[Dict[str, Any]],
) -> List[str]:
    """The Console block of a refresh: the chunk, the threshold, the
    marker projections and how many are at or above the threshold, the
    markers by role, the images, the object-space status of the markers
    with reference coordinates, and the rows of the Diagnostics table."""
    aboveCount = sum(1 for row in projectionRows if float(row["errorPixels"]) >= thresholdPixels)
    roleCounts = {"control": 0, "check": 0, "tie-only": 0}
    for row in markerRows:
        role = row.get("role")
        if role in roleCounts:
            roleCounts[role] += 1
    roleText = ", ".join((FormatCountWithNoun(roleCounts["control"], "control point", "control points"),
                          FormatCountWithNoun(roleCounts["check"], "check point", "check points"),
                          FormatCountWithNoun(roleCounts["tie-only"], "tie-only marker", "tie-only markers")))
    evaluatedRows = [row for row in markerRows
                     if row.get("objectSpaceStatus") in (_OBJECT_SPACE_WITHIN, _OBJECT_SPACE_EXCEEDS)]
    exceedingLabels = [str(row.get("marker", "")) for row in evaluatedRows
                       if row.get("objectSpaceStatus") == _OBJECT_SPACE_EXCEEDS]
    if not evaluatedRows:
        statusText = "not evaluated for any marker"
    else:
        statusText = "{:,} of {} with an evaluated object-space error {} twice the root sum of squares of the a " \
                     "priori standard deviations".format(
                         len(exceedingLabels), FormatCountWithNoun(len(evaluatedRows), "marker", "markers"),
                         "exceeds" if len(exceedingLabels) == 1 else "exceed")
        if exceedingLabels:
            listedLabels = ", ".join(exceedingLabels[:_CONSOLE_LISTED_MARKER_COUNT])
            if len(exceedingLabels) > _CONSOLE_LISTED_MARKER_COUNT:
                listedLabels += ", and {:,} more".format(len(exceedingLabels) - _CONSOLE_LISTED_MARKER_COUNT)
            statusText += ": " + listedLabels
    lines = ConsoleBlockStart("Marker Residual Inspector")
    lines.append(ConsoleLabelledLine("Chunk:", chunkLabel or "(unnamed)"))
    lines.append(ConsoleLabelledLine("Residual threshold:", "{:.3f} px".format(thresholdPixels)))
    lines.append(ConsoleLabelledLine("Marker projections:", "{:,} ({}), {:,} at or above the threshold".format(
        len(projectionRows), "valid only" if validOnly else "valid and invalid", aboveCount)))
    lines.append(ConsoleLabelledLine("Markers:", "{:,} ({})".format(len(markerRows), roleText)))
    lines.append(ConsoleLabelledLine("Images:", "{:,}".format(len(cameraRows))))
    lines.append(ConsoleLabelledLine("Object-space status:", statusText))
    lines.append(ConsoleLabelledLine("Diagnostics:", FormatCountWithNoun(len(diagnosticRows), "row", "rows")))
    lines.extend(ConsoleBlockEnd())
    return lines


# The lines of a refresh block that depend on the residual threshold (see
# RefreshConsoleOutput).
_THRESHOLD_DEPENDENT_LABELS = ("Residual threshold:", "Marker projections:", "Diagnostics:")


def ThresholdConsoleLine(
    thresholdPixels: float,
    projectionRows: Sequence[Dict[str, Any]],
    diagnosticRows: Sequence[Dict[str, Any]],
) -> str:
    """The Console line of a change of the residual threshold: the new
    residual threshold, the marker projections at or above it, and the
    rows of the Diagnostics table."""
    aboveCount = sum(1 for row in projectionRows if float(row["errorPixels"]) >= thresholdPixels)
    return ConsoleStatusLine("residual threshold {:.3f} px: {:,} of {} at or above it, {} in the Diagnostics "
                             "table.".format(
        thresholdPixels, aboveCount,
        FormatCountWithNoun(len(projectionRows), "marker projection", "marker projections"),
        FormatCountWithNoun(len(diagnosticRows), "row", "rows")))


def RefreshConsoleOutput(
    lines: Sequence[str],
    previousLines: Optional[Sequence[str]],
    mode: Optional[str],
    thresholdLine: str,
) -> List[str]:
    """The lines that a refresh prints: the block *lines* of
    RefreshConsoleLines, except after a change of the threshold (*mode*
    "threshold"), which prints *thresholdLine* when nothing but the
    threshold-dependent lines changed since the previous refresh
    (*previousLines*), and after a review flag (*mode* "quiet"), which
    prints nothing when nothing changed."""
    if mode == "quiet" and previousLines is not None and list(lines) == list(previousLines):
        return []
    if mode == "threshold" and previousLines is not None:
        if _WithoutThresholdLines(lines) == _WithoutThresholdLines(previousLines):
            return [thresholdLine]
    return list(lines)


def _WithoutThresholdLines(blockLines: Sequence[str]) -> List[str]:
    """The lines of a refresh block that do not depend on the threshold."""
    return [line for line in blockLines if not line.startswith(_THRESHOLD_DEPENDENT_LABELS)]


def FormatValue(value: Any, decimals: int = 3, isLength: bool = False) -> str:
    """Text of one table cell: numbers with *decimals* decimals, a length
    (*isLength*) with four significant digits where these give fewer
    (FormatLengthWithDecimals), a numeric triple
    component by component, yes or no for a flag, and an empty string for
    None or NaN."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if math.isnan(value):
            return ""
        if math.isinf(value):
            return "∞"
        if isLength:
            return FormatLengthWithDecimals(value, decimals)
        return f"{value:.{decimals}f}"
    if isinstance(value, (tuple, list)):
        return ", ".join(FormatValue(component, decimals, isLength) for component in value)
    return str(value)


def FormatCsvValue(value: Any, decimalSeparator: str = ".", isLength: bool = False) -> str:
    """Text of one CSV cell: numbers with six decimals and
    *decimalSeparator*, a length in metres (*isLength*) with as many more
    as four significant digits need (FormatLengthForCsv), a numeric triple
    component by component, separated by slashes as in the Accuracy column of
    Metashape's Reference pane (so that no comma separates components
    when the decimal separator is a comma), an empty cell for
    None or NaN, everything else as text.  The values of a numeric column
    are numbers, those of a text column text, so that the decimal
    separator never changes a label."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        if math.isnan(value):
            return ""
        if isLength:
            return WithDecimalSeparator(FormatLengthForCsv(value, 6), decimalSeparator)
        return WithDecimalSeparator(f"{value:.6f}", decimalSeparator)
    if isinstance(value, (tuple, list)):
        return "/".join(FormatCsvValue(component, decimalSeparator, isLength) for component in value)
    return str(value)


def SafeVectorComponent(vector: Any, index: int) -> Optional[float]:
    try:
        return float(vector[index])
    except Exception as suppressedError:
        ReportSuppressedError("markerResidualInspector.SafeVectorComponent", suppressedError)
        return None


def VectorDistance(firstVector: Any, secondVector: Any) -> Optional[float]:
    """Euclidean distance between two vectors, or None if either is
    missing."""
    if firstVector is None or secondVector is None:
        return None
    differences = []
    for index in range(3):
        firstComponent = SafeVectorComponent(firstVector, index)
        secondComponent = SafeVectorComponent(secondVector, index)
        if firstComponent is None or secondComponent is None:
            return None
        differences.append(firstComponent - secondComponent)
    return math.sqrt(sum(difference * difference for difference in differences))


def Rms(values: Sequence[float]) -> Optional[float]:
    if not values:
        return None
    return math.sqrt(sum(value * value for value in values) / len(values))


def Median(values: Sequence[float]) -> Optional[float]:
    if not values:
        return None
    return float(statistics.median(values))


def Quantile(values: Sequence[float], quantileLevel: float) -> Optional[float]:
    """Linearly interpolated (type 7) quantile of the values at
    *quantileLevel* (0 to 1), or None if there are none."""
    if not values:
        return None
    sortedValues = sorted(values)
    if len(sortedValues) == 1:
        return float(sortedValues[0])
    position = (len(sortedValues) - 1) * quantileLevel
    lowerIndex = int(math.floor(position))
    upperIndex = int(math.ceil(position))
    if lowerIndex == upperIndex:
        return float(sortedValues[lowerIndex])
    fraction = position - lowerIndex
    return float(sortedValues[lowerIndex] * (1.0 - fraction) + sortedValues[upperIndex] * fraction)


def FirstMetadataValue(metadata: Any, keys: Sequence[str]) -> str:
    """First non-empty metadata value among *keys*, or an empty string."""
    if metadata is None:
        return ""
    for key in keys:
        try:
            value = metadata[key]
            if value not in (None, ""):
                return str(value)
        except Exception as suppressedError:
            ReportSuppressedError("markerResidualInspector.FirstMetadataValue.indexing", suppressedError)
        try:
            value = metadata.get(key)  # type: ignore[attr-defined]
            if value not in (None, ""):
                return str(value)
        except Exception as suppressedError:
            ReportSuppressedError("markerResidualInspector.FirstMetadataValue.getMethod", suppressedError)
    return ""


def GetImageQuality(camera: Any) -> str:
    """Image quality estimate stored in the image metadata
    (`camera.meta`, else `camera.photo.meta`), or an empty string."""
    keys = [
        "Image/Quality",
        "Image/QualityValue",
        "Quality",
        "Photo/Quality",
    ]
    value = FirstMetadataValue(getattr(camera, "meta", None), keys)
    if value:
        return value
    photo = getattr(camera, "photo", None)
    return FirstMetadataValue(getattr(photo, "meta", None), keys)


def GetPixelCount(camera: Any) -> Tuple[Optional[float], Optional[float]]:
    """Numbers of pixel columns and rows of the image, from the interior
    orientation group or, failing that, from `camera.photo`."""
    sensor = getattr(camera, "sensor", None)
    if sensor is not None:
        calibration = getattr(sensor, "calibration", None)
        if calibration is not None:
            pixelColumnCount = getattr(calibration, "width", None)
            pixelRowCount = getattr(calibration, "height", None)
            if pixelColumnCount and pixelRowCount:
                return float(pixelColumnCount), float(pixelRowCount)
        pixelColumnCount = getattr(sensor, "width", None)
        pixelRowCount = getattr(sensor, "height", None)
        if pixelColumnCount and pixelRowCount:
            return float(pixelColumnCount), float(pixelRowCount)
    photo = getattr(camera, "photo", None)
    if photo is not None:
        # Do not load the full image: use its width and height attributes
        # (the numbers of pixel columns and rows) only if camera.photo
        # already exposes them.
        pixelColumnCount = getattr(photo, "width", None)
        pixelRowCount = getattr(photo, "height", None)
        if pixelColumnCount and pixelRowCount:
            return float(pixelColumnCount), float(pixelRowCount)
    return None, None


def ReferenceLocation(marker: Any) -> Any:
    reference = getattr(marker, "reference", None)
    if reference is None:
        return None
    return getattr(reference, "location", None)


def ReferenceAccuracy(marker: Any) -> Any:
    reference = getattr(marker, "reference", None)
    if reference is None:
        return None
    return getattr(reference, "accuracy", None)


def ReferenceEnabled(marker: Any) -> bool:
    reference = getattr(marker, "reference", None)
    if reference is None:
        return False
    try:
        return bool(reference.enabled)
    except Exception as suppressedError:
        ReportSuppressedError("markerResidualInspector.ReferenceEnabled", suppressedError)
        return False


def MarkerRole(marker: Any) -> str:
    """Role of a marker: control (reference location, reference ticked),
    check (reference location, reference unticked), tie-only (no
    reference location), or inspection, an
    inspection marker of the GSD Value Exporter, whose model-derived
    reference coordinates make it neither a control nor a check point
    (IsInspectionMarker), and which the tables leave out."""
    if IsInspectionMarker(marker):
        return "inspection"
    location = ReferenceLocation(marker)
    if location is None:
        return "tie-only"
    if ReferenceEnabled(marker):
        return "control"
    return "check"


def AccuracyComponents(accuracyVector: Any) -> Optional[Tuple[float, float, float]]:
    """The three components of an accuracy vector as floats, or None
    when the vector is missing or cannot be read."""
    if accuracyVector is None:
        return None
    components = []
    for index in range(3):
        component = SafeVectorComponent(accuracyVector, index)
        if component is None:
            return None
        components.append(component)
    return tuple(components)


def AccuracyNorm(accuracyComponents: Any) -> Optional[float]:
    """The root sum of squares of the per-axis a priori standard
    deviations (the values of the Accuracy field), sqrt(sigma_x^2 +
    sigma_y^2 + sigma_z^2), or None when they are not available.  It is
    the root mean square length of a centred error vector with these
    standard deviations, not the standard deviation of its length."""
    if not isinstance(accuracyComponents, (tuple, list)) or not accuracyComponents:
        return None
    return math.sqrt(sum(float(component) ** 2 for component in accuracyComponents))


def ObjectSpaceErrorRatio(objectError: Optional[float], accuracyNorm: Optional[float]) -> Optional[float]:
    """Object-space error divided by the root sum of squares of the a
    priori standard deviations (AccuracyNorm): how far a marker lies
    from the threshold of the object-space status, which is
    _OBJECT_SPACE_STATUS_FACTOR.  None when either is unavailable, and
    infinity for an error above zero with a root sum of squares of
    zero."""
    if objectError is None or accuracyNorm is None:
        return None
    if accuracyNorm <= 0.0:
        return float("inf") if objectError > 0.0 else None
    return objectError / accuracyNorm


def ObjectSpaceStatus(role: str, objectError: Optional[float], accuracyNorm: Optional[float]) -> str:
    """Object-space status of a marker, judged apart from its image
    residuals: whether its object-space error is within twice the root
    sum of squares of its a priori standard deviations
    (_OBJECT_SPACE_STATUS_FACTOR times AccuracyNorm), "not evaluated"
    when either is unavailable, and an empty string for a tie-only
    marker, which has no reference coordinates.  Small image residuals
    do not imply a small object-space error, for example when the
    reference coordinates are wrong.  The comparison is a heuristic, not
    a statistical test: the discrepancy also contains the uncertainty
    of the estimated position, and a comparison of lengths ignores
    anisotropic and correlated uncertainties.  Only under an ideal model
    with three independent normal components of equal standard
    deviation would 0.74 % of the markers exceed twice the root sum of
    squares (39 % the root sum of squares itself)."""
    if role == "tie-only":
        return ""
    if objectError is None or accuracyNorm is None:
        return _OBJECT_SPACE_NOT_EVALUATED
    if objectError <= _OBJECT_SPACE_STATUS_FACTOR * accuracyNorm:
        return _OBJECT_SPACE_WITHIN
    return _OBJECT_SPACE_EXCEEDS


def ObjectSpaceDiagnostic(markerSummary: Dict[str, Any]) -> Dict[str, Any]:
    """Diagnostics row of the kind Object space for a marker summary
    whose object-space error exceeds the threshold of the object-space
    status: the object-space error, the root sum of squares of the a
    priori standard deviations, and their ratio as evidence.  It carries
    no severity, no residual, and no image, because it is not ranked on
    the severity scale of the image residuals and stands for no
    projection."""
    objectError = markerSummary.get("objectError")
    accuracyNorm = AccuracyNorm(markerSummary.get("referenceAccuracy"))
    role = str(markerSummary.get("role", ""))
    return {
        "kind": _DIAGNOSTIC_KIND_OBJECT_SPACE,
        "severity": None,
        "markerKey": int(markerSummary["markerKey"]),
        "marker": markerSummary["marker"],
        "camera": "",
        "role": role,
        "errorPixels": None,
        "objectErrorRatio": markerSummary.get("objectErrorRatio"),
        "evidence": "object-space error {} m, root sum of squares of the a priori standard deviations {} m, "
                    "ratio {}".format(FormatValue(objectError, 4, isLength=True),
                                      FormatValue(accuracyNorm, 4, isLength=True),
                                      FormatValue(markerSummary.get("objectErrorRatio"), 2)),
        "interpretation": "{} point whose object-space error exceeds twice the root sum of squares of its a "
                          "priori standard deviations".format(role.capitalize()),
        "suggestedAction": "Check the reference coordinates and the identity of the marker, then its projections.",
    }


def ObjectSpaceError(chunk: Any, marker: Any) -> Optional[float]:
    """Distance between the reference and the estimated marker position.

    The reference coordinates are read in the CRS of the markers: the
    separate marker CRS of the Reference Settings when one is set
    (chunk.marker_crs), otherwise the chunk CRS, and a coordinate
    transformation brings them into the geocentric coordinates of the
    chunk CRS when the two differ (see MarkerReferenceDiscrepancy).
    With a CRS the distance is evaluated in local east-north-up
    coordinates at the reference point, so the result is metres for
    any CRS, projected or geographic.  Without one it is the plain
    distance in the chunk's world coordinates, which are the reference
    coordinates.  The same plain distance serves a local chunk CRS,
    also used by the markers, whose local east-north-up coordinates
    cannot be built.

    Returns None when the marker has no reference location or no
    estimated position, when the chunk has no transform (internal and
    reference coordinates cannot be compared), when the local
    east-north-up coordinates cannot be built in a CRS that is not
    local (a plain distance would mix degrees and metres), and when the
    marker CRS cannot be related to the chunk CRS.
    """
    referenceLocation = ReferenceLocation(marker)
    position = getattr(marker, "position", None)
    transformMatrix = getattr(getattr(chunk, "transform", None), "matrix", None)
    if referenceLocation is None or position is None or transformMatrix is None:
        return None
    crs = getattr(chunk, "crs", None)
    try:
        estimatedWorld = transformMatrix.mulp(position)
    except Exception as suppressedError:
        ReportSuppressedError("markerResidualInspector.ObjectSpaceError.worldPosition", suppressedError)
        return None
    if MarkerReferenceCoordinateSystem(chunk) is not None:
        try:
            return MarkerReferenceDiscrepancy(chunk, referenceLocation, estimatedWorld).norm()
        except Exception as suppressedError:
            ReportSuppressedError("markerResidualInspector.ObjectSpaceError.localFrame", suppressedError)
            if CrsKind(crs) != "local" or not MarkerReferenceUsesChunkCoordinateSystem(chunk):
                return None
    return VectorDistance(estimatedWorld, referenceLocation)


def VectorCoherence(rows: Sequence[Dict[str, Any]]) -> Optional[float]:
    """
    Ratio between the length of the mean residual vector and the mean residual
    length. Values near 1 mean the residual vectors mostly point in the same
    direction. Values near 0 mean their directions cancel out.
    """
    if not rows:
        return None
    errors = [float(row["errorPixels"]) for row in rows if row.get("errorPixels") is not None]
    if not errors:
        return None
    meanError = sum(errors) / len(errors)
    if meanError <= 0:
        return 0.0
    meanDx = sum(float(row.get("dxPixels", 0.0)) for row in rows) / len(rows)
    meanDy = sum(float(row.get("dyPixels", 0.0)) for row in rows) / len(rows)
    return min(1.0, math.hypot(meanDx, meanDy) / meanError)


def Mean(values: Sequence[float]) -> Optional[float]:
    if not values:
        return None
    return float(sum(values) / len(values))


def ChunkIdentity(chunk: Any) -> Any:
    """Key that tells the chunks of a session apart: the chunk key (the
    chunk label when there is no key).  Marker and camera keys repeat
    across chunks, so review flags are stored per chunk identity.  The
    document path is not part of it, because it changes on the first
    save of a project and on Save As."""
    chunkKey = getattr(chunk, "key", None)
    if chunkKey is None:
        chunkKey = getattr(chunk, "label", "")
    return chunkKey


def ProjectionLabelsByPair(kind: str, records: Sequence[Dict[str, Any]]) -> Dict[Tuple[int, int], Tuple[str, str]]:
    """Marker label and image label, by (marker key, camera key) pair, of
    the selected rows that stand for one marker projection: rows of the
    Projection details table and Projection rows of the Diagnostics
    table.  Summary rows are ignored, because they name only their worst
    projection."""
    labelsByPair: Dict[Tuple[int, int], Tuple[str, str]] = {}
    for row in records:
        standsForProjection = (kind == "projections" or (
            kind == "diagnostics" and row.get("kind") == _DIAGNOSTIC_KIND_PROJECTION))
        if not standsForProjection:
            continue
        markerKey = row.get("markerKey")
        cameraKey = row.get("cameraKey")
        if markerKey is None or cameraKey is None:
            continue
        labelsByPair[(int(markerKey), int(cameraKey))] = (
            str(row.get("marker", "")), str(row.get("camera", "")))
    return labelsByPair


def ProjectionPairsFromRecords(kind: str, records: Sequence[Dict[str, Any]]) -> List[Tuple[int, int]]:
    """Sorted (marker key, camera key) pairs of the selected rows that
    stand for one marker projection (see ProjectionLabelsByPair)."""
    return sorted(ProjectionLabelsByPair(kind, records))


def ReviewFlagForProjection(
    reviewFlags: Dict[Tuple[int, int], Tuple[str, str, str]],
    markerKey: int,
    cameraKey: int,
    markerLabel: str,
    imageLabel: str,
) -> str:
    """Review flag of one marker projection, or an empty string.  A flag
    is stored as (flag, marker label, image label) and counts only while
    both labels still match: a project opened later in the session can
    reuse the chunk, marker, and camera keys."""
    storedFlag = reviewFlags.get((markerKey, cameraKey))
    if not storedFlag:
        return ""
    flag, storedMarkerLabel, storedImageLabel = storedFlag
    if (storedMarkerLabel, storedImageLabel) != (markerLabel, imageLabel):
        return ""
    return flag


def CsvFileNameForKind(kind: str) -> str:
    """File name of the CSV export of one table kind."""
    return "MarkerResidualInspector{}.csv".format(
        _CSV_FILE_WORDS.get(kind, kind.capitalize()))


def RoleFilterComment(role: str, kind: str) -> str:
    """Role filter as recorded in the CSV export of one table kind.  The
    filter applies to marker, projection, and object-space rows only (see
    FilterRows): the image summaries, and the image rows of the
    diagnostics, include the markers of every role, and the comment says
    so."""
    if role == "All":
        return role
    if kind == "cameras":
        return "{} (not applied: the image summaries include the markers of every role)".format(role)
    if kind == "diagnostics":
        return ("{} (marker, projection, and object-space rows only: the image rows include the markers of "
                "every role)".format(role))
    return role


def CsvCommentLines(
    chunkLabel: str,
    thresholdPixels: float,
    validOnly: bool,
    role: str,
    searchText: str,
    aboveOnly: bool,
    kind: str = "",
    markerCoordinateSystemName: str = "",
) -> List[str]:
    """Comment lines that record, at the top of the CSV export of one
    table kind, the chunk, the CRS in which the reference
    coordinates of the markers were read (for the object-space errors),
    and the settings the exported rows depend on."""
    return [
        "# MetaFOCUS Marker Residual Inspector",
        "# Chunk: {}".format(chunkLabel or "(unnamed)"),
        "# Marker reference CRS: {}".format(markerCoordinateSystemName or "(none)"),
        "# Residual threshold (px): {:.3f}".format(thresholdPixels),
        "# Valid projections only: {}".format("yes" if validOnly else "no"),
        "# Role filter: {}".format(RoleFilterComment(role, kind)),
        "# Search filter: {}".format(searchText or "(none)"),
        "# Only rows at or above the threshold: {}".format("yes" if aboveOnly else "no"),
    ]


# -----------------------------------------------------------------------------
# Sortable QTableWidgetItem
# -----------------------------------------------------------------------------


class SortableItem(QtWidgets.QTableWidgetItem):
    def __init__(self, value: Any, decimals: int = 3, recordId: str = "", isLength: bool = False):
        super().__init__(FormatValue(value, decimals=decimals, isLength=isLength))
        self.sortValue = self.MakeSortValue(value)
        if recordId:
            self.setData(userRole, recordId)
        self.setFlags(self.flags() & ~QtItemIsEditable())
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            self.setTextAlignment(QtAlignRight())
        elif isinstance(value, bool):
            self.setTextAlignment(QtAlignCenter())

    @staticmethod
    def MakeSortValue(value: Any) -> Any:
        """Sort key of a table cell: numbers as they are, a numeric triple
        by its root sum of squares, missing values as minus infinity, and
        text in lower case."""
        if value is None:
            return -float("inf")
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, (int, float)):
            if isinstance(value, float) and math.isnan(value):
                return -float("inf")
            return value
        if isinstance(value, (tuple, list)):
            norm = AccuracyNorm(value)
            return -float("inf") if norm is None else norm
        return str(value).lower()

    def __lt__(self, other: QtWidgets.QTableWidgetItem) -> bool:
        if isinstance(other, SortableItem):
            try:
                return self.sortValue < other.sortValue
            except Exception:
                return str(self.sortValue) < str(other.sortValue)
        return super().__lt__(other)


# -----------------------------------------------------------------------------
# Data model and diagnostics
# -----------------------------------------------------------------------------


def DiagnosisSentence(parts: Sequence[str]) -> str:
    """The parts of a diagnosis of a marker or an image, joined by commas,
    with a capital first letter, as every text of the Interpretation
    column: "Suspect image-level issue, edge-of-image residuals
    present"."""
    text = ", ".join(parts)
    return text[:1].upper() + text[1:]


class ResidualDataBuilder:
    def __init__(self, chunk: Any, reviewFlags: Dict[Tuple[int, int], Tuple[str, str, str]]):
        self.chunk = chunk
        self.reviewFlags = reviewFlags
        self.markerByKey: Dict[int, Any] = {}
        self.cameraByKey: Dict[int, Any] = {}

    def BuildProjectionRows(self, validOnly: bool = True) -> List[Dict[str, Any]]:
        """One row per marker projection: measured and predicted image
        positions, residual, and the role of the marker."""
        rows: List[Dict[str, Any]] = []
        markers = getattr(self.chunk, "markers", []) or []

        for marker in markers:
            markerPosition = getattr(marker, "position", None)
            if markerPosition is None:
                continue

            markerKey = int(getattr(marker, "key", -1))
            self.markerByKey[markerKey] = marker

            markerLabel = getattr(marker, "label", str(markerKey))
            markerEnabled = bool(getattr(marker, "enabled", True))
            role = MarkerRole(marker)
            if role == "inspection":
                continue
            referenceEnabled = ReferenceEnabled(marker)
            # Per-axis values in metres of the Accuracy field of a marker
            # with reference coordinates (a priori standard deviations):
            # its own, else the chunk default that applies.
            referenceAccuracy = None
            if role != "tie-only":
                referenceAccuracy = AccuracyComponents(ReferenceAccuracy(marker))
                if referenceAccuracy is None:
                    referenceAccuracy = AccuracyComponents(
                        getattr(self.chunk, "marker_location_accuracy", None))
            objectError = ObjectSpaceError(self.chunk, marker)

            projections = getattr(marker, "projections", None)
            if projections is None:
                continue

            try:
                projectionItems = projections.items()
            except Exception as suppressedError:
                ReportSuppressedError("markerResidualInspector.BuildProjectionRows.projectionItems", suppressedError)
                continue

            for camera, projection in projectionItems:
                if camera is None or projection is None:
                    continue
                cameraTransform = getattr(camera, "transform", None)
                if cameraTransform is None:
                    continue

                projectionValid = bool(getattr(projection, "valid", True))
                if validOnly and not projectionValid:
                    continue

                cameraKey = int(getattr(camera, "key", -1))
                self.cameraByKey[cameraKey] = camera

                try:
                    measured = projection.coord
                    predicted = camera.project(markerPosition)
                    if predicted is None:
                        continue
                    measuredX = float(measured[0])
                    measuredY = float(measured[1])
                    predictedX = float(predicted[0])
                    predictedY = float(predicted[1])
                except Exception as suppressedError:
                    ReportSuppressedError("markerResidualInspector.BuildProjectionRows.reprojection", suppressedError)
                    continue

                residualX = measuredX - predictedX
                residualY = measuredY - predictedY
                errorPixels = math.hypot(residualX, residualY)

                pixelColumnCount, pixelRowCount = GetPixelCount(camera)
                centreDistancePixels = None
                centreDistanceNormalised = None
                if pixelColumnCount and pixelRowCount:
                    centreDistancePixels = math.hypot(measuredX - pixelColumnCount / 2.0,
                                                      measuredY - pixelRowCount / 2.0)
                    halfDiagonal = math.hypot(pixelColumnCount / 2.0, pixelRowCount / 2.0)
                    if halfDiagonal > 0:
                        centreDistanceNormalised = centreDistancePixels / halfDiagonal

                imageLabel = getattr(camera, "label", str(cameraKey))
                reviewFlag = ReviewFlagForProjection(
                    self.reviewFlags, markerKey, cameraKey, markerLabel, imageLabel)

                rows.append({
                    "markerKey": markerKey,
                    "cameraKey": cameraKey,
                    "marker": markerLabel,
                    "camera": imageLabel,
                    "role": role,
                    "markerEnabled": markerEnabled,
                    "referenceEnabled": referenceEnabled,
                    "referenceAccuracy": referenceAccuracy,
                    "objectError": objectError,
                    "measuredX": measuredX,
                    "measuredY": measuredY,
                    "predictedX": predictedX,
                    "predictedY": predictedY,
                    "dxPixels": residualX,
                    "dyPixels": residualY,
                    "errorPixels": errorPixels,
                    "pinned": bool(getattr(projection, "pinned", False)),
                    "valid": projectionValid,
                    "projectionSize": getattr(projection, "size", None),
                    "centreDistancePixels": centreDistancePixels,
                    "centreDistanceNormalised": centreDistanceNormalised,
                    "imageQuality": GetImageQuality(camera),
                    "review": reviewFlag,
                })

        return rows

    def BuildMarkerSummaries(self, projectionRows: Sequence[Dict[str, Any]], thresholdPixels: float) -> List[Dict[str, Any]]:
        """Per-marker summary of the projection residuals (median, maximum,
        count at or above the threshold, coherence) with a diagnosis."""
        grouped: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
        for row in projectionRows:
            grouped[int(row["markerKey"])].append(row)

        summaries: List[Dict[str, Any]] = []
        for markerKey, rows in grouped.items():
            errors = [float(rowRecord["errorPixels"]) for rowRecord in rows]
            highRows = [rowRecord for rowRecord in rows if float(rowRecord["errorPixels"]) >= thresholdPixels]
            worst = max(rows, key=lambda rowRecord: float(rowRecord["errorPixels"]))
            medianPixels = Median(errors)
            rmsPixels = Rms(errors)
            maximumPixels = max(errors) if errors else None
            percentile95Pixels = Quantile(errors, 0.95)
            maximumToMedianRatio = None
            if maximumPixels is not None and medianPixels is not None:
                maximumToMedianRatio = float("inf") if medianPixels <= 1e-9 and maximumPixels > 0 else maximumPixels / max(medianPixels, 1e-9)

            meanDxPixels = Mean([float(rowRecord["dxPixels"]) for rowRecord in rows])
            meanDyPixels = Mean([float(rowRecord["dyPixels"]) for rowRecord in rows])
            coherence = VectorCoherence(rows)

            diagnosis = self.MarkerDiagnosis(rows, thresholdPixels, maximumToMedianRatio, medianPixels, maximumPixels, coherence)

            summaries.append({
                "markerKey": markerKey,
                "cameraKey": int(worst["cameraKey"]),
                "marker": worst["marker"],
                "role": worst["role"],
                "markerEnabled": worst["markerEnabled"],
                "referenceEnabled": worst["referenceEnabled"],
                "referenceAccuracy": worst["referenceAccuracy"],
                "objectError": worst["objectError"],
                "objectErrorRatio": ObjectSpaceErrorRatio(
                    worst["objectError"], AccuracyNorm(worst["referenceAccuracy"])),
                "objectSpaceStatus": ObjectSpaceStatus(
                    worst["role"], worst["objectError"], AccuracyNorm(worst["referenceAccuracy"])),
                "projectionCount": len(rows),
                "aboveThreshold": len(highRows),
                "medianPixels": medianPixels,
                "rmsPixels": rmsPixels,
                "percentile95Pixels": percentile95Pixels,
                "maximumPixels": maximumPixels,
                "maximumToMedianRatio": maximumToMedianRatio,
                "meanDxPixels": meanDxPixels,
                "meanDyPixels": meanDyPixels,
                "vectorCoherence": coherence,
                "worstCamera": worst["camera"],
                "diagnosis": diagnosis,
            })

        summaries.sort(key=lambda rowRecord: (rowRecord.get("aboveThreshold", 0), rowRecord.get("rmsPixels") or 0.0, rowRecord.get("maximumPixels") or 0.0), reverse=True)
        return summaries

    @staticmethod
    def MarkerDiagnosis(
        rows: Sequence[Dict[str, Any]],
        thresholdPixels: float,
        maximumToMedianRatio: Optional[float],
        medianPixels: Optional[float],
        maximumPixels: Optional[float],
        coherence: Optional[float],
    ) -> str:
        """Plain-language diagnosis of one marker from its projection
        residuals and, for a marker with reference coordinates, from its
        object-space error, which is judged apart from the image
        residuals: residuals below the threshold say nothing about the
        object-space error (see ObjectSpaceStatus)."""
        if not rows or maximumPixels is None:
            return "No valid projections"

        highCount = sum(1 for row in rows if float(row["errorPixels"]) >= thresholdPixels)
        role = rows[0].get("role", "")
        objectError = rows[0].get("objectError")
        accuracyNorm = AccuracyNorm(rows[0].get("referenceAccuracy"))

        parts = []
        if highCount == 0:
            parts.append("No image residual at or above the threshold")
        elif maximumToMedianRatio is not None and maximumToMedianRatio >= _SUSPECT_PROJECTION_MAXIMUM_TO_MEDIAN_RATIO and medianPixels is not None and medianPixels < thresholdPixels:
            parts.append("suspect one or a few individual projections with large residuals")
        elif medianPixels is not None and medianPixels >= thresholdPixels:
            parts.append("marker-level inconsistency")
        else:
            parts.append("review the projections at or above the threshold")

        if coherence is not None and coherence >= _SYSTEMATIC_COHERENCE_THRESHOLD and highCount >= _MINIMUM_EXCEEDANCE_COUNT:
            parts.append("residual vectors coherent")

        imageSpaceWeak = medianPixels is not None and medianPixels >= thresholdPixels
        # Judged against the root sum of squares of the marker's own a
        # priori standard deviations, so the rules hold for close-range
        # and aerial blocks alike: a control point fits its reference
        # coordinates within _REFERENCE_FIT_ACCURACY_FACTOR times it (the
        # reference-weighting remark), and a discrepancy is too large
        # beyond _OBJECT_SPACE_STATUS_FACTOR times it, the threshold of the
        # object-space status.
        fitsReference = None
        exceedsStatusThreshold = None
        if objectError is not None and accuracyNorm is not None:
            fitsReference = objectError <= _REFERENCE_FIT_ACCURACY_FACTOR * accuracyNorm
            exceedsStatusThreshold = objectError > _OBJECT_SPACE_STATUS_FACTOR * accuracyNorm
        if role == "control":
            if fitsReference and imageSpaceWeak:
                parts.append("possible reference-weighting tension")
            elif exceedsStatusThreshold:
                parts.append("object-space discrepancy " + _OBJECT_SPACE_EXCEEDS)
        elif role == "check":
            if imageSpaceWeak and exceedsStatusThreshold:
                parts.append("check point weak in object and image space")
            elif imageSpaceWeak:
                parts.append("check point weak in image space")
            elif exceedsStatusThreshold:
                parts.append("check point weak in object space")

        return DiagnosisSentence(parts)

    def BuildCameraSummaries(self, projectionRows: Sequence[Dict[str, Any]], thresholdPixels: float) -> List[Dict[str, Any]]:
        """Per-image summary of the marker projection residuals with a
        diagnosis."""
        grouped: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
        for row in projectionRows:
            grouped[int(row["cameraKey"])].append(row)

        summaries: List[Dict[str, Any]] = []
        for cameraKey, rows in grouped.items():
            errors = [float(rowRecord["errorPixels"]) for rowRecord in rows]
            highRows = [rowRecord for rowRecord in rows if float(rowRecord["errorPixels"]) >= thresholdPixels]
            worst = max(rows, key=lambda rowRecord: float(rowRecord["errorPixels"]))
            medianPixels = Median(errors)
            rmsPixels = Rms(errors)
            maximumPixels = max(errors) if errors else None
            percentile95Pixels = Quantile(errors, 0.95)
            meanDxPixels = Mean([float(rowRecord["dxPixels"]) for rowRecord in rows])
            meanDyPixels = Mean([float(rowRecord["dyPixels"]) for rowRecord in rows])
            coherence = VectorCoherence(rows)
            meanCentreDistanceNormalised = Mean([float(rowRecord["centreDistanceNormalised"]) for rowRecord in rows if rowRecord.get("centreDistanceNormalised") is not None])
            diagnosis = self.CameraDiagnosis(rows, thresholdPixels, medianPixels, maximumPixels, coherence)

            summaries.append({
                "cameraKey": cameraKey,
                "markerKey": int(worst["markerKey"]),
                "camera": worst["camera"],
                "projectionCount": len(rows),
                "aboveThreshold": len(highRows),
                "medianPixels": medianPixels,
                "rmsPixels": rmsPixels,
                "percentile95Pixels": percentile95Pixels,
                "maximumPixels": maximumPixels,
                "meanDxPixels": meanDxPixels,
                "meanDyPixels": meanDyPixels,
                "vectorCoherence": coherence,
                "meanCentreDistanceNormalised": meanCentreDistanceNormalised,
                "imageQuality": worst.get("imageQuality", ""),
                "worstMarker": worst["marker"],
                "diagnosis": diagnosis,
            })

        summaries.sort(key=lambda rowRecord: (rowRecord.get("aboveThreshold", 0), rowRecord.get("rmsPixels") or 0.0, rowRecord.get("maximumPixels") or 0.0), reverse=True)
        return summaries

    @staticmethod
    def CameraDiagnosis(
        rows: Sequence[Dict[str, Any]],
        thresholdPixels: float,
        medianPixels: Optional[float],
        maximumPixels: Optional[float],
        coherence: Optional[float],
    ) -> str:
        """Plain-language diagnosis of one image from its marker residuals."""
        if not rows or maximumPixels is None:
            return "No valid marker projections"
        highCount = sum(1 for row in rows if float(row["errorPixels"]) >= thresholdPixels)
        if highCount == 0:
            # Scoped to what was tested: the image residuals of the
            # markers, not the object-space errors of the markers.
            return "No marker residual at or above the threshold"

        fraction = highCount / max(1, len(rows))
        parts = []
        if highCount >= _MINIMUM_EXCEEDANCE_COUNT and fraction >= 0.30:
            parts.append("suspect image-level issue")
        elif medianPixels is not None and medianPixels < thresholdPixels:
            parts.append("suspect one or a few individual projections with large residuals in this image")
        else:
            parts.append("several marker observations inconsistent")

        if coherence is not None and coherence >= _SYSTEMATIC_COHERENCE_THRESHOLD and highCount >= _MINIMUM_EXCEEDANCE_COUNT:
            parts.append("systematic residual direction")

        edgeRows = [row for row in rows if row.get("centreDistanceNormalised") is not None and float(row["centreDistanceNormalised"]) > 0.75 and float(row["errorPixels"]) >= thresholdPixels]
        if edgeRows:
            parts.append("edge-of-image residuals present")

        return DiagnosisSentence(parts)

    def BuildDiagnostics(
        self,
        projectionRows: Sequence[Dict[str, Any]],
        markerSummaries: Sequence[Dict[str, Any]],
        cameraSummaries: Sequence[Dict[str, Any]],
        thresholdPixels: float,
    ) -> List[Dict[str, Any]]:
        """Issues across markers and images, ranked from most to least
        severe."""
        diagnostics: List[Dict[str, Any]] = []

        for row in projectionRows:
            errorPixels = float(row["errorPixels"])
            if errorPixels < thresholdPixels:
                continue
            reviewFlag = row.get("review", "")
            if reviewFlag == "visually correct":
                interpretation = "High residual but visually accepted in this session"
                suggestion = ("Do not invalidate automatically. Inspect the other projections of this marker, "
                              "the other markers in this image, and the reference weighting.")
            elif reviewFlag in ("wrong placement", "occluded", "blurred", "ambiguous centre", "low target quality"):
                interpretation = f"High residual and manually flagged as {reviewFlag}"
                suggestion = "Consider invalidating this projection or reducing its influence after checking neighbouring views."
            else:
                interpretation = "High individual projection residual"
                suggestion = "Open the image, inspect the marker placement, and compare with the marker and image summaries."

            diagnostics.append({
                "kind": _DIAGNOSTIC_KIND_PROJECTION,
                "severity": errorPixels / max(thresholdPixels, 1e-9),
                "markerKey": int(row["markerKey"]),
                "cameraKey": int(row["cameraKey"]),
                "marker": row["marker"],
                "camera": row["camera"],
                "role": row.get("role", ""),
                "errorPixels": errorPixels,
                "evidence": f"dx={row['dxPixels']:.3f} px, dy={row['dyPixels']:.3f} px, review={reviewFlag or 'unreviewed'}",
                "interpretation": interpretation,
                "suggestedAction": suggestion,
            })

        for row in markerSummaries:
            highCount = int(row.get("aboveThreshold", 0) or 0)
            if highCount <= 0:
                continue
            diagnosis = row.get("diagnosis", "")
            severity = (float(row.get("rmsPixels") or 0.0) / max(thresholdPixels, 1e-9)) + highCount * 0.25
            diagnostics.append({
                "kind": "Marker",
                "severity": severity,
                "markerKey": int(row["markerKey"]),
                "cameraKey": int(row["cameraKey"]),
                "marker": row["marker"],
                "camera": row["worstCamera"],
                "role": row.get("role", ""),
                "errorPixels": row.get("rmsPixels"),
                "evidence": f"{highCount}/{row['projectionCount']} projections at or above the threshold, maximum {FormatValue(row.get('maximumPixels'))} px, median {FormatValue(row.get('medianPixels'))} px",
                "interpretation": diagnosis,
                "suggestedAction": "Inspect the Projection details rows of this marker before invalidating any projection.",
            })

        for row in cameraSummaries:
            highCount = int(row.get("aboveThreshold", 0) or 0)
            if highCount <= 0:
                continue
            diagnosis = row.get("diagnosis", "")
            severity = (float(row.get("rmsPixels") or 0.0) / max(thresholdPixels, 1e-9)) + highCount * 0.25
            diagnostics.append({
                "kind": "Image",
                "severity": severity,
                "markerKey": int(row["markerKey"]),
                "cameraKey": int(row["cameraKey"]),
                "marker": row["worstMarker"],
                "camera": row["camera"],
                "errorPixels": row.get("rmsPixels"),
                "evidence": f"{highCount}/{row['projectionCount']} marker projections at or above the threshold, vector coherence {FormatValue(row.get('vectorCoherence'))}",
                "interpretation": diagnosis,
                "suggestedAction": "Inspect the image quality, tie-point support, orientation, and calibration behaviour of this image.",
            })

        diagnostics.sort(key=lambda rowRecord: float(rowRecord.get("severity") or 0.0), reverse=True)

        # Markers whose object-space error exceeds the threshold of the
        # object-space status, whatever their image residuals: a group of
        # their own after the ranked rows, sorted by the ratio, because an
        # object-space error has no place on the severity scale of the
        # image residuals.
        objectSpaceDiagnostics = [ObjectSpaceDiagnostic(row) for row in markerSummaries
                                  if row.get("objectSpaceStatus") == _OBJECT_SPACE_EXCEEDS]
        objectSpaceDiagnostics.sort(key=lambda rowRecord: float(rowRecord.get("objectErrorRatio") or 0.0),
                                    reverse=True)
        diagnostics.extend(objectSpaceDiagnostics)
        return diagnostics


# -----------------------------------------------------------------------------
# GUI
# -----------------------------------------------------------------------------


_ABOUT_HTML = """
<h2>Marker Residual Inspector</h2>
<p><b>Version:</b> 1.0.0 (2026-11-18)<br>
<b>Author:</b> Geert J. Verhoeven
(ORCID: <a href="https://orcid.org/0000-0003-4825-9604">
0000-0003-4825-9604</a>)<br>
<b>Contact:</b>
<a href="mailto:geert.verhoeven@univie.ac.at">
geert.verhoeven@univie.ac.at</a><br>
<b>Licence:</b> GNU General Public License, version 3 or any later version (GPL-3.0-or-later),
with an additional permission to convey MetaFOCUS combined with Agisoft Metashape and its
Python module (see LICENSE and ADDITIONAL_PERMISSION.txt in the MetaFOCUS ZIP file)</p>

<h3>What this tool does</h3>
<p>This tool inspects the reprojection residuals of all markers in all images of the active chunk,
without the need to open each marker&rsquo;s <i>Show Info</i> panel individually. It computes the
per-image reprojection residuals as:<br>
<code>residual = measured &minus; camera.project(marker.position)</code></p>

<p>This is the opposite sign of the residual of a 2D tie point in the Interior Orientation Viewer
and the Gradual Selection Filter (i.e. the projected minus the measured position). For the same
displacement, the residual vectors of the two conventions point in opposite directions. Those tools
compute the projected position with Camera.error, which applies the camera motion of an image with
rolling-shutter compensation. For such an image the two conventions can also differ by more than the
sign when the residual is not zero. This is because Camera.error predicts the position with the
camera pose of the image row of the measured position, whereas camera.project finds the image row of
the projected position (checked in Metashape 2.3.2). Without rolling-shutter compensation they
differ only by the sign.</p>

<p>A residual counts as high when it is at or above the <i>Residual threshold</i>. The tool reads
the active chunk again when <i>Refresh</i> is clicked, when the residual threshold or the <i>Valid
projections only</i> option changes, and when the tool is opened again from the menu. When no chunk
is active, the tables are emptied.</p>

<p>Every refresh writes the chunk, the residual threshold, the marker projections, the markers by
role, the images, the object-space status, and the rows of the Diagnostics table to the Console
pane. When nothing else has changed since the previous refresh, a change of the residual threshold
writes one line with the new counts. The refresh after a review flag writes nothing when nothing has
changed. A typed residual threshold takes effect on pressing Enter or on leaving the field. The
actions of the tool, its messages, and its errors are echoed to the Console pane as well, but not a
change of tab.</p>

<h3>The four tabs</h3>
<p>The tool has four tabs:</p>
<ul>
<li><b>Marker summary:</b> The tab groups the residuals by marker. Sort by &lsquo;RMS (px)&rsquo;
(root mean square) or &lsquo;Maximum (px)&rsquo; to find markers that behave badly overall.</li>
<li><b>Projection details:</b> The tab has one row per marker&times;image observation. This is the
core inspection tab: sort by &lsquo;Error (px)&rsquo; and double-click a row to open the image.</li>
<li><b>Image summary:</b> The tab groups the residuals by image. It reveals images where several
markers have high residuals (suspect image orientation or calibration).</li>
<li><b>Diagnostics:</b> The tab gives a prioritised triage list of the image residuals, combining
projection, marker, and image patterns with severity ranking and suggested actions. The triage list
is followed by the markers whose object-space error exceeds twice the root sum of squares of their a
priori standard deviations, as a group of their own.</li>
</ul>

<h3>Columns</h3>
<p>The tables include the following columns:</p>
<ul>
<li><b>Role:</b> control (reference coordinates, reference ticked),
check (reference coordinates, reference unticked), or tie-only (no
reference coordinates).  The inspection markers of the GSD Value
Exporter, whose reference coordinates come from the surface model, are
left out of the tables.</li>
<li><b>Object-space error (m):</b> 3D distance between the estimated marker position and its
reference coordinates, evaluated in a local east-north-up coordinate reference system (CRS) at the
reference point. The distance is therefore in metres in a projected and in a geographic CRS alike.
The reference coordinates are read in the marker CRS: the separate CRS for markers of the Reference
Settings when one is set, otherwise the chunk CRS. When the two differ, a coordinate operation from
the marker CRS to the chunk CRS brings the reference coordinates into the world coordinates of the
chunk. Exported CSV files name the marker CRS in the comment line &lsquo;Marker reference CRS&rsquo;.
Without a CRS, and in a local CRS that the markers share with the chunk, the error is the plain
distance in the world coordinates of the chunk. For a control point, the object-space error is a
residual of the adjustment, whereas for a check point it is a check discrepancy against reference
coordinates that took no part in it. Both include the uncertainty of the reference coordinates (see
&lsquo;Measurement error, residual, and check discrepancy&rsquo; in the terminology). The cell stays
empty when the chunk has no transform yet, and when the local east-north-up CRS cannot be built for
a CRS that is not local. It also stays empty when a local CRS would have to be related to a
geographic or projected one.</li>
<li><b>Accuracy (m):</b> the values of the marker&rsquo;s &lsquo;Accuracy&rsquo; field in the
Reference pane, or the chunk&rsquo;s default marker accuracy when the marker has none, read as the a
priori standard deviation of its reference coordinates per axis.</li>
<li><b>Object-space error/root sum of squares:</b> the object-space error divided by the root sum of
squares of the per-axis a priori standard deviations (the &lsquo;Accuracy&rsquo; values), &radic;(&sigma;<sub>x</sub>&sup2;
+ &sigma;<sub>y</sub>&sup2; + &sigma;<sub>z</sub>&sup2;). This ratio shows how far a marker lies
from the threshold of the object-space status. This root sum of squares is the RMS length of a
centred error vector with these standard deviations, not the standard deviation of its length.</li>
<li><b>Object-space status:</b> for a marker with reference
coordinates, whether its object-space error is within twice the root
sum of squares of its a priori standard deviations (a ratio of at most
2).  The status is judged apart from the image residuals.  A marker whose image
residuals are all below the threshold can still lie far from its
reference coordinates, for example when these are wrong.  Comparing
the length of the discrepancy with this threshold is a heuristic, not
a statistical test.  The discrepancy also contains the uncertainty of
the estimated position, anisotropic or correlated uncertainties are
not taken into account, and no coverage probability is attached to
it.  In an ideal model, the three components of the
discrepancy are independent and normally distributed with the a
priori standard deviations, all three equal.  Only under such a model would 0.74&nbsp;% of the
markers exceed twice the root sum of squares, and 39&nbsp;% the root
sum of squares itself.  These percentages are a property of that model, not an expected
rate.</li>
</ul>
<p>Lengths in metres are shown with four decimals (0.1&nbsp;mm) and
written to CSV files with six.  A length with
fewer than four significant digits in these decimals gets four: in
exponent notation below 10<sup>&minus;4</sup> in the tables, and with
more decimals in the CSV files.  An object-space error of
2&nbsp;&micro;m thus reads 2.000e-06 and 0.000002000, not 0.0000 and
0.000002.</p>

<h3>Role filter</h3>
<p>The role filter applies to the markers, the projections, and their
diagnostics.  The image summaries, and the image rows of the
diagnostics, always include the markers of every role.</p>

<h3>Important interpretation note</h3>
<p>A large image residual is a <b>symptom</b>, not an automatic
instruction to delete a marker projection.  Common causes
include:</p>
<ul>
<li>Incorrect marker placement in the image</li>
<li>Marker occluded, blurred, or at the image edge</li>
<li>Weak image orientation (few tie points, poor geometry)</li>
<li>Tension between reference coordinates and image observations
(inconsistent reference coordinates, or reference coordinates weighted
too strongly against the image observations)</li>
<li>Residual lens distortion not captured by the camera model</li>
</ul>
<p>The marker placement should always be inspected visually before the validity of a projection is
changed.</p>

<h3>Review flags</h3>
<p>Review flags are <b>session-only</b> annotations: they are kept while Metashape runs, also when
the window is closed and the tool is opened again from the menu. They are included in CSV exports
but are not written into the Metashape project. They serve to document the inspection decisions
(e.g. &lsquo;visually correct&rsquo;, &lsquo;wrong placement&rsquo;, or &lsquo;occluded&rsquo;)
before any changes are made.</p>
<p>A flag belongs to one marker projection in one chunk.  It is
stored with the chunk key and with the marker and image labels, so
it survives switching between chunks and saving the project under
another name (<i>Save As</i>).  Renaming the marker or the image
drops its flags, and a project opened later in the session whose
keys are the same but whose labels differ does not inherit them.
Flags apply only to rows that stand for one projection: rows of the
<i>Projection details</i> tab and Projection rows of the
<i>Diagnostics</i> tab.</p>

<h3>Invalidating projections</h3>
<p><i>Invalidate selected projection(s)</i> acts only on rows that
stand for one projection, as review flags do.  After a confirmation
it sets <code>projection.valid = False</code> for each selected
projection and then updates the chunk transform from the reference
data, as <i>Update</i> in the Reference pane does.  Run
<i>Optimize Cameras</i> again afterwards if needed.</p>

<h3>CSV export</h3>
<p><i>Export active CSV</i> writes the visible rows of the active tab. <i>Export all CSVs</i> writes
the visible rows of all four tabs to MarkerResidualInspectorMarkers.csv,
MarkerResidualInspectorProjections.csv, MarkerResidualInspectorImages.csv, and
MarkerResidualInspectorDiagnostics.csv in a chosen folder. Each file starts with comment lines (#)
that record the chunk, the residual threshold, and the filters. The role filter line states where
the filter does not apply. The option <i>Delimiter and decimal separator</i> chooses a comma with a
decimal point (1.25), a semicolon with a decimal comma (1,25), or a tab with a decimal point. The
semicolon with the decimal comma suits Excel with regional settings that use the decimal comma, for
example German or French. Numbers are written with six decimals, lengths with at least four
significant digits, and the chosen decimal separator, whereas the comment lines and the text cells,
such as marker and image labels, keep their text unchanged.</p>

<h3>Diagnostic logic</h3>
<p>The tool uses simple pattern-detection rules:</p>
<ul>
<li><b>Maximum-to-median ratio:</b> If the maximum/median ratio &ge; 5
and the median is below the threshold, suspect individual projections
with large residuals. The rule is a heuristic that points to suspect
projections, not a statistical outlier test (ISO 5725-1:2023, 3.7).</li>
<li><b>Vector coherence:</b> The rule looks at markers and images with
at least three projections at or above the threshold.  If the residual
vectors of all their projections mostly point in the same direction
(coherence &ge; 0.70), they suggest a systematic effect to check, not a
diagnosis.</li>
<li><b>Image-level issue:</b> If at least three marker projections of
an image, and at least 30&nbsp;% of them, are at or above the
threshold, suspect an issue of the image itself (orientation or
calibration).</li>
<li><b>Edge-of-image:</b> Residuals at or above the threshold at a
normalised centre distance &gt; 0.75 are flagged, because residual
distortion or weak coverage by observations can have larger effects
there.</li>
<li><b>Reference weighting:</b> A control marker whose object-space
error is at most the root sum of squares of its a priori standard
deviations, while its median residual is at or above the threshold,
points to possible reference-weighting tension.  A ratio of at most 1
between that error and that root sum of squares is a stricter test than
that of the object-space status.</li>
<li><b>Check points:</b> A check point whose median residual is at or
above the threshold and whose object-space error exceeds twice the root
sum of squares of its a priori standard deviations is reported as weak
in object and image space.  When only the median residual is at or above
the threshold, the check point is reported as weak in image space, and
when only the object-space error exceeds that limit, as weak in object
space.</li>
<li><b>Object space:</b> A control point whose object-space error
exceeds twice the root sum of squares of its a priori standard
deviations is reported as such, whatever its image residuals.  A
marker without image residuals at or above the threshold reads <i>No
image residual at or above the threshold</i>.  An image without
such marker residuals reads <i>No marker residual at or above the
threshold</i>.  Both statements concern the image residuals only.
The Diagnostics tab ranks the image
residuals at or above the threshold by severity.  It then lists every
marker whose object-space error exceeds twice the root sum of squares
of its a priori standard deviations as an entry of the kind
<i>Object space</i>.  A marker gets this entry whatever its image residuals, with the
object-space error, the root sum of squares, and their ratio as
evidence.  These entries form a group of their own, sorted by the
ratio, because an object-space error has no place on the severity
scale of the image residuals.</li>
</ul>
<p><b>Limitation of the per-marker coherence:</b> the residuals of one
marker come from different images, each with its own pixel axes.  The
same systematic effect (a mislocated reference point, a consistent
centring bias) is coherent in a block of equally rotated images.  However, the effect
can cancel out in a convergent close-range block with varied roll
angles or with mixed portrait and landscape images.  The coherence in
the image summary, whose residuals share one set of pixel axes, is
more dependable.</p>
"""


class MarkerResidualInspectorDialog(QtWidgets.QDialog):
    # How the running refresh is echoed to the Console (RefreshConsoleOutput):
    # None for the block, "threshold" after a change of the threshold, and
    # "quiet" after a review flag.  The block of the previous refresh.
    _refreshConsoleMode = None
    _previousRefreshConsoleLines = None
    # The search text echoed last (_OnSearchEditingFinished).
    _lastEchoedSearch = ""

    markerColumns = [
        ("marker", "Marker", 0),
        ("role", "Role", 0),
        ("markerEnabled", "Enabled", 0),
        ("referenceEnabled", "Reference enabled", 0),
        ("projectionCount", "Projections", 0),
        ("aboveThreshold", "At or above threshold", 0),
        ("medianPixels", "Median (px)", 3),
        ("rmsPixels", "RMS (px)", 3),
        ("percentile95Pixels", "95th percentile (px)", 3),
        ("maximumPixels", "Maximum (px)", 3),
        ("maximumToMedianRatio", "Maximum/median ratio", 2),
        ("meanDxPixels", "Mean dx (px)", 3),
        ("meanDyPixels", "Mean dy (px)", 3),
        ("vectorCoherence", "Vector coherence", 3),
        ("objectError", "Object-space error (m)", 4),
        ("referenceAccuracy", "Accuracy (m)", 4),
        ("objectErrorRatio", "Object-space error/root sum of squares", 2),
        ("objectSpaceStatus", "Object-space status", 0),
        ("worstCamera", "Worst image", 0),
        ("diagnosis", "Diagnosis", 0),
    ]

    projectionColumns = [
        ("marker", "Marker", 0),
        ("camera", "Image", 0),
        ("role", "Role", 0),
        ("errorPixels", "Error (px)", 3),
        ("dxPixels", "dx (px)", 3),
        ("dyPixels", "dy (px)", 3),
        ("measuredX", "Measured x (px)", 2),
        ("measuredY", "Measured y (px)", 2),
        ("predictedX", "Projected x (px)", 2),
        ("predictedY", "Projected y (px)", 2),
        ("pinned", "Pinned", 0),
        ("valid", "Valid", 0),
        ("centreDistancePixels", "Centre distance (px)", 1),
        ("centreDistanceNormalised", "Normalised centre distance", 3),
        ("imageQuality", "Image quality", 0),
        ("review", "Review flag", 0),
    ]

    cameraColumns = [
        ("camera", "Image", 0),
        ("projectionCount", "Marker projections", 0),
        ("aboveThreshold", "At or above threshold", 0),
        ("medianPixels", "Median (px)", 3),
        ("rmsPixels", "RMS (px)", 3),
        ("percentile95Pixels", "95th percentile (px)", 3),
        ("maximumPixels", "Maximum (px)", 3),
        ("meanDxPixels", "Mean dx (px)", 3),
        ("meanDyPixels", "Mean dy (px)", 3),
        ("vectorCoherence", "Vector coherence", 3),
        ("meanCentreDistanceNormalised", "Mean normalised centre distance", 3),
        ("imageQuality", "Image quality", 0),
        ("worstMarker", "Worst marker", 0),
        ("diagnosis", "Diagnosis", 0),
    ]

    diagnosticColumns = [
        ("kind", "Kind", 0),
        ("severity", "Severity", 2),
        ("marker", "Marker", 0),
        ("camera", "Image", 0),
        ("errorPixels", "Error or RMS (px)", 3),
        ("evidence", "Evidence", 0),
        ("interpretation", "Interpretation", 0),
        ("suggestedAction", "Suggested action", 0),
    ]

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("Marker Residual Inspector")
        self.setWindowFlags(
            self.windowFlags()
            & ~QtCore.Qt.WindowContextHelpButtonHint)
        self.resize(1500, 900)

        self.chunk = None
        # Review flags of the chunk shown (one dictionary per chunk
        # identity, because marker and camera keys repeat across chunks),
        # each stored as (flag, marker label, image label).
        self.reviewFlagsByChunk: Dict[Any, Dict[Tuple[int, int], Tuple[str, str, str]]] = {}
        self.reviewFlags: Dict[Tuple[int, int], Tuple[str, str, str]] = {}
        self.markerByKey: Dict[int, Any] = {}
        self.cameraByKey: Dict[int, Any] = {}
        self.records: Dict[str, List[Dict[str, Any]]] = {
            "markers": [],
            "projections": [],
            "cameras": [],
            "diagnostics": [],
        }
        self.visibleRecords: Dict[str, List[Dict[str, Any]]] = {
            "markers": [],
            "projections": [],
            "cameras": [],
            "diagnostics": [],
        }

        self._BuildUi()
        self.Refresh()

    # ------------------------------------------------------------------ GUI construction

    def _BuildUi(self) -> None:
        layout = QtWidgets.QVBoxLayout(self)

        controlsLayout = QtWidgets.QHBoxLayout()

        self.thresholdSpinBox = QtWidgets.QDoubleSpinBox()
        self.thresholdSpinBox.setRange(0.0, 1000.0)
        self.thresholdSpinBox.setDecimals(3)
        self.thresholdSpinBox.setSingleStep(0.1)
        self.thresholdSpinBox.setValue(_DEFAULT_THRESHOLD_PIXELS)
        self.thresholdSpinBox.setSuffix(" px")
        # A typed threshold applies when typing ends (Enter or leaving the
        # field), not at every keystroke.
        self.thresholdSpinBox.setKeyboardTracking(False)
        controlsLayout.addWidget(QtWidgets.QLabel("Residual threshold:"))
        controlsLayout.addWidget(self.thresholdSpinBox)

        self.validOnlyCheckBox = QtWidgets.QCheckBox("Valid projections only")
        self.validOnlyCheckBox.setChecked(True)
        controlsLayout.addWidget(self.validOnlyCheckBox)

        self.showAboveCheckBox = QtWidgets.QCheckBox("Show only rows at or above the threshold")
        self.showAboveCheckBox.setChecked(False)
        controlsLayout.addWidget(self.showAboveCheckBox)

        self.highlightCheckBox = QtWidgets.QCheckBox("Highlight rows at or above the threshold")
        self.highlightCheckBox.setChecked(True)
        self.highlightCheckBox.setToolTip(
            "Shows rows with residuals at or above the threshold\n"
            "in bold red so that they stand out.")
        controlsLayout.addWidget(self.highlightCheckBox)

        controlsLayout.addWidget(QtWidgets.QLabel("Role:"))
        self.roleComboBox = QtWidgets.QComboBox()
        self.roleComboBox.addItems(["All", "control", "check", "tie-only"])
        self.roleComboBox.setToolTip(
            "Shows the markers, projections, and the marker, projection,\n"
            "and object-space diagnostics of one role. The image\n"
            "summaries and their diagnostics always include the\n"
            "markers of every role.")
        controlsLayout.addWidget(self.roleComboBox)

        controlsLayout.addWidget(QtWidgets.QLabel("Search:"))
        self.searchEdit = QtWidgets.QLineEdit()
        self.searchEdit.setPlaceholderText("marker or image label")
        controlsLayout.addWidget(self.searchEdit, stretch=1)

        self.refreshButton = QtWidgets.QPushButton("Refresh")
        controlsLayout.addWidget(self.refreshButton)

        layout.addLayout(controlsLayout)

        self.tabs = QtWidgets.QTabWidget()
        self.markerTable = self.CreateTable()
        self.projectionTable = self.CreateTable()
        self.cameraTable = self.CreateTable()
        self.diagnosticTable = self.CreateTable()

        self.tabs.addTab(self.markerTable, "Marker summary")
        self.tabs.addTab(self.projectionTable, "Projection details")
        self.tabs.addTab(self.cameraTable, "Image summary")
        self.tabs.addTab(self.diagnosticTable, "Diagnostics")
        layout.addWidget(self.tabs, stretch=1)

        actionsLayout = QtWidgets.QHBoxLayout()
        self.openPhotoButton = QtWidgets.QPushButton("Open selected image")
        self.selectMarkerButton = QtWidgets.QPushButton("Select marker(s)")
        self.photosFilterButton = QtWidgets.QPushButton("Filter Photos pane to selected image(s)")
        self.resetPhotosFilterButton = QtWidgets.QPushButton("Reset Photos pane filter")
        self.reviewComboBox = QtWidgets.QComboBox()
        self.reviewComboBox.addItems([
            "",
            "visually correct",
            "wrong placement",
            "ambiguous centre",
            "occluded",
            "blurred",
            "low target quality",
            "keep/investigate geometry",
        ])
        self.applyReviewButton = QtWidgets.QPushButton("Apply review flag")
        self.invalidateButton = QtWidgets.QPushButton("Invalidate selected projection(s)")
        self.exportActiveButton = QtWidgets.QPushButton("Export active CSV")
        self.exportAllButton = QtWidgets.QPushButton("Export all CSVs")
        self.delimiterComboBox = QtWidgets.QComboBox()
        FillCsvFormatComboBox(self.delimiterComboBox)
        self.aboutButton = QtWidgets.QPushButton("About")
        self.closeButton = QtWidgets.QPushButton("Close")

        actionsLayout.addWidget(self.openPhotoButton)
        actionsLayout.addWidget(self.selectMarkerButton)
        actionsLayout.addWidget(self.photosFilterButton)
        actionsLayout.addWidget(self.resetPhotosFilterButton)
        actionsLayout.addStretch(1)
        actionsLayout.addWidget(QtWidgets.QLabel("Review:"))
        actionsLayout.addWidget(self.reviewComboBox)
        actionsLayout.addWidget(self.applyReviewButton)
        actionsLayout.addWidget(self.invalidateButton)
        actionsLayout.addWidget(QtWidgets.QLabel(CSV_FORMAT_ROW_LABEL_TEXT))
        actionsLayout.addWidget(self.delimiterComboBox)
        actionsLayout.addWidget(self.exportActiveButton)
        actionsLayout.addWidget(self.exportAllButton)
        actionsLayout.addWidget(self.aboutButton)
        actionsLayout.addWidget(self.closeButton)
        layout.addLayout(actionsLayout)

        self.statusLabel = QtWidgets.QLabel("")
        layout.addWidget(self.statusLabel)

        self.refreshButton.clicked.connect(self.Refresh)
        self.thresholdSpinBox.valueChanged.connect(self._OnThresholdChanged)
        self.validOnlyCheckBox.stateChanged.connect(self.Refresh)
        self.showAboveCheckBox.stateChanged.connect(self.PopulateTables)
        self.highlightCheckBox.stateChanged.connect(self.PopulateTables)
        self.roleComboBox.currentTextChanged.connect(self.PopulateTables)
        self.searchEdit.textChanged.connect(self.PopulateTables)
        # The Console lines of the view filters come from the signals of
        # the user (clicked, activated, editingFinished), so that a filter
        # is echoed once per change, not per keystroke.
        self.showAboveCheckBox.clicked.connect(self._OnShowAboveClicked)
        self.highlightCheckBox.clicked.connect(self._OnHighlightClicked)
        self.roleComboBox.activated.connect(self._OnRoleActivated)
        self.searchEdit.editingFinished.connect(self._OnSearchEditingFinished)
        self.openPhotoButton.clicked.connect(self.OpenSelectedPhoto)
        self.selectMarkerButton.clicked.connect(self.SelectMarkersFromSelection)
        self.photosFilterButton.clicked.connect(self.FilterPhotosPaneFromSelection)
        self.resetPhotosFilterButton.clicked.connect(self.ResetPhotosPaneFilter)
        self.applyReviewButton.clicked.connect(self.ApplyReviewFlag)
        self.invalidateButton.clicked.connect(self.InvalidateSelectedProjections)
        self.exportActiveButton.clicked.connect(self.ExportActiveCsv)
        self.exportAllButton.clicked.connect(self.ExportAllCsvs)
        self.aboutButton.clicked.connect(self._OnAbout)
        self.closeButton.clicked.connect(self.close)

        for table in [self.markerTable, self.projectionTable, self.cameraTable, self.diagnosticTable]:
            table.doubleClicked.connect(self.OpenSelectedPhoto)

        # Enter in a field, for example a typed threshold, must click no
        # button: a default button (Refresh) would refresh a second time.
        for button in (self.refreshButton, self.openPhotoButton, self.selectMarkerButton, self.photosFilterButton,
                       self.resetPhotosFilterButton, self.applyReviewButton, self.invalidateButton,
                       self.exportActiveButton, self.exportAllButton, self.aboutButton, self.closeButton):
            button.setAutoDefault(False)
            button.setDefault(False)

    def _OnAbout(self):
        aboutDialog = QtWidgets.QDialog(self)
        aboutDialog.setWindowTitle(
            "About: Marker Residual Inspector")
        aboutDialog.setWindowFlags(
            aboutDialog.windowFlags()
            & ~QtCore.Qt.WindowContextHelpButtonHint)
        aboutDialog.resize(540, 560)
        aboutLayout = QtWidgets.QVBoxLayout(aboutDialog)
        textBrowser = QtWidgets.QTextBrowser()
        textBrowser.setOpenExternalLinks(True)
        textBrowser.setHtml(_ABOUT_HTML)
        aboutLayout.addWidget(textBrowser)
        closeButton = QtWidgets.QPushButton("Close")
        closeButton.clicked.connect(aboutDialog.close)
        buttonRow = QtWidgets.QHBoxLayout()
        buttonRow.addStretch()
        buttonRow.addWidget(closeButton)
        aboutLayout.addLayout(buttonRow)
        aboutDialog.exec_()

    def CreateTable(self) -> QtWidgets.QTableWidget:
        """Create a sortable, read-only results table."""
        table = QtWidgets.QTableWidget()
        table.setSortingEnabled(True)
        table.setAlternatingRowColors(True)
        table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        table.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        table.verticalHeader().setVisible(False)
        table.setWordWrap(False)
        table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        return table

    # ------------------------------------------------------------------ Data and population

    def Refresh(self) -> None:
        """Recompute all rows from the active chunk and repopulate the
        tables."""
        try:
            if Metashape is None:
                Message("This script must be run inside Agisoft Metashape Professional.")
                return
            document = Metashape.app.document
            self.chunk = getattr(document, "chunk", None)
            if self.chunk is None:
                # The dialog is reused: it must not keep the rows and the
                # Metashape objects of a project that is no longer open.
                self.markerByKey = {}
                self.cameraByKey = {}
                for kind in self.records:
                    self.records[kind] = []
                    self.visibleRecords[kind] = []
                self.PopulateTables()
                self._previousRefreshConsoleLines = None
                self._SetStatus("No active chunk.")
                return
            # The flags of this chunk: switching chunks must neither carry
            # flags to projections with the same keys nor lose them.
            self.reviewFlags = self.reviewFlagsByChunk.setdefault(
                ChunkIdentity(self.chunk), {})

            threshold = float(self.thresholdSpinBox.value())
            builder = ResidualDataBuilder(self.chunk, self.reviewFlags)
            projectionRows = builder.BuildProjectionRows(validOnly=self.validOnlyCheckBox.isChecked())
            markerRows = builder.BuildMarkerSummaries(projectionRows, threshold)
            cameraRows = builder.BuildCameraSummaries(projectionRows, threshold)
            diagnosticRows = builder.BuildDiagnostics(projectionRows, markerRows, cameraRows, threshold)

            self.markerByKey = builder.markerByKey
            self.cameraByKey = builder.cameraByKey
            self.records["projections"] = projectionRows
            self.records["markers"] = markerRows
            self.records["cameras"] = cameraRows
            self.records["diagnostics"] = diagnosticRows

            self.PopulateTables()
            # The status line, and the Console output (RefreshConsoleOutput).
            self.statusLabel.setText(
                "Chunk {}: {} of {} in {}. Residual threshold: {:.3f} px.".format(
                    getattr(self.chunk, "label", "") or "(unnamed)",
                    FormatCountWithNoun(len(projectionRows), "marker projection", "marker projections"),
                    FormatCountWithNoun(len(markerRows), "marker", "markers"),
                    FormatCountWithNoun(len(cameraRows), "image", "images"),
                    threshold))
            consoleLines = RefreshConsoleLines(
                getattr(self.chunk, "label", "") or "", threshold, self.validOnlyCheckBox.isChecked(),
                projectionRows, markerRows, cameraRows, diagnosticRows)
            PrintConsoleLines(RefreshConsoleOutput(
                consoleLines, self._previousRefreshConsoleLines, self._refreshConsoleMode,
                ThresholdConsoleLine(threshold, projectionRows, diagnosticRows)))
            self._previousRefreshConsoleLines = consoleLines
        except Exception:
            ErrorMessage("Could not compute marker residuals:\n\n" + traceback.format_exc())

    def _OnShowAboveClicked(self, checked: bool = False) -> None:
        print(ShowAboveConsoleLine(bool(checked)))

    def _OnHighlightClicked(self, checked: bool = False) -> None:
        print(HighlightConsoleLine(bool(checked)))

    def _OnRoleActivated(self, *arguments: Any) -> None:
        print(RoleFilterConsoleLine(self.roleComboBox.currentText()))

    def _OnSearchEditingFinished(self) -> None:
        """Echo the search when editing finishes (Enter or leaving the
        field) and the text differs from the search echoed last."""
        searchText = self.searchEdit.text().strip()
        if searchText == self._lastEchoedSearch:
            return
        self._lastEchoedSearch = searchText
        print(SearchConsoleLine(searchText))

    def _SetStatus(self, text: str) -> None:
        """Show *text* in the status line and echo it to the Console."""
        self.statusLabel.setText(text)
        print(ConsoleStatusLine(text))

    def _RefreshWithConsoleMode(self, mode: str) -> None:
        """Refresh with the Console output of *mode* (RefreshConsoleOutput)."""
        self._refreshConsoleMode = mode
        try:
            self.Refresh()
        finally:
            self._refreshConsoleMode = None

    def _OnThresholdChanged(self, *arguments: Any) -> None:
        """Refresh after a change of the threshold, echoed as one line when
        nothing else changed."""
        self._RefreshWithConsoleMode("threshold")

    def PopulateTables(self) -> None:
        """Fill the projection, marker, image, and diagnostics tables using
        the current filters."""
        threshold = float(self.thresholdSpinBox.value())
        search = self.searchEdit.text().strip().lower()
        role = self.roleComboBox.currentText()
        aboveOnly = self.showAboveCheckBox.isChecked()

        self.PopulateTable(
            self.markerTable,
            self.markerColumns,
            self.FilterRows(self.records["markers"], "markers", threshold, search, role, aboveOnly),
            "markers",
        )
        self.PopulateTable(
            self.projectionTable,
            self.projectionColumns,
            self.FilterRows(self.records["projections"], "projections", threshold, search, role, aboveOnly),
            "projections",
        )
        self.PopulateTable(
            self.cameraTable,
            self.cameraColumns,
            self.FilterRows(self.records["cameras"], "cameras", threshold, search, role, aboveOnly),
            "cameras",
        )
        self.PopulateTable(
            self.diagnosticTable,
            self.diagnosticColumns,
            self.FilterRows(self.records["diagnostics"], "diagnostics", threshold, search, role, aboveOnly),
            "diagnostics",
        )

    def FilterRows(
        self,
        rows: Sequence[Dict[str, Any]],
        kind: str,
        threshold: float,
        search: str,
        role: str,
        aboveOnly: bool,
    ) -> List[Dict[str, Any]]:
        """Rows that pass the role filter, the search text, and, when it
        is ticked, the filter for rows at or above the threshold."""
        filtered = []
        for row in rows:
            if role != "All":
                # Rows that carry a role (markers, projections, and their
                # diagnostics) are filtered by it. Image summaries and
                # image diagnostics have none: they cover every role.
                rowRole = row.get("role")
                if rowRole is not None and rowRole != role:
                    continue
            if search:
                haystack = f"{row.get('marker', '')} {row.get('camera', '')} {row.get('worstCamera', '')} {row.get('worstMarker', '')}".lower()
                if search not in haystack:
                    continue
            if aboveOnly:
                if kind == "projections" and float(row.get("errorPixels") or 0.0) < threshold:
                    continue
                if kind in ("markers", "cameras") and int(row.get("aboveThreshold") or 0) <= 0:
                    continue
                # Diagnostics are already threshold-derived, so keep them.
            filtered.append(row)
        self.visibleRecords[kind] = filtered
        return filtered

    def PopulateTable(
        self,
        table: QtWidgets.QTableWidget,
        columns: Sequence[Tuple[str, str, int]],
        rows: Sequence[Dict[str, Any]],
        kind: str,
    ) -> None:
        """Fill one table with *rows* using the given column definitions."""
        table.setSortingEnabled(False)
        table.clear()
        table.setColumnCount(len(columns))
        table.setHorizontalHeaderLabels([title for _, title, _ in columns])
        table.setRowCount(len(rows))

        threshold = float(self.thresholdSpinBox.value())
        doHighlight = self.highlightCheckBox.isChecked()
        boldFont = QtGui.QFont()
        boldFont.setBold(True)
        redBrush = QtGui.QBrush(QtGui.QColor(200, 0, 0))
        lightRedBrush = QtGui.QBrush(QtGui.QColor(255, 235, 235))

        for rowIndex, row in enumerate(rows):
            recordId = f"{kind}:{rowIndex}"

            # Whether this row is at or above the threshold.
            isExceedance = False
            if doHighlight:
                if kind == "projections":
                    isExceedance = float(row.get("errorPixels") or 0.0) >= threshold
                elif kind in ("markers", "cameras"):
                    isExceedance = int(row.get("aboveThreshold") or 0) > 0
                elif kind == "diagnostics":
                    # Every diagnostic row is at or above a threshold: the
                    # image residual threshold, or for an object-space row
                    # the threshold of the object-space status.
                    isExceedance = True

            for columnIndex, (key, titleUnused, decimals) in enumerate(columns):
                item = SortableItem(row.get(key), decimals=decimals, recordId=recordId,
                                    isLength=key in _LENGTH_COLUMN_KEYS)

                if isExceedance:
                    item.setFont(boldFont)
                    item.setForeground(redBrush)
                    item.setBackground(lightRedBrush)

                table.setItem(rowIndex, columnIndex, item)

        table.resizeColumnsToContents()
        header = table.horizontalHeader()
        try:
            header.setStretchLastSection(True)
        except Exception as suppressedError:
            ReportSuppressedError("markerResidualInspector.PopulateTable", suppressedError)
        table.setSortingEnabled(True)

    # ------------------------------------------------------------------ Selection helpers

    def CurrentKindAndTable(self) -> Tuple[str, QtWidgets.QTableWidget]:
        """Kind and widget of the table on the active tab."""
        widget = self.tabs.currentWidget()
        if widget is self.markerTable:
            return "markers", self.markerTable
        if widget is self.projectionTable:
            return "projections", self.projectionTable
        if widget is self.cameraTable:
            return "cameras", self.cameraTable
        return "diagnostics", self.diagnosticTable

    def SelectedRecords(self) -> Tuple[str, List[Dict[str, Any]]]:
        """Kind and records of the rows selected in the active table."""
        kind, table = self.CurrentKindAndTable()
        selectionModel = table.selectionModel()
        if selectionModel is None:
            return kind, []
        selectedRows = selectionModel.selectedRows()
        records: List[Dict[str, Any]] = []
        seen = set()
        for index in selectedRows:
            rowNumber = index.row()
            item = table.item(rowNumber, 0)
            if item is None:
                continue
            recordId = item.data(userRole)
            if not recordId or recordId in seen:
                continue
            seen.add(recordId)
            try:
                kindUnused, visibleIndexText = str(recordId).split(":", 1)
                visibleIndex = int(visibleIndexText)
                records.append(self.visibleRecords[kind][visibleIndex])
            except Exception as suppressedError:
                ReportSuppressedError("markerResidualInspector.SelectedRecords", suppressedError)
        return kind, records

    def FirstSelectedRecord(self) -> Optional[Dict[str, Any]]:
        kindUnused, records = self.SelectedRecords()
        return records[0] if records else None

    def CollectMarkerKeys(self, records: Sequence[Dict[str, Any]]) -> List[int]:
        keys = []
        for row in records:
            key = row.get("markerKey")
            if key is not None:
                keys.append(int(key))
        return sorted(set(keys))

    def CollectCameraKeys(self, records: Sequence[Dict[str, Any]]) -> List[int]:
        keys = []
        for row in records:
            key = row.get("cameraKey")
            if key is not None:
                keys.append(int(key))
        return sorted(set(keys))

    # ------------------------------------------------------------------ Actions

    def OpenSelectedPhoto(self) -> None:
        """Open the image of the first selected row in the photo view."""
        row = self.FirstSelectedRecord()
        if not row:
            return
        cameraKey = row.get("cameraKey")
        if cameraKey is None:
            return
        camera = self.cameraByKey.get(int(cameraKey))
        if camera is None:
            Message("The selected row does not refer to an available image.")
            return
        try:
            Metashape.app.photo_view.open(camera, False)
            Metashape.app.photo_view.show_markers = True
            centred = row.get("measuredX") is not None and row.get("measuredY") is not None
            if centred:
                Metashape.app.photo_view.center = Metashape.Vector([float(row["measuredX"]), float(row["measuredY"])])
            Metashape.app.update()
            self._SetStatus("Opened {} in the Photo view{}.".format(
                getattr(camera, "label", "") or "the image",
                ", centred on the marker projection of {}".format(row.get("marker")) if centred else ""))
        except Exception:
            ErrorMessage("Could not open the selected image:\n\n" + traceback.format_exc())

    def SelectMarkersFromSelection(self) -> None:
        """Select the markers of the selected rows in Metashape."""
        kindUnused, records = self.SelectedRecords()
        markerKeys = self.CollectMarkerKeys(records)
        if not markerKeys:
            Message("The current selection does not contain markers.")
            return
        try:
            for marker in getattr(self.chunk, "markers", []) or []:
                try:
                    marker.selected = False
                except Exception as suppressedError:
                    ReportSuppressedError("markerResidualInspector.SelectMarkersFromSelection", suppressedError)
            for key in markerKeys:
                marker = self.markerByKey.get(key)
                if marker is not None:
                    marker.selected = True
            Metashape.app.update()
            self._SetStatus("Selected {} in the active chunk.".format(
                FormatCountWithNoun(len(markerKeys), "marker", "markers")))
        except Exception:
            ErrorMessage("Could not select marker(s):\n\n" + traceback.format_exc())

    def FilterPhotosPaneFromSelection(self) -> None:
        """Show only the images of the selected rows in the Photos pane."""
        kindUnused, records = self.SelectedRecords()
        cameraKeys = self.CollectCameraKeys(records)
        cameras = [self.cameraByKey[cameraKey] for cameraKey in cameraKeys if cameraKey in self.cameraByKey]
        if not cameras:
            Message("The current selection does not contain images.")
            return
        try:
            Metashape.app.photos_pane.setFilter(cameras)
            Metashape.app.update()
            self._SetStatus("Photos pane filtered to {}.".format(
                FormatCountWithNoun(len(cameras), "image", "images")))
        except Exception:
            ErrorMessage("Could not filter the Photos pane:\n\n" + traceback.format_exc())

    def ResetPhotosPaneFilter(self) -> None:
        try:
            Metashape.app.photos_pane.resetFilter()
            Metashape.app.update()
            self._SetStatus("Photos pane filter reset.")
        except Exception:
            ErrorMessage("Could not reset the Photos pane filter:\n\n" + traceback.format_exc())

    def ApplyReviewFlag(self) -> None:
        """Apply the review flag to the selected projection rows."""
        kind, records = self.SelectedRecords()
        # Review flags are only well-defined for individual marker
        # projections: summary rows, which name only their worst
        # projection, are not used.
        labelsByPair = ProjectionLabelsByPair(kind, records)
        if not labelsByPair:
            Message(_SELECT_PROJECTION_ROWS_TEXT)
            return

        flag = self.reviewComboBox.currentText()
        for pair, (markerLabel, imageLabel) in labelsByPair.items():
            if flag:
                # Stored with the labels (see ReviewFlagForProjection).
                self.reviewFlags[pair] = (flag, markerLabel, imageLabel)
            else:
                self.reviewFlags.pop(pair, None)
        # A review flag changes no count: the refresh is echoed only when
        # something else changed since the previous refresh.
        self._RefreshWithConsoleMode("quiet")
        self._SetStatus("Applied review flag '{}' to {}.".format(
            flag or "unreviewed", FormatCountWithNoun(len(labelsByPair), "marker projection", "marker projections")))

    def InvalidateSelectedProjections(self) -> None:
        """Mark the selected marker projections as invalid after a
        confirmation, then update the chunk transform from the reference
        data."""
        kind, records = self.SelectedRecords()
        # Only rows that stand for one projection: invalidating the
        # hidden worst projection of a summary row is not allowed.
        pairs = ProjectionPairsFromRecords(kind, records)
        if not pairs:
            Message(_SELECT_PROJECTION_ROWS_TEXT)
            return

        answer = QtWidgets.QMessageBox.question(
            self,
            "Marker Residual Inspector",
            "Invalidate the selected marker-image projection(s)?\n\n"
            "This sets projection.valid = False for each of them and then updates the chunk "
            "transform from the reference data, as Update in the Reference pane does.\n\n"
            "Do this only after visual inspection. Continue?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No,
        )
        if answer != QtWidgets.QMessageBox.Yes:
            return

        changed = 0
        try:
            for markerKey, cameraKey in pairs:
                marker = self.markerByKey.get(markerKey)
                camera = self.cameraByKey.get(cameraKey)
                if marker is None or camera is None:
                    continue
                try:
                    projection = marker.projections[camera]
                    projection.valid = False
                    changed += 1
                except Exception as suppressedError:
                    ReportSuppressedError("markerResidualInspector.InvalidateSelectedProjections.validFlag", suppressedError)
            try:
                self.chunk.updateTransform()
            except Exception as suppressedError:
                ReportSuppressedError("markerResidualInspector.InvalidateSelectedProjections.updateTransform", suppressedError)
            if changed == 0:
                print(ConsoleStatusLine("Nothing changed: no projection was invalidated. The projection.valid "
                                        "attribute may not be writable in this Metashape version."))
                QtWidgets.QMessageBox.warning(
                    self, "Marker Residual Inspector",
                    "Nothing changed: no projection was invalidated.\n"
                    "The projection.valid attribute may not be\n"
                    "writable in this Metashape version.")
            self.Refresh()
            if changed == 0:
                self._SetStatus("No marker projection was invalidated.")
            else:
                self._SetStatus("Invalidated {}. Run Optimize Cameras again if needed.".format(
                    FormatCountWithNoun(changed, "marker projection", "marker projections")))
        except Exception:
            ErrorMessage("Could not invalidate projection(s):\n\n" + traceback.format_exc())

    # ------------------------------------------------------------------ CSV export

    def CurrentCsvFormat(self) -> Tuple[str, str]:
        """(delimiter, decimal separator) chosen for the CSV exports (a
        comma and a decimal point by default)."""
        return ChosenCsvFormat(self.delimiterComboBox)

    def CurrentCsvCommentLines(self, kind: str) -> List[str]:
        """Comment lines of the CSV export of one table kind: the chunk
        and the threshold and filters the exported rows depend on."""
        return CsvCommentLines(
            getattr(self.chunk, "label", "") or "",
            float(self.thresholdSpinBox.value()),
            self.validOnlyCheckBox.isChecked(),
            self.roleComboBox.currentText(),
            self.searchEdit.text().strip(),
            self.showAboveCheckBox.isChecked(),
            kind,
            getattr(MarkerReferenceCoordinateSystem(self.chunk), "name", "") or "")

    def ExportActiveCsv(self) -> None:
        """Export the visible rows of the active table to a CSV file."""
        kind, tableUnused = self.CurrentKindAndTable()
        path, filterUnused = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "Export active table as CSV",
            CsvFileNameForKind(kind),
            "CSV files (*.csv);;All files (*)",
        )
        if not path:
            return
        rows = self.visibleRecords[kind]
        columns = self.ColumnsForKind(kind)
        try:
            delimiter, decimalSeparator = self.CurrentCsvFormat()
            self.WriteCsv(path, rows, columns, delimiter, self.CurrentCsvCommentLines(kind),
                          decimalSeparator=decimalSeparator)
            self._SetStatus("Exported {} to {}".format(
                FormatCountWithNoun(len(rows), "row", "rows"), path))
        except Exception:
            ErrorMessage("Could not export CSV:\n\n" + traceback.format_exc())

    def ExportAllCsvs(self) -> None:
        """Export the visible rows of every table, one CSV file per
        table, into a chosen folder."""
        directory = QtWidgets.QFileDialog.getExistingDirectory(self, "Select output folder for CSV exports")
        if not directory:
            return
        try:
            delimiter, decimalSeparator = self.CurrentCsvFormat()
            for kind in ["markers", "projections", "cameras", "diagnostics"]:
                path = os.path.join(directory, CsvFileNameForKind(kind))
                self.WriteCsv(path, self.visibleRecords[kind], self.ColumnsForKind(kind), delimiter,
                              self.CurrentCsvCommentLines(kind), decimalSeparator=decimalSeparator)
            self._SetStatus(f"Exported all visible tables to {directory}")
        except Exception:
            ErrorMessage("Could not export CSVs:\n\n" + traceback.format_exc())

    def ColumnsForKind(self, kind: str) -> Sequence[Tuple[str, str, int]]:
        if kind == "markers":
            return self.markerColumns
        if kind == "projections":
            return self.projectionColumns
        if kind == "cameras":
            return self.cameraColumns
        return self.diagnosticColumns

    @staticmethod
    def WriteCsv(
        path: str,
        rows: Sequence[Dict[str, Any]],
        columns: Sequence[Tuple[str, str, int]],
        delimiter: str = ",",
        commentLines: Sequence[str] = (),
        decimalSeparator: str = ".",
    ) -> None:
        """Write *rows* with the given column definitions to a CSV file
        (UTF-8 with a byte order mark), after the comment lines, with
        *delimiter* between the cells and *decimalSeparator* in the
        numbers. Numbers are written by FormatCsvValue, and the image
        quality, a number that Metashape keeps as text
        (_CSV_NUMERIC_TEXT_COLUMN_KEYS), gets the decimal separator as
        well, whereas the comment lines, the header row, and the text
        cells keep their text."""
        with open(path, "w", newline="", encoding="utf-8-sig") as file:
            for commentLine in commentLines:
                file.write(commentLine + "\r\n")
            writer = csv.writer(file, delimiter=delimiter)
            writer.writerow([title for keyUnused, title, decimalsUnused in columns])
            for row in rows:
                cells = []
                for key, titleUnused, decimalsUnused in columns:
                    cellText = FormatCsvValue(row.get(key), decimalSeparator, isLength=key in _LENGTH_COLUMN_KEYS)
                    if key in _CSV_NUMERIC_TEXT_COLUMN_KEYS:
                        cellText = WithDecimalSeparator(cellText, decimalSeparator)
                    cells.append(cellText)
                writer.writerow(cells)


# -----------------------------------------------------------------------------
# Menu registration
# -----------------------------------------------------------------------------


markerResidualInspectorDialog = None


def ShowMarkerResidualInspector() -> None:
    """Open the Marker Residual Inspector dialog. A dialog opened
    before is shown, raised, and refreshed instead of replaced, so that
    the review flags of the session are kept."""
    global markerResidualInspectorDialog
    try:
        if markerResidualInspectorDialog is not None:
            try:
                markerResidualInspectorDialog.show()
            except RuntimeError as suppressedError:
                # The Qt object behind the dialog was deleted (for
                # example with its parent window): open a new one.
                ReportSuppressedError("markerResidualInspector.ShowMarkerResidualInspector", suppressedError)
                markerResidualInspectorDialog = None
            else:
                markerResidualInspectorDialog.raise_()
                markerResidualInspectorDialog.activateWindow()
                markerResidualInspectorDialog.Refresh()
                return
        application = QtWidgets.QApplication.instance()
        parent = application.activeWindow() if application is not None else None
        markerResidualInspectorDialog = MarkerResidualInspectorDialog(parent)
        markerResidualInspectorDialog.show()
        markerResidualInspectorDialog.raise_()
        markerResidualInspectorDialog.activateWindow()
    except Exception:
        ErrorMessage("Could not start the Marker Residual Inspector:\n\n" + traceback.format_exc())


def RegisterMenuItem():
    """Register this tool in the MetaFOCUS menu."""
    # Remove any existing item first, so reloading the toolbox
    # never creates a duplicate menu entry.
    try:
        Metashape.app.removeMenuItem(_MENU_LABEL)
    except Exception as suppressedError:
        ReportSuppressedError("markerResidualInspector.RegisterMenuItem", suppressedError)
    iconPath = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "icons", _ICON_NAME)
    if os.path.exists(iconPath):
        Metashape.app.addMenuItem(
            _MENU_LABEL, ShowMarkerResidualInspector, icon=iconPath)
    else:
        Metashape.app.addMenuItem(_MENU_LABEL, ShowMarkerResidualInspector)
