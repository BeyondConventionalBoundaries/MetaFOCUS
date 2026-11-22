"""
Interior Orientation Viewer for Agisoft Metashape Professional
===============================================================

Displays the interior orientation parameters of each interior
orientation group in the active chunk, together with statistical quality
indicators and plots of the distortion model and the residuals:

  - Parameter estimates (c_pix, cx, cy, b1, and b2 in pixels, k1 to k4
    and p1 to p4 dimensionless), with c_pix the principal distance in
    pixels (Metashape's calibration attribute f)
  - Standard errors (SE) from the bundle adjustment covariance
  - estimate/SE, the statistic of a test of each parameter against
    zero, and the two-sided p-value of that test under a normal
    approximation, erfc(|estimate/SE| / sqrt(2)), conditional on the
    reported standard errors and the adjustment model (no p-value for
    c_pix, because a principal distance cannot be zero)
  - Estimates from counts of the degrees of freedom of the tie-point
    network and of all observations, with the datum defect and the
    observation and unknown counts behind them (only what takes part in
    the adjustment is counted, and no rank deficiency beyond the datum
    defect and no further constraints are assumed)
  - Correlation matrix (lower triangle) and the five most strongly
    correlated parameter pairs
  - Photosite pitch in micrometres, and c, cx, and cy in millimetres (c
    and cy with the vertical photosite pitch, cx with the horizontal one)
  - Distortion vector fields (total, radial, decentring, additional
    corrections, and the difference from the initial calibration) and the
    cell means of the reprojection residuals, defined as in Metashape's
    Distortion Plot, for frame and fisheye groups (spherical, cylindrical,
    and RPC groups show the residuals only), drawn at a total
    magnification that a label and the plot give
  - Radial and decentring distortion profiles, as in Metashape's Profile
    tab
  - Options for three other definitions, with Metashape's as the
    default: the Difference against a reference calibration of the
    user's choice (another interior orientation group of the open
    project or a calibration file), the residuals as cell means without
    weights or as cell medians, and the balanced radial distortion
  - A table of the RMS and the maximum of every vector field, and a CSV
    export of the tab shown (parameters, correlations, estimated degrees
    of freedom, that table, the values of the profiles, and the table of
    the coverage by radius)
  - Tooltips with the values under the pointer on the vector field and
    on the profiles
  - The coverage of the image by the 2D tie points: a Coverage mode of
    the vector field, with the 2D tie points per cell, and the coverage
    by radius, as two plots beside the table of the vector fields, in
    all directions or in the wedge of a direction chosen in the Coverage
    map, with r including the decentring distortion
  - The layout of the plots in a row of fixed height
  - Export HTML: the tab shown, or every interior orientation group, each
    as one interactive HTML page (interiorOrientationReport), whose plots
    a web browser changes as the viewer does and prints, for example to a
    PDF file

The estimate/SE and the p-value of a parameter with |estimate/SE| < 3
are shown in bold: its estimate is not distinguishable from zero at that
level, which is a reason to compare adjustments with and without the
parameter (preferably with check points), not a rule to fix it.

Each tab shows the whole parameter table, one line with the two
estimated degrees of freedom, and the plots, in a scroll area. The
statistics, the units line, the most correlated pairs, and the legend
are in a side panel to the right, which "Show notes and statistics"
opens and closes for all tabs.

Author:      Geert J. Verhoeven (ORCID: 0000-0003-4825-9604)
Version:     1.1.0 (2026-11-22)
Date:        2026-06-12
Licence:     GPL-3.0-or-later WITH GPL-3.0-linking-exception
Contact:     geert.verhoeven@univie.ac.at
Requires:    Agisoft Metashape Professional 2.x+
Developed for: Agisoft Metashape Professional 2.3.2

Usage:
    Loaded automatically by the MetaFOCUS loader.
    MetaFOCUS > SfM Process Diagnostics > Interior Orientation Viewer
    opens the dialog.
"""

from .photogrammetryUtilities import (
    ReportSuppressedError,
    FormatCountWithNoun, DescribeException, JoinWithAnd,
    ComputeRedundancyBreakdown, CountProjectionsPerTrack, GetSensorCalibration, ImagesWithTiePointsKeys,
    IsOrientedEnabledCamera, IsKeyframe, NonFiniteResidualsText,
    DisabledOrientedImagesWithObservations, DisabledOrientedImagesText,
    TiePointResidual, SharedGroupLabels, GroupLabelWithKey,
    CSV_FORMAT_ROW_LABEL_TEXT, ChosenCsvFormat, CsvFormatName, DataRowWithDecimalSeparator, FillCsvFormatComboBox,
    ConsoleBlockEnd, ConsoleBlockStartOfChunk, PrintConsoleLines,
    DeclinedReplacingText, ReplaceUnconfirmedFileQuestionText, ReplacesUnconfirmedFile,
)
import Metashape
import array
import bisect
import collections
import colorsys
import csv
import datetime
import math
import os
import re
import shutil
import statistics
import tempfile
from xml.etree import ElementTree

from PySide2 import QtWidgets, QtCore, QtGui
import functools
from .interiorOrientationReport import RemoveTemporaryReport, ReportData, ReportFileName, WriteReport

# ---------- formatting helpers ----------
def FormatFixedDecimals(value, decimals=3):
    try: return f"{value:.{decimals}f}"
    except Exception: return "n/a"

def FormatFourDecimals(value):
    try: return f"{value:.4f}"
    except Exception: return "n/a"

def FormatSignificant(value):
    """Format a value with a number of decimals that falls with its
    magnitude ("n/a" if it is not finite)."""
    if value is None or not math.isfinite(value): return "n/a"
    absoluteValue = abs(value)
    if absoluteValue >= 1000: return f"{value:.0f}"
    if absoluteValue >= 100:  return f"{value:.1f}"
    if absoluteValue >= 10:   return f"{value:.2f}"
    if absoluteValue >= 1:    return f"{value:.3f}"
    if absoluteValue >= 0.01: return f"{value:.4f}"
    return f"{value:.3e}"


def FormatCalibrationValue(value):
    """Format a calibration estimate or standard error without losing
    information: six decimals from 1 upwards, six significant digits
    below 1 (scientific notation for very small values), and "n/a" if
    the value is not finite."""
    if value is None or not math.isfinite(value):
        return "n/a"
    if abs(value) >= 1.0:
        return "{:.6f}".format(value)
    return "{:.6g}".format(value)


# ---------- calibration access ----------


def GetParameterEstimate(calibration, parameterName):
    """Look up the numeric estimate for a calibration parameter.

    covariance_params lists names such as 'f', 'cx', 'cy', 'k1', 'k2',
    'p1', 'p2', 'b1', and 'b2', which are read as attributes of the
    Metashape Calibration. A name in another case is also tried in
    lower case.

    Parameters
    ----------
    calibration : Metashape.Calibration
    parameterName : str
        Name as returned by calibration.covariance_params.

    Returns
    -------
    float or NaN if not available.
    """
    if calibration is None:
        return float('nan')

    name = parameterName.strip()

    # Direct attribute lookup (covers f, cx, cy, k1-k4, p1-p4, b1, b2).
    if hasattr(calibration, name):
        try:
            value = float(getattr(calibration, name))
            if math.isfinite(value):
                return value
        except (TypeError, ValueError):
            pass

    # Lower-case fallback (for example, when covariance_params returns 'F').
    lowerCaseName = name.lower()
    if lowerCaseName != name and hasattr(calibration, lowerCaseName):
        try:
            value = float(getattr(calibration, lowerCaseName))
            if math.isfinite(value):
                return value
        except (TypeError, ValueError):
            pass

    return float('nan')

def NormalisedParameterName(parameterName):
    """The name of a calibration parameter as the viewer compares it, in
    lower case without surrounding spaces: Metashape can list the
    parameters of covariance_params in capitals (F, Cx, K1 in Camillo
    Ressl's project, 9 October 2026), whereas the attributes
    of the calibration and the names of the viewer are in lower case
    (f, cx, k1)."""
    return str(parameterName).strip().lower()


# ---------- significance helpers ----------
# A parameter with |estimate/SE| below this value is shown in bold: its
# estimate is not distinguishable from zero at that level.  The rule is
# on |estimate/SE| itself, not on a p-value above 0.0027, which would
# differ for 2.999977 < |estimate/SE| < 3, so that the estimate/SE and
# the p-value columns always emphasise the same parameters.
LOW_TEST_STATISTIC_LIMIT = 3.0

# Below this value a p-value is shown as "< 1e-300": the floating-point
# computation keeps no reliable digits there.
SMALLEST_SHOWN_P_VALUE = 1e-300


def TwoSidedPValue(testStatistic):
    """Two-sided p-value of the test of a parameter against zero, from its
    test statistic estimate/SE and the standard normal distribution:
    p = erfc(|estimate/SE| / sqrt(2)).  The complementary error function
    evaluates small p-values without the cancellation of 1 - erf(...),
    which loses their digits (and gives exactly zero beyond
    |estimate/SE| of about 8.3).
    The p-value is a normal approximation, conditional on the reported
    standard errors and the adjustment model.  The normal distribution
    would be exact for a z-statistic (a standard error from the a priori
    variance factor) only with normally distributed observations, correct
    weights, and a correct, linear adjustment model, whereas the bundle
    adjustment only linearises its model at the estimate, and it is an
    approximation for a
    t-statistic (a standard error scaled by a variance factor estimated
    from the residuals): with 5 degrees of freedom a Student t
    distribution gives 0.0301 instead of 0.00270 at |estimate/SE| = 3.
    The p-value is not the probability that the parameter is zero.
    None when the statistic is not a finite number."""
    if testStatistic is None or not math.isfinite(testStatistic):
        return None
    return math.erfc(abs(testStatistic) / math.sqrt(2.0))


def FormatPValue(pValue):
    """Text of a p-value with three significant digits, trailing zeros
    kept (0.0500, 0.00270, 0.000123, 2.26e-19), "< 1e-300" below
    SMALLEST_SHOWN_P_VALUE, and "n/a" when there is none."""
    if pValue is None or not math.isfinite(pValue):
        return "n/a"
    if pValue < SMALLEST_SHOWN_P_VALUE:
        return "< 1e-300"
    return "{:#.3g}".format(pValue)


# ---------- photosite pitch ----------
def GetPhotositePitchMillimetres(sensor):
    """Return the horizontal photosite pitch in mm of an interior
    orientation group (a Metashape Sensor).

    The Sensor provides pixel_width (float, mm) and pixel_size
    (Vector, mm). pixel_width is preferred because it is a scalar.
    """
    # pixel_width is a direct float in mm.
    horizontalPitch = getattr(sensor, "pixel_width", None)
    if horizontalPitch is not None:
        try:
            value = float(horizontalPitch)
            if math.isfinite(value) and value > 0:
                return value
        except (TypeError, ValueError):
            pass

    # pixel_size is a Vector: take the x component.
    pitchVector = getattr(sensor, "pixel_size", None)
    if pitchVector is not None:
        try:
            value = float(pitchVector[0])
            if math.isfinite(value) and value > 0:
                return value
        except (TypeError, ValueError, IndexError):
            pass

    return None


def GetPhotositePitchHeightMillimetres(sensor):
    """Return the vertical photosite pitch in mm of an interior
    orientation group (a Metashape Sensor).

    Prefers pixel_height, then the y component of pixel_size, and
    finally falls back to the horizontal pitch (square photosites).
    Used for the millimetre conversion of cy, which runs along the
    image y axis, and of c, because Metashape's attribute f scales the
    image y coordinate exactly (see BuildUnitsText).
    """
    verticalPitch = getattr(sensor, "pixel_height", None)
    if verticalPitch is not None:
        try:
            value = float(verticalPitch)
            if math.isfinite(value) and value > 0:
                return value
        except (TypeError, ValueError):
            pass
    pitchVector = getattr(sensor, "pixel_size", None)
    if pitchVector is not None:
        try:
            value = float(pitchVector[1])
            if math.isfinite(value) and value > 0:
                return value
        except (TypeError, ValueError, IndexError):
            pass
    return GetPhotositePitchMillimetres(sensor)

# ---------- UI builder ----------
# Metashape's internal calibration parameter names mapped to display
# names. Metashape's attribute "f" holds the principal distance in
# pixels c_pix = c/p (the principal distance c divided by the photosite
# pitch p), not the focal length of the lens. Table cells and header
# labels show plain text, so they write c_pix: the Unicode subscript
# letter p (U+209A) is missing from common interface fonts, so a cell
# would mix fonts or show a missing glyph. Rich-text labels write the
# subscript in HTML.
PARAMETER_DISPLAY_NAMES = {"f": "c_pix (principal distance in pixels)"}
PARAMETER_DISPLAY_NAMES_RICH_TEXT = {"f": "c<sub>pix</sub> (principal distance in pixels)"}


def _DisplayParameterName(internalName):
    """Display name of a Metashape calibration parameter in plain text
    (table cells and header labels)."""
    return PARAMETER_DISPLAY_NAMES.get(internalName, internalName)


def _DisplayParameterNameRichText(internalName):
    """Display name of a Metashape calibration parameter in a rich-text
    label, with the subscript of the principal distance in pixels."""
    return PARAMETER_DISPLAY_NAMES_RICH_TEXT.get(internalName, internalName)


# The correlation columns are narrow, so the column of Metashape's
# attribute f is headed c_pix alone, and its header carries the full name
# of its row as a tooltip (decision of 7 October 2026).
CORRELATION_HEADER_NAMES = {"f": "c_pix"}


def _CorrelationHeaderName(internalName):
    """Header of the correlation column of a Metashape calibration
    parameter (plain text)."""
    return CORRELATION_HEADER_NAMES.get(internalName, internalName)


# Metashape's name of the principal distance in pixels in
# covariance_params. A principal distance cannot be zero, so its row gets
# no test against zero: a dash instead of the p-value, and no emphasis.
PRINCIPAL_DISTANCE_PARAMETER_NAME = "f"

# The leading columns of the parameter table, followed by one column per
# parameter for the correlations.
LEADING_COLUMN_HEADERS = ["Parameter", "Parameter estimate", "Standard error (SE)", "estimate/SE",
                          "p-value (two-sided)", "Estimated DoF (tie points)", "Estimated DoF (all observations)"]

PRINCIPAL_DISTANCE_P_VALUE_TOOLTIP = (
    "No p-value: a principal distance cannot be zero, so a test of c_pix against zero says nothing about "
    "whether it should be adjusted or fixed at a calibrated value.")

# The side panel of each tab with the statistics, the units line, the
# most correlated pairs, and the legend, and its button.
NOTES_PANEL_SHOW_TEXT = "Show notes and statistics"
NOTES_PANEL_HIDE_TEXT = "Hide notes and statistics"
NOTES_PANEL_BUTTON_TOOLTIP = (
    "The statistics of the chunk, the photosite pitch with c, cx, and cy in millimetres, the most correlated "
    "parameter pairs, and notes on the columns, in a panel on the right. Opening or closing it applies to every "
    "tab.")
# Width of the panel: this many average character widths of its font,
# and at least NOTES_PANEL_MINIMUM_WIDTH screen pixels.
NOTES_PANEL_WIDTH_IN_CHARACTERS = 52
NOTES_PANEL_MINIMUM_WIDTH = 360


def BuildUnitsText(calibration, sensor):
    """Photosite pitch in micrometres and the principal distance and the
    principal point offsets in millimetres, or "" when the photosite pitch
    is unknown. The values are read from the calibration itself, so that a
    parameter that was not adjusted (and therefore has no covariance) is
    converted as well. Both pitches are shown when they differ. c and cy
    are converted with the vertical (y) photosite pitch and cx with the
    horizontal (x) one: in Metashape's frame camera model (manual,
    Appendix D) the attribute f, which holds c_pix, scales the image y
    coordinate exactly, v = h * 0.5 + cy + y'f, whereas the x coordinate
    also carries the terms b1 and b2, u = w * 0.5 + cx + x'f + x'b1 +
    y'b2. So c = c_pix * p_y."""
    photositePitchMillimetres = GetPhotositePitchMillimetres(sensor)
    if not (photositePitchMillimetres and math.isfinite(photositePitchMillimetres) and photositePitchMillimetres > 0):
        return ""
    photositePitchHeightMillimetres = GetPhotositePitchHeightMillimetres(sensor)
    pitchWidthMicrometres = photositePitchMillimetres * 1000.0
    pitchHeightMicrometres = photositePitchHeightMillimetres * 1000.0
    if abs(pitchHeightMicrometres - pitchWidthMicrometres) < 0.0005:
        pitchText = "Photosite pitch: {:.3f} \u00b5m".format(pitchWidthMicrometres)
    else:
        pitchText = "Photosite pitch: {:.3f} \u00b5m (x), {:.3f} \u00b5m (y)".format(
            pitchWidthMicrometres, pitchHeightMicrometres)
    principalDistancePixels = GetParameterEstimate(calibration, "f")
    cxPixels = GetParameterEstimate(calibration, "cx")
    cyPixels = GetParameterEstimate(calibration, "cy")
    millimetreTexts = [FormatFourDecimals(pixelValue * pitchMillimetres) if math.isfinite(pixelValue) else "n/a"
                       for pixelValue, pitchMillimetres in ((principalDistancePixels, photositePitchHeightMillimetres),
                                                            (cxPixels, photositePitchMillimetres),
                                                            (cyPixels, photositePitchHeightMillimetres))]
    return pitchText + " | " + "c \u2248 {} mm, cx \u2248 {} mm, cy \u2248 {} mm".format(*millimetreTexts)


def CountOrientedImages(chunk):
    """Number of enabled, oriented images in the chunk, slave images of a
    multi-camera rig included (keyframes of an animation track are not
    images and are left out)."""
    return sum(1 for camera in chunk.cameras if IsOrientedEnabledCamera(camera))


# What a disabled image that is still oriented and has 2D tie points or
# marker projections means for the estimated DoF, after the sentence that
# names such images (DisabledOrientedImagesText): for one image and for
# more.
DISABLED_IMAGE_DEGREES_OF_FREEDOM_TEXT = (
    "Optimize Cameras still adjusts such an image, but the estimated DoF count the observations and unknowns of the "
    "enabled images only. Enabling the image, or resetting its orientation, removes this difference.")
DISABLED_IMAGES_DEGREES_OF_FREEDOM_TEXT = (
    "Optimize Cameras still adjusts such images, but the estimated DoF count the observations and unknowns of the "
    "enabled images only. Enabling these images, or resetting their orientation, removes this difference.")


def ComputeChunkRedundancyBreakdowns(chunk):
    """Redundancy breakdowns of the tie-point network and of all
    observations. They describe the whole chunk, so the dialog computes
    them once, from one count of the projections per track, and every tab
    shows the same values.  A second pass over the projections finds the
    images with 2D tie points in the adjustment, once for both
    breakdowns.  When a disabled image is still oriented and has 2D tie
    points or marker projections, which Metashape adjusts as well
    (DisabledOrientedImagesWithObservations), both breakdowns carry the text
    that names these images for the notes panel (disabledImagesNote,
    otherwise empty), and the estimated DoF values are given as for the
    enabled images."""
    projectionCounts = CountProjectionsPerTrack(chunk)
    # The images with 2D tie points in the adjustment, found once for both
    # (ComputeRedundancyBreakdown).
    tiePointImageKeys = ImagesWithTiePointsKeys(chunk, projectionCounts)
    breakdowns = (ComputeRedundancyBreakdown(chunk, includeControl=False, projectionCounts=projectionCounts,
                                             tiePointImageKeys=tiePointImageKeys),
                  ComputeRedundancyBreakdown(chunk, includeControl=True, projectionCounts=projectionCounts,
                                             tiePointImageKeys=tiePointImageKeys))
    disabledImagesNote = DisabledOrientedImagesText(DisabledOrientedImagesWithObservations(chunk),
                                                    DISABLED_IMAGE_DEGREES_OF_FREEDOM_TEXT,
                                                    DISABLED_IMAGES_DEGREES_OF_FREEDOM_TEXT)
    for breakdown in breakdowns:
        breakdown["disabledImagesNote"] = disabledImagesNote
    return breakdowns


class PageScrollArea(QtWidgets.QScrollArea):
    """Scroll area of a tab page whose size hint is that of the whole page
    (the size hint of QScrollArea is limited to a few dozen lines of
    text), so that the dialog can open large enough for the whole
    parameter table and the plots when the screen allows."""

    def sizeHint(self):
        pageWidget = self.widget()
        if pageWidget is None:
            return super().sizeHint()
        pageSize = pageWidget.sizeHint()
        frameWidth = 2 * self.frameWidth()
        return QtCore.QSize(pageSize.width() + frameWidth, pageSize.height() + frameWidth)


def WrapPageInScrollArea(pageWidget):
    """A resizable scroll area around a tab page, so that a window too
    small for the page scrolls instead of clipping it."""
    pageScrollArea = PageScrollArea()
    pageScrollArea.setWidgetResizable(True)
    pageScrollArea.setFrameShape(QtWidgets.QFrame.NoFrame)
    pageScrollArea.setWidget(pageWidget)
    return pageScrollArea


def FitTableToItsContents(table):
    """Size a table to show all its rows and columns without inner scroll
    bars: the scroll bars are switched off, the size follows the contents,
    and the layout can neither squeeze nor stretch the table, so that the
    page around it scrolls instead when the window is too small."""
    table.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
    table.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
    table.setSizeAdjustPolicy(QtWidgets.QAbstractScrollArea.AdjustToContents)
    table.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
    table.resizeColumnsToContents()
    table.resizeRowsToContents()


def ObservedImagesText(tiePointBreakdown):
    """" (with 2D tie points in the adjustment: 48)" for the statistics of
    the notes panel, the images whose unknowns "Estimated DoF (tie points)"
    counts (ComputeRedundancyBreakdown), or an empty text without that
    count."""
    observedImageCount = tiePointBreakdown.get("observedImageCount")
    if observedImageCount is None:
        return ""
    return " (with 2D tie points in the adjustment: {:,})".format(observedImageCount)


def DescribeEstimatedDegreesOfFreedom(degreesOfFreedomTiePoints, degreesOfFreedomAllObservations):
    """The one line between the parameter table and the plots: both
    estimated degrees of freedom (a dash for a value that is not
    positive), or why there are none."""
    if degreesOfFreedomTiePoints is None and degreesOfFreedomAllObservations is None:
        return "Estimated DoF not available: the network has no more observations than unknowns."
    valueTexts = ["\u2013" if value is None else "{:,}".format(int(value))
                  for value in (degreesOfFreedomTiePoints, degreesOfFreedomAllObservations)]
    return "Estimated DoF (tie points): {} | Estimated DoF (all observations): {}".format(*valueTexts)


def NotesPanelWidth(notesPanel):
    """Width of the notes panel in screen pixels, from the average
    character width of its font (NOTES_PANEL_MINIMUM_WIDTH when the font
    cannot tell)."""
    try:
        averageCharacterWidth = notesPanel.fontMetrics().averageCharWidth()
    except Exception as suppressedError:
        ReportSuppressedError("interiorOrientationViewer.NotesPanelWidth", suppressedError)
        return NOTES_PANEL_MINIMUM_WIDTH
    if not isinstance(averageCharacterWidth, int) or averageCharacterWidth <= 0:
        return NOTES_PANEL_MINIMUM_WIDTH
    return max(NOTES_PANEL_MINIMUM_WIDTH, NOTES_PANEL_WIDTH_IN_CHARACTERS * averageCharacterWidth)


def BuildNotesPanel(noteWidgets):
    """Side panel to the right of a tab page with the given widgets (the
    statistics, the units line, the most correlated pairs, and the
    legend), in a scroll area of fixed width that stays hidden until
    "Show notes and statistics" opens it."""
    notesContent = QtWidgets.QWidget()
    notesLayout = QtWidgets.QVBoxLayout(notesContent)
    for noteWidget in noteWidgets:
        notesLayout.addWidget(noteWidget)
    notesLayout.addStretch()
    notesPanel = QtWidgets.QScrollArea()
    notesPanel.setWidgetResizable(True)
    notesPanel.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
    notesPanel.setWidget(notesContent)
    notesPanel.setFixedWidth(NotesPanelWidth(notesPanel))
    notesPanel.setVisible(False)
    return notesPanel


def _SetNotesPanelVisible(notesPanel, notesToggleButton, isVisible):
    """Show or hide the notes panel of one tab, and name what its button
    does next."""
    notesPanel.setVisible(isVisible)
    notesToggleButton.setText(NOTES_PANEL_HIDE_TEXT if isVisible else NOTES_PANEL_SHOW_TEXT)


def _ApplySharedNotesPanelState(notesPanel, notesToggleButton, sharedPlotModes):
    """Show or hide the notes panel of a tab as the state shared by all
    tabs says (on a tab change, and for a tab that is built after the
    panel was opened)."""
    _SetNotesPanelVisible(notesPanel, notesToggleButton, bool(sharedPlotModes.get("notesPanelVisible", False)))


def _OnNotesToggleClicked(tabContent, pageScrollArea, notesPanel, notesToggleButton, sharedPlotModes,
                          buttonChecked=False, groupName=""):
    """Open or close the notes panel for all tabs. The clicked signal may
    pass the checked state of the button, which is not checkable, so
    buttonChecked is ignored. Before the panel opens, the window is
    widened as far as the page needs beside it, and it returns to its
    width when the panel closes."""
    isVisible = not sharedPlotModes.get("notesPanelVisible", False)
    sharedPlotModes["notesPanelVisible"] = isVisible
    print(ViewerConsoleLine(groupName, "notes and statistics " + ("shown" if isVisible else "hidden")))
    if isVisible:
        sharedPlotModes["notesPanelWindowGrowth"] = _ExtendWindowForNotesPanel(tabContent, pageScrollArea,
                                                                               notesPanel)
    _SetNotesPanelVisible(notesPanel, notesToggleButton, isVisible)
    if not isVisible:
        _ShrinkWindowAfterNotesPanel(tabContent, sharedPlotModes.pop("notesPanelWindowGrowth", 0))


def NotesPanelWindowGrowth(pageNeededWidth, tabWidth, notesPanelWidth, spacing):
    """Extra window width that keeps a tab page at the width it needs
    beside the opened notes panel and the spacing before it: 0 when the
    tab is wide enough for both."""
    return max(0, int(pageNeededWidth + spacing + notesPanelWidth - tabWidth))


def _ExtendWindowForNotesPanel(tabContent, pageScrollArea, notesPanel):
    """Widen the window, within the available screen, by as much as the
    page of the tab needs beside the notes panel that is about to open,
    so that the parameter table stays fully visible when there is room.
    A maximised or full-screen window is left as it is, and a window
    moved partly off the screen by the extension is moved back.  Returns
    the width added in screen pixels (for _ShrinkWindowAfterNotesPanel),
    0 when the window stays as it is."""
    try:
        window = tabContent.window()
        if window is None or window.isMaximized() or window.isFullScreen():
            return 0
        tabLayout = tabContent.layout()
        spacing = max(0, tabLayout.spacing()) if tabLayout is not None else 0
        growth = NotesPanelWindowGrowth(pageScrollArea.sizeHint().width(), tabContent.width(),
                                        notesPanel.width(), spacing)
        if growth <= 0:
            return 0
        oldWidth = window.width()
        newWidth = oldWidth + growth
        screenGeometry = AvailableScreenGeometry(window)
        if screenGeometry is not None:
            screenLeft, _, screenWidth, _ = screenGeometry
            frameWidth = max(0, window.frameGeometry().width() - window.width())
            newWidth = min(newWidth, screenWidth - frameWidth)
        if newWidth <= oldWidth:
            return 0
        window.resize(newWidth, window.height())
        if screenGeometry is not None:
            overhang = window.frameGeometry().x() + window.frameGeometry().width() - (screenLeft + screenWidth)
            if overhang > 0:
                window.move(max(screenLeft, window.x() - overhang), window.y())
        return newWidth - oldWidth
    except Exception as suppressedError:
        ReportSuppressedError("interiorOrientationViewer._ExtendWindowForNotesPanel", suppressedError)
        return 0


def _ShrinkWindowAfterNotesPanel(tabContent, growth):
    """Take the width that _ExtendWindowForNotesPanel added back when the
    notes panel closes, unless the window is maximised or full-screen."""
    if not growth or growth <= 0:
        return
    try:
        window = tabContent.window()
        if window is None or window.isMaximized() or window.isFullScreen():
            return
        window.resize(max(window.minimumWidth(), window.width() - growth), window.height())
    except Exception as suppressedError:
        ReportSuppressedError("interiorOrientationViewer._ShrinkWindowAfterNotesPanel", suppressedError)


def AvailableScreenGeometry(widget):
    """Left, top, width, and height of the available area (without task
    bars) of the screen of the parent window of *widget*, of *widget*
    itself, or of the primary screen. None when Qt cannot tell
    (QWidget.screen needs Qt 5.14)."""
    try:
        screen = None
        for candidate in (widget.parentWidget(), widget):
            screenOfCandidate = getattr(candidate, "screen", None) if candidate is not None else None
            if screenOfCandidate is not None:
                screen = screenOfCandidate()
                if screen is not None:
                    break
        if screen is None:
            screen = QtGui.QGuiApplication.primaryScreen()
        geometry = screen.availableGeometry()
        values = (geometry.x(), geometry.y(), geometry.width(), geometry.height())
    except Exception as suppressedError:
        ReportSuppressedError("interiorOrientationViewer.AvailableScreenGeometry", suppressedError)
        return None
    if all(isinstance(value, int) for value in values) and values[2] > 0 and values[3] > 0:
        return values
    return None


def ScrollBarExtent(widget):
    """Width of a vertical scroll bar in the style of *widget*, or 0 when
    the style cannot tell."""
    try:
        extent = widget.style().pixelMetric(QtWidgets.QStyle.PM_ScrollBarExtent)
    except Exception as suppressedError:
        ReportSuppressedError("interiorOrientationViewer.ScrollBarExtent", suppressedError)
        return 0
    return extent if isinstance(extent, int) and extent > 0 else 0


# The smallest size at which the dialog opens when the screen allows (it
# leaves the plots room).
DEFAULT_DIALOG_WIDTH = 1320
DEFAULT_DIALOG_HEIGHT = 820
# Room for the window frame (title bar and borders), which the size of a
# dialog does not include.
WINDOW_FRAME_WIDTH_ALLOWANCE = 16
WINDOW_FRAME_HEIGHT_ALLOWANCE = 48


def ChooseInitialDialogSize(neededWidth, neededHeight, availableWidth, availableHeight, scrollBarExtent=0):
    """Width and height at which the dialog opens: large enough for the
    whole content of its tabs (the parameter table above all), at least
    DEFAULT_DIALOG_WIDTH x DEFAULT_DIALOG_HEIGHT, and at most the
    available screen less the window frame. When the screen is too low
    for the content, the page scrolls vertically, so the width keeps
    room for that scroll bar. Without screen information (None) the
    content decides."""
    width, height = neededWidth, neededHeight
    if availableHeight and neededHeight > availableHeight - WINDOW_FRAME_HEIGHT_ALLOWANCE:
        width += scrollBarExtent
    width = max(DEFAULT_DIALOG_WIDTH, width)
    height = max(DEFAULT_DIALOG_HEIGHT, height)
    if availableWidth:
        width = min(width, availableWidth - WINDOW_FRAME_WIDTH_ALLOWANCE)
    if availableHeight:
        height = min(height, availableHeight - WINDOW_FRAME_HEIGHT_ALLOWANCE)
    return int(width), int(height)


def BuildMessageWithPlots(messageText, sensor, chunk, sharedPlotModes):
    """Page of an interior orientation group without a usable covariance:
    the message, followed by the distortion and residual plots, which need
    only the calibration and the tie points, in a scroll area."""
    messageLabel = QtWidgets.QLabel(messageText)
    messageLabel.setStyleSheet("color:#b00;")
    messageLabel.setWordWrap(True)
    plotsWidget = BuildDistortionPlotsForSensor(sensor, chunk, sharedPlotModes)
    if plotsWidget is None:
        return messageLabel
    pageWidget = QtWidgets.QWidget()
    pageLayout = QtWidgets.QVBoxLayout(pageWidget)
    pageLayout.setContentsMargins(0, 0, 0, 0)
    pageLayout.addWidget(messageLabel)
    pageLayout.addWidget(plotsWidget)
    pageScrollArea = WrapPageInScrollArea(pageWidget)
    pageScrollArea._plotsWidget = plotsWidget
    # Without a covariance the CSV export has no parameter table, no
    # correlations, and no estimated degrees of freedom.
    pageScrollArea._csvContent = functools.partial(TabCsvContent, chunk, sensor, None, plotsWidget)
    pageScrollArea._csvFileName = CsvFileName(chunk, sensor)
    pageScrollArea._reportData = functools.partial(TabReportData, chunk, sensor, None, plotsWidget)
    pageScrollArea._reportFileName = ReportFileName(chunk, sensor)
    return pageScrollArea


def BuildWidgetForSensor(sensor, chunk, sharedPlotModes=None, redundancyBreakdowns=None):
    """Build the tab of one interior orientation group: a page in a scroll
    area with the parameter table and its correlations (all rows and
    columns, without inner scroll bars), one line with the two estimated
    degrees of freedom and the button of the notes panel, and the
    distortion and residual plots, and to its right the notes panel with
    the statistics, the units line, the top correlated pairs, and the
    legend, closed until the button opens it. sharedPlotModes carries the
    plot settings of BuildDistortionPlotsForSensor and, under
    "notesPanelVisible", whether the notes panel is open, for all tabs.
    Without a usable covariance the page holds a message and the plots.
    redundancyBreakdowns holds the two breakdowns of
    ComputeChunkRedundancyBreakdowns, computed here when not given."""
    if sharedPlotModes is None:
        sharedPlotModes = {"vector": 0, "profile": 2, "notesPanelVisible": False}
    calibration = GetSensorCalibration(sensor)
    if calibration is None:
        return QtWidgets.QLabel("No calibration for this interior orientation group.")

    parameterNames = getattr(calibration, "covariance_params", None)
    covariance    = getattr(calibration, "covariance_matrix", None)
    if not parameterNames or covariance is None:
        # The plots need no covariance, so they are shown below the message.
        return BuildMessageWithPlots(
            "No calibration covariance for this interior orientation group: its parameters "
            "were not adjusted (a fixed calibration), or Optimize Cameras has not been run "
            "with them enabled.",
            sensor, chunk, sharedPlotModes)

    parameterNames = [NormalisedParameterName(parameterName) for parameterName in parameterNames]
    parameterCount = len(parameterNames)

    # Covariance → SE and correlation
    try:
        covarianceMatrix = [[covariance[rowIndex, columnIndex] for columnIndex in range(parameterCount)] for rowIndex in range(parameterCount)]
    except Exception as suppressedError:
        ReportSuppressedError("interiorOrientationViewer.BuildWidgetForSensor", suppressedError)
        return BuildMessageWithPlots(
            "The calibration covariance does not match the list of adjusted parameters "
            "(details in the Console).", sensor, chunk, sharedPlotModes)

    standardErrors = []
    for rowIndex in range(parameterCount):
        variance = covarianceMatrix[rowIndex][rowIndex]
        standardError = math.sqrt(variance) if variance is not None and variance > 0 and math.isfinite(variance) else float('nan')
        standardErrors.append(standardError)

    correlationMatrix = [[float('nan')]*parameterCount for _ in range(parameterCount)]
    for rowIndex in range(parameterCount):
        for columnIndex in range(parameterCount):
            if rowIndex == columnIndex:
                correlationMatrix[rowIndex][columnIndex] = 1.0
            else:
                standardErrorProduct = standardErrors[rowIndex]*standardErrors[columnIndex]
                correlationMatrix[rowIndex][columnIndex] = (covarianceMatrix[rowIndex][columnIndex]/standardErrorProduct) if standardErrorProduct and standardErrorProduct > 0 and math.isfinite(standardErrorProduct) else float('nan')

    # Resolve parameter estimates
    parameterEstimates = [GetParameterEstimate(calibration, parameter) for parameter in parameterNames]

    # Test statistic of the test against zero, estimate/SE, and its
    # two-sided p-value.
    testStatistics = [(parameterEstimate/standardError) if (math.isfinite(parameterEstimate) and math.isfinite(standardError) and standardError>0) else float('nan')
                      for parameterEstimate, standardError in zip(parameterEstimates, standardErrors)]
    pValues = [TwoSidedPValue(testStatistic) for testStatistic in testStatistics]

    # --- Degrees of freedom (shown as "–" when not positive) ---
    if redundancyBreakdowns is None:
        redundancyBreakdowns = ComputeChunkRedundancyBreakdowns(chunk)
    tiePointBreakdown, fullBreakdown = redundancyBreakdowns
    # The counts take the enabled images.  A disabled image that is still
    # oriented and has 2D tie points or marker projections, which Metashape
    # adjusts as well, is named in the notes panel and in the exports
    # (disabledImagesNote).
    disabledImagesNote = tiePointBreakdown.get("disabledImagesNote", "")
    degreesOfFreedomTiePoints = (tiePointBreakdown["redundancy"]
                  if tiePointBreakdown["redundancy"] > 0 else None)
    degreesOfFreedomAllObservations = (fullBreakdown["redundancy"]
                 if fullBreakdown["redundancy"] > 0 else None)

    displayParameterNames = [_DisplayParameterName(parameter) for parameter in parameterNames]
    richTextParameterNames = [_DisplayParameterNameRichText(parameter) for parameter in parameterNames]
    correlationHeaderNames = [_CorrelationHeaderName(parameter) for parameter in parameterNames]

    # Columns: parameter, estimate, standard error, estimate/SE, p-value,
    # the two estimated DoF, then one per correlation.
    leadingColumnCount = len(LEADING_COLUMN_HEADERS)
    columnCount = leadingColumnCount + parameterCount
    table = QtWidgets.QTableWidget(parameterCount, columnCount)
    table.setAlternatingRowColors(True)
    table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
    table.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
    table.verticalHeader().setVisible(False)
    table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeToContents)
    table.setHorizontalHeaderLabels(LEADING_COLUMN_HEADERS + correlationHeaderNames)
    # A correlation column with a short header (c_pix) names its row in
    # full in the tooltip of the header.
    for parameterIndex, correlationHeaderName in enumerate(correlationHeaderNames):
        if correlationHeaderName != displayParameterNames[parameterIndex]:
            headerItem = table.horizontalHeaderItem(leadingColumnCount + parameterIndex)
            if headerItem is not None:
                headerItem.setToolTip(displayParameterNames[parameterIndex])

    boldFont = QtGui.QFont(); boldFont.setBold(True)

    # Off-diagonal correlations, for the top correlated pairs.
    correlationPairs = []

    for rowIndex in range(parameterCount):
        # Parameter name
        parameterNameItem = QtWidgets.QTableWidgetItem(displayParameterNames[rowIndex])
        parameterNameItem.setTextAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        table.setItem(rowIndex, 0, parameterNameItem)

        # Estimate
        rowParameterEstimate = parameterEstimates[rowIndex]
        estimateItem = QtWidgets.QTableWidgetItem(FormatCalibrationValue(rowParameterEstimate))
        estimateItem.setTextAlignment(QtCore.Qt.AlignCenter)
        table.setItem(rowIndex, 1, estimateItem)

        # Standard error
        rowStandardError = standardErrors[rowIndex]
        standardErrorItem = QtWidgets.QTableWidgetItem(FormatCalibrationValue(rowStandardError))
        standardErrorItem.setTextAlignment(QtCore.Qt.AlignCenter)
        table.setItem(rowIndex, 2, standardErrorItem)

        # estimate/SE (signed). A parameter that is not distinguishable
        # from zero at three standard errors is shown in bold, without a
        # warning colour, in this column and the p-value column. A
        # principal distance cannot be zero, so c_pix is never emphasised.
        rowTestStatistic = testStatistics[rowIndex]
        isPrincipalDistance = parameterNames[rowIndex] == PRINCIPAL_DISTANCE_PARAMETER_NAME
        isEmphasised = (not isPrincipalDistance and math.isfinite(rowTestStatistic)
                        and abs(rowTestStatistic) < LOW_TEST_STATISTIC_LIMIT)
        testStatisticItem = QtWidgets.QTableWidgetItem(FormatSignificant(rowTestStatistic))
        testStatisticItem.setTextAlignment(QtCore.Qt.AlignCenter)
        if isEmphasised:
            testStatisticItem.setFont(boldFont)
        if math.isfinite(rowParameterEstimate) and math.isfinite(rowStandardError) and rowStandardError > 0:
            testStatisticToolTip = (f"estimate/SE = {rowParameterEstimate:.12g} / {rowStandardError:.12g} "
                                    f"= {rowTestStatistic:.12g}")
            if isEmphasised:
                testStatisticToolTip += (
                    "\n|estimate/SE| < 3: the estimate is not distinguishable from zero at that level. Compare "
                    "adjustments with and without the parameter, preferably with check points.")
            testStatisticItem.setToolTip(testStatisticToolTip)
        table.setItem(rowIndex, 3, testStatisticItem)

        # Two-sided p-value of the test against zero, a dash for c_pix.
        if isPrincipalDistance:
            pValueItem = QtWidgets.QTableWidgetItem("\u2013")
            pValueItem.setToolTip(PRINCIPAL_DISTANCE_P_VALUE_TOOLTIP)
        else:
            rowPValue = pValues[rowIndex]
            pValueItem = QtWidgets.QTableWidgetItem(FormatPValue(rowPValue))
            if isEmphasised:
                pValueItem.setFont(boldFont)
            if rowPValue is not None:
                pValueItem.setToolTip(
                    "Two-sided p-value of the test against zero under a normal approximation, conditional on the "
                    "reported standard errors and the adjustment model: "
                    "p = erfc(|estimate/SE| / \u221a2) {}".format(
                        "= {:.6g}".format(rowPValue) if rowPValue >= SMALLEST_SHOWN_P_VALUE else "< 1e-300"))
        pValueItem.setTextAlignment(QtCore.Qt.AlignCenter)
        table.setItem(rowIndex, 4, pValueItem)

        # DoF columns
        tiePointDegreesOfFreedomItem = QtWidgets.QTableWidgetItem(
            "–" if degreesOfFreedomTiePoints is None else "{:,}".format(int(degreesOfFreedomTiePoints)))
        tiePointDegreesOfFreedomItem.setTextAlignment(QtCore.Qt.AlignCenter)
        table.setItem(rowIndex, 5, tiePointDegreesOfFreedomItem)

        allObservationsDegreesOfFreedomItem = QtWidgets.QTableWidgetItem(
            "–" if degreesOfFreedomAllObservations is None else "{:,}".format(int(degreesOfFreedomAllObservations)))
        allObservationsDegreesOfFreedomItem.setTextAlignment(QtCore.Qt.AlignCenter)
        table.setItem(rowIndex, 6, allObservationsDegreesOfFreedomItem)

        # Correlations up to and including the diagonal.
        for columnIndex in range(parameterCount):
            tableColumnIndex = leadingColumnCount + columnIndex
            if columnIndex <= rowIndex:
                correlationCoefficient = correlationMatrix[rowIndex][columnIndex]
                correlationText = FormatFixedDecimals(correlationCoefficient, 3) if math.isfinite(correlationCoefficient) else "n/a"
                correlationItem = QtWidgets.QTableWidgetItem(correlationText)
                correlationItem.setTextAlignment(QtCore.Qt.AlignCenter)
                if columnIndex != rowIndex and math.isfinite(correlationCoefficient):
                    correlationPairs.append((abs(correlationCoefficient), correlationCoefficient, rowIndex, columnIndex))
                    if abs(correlationCoefficient) >= 0.90:
                        correlationItem.setFont(boldFont)
                table.setItem(rowIndex, tableColumnIndex, correlationItem)
            else:
                correlationItem = QtWidgets.QTableWidgetItem("")
                correlationItem.setFlags(QtCore.Qt.NoItemFlags)
                table.setItem(rowIndex, tableColumnIndex, correlationItem)

    # Top correlated pairs
    topCorrelationPairs = sorted([correlationPair for correlationPair in correlationPairs if correlationPair[2] != correlationPair[3]], key=lambda candidatePair: candidatePair[0], reverse=True)[:5]
    if topCorrelationPairs:
        correlationPairTexts = []
        for _, correlationCoefficient, rowIndex, columnIndex in topCorrelationPairs:
            correlationPairTexts.append(f"{richTextParameterNames[rowIndex]}\u2013{richTextParameterNames[columnIndex]}: {FormatFixedDecimals(correlationCoefficient,3)}")
        topPairsLabel = QtWidgets.QLabel("<b>Top correlated parameter pairs:</b> " + " | ".join(correlationPairTexts))
        topPairsLabel.setWordWrap(True)
    else:
        topPairsLabel = QtWidgets.QLabel("<b>Top correlated parameter pairs:</b> n/a")

    # Statistics badge: observation and unknown counts for the active chunk,
    # taken from the parts of the redundancy breakdown (no further pass
    # over the chunk). The tie points in the adjustment are those seen on
    # at least two enabled, oriented images, three unknowns each.
    datumDefect = fullBreakdown["datumDefect"]
    # The datum defect follows from counts of the control, not from its
    # geometry, so the anchored cases say so.
    datumDescription = ("free network" if datumDefect == 7 else
                        "fully anchored by the counted control" if datumDefect == 0
                        else "partly anchored by the counted control")
    statisticsLines = []
    statisticsLines.append(
        f"Chunk: {chunk.label}"
        f" | Oriented images: {CountOrientedImages(chunk):,}"
        f"{ObservedImagesText(tiePointBreakdown)}"
        f" | Tie points in the adjustment: {fullBreakdown['tiePointUnknowns'] // 3:,}")
    statisticsLines.append(
        f"Scalar observations (tie points): {tiePointBreakdown['observations']:,}"
        f" | Scalar observations (all): {fullBreakdown['observations']:,}")
    statisticsLines.append(
        f"Unknowns: {fullBreakdown['unknowns']:,}"
        f" (exterior orientation: {fullBreakdown['exteriorOrientationUnknowns']:,}"
        f", interior orientation: {fullBreakdown['interiorOrientationUnknowns']:,}"
        f", tie-point coordinates: {fullBreakdown['tiePointUnknowns']:,}"
        f", marker coordinates: {fullBreakdown['markerUnknowns']:,}"
        f", rolling shutter: {fullBreakdown['additionalModelUnknowns']:,})")
    statisticsLines.append(f"Datum defect: +{datumDefect} ({datumDescription})")
    if degreesOfFreedomTiePoints is not None:
        statisticsLines.append(f"Estimated DoF (tie points): {degreesOfFreedomTiePoints:,}")
    if degreesOfFreedomAllObservations is not None:
        statisticsLines.append(f"Estimated DoF (all observations): {degreesOfFreedomAllObservations:,}")
    if degreesOfFreedomTiePoints is None and degreesOfFreedomAllObservations is None:
        statisticsLines.append(
            "\u26a0\ufe0f Estimated DoF not available: the network has no more observations than unknowns.")
    if disabledImagesNote:
        statisticsLines.append("\u26a0\ufe0f " + disabledImagesNote)
    statisticsLabel = QtWidgets.QLabel("<br>".join(statisticsLines))
    statisticsLabel.setWordWrap(True)

    # Units line: the photosite pitch, and c, cx, and cy in mm (only when
    # the pitch is known).
    unitsText = BuildUnitsText(calibration, sensor)
    unitsLabel = QtWidgets.QLabel(unitsText)
    unitsLabel.setWordWrap(True)

    # Legend, in the notes panel.
    legendLabel = QtWidgets.QLabel(
         "Correlations are shown up to the diagonal, off-diagonal values in <b>bold</b> if |\u03c1| \u2265 0.90. "
         "<br><b>SE</b> (standard error) = \u221a(covariance diagonal), the a posteriori standard deviation of the estimate. "
         "<br><b>estimate/SE</b> is the statistic of a test of the parameter against zero. Values with <b>|estimate/SE| &lt; 3</b> and their p-values are shown in bold: the estimate is not distinguishable from zero at that level, which is a reason to compare adjustments with and without the parameter (preferably with check points), not a rule to fix it. A low |estimate/SE| can also belong to a well-determined value close to zero (a small SE), and for c<sub>pix</sub> a test against zero says nothing. "
         "<br><b>p-value (two-sided)</b> = erfc(|estimate/SE| / \u221a2), from the standard normal distribution, a normal approximation conditional on the reported standard errors and the adjustment model: the probability of an estimate at least this many standard errors from zero if the true value were zero. It is neither the probability that the parameter is zero nor a coverage probability. The cell of c<sub>pix</sub> shows a dash. "
         "<b>The emphasis is not a rule: a high |estimate/SE| does not show that a parameter improves the results. Judge the camera model also by the SE, the correlations, and a comparison of adjustments with and without the parameter.</b>"
         "<br><b>Estimated DoF (tie points)</b>: 2 \u00d7 (2D tie points of the tie points in the adjustment, the valid tie points seen on at least two enabled, oriented images) \u2212 (the tie-point unknowns and the unknowns of the camera stations, interior orientation groups, and images with such 2D tie points) + 7: the tie-point network alone, usually but not always below the value for all observations. "
         "<br><b>Estimated DoF (all observations)</b>: adds the image measurements (valid marker projections only) and the enabled reference coordinates of the markers in the adjustment (seen on at least two enabled, oriented images, or on one when they have an enabled reference location), the Metashape scale bars between points in the adjustment, and the enabled location and rotation priors of the images of the camera stations with 2D tie points or marker measurements, counts 3 unknowns per marker in the adjustment and the unknowns of the images with marker measurements only, and uses the datum defect of the control that takes part. "
         "<br>Both DoF values are estimates from counts, not from the rank of the adjustment: they assume no rank deficiency beyond the datum defect and no further constraints among the counted unknowns, one connected block of images, and control points that are not collinear (see About)."
         )
    legendLabel.setWordWrap(True)

    # ---- Distortion and residual plots ----
    plotsWidget = BuildDistortionPlotsForSensor(
        sensor, chunk, sharedPlotModes)

    # The table shows all its rows and columns. One line stands between it
    # and the plots: the two estimated degrees of freedom and the button of
    # the notes panel.
    FitTableToItsContents(table)
    degreesOfFreedomLabel = QtWidgets.QLabel(
        DescribeEstimatedDegreesOfFreedom(degreesOfFreedomTiePoints, degreesOfFreedomAllObservations))
    notesToggleButton = QtWidgets.QPushButton(NOTES_PANEL_SHOW_TEXT)
    notesToggleButton.setToolTip(NOTES_PANEL_BUTTON_TOOLTIP)
    # The button follows the text, so that it stays in view when a wide
    # table makes the page scroll sideways.
    summaryRow = QtWidgets.QHBoxLayout()
    summaryRow.addWidget(degreesOfFreedomLabel)
    summaryRow.addSpacing(12)
    summaryRow.addWidget(notesToggleButton)
    summaryRow.addStretch()

    # Assemble the page, in a scroll area so that a window too small for it
    # scrolls instead of clipping it.
    pageWidget = QtWidgets.QWidget()
    pageLayout = QtWidgets.QVBoxLayout(pageWidget); pageLayout.setContentsMargins(0,0,0,0)
    pageLayout.addWidget(table, 0, QtCore.Qt.AlignLeft)
    pageLayout.addLayout(summaryRow)
    if plotsWidget is not None:
        pageLayout.addWidget(plotsWidget)
    pageScrollArea = WrapPageInScrollArea(pageWidget)

    # The statistics, the units line, the top correlated pairs, and the
    # legend go into the notes panel to the right of the page.
    notesPanel = BuildNotesPanel(
        [statisticsLabel] + ([unitsLabel] if unitsText else []) + [topPairsLabel, legendLabel])

    tabContent = QtWidgets.QWidget()
    tabLayout = QtWidgets.QHBoxLayout(tabContent)
    tabLayout.setContentsMargins(0, 0, 0, 0)
    tabLayout.addWidget(pageScrollArea, 1)
    tabLayout.addWidget(notesPanel)
    tabContent._plotsWidget = plotsWidget
    # The CSV export of the dialog (Export CSV) writes the values of this
    # tab: the parameter table, the table of the vector fields, the values
    # of the profiles, and the coverage by radius.
    # c_pix gets no p-value, as in the table (a dash there).
    csvPValues = [None if parameterName == PRINCIPAL_DISTANCE_PARAMETER_NAME else pValue
                  for parameterName, pValue in zip(parameterNames, pValues)]
    parameterTable = ParameterTableData(
        parameterNames, parameterEstimates, standardErrors, testStatistics, csvPValues, correlationMatrix,
        degreesOfFreedomTiePoints, degreesOfFreedomAllObservations, degreesOfFreedomNote=disabledImagesNote)
    tabContent._csvContent = functools.partial(TabCsvContent, chunk, sensor, parameterTable, plotsWidget)
    tabContent._csvFileName = CsvFileName(chunk, sensor)
    # Export HTML writes the same values and the plots as one HTML page
    # (interiorOrientationReport).
    tabContent._reportData = functools.partial(TabReportData, chunk, sensor, parameterTable, plotsWidget)
    tabContent._reportFileName = ReportFileName(chunk, sensor)
    tabContent._applySharedNotesPanelState = functools.partial(
        _ApplySharedNotesPanelState, notesPanel, notesToggleButton, sharedPlotModes)
    notesToggleButton.clicked.connect(functools.partial(
        _OnNotesToggleClicked, tabContent, pageScrollArea, notesPanel, notesToggleButton, sharedPlotModes,
        groupName=InteriorOrientationGroupName(sensor, chunk)))
    # A tab that is built after the panel was opened on another tab opens it
    # as well.
    tabContent._applySharedNotesPanelState()
    return tabContent



# ===================================================================
# Distortion and residual plots
# ===================================================================

# Residuals computed per interior orientation group: larger groups are
# sampled systematically (every n-th projection of every image).
# Metashape's Residuals use every 2D tie point, and a sample makes every
# cell mean noisier, so only groups with more than two million matched
# keypoints are sampled.
RESIDUAL_SAMPLE_LIMIT = 2000000

# Metashape's Distortion Plot evaluates its vector fields at the centres
# of a grid of image cells: 60 columns and 41 rows for an image of 6016 x
# 4016 pixels (Geert's Project5 in Metashape 2.3.2, 8 October 2026: the
# numbers of all six modes and the positions of the drawn vectors).  The
# rule for other ratios of the numbers of pixel columns and rows is not
# known.  The viewer takes 60 columns and as many rows as cells of the
# same width need, rounded up (41 = ceil(60 x 4016 / 6016)), and at most
# 240 rows, four times the columns, so that an image of extreme
# proportions does not get millions of cells (1 x 4016 pixels would give
# 240,960 rows).
DISTORTION_GRID_COLUMNS = 60
DISTORTION_GRID_MAXIMUM_ROWS = 4 * DISTORTION_GRID_COLUMNS

# A remainder of the Total that the radial and decentring terms do not
# explain, when the calibration file cannot be read or holds corrections
# that the viewer cannot evaluate, counts as corrections applied by
# Metashape's unprojection when it exceeds this length somewhere on the
# grid (in pixels).  Without additional corrections it is rounding noise
# of the unprojection, about 1e-12 px in Project5.  Corrections that the
# viewer can evaluate are compared with the remainder instead
# (CheckAdditionalCorrections).
ADDITIONAL_CORRECTION_THRESHOLD_PIXELS = 1e-3

# Metashape draws the median vector of a field about 0.65 to 0.72 of a
# grid cell long (Project5), and colours the vectors from blue to red,
# red from twice the median length on.
MEDIAN_VECTOR_CELL_FRACTION = 0.68
RED_AT_MEDIAN_MULTIPLE = 2.0

# Vector magnification slider: positions 0 to 100 map exponentially to
# magnifications from 1/20 to 20, with 1 in the middle.
MAGNIFICATION_SLIDER_MINIMUM = 0
MAGNIFICATION_SLIDER_MAXIMUM = 100
MAGNIFICATION_SLIDER_DEFAULT = 50
MAXIMUM_USER_MAGNIFICATION = 20.0

# Vector-field modes in the order of their radio buttons, with the
# Coverage, which shows the 2D tie points per cell instead of vectors, and
# the modes with vectors, which the table of the vector fields lists.
VECTOR_MODE_IDS = (0, 1, 2, 3, 4, 5, 6)
VECTOR_FIELD_MODE_IDS = (0, 1, 2, 3, 4, 5)
COVERAGE_MODE_ID = 6

# Displacements shorter than this (in pixels) count as zero when the
# vector field is scaled: they are rounding noise, not distortion.
NEGLIGIBLE_DISPLACEMENT_PIXELS = 1e-6

# The constants of the automatic scale and the colours of the vectors, for
# the report (interiorOrientationReport), whose page draws the fields as
# the viewer does.
AUTOMATIC_MAGNIFICATION_CONSTANTS = {
    "medianVectorCellFraction": MEDIAN_VECTOR_CELL_FRACTION, "redAtMedianMultiple": RED_AT_MEDIAN_MULTIPLE,
    "maximumUserMagnification": MAXIMUM_USER_MAGNIFICATION,
    "negligibleDisplacementPixels": NEGLIGIBLE_DISPLACEMENT_PIXELS}

# A ray of a grid point whose projection lies farther than this from the
# point (in pixels) is an unprojection that has not converged, and the
# viewer refines it by Newton's method (RefinedRay).  A grid point whose
# refined ray does not project back within this distance either has no
# ray and is left out (ComputeGridDistortion): beyond 90 degrees from the
# camera axis of a fisheye, for example.  Beyond the largest distorted
# radius of the radial distortion polynomial Metashape 2.3.2 keeps the
# radial factor of that radius for the calibration of an interior
# orientation group, so positions there have rays.  A Metashape.Calibration
# that belongs to no group follows the polynomial.  Metashape's unprojection
# stopped before it converged at 618 of 2,700 grid points in Geert's
# processing of Camillo Ressl's DJI M4E images (10 October 2026).
GRID_ROUND_TRIP_TOLERANCE_PIXELS = 1e-3

# Newton's method of RefinedRay: at most this many iterations, the step of
# its numerical derivatives (in normalised image coordinates), and how
# close the projection must come to the pixel position (px) for it to
# stop.
RAY_REFINEMENT_MAXIMUM_ITERATIONS = 60
RAY_REFINEMENT_DERIVATIVE_STEP = 1e-6
RAY_REFINEMENT_STOP_PIXELS = 1e-9

# A ray of Metashape's unprojection whose projection lies within this
# many px of the position is taken as it is, and any other
# is refined, so that every vector field rests on the exact inverse of
# the camera model, whatever the stopping rule of Metashape's
# unprojection, which is not documented.
RAY_ACCEPTANCE_PIXELS = 1e-6


# Camera models of the viewer with a distortion model of their own: the
# frame model and Metashape's two fisheye models (Metashape 2.3 manual,
# Appendix D, pp. 231 to 232).
DISTORTION_CAMERA_MODELS = ("frame", "fisheye", "equisolid fisheye")
FISHEYE_CAMERA_MODELS = ("fisheye", "equisolid fisheye")


def CalibrationModel(calibration):
    """Camera model of a calibration: "frame", "fisheye" (Metashape's
    equidistant fisheye model, Sensor.Type Fisheye or EquidistantFisheye),
    "equisolid fisheye" (EquisolidFisheye), or "other" (spherical,
    cylindrical, RPC). A calibration without a type counts as a frame
    camera."""
    calibrationType = getattr(calibration, "type", None)
    if calibrationType is None:
        return "frame"
    sensorTypes = getattr(getattr(Metashape, "Sensor", None), "Type", None)
    if sensorTypes is None or calibrationType == getattr(sensorTypes, "Frame", None):
        return "frame"
    for typeName in ("Fisheye", "EquidistantFisheye"):
        fisheyeType = getattr(sensorTypes, typeName, None)
        if fisheyeType is not None and calibrationType == fisheyeType:
            return "fisheye"
    equisolidType = getattr(sensorTypes, "EquisolidFisheye", None)
    if equisolidType is not None and calibrationType == equisolidType:
        return "equisolid fisheye"
    return "other"


def RayDirectionForNormalisedPoint(normalisedX, normalisedY, calibrationModel):
    """Camera-frame direction (x, y, 1) of the ray whose undistorted image
    is the given normalised point. For a frame camera this is the point on
    the plane z = 1. In Metashape's equidistant fisheye model the
    undistorted radius r is the angle from the camera axis, theta (the
    camera axis is the z axis of the camera frame), and in its equisolid
    model it is 2 sin(theta / 2) (Metashape 2.3 manual, Appendix D, pp.
    231 to 232), so the point is scaled by tan(theta) / r. None for a
    fisheye point at 90 degrees or more from the axis."""
    if calibrationModel not in FISHEYE_CAMERA_MODELS:
        return (normalisedX, normalisedY, 1.0)
    radius = math.hypot(normalisedX, normalisedY)
    if radius < 1e-12:
        return (normalisedX, normalisedY, 1.0)
    if calibrationModel == "equisolid fisheye":
        if radius >= 2.0 * math.sin(0.25 * math.pi) - 1e-9:
            return None
        angleFromAxis = 2.0 * math.asin(0.5 * radius)
    else:
        if radius >= 0.5 * math.pi - 1e-9:
            return None
        angleFromAxis = radius
    scale = math.tan(angleFromAxis) / radius
    return (normalisedX * scale, normalisedY * scale, 1.0)


def SemiDiagonalAnglesDegrees(pixelColumnCount, pixelRowCount):
    """Directions of the four image semi-diagonals in degrees from the
    image x axis: plus and minus atan(pixel rows / pixel columns) and the
    same two directions turned by 180 degrees (45 degrees only for as many
    pixel rows as pixel columns)."""
    diagonalDegrees = math.degrees(math.atan2(pixelRowCount, pixelColumnCount))
    return (diagonalDegrees, 180.0 - diagonalDegrees,
            180.0 + diagonalDegrees, 360.0 - diagonalDegrees)


def ChooseTickInterval(minimumValue, maximumValue, maximumIntervalCount=6):
    """Tick interval from the 1, 2, 5 sequence that divides the range from
    minimumValue to maximumValue into at most maximumIntervalCount
    intervals (1 for an empty range)."""
    span = abs(maximumValue - minimumValue)
    if not math.isfinite(span) or span <= 0.0:
        return 1.0
    smallestInterval = span / max(1, maximumIntervalCount)
    exponent = math.floor(math.log10(smallestInterval))
    for mantissa in (1.0, 2.0, 5.0):
        interval = mantissa * 10.0 ** exponent
        if interval >= smallestInterval * (1.0 - 1e-9):
            return interval
    return 10.0 ** (exponent + 1)


def TickLabelDecimals(interval):
    """Number of decimals that shows every multiple of a tick interval
    exactly: 0 for whole numbers, 1 for 0.5, 2 for 0.25, at most 9."""
    if not interval or not math.isfinite(interval):
        return 0
    for decimals in range(10):
        scaledInterval = abs(interval) * 10 ** decimals
        roundedInterval = round(scaledInterval)
        if roundedInterval >= 1 and abs(scaledInterval - roundedInterval) <= 1e-6 * scaledInterval:
            return decimals
    return 9


def FormatTickLabel(value, interval):
    """Axis label of a tick value with the decimals its interval needs."""
    decimals = TickLabelDecimals(interval)
    if abs(value) < 0.5 * 10.0 ** (-decimals):
        value = 0.0
    return "{:.{}f}".format(value, decimals)


def ChooseReferenceVectorLength(screenPixelsPerImagePixel, targetScreenPixels=50.0):
    """Displacement in pixels of the reference vector of the vector
    plots: the largest value of the 1, 2, 5 sequence that is drawn no
    longer than targetScreenPixels, so between 40 and 100 per cent of it.
    None if the scale is not positive."""
    if not (screenPixelsPerImagePixel > 0.0 and math.isfinite(screenPixelsPerImagePixel)):
        return None
    largestLength = targetScreenPixels / screenPixelsPerImagePixel
    exponent = math.floor(math.log10(largestLength))
    for mantissa in (5.0, 2.0, 1.0):
        length = mantissa * 10.0 ** exponent
        if length <= largestLength * (1.0 + 1e-9):
            return length
    return 10.0 ** exponent


def MagnificationForSliderPosition(sliderPosition):
    """User magnification of the vector field for a slider position:
    exponential from 1/20 at the minimum through 1 at the default (middle)
    position to 20 at the maximum."""
    halfRange = 0.5 * (MAGNIFICATION_SLIDER_MAXIMUM - MAGNIFICATION_SLIDER_MINIMUM)
    return math.exp((sliderPosition - MAGNIFICATION_SLIDER_DEFAULT) / halfRange
                    * math.log(MAXIMUM_USER_MAGNIFICATION))


def FormatMagnification(magnificationFactor):
    """Read-out of a magnification factor beside the slider."""
    return "\u00d7{:.1f}".format(magnificationFactor) if magnificationFactor >= 0.1 else "\u00d7{:.2f}".format(magnificationFactor)


def AutomaticMagnification(vectors, pixelColumnCount, pixelRowCount):
    """Automatic scale of a vector field as in Metashape's Distortion
    Plot: the number of times a vector is drawn longer than its
    displacement, both in the pixel coordinates of the image as drawn,
    such that the median vector is MEDIAN_VECTOR_CELL_FRACTION of a grid
    cell long (the cell width in pixels divided by the median length,
    times that fraction).  When more than half of the vectors are zero
    the longest one is drawn one cell long, a field of rounding noise is
    not magnified (1), and without vectors there is no scale (None).
    Vectors that are not finite numbers are left out, as in the plot."""
    lengths = sorted(math.hypot(displacementX, displacementY)
                     for _, _, displacementX, displacementY in vectors
                     if math.isfinite(displacementX) and math.isfinite(displacementY))
    if not lengths:
        return None
    cellWidthPixels = pixelColumnCount / float(DistortionGridShape(pixelColumnCount, pixelRowCount)[0])
    medianLength = lengths[len(lengths) // 2]
    if medianLength > NEGLIGIBLE_DISPLACEMENT_PIXELS:
        return MEDIAN_VECTOR_CELL_FRACTION * cellWidthPixels / medianLength
    if lengths[-1] > NEGLIGIBLE_DISPLACEMENT_PIXELS:
        return cellWidthPixels / lengths[-1]
    return 1.0


def TotalMagnification(vectors, pixelColumnCount, pixelRowCount, userMagnification):
    """Total magnification of a vector field: the automatic scale
    (AutomaticMagnification) times the factor of the Vector scale
    slider, or None without vectors.  Metashape's plot presumably gives
    this number, rounded (for example x20 for the radial distortion)."""
    automaticMagnification = AutomaticMagnification(vectors, pixelColumnCount, pixelRowCount)
    return None if automaticMagnification is None else automaticMagnification * userMagnification


def FormatTotalMagnification(magnification):
    """A total magnification with three significant digits and thousands
    separators (x19.5, x1,710, x0.195), or "n/a"."""
    if magnification is None or not (math.isfinite(magnification) and magnification > 0.0):
        return "n/a"
    decimals = 2 - int(math.floor(math.log10(magnification)))
    if decimals <= 0:
        return "\u00d7{:,.0f}".format(round(magnification, decimals))
    return "\u00d7{:.{}f}".format(magnification, decimals)


# Tooltips of the Vector scale slider and of the total magnification.
VECTOR_SCALE_TOOLTIP = (
    "The automatic scale draws the median vector about 0.68 of a grid cell long, as Metashape does.\n"
    "The slider multiplies this automatic scale from 1/20 to 20 times (\u00d71 in the middle,\n"
    "the automatic scale unchanged): left for shorter vectors, right for longer vectors.\n"
    "The total magnification beside it is the automatic scale times this factor.")
TOTAL_MAGNIFICATION_TOOLTIP = (
    "Total magnification: how many times a drawn vector is longer than the displacement it shows,\n"
    "both in the pixel coordinates of the image as drawn. It is the automatic scale, which draws\n"
    "the median vector about 0.68 of a grid cell long, times the factor of the Vector scale slider\n"
    "(1/20 to 20). Metashape's Distortion Plot presumably shows this number, rounded, for example\n"
    "\u00d720 for the radial distortion, while the factor of the slider stays \u00d71.")


def ResidualSamplingStride(projectionCount, sampleLimit):
    """Step n of the systematic sample (every n-th projection) that keeps
    the number of residuals near sampleLimit: n = ceil(projectionCount /
    sampleLimit), and 1 when no sampling is needed."""
    if sampleLimit <= 0 or projectionCount <= sampleLimit:
        return 1
    return int(math.ceil(projectionCount / float(sampleLimit)))


def SelectGroupImages(sensor, chunk):
    """Enabled, oriented images of one interior orientation group (slave
    images of a multi-camera rig included), and the number of images left
    out for each reason. Keyframes of an animation track are not images
    and are neither used nor counted."""
    groupImages = []
    skippedImageCounts = {"disabled": 0, "notAligned": 0, "otherGroup": 0, "withoutGroup": 0}
    for camera in chunk.cameras:
        if IsKeyframe(camera):
            continue
        if camera.sensor is None:
            skippedImageCounts["withoutGroup"] += 1
        elif camera.sensor.key != sensor.key:
            skippedImageCounts["otherGroup"] += 1
        elif not camera.enabled:
            skippedImageCounts["disabled"] += 1
        elif camera.transform is None:
            skippedImageCounts["notAligned"] += 1
        else:
            groupImages.append(camera)
    return groupImages, skippedImageCounts


def DescribeImageSelection(usedImageCount, skippedImageCounts):
    """Console text that lists the images used and left out, in words."""
    return ("images used: {:,}, disabled: {:,}, not oriented: {:,}, "
            "in other interior orientation groups: {:,}, "
            "without an interior orientation group: {:,}").format(
        usedImageCount, skippedImageCounts["disabled"], skippedImageCounts["notAligned"],
        skippedImageCounts["otherGroup"], skippedImageCounts["withoutGroup"])


def CountGroupProjections(groupImages, tiePoints):
    """Number of tie-point projections on the given images. An image
    without projections (KeyError) adds none and is not reported."""
    if tiePoints is None:
        return 0
    projectionCount = 0
    for camera in groupImages:
        try:
            imageProjections = tiePoints.projections[camera]
        except KeyError:
            continue
        except Exception as suppressedError:
            ReportSuppressedError("interiorOrientationViewer.CountGroupProjections", suppressedError)
            continue
        if imageProjections is not None:
            projectionCount += len(imageProjections)
    return projectionCount


def ChooseAvailableVectorMode(requestedMode, availableModes):
    """The requested vector-field mode when this tab offers it, otherwise
    the first available mode (Total, when it is available)."""
    availableModes = list(availableModes)
    if requestedMode in availableModes:
        return requestedMode
    return availableModes[0] if availableModes else requestedMode


def DistortionGridShape(pixelColumnCount, pixelRowCount):
    """(columns, rows) of the grid of image cells at whose centres the
    vector fields are evaluated, as in Metashape's Distortion Plot: 60
    columns and as many rows as cells of the same width need, rounded
    up, at least one and at most DISTORTION_GRID_MAXIMUM_ROWS (60 and 41
    for 6016 x 4016 pixels).  The rule was established for 6016 x 4016
    pixels and is assumed for other numbers of pixel columns and rows."""
    columns = DISTORTION_GRID_COLUMNS
    rows = max(1, int(math.ceil(columns * float(pixelRowCount) / float(pixelColumnCount) - 1e-9)))
    return columns, min(rows, DISTORTION_GRID_MAXIMUM_ROWS)


def DistortionGridPositions(pixelColumnCount, pixelRowCount):
    """Centres of the cells of the grid of DistortionGridShape, row by
    row, in pixel coordinates."""
    columns, rows = DistortionGridShape(pixelColumnCount, pixelRowCount)
    cellWidth = pixelColumnCount / float(columns)
    cellHeight = pixelRowCount / float(rows)
    return [((column + 0.5) * cellWidth, (row + 0.5) * cellHeight)
            for row in range(rows) for column in range(columns)]


def _VectorComponents(vector, count):
    """The first *count* components of a Metashape.Vector or a sequence."""
    try:
        return tuple(float(getattr(vector, name)) for name in ("x", "y", "z")[:count])
    except (AttributeError, TypeError):
        return tuple(float(vector[index]) for index in range(count))


def NormalisedPointForRayDirection(rayDirection, calibrationModel):
    """Normalised image coordinates (x, y) of the camera model before lens
    distortion of a ray in camera coordinates: x = X/Z and y = Y/Z for a
    frame camera, and for a fisheye camera the point at the undistorted
    radial distance theta (equidistant model) or 2 sin(theta / 2)
    (equisolid model) in the direction of the ray, with theta its angle
    from the camera axis (Metashape 2.3 manual, Appendix D, pp. 231 to
    232).  The inverse of RayDirectionForNormalisedPoint for rays less
    than 90 degrees from the camera axis: a fisheye ray at 90 degrees or
    more is mapped as well, whereas RayDirectionForNormalisedPoint gives
    no ray (x, y, 1) there, so that the Difference leaves out grid points
    that the Total keeps.  None for a frame ray that does not point
    forwards, for a fisheye ray along the camera axis backwards, and for a
    ray that is not a finite number."""
    if not all(math.isfinite(component) for component in rayDirection):
        return None
    rayX, rayY, rayZ = rayDirection
    if calibrationModel in FISHEYE_CAMERA_MODELS:
        lateralLength = math.hypot(rayX, rayY)
        if lateralLength <= 1e-15 * max(1.0, abs(rayZ)):
            return (0.0, 0.0) if rayZ > 0.0 else None
        angleFromAxis = math.atan2(lateralLength, rayZ)
        radius = (2.0 * math.sin(0.5 * angleFromAxis) if calibrationModel == "equisolid fisheye"
                  else angleFromAxis)
        return (rayX / lateralLength * radius, rayY / lateralLength * radius)
    if not rayZ > 0.0:
        return None
    return (rayX / rayZ, rayY / rayZ)


def _Coefficient(calibration, name):
    value = getattr(calibration, name, 0.0)
    return float(value) if value else 0.0


def RadialTerm(calibration, radiusSquared):
    """k1 r^2 + k2 r^4 + k3 r^6 + k4 r^8 of Metashape's radial distortion
    at the squared normalised radius *radiusSquared*: the radial
    displacement divided by the radius, a number."""
    return radiusSquared * (_Coefficient(calibration, "k1") + radiusSquared * (
        _Coefficient(calibration, "k2") + radiusSquared * (
            _Coefficient(calibration, "k3") + radiusSquared * _Coefficient(calibration, "k4"))))


def LensDistortionDisplacements(calibration, normalisedX, normalisedY, radialOffset=0.0):
    """(radialX, radialY, decentringX, decentringY): the displacements of
    the normalised point (x, y) by the radial terms k1 to k4 and by the
    decentring terms p1 to p4, with Metashape's factor 1 + p3 r^2 + p4
    r^4, in normalised image coordinates (Metashape 2.3 manual, Appendix
    D).  With *radialOffset*, the radial term at the zero crossing of the
    balanced radial distortion (BalancedRadialOffset), the radial
    displacements are those of the balanced form, which subtracts the
    displacement proportional to the radius that this term gives."""
    radiusSquared = normalisedX * normalisedX + normalisedY * normalisedY
    radialFactor = RadialTerm(calibration, radiusSquared) - radialOffset
    p1Value, p2Value = _Coefficient(calibration, "p1"), _Coefficient(calibration, "p2")
    higherOrderFactor = 1.0 + radiusSquared * (_Coefficient(calibration, "p3")
                                               + radiusSquared * _Coefficient(calibration, "p4"))
    return (normalisedX * radialFactor, normalisedY * radialFactor,
            higherOrderFactor * (p1Value * (radiusSquared + 2.0 * normalisedX * normalisedX)
                                 + 2.0 * p2Value * normalisedX * normalisedY),
            higherOrderFactor * (p2Value * (radiusSquared + 2.0 * normalisedY * normalisedY)
                                 + 2.0 * p1Value * normalisedX * normalisedY))


def _ProjectsBackOnto(calibration, ray, pixelX, pixelY):
    """True when the projection of *ray* (the calibration's project())
    lies within GRID_ROUND_TRIP_TOLERANCE_PIXELS of (pixelX, pixelY), or
    when the calibration has no project() to check it with."""
    project = getattr(calibration, "project", None)
    if project is None:
        return True
    projected = project(ray)
    if projected is None:
        return False
    projectedX, projectedY = _VectorComponents(projected, 2)
    return math.hypot(projectedX - pixelX, projectedY - pixelY) <= GRID_ROUND_TRIP_TOLERANCE_PIXELS


def _ProjectedNormalisedPoint(calibration, calibrationModel, normalisedX, normalisedY):
    """Pixel position (x, y) of the ray whose undistorted normalised point
    is (normalisedX, normalisedY), by the calibration's project(), or None
    when there is no such ray or no finite position."""
    rayDirection = RayDirectionForNormalisedPoint(normalisedX, normalisedY, calibrationModel)
    if rayDirection is None:
        return None
    projected = calibration.project(Metashape.Vector(list(rayDirection)))
    if projected is None:
        return None
    projectedX, projectedY = _VectorComponents(projected, 2)
    if not (math.isfinite(projectedX) and math.isfinite(projectedY)):
        return None
    return projectedX, projectedY


def _ProjectionJacobian(calibration, calibrationModel, normalisedX, normalisedY):
    """(dx/du, dx/dv, dy/du, dy/dv): the derivatives of the pixel position
    (x, y) with respect to the undistorted normalised point (u, v), by
    central differences, or None."""
    step = RAY_REFINEMENT_DERIVATIVE_STEP
    columns = []
    for offsetX, offsetY in ((step, 0.0), (0.0, step)):
        plus = _ProjectedNormalisedPoint(calibration, calibrationModel, normalisedX + offsetX, normalisedY + offsetY)
        minus = _ProjectedNormalisedPoint(calibration, calibrationModel, normalisedX - offsetX,
                                          normalisedY - offsetY)
        if plus is None or minus is None:
            return None
        columns.append(((plus[0] - minus[0]) / (2.0 * step), (plus[1] - minus[1]) / (2.0 * step)))
    (derivativeXU, derivativeYU), (derivativeXV, derivativeYV) = columns
    return derivativeXU, derivativeXV, derivativeYU, derivativeYV


def _JacobianDeterminant(jacobian):
    derivativeXU, derivativeXV, derivativeYU, derivativeYV = jacobian
    return derivativeXU * derivativeYV - derivativeXV * derivativeYU


def RefinedRay(calibration, ray, pixelX, pixelY, calibrationModel, startPoint=None):
    """The ray of the pixel position (pixelX, pixelY) by Newton's method
    on the undistorted normalised point of the camera model, with the
    calibration's own project() and its derivatives by central
    differences (_ProjectionJacobian), started at the normalised point of
    *ray* (the calibration's unproject()) or else at *startPoint*.

    Metashape's unproject() appears to invert the lens distortion by the
    fixed-point iteration x = (x_d - tangential(x)) / radial(x), started
    at the distorted point, and to stop when the steps become small: this
    reproduces two round trips of Geert's processing of Camillo Ressl's
    DJI M4E images to every printed digit after 9 and 10 steps (10
    October 2026), but not the number of grid points whose unprojection
    projects back within GRID_ROUND_TRIP_TOLERANCE_PIXELS, so the exact
    stopping rule is not known.
    Where the radial distortion approaches the largest distorted radius
    of its polynomial the iteration converges slowly, so it can stop
    before its projection is back on the position.  Each Newton step is
    halved until the projection comes closer to the position at a point
    where the determinant of the derivatives is positive, so that the
    refinement stays on one branch of a mapping that folds back, as the
    raw polynomial would beyond its largest distorted radius (for the
    calibration of an interior orientation group, Metashape 2.3.2 keeps
    the radial factor of that radius instead, so its mapping keeps growing
    there).  A positive determinant holds locally only: the solution is a
    locally converged inverse, not one proved unique (the decentring
    distortion can map two undistorted points to one position).

    Returns the ray (Metashape.Vector) whose projection lies within
    GRID_ROUND_TRIP_TOLERANCE_PIXELS of the position, or None: no such ray
    was found, for example at 90 degrees or more from the camera axis of
    a fisheye."""
    start = None
    if ray is not None:
        start = NormalisedPointForRayDirection(_VectorComponents(ray, 3), calibrationModel)
    if start is None:
        start = startPoint
    if start is None:
        return None
    normalisedX, normalisedY = start
    current = _ProjectedNormalisedPoint(calibration, calibrationModel, normalisedX, normalisedY)
    jacobian = _ProjectionJacobian(calibration, calibrationModel, normalisedX, normalisedY)
    if current is None or jacobian is None or not _JacobianDeterminant(jacobian) > 0.0:
        return None
    distance = math.hypot(current[0] - pixelX, current[1] - pixelY)
    for _ in range(RAY_REFINEMENT_MAXIMUM_ITERATIONS):
        if distance <= RAY_REFINEMENT_STOP_PIXELS:
            break
        derivativeXU, derivativeXV, derivativeYU, derivativeYV = jacobian
        determinant = _JacobianDeterminant(jacobian)
        errorX, errorY = current[0] - pixelX, current[1] - pixelY
        stepU = (derivativeYV * errorX - derivativeXV * errorY) / determinant
        stepV = (-derivativeYU * errorX + derivativeXU * errorY) / determinant
        stepScale = 1.0
        while True:
            candidateX, candidateY = normalisedX - stepScale * stepU, normalisedY - stepScale * stepV
            candidate = _ProjectedNormalisedPoint(calibration, calibrationModel, candidateX, candidateY)
            if candidate is not None:
                candidateDistance = math.hypot(candidate[0] - pixelX, candidate[1] - pixelY)
                if candidateDistance < distance:
                    candidateJacobian = _ProjectionJacobian(calibration, calibrationModel, candidateX, candidateY)
                    if candidateJacobian is not None and _JacobianDeterminant(candidateJacobian) > 0.0:
                        break
            stepScale *= 0.5
            if stepScale < 1.0 / 4096.0:
                break
        if stepScale < 1.0 / 4096.0:
            break
        normalisedX, normalisedY = candidateX, candidateY
        current, distance, jacobian = candidate, candidateDistance, candidateJacobian
    if not distance <= GRID_ROUND_TRIP_TOLERANCE_PIXELS:
        return None
    rayDirection = RayDirectionForNormalisedPoint(normalisedX, normalisedY, calibrationModel)
    return Metashape.Vector(list(rayDirection)) if rayDirection is not None else None


def _RoundTripPixels(calibration, ray, pixelX, pixelY):
    """How far the projection of *ray* lies from the pixel position, in
    pixel coordinates (px), or None when there is no ray or no finite
    projection."""
    if ray is None:
        return None
    projected = calibration.project(ray)
    if projected is None:
        return None
    projectedX, projectedY = _VectorComponents(projected, 2)
    roundTrip = math.hypot(projectedX - pixelX, projectedY - pixelY)
    return roundTrip if math.isfinite(roundTrip) else None


def DistortedNormalisedPoint(calibration, pixelColumnCount, pixelRowCount, pixelX, pixelY):
    """The normalised point of a pixel position before the lens distortion
    is removed, ((x - x0 - y_d b2) / (c_pix + b1), (y - y0) / c_pix) with
    the principal point (x0, y0), or None without a positive c_pix: the
    start of RefinedRay when the unprojection gives no ray."""
    principalDistancePixels = getattr(calibration, "f", None)
    if not IsPositiveNumber(principalDistancePixels):
        return None
    distortedY = (pixelY - pixelRowCount * 0.5 - _Coefficient(calibration, "cy")) / principalDistancePixels
    distortedX = ((pixelX - pixelColumnCount * 0.5 - _Coefficient(calibration, "cx")
                   - distortedY * _Coefficient(calibration, "b2"))
                  / (principalDistancePixels + _Coefficient(calibration, "b1")))
    return distortedX, distortedY


def ExactRay(calibration, pixelX, pixelY, calibrationModel, startPoint=None, rayReport=None):
    """The ray of a pixel position: the calibration's unproject() when its
    projection lies within RAY_ACCEPTANCE_PIXELS of the position (or when
    the calibration has no project() to check it with), else the ray of
    RefinedRay.  When no refined ray is found, the unprojection's own ray
    is kept if its projection lies within
    GRID_ROUND_TRIP_TOLERANCE_PIXELS of the position (a fisheye ray at 90
    degrees or more from the camera axis, which RefinedRay does not
    reach), and otherwise the position has no ray (None).  *rayReport*
    counts the refined rays of unprojections that had not converged
    within GRID_ROUND_TRIP_TOLERANCE_PIXELS ("refined") and keeps their
    largest round trip ("largestUnrefinedRoundTripPixels")."""
    ray = calibration.unproject(Metashape.Vector([pixelX, pixelY]))
    if getattr(calibration, "project", None) is None:
        return ray
    roundTrip = _RoundTripPixels(calibration, ray, pixelX, pixelY)
    if roundTrip is not None and roundTrip <= RAY_ACCEPTANCE_PIXELS:
        return ray
    hasConverged = roundTrip is not None and roundTrip <= GRID_ROUND_TRIP_TOLERANCE_PIXELS
    refinedRay = RefinedRay(calibration, ray, pixelX, pixelY, calibrationModel, startPoint)
    if refinedRay is None:
        return ray if hasConverged else None
    if not hasConverged and rayReport is not None:
        if roundTrip is None:
            # No round trip of the unprojection to report (none, or no
            # projection): counted apart, so that the report does not say
            # that it missed by up to 0 px.
            rayReport["refinedWithoutRoundTrip"] = rayReport.get("refinedWithoutRoundTrip", 0) + 1
        else:
            rayReport["refined"] = rayReport.get("refined", 0) + 1
            rayReport["largestUnrefinedRoundTripPixels"] = max(
                rayReport.get("largestUnrefinedRoundTripPixels", 0.0), roundTrip)
    return refinedRay


# The radial distortion of a camera model as Metashape's, a function of
# the undistorted normalised radius r, reaches its largest distorted
# radius r (1 + k1 r^2 + k2 r^4 + k3 r^6 + k4 r^8) where its derivative
# 1 + 3 k1 r^2 + 5 k2 r^4 + 7 k3 r^6 + 9 k4 r^8 first becomes zero.  The
# search looks up to this multiple of the normalised radius of the
# farthest image corner.
RADIAL_MAXIMUM_SEARCH_FACTOR = 2.0
RADIAL_MAXIMUM_SEARCH_STEPS = 4000

def _DistortedRadiusDerivative(coefficients, radius):
    """d/dr of r (1 + k1 r^2 + k2 r^4 + k3 r^6 + k4 r^8) for the
    coefficients (k1, k2, k3, k4): 1 + 3 k1 r^2 + 5 k2 r^4 + 7 k3 r^6 +
    9 k4 r^8."""
    radiusSquared = radius * radius
    return 1.0 + radiusSquared * (3.0 * coefficients[0] + radiusSquared * (
        5.0 * coefficients[1] + radiusSquared * (7.0 * coefficients[2] + radiusSquared * 9.0 * coefficients[3])))


RadialDistortionMaximumResult = collections.namedtuple(
    "RadialDistortionMaximumResult", ("undistortedRadius", "distortedRadiusPixels", "cornersBeyond"))


def RadialDistortionMaximum(calibration, pixelColumnCount, pixelRowCount):
    """Where the radial distortion polynomial of *calibration* stops
    growing: the undistorted normalised radius at which the distorted
    radius of the polynomial is largest, that largest radius in pixels
    from the principal point (c_pix times the distorted normalised
    radius), and the number of image corners farther from the principal
    point.  Within this radius the polynomial is a one-to-one mapping of
    the radius, beyond it the polynomial alone would fold back, but for
    the calibration of an interior orientation group Metashape 2.3.2
    keeps the radial factor of this radius for larger radii, so that its
    projection keeps growing and the positions beyond have rays.  A
    Metashape.Calibration that belongs to no group follows the
    polynomial.  None when the distorted radius keeps growing up to
    RADIAL_MAXIMUM_SEARCH_FACTOR times the normalised radius of the
    farthest image corner, or without a positive c_pix.  Camillo Ressl's
    DJI M4E calibration (9 October 2026): 3,302 px, beyond which lie the
    bottom corners, 3,332 px from the principal point."""
    principalDistancePixels = getattr(calibration, "f", None)
    if not IsPositiveNumber(principalDistancePixels):
        return None
    coefficients = [_Coefficient(calibration, name) for name in ("k1", "k2", "k3", "k4")]
    if not any(coefficients):
        return None
    principalPointX = pixelColumnCount * 0.5 + _Coefficient(calibration, "cx")
    principalPointY = pixelRowCount * 0.5 + _Coefficient(calibration, "cy")
    cornerRadii = [math.hypot(cornerX - principalPointX, cornerY - principalPointY)
                   for cornerX in (0.0, float(pixelColumnCount)) for cornerY in (0.0, float(pixelRowCount))]

    derivative = functools.partial(_DistortedRadiusDerivative, coefficients)
    searchLimit = RADIAL_MAXIMUM_SEARCH_FACTOR * max(cornerRadii) / principalDistancePixels
    previousRadius = 0.0
    for stepIndex in range(1, RADIAL_MAXIMUM_SEARCH_STEPS + 1):
        radius = searchLimit * stepIndex / RADIAL_MAXIMUM_SEARCH_STEPS
        if derivative(radius) > 0.0:
            previousRadius = radius
            continue
        lowRadius, highRadius = previousRadius, radius
        for _ in range(100):
            middleRadius = 0.5 * (lowRadius + highRadius)
            if derivative(middleRadius) > 0.0:
                lowRadius = middleRadius
            else:
                highRadius = middleRadius
        undistortedRadius = 0.5 * (lowRadius + highRadius)
        distortedRadiusPixels = principalDistancePixels * undistortedRadius * (
            1.0 + RadialTerm(calibration, undistortedRadius * undistortedRadius))
        return RadialDistortionMaximumResult(undistortedRadius, distortedRadiusPixels,
                                             sum(1 for cornerRadius in cornerRadii
                                                 if cornerRadius > distortedRadiusPixels))
    return None


def RayReportLines(rayReport, radialMaximum):
    """Console lines (also comment lines of the CSV export and lines of the
    report) on the rays of the grid points: how many unprojections were
    refined by Newton's method, how many grid points have no ray, and
    where the radial distortion polynomial reaches its largest distorted
    radius when image corners lie farther out.  For the calibration of an
    interior orientation group, Metashape 2.3.2 does not follow the
    polynomial beyond that radius: it keeps the radial factor of that
    radius for larger radii, so that its projection keeps growing and
    positions farther out have rays (c_pix = 900 px and k1 = -0.5, and
    Camillo Ressl's DJI M4E calibration, all four corners with a ray).
    Only the count of grid points without a ray, whose ray MetaFOCUS
    cannot find, says that a grid point has no ray."""
    lines = []
    gridPointCount = rayReport.get("gridPointCount", 0)
    refinedCount = rayReport.get("refined", 0)
    if refinedCount:
        # The refined grid points only, as the GSD tools count them.
        lines.append("Rays: for {:,} of the {:,} grid points, the {} of Metashape's unprojection projected back more "
                     "than {:g} px from the grid point (up to {} px), so MetaFOCUS refined {} by Newton's method. "
                     "Metashape's Distortion Plot keeps its own rays, so its numbers can differ in the outermost "
                     "cells.".format(
                         refinedCount, gridPointCount, "ray" if refinedCount == 1 else "rays",
                         GRID_ROUND_TRIP_TOLERANCE_PIXELS,
                         FormatConsoleNumber(rayReport.get("largestUnrefinedRoundTripPixels", 0.0)),
                         "it" if refinedCount == 1 else "them"))
    # After the refined rays as in the GSD tools (ExactRayText).
    refinedWithoutRoundTripCount = rayReport.get("refinedWithoutRoundTrip", 0)
    if refinedWithoutRoundTripCount:
        lines.append("Rays: for {:,} of the {:,} grid points, Metashape's unprojection gave no ray that projects "
                     "back, so MetaFOCUS found {} by Newton's method.".format(
                         refinedWithoutRoundTripCount, gridPointCount,
                         "it" if refinedWithoutRoundTripCount == 1 else "them"))
    withoutRayCount = rayReport.get("withoutRay", 0)
    if withoutRayCount:
        lines.append("Rays: {:,} of the {:,} grid points have no ray and are left out.".format(
            withoutRayCount, gridPointCount))
    if radialMaximum is not None and radialMaximum.cornersBeyond:
        lines.append("Radial distortion: the radial distortion polynomial reaches its largest distorted radius {:,.0f} "
                     "px from the principal point, and {} of the 4 image corners lie farther out. For the "
                     "calibration of an interior orientation group, Metashape 2.3.2 does not follow the polynomial "
                     "beyond that radius: it keeps the radial factor of that radius, so that its projection keeps "
                     "growing.".format(radialMaximum.distortedRadiusPixels, radialMaximum.cornersBeyond))
    return lines


def NormalisedToPixelOffset(principalDistancePixels, affinity, nonOrthogonality, normalisedX, normalisedY):
    """Offset in pixel coordinates of a normalised offset (x, y) under
    Metashape's frame camera model: (x (c_pix + b1) + y b2, y c_pix)."""
    return (normalisedX * (principalDistancePixels + affinity) + normalisedY * nonOrthogonality,
            normalisedY * principalDistancePixels)


def ComputeGridDistortion(calibration, pixelColumnCount, pixelRowCount, correctionAt=None, radialOffset=0.0,
                          rayReport=None):
    """Total, radial, and decentring distortion on the grid of
    DistortionGridPositions, as in Metashape's Distortion Plot.

    Each grid point is an observed (distorted) image position.  Its ray
    comes from the calibration's own unproject(), which inverts the whole
    camera model of Metashape, additional corrections included, and the
    normalised point of the ray (NormalisedPointForRayDirection, for the
    frame model and Metashape's equidistant and equisolid fisheye
    models) gives

    - Total: the grid point minus the image position of the ray with all
      lens distortion coefficients set to zero and without additional
      corrections, with the principal distance, the principal point, and
      the affinity and non-orthogonality terms b1 and b2 kept, so b1 and
      b2 are not part of it,
    - Radial and Decentring: the displacements by the terms k1 to k4 and
      by the terms p1 to p4 of that normalised point, in pixels.

    With *correctionAt*, the additional corrections that the viewer
    evaluates itself because the calibration's unproject() leaves them
    out (CheckAdditionalCorrections), each grid point is taken back to
    its standard position (StandardPosition) before it is unprojected.
    A ray whose projection (the calibration's project()) lies farther
    than GRID_ROUND_TRIP_TOLERANCE_PIXELS from the position it was
    unprojected from is an unprojection that has not converged, and the
    ray is refined by Newton's method (ExactRay, RefinedRay).  A grid
    point without a ray, whose ray MetaFOCUS cannot find, is left out.
    *rayReport*, a dictionary, receives the number of grid points
    ("gridPointCount"), of refined rays ("refined", and
    "refinedWithoutRoundTrip" for those whose unprojection gave no ray
    that projects back), of grid points left out ("withoutRay"), and the
    largest round trip of the refined unprojections
    ("largestUnrefinedRoundTripPixels").

    Each displacement of the Total points from the ideal to the observed
    position and is drawn from the grid point, and every number of
    Metashape 2.3.2 for Geert's Project5 is reproduced.  A grid point
    whose ray or displacement is not a finite number is left out.

    With *radialOffset* (BalancedRadialOffset, the option Balanced of the
    radial distortion), the Radial is that of the balanced form, whereas
    the Total and the Decentring do not change.

    Returns gridTotal, gridRadial, gridDecentring: lists of (gridX,
    gridY, displacementX, displacementY) at the same grid points, in the
    same order.
    """
    gridTotal, gridRadial, gridDecentring = [], [], []
    principalDistancePixels = getattr(calibration, "f", None)
    if not (principalDistancePixels and math.isfinite(principalDistancePixels) and principalDistancePixels > 0):
        return gridTotal, gridRadial, gridDecentring
    principalPointX = pixelColumnCount * 0.5 + _Coefficient(calibration, "cx")
    principalPointY = pixelRowCount * 0.5 + _Coefficient(calibration, "cy")
    calibrationModel = CalibrationModel(calibration)
    toPixels = functools.partial(NormalisedToPixelOffset, principalDistancePixels, _Coefficient(calibration, "b1"),
                                 _Coefficient(calibration, "b2"))
    if rayReport is None:
        rayReport = {}
    rayReport.clear()
    rayReport.update(gridPointCount=0, refined=0, refinedWithoutRoundTrip=0, withoutRay=0,
                     largestUnrefinedRoundTripPixels=0.0)

    for gridX, gridY in DistortionGridPositions(pixelColumnCount, pixelRowCount):
        rayReport["gridPointCount"] += 1
        try:
            standardX, standardY = (gridX, gridY) if correctionAt is None else StandardPosition(
                correctionAt, gridX, gridY)
            ray = ExactRay(calibration, standardX, standardY, calibrationModel,
                           DistortedNormalisedPoint(calibration, pixelColumnCount, pixelRowCount, standardX, standardY),
                           rayReport)
            if ray is None:
                continue
            normalisedPoint = NormalisedPointForRayDirection(_VectorComponents(ray, 3), calibrationModel)
            if normalisedPoint is None:
                continue
            normalisedX, normalisedY = normalisedPoint
            idealOffsetX, idealOffsetY = toPixels(normalisedX, normalisedY)
            radialX, radialY, decentringX, decentringY = LensDistortionDisplacements(
                calibration, normalisedX, normalisedY, radialOffset)
            total = (gridX, gridY, gridX - principalPointX - idealOffsetX, gridY - principalPointY - idealOffsetY)
            radial = (gridX, gridY) + toPixels(radialX, radialY)
            decentring = (gridX, gridY) + toPixels(decentringX, decentringY)
            if not all(math.isfinite(component) for component in total + radial + decentring):
                continue
            gridTotal.append(total)
            gridRadial.append(radial)
            gridDecentring.append(decentring)
        except Exception as suppressedError:
            ReportSuppressedError("interiorOrientationViewer.ComputeGridDistortion", suppressedError)
            continue
    rayReport["withoutRay"] = rayReport["gridPointCount"] - len(gridTotal)
    return gridTotal, gridRadial, gridDecentring


def ComputeGridCorrections(gridTotal, gridRadial, gridDecentring):
    """The part of the Total that the radial and decentring terms do not
    explain, at the same grid points: Metashape's additional corrections
    (Fit additional corrections of Optimize Cameras), which its
    Distortion Plot shows as Corrections, and rounding noise of the
    unprojection when the calibration has none (about 1e-12 px)."""
    return [(gridX, gridY, totalX - radialX - decentringX, totalY - radialY - decentringY)
            for (gridX, gridY, totalX, totalY), (_, _, radialX, radialY), (_, _, decentringX, decentringY)
            in zip(gridTotal, gridRadial, gridDecentring)]


def FourierCorrectionTerms(coefficientCount):
    """Terms (trigonometric function, (wave number along x, wave number
    along y)) of Metashape's additional corrections of type fourier, in
    the order of their coefficients, or None when the count does not
    fill whole rings: rings r = max(|m|, |n|) = 1, 2, ... of the
    half-plane of wave numbers (m, n), each with the cosine terms of its
    wave vectors (0, r), (1, r) .. (r, r), (r, r - 1) .. (r, -r), (r - 1,
    -r) .. (1, -r) and then their sine terms, and an x and a y coefficient
    for each term, 8 R (R + 1) coefficients for R rings (96 for three).
    Agisoft does not document the model.  It was identified from chunk 3
    of Geert's Project5 in Metashape 2.3.2 (RMS 0.0776 px and maximum
    0.176 px of the Corrections, and the pattern of their vectors).  The
    viewer uses it to check the corrections that it obtains from
    Metashape's own unprojection and, when that unprojection leaves them
    out, to evaluate them itself, show them, and add them to the Total and
    the Difference (CheckAdditionalCorrections)."""
    ringCount, termCount = 0, 0
    while 2 * termCount < coefficientCount:
        ringCount += 1
        termCount += 8 * ringCount
    if ringCount == 0 or 2 * termCount != coefficientCount:
        return None
    terms = []
    for order in range(1, ringCount + 1):
        waves = ([(0, order)] + [(waveX, order) for waveX in range(1, order + 1)]
                 + [(order, waveY) for waveY in range(order - 1, -order - 1, -1)]
                 + [(waveX, -order) for waveX in range(order - 1, 0, -1)])
        terms += [(math.cos, wave) for wave in waves] + [(math.sin, wave) for wave in waves]
    return terms


def FourierAdditionalCorrection(terms, coefficients, extent, pixelX, pixelY):
    """Additional correction (correctionX, correctionY) in pixels at the
    pixel position (pixelX, pixelY) under the model of
    FourierCorrectionTerms: plane waves cos and sin of 2 pi (m relativeX
    + n relativeY), with relativeX and relativeY the position relative to
    the middle of the extent ((minimum x, minimum y), (maximum x, maximum
    y)) in units of its width and height."""
    (minimumX, minimumY), (maximumX, maximumY) = extent
    relativeX = (pixelX - minimumX) / (maximumX - minimumX) - 0.5
    relativeY = (pixelY - minimumY) / (maximumY - minimumY) - 0.5
    correctionX = correctionY = 0.0
    for termIndex, (trigonometricFunction, (waveX, waveY)) in enumerate(terms):
        basis = trigonometricFunction(2.0 * math.pi * (waveX * relativeX + waveY * relativeY))
        correctionX += coefficients[2 * termIndex] * basis
        correctionY += coefficients[2 * termIndex + 1] * basis
    return correctionX, correctionY


def ReadAdditionalCorrections(calibration):
    """(type, coefficients, extent) of the additional corrections of
    *calibration* as Metashape writes them to a calibration XML file
    (Calibration.save to a temporary folder, which is removed with
    everything in it), with extent ((minimum x, minimum y), (maximum x,
    maximum y)), or None when the file has none.  Raises when the file
    cannot be written or read, or holds a value that is not a number.
    The names of the elements (corrections with the attribute type,
    coeffs, extent with min and max) are those of the exports of
    Project5 in Metashape 2.3.2."""
    temporaryDirectory = tempfile.mkdtemp(prefix="MetaFOCUS_calibration_")
    calibrationPath = os.path.join(temporaryDirectory, "calibration.xml")
    try:
        calibration.save(calibrationPath)
        root = ElementTree.parse(calibrationPath).getroot()
    finally:
        # The folder is removed with everything in it, so that a second
        # file does not leave it behind, and without an error of its own,
        # which would replace that of save.
        shutil.rmtree(temporaryDirectory, ignore_errors=True)
    correctionsElement = root.find("corrections")
    if correctionsElement is None:
        return None
    coefficients = [float(text) for text in (correctionsElement.findtext("coeffs") or "").split()]
    minimum = [float(text) for text in (correctionsElement.findtext("extent/min") or "").split()]
    maximum = [float(text) for text in (correctionsElement.findtext("extent/max") or "").split()]
    return correctionsElement.get("type"), coefficients, (tuple(minimum), tuple(maximum))


def FourierCorrectionModel(correctionType, coefficients, extent):
    """(function of a pixel position that gives its additional
    correction, None) for corrections of type fourier that the viewer can
    evaluate (FourierAdditionalCorrection), or (None, the reason in
    words): another type, a number of coefficients that fills no whole
    ring, an extent that is not two pairs of finite numbers or has no
    width or height, or a coefficient that is not a finite number."""
    if correctionType != "fourier":
        return None, "it knows only the type fourier"
    terms = FourierCorrectionTerms(len(coefficients))
    if terms is None:
        return None, "{} do not fill whole rings of the model of type fourier".format(
            FormatCountWithNoun(len(coefficients), "coefficient", "coefficients"))
    (minimum, maximum) = extent
    if len(minimum) != 2 or len(maximum) != 2 or not all(math.isfinite(value) for value in minimum + maximum):
        return None, "an extent that is not two pairs of finite numbers"
    if not maximum[0] > minimum[0]:
        return None, "an extent without width"
    if not maximum[1] > minimum[1]:
        return None, "an extent without height"
    if not all(math.isfinite(coefficient) for coefficient in coefficients):
        return None, "a coefficient that is not a number"
    return functools.partial(FourierAdditionalCorrection, terms, coefficients, extent), None


# Texts of the Corrections mode when it shows no vectors: Metashape leaves
# the plot empty for a calibration without additional corrections.
NO_ADDITIONAL_CORRECTIONS_TEXT = ("No additional corrections (Fit additional corrections of Optimize Cameras "
                                  "estimates them).")
UNEVALUATED_ADDITIONAL_CORRECTIONS_TEXT = ("Additional corrections of type {} that MetaFOCUS cannot evaluate "
                                           "(details in the Console).")


class AdditionalCorrectionsCheck:
    """Result of CheckAdditionalCorrections: the vectors of the
    Corrections mode, the Console lines, the text of the mode when it
    shows no vectors, and, when Metashape's unprojection leaves the
    additional corrections out and the viewer evaluates them itself, the
    function of a pixel position that gives them (correctionAt, None
    otherwise)."""

    def __init__(self, corrections, consoleLines, emptyText=NO_ADDITIONAL_CORRECTIONS_TEXT, correctionAt=None):
        self.corrections, self.consoleLines = corrections, consoleLines
        self.emptyText, self.correctionAt = emptyText, correctionAt


def CheckAdditionalCorrections(calibration, gridCorrections):
    """Compare the remainder that the unprojection gives
    (ComputeGridCorrections) with the additional corrections of the
    calibration's XML file (ReadAdditionalCorrections) and return an
    AdditionalCorrectionsCheck.

    - A file without additional corrections: no vectors, as in Metashape,
      and a remainder above ADDITIONAL_CORRECTION_THRESHOLD_PIXELS is
      reported in the Console only.
    - Corrections that the viewer can evaluate (FourierCorrectionModel):
      the remainder is compared with them and with zero.  When it is
      nearer to them, Metashape's unprojection has applied them and the
      remainder is shown.  Otherwise the unprojection has left them out,
      and correctionAt gives them, so that the viewer can add them to the
      unprojection and the projection (ComputeGridDistortion and
      ComputeGridDifference).
    - A file that cannot be read, or corrections that the viewer cannot
      evaluate: the remainder is shown when it exceeds
      ADDITIONAL_CORRECTION_THRESHOLD_PIXELS, as corrections that the
      unprojection applied, and otherwise no vectors, with a text that
      says so for corrections that cannot be evaluated.
    - Without a grid (no positive principal distance) nothing is
      claimed."""
    if not gridCorrections:
        return AdditionalCorrectionsCheck([], [])
    largestRemainder = max(math.hypot(remainderX, remainderY) for _, _, remainderX, remainderY in gridCorrections)
    hasRemainder = largestRemainder > ADDITIONAL_CORRECTION_THRESHOLD_PIXELS
    try:
        additionalCorrections = ReadAdditionalCorrections(calibration)
    except Exception as suppressedError:
        ReportSuppressedError("interiorOrientationViewer.ReadAdditionalCorrections", suppressedError)
        return AdditionalCorrectionsCheck(gridCorrections if hasRemainder else [], [
            "Corrections: the calibration file could not be read ({}), so the corrections shown are the part of "
            "the Total that the radial and decentring terms do not explain, when it exceeds {} px.".format(
                DescribeException(suppressedError), FormatConsoleNumber(ADDITIONAL_CORRECTION_THRESHOLD_PIXELS))])
    if additionalCorrections is None:
        consoleLines = []
        if hasRemainder:
            consoleLines.append("Corrections: the unprojection leaves up to {} px that the radial and decentring "
                                "terms do not explain, although the calibration file holds no additional "
                                "corrections, so the mode stays empty as in Metashape.".format(
                                    FormatConsoleNumber(largestRemainder)))
        return AdditionalCorrectionsCheck([], consoleLines)
    correctionType, coefficients, extent = additionalCorrections
    description = "additional corrections of type {} with {}".format(
        correctionType, FormatCountWithNoun(len(coefficients), "coefficient", "coefficients"))
    correctionAt, reason = FourierCorrectionModel(correctionType, coefficients, extent)
    modelCorrections = None
    if correctionAt is not None:
        try:
            modelCorrections = [(gridX, gridY) + correctionAt(gridX, gridY) for gridX, gridY, _, _ in gridCorrections]
            if not all(math.isfinite(component) for vector in modelCorrections for component in vector):
                modelCorrections, reason = None, "an evaluation that is not a finite number"
        except Exception as suppressedError:
            ReportSuppressedError("interiorOrientationViewer.CheckAdditionalCorrections.model", suppressedError)
            modelCorrections, reason = None, "an evaluation that failed ({})".format(DescribeException(suppressedError))
    if modelCorrections is None:
        if hasRemainder:
            return AdditionalCorrectionsCheck(gridCorrections, [
                "Corrections: {}, which MetaFOCUS cannot evaluate ({}), so the corrections shown are those that "
                "Metashape's unprojection applied.".format(description, reason)])
        return AdditionalCorrectionsCheck([], [
            "Corrections: {}, which MetaFOCUS cannot evaluate ({}), and Metashape's unprojection did not apply "
            "them, so the Total leaves them out.".format(description, reason)],
            UNEVALUATED_ADDITIONAL_CORRECTIONS_TEXT.format(correctionType))
    distanceToModel = max(math.hypot(remainderX - modelX, remainderY - modelY)
                          for (_, _, remainderX, remainderY), (_, _, modelX, modelY)
                          in zip(gridCorrections, modelCorrections))
    if distanceToModel <= largestRemainder:
        return AdditionalCorrectionsCheck(gridCorrections, [
            "Corrections: {}, from Metashape's unprojection, which differ by at most {} px from MetaFOCUS's "
            "evaluation of them.".format(description, FormatConsoleNumber(distanceToModel))])
    return AdditionalCorrectionsCheck(modelCorrections, [
        "Corrections: the calibration file holds {}, but Metashape's unprojection did not apply them (it leaves "
        "at most {} px that the radial and decentring terms do not explain), so the viewer evaluates them itself "
        "and adds them to the Total and the Difference.".format(description, FormatConsoleNumber(largestRemainder))],
        correctionAt=correctionAt)


def StandardPosition(correctionAt, observedX, observedY):
    """The standard position, before the additional corrections of
    *correctionAt* (a function of a pixel position), of the observed
    position (observedX, observedY): the corrected position is the
    standard position plus the correction there, as in Metashape's model
    of the additional corrections identified from Project5, so the
    standard position follows by fixed-point iteration (the corrections
    are a fraction of a pixel and change slowly across the image)."""
    standardX, standardY = observedX, observedY
    for _ in range(30):
        correctionX, correctionY = correctionAt(standardX, standardY)
        nextX, nextY = observedX - correctionX, observedY - correctionY
        hasConverged = abs(nextX - standardX) + abs(nextY - standardY) < 1e-12
        standardX, standardY = nextX, nextY
        if hasConverged:
            break
    return standardX, standardY


def InitialCalibrationForDifference(sensor, calibration):
    """(initial calibration, principal distance in pixels, b1 in pixels,
    description) for the Difference.  The initial calibration is the one
    set for the group in the Camera Calibration dialog (sensor.user_calib),
    and the principal distance is then None and b1 zero.  Otherwise the
    initial calibration is None, and the principal distance and b1 are
    those of Type Auto, as Metashape 2.3.2 sets them from the focal length
    and the photosite pitches set for the group, normally from the Exif
    data: c_pix = focal length / vertical pitch (pixel_height) and
    b1 = focal length / horizontal pitch (pixel_width) - c_pix, so that b1
    is zero for equal pitches (c_pix 500 and b1 500 for pitches of 0.004 mm
    and 0.008 mm and a focal length of 4 mm; Project5:
    50 mm / 0.00597010 mm = 8375.07, which reproduces Metashape's
    Difference), with the principal point at the image centre and all lens
    distortion coefficients set to zero (ComputeGridDifference).  A
    vertical pitch that is not set is taken equal to the horizontal one,
    and the description says so.  Without focal length or horizontal pitch
    the adjusted principal distance stands in for it, and without that as
    well there is no initial calibration (None, None, 0.0, description)."""
    userCalibration = getattr(sensor, "user_calib", None)
    if userCalibration is not None:
        return (userCalibration, None, 0.0,
                "the initial calibration of the group (Camera Calibration dialog, Initial tab)")
    focalLength = getattr(sensor, "focal_length", None)
    horizontalPitch = getattr(sensor, "pixel_width", None)
    if IsPositiveNumber(focalLength) and IsPositiveNumber(horizontalPitch):
        verticalPitch = getattr(sensor, "pixel_height", None)
        verticalPitchAssumed = not IsPositiveNumber(verticalPitch)
        if verticalPitchAssumed:
            verticalPitch = horizontalPitch
        principalDistancePixels = focalLength / verticalPitch
        affinityPixels = focalLength / horizontalPitch - principalDistancePixels
        if not verticalPitchAssumed and verticalPitch == horizontalPitch:
            affinityPixels = 0.0
        return (None, principalDistancePixels, affinityPixels,
                "the initial calibration of Type Auto, c_pix = focal length / vertical photosite pitch = {:g} mm / "
                "{:.8g} mm = {:.2f} and b1 = focal length / horizontal photosite pitch - c_pix = {:.2f}, as set "
                "for the group, normally from the Exif data{}, principal point at the image centre, all lens "
                "distortion coefficients set to zero".format(
                    focalLength, verticalPitch, principalDistancePixels, affinityPixels,
                    " (vertical photosite pitch not set, taken equal to the horizontal one)"
                    if verticalPitchAssumed else ""))
    adjustedPrincipalDistance = getattr(calibration, "f", None)
    if not IsPositiveNumber(adjustedPrincipalDistance):
        return (None, None, 0.0, "nothing, because neither the focal length and the photosite pitch nor a positive "
                                 "adjusted c_pix are known")
    return (None, adjustedPrincipalDistance, 0.0,
            "a substitute, because the focal length or the photosite pitch is not known: the adjusted c_pix = "
            "{:.2f}, principal point at the image centre, all lens distortion coefficients set to zero".format(
                adjustedPrincipalDistance))


def IsPositiveNumber(value):
    """True for an int or float that is finite and positive."""
    return isinstance(value, (int, float)) and math.isfinite(value) and value > 0.0


# ===================================================================
# Other definitions than Metashape's (options of each tab)
# ===================================================================

# The Difference against the initial calibration (Metashape's
# definition, the default), against another interior orientation group of
# the open project, or against a calibration file: the data of the items
# of the combo box Difference against.
DIFFERENCE_AGAINST_INITIAL_CALIBRATION = "initial"
DIFFERENCE_AGAINST_CALIBRATION_FILE = "file"
DIFFERENCE_AGAINST_INITIAL_CALIBRATION_TEXT = "Initial calibration (Metashape)"
DIFFERENCE_AGAINST_CALIBRATION_FILE_TEXT = "Calibration file…"
DIFFERENCE_AGAINST_TOOLTIP = (
    "Calibration against which the Difference is computed. Metashape's Distortion Plot takes the initial\n"
    "calibration of the group (the default). Another interior orientation group of the open project with the\n"
    "same numbers of pixel columns and rows, or a calibration file in Agisoft's XML format (a laboratory\n"
    "calibration, or the calibration of another project saved with Metashape's Camera Calibration dialog),\n"
    "shows how far apart the two calibrations place the same ray in the image. Judged against the standard\n"
    "errors of both calibrations, this helps to assess whether a calibration is stable between projects.")

# Statistics of the residuals in a grid cell: (statistic, item of the
# combo box Residuals, words of the plot title and of the Console).
RESIDUAL_STATISTIC_WEIGHTED_MEAN = "weightedMean"
RESIDUAL_STATISTIC_MEAN = "mean"
RESIDUAL_STATISTIC_MEDIAN = "median"
RESIDUAL_STATISTIC_CHOICES = (
    (RESIDUAL_STATISTIC_WEIGHTED_MEAN, "Weighted cell means (Metashape)", "weighted cell means"),
    (RESIDUAL_STATISTIC_MEAN, "Cell means without weights", "cell means without weights"),
    (RESIDUAL_STATISTIC_MEDIAN, "Cell medians", "cell medians"),
)
RESIDUAL_STATISTIC_TOOLTIP = (
    "Statistic of the residuals in each grid cell of the Residuals mode. The default weights every residual by\n"
    "the inverse of the keypoint size of its 2D tie point, which reproduces Metashape's Distortion Plot. Without\n"
    "weights, every residual counts equally. The cell medians are the median of the x components and the\n"
    "median of the y components of the residuals in the cell, which single large residuals affect less than\n"
    "a mean.")

# The radial distortion as estimated (Gaussian, Metashape's definition, the
# default) or balanced, with its zero crossing at a share of the largest
# radius of the profile.
RADIAL_CONVENTION_GAUSSIAN = "gaussian"
RADIAL_CONVENTION_BALANCED = "balanced"
RADIAL_CONVENTION_CHOICES = (
    (RADIAL_CONVENTION_GAUSSIAN, "Gaussian (Metashape)"),
    (RADIAL_CONVENTION_BALANCED, "Balanced"),
)
DEFAULT_BALANCED_ZERO_CROSSING_PERCENT = 70
BALANCED_ZERO_CROSSING_PERCENT_RANGE = (10, 100)
RADIAL_CONVENTION_TOOLTIP = (
    "Radial distortion of the profile and of the Radial vector field. Gaussian is the radial distortion of the\n"
    "estimated coefficients k1 to k4, as in Metashape (the default). Balanced adds the displacement\n"
    "proportional to the radius that makes the radial distortion zero at the radius r0, a change of scale that\n"
    "the principal distance in pixels of the balanced form absorbs: it changes the picture, not the geometry.\n"
    "The Total, the Corrections, and the Difference keep Metashape's definitions.")
BALANCED_ZERO_CROSSING_TOOLTIP = (
    "Radius r0 of the zero crossing of the balanced radial distortion, as a share of the largest radius of the\n"
    "profile (half the diagonal of the image format).")


def LargestProfileRadiusPixels(pixelColumnCount, pixelRowCount):
    """Largest radius of the distortion profiles in pixels: half the
    diagonal of the image format."""
    return math.hypot(0.5 * pixelColumnCount, 0.5 * pixelRowCount)


def BalancedZeroCrossingRadiusPixels(pixelColumnCount, pixelRowCount, zeroCrossingPercent):
    """Radius r0 in pixels of the zero crossing of the balanced radial
    distortion: *zeroCrossingPercent* per cent of the largest radius of
    the profiles (LargestProfileRadiusPixels)."""
    return zeroCrossingPercent / 100.0 * LargestProfileRadiusPixels(pixelColumnCount, pixelRowCount)


def BalancedRadialOffset(calibration, zeroCrossingRadiusPixels):
    """Radial term (RadialTerm) at the zero crossing r0 of the balanced
    radial distortion, given in pixels: the Gaussian radial displacement
    at r0 divided by r0, Delta r(r0) / r0, negative for a barrel
    distortion.  The balanced form subtracts it from the radial term
    everywhere, so that its displacement differs from the Gaussian one
    by a displacement proportional to the radius and is zero at r0.  None
    without a positive principal distance in pixels."""
    principalDistancePixels = getattr(calibration, "f", None)
    if not IsPositiveNumber(principalDistancePixels):
        return None
    normalisedRadius = zeroCrossingRadiusPixels / principalDistancePixels
    return RadialTerm(calibration, normalisedRadius * normalisedRadius)


def BalancedPrincipalDistancePixels(calibration, zeroCrossingRadiusPixels):
    """Principal distance in pixels of the balanced form,
    c_pix (1 + Delta r(r0) / r0).  A radial displacement proportional to
    the radius is a change of scale, so the balanced form describes the
    same camera model with this principal distance: a barrel distortion
    gives a shorter one.  None without a positive principal distance."""
    radialOffset = BalancedRadialOffset(calibration, zeroCrossingRadiusPixels)
    if radialOffset is None:
        return None
    return calibration.f * (1.0 + radialOffset)


def ReferenceCalibrationCandidates(document, sensor, chunk):
    """[(label, calibration)] of the other interior orientation groups of
    the open project (*document*, every chunk) whose calibration has the
    numbers of pixel columns and rows of the calibration of *sensor*, the
    group of *chunk*, labelled "<group> in <chunk>".  A document that
    cannot be read gives none, and the Console says so once."""
    calibration = GetSensorCalibration(sensor)
    candidates = []
    try:
        for candidateChunk in list(document.chunks):
            for candidateSensor in list(candidateChunk.sensors):
                if candidateChunk is chunk and candidateSensor is sensor:
                    continue
                candidateCalibration = GetSensorCalibration(candidateSensor)
                if candidateCalibration is None or ReferenceCalibrationProblem(candidateCalibration, calibration):
                    continue
                candidates.append(("{} in {}".format(InteriorOrientationGroupName(candidateSensor, candidateChunk),
                                                     candidateChunk.label), candidateCalibration))
    except Exception as suppressedError:
        ReportSuppressedError("interiorOrientationViewer.ReferenceCalibrationCandidates", suppressedError)
    return candidates


def ReferenceCalibrationProblem(referenceCalibration, calibration):
    """Why *referenceCalibration* cannot serve as the reference of the
    Difference of *calibration*, in words, or None: it needs the same
    numbers of pixel columns and rows, because the grid points are image
    positions, and a positive principal distance in pixels."""
    referenceSize = (getattr(referenceCalibration, "width", None), getattr(referenceCalibration, "height", None))
    groupSize = (getattr(calibration, "width", None), getattr(calibration, "height", None))
    if referenceSize != groupSize:
        return "the reference has {} × {} pixels, the group {} × {}".format(*(referenceSize + groupSize))
    if not IsPositiveNumber(getattr(referenceCalibration, "f", None)):
        return "the reference has no positive principal distance"
    return None


def LoadCalibrationFile(path):
    """Calibration read from a calibration file in Agisoft's XML format
    (Metashape.Calibration.load, whose default format it is).  Raises
    when the file cannot be read."""
    calibration = Metashape.Calibration()
    calibration.load(path)
    return calibration


def ComputeGridDifference(calibration, initialCalibration, pixelColumnCount, pixelRowCount,
                          initialPrincipalDistancePixels=None, correctionAt=None, initialAffinityPixels=0.0):
    """Difference between the adjusted and the initial calibration, as in
    Metashape's Distortion Plot: each grid point of
    DistortionGridPositions is the image position of a ray under the
    initial calibration, and the vector is the image position of that
    ray under the adjusted calibration (its project(), all terms
    included) minus the grid point.  The ray comes from the unproject()
    of *initialCalibration*, refined by Newton's method when it has not
    converged (ExactRay), or, when it is None, from a calibration with
    the principal distance *initialPrincipalDistancePixels*, the
    affinity term b1 *initialAffinityPixels* (Type Auto with unequal
    photosite pitches), the principal point at the image centre, all lens
    distortion coefficients set to zero, and the
    camera model of the adjusted calibration
    (InitialCalibrationForDifference).  With *correctionAt*,
    the additional corrections that the viewer evaluates itself because
    the calibration's project() leaves them out, the correction at the
    projected position is added to it.  A position that is not a finite
    number is left out, and without either initial calibration the list
    is empty.

    Returns a list of (gridX, gridY, differenceX, differenceY).
    """
    if initialCalibration is None and not IsPositiveNumber(initialPrincipalDistancePixels):
        return []
    gridDifference = []
    calibrationModel = CalibrationModel(calibration)
    for gridX, gridY in DistortionGridPositions(pixelColumnCount, pixelRowCount):
        try:
            if initialCalibration is not None:
                ray = ExactRay(initialCalibration, gridX, gridY, CalibrationModel(initialCalibration),
                               DistortedNormalisedPoint(initialCalibration, pixelColumnCount, pixelRowCount,
                                                        gridX, gridY))
            else:
                rayDirection = RayDirectionForNormalisedPoint(
                    (gridX - pixelColumnCount * 0.5) / (initialPrincipalDistancePixels + initialAffinityPixels),
                    (gridY - pixelRowCount * 0.5) / initialPrincipalDistancePixels, calibrationModel)
                ray = Metashape.Vector(list(rayDirection)) if rayDirection is not None else None
            if ray is None:
                continue
            adjustedPosition = calibration.project(ray)
            if adjustedPosition is None:
                continue
            adjustedX, adjustedY = _VectorComponents(adjustedPosition, 2)
            if correctionAt is not None:
                correctionX, correctionY = correctionAt(adjustedX, adjustedY)
                adjustedX, adjustedY = adjustedX + correctionX, adjustedY + correctionY
            if not (math.isfinite(adjustedX) and math.isfinite(adjustedY)):
                continue
            gridDifference.append((gridX, gridY, adjustedX - gridX, adjustedY - gridY))
        except Exception as suppressedError:
            ReportSuppressedError("interiorOrientationViewer.ComputeGridDifference", suppressedError)
            continue
    return gridDifference


# ===================================================================
# Coverage of the image by the 2D tie points
# ===================================================================

# Rings of the coverage by radius per interval of the radius ticks of the
# profiles, and the fewest 2D tie points of a ring with a radial residual.
COVERAGE_RINGS_PER_TICK = 10
COVERAGE_MINIMUM_RESIDUAL_COUNT = 10
# The share of the image format in each ring and sector of direction comes
# from rays from the principal point, this many per degree, along which r is
# found this many times per ring width and taken as linear in between (a
# grid of 240 columns of pixel positions would be too coarse for the wedge
# of a direction).
COVERAGE_FORMAT_RAYS_PER_DEGREE = 2
COVERAGE_FORMAT_SAMPLES_PER_RING = 1
# Where r is not linear between two of these samples, the stretch is halved
# until r at its middle lies within this share of a ring width of the
# straight line, or at most this many times (near the largest distorted
# radius, where r rises steeply, a linear r would push the image format into
# the outermost rings, up to six times their share).
COVERAGE_FORMAT_RADIUS_TOLERANCE_RINGS = 0.002
COVERAGE_FORMAT_MAXIMUM_HALVINGS = 14
# The direction of the coverage by radius: a wedge of plus and minus this
# many degrees around a direction in whole degrees, the sum of its sectors
# of one degree.
DIRECTION_HALF_WIDTH_DEGREES = 15
# The inversion of the decentring distortion in the r of the coverage: the
# most passes, and the change of the undistorted point, times c_pix, below
# which it stops (a ring is at least a few pixels wide).
RADIUS_INVERSION_MAXIMUM_PASSES = 50
RADIUS_INVERSION_TOLERANCE_PIXELS = 0.01
# Newton's method on the radial and decentring distortion together
# where the passes above fail: the most steps, the most halvings of a step
# that does not reduce the miss, and the step of the central differences of
# its Jacobian in normalised coordinates.
RADIUS_NEWTON_MAXIMUM_STEPS = 40
RADIUS_NEWTON_MAXIMUM_HALVINGS = 20
RADIUS_NEWTON_DIFFERENCE_STEP = 1e-7
# A miss of the distorted point, in normalised coordinates, at which a
# step of Newton's method counts as reducing it even if it does not
# (rounding near the solution).
RADIUS_NEWTON_RESIDUAL_FLOOR = 1e-15
# Where Newton's method finds no point from its start, it starts again from
# the start scaled towards the principal point by each of these factors
# (near the radius of the largest distorted radius the decentring
# distortion can fold the camera model, and Newton's method cannot cross a
# fold to a point before it, for example with p3 = -0.19 in 780 positions of
# a grid of 0.01 between 0.85 and 1 times that radius).
RADIUS_NEWTON_RESTART_FACTORS = (0.8,)
# The comment line of Export CSV that names what the shares of the coverage
# by radius are taken of (both leave out what has no r).
COVERAGE_SHARES_LINE = ("Shares: of the 2D tie points with an r and of the part of the image format where r is "
                        "found, in all directions")


def CoverageRingWidthPixels(pixelColumnCount, pixelRowCount):
    """Width of a ring of the coverage by radius in pixels: a tenth of the
    interval of the radius ticks of the profiles (COVERAGE_RINGS_PER_TICK),
    so that ten rings fill the space between two ticks."""
    return ChooseTickInterval(0.0, LargestProfileRadiusPixels(pixelColumnCount, pixelRowCount), 6) / float(
        COVERAGE_RINGS_PER_TICK)


def DirectionSector(offsetX, offsetY):
    """The sector of one degree, 0 to 359, of the direction of the offset
    (offsetX, offsetY) from the principal point in pixel coordinates,
    counted from the image x axis towards the image y axis as the
    Decentring angle counts it, so that 90 degrees points down the image
    and 270 degrees up."""
    return int(math.degrees(math.atan2(offsetY, offsetX)) % 360.0) % 360


def WedgeSectors(directionDegrees):
    """The sectors of the wedge of the direction *directionDegrees* (whole
    degrees): from the direction minus DIRECTION_HALF_WIDTH_DEGREES up to
    the direction plus it, 30 sectors of one degree."""
    return [(int(directionDegrees) + offset) % 360
            for offset in range(-DIRECTION_HALF_WIDTH_DEGREES, DIRECTION_HALF_WIDTH_DEGREES)]


def SectorKey(ring, sector):
    """The key of a ring and a sector of one degree in the coverage by
    radius: ring * 360 + sector, so that ring = key // 360 and sector =
    key % 360 (an integer, faster than a pair)."""
    return ring * 360 + sector


def DirectionValueText(directionDegrees):
    """The direction of the coverage by radius in the direction row:
    "34° ± 15°", or "all directions" for None."""
    if directionDegrees is None:
        return "all directions"
    return "{}° ± {}°".format(int(directionDegrees), DIRECTION_HALF_WIDTH_DEGREES)


def DirectionTitleWords(directionDegrees):
    """The direction in the titles of the coverage by radius: " at 34°", or
    "" in all directions.  The titles leave the ± 15° to the direction row,
    because the longer title ran into the key of the red rings."""
    return "" if directionDegrees is None else " at {}°".format(int(directionDegrees))


def RayFormatSpan(startX, startY, directionX, directionY, pixelColumnCount, pixelRowCount):
    """(entry, exit): the distances along the ray from (startX, startY) in
    the unit direction (directionX, directionY), in pixel coordinates,
    between which the ray lies in the image format, from 0 to the numbers
    of pixel columns and rows, entry 0 when it starts in the format, or None
    when it misses the format."""
    entry, exit = 0.0, float("inf")
    for start, direction, limit in ((startX, directionX, float(pixelColumnCount)),
                                    (startY, directionY, float(pixelRowCount))):
        if abs(direction) < 1e-15:
            if start < 0.0 or start > limit:
                return None
            continue
        near, far = -start / direction, (limit - start) / direction
        if near > far:
            near, far = far, near
        entry, exit = max(entry, near), min(exit, far)
    if not exit > entry:
        return None
    return entry, exit


class RadialDistanceMap:
    """The radial distance r of the distortion profiles at a pixel
    position: c_pix times the radius of the undistorted normalised point
    that the camera model distorts to the position.  The offset from the
    principal point is freed from b1 and b2 and divided by c_pix, which
    gives the distorted normalised point, and the radial distortion (k1 to
    k4) is inverted along the radius with a table of the polynomial.

    The decentring distortion (p1 to p4) is inverted as well (Geert, 14
    October 2026): its displacement at the point found is taken off the
    distorted point and the radial distortion is inverted again, until the
    point changes by less than RADIUS_INVERSION_TOLERANCE_PIXELS.  Without
    the decentring distortion, r would differ from that of Metashape's
    unprojection by up to 39 px, with a median of 9.8 px, for a NIKKOR Z
    20 mm group with p2 = 0.004.  The additional corrections are left out.

    Where these passes fail, Newton's method on the radial and decentring
    distortion together takes over (_FullModelInverse, started again
    nearer the principal point where it finds no point,
    _InverseWithRestarts), within the radius at which the radial
    polynomial reaches its largest distorted radius: the decentring
    distortion can carry a position beyond that distorted radius while its
    undistorted point lies within it, which the radial inversion of the
    first pass cannot find (c_pix = 900, k1 = -0.5, p1 = 0.004, r = 729 px
    at 496.94 px from the principal point, where the radial polynomial
    reaches at most 489.90 px).  A position has no r
    (None) when no undistorted point is found within that radius
    (lastFailure "beyond") or when the inversion does not converge
    (lastFailure "unconverged").  Near that radius the decentring
    distortion can make two undistorted points within it distort to the
    same position (46 px apart for c_pix = 1500, k1 = -0.6, k2 = 0.1,
    p1 = 0.003, p2 = 0.002, and p3 = 0.5), and r is that of the point
    found."""

    TABLE_STEPS = 4000

    def __init__(self, calibration, pixelColumnCount, pixelRowCount):
        self.principalDistancePixels = float(calibration.f)
        self.principalPointX = pixelColumnCount * 0.5 + _Coefficient(calibration, "cx")
        self.principalPointY = pixelRowCount * 0.5 + _Coefficient(calibration, "cy")
        self.affinity = _Coefficient(calibration, "b1")
        self.nonOrthogonality = _Coefficient(calibration, "b2")
        self.p1, self.p2 = _Coefficient(calibration, "p1"), _Coefficient(calibration, "p2")
        self.p3, self.p4 = _Coefficient(calibration, "p3"), _Coefficient(calibration, "p4")
        # Without p1 and p2 there is no decentring distortion, whatever p3
        # and p4 are.
        self.hasDecentring = bool(self.p1 or self.p2)
        self.k1, self.k2 = _Coefficient(calibration, "k1"), _Coefficient(calibration, "k2")
        self.k3, self.k4 = _Coefficient(calibration, "k3"), _Coefficient(calibration, "k4")
        self.lastFailure = None
        largestRadius = 2.0 * LargestProfileRadiusPixels(pixelColumnCount, pixelRowCount) / self.principalDistancePixels
        self.undistortedRadii, self.distortedRadii = [0.0], [0.0]
        for step in range(1, self.TABLE_STEPS + 1):
            undistortedRadius = largestRadius * step / self.TABLE_STEPS
            distortedRadius = undistortedRadius * (1.0 + RadialTerm(calibration, undistortedRadius * undistortedRadius))
            if not distortedRadius > self.distortedRadii[-1]:
                break
            self.undistortedRadii.append(undistortedRadius)
            self.distortedRadii.append(distortedRadius)

    def Offset(self, pixelX, pixelY):
        """(x, y) of the position from the principal point in pixels."""
        return pixelX - self.principalPointX, pixelY - self.principalPointY

    def _RadialInverse(self, distortedX, distortedY):
        """The normalised point whose radial distortion gives the point
        (distortedX, distortedY): on the same radius, at the undistorted
        radius interpolated in the table, or None beyond the largest
        distorted radius of the table."""
        distortedRadius = math.hypot(distortedX, distortedY)
        distortedRadii = self.distortedRadii
        index = bisect.bisect_left(distortedRadii, distortedRadius)
        if index == 0:
            return 0.0, 0.0
        if index >= len(distortedRadii):
            return None
        lowerDistorted = distortedRadii[index - 1]
        lowerUndistorted = self.undistortedRadii[index - 1]
        undistortedRadius = lowerUndistorted + (distortedRadius - lowerDistorted) / (
            distortedRadii[index] - lowerDistorted) * (self.undistortedRadii[index] - lowerUndistorted)
        scale = undistortedRadius / distortedRadius
        return distortedX * scale, distortedY * scale

    def _DecentringDisplacement(self, pointX, pointY):
        """(dx, dy) of the decentring terms p1 to p4 at the normalised point,
        with Metashape's factor 1 + p3 r^2 + p4 r^4
        (LensDistortionDisplacements)."""
        radiusSquared = pointX * pointX + pointY * pointY
        higherOrderFactor = 1.0 + radiusSquared * (self.p3 + radiusSquared * self.p4)
        crossTerm = 2.0 * pointX * pointY
        return (higherOrderFactor * (self.p1 * (radiusSquared + 2.0 * pointX * pointX) + self.p2 * crossTerm),
                higherOrderFactor * (self.p2 * (radiusSquared + 2.0 * pointY * pointY) + self.p1 * crossTerm))

    def _DistortedPoint(self, pointX, pointY):
        """The distorted normalised point of the undistorted normalised point
        (pointX, pointY): radial (k1 to k4) and decentring (p1 to p4)
        distortion, before b1 and b2."""
        radiusSquared = pointX * pointX + pointY * pointY
        radialFactor = 1.0 + radiusSquared * (self.k1 + radiusSquared * (
            self.k2 + radiusSquared * (self.k3 + radiusSquared * self.k4)))
        decentringX, decentringY = self._DecentringDisplacement(pointX, pointY)
        return pointX * radialFactor + decentringX, pointY * radialFactor + decentringY

    def _NewtonStartPoint(self, distortedX, distortedY):
        """The normalised point in the direction of (distortedX, distortedY)
        one table step inside the largest undistorted radius of the table,
        at which the radial polynomial reaches its largest distorted radius:
        the start of _FullModelInverse when the radial inversion finds no
        point.  Started at that radius itself, where the radial derivative
        is about zero, a first step outwards could not be shortened into
        the radius, so that no point would be found (c_pix = 1200,
        k1 = -0.35, k2 = 0.12, k3 = -0.02, p1 = 0.001, p2 = -0.002, and
        r = 1846.17 px within 1860 px)."""
        distortedRadius = math.hypot(distortedX, distortedY)
        if not distortedRadius > 0.0:
            return 0.0, 0.0
        scale = self.undistortedRadii[max(len(self.undistortedRadii) - 2, 0)] / distortedRadius
        return distortedX * scale, distortedY * scale

    def _NewtonStep(self, pointX, pointY, missX, missY):
        """(stepX, stepY) of Newton's method from the normalised point for the
        miss (missX, missY) of its distorted point, from the Jacobian of
        _DistortedPoint by central differences, or None for a singular
        Jacobian."""
        step = RADIUS_NEWTON_DIFFERENCE_STEP
        rightX, rightY = self._DistortedPoint(pointX + step, pointY)
        leftX, leftY = self._DistortedPoint(pointX - step, pointY)
        upperX, upperY = self._DistortedPoint(pointX, pointY + step)
        lowerX, lowerY = self._DistortedPoint(pointX, pointY - step)
        derivativeXX, derivativeYX = (rightX - leftX) / (2.0 * step), (rightY - leftY) / (2.0 * step)
        derivativeXY, derivativeYY = (upperX - lowerX) / (2.0 * step), (upperY - lowerY) / (2.0 * step)
        determinant = derivativeXX * derivativeYY - derivativeXY * derivativeYX
        if not (abs(determinant) > 0.0 and math.isfinite(determinant)):
            return None
        return ((derivativeYY * missX - derivativeXY * missY) / determinant,
                (derivativeXX * missY - derivativeYX * missX) / determinant)

    def _FullModelInverse(self, distortedX, distortedY, startPoint, failure):
        """The undistorted normalised point of (distortedX, distortedY) by
        Newton's method on the radial and decentring distortion together
        (_DistortedPoint) from *startPoint*, or None with *failure* in
        lastFailure.  A step that does not reduce the miss, or that leaves
        the largest undistorted radius of the table, is halved.  The point
        is found when its distorted point misses by less than
        RADIUS_INVERSION_TOLERANCE_PIXELS and its last step, times c_pix,
        is below a hundredth of that tolerance, and only within that
        radius, where the radial polynomial has not passed its largest
        distorted radius.  Near that radius the distorted point changes
        little with the undistorted one, so the step, not the miss alone,
        decides."""
        tolerance = RADIUS_INVERSION_TOLERANCE_PIXELS / self.principalDistancePixels
        largestRadius = self.undistortedRadii[-1]
        pointX, pointY = startPoint
        forwardX, forwardY = self._DistortedPoint(pointX, pointY)
        missX, missY = forwardX - distortedX, forwardY - distortedY
        missLength = math.hypot(missX, missY)
        for _ in range(RADIUS_NEWTON_MAXIMUM_STEPS):
            step = self._NewtonStep(pointX, pointY, missX, missY)
            if step is None:
                break
            stepFactor = 1.0
            for _ in range(RADIUS_NEWTON_MAXIMUM_HALVINGS):
                nextX, nextY = pointX - stepFactor * step[0], pointY - stepFactor * step[1]
                if math.hypot(nextX, nextY) <= largestRadius:
                    nextForwardX, nextForwardY = self._DistortedPoint(nextX, nextY)
                    nextMissX, nextMissY = nextForwardX - distortedX, nextForwardY - distortedY
                    nextMissLength = math.hypot(nextMissX, nextMissY)
                    if nextMissLength < missLength or nextMissLength <= RADIUS_NEWTON_RESIDUAL_FLOOR:
                        break
                stepFactor *= 0.5
            else:
                break
            stepLength = stepFactor * math.hypot(step[0], step[1])
            pointX, pointY, missX, missY, missLength = nextX, nextY, nextMissX, nextMissY, nextMissLength
            if missLength <= tolerance and stepLength <= 0.01 * tolerance:
                self.lastFailure = None
                return pointX, pointY
        self.lastFailure = failure
        return None

    def _InverseWithRestarts(self, distortedX, distortedY, startPoint, failure):
        """_FullModelInverse from *startPoint*, and where it finds no point,
        from the start scaled towards the principal point by each of
        RADIUS_NEWTON_RESTART_FACTORS in turn."""
        point = self._FullModelInverse(distortedX, distortedY, startPoint, failure)
        for factor in RADIUS_NEWTON_RESTART_FACTORS:
            if point is not None:
                break
            point = self._FullModelInverse(distortedX, distortedY, (factor * startPoint[0], factor * startPoint[1]),
                                           failure)
        return point

    def UndistortedPoint(self, pixelX, pixelY):
        """The undistorted normalised point (x, y) of a pixel position, or
        None, with the reason in lastFailure.  Each pass takes the
        displacement of the decentring terms p1 to p4 at the point found
        (_DecentringDisplacement) off the distorted point.  Where the passes
        fail, Newton's method on the radial and decentring distortion
        together takes over (_FullModelInverse)."""
        principalDistancePixels = self.principalDistancePixels
        distortedY = (pixelY - self.principalPointY) / principalDistancePixels
        distortedX = (pixelX - self.principalPointX - self.nonOrthogonality * distortedY) / (
            principalDistancePixels + self.affinity)
        point = self._RadialInverse(distortedX, distortedY)
        if point is None:
            if not self.hasDecentring:
                self.lastFailure = "beyond"
                return None
            # The decentring distortion can carry a position beyond the
            # largest distorted radius of the radial polynomial.
            return self._InverseWithRestarts(distortedX, distortedY, self._NewtonStartPoint(distortedX, distortedY),
                                             "beyond")
        if not self.hasDecentring:
            self.lastFailure = None
            return point
        tolerance = RADIUS_INVERSION_TOLERANCE_PIXELS / principalDistancePixels
        for _ in range(RADIUS_INVERSION_MAXIMUM_PASSES):
            pointX, pointY = point
            decentringX, decentringY = self._DecentringDisplacement(pointX, pointY)
            nextPoint = self._RadialInverse(distortedX - decentringX, distortedY - decentringY)
            if nextPoint is None:
                return self._InverseWithRestarts(distortedX, distortedY, point, "beyond")
            if abs(nextPoint[0] - pointX) + abs(nextPoint[1] - pointY) <= tolerance:
                self.lastFailure = None
                return nextPoint
            point = nextPoint
        return self._InverseWithRestarts(distortedX, distortedY, point, "unconverged")

    def RadiusAt(self, pixelX, pixelY):
        """r of the profiles in pixels at the position, or None."""
        point = self.UndistortedPoint(pixelX, pixelY)
        if point is None:
            return None
        return self.principalDistancePixels * math.hypot(point[0], point[1])


class CoverageRing:
    """One ring of the coverage by radius: from *start* to *end* in pixels
    of r, its 2D tie points, their share of all 2D tie points with an r,
    the share of the image format in the ring, the relative density (the
    first share divided by the second, None outside the image format), and
    the radial residual (None with fewer than COVERAGE_MINIMUM_RESIDUAL_COUNT
    2D tie points or without a value).  In the wedge of a direction the
    counts and shares are those of the part of the ring in the wedge, and
    the shares stay shares of all 2D tie points with an r and of all of
    the image format where r is found, in all directions, as
    COVERAGE_SHARES_LINE says."""

    def __init__(self, start, end, pointCount, pointShare, areaShare, relativeDensity, radialResidual):
        self.start, self.end, self.pointCount = start, end, pointCount
        self.pointShare, self.areaShare = pointShare, areaShare
        self.relativeDensity, self.radialResidual = relativeDensity, radialResidual


class ResidualCellAccumulator:
    """Cell means of residual vectors on the grid of DistortionGridShape,
    as Metashape's Residuals: each residual is weighted by the inverse of
    the keypoint size of its 2D tie point, and the mean of a cell is the
    weighted mean of all residuals in it, from all images of the group.
    Chunk 1 of Project5 gives Metashape's 0.0428 px RMS and 0.166 px
    maximum, and 0.0784 px RMS without the weights.  A residual without a
    positive keypoint size is counted and takes no part in the cell
    means, and one at a position or with components that are not finite
    numbers is counted apart and left out altogether.  The sum of the
    squared lengths of the other residuals gives the RMS of the individual
    residuals.

    The accumulator also keeps the unweighted sums and the x and y
    components of the residuals of every cell, for the options Cell means
    without weights and Cell medians (CellValues), in which a residual
    without a keypoint size takes part as well.

    With the *calibration* of a frame or fisheye group, it also keeps the
    coverage by radius: the 2D tie points per ring of the radial distance
    r of the profiles (RadialDistanceMap), with the sums and the values of
    their radial residual, the component of the residual along the
    direction away from the principal point, and the share of the image
    format in each ring (CoverageRings).  The counts per cell give the
    Coverage mode (CoverageCells).  The coverage by radius is kept per
    ring and per sector of one degree of the direction from the principal
    point (DirectionSector), so that the rings of the wedge of a direction
    are the sums of its sectors, and the share of the image format per
    ring and sector comes from rays from the principal point
    (_CountImageFormat)."""

    def __init__(self, pixelColumnCount, pixelRowCount, calibration=None):
        self.columns, self.rows = DistortionGridShape(pixelColumnCount, pixelRowCount)
        self.cellWidth = pixelColumnCount / float(self.columns)
        self.cellHeight = pixelRowCount / float(self.rows)
        cellCount = self.columns * self.rows
        self.weightedSumsX, self.weightedSumsY, self.weightSums = [0.0] * cellCount, [0.0] * cellCount, [0.0] * cellCount
        self.sumsX, self.sumsY, self.counts = [0.0] * cellCount, [0.0] * cellCount, [0] * cellCount
        # The components in arrays of doubles, eight bytes each, so that two
        # million residuals take about 32 MB.
        self.componentsX = [array.array("d") for _ in range(cellCount)]
        self.componentsY = [array.array("d") for _ in range(cellCount)]
        self.residualCount = 0
        self.squaredLengthSum = 0.0
        self.withoutKeypointSizeCount = 0
        self.nonFiniteCount = 0
        # The coverage by radius, per ring and sector of one degree, keyed
        # by SectorKey.
        self.radialMap, self.ringWidth = None, None
        self.sectorCounts = collections.Counter()
        self.sectorSums, self.sectorWeightedSums, self.sectorWeightSums = {}, {}, {}
        self.sectorComponents = {}
        self.withoutRadiusCount = 0
        self.unconvergedRadiusCount = 0
        # The image format per ring and sector, in px², and its sum, whose
        # ratio is the share of the format.
        self.formatParts, self.formatTotal = collections.Counter(), 0.0
        if calibration is not None and IsPositiveNumber(getattr(calibration, "f", None)):
            try:
                self.radialMap = RadialDistanceMap(calibration, pixelColumnCount, pixelRowCount)
                self.ringWidth = CoverageRingWidthPixels(pixelColumnCount, pixelRowCount)
                self._CountImageFormat(pixelColumnCount, pixelRowCount)
            except Exception as suppressedError:
                ReportSuppressedError("interiorOrientationViewer.ResidualCellAccumulator.coverage", suppressedError)
                self.radialMap = None

    def _CountImageFormat(self, pixelColumnCount, pixelRowCount):
        """Find the share of the image format in every ring of r and sector
        of direction (formatParts and formatTotal): along
        COVERAGE_FORMAT_RAYS_PER_DEGREE rays per degree from the principal
        point, r is found where each ray enters the image format, every
        ring width (COVERAGE_FORMAT_SAMPLES_PER_RING times per ring), and
        where it leaves the format.  Between two of these points r is taken
        as linear where it is (_AddRefinedStretch), and the stretch is
        halved where it is not.  The stretch of a ray from the distance d1
        to d2 from the principal point stands for (d2² − d1²) / 2 times the
        angle between two rays of the format, in px².  Where no r is found,
        the ray ends (_LastDistanceWithRadius)."""
        rayCount = 360 * COVERAGE_FORMAT_RAYS_PER_DEGREE
        angleStep = 2.0 * math.pi / rayCount
        sampleStep = self.ringWidth / float(COVERAGE_FORMAT_SAMPLES_PER_RING)
        for rayIndex in range(rayCount):
            angle = (rayIndex + 0.5) * angleStep
            self._CountRay(pixelColumnCount, pixelRowCount, math.cos(angle), math.sin(angle), angleStep, sampleStep,
                           (rayIndex // COVERAGE_FORMAT_RAYS_PER_DEGREE) % 360)

    def _CountRay(self, pixelColumnCount, pixelRowCount, directionX, directionY, angleStep, sampleStep, sector):
        """Add the image format along one ray of *sector* (_CountImageFormat)."""
        radialMap = self.radialMap
        span = RayFormatSpan(radialMap.principalPointX, radialMap.principalPointY, directionX, directionY,
                             pixelColumnCount, pixelRowCount)
        if span is None:
            return
        entry, exit = span
        stepCount = max(1, int(math.ceil((exit - entry) / sampleStep)))
        previousDistance, previousRadius = None, None
        for stepIndex in range(stepCount + 1):
            distance = entry + (exit - entry) * stepIndex / float(stepCount)
            radius = radialMap.RadiusAt(radialMap.principalPointX + distance * directionX,
                                        radialMap.principalPointY + distance * directionY)
            if radius is None:
                if previousDistance is not None:
                    endDistance, endRadius = self._LastDistanceWithRadius(previousDistance, previousRadius, distance,
                                                                          directionX, directionY)
                    self._AddRefinedStretch(sector, previousDistance, endDistance, previousRadius, endRadius,
                                            angleStep, directionX, directionY, 0)
                return
            if previousDistance is not None:
                self._AddRefinedStretch(sector, previousDistance, distance, previousRadius, radius, angleStep,
                                        directionX, directionY, 0)
            previousDistance, previousRadius = distance, radius

    def _AddRefinedStretch(self, sector, startDistance, endDistance, startRadius, endRadius, angleStep,
                           directionX, directionY, halvings):
        """Add a stretch of a ray (_AddStretch), halved where r is not
        linear along it: r is found at its middle, and each half is added
        as linear when that r lies within COVERAGE_FORMAT_RADIUS_TOLERANCE_RINGS
        ring widths of the straight line between the ends, or after
        COVERAGE_FORMAT_MAXIMUM_HALVINGS halvings, and is halved again
        otherwise."""
        radialMap = self.radialMap
        middleDistance = 0.5 * (startDistance + endDistance)
        middleRadius = radialMap.RadiusAt(radialMap.principalPointX + middleDistance * directionX,
                                          radialMap.principalPointY + middleDistance * directionY)
        if middleRadius is None:
            self._AddStretch(sector, startDistance, endDistance, startRadius, endRadius, angleStep)
            return
        isLinear = (abs(middleRadius - 0.5 * (startRadius + endRadius))
                    <= COVERAGE_FORMAT_RADIUS_TOLERANCE_RINGS * self.ringWidth)
        if isLinear or halvings >= COVERAGE_FORMAT_MAXIMUM_HALVINGS:
            self._AddStretch(sector, startDistance, middleDistance, startRadius, middleRadius, angleStep)
            self._AddStretch(sector, middleDistance, endDistance, middleRadius, endRadius, angleStep)
            return
        self._AddRefinedStretch(sector, startDistance, middleDistance, startRadius, middleRadius, angleStep,
                                directionX, directionY, halvings + 1)
        self._AddRefinedStretch(sector, middleDistance, endDistance, middleRadius, endRadius, angleStep,
                                directionX, directionY, halvings + 1)

    def _LastDistanceWithRadius(self, distanceWithRadius, radius, distanceWithout, directionX, directionY):
        """(distance, r) of the farthest point of a ray with an r between a
        distance with an r and a farther one without, by bisection."""
        radialMap = self.radialMap
        for _ in range(24):
            middle = 0.5 * (distanceWithRadius + distanceWithout)
            middleRadius = radialMap.RadiusAt(radialMap.principalPointX + middle * directionX,
                                              radialMap.principalPointY + middle * directionY)
            if middleRadius is None:
                distanceWithout = middle
            else:
                distanceWithRadius, radius = middle, middleRadius
        return distanceWithRadius, radius

    def _AddFormatPart(self, ring, sector, startDistance, endDistance, angleStep):
        part = 0.5 * abs(endDistance * endDistance - startDistance * startDistance) * angleStep
        self.formatParts[SectorKey(ring, sector)] += part
        self.formatTotal += part

    def _AddStretch(self, sector, startDistance, endDistance, startRadius, endRadius, angleStep):
        """Add a stretch of a ray of *sector* from the distance
        *startDistance* to *endDistance*, along which r runs linearly from
        *startRadius* to *endRadius*, to the rings that r passes through."""
        ringWidth = self.ringWidth
        startRing, endRing = int(startRadius // ringWidth), int(endRadius // ringWidth)
        ringStep = 1 if endRing > startRing else -1
        ring, distance = startRing, startDistance
        while ring != endRing:
            boundary = (ring + 1) * ringWidth if ringStep > 0 else ring * ringWidth
            boundaryDistance = startDistance + (boundary - startRadius) / (endRadius - startRadius) * (
                endDistance - startDistance)
            self._AddFormatPart(ring, sector, distance, boundaryDistance, angleStep)
            ring, distance = ring + ringStep, boundaryDistance
        self._AddFormatPart(endRing, sector, distance, endDistance, angleStep)

    def _AddToRing(self, imageX, imageY, residualX, residualY, weight):
        """Add a residual to the ring of r and the sector of direction of
        its position, with its radial component, the component along the
        direction away from the principal point."""
        radius = self.radialMap.RadiusAt(imageX, imageY)
        if radius is None:
            self.withoutRadiusCount += 1
            if self.radialMap.lastFailure == "unconverged":
                self.unconvergedRadiusCount += 1
            return
        offsetX, offsetY = self.radialMap.Offset(imageX, imageY)
        offsetLength = math.hypot(offsetX, offsetY)
        radialComponent = (residualX * offsetX + residualY * offsetY) / offsetLength if offsetLength > 0.0 else 0.0
        key = SectorKey(int(radius // self.ringWidth), DirectionSector(offsetX, offsetY))
        self.sectorCounts[key] += 1
        self.sectorSums[key] = self.sectorSums.get(key, 0.0) + radialComponent
        components = self.sectorComponents.get(key)
        if components is None:
            components = self.sectorComponents[key] = array.array("d")
        components.append(radialComponent)
        if weight is not None:
            self.sectorWeightedSums[key] = self.sectorWeightedSums.get(key, 0.0) + weight * radialComponent
            self.sectorWeightSums[key] = self.sectorWeightSums.get(key, 0.0) + weight

    def Add(self, imageX, imageY, residualX, residualY, keypointSize):
        if not all(math.isfinite(value) for value in (imageX, imageY, residualX, residualY)):
            self.nonFiniteCount += 1
            return
        self.residualCount += 1
        self.squaredLengthSum += residualX * residualX + residualY * residualY
        column = min(max(int(imageX / self.cellWidth), 0), self.columns - 1)
        row = min(max(int(imageY / self.cellHeight), 0), self.rows - 1)
        cellIndex = row * self.columns + column
        self.sumsX[cellIndex] += residualX
        self.sumsY[cellIndex] += residualY
        self.counts[cellIndex] += 1
        self.componentsX[cellIndex].append(residualX)
        self.componentsY[cellIndex].append(residualY)
        hasKeypointSize = isinstance(keypointSize, (int, float)) and math.isfinite(keypointSize) and keypointSize > 0.0
        if self.radialMap is not None:
            self._AddToRing(imageX, imageY, residualX, residualY, 1.0 / keypointSize if hasKeypointSize else None)
        if not hasKeypointSize:
            self.withoutKeypointSizeCount += 1
            return
        weight = 1.0 / keypointSize
        self.weightedSumsX[cellIndex] += weight * residualX
        self.weightedSumsY[cellIndex] += weight * residualY
        self.weightSums[cellIndex] += weight

    def CoverageCells(self):
        """(counts per cell in rows from the top, columns, rows) of the
        Coverage mode: the 2D tie points of every cell, pooled over the
        images of the group."""
        return list(self.counts), self.columns, self.rows

    def _RingResidual(self, keys, pointCount, statistic):
        """The radial residual of the part of a ring in the sectors of
        *keys* by *statistic*, as the option Residuals defines the cell
        values (CellValues)."""
        if statistic == RESIDUAL_STATISTIC_MEAN:
            return sum(self.sectorSums.get(key, 0.0) for key in keys) / pointCount
        if statistic == RESIDUAL_STATISTIC_MEDIAN:
            values = array.array("d")
            for key in keys:
                components = self.sectorComponents.get(key)
                if components is not None:
                    values.extend(components)
            return statistics.median(values)
        weightSum = sum(self.sectorWeightSums.get(key, 0.0) for key in keys)
        if not weightSum > 0.0:
            return None
        return sum(self.sectorWeightedSums.get(key, 0.0) for key in keys) / weightSum

    def CoverageRings(self, statistic=RESIDUAL_STATISTIC_WEIGHTED_MEAN, directionDegrees=None):
        """The CoverageRing of every ring of r with a share of the image
        format or 2D tie points, in order of r, or [] without the coverage
        by radius.  In all directions (None) by default, and in the wedge
        of *directionDegrees* (WedgeSectors): the share of all 2D tie
        points with an r that lie in the ring and the wedge, divided by the
        share of the image format where r is found (formatTotal) that lies
        in them, so that an even spread gives 1 in every direction."""
        if self.radialMap is None:
            return []
        sectors = range(360) if directionDegrees is None else WedgeSectors(directionDegrees)
        pointTotal = sum(self.sectorCounts.values())
        ringIndices = sorted({key // 360 for key in self.sectorCounts} | {key // 360 for key in self.formatParts})
        rings = []
        for ring in ringIndices:
            keys = [SectorKey(ring, sector) for sector in sectors]
            pointCount = sum(self.sectorCounts.get(key, 0) for key in keys)
            formatPart = sum(self.formatParts.get(key, 0.0) for key in keys)
            if pointCount <= 0 and not formatPart > 0.0:
                continue
            areaShare = formatPart / self.formatTotal if self.formatTotal > 0.0 else 0.0
            pointShare = pointCount / float(pointTotal) if pointTotal else 0.0
            radialResidual = (self._RingResidual(keys, pointCount, statistic)
                              if pointCount >= COVERAGE_MINIMUM_RESIDUAL_COUNT else None)
            rings.append(CoverageRing(ring * self.ringWidth, (ring + 1) * self.ringWidth, pointCount, pointShare,
                                      areaShare, pointShare / areaShare if areaShare > 0.0 else None,
                                      radialResidual))
        return rings

    def CellCentre(self, cellIndex):
        """(centreX, centreY) of a cell in pixel coordinates."""
        return ((cellIndex % self.columns + 0.5) * self.cellWidth, (cellIndex // self.columns + 0.5) * self.cellHeight)

    def CellMeans(self):
        """(centreX, centreY, meanX, meanY) of every cell with residuals."""
        return [self.CellCentre(cellIndex) + (self.weightedSumsX[cellIndex] / weightSum,
                                             self.weightedSumsY[cellIndex] / weightSum)
                for cellIndex, weightSum in enumerate(self.weightSums) if weightSum > 0.0]

    def CellValues(self, statistic):
        """(centreX, centreY, valueX, valueY) of every cell with residuals
        for *statistic*: the means weighted by the inverse keypoint size
        (RESIDUAL_STATISTIC_WEIGHTED_MEAN, Metashape's, CellMeans), the
        means without weights (RESIDUAL_STATISTIC_MEAN), or the medians of
        the x and of the y components (RESIDUAL_STATISTIC_MEDIAN)."""
        if statistic == RESIDUAL_STATISTIC_MEAN:
            return [self.CellCentre(cellIndex) + (self.sumsX[cellIndex] / count, self.sumsY[cellIndex] / count)
                    for cellIndex, count in enumerate(self.counts) if count > 0]
        if statistic == RESIDUAL_STATISTIC_MEDIAN:
            return [self.CellCentre(cellIndex) + (statistics.median(self.componentsX[cellIndex]),
                                                  statistics.median(self.componentsY[cellIndex]))
                    for cellIndex, count in enumerate(self.counts) if count > 0]
        return self.CellMeans()

    def CellResidualCount(self, statistic):
        """Number of residuals in the cell values of *statistic*: those
        with a positive keypoint size for the weighted means, all of them
        for the means without weights and the medians."""
        if statistic in (RESIDUAL_STATISTIC_MEAN, RESIDUAL_STATISTIC_MEDIAN):
            return self.residualCount
        return self.CellMeanResidualCount()

    def IndividualRms(self):
        """RMS of the lengths of all residuals added, or None."""
        return math.sqrt(self.squaredLengthSum / self.residualCount) if self.residualCount else None

    def CellMeanResidualCount(self):
        """Number of residuals in the cell means."""
        return self.residualCount - self.withoutKeypointSizeCount


def CollectResiduals(sensor, chunk, samplingStride=1, residualCells=None):
    """Collect the reprojection residuals of the valid tie points on every
    enabled, oriented image of the given interior orientation group (slave
    images of a multi-camera rig included, keyframes of an animation track
    left out). With samplingStride n > 1 only every n-th projection of
    each image is used, so that every image still contributes. The
    Console lists the images used and left out, the number of residuals
    and any failures.  With a ResidualCellAccumulator as *residualCells*,
    each residual is added to it with the keypoint size of its 2D tie
    point instead of being kept, and the list returned is empty.

    Returns list of (imageX, imageY, residualDx, residualDy).
    """
    residuals = []
    calibration = sensor.calibration
    if calibration is None:
        print("  [MetaFOCUS] CollectResiduals: the interior orientation group has no calibration.")
        return residuals

    tiePoints = getattr(chunk, "tie_points", None)
    if tiePoints is None:
        print("  [MetaFOCUS] CollectResiduals: the chunk has no tie points.")
        return residuals
    if tiePoints.points is None:
        print("  [MetaFOCUS] CollectResiduals: the tie points have no point list (tie_points.points is None).")
        return residuals

    pointByTrackId = {}
    for point in tiePoints.points:
        if point.valid:
            pointByTrackId[point.track_id] = point
    print("  [MetaFOCUS] CollectResiduals: {:,} valid tie points.".format(
        len(pointByTrackId)))

    groupImages, skippedImageCounts = SelectGroupImages(sensor, chunk)
    samplingStride = max(1, int(samplingStride))
    examinedProjectionCount = 0
    collectedResidualCount = 0
    failedProjectionCount = 0
    unevaluatedResidualCount = 0
    nonFiniteResidualCount = 0
    nonFinitePositionCount = 0
    firstFailure = None

    for camera in groupImages:
        try:
            projections = tiePoints.projections[camera]
        except KeyError:
            continue
        except Exception as suppressedError:
            ReportSuppressedError("interiorOrientationViewer.CollectResiduals.projections", suppressedError)
            continue
        if projections is None:
            continue

        # The residuals come from the camera model of the image,
        # rolling-shutter motion included (TiePointResidual), as in the
        # Gradual Selection Filter.  The homogeneous coordinates also
        # cover the 3D tie points at infinity (w = 0), and a residual that
        # is not a finite number is left out and counted, as in the filter.
        for projectionIndex, projection in enumerate(projections):
            if projectionIndex % samplingStride:
                continue
            examinedProjectionCount += 1
            tiePoint = pointByTrackId.get(projection.track_id)
            if tiePoint is None:
                continue
            try:
                errorVector = TiePointResidual(camera, tiePoint, projection)
                # Camera.error gives no residual for a 3D tie point that the
                # image cannot project (Metashape 2.3.2 in Geert's Project4):
                # counted, as in the Gradual Selection Filter, instead of an
                # AttributeError.
                if errorVector is None:
                    unevaluatedResidualCount += 1
                    continue
                residualX, residualY = errorVector.x, errorVector.y
                if not (math.isfinite(residualX) and math.isfinite(residualY)):
                    nonFiniteResidualCount += 1
                    continue
                measuredPosition = projection.coord
                if residualCells is not None:
                    residualCells.Add(measuredPosition.x, measuredPosition.y, residualX, residualY,
                                      getattr(projection, "size", None))
                    if residualCells.nonFiniteCount > nonFinitePositionCount:
                        nonFinitePositionCount = residualCells.nonFiniteCount
                        continue
                else:
                    residuals.append((measuredPosition.x, measuredPosition.y, residualX, residualY))
                collectedResidualCount += 1
            except Exception as exceptionDetails:
                failedProjectionCount += 1
                if firstFailure is None:
                    firstFailure = exceptionDetails
                    ReportSuppressedError("interiorOrientationViewer.CollectResiduals.residual", exceptionDetails)
                continue

    print("  [MetaFOCUS] CollectResiduals: interior orientation group '{}' (key={})".format(
        sensor.label or "unknown", sensor.key))
    print("  [MetaFOCUS]   " + DescribeImageSelection(len(groupImages), skippedImageCounts))
    # The keypoints examined include those of tracks without a valid tie
    # point, which give no residual.
    if samplingStride > 1:
        print("  [MetaFOCUS]   residuals collected: {:,} (2D tie points of valid tie points), matched keypoints "
              "examined: {:,} (sampled 1 in {:,})".format(collectedResidualCount, examinedProjectionCount,
                                                         samplingStride))
    else:
        print("  [MetaFOCUS]   residuals collected: {:,} (2D tie points of valid tie points), matched keypoints "
              "examined: {:,}".format(collectedResidualCount, examinedProjectionCount))
    if failedProjectionCount:
        print("  [MetaFOCUS]   residual computation failed for 2D tie points: {:,} (first error: {})".format(
            failedProjectionCount, DescribeException(firstFailure)))
    if unevaluatedResidualCount:
        print("  [MetaFOCUS]   2D tie points whose residuals Metashape could not evaluate (Camera.error gave none), "
              "left out: {:,}".format(unevaluatedResidualCount))
    if nonFiniteResidualCount:
        print("  [MetaFOCUS]   {} left out.".format(NonFiniteResidualsText(nonFiniteResidualCount)))
    if nonFinitePositionCount:
        print("  [MetaFOCUS]   {} at a position that is not a finite number left out.".format(
            FormatCountWithNoun(nonFinitePositionCount, "residual", "residuals")))
    if residualCells is not None and residualCells.withoutKeypointSizeCount:
        print("  [MetaFOCUS]   {} without a positive keypoint size left out of the cell means.".format(
            FormatCountWithNoun(residualCells.withoutKeypointSizeCount, "residual", "residuals")))
    return residuals


def FieldStatistics(vectors):
    """(RMS, maximum) of the lengths of the vectors (x, y, dx, dy), or
    (None, None) for no vectors."""
    if not vectors:
        return None, None
    squaredLengths = [displacementX * displacementX + displacementY * displacementY
                      for _, _, displacementX, displacementY in vectors]
    return math.sqrt(sum(squaredLengths) / len(squaredLengths)), math.sqrt(max(squaredLengths))


def FormatAsMetashape(value):
    """A value with three significant digits, as Metashape's Distortion
    Plot writes its RMS and maximum ("n/a" if it is not finite)."""
    if value is None or not math.isfinite(value):
        return "n/a"
    return "{:.3g}".format(value)


def FormatConsoleNumber(value):
    """A value with six significant digits for the Console."""
    if value is None or not math.isfinite(value):
        return "n/a"
    return "{:.6g}".format(value)


def VectorColourRgb(length, referenceLength):
    """(red, green, blue) of a vector of *length*: the hue runs from blue
    for zero to red for *referenceLength* and longer, as in Metashape's
    Distortion Plot, where the reference length is twice the median
    length (RED_AT_MEDIAN_MULTIPLE)."""
    fraction = min(max(length / referenceLength, 0.0), 1.0) if referenceLength > 0.0 else 0.0
    red, green, blue = colorsys.hsv_to_rgb((1.0 - fraction) * 240.0 / 360.0, 1.0, 1.0)
    return int(round(255 * red)), int(round(255 * green)), int(round(255 * blue))


# A vector of the vector field is read out when the pointer lies within
# this share of the width of a grid cell from its grid point
# (VectorReadOutText).
VECTOR_READ_OUT_CELL_FRACTION = 0.75


def NearestVector(vectors, imageX, imageY):
    """The vector (gridX, gridY, displacementX, displacementY) of *vectors*
    whose grid point lies nearest to (imageX, imageY), with that distance,
    or (None, None) without vectors."""
    nearestVector, nearestDistance = None, None
    for vector in vectors:
        distance = math.hypot(vector[0] - imageX, vector[1] - imageY)
        if nearestDistance is None or distance < nearestDistance:
            nearestVector, nearestDistance = vector, distance
    return nearestVector, nearestDistance


def VectorReadOutText(vectors, title, geometry, pixelColumnCount, pixelRowCount, screenX, screenY):
    """The text of the tooltip of the vector field at the screen position
    (screenX, screenY) of the widget, whose plot has the *geometry* of
    VectorFieldWidget.PlotGeometry: the title of the field, the grid
    point nearest to the position in pixel coordinates, and the components
    and the length of its vector, in px with three significant digits as
    the plot writes them.  "" outside the plot, without vectors, and
    farther than VECTOR_READ_OUT_CELL_FRACTION of a grid cell from every
    grid point (Geert, 13 October 2026: "a read-out when you hover over a
    plot would be cool")."""
    if geometry is None or not vectors or not pixelColumnCount or not pixelRowCount:
        return ""
    originX, originY, plotWidth, plotHeight = geometry
    if not (originX <= screenX <= originX + plotWidth and originY <= screenY <= originY + plotHeight):
        return ""
    imageX = (screenX - originX) * pixelColumnCount / float(plotWidth)
    imageY = (screenY - originY) * pixelRowCount / float(plotHeight)
    vector, distance = NearestVector(vectors, imageX, imageY)
    cellWidth = pixelColumnCount / float(DistortionGridShape(pixelColumnCount, pixelRowCount)[0])
    if vector is None or distance > VECTOR_READ_OUT_CELL_FRACTION * cellWidth:
        return ""
    gridX, gridY, displacementX, displacementY = vector
    return ("{}\nGrid point at x {:,.1f} px, y {:,.1f} px\nVector: {} px along x, {} px along y, length {} px".format(
        title, gridX, gridY, FormatAsMetashape(displacementX), FormatAsMetashape(displacementY),
        FormatAsMetashape(math.hypot(displacementX, displacementY))))


# Rows below the lowest profile plot, in screen pixels: the tick labels
# (baseline 12 below the plot) and "Radius (px)" (baseline 22 below it),
# and then the legend.
PROFILE_AXIS_LABELS_HEIGHT = 28
PROFILE_LEGEND_ROW_HEIGHT = 18
# The plot row of a tab (the layout that Geert approved on 14 October 2026:
# "Perfect. It looks amazing! Keep it like that."), in screen pixels.  The
# profiles are PLOT_ROW_HEIGHT high, with their first plot PROFILE_MARGIN_TOP
# below their top and the radius labels and the legend (PROFILE_BOTTOM_BAND)
# below the last.  The vector field beside them is SLIDER_RAISE less high, so
# that the vector scale slider under it sits that much higher ("you can also
# move the vector scale slider 5 pixels up"), and its plot runs from the top
# of the radial profile to the bottom of the decentring profile.  The plot
# row has a fixed height, so that the plots do not grow with the width of
# the window, which Show notes and statistics widens.
PLOT_ROW_HEIGHT = 470
PROFILE_MARGIN_TOP = 28
PROFILE_BOTTOM_BAND = PROFILE_AXIS_LABELS_HEIGHT + PROFILE_LEGEND_ROW_HEIGHT
SLIDER_RAISE = 5
VECTOR_PLOT_MARGIN_BOTTOM = PROFILE_BOTTOM_BAND - SLIDER_RAISE
VECTOR_PLOT_SIDE_MARGIN = 10
# The widest plot of the vector field that sets the width of its column, as
# a multiple of its height (an image format of 2:1 at most: a wider one,
# a panorama for example, gets a less high plot instead).
VECTOR_PLOT_WIDEST_ASPECT = 2.0
# Space added between the vector scale slider and the table of the vector
# fields (Geert: "not a lot, just a bit").
SLIDER_TABLE_EXTRA_SPACE = 8


def VectorPlotHeight():
    """Height of the plot of the vector field in the plot row: from the top
    of the radial profile to the bottom of the decentring profile."""
    return PLOT_ROW_HEIGHT - PROFILE_MARGIN_TOP - PROFILE_BOTTOM_BAND


def VectorFieldMinimumWidth(pixelColumnCount, pixelRowCount):
    """The width of the vector field widget that leaves its plot the whole
    height of the plot row (VectorPlotHeight), for an image format of at
    most VECTOR_PLOT_WIDEST_ASPECT to 1."""
    aspectRatio = min(pixelColumnCount / float(max(pixelRowCount, 1)), VECTOR_PLOT_WIDEST_ASPECT)
    return int(math.ceil(VectorPlotHeight() * aspectRatio)) + 2 * VECTOR_PLOT_SIDE_MARGIN


def BorderPoint(principalPoint, angleDegrees, pixelColumnCount, pixelRowCount):
    """The point, in pixel coordinates, where the ray from the principal
    point in the direction *angleDegrees* (from the image x axis towards the
    image y axis) leaves the image format, or None when it misses the
    format (RayFormatSpan)."""
    directionX, directionY = math.cos(math.radians(angleDegrees)), math.sin(math.radians(angleDegrees))
    span = RayFormatSpan(principalPoint[0], principalPoint[1], directionX, directionY, pixelColumnCount,
                         pixelRowCount)
    if span is None:
        return None
    return principalPoint[0] + span[1] * directionX, principalPoint[1] + span[1] * directionY


def ScreenPointOfPixel(point, geometry, pixelColumnCount, pixelRowCount):
    """The screen position (x, y) of a point in pixel coordinates in the
    plot of *geometry* (originX, originY, plotWidth, plotHeight)."""
    originX, originY, plotWidth, plotHeight = geometry
    return (originX + point[0] * plotWidth / float(pixelColumnCount),
            originY + point[1] * plotHeight / float(pixelRowCount))


def DirectionAtScreenPosition(geometry, pixelColumnCount, pixelRowCount, principalPoint, screenX, screenY):
    """The direction in whole degrees, 0 to 359, of the screen position
    (screenX, screenY) in the plot of *geometry* from the principal point,
    counted from the image x axis towards the image y axis, or None outside
    the plot or at the principal point."""
    if geometry is None or principalPoint is None or not pixelColumnCount or not pixelRowCount:
        return None
    originX, originY, plotWidth, plotHeight = geometry
    if not (originX <= screenX <= originX + plotWidth and originY <= screenY <= originY + plotHeight):
        return None
    offsetX = (screenX - originX) / float(plotWidth) * pixelColumnCount - principalPoint[0]
    offsetY = (screenY - originY) / float(plotHeight) * pixelRowCount - principalPoint[1]
    if math.hypot(offsetX, offsetY) < 1e-9:
        return None
    return int(round(math.degrees(math.atan2(offsetY, offsetX)) % 360.0)) % 360


def PaintCoverageDirection(painter, geometry, pixelColumnCount, pixelRowCount, principalPoint, directionDegrees):
    """The direction of the coverage by radius in the Coverage map: a line
    from the principal point to the border of the image format, the edges
    of the wedge (DIRECTION_HALF_WIDTH_DEGREES) dashed, each on a white
    underlay so that it stays visible on the dark cells, and a white dot
    at the principal point."""
    originX, originY, plotWidth, plotHeight = geometry
    startX, startY = ScreenPointOfPixel(principalPoint, geometry, pixelColumnCount, pixelRowCount)
    start = QtCore.QPointF(startX, startY)
    painter.save()
    painter.setClipRect(originX, originY, plotWidth, plotHeight)
    for angleDegrees, underlayWidth, lineWidth, isDashed in (
            (directionDegrees - DIRECTION_HALF_WIDTH_DEGREES, 3.0, 1.3, True),
            (directionDegrees + DIRECTION_HALF_WIDTH_DEGREES, 3.0, 1.3, True),
            (directionDegrees, 4.5, 2.0, False)):
        border = BorderPoint(principalPoint, angleDegrees, pixelColumnCount, pixelRowCount)
        if border is None:
            continue
        endX, endY = ScreenPointOfPixel(border, geometry, pixelColumnCount, pixelRowCount)
        end = QtCore.QPointF(endX, endY)
        painter.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255, 230), underlayWidth))
        painter.drawLine(start, end)
        painter.setPen(QtGui.QPen(QtGui.QColor(0, 0, 0), lineWidth,
                                  QtCore.Qt.DashLine if isDashed else QtCore.Qt.SolidLine))
        painter.drawLine(start, end)
    painter.setPen(QtGui.QPen(QtGui.QColor(0, 0, 0), 1.2))
    painter.setBrush(QtGui.QColor(255, 255, 255))
    painter.drawEllipse(start, 3.5, 3.5)
    painter.restore()


# The legend of the Coverage mode, under the line of its counts (Geert, 14
# October 2026).
COVERAGE_LEGEND_LABEL = "2D tie points per cell:"
COVERAGE_LEGEND_NONE = "none"
COVERAGE_LEGEND_BAR_WIDTH = 150


def CoverageLegendTexts(referenceCount):
    """The texts of the legend of the Coverage mode: the label, "none" for
    the red cells, and the ends of the colour scale, 1 and the count of the
    darkest colour (CoverageColourReference) "or more", which the scale
    leaves out without 2D tie points."""
    if referenceCount <= 0:
        return [COVERAGE_LEGEND_LABEL, COVERAGE_LEGEND_NONE]
    return [COVERAGE_LEGEND_LABEL, COVERAGE_LEGEND_NONE, "1", "{:,} or more".format(referenceCount)]


def PaintCoverageLegend(painter, left, top, coverage):
    """The legend of the Coverage mode from *left*, *top*: no 2D tie points
    (red), and the scale from 1 to the count of the darkest colour."""
    referenceCount = CoverageColourReference(coverage[0])
    texts = CoverageLegendTexts(referenceCount)
    font = QtGui.QFont()
    font.setPointSize(7)
    painter.setFont(font)
    metrics = QtGui.QFontMetrics(font)
    black, frame = QtGui.QColor(0, 0, 0), QtGui.QColor(150, 150, 150)
    painter.setPen(QtGui.QPen(black, 1))
    positionX = left
    painter.drawText(positionX, top + 9, texts[0])
    positionX += metrics.horizontalAdvance(texts[0]) + 10
    painter.fillRect(positionX, top, 16, 10, QtGui.QColor(*COVERAGE_EMPTY_RGB))
    painter.setPen(QtGui.QPen(frame, 1))
    painter.drawRect(positionX, top, 16, 10)
    painter.setPen(QtGui.QPen(black, 1))
    painter.drawText(positionX + 21, top + 9, texts[1])
    if len(texts) < 4:
        return
    positionX += 21 + metrics.horizontalAdvance(texts[1]) + 16
    painter.drawText(positionX, top + 9, texts[2])
    positionX += metrics.horizontalAdvance(texts[2]) + 5
    gradient = QtGui.QLinearGradient(positionX, 0, positionX + COVERAGE_LEGEND_BAR_WIDTH, 0)
    gradient.setColorAt(0.0, QtGui.QColor(*CoverageCellRgb(1, referenceCount)))
    gradient.setColorAt(1.0, QtGui.QColor(*COVERAGE_MANY_RGB))
    painter.fillRect(positionX, top, COVERAGE_LEGEND_BAR_WIDTH, 10, QtGui.QBrush(gradient))
    painter.setPen(QtGui.QPen(frame, 1))
    painter.drawRect(positionX, top, COVERAGE_LEGEND_BAR_WIDTH, 10)
    painter.setPen(QtGui.QPen(black, 1))
    painter.drawText(positionX + COVERAGE_LEGEND_BAR_WIDTH + 5, top + 9, texts[3])


# Colours of the Coverage mode: cells without 2D tie points, and the range
# from few to many 2D tie points.
COVERAGE_EMPTY_RGB = (240, 170, 170)
COVERAGE_FEW_RGB = (236, 242, 250)
COVERAGE_MANY_RGB = (20, 70, 160)
# The count that gets the darkest colour: this share of the cells with 2D
# tie points has at most as many.
COVERAGE_COLOUR_QUANTILE = 0.95


def CoverageColourReference(counts):
    """The count of a cell that gets the darkest colour of the Coverage
    mode: the COVERAGE_COLOUR_QUANTILE quantile of the counts of the cells
    with 2D tie points, so that a few crowded cells do not pale the rest,
    or 0 without 2D tie points."""
    filled = sorted(count for count in counts if count > 0)
    if not filled:
        return 0
    return filled[min(len(filled) - 1, int(COVERAGE_COLOUR_QUANTILE * len(filled)))]


def CoverageCellRgb(count, referenceCount):
    """(red, green, blue) of a cell of the Coverage mode with *count* 2D
    tie points: COVERAGE_EMPTY_RGB without, and from COVERAGE_FEW_RGB to
    COVERAGE_MANY_RGB at *referenceCount* and more."""
    if count <= 0:
        return COVERAGE_EMPTY_RGB
    fraction = min(count / float(referenceCount), 1.0) if referenceCount > 0 else 1.0
    return tuple(int(round(few + fraction * (many - few))) for few, many in zip(COVERAGE_FEW_RGB, COVERAGE_MANY_RGB))


def CoverageStatisticsText(coverage):
    """The line under the plot of the Coverage mode."""
    counts, columns, rows = coverage
    emptyCount = sum(1 for count in counts if count <= 0)
    return "2D tie points: {:,} in {:,} cells, {:,} of them without 2D tie points".format(
        sum(counts), columns * rows, emptyCount)


def CoverageCellReadOutText(coverage, title, geometry, pixelColumnCount, pixelRowCount, screenX, screenY):
    """The text of the tooltip of the Coverage mode at the screen position
    (screenX, screenY): the cell under the pointer in pixel coordinates and
    its 2D tie points, or "" outside the plot."""
    if geometry is None or coverage is None or not pixelColumnCount or not pixelRowCount:
        return ""
    originX, originY, plotWidth, plotHeight = geometry
    if not (originX <= screenX < originX + plotWidth and originY <= screenY < originY + plotHeight):
        return ""
    counts, columns, rows = coverage
    column = min(int((screenX - originX) / float(plotWidth) * columns), columns - 1)
    row = min(int((screenY - originY) / float(plotHeight) * rows), rows - 1)
    cellWidth, cellHeight = pixelColumnCount / float(columns), pixelRowCount / float(rows)
    count = counts[row * columns + column]
    return "{}\nCell from x {:,.1f} to {:,.1f} px, y {:,.1f} to {:,.1f} px\n{}".format(
        title, column * cellWidth, (column + 1) * cellWidth, row * cellHeight, (row + 1) * cellHeight,
        "No 2D tie points" if count <= 0 else FormatCountWithNoun(count, "2D tie point", "2D tie points"))


class VectorFieldWidget(QtWidgets.QWidget):
    """Displays one of the switchable vector fields (distortion,
    corrections, difference, or the cell means of the reprojection
    residuals) as Metashape's Distortion Plot does: each vector starts at
    its grid point, its colour runs from blue (short) to red at twice the
    median length and beyond, the median vector is drawn about 0.68 of a
    grid cell long and then scaled by the user magnification, and the
    RMS and the maximum of the vector lengths are written with three
    significant digits.  The residual field adds the RMS of the
    individual residuals.  The vectors are clipped to the plot.

    The plot is centred in the widget, and with *coverage* (SetData) the
    widget shows the Coverage mode instead: the 2D tie points per cell, the
    cells without any tinted red.  The plot fills the plot row from the top
    of the radial profile to the bottom of the decentring profile
    (PlotGeometry), the Coverage mode has a legend of its colours and
    shows the direction of the coverage by radius (coverageDirection, from
    the principalPoint in pixel coordinates), and a click in it calls
    onCoverageClicked with the direction of the click in whole degrees.
    """

    def __init__(self, pixelColumnCount, pixelRowCount, parent=None):
        super().__init__(parent)
        self.setMinimumSize(420, 320)
        # The tooltip follows the pointer (VectorReadOutText).
        self.setMouseTracking(True)
        self.pixelColumnCount = pixelColumnCount
        self.pixelRowCount = pixelRowCount
        self.vectors = []
        self.title = ""
        self.individualRms = None
        self.emptyText = None
        self.coverage = None
        self.userMagnification = 1.0
        # The direction of the coverage by radius in the Coverage mode.
        self.principalPoint = None
        self.coverageDirection = None
        self.onCoverageClicked = None

    def SetCoverageDirection(self, directionDegrees):
        """Show the direction *directionDegrees* (None for all directions)
        in the Coverage mode."""
        self.coverageDirection = directionDegrees
        self.update()

    def SetData(self, vectors, title, individualRms=None, emptyText=None, coverage=None):
        # A vector that is not a finite number is not drawn and takes no
        # part in the statistics (painting one would raise).
        self.vectors = [vector for vector in vectors if all(math.isfinite(component) for component in vector)]
        self.title = title
        self.individualRms = individualRms
        self.emptyText = emptyText
        self.coverage = coverage
        self.update()

    def SetUserMagnification(self, magnificationFactor):
        self.userMagnification = magnificationFactor
        self.update()

    def PlotGeometry(self):
        """(originX, originY, plotWidth, plotHeight) of the plot in the
        widget, in screen pixels, as paintEvent lays it out, or None when
        the widget is too small for it.  The plot is centred across the
        width of the widget (Geert, 13 October 2026).  It runs from
        PROFILE_MARGIN_TOP below the top of the widget to
        VECTOR_PLOT_MARGIN_BOTTOM above its bottom, so that in the plot row
        it spans the profiles beside it, and it is as wide as the image
        format needs at that height, or as wide as the widget allows
        (VECTOR_PLOT_SIDE_MARGIN) and then less high."""
        width = self.width()
        height = self.height()
        aspectRatio = self.pixelColumnCount / float(max(self.pixelRowCount, 1))
        plotHeight = height - PROFILE_MARGIN_TOP - VECTOR_PLOT_MARGIN_BOTTOM
        plotWidth = int(round(plotHeight * aspectRatio))
        if plotWidth > width - 2 * VECTOR_PLOT_SIDE_MARGIN:
            plotWidth = width - 2 * VECTOR_PLOT_SIDE_MARGIN
            plotHeight = int(plotWidth / aspectRatio)
        if plotWidth < 50 or plotHeight < 50:
            return None
        return (width - plotWidth) // 2, PROFILE_MARGIN_TOP, plotWidth, plotHeight

    def mousePressEvent(self, event):
        """A left click in the Coverage map sets the direction of the
        coverage by radius to the direction of the click from the principal
        point, in whole degrees (onCoverageClicked)."""
        if self.coverage is None or self.onCoverageClicked is None or event.button() != QtCore.Qt.LeftButton:
            return
        position = event.pos()
        directionDegrees = DirectionAtScreenPosition(self.PlotGeometry(), self.pixelColumnCount, self.pixelRowCount,
                                                     self.principalPoint, position.x(), position.y())
        if directionDegrees is not None:
            self.onCoverageClicked(directionDegrees)

    def mouseMoveEvent(self, event):
        """Show the vector nearest to the pointer, or the cell under it in
        the Coverage mode, in a tooltip (VectorReadOutText,
        CoverageCellReadOutText), and hide the tooltip elsewhere."""
        position = event.pos()
        if self.coverage is not None:
            text = CoverageCellReadOutText(self.coverage, self.title, self.PlotGeometry(), self.pixelColumnCount,
                                           self.pixelRowCount, position.x(), position.y())
        else:
            text = VectorReadOutText(self.vectors, self.title, self.PlotGeometry(), self.pixelColumnCount,
                                     self.pixelRowCount, position.x(), position.y())
        if text:
            QtWidgets.QToolTip.showText(event.globalPos(), text, self)
        else:
            QtWidgets.QToolTip.hideText()

    def leaveEvent(self, event):
        QtWidgets.QToolTip.hideText()

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)

        geometry = self.PlotGeometry()
        if geometry is None:
            painter.end()
            return
        originX, margin, plotWidth, plotHeight = geometry

        # Background.
        painter.fillRect(originX, margin, plotWidth, plotHeight,
                         QtGui.QColor(250, 250, 250))
        painter.setPen(QtGui.QPen(QtGui.QColor(180, 180, 180), 1))
        painter.drawRect(originX, margin, plotWidth, plotHeight)

        if self.coverage is not None:
            self._PaintCoverage(painter, originX, margin, plotWidth, plotHeight)
            painter.end()
            return

        if not self.vectors:
            painter.setPen(QtGui.QPen(QtGui.QColor(120, 120, 120), 1))
            # Wrapped within the plot, so that a long note stays readable.
            painter.drawText(QtCore.QRectF(originX + 10, margin + 10, plotWidth - 20, plotHeight - 20),
                             QtCore.Qt.TextWordWrap, self.emptyText or "No data available.")
            painter.end()
            return

        # Statistics.
        magnitudes = [math.sqrt(displacementX*displacementX + displacementY*displacementY)
                      for _, _, displacementX, displacementY in self.vectors]
        rmsMagnitude, longestMagnitude = FieldStatistics(self.vectors)
        sortedMagnitudes = sorted(magnitudes)
        medianMagnitude = sortedMagnitudes[len(sortedMagnitudes) // 2]

        scaleX = plotWidth / self.pixelColumnCount
        scaleY = plotHeight / self.pixelRowCount

        # Automatic scale as in Metashape: the median vector is drawn
        # MEDIAN_VECTOR_CELL_FRACTION of a grid cell long.  When more than
        # half of the vectors are zero, the longest one is drawn one cell
        # long, and a field of rounding noise is not magnified.
        cellScreenWidth = plotWidth / float(DistortionGridShape(self.pixelColumnCount, self.pixelRowCount)[0])
        if medianMagnitude > NEGLIGIBLE_DISPLACEMENT_PIXELS:
            vectorMagnification = MEDIAN_VECTOR_CELL_FRACTION * cellScreenWidth / (medianMagnitude * scaleX)
        elif longestMagnitude > NEGLIGIBLE_DISPLACEMENT_PIXELS:
            vectorMagnification = cellScreenWidth / (longestMagnitude * scaleX)
        else:
            vectorMagnification = 1.0

        # Apply user-controlled magnification.
        vectorMagnification *= self.userMagnification

        # Colours from blue to red at twice the median length, as in
        # Metashape.
        colourReferenceLength = (RED_AT_MEDIAN_MULTIPLE * medianMagnitude
                                 if medianMagnitude > NEGLIGIBLE_DISPLACEMENT_PIXELS else longestMagnitude)

        # Draw the vectors inside the plot area only.
        painter.setClipRect(originX, margin, plotWidth, plotHeight)
        for (pixelX, pixelY, displacementX, displacementY), displacementMagnitude in zip(self.vectors, magnitudes):
            screenX = originX + int(pixelX * scaleX)
            screenY = margin + int(pixelY * scaleY)
            red, green, blue = VectorColourRgb(displacementMagnitude, colourReferenceLength)
            endX = screenX + int(displacementX * scaleX * vectorMagnification)
            endY = screenY + int(displacementY * scaleY * vectorMagnification)
            painter.setPen(QtGui.QPen(
                QtGui.QColor(red, green, blue, 200), 1))
            painter.drawLine(screenX, screenY, endX, endY)
        painter.setClipping(False)

        # Title.
        painter.setPen(QtGui.QPen(QtGui.QColor(0, 0, 0), 1))
        titleFont = QtGui.QFont()
        titleFont.setPointSize(9)
        titleFont.setBold(True)
        painter.setFont(titleFont)
        painter.drawText(originX, margin - 6, self.title)

        # Statistics and reference vector.
        smallFont = QtGui.QFont()
        smallFont.setPointSize(7)
        painter.setFont(smallFont)
        statisticsText = "RMS: {} px, maximum: {} px".format(
            FormatAsMetashape(rmsMagnitude), FormatAsMetashape(longestMagnitude))
        if self.individualRms is not None:
            statisticsText += ", RMS of the individual residuals: {} px".format(
                FormatAsMetashape(self.individualRms))
        painter.drawText(
            originX, margin + plotHeight + 13, statisticsText)
        # The total magnification, the automatic scale times the factor of
        # the slider, at the top right, beside the title.
        painter.drawText(QtCore.QRectF(originX, margin - 20, plotWidth, 16),
                         QtCore.Qt.AlignRight | QtCore.Qt.AlignBottom,
                         "Total magnification " + FormatTotalMagnification(vectorMagnification))

        # Reference vector: a displacement in pixels from the 1, 2, 5
        # sequence, drawn 20 to 50 screen pixels long at any magnification.
        screenPixelsPerImagePixel = scaleX * vectorMagnification
        referenceVectorLength = ChooseReferenceVectorLength(screenPixelsPerImagePixel)
        if referenceVectorLength is not None:
            referenceVectorScreenPixels = referenceVectorLength * screenPixelsPerImagePixel
            barX = originX + plotWidth - int(referenceVectorScreenPixels) - 10
            barY = margin + plotHeight - 12
            painter.setPen(QtGui.QPen(QtGui.QColor(0, 0, 0), 2))
            painter.drawLine(barX, barY, barX + int(referenceVectorScreenPixels), barY)
            # End caps.
            painter.drawLine(barX, barY - 3, barX, barY + 3)
            painter.drawLine(barX + int(referenceVectorScreenPixels), barY - 3,
                             barX + int(referenceVectorScreenPixels), barY + 3)
            painter.drawText(barX, barY - 6,
                             FormatTickLabel(referenceVectorLength, referenceVectorLength) + " px")

        painter.end()

    def _PaintCoverage(self, painter, originX, originY, plotWidth, plotHeight):
        """Draw the Coverage mode: every cell filled by its count of 2D tie
        points (CoverageCellRgb), with the title and the counts, the
        direction of the coverage by radius (PaintCoverageDirection), and
        the legend (PaintCoverageLegend)."""
        counts, columns, rows = self.coverage
        referenceCount = CoverageColourReference(counts)
        for cellIndex, count in enumerate(counts):
            column, row = cellIndex % columns, cellIndex // columns
            left = originX + int(round(column * plotWidth / float(columns)))
            right = originX + int(round((column + 1) * plotWidth / float(columns)))
            top = originY + int(round(row * plotHeight / float(rows)))
            bottom = originY + int(round((row + 1) * plotHeight / float(rows)))
            painter.fillRect(left, top, max(right - left, 1), max(bottom - top, 1),
                             QtGui.QColor(*CoverageCellRgb(count, referenceCount)))
        painter.setPen(QtGui.QPen(QtGui.QColor(180, 180, 180), 1))
        painter.drawRect(originX, originY, plotWidth, plotHeight)
        painter.setPen(QtGui.QPen(QtGui.QColor(0, 0, 0), 1))
        titleFont = QtGui.QFont()
        titleFont.setPointSize(9)
        titleFont.setBold(True)
        painter.setFont(titleFont)
        painter.drawText(originX, originY - 6, self.title)
        smallFont = QtGui.QFont()
        smallFont.setPointSize(7)
        painter.setFont(smallFont)
        painter.drawText(originX, originY + plotHeight + 13, CoverageStatisticsText(self.coverage))
        # The direction of the coverage by radius and the legend of the
        # colours.
        if self.coverageDirection is not None and self.principalPoint is not None:
            PaintCoverageDirection(painter, (originX, originY, plotWidth, plotHeight), self.pixelColumnCount,
                                   self.pixelRowCount, self.principalPoint, self.coverageDirection)
        PaintCoverageLegend(painter, originX, originY + plotHeight + 21, self.coverage)


# Colours (red, green, blue) of the three overlays of the decentring
# profile, from the colour-blind-safe palette of Okabe and Ito:
# vermilion, bluish green, and black, far apart from each other and from
# the purple decentring curves, and the azimuth mean dotted
# (AZIMUTH_MEAN_DOTTED), unlike the dashed curves of p1 and p2.
ANGLE_MAGNITUDE_RGB = (213, 94, 0)
SEMI_DIAGONAL_MEAN_RGB = (0, 158, 115)
AZIMUTH_MEAN_RGB = (0, 0, 0)
AZIMUTH_MEAN_DOTTED = True
AZIMUTH_MEAN_LINE_WIDTH = 2
ANGLE_MAGNITUDE_COLOUR = QtGui.QColor(*ANGLE_MAGNITUDE_RGB)

DecentringProfileAnglesResult = collections.namedtuple("DecentringProfileAnglesResult", ("equal", "threeTimes"))


def DecentringProfileAngles(p1Value, p2Value):
    """The directions, in degrees from the image x axis towards the image
    y axis as the Decentring angle counts them, in which the magnitude of
    the decentring distortion equals its profile function, sqrt(p1^2 +
    p2^2) r^2, and in which it is three times as long: with phi0 =
    atan2(p2, p1), the magnitude is sqrt(p1^2 + p2^2) r^2 sqrt(1 + 8
    cos^2(theta - phi0)), so it equals the profile function at phi0 plus
    and minus 90 degrees and is three times as long at phi0 and phi0 plus
    180 degrees (the factor 1 + p3 r^2 + p4 r^4 changes neither).  Each
    pair runs from 0 to 360 degrees, smaller first.  None when p1 and p2
    are both zero.  Geert found the magnitude on the profile function at
    about 120 degrees in Project5 (10 October 2026): 119.3 degrees for
    chunk 3, 114.5 degrees for chunk 1."""
    if not (p1Value or p2Value):
        return None
    largestDegrees = math.degrees(math.atan2(p2Value, p1Value)) % 180.0
    equalDegrees = (largestDegrees + 90.0) % 180.0
    return DecentringProfileAnglesResult((equalDegrees, equalDegrees + 180.0),
                                         (largestDegrees, largestDegrees + 180.0))


def DecentringAngleToolTip(semiDiagonalDegrees, p1Value, p2Value):
    """Tooltip of the Decentring angle: what the angle sets and, for the
    group, the directions of DecentringProfileAngles."""
    text = ("Angle from the image x axis at which the overlay Magnitude at the angle\n"
            "evaluates the decentring distortion (decentring-only profile view).\n"
            "0\u00b0 = along the x axis,\n"
            "90\u00b0 = along the y axis,\n"
            "{:.1f}\u00b0 = along the image diagonal.".format(semiDiagonalDegrees))
    angles = DecentringProfileAngles(p1Value, p2Value)
    if angles is not None:
        text += ("\nFor this group the magnitude equals the profile function at {:.1f}\u00b0 and {:.1f}\u00b0\n"
                 "and is three times as long at {:.1f}\u00b0 and {:.1f}\u00b0.").format(
                     angles.equal[0], angles.equal[1], angles.threeTimes[0], angles.threeTimes[1])
    return text


def _DecentringMagnitudeAt(principalDistancePixels, higherOrderFactor, p1Value, p2Value, radiusSquared, normalisedRadius, angleDegrees):
    angleRadians = math.radians(angleDegrees)
    normalisedX = normalisedRadius * math.cos(angleRadians)
    normalisedY = normalisedRadius * math.sin(angleRadians)
    normalisedDecentringDx = higherOrderFactor * (
        p1Value * (radiusSquared + 2*normalisedX**2) + 2*p2Value*normalisedX*normalisedY)
    normalisedDecentringDy = higherOrderFactor * (
        2*p1Value*normalisedX*normalisedY + p2Value*(radiusSquared + 2*normalisedY**2))
    return principalDistancePixels * math.sqrt(normalisedDecentringDx**2 + normalisedDecentringDy**2)


# The two kinds of profile plot, for the read-out (ProfileReadOutText).
PROFILE_PLOT_RADIAL = "radial"
PROFILE_PLOT_DECENTRING = "decentring"


def ProfilePlotAreaAt(plotAreas, screenX, screenY):
    """The plot area (kind, originX, originY, plotWidth, plotHeight,
    roundedMaximumRadius) of ProfilePlotWidget.plotAreas that holds the
    screen position (screenX, screenY), or None."""
    for area in plotAreas:
        kind, originX, originY, plotWidth, plotHeight, roundedMaximumRadius = area
        if originX <= screenX <= originX + plotWidth and originY <= screenY <= originY + plotHeight:
            return area
    return None


def ProfileValueAt(radii, values, radius):
    """A profile curve (*values* at the evenly spaced *radii* of
    ProfilePlotWidget) at *radius*, interpolated linearly, or None outside
    the radii or without values."""
    if not radii or len(values) != len(radii) or not (radii[0] <= radius <= radii[-1]):
        return None
    if len(radii) == 1 or radii[-1] <= radii[0]:
        return values[0]
    position = (radius - radii[0]) / (radii[-1] - radii[0]) * (len(radii) - 1)
    lowerIndex = min(int(math.floor(position)), len(radii) - 2)
    fraction = position - lowerIndex
    return values[lowerIndex] + fraction * (values[lowerIndex + 1] - values[lowerIndex])


def ProfileReadOutText(profileWidget, kind, radius):
    """The text of the tooltip of a profile plot (*kind*
    PROFILE_PLOT_RADIAL or PROFILE_PLOT_DECENTRING) at *radius*, in pixels
    from the principal point: the radius and the value of every curve that
    the plot draws, named as in its legend, in px with four significant
    digits."""
    radii = profileWidget.radii
    lines = ["Radius {:,.0f} px".format(radius)]
    if not radii or radius > radii[-1] * (1.0 + 1e-9):
        lines.append("Beyond the largest radius of the profiles, {:,.0f} px".format(radii[-1] if radii else 0.0))
        return "\n".join(lines)
    if kind == PROFILE_PLOT_RADIAL:
        curves = [("Total radial", profileWidget.totalRadial)] + [
            (name, curve) for name, isDrawn, curve in (
                ("k1", profileWidget.hasK1, profileWidget.k1Curve), ("k2", profileWidget.hasK2, profileWidget.k2Curve),
                ("k3", profileWidget.hasK3, profileWidget.k3Curve), ("k4", profileWidget.hasK4, profileWidget.k4Curve))
            if isDrawn]
    else:
        curves = [("Total decentring", profileWidget.totalDecentring)] + [
            (name, curve) for name, isDrawn, curve in (
                ("p1", profileWidget.hasP1, profileWidget.p1Curve), ("p2", profileWidget.hasP2, profileWidget.p2Curve))
            if isDrawn]
        if profileWidget.mode == ProfilePlotWidget.MODE_DECENTRING:
            curves += [(name, curve) for name, isDrawn, curve in (
                ("Magnitude at {:g}\u00b0".format(profileWidget.decentringAngleDegrees),
                 profileWidget.showAngleMagnitude, profileWidget.angleMagnitudeDecentring),
                ("Semi-diagonal mean", profileWidget.showSemiDiagonalMean, profileWidget.semiDiagonalMeanDecentring),
                ("Azimuth mean", profileWidget.showAzimuthMean, profileWidget.azimuthMeanDecentring)) if isDrawn]
    for name, curve in curves:
        lines.append("{}: {} px".format(name, FormatProfileTableValue(ProfileValueAt(radii, curve, radius), 4)))
    return "\n".join(lines)


class ProfilePlotWidget(QtWidgets.QWidget):
    """Displays the radial and the decentring distortion against the
    radius from the principal point, together or separately, as
    Metashape's Profile tab does: the radial displacement with the sign
    that makes a barrel distortion (towards the principal point)
    positive, and the profile function of the decentring distortion,
    sqrt(p1^2 + p2^2) r^2 / c_pix (1 + p3 (r / c_pix)^2 + p4 (r /
    c_pix)^4) in pixels, with the radial distance r in pixels, with the
    contributions of the individual coefficients and, in the
    decentring-only view, the optional magnitude at the decentring angle
    and the semi-diagonal and azimuth means.  Without a positive
    principal distance there is no profile and nothing is drawn.

    With the option Balanced (SetRadialConvention), the radial profile is
    the balanced radial distortion, zero at the radius r0, and so is the
    contribution of each coefficient, so that they still add up to the
    total, and the plot names r0 and the principal distance in pixels of
    the balanced form (BalancedPrincipalDistancePixels)."""

    MODE_RADIAL = 0
    MODE_DECENTRING = 1
    MODE_BOTH = 2

    def __init__(self, calibration, pixelColumnCount, pixelRowCount,
                 parent=None):
        super().__init__(parent)
        self.setMinimumSize(420, 320)
        # The tooltip follows the pointer (ProfileReadOutText), over the
        # plots that paintEvent records in plotAreas.
        self.setMouseTracking(True)
        self.plotAreas = []
        self.calibration = calibration
        self.pixelColumnCount = pixelColumnCount
        self.pixelRowCount = pixelRowCount
        self.mode = self.MODE_BOTH
        self.decentringAngleDegrees = 0.0  # Default: along the x axis.
        self.showAngleMagnitude = False
        self.showSemiDiagonalMean = False
        self.showAzimuthMean = False
        self.radialConvention = RADIAL_CONVENTION_GAUSSIAN
        self.zeroCrossingRadiusPixels = None
        self.balancedPrincipalDistancePixels = None
        self._ComputeProfiles()

    def SetRadialConvention(self, convention, zeroCrossingRadiusPixels):
        """Draw the radial distortion as estimated (Gaussian, Metashape's)
        or balanced, with its zero crossing at *zeroCrossingRadiusPixels*."""
        isBalanced = convention == RADIAL_CONVENTION_BALANCED and zeroCrossingRadiusPixels is not None
        self.radialConvention = RADIAL_CONVENTION_BALANCED if isBalanced else RADIAL_CONVENTION_GAUSSIAN
        self.zeroCrossingRadiusPixels = zeroCrossingRadiusPixels if isBalanced else None
        self._ComputeProfiles()
        self.update()

    def SetMode(self, mode):
        self.mode = mode
        self.update()

    def SetDecentringAngle(self, angleDegrees):
        self.decentringAngleDegrees = angleDegrees
        self._ComputeProfiles()
        self.update()

    def SetShowAngleMagnitude(self, enabled):
        self.showAngleMagnitude = bool(enabled)
        self.update()

    def SetShowSemiDiagonalMean(self, enabled):
        self.showSemiDiagonalMean = bool(enabled)
        self.update()

    def SetShowAzimuthMean(self, enabled):
        self.showAzimuthMean = bool(enabled)
        self.update()

    def _ComputeProfiles(self):
        calibration = self.calibration
        principalDistancePixels = calibration.f
        coefficientK1 = getattr(calibration, 'k1', 0.0) or 0.0
        coefficientK2 = getattr(calibration, 'k2', 0.0) or 0.0
        coefficientK3 = getattr(calibration, 'k3', 0.0) or 0.0
        coefficientK4 = getattr(calibration, 'k4', 0.0) or 0.0
        p1Value = getattr(calibration, 'p1', 0.0) or 0.0
        p2Value = getattr(calibration, 'p2', 0.0) or 0.0
        p3Value = getattr(calibration, 'p3', 0.0) or 0.0
        p4Value = getattr(calibration, 'p4', 0.0) or 0.0

        maximumRadiusPixels = math.sqrt(
            (self.pixelColumnCount*0.5)**2 + (self.pixelRowCount*0.5)**2)
        semiDiagonalAngles = SemiDiagonalAnglesDegrees(self.pixelColumnCount, self.pixelRowCount)

        sampleCount = 200
        self.radii = []
        self.totalRadial = []
        self.k1Curve = []; self.k2Curve = []
        self.k3Curve = []; self.k4Curve = []
        self.totalDecentring = []
        self.p1Curve = []; self.p2Curve = []
        self.angleMagnitudeDecentring = []
        self.semiDiagonalMeanDecentring = []
        self.azimuthMeanDecentring = []

        self.hasK1 = abs(coefficientK1) > 1e-15
        self.hasK2 = abs(coefficientK2) > 1e-15
        self.hasK3 = abs(coefficientK3) > 1e-15
        self.hasK4 = abs(coefficientK4) > 1e-15
        self.hasP1 = abs(p1Value) > 1e-15
        self.hasP2 = abs(p2Value) > 1e-15

        # Without a positive principal distance (an RPC group, for
        # example) there is no profile, and paintEvent draws nothing.
        self.balancedPrincipalDistancePixels = None
        if not (principalDistancePixels and math.isfinite(principalDistancePixels) and principalDistancePixels > 0):
            return

        # The balanced radial distortion subtracts the value of each radial
        # term at the zero crossing r0 (zero for the Gaussian one).
        zeroTermK1 = zeroTermK2 = zeroTermK3 = zeroTermK4 = 0.0
        if self.radialConvention == RADIAL_CONVENTION_BALANCED and self.zeroCrossingRadiusPixels is not None:
            zeroRadiusSquared = (self.zeroCrossingRadiusPixels / principalDistancePixels) ** 2
            zeroTermK1 = coefficientK1 * zeroRadiusSquared
            zeroTermK2 = coefficientK2 * zeroRadiusSquared ** 2
            zeroTermK3 = coefficientK3 * zeroRadiusSquared ** 3
            zeroTermK4 = coefficientK4 * zeroRadiusSquared ** 4
            self.balancedPrincipalDistancePixels = BalancedPrincipalDistancePixels(
                calibration, self.zeroCrossingRadiusPixels)

        for index in range(sampleCount + 1):
            radiusPixels = index / sampleCount * maximumRadiusPixels
            normalisedRadius = radiusPixels / principalDistancePixels
            radiusSquared = normalisedRadius**2; radiusFourthPower = radiusSquared**2; radiusSixthPower = radiusSquared*radiusFourthPower; radiusEighthPower = radiusFourthPower**2

            # Radial: the displacement r * totalRadialTerm with the sign of
            # Metashape's Profile tab, positive for a barrel distortion
            # (towards the principal point), so that a negative k2 that
            # partly cancels k1 still shows.
            radialTermK1 = coefficientK1*radiusSquared - zeroTermK1; radialTermK2 = coefficientK2*radiusFourthPower - zeroTermK2; radialTermK3 = coefficientK3*radiusSixthPower - zeroTermK3; radialTermK4 = coefficientK4*radiusEighthPower - zeroTermK4
            totalRadialTerm = radialTermK1 + radialTermK2 + radialTermK3 + radialTermK4

            self.radii.append(radiusPixels)
            self.totalRadial.append(-radiusPixels * totalRadialTerm)
            self.k1Curve.append(-radiusPixels * radialTermK1)
            self.k2Curve.append(-radiusPixels * radialTermK2)
            self.k3Curve.append(-radiusPixels * radialTermK3)
            self.k4Curve.append(-radiusPixels * radialTermK4)

            # Decentring: the profile function of Metashape's Profile tab,
            # sqrt(p1^2 + p2^2) r^2 with the higher-order factor 1 + p3
            # r^2 + p4 r^4, in pixels, which is the smallest magnitude over
            # all azimuths (the largest is three times as long), and the
            # profile functions of p1 and p2 alone.
            higherOrderFactor = 1.0 + p3Value*radiusSquared + p4Value*radiusFourthPower
            profileScale = principalDistancePixels * radiusSquared * abs(higherOrderFactor)
            self.totalDecentring.append(profileScale * math.hypot(p1Value, p2Value))
            self.p1Curve.append(profileScale * abs(p1Value))
            self.p2Curve.append(profileScale * abs(p2Value))

            decentringMagnitudeAt = functools.partial(_DecentringMagnitudeAt, principalDistancePixels, higherOrderFactor, p1Value, p2Value, radiusSquared, normalisedRadius)

            self.angleMagnitudeDecentring.append(decentringMagnitudeAt(self.decentringAngleDegrees))
            self.semiDiagonalMeanDecentring.append(
                sum(decentringMagnitudeAt(semiDiagonalAngle) for semiDiagonalAngle in semiDiagonalAngles)
                / len(semiDiagonalAngles))
            self.azimuthMeanDecentring.append(sum(
                decentringMagnitudeAt(sampleAngle)
                for sampleAngle in range(0, 360, 3)) / 120.0)

    def _DrawCurve(self, painter, valuesX, valuesY, maximumX, minimumY, maximumY,
                   originX, originY, plotWidth, plotHeight, colour, lineWidth=1, dashed=False, dotted=False):
        yRange = maximumY - minimumY
        if maximumX < 1e-12 or yRange < 1e-12:
            return
        style = (QtCore.Qt.DashLine if dashed else QtCore.Qt.DotLine if dotted else QtCore.Qt.SolidLine)
        pen = QtGui.QPen(colour, lineWidth, style)
        if dashed:
            pen.setDashPattern([6, 4])
        painter.setPen(pen)
        painter.setBrush(QtCore.Qt.NoBrush)
        painter.save()
        painter.setClipRect(originX, originY, plotWidth, plotHeight)
        path = QtGui.QPainterPath()
        isFirstPoint = True
        for valueX, valueY in zip(valuesX, valuesY):
            screenX = originX + valueX / maximumX * plotWidth
            screenY = originY + plotHeight - (valueY - minimumY) / yRange * plotHeight
            if isFirstPoint:
                path.moveTo(screenX, screenY)
                isFirstPoint = False
            else:
                path.lineTo(screenX, screenY)
        painter.drawPath(path)
        painter.restore()

    @staticmethod
    def _RoundUpToMultiple(value, interval):
        """Round value up to the next multiple of interval."""
        if interval <= 0:
            return value
        return math.ceil(value / interval) * interval

    @staticmethod
    def _RoundDownToMultiple(value, interval):
        """Round value down to the previous multiple of interval."""
        if interval <= 0:
            return value
        return math.floor(value / interval) * interval

    def _DrawFrame(self, painter, originX, originY, plotWidth, plotHeight,
                   maximumX, minimumY, maximumY, title,
                   xGridInterval=500.0, yGridInterval=5.0):
        """Draw the plot frame with grid, ticks, and labels: radius ticks
        every xGridInterval and distortion ticks every yGridInterval,
        labelled with the decimals that the interval needs.

        Supports a signed distortion range (minimumY can be negative).
        """
        # Round X axis to clean interval.
        roundedMaximumX = self._RoundUpToMultiple(maximumX, xGridInterval)
        if roundedMaximumX < maximumX:
            roundedMaximumX = maximumX

        # Round Y range to clean interval.
        roundedMinimumY = self._RoundDownToMultiple(minimumY, yGridInterval)
        roundedMaximumY = self._RoundUpToMultiple(maximumY, yGridInterval)
        yRange = roundedMaximumY - roundedMinimumY
        if yRange < yGridInterval:
            yRange = yGridInterval
            roundedMaximumY = roundedMinimumY + yRange

        # Background and border.
        painter.fillRect(originX, originY, plotWidth, plotHeight,
                         QtGui.QColor(255, 255, 255))
        painter.setPen(QtGui.QPen(QtGui.QColor(0, 0, 0), 1))
        painter.drawRect(originX, originY, plotWidth, plotHeight)

        # Vertical grid lines (X axis).
        painter.setPen(QtGui.QPen(
            QtGui.QColor(220, 220, 220), 1, QtCore.Qt.DotLine))
        xTick = xGridInterval
        while xTick < roundedMaximumX * 0.999:
            gridX = originX + int(xTick / roundedMaximumX * plotWidth)
            painter.drawLine(gridX, originY, gridX, originY + plotHeight)
            xTick += xGridInterval

        # Horizontal grid lines (Y axis), one per tick between the frame
        # edges. The ticks are counted, not accumulated, so they do not drift.
        yTickCount = max(1, int(round(yRange / yGridInterval)))
        for yTickIndex in range(1, yTickCount):
            fraction = yTickIndex * yGridInterval / yRange
            gridY = originY + plotHeight - int(fraction * plotHeight)
            if gridY <= originY or gridY >= originY + plotHeight:
                continue
            painter.drawLine(originX, gridY, originX + plotWidth, gridY)

        # Zero line (if Y range includes zero).
        if roundedMinimumY < 0 < roundedMaximumY:
            zeroY = originY + plotHeight - int((0 - roundedMinimumY) / yRange * plotHeight)
            painter.setPen(QtGui.QPen(
                QtGui.QColor(150, 150, 150), 1, QtCore.Qt.SolidLine))
            painter.drawLine(originX, zeroY, originX + plotWidth, zeroY)

        # Title.
        painter.setPen(QtGui.QPen(QtGui.QColor(0, 0, 0), 1))
        titleFont = QtGui.QFont(); titleFont.setPointSize(9); titleFont.setBold(True)
        painter.setFont(titleFont)
        painter.drawText(originX + 4, originY - 6, title)

        # X tick labels.
        smallFont = QtGui.QFont(); smallFont.setPointSize(7)
        painter.setFont(smallFont)
        xTick = 0.0
        while xTick <= roundedMaximumX * 1.001:
            tickX = originX + int(xTick / roundedMaximumX * plotWidth)
            painter.drawText(tickX - 12, originY + plotHeight + 12,
                             "{:.0f}".format(xTick))
            xTick += xGridInterval

        # Y tick labels, with the decimals that the tick interval needs.
        for yTickIndex in range(yTickCount + 1):
            yTick = roundedMinimumY + yTickIndex * yGridInterval
            fraction = yTickIndex * yGridInterval / yRange
            tickY = originY + plotHeight - int(fraction * plotHeight)
            label = FormatTickLabel(yTick, yGridInterval)
            painter.drawText(originX - 38, tickY + 4, label)

        # Axis labels.
        painter.setPen(QtGui.QPen(QtGui.QColor(100, 100, 100), 1))
        axisFont = QtGui.QFont(); axisFont.setPointSize(7)
        painter.setFont(axisFont)
        painter.drawText(originX + plotWidth - 60, originY + plotHeight + 22,
                         "Radius (px)")
        painter.save()
        painter.translate(originX - 44, originY + plotHeight // 2)
        painter.rotate(-90)
        painter.drawText(0, 0, "Distortion (px)")
        painter.restore()

        return roundedMaximumX, roundedMinimumY, roundedMaximumY

    def mouseMoveEvent(self, event):
        """Show the values of the curves at the radius under the pointer
        in a tooltip (ProfileReadOutText), and hide it outside the plots."""
        position = event.pos()
        area = ProfilePlotAreaAt(self.plotAreas, position.x(), position.y())
        if area is None:
            QtWidgets.QToolTip.hideText()
            return
        kind, originX, originY, plotWidth, plotHeight, roundedMaximumRadius = area
        radius = (position.x() - originX) / float(plotWidth) * roundedMaximumRadius
        QtWidgets.QToolTip.showText(event.globalPos(), ProfileReadOutText(self, kind, radius), self)

    def leaveEvent(self, event):
        QtWidgets.QToolTip.hideText()

    def paintEvent(self, event):
        self.plotAreas = []
        if not self.radii:
            return
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)

        widgetWidth = self.width(); widgetHeight = self.height()
        # The right margin leaves room for a five-digit radius label
        # ("10000") centred on the last tick.  Below the lowest plot come
        # its tick labels and "Radius (px)" (AXIS_LABELS_HEIGHT) and then
        # the legend (LEGEND_ROW_HEIGHT), so that the legend does not run
        # through the radius labels.
        marginLeft = 54; marginRight = 18; marginTop = PROFILE_MARGIN_TOP; gap = 50
        bottomHeight = PROFILE_AXIS_LABELS_HEIGHT + PROFILE_LEGEND_ROW_HEIGHT
        plotWidth = widgetWidth - marginLeft - marginRight
        if plotWidth < 50:
            painter.end(); return

        maximumRadiusPixels = max(self.radii) if self.radii else 1.0

        totalRadialColour = QtGui.QColor(0, 0, 0)
        # Radial family: warm reds/oranges, perceptually distinct.
        k1Colour = QtGui.QColor(180, 30, 30)       # dark red
        k2Colour = QtGui.QColor(220, 120, 20)      # orange
        k3Colour = QtGui.QColor(160, 80, 0)        # brown
        k4Colour = QtGui.QColor(210, 50, 90)       # crimson
        # Decentring family: cool purples/violets.
        totalDecentringColour = QtGui.QColor(100, 20, 160)     # deep purple
        p1Colour = QtGui.QColor(150, 50, 200)      # violet
        p2Colour = QtGui.QColor(80, 60, 180)       # blue-purple
        # Overlays of the decentring-only view.
        semiDiagonalMeanColour = QtGui.QColor(*SEMI_DIAGONAL_MEAN_RGB)     # bluish green
        azimuthMeanColour = QtGui.QColor(*AZIMUTH_MEAN_RGB)  # black, dotted

        drawRadialProfilePlot = functools.partial(self._DrawRadialProfilePlot, k1Colour, k2Colour, k3Colour, k4Colour, totalRadialColour, maximumRadiusPixels, marginLeft, painter, plotWidth)

        drawDecentringProfilePlot = functools.partial(self._DrawDecentringProfilePlot, azimuthMeanColour, totalDecentringColour, p1Colour, p2Colour, semiDiagonalMeanColour, maximumRadiusPixels, marginLeft, painter, plotWidth)

        if self.mode == self.MODE_BOTH:
            plotHeight = (widgetHeight - marginTop - gap - bottomHeight) // 2
            if plotHeight < 30: painter.end(); return
            drawRadialProfilePlot(marginTop, plotHeight, isBothMode=True)
            drawDecentringProfilePlot(marginTop + plotHeight + gap, plotHeight, isBothMode=True)
        elif self.mode == self.MODE_RADIAL:
            plotHeight = widgetHeight - marginTop - bottomHeight
            if plotHeight < 30: painter.end(); return
            drawRadialProfilePlot(marginTop, plotHeight)
        else:
            plotHeight = widgetHeight - marginTop - bottomHeight
            if plotHeight < 30: painter.end(); return
            drawDecentringProfilePlot(marginTop, plotHeight)

        # Legend, in its own row below the radius labels: the line of an
        # entry at legendY, its label on a baseline 4 screen pixels lower.
        legendY = widgetHeight - PROFILE_LEGEND_ROW_HEIGHT + 6
        font = QtGui.QFont(); font.setPointSize(7)
        painter.setFont(font)
        # (label, colour, line style: "solid", "dashed", or "dotted")
        legendItems = []
        if self.mode != self.MODE_DECENTRING:
            legendItems.append(("Total radial", totalRadialColour, "solid"))
            if self.hasK1: legendItems.append(("k1", k1Colour, "dashed"))
            if self.hasK2: legendItems.append(("k2", k2Colour, "dashed"))
            if self.hasK3: legendItems.append(("k3", k3Colour, "dashed"))
            if self.hasK4: legendItems.append(("k4", k4Colour, "dashed"))
        if self.mode != self.MODE_RADIAL:
            legendItems.append(("Total decentring", totalDecentringColour, "solid"))
            if self.hasP1: legendItems.append(("p1", p1Colour, "dashed"))
            if self.hasP2: legendItems.append(("p2", p2Colour, "dashed"))
            if self.mode == self.MODE_DECENTRING:
                if self.showAngleMagnitude:
                    legendItems.append(("Magnitude at {:g}\u00b0".format(self.decentringAngleDegrees),
                                        ANGLE_MAGNITUDE_COLOUR, "solid"))
                if self.showSemiDiagonalMean:
                    legendItems.append(("Semi-diagonal mean",
                                  semiDiagonalMeanColour, "solid"))
                if self.showAzimuthMean:
                    legendItems.append(("Azimuth mean", azimuthMeanColour,
                                  "dotted" if AZIMUTH_MEAN_DOTTED else "solid"))
        legendX = marginLeft
        for label, colour, lineStyleName in legendItems:
            lineStyle = {"dashed": QtCore.Qt.DashLine, "dotted": QtCore.Qt.DotLine}.get(
                lineStyleName, QtCore.Qt.SolidLine)
            painter.setPen(QtGui.QPen(colour, 2, lineStyle))
            painter.drawLine(legendX, legendY, legendX + 14, legendY)
            painter.setPen(QtGui.QPen(QtGui.QColor(0, 0, 0), 1))
            painter.drawText(legendX + 18, legendY + 4, label)
            legendX += 18 + painter.fontMetrics().horizontalAdvance(
                label) + 8
        painter.end()

    def _DrawDecentringProfilePlot(self, azimuthMeanColour, totalDecentringColour, p1Colour, p2Colour, semiDiagonalMeanColour, maximumRadiusPixels, marginLeft, painter, plotWidth, originY, plotHeight, isBothMode=False):
        showAveragesHere = (not isBothMode
                            and self.mode == self.MODE_DECENTRING)
        yCandidates = list(self.totalDecentring)
        if showAveragesHere and self.showAngleMagnitude:
            yCandidates += self.angleMagnitudeDecentring
        if showAveragesHere and self.showSemiDiagonalMean:
            yCandidates += self.semiDiagonalMeanDecentring
        if showAveragesHere and self.showAzimuthMean:
            yCandidates += self.azimuthMeanDecentring
        maximumY = max(yCandidates) if yCandidates else 1.0
        if maximumY < 0.001: maximumY = 1.0
        # Fewer ticks in Both mode (less space). The radius ticks follow the
        # pixel count (a thermal image of 320 pixel columns as well as 150 megapixels).
        distortionTickInterval = ChooseTickInterval(0.0, maximumY, 4 if isBothMode else 6)
        radiusTickInterval = ChooseTickInterval(0.0, maximumRadiusPixels, 6)
        roundedMaximumRadiusPixels, roundedMinimumY, roundedMaximumY = self._DrawFrame(
            painter, marginLeft, originY, plotWidth, plotHeight, maximumRadiusPixels, 0.0, maximumY,
            "Decentring distortion",
            xGridInterval=radiusTickInterval, yGridInterval=distortionTickInterval)
        self.plotAreas.append((PROFILE_PLOT_DECENTRING, marginLeft, originY, plotWidth, plotHeight,
                               roundedMaximumRadiusPixels))
        if self.hasP1:
            self._DrawCurve(painter, self.radii, self.p1Curve,
                roundedMaximumRadiusPixels, roundedMinimumY, roundedMaximumY, marginLeft, originY, plotWidth, plotHeight, p1Colour, 1, True)
        if self.hasP2:
            self._DrawCurve(painter, self.radii, self.p2Curve,
                roundedMaximumRadiusPixels, roundedMinimumY, roundedMaximumY, marginLeft, originY, plotWidth, plotHeight, p2Colour, 1, True)
        self._DrawCurve(painter, self.radii, self.totalDecentring,
            roundedMaximumRadiusPixels, roundedMinimumY, roundedMaximumY, marginLeft, originY, plotWidth, plotHeight, totalDecentringColour, 2)
        if showAveragesHere and self.showAngleMagnitude:
            self._DrawCurve(painter, self.radii,
                self.angleMagnitudeDecentring,
                roundedMaximumRadiusPixels, roundedMinimumY, roundedMaximumY, marginLeft, originY, plotWidth, plotHeight,
                ANGLE_MAGNITUDE_COLOUR, 1)
        if showAveragesHere and self.showSemiDiagonalMean:
            self._DrawCurve(painter, self.radii,
                self.semiDiagonalMeanDecentring,
                roundedMaximumRadiusPixels, roundedMinimumY, roundedMaximumY, marginLeft, originY, plotWidth, plotHeight,
                semiDiagonalMeanColour, 1)
        if showAveragesHere and self.showAzimuthMean:
            self._DrawCurve(painter, self.radii,
                self.azimuthMeanDecentring,
                roundedMaximumRadiusPixels, roundedMinimumY, roundedMaximumY, marginLeft, originY, plotWidth, plotHeight,
                azimuthMeanColour, AZIMUTH_MEAN_LINE_WIDTH, dotted=AZIMUTH_MEAN_DOTTED)

    def _DrawRadialProfilePlot(self, k1Colour, k2Colour, k3Colour, k4Colour, totalRadialColour, maximumRadiusPixels, marginLeft, painter, plotWidth, originY, plotHeight, isBothMode=False):
        allValues = (self.totalRadial + self.k1Curve
                     + self.k2Curve + self.k3Curve + self.k4Curve)
        minimumY = min(allValues) if allValues else -1.0
        maximumY = max(allValues) if allValues else 1.0
        if maximumY - minimumY < 0.01:
            maximumY = minimumY + 1.0
        # Fewer ticks in Both mode (less space). The radius ticks follow the
        # pixel count.
        distortionTickInterval = ChooseTickInterval(minimumY, maximumY, 4 if isBothMode else 6)
        radiusTickInterval = ChooseTickInterval(0.0, maximumRadiusPixels, 6)
        roundedMaximumRadiusPixels, roundedMinimumY, roundedMaximumY = self._DrawFrame(
            painter, marginLeft, originY, plotWidth, plotHeight, maximumRadiusPixels, minimumY, maximumY,
            RadialProfileTitle(self.radialConvention, self.zeroCrossingRadiusPixels),
            xGridInterval=radiusTickInterval, yGridInterval=distortionTickInterval)
        self.plotAreas.append((PROFILE_PLOT_RADIAL, marginLeft, originY, plotWidth, plotHeight,
                               roundedMaximumRadiusPixels))
        if self.radialConvention == RADIAL_CONVENTION_BALANCED and self.zeroCrossingRadiusPixels is not None:
            # The zero crossing r0 as a dashed vertical line, and the
            # principal distance in pixels of the balanced form.
            zeroCrossingX = marginLeft + int(self.zeroCrossingRadiusPixels / roundedMaximumRadiusPixels * plotWidth)
            painter.setPen(QtGui.QPen(QtGui.QColor(120, 120, 120), 1, QtCore.Qt.DashLine))
            painter.drawLine(zeroCrossingX, originY, zeroCrossingX, originY + plotHeight)
            painter.setPen(QtGui.QPen(QtGui.QColor(0, 0, 0), 1))
            painter.drawText(QtCore.QRectF(marginLeft, originY - 20, plotWidth, 16),
                             QtCore.Qt.AlignRight | QtCore.Qt.AlignBottom,
                             BalancedPrincipalDistanceText(self.balancedPrincipalDistancePixels))
        if self.hasK1:
            self._DrawCurve(painter, self.radii, self.k1Curve,
                roundedMaximumRadiusPixels, roundedMinimumY, roundedMaximumY, marginLeft, originY, plotWidth, plotHeight, k1Colour, 1, True)
        if self.hasK2:
            self._DrawCurve(painter, self.radii, self.k2Curve,
                roundedMaximumRadiusPixels, roundedMinimumY, roundedMaximumY, marginLeft, originY, plotWidth, plotHeight, k2Colour, 1, True)
        if self.hasK3:
            self._DrawCurve(painter, self.radii, self.k3Curve,
                roundedMaximumRadiusPixels, roundedMinimumY, roundedMaximumY, marginLeft, originY, plotWidth, plotHeight, k3Colour, 1, True)
        if self.hasK4:
            self._DrawCurve(painter, self.radii, self.k4Curve,
                roundedMaximumRadiusPixels, roundedMinimumY, roundedMaximumY, marginLeft, originY, plotWidth, plotHeight, k4Colour, 1, True)
        self._DrawCurve(painter, self.radii, self.totalRadial,
            roundedMaximumRadiusPixels, roundedMinimumY, roundedMaximumY, marginLeft, originY, plotWidth, plotHeight, totalRadialColour, 2)


# The coverage by radius (Geert, 13 October 2026: "Your IO viewer options
# are cool"), as two plots beside the table of the vector fields: their
# titles, which name the direction, the texts before and while the
# residuals are collected, the key of the red rings, and the colours of the
# bars, of the rings without 2D tie points, and of the radial residual.
COVERAGE_DENSITY_TITLE = "Coverage by radius{}: relative density of the 2D tie points"
COVERAGE_RESIDUAL_TITLE = "Radial residual by radius{} (px)"
COVERAGE_NOT_COLLECTED_TEXT = "Collected with the residuals when the tab is first shown."
COVERAGE_COLLECTING_TEXT = "Collecting the residuals of the group…"
COVERAGE_FAILED_TEXT = "The residuals of the group could not be collected (details in the Console)."
COVERAGE_EMPTY_RING_KEY_TEXT = "ring in the image format without 2D tie points"
COVERAGE_EMPTY_RING_SHORT_KEY_TEXT = "no 2D tie points"
COVERAGE_EVEN_SPREAD_TEXT = "1 = even spread"
# Space between a title and the key or the note to its right, and the room
# of the red box of the key, in screen pixels.
COVERAGE_HEADER_GAP = 12
COVERAGE_KEY_BOX_ROOM = 22
COVERAGE_BAR_RGB = (90, 120, 170)
COVERAGE_EMPTY_RING_RGB = (250, 205, 205)
COVERAGE_RESIDUAL_RGB = (0, 0, 0)
# Below the plots of the coverage by radius as high as the table of the
# vector fields, they are no lower than this.
COVERAGE_MINIMUM_HEIGHT = 140


def CoverageRingAt(rings, radius):
    """The CoverageRing of *rings* that holds *radius*, or None."""
    for ring in rings or ():
        if ring.start <= radius < ring.end:
            return ring
    return None


# The words of the statistic of the option Residuals for a ring of the
# coverage by radius (ResidualStatisticWords names cells).
RING_STATISTIC_WORDS = {
    RESIDUAL_STATISTIC_WEIGHTED_MEAN: "mean weighted by the inverse keypoint size",
    RESIDUAL_STATISTIC_MEAN: "mean without weights",
    RESIDUAL_STATISTIC_MEDIAN: "median",
}


def RingStatisticWords(statistic):
    """Words of a statistic of the option Residuals for a ring."""
    return RING_STATISTIC_WORDS.get(statistic, RING_STATISTIC_WORDS[RESIDUAL_STATISTIC_WEIGHTED_MEAN])


# Shorter words of the same statistics, for the note above the plot of the
# radial residual when the longer ones do not fit beside its title.
RING_STATISTIC_SHORT_WORDS = {
    RING_STATISTIC_WORDS[RESIDUAL_STATISTIC_WEIGHTED_MEAN]: "weighted mean",
}


def FittingText(metrics, candidates, availableWidth):
    """The first of *candidates* that *metrics* (QFontMetrics) measure no
    wider than *availableWidth*, or "" when none fits."""
    for candidate in candidates:
        if metrics.horizontalAdvance(candidate) <= availableWidth:
            return candidate
    return ""


def CoverageDensityTitle(directionDegrees):
    """Title of the plot of the relative density, with the direction
    (DirectionTitleWords)."""
    return COVERAGE_DENSITY_TITLE.format(DirectionTitleWords(directionDegrees))


def CoverageResidualTitle(directionDegrees):
    """Title of the plot of the radial residual, with the direction."""
    return COVERAGE_RESIDUAL_TITLE.format(DirectionTitleWords(directionDegrees))


def SamplingWords(samplingStride):
    """", sampled 1 in N" for a group whose 2D tie points are sampled, else
    ""."""
    return ", sampled 1 in {:,}".format(samplingStride) if samplingStride > 1 else ""


def CoverageResidualNote(residualWords, samplingStride=1):
    """The note right-aligned above the plot of the radial residual: the
    fewest 2D tie points of a ring with a radial residual, the statistic
    of the option Residuals, and the sampling of a large group (a title
    with the sampling would be cut off at the right edge of the
    widget)."""
    return "per ring of at least {} 2D tie points, {}{}".format(COVERAGE_MINIMUM_RESIDUAL_COUNT, residualWords,
                                                               SamplingWords(samplingStride))


def CoverageResidualNotes(residualWords, samplingStride=1):
    """The note above the plot of the radial residual, from the longest to
    the shortest form, so that a narrow window shows the longest that fits
    beside the title (FittingText): without the fewest 2D tie points of a
    ring, which the About gives, and then with the short words of the
    statistic.  The sampling stays in every form."""
    shortWords = RING_STATISTIC_SHORT_WORDS.get(residualWords, residualWords)
    notes = [CoverageResidualNote(residualWords, samplingStride), residualWords + SamplingWords(samplingStride)]
    if shortWords != residualWords:
        notes.append(shortWords + SamplingWords(samplingStride))
    return notes


def CoverageReadOutText(rings, radius, residualWords, directionDegrees=None, samplingStride=1):
    """The text of the tooltip of the coverage by radius at *radius*: the
    ring, its 2D tie points and their share of all 2D tie points with an r,
    its share of the part of the image format where r is found, the
    relative density, the radial residual by the statistic named
    *residualWords* (RingStatisticWords), the wedge of *directionDegrees*
    when it is given, and the sampling of a large group.  The page of
    Export HTML gives the same text (coverageReadOutText of
    interiorOrientationReport)."""
    ring = CoverageRingAt(rings, radius)
    firstLines = [] if directionDegrees is None else ["Direction " + DirectionValueText(directionDegrees)]
    if samplingStride > 1:
        firstLines.append("2D tie points sampled 1 in {:,}".format(samplingStride))
    if ring is None:
        return "\n".join(firstLines + ["Radius {:,.0f} px".format(radius),
                                        "No part of the image format and no 2D tie points"])
    lines = firstLines + ["Radius {:,.0f} to {:,.0f} px".format(ring.start, ring.end)]
    # The shares name what they are taken of.
    if ring.pointCount <= 0:
        lines.append("No 2D tie points in this ring")
    else:
        lines.append("{} ({:.2f} % of all with an r)".format(
            FormatCountWithNoun(ring.pointCount, "2D tie point", "2D tie points"), 100.0 * ring.pointShare))
    lines.append("Share of the image format where r is found: {:.2f} %".format(100.0 * ring.areaShare)
                 if ring.areaShare > 0.0 else "No part of the image format in this ring")
    if ring.relativeDensity is not None:
        lines.append("Relative density: {:.2f}".format(ring.relativeDensity))
    if ring.radialResidual is not None:
        lines.append("Radial residual, {}: {} px".format(residualWords, FormatProfileTableValue(ring.radialResidual, 3)))
    elif 0 < ring.pointCount < COVERAGE_MINIMUM_RESIDUAL_COUNT:
        lines.append("Radial residual: fewer than {} 2D tie points".format(COVERAGE_MINIMUM_RESIDUAL_COUNT))
    elif ring.pointCount > 0:
        lines.append("Radial residual: none, because no 2D tie point of the ring has a positive keypoint size")
    return "\n".join(lines)


def _CoverageScreenX(originX, plotWidth, roundedMaximum, radius):
    """Screen x of *radius* on the axis of the coverage by radius."""
    return originX + min(radius, roundedMaximum) / roundedMaximum * plotWidth


def _CoverageResidualY(residualTop, residualHeight, residualLimit, value):
    """Screen y of a radial residual in its plot, 0 in the middle."""
    return residualTop + residualHeight / 2.0 - value / residualLimit * residualHeight / 2.0


class CoverageByRadiusWidget(QtWidgets.QWidget):
    """The coverage by radius, on the radius axis of the profiles, with
    their margins and ticks, as two plots: the relative density of the 2D
    tie points of each ring of r (their share divided by the share of the
    image format in the ring, 1 for an even spread) as bars, with the
    rings in the image format
    but without 2D tie points tinted red, and the radial residual of each
    ring with at least COVERAGE_MINIMUM_RESIDUAL_COUNT 2D tie points.  The
    radius labels lie between the two plots, and the titles name the
    direction of the rings (directionDegrees, None for all directions).

    In the tab the widget stands beside the table of the vector fields and
    is as high as it (linkedTable, sizeHint).  Without rings it shows its
    status text: when the residuals are collected, that they are being
    collected, or that they could not be (SetStatus).  When it is first
    shown, it calls onFirstShown, which collects them."""

    MARGIN_LEFT, MARGIN_RIGHT = 54, 18
    TITLE_HEIGHT, LABEL_ROW_HEIGHT, EXTRA_GAP = 18, 14, 6

    def __init__(self, pixelColumnCount, pixelRowCount, parent=None):
        super().__init__(parent)
        self.setMinimumSize(420, COVERAGE_MINIMUM_HEIGHT)
        self.setMouseTracking(True)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        self.pixelColumnCount, self.pixelRowCount = pixelColumnCount, pixelRowCount
        self.rings = None
        self.residualWords = ""
        self.samplingStride = 1
        self.directionDegrees = None
        self.statusText = COVERAGE_NOT_COLLECTED_TEXT
        self.plotArea = None
        self.linkedTable = None
        self.onFirstShown = None

    def SetRings(self, rings, residualWords, samplingStride=1, directionDegrees=None):
        self.rings, self.residualWords, self.samplingStride = rings, residualWords, samplingStride
        self.directionDegrees = directionDegrees
        self.update()

    def SetStatus(self, statusText):
        """Show *statusText* in place of the rings, at once (repaint, before
        a long collection blocks the window)."""
        self.rings, self.statusText = None, statusText
        self.repaint()

    def Title(self):
        """The title of the plot of the relative density."""
        return CoverageDensityTitle(self.directionDegrees)

    def ResidualTitle(self):
        """The title of the plot of the radial residual."""
        return CoverageResidualTitle(self.directionDegrees)

    def AxisMaximum(self):
        """(rounded maximum radius, tick interval) of the radius axis, as in
        the profiles (ProfilePlotWidget._DrawFrame)."""
        largestRadius = LargestProfileRadiusPixels(self.pixelColumnCount, self.pixelRowCount)
        interval = ChooseTickInterval(0.0, largestRadius, 6)
        return max(math.ceil(largestRadius / interval) * interval, largestRadius), interval

    def sizeHint(self):
        """As high as the table of the vector fields beside it (linkedTable),
        and no lower than COVERAGE_MINIMUM_HEIGHT."""
        height = COVERAGE_MINIMUM_HEIGHT
        if self.linkedTable is not None:
            tableHeight = self.linkedTable.sizeHint().height()
            if isinstance(tableHeight, int):
                height = max(height, tableHeight)
        return QtCore.QSize(420, height)

    def showEvent(self, event):
        """Follow the height of the table, and call onFirstShown the first
        time the widget is shown, after the window has been drawn."""
        self.updateGeometry()
        if self.onFirstShown is not None:
            onFirstShown, self.onFirstShown = self.onFirstShown, None
            QtCore.QTimer.singleShot(0, onFirstShown)

    def PlotLayout(self):
        """(densityTop, densityHeight, residualTop, residualHeight) in the
        widget: the title of each plot above it, and the radius labels
        between the plots, so that the frame of the lower plot ends at the
        bottom of the widget, level with the bottom of the table beside it.
        None when the widget is too small."""
        available = self.height() - 2 * self.TITLE_HEIGHT - self.LABEL_ROW_HEIGHT - self.EXTRA_GAP - 1
        densityHeight = available // 2
        residualHeight = available - densityHeight
        if densityHeight < 30 or residualHeight < 30:
            return None
        densityTop = self.TITLE_HEIGHT
        residualTop = densityTop + densityHeight + self.LABEL_ROW_HEIGHT + self.EXTRA_GAP + self.TITLE_HEIGHT
        return densityTop, densityHeight, residualTop, residualHeight

    def mouseMoveEvent(self, event):
        position = event.pos()
        if self.plotArea is None or not self.rings:
            QtWidgets.QToolTip.hideText()
            return
        originX, originY, plotWidth, plotHeight, roundedMaximum = self.plotArea
        if not (originX <= position.x() <= originX + plotWidth and originY <= position.y() <= originY + plotHeight):
            QtWidgets.QToolTip.hideText()
            return
        radius = (position.x() - originX) / float(plotWidth) * roundedMaximum
        QtWidgets.QToolTip.showText(event.globalPos(), CoverageReadOutText(
            self.rings, radius, self.residualWords, self.directionDegrees, self.samplingStride), self)

    def leaveEvent(self, event):
        QtWidgets.QToolTip.hideText()

    def paintEvent(self, event):
        self.plotArea = None
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        originX = self.MARGIN_LEFT
        plotWidth = self.width() - self.MARGIN_LEFT - self.MARGIN_RIGHT
        plotLayout = self.PlotLayout()
        if plotWidth < 50 or plotLayout is None:
            painter.end()
            return
        densityTop, densityHeight, residualTop, residualHeight = plotLayout
        roundedMaximum, interval = self.AxisMaximum()
        screenX = functools.partial(_CoverageScreenX, originX, plotWidth, roundedMaximum)
        titleFont = QtGui.QFont()
        titleFont.setPointSize(9)
        titleFont.setBold(True)
        smallFont = QtGui.QFont()
        smallFont.setPointSize(7)
        for top, height in ((densityTop, densityHeight), (residualTop, residualHeight)):
            painter.fillRect(originX, top, plotWidth, height, QtGui.QColor(255, 255, 255))
            painter.setPen(QtGui.QPen(QtGui.QColor(220, 220, 220), 1, QtCore.Qt.DotLine))
            tick = interval
            while tick < roundedMaximum * 0.999:
                painter.drawLine(int(screenX(tick)), top, int(screenX(tick)), top + height)
                tick += interval
        painter.setPen(QtGui.QPen(QtGui.QColor(0, 0, 0), 1))
        painter.setFont(titleFont)
        painter.drawText(originX, densityTop - 5, self.Title())
        painter.drawText(originX, residualTop - 5, self.ResidualTitle())
        if self.rings is None:
            for top, height in ((densityTop, densityHeight), (residualTop, residualHeight)):
                painter.drawRect(originX, top, plotWidth, height)
            painter.setFont(smallFont)
            painter.setPen(QtGui.QPen(QtGui.QColor(120, 120, 120), 1))
            painter.drawText(QtCore.QRectF(originX + 8, densityTop + 8, plotWidth - 16, densityHeight - 16),
                             QtCore.Qt.TextWordWrap, self.statusText)
            painter.end()
            return
        visibleRings = [ring for ring in self.rings if ring.start < roundedMaximum]
        self._PaintDensity(painter, smallFont, screenX, visibleRings, originX, plotWidth, densityTop, densityHeight)
        self._PaintResidual(painter, smallFont, screenX, visibleRings, originX, plotWidth, residualTop,
                            residualHeight)
        painter.setPen(QtGui.QPen(QtGui.QColor(0, 0, 0), 1))
        painter.drawRect(originX, densityTop, plotWidth, densityHeight)
        painter.drawRect(originX, residualTop, plotWidth, residualHeight)
        # The key of the red rings and the note on the radial residual beside
        # the titles, in the longest form that fits, so that no text runs
        # into another in a narrow window.
        titleMetrics, smallMetrics = QtGui.QFontMetrics(titleFont), QtGui.QFontMetrics(smallFont)
        keyText = FittingText(smallMetrics, (COVERAGE_EMPTY_RING_KEY_TEXT, COVERAGE_EMPTY_RING_SHORT_KEY_TEXT),
                              plotWidth - titleMetrics.horizontalAdvance(self.Title()) - COVERAGE_HEADER_GAP
                              - COVERAGE_KEY_BOX_ROOM)
        if keyText:
            PaintEmptyRingKey(painter, originX + plotWidth, densityTop - 15, keyText)
        noteText = FittingText(smallMetrics, CoverageResidualNotes(self.residualWords, self.samplingStride),
                               plotWidth - titleMetrics.horizontalAdvance(self.ResidualTitle()) - COVERAGE_HEADER_GAP)
        if noteText:
            painter.setFont(smallFont)
            painter.setPen(QtGui.QPen(QtGui.QColor(90, 90, 90), 1))
            painter.drawText(QtCore.QRectF(originX, residualTop - 16, plotWidth, 12), QtCore.Qt.AlignRight, noteText)
        PaintRadiusLabels(painter, screenX, roundedMaximum, interval, densityTop + densityHeight + 12)
        self.plotArea = (originX, densityTop, plotWidth, residualTop + residualHeight - densityTop, roundedMaximum)
        painter.end()

    def _PaintDensity(self, painter, smallFont, screenX, visibleRings, originX, plotWidth, densityTop, densityHeight):
        """The density bars, with 1 (an even spread) dashed, and the red
        rings."""
        densities = [ring.relativeDensity for ring in visibleRings if ring.relativeDensity is not None]
        densityInterval, densityMaximum = CoverageDensityAxis(densities)
        for ring in visibleRings:
            left, right = screenX(ring.start), screenX(ring.end)
            if ring.areaShare > 0.0 and ring.pointCount <= 0:
                painter.fillRect(QtCore.QRectF(left, densityTop + 1, max(right - left, 1.0), densityHeight - 1),
                                 QtGui.QColor(*COVERAGE_EMPTY_RING_RGB))
            elif ring.relativeDensity is not None:
                barHeight = min(ring.relativeDensity / densityMaximum, 1.0) * densityHeight
                painter.fillRect(QtCore.QRectF(left, densityTop + densityHeight - barHeight,
                                               max(right - left - 1.0, 1.0), barHeight),
                                 QtGui.QColor(*COVERAGE_BAR_RGB))
        evenY = densityTop + densityHeight - 1.0 / densityMaximum * densityHeight
        painter.setPen(QtGui.QPen(QtGui.QColor(60, 60, 60), 1, QtCore.Qt.DashLine))
        painter.drawLine(QtCore.QPointF(originX, evenY), QtCore.QPointF(originX + plotWidth, evenY))
        painter.setFont(smallFont)
        painter.drawText(QtCore.QRectF(originX, evenY - 14, plotWidth - 6, 12), QtCore.Qt.AlignRight,
                         COVERAGE_EVEN_SPREAD_TEXT)
        painter.setPen(QtGui.QPen(QtGui.QColor(0, 0, 0), 1))
        for tickIndex in range(int(round(densityMaximum / densityInterval)) + 1):
            value = tickIndex * densityInterval
            tickY = densityTop + densityHeight - value / densityMaximum * densityHeight
            painter.drawText(originX - 30, int(tickY) + 4, FormatTickLabel(value, densityInterval))

    def _PaintResidual(self, painter, smallFont, screenX, visibleRings, originX, plotWidth, residualTop,
                       residualHeight):
        """The radial residual of the rings, around 0."""
        residuals = [(ring, ring.radialResidual) for ring in visibleRings if ring.radialResidual is not None]
        residualLimit = max([abs(value) for _, value in residuals] + [1e-6])
        residualInterval = ChooseTickInterval(0.0, residualLimit, 2)
        residualLimit = math.ceil(residualLimit / residualInterval) * residualInterval
        residualY = functools.partial(_CoverageResidualY, residualTop, residualHeight, residualLimit)
        painter.setPen(QtGui.QPen(QtGui.QColor(150, 150, 150), 1))
        painter.drawLine(QtCore.QPointF(originX, residualY(0.0)), QtCore.QPointF(originX + plotWidth, residualY(0.0)))
        painter.setPen(QtGui.QPen(QtGui.QColor(*COVERAGE_RESIDUAL_RGB), 1.5))
        previousPoint, previousEnd = None, None
        for ring, value in residuals:
            point = QtCore.QPointF(screenX(0.5 * (ring.start + ring.end)), residualY(value))
            if previousPoint is not None and abs(ring.start - previousEnd) < 1e-6:
                painter.drawLine(previousPoint, point)
            painter.drawEllipse(point, 1.8, 1.8)
            previousPoint, previousEnd = point, ring.end
        painter.setFont(smallFont)
        painter.setPen(QtGui.QPen(QtGui.QColor(0, 0, 0), 1))
        for value in (-residualLimit, 0.0, residualLimit):
            painter.drawText(originX - 34, min(int(residualY(value)) + 4, self.height() - 2),
                             FormatTickLabel(value, residualInterval))


def PaintRadiusLabels(painter, screenX, roundedMaximum, interval, baselineY):
    """The radius ticks of the coverage by radius between its two plots,
    and "Radius (px)" between the last two of them when it fits there."""
    font = QtGui.QFont()
    font.setPointSize(7)
    painter.setFont(font)
    metrics = QtGui.QFontMetrics(font)
    painter.setPen(QtGui.QPen(QtGui.QColor(0, 0, 0), 1))
    tickPositions = []
    tick = 0.0
    while tick <= roundedMaximum * 1.001:
        label = "{:.0f}".format(tick)
        labelWidth = metrics.horizontalAdvance(label)
        painter.drawText(int(screenX(tick) - labelWidth / 2.0), baselineY, label)
        tickPositions.append((screenX(tick), labelWidth))
        tick += interval
    if len(tickPositions) >= 2:
        (lastButOneX, lastButOneWidth), (lastX, lastWidth) = tickPositions[-2], tickPositions[-1]
        text = "Radius (px)"
        textWidth = metrics.horizontalAdvance(text)
        freeLeft, freeRight = lastButOneX + lastButOneWidth / 2.0 + 8, lastX - lastWidth / 2.0 - 8
        if freeRight - freeLeft >= textWidth:
            painter.setPen(QtGui.QPen(QtGui.QColor(100, 100, 100), 1))
            painter.drawText(int(0.5 * (freeLeft + freeRight) - textWidth / 2.0), baselineY, text)


def PaintEmptyRingKey(painter, right, top, keyText):
    """The key of the red rings, *keyText* after a red box, right-aligned
    above the plot of the relative density."""
    font = QtGui.QFont()
    font.setPointSize(7)
    painter.setFont(font)
    textWidth = QtGui.QFontMetrics(font).horizontalAdvance(keyText)
    painter.fillRect(right - textWidth - COVERAGE_KEY_BOX_ROOM, top + 1, 16, 10, QtGui.QColor(*COVERAGE_EMPTY_RING_RGB))
    painter.setPen(QtGui.QPen(QtGui.QColor(150, 150, 150), 1))
    painter.drawRect(right - textWidth - COVERAGE_KEY_BOX_ROOM, top + 1, 16, 10)
    painter.setPen(QtGui.QPen(QtGui.QColor(90, 90, 90), 1))
    painter.drawText(right - textWidth, top + 10, keyText)


def RadialProfileTitle(radialConvention, zeroCrossingRadiusPixels):
    """Title of the radial profile: "Radial distortion", or with the
    option Balanced its zero crossing r0 in pixels."""
    if radialConvention == RADIAL_CONVENTION_BALANCED and zeroCrossingRadiusPixels is not None:
        return "Radial distortion, balanced (zero at r0 = {:,.0f} px)".format(zeroCrossingRadiusPixels)
    return "Radial distortion"


def BalancedPrincipalDistanceText(balancedPrincipalDistancePixels):
    """Text of the principal distance in pixels of the balanced form in
    the radial profile."""
    if balancedPrincipalDistancePixels is None:
        return "balanced c_pix: n/a"
    return "balanced c_pix = {:.1f}".format(balancedPrincipalDistancePixels)


# Names of the vector field modes and of the profile modes, as their radio
# buttons give them, for the Console lines of the view controls.
VECTOR_MODE_NAMES = {0: "Total", 1: "Radial", 2: "Decentring", 3: "Corrections", 4: "Difference", 5: "Residuals",
                     6: "Coverage"}
PROFILE_MODE_NAMES = {2: "Both", 0: "Radial", 1: "Decentring"}


def ViewerConsoleLine(groupName, text):
    """A change of a view control of the tab of the interior orientation
    group *groupName* as a line of the Console."""
    return "Interior Orientation Viewer, {}: {}.".format(groupName, text)


def InteriorOrientationGroupName(sensor, chunk=None):
    """Name of an interior orientation group in the tab labels and the
    Console: its label, or "Interior orientation group <key>", and its
    label with " (key N)" when another group of *chunk* has the same label
    (GroupLabelWithKey)."""
    if not sensor.label:
        return "Interior orientation group {}".format(getattr(sensor, 'key', ''))
    try:
        sensors = list(getattr(chunk, "sensors", None) or ()) if chunk is not None else []
    except Exception as suppressedError:
        ReportSuppressedError("interiorOrientationViewer.InteriorOrientationGroupName", suppressedError)
        sensors = []
    return GroupLabelWithKey(sensor, SharedGroupLabels(sensors))


def _OnVectorModeChanged(groupName, vectorDataByMode, sharedPlotModes, vectorWidget, buttonId):
    vectorWidget.SetData(*vectorDataByMode[buttonId])
    sharedPlotModes["vector"] = buttonId
    print(ViewerConsoleLine(groupName, "vector field " + VECTOR_MODE_NAMES.get(buttonId, str(buttonId))))


def _OnMagnificationSliderChanged(groupName, magnificationSlider, sharedPlotModes, sliderValueLabel, vectorWidget,
                                  sliderValue, afterChange=None):
    """Apply the slider position.  A step with the keyboard or a click on
    the groove is echoed at once, a drag only when it is released
    (_OnMagnificationSliderReleased), and the synchronisation of another
    tab to the shared value not at all.  *afterChange* updates the total
    magnification and the table (RefreshDerivedViews)."""
    isUserChange = sliderValue != sharedPlotModes.get("vectorMagnificationSliderValue", MAGNIFICATION_SLIDER_DEFAULT)
    magnificationFactor = MagnificationForSliderPosition(sliderValue)
    vectorWidget.SetUserMagnification(magnificationFactor)
    sliderValueLabel.setText(FormatMagnification(magnificationFactor))
    sharedPlotModes["vectorMagnificationSliderValue"] = sliderValue
    if afterChange is not None:
        afterChange()
    if isUserChange and not magnificationSlider.isSliderDown():
        print(ViewerConsoleLine(groupName, "vector scale " + FormatMagnification(magnificationFactor)))


def _OnMagnificationSliderPressed(sliderState, magnificationSlider):
    """Record the slider position at the press of its handle
    (_OnMagnificationSliderReleased)."""
    sliderState["valueAtPress"] = magnificationSlider.value()


def _OnMagnificationSliderReleased(groupName, magnificationSlider, sliderState):
    """Echo a dragged vector scale once, on the release, and only when the
    drag changed it.  A click that makes the handle jump changes the
    value before the press, which _OnMagnificationSliderChanged echoes
    already, and a press without a move changes nothing."""
    if magnificationSlider.value() == sliderState.get("valueAtPress"):
        return
    print(ViewerConsoleLine(groupName, "vector scale " + FormatMagnification(
        MagnificationForSliderPosition(magnificationSlider.value()))))


def _OnAverageCurveClicked(groupName, curveName, checkBox, *unusedArguments):
    """Echo a click of the user on a mean-curve check box.  The state is
    read from *checkBox*, because PySide2 5.15 calls a functools.partial
    that is connected to the clicked signal without the checked
    argument (a slot that required it would raise TypeError at every
    click)."""
    print(ViewerConsoleLine(groupName, "{} {}".format(curveName, "shown" if checkBox.isChecked() else "hidden")))


def _OnProfileModeChanged(groupName, profileWidget, sharedPlotModes, updateAverageCurveVisibility, buttonId):
    profileWidget.SetMode(buttonId)
    sharedPlotModes["profile"] = buttonId
    updateAverageCurveVisibility(buttonId)
    print(ViewerConsoleLine(groupName, "profiles " + PROFILE_MODE_NAMES.get(buttonId, str(buttonId))))


def _OnDecentringAngleChanged(groupName, profileWidget, sharedPlotModes, angleDegrees):
    """Apply the decentring angle, echoed unless another tab set the
    shared value."""
    isUserChange = angleDegrees != sharedPlotModes.get("decentringAngleDegrees", 0)
    profileWidget.SetDecentringAngle(angleDegrees)
    sharedPlotModes["decentringAngleDegrees"] = angleDegrees
    if isUserChange:
        print(ViewerConsoleLine(groupName, "decentring angle {}\u00b0".format(angleDegrees)))


def _UpdateAverageCurveVisibility(azimuthMeanCheckBox, semiDiagonalMeanCheckBox, profileModeId,
                                  angleMagnitudeCheckBox=None, angleWidgets=(), plotState=None):
    """Show the overlays of the decentring-only view, and the Decentring
    angle only while the overlay Magnitude at the angle, the only one that
    uses it, is shown (Geert, 13 October 2026: "Otherwise, this box does
    nothing"), and, with *plotState*, the radial distortion type and its
    zero crossing in the other views only (UpdateRadialControlVisibility)."""
    if plotState is not None:
        plotState.profileModeId = profileModeId
        UpdateRadialControlVisibility(plotState)
        # The decentring controls take the row of the radial controls in the
        # decentring-only view.
        if getattr(plotState, "profileControlsStack", None) is not None:
            plotState.profileControlsStack.setCurrentIndex(1 if profileModeId == 1 else 0)
    visible = profileModeId == 1
    semiDiagonalMeanCheckBox.setVisible(visible)
    azimuthMeanCheckBox.setVisible(visible)
    if angleMagnitudeCheckBox is not None:
        angleMagnitudeCheckBox.setVisible(visible)
    angleVisible = visible and (angleMagnitudeCheckBox is None or bool(angleMagnitudeCheckBox.isChecked()))
    for angleWidget in angleWidgets:
        angleWidget.setVisible(angleVisible)


def _OnAngleMagnitudeToggled(updateAverageCurveVisibility, plotState, *unusedArguments):
    """Show or hide the Decentring angle with the overlay Magnitude at the
    angle (_UpdateAverageCurveVisibility), in the profile view shown."""
    updateAverageCurveVisibility(plotState.profileModeId)


def _ApplySharedPlotModes(angleSpinBox, vectorDataByMode, magnificationSlider, profileButtonGroup, profileWidget, sharedPlotModes, vectorButtonGroup, vectorWidget, updateAverageCurveVisibility):
    """Synchronise this tab's plots with the shared state. A vector mode
    that this tab cannot show (a disabled Difference) falls back to the
    first available mode, and the average-curve check boxes follow the
    profile mode."""
    availableVectorModes = [modeId for modeId in VECTOR_MODE_IDS
                            if vectorButtonGroup.button(modeId) is not None
                            and vectorButtonGroup.button(modeId).isEnabled()]
    vectorMode = ChooseAvailableVectorMode(sharedPlotModes.get("vector", 0), availableVectorModes)
    vectorModeButton = vectorButtonGroup.button(vectorMode)
    if vectorModeButton and not vectorModeButton.isChecked():
        vectorModeButton.setChecked(True)
        vectorWidget.SetData(*vectorDataByMode.Get(vectorMode, vectorDataByMode[0]))

    # Synchronise the magnification slider.
    sliderValue = sharedPlotModes.get("vectorMagnificationSliderValue", MAGNIFICATION_SLIDER_DEFAULT)
    if magnificationSlider.value() != sliderValue:
        magnificationSlider.setValue(sliderValue)

    # Synchronise the decentring angle.
    angleValue = int(sharedPlotModes.get("decentringAngleDegrees", 0))
    if angleSpinBox.value() != angleValue:
        angleSpinBox.setValue(angleValue)

    profileMode = sharedPlotModes.get("profile", 2)
    profileButton = profileButtonGroup.button(profileMode)
    if profileButton and not profileButton.isChecked():
        profileButton.setChecked(True)
        profileWidget.SetMode(profileMode)
    updateAverageCurveVisibility(profileMode)


# The axis of the relative density of the coverage by radius: at least up
# to 1.5, with at most this many tick intervals (two, for the lower plots of
# the layout, also on the page of Export HTML, so that it draws the same
# bars on the same scale).
COVERAGE_DENSITY_SMALLEST_MAXIMUM = 1.5
COVERAGE_DENSITY_INTERVAL_COUNT = 2


def CoverageDensityAxis(densities):
    """(tick interval, top of the axis) of the relative density of the
    coverage by radius for the relative densities *densities* of the rings
    shown, as the viewer draws it and the page of Export HTML
    (densityAxis of interiorOrientationReport.py)."""
    densityMaximum = max([COVERAGE_DENSITY_SMALLEST_MAXIMUM] + list(densities))
    densityInterval = ChooseTickInterval(0.0, densityMaximum, COVERAGE_DENSITY_INTERVAL_COUNT)
    return densityInterval, math.ceil(densityMaximum / densityInterval) * densityInterval


def ResidualCollectionText(hasDistortionModel):
    """When the viewer collects the residuals and the coverage of a group,
    for the Console: when its tab is first shown, or at an export, and for
    a group without the frame or fisheye model (*hasDistortionModel*
    False), whose tab shows the Residuals first, when the window opens."""
    if hasDistortionModel:
        return "Residuals and coverage: collected when the tab is first shown, or at an export"
    return "Residuals and coverage: collected when the window opens, for the Residuals mode that the tab shows first"


def PrintVectorFieldNumbers(groupName, pixelColumnCount, pixelRowCount, hasDistortionModel, fieldsByName,
                            consoleLines, gridPointCount=None):
    """Write the RMS and the maximum of every vector field of the
    distortion model to the Console with six significant digits
    (Metashape rounds them to three), with the notes of *consoleLines*.
    The Residuals follow when they are collected (ResidualField)."""
    columns, rows = DistortionGridShape(pixelColumnCount, pixelRowCount)
    gridText = "" if gridPointCount is None else ", {:,} of whose points have a ray".format(gridPointCount)
    print("  [MetaFOCUS] Vector fields of '{}' on a grid of {} x {} cells{} (RMS and maximum of the vector "
          "lengths, which Metashape rounds to three significant digits):".format(groupName, columns, rows, gridText))
    if hasDistortionModel:
        for name in ("Total", "Radial", "Decentring", "Corrections", "Difference"):
            rms, maximum = FieldStatistics(fieldsByName[name])
            if rms is None:
                text = "none" if name == "Corrections" else "not available"
            else:
                text = "RMS {} px, maximum {} px".format(FormatConsoleNumber(rms), FormatConsoleNumber(maximum))
            print("  [MetaFOCUS]   {}: {}".format(name, text))
    # Collected when the tab is first shown, and for a group without the
    # frame or fisheye model, whose first mode is the Residuals, when the
    # window opens.
    print("  [MetaFOCUS]   " + ResidualCollectionText(hasDistortionModel))
    for line in consoleLines:
        print("  [MetaFOCUS]   " + line)


def PrintResidualFieldNumbers(residualCellMeans, residualCells):
    """Write the RMS and the maximum of the cell means of the residuals,
    the number of residuals in them, and the RMS of the individual
    residuals to the Console, with six significant digits, after the
    lines of CollectResiduals, which name the group."""
    rms, maximum = FieldStatistics(residualCellMeans)
    if rms is None:
        print("  [MetaFOCUS]   Residuals: none")
        return
    print("  [MetaFOCUS]   Residuals: RMS {} px, maximum {} px, cell means of {} weighted by the inverse of the "
          "keypoint size, RMS of the individual residuals {} px".format(
              FormatConsoleNumber(rms), FormatConsoleNumber(maximum),
              FormatCountWithNoun(residualCells.CellMeanResidualCount(), "residual", "residuals"),
              FormatConsoleNumber(residualCells.IndividualRms())))
    # The other two statistics of the option Residuals.
    for statistic, choiceText, statisticWords in RESIDUAL_STATISTIC_CHOICES[1:]:
        otherRms, otherMaximum = FieldStatistics(residualCells.CellValues(statistic))
        print("  [MetaFOCUS]   Residuals as {}: RMS {} px, maximum {} px, {}".format(
            statisticWords, FormatConsoleNumber(otherRms), FormatConsoleNumber(otherMaximum),
            FormatCountWithNoun(residualCells.CellResidualCount(statistic), "residual", "residuals")))


class ResidualField:
    """The Residuals mode of one interior orientation group, collected
    (CollectResiduals into a ResidualCellAccumulator) the first time it is
    shown, because a group with two million 2D tie points takes many
    seconds.  Data gives (cell values, title, RMS of the individual
    residuals, None) for VectorFieldWidget.SetData,
    with the cell values of the statistic chosen with the option
    Residuals (*statistic*, Metashape's weighted cell means by default),
    and *title* names the weighted cell means, which the title of another
    statistic replaces by its words."""

    def __init__(self, sensor, chunk, groupName, pixelColumnCount, pixelRowCount, samplingStride, title,
                 coverageCalibration=None):
        self.sensor, self.chunk, self.groupName = sensor, chunk, groupName
        self.pixelColumnCount, self.pixelRowCount = pixelColumnCount, pixelRowCount
        self.samplingStride, self.title = samplingStride, title
        self.statistic = RESIDUAL_STATISTIC_WEIGHTED_MEAN
        self.residualCells = None
        self.cellValuesByStatistic = {}
        # The calibration of a frame or fisheye group, for the coverage by
        # radius, and what to call once the residuals are collected.
        self.coverageCalibration = coverageCalibration
        self.onCollected = None

    def IsCollected(self):
        return self.residualCells is not None

    def Collect(self):
        """The ResidualCellAccumulator of the group, filled on the first
        call, which also writes the numbers to the Console and then calls
        onCollected."""
        if self.residualCells is None:
            residualCells = ResidualCellAccumulator(self.pixelColumnCount, self.pixelRowCount,
                                                    self.coverageCalibration)
            CollectResiduals(self.sensor, self.chunk, samplingStride=self.samplingStride, residualCells=residualCells)
            self.residualCells = residualCells
            PrintResidualFieldNumbers(residualCells.CellMeans(), residualCells)
            PrintCoverageNumbers(residualCells, self.samplingStride)
            if self.onCollected is not None:
                self.onCollected()
        return self.residualCells

    def CoverageTitle(self):
        """Title of the Coverage mode."""
        title = "Coverage, 2D tie points per cell"
        if self.samplingStride > 1:
            title += ", sampled 1 in {:,}".format(self.samplingStride)
        return title

    def CoverageData(self):
        """(no vectors, title, None, None, coverage) of the Coverage mode
        for VectorFieldWidget.SetData."""
        return ([], self.CoverageTitle(), None, None, self.Collect().CoverageCells())

    def CellValues(self, statistic=None):
        statistic = statistic or self.statistic
        if statistic not in self.cellValuesByStatistic:
            self.cellValuesByStatistic[statistic] = self.Collect().CellValues(statistic)
        return self.cellValuesByStatistic[statistic]

    def Title(self, statistic=None):
        statistic = statistic or self.statistic
        return self.title.replace(RESIDUAL_STATISTIC_CHOICES[0][2], ResidualStatisticWords(statistic))

    def Data(self):
        cellValues = self.CellValues()
        return (cellValues, self.Title(), self.residualCells.IndividualRms(), None)


def ResidualStatisticWords(statistic):
    """Words of a statistic of the option Residuals for titles and the
    Console (RESIDUAL_STATISTIC_CHOICES)."""
    for choiceStatistic, choiceText, statisticWords in RESIDUAL_STATISTIC_CHOICES:
        if choiceStatistic == statistic:
            return statisticWords
    return RESIDUAL_STATISTIC_CHOICES[0][2]


class VectorDataByMode:
    """(vectors, title, RMS of the individual residuals, text without
    vectors) of each vector mode of one tab, by mode number, with the
    Residuals (mode 5) and the Coverage (mode 6, with the counts per cell
    as a fifth item) from a ResidualField,
    collected when they are first asked for."""

    def __init__(self, dataByMode, residualField):
        self.dataByMode, self.residualField = dataByMode, residualField

    def __getitem__(self, modeId):
        if modeId == 5:
            return self.residualField.Data()
        if modeId == COVERAGE_MODE_ID:
            return self.residualField.CoverageData()
        return self.dataByMode[modeId]

    def Get(self, modeId, default=None):
        """The data of *modeId*, or *default* for a mode without data."""
        if modeId == 5:
            return self.residualField.Data()
        if modeId == COVERAGE_MODE_ID:
            return self.residualField.CoverageData()
        return self.dataByMode.get(modeId, default)


def PrintCoverageNumbers(residualCells, samplingStride):
    """Write the coverage of the collected residuals to the Console: the
    cells without 2D tie points and, for the coverage by radius in all
    directions, the rings in the image format but without 2D tie points,
    the 2D tie points for which no undistorted point is found within the
    radius at which the radial polynomial reaches its largest distorted
    radius, and those whose r was not found because the inversion of the
    decentring distortion did not converge."""
    counts, columns, rows = residualCells.CoverageCells()
    sampledText = "" if samplingStride <= 1 else " (sampled 1 in {:,})".format(samplingStride)
    print("  [MetaFOCUS]   Coverage: {} in {:,} cells{}, {:,} of the cells without 2D tie points".format(
        FormatCountWithNoun(sum(counts), "2D tie point", "2D tie points"), columns * rows, sampledText,
        sum(1 for count in counts if count <= 0)))
    rings = residualCells.CoverageRings()
    emptyRings = [ring for ring in rings if ring.areaShare > 0.0 and ring.pointCount <= 0]
    if emptyRings:
        print("  [MetaFOCUS]   Coverage by radius: {} in the image format but without 2D tie points, the first at "
              "r = {:,.0f} px".format(FormatCountWithNoun(len(emptyRings), "ring", "rings"), emptyRings[0].start))
    beyondCount = residualCells.withoutRadiusCount - residualCells.unconvergedRadiusCount
    if beyondCount:
        # The undistorted point decides: the decentring distortion can carry
        # a position with an r beyond the largest distorted radius.  The text
        # says what was not found, not where the point lies.
        print("  [MetaFOCUS]   Coverage by radius: {} left out, for which no undistorted point was found within the "
              "radius at which the radial distortion polynomial reaches its largest distorted radius.".format(
                  FormatCountWithNoun(beyondCount, "2D tie point", "2D tie points")))
    if residualCells.unconvergedRadiusCount:
        print("  [MetaFOCUS]   Coverage by radius: {} whose r was not found, because the inversion of the decentring "
              "distortion did not converge, left out.".format(FormatCountWithNoun(
                  residualCells.unconvergedRadiusCount, "2D tie point", "2D tie points")))


# ===================================================================
# The state of the plots of one tab, the options, the table of the
# vector fields, and the CSV export
# ===================================================================

# Short definitions of the vector fields for the table and the CSV
# export: Metashape's definitions, the default.
VECTOR_FIELD_DEFINITIONS = {
    0: "All lens distortion terms and the additional corrections, b1 and b2 left out (Metashape)",
    1: "Gaussian, the terms of k1 to k4 (Metashape)",
    2: "The terms of p1 to p4 (Metashape)",
    3: "The additional corrections (Metashape)",
    4: "Against the initial calibration (Metashape)",
    5: "Cell means weighted by the inverse keypoint size (Metashape)",
}
RESIDUAL_FIELD_DEFINITIONS = {
    RESIDUAL_STATISTIC_WEIGHTED_MEAN: VECTOR_FIELD_DEFINITIONS[5],
    RESIDUAL_STATISTIC_MEAN: "Cell means without weights",
    RESIDUAL_STATISTIC_MEDIAN: "Cell medians of the x and the y components",
}
STATISTICS_TABLE_HEADERS = ["Vector field", "RMS (px)", "Maximum (px)", "Vectors", "Definition"]
# The same headers in the tab, with "Max. (px)" on one line (Geert, 14
# October 2026: "I want "Max. (px)", NOT "Maximum (px)"", his choice of an
# abbreviated label).  The CSV export keeps "Maximum (px)".
STATISTICS_TABLE_HEADER_LABELS = ["Vector field", "RMS (px)", "Max. (px)", "Vectors", "Definition"]
STATISTICS_TABLE_TOOLTIP = (
    "RMS and maximum of the vectors of every mode, in pixels with three significant digits as\n"
    "Metashape's Distortion Plot writes them, the number of vectors, and the definition used. Export CSV\n"
    "writes them with six significant digits.")
RESIDUALS_NOT_COLLECTED_TEXT = "not collected yet (when the tab is first shown, or at an export)"
# Width of the Definition column of that table, in average characters:
# longer definitions wrap, so that the table does not widen the plots.
STATISTICS_TABLE_DEFINITION_CHARACTERS = 56
# Comment line of the CSV export on the units of its values.
CSV_UNITS_LINE = ("Units: c_pix, cx, cy, b1, b2, their standard errors, the vector fields, r, the profiles, and the "
                  "radial residuals in pixels (px), the coefficients k1 to k4 and p1 to p4, estimate/SE, the "
                  "p-values, the correlations, the shares, and the relative density are numbers")
CSV_PARAMETER_HEADERS = ["Parameter", "Parameter estimate", "Standard error (SE)", "estimate/SE",
                         "p-value (two-sided)"]
CSV_NO_COVARIANCE_LINE = ("No calibration covariance for this interior orientation group, so no parameter table, "
                          "correlations, or estimated degrees of freedom")
CSV_FORMAT_TOOLTIP_TEXT_OF_THE_VIEWER = (
    "Delimiter and decimal separator of Export CSV. Comma with a decimal point suits most software and Excel\n"
    "with English regional settings. Semicolon with a decimal comma suits Excel with regional settings that use\n"
    "the decimal comma, for example German or French. Comment lines that start with # and text cells keep their\n"
    "text unchanged.")


class ParameterTableData:
    """The values of the parameter table of one interior orientation
    group, for the CSV export: the names of the adjusted parameters in
    Metashape's notation, their estimates, standard errors, estimate/SE,
    and two-sided p-values (None for c_pix), the correlation matrix, the
    two estimated degrees of freedom (None when not positive), and the
    text that names the disabled images that are still oriented and have
    2D tie points or marker projections (DisabledOrientedImagesText),
    whose observations and unknowns these counts leave out, or an empty
    text."""

    def __init__(self, parameterNames, estimates, standardErrors, testStatistics, pValues, correlationMatrix,
                 degreesOfFreedomTiePoints, degreesOfFreedomAllObservations, degreesOfFreedomNote=""):
        self.parameterNames, self.estimates, self.standardErrors = parameterNames, estimates, standardErrors
        self.testStatistics, self.pValues, self.correlationMatrix = testStatistics, pValues, correlationMatrix
        self.degreesOfFreedomTiePoints = degreesOfFreedomTiePoints
        self.degreesOfFreedomAllObservations = degreesOfFreedomAllObservations
        self.degreesOfFreedomNote = degreesOfFreedomNote


def VectorFieldStatisticsRow(name, vectors, definition):
    """(name, RMS, maximum, number of vectors, definition) of one vector
    field, with RMS and maximum None for a field without vectors."""
    rms, maximum = FieldStatistics(vectors)
    return (name, rms, maximum, len(vectors), definition)


def StatisticsTableTexts(row):
    """The cells of a row of the table of the vector fields: the RMS and
    the maximum with three significant digits as Metashape writes them,
    "none" for a field without vectors, and a dash for Residuals that are
    not collected yet."""
    name, rms, maximum, vectorCount, definition = row
    if vectorCount is None:
        return [name, "–", "–", "–", definition]
    if not vectorCount:
        return [name, "none", "none", "0", definition]
    return [name, FormatAsMetashape(rms), FormatAsMetashape(maximum), "{:,}".format(vectorCount), definition]


def FillStatisticsTable(table, rows):
    """Write *rows* (VectorFieldStatisticsRow) into the table of the
    vector fields."""
    table.setRowCount(len(rows))
    for rowIndex, row in enumerate(rows):
        for columnIndex, text in enumerate(StatisticsTableTexts(row)):
            item = QtWidgets.QTableWidgetItem(text)
            item.setTextAlignment((QtCore.Qt.AlignLeft if columnIndex in (0, 4) else QtCore.Qt.AlignRight)
                                  | QtCore.Qt.AlignVCenter)
            table.setItem(rowIndex, columnIndex, item)
    WrapDefinitionColumn(table)


def WrapDefinitionColumn(table):
    """Size the columns of the table of the vector fields to their
    contents, but the Definition column to at most
    STATISTICS_TABLE_DEFINITION_CHARACTERS average characters, in which
    longer definitions wrap, and the rows to their wrapped text."""
    table.setWordWrap(True)
    table.resizeColumnsToContents()
    definitionColumn = len(STATISTICS_TABLE_HEADERS) - 1
    characterWidth = table.fontMetrics().averageCharWidth()
    definitionWidth = table.columnWidth(definitionColumn)
    if isinstance(characterWidth, int) and isinstance(definitionWidth, int):
        maximumWidth = characterWidth * STATISTICS_TABLE_DEFINITION_CHARACTERS
        if definitionWidth > maximumWidth:
            table.setColumnWidth(definitionColumn, maximumWidth)
    table.resizeRowsToContents()


# The values of the distortion profiles, as in the table of the HTML page
# (interiorOrientationReport): from 0 to the largest radius of the profiles
# in steps from the 1, 2, 5 sequence that give at most
# PROFILE_TABLE_MAXIMUM_INTERVAL_COUNT intervals.  Export CSV writes them,
# with the comment lines of ProfileCommentLines.
PROFILE_TABLE_MAXIMUM_INTERVAL_COUNT = 14
PROFILE_TABLE_HEADERS = ["r (px)", "Radial, Gaussian (px)", "Radial, balanced (px)",
                         "Decentring profile function (px)"]


def ProfileCommentLines(zeroCrossingRadiusPixels, zeroCrossingPercent):
    """Comment lines of the CSV export that define its values of the
    profiles, with the zero crossing r0 of the balanced radial
    distortion."""
    return ["Profiles: r is the radial distance of the undistorted ray from the principal point, up to half the "
            "diagonal of the image format",
            "Radial distortion: positive towards the principal point, as in Metashape's Profile tab, as estimated "
            "(Gaussian) and balanced, zero at r0 = {:,.1f} px ({} % of the largest radius)".format(
                zeroCrossingRadiusPixels, zeroCrossingPercent),
            "Decentring profile function: sqrt(p1² + p2²) r²/c_pix with the factor 1 + p3 (r/c_pix)² + p4 "
            "(r/c_pix)⁴"]


def FormatProfileTableValue(value, significantDigits):
    """A value of the table of the profile values with *significantDigits*
    significant digits, as the page of Export HTML writes it (formatG of
    interiorOrientationReport): "0" for zero, also for a negative zero, and
    "n/a" for a value that is not a finite number."""
    if value is None or not isinstance(value, (int, float)) or not math.isfinite(value):
        return "n/a"
    if value == 0:
        return "0"
    return "{:.{}g}".format(value, significantDigits)


def ProfileTableRows(calibration, pixelColumnCount, pixelRowCount, zeroCrossingPercent):
    """Rows (radius, Gaussian radial distortion, balanced radial distortion,
    decentring profile function) of the table of the profile values, in
    pixels, computed as the page of Export HTML computes its table
    (fillProfileTable of interiorOrientationReport), with the zero crossing
    of the balanced form at *zeroCrossingPercent* per cent of the largest
    radius.  [] without a positive c_pix."""
    principalDistancePixels = getattr(calibration, "f", None)
    if not IsPositiveNumber(principalDistancePixels):
        return []
    maximumRadius = LargestProfileRadiusPixels(pixelColumnCount, pixelRowCount)
    interval = ChooseTickInterval(0.0, maximumRadius, PROFILE_TABLE_MAXIMUM_INTERVAL_COUNT)
    zeroRadius = BalancedZeroCrossingRadiusPixels(pixelColumnCount, pixelRowCount, zeroCrossingPercent)
    zeroNormalised = zeroRadius / principalDistancePixels
    offsetTerm = RadialTerm(calibration, zeroNormalised * zeroNormalised)
    p1Value, p2Value = _Coefficient(calibration, "p1"), _Coefficient(calibration, "p2")
    p3Value, p4Value = _Coefficient(calibration, "p3"), _Coefficient(calibration, "p4")
    rows = []
    radius = 0.0
    # The radius grows by the interval, as on the page.
    while radius <= maximumRadius + 1e-9:
        normalised = radius / principalDistancePixels
        radiusSquared = normalised * normalised
        term = RadialTerm(calibration, radiusSquared)
        higherOrder = 1 + p3Value * radiusSquared + p4Value * radiusSquared * radiusSquared
        rows.append((radius, -radius * term, -radius * (term - offsetTerm),
                     principalDistancePixels * radiusSquared * abs(higherOrder) * math.hypot(p1Value, p2Value)))
        radius += interval
    return rows


def CoverageDirection(plotState):
    """The direction of the coverage by radius in whole degrees, which the
    tabs share, or None for all directions."""
    return plotState.sharedPlotModes.get("coverageDirectionDegrees")


def FitCoverageToTable(plotState):
    """Let the plots of the coverage by radius follow the height of the
    table of the vector fields beside them (CoverageByRadiusWidget.sizeHint)
    after the table has changed."""
    if getattr(plotState, "coverageWidget", None) is not None:
        plotState.coverageWidget.updateGeometry()


def RefreshCoverage(plotState):
    """Show the coverage by radius of the collected residuals in the
    direction that the tabs share, with the statistic of the option
    Residuals, and fill the Residuals into the table of the vector
    fields."""
    residualField = plotState.ResidualField()
    if not residualField.IsCollected():
        return
    if getattr(plotState, "coverageWidget", None) is not None:
        directionDegrees = CoverageDirection(plotState)
        plotState.coverageWidget.SetRings(
            residualField.residualCells.CoverageRings(residualField.statistic, directionDegrees),
            RingStatisticWords(residualField.statistic), residualField.samplingStride, directionDegrees)
    if getattr(plotState, "statisticsTable", None) is not None:
        FillStatisticsTable(plotState.statisticsTable, plotState.StatisticsRows())
        FitCoverageToTable(plotState)


def CollectCoverageOfShownTab(plotState):
    """Collect the residuals of the group when its tab is first shown
    (CoverageByRadiusWidget.onFirstShown), which fills the coverage, with a
    busy pointer, while the plots say that the residuals are being
    collected, which takes a while for a large group."""
    residualField = plotState.ResidualField()
    if residualField.IsCollected():
        RefreshCoverage(plotState)
        return
    coverageWidget = getattr(plotState, "coverageWidget", None)
    if coverageWidget is not None:
        coverageWidget.SetStatus(COVERAGE_COLLECTING_TEXT)
    QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
    try:
        # Collect calls RefreshCoverage (onCollected).
        residualField.Collect()
    except Exception as collectError:
        ReportSuppressedError("interiorOrientationViewer.CollectCoverageOfShownTab", collectError)
        if coverageWidget is not None:
            coverageWidget.SetStatus(COVERAGE_FAILED_TEXT)
    finally:
        QtWidgets.QApplication.restoreOverrideCursor()


def ApplyCoverageDirection(plotState, directionDegrees):
    """Show the coverage by radius in the wedge of *directionDegrees*, or
    in all directions (None), for this tab and, through sharedPlotModes, the
    others: the rings, the line in the Coverage map, the direction row, and
    its button All directions."""
    plotState.sharedPlotModes["coverageDirectionDegrees"] = directionDegrees
    if plotState.vectorWidget is not None:
        plotState.vectorWidget.SetCoverageDirection(directionDegrees)
    if plotState.directionValueLabel is not None:
        plotState.directionValueLabel.setText(DirectionValueText(directionDegrees))
    if plotState.allDirectionsButton is not None:
        plotState.allDirectionsButton.setEnabled(directionDegrees is not None)
    RefreshCoverage(plotState)


def _OnCoverageClicked(plotState, directionDegrees):
    """A click in the Coverage map sets the direction of the coverage by
    radius (VectorFieldWidget.mousePressEvent)."""
    ApplyCoverageDirection(plotState, directionDegrees)
    print(ViewerConsoleLine(plotState.groupName, "coverage by radius at " + DirectionValueText(directionDegrees)))


def _OnAllDirectionsClicked(plotState, *unusedArguments):
    """All directions: the coverage by radius of the whole rings again."""
    ApplyCoverageDirection(plotState, None)
    print(ViewerConsoleLine(plotState.groupName, "coverage by radius in all directions"))


class DistortionPlotState:
    """What the plots of one interior orientation group show and how: the
    vector fields by mode (VectorDataByMode), the definitions chosen with
    the options of the tab (Metashape's by default), and the widgets that
    follow them.  The option controls change it, and the total
    magnification, the table of the vector fields, and the CSV export read
    it."""

    def __init__(self, sensor, chunk, calibration, groupName, pixelColumnCount, pixelRowCount, hasDistortionModel,
                 sharedPlotModes):
        self.sensor, self.chunk, self.calibration, self.groupName = sensor, chunk, calibration, groupName
        self.pixelColumnCount, self.pixelRowCount = pixelColumnCount, pixelRowCount
        self.hasDistortionModel, self.sharedPlotModes = hasDistortionModel, sharedPlotModes
        self.vectorDataByMode = None
        self.currentMode = 0
        self.correctionAt = None
        self.gaussianRadial = []
        self.initialDifference = []
        self.initialDescription = ""
        # The reference of the Difference in words, "" for the initial
        # calibration, and the calibrations of the other items of the combo
        # box by their data.
        self.differenceDescription = ""
        self.referenceCalibrationsByData = {}
        self.differenceChoiceIndex = 0
        self.radialConvention = RADIAL_CONVENTION_GAUSSIAN
        self.balancedZeroCrossingPercent = DEFAULT_BALANCED_ZERO_CROSSING_PERCENT
        self.vectorWidget = None
        self.vectorButtonGroup = None
        self.totalMagnificationLabel = None
        self.statisticsTable = None
        # The coverage by radius, and the label of its direction and the
        # button All directions.
        self.coverageWidget = None
        self.directionValueLabel = None
        self.allDirectionsButton = None
        # The radial controls and, in the decentring-only view, the
        # decentring controls in their place.
        self.profileControlsStack = None
        self.profileWidget = None
        self.differenceComboBox = None
        self.residualComboBox = None
        self.radialComboBox = None
        self.zeroCrossingSpinBox = None
        self.zeroCrossingWidgets = ()
        # The label and the combo box of the radial distortion type, and
        # the profile view shown (2 Both, 0 Radial, 1 Decentring), for
        # UpdateRadialControlVisibility.
        self.radialConventionWidgets = ()
        self.profileModeId = 2
        # Lines on the rays of the grid points (RayReportLines).
        self.rayLines = []

    def ResidualField(self):
        return self.vectorDataByMode.residualField

    def ZeroCrossingRadiusPixels(self):
        return BalancedZeroCrossingRadiusPixels(self.pixelColumnCount, self.pixelRowCount,
                                                self.balancedZeroCrossingPercent)

    def Definition(self, modeId):
        """The definition of a vector field in words, as the options set
        it."""
        if modeId == 1 and self.radialConvention == RADIAL_CONVENTION_BALANCED:
            zeroCrossingRadiusPixels = self.ZeroCrossingRadiusPixels()
            balancedPrincipalDistance = BalancedPrincipalDistancePixels(self.calibration, zeroCrossingRadiusPixels)
            return ("Balanced, zero at r0 = {:,.1f} px ({} % of the largest radius), principal distance in pixels "
                    "of the balanced form {}").format(
                zeroCrossingRadiusPixels, self.balancedZeroCrossingPercent,
                "n/a" if balancedPrincipalDistance is None else "{:.2f}".format(balancedPrincipalDistance))
        if modeId == 4 and self.differenceDescription:
            return "Against " + self.differenceDescription
        if modeId == 5:
            return RESIDUAL_FIELD_DEFINITIONS.get(self.ResidualField().statistic, VECTOR_FIELD_DEFINITIONS[5])
        return VECTOR_FIELD_DEFINITIONS[modeId]

    def StatisticsRows(self, collectResiduals=False):
        """Rows (VectorFieldStatisticsRow) of every mode of this tab, in
        the order of the radio buttons.  The Residuals are collected only
        with *collectResiduals* (the CSV export) or when they were
        already."""
        rows = []
        for modeId in VECTOR_FIELD_MODE_IDS:
            if not self.hasDistortionModel and modeId != 5:
                continue
            name = VECTOR_MODE_NAMES[modeId]
            if modeId == 5 and not (collectResiduals or self.ResidualField().IsCollected()):
                rows.append((name, None, None, None,
                             "{}, {}".format(self.Definition(5), RESIDUALS_NOT_COLLECTED_TEXT)))
                continue
            rows.append(VectorFieldStatisticsRow(name, self.vectorDataByMode.Get(modeId, ([],))[0],
                                                 self.Definition(modeId)))
        return rows

    def SettingsLines(self):
        """Comment lines of the CSV export that name the definitions
        chosen with the options."""
        differenceReference = (self.differenceDescription if self.differenceDescription
                               else (self.initialDescription or "the initial calibration") + " (Metashape)")
        return ["Difference: against " + differenceReference,
                "Residuals: " + self.Definition(5),
                "Radial distortion: " + self.Definition(1)] + list(self.rayLines)


def CsvNumber(value, significantDigits):
    """A number of the CSV export with *significantDigits* significant
    digits, or an empty cell for None or a value that is not finite.
    Zero is "0", also a negative zero, as on the page of Export HTML (the
    radial distortion of the profiles, positive towards the principal
    point, would otherwise write "-0" with all lens distortion
    coefficients set to zero)."""
    if value is None or not isinstance(value, (int, float)) or not math.isfinite(value):
        return ""
    if value == 0:
        return "0"
    return "{:.{}g}".format(value, significantDigits)


def CsvPValue(pValue):
    """A p-value of the CSV export with six significant digits, "< 1e-300"
    below SMALLEST_SHOWN_P_VALUE as in the table (erfc underflows there),
    and an empty cell when there is none, as for c_pix."""
    if pValue is not None and isinstance(pValue, (int, float)) and math.isfinite(pValue) \
            and pValue < SMALLEST_SHOWN_P_VALUE:
        return "< 1e-300"
    return CsvNumber(pValue, 6)


# The table of the coverage by radius in the CSV export.
COVERAGE_CSV_HEADERS = ["Radius from (px)", "Radius to (px)", "2D tie points", "Share of the 2D tie points",
                        "Share of the image format", "Relative density", "Radial residual (px)"]


def CoverageCommentLines(ringWords, samplingStride, directionDegrees=None):
    """Comment lines of the CSV export on its table of the coverage by
    radius, with the words of the ring statistic (RingStatisticWords) and
    the direction shown (all directions, or the wedge of
    *directionDegrees*)."""
    if directionDegrees is None:
        directionLine = "Direction of the coverage by radius: all directions"
    else:
        directionLine = ("Direction of the coverage by radius: {}, counted from the image x axis towards the image y "
                         "axis".format(DirectionValueText(directionDegrees)))
    # The wedge only with a direction.
    if directionDegrees is None:
        densityLine = ("Relative density: the share of the 2D tie points that lie in a ring divided by the share of "
                       "the image format in it, 1 for an even spread")
    else:
        densityLine = ("Relative density: the share of the 2D tie points that lie in the part of a ring within the "
                       "wedge divided by the share of the image format in that part, 1 for an even spread")
    lines = ["Coverage by radius: rings of the radial distance r of the profiles",
             directionLine,
             densityLine,
             COVERAGE_SHARES_LINE,
             "Radial residual: the {} of the components of the residuals along the direction away from the principal "
             "point, for rings with at least {} 2D tie points".format(ringWords, COVERAGE_MINIMUM_RESIDUAL_COUNT)]
    if samplingStride > 1:
        lines.append("The 2D tie points are sampled 1 in {:,}".format(samplingStride))
    return lines


def InteriorOrientationCsvContent(commentLines, parameterTable, statisticsRows, profileRows=(), coverageRings=()):
    """Content of the CSV export of one interior orientation group, as a
    list of ("comment", text), ("blank",), ("header", cells), and ("row",
    cells, indices of the text cells): the comment lines, the parameter
    table with all digits (estimates and standard errors with fifteen
    significant digits, estimate/SE and the p-values with six), the lower
    triangle of the correlation matrix, the two estimated degrees of
    freedom, and the table of the vector fields (six significant digits).
    Without a parameter table (no calibration covariance) a comment line
    says so.  The values of the profiles (ProfileTableRows) and the
    coverage by radius (CoverageRing) follow, with six significant digits,
    when they are given."""
    content = [("comment", line) for line in commentLines]
    if parameterTable is None:
        content.append(("comment", CSV_NO_COVARIANCE_LINE))
    else:
        content += [("blank",), ("header", CSV_PARAMETER_HEADERS)]
        for parameterIndex, parameterName in enumerate(parameterTable.parameterNames):
            pValueText = CsvPValue(parameterTable.pValues[parameterIndex])
            content.append(("row", [
                _DisplayParameterName(parameterName),
                CsvNumber(parameterTable.estimates[parameterIndex], 15),
                CsvNumber(parameterTable.standardErrors[parameterIndex], 15),
                CsvNumber(parameterTable.testStatistics[parameterIndex], 6),
                pValueText], {0, 4} if pValueText.startswith("<") else {0}))
        headerNames = [_CorrelationHeaderName(parameterName) for parameterName in parameterTable.parameterNames]
        content += [("blank",), ("header", ["Correlation"] + headerNames)]
        for rowIndex, headerName in enumerate(headerNames):
            content.append(("row", [headerName] + [
                CsvNumber(parameterTable.correlationMatrix[rowIndex][columnIndex], 6) if columnIndex <= rowIndex else ""
                for columnIndex in range(len(headerNames))], {0}))
        content.append(("blank",))
        for label, value in (("Estimated DoF (tie points)", parameterTable.degreesOfFreedomTiePoints),
                             ("Estimated DoF (all observations)", parameterTable.degreesOfFreedomAllObservations)):
            content.append(("row", [label, "" if value is None else "{:d}".format(int(value))], {0}))
        # The disabled images that the counts leave out (ParameterTableData).
        if getattr(parameterTable, "degreesOfFreedomNote", ""):
            content.append(("comment", parameterTable.degreesOfFreedomNote))
    content += [("blank",), ("header", STATISTICS_TABLE_HEADERS)]
    for name, rms, maximum, vectorCount, definition in statisticsRows:
        content.append(("row", [name, CsvNumber(rms, 6), CsvNumber(maximum, 6),
                                "" if vectorCount is None else "{:d}".format(vectorCount), definition], {0, 4}))
    if profileRows:
        content += [("blank",), ("header", PROFILE_TABLE_HEADERS)]
        for row in profileRows:
            content.append(("row", [CsvNumber(value, 6) for value in row], set()))
    if coverageRings:
        content += [("blank",), ("header", COVERAGE_CSV_HEADERS)]
        for ring in coverageRings:
            content.append(("row", [CsvNumber(ring.start, 6), CsvNumber(ring.end, 6), "{:d}".format(ring.pointCount),
                                    CsvNumber(ring.pointShare, 6), CsvNumber(ring.areaShare, 6),
                                    CsvNumber(ring.relativeDensity, 6), CsvNumber(ring.radialResidual, 6)], set()))
    return content


def WriteInteriorOrientationCsv(path, content, csvDelimiter=",", decimalSeparator="."):
    """Write the content of InteriorOrientationCsvContent to *path* in
    UTF-8 with *csvDelimiter*: comment lines start with # and keep their
    text, the numbers of the data rows get *decimalSeparator*, and the
    header rows and the text cells keep their text.  The file is written to
    a new temporary file next to *path*, which then replaces *path*
    (os.replace), as the report of Export HTML is (WriteReport), so that a
    failure leaves an earlier file at *path* as it was.  The temporary file
    of a failed write is removed (RemoveTemporaryReport)."""
    fileDescriptor, temporaryPath = tempfile.mkstemp(
        dir=os.path.dirname(os.path.abspath(path)), prefix=os.path.basename(path) + ".", suffix=".tmp")
    try:
        with os.fdopen(fileDescriptor, "w", newline="", encoding="utf-8") as fileHandle:
            csvWriter = csv.writer(fileHandle, delimiter=csvDelimiter)
            for item in content:
                if item[0] == "comment":
                    fileHandle.write("# {}\r\n".format(item[1]))
                elif item[0] == "blank":
                    csvWriter.writerow([])
                elif item[0] == "header":
                    csvWriter.writerow(item[1])
                else:
                    csvWriter.writerow(DataRowWithDecimalSeparator(item[1], decimalSeparator, item[2]))
        os.replace(temporaryPath, path)
    except BaseException:
        RemoveTemporaryReport(temporaryPath)
        raise


def CsvFileNamePart(text):
    """*text* for use in a file name: every run of characters other than
    letters, digits, '.', '_', and '-' becomes one underscore."""
    return re.sub(r"[^\w.-]+", "_", text or "").strip("_") or "unnamed"


def CsvFileName(chunk, sensor):
    """Default name of the CSV export of one interior orientation
    group."""
    return "interiorOrientation_{}_{}.csv".format(CsvFileNamePart(getattr(chunk, "label", "")),
                                                   CsvFileNamePart(InteriorOrientationGroupName(sensor, chunk)))


def TabCsvContent(chunk, sensor, parameterTable, plotsWidget):
    """Content of the CSV export of one tab (InteriorOrientationCsvContent):
    comment lines with the MetaFOCUS version, the time of the export, the
    chunk, the group, its numbers of pixel columns and rows, the
    definitions chosen, the notes on the rays of the grid points, the
    definitions of the profiles and the coverage by radius, and the
    units, then the parameter table (None without a covariance), the
    table of the vector fields, the values of the profiles, and the
    coverage by radius.  The residuals are collected when they are not
    yet."""
    from . import _METAFOCUS_VERSION
    commentLines = ["MetaFOCUS {}, Interior Orientation Viewer".format(_METAFOCUS_VERSION),
                    "Exported: {}".format(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
                    "Chunk: {}".format(getattr(chunk, "label", "")),
                    "Interior orientation group: {}".format(InteriorOrientationGroupName(sensor, chunk))]
    calibration = GetSensorCalibration(sensor)
    if calibration is not None:
        commentLines.append("Pixel columns and rows: {} x {}".format(calibration.width, calibration.height))
    plotState = getattr(plotsWidget, "_plotState", None) if plotsWidget is not None else None
    statisticsRows, profileRows, coverageRings = [], [], []
    if plotState is not None:
        commentLines += plotState.SettingsLines()
        statisticsRows = plotState.StatisticsRows(collectResiduals=True)
        if plotState.hasDistortionModel:
            profileRows = ProfileTableRows(plotState.calibration, plotState.pixelColumnCount,
                                           plotState.pixelRowCount, plotState.balancedZeroCrossingPercent)
            if profileRows:
                commentLines += ProfileCommentLines(plotState.ZeroCrossingRadiusPixels(),
                                                    plotState.balancedZeroCrossingPercent)
        residualField = plotState.ResidualField()
        if residualField.IsCollected():
            # The direction shown.
            directionDegrees = CoverageDirection(plotState)
            coverageRings = residualField.residualCells.CoverageRings(residualField.statistic, directionDegrees)
            if coverageRings:
                commentLines += CoverageCommentLines(RingStatisticWords(residualField.statistic),
                                                     residualField.samplingStride, directionDegrees)
    commentLines.append(CSV_UNITS_LINE)
    return InteriorOrientationCsvContent(commentLines, parameterTable, statisticsRows, profileRows, coverageRings)


def TabReportData(chunk, sensor, parameterTable, plotsWidget):
    """The data of the report of one tab (interiorOrientationReport.ReportData):
    the parameter table (None without a covariance) and the plots of the
    tab, whose Residuals are collected when they are not yet, as for
    Export CSV."""
    plotState = getattr(plotsWidget, "_plotState", None) if plotsWidget is not None else None
    document = getattr(getattr(Metashape, "app", None), "document", None)
    documentPath = getattr(document, "path", "") if document is not None else ""
    return ReportData(chunk, sensor, parameterTable, plotState,
                      documentPath=documentPath if isinstance(documentPath, str) else "")


# Tooltip of the Coverage mode (the click only for the frame and fisheye
# camera models, whose principal point the direction starts from).
COVERAGE_MODE_TOOLTIP = (
    "The 2D tie points of the valid tie points per cell of the grid, pooled over the images of the group,\n"
    "darker for more, and the cells without 2D tie points tinted red. Collected with the residuals.")
COVERAGE_MODE_CLICK_TOOLTIP = "A click in the map chooses the direction of the coverage by radius."


def CoverageModeToolTip(hasDistortionModel):
    """The tooltip of the Coverage mode: with the click that chooses the
    direction of the coverage by radius only for a group with the frame or
    fisheye model (*hasDistortionModel*), the only one with a principal
    point and the coverage by radius."""
    if hasDistortionModel:
        return COVERAGE_MODE_TOOLTIP + "\n" + COVERAGE_MODE_CLICK_TOOLTIP
    return COVERAGE_MODE_TOOLTIP
# The direction row of the coverage by radius.
DIRECTION_HINT_TEXT = "Click in the Coverage map to choose a direction."
DIRECTION_TOOLTIP = (
    "The direction of the coverage by radius: the rings of a wedge of \u00b115\u00b0 around it. The direction is\n"
    "counted from the image x axis towards the image y axis, as the Decentring angle, so that 90\u00b0 points\n"
    "down the image and 270\u00b0 up. A click in the Coverage map chooses it.")
ALL_DIRECTIONS_TOOLTIP = "Show the coverage by radius of the whole rings again, in all directions."
# The grid of the plots of a tab: the spacing of its columns and rows, the
# space between the boxes Difference against and Residuals, the room for the
# arrow of a box, the room for the scroll bar of the list of a box, and the
# width of the vector scale slider.
PLOT_GROUP_HORIZONTAL_SPACING = 18
PLOT_GROUP_VERTICAL_SPACING = 4
OPTION_BOX_SPACING = 14
OPTION_BOX_ARROW_ALLOWANCE = 38
OPTION_LIST_MARGIN = 24
MAGNIFICATION_SLIDER_WIDTH = 200


def ControlRow():
    """(widget, its QHBoxLayout without margins): a row of the grid of the
    plots."""
    rowWidget = QtWidgets.QWidget()
    rowLayout = QtWidgets.QHBoxLayout(rowWidget)
    rowLayout.setContentsMargins(0, 0, 0, 0)
    return rowWidget, rowLayout


def ColumnOfWidgets(spacing):
    """(widget, its QVBoxLayout without margins and with *spacing*): one
    column of the plot row."""
    columnWidget = QtWidgets.QWidget()
    columnLayout = QtWidgets.QVBoxLayout(columnWidget)
    columnLayout.setContentsMargins(0, 0, 0, 0)
    columnLayout.setSpacing(spacing)
    return columnWidget, columnLayout


def RetainSpaceWhenHidden(widget):
    """Keep the space of *widget* in its row while it is hidden, so that the
    widgets after it stay where they are."""
    sizePolicy = widget.sizePolicy()
    sizePolicy.setRetainSizeWhenHidden(True)
    widget.setSizePolicy(sizePolicy)


def OptionBoxStandardTexts():
    """The standard items of the boxes Difference against and Residuals,
    without the names of other interior orientation groups and of loaded
    calibration files."""
    return ([DIFFERENCE_AGAINST_INITIAL_CALIBRATION_TEXT, DIFFERENCE_AGAINST_CALIBRATION_FILE_TEXT]
            + [choiceText for _, choiceText, _ in RESIDUAL_STATISTIC_CHOICES])


def WidenComboBoxList(comboBox):
    """Let the list of *comboBox* show its longest item in full."""
    listView = comboBox.view()
    widestItem = listView.sizeHintForColumn(0)
    if isinstance(widestItem, int) and widestItem > 0:
        listView.setMinimumWidth(widestItem + OPTION_LIST_MARGIN)


def SizeOptionBoxes(comboBoxes, itemTexts):
    """Give the boxes Difference against and Residuals the width of the
    longest of *itemTexts* and the arrow, and their lists the width of
    their longest item, so that a long group name is cut in the closed box
    only (Geert: "shorter drop-down boxes")."""
    metrics = comboBoxes[0].fontMetrics()
    textWidths = [metrics.horizontalAdvance(itemText) for itemText in itemTexts]
    if not all(isinstance(textWidth, int) for textWidth in textWidths):
        return
    for comboBox in comboBoxes:
        comboBox.setFixedWidth(max(textWidths) + OPTION_BOX_ARROW_ALLOWANCE)
        WidenComboBoxList(comboBox)


def DirectionValueWidth(valueLabel):
    """The width of the label of the direction that holds its longest text,
    so that the button after it does not move, or None when the font cannot
    tell."""
    metrics = valueLabel.fontMetrics()
    textWidths = [metrics.horizontalAdvance(DirectionValueText(directionDegrees)) for directionDegrees in (None, 270)]
    if not all(isinstance(textWidth, int) for textWidth in textWidths):
        return None
    return max(textWidths) + 4


def BuildDirectionRow(plotState, sliderRowWidget):
    """The direction of the coverage by radius under the profiles, beside
    the slider row and as high as it: the direction, the button All
    directions, enabled once a direction is set, and how to set one."""
    rowWidget, rowLayout = ControlRow()
    rowHeight = sliderRowWidget.sizeHint().height()
    if isinstance(rowHeight, int):
        rowWidget.setFixedHeight(rowHeight)
    directionLabel = QtWidgets.QLabel("Direction:")
    directionLabel.setToolTip(DIRECTION_TOOLTIP)
    rowLayout.addWidget(directionLabel)
    directionDegrees = CoverageDirection(plotState)
    valueLabel = QtWidgets.QLabel(DirectionValueText(directionDegrees))
    valueLabel.setToolTip(DIRECTION_TOOLTIP)
    valueWidth = DirectionValueWidth(valueLabel)
    if valueWidth is not None:
        valueLabel.setFixedWidth(valueWidth)
    rowLayout.addWidget(valueLabel)
    rowLayout.addSpacing(6)
    allDirectionsButton = QtWidgets.QPushButton("All directions")
    allDirectionsButton.setToolTip(ALL_DIRECTIONS_TOOLTIP)
    allDirectionsButton.setEnabled(directionDegrees is not None)
    if isinstance(rowHeight, int):
        allDirectionsButton.setFixedHeight(rowHeight)
    allDirectionsButton.clicked.connect(functools.partial(_OnAllDirectionsClicked, plotState))
    rowLayout.addWidget(allDirectionsButton)
    rowLayout.addSpacing(12)
    hintLabel = QtWidgets.QLabel(DIRECTION_HINT_TEXT)
    hintLabel.setStyleSheet("color: rgb(100, 100, 100);")
    rowLayout.addWidget(hintLabel)
    rowLayout.addStretch()
    plotState.directionValueLabel, plotState.allDirectionsButton = valueLabel, allDirectionsButton
    return rowWidget


# Tooltip of Export HTML.
EXPORT_REPORT_TOOLTIP = (
    "Write a report of the interior orientation group of the tab shown, or of every group, as one HTML file\n"
    "per group: the parameters, the camera model, the vector fields, and the distortion profiles, with the\n"
    "controls of the viewer. A web browser shows it without an internet connection and prints it, for example\n"
    "to a PDF file.")

# The two choices of Export HTML with more than one interior orientation
# group (the question of ReportScopeQuestionText).
REPORT_SCOPE_TAB_SHOWN = "tab shown"
REPORT_SCOPE_EVERY_GROUP = "every group"

# Why Export HTML of every group did not write a report, with the place of
# the file: a file of that name appeared in the folder during the export.
APPEARED_REPORT_REASON_TEXT = "{} appeared in the folder during the export and was kept"


def ReportScopeQuestionText(reportTabCount, tabCount, chunkGroupCount, tabShownHasReport=True):
    """The question of Export HTML in Geert's words (20 November 2026): the
    report of the active tab, or those of all tabs.  Of the *tabCount* tabs,
    *reportTabCount* have a report, and the chunk has *chunkGroupCount*
    interior orientation groups, also those without an enabled, oriented
    image, which get no tab.  When a tab has no report, all tabs means all
    tabs that have one, and when the active tab has none
    (*tabShownHasReport*), only these are offered."""
    if reportTabCount == chunkGroupCount:
        groupsText = "the {:,} interior orientation groups in this chunk".format(reportTabCount)
    else:
        groupsText = "{:,} of the {:,} interior orientation groups in this chunk".format(reportTabCount,
                                                                                        chunkGroupCount)
    tabsText = "all tabs" if reportTabCount == tabCount else "all tabs that have one"
    if not tabShownHasReport:
        return ("The active tab has no interior orientation report. Export HTML writes the interior orientation "
                "report of {} (i.e. {}) in a folder that is chosen next.".format(tabsText, groupsText))
    return ("Export HTML writes the interior orientation report of either the active tab or of {} (i.e. {}) in a "
            "folder that is chosen next.".format(tabsText, groupsText))


def NoReportOfTabShownText(tabText, actionName):
    """The message of Export HTML or Export CSV (*actionName*) when the
    interior orientation group of the tab shown, *tabText*, has no
    calibration that the viewer can plot, so that its tab holds only a
    message (BuildWidgetForSensor)."""
    return ("The interior orientation group of the tab shown, {}, has no calibration that the viewer can plot, so "
            "{} has nothing to write for it.".format(tabText, actionName))


def ReportTabs(tabs):
    """(tab text, tab widget) of every tab of *tabs* (a QTabWidget) that
    has a report (its _reportData, see BuildWidgetForSensor), in the order
    of the tabs."""
    reportTabs = []
    for tabIndex in range(tabs.count()):
        tabWidget = tabs.widget(tabIndex)
        if getattr(tabWidget, "_reportData", None) is not None:
            reportTabs.append((tabs.tabText(tabIndex), tabWidget))
    return reportTabs


def GroupReportPaths(folder, reportFileNames):
    """The paths in *folder* of the reports of the interior orientation
    groups whose file names are *reportFileNames* (ReportFileName): each
    its own name, with _2, _3, and so on before the extension when an
    earlier report of the same export has that name already, in any case,
    as for two groups whose names differ only in characters that a file
    name cannot hold."""
    usedNames = set()
    paths = []
    for fileName in reportFileNames:
        nameStem, extension = os.path.splitext(fileName)
        candidateName, number = fileName, 1
        while candidateName.lower() in usedNames:
            number += 1
            candidateName = "{}_{}{}".format(nameStem, number, extension)
        usedNames.add(candidateName.lower())
        paths.append(os.path.join(folder, candidateName))
    return paths


def ReplaceReportsQuestionText(existingPaths):
    """The question before Export HTML of every group replaces the files
    *existingPaths*."""
    if len(existingPaths) == 1:
        return "The folder already holds {}.\n\nReplace it?".format(os.path.basename(existingPaths[0]))
    return "The folder already holds {:,} of these reports, among them {}.\n\nReplace them?".format(
        len(existingPaths), os.path.basename(existingPaths[0]))


def EveryGroupReportMessage(groupCount, writtenCount, failures, folder, wasCancelled):
    """The message after Export HTML of every group: how many of the
    *groupCount* reports were written to *folder*, whether the export was
    cancelled, and the groups whose report failed (*failures*, a list of
    (tab text, error))."""
    if wasCancelled:
        text = "Export HTML was cancelled after {:,} of {:,} reports.".format(writtenCount, groupCount)
    elif writtenCount == groupCount:
        text = "Export HTML wrote {:,} reports, one per interior orientation group.".format(groupCount)
    else:
        text = "Export HTML wrote {:,} of {:,} reports.".format(writtenCount, groupCount)
    if writtenCount:
        text += "\n\nFolder: {}".format(folder)
    if failures:
        text += "\n\nNot written: {}.".format(JoinWithAnd(
            ["{} ({})".format(tabText, errorText) for tabText, errorText in failures]))
    return text


def UpdateTotalMagnificationLabel(plotState):
    """Write the total magnification of the vector field shown, the
    automatic scale times the factor of the slider, into its label."""
    vectorWidget = plotState.vectorWidget
    totalMagnification = TotalMagnification(vectorWidget.vectors, plotState.pixelColumnCount,
                                            plotState.pixelRowCount, vectorWidget.userMagnification)
    plotState.totalMagnificationLabel.setText("Total magnification: " + FormatTotalMagnification(totalMagnification))


def RefreshDerivedViews(plotState):
    """Update the total magnification and the table of the vector fields
    after a change of the mode, the slider, or a definition."""
    UpdateTotalMagnificationLabel(plotState)
    if plotState.statisticsTable is not None:
        FillStatisticsTable(plotState.statisticsTable, plotState.StatisticsRows())


def RefreshVectorFieldOfMode(plotState, modeId):
    """Show the new data of *modeId* when that mode is shown, and update
    the derived views."""
    if plotState.currentMode == modeId:
        plotState.vectorWidget.SetData(*plotState.vectorDataByMode[modeId])
    RefreshDerivedViews(plotState)


def _OnVectorModeShown(plotState, buttonId):
    """Record the vector mode of the tab after _OnVectorModeChanged and
    update the derived views."""
    plotState.currentMode = buttonId
    RefreshDerivedViews(plotState)


def PrintFieldNumbers(plotState, modeId):
    """The RMS and the maximum of one vector field in the Console, after a
    change of its definition."""
    rms, maximum = FieldStatistics(plotState.vectorDataByMode.Get(modeId, ([],))[0])
    print("  [MetaFOCUS]   {}: {}".format(VECTOR_MODE_NAMES[modeId], "none" if rms is None else
                                          "RMS {} px, maximum {} px".format(FormatConsoleNumber(rms),
                                                                            FormatConsoleNumber(maximum))))


def ApplyDifferenceReference(plotState, itemData):
    """Compute the Difference against the item of the combo box
    Difference against with *itemData*: the initial calibration
    (Metashape's definition) or a reference calibration, with the same
    definition (ComputeGridDifference)."""
    if itemData == DIFFERENCE_AGAINST_INITIAL_CALIBRATION or itemData not in plotState.referenceCalibrationsByData:
        vectors, title, description = plotState.initialDifference, "Difference (adjusted − initial)", ""
        print(ViewerConsoleLine(plotState.groupName, "difference against the initial calibration"))
    else:
        description, referenceCalibration = plotState.referenceCalibrationsByData[itemData]
        try:
            vectors = ComputeGridDifference(plotState.calibration, referenceCalibration, plotState.pixelColumnCount,
                                            plotState.pixelRowCount, None, plotState.correctionAt)
        except Exception as suppressedError:
            ReportSuppressedError("interiorOrientationViewer.ApplyDifferenceReference", suppressedError)
            vectors = []
        title = "Difference (adjusted − reference)"
        print(ViewerConsoleLine(plotState.groupName, "difference against " + description))
    plotState.differenceDescription = description
    plotState.vectorDataByMode.dataByMode[4] = (vectors, title, None, None)
    PrintFieldNumbers(plotState, 4)
    differenceButton = plotState.vectorButtonGroup.button(4) if plotState.vectorButtonGroup is not None else None
    if differenceButton is not None:
        differenceButton.setEnabled(bool(vectors))
    RefreshVectorFieldOfMode(plotState, 4)


def LoadReferenceCalibrationItem(plotState):
    """Ask for a calibration file, load it, and add it to the combo box
    Difference against.  Returns the data of its item, or None when no
    file is chosen or it cannot serve as the reference (the Console and a
    message say why)."""
    path, _ = QtWidgets.QFileDialog.getOpenFileName(
        QtWidgets.QApplication.activeWindow(), "Reference calibration for the Difference", "",
        "Agisoft calibration XML (*.xml);;All files (*)")
    if not path:
        return None
    try:
        referenceCalibration = LoadCalibrationFile(path)
        problem = ReferenceCalibrationProblem(referenceCalibration, plotState.calibration)
    except Exception as loadError:
        problem = "it cannot be read ({})".format(DescribeException(loadError))
    if problem:
        message = "The calibration file {} cannot serve as the reference of the Difference: {}.".format(path, problem)
        print(ViewerConsoleLine(plotState.groupName, message[:-1]))
        QtWidgets.QMessageBox.warning(QtWidgets.QApplication.activeWindow(), "Interior Orientation Viewer", message)
        return None
    itemData = "loaded:" + path
    if plotState.differenceComboBox.findData(itemData) < 0:
        plotState.differenceComboBox.insertItem(plotState.differenceComboBox.count() - 1,
                                                "File " + os.path.basename(path), itemData)
        WidenComboBoxList(plotState.differenceComboBox)
    plotState.referenceCalibrationsByData[itemData] = ("the calibration file " + path, referenceCalibration)
    return itemData


def _OnDifferenceReferenceChosen(plotState, chosenIndex):
    """Apply the reference chosen in the combo box Difference against.
    Calibration file asks for a file and keeps the previous reference
    when none is chosen or it cannot serve."""
    itemData = plotState.differenceComboBox.itemData(chosenIndex)
    if itemData == DIFFERENCE_AGAINST_CALIBRATION_FILE:
        itemData = LoadReferenceCalibrationItem(plotState)
        if itemData is None:
            plotState.differenceComboBox.setCurrentIndex(plotState.differenceChoiceIndex)
            return
        chosenIndex = plotState.differenceComboBox.findData(itemData)
        plotState.differenceComboBox.setCurrentIndex(chosenIndex)
    plotState.differenceChoiceIndex = chosenIndex
    ApplyDifferenceReference(plotState, itemData)


def ApplyResidualStatistic(plotState, statistic):
    """Show the Residuals as the cell values of *statistic*."""
    residualField = plotState.ResidualField()
    if residualField.statistic == statistic:
        return
    residualField.statistic = statistic
    RefreshVectorFieldOfMode(plotState, 5)
    RefreshCoverage(plotState)


def _OnResidualStatisticChosen(plotState, chosenIndex):
    statistic = plotState.residualComboBox.itemData(chosenIndex)
    plotState.sharedPlotModes["residualStatistic"] = statistic
    print(ViewerConsoleLine(plotState.groupName, "residuals as " + ResidualStatisticWords(statistic)))
    ApplyResidualStatistic(plotState, statistic)


def ApplyRadialConvention(plotState, convention, zeroCrossingPercent, echo):
    """Show the radial distortion of the profile and of the Radial vector
    field as estimated (Gaussian) or balanced, with its zero crossing at
    *zeroCrossingPercent* per cent of the largest radius."""
    plotState.radialConvention, plotState.balancedZeroCrossingPercent = convention, zeroCrossingPercent
    radialVectors, radialTitle, zeroCrossingRadiusPixels = plotState.gaussianRadial, "Radial distortion", None
    if convention == RADIAL_CONVENTION_BALANCED and plotState.hasDistortionModel:
        zeroCrossingRadiusPixels = plotState.ZeroCrossingRadiusPixels()
        radialOffset = BalancedRadialOffset(plotState.calibration, zeroCrossingRadiusPixels)
        if radialOffset is not None:
            radialVectors = ComputeGridDistortion(plotState.calibration, plotState.pixelColumnCount,
                                                  plotState.pixelRowCount, plotState.correctionAt, radialOffset)[1]
            radialTitle = RadialProfileTitle(convention, zeroCrossingRadiusPixels)
    plotState.vectorDataByMode.dataByMode[1] = (radialVectors, radialTitle, None, None)
    UpdateRadialControlVisibility(plotState)
    if plotState.profileWidget is not None:
        plotState.profileWidget.SetRadialConvention(convention, zeroCrossingRadiusPixels)
    if echo:
        if zeroCrossingRadiusPixels is None:
            print(ViewerConsoleLine(plotState.groupName, "radial distortion Gaussian"))
        else:
            print(ViewerConsoleLine(plotState.groupName, "radial distortion balanced, {}".format(
                plotState.Definition(1)[len("Balanced, "):])))
        PrintFieldNumbers(plotState, 1)
    RefreshVectorFieldOfMode(plotState, 1)


def UpdateRadialControlVisibility(plotState):
    """Show the radial distortion type only in the views with the radial
    profile (Both and Radial), and its zero crossing only there and for
    the balanced radial distortion (Geert, 13 October 2026)."""
    radialShown = getattr(plotState, "profileModeId", 2) != 1
    for radialConventionWidget in getattr(plotState, "radialConventionWidgets", ()):
        radialConventionWidget.setVisible(radialShown)
    zeroCrossingShown = radialShown and getattr(plotState, "radialConvention", None) == RADIAL_CONVENTION_BALANCED
    for zeroCrossingWidget in getattr(plotState, "zeroCrossingWidgets", ()):
        zeroCrossingWidget.setVisible(zeroCrossingShown)


def _OnRadialConventionChosen(plotState, chosenIndex):
    convention = plotState.radialComboBox.itemData(chosenIndex)
    plotState.sharedPlotModes["radialConvention"] = convention
    ApplyRadialConvention(plotState, convention, plotState.balancedZeroCrossingPercent, echo=True)


def _OnZeroCrossingPercentChanged(plotState, zeroCrossingPercent):
    """Apply the share of the largest radius at which the balanced radial
    distortion is zero, echoed unless another tab set the shared
    value."""
    isUserChange = zeroCrossingPercent != plotState.sharedPlotModes.get(
        "balancedZeroCrossingPercent", DEFAULT_BALANCED_ZERO_CROSSING_PERCENT)
    plotState.sharedPlotModes["balancedZeroCrossingPercent"] = zeroCrossingPercent
    ApplyRadialConvention(plotState, plotState.radialConvention, zeroCrossingPercent,
                          echo=isUserChange and plotState.radialConvention == RADIAL_CONVENTION_BALANCED)


def SelectComboBoxData(comboBox, itemData):
    """Select the item of *comboBox* with *itemData*, without a signal of
    a choice of the user."""
    itemIndex = comboBox.findData(itemData)
    if isinstance(itemIndex, int) and itemIndex >= 0 and comboBox.currentIndex() != itemIndex:
        comboBox.setCurrentIndex(itemIndex)


def _ApplySharedPlotModesAndDefinitions(applySharedPlotModes, plotState):
    """Synchronise a tab with the shared state on a tab change: first the
    definitions of the residuals and of the radial distortion, then the
    plot modes (_ApplySharedPlotModes), the mode shown, and the derived
    views.  The reference of the Difference belongs to each tab."""
    statistic = plotState.sharedPlotModes.get("residualStatistic", RESIDUAL_STATISTIC_WEIGHTED_MEAN)
    SelectComboBoxData(plotState.residualComboBox, statistic)
    ApplyResidualStatistic(plotState, statistic)
    convention = plotState.sharedPlotModes.get("radialConvention", RADIAL_CONVENTION_GAUSSIAN)
    zeroCrossingPercent = plotState.sharedPlotModes.get("balancedZeroCrossingPercent",
                                                        DEFAULT_BALANCED_ZERO_CROSSING_PERCENT)
    if (convention, zeroCrossingPercent) != (plotState.radialConvention, plotState.balancedZeroCrossingPercent):
        SelectComboBoxData(plotState.radialComboBox, convention)
        if plotState.zeroCrossingSpinBox.value() != zeroCrossingPercent:
            plotState.zeroCrossingSpinBox.setValue(zeroCrossingPercent)
        ApplyRadialConvention(plotState, convention, zeroCrossingPercent, echo=False)
    applySharedPlotModes()
    for modeId in VECTOR_MODE_IDS:
        modeButton = plotState.vectorButtonGroup.button(modeId)
        if modeButton is not None and modeButton.isChecked():
            plotState.currentMode = modeId
    # The direction of the coverage by radius.
    if plotState.hasDistortionModel:
        ApplyCoverageDirection(plotState, CoverageDirection(plotState))
    RefreshDerivedViews(plotState)


def BuildDistortionPlotsForSensor(sensor, chunk,
                                  sharedPlotModes=None):
    """Build the lower panel with the switchable vector field and the
    distortion profiles.

    sharedPlotModes (a dict with the keys 'vector', 'profile',
    'vectorMagnificationSliderValue', 'decentringAngleDegrees',
    'residualStatistic', 'radialConvention', and
    'balancedZeroCrossingPercent') carries the vector and profile modes,
    the slider position, the decentring angle, and the definitions of the
    residuals and of the radial distortion across all interior
    orientation group tabs.  The reference of the Difference belongs to
    each tab.  The residual field of a large group is sampled
    systematically (RESIDUAL_SAMPLE_LIMIT) and collected when it is first
    shown (ResidualField). A group without the frame or fisheye model
    keeps only the residual field.  The panel carries its
    DistortionPlotState as _plotState, for the CSV export.
    """
    if sharedPlotModes is None:
        sharedPlotModes = {"vector": 0, "profile": 2}
    calibration = GetSensorCalibration(sensor)
    if calibration is None:
        return None
    pixelColumnCount = calibration.width
    pixelRowCount = calibration.height
    if not pixelColumnCount or not pixelRowCount:
        return None
    calibrationModel = CalibrationModel(calibration)
    # The distortion fields and profiles need the frame or fisheye model.
    # The residual field needs none, so spherical, cylindrical, and RPC
    # groups keep it.
    hasDistortionModel = calibrationModel in DISTORTION_CAMERA_MODELS

    # Precompute the vector fields of the distortion model, as
    # Metashape's Distortion Plot defines them (Project5, 8 October
    # 2026).
    groupName = InteriorOrientationGroupName(sensor, chunk)
    plotState = DistortionPlotState(sensor, chunk, calibration, groupName, pixelColumnCount, pixelRowCount,
                                    hasDistortionModel, sharedPlotModes)
    consoleLines = []
    correctionsEmptyText = NO_ADDITIONAL_CORRECTIONS_TEXT
    gridTotal, gridRadial, gridDecentring, gridCorrections, gridDifference = [], [], [], [], []
    rayReport = {}
    if hasDistortionModel:
        gridTotal, gridRadial, gridDecentring = ComputeGridDistortion(
            calibration, pixelColumnCount, pixelRowCount, rayReport=rayReport)
        correctionsCheck = CheckAdditionalCorrections(
            calibration, ComputeGridCorrections(gridTotal, gridRadial, gridDecentring))
        consoleLines += correctionsCheck.consoleLines
        correctionsEmptyText = correctionsCheck.emptyText
        plotState.correctionAt = correctionsCheck.correctionAt
        if correctionsCheck.correctionAt is not None:
            # The unprojection and the projection leave the additional
            # corrections out: the viewer adds them to both, so that the
            # Total, the Radial, the Decentring, and the Difference are
            # those of the whole model.
            gridTotal, gridRadial, gridDecentring = ComputeGridDistortion(
                calibration, pixelColumnCount, pixelRowCount, correctionsCheck.correctionAt, rayReport=rayReport)
            gridCorrections = ComputeGridCorrections(gridTotal, gridRadial, gridDecentring)
        else:
            gridCorrections = correctionsCheck.corrections
        # The rays of the grid points and where the radial distortion
        # polynomial reaches its largest distorted radius, also in the CSV
        # export and the report.
        plotState.rayLines = RayReportLines(rayReport, RadialDistortionMaximum(calibration, pixelColumnCount,
                                                                               pixelRowCount))
        consoleLines += plotState.rayLines
        # Difference = adjusted minus initial calibration, against the
        # initial calibration of the Camera Calibration dialog or the one
        # of Type Auto from the focal length and the photosite pitch set
        # for the group (InitialCalibrationForDifference).
        try:
            initialCalibration, initialPrincipalDistancePixels, initialAffinityPixels, initialDescription = (
                InitialCalibrationForDifference(sensor, calibration))
            plotState.initialDescription = initialDescription
            consoleLines.append("Difference: against " + initialDescription + ".")
            gridDifference = ComputeGridDifference(
                calibration, initialCalibration, pixelColumnCount, pixelRowCount,
                initialPrincipalDistancePixels, correctionsCheck.correctionAt, initialAffinityPixels)
        except Exception as suppressedError:
            ReportSuppressedError("interiorOrientationViewer.BuildDistortionPlotsForSensor.differenceGrid", suppressedError)
            gridDifference = []
    plotState.gaussianRadial, plotState.initialDifference = gridRadial, gridDifference
    # Metashape uses every 2D tie point, and only a very large group is
    # sampled systematically, so that every image still contributes.
    groupImages, _ = SelectGroupImages(sensor, chunk)
    samplingStride = ResidualSamplingStride(
        CountGroupProjections(groupImages, getattr(chunk, "tie_points", None)),
        RESIDUAL_SAMPLE_LIMIT)

    PrintVectorFieldNumbers(groupName, pixelColumnCount, pixelRowCount, hasDistortionModel,
                            dict(Total=gridTotal, Radial=gridRadial, Decentring=gridDecentring,
                                 Corrections=gridCorrections, Difference=gridDifference),
                            consoleLines, len(gridTotal) if hasDistortionModel else None)

    plotGroup = QtWidgets.QGroupBox("Distortion and residuals")
    # The layout that Geert approved on 14 October 2026, in a grid: two rows
    # of controls, the plot row of fixed height with the vector field and its
    # slider on the left and the profiles and the direction of the coverage
    # by radius on the right, and under them the table of the vector fields
    # beside the coverage by radius, which is as high as the table.
    groupGrid = QtWidgets.QGridLayout(plotGroup)
    groupGrid.setHorizontalSpacing(PLOT_GROUP_HORIZONTAL_SPACING)
    groupGrid.setVerticalSpacing(PLOT_GROUP_VERTICAL_SPACING)

    vectorWidget = VectorFieldWidget(pixelColumnCount, pixelRowCount)
    vectorWidget.setFixedHeight(PLOT_ROW_HEIGHT - SLIDER_RAISE)
    vectorWidget.setMinimumWidth(VectorFieldMinimumWidth(pixelColumnCount, pixelRowCount))
    plotState.vectorWidget = vectorWidget

    residualTitle = "Reprojection residuals, " + RESIDUAL_STATISTIC_CHOICES[0][2]
    if samplingStride > 1:
        residualTitle += ", sampled 1 in {:,}".format(samplingStride)
    # (vectors, title, RMS of the individual residuals, text without
    # vectors), the Residuals when they are first shown.
    vectorDataByMode = VectorDataByMode({
        0: (gridTotal, "Total distortion", None, None),
        1: (gridRadial, "Radial distortion", None, None),
        2: (gridDecentring, "Decentring distortion", None, None),
        3: (gridCorrections, "Additional corrections", None, correctionsEmptyText),
        4: (gridDifference, "Difference (adjusted − initial)", None, None),
    }, ResidualField(sensor, chunk, groupName, pixelColumnCount, pixelRowCount, samplingStride, residualTitle,
                     calibration if hasDistortionModel else None))
    plotState.vectorDataByMode = vectorDataByMode
    vectorDataByMode.residualField.onCollected = functools.partial(RefreshCoverage, plotState)
    # The shared definitions of the residuals and of the radial
    # distortion, which another tab may have changed already.
    vectorDataByMode.residualField.statistic = sharedPlotModes.get("residualStatistic",
                                                                   RESIDUAL_STATISTIC_WEIGHTED_MEAN)
    # The direction of the coverage by radius: a click in the Coverage map
    # of a frame or fisheye group sets it.
    if hasDistortionModel:
        vectorWidget.principalPoint = (pixelColumnCount * 0.5 + _Coefficient(calibration, "cx"),
                                       pixelRowCount * 0.5 + _Coefficient(calibration, "cy"))
        vectorWidget.coverageDirection = sharedPlotModes.get("coverageDirectionDegrees")
        vectorWidget.onCoverageClicked = functools.partial(_OnCoverageClicked, plotState)

    # Apply the shared vector mode (a disabled Difference falls back to
    # the first available mode, a group without a distortion model shows
    # its residuals).
    availableVectorModes = [modeId for modeId in VECTOR_MODE_IDS
                            if (hasDistortionModel or modeId in (5, COVERAGE_MODE_ID))
                            and (modeId != 4 or gridDifference)]
    initialVectorMode = ChooseAvailableVectorMode(
        sharedPlotModes.get("vector", 0), availableVectorModes)
    plotState.currentMode = initialVectorMode

    # Row 0, left: the vector modes.
    vectorModeRow, vectorModeLayout = ControlRow()
    vectorButtonGroup = QtWidgets.QButtonGroup(plotGroup)
    plotState.vectorButtonGroup = vectorButtonGroup
    modeLabels = [
        (0, "Total"), (1, "Radial"), (2, "Decentring"),
        (3, "Corrections"), (4, "Difference"), (5, "Residuals"), (COVERAGE_MODE_ID, "Coverage")]
    for modeId, label in modeLabels:
        radioButton = QtWidgets.QRadioButton(label)
        if modeId == initialVectorMode:
            radioButton.setChecked(True)
        if modeId == COVERAGE_MODE_ID:
            radioButton.setToolTip(CoverageModeToolTip(hasDistortionModel))
        if not hasDistortionModel and modeId not in (5, COVERAGE_MODE_ID):
            radioButton.setEnabled(False)
            radioButton.setToolTip("Available only for the frame and fisheye camera models.")
        # Disable Difference when the initial calibration could not be
        # evaluated.
        elif modeId == 4 and not gridDifference:
            radioButton.setEnabled(False)
            radioButton.setToolTip("Not available: the initial calibration could not "
                          "be evaluated (details in the Console).")
        vectorButtonGroup.addButton(radioButton, modeId)
        vectorModeLayout.addWidget(radioButton)
    vectorModeLayout.addStretch()
    groupGrid.addWidget(vectorModeRow, 0, 0)

    # Row 1, left: the reference of the Difference and the statistic of the
    # residuals (Metashape's definitions by default) side by side, in boxes
    # as wide as their longest standard item.
    optionsRow, optionsLayout = ControlRow()
    optionsLayout.addWidget(QtWidgets.QLabel("Difference against:"))
    differenceComboBox = QtWidgets.QComboBox()
    plotState.differenceComboBox = differenceComboBox
    differenceComboBox.addItem(DIFFERENCE_AGAINST_INITIAL_CALIBRATION_TEXT, DIFFERENCE_AGAINST_INITIAL_CALIBRATION)
    candidates = []
    if hasDistortionModel:
        candidates = ReferenceCalibrationCandidates(getattr(getattr(Metashape, "app", None), "document", None),
                                                    sensor, chunk)
    for candidateIndex, (candidateLabel, candidateCalibration) in enumerate(candidates):
        candidateData = "group:{}".format(candidateIndex)
        differenceComboBox.addItem(candidateLabel, candidateData)
        plotState.referenceCalibrationsByData[candidateData] = (candidateLabel, candidateCalibration)
    differenceComboBox.addItem(DIFFERENCE_AGAINST_CALIBRATION_FILE_TEXT, DIFFERENCE_AGAINST_CALIBRATION_FILE)
    differenceComboBox.setCurrentIndex(0)
    differenceComboBox.setToolTip(DIFFERENCE_AGAINST_TOOLTIP)
    differenceComboBox.setEnabled(hasDistortionModel)
    differenceComboBox.activated.connect(functools.partial(_OnDifferenceReferenceChosen, plotState))
    optionsLayout.addWidget(differenceComboBox)
    optionsLayout.addSpacing(OPTION_BOX_SPACING)
    optionsLayout.addWidget(QtWidgets.QLabel("Residuals:"))
    residualComboBox = QtWidgets.QComboBox()
    plotState.residualComboBox = residualComboBox
    for statistic, choiceText, statisticWords in RESIDUAL_STATISTIC_CHOICES:
        residualComboBox.addItem(choiceText, statistic)
    SelectComboBoxData(residualComboBox, vectorDataByMode.residualField.statistic)
    residualComboBox.setToolTip(RESIDUAL_STATISTIC_TOOLTIP)
    residualComboBox.activated.connect(functools.partial(_OnResidualStatisticChosen, plotState))
    optionsLayout.addWidget(residualComboBox)
    optionsLayout.addStretch()
    SizeOptionBoxes((differenceComboBox, residualComboBox), OptionBoxStandardTexts())
    groupGrid.addWidget(optionsRow, 1, 0)

    onVectorModeChanged = functools.partial(_OnVectorModeChanged, groupName, vectorDataByMode, sharedPlotModes,
                                            vectorWidget)
    onVectorModeShown = functools.partial(_OnVectorModeShown, plotState)

    try:
        vectorButtonGroup.idClicked.connect(onVectorModeChanged)
        vectorButtonGroup.idClicked.connect(onVectorModeShown)
    except AttributeError:
        vectorButtonGroup.buttonClicked[int].connect(onVectorModeChanged)
        vectorButtonGroup.buttonClicked[int].connect(onVectorModeShown)

    # Row 2, left: the vector field, SLIDER_RAISE less high than the
    # profiles, and under it the magnification slider, its factor, and the
    # total magnification.
    sliderRowWidget, sliderRow = ControlRow()
    sliderLabel = QtWidgets.QLabel("Vector scale:")
    sliderLabel.setToolTip(VECTOR_SCALE_TOOLTIP)
    sliderRow.addWidget(sliderLabel)
    magnificationSlider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
    magnificationSlider.setMinimum(MAGNIFICATION_SLIDER_MINIMUM)
    magnificationSlider.setMaximum(MAGNIFICATION_SLIDER_MAXIMUM)
    magnificationSlider.setValue(MAGNIFICATION_SLIDER_DEFAULT)  # 1.0×, the middle of the exponential scale
    magnificationSlider.setTickPosition(
        QtWidgets.QSlider.TicksBelow)
    magnificationSlider.setTickInterval(25)
    magnificationSlider.setToolTip(VECTOR_SCALE_TOOLTIP)
    magnificationSlider.setFixedWidth(MAGNIFICATION_SLIDER_WIDTH)
    # Apply shared slider value.
    initialSliderValue = sharedPlotModes.get("vectorMagnificationSliderValue", MAGNIFICATION_SLIDER_DEFAULT)
    magnificationSlider.setValue(initialSliderValue)
    initialMagnificationFactor = MagnificationForSliderPosition(initialSliderValue)
    vectorWidget.SetUserMagnification(initialMagnificationFactor)
    sliderValueLabel = QtWidgets.QLabel(FormatMagnification(initialMagnificationFactor))
    sliderValueLabel.setMinimumWidth(40)
    sliderValueLabel.setToolTip(VECTOR_SCALE_TOOLTIP)
    totalMagnificationLabel = QtWidgets.QLabel("Total magnification: n/a")
    totalMagnificationLabel.setToolTip(TOTAL_MAGNIFICATION_TOOLTIP)
    plotState.totalMagnificationLabel = totalMagnificationLabel
    sliderRow.addWidget(magnificationSlider)
    sliderRow.addWidget(sliderValueLabel)
    sliderRow.addSpacing(12)
    sliderRow.addWidget(totalMagnificationLabel)
    sliderRow.addStretch()

    onMagnificationSliderChanged = functools.partial(_OnMagnificationSliderChanged, groupName, magnificationSlider,
                                                     sharedPlotModes, sliderValueLabel, vectorWidget,
                                                     afterChange=functools.partial(RefreshDerivedViews, plotState))

    magnificationSlider.valueChanged.connect(onMagnificationSliderChanged)
    magnificationSliderState = {}
    magnificationSlider.sliderPressed.connect(functools.partial(
        _OnMagnificationSliderPressed, magnificationSliderState, magnificationSlider))
    magnificationSlider.sliderReleased.connect(functools.partial(
        _OnMagnificationSliderReleased, groupName, magnificationSlider, magnificationSliderState))
    leftColumn, leftColumnLayout = ColumnOfWidgets(PLOT_GROUP_VERTICAL_SPACING)
    leftColumnLayout.addWidget(vectorWidget)
    leftColumnLayout.addWidget(sliderRowWidget)
    groupGrid.addWidget(leftColumn, 2, 0, QtCore.Qt.AlignTop)

    # Table of the RMS and the maximum of every vector field.
    statisticsTable = QtWidgets.QTableWidget(0, len(STATISTICS_TABLE_HEADERS))
    statisticsTable.setHorizontalHeaderLabels(STATISTICS_TABLE_HEADER_LABELS)
    statisticsTable.verticalHeader().setVisible(False)
    statisticsTable.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
    statisticsTable.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
    statisticsTable.setToolTip(STATISTICS_TABLE_TOOLTIP)
    statisticsTable.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
    statisticsTable.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
    statisticsTable.setSizeAdjustPolicy(QtWidgets.QAbstractScrollArea.AdjustToContents)
    plotState.statisticsTable = statisticsTable

    # --- Right: profile curves with mode selector ---
    profileWidget = ProfilePlotWidget(
        calibration, pixelColumnCount, pixelRowCount)
    profileWidget.setFixedHeight(PLOT_ROW_HEIGHT)
    plotState.profileWidget = profileWidget

    # Apply the shared profile mode.
    initialProfileMode = sharedPlotModes.get("profile", 2)
    profileWidget.SetMode(initialProfileMode)

    # Row 0, right: the profile views.
    profileModeRow, profileModeLayout = ControlRow()
    profileButtonGroup = QtWidgets.QButtonGroup(plotGroup)
    for modeId, label in [(2, "Both"), (0, "Radial"),
                          (1, "Decentring")]:
        radioButton = QtWidgets.QRadioButton(label)
        if modeId == initialProfileMode:
            radioButton.setChecked(True)
        profileButtonGroup.addButton(radioButton, modeId)
        profileModeLayout.addWidget(radioButton)
    profileModeLayout.addStretch()

    # Row 1, right: the radial distortion as estimated (Metashape) or
    # balanced, with the share of the largest radius at which it is zero,
    # or in the decentring-only view the decentring controls in its place,
    # so that the plots stay where they are.
    radialRowWidget, radialRow = ControlRow()
    radialLabel = QtWidgets.QLabel("Radial distortion:")
    radialRow.addWidget(radialLabel)
    radialComboBox = QtWidgets.QComboBox()
    plotState.radialComboBox = radialComboBox
    plotState.radialConventionWidgets = (radialLabel, radialComboBox)
    for convention, choiceText in RADIAL_CONVENTION_CHOICES:
        radialComboBox.addItem(choiceText, convention)
    radialComboBox.setToolTip(RADIAL_CONVENTION_TOOLTIP)
    radialComboBox.setEnabled(hasDistortionModel)
    radialRow.addWidget(radialComboBox)
    zeroCrossingLabel = QtWidgets.QLabel("zero at")
    zeroCrossingSpinBox = QtWidgets.QSpinBox()
    zeroCrossingSpinBox.setRange(*BALANCED_ZERO_CROSSING_PERCENT_RANGE)
    zeroCrossingSpinBox.setSuffix(" %")
    zeroCrossingSpinBox.setKeyboardTracking(False)
    zeroCrossingSpinBox.setToolTip(BALANCED_ZERO_CROSSING_TOOLTIP)
    zeroCrossingSuffixLabel = QtWidgets.QLabel("of the largest radius")
    plotState.zeroCrossingSpinBox = zeroCrossingSpinBox
    plotState.zeroCrossingWidgets = (zeroCrossingLabel, zeroCrossingSpinBox, zeroCrossingSuffixLabel)
    radialRow.addWidget(zeroCrossingLabel)
    radialRow.addWidget(zeroCrossingSpinBox)
    radialRow.addWidget(zeroCrossingSuffixLabel)
    radialRow.addStretch()
    sharedConvention = sharedPlotModes.get("radialConvention", RADIAL_CONVENTION_GAUSSIAN)
    sharedZeroCrossingPercent = sharedPlotModes.get("balancedZeroCrossingPercent",
                                                    DEFAULT_BALANCED_ZERO_CROSSING_PERCENT)
    SelectComboBoxData(radialComboBox, sharedConvention)
    zeroCrossingSpinBox.setValue(sharedZeroCrossingPercent)
    radialComboBox.activated.connect(functools.partial(_OnRadialConventionChosen, plotState))
    zeroCrossingSpinBox.valueChanged.connect(functools.partial(_OnZeroCrossingPercentChanged, plotState))

    # Decentring evaluation angle, of the overlay Magnitude at the angle,
    # and the overlays of the decentring-only view.
    decentringRowWidget, decentringRow = ControlRow()
    angleLabel = QtWidgets.QLabel("Decentring angle:")
    decentringRow.addWidget(angleLabel)
    angleSpinBox = QtWidgets.QSpinBox()
    angleSpinBox.setRange(0, 359)
    angleSpinBox.setValue(
        int(sharedPlotModes.get("decentringAngleDegrees", 0)))
    angleSpinBox.setSuffix("°")
    semiDiagonalAngles = SemiDiagonalAnglesDegrees(pixelColumnCount, pixelRowCount)
    angleSpinBox.setToolTip(DecentringAngleToolTip(semiDiagonalAngles[0], _Coefficient(calibration, "p1"),
                                                   _Coefficient(calibration, "p2")))
    decentringRow.addWidget(angleSpinBox)
    decentringRow.addSpacing(8)
    # The angle shows only with the overlay that uses it, and keeps its
    # space while hidden, so that the check boxes after it do not move.
    RetainSpaceWhenHidden(angleLabel)
    RetainSpaceWhenHidden(angleSpinBox)

    onDecentringAngleChanged = functools.partial(_OnDecentringAngleChanged, groupName, profileWidget, sharedPlotModes)

    # A typed angle applies on Enter or when the field loses the focus,
    # so that typing 45 is one change, not 4 and then 45.
    angleSpinBox.setKeyboardTracking(False)
    angleSpinBox.valueChanged.connect(onDecentringAngleChanged)

    angleMagnitudeCheckBox = QtWidgets.QCheckBox("Magnitude at the angle")
    angleMagnitudeCheckBox.setToolTip(
        "Overlay the magnitude of the decentring distortion at the\n"
        "Decentring angle, between once and three times the profile\n"
        "function, depending on the angle. The Decentring angle is shown\n"
        "with it.\nOnly available in the decentring-only profile view.")
    semiDiagonalMeanCheckBox = QtWidgets.QCheckBox("Semi-diagonal mean")
    semiDiagonalMeanCheckBox.setToolTip(
        "Overlay the mean decentring magnitude over the four\n"
        "image semi-diagonals ({}).\nOnly available in the decentring-only "
        "profile view.".format(", ".join(
            "{:.1f}°".format(semiDiagonalAngle) for semiDiagonalAngle in semiDiagonalAngles)))
    azimuthMeanCheckBox = QtWidgets.QCheckBox("Azimuth mean")
    azimuthMeanCheckBox.setToolTip(
        "Overlay the decentring magnitude averaged over all\n"
        "azimuths (0 to 357° in steps of 3°).\nOnly available in the "
        "decentring-only profile view.")
    decentringRow.addWidget(angleMagnitudeCheckBox)
    decentringRow.addWidget(semiDiagonalMeanCheckBox)
    decentringRow.addWidget(azimuthMeanCheckBox)
    decentringRow.addStretch()
    angleMagnitudeCheckBox.toggled.connect(
        profileWidget.SetShowAngleMagnitude)
    semiDiagonalMeanCheckBox.toggled.connect(
        profileWidget.SetShowSemiDiagonalMean)
    azimuthMeanCheckBox.toggled.connect(
        profileWidget.SetShowAzimuthMean)
    angleMagnitudeCheckBox.clicked.connect(functools.partial(
        _OnAverageCurveClicked, groupName, "magnitude at the angle", angleMagnitudeCheckBox))
    semiDiagonalMeanCheckBox.clicked.connect(functools.partial(
        _OnAverageCurveClicked, groupName, "semi-diagonal mean", semiDiagonalMeanCheckBox))
    azimuthMeanCheckBox.clicked.connect(functools.partial(
        _OnAverageCurveClicked, groupName, "azimuth mean", azimuthMeanCheckBox))

    profileControlsStack = QtWidgets.QStackedWidget()
    profileControlsStack.addWidget(radialRowWidget)
    profileControlsStack.addWidget(decentringRowWidget)
    plotState.profileControlsStack = profileControlsStack

    updateAverageCurveVisibility = functools.partial(_UpdateAverageCurveVisibility, azimuthMeanCheckBox,
                                                     semiDiagonalMeanCheckBox,
                                                     angleMagnitudeCheckBox=angleMagnitudeCheckBox,
                                                     angleWidgets=(angleLabel, angleSpinBox), plotState=plotState)
    # The Decentring angle follows the overlay that uses it.
    angleMagnitudeCheckBox.toggled.connect(functools.partial(
        _OnAngleMagnitudeToggled, updateAverageCurveVisibility, plotState))
    onProfileModeChanged = functools.partial(_OnProfileModeChanged, groupName, profileWidget, sharedPlotModes,
                                             updateAverageCurveVisibility)
    try:
        profileButtonGroup.idClicked.connect(onProfileModeChanged)
    except AttributeError:
        profileButtonGroup.buttonClicked[int].connect(onProfileModeChanged)
    updateAverageCurveVisibility(initialProfileMode)

    # Row 2, right: the profiles, and under them, beside the slider row, the
    # direction of the coverage by radius.
    rightColumn, rightColumnLayout = ColumnOfWidgets(0)
    rightColumnLayout.addWidget(profileWidget)
    coverageWidget = None
    if hasDistortionModel:
        rightColumnLayout.addWidget(BuildDirectionRow(plotState, sliderRowWidget))
        coverageWidget = CoverageByRadiusWidget(pixelColumnCount, pixelRowCount)
        coverageWidget.setToolTip("")
        coverageWidget.linkedTable = statisticsTable
        coverageWidget.onFirstShown = functools.partial(CollectCoverageOfShownTab, plotState)
        plotState.coverageWidget = coverageWidget
    groupGrid.addWidget(profileModeRow, 0, 1)
    groupGrid.addWidget(profileControlsStack, 1, 1)
    groupGrid.addWidget(rightColumn, 2, 1, QtCore.Qt.AlignTop)
    if not hasDistortionModel:
        # No distortion profiles without a distortion model: the right
        # column stays hidden (its widgets keep the tab synchronisation
        # working).
        for rightWidget in (profileModeRow, profileControlsStack, rightColumn):
            rightWidget.setVisible(False)
        groupGrid.addWidget(QtWidgets.QLabel(
            "Distortion profiles are available only for the frame and fisheye camera models."), 2, 1,
            QtCore.Qt.AlignTop)

    # Rows 3 and 4: a little space under the slider, then the table of the
    # vector fields and beside it the coverage by radius, as high as the
    # table (the values of the profiles are in Export CSV and on the page of
    # Export HTML).
    groupGrid.setRowMinimumHeight(3, SLIDER_TABLE_EXTRA_SPACE)
    groupGrid.addWidget(statisticsTable, 4, 0, QtCore.Qt.AlignLeft | QtCore.Qt.AlignTop)
    if coverageWidget is not None:
        groupGrid.addWidget(coverageWidget, 4, 1, QtCore.Qt.AlignTop)
    groupGrid.setColumnStretch(1, 1)

    # The radial convention shared by the tabs (Gaussian by default), then
    # the vector field of the initial mode with its total magnification
    # and the table.
    ApplyRadialConvention(plotState, sharedConvention, sharedZeroCrossingPercent, echo=False)
    vectorWidget.SetData(*vectorDataByMode.Get(initialVectorMode, vectorDataByMode[0]))
    RefreshDerivedViews(plotState)
    FitTableToItsContents(statisticsTable)
    WrapDefinitionColumn(statisticsTable)
    RefreshCoverage(plotState)

    # Store the synchronisation callback for tab changes.
    applySharedPlotModes = functools.partial(_ApplySharedPlotModes, angleSpinBox, vectorDataByMode, magnificationSlider, profileButtonGroup, profileWidget, sharedPlotModes, vectorButtonGroup, vectorWidget, updateAverageCurveVisibility)

    plotGroup._applySharedModes = functools.partial(_ApplySharedPlotModesAndDefinitions, applySharedPlotModes,
                                                    plotState)
    plotGroup._plotState = plotState

    return plotGroup


_ABOUT_HTML = """
<h2>Interior Orientation Viewer</h2>
<p><b>Version:</b> 1.1.0 (2026-11-22)<br>
<b>Author:</b> Geert J. Verhoeven
(ORCID: <a href="https://orcid.org/0000-0003-4825-9604">
0000-0003-4825-9604</a>)<br>
<b>Contact:</b>
<a href="mailto:geert.verhoeven@univie.ac.at">
geert.verhoeven@univie.ac.at</a><br>
<b>Licence:</b> GNU General Public License, version 3 or any later version (GPL-3.0-or-later),
with an additional permission to convey MetaFOCUS combined with Agisoft Metashape and its
Python module (see LICENSE and ADDITIONAL_PERMISSION.txt in the MetaFOCUS ZIP file)</p>

<h3>Overview</h3>
<p>The Interior Orientation Viewer displays the interior orientation parameters of each interior
orientation group in the active chunk, together with statistical quality indicators derived from the
covariance matrix of the bundle adjustment. Plots of the distortion model and the reprojection
residuals accompany them. Together, these help to judge whether the camera model is well determined,
over-parameterised, or affected by correlated parameters.</p>
<p>Metashape shows the parameter estimates, their standard errors (SE), and the correlation matrix
in the <i>Camera Calibration</i> dialog, but not the test statistics against zero (estimate/SE),
their p-values, or the degrees of freedom (DoF). This tool adds them and shows everything in one
table per interior orientation group.</p>
<p>Every interior orientation group with at least one enabled, oriented image gets a tab, including
the group of each slave plane of a multi-camera rig. Keyframes in an animation track are not images
and are omitted. A group without a calibration covariance (a fixed calibration, or one that <i>Optimize
Cameras</i> has not adjusted) shows a message instead of the table, followed by its plots, which
need no covariance.</p>
<p>Each tab shows the parameter table with all its rows and columns, one line with the two estimated
DoF values, and the plots. It scrolls when the window is too small to show them all. The dialog
opens large enough for the whole table when the screen allows. <i>Show notes and statistics</i>
opens a panel on the right with the chunk statistics, the photosite pitch, the most correlated
parameter pairs, and the column notes. It also widens the window for the panel when there is room.
Closing the panel gives the window its width back. The panel is closed when the dialog opens, and
opening or closing it applies to every tab, as the plot modes do.</p>

<h3>Table columns</h3>
<p><b>Parameter estimate</b><br>
The interior orientation parameter as estimated by the bundle adjustment: c<sub>pix</sub>, cx, cy,
b1, and b2 in pixels, the radial and decentring coefficients k1&ndash;k4 and p1&ndash;p4
dimensionless. Estimates and SEs keep at least six significant digits (scientific notation for
values close to zero). Metashape&rsquo;s calibration attribute &lsquo;f&rsquo; holds the principal
distance in pixels, but this viewer writes <b>c<sub>pix</sub></b> to distinguish it from the nominal
focal length f of the lens. The principal distance c is the perpendicular distance from the
perspective centre to the image plane. For an image that keeps the sensor sampling, c<sub>pix</sub>
= c/p, where p is the photosite pitch. Strictly speaking, c<sub>pix</sub> is then the principal
distance divided by the photosite pitch, a number, and c<sub>pix</sub> = 5,000&nbsp;px means a
principal distance of 5,000 photosite pitches. Values of c<sub>pix</sub> nevertheless keep the label
&lsquo;px&rsquo;, as in Metashape and in photogrammetric practice, because &lsquo;px&rsquo; labels
values in pixel coordinates.</p>
<p><b>Note on b1 and b2:</b> b1 (affinity) and b2 (non-orthogonality) appear in the table only when
they were enabled in <i>Optimize Cameras</i>. If they are absent, they were not estimated. For most
modern digital cameras, b1 and b2 are negligible and are usually left disabled. Still, that is a
rule of thumb: a camera calibration, resampled images, or evidence of sensor or lens behaviour take
precedence.</p>

<p><b>Standard error (SE)</b><br>
The a posteriori standard deviation of the estimate, the square root of the diagonal element of the
covariance matrix. It is conditional on the weights of the observations, the parameters estimated,
the constraints, and the adjustment model. Its size should be judged by the displacement in pixels
that a change of the parameter by one SE causes across the image, and by the intended use, not by
its ratio to the estimate. How far the estimate lies from zero in SEs is a different question, which
estimate/SE answers. A large SE can come from a weak network geometry, few or uncertain
observations, or a strong correlation with other parameters.</p>

<p><b>estimate/SE</b><br>
The estimate divided by its SE: how many SEs the estimate lies away from zero. It is the test
statistic for the hypothesis that the parameter is zero, and that is all it tests. A parameter whose
true value is zero (for example a radial distortion coefficient of a nearly distortion-free lens)
should have a small estimate relative to its SE, and so a low |estimate/SE|. For the principal
distance in pixels c<sub>pix</sub>, and for any other parameter that cannot be zero, a very large
|estimate/SE| is to be expected. It says nothing about whether the parameter should be adjusted or
fixed at a calibrated value.</p>
<p>The estimate/SE and the p-value of a parameter with <b>|estimate/SE| &lt; 3</b> are shown in
bold: its estimate is not distinguishable from zero at that level (at |estimate/SE| = 3 the
two-sided p-value is 0.0027). This does <i>not</i> mean that the parameter is wrong. A low
|estimate/SE| has two different causes. Either the data do not contain enough information to
determine the parameter (a large SE), or the parameter is well determined but close to zero (a small
SE). In both cases, the bold values are a reason to compare adjustments with and without the
parameter, preferably with check points, not a rule to fix it. The test concerns a value of zero,
that is, dropping the term from the model. A fixed parameter keeps the value it is given, so fixing
it at a non-zero value is a different decision, which the test does not address. A simpler camera
model (fewer distortion terms) and a stronger network geometry (more convergent images, better
coverage, calibration targets) can be compared in the same way.</p>
<p>Conversely, a high |estimate/SE| does not show that a parameter is needed: a coefficient
distinguishable from zero can still be too small to matter, or can absorb an error of another part
of the model. The distortion profiles below show the displacement in pixels that each term
contributes across the image, which tells whether a term matters in practice. Each estimate/SE also
tests one parameter on its own, whereas strongly correlated distortion terms must be judged together
(see the correlation matrix). The camera model should be chosen by comparing adjustments with and
without the parameter, as in the workflow below, and preferably with check points.</p>

<p><b>p-value (two-sided)</b><br>
The two-sided p-value is the probability of an estimate at least as many SEs from zero as this one
if the true value of the parameter were zero. Under a normal approximation, p&nbsp;=&nbsp;2&nbsp;[1&nbsp;&minus;&nbsp;&Phi;(|estimate/SE|)],
with &Phi; the cumulative distribution function of the standard normal distribution. It is shown
with three significant digits, and values below 10<sup>&minus;300</sup> as &lt;&nbsp;1e-300. It is
conditional on the reported SEs and the adjustment model: it takes the SEs of Metashape as correct
and the adjustment model, linearised at the estimate, as valid. The value is not the probability
that the parameter is zero, nor the probability that the camera model is right, nor the coverage
probability of an uncertainty interval. A value of 0.001 means that an estimate at least this far
from zero would arise by chance in only 0.1&nbsp;% of cases if the true value were zero. The p-value
cell of c<sub>pix</sub> shows a dash, because a principal distance cannot be zero, so a test of it
against zero says nothing.</p>
<p><b>A z-statistic or a t-statistic?</b><br>
The ratio estimate/SE is the statistic of a test of the parameter against zero. With the SE taken as
known, from the a priori variance factor, it is the z-statistic of that test. Under the assumptions
given below, the z-statistic follows the standard normal distribution when the parameter is zero. F&ouml;rstner
and Wrobel (2016, p. 133, equation 4.318) write the test of an individual parameter as z = &#349;/&sigma;<sub>&#349;</sub>.
With an SE scaled by a variance factor estimated from the residuals, it is the t-statistic, which
follows Student&rsquo;s t distribution with the redundancy as its DoF. Metashape does not document
whether its calibration covariance contains an estimated variance factor, but checks support that it
does (see Tie Point Covariance Exporter), and then estimate/SE is the t-statistic. The viewer takes
the p-value from the standard normal distribution, a normal approximation. Even the z-statistic
follows the standard normal distribution exactly only for normally distributed observations with
correct weights and a correct, linear adjustment model, whereas the bundle adjustment only
linearises its model at the estimate. Student&rsquo;s t distribution approaches the normal
distribution as the redundancy grows. How close the two p-values are, however, depends on the
redundancy and on how far in the tail they lie. At |estimate/SE| = 3, Student&rsquo;s t distribution
gives 0.0301 with 5 DoF, 0.00341 with 100, and 0.00277 with 1,000, against 0.00270 from the normal
distribution. With the redundancy of a typical structure from motion (SfM) block, often hundreds of
thousands, the difference matters only for p-values far smaller than these. The p-values are
therefore approximate diagnostics, conditional on the SEs and the linearised model, not a check of
that model.</p>
<p>For <b>|estimate/SE| &lt; 3</b>, both columns show the parameter in bold, without a warning
colour, as explained above. For <b>|estimate/SE| &ge; 3</b>, the estimate is distinguishable from
zero, which supports keeping the term but does not by itself show that the term improves the
results. Neither case is a rule: each parameter should also be judged by its SE, its correlations,
and the effect of fixing it on the reprojection error and on the check points.</p>
<p>Note that the p-value is computed from estimate/SE, so the two columns carry the same
information, once as a distance from zero in SEs and once as a probability. Both are shown because
estimate/SE is the familiar statistic in the photogrammetric literature, while the p-value can be
compared directly with a chosen significance level.</p>

<p><b>Degrees of freedom (DoF)</b><br>
The redundancy of the adjustment: the number of observations minus the number of unknowns, plus the
datum defect, which is the number of datum parameters (translations, rotations, scale) that the
observations leave undetermined. The DoF says how many more observations there are than the minimum
needed to solve for all unknowns. Both estimated DoF values describe the network of the enabled
images of the chunk, not a single interior orientation group, and are the same in every tab.</p>
<p>They are <b>estimates from counts</b>, not from the rank of the adjustment, which Metashape does
not expose. They equal the redundancy, the number of scalar observations minus the rank of the
design matrix of the linearised adjustment, when that rank is the number of counted unknowns minus
the datum defect. That is the case when there is no rank deficiency beyond the datum defect and no
further constraint among the counted unknowns. They also rest on the following assumptions:</p>
<ul>
<li>The tie points connect all images into one block (two free blocks that share no tie points have
a datum defect of 14, not 7).</li>
<li>Three or more control points are not collinear (control points on one line leave the rotation
about that line free).</li>
<li>Every camera station has 6 exterior orientation unknowns and every interior orientation group
the adjusted parameters of its calibration.</li>
<li>The rolling-shutter unknowns are counted as Agisoft&rsquo;s support gave them for the two models
(see Rolling shutter below).</li>
</ul>
<p>Images that share one perspective centre, estimated relative orientations within a multi-camera
rig, and per-image calibration parameters change the numbers of exterior and interior orientation
unknowns but are not detected. Where these assumptions do not hold, the values can differ from the
actual redundancy. Only what takes part in the bundle adjustment is counted. An image counts only
when the observations of the estimate involve it: its 2D tie points in the adjustment, and for all
observations also its marker measurements. The counts take the enabled images. In a check in
Metashape 2.3.2, <i>Optimize Cameras</i> also adjusted a disabled image that was still oriented and
had 2D tie points or marker projections. The notes panel, Export CSV, and Export HTML name
such images, because the counts leave out their observations and unknowns. Enabling such an image,
or resetting its orientation, removes this difference. An unknown that no
observation involves adds one to the
unknowns and one to the rank defect, so it leaves the redundancy unchanged and is left out. An image
keeps its orientation when its tie points are removed, until the next <i>Optimize Cameras</i>. A
slave image of a multi-camera rig and an image with an imported orientation can also be oriented
without 2D tie points. The counts consist of the following parts:</p>
<ul>
<li><b>Tie points in the adjustment</b>: the valid tie points seen on at
least two enabled, oriented images. Each adds 2 observations per 2D
tie point on such an image and 3 unknowns. The 2D tie points of tie
points that were removed (for example by gradual selection) or never
triangulated add nothing.</li>
<li><b>Exterior orientation</b>: 6 unknowns per camera station with at
least one counted image (the slave images of a multi-camera rig follow
their master).</li>
<li><b>Interior orientation</b>: the adjusted parameters (those in the
calibration covariance) of every interior orientation group with at
least one counted image.</li>
<li><b>Rolling shutter</b>: 2 unknowns per counted image of an
interior orientation group with the Regularized rolling-shutter model
and 6 with the Full model. Agisoft&rsquo;s support wrote that the
Regularized model optimises &ldquo;only two parameters&rdquo; per image,
the shift along x and y in the image plane (Agisoft forum, 31 July 2022).
The same post gives six per image for the Full model, the shift along
and the rotation about three axes. The Metashape 2.3 manual does not give
these numbers.</li>
<li><b>Markers in the adjustment</b>: markers that are not fiducial
marks and are measured (pinned or unpinned) on at least two enabled,
oriented images, or on one when an enabled reference location (a
control point) supplies the missing observations. Only valid marker
projections count: a marker projection invalidated in the Marker
Residual Inspector is no longer a measurement. Each marker adds 2 observations
per measurement, 3 more for an enabled reference location, and 3
unknowns.</li>
<li><b>Metashape scale bars</b> with an enabled reference distance
between two points in the adjustment (markers in the adjustment, or
images of a camera station with a counted image): 1 observation
each.</li>
<li><b>Location and rotation priors</b> of the images of a camera
station with a counted image: 3 observations for an enabled reference
location and 3 for an enabled reference rotation. The priors of the
images of a station without a counted image would fix only the unknowns
of that station, so they are left out with them. The priors of a rig
master without measurements of its own count when an image of its
station is counted.</li>
</ul>
<p>From these parts, the viewer estimates two DoF values:</p>
<ul>
<li><b>Estimated DoF (tie points)</b> counts the tie-point network alone.
It is 2 &times; (2D tie points of the tie points in the adjustment) &minus;
(the tie-point unknowns and the exterior orientation, interior
orientation, and rolling-shutter unknowns of the images with such 2D
tie points) + 7. A tie-point network fixes no datum, so its datum
defect is 7 (for one connected block). The value is usually, but not
always, below the value for all observations. When the Gradual
Selection Filter leaves no 2D tie point out of its approximate
&sigma;&#8320;, its redundancy is this value.</li>
<li><b>Estimated DoF (all observations)</b> adds the marker, reference-distance,
and prior observations and the marker coordinates. Its datum defect
follows from the control that takes part in the adjustment. It ranges from 7
without any control down to 0 once control points, reference distances,
and rotation priors fix translation, rotation, and scale. One control
point leaves 4, two leave 1, and three or more that are not collinear
leave 0. Markers in the adjustment with an enabled reference location
count as control points, and so do the images with an enabled location
prior of a camera station with a counted image. The datum defect is read from these counts, not from
the geometry of the control. This is the estimate for the whole
adjustment, under the assumptions above.</li>
</ul>
<p>The panel that <i>Show notes and statistics</i> opens lists the oriented images and how many of
them have 2D tie points in the adjustment. It also lists the tie points in the adjustment, the
scalar observations, the unknowns with their parts, the datum defect used, and both estimated DoF
values. The line below the table repeats both. A high DoF (thousands or more) shows that there are
many more observations than unknowns, but not that every parameter is well determined. Weak image
geometry can leave single parameters, or combinations of them, poorly determined however many
observations there are. The SEs and the correlations show where that happens. A low DoF suggests
that too many parameters are estimated for the observations. Below the statistics, when the
photosite pitch is known, it is shown in micrometres (both pitches when they differ), with the
principal distance c, cx, and cy in millimetres. In Metashape&rsquo;s frame camera model (Metashape
manual, Appendix D), the attribute f scales the image y coordinate exactly, whereas the x coordinate
also carries the terms b1 and b2. Therefore, c = c<sub>pix</sub> &times; p<sub>y</sub> and cy are
converted with the vertical photosite pitch p<sub>y</sub>, and cx with the horizontal pitch p<sub>x</sub>.
For a binned or resampled image, these lengths hold only if Metashape&rsquo;s &lsquo;pixel size&rsquo;
of the group gives the distance on the sensor that 1 px of that image represents (see &lsquo;px&rsquo;
in the terminology).</p>

<h3>Correlation matrix</h3>
<p>The lower triangle shows the correlation coefficient &rho; between each pair of interior
orientation parameters (&rho; = 1 on the diagonal), with |&rho;| &ge; 0.90 in bold. Values close to
&plusmn;1 mean that the two parameters can largely compensate each other. A change in one can be
offset by a change in the other with little change in the residuals, so the adjustment separates
them only weakly. The five most strongly correlated pairs are listed in the panel that <i>Show notes
and statistics</i> opens.</p>
<p>High correlations are common between the following parameters:</p>
<ul>
<li><b>c<sub>pix</sub>&ndash;k1</b>: principal distance in pixels and first radial distortion
coefficient. Both affect the image scale in a similar way.</li>
<li><b>cx&ndash;p1</b> and <b>cy&ndash;p2</b>: principal point offsets and decentring distortion.
Both shift the image in the same direction.</li>
<li><b>k1&ndash;k2&ndash;k3</b>: higher-order radial terms compensate each other.</li>
</ul>
<p>A correlation of |&rho;| &ge; 0.90 is a reason to consider whether both parameters are needed, or
whether the network geometry should be strengthened (more convergent images, rolled images,
calibration targets).</p>

<h3>Practical workflow</h3>
<p>The practical workflow has five steps:</p>
<ol>
<li>Run <i>Optimize Cameras</i> in Metashape with the desired interior
orientation parameters enabled.</li>
<li>Open this viewer and check estimate/SE: a parameter with
|estimate/SE| &lt; 3 is not distinguishable from zero at that level,
which is a reason to compare adjustments with and without it, not a
rule to fix it.</li>
<li>Check the correlation matrix: if two parameters have
|&rho;| &ge; 0.90, consider fixing one of them.</li>
<li>Rerun <i>Optimize Cameras</i> with the simplified model.</li>
<li>Compare the root mean square (RMS) reprojection error before and after. A higher RMS shows that
the simpler model fits these observations less closely. However, it does not by itself show that the
removed parameter is needed: a more flexible model can fit the observations better without
predicting better. Where check points are available, compare their root mean square error (RMSE) as
well and prefer the simpler model when it does not raise the check-point RMSE. Check points used for
this choice are then no longer an independent final check.</li>
</ol>

<h3>Distortion and residual plots</h3>
<p>The lower panel shows a vector field on the left and the distortion profiles on the right, in a
row of fixed height, so that the plots do not grow with the window. The plot of the vector field
runs from the top of the radial profile to the bottom of the decentring profile, and the vector
scale lies under it. Under the plots lie the table of the vector fields and, beside it and as high
as it, the coverage by radius. The plot modes, the vector scale, the decentring angle, and the
direction of the coverage by radius are shared by all tabs. The vector field and the profiles follow
Metashape&rsquo;s Distortion Plot (Camera Calibration dialog), whose grid and most of whose
definitions Agisoft does not document. The definitions below reproduce every RMS and maximum of its
six modes, as Metashape&nbsp;2.3.2 shows them, for the three chunks of a project of 158 images of
6,016&nbsp;&times;&nbsp;4,016 pixels, from their adjusted calibrations and residuals. The viewer
also writes every value with six significant digits to the Console.</p>
<p><b>Vector field</b>, in six modes and the Coverage mode. Its grid has 60 columns of image cells
and as many rows as cells of the same width need, rounded up (41 for 6,016&nbsp;&times;&nbsp;4,016
pixels). The six modes draw one vector at the centre of each cell. The rule was established for
6,016&nbsp;&times;&nbsp;4,016 pixels and is assumed for other numbers of pixel columns and rows,
with at most 240 rows. The grid point is an observed position, in the distorted image, and each
vector starts there:</p>
<ul>
<li><b>Total</b>: the grid point minus the image position of the same ray with all lens distortion
coefficients set to zero and without additional corrections. That image position keeps the principal
distance, the principal point, and the affinity and non-orthogonality terms b1 and b2. The Total
therefore includes the additional corrections but not b1 and b2. The ray comes from Metashape&rsquo;s
own unprojection of the grid point. Metashape&rsquo;s unprojection appears to remove the lens
distortion step by step and to stop when the steps become small (this reproduces its round trips in
Metashape 2.3.2, and Agisoft does not document it). Where the distortion is severe, the ray
therefore need not project back onto the grid point. MetaFOCUS then refines it by Newton&rsquo;s
method until it does, and the Console says for how many grid points it missed by more than 0.001 px.
Metashape&rsquo;s Distortion Plot keeps its own rays, so its numbers can differ in the outermost
cells. A grid point without a ray is left out: one beyond 90&deg; from the camera axis of a fisheye,
for example, or any other grid point whose ray MetaFOCUS cannot find, and the Console counts them.
When image corners lie farther from the principal point than the largest distorted radius of the
radial distortion polynomial, the Console gives that radius. Beyond it, for the calibration of an
interior orientation group, Metashape 2.3.2 does not follow the polynomial but keeps the radial
factor of that radius. Its projection keeps growing, so these positions have rays. A calibration
that belongs to no interior orientation group followed the polynomial in a check in Metashape 2.3.2,
and a calibration file loaded for the Difference has not been checked. The Radial field and the
radial profile still follow the polynomial there, so that at these grid points the radial and
decentring terms need not explain the Total.</li>
<li><b>Radial</b> and <b>Decentring</b>: the displacements by the
k1&ndash;k4 and by the p1&ndash;p4 terms of that ray.</li>
<li><b>Corrections</b>: Metashape&rsquo;s additional corrections, which
<i>Optimize Cameras</i> estimates with <i>Fit additional
corrections</i>, as the part of the Total that the radial and decentring
terms do not explain. Without them the mode stays empty, as in
Metashape. When the calibration file holds Fourier corrections, the
Console also compares them with MetaFOCUS&rsquo;s own evaluation of
their model, which Agisoft does not document and which was identified
from that project. When Metashape&rsquo;s unprojection leaves them out,
the viewer evaluates them itself and adds them to the Total and the
Difference. Corrections of another kind, which MetaFOCUS cannot evaluate,
are shown only as far as the unprojection applies them, and the mode
and the Console say so otherwise.</li>
<li><b>Difference</b>: the adjusted minus the initial calibration. The grid point is the image of a
ray under the initial calibration, and the vector is its image under the adjusted calibration minus
the grid point. The initial calibration is the one set for the group in the <i>Camera Calibration</i>
dialog, or else that of Type &lsquo;Auto&rsquo;. The calibration of Type &lsquo;Auto&rsquo; has all
lens distortion coefficients set to zero and the principal point at the image centre. Its c<sub>pix</sub>
equals the focal length divided by the vertical photosite pitch, and its b1 equals the focal length
divided by the horizontal photosite pitch minus c<sub>pix</sub>. Both come from the values set for
the group, normally from the Exif data, as Metashape 2.3.2 sets them (b1 is zero when the two
pitches are equal). Without them the adjusted c<sub>pix</sub> stands in, and the Console says so.</li>
<li><b>Residuals</b>: the residuals of the 2D tie points of the valid tie points on the enabled,
oriented images of the group, each computed with the camera model of its image (Metashape&rsquo;s
Camera.error). Camera.error also applies the camera motion during the readout that Metashape
estimates for an image with rolling-shutter compensation. The residuals are averaged per grid cell
over all images, each residual weighted by the inverse of the keypoint size of its 2D tie point. The
cell vector is &Sigma;(<b>v</b><sub>i</sub>/s<sub>i</sub>)&nbsp;/&nbsp;&Sigma;(1/s<sub>i</sub>),
with <b>v</b><sub>i</sub> the residual in pixels and s<sub>i</sub> the keypoint size, so the cell
vector stays in pixels. These weights reproduce Metashape&rsquo;s plot. They are not the weights
that the adjustment gives the 2D tie points. Those weights follow from the a priori standard
deviation of each image coordinate, &sigma;<sub>t</sub>&nbsp;s<sub>i</sub>, the &lsquo;Tie point
accuracy&rsquo; &sigma;<sub>t</sub> times the keypoint size, so that they are proportional to 1/s<sub>i</sub><sup>2</sup>.
A residual without a positive keypoint size takes no part in the cell means, and the Console counts
it. Random errors average out, so the systematic part becomes visible. Opposing patterns of
different images average out as well, however, and a cell with few residuals is noisy. The RMS and
the maximum are those of the cell means, as in Metashape, and the RMS of the individual residuals
follows them. In a group with more than two million matched keypoints in the tie-point data of its
images, only 1 in N of the matched keypoints of every image is examined. The title says so, and the
numbers then differ from Metashape&rsquo;s. The residuals of a group are collected when its tab is
first shown. A group without the frame or fisheye model, which opens with its Residuals, collects
them when the dialog opens. An export collects them when they are not collected yet. A large group
takes a while, and the plots of the coverage by radius say so meanwhile. The Console then gives
their numbers.</li>
<li><b>Coverage</b>: instead of vectors, the number of 2D tie points of the Residuals per grid cell,
pooled over the images of the group. The more 2D tie points a cell holds, the darker it is, and a
cell without 2D tie points is tinted red. The darkest colour goes to the count that 95&nbsp;% of the
cells with 2D tie points do not exceed. The line under the plot gives the 2D tie points and the
cells without any, and the Console gives the same numbers. A legend under it gives the colours. A
click in the map chooses the direction of the coverage by radius. The map then shows that direction
as a line from the principal point to the border of the image format, with the edges of its wedge
dashed.</li>
</ul>
<p>As in Metashape, the vectors are coloured from blue (short) to red, which they reach at twice the
median length. The automatic scale also follows Metashape and draws the median vector about two
thirds of a cell long (0.68 of a cell). The <i>Vector scale</i> slider multiplies the automatic
scale from 1/20 to 20 times, and its factor, &times;1 in the middle, is shown beside it. The <b>Total
magnification</b>, the automatic scale times the factor of the slider, is the number of times a
drawn vector is longer than the displacement it shows, both in the pixel coordinates of the image as
drawn. It is shown beside the slider and above the plot. Metashape&rsquo;s plot gives one such
number per mode (for example &times;20 for the radial distortion of a test project), presumably also
the total magnification, rounded, which Agisoft does not document. With the factor of the slider at
&times;1, the vectors are drawn about as long as in Metashape&rsquo;s plot. A reference vector gives
a displacement in pixels. The RMS and the maximum under the plot have three significant digits, as
in Metashape.</p>
<p><b>Table of the vector fields.</b> Under the vector field, a table gives for every mode the RMS
and the maximum of the vectors, the number of vectors, and the definition used. The RMS and the
maximum are in pixels with three significant digits, as Metashape writes them. All six modes can
thus be compared with Metashape&rsquo;s Distortion Plot at once. The column of the maximum is headed
&lsquo;Max. (px)&rsquo;, and Export CSV writes &lsquo;Maximum (px)&rsquo;. The Residuals are
collected as the Residuals mode describes, and the table says so until then.</p>
<p><b>Coverage by radius.</b> Beside the table of the vector fields and as high as it, two plots are
drawn on the radius axis of the profiles. They show how the 2D tie points of the group spread over
the radial distance r of the profiles. Each ring is a tenth of the spacing of the radius ticks wide.
In the upper plot, a bar gives the relative density of a ring: the share of the 2D tie points in the
ring divided by the share of the image format in it. Both shares are taken of what has an r (see
below): of the 2D tie points with an r and of the part of the image format where r is found, not of
all of them. A relative density of 1, the dashed line, means an even spread. A ring in the image
format but without 2D tie points is tinted red. There the distortion polynomial is extrapolated, or
interpolated across a gap. The lower plot gives the radial residual of each ring with at least 10 2D
tie points. This is the component of the residuals along the direction away from the principal
point, summarised with the statistic of the option <i>Residuals</i>. The note above the lower plot
names that statistic and the sampling of a large group. When the window is narrow, this note and the
key of the red rings take a shorter form. The coverage is collected with the residuals, when the tab
is first shown.</p>
<p>The r of a 2D tie point is c<sub>pix</sub> times the radius of the
undistorted normalised point that the camera model distorts to its
position. The offset from the principal point is freed from b1 and b2,
and the radial distortion (k1 to k4) is inverted along the radius. The
decentring distortion (p1 to p4) is inverted as well,
by taking its displacement off and inverting the radial distortion again
until the point changes by less than 0.01&nbsp;px. Without it, r would
differ by up to 39&nbsp;px from that of Metashape&rsquo;s unprojection for
a group with p2&nbsp;=&nbsp;0.004. The additional corrections are left out. Where
these passes fail, Newton&rsquo;s method on the radial and decentring
distortion together takes over. The decentring distortion can carry a position
beyond the largest distorted radius of the radial polynomial while its
undistorted point lies within the radius at which the polynomial reaches
it. A 2D tie point for which no undistorted point is
found within that radius has no r, nor has one whose inversion does not
converge, and the Console counts both. Near that radius the decentring
distortion can also make the camera model distort two undistorted points
within it to the same position, so that r is that of the point that the
inversion finds. The image format is treated in the same way.</p>
<p><b>Direction.</b> A click in the Coverage map
chooses a direction from the principal point in whole degrees. It is
counted from the image x axis towards the image y axis, as the Decentring
angle, so that 90&deg; points down the image and 270&deg; up. Both plots
then show the part of each ring in a wedge of &plusmn;15&deg; around the
direction, and their titles name it. In the wedge, the relative density
is the share of the 2D tie points that lie in the part of the ring in the
wedge, divided by the share of the image format in it. Both shares stay
those of all directions: of the 2D tie points with an r and of the part
of the image format where r is found. An even spread therefore gives 1
in every direction. The 2D tie points and the image
format are kept per ring and per sector of one degree, so that a new
direction takes a few milliseconds. The share of the image format per ring
and sector comes from two rays per degree from the principal point, along
which r is found once per ring width. Where r along a ray is not linear,
as near the largest distorted radius, a stretch is halved until r at its
middle lies within 0.002 ring widths of the straight line, at most 14
times. The row under the profiles gives the
direction, and <i>All directions</i> returns to the whole rings. The
direction stays when another vector field is shown.</p>
<p><b>Values under the pointer.</b> Pointing at a plot shows its values
in a tooltip. On the vector field, it gives the grid point nearest to the
pointer, with the components and the length of its vector. On a
profile, it gives the radius under the pointer with the value of every curve
drawn there. In the Coverage mode it gives the
cell under the pointer with its 2D tie points. On
the coverage by radius it gives the ring under the pointer with its 2D tie
points and their share of all 2D tie points with an r. It also gives the
ring&rsquo;s share of the part of the image format where r is found, its
relative density, and its radial residual. It gives the direction and the sampling
of a large group as well, and it names what the shares are
taken of. The page of Export HTML shows the same
values in a box beside the pointer, with the names that the page gives its
plots and curves.</p>
<p><b>Export CSV</b>, at the bottom of the dialog, writes one CSV file for the interior orientation
group of the tab shown. Its comment lines start with &lsquo;#&rsquo;. They give the MetaFOCUS
version, the time of the export, the chunk, the group, its numbers of pixel columns and rows, the
definitions chosen, the notes on the rays of the grid points, and the units. The parameter table
follows, with the estimates and the SEs with fifteen significant digits, and estimate/SE and the
two-sided p-values with six. As in the table, c<sub>pix</sub> has no p-value, and a p-value below 10<sup>&minus;300</sup>
is written as &lt;&nbsp;1e-300. Then come the lower triangle of the correlation matrix, the two
estimated DoF values, and the table of the vector fields with six significant digits. The values of
the profiles and the table of the coverage by radius follow, also with six significant digits, and
comment lines define their columns. The table of the coverage by radius gives the rings of the
direction shown, which a comment line names. The export collects the residuals when they are not yet
collected. The option ‘Delimiter and decimal separator’ beside the button chooses a comma with
a decimal point, a semicolon with a decimal comma, or a tab with a decimal point. For a group
without a calibration covariance, a comment line says that the file has no parameter table, no
correlations, and no estimated DoF values.</p>
<p><b>Export HTML</b>, beside it, writes a report of the interior orientation group of the tab shown
as one HTML file, modelled on the camera calibration report of CameraCalibrator (Photometrix). The
page gives the project and the group, the parameter table with the correlations and the estimated
DoF values, and Metashape&rsquo;s camera model with its equations and the values of the group. Then
come the vector fields with their table, the radial and decentring distortion profiles with a table
of their values, and the notes on the rays of the grid points. The page also has the Coverage mode
with its legend, the two plots of the coverage by radius under the profiles, and the direction,
which it opens with as in the viewer. The page&rsquo;s table of the vector fields heads the maximum
&lsquo;Max. (px)&rsquo;. Pointing at a plot of the page shows the values under the pointer, as in
the viewer. The file holds its data and needs neither an internet connection nor further software. A
web browser draws the plots and offers the controls of the viewer. These controls set the vector
field, the vector scale, the statistic of the residuals, Gaussian or balanced radial distortion and
its zero crossing, the overlays of the decentring profile, and the decentring angle. A click in the
Coverage map and <i>All directions</i> set the direction. Printing the page, for example to a PDF
file, gives the plots as set, each section starting on a new page. For the coverage by radius, the
page holds the 2D tie points and the image format per ring and sector of one degree, which it sums
for every direction as the viewer does. The page also holds the medians of every ring in all
directions and in each of the 360 directions, because medians cannot be summed. The residuals are
collected for it when they are not yet, as for Export CSV, and MetaFOCUS opens the file in the web
browser.</p>
<p>With more than one interior orientation group that has a report, Export HTML first asks whether to
write the report of the group of the tab shown or one report for every group that has one. For every
group, it asks for a folder and writes one HTML file per group, each under the file name that Export
HTML proposes for that group. Two groups whose names give the same file name get _2, _3, and so on. Before it replaces
reports in the folder, it asks. A progress dialog shows the group being written and can cancel the
export. The Console names every file, a report that fails does not stop the others, and the folder
opens at the end when a report was written.</p>
<p>Spherical, cylindrical, and rational polynomial coefficient (RPC) groups have no distortion model
of this kind: they show only the residual field, the other modes are disabled, and the profile panel
is replaced by a note. For fisheye groups, the plots follow Metashape&rsquo;s equidistant fisheye
model (the camera types &lsquo;Fisheye&rsquo; and &lsquo;Equidistant fisheye&rsquo;) or its
equisolid model (&lsquo;Equisolid fisheye&rsquo;). In these models the undistorted radial distance r
from the principal point is c<sub>pix</sub>&nbsp;&theta; and 2c<sub>pix</sub>&nbsp;sin(&theta;/2)
respectively, with &theta; the angle from the camera axis (Metashape manual, Appendix D).</p>
<p><b>Distortion profiles</b>, as in Metashape&rsquo;s Profile tab: the radial distortion in pixels
against the radius from the principal point, positive for a barrel distortion (towards the principal
point) as in Metashape. The decentring distortion is shown as its profile function, &radic;(p1<sup>2</sup>&nbsp;+&nbsp;p2<sup>2</sup>)&nbsp;r<sup>2</sup>/c<sub>pix</sub>
with the radius r in pixels. When p3 or p4 is set, the profile function is multiplied by the
absolute value of 1&nbsp;+&nbsp;p3&nbsp;(r/c<sub>pix</sub>)<sup>2</sup>&nbsp;+&nbsp;p4&nbsp;(r/c<sub>pix</sub>)<sup>4</sup>.
The profile function is the smallest magnitude of the decentring distortion over all azimuths, and
in the direction of its greatest effect the magnitude is three times as large. The smallest
magnitude lies in the two directions perpendicular to atan2(p2,&nbsp;p1) from the image x axis, and
the largest along it. The tooltip of the <i>Decentring angle</i> gives these angles for the group.
This holds for the decentring terms converted to pixels with c<sub>pix</sub> alone, as the profiles
do: the affinity and non-orthogonality terms b1 and b2 can change the magnitudes in the image
slightly. Whether Metashape draws a pincushion distortion (away from the principal point) negative,
as the viewer does, has not been checked.</p>
<p>The profiles are drawn together (<i>Both</i>) or separately. The thick solid curves are the
totals (black for radial, deep purple for decentring). The dashed curves are the contributions of k1
(dark red), k2 (orange), k3 (brown), and k4 (crimson) and the profile functions of p1 (violet) and
p2 (blue-purple). In the decentring-only view three overlays are available: the magnitude at the <i>Decentring
angle</i> (vermilion), measured from the image x axis, the mean over the four image semi-diagonals
(bluish green), and the mean over all azimuths (black, dotted). The semi-diagonals lie at plus and
minus atan(pixel rows / pixel columns) from the x axis and in the opposite directions (45&deg; only
for as many pixel rows as pixel columns). These directions are parallel to the diagonals of the
image format, which point to its corners only when the principal point lies at the image centre. The
<i>Decentring angle</i> is shown only while the magnitude at the angle is shown, the only overlay
that uses it.</p>
<p>The key things to look for in the profiles are the following:</p>
<ul>
<li>If k3 or k4 contribute almost nothing (flat near zero), they are
candidates for fixing.</li>
<li>If the k1 and k2 curves nearly cancel each other, the question is whether both are needed or
whether a simpler model would suffice.</li>
<li>Decentring distortion is usually small for modern lenses (a rule of thumb, not a test). If it is
large, it should prompt a check for a decentred lens element or systematic errors in the network
geometry.</li>
</ul>
<p>The key things to look for in the residual field are the following (each pattern suggests
hypotheses to check, not diagnoses):</p>
<ul>
<li><b>Random scatter</b> (no spatial pattern): no sign of a systematic
error of the distortion model in this view. That does not prove the
model adequate: parts of the image without tie points show nothing,
and opposing patterns of different images can cancel in the cell
means.</li>
<li><b>Radial pattern</b> (vectors pointing outwards or inwards in rings): unmodelled radial
distortion. Another k term should be considered.</li>
<li><b>Systematic pattern in the corners</b>: the corners may not be
well covered by tie points, or the distortion model breaks down at
large radii.</li>
<li><b>Tangential streaks</b>: unmodelled decentring distortion or a
decentred lens.</li>
<li><b>Asymmetric pattern</b>: possibly a rolling-shutter effect (if
not compensated) or a lens defect away from the optical axis.</li>
</ul>

<h3>Other definitions (options)</h3>
<p>The values of the plots can be computed in more than one way. Metashape&rsquo;s definitions are
the default, and everything above follows them. Three options of each tab offer other definitions,
with Metashape&rsquo;s as the first item. Every choice is written to the Console and named in the
title of the plot, in the table of the vector fields, and in the CSV export. The choices of the
residuals and of the radial distortion apply to every tab, as the plot modes do, whereas the
reference of the Difference belongs to each tab. The three options are the following:</p>
<ul>
<li><b>Difference against</b>: the initial calibration, as in Metashape (the default), another
interior orientation group of the open project, or a calibration file in Agisoft&rsquo;s XML format
(<i>Calibration file&hellip;</i>). Such a group, for example the same camera in another chunk, has
the same numbers of pixel columns and rows. Such a file can be, for example, a laboratory
calibration or the calibration of another project saved with the Camera Calibration dialog of
Metashape. The definition stays that of Metashape: the grid point is the image of a ray under the
reference calibration, and the vector is the image of that ray under the adjusted calibration minus
the grid point. The vectors therefore show how far apart the two calibrations place the same ray in
the image. Judged against the SEs of both calibrations, they help to assess whether a calibration is
stable between projects. Additional corrections of a reference count only as far as Metashape&rsquo;s
unprojection applies them. Only groups whose calibration has the same numbers of pixel columns and
rows and a positive principal distance are listed. A calibration file with other numbers of pixel
columns and rows, or without a positive principal distance, is refused, and the Console and a
message say why.</li>
<li><b>Residuals</b>: cell means weighted by the inverse keypoint size,
as in Metashape (the default), cell means without weights, or cell medians.
In the cell means without weights, every residual counts equally and the residuals without a keypoint size
take part as well. The cell medians are the median of the x components and
the median of the y components of the residuals in each cell, which
single large residuals affect less than a mean. A cell median is not the median of
the vector lengths. The RMS of the individual residuals is the same for
all three, and the Console gives the numbers of all three when the
residuals are collected.</li>
<li><b>Radial distortion</b>: Gaussian, the radial distortion of the
estimated coefficients k1 to k4, as in Metashape (the default), or
balanced, a convention of photogrammetric calibration reports. The
balanced form adds the displacement proportional to the radius that
makes the radial distortion zero at the radius r<sub>0</sub>. By default, r<sub>0</sub> is
70&nbsp;% of the largest radius of the profile (half the diagonal of the
image format), which the option <i>zero at</i> sets. A displacement proportional to
the radius is a change of scale, so the balanced form describes the same
camera model with the principal distance in pixels
c<sub>pix</sub>&nbsp;(1&nbsp;+&nbsp;&Delta;r(r<sub>0</sub>)/r<sub>0</sub>).
Here &Delta;r(r<sub>0</sub>) is the Gaussian radial displacement at r<sub>0</sub>, negative towards the principal point (the profile draws it with the opposite sign). A barrel
distortion therefore gives a shorter principal distance. The profile shows r<sub>0</sub>
as a dashed line and this principal distance above the plot. The
balanced form changes the picture, not the geometry. The radial profile,
the contributions of k1 to k4 (each balanced at r<sub>0</sub>, so that
they still add up to the total), and the Radial vector field follow the
option. The Total, the Corrections, and the Difference, however, keep
Metashape&rsquo;s definitions. The option is shown only when the
profiles include the radial distortion (Both and Radial), and
<i>zero at</i> only for the balanced form, while the Radial vector field
keeps the form chosen.</li>
</ul>

<h3>References</h3>
<p>F&ouml;rstner, W. and Wrobel, B.P. (2016). <i>Photogrammetric
Computer Vision: Statistics, Geometry, Orientation and
Reconstruction</i>. Geometry and Computing 11. Springer, Cham.
<a href="https://doi.org/10.1007/978-3-319-11550-4">doi:10.1007/978-3-319-11550-4</a></p>
"""


def _EchoInteriorOrientationTab(tabs, tabIndex):
    """Echo the tab that the user shows, by a click, Ctrl+Tab, or the mouse
    wheel (the currentChanged signal, connected after all tabs were
    added, so the first tab shown is not echoed).  tabBarClicked is not
    used, because the press of any mouse button sends it, also on the tab
    already shown."""
    if tabIndex >= 0:
        print("Interior Orientation Viewer: tab {} shown.".format(tabs.tabText(tabIndex)))


def ApplySharedPlotChoices(tabWidget):
    """Give the plots of the tab *tabWidget* the choices that apply to every
    tab (the modes of the plots and the definitions of the residuals and
    of the radial distortion, see BuildDistortionPlotsForSensor), as
    showing the tab does.  Export HTML of every group does this before the
    report of each tab, so that a report of a tab that was never shown
    holds the same choices.  The reference of the Difference stays with
    each tab."""
    plotsWidget = getattr(tabWidget, '_plotsWidget', None)
    applySharedModes = getattr(plotsWidget, '_applySharedModes', None) if plotsWidget is not None else None
    if applySharedModes is not None:
        applySharedModes()


def _OnInteriorOrientationTabChanged(tabs, tabIndex):
    tabWidget = tabs.widget(tabIndex)
    ApplySharedPlotChoices(tabWidget)
    # The notes panel opens or closes as it was left on another tab.
    applySharedNotesPanelState = getattr(tabWidget, '_applySharedNotesPanelState', None)
    if applySharedNotesPanelState is not None:
        applySharedNotesPanelState()


class InteriorOrientationDialog(QtWidgets.QDialog):
    """Dialog with one tab per interior orientation group of the active chunk.

    Each tab shows the parameter table and the distortion and residual
    plots of its group, with the statistics and notes in a side panel.
    The dialog opens large enough for the whole table when the screen
    allows.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        chunk = Metashape.app.document.chunk
        chunkLabel = chunk.label if chunk else "no active chunk"
        self.setWindowTitle(
            "Interior Orientation Viewer: {}".format(chunkLabel))
        self.setWindowFlags(
            self.windowFlags()
            & ~QtCore.Qt.WindowContextHelpButtonHint)
        # Opening the viewer is one Console block (ConsoleBlockStartOfChunk),
        # with the lines that the tabs print while they are built.
        PrintConsoleLines(ConsoleBlockStartOfChunk("Interior Orientation Viewer", chunk))
        try:
            self._BuildUi()
        finally:
            PrintConsoleLines(ConsoleBlockEnd())
        self._ResizeToFitContents()

    def _ResizeToFitContents(self):
        """Open the dialog large enough for the whole parameter table and
        the plots of every tab (the size hint of each tab includes its
        whole page), within the available screen: on a smaller screen the
        pages scroll."""
        self.ensurePolished()
        neededSize = self.sizeHint()
        neededWidth, neededHeight = neededSize.width(), neededSize.height()
        if not (isinstance(neededWidth, int) and isinstance(neededHeight, int)):
            neededWidth, neededHeight = DEFAULT_DIALOG_WIDTH, DEFAULT_DIALOG_HEIGHT
        screenGeometry = AvailableScreenGeometry(self)
        availableWidth, availableHeight = (screenGeometry[2], screenGeometry[3]) if screenGeometry else (None, None)
        self.resize(*ChooseInitialDialogSize(neededWidth, neededHeight, availableWidth, availableHeight,
                                             ScrollBarExtent(self)))

    def _BuildUi(self):
        mainLayout = QtWidgets.QVBoxLayout(self)

        chunk = Metashape.app.document.chunk

        # Interior orientation groups with at least one enabled, oriented
        # image in the active chunk (slave images of a rig included), with
        # their image counts. Only these get a tab.
        sensorsWithImageCounts = []
        if chunk:
            for sensor in chunk.sensors:
                groupImages, _ = SelectGroupImages(sensor, chunk)
                if groupImages:
                    sensorsWithImageCounts.append((sensor, len(groupImages)))
        # The number of interior orientation groups of the chunk, also of
        # those without a tab, for the question of Export HTML
        # (ReportScopeQuestionText).
        self.chunkGroupCount = len(chunk.sensors) if chunk else 0

        # Plot settings shared by the tabs of all interior orientation
        # groups: a mode, magnification, or angle chosen on one tab carries
        # over to the others, and so does the notes panel, which is closed
        # when the dialog opens.
        sharedPlotModes = {"vector": 0, "profile": 2, "notesPanelVisible": False}

        tabs = QtWidgets.QTabWidget()
        tabs.setElideMode(QtCore.Qt.ElideNone)
        tabs.setUsesScrollButtons(True)
        # Qt sizes a tab with the font of the tab bar, not with the font of
        # the style sheet.  The tab bar therefore has the bold font of the
        # selected tab, so that its label fits, and the style sheet writes
        # the other tabs in the normal weight.
        boldTabFont = QtGui.QFont(tabs.tabBar().font())
        boldTabFont.setBold(True)
        tabs.tabBar().setFont(boldTabFont)
        tabs.setStyleSheet("""
            QTabBar::tab {
                color: #1a5294;
                font-weight: normal;
                padding: 5px 12px;
            }
            QTabBar::tab:selected {
                color: #0b3d91;
                font-weight: bold;
            }
        """)
        hasSensors = False
        # The observation and unknown counts describe the whole chunk:
        # computed once, not once per tab (each is a pass over every
        # tie-point projection).
        redundancyBreakdowns = (ComputeChunkRedundancyBreakdowns(chunk)
                                if sensorsWithImageCounts else None)
        for sensor, imageCount in sensorsWithImageCounts:
            tabLabel = "{} ({})".format(
                InteriorOrientationGroupName(sensor, chunk),
                FormatCountWithNoun(imageCount, "image", "images"))
            tabs.addTab(
                BuildWidgetForSensor(sensor, chunk, sharedPlotModes, redundancyBreakdowns),
                tabLabel)
            hasSensors = True

        self.tabs = tabs if hasSensors else None
        if not hasSensors:
            mainLayout.addWidget(QtWidgets.QLabel(
                "No interior orientation group in the active chunk has an enabled, oriented image."))
        else:
            # On a tab change, apply the shared plot settings to the new tab.
            onInteriorOrientationTabChanged = functools.partial(_OnInteriorOrientationTabChanged, tabs)

            tabs.currentChanged.connect(onInteriorOrientationTabChanged)
            # Every tab that the user shows is echoed: the tabs are all
            # added, so a change of the current tab now comes from the
            # user.
            tabs.currentChanged.connect(functools.partial(_EchoInteriorOrientationTab, tabs))
            mainLayout.addWidget(tabs)

        # --- Buttons ---
        buttonLayout = QtWidgets.QHBoxLayout()

        aboutButton = QtWidgets.QPushButton("About")
        aboutButton.clicked.connect(self._ShowAbout)
        buttonLayout.addWidget(aboutButton)

        # Export CSV of the tab shown, with the delimiter and the decimal
        # separator of the other exporters.
        buttonLayout.addSpacing(12)
        buttonLayout.addWidget(QtWidgets.QLabel(CSV_FORMAT_ROW_LABEL_TEXT))
        self.csvFormatComboBox = QtWidgets.QComboBox()
        FillCsvFormatComboBox(self.csvFormatComboBox, CSV_FORMAT_TOOLTIP_TEXT_OF_THE_VIEWER)
        buttonLayout.addWidget(self.csvFormatComboBox)
        exportCsvButton = QtWidgets.QPushButton("Export CSV…")
        exportCsvButton.setToolTip(
            "Write the parameter table, the correlations, the estimated degrees of freedom, the table of the\n"
            "vector fields, the values of the profiles, and the coverage by radius of the interior orientation\n"
            "group of the tab shown to a CSV file.")
        exportCsvButton.setEnabled(hasSensors)
        exportCsvButton.clicked.connect(self._ExportCsv)
        buttonLayout.addWidget(exportCsvButton)
        exportReportButton = QtWidgets.QPushButton("Export HTML…")
        exportReportButton.setToolTip(EXPORT_REPORT_TOOLTIP)
        exportReportButton.setEnabled(hasSensors)
        exportReportButton.clicked.connect(self._ExportReport)
        buttonLayout.addWidget(exportReportButton)

        buttonLayout.addStretch()

        closeButton = QtWidgets.QPushButton("Close")
        closeButton.clicked.connect(self.reject)
        buttonLayout.addWidget(closeButton)

        mainLayout.addLayout(buttonLayout)

    def _ExportCsv(self, *unusedArguments):
        """Export CSV: ask for a file and write the values of the tab
        shown (TabCsvContent), collecting its residuals when they are not
        collected yet.  A tab without values says so
        (NoReportOfTabShownText)."""
        if self.tabs is None:
            return
        tabWidget = self.tabs.currentWidget()
        csvContent = getattr(tabWidget, "_csvContent", None)
        if csvContent is None:
            QtWidgets.QMessageBox.information(self, "Interior Orientation Viewer", NoReportOfTabShownText(
                self.tabs.tabText(self.tabs.currentIndex()), "Export CSV"))
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Export CSV", getattr(tabWidget, "_csvFileName", "interiorOrientation.csv"),
            "CSV files (*.csv);;All files (*)")
        if not path:
            return
        chosenPath = path
        if not os.path.splitext(path)[1]:
            path += ".csv"
        if ReplacesUnconfirmedFile(chosenPath, path) and QtWidgets.QMessageBox.question(
                self, "Interior Orientation Viewer", ReplaceUnconfirmedFileQuestionText(path),
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.No) != QtWidgets.QMessageBox.Yes:
            print("Interior Orientation Viewer: Export CSV: " + DeclinedReplacingText(path))
            return
        csvDelimiter, decimalSeparator = ChosenCsvFormat(self.csvFormatComboBox)
        try:
            WriteInteriorOrientationCsv(path, csvContent(), csvDelimiter, decimalSeparator)
        except Exception as exportError:
            message = "The CSV file could not be written: {}".format(DescribeException(exportError))
            print("Interior Orientation Viewer: " + message)
            QtWidgets.QMessageBox.warning(self, "Interior Orientation Viewer", message)
            return
        print("Interior Orientation Viewer: CSV export of tab {} written to {} ({}).".format(
            self.tabs.tabText(self.tabs.currentIndex()), path, CsvFormatName(csvDelimiter, decimalSeparator)))

    def _ExportReport(self, *unusedArguments):
        """Export HTML.  With more than one interior orientation group that
        has a report, it first asks whether to write the report of the tab
        shown or one report per group (_AskReportScope), and only offers one
        report per group when the tab shown has no report.  Otherwise a tab
        without a report says so (NoReportOfTabShownText)."""
        if self.tabs is None:
            return
        reportTabs = ReportTabs(self.tabs)
        tabShownHasReport = getattr(self.tabs.currentWidget(), "_reportData", None) is not None
        if len(reportTabs) > 1:
            reportScope = self._AskReportScope(len(reportTabs), tabShownHasReport)
            if reportScope is None:
                return
            if reportScope == REPORT_SCOPE_EVERY_GROUP:
                self._ExportReportOfEveryGroup(reportTabs)
                return
        if not tabShownHasReport:
            QtWidgets.QMessageBox.information(self, "Interior Orientation Viewer", NoReportOfTabShownText(
                self.tabs.tabText(self.tabs.currentIndex()), "Export HTML"))
            return
        self._ExportReportOfTabShown()

    def _AskReportScope(self, groupCount, tabShownHasReport=True):
        """The choice of Export HTML with *groupCount* interior orientation
        groups that have a report (ReportScopeQuestionText, with the number
        of tabs and of the groups of the chunk): REPORT_SCOPE_TAB_SHOWN,
        REPORT_SCOPE_EVERY_GROUP, or None when it is cancelled.  The report
        of the tab shown is offered only when it has one
        (*tabShownHasReport*)."""
        messageBox = QtWidgets.QMessageBox(self)
        messageBox.setWindowTitle("Interior Orientation Viewer")
        messageBox.setText(ReportScopeQuestionText(groupCount, self.tabs.count(), self.chunkGroupCount,
                                                   tabShownHasReport))
        tabShownButton = (messageBox.addButton("Group of the tab shown", QtWidgets.QMessageBox.AcceptRole)
                          if tabShownHasReport else None)
        everyGroupButton = messageBox.addButton("Every group", QtWidgets.QMessageBox.AcceptRole)
        messageBox.addButton(QtWidgets.QMessageBox.Cancel)
        messageBox.exec_()
        clickedButton = messageBox.clickedButton()
        if tabShownButton is not None and clickedButton == tabShownButton:
            return REPORT_SCOPE_TAB_SHOWN
        if clickedButton == everyGroupButton:
            return REPORT_SCOPE_EVERY_GROUP
        return None

    def _ExportReportOfEveryGroup(self, reportTabs):
        """Export HTML of every group: ask for a folder, write the report of
        every tab of *reportTabs* (ReportTabs) under the name that Export
        HTML proposes for its group (GroupReportPaths), after a question
        when this replaces files, with a progress dialog that can cancel
        it.  Each tab first gets the choices of the plots that apply to
        every tab (ApplySharedPlotChoices).  A file that appears in the
        folder during the export, at a path that was free at the question,
        is kept, and its report is not written.  A report that fails does
        not stop the others.  The Console names every file, and the folder
        opens when a report was written."""
        document = Metashape.app.document
        documentPath = getattr(document, "path", "") if document is not None else ""
        folder = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Export HTML: folder of the reports",
            os.path.dirname(documentPath) if isinstance(documentPath, str) and documentPath else "")
        if not folder:
            return
        paths = GroupReportPaths(folder, [tabWidget._reportFileName for tabText, tabWidget in reportTabs])
        existingPaths = [path for path in paths if os.path.lexists(path)]
        if existingPaths and QtWidgets.QMessageBox.question(
                self, "Interior Orientation Viewer", ReplaceReportsQuestionText(existingPaths),
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.No) != QtWidgets.QMessageBox.Yes:
            print("Interior Orientation Viewer: Export HTML of every group not run: replacing {:,} {} in {} was "
                  "declined.".format(len(existingPaths), "report" if len(existingPaths) == 1 else "reports", folder))
            return
        progressDialog = QtWidgets.QProgressDialog("Writing the reports\u2026", "Cancel", 0, len(reportTabs), self)
        progressDialog.setWindowTitle("Interior Orientation Viewer")
        progressDialog.setWindowModality(QtCore.Qt.WindowModal)
        progressDialog.setMinimumDuration(0)
        writtenCount, failures, wasCancelled = 0, [], False
        for reportIndex, ((tabText, tabWidget), path) in enumerate(zip(reportTabs, paths)):
            progressDialog.setValue(reportIndex)
            progressDialog.setLabelText("Writing the report of {} ({:,} of {:,})\u2026".format(
                tabText, reportIndex + 1, len(reportTabs)))
            QtWidgets.QApplication.processEvents()
            if progressDialog.wasCanceled():
                wasCancelled = True
                break
            # A file that another program put into the folder after the
            # question is not replaced without a question.
            if path not in existingPaths and os.path.lexists(path):
                failures.append((tabText, APPEARED_REPORT_REASON_TEXT.format(os.path.basename(path))))
                print("Interior Orientation Viewer: the report of tab {} was not written: {}.".format(
                    tabText, APPEARED_REPORT_REASON_TEXT.format(path)))
                continue
            # The shared plot choices belong to the report of this tab, so
            # that a failure there stops only this report.
            try:
                ApplySharedPlotChoices(tabWidget)
                WriteReport(path, tabWidget._reportData())
            except Exception as exportError:
                failures.append((tabText, DescribeException(exportError)))
                print("Interior Orientation Viewer: the report of tab {} could not be written: {}".format(
                    tabText, DescribeException(exportError)))
                continue
            writtenCount += 1
            print("Interior Orientation Viewer: report of tab {} written to {}.".format(tabText, path))
        progressDialog.setValue(len(reportTabs))
        message = EveryGroupReportMessage(len(reportTabs), writtenCount, failures, folder, wasCancelled)
        print("Interior Orientation Viewer: " + " ".join(message.split()))
        if failures:
            QtWidgets.QMessageBox.warning(self, "Interior Orientation Viewer", message)
        else:
            QtWidgets.QMessageBox.information(self, "Interior Orientation Viewer", message)
        if writtenCount:
            try:
                QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(folder))
            except Exception as suppressedError:
                ReportSuppressedError("interiorOrientationViewer._ExportReportOfEveryGroup.open", suppressedError)

    def _ExportReportOfTabShown(self):
        """Export HTML of the tab shown: ask for a file, write the report of
        the tab (TabReportData, interiorOrientationReport.WriteReport), name
        it in the Console, and open it in the web browser."""
        tabWidget = self.tabs.currentWidget() if self.tabs is not None else None
        reportData = getattr(tabWidget, "_reportData", None)
        if reportData is None:
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Export HTML", getattr(tabWidget, "_reportFileName", "interiorOrientationReport.html"),
            "HTML files (*.html *.htm);;All files (*)")
        if not path:
            return
        chosenPath = path
        if not os.path.splitext(path)[1]:
            path += ".html"
        if ReplacesUnconfirmedFile(chosenPath, path) and QtWidgets.QMessageBox.question(
                self, "Interior Orientation Viewer", ReplaceUnconfirmedFileQuestionText(path),
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.No) != QtWidgets.QMessageBox.Yes:
            print("Interior Orientation Viewer: Export HTML: " + DeclinedReplacingText(path))
            return
        try:
            WriteReport(path, reportData())
        except Exception as exportError:
            message = "The report could not be written: {}".format(DescribeException(exportError))
            print("Interior Orientation Viewer: " + message)
            QtWidgets.QMessageBox.warning(self, "Interior Orientation Viewer", message)
            return
        print("Interior Orientation Viewer: report of tab {} written to {}.".format(
            self.tabs.tabText(self.tabs.currentIndex()), path))
        try:
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(path))
        except Exception as suppressedError:
            ReportSuppressedError("interiorOrientationViewer._ExportReport.open", suppressedError)

    def _ShowAbout(self):
        aboutDialog = QtWidgets.QDialog(self)
        aboutDialog.setWindowTitle(
            "About: Interior Orientation Viewer")
        aboutDialog.setWindowFlags(
            aboutDialog.windowFlags()
            & ~QtCore.Qt.WindowContextHelpButtonHint)
        aboutDialog.setMinimumSize(520, 520)
        layout = QtWidgets.QVBoxLayout(aboutDialog)
        textBrowser = QtWidgets.QTextBrowser()
        textBrowser.setOpenExternalLinks(True)
        textBrowser.setHtml(_ABOUT_HTML)
        layout.addWidget(textBrowser)
        closeButton = QtWidgets.QPushButton("Close")
        closeButton.clicked.connect(aboutDialog.accept)
        layout.addWidget(closeButton)
        aboutDialog.exec_()


# ===================================================================
# Menu registration
# ===================================================================

_MENU_LABEL = "MetaFOCUS/SfM Process Diagnostics/Interior Orientation Viewer"
_ICON_NAME = "interior_orientation_viewer_32.png"


def _ShowInteriorOrientationDialog():
    document = Metashape.app.document
    if not document or not document.chunk:
        QtWidgets.QMessageBox.information(
            QtWidgets.QApplication.activeWindow(), "Interior Orientation Viewer",
            "Open a project with at least one chunk first.")
        return
    chunk = document.chunk
    if not chunk.sensors:
        QtWidgets.QMessageBox.information(
            QtWidgets.QApplication.activeWindow(), "Interior Orientation Viewer",
            "The active chunk has no interior orientation groups.")
        return
    InteriorOrientationDialog(
        QtWidgets.QApplication.activeWindow()).exec_()


def RegisterMenuItem():
    """Register the menu item. Called by the MetaFOCUS loader."""
    try:
        Metashape.app.removeMenuItem(_MENU_LABEL)
    except Exception as suppressedError:
        ReportSuppressedError("interiorOrientationViewer.RegisterMenuItem", suppressedError)
    iconPath = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "icons", _ICON_NAME)
    if os.path.exists(iconPath):
        Metashape.app.addMenuItem(
            _MENU_LABEL, _ShowInteriorOrientationDialog, icon=iconPath)
    else:
        Metashape.app.addMenuItem(_MENU_LABEL, _ShowInteriorOrientationDialog)
