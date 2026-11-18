"""
SfM Log Parser for Agisoft Metashape Professional
===================================================

Parses the log of a Metashape structure-from-motion (SfM) run (the
Align Photos command), read from a log file (TXT, LOG, HTML) or pasted
from the clipboard, and renders a structured summary of the entire SfM
pipeline:

  - SfM parameters (downscale factor, keypoint limit, and others)
  - Keypoint detection totals and averages
  - Matching pipeline (raw → unique → geometrically consistent)
  - Track building (indices, generated tracks, cleanup)
  - Orientation estimation (the image pairs that the log reports, with
    their robust matches, and the triangulation steps before and during
    sigma filtering)
  - Final tie point counts (from the log and from the open chunk, with
    their difference)
  - Text flowchart of the SfM pipeline

A log that holds several Align Photos runs is split at every Align
Photos parameter line, and one run is summarised, after a list of all
runs: the run chosen in the list of runs above the tabs, by default the
last run with a final tie point count in the log.

Five graphs, drawn with QPainter on tabs next to the summary, show the
image pairs that the log reports, the growth of the 3D tie points during
image orientation, the keypoints per image against the keypoint limit,
and the pipeline funnel of the chosen run, and the runs of the log side
by side.  A sixth graph shows the shared 3D tie points of the open chunk
(SharedTiePointData).  Their values come from pure functions of a parsed
run or of the chunk (the section "Graphs: values"), separate from the
drawing.  With many images, the graphs show groups of consecutive
images, so that they never paint more cells or bars than they have room
for.  Each graph sits in a scroll area and can be copied or saved as a
PNG file (GraphPicture).

The summary and the sixth graph read the open chunk once (ReadOpenChunk)
and name it and its project.  The summary describes the log and compares
it with the open chunk, and the sixth graph shows the open chunk.  They warn when the selected run may belong to
another chunk or project (OpenChunkWarnings), and a check box leaves the
open chunk out, for comparing logs of other projects.

Author:      Geert J. Verhoeven (ORCID: 0000-0003-4825-9604)
Version:     1.0.0 (2026-11-18)
Date:        2026-03-24
Licence:     GPL-3.0-or-later WITH GPL-3.0-linking-exception
Contact:     geert.verhoeven@univie.ac.at
Requires:    Agisoft Metashape Professional 2.x+
Developed for: Agisoft Metashape Professional 2.3.2 (tested offline, see README.md)

Usage:
    Loaded automatically by the MetaFOCUS loader.
    MetaFOCUS > SfM Process Diagnostics > SfM Log Parser opens the
    dialog.
"""

import Metashape
import collections
import functools
import itertools
import operator
import re
import os
import html
import math
import time

from PySide2 import QtWidgets, QtCore, QtGui
from .photogrammetryUtilities import (ConsoleBlockEnd, ConsoleBlockStart, ConsoleLabelledLine, DescribeException,
                                     FormatCountWithNoun, IsKeyframe, IsOrientedEnabledCamera, JoinWithAnd,
                                     PrintConsoleLines, ReportSuppressedError)


# ===================================================================
# Regex patterns
# ===================================================================

# Keypoint detection lines “photo N: X points”, with or without a device
# tag (“[GPU]”, “[GPU 1]”, “[CPU]”) and an optional “[NNN strong]”.  N is
# the number of the image in the log, X its keypoints.
KEYPOINT_DETECTION_PATTERN        = re.compile(r'(?:\[(?:GPU|CPU)[^\]]*\]\s*)?\bphoto\s+(?P<imageNumber>\d+)\s*:\s*(?P<keypointCount>\d+)(?:\s*\[(?P<strongKeypointCount>\d+)\s*strong\])?\s*points', re.I)

# Parameter line of the SfM run: “AlignPhotos: key = value, …”
ALIGN_PARAMETERS_PATTERN     = re.compile(r'AlignPhotos:\s*(.*)$', re.I)

# Parameter line of image matching started from Python
# (Chunk.matchPhotos), “MatchPhotos: key = value, …”, which comes without
# an AlignPhotos: line (observed in Metashape 2.3.2).  It does not start
# a run (RunsWithSeveralMatchings).
MATCH_PHOTOS_PARAMETERS_PATTERN = re.compile(r'\bMatchPhotos:', re.I)

# “NNN matches found”
MATCHES_FOUND_PATTERN    = re.compile(r'(\d+)\s+matches\s+found', re.I)
# “NNN guided matches found”
GUIDED_MATCHES_FOUND_PATTERN     = re.compile(r'(\d+)\s+guided\s+matches\s+found', re.I)

# “filtered X out of Y matches”
FILTERED_PATTERN         = re.compile(r'filtered\s+(\d+)\s+out of\s+(\d+)\s+matches', re.I)

SETTING_INDICES_PATTERN  = re.compile(r'setting point indices\.\.\.\s*(\d+)', re.I)
GENERATED_TIE_POINTS_PATTERN    = re.compile(r'generated\s+(\d+)\s+tie points,\s*([0-9.]+)\s+average projections', re.I)
REMOVED_MULTIPLE_INDICES_PATTERN    = re.compile(r'removed\s+(\d+)\s+multiple indices', re.I)
REMOVED_TRACKS_PATTERN   = re.compile(r'removed\s+(\d+)\s+tracks', re.I)
SELECTED_TRACKS_PATTERN  = re.compile(r'selected\s+(\d+)\s+tracks out of\s+(\d+)', re.I)

# “pair i and j: K robust [from M]”
PAIR_ROBUST_PATTERN      = re.compile(r'pair\s+(\d+)\s+and\s+(\d+)\s*:\s*(\d+)\s+robust(?:\s+from\s+(\d+))?', re.I)

SIGMA_START_PATTERN      = re.compile(r'\bsigma filtering\b', re.I)
ADDING_POINTS_PATTERN    = re.compile(r'adding\s+(\d+)\s+points(?:,\s*(\d+)\s+far)?', re.I)

# “pt: (total, valid, weak)”
POINT_TUPLE_PATTERN         = re.compile(r'pt:\s*\((\d+)\s*,\s*(\d+)\s*,\s*(\d+)\)', re.I)

# “block: S sensors, C cameras, P points, R projections”
BLOCK_PATTERN            = re.compile(r'block:\s+\d+\s+sensors,\s+(\d+)\s+cameras,\s+(\d+)\s+points,\s+(\d+)\s+projections', re.I)

# “entities: cm: (total_images, oriented, 0)”
CAMERA_ENTITIES_PATTERN      = re.compile(r'entities:\s*cm:\s*\((\d+)\s*,\s*(\d+)\s*,\s*(\d+)\)', re.I)

# Timestamp at the start of a Console line, “2026-10-06 10:00:00”
LOG_TIMESTAMP_PATTERN    = re.compile(r'^\s*(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})')

# A line that loads or saves a project, “LoadProject: path = C:/projects/test.psx” or “SaveProject: path = …”.
# The path is the value of the first argument named path (the only one in every log of Metashape 2.3.2 seen).
# It ends at the last extension .psx or .psz of the line that a comma or the end of the line follows, after
# optional white space or a closing quotation mark.  A path may thus hold a comma, an equals sign, and a
# folder whose name holds “.psx,” or “, path =”, and a further “, key = value” stays out of it unless its
# value also ends in .psx or .psz.  Quotation marks around the whole path are not part of it.
PROJECT_PATH_PATTERN     = re.compile(
    r'\b(?P<projectAction>Load|Save)Project:(?:.*?,)??\s*path\s*=\s*(?P<quote>["\']?)'
    r'(?P<projectPath>.*\.ps[xz])(?P=quote)\s*(?=,|$)', re.I)
# A path without the extension of a project runs to the end of the line, or to a further “, key = value”.
PROJECT_PATH_FALLBACK_PATTERN = re.compile(
    r'\b(?P<projectAction>Load|Save)Project:(?:.*?,)?\s*path\s*=\s*(?P<projectPath>.*?)\s*(?=,\s*[A-Za-z_]\w*\s*=|$)', re.I)

# -------- Parameter labels (Metashape parameter key → descriptive label) --------
PARAMETER_LABELS = {
    "downscale":               "Accuracy setting of Align Photos as image downscale factor (0 = Highest, 1 = High, 2 = Medium, 4 = Low, 8 = Lowest)",
    "generic_preselection":    "Generic preselection (image pairs found first by matching downscaled images, at a lower Accuracy setting)",
    "reference_preselection":  "Reference preselection (image pairs found from the image positions)",
    "filter_mask":             "Filter points by masks",
    "mask_tiepoints":          "Apply masks to tie points (otherwise to keypoints)",
    "filter_stationary_points":"Exclude stationary tie points",
    "keypoint_limit":          "Keypoint limit per image",
    "keypoint_limit_per_mpx":  "Keypoint limit per megapixel (used with guided image matching)",
    "tiepoint_limit":          "Tie point limit per image",
    "keep_keypoints":          "Keep keypoints in the project (for adding images to the SfM solution later)",
    "guided_matching":         "Guided image matching (more keypoints per image without much extra matching time)",
    "adaptive_fitting":        "Adaptive camera model fitting (automatically selects the interior orientation parameters to adjust, to avoid over-parameterisation)",
    # Further matchPhotos and alignCameras parameters of the 2.x API.
    "downscale_3d":            "Accuracy setting for laser scans (downscale factor)",
    "keypoint_limit_3d":       "Keypoint limit per laser scan",
    "reference_preselection_mode": "Reference preselection mode",
    "reset_matches":           "Reset existing matches",
    "subdivide_task":          "Fine-level task subdivision",
    "workitem_size_cameras":   "Images per work item",
    "workitem_size_pairs":     "Image pairs per work item",
    "max_workgroup_size":      "Maximum work group size",
    "laser_scans_vertical_axis": "Vertical axis of laser scans",
    "laser_scans_use_initial_orientation": "Use the initial orientation of laser scans",
    "match_laser_scans":       "Match laser scans",
    "min_image":               "Minimum number of 2D tie points per tie point",
    "reset_alignment":         "Reset the existing image orientations",
}

# -------- Text normaliser (handles HTML or plain) --------
def NormaliseText(rawLogText, hintPath=None):
    """Plain text of a log supplied as plain text or as HTML, with Unix
    line endings.  The input is treated as HTML when *hintPath* (the
    file name, if any) ends in .html or .htm, or when it contains HTML
    tags."""
    text = rawLogText
    isHtml = False
    if hintPath and os.path.splitext(hintPath)[1].lower() in (".html", ".htm"):
        isHtml = True
    if ("<html" in text.lower()) or ("<br" in text.lower()) or ("</div>" in text.lower()):
        isHtml = True
    if isHtml:
        text = re.sub(r'(?i)<br\s*/?>', "\n", text)
        # The end of every block element is the end of a line, so that a
        # log whose lines are div elements keeps its lines.
        text = re.sub(r'(?i)</(?:p|div|li|tr|pre|h[1-6])\s*>', "\n", text)
        text = re.sub(r'<[^>]+>', '', text)
        text = html.unescape(text)
    return text.replace("\r\n", "\n").replace("\r", "\n")

# -------- Formatting helpers of the summary --------
def _FormatNumber(value):
    return "n/a" if value is None else f"{value:,}"


def _FormatFixedDecimals(value, decimals=1):
    if value is None: return "n/a"
    try: return f"{value:.{decimals}f}"
    except Exception: return "n/a"


def _FormatFixedDecimalsWithThousands(value, decimals=1):
    if value is None: return "n/a"
    try: return f"{value:,.{decimals}f}"
    except Exception: return "n/a"


def _SectionHeadingHtml(title):
    return f'<div class="sec">{html.escape(title)}</div>'


def _SummaryRowHtml(label, value, note=""):
    labelHtml = f'<span class="lab">{html.escape(label)}</span>'
    valueHtml = f'<span class="val"><b>{html.escape(value)}</b></span>'
    noteHtml = f' <span class="note">{html.escape(note)}</span>' if note else ""
    return f'<div class="row">{labelHtml} {valueHtml}{noteHtml}</div>'


def ParseSfmRunLines(runLines):
    """Counts of one SfM run, read from its log lines (plain text), as a
    dictionary whose keys are the names of the accumulators below.  A
    count that no line gives stays None (or an empty list).
    "firstSfmLineIndex" is the 0-based index of the first line after which
    the counts hold an SfM line (IsAnySfmCountSet, as HasSfmLines), or None
    without one, so that FirstSfmLineNumber need not parse the lines
    again."""
    # accumulators
    interestPointCounts = []
    # image number of each detection line, in the order of
    # interestPointCounts (graph of the keypoints per image)
    keypointImageNumbers = []
    alignmentParameterPairs = None  # list[(key, value)] from AlignPhotos

    tentativeTotal = None        # raw + guided, None when no line matched
    # Sums across phases, None when no line matched, so that an explicit
    # zero ("filtered 0 out of 0 matches") stays a count.
    filteredOut = None
    filteredBase = None          # “out of Y matches”

    indexedMatchCount = None
    generatedTiepoints = None
    averageProjection = None
    removedMultipleIndexCount = None     # None when no line matched
    removedTracksCount = None    # None when no line matched
    selectedTracks = None
    selectedOutOf = None
    totalProjections = None
    blockCameraCount = None
    lastCameraEntityCounts = None

    # Pairwise robust entries without duplicates, preferring the
    # “robust from …” form of a pair
    pairRobustMap = {}

    addedPointCountsBeforeSigmaFiltering, addedPointCountsDuringSigmaFiltering = [], []
    farPointCountsDuringSigmaFiltering = []
    farPointCountsBeforeSigmaFiltering = []
    # points of the "adding" lines since the last "pt:" tuple, before and
    # during sigma filtering
    pointsAddedAfterLastTuple = []
    pointsAddedAfterLastTupleDuringSigmaFiltering = []
    # The far points of the "adding" lines since the last "pt:" tuple
    # during sigma filtering, and whether every step of the current block
    # of sigma filtering (from its "sigma filtering" line) obeys valid
    # after = valid before + added - far, which shows that Metashape
    # prints the "pt:" tuple of a step before the line that rejects its
    # far points (a log of Metashape 2.3.2).
    farPointsAfterLastTupleDuringSigmaFiltering = []
    sigmaBlockPreviousValid = None
    sigmaBlockStepCount = 0
    sigmaBlockConsistent = True
    validPointCountBeforeSigmaFiltering = None
    finalPointTotal = finalPointValid = None
    sigmaStarted = False
    # The orientation lines in their order, for the graph of the growth
    # of the 3D tie points: every "adding" line is a triangulation step
    # (added points, far points or None, during sigma filtering), every
    # "pt:" tuple is placed after the steps before it (number of steps
    # before it, total, valid, weak, during sigma filtering), and sigma
    # filtering starts after sigmaFilteringStepCount steps.
    triangulationSteps = []
    pointTupleSteps = []
    sigmaFilteringStepCount = None
    firstSfmLineIndex = None

    # parse lines
    for lineIndex, logLine in enumerate(runLines):
        # The counts are checked before each line until they hold an SfM
        # line, which is then the line before (the counts only grow).
        if firstSfmLineIndex is None and lineIndex > 0 and IsAnySfmCountSet(
                alignmentParameterPairs, interestPointCounts, tentativeTotal, filteredBase, indexedMatchCount,
                generatedTiepoints, removedMultipleIndexCount, removedTracksCount, selectedTracks,
                totalProjections, lastCameraEntityCounts, pairRobustMap, addedPointCountsBeforeSigmaFiltering,
                addedPointCountsDuringSigmaFiltering, sigmaStarted, finalPointValid):
            firstSfmLineIndex = lineIndex - 1
        match = ALIGN_PARAMETERS_PATTERN.search(logLine)
        if match and alignmentParameterPairs is None:
            parameterListText = match.group(1)
            parameterAssignments = [part.strip() for part in parameterListText.split(",")]
            parsed = []
            for part in parameterAssignments:
                if "=" in part:
                    parameterKey, value = part.split("=", 1)
                    parsed.append((parameterKey.strip(), value.strip()))
            alignmentParameterPairs = parsed or []
            continue

        match = KEYPOINT_DETECTION_PATTERN.search(logLine)
        if match:
            try:
                keypointCount = int(match.group("keypointCount"))  # all keypoints of the image (the strong ones are not counted separately)
                imageNumber = int(match.group("imageNumber"))
                interestPointCounts.append(keypointCount)
                keypointImageNumbers.append(imageNumber)
            except Exception as suppressedError:
                ReportSuppressedError("sfmLogParser.ParseSfmRunLines (detected keypoint count)", suppressedError)
            continue

        match = GUIDED_MATCHES_FOUND_PATTERN.search(logLine)
        if match:
            tentativeTotal = (tentativeTotal or 0) + int(match.group(1))
            continue

        match = MATCHES_FOUND_PATTERN.search(logLine)
        if match:
            tentativeTotal = (tentativeTotal or 0) + int(match.group(1))
            continue

        match = FILTERED_PATTERN.search(logLine)
        if match:
            filteredOut  = (filteredOut or 0) + int(match.group(1))
            filteredBase = (filteredBase or 0) + int(match.group(2))
            continue

        match = SETTING_INDICES_PATTERN.search(logLine)
        if match:
            indexedMatchCount = int(match.group(1)); continue

        match = GENERATED_TIE_POINTS_PATTERN.search(logLine)
        if match:
            generatedTiepoints = int(match.group(1))
            try: averageProjection = float(match.group(2))
            except Exception: averageProjection = None
            continue

        match = REMOVED_MULTIPLE_INDICES_PATTERN.search(logLine)
        if match:
            removedMultipleIndexCount = (removedMultipleIndexCount or 0) + int(match.group(1)); continue

        match = REMOVED_TRACKS_PATTERN.search(logLine)
        if match:
            removedTracksCount = (removedTracksCount or 0) + int(match.group(1)); continue

        match = SELECTED_TRACKS_PATTERN.search(logLine)
        if match:
            selectedTracks = int(match.group(1))
            selectedOutOf  = int(match.group(2))
            continue

        match = PAIR_ROBUST_PATTERN.search(logLine)
        if match:
            firstIndex, secondIndex = int(match.group(1)), int(match.group(2))
            robustMatchCount   = int(match.group(3))
            candidateMatchCount  = int(match.group(4)) if match.group(4) else None
            key = (min(firstIndex, secondIndex), max(firstIndex, secondIndex))
            previous = pairRobustMap.get(key)
            if (previous is None) or (previous[1] is None and candidateMatchCount is not None):
                pairRobustMap[key] = (robustMatchCount, candidateMatchCount)
            continue

        if SIGMA_START_PATTERN.search(logLine):
            if not sigmaStarted:
                sigmaFilteringStepCount = len(triangulationSteps)
            sigmaStarted = True
            sigmaBlockPreviousValid, sigmaBlockStepCount, sigmaBlockConsistent = None, 0, True

        match = ADDING_POINTS_PATTERN.search(logLine)
        if match:
            pointsAddedInLine = int(match.group(1))
            if sigmaStarted:
                pointsAddedAfterLastTupleDuringSigmaFiltering.append(pointsAddedInLine)
                farPointsAfterLastTupleDuringSigmaFiltering.append(int(match.group(2)) if match.group(2) else 0)
            else:
                pointsAddedAfterLastTuple.append(pointsAddedInLine)
            if sigmaStarted:
                addedPointCountsDuringSigmaFiltering.append(pointsAddedInLine)
            else:
                addedPointCountsBeforeSigmaFiltering.append(pointsAddedInLine)
            if match.group(2):
                if sigmaStarted:
                    farPointCountsDuringSigmaFiltering.append(int(match.group(2)))
                else:
                    farPointCountsBeforeSigmaFiltering.append(int(match.group(2)))
            triangulationSteps.append((pointsAddedInLine, int(match.group(2)) if match.group(2) else None,
                                       sigmaStarted))
            continue

        match = POINT_TUPLE_PATTERN.search(logLine)
        if match:
            totalPointCount, validPointCount = int(match.group(1)), int(match.group(2))
            if not sigmaStarted:
                validPointCountBeforeSigmaFiltering = validPointCount
            elif sigmaBlockPreviousValid is not None:
                sigmaBlockStepCount += 1
                if validPointCount != (sigmaBlockPreviousValid + sum(pointsAddedAfterLastTupleDuringSigmaFiltering)
                                       - sum(farPointsAfterLastTupleDuringSigmaFiltering)):
                    sigmaBlockConsistent = False
            if sigmaStarted:
                sigmaBlockPreviousValid = validPointCount
            finalPointTotal, finalPointValid = totalPointCount, validPointCount
            pointsAddedAfterLastTuple = []
            pointsAddedAfterLastTupleDuringSigmaFiltering = []
            farPointsAfterLastTupleDuringSigmaFiltering = []
            pointTupleSteps.append((len(triangulationSteps), totalPointCount, validPointCount,
                                    int(match.group(3)), sigmaStarted))
            continue

        match = BLOCK_PATTERN.search(logLine)
        if match:
            blockCameraCount = int(match.group(1))
            totalProjections = int(match.group(3))
            continue

        match = CAMERA_ENTITIES_PATTERN.search(logLine)
        if match:
            lastCameraEntityCounts = (int(match.group(1)), int(match.group(2)), int(match.group(3)))
            continue

    if firstSfmLineIndex is None and runLines and IsAnySfmCountSet(
            alignmentParameterPairs, interestPointCounts, tentativeTotal, filteredBase, indexedMatchCount,
            generatedTiepoints, removedMultipleIndexCount, removedTracksCount, selectedTracks, totalProjections,
            lastCameraEntityCounts, pairRobustMap, addedPointCountsBeforeSigmaFiltering,
            addedPointCountsDuringSigmaFiltering, sigmaStarted, finalPointValid):
        firstSfmLineIndex = len(runLines) - 1

    return {
        "interestPointCounts": interestPointCounts,
        "alignmentParameterPairs": alignmentParameterPairs,
        "tentativeTotal": tentativeTotal,
        "filteredOut": filteredOut,
        "filteredBase": filteredBase,
        "indexedMatchCount": indexedMatchCount,
        "generatedTiepoints": generatedTiepoints,
        "averageProjection": averageProjection,
        "removedMultipleIndexCount": removedMultipleIndexCount,
        "removedTracksCount": removedTracksCount,
        "selectedTracks": selectedTracks,
        "selectedOutOf": selectedOutOf,
        "totalProjections": totalProjections,
        "blockCameraCount": blockCameraCount,
        "lastCameraEntityCounts": lastCameraEntityCounts,
        "pairRobustMap": pairRobustMap,
        "addedPointCountsBeforeSigmaFiltering": addedPointCountsBeforeSigmaFiltering,
        "addedPointCountsDuringSigmaFiltering": addedPointCountsDuringSigmaFiltering,
        "farPointCountsBeforeSigmaFiltering": farPointCountsBeforeSigmaFiltering,
        "farPointCountsDuringSigmaFiltering": farPointCountsDuringSigmaFiltering,
        "pointsAddedAfterLastTuple": pointsAddedAfterLastTuple,
        "pointsAddedAfterLastTupleDuringSigmaFiltering": pointsAddedAfterLastTupleDuringSigmaFiltering,
        "farPointsAfterLastTupleDuringSigmaFiltering": farPointsAfterLastTupleDuringSigmaFiltering,
        "lastSigmaBlockStepCount": sigmaBlockStepCount,
        "lastSigmaBlockConsistent": sigmaBlockConsistent,
        "validPointCountBeforeSigmaFiltering": validPointCountBeforeSigmaFiltering,
        "finalPointTotal": finalPointTotal,
        "finalPointValid": finalPointValid,
        "sigmaStarted": sigmaStarted,
        "keypointImageNumbers": keypointImageNumbers,
        "triangulationSteps": triangulationSteps,
        "pointTupleSteps": pointTupleSteps,
        "sigmaFilteringStepCount": sigmaFilteringStepCount,
        "firstSfmLineIndex": firstSfmLineIndex,
    }


def IsAnySfmCountSet(alignmentParameterPairs, interestPointCounts, tentativeTotal, filteredBase, indexedMatchCount,
                     generatedTiepoints, removedMultipleIndexCount, removedTracksCount, selectedTracks,
                     totalProjections, lastCameraEntityCounts, pairRobustMap, addedPointCountsBeforeSigmaFiltering,
                     addedPointCountsDuringSigmaFiltering, sigmaStarted, finalPointValid):
    """True when one of the counts of ParseSfmRunLines that only a line of
    an SfM run sets is set: the parameters of Align Photos or any count of
    HasSfmCountLines.  ParseSfmRunLines checks its accumulators with it,
    and HasSfmLines its counts, so that both use one rule."""
    return (alignmentParameterPairs is not None or bool(interestPointCounts) or tentativeTotal is not None
            or filteredBase is not None or indexedMatchCount is not None or generatedTiepoints is not None
            or removedMultipleIndexCount is not None or removedTracksCount is not None or selectedTracks is not None
            or totalProjections is not None or lastCameraEntityCounts is not None or bool(pairRobustMap)
            or bool(addedPointCountsBeforeSigmaFiltering) or bool(addedPointCountsDuringSigmaFiltering)
            or bool(sigmaStarted) or finalPointValid is not None)


def HasSfmLines(counts):
    """True when the parsed lines hold at least one line of an SfM run
    (ParseSfmRunLines), such as a parameter, keypoint, or "pt:" line
    (IsAnySfmCountSet)."""
    return IsAnySfmCountSet(
        counts["alignmentParameterPairs"], counts["interestPointCounts"], counts["tentativeTotal"],
        counts["filteredBase"], counts["indexedMatchCount"], counts["generatedTiepoints"],
        counts["removedMultipleIndexCount"], counts["removedTracksCount"], counts["selectedTracks"],
        counts["totalProjections"], counts["lastCameraEntityCounts"], counts["pairRobustMap"],
        counts["addedPointCountsBeforeSigmaFiltering"], counts["addedPointCountsDuringSigmaFiltering"],
        counts["sigmaStarted"], counts["finalPointValid"])


def HasSfmCountLines(counts):
    """True when the parsed lines hold at least one line of an SfM run
    other than the parameter line of Align Photos (ParseSfmRunLines),
    such as a keypoint, matching, or "pt:" line (IsAnySfmCountSet without
    the parameters)."""
    return IsAnySfmCountSet(
        None, counts["interestPointCounts"], counts["tentativeTotal"], counts["filteredBase"],
        counts["indexedMatchCount"], counts["generatedTiepoints"], counts["removedMultipleIndexCount"],
        counts["removedTracksCount"], counts["selectedTracks"], counts["totalProjections"],
        counts["lastCameraEntityCounts"], counts["pairRobustMap"], counts["addedPointCountsBeforeSigmaFiltering"],
        counts["addedPointCountsDuringSigmaFiltering"], counts["sigmaStarted"], counts["finalPointValid"])


def SplitLogIntoRuns(text):
    """The SfM runs of a log (plain text) in their order.  Every Align
    Photos parameter line (“AlignPhotos: …”) starts a new run, and the
    lines before the first such line form a run of their own, whose
    start is not in the text.  Each run is a dictionary with its
    "lines", the 1-based "firstLineNumber" of its first line in the
    text, the first "timestamp" among its lines (None without one), and
    "hasParameterLine"."""
    runs = []
    for lineNumber, logLine in enumerate(text.splitlines(), 1):
        isParameterLine = bool(ALIGN_PARAMETERS_PATTERN.search(logLine))
        if isParameterLine or not runs:
            runs.append({"lines": [], "firstLineNumber": lineNumber, "timestamp": None,
                         "hasParameterLine": isParameterLine})
        currentRun = runs[-1]
        currentRun["lines"].append(logLine)
        if currentRun["timestamp"] is None:
            timestampMatch = LOG_TIMESTAMP_PATTERN.search(logLine)
            if timestampMatch:
                currentRun["timestamp"] = " ".join(timestampMatch.group(1).split())
    return runs


def ProjectLinesOfLog(text):
    """(line number, project path, whether the line saves the project) of
    every line of a log (plain text) that loads or saves a project
    (PROJECT_PATH_PATTERN, otherwise PROJECT_PATH_FALLBACK_PATTERN), in
    their order, with the 1-based line numbers of SplitLogIntoRuns."""
    projectLines = []
    for lineNumber, logLine in enumerate(text.splitlines(), 1):
        if "project:" not in logLine.lower():
            continue
        match = PROJECT_PATH_PATTERN.search(logLine) or PROJECT_PATH_FALLBACK_PATTERN.search(logLine)
        if match and match.group("projectPath"):
            projectLines.append((lineNumber, match.group("projectPath"),
                                 match.group("projectAction").lower() == "save"))
    return projectLines


def FirstSfmLineNumber(run):
    """The 1-based line number of the first SfM line of *run*
    (SplitLogIntoRuns, with its "counts" of ParseSfmRunLines): its Align
    Photos parameter line, or for a run without one, as a run started from
    Python, the first line that is a line of an SfM run, such as a keypoint
    line ("firstSfmLineIndex" of its counts, recorded by the parse of the
    run).  The first line of the run when none is."""
    firstSfmLineIndex = run["counts"].get("firstSfmLineIndex")
    if run["hasParameterLine"] or firstSfmLineIndex is None:
        return run["firstLineNumber"]
    return run["firstLineNumber"] + firstSfmLineIndex


def LogProjectPathOfRun(projectLines, firstSfmLineNumber):
    """The project that the log names for the run whose first SfM line
    has the 1-based *firstSfmLineNumber* (FirstSfmLineNumber): the path of
    the last line that loads or saves a project before that line
    (ProjectLinesOfLog).  Otherwise it is the path of the first line after
    it that saves a project, unless a line that loads a project comes
    first: that project replaced the project of the run, so it cannot name
    it.  None when the log names no project of the run."""
    pathsBefore = [projectPath for lineNumber, projectPath, _ in projectLines if lineNumber < firstSfmLineNumber]
    if pathsBefore:
        return pathsBefore[-1]
    for lineNumber, projectPath, isSave in projectLines:
        if lineNumber > firstSfmLineNumber:
            return projectPath if isSave else None
    return None


def ComparableProjectPath(projectPath):
    """*projectPath* in the form in which two paths are compared, without
    regard to case and to forward or backward slashes: in lower case,
    with forward slashes, and without white space at its ends."""
    return projectPath.strip().replace("\\", "/").lower()


def ParseSfmRuns(text):
    """The SfM runs of a log (SplitLogIntoRuns), each with its parsed
    "counts" (ParseSfmRunLines) and the project that the log names for
    it ("logProjectPath", LogProjectPathOfRun, None without one).  Lines
    before the first Align Photos parameter line that hold no SfM line,
    such as the loading of a project, are not a run.  A text without any
    SfM run gives one run with all its lines, so that its summary shows
    n/a throughout."""
    runs = []
    for run in SplitLogIntoRuns(text):
        run["counts"] = ParseSfmRunLines(run["lines"])
        if run["hasParameterLine"] or HasSfmLines(run["counts"]):
            runs.append(run)
    if not runs:
        allLines = text.splitlines()
        runs = [{"lines": allLines, "firstLineNumber": 1, "timestamp": None, "hasParameterLine": False,
                 "counts": ParseSfmRunLines(allLines)}]
    projectLines = ProjectLinesOfLog(text)
    for run in runs:
        run["logProjectPath"] = LogProjectPathOfRun(projectLines, FirstSfmLineNumber(run))
    return runs


def IsCompleteRun(counts):
    """True when the log holds the final tie point count of the run, its
    last “pt:” line.  A run without one was cancelled or failed, is
    still running, or was copied only in part."""
    return counts["finalPointValid"] is not None


def RunsWithSeveralMatchings(runs):
    """1-based numbers of the runs of *runs* in which a MatchPhotos: line
    follows SfM count lines of the same run (HasSfmCountLines), that is,
    a second image matching in one run.  Only AlignPhotos: lines start a
    run, and matching started from Python (Chunk.matchPhotos) prints a
    MatchPhotos: line without one, so such a run combines several runs.
    A MatchPhotos: line before the counts of its run, as Align Photos may
    print one, is not counted.  Each run is checked on its own, so that a
    run started from Python after a run of Align Photos is found and a
    single run started from Python gives no warning."""
    runNumbers = []
    for runIndex, run in enumerate(runs):
        runLines = run["lines"]
        for lineIndex, logLine in enumerate(runLines):
            if (lineIndex and MATCH_PHOTOS_PARAMETERS_PATTERN.search(logLine)
                    and HasSfmCountLines(ParseSfmRunLines(runLines[:lineIndex]))):
                runNumbers.append(runIndex + 1)
                break
    return runNumbers


def PythonRunsNote(runs):
    """(value, note) of the warning about runs started from Python, which
    names the runs with several matchings (RunsWithSeveralMatchings), or
    None when there is none."""
    runNumbers = RunsWithSeveralMatchings(runs)
    if not runNumbers:
        return None
    value = ("run {}" if len(runNumbers) == 1 else "runs {}").format(
        JoinWithAnd([str(runNumber) for runNumber in runNumbers]))
    note = ("(a MatchPhotos: line follows the counts of an earlier matching in the same run: matching started "
            "from Python prints a MatchPhotos: line without an AlignPhotos: line, and only AlignPhotos: lines "
            "start a run, so the counts of such a run combine several runs)")
    return value, note


def SelectSummarisedRunIndex(runs):
    """Index of the run that the summary covers by default: the last run
    whose final tie point count is in the log, or the last run when no
    run has one.  One run is summarised so that no count mixes two runs,
    and the list of runs above the tabs can choose another."""
    completeRunIndices = [index for index, run in enumerate(runs) if IsCompleteRun(run["counts"])]
    return completeRunIndices[-1] if completeRunIndices else len(runs) - 1


def FinalLogInlierCount(counts):
    """3D tie points (inliers) of the run in the log: the valid count of
    its last “pt:” line plus the points of the “adding” lines after it
    before sigma filtering, as long as that sum does not exceed the
    selected tracks, or, during sigma filtering, minus the far points of
    the adding lines after it when the log confirms that they belong to
    the end of the run (TrailingFarPoints) (None without a “pt:” line).
    The points of adding lines during sigma filtering are not added
    (NOT_SUMMED_NOTE), as in the summary.  Without the far points after
    the last pt: line subtracted, a log of Metashape 2.3.2 would give
    2,194 for a chunk with 2,082 valid 3D tie points."""
    finalPointValid = counts["finalPointValid"]
    pointsAddedAfterLastTuple = counts["pointsAddedAfterLastTuple"]
    selectedTracks = counts["selectedTracks"]
    if finalPointValid is not None and pointsAddedAfterLastTuple:
        correctedValidPointCount = finalPointValid + sum(pointsAddedAfterLastTuple)
        if selectedTracks is None or correctedValidPointCount <= selectedTracks:
            return correctedValidPointCount
    trailingFarPoints = TrailingFarPoints(counts)
    if finalPointValid is not None and trailingFarPoints["subtracted"]:
        return finalPointValid - trailingFarPoints["farPoints"]
    return finalPointValid


# Why TrailingFarPoints does not subtract the far points after the last
# pt: line, each with the end of the note of the summary that says so.
TRAILING_FAR_POINTS_NO_EARLIER_STEP = "noEarlierStep"
TRAILING_FAR_POINTS_ADDED = "added"
TRAILING_FAR_POINTS_INCONSISTENT = "inconsistent"
# More far points after the last pt: line than valid points in it, which
# would give a negative count.
TRAILING_FAR_POINTS_EXCEEDING = "exceeding"
TRAILING_FAR_POINTS_REASON_TEXTS = {
    TRAILING_FAR_POINTS_NO_EARLIER_STEP: ("its sigma filtering has no earlier step that confirms that a pt: line "
                                          "comes before the far points of its step"),
    TRAILING_FAR_POINTS_ADDED: "points are also added after it",
    TRAILING_FAR_POINTS_INCONSISTENT: ("the earlier steps of its sigma filtering do not confirm that a pt: line "
                                       "comes before the far points of its step"),
    TRAILING_FAR_POINTS_EXCEEDING: "they outnumber the valid points of that line",
}


def TrailingFarPoints(counts):
    """The far points of the adding lines after the last “pt:” line
    during sigma filtering: {"farPoints": their sum, "lineCount": the
    number of adding lines, "subtracted": whether FinalLogInlierCount
    subtracts them, "reason": why far points are not subtracted, or None
    (TRAILING_FAR_POINTS_NO_EARLIER_STEP, TRAILING_FAR_POINTS_ADDED,
    TRAILING_FAR_POINTS_INCONSISTENT, or TRAILING_FAR_POINTS_EXCEEDING)}.
    Metashape prints the pt: line of each step of sigma
    filtering before the adding line that rejects the far points of that
    step, so the far points after the last pt: line are rejected at the
    end of the run (a log of Metashape 2.3.2).  They are subtracted only when the log
    confirms this order for the run: at least one earlier step of the
    same block of sigma filtering, every such step obeying valid after =
    valid before + added - far, and no points added after the last pt:
    line (the log does not say whether those are new), and only when they
    do not outnumber the valid points of the last pt: line, which would
    give a negative count (-1 for 91 far points after 90 valid points)."""
    farPoints = counts.get("farPointsAfterLastTupleDuringSigmaFiltering") or []
    addedPoints = counts.get("pointsAddedAfterLastTupleDuringSigmaFiltering") or []
    farPointSum = sum(farPoints)
    reason = None
    if counts.get("finalPointValid") is not None and farPointSum > 0:
        if farPointSum > counts["finalPointValid"]:
            reason = TRAILING_FAR_POINTS_EXCEEDING
        elif counts.get("lastSigmaBlockStepCount", 0) < 1:
            reason = TRAILING_FAR_POINTS_NO_EARLIER_STEP
        elif sum(addedPoints) != 0:
            reason = TRAILING_FAR_POINTS_ADDED
        elif not counts.get("lastSigmaBlockConsistent"):
            reason = TRAILING_FAR_POINTS_INCONSISTENT
    subtracted = counts.get("finalPointValid") is not None and farPointSum > 0 and reason is None
    return {"farPoints": farPointSum, "lineCount": sum(1 for value in farPoints if value > 0),
            "subtracted": subtracted, "reason": reason}


def IsFinalLogInlierCountConfirmed(counts):
    """Whether the log confirms FinalLogInlierCount as the valid count at
    the end of the run: no far points after the last pt: line left
    unsubtracted (TrailingFarPoints), no points added after it during
    sigma filtering, which are not summed, and no points added after it
    before sigma filtering left out because they would exceed the
    selected tracks.  Otherwise the summary calls the count the last
    reported valid count."""
    finalPointValid = counts["finalPointValid"]
    if finalPointValid is None or TrailingFarPoints(counts)["reason"] is not None:
        return False
    if sum(counts.get("pointsAddedAfterLastTupleDuringSigmaFiltering") or []) > 0:
        return False
    pointsAddedAfterLastTuple = counts["pointsAddedAfterLastTuple"]
    selectedTracks = counts["selectedTracks"]
    return not (pointsAddedAfterLastTuple and selectedTracks is not None
                and finalPointValid + sum(pointsAddedAfterLastTuple) > selectedTracks)


def LastKeypointCountByImage(counts):
    """{image number: keypoints} of the run, each image with the count of
    its last detection line.  An image whose detection is logged more
    than once in a run (for example by a generic preselection at a lower
    Accuracy setting before the full detection, which a Metashape 2.3.2
    log has yet to show) would otherwise be counted twice, with its
    keypoints, so every count of the parser uses the last line of each
    image."""
    return dict(zip(counts["keypointImageNumbers"], counts["interestPointCounts"]))


def ImagesWithSeveralDetectionLines(counts):
    """Image numbers with more than one detection line in the run, in
    ascending order."""
    detectionLineCountByImage = {}
    for imageNumber in counts["keypointImageNumbers"]:
        detectionLineCountByImage[imageNumber] = detectionLineCountByImage.get(imageNumber, 0) + 1
    return sorted(imageNumber for imageNumber, lineCount in detectionLineCountByImage.items() if lineCount > 1)


def DetectedKeypointTotal(counts):
    """Keypoints detected in the run: the count of the last detection line
    of each image, summed over the images (None without a detection
    line)."""
    keypointCountByImage = LastKeypointCountByImage(counts)
    return sum(keypointCountByImage.values()) if keypointCountByImage else None


def InputImageCount(counts):
    """Images of the run: the image count of the last entities tuple, if
    present, otherwise the number of distinct image numbers of the
    keypoint detection lines (None without either)."""
    if counts["lastCameraEntityCounts"]:
        return counts["lastCameraEntityCounts"][0]
    return len(LastKeypointCountByImage(counts)) or None


def OrientedImageCount(counts):
    """Images oriented in the run: the count C of its last block line
    ("C cameras" in the log), otherwise the oriented count of its last
    entities tuple (None without either)."""
    if counts["blockCameraCount"] is not None:
        return counts["blockCameraCount"]
    return counts["lastCameraEntityCounts"][1] if counts["lastCameraEntityCounts"] else None


def DescribeOpenChunkDifference(logInlierCount, openChunkInlierCount):
    """(difference, reasons) of the valid tie points of the open chunk
    compared with the 3D tie points (inliers) of the log, or None when
    either count is missing.  The reasons are empty for equal counts."""
    if logInlierCount is None or openChunkInlierCount is None:
        return None
    difference = openChunkInlierCount - logInlierCount
    if difference < 0:
        return (f"{_FormatNumber(-difference)} fewer in the open chunk",
                "tie points removed after this run (for example after gradual selection or by hand), "
                "the chunk oriented again, or a log that belongs to another chunk")
    if difference > 0:
        return (f"{_FormatNumber(difference)} more in the open chunk",
                "tie points added after this run (for example by orienting further images), "
                "the chunk oriented again, or a log that belongs to another chunk")
    return ("the same count", "")


def _TrackSourceText(selectedTracks, finalPointTotal):
    """The tracks given with the inlier count of the log, named after the
    line they come from (empty without either line)."""
    if selectedTracks is not None:
        return f"from {_FormatNumber(selectedTracks)} tracks selected"
    if finalPointTotal is not None:
        return f"of {_FormatNumber(finalPointTotal)} in total in its last pt: line"
    return ""


# How a run is described.  The log shows no end marker of a run that the
# parser could rely on before a Metashape 2.3.2 log has been checked, so
# a run is described only by whether its final tie point count (a pt:
# line) is in the log, never as complete or incomplete.
RUN_WITH_FINAL_COUNT_TEXT = "with a final tie point count in the log"
RUN_WITHOUT_FINAL_COUNT_TEXT = "without a final tie point count"
RUN_WITHOUT_FINAL_COUNT_REASONS = "cancelled, failed, still running, or copied only in part"
# The status of a text without a single line of an SfM run, in the
# Summary, the list of runs, and the Console alike (Geert, 21 October
# 2026: the Console follows the Summary).
NO_SFM_RUN_TEXT = "no SfM run found"
NO_SFM_RUN_REASON = "no line of the text belongs to an Align Photos run"


def HoldsSfmRun(runs):
    """Whether the parsed *runs* (ParseSfmRuns) hold an SfM run, a run
    with an Align Photos parameter line or with a line of an SfM run.  A
    text without one gives ParseSfmRuns a single run of all its lines,
    which is not an SfM run."""
    return any(run["hasParameterLine"] or HasSfmLines(run["counts"]) for run in runs)


def RunStatusText(counts):
    """"with a final tie point count in the log" or "without a final tie
    point count (cancelled, failed, still running, or copied only in
    part)"."""
    if IsCompleteRun(counts):
        return RUN_WITH_FINAL_COUNT_TEXT
    return f"{RUN_WITHOUT_FINAL_COUNT_TEXT} ({RUN_WITHOUT_FINAL_COUNT_REASONS})"


def RunStartText(run):
    """Where a run starts in the text: "line 63, 2026-10-06 11:00:00",
    without the timestamp when its lines have none, and with "start not
    in the text" when the run has no Align Photos parameter line."""
    startTexts = [f"line {run['firstLineNumber']}"]
    if run["timestamp"]:
        startTexts.append(run["timestamp"])
    if not run["hasParameterLine"]:
        startTexts.append("start not in the text")
    return ", ".join(startTexts)


def RunChoiceText(runs, runIndex):
    """Entry of a run in the list of runs above the tabs, for example "Run
    2 of 3: line 63, 2026-10-06 11:00:00, with a final tie point count in
    the log"."""
    run = runs[runIndex]
    return f"Run {runIndex + 1} of {len(runs)}: {RunStartText(run)}, {RunStatusText(run['counts'])}"


def RunChoiceTexts(runs):
    """The entries of the list of runs above the tabs: one per run
    (RunChoiceText), or the one entry "No SfM run found" for a text
    without an SfM run (HoldsSfmRun), as the Summary says."""
    if not HoldsSfmRun(runs):
        return [NO_SFM_RUN_TEXT[0].upper() + NO_SFM_RUN_TEXT[1:]]
    return [RunChoiceText(runs, runIndex) for runIndex in range(len(runs))]


def RunCountsText(counts):
    """The main counts of a run, as the list of runs of the summary and
    the Console give them: images, keypoints, tracks selected, and 3D tie
    points (inliers, log), n/a for a count that is not in the log."""
    return (f"images: {_FormatNumber(InputImageCount(counts))}, "
            f"keypoints: {_FormatNumber(DetectedKeypointTotal(counts))}, "
            f"tracks selected: {_FormatNumber(counts['selectedTracks'])}, "
            f"3D tie points (inliers, log): {_FormatNumber(FinalLogInlierCount(counts))}")


def DefaultRunReason(runs):
    """Why the run of SelectSummarisedRunIndex is shown by default."""
    if IsCompleteRun(runs[SelectSummarisedRunIndex(runs)]["counts"]):
        return "the last run with a final tie point count in the log"
    return "no run has a final tie point count in the log, so the last run is shown"


def _RunListHtml(runs, summarisedRunIndex):
    """Section listing every run of the log with its main counts and
    whether the log holds its final tie point count, and the run that the
    summary covers, with how it was chosen (only for a log with several
    runs)."""
    parts = [_SectionHeadingHtml("SfM runs in this log")]
    for runIndex, run in enumerate(runs):
        counts = run["counts"]
        selectionText = "summarised below" if runIndex == summarisedRunIndex else "not summarised"
        parts.append(_SummaryRowHtml(f"Run {runIndex + 1} ({RunStartText(run)}):", RunCountsText(counts),
                                     f"[{selectionText}, {RunStatusText(counts)}]"))
    defaultRunIndex = SelectSummarisedRunIndex(runs)
    defaultRunReason = DefaultRunReason(runs)
    if summarisedRunIndex == defaultRunIndex:
        selectionNote = (f"Shown by default: {defaultRunReason}. Choose another run in the list of runs above the "
                         "tabs.")
    else:
        selectionNote = (f"Chosen in the list of runs above the tabs. Shown by default: Run {defaultRunIndex + 1}, "
                         f"{defaultRunReason}.")
    parts.append(_SummaryRowHtml("Summarised run:", f"Run {summarisedRunIndex + 1} of {len(runs)}",
                                 f"({RunStartText(runs[summarisedRunIndex])}. {selectionNote})"))
    return "".join(parts)


# The summary lists the reported image pairs up to this number: a row
# per pair of a large image set (100,000 pairs and more for 50,000 images)
# would make an HTML text of many megabytes that the summary pane needs
# seconds or minutes to show.  The Image pairs graph shows every pair.
MAXIMUM_LISTED_PAIR_COUNT = 1000


def MatchedPairNote(possiblePairCount, matchedPairCount):
    """Note of the image pairs that the log reports in the summary, in the
    words of the Image pairs graph: "(of 66 possible image pairs, the log
    does not report the other 54)", without the second part when the log
    reports every possible pair or no pair (n/a, not zero), and "" without
    an image count."""
    if possiblePairCount is None:
        return ""
    noteText = "of " + FormatCountWithNoun(possiblePairCount, "possible image pair", "possible image pairs")
    if matchedPairCount is not None and possiblePairCount > matchedPairCount:
        noteText += f", the log does not report the other {_FormatNumber(possiblePairCount - matchedPairCount)}"
    return f"({noteText})"

# The note of the row with the triangulation steps during sigma filtering
# (one sentence).
NOT_SUMMED_NOTE = ("(the log does not say whether such a step adds new 3D tie points or triangulates 3D tie points "
                   "again, so the points of these steps are not summed)")


def SummariseHtml(text, label="(clipboard)", runs=None, runIndex=None, openChunk=None):
    """Parse an SfM log (plain text) and return the HTML summary of the
    SfM pipeline, with *label* (the clipboard or the file name)
    as its source.  Counts that the log does not contain are shown as
    n/a.  A log that holds several Align Photos runs is split into its
    runs, and only one run is summarised, after a list of all runs: the
    run *runIndex* (chosen in the list of runs above the tabs) or, by
    default, SelectSummarisedRunIndex.  The valid tie points of the open
    chunk, if any, are counted for comparison and shown next to the log
    values, never in their place, with their difference.  The summary
    names the open chunk and its project after its source, with the
    warnings and the note on the match of the run with the open chunk
    (OpenChunkSummaryRows), and leaves the values of the open chunk out
    when the open chunk is left out.  *runs*, when given, are the runs of
    *text* already parsed by ParseSfmRuns, and *openChunk* the open chunk
    already read by ReadOpenChunk (or LeftOutOpenChunk), so that the
    dialog parses a log and reads the open chunk once for the summary and
    the graphs.  Without *openChunk*, the summary reads the open chunk
    itself, without its shared 3D tie points."""
    if runs is None:
        runs = ParseSfmRuns(text)
    if openChunk is None:
        openChunk = ReadOpenChunk(withSharedTiePoints=False)
    if runIndex is not None and 0 <= runIndex < len(runs):
        summarisedRunIndex = runIndex
    else:
        summarisedRunIndex = SelectSummarisedRunIndex(runs)
    counts = runs[summarisedRunIndex]["counts"]
    alignmentParameterPairs = counts["alignmentParameterPairs"]
    tentativeTotal = counts["tentativeTotal"]
    filteredOut = counts["filteredOut"]
    filteredBase = counts["filteredBase"]
    indexedMatchCount = counts["indexedMatchCount"]
    generatedTiepoints = counts["generatedTiepoints"]
    averageProjection = counts["averageProjection"]
    removedMultipleIndexCount = counts["removedMultipleIndexCount"]
    removedTracksCount = counts["removedTracksCount"]
    selectedTracks = counts["selectedTracks"]
    selectedOutOf = counts["selectedOutOf"]
    totalProjections = counts["totalProjections"]
    pairRobustMap = counts["pairRobustMap"]
    addedPointCountsBeforeSigmaFiltering = counts["addedPointCountsBeforeSigmaFiltering"]
    addedPointCountsDuringSigmaFiltering = counts["addedPointCountsDuringSigmaFiltering"]
    farPointCountsBeforeSigmaFiltering = counts["farPointCountsBeforeSigmaFiltering"]
    farPointCountsDuringSigmaFiltering = counts["farPointCountsDuringSigmaFiltering"]
    validPointCountBeforeSigmaFiltering = counts["validPointCountBeforeSigmaFiltering"]
    finalPointTotal = counts["finalPointTotal"]

    # sorted list of the pairwise robust entries
    pairRobustList = [(firstIndex, secondIndex, robustMatchCount, candidateMatchCount) for (firstIndex, secondIndex), (robustMatchCount, candidateMatchCount) in sorted(pairRobustMap.items())]

    # computed fields
    # Prefer the image count of the entities tuple, if present, to the
    # number of images with a keypoint detection line.  Each image counts
    # once, with its last detection line (LastKeypointCountByImage).
    numberImages = InputImageCount(counts)
    keypointCountByImage = LastKeypointCountByImage(counts)
    totalInterestPoints = DetectedKeypointTotal(counts)
    averageInterestPoints = (totalInterestPoints / len(keypointCountByImage)) if keypointCountByImage else None
    imagesWithSeveralDetectionLines = ImagesWithSeveralDetectionLines(counts)

    # None only where the line is missing: an explicit zero is a count,
    # not n/a.
    uniqueMatchCount   = filteredBase
    geometricallyConsistentMatchCount = (filteredBase - filteredOut) if filteredBase is not None else None
    removedRawMatchCount = ((tentativeTotal - uniqueMatchCount)
                            if (tentativeTotal is not None and uniqueMatchCount is not None) else None)
    geometricallyFilteredCount   = filteredOut

    # Triangulation steps: one per "adding" line.  The points of the steps
    # before sigma filtering are summed, those of the steps during it are
    # not (NOT_SUMMED_NOTE).
    hasTriangulationSteps = bool(counts["triangulationSteps"])
    stepCountBeforeSigmaFiltering = len(addedPointCountsBeforeSigmaFiltering)
    stepCountDuringSigmaFiltering = len(addedPointCountsDuringSigmaFiltering)
    addedPointSumBeforeSigmaFiltering = sum(addedPointCountsBeforeSigmaFiltering)
    farPointSumBeforeSigmaFiltering = sum(farPointCountsBeforeSigmaFiltering)
    farPointSumDuringSigmaFiltering = sum(farPointCountsDuringSigmaFiltering)

    orientedImageCount = OrientedImageCount(counts)

    # additional derived metrics
    unindexedMatchCount = None
    if (geometricallyConsistentMatchCount is not None) and (indexedMatchCount is not None):
        unindexedMatchCount = max(0, geometricallyConsistentMatchCount - indexedMatchCount)

    preThinningTracks = selectedOutOf  # tracks available before thinning

    # compute average projections per final unique track (from totals)
    averageProjectionFromTotals = None
    if totalProjections is not None and selectedTracks is not None and selectedTracks > 0:
        averageProjectionFromTotals = totalProjections / selectedTracks

    # final log inliers: the valid count of the last 'pt:' tuple plus the
    # points of the "adding" lines after it before sigma filtering
    # (FinalLogInlierCount)
    finalPointValid = FinalLogInlierCount(counts)

    # tracks removed after selection (selected, not inliers)
    removedTrackCountLog = None
    if (selectedTracks is not None) and (finalPointValid is not None):
        removedTrackCountLog = max(0, selectedTracks - finalPointValid)

    # ---------- Tie point count of the open chunk (independent of the log) ----------
    # Valid tie points of the open chunk (Metashape 2.x chunk.tie_points,
    # ReadOpenChunk), None when the open chunk is left out, cannot be
    # read, or is missing.  chunk.point_cloud is the dense point cloud and
    # is never counted.
    apiInliers = openChunk["validTiePointCount"] if IsOpenChunkRead(openChunk) else None
    # -----------------------------------------------------------

    # Possible and reported image pairs, in the words of the Image pairs
    # graph: the log reports a pair with a line "pair i and j: K robust
    # from M" and does not report the other possible pairs.
    possiblePairCount = None
    if numberImages is not None:
        try:
            possiblePairCount = (numberImages * (numberImages - 1)) // 2
        except Exception:
            possiblePairCount = None
    # None (n/a) when the log has no pair lines: absence is not zero
    matchedPairCount = len(pairRobustList) if pairRobustList else None
    matchedPairNote = MatchedPairNote(possiblePairCount, matchedPairCount)

    # Colours of headings and subsection labels
    headingColour  = "#0b3d91"  # headings
    subsectionColour = "#3a78c9"  # subsection labels

    # Build HTML
    parts = []
    parts.append(f"""
    <style>
      .wrap {{
        font-family: Segoe UI, Arial, Helvetica, sans-serif;
        font-size: 13px;
        line-height: 1.45;
        color: #000;
        background: #fff;
      }}
      .sec {{
        color: {headingColour};
        font-weight: 700;
        font-size: 14px;
        margin: 14px 0 6px 0;
      }}
      .subsec {{
        color: {subsectionColour};
        font-weight: 600;
      }}
      .row {{ display:flex; gap:10px; margin: 2px 0; }}
      .lab {{ width: 360px; color:#333; }}
      .lab.subsec {{ color: {subsectionColour}; font-weight: 600; }}
      .val {{ color:#000; }}
      .note {{ color:#555; }}
      .flow {{
        white-space: pre;
        font-family: Segoe UI, Arial, Helvetica, sans-serif;
        font-size: 13px;
        background: #fff;
        border: 1px solid {headingColour};
        border-radius: 6px;
        padding: 8px;
        color: #000;
      }}
      .kv {{ display:flex; flex-wrap:wrap; gap:12px 24px; }}
      .kv .item {{ min-width: 300px; }}
      .kv .k {{ color:#333; }}
      .kv .v {{ color:#000; font-weight:600; }}
    </style>
    <div class="wrap">
    """)
    parts.append(_SummaryRowHtml("Source:", label))
    # The open chunk next to the log, its other source, with the warnings
    # and the note on their match at the top, so that they stand out.
    for rowLabel, rowValue, rowNote in OpenChunkSummaryRows(runs[summarisedRunIndex], openChunk):
        parts.append(_SummaryRowHtml(rowLabel, rowValue, rowNote))
    pythonRunsNote = PythonRunsNote(runs)
    if pythonRunsNote is not None:
        parts.append(_SummaryRowHtml("Runs started from Python:", *pythonRunsNote))
    if len(runs) > 1:
        parts.append(_RunListHtml(runs, summarisedRunIndex))
    if not runs[summarisedRunIndex]["hasParameterLine"] and not HasSfmLines(counts):
        # A text without a single line of an SfM run is not a run at all,
        # so it is not described as a run without a final tie point count.
        parts.append(_SummaryRowHtml("Run status:", NO_SFM_RUN_TEXT, f"({NO_SFM_RUN_REASON})"))
    elif not IsCompleteRun(counts):
        parts.append(_SummaryRowHtml("Run status:", RUN_WITHOUT_FINAL_COUNT_TEXT,
                                     f"({RUN_WITHOUT_FINAL_COUNT_REASONS})"))

    # SfM parameters (descriptive label + [key])
    parts.append(_SectionHeadingHtml("SfM parameters"))
    if alignmentParameterPairs:
        parts.append('<div class="kv">')
        for parameterKey, value in alignmentParameterPairs:
            description = PARAMETER_LABELS.get(parameterKey, parameterKey)
            parts.append(
                f'<div class="item"><span class="k">{html.escape(description)} [{html.escape(parameterKey)}]: </span>'
                f'<span class="v">{html.escape(value)}</span></div>'
            )
        parts.append('</div>')
    else:
        parts.append(_SummaryRowHtml("Parameters:", "n/a"))

    # Detecting keypoints
    parts.append(_SectionHeadingHtml("Detecting keypoints"))
    parts.append(_SummaryRowHtml("Input images:", _FormatNumber(numberImages)))
    parts.append(_SummaryRowHtml("Keypoints detected (total):", _FormatNumber(totalInterestPoints)))
    parts.append(_SummaryRowHtml("Keypoints per image (average):", _FormatFixedDecimalsWithThousands(averageInterestPoints,1)))  # thousands separator
    if imagesWithSeveralDetectionLines:
        parts.append(_SummaryRowHtml("Images with more than one detection line:",
                                     _FormatNumber(len(imagesWithSeveralDetectionLines)),
                                     f"({ImageNumbersText(imagesWithSeveralDetectionLines)}, the last count of each is "
                                     "used)"))

    # Matching keypoints
    parts.append(_SectionHeadingHtml("Matching keypoints"))
    parts.append(_SummaryRowHtml("Raw, unconsolidated matches (sum over all image pairs):", _FormatNumber(tentativeTotal)))
    parts.append(_SummaryRowHtml("Unique and mutually consistent matches (sum over all image pairs):",
                     _FormatNumber(uniqueMatchCount),
                     f"({_FormatNumber(removedRawMatchCount)} raw matches removed)" if removedRawMatchCount is not None else ""))
    parts.append(_SummaryRowHtml("Geometrically consistent matches (sum over all image pairs):",
                     _FormatNumber(geometricallyConsistentMatchCount),
                     f"({_FormatNumber(geometricallyFilteredCount)} additional matches filtered)" if geometricallyFilteredCount is not None else ""))

    # Building tracks
    parts.append(_SectionHeadingHtml("Building tracks"))
    parts.append(_SummaryRowHtml("Pairwise matches given a unique index:", _FormatNumber(indexedMatchCount),
                     f"({_FormatNumber(unindexedMatchCount)} matches not given a unique index)" if unindexedMatchCount is not None else ""))
    if generatedTiepoints is not None:
        parts.append(_SummaryRowHtml("Tracks generated:", _FormatNumber(generatedTiepoints),
                         f"(average 2D tie points per track = {_FormatFixedDecimals(averageProjection,3)})" if averageProjection is not None else ""))
    # Cleanup with only numbers bold + show tracks before thinning (bold)
    cleanupText = (
        f'<b>{_FormatNumber(removedMultipleIndexCount)}</b> multiple indices removed, '
        f'<b>{_FormatNumber(removedTracksCount)}</b> tracks removed'
        + (f' \u2192 <b>{_FormatNumber(preThinningTracks)}</b> tracks before thinning' if preThinningTracks is not None else '')
    )
    parts.append(f'<div class="row"><span class="lab">Cleanup:</span> <span class="val">{cleanupText}</span></div>')
    if selectedTracks is not None:
        parts.append(_SummaryRowHtml("Final unique tracks/tie points:", f"{_FormatNumber(selectedTracks)}",
                         f"(out of {_FormatNumber(preThinningTracks)} tracks)"))
    if totalProjections is not None:
        note = f"(average 2D tie points per track = {_FormatFixedDecimals(averageProjectionFromTotals,3)})" if averageProjectionFromTotals is not None else ""
        parts.append(_SummaryRowHtml("Total 2D tie points:", _FormatNumber(totalProjections), note))
    if orientedImageCount is not None and numberImages is not None:
        parts.append(_SummaryRowHtml("Images oriented:", f"{_FormatNumber(orientedImageCount)} / {_FormatNumber(numberImages)}"))

    # Estimating image orientations (with light-blue subsections)
    parts.append(_SectionHeadingHtml("Estimating image orientations"))
    # subheading + the image pairs that the log reports and their robust matches
    parts.append('<div class="row"><span class="lab subsec">Image pairs reported in the log and their robust matches:</span> <span class="val"></span></div>')
    if possiblePairCount is not None or matchedPairCount is not None:
        parts.append(_SummaryRowHtml("  Image pairs reported:", _FormatNumber(matchedPairCount), matchedPairNote))
    if pairRobustList:
        for firstIndex,secondIndex,robustMatchCount,candidateMatchCount in pairRobustList[:MAXIMUM_LISTED_PAIR_COUNT]:
            note = (f"(robust matches of {_FormatNumber(candidateMatchCount)} candidate matches)"
                    if candidateMatchCount is not None else "(robust matches)")
            parts.append(_SummaryRowHtml(f"  Pair {firstIndex}–{secondIndex}:", _FormatNumber(robustMatchCount), note))
        if len(pairRobustList) > MAXIMUM_LISTED_PAIR_COUNT:
            parts.append(_SummaryRowHtml(
                "  More image pairs:", f"{_FormatNumber(len(pairRobustList) - MAXIMUM_LISTED_PAIR_COUNT)} not listed",
                "(all reported image pairs are in the Image pairs graph)"))

    # Triangulation: counts and sums of the steps instead of their values,
    # which the Tie point growth graph shows step by step.
    parts.append('<div class="row"><span class="lab subsec">Triangulating 3D tie points:</span> <span class="val"></span></div>')
    parts.append(_SummaryRowHtml("  Triangulation steps before sigma filtering:",
                                 _FormatNumber(stepCountBeforeSigmaFiltering if hasTriangulationSteps else None),
                                 "(one per line “adding N points” of the log)" if hasTriangulationSteps else ""))
    if addedPointCountsBeforeSigmaFiltering:
        parts.append(_SummaryRowHtml("  3D tie points added before sigma filtering (sum over the steps):",
                                     _FormatNumber(addedPointSumBeforeSigmaFiltering)))
    if farPointCountsBeforeSigmaFiltering:
        parts.append(_SummaryRowHtml("  Far points rejected before sigma filtering (sum over the steps):",
                                     _FormatNumber(farPointSumBeforeSigmaFiltering)))
    parts.append(_SummaryRowHtml("  Valid 3D tie points before sigma filtering:", _FormatNumber(validPointCountBeforeSigmaFiltering),
                                 "(valid count of the last pt: line before sigma filtering)"
                                 if validPointCountBeforeSigmaFiltering is not None else ""))
    if counts["sigmaStarted"]:
        parts.append(_SummaryRowHtml("  Triangulation steps during sigma filtering:",
                                     _FormatNumber(stepCountDuringSigmaFiltering), NOT_SUMMED_NOTE))
        if farPointCountsDuringSigmaFiltering:
            parts.append(_SummaryRowHtml("  Far points rejected during sigma filtering (sum over the steps):",
                                         _FormatNumber(farPointSumDuringSigmaFiltering)))
    # A count that the log does not confirm as the one at the end of the
    # run is the last reported valid count.
    isFinalCountConfirmed = IsFinalLogInlierCountConfirmed(counts)
    parts.append(_SummaryRowHtml("  Valid 3D tie points at the end of the run:" if isFinalCountConfirmed or
                                 finalPointValid is None else "  Last reported valid count of 3D tie points:",
                                 _FormatNumber(finalPointValid),
                                 "(the same count as under Final counts)" if finalPointValid is not None else ""))
    if hasTriangulationSteps:
        parts.append(_SummaryRowHtml("  Values of every triangulation step:", "see the Tie point growth graph"))

    # Final
    parts.append(_SectionHeadingHtml("Final counts"))
    # Final counts from the open chunk and from the log, each with its own
    # source.  The log gives the valid tie points at the end of the run,
    # the open chunk those valid now, so the two differ whenever tie
    # points changed after the run, and the summary says by how much.
    # The tracks of the log belong to the log count only.
    trackSourceText = _TrackSourceText(selectedTracks, finalPointTotal)
    openChunkDifference = DescribeOpenChunkDifference(finalPointValid, apiInliers)
    # The open chunk that is left out gives no row (OpenChunkSummaryRows
    # says that it is left out).
    if not openChunk["isLeftOut"]:
        parts.append(_SummaryRowHtml("3D tie points (inliers, open chunk):", _FormatNumber(apiInliers),
                                     OpenChunkCountNote(openChunk)))
    trailingFarPoints = TrailingFarPoints(counts)
    if trailingFarPoints["subtracted"]:
        logNote = ("[valid at the end of the run: the valid count of its last pt: line, {}, minus the {} of {} after "
                   "it, as in every earlier step of its sigma filtering]".format(
                       _FormatNumber(counts["finalPointValid"]),
                       FormatCountWithNoun(trailingFarPoints["farPoints"], "far point", "far points"),
                       "the adding line" if trailingFarPoints["lineCount"] == 1 else "the adding lines"))
    else:
        logNote = ("[{}: the valid count of its last pt: line plus the points of any adding lines after it before "
                   "sigma filtering]".format("valid at the end of the run" if isFinalCountConfirmed
                                             else "last reported valid count"))
    pointsAddedBeforeSigmaFiltering = counts["pointsAddedAfterLastTuple"]
    if (finalPointValid is not None and pointsAddedBeforeSigmaFiltering and selectedTracks is not None
            and counts["finalPointValid"] is not None
            and counts["finalPointValid"] + sum(pointsAddedBeforeSigmaFiltering) > selectedTracks):
        logNote += (" ({} of {} after it before sigma filtering not added, because the sum would exceed the {} "
                    "selected tracks)".format(
                        FormatCountWithNoun(sum(pointsAddedBeforeSigmaFiltering), "point", "points"),
                        FormatCountWithNoun(len(pointsAddedBeforeSigmaFiltering), "adding line", "adding lines"),
                        _FormatNumber(selectedTracks)))
    pointsNotAdded = counts["pointsAddedAfterLastTupleDuringSigmaFiltering"]
    if finalPointValid is not None and pointsNotAdded and sum(pointsNotAdded) > 0:
        logNote += (" ({} of {} after it during sigma filtering not added, because the log does not say "
                    "whether they are new)".format(
                        FormatCountWithNoun(sum(pointsNotAdded), "point", "points"),
                        FormatCountWithNoun(len(pointsNotAdded), "adding line", "adding lines")))
    if finalPointValid is not None and trailingFarPoints["reason"] is not None:
        logNote += (" ({} of {} after its last pt: line not subtracted, because {})".format(
            FormatCountWithNoun(trailingFarPoints["farPoints"], "far point", "far points"),
            "the adding line" if trailingFarPoints["lineCount"] == 1 else "the adding lines",
            TRAILING_FAR_POINTS_REASON_TEXTS[trailingFarPoints["reason"]]))
    parts.append(_SummaryRowHtml("3D tie points (inliers, log):", _FormatNumber(finalPointValid),
                                 f"({trackSourceText}) {logNote}" if trackSourceText else logNote))
    if openChunkDifference is not None:
        differenceText, differenceReasons = openChunkDifference
        parts.append(_SummaryRowHtml("Open chunk compared with the log:", differenceText,
                                     f"[possible reasons: {differenceReasons}]" if differenceReasons else ""))

    if removedTrackCountLog is not None:
        parts.append(_SummaryRowHtml("Tracks removed (selected, not inliers):", _FormatNumber(removedTrackCountLog)))

    # SfM flowchart
    flowLines = []
    flowLines.append(f"[Keypoints] {_FormatNumber(totalInterestPoints)} total  →  {_FormatFixedDecimalsWithThousands(averageInterestPoints,1)} per image")
    flowLines.append("   ↓")
    flowLines.append(f"[Matching]  raw {_FormatNumber(tentativeTotal)} → unique {_FormatNumber(uniqueMatchCount)} → geometrically consistent {_FormatNumber(geometricallyConsistentMatchCount)}")
    flowLines.append("   ↓")
    flowLines.append(f"[Tracks]  indices {_FormatNumber(indexedMatchCount)} → tracks {_FormatNumber(generatedTiepoints)} (average 2D tie points per track {_FormatFixedDecimals(averageProjection,3)})")
    flowLines.append(f"   cleanup: {_FormatNumber(removedMultipleIndexCount)} multiple indices removed, {_FormatNumber(removedTracksCount)} tracks removed → selected {_FormatNumber(selectedTracks)} / {_FormatNumber(selectedOutOf)}")
    flowLines.append(f"   2D tie points: {_FormatNumber(totalProjections)}   |   images oriented: {_FormatNumber(orientedImageCount)} / {_FormatNumber(numberImages)}")
    if possiblePairCount is not None or matchedPairCount is not None:
        flowLines.append(f"   image pairs reported: {_FormatNumber(matchedPairCount)}"
                         + (f" {matchedPairNote}" if matchedPairNote else ""))
    flowLines.append("   ↓")
    if hasTriangulationSteps:
        stepsBeforeText = FormatCountWithNoun(stepCountBeforeSigmaFiltering, "triangulation step", "triangulation steps")
        if addedPointCountsBeforeSigmaFiltering:
            stepsBeforeText += " adding " + FormatCountWithNoun(addedPointSumBeforeSigmaFiltering, "3D tie point",
                                                                "3D tie points")
        beforeSigmaFilteringTexts = [stepsBeforeText]
        if farPointCountsBeforeSigmaFiltering:
            beforeSigmaFilteringTexts.append(FormatCountWithNoun(farPointSumBeforeSigmaFiltering, "far point rejected",
                                                                 "far points rejected"))
        flowLines.append(f"[Estimating orientations]  before sigma filtering: {', '.join(beforeSigmaFilteringTexts)} → valid before sigma filtering {_FormatNumber(validPointCountBeforeSigmaFiltering)}")
    else:
        flowLines.append(f"[Estimating orientations]  triangulation steps before sigma filtering: n/a → valid before sigma filtering {_FormatNumber(validPointCountBeforeSigmaFiltering)}")
    if counts["sigmaStarted"]:
        duringSigmaFilteringTexts = [FormatCountWithNoun(stepCountDuringSigmaFiltering, "triangulation step",
                                                         "triangulation steps") + " (their points are not summed)"]
        if farPointCountsDuringSigmaFiltering:
            duringSigmaFilteringTexts.append(FormatCountWithNoun(farPointSumDuringSigmaFiltering, "far point rejected",
                                                                 "far points rejected"))
        flowLines.append(f"   during sigma filtering: {', '.join(duringSigmaFilteringTexts)}")
    flowLines.append("   ↓")
    # The log value is the result of the SfM run. The open chunk's count is
    # shown next to it with its source, never in its place, and the line
    # after it gives their difference with its possible reasons.
    endLine = f"[Final]  3D tie points (inliers): {_FormatNumber(finalPointValid)} in the log"
    if finalPointValid is not None:
        endLine += (" (valid at the end of the run" if isFinalCountConfirmed else " (last reported valid count") + (
            f", {trackSourceText}" if trackSourceText else "") + ")"
    if apiInliers is not None:
        endLine += f", {_FormatNumber(apiInliers)} in the open chunk{OpenChunkLabelSuffix(openChunk)} (valid now)"
    flowLines.append(endLine)
    if openChunkDifference is not None:
        differenceText, differenceReasons = openChunkDifference
        flowLines.append(f"   {differenceText}, possible reasons: {differenceReasons}" if differenceReasons
                         else f"   {differenceText} in the open chunk and in the log")

    flowHtml = html.escape("\n".join(flowLines))
    parts.append(_SectionHeadingHtml("SfM flowchart"))
    parts.append('<div class="flow">' + flowHtml + '</div>')
    parts.append("</div>")
    return "".join(parts)


# ===================================================================
# Graphs: values (pure functions of a parsed run, without Qt)
# ===================================================================

# The log lines that the graphs read, named in the message of a graph
# whose lines are missing from the log.  The line of the image pairs is
# named only in the About, once, so that the reader of the Image pairs
# graph never needs the term "pair line" (Geert, 7 October 2026).
PAIR_LINE_FORM = "pair i and j: K robust from M"
ADDING_LINE_FORM = "adding N points, F far"
POINT_TUPLE_LINE_FORM = "pt: (total, valid, weak)"
DETECTION_LINE_FORM = "photo N: X points"
MATCHES_LINE_FORM = "N matches found"
FILTERED_LINE_FORM = "filtered X out of Y matches"
INDICES_LINE_FORM = "setting point indices... N"
GENERATED_LINE_FORM = "generated N tie points"
SELECTED_LINE_FORM = "selected N tracks out of M"
PARAMETER_LINE_FORM = "AlignPhotos: key = value, ..."
ORIENTED_IMAGES_LINE_FORM = "block: S sensors, C cameras, P points, R projections or entities: cm: (images, oriented, 0)"

# A pair whose robust matches are fewer than this share of its candidate
# matches is outlined in the connectivity matrix: repetitive texture or
# moving objects leave many candidate matches but few robust ones.
LOW_ROBUST_SHARE = 0.5

# How many images, pairs, or groups a note names at most of each kind.
NAMED_EXTREME_COUNT = 5

# Stages of the pipeline funnel: label, what the stage counts (which sets
# its colour), the log line it is read from, and whether it counts the
# same things as the stage above it, so that it can be given as a share
# of that stage.
PIPELINE_FUNNEL_STAGES = (
    ("Keypoints detected", "keypoints", DETECTION_LINE_FORM, False),
    ("Raw matches", "matches", MATCHES_LINE_FORM, False),
    ("Unique matches", "matches", FILTERED_LINE_FORM, True),
    ("Geometrically consistent matches", "matches", FILTERED_LINE_FORM, True),
    ("Matches with a unique index", "matches", INDICES_LINE_FORM, True),
    ("Tracks generated", "tracks", GENERATED_LINE_FORM, False),
    ("Tracks selected", "tracks", SELECTED_LINE_FORM, True),
    ("3D tie points (inliers, log)", "3D tie points", POINT_TUPLE_LINE_FORM, True),
)

# Counts of the runs graph: label and the log line it is read from.
IMAGES_ORIENTED_LABEL = "Images oriented"
RUN_COMPARISON_MEASURES = (
    ("Keypoints detected", DETECTION_LINE_FORM),
    ("Tracks selected", SELECTED_LINE_FORM),
    ("3D tie points (inliers, log)", POINT_TUPLE_LINE_FORM),
    (IMAGES_ORIENTED_LABEL, ORIENTED_IMAGES_LINE_FORM),
)


def ParameterValueOfRun(counts, parameterKey):
    """Value (text) of *parameterKey* in the Align Photos parameter line of
    the run, or None without the line or the parameter."""
    for runParameterKey, value in counts["alignmentParameterPairs"] or []:
        if runParameterKey == parameterKey:
            return value
    return None


def _WholeNumber(valueText):
    """*valueText* as a whole number, or None when it is not one."""
    try:
        value = float(valueText)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value) or value != math.floor(value):
        return None
    return int(value)


def KeypointLimitOfRun(counts):
    """Keypoint limit per image of the run (keypoint_limit of its Align
    Photos parameter line), 0 for no limit, or None when the line or a
    whole number is missing."""
    return _WholeNumber(ParameterValueOfRun(counts, "keypoint_limit"))


def RunSettingsText(counts):
    """Downscale, keypoint limit, and tie point limit of the Align Photos
    parameter line of a run, as the runs graph names them."""
    if counts["alignmentParameterPairs"] is None:
        return "no Align Photos parameter line in the text"
    settingTexts = []
    for parameterKey, settingName in (("downscale", "downscale"), ("keypoint_limit", "keypoint limit"),
                                      ("tiepoint_limit", "tie point limit")):
        valueText = ParameterValueOfRun(counts, parameterKey)
        if valueText is not None:
            wholeNumber = _WholeNumber(valueText)
            settingTexts.append(f"{settingName} {_FormatNumber(wholeNumber) if wholeNumber is not None else valueText}")
    return ", ".join(settingTexts) if settingTexts else "no downscale or limit in the parameter line"


def ImageGroupsOfPairs(pairs):
    """Clusters of images that are connected through image pairs with at
    least one robust match (the connected components of the pairs), each
    a sorted list of image numbers, the largest cluster first.  *pairs*
    are tuples (first image, second image, robust matches, candidate
    matches).  An image without such a pair belongs to no cluster.  The
    texts call them clusters, because a group is a set of consecutive
    image numbers that one cell or bar of a graph shows."""
    neighboursByImage = {}
    for firstImage, secondImage, robustMatchCount, _ in pairs:
        if robustMatchCount > 0:
            neighboursByImage.setdefault(firstImage, []).append(secondImage)
            neighboursByImage.setdefault(secondImage, []).append(firstImage)
    visitedImages = set()
    imageGroups = []
    for startImage in sorted(neighboursByImage):
        if startImage in visitedImages:
            continue
        visitedImages.add(startImage)
        imagesToVisit = [startImage]
        imageGroup = []
        while imagesToVisit:
            imageNumber = imagesToVisit.pop()
            imageGroup.append(imageNumber)
            for neighbourImage in neighboursByImage[imageNumber]:
                if neighbourImage not in visitedImages:
                    visitedImages.add(neighbourImage)
                    imagesToVisit.append(neighbourImage)
        imageGroups.append(sorted(imageGroup))
    return sorted(imageGroups, key=lambda candidateGroup: (-len(candidateGroup), candidateGroup[0]))


def PairConnectivityData(counts):
    """Values of the image pair connectivity matrix of a run (graph 1), or
    None when the run has no pair line (PAIR_LINE_FORM).  The matrix has
    a row and a column for every image number up to the image count of
    the run or the highest image number of its pair and detection lines,
    whichever is larger, so that no image is lost.  The images of the log
    are the image numbers of its detection and pair lines (all numbers of
    the matrix without detection lines), so that a gap in the numbering
    is not taken for an image without a pair line.  The pairs and the
    robust matches of an image count only its pairs with at least one
    robust match."""
    pairRobustMap = counts["pairRobustMap"]
    if not pairRobustMap:
        return None
    pairs = [(firstImage, secondImage, robustMatchCount, candidateMatchCount)
             for (firstImage, secondImage), (robustMatchCount, candidateMatchCount) in sorted(pairRobustMap.items())]
    keypointImageNumbers = counts["keypointImageNumbers"]
    imageCount = max(InputImageCount(counts) or 0, max(pair[1] for pair in pairs) + 1,
                     max(keypointImageNumbers) + 1 if keypointImageNumbers else 0)
    imageNumbersOfLog = set(keypointImageNumbers) or set(range(imageCount))
    pairCountByImage = [0] * imageCount
    robustMatchesByImage = [0] * imageCount
    imagesWithPairLine = set()
    for firstImage, secondImage, robustMatchCount, _ in pairs:
        imagesWithPairLine.update((firstImage, secondImage))
        if robustMatchCount > 0:
            for pairImage in (firstImage, secondImage):
                pairCountByImage[pairImage] += 1
                robustMatchesByImage[pairImage] += robustMatchCount
    positiveRobustMatchCounts = [pair[2] for pair in pairs if pair[2] > 0]
    if counts["blockCameraCount"] is not None:
        orientedImageSource = "block line"
    elif counts["lastCameraEntityCounts"]:
        orientedImageSource = "entities line"
    else:
        orientedImageSource = None
    return {
        "imageCount": imageCount,
        "pairs": pairs,
        "pairCountByImage": pairCountByImage,
        "robustMatchesByImage": robustMatchesByImage,
        "imageNumbersOfLog": sorted(imageNumbersOfLog | imagesWithPairLine),
        "imagesWithoutPairLine": sorted(imageNumbersOfLog - imagesWithPairLine),
        "imagesByFewestRobustMatches": sorted(imagesWithPairLine, key=lambda imageNumber: (
            robustMatchesByImage[imageNumber], imageNumber)),
        "lowSharePairs": sorted((pair for pair in pairs if pair[3] and pair[2] < LOW_ROBUST_SHARE * pair[3]),
                                key=lambda pair: (pair[2] / pair[3], pair[0], pair[1])),
        "imageGroups": ImageGroupsOfPairs(pairs),
        "robustMinimum": min(positiveRobustMatchCounts) if positiveRobustMatchCounts else None,
        "robustMaximum": max(positiveRobustMatchCounts) if positiveRobustMatchCounts else None,
        "orientedImageCount": OrientedImageCount(counts),
        "orientedImageSource": orientedImageSource,
        "inputImageCount": InputImageCount(counts),
    }


# The track of a projection (TiePoints.Projection.track_id), and a test
# for a value that is not None, for the reading of the open chunk at the
# speed of the built-in functions.
_TRACK_ID_OF = operator.attrgetter("track_id")
_IS_NOT_NONE = functools.partial(operator.is_not, None)


def _ValidPointIndicesOfImage(tiePoints, image, validPointIndexByTrackId):
    """The indices of the valid 3D tie points of which *image* holds a 2D
    tie point (TiePoints.projections[image]), each once, or [] for an
    image without projections, for which Metashape raises a KeyError.
    Any other error is reported once in the Console, and the image then
    counts as one without projections."""
    try:
        projections = tiePoints.projections[image]
    except KeyError:
        return []
    except Exception as readError:
        ReportSuppressedError("sfmLogParser.SharedTiePointData", readError)
        return []
    if projections is None:
        return []
    return list(dict.fromkeys(filter(_IS_NOT_NONE, map(validPointIndexByTrackId.get,
                                                         map(_TRACK_ID_OF, projections)))))


def SharedTiePointData(chunk):
    """Values of the matrix of the shared 3D tie points of *chunk* (graph
    6), or None when the chunk has no tie points or fewer than two
    enabled, oriented images.  The images are the enabled, oriented
    images of the chunk (IsOrientedEnabledCamera, so never a keyframe of
    an animation track), in chunk order, numbered from 0.  Two images
    share a valid 3D tie point when both observe it, that is, when each
    has a 2D tie point of its track (TiePoints.projections).  They can
    share it without a match between them, through the other images of
    the track.  The projections are read once, image by image, and the
    pairs of the images of every 3D tie point are counted in one pass with
    built-in functions.  The values have the keys of
    PairConnectivityData that the matrix, its bars, and its notes read,
    with the shared 3D tie points in the place of the robust matches, and
    also the labels of the images (imageLabels) and the images of the
    chunk, without keyframes (chunkImageCount)."""
    tiePoints = getattr(chunk, "tie_points", None)
    cameras = getattr(chunk, "cameras", None)
    if tiePoints is None or not isinstance(cameras, (list, tuple)):
        return None
    images = [camera for camera in cameras if IsOrientedEnabledCamera(camera)]
    if len(images) < 2:
        return None
    # The valid 3D tie points, numbered from 0 by their tracks.
    validPointIndexByTrackId = {}
    for point in tiePoints.points:
        if getattr(point, "valid", True):
            validPointIndexByTrackId.setdefault(point.track_id, len(validPointIndexByTrackId))
    # The images of each valid 3D tie point, in increasing order: the
    # index of each image is appended to the list of each of its points
    # (a deque of length 0 runs the appends without keeping their
    # results).
    imagesByPoint = [[] for _ in range(len(validPointIndexByTrackId))]
    for imageIndex, image in enumerate(images):
        pointIndices = _ValidPointIndicesOfImage(tiePoints, image, validPointIndexByTrackId)
        collections.deque(map(list.append, map(imagesByPoint.__getitem__, pointIndices),
                              itertools.repeat(imageIndex)), maxlen=0)
    # Every pair of the images of each point, (smaller, larger), counted.
    sharedCountByPair = collections.Counter(itertools.chain.from_iterable(
        map(itertools.combinations, imagesByPoint, itertools.repeat(2))))
    del imagesByPoint
    imageCount = len(images)
    pairs = [(firstImage, secondImage, sharedCount, None)
             for (firstImage, secondImage), sharedCount in sorted(sharedCountByPair.items())]
    pairCountByImage = [0] * imageCount
    sharedPointsByImage = [0] * imageCount
    for firstImage, secondImage, sharedCount, _ in pairs:
        for pairImage in (firstImage, secondImage):
            pairCountByImage[pairImage] += 1
            sharedPointsByImage[pairImage] += sharedCount
    imagesWithPair = [imageIndex for imageIndex in range(imageCount) if pairCountByImage[imageIndex]]
    sharedCounts = [pair[2] for pair in pairs]
    return {
        "imageCount": imageCount,
        "pairs": pairs,
        "pairCountByImage": pairCountByImage,
        "robustMatchesByImage": sharedPointsByImage,
        "imageNumbersOfLog": list(range(imageCount)),
        "imagesWithoutPairLine": [imageIndex for imageIndex in range(imageCount) if not pairCountByImage[imageIndex]],
        "imagesByFewestRobustMatches": sorted(imagesWithPair, key=functools.partial(_SharedPointSortKey,
                                                                                     sharedPointsByImage)),
        "lowSharePairs": [],
        "imageGroups": ImageGroupsOfPairs(pairs),
        "robustMinimum": min(sharedCounts) if sharedCounts else None,
        "robustMaximum": max(sharedCounts) if sharedCounts else None,
        "imageLabels": [str(getattr(image, "label", "") or "") for image in images],
        "chunkImageCount": sum(1 for camera in cameras if not IsKeyframe(camera)),
    }


def _SharedPointSortKey(sharedPointsByImage, imageIndex):
    return sharedPointsByImage[imageIndex], imageIndex


def ReadSharedTiePointsOfOpenChunk():
    """(data, failure) of the active chunk of the open project, read when a
    log is parsed: data is its SharedTiePointData, or None without one,
    and failure is None, or, when the chunk cannot be read, data is None
    and failure the error in words (DescribeException), which the Console
    also reports once."""
    try:
        document = getattr(Metashape.app, "document", None)
        chunk = getattr(document, "chunk", None) if document else None
        return (SharedTiePointData(chunk) if chunk is not None else None), None
    except Exception as readError:
        ReportSuppressedError("sfmLogParser.ReadSharedTiePointsOfOpenChunk", readError)
        return None, DescribeException(readError)


def SharedTiePointDataOfOpenChunk():
    """SharedTiePointData of the active chunk of the open project
    (ReadSharedTiePointsOfOpenChunk), or None without one or when it
    cannot be read."""
    return ReadSharedTiePointsOfOpenChunk()[0]


# -------- The open chunk: read once, named, and compared with the log --------

def _ProjectPathOfDocument(document):
    """The project file of the open project (Metashape.app.document.path),
    "" for a project that has not been saved or a document that gives no
    path, or None when the path cannot be read (reported once)."""
    try:
        projectPath = getattr(document, "path", "")
    except Exception as readError:
        ReportSuppressedError("sfmLogParser._ProjectPathOfDocument", readError)
        return None
    return projectPath if isinstance(projectPath, str) else ""


def _ChunkLabelOf(chunk):
    """The label of *chunk* (Chunk.label), or "" when the chunk gives none
    or it cannot be read (reported once)."""
    try:
        chunkLabel = getattr(chunk, "label", "")
    except Exception as readError:
        ReportSuppressedError("sfmLogParser._ChunkLabelOf", readError)
        return ""
    return chunkLabel if isinstance(chunkLabel, str) else ""


def _ValidTiePointCountOfChunk(chunk):
    """(count, failure) of the tie points of *chunk* with the valid flag
    set (Metashape 2.x Chunk.tie_points): count is None without tie
    points, and failure is None, or, when the tie points cannot be read,
    count is None and failure the error in words (DescribeException),
    which the Console also reports once.  Chunk.point_cloud is the dense
    point cloud and is never counted."""
    try:
        tiePointObject = getattr(chunk, "tie_points", None)
        if tiePointObject is None:
            return None, None
        return sum(1 for tiePoint in tiePointObject.points if getattr(tiePoint, "valid", True)), None
    except Exception as countError:
        ReportSuppressedError("sfmLogParser._ValidTiePointCountOfChunk", countError)
        return None, DescribeException(countError)


def _OrientedImageCountOfChunk(chunk):
    """The enabled, oriented images of *chunk* (IsOrientedEnabledCamera,
    so never a keyframe of an animation track), the images of the matrix
    of the shared 3D tie points, or None when the chunk gives no list of
    images or it cannot be read (reported once)."""
    try:
        cameras = getattr(chunk, "cameras", None)
        if not isinstance(cameras, (list, tuple)):
            return None
        return sum(1 for camera in cameras if IsOrientedEnabledCamera(camera))
    except Exception as readError:
        ReportSuppressedError("sfmLogParser._OrientedImageCountOfChunk", readError)
        return None


def _OpenChunkValues(isLeftOut=False):
    """The values of the open chunk before it is read (ReadOpenChunk): no
    chunk and no values."""
    return {"isLeftOut": isLeftOut, "hasChunk": False, "chunkLabel": None, "projectPath": None,
            "validTiePointCount": None, "orientedImageCount": None, "sharedTiePointData": None,
            "readFailure": None, "readSeconds": 0.0}


def LeftOutOpenChunk():
    """The open chunk while the box Leave out the open chunk is ticked:
    not read, so without values, and with "isLeftOut" True."""
    return _OpenChunkValues(isLeftOut=True)


def ReadOpenChunk(withSharedTiePoints=True):
    """The active chunk of the open project, read once, when a log is
    parsed and when the open chunk is included again, as a dictionary:
    "isLeftOut" (False), "hasChunk" (whether the open project has an
    active chunk), "chunkLabel" (Chunk.label), "projectPath"
    (Metashape.app.document.path, "" for a project that has not been
    saved), "validTiePointCount" (its tie points with the valid flag set,
    None without tie points), "orientedImageCount" (its enabled, oriented
    images, _OrientedImageCountOfChunk), "sharedTiePointData"
    (SharedTiePointData, read only *withSharedTiePoints*), "readFailure"
    (the error in words when the chunk cannot be read, None otherwise),
    and "readSeconds".  The label and the path are read with the values,
    so that the texts name the chunk and the project that gave them.  The
    dialog is modal, so the open chunk does not change while it is
    open."""
    readStart = time.perf_counter()
    openChunk = _OpenChunkValues()
    try:
        document = getattr(Metashape.app, "document", None)
        chunk = getattr(document, "chunk", None) if document else None
    except Exception as readError:
        ReportSuppressedError("sfmLogParser.ReadOpenChunk", readError)
        openChunk["readFailure"] = DescribeException(readError)
        openChunk["readSeconds"] = time.perf_counter() - readStart
        return openChunk
    if document:
        openChunk["projectPath"] = _ProjectPathOfDocument(document)
    if chunk is not None:
        openChunk["hasChunk"] = True
        openChunk["chunkLabel"] = _ChunkLabelOf(chunk)
        openChunk["orientedImageCount"] = _OrientedImageCountOfChunk(chunk)
        if withSharedTiePoints:
            openChunk["sharedTiePointData"], openChunk["readFailure"] = ReadSharedTiePointsOfOpenChunk()
        if openChunk["readFailure"] is None:
            openChunk["validTiePointCount"], openChunk["readFailure"] = _ValidTiePointCountOfChunk(chunk)
    openChunk["readSeconds"] = time.perf_counter() - readStart
    return openChunk


def IsOpenChunkRead(openChunk):
    """True when *openChunk* (ReadOpenChunk) is an active chunk of the open
    project that was read, neither left out nor with a read failure, so
    that its values can be shown and compared with the log."""
    return bool(openChunk["hasChunk"]) and not openChunk["isLeftOut"] and openChunk["readFailure"] is None


# The check box of the dialog that leaves the open chunk out, as the texts
# name it.
LEAVE_OUT_OPEN_CHUNK_TEXT = "Leave out the open chunk"


def OpenChunkProjectText(openChunk):
    """The project of the open chunk in words: "of the project
    C:/projects/test.psx", "of a project that has not been saved", or,
    when its path cannot be read, "of a project whose file cannot be
    read"."""
    projectPath = openChunk["projectPath"]
    if projectPath is None:
        return "of a project whose file cannot be read"
    return f"of the project {projectPath}" if projectPath else "of a project that has not been saved"


def OpenChunkLabelText(openChunk):
    """The label of the open chunk in quotation marks, “Chunk 1”, or
    "(without a label)"."""
    chunkLabel = openChunk["chunkLabel"]
    return f"“{chunkLabel}”" if chunkLabel else "(without a label)"


def OpenChunkLabelSuffix(openChunk):
    """The label of an open chunk that was read, to follow "the open
    chunk": " “Chunk 1”", or "" without one."""
    chunkLabel = openChunk["chunkLabel"] if IsOpenChunkRead(openChunk) else None
    return f" “{chunkLabel}”" if chunkLabel else ""


def OpenChunkNameText(openChunk):
    """The open chunk and its project in words, to follow "the open
    chunk": "“Chunk 1” of the project C:/projects/test.psx"."""
    return f"{OpenChunkLabelText(openChunk)} {OpenChunkProjectText(openChunk)}"


def OpenChunkCountNote(openChunk):
    """The note of the valid tie points of the open chunk in the summary:
    counted in the open chunk, named by its label, and not in the log, or,
    without an open chunk that was read, counted in the open chunk now."""
    if IsOpenChunkRead(openChunk):
        return (f"[tie points with the valid flag set, counted in the open chunk{OpenChunkLabelSuffix(openChunk)}, "
                "not in the log]")
    return "[tie points with the valid flag set, counted in the open chunk now]"


# The labels of the warnings and of the note on the match of the log with
# the open chunk, in the summary and the Console.
IMAGE_COUNTS_DIFFER_LABEL = "Image counts differ:"
PROJECTS_DIFFER_LABEL = "Projects differ:"
OPEN_CHUNK_MATCH_LABEL = "Match with the open chunk:"


def OpenChunkImageCountWarning(counts, openChunk):
    """(label, value, note) of the warning that the selected run, with the
    counts *counts*, oriented more or fewer images according to the log
    (OrientedImageCount) than the open chunk holds enabled, oriented
    images, or None when the two agree or one is missing.  Only when the
    log gives no count of oriented images are the images of the run
    (InputImageCount) compared instead: compared with the oriented images
    of the chunk, the images of a run would warn of every log of the open
    chunk in which an image was not oriented."""
    chunkImageCount = openChunk["orientedImageCount"]
    logImageCount = OrientedImageCount(counts)
    logImageText = "images oriented in the selected run according to the log"
    if logImageCount is None:
        logImageCount = InputImageCount(counts)
        logImageText = "images of the selected run according to the log"
    if chunkImageCount is None or logImageCount is None or logImageCount == chunkImageCount:
        return None
    return (IMAGE_COUNTS_DIFFER_LABEL,
            f"{_FormatNumber(logImageCount)} in the log, {_FormatNumber(chunkImageCount)} in the open chunk",
            f"({logImageText}, and enabled, oriented images of the open chunk)")


def OpenChunkProjectWarning(run, openChunk):
    """(label, value, note) of the warning that the log names a project
    for *run* (its "logProjectPath", LogProjectPathOfRun) other than the
    open project, with the path in the log, and that of the open project
    when it has been saved, which are compared without regard to
    case and to forward or backward slashes (ComparableProjectPath), or
    None when the log names the open project or no project, or the path
    of the open project cannot be read."""
    logProjectPath = run.get("logProjectPath")
    openProjectPath = openChunk["projectPath"]
    if not logProjectPath or openProjectPath is None:
        return None
    if openProjectPath and ComparableProjectPath(logProjectPath) == ComparableProjectPath(openProjectPath):
        return None
    openProjectText = (f"the open project is {openProjectPath}" if openProjectPath
                       else "the open project has not been saved")
    return PROJECTS_DIFFER_LABEL, f"the log names {logProjectPath}", f"({openProjectText})"


def OpenChunkWarnings(run, openChunk):
    """The warnings that the log of *run* may belong to another chunk or
    project than the open chunk, each (label, value, note): its images
    (OpenChunkImageCountWarning) and its project (OpenChunkProjectWarning).
    [] without a warning, and when the open chunk is left out, missing, or
    not read (IsOpenChunkRead)."""
    if not IsOpenChunkRead(openChunk):
        return []
    warnings = (OpenChunkImageCountWarning(run["counts"], openChunk), OpenChunkProjectWarning(run, openChunk))
    return [warning for warning in warnings if warning is not None]


def OpenChunkMatchReason(run):
    """Why the match of the log of *run* with the open chunk cannot be
    confirmed: "the log does not name its chunk", or, when the log names
    no project for the run either, "the log names neither its chunk nor
    its project"."""
    if run.get("logProjectPath"):
        return "the log does not name its chunk"
    return "the log names neither its chunk nor its project"


def OpenChunkNote(run, openChunk):
    """(label, value, note) of the note that the match of the log of *run*
    with the open chunk cannot be confirmed (OpenChunkMatchReason), when
    the open chunk was read and no warning applies (OpenChunkWarnings),
    otherwise None.  A Metashape log names no chunk, so the match is never
    confirmed."""
    if not IsOpenChunkRead(openChunk) or OpenChunkWarnings(run, openChunk):
        return None
    return OPEN_CHUNK_MATCH_LABEL, "cannot be confirmed", f"({OpenChunkMatchReason(run)})"


def OpenChunkNoteSentence(run, openChunk):
    """The note of OpenChunkNote as a sentence: "The log does not name its
    chunk, so the match with the open chunk cannot be confirmed.", or ""
    without the note."""
    if OpenChunkNote(run, openChunk) is None:
        return ""
    reasonText = OpenChunkMatchReason(run)
    return f"{reasonText[:1].upper()}{reasonText[1:]}, so the match with the open chunk cannot be confirmed."


def OpenChunkFindings(run, openChunk):
    """The warnings (OpenChunkWarnings) and the note (OpenChunkNote) on the
    match of the log of *run* with the open chunk, each (label, value,
    note), the warnings first, as the summary and the Console give
    them."""
    note = OpenChunkNote(run, openChunk)
    return OpenChunkWarnings(run, openChunk) + ([note] if note is not None else [])


def OpenChunkFindingSentence(finding):
    """A warning or the note (label, value, note) as one sentence, as the
    tab Shared tie points draws it: "Image counts differ: 6 in the log, 9
    in the open chunk (…)."."""
    findingLabel, findingValue, findingNote = finding
    return f"{findingLabel} {findingValue} {findingNote}."


# The notes of the open chunk in the summary and in the Console.  The
# summary describes the log and compares it with the open chunk wherever it
# names the open chunk or the open project, and the tab Shared tie points
# shows the open chunk.
OPEN_CHUNK_SUMMARY_NOTE = ("(the summary describes the log and compares it with this chunk wherever it names the open "
                           "chunk or the open project)")
OPEN_CHUNK_CONSOLE_NOTE = ("(the tab Shared tie points shows this chunk, and the Summary compares the log with it "
                           "wherever it names the open chunk or the open project)")
OPEN_CHUNK_READ_FAILURE_NOTE = "(its tie points could not be read, and the Console names the error)"


def OpenChunkSummaryRows(run, openChunk):
    """Rows (label, value, note) of the summary after its source: the open
    chunk by its label and its project (OpenChunkNameText), with the note
    that the summary describes the log and compares it with this chunk
    wherever it names the open chunk or the open project, and then the
    warnings and the note on its match with the log of *run*
    (OpenChunkFindings), or that the open chunk is left out or that its
    tie points could not be read.  [] when the open project has no active
    chunk."""
    if openChunk["isLeftOut"]:
        return [("Open chunk:", "left out", f"(the box {LEAVE_OUT_OPEN_CHUNK_TEXT} is ticked, so this summary "
                                             "gives no values of the open chunk)")]
    if not openChunk["hasChunk"]:
        return []
    if openChunk["readFailure"] is not None:
        return [("Open chunk:", OpenChunkNameText(openChunk), OPEN_CHUNK_READ_FAILURE_NOTE)]
    return ([("Open chunk:", OpenChunkNameText(openChunk), OPEN_CHUNK_SUMMARY_NOTE)]
            + OpenChunkFindings(run, openChunk))


def OpenChunkFindingConsoleLines(run, openChunk):
    """The warnings and the note on the match of the log of *run* with the
    open chunk (OpenChunkFindings), as labelled lines of the Console."""
    return [ConsoleLabelledLine(findingLabel, f"{findingValue} {findingNote}")
            for findingLabel, findingValue, findingNote in OpenChunkFindings(run, openChunk)]


def OpenChunkConsoleLines(run, openChunk):
    """The Console lines of the open chunk for *run*, the run shown: the
    chunk and its project, with the note that the tab Shared tie points
    shows this chunk and that the Summary compares the log with it
    wherever it names the open chunk or the open project, and the warnings
    and the note on its match with the log (OpenChunkFindingConsoleLines),
    or that the open chunk is left out.  [] when the open project has no
    active chunk that was read."""
    if openChunk["isLeftOut"]:
        return [ConsoleLabelledLine("Open chunk:", f"left out (the box {LEAVE_OUT_OPEN_CHUNK_TEXT} is ticked)")]
    if not IsOpenChunkRead(openChunk):
        return []
    return ([ConsoleLabelledLine("Open chunk:", f"{OpenChunkNameText(openChunk)} {OPEN_CHUNK_CONSOLE_NOTE}")]
            + OpenChunkFindingConsoleLines(run, openChunk))


def TiePointGrowthData(counts):
    """Values of the graph of the growth of the 3D tie points of a run
    (graph 2), or None when the run has no "adding" line
    (ADDING_LINE_FORM).  Every adding line is a triangulation step, with
    its added points and its far points (None when its line gives none).
    The added points are summed only over the steps before sigma
    filtering: the log does not say whether a step during sigma
    filtering adds new 3D tie points or triangulates them again, so the
    points of those steps are kept per step (step number, points).  The
    valid count of every pt: line is placed at the number of steps before
    it (0 before the first step).  Sigma filtering starts with step
    firstSigmaFilteringStep, the step after the sigmaFilteringStepCount
    steps before its line.  The final count is the 3D tie point count of
    the log in the summary."""
    triangulationSteps = counts["triangulationSteps"]
    if not triangulationSteps:
        return None
    sigmaFilteringStepCount = counts["sigmaFilteringStepCount"]
    stepCountBeforeSigmaFiltering = (len(triangulationSteps) if sigmaFilteringStepCount is None
                                     else min(sigmaFilteringStepCount, len(triangulationSteps)))
    cumulativeAddedPointCounts = []
    runningTotal = 0
    for addedPointCount, _, _ in triangulationSteps[:stepCountBeforeSigmaFiltering]:
        runningTotal += addedPointCount
        cumulativeAddedPointCounts.append(runningTotal)
    farPointCounts = [step[1] for step in triangulationSteps]
    return {
        "stepCount": len(triangulationSteps),
        "addedPointCounts": [step[0] for step in triangulationSteps],
        "cumulativeAddedPointCountsBeforeSigmaFiltering": cumulativeAddedPointCounts,
        "addedPointStepsDuringSigmaFiltering": [
            (stepNumber, step[0]) for stepNumber, step in enumerate(triangulationSteps, 1)
            if stepNumber > stepCountBeforeSigmaFiltering],
        "farPointCounts": farPointCounts,
        "farPointSumBeforeSigmaFiltering": sum(farPointCount for farPointCount
                                               in farPointCounts[:stepCountBeforeSigmaFiltering]
                                               if farPointCount is not None),
        "farPointSumDuringSigmaFiltering": sum(farPointCount for farPointCount
                                               in farPointCounts[stepCountBeforeSigmaFiltering:]
                                               if farPointCount is not None),
        "validPointSteps": [(pointTupleStep[0], pointTupleStep[2]) for pointTupleStep in counts["pointTupleSteps"]],
        "sigmaFilteringStepCount": sigmaFilteringStepCount,
        "firstSigmaFilteringStep": None if sigmaFilteringStepCount is None else sigmaFilteringStepCount + 1,
        "finalLogInlierCount": FinalLogInlierCount(counts),
    }


def KeypointChartData(counts):
    """Values of the graph of the keypoints per image of a run (graph 3),
    or None when the run has no detection line (DETECTION_LINE_FORM).
    One bar per image, in the order of the image numbers, with the count
    of the image's last detection line (LastKeypointCountByImage), the
    keypoint limit of the run, the images at the limit (none without a
    positive limit), and the images in the order of their keypoints, the
    fewest first."""
    if not counts["interestPointCounts"]:
        return None
    keypointCountByImage = LastKeypointCountByImage(counts)
    imageNumbers = sorted(keypointCountByImage)
    keypointLimit = KeypointLimitOfRun(counts)
    hasPositiveLimit = keypointLimit is not None and keypointLimit > 0
    return {
        "imageNumbers": imageNumbers,
        "keypointCounts": [keypointCountByImage[imageNumber] for imageNumber in imageNumbers],
        "keypointLimit": keypointLimit,
        "imagesAtLimit": [imageNumber for imageNumber in imageNumbers
                          if hasPositiveLimit and keypointCountByImage[imageNumber] >= keypointLimit],
        "imagesByFewestKeypoints": sorted(imageNumbers, key=lambda imageNumber: (
            keypointCountByImage[imageNumber], imageNumber)),
        "imagesWithSeveralDetectionLines": ImagesWithSeveralDetectionLines(counts),
    }


def PipelineFunnelData(counts):
    """Stages of the pipeline funnel of a run (graph 4), or None when the
    run has none of their lines.  The values are those of the summary.
    Each stage holds its label, what it counts, the log line it is read
    from, its value (None when the line is missing), and its share of the
    stage above it, given only where both count the same things."""
    filteredBase = counts["filteredBase"]
    values = [DetectedKeypointTotal(counts),
              counts["tentativeTotal"],
              filteredBase,
              (filteredBase - counts["filteredOut"]) if filteredBase is not None else None,
              counts["indexedMatchCount"],
              counts["generatedTiepoints"],
              counts["selectedTracks"],
              FinalLogInlierCount(counts)]
    if all(value is None for value in values):
        return None
    stages = []
    for stageIndex, (label, countedThings, lineForm, isComparableWithStageAbove) in enumerate(PIPELINE_FUNNEL_STAGES):
        value = values[stageIndex]
        valueAbove = values[stageIndex - 1] if stageIndex > 0 else None
        shareOfStageAbove = (value / valueAbove if isComparableWithStageAbove and value is not None and valueAbove
                             else None)
        stages.append({"label": label, "entity": countedThings, "lineForm": lineForm, "value": value,
                       "shareOfStageAbove": shareOfStageAbove})
    return stages


def RunComparisonData(runs, summarisedRunIndex):
    """Values of the runs graph (graph 5): the counts of every run of the
    log (RUN_COMPARISON_MEASURES, None where its line is missing), with
    the settings and the start of each run, whether the log holds its
    final tie point count ("isComplete", IsCompleteRun), and whether it is
    the run chosen in the list of runs, which the summary and graphs 1 to
    4 show ("isSummarised").  None when the text holds no SfM run."""
    if not HoldsSfmRun(runs):
        return None
    runEntries = []
    for runIndex, run in enumerate(runs):
        counts = run["counts"]
        runEntries.append({
            "runNumber": runIndex + 1,
            "values": [DetectedKeypointTotal(counts), counts["selectedTracks"], FinalLogInlierCount(counts),
                       OrientedImageCount(counts)],
            "inputImageCount": InputImageCount(counts),
            "settingsText": RunSettingsText(counts),
            "startText": RunStartText(run),
            "isComplete": IsCompleteRun(counts),
            "isSummarised": runIndex == summarisedRunIndex,
        })
    return {"measures": list(RUN_COMPARISON_MEASURES), "runs": runEntries}


# -------- Blocks of consecutive images or steps (many images) --------

def BlockSize(itemCount, availableLength, minimumSlotLength):
    """Number of consecutive items (images or triangulation steps) that
    one cell or bar of a graph shows: the smallest of 1, 2, 5, 10, 20, 50,
    and so on with which every block gets at least *minimumSlotLength*
    of *availableLength* screen pixels, so that drawing never paints more
    cells or bars than the plot has room for (1 when every item fits)."""
    if itemCount <= 0 or not availableLength > 0:
        return 1
    for exponent in range(13):
        for mantissa in (1, 2, 5):
            blockSize = mantissa * 10 ** exponent
            blockCount = (itemCount + blockSize - 1) // blockSize
            if availableLength / blockCount >= minimumSlotLength:
                return blockSize
    return 10 ** 13


def BlockStatistics(valueByItem, blockSize):
    """The values of {item number: value} in blocks of *blockSize*
    consecutive item numbers: for every block that holds at least one
    value, in the order of the blocks, its index (item number divided by
    the block size, whole numbers), its first item number, the minimum,
    median, maximum, and total of its values, and how many it holds.  A
    block always covers the same item numbers, so that a gap in the
    numbering leaves a block with fewer items or out."""
    valuesByBlock = {}
    for itemNumber, value in valueByItem.items():
        valuesByBlock.setdefault(itemNumber // blockSize, []).append(value)
    blocks = []
    for blockIndex in sorted(valuesByBlock):
        values = valuesByBlock[blockIndex]
        blocks.append({"blockIndex": blockIndex, "firstItem": blockIndex * blockSize, "minimum": min(values),
                       "median": _Median(values), "maximum": max(values), "total": sum(values),
                       "itemCount": len(values)})
    return blocks


def ConnectivityBlockCells(data, blockSize):
    """Cells of the connectivity matrix in blocks of *blockSize*
    consecutive image numbers: {(row block, column block): (image pairs,
    robust matches)} of the image pairs with a pair line between the
    images of the two blocks, in both triangles of the matrix.  A cell on
    the diagonal holds the pairs within one block, and a block size of 1
    gives one cell per pair."""
    blockCells = {}
    for firstImage, secondImage, robustMatchCount, _ in data["pairs"]:
        rowBlock, columnBlock = firstImage // blockSize, secondImage // blockSize
        for cellKey in {(rowBlock, columnBlock), (columnBlock, rowBlock)}:
            pairCount, robustMatches = blockCells.get(cellKey, (0, 0))
            blockCells[cellKey] = (pairCount + 1, robustMatches + robustMatchCount)
    return blockCells


def PairCountsOfImagesOfTheLog(data):
    """{image number: image pairs with at least one robust match} of the
    images of the log (the per-image view of the connectivity)."""
    pairCountByImage = data["pairCountByImage"]
    return {imageNumber: pairCountByImage[imageNumber] for imageNumber in data["imageNumbersOfLog"]
            if imageNumber < len(pairCountByImage)}


def PairCountBlocks(data, blockSize):
    """BlockStatistics of the pairs with robust matches of the images of
    the log (PairCountsOfImagesOfTheLog), the per-image view of the image
    pairs graph."""
    return BlockStatistics(PairCountsOfImagesOfTheLog(data), blockSize)


def KeypointBlockStatistics(data, blockSize):
    """BlockStatistics of the keypoints per image, each block also with
    the number of its images at the keypoint limit ("atLimitCount")."""
    blocks = BlockStatistics(dict(zip(data["imageNumbers"], data["keypointCounts"])), blockSize)
    atLimitCountByBlock = {}
    for imageNumber in data["imagesAtLimit"]:
        atLimitCountByBlock[imageNumber // blockSize] = atLimitCountByBlock.get(imageNumber // blockSize, 0) + 1
    for block in blocks:
        block["atLimitCount"] = atLimitCountByBlock.get(block["blockIndex"], 0)
    return blocks


def StepBlockValues(data, blockSize, firstStep):
    """The steps of the growth graph in blocks of *blockSize* consecutive
    steps, counted from *firstStep*: [(index of the block, far points
    rejected in its steps or None when their lines give none, smallest
    and largest points of its steps during sigma filtering or None)]."""
    farPointsByBlock = {}
    for stepNumber, farPointCount in enumerate(data["farPointCounts"], 1):
        if farPointCount is not None:
            blockIndex = (stepNumber - firstStep) // blockSize
            farPointsByBlock[blockIndex] = farPointsByBlock.get(blockIndex, 0) + farPointCount
    sigmaPointsByBlock = {}
    for stepNumber, addedPointCount in data["addedPointStepsDuringSigmaFiltering"]:
        sigmaPointsByBlock.setdefault((stepNumber - firstStep) // blockSize, []).append(addedPointCount)
    return [(blockIndex, farPointsByBlock.get(blockIndex),
             (min(sigmaPointsByBlock[blockIndex]), max(sigmaPointsByBlock[blockIndex]))
             if blockIndex in sigmaPointsByBlock else None)
            for blockIndex in sorted(set(farPointsByBlock) | set(sigmaPointsByBlock))]


# -------- Notes under the graphs (they name the extremes) --------

def _JoinedWithAnd(texts):
    """"a", "a and b", or "a, b, and c", with the serial comma."""
    if len(texts) <= 1:
        return "".join(texts)
    if len(texts) == 2:
        return texts[0] + " and " + texts[1]
    return ", ".join(texts[:-1]) + ", and " + texts[-1]


def _Median(values):
    """Median of a non-empty list of numbers."""
    sortedValues = sorted(values)
    middleIndex = len(sortedValues) // 2
    if len(sortedValues) % 2:
        return sortedValues[middleIndex]
    return (sortedValues[middleIndex - 1] + sortedValues[middleIndex]) / 2.0


def _RankedList(texts, maximumPartCount=NAMED_EXTREME_COUNT):
    """The first *maximumPartCount* of *texts*, joined with commas, and
    how many more there are."""
    shownTexts = texts[:maximumPartCount]
    if len(texts) <= maximumPartCount:
        return ", ".join(shownTexts)
    # The rest counts as the last item of the list, with the serial comma.
    return _JoinedWithAnd(shownTexts + [f"{_FormatNumber(len(texts) - maximumPartCount)} more"])


def ImageNumbersText(imageNumbers, maximumPartCount=NAMED_EXTREME_COUNT):
    """"image 6", "images 2 and 3", or "images 0 to 5, 7, and 9 to 11": the
    image numbers in order, three or more consecutive numbers joined into
    a range, at most *maximumPartCount* parts, and then how many more
    images there are."""
    ranges = []
    for imageNumber in sorted(imageNumbers):
        if ranges and imageNumber == ranges[-1][1] + 1:
            ranges[-1][1] = imageNumber
        else:
            ranges.append([imageNumber, imageNumber])
    partEntries = []
    for firstNumber, lastNumber in ranges:
        if lastNumber - firstNumber >= 2:
            partEntries.append((f"{firstNumber} to {lastNumber}", lastNumber - firstNumber + 1))
        else:
            partEntries.extend((str(rangeNumber), 1) for rangeNumber in range(firstNumber, lastNumber + 1))
    partTexts = [partText for partText, _ in partEntries[:maximumPartCount]]
    remainingImageCount = sum(partImageCount for _, partImageCount in partEntries[maximumPartCount:])
    if remainingImageCount:
        partTexts.append(FormatCountWithNoun(remainingImageCount, "more image", "more images"))
    return ("image " if len(imageNumbers) == 1 else "images ") + _JoinedWithAnd(partTexts)


def LowShareLegendText():
    """What the red outline of an image pair says: "robust matches below
    50 % of the candidate matches"."""
    return f"robust matches below {100.0 * LOW_ROBUST_SHARE:.0f}\u00a0% of the candidate matches"


# What a light red row and column of the image pairs graph say of an
# image: no line of the log reports an image pair of it.
NO_REPORTED_IMAGE_PAIR_TEXT = "for which the log reports no image pair"


# How many parts the lists in the tooltip of the image pairs graph name
# at most (images for which the log reports no image pair, image pairs with a low
# share of robust matches): the tooltip holds the lists that the short
# note under the graph leaves out, and it must still fit on the screen.
DETAIL_LIST_MAXIMUM_PART_COUNT = 40


def _ImagesAndPairsSentence(data):
    """"12 images, 66 possible image pairs, of which the log reports
    12.\""""
    imageCount = len(data["imageNumbersOfLog"])
    possiblePairCount = imageCount * (imageCount - 1) // 2
    return (f"{FormatCountWithNoun(imageCount, 'image', 'images')}, "
            f"{FormatCountWithNoun(possiblePairCount, 'possible image pair', 'possible image pairs')}, "
            f"of which the log reports {_FormatNumber(len(data['pairs']))}.")


def _OrientedImagesSentence(data):
    """"Images oriented according to the log: 6 of 12.", or "" without
    the count.  The log line that gave the count (orientedImageSource) is
    not named, because the reader needs no log syntax."""
    if data["orientedImageCount"] is None:
        return ""
    ofInputText = f" of {_FormatNumber(data['inputImageCount'])}" if data["inputImageCount"] is not None else ""
    return f"Images oriented according to the log: {_FormatNumber(data['orientedImageCount'])}{ofInputText}."


def _WeakestImagesSentence(data):
    """The images with the fewest robust matches summed over their pairs,
    only those below the median of the images with a pair line, so that
    typical images are not named ("" when no image is below it)."""
    robustMatchesByImage = data["robustMatchesByImage"]
    if not data["imagesByFewestRobustMatches"]:
        return ""
    medianRobustMatches = _Median([robustMatchesByImage[imageNumber]
                                   for imageNumber in data["imagesByFewestRobustMatches"]])
    weakestImageTexts = []
    for imageNumber in data["imagesByFewestRobustMatches"]:
        if robustMatchesByImage[imageNumber] >= medianRobustMatches:
            break
        pairCount = data["pairCountByImage"][imageNumber]
        if pairCount:
            weakestImageTexts.append(f"image {imageNumber} ({_FormatNumber(robustMatchesByImage[imageNumber])} with "
                                     f"{FormatCountWithNoun(pairCount, 'other image', 'other images')})")
        else:
            weakestImageTexts.append(f"image {imageNumber} (no robust match)")
    if not weakestImageTexts:
        return ""
    return (f"Fewest robust matches, summed over the image pairs of an image (median "
            f"{_FormatNumber(int(round(medianRobustMatches)))}): {_RankedList(weakestImageTexts)}.")


def _ImageGroupsSentence(data):
    """The clusters of images that share no pair, or that all images with
    a robust match form one cluster ("" without such a pair)."""
    imageGroups = data["imageGroups"]
    if len(imageGroups) > 1:
        groupTexts = [f"{ImageNumbersText(imageGroup)} ({FormatCountWithNoun(len(imageGroup), 'image', 'images')})"
                      for imageGroup in imageGroups[:NAMED_EXTREME_COUNT]]
        if len(imageGroups) > NAMED_EXTREME_COUNT:
            groupTexts.append(FormatCountWithNoun(len(imageGroups) - NAMED_EXTREME_COUNT, "smaller cluster",
                                                  "smaller clusters"))
        return (f"The images with a robust match form {len(imageGroups)} clusters with no robust match between "
                f"them: {_JoinedWithAnd(groupTexts)}.")
    if imageGroups:
        return "All images with a robust match are connected in one cluster."
    return ""


def PairConnectivityShortNote(data):
    """Note painted under the image pairs graph, kept to the key facts: the
    images and pairs, the oriented images, the clusters of images that
    share no pair, and the images with the fewest robust matches.  The
    lists of the images without a pair line and of the pairs with a low
    robust share are in the tooltip (PairConnectivityNote), and the note
    says how many each holds."""
    sentences = [_ImagesAndPairsSentence(data), _OrientedImagesSentence(data), _ImageGroupsSentence(data),
                 _WeakestImagesSentence(data)]
    listedTexts = []
    if data["imagesWithoutPairLine"]:
        listedTexts.append(FormatCountWithNoun(len(data["imagesWithoutPairLine"]),
                                               "image " + NO_REPORTED_IMAGE_PAIR_TEXT,
                                               "images " + NO_REPORTED_IMAGE_PAIR_TEXT))
    if data["lowSharePairs"]:
        listedTexts.append(FormatCountWithNoun(len(data["lowSharePairs"]), "image pair with " + LowShareLegendText(),
                                               "image pairs with " + LowShareLegendText()))
    if listedTexts:
        sentences.append(f"Listed in the tooltip (hover over the graph): {_JoinedWithAnd(listedTexts)}.")
    return " ".join(sentence for sentence in sentences if sentence)


def PairConnectivityNote(data):
    """Full note of the connectivity matrix, the tooltip of the image
    pairs graph: the images and pairs of the run, the oriented images,
    the images without a pair line, the images with the fewest robust
    matches (only those below the median of the images with a pair line,
    so that typical images are not named), the pairs with a low robust
    share, and the clusters of images that share no pair.  The lists name
    up to DETAIL_LIST_MAXIMUM_PART_COUNT parts."""
    sentences = [_ImagesAndPairsSentence(data), _OrientedImagesSentence(data)]
    if data["imagesWithoutPairLine"]:
        sentences.append(f"The log reports no image pair for "
                         f"{ImageNumbersText(data['imagesWithoutPairLine'], DETAIL_LIST_MAXIMUM_PART_COUNT)}.")
    sentences.append(_WeakestImagesSentence(data))
    if data["lowSharePairs"]:
        shareTexts = [f"pair {firstImage}–{secondImage} ({100.0 * robustMatchCount / candidateMatchCount:.0f} %)"
                      for firstImage, secondImage, robustMatchCount, candidateMatchCount in data["lowSharePairs"]]
        sentences.append(f"{LowShareLegendText().capitalize()}: "
                         f"{_RankedList(shareTexts, DETAIL_LIST_MAXIMUM_PART_COUNT)}.")
    sentences.append(_ImageGroupsSentence(data))
    return " ".join(sentence for sentence in sentences if sentence)


def SharedImageText(data, imageIndex, detailText=""):
    """An image of the shared 3D tie point matrix by its number and its
    label, with *detailText* after the label: "image 3 (IMG_0004)" or
    "image 3 (IMG_0004, 720 with 4 other images)"."""
    bracketTexts = [text for text in (data["imageLabels"][imageIndex], detailText) if text]
    return f"image {imageIndex}" + (f" ({', '.join(bracketTexts)})" if bracketTexts else "")


def _SharedPairsSentence(data):
    """"28 images, 378 possible image pairs, of which 120 share at least
    one valid 3D tie point.\""""
    imageCount = data["imageCount"]
    possiblePairCount = imageCount * (imageCount - 1) // 2
    return (f"{FormatCountWithNoun(imageCount, 'image', 'images')}, "
            f"{FormatCountWithNoun(possiblePairCount, 'possible image pair', 'possible image pairs')}, "
            f"of which {_FormatNumber(len(data['pairs']))} {'shares' if len(data['pairs']) == 1 else 'share'} "
            f"at least one valid 3D tie point.")


def _SharedImagesSentence(data):
    """Which images of the open chunk the matrix shows, when the chunk has
    others: "The matrix shows the 28 enabled, oriented images of the 30
    images of the open chunk." ("" when it shows every image)."""
    if data["chunkImageCount"] == data["imageCount"]:
        return ""
    return (f"The matrix shows the {_FormatNumber(data['imageCount'])} enabled, oriented images of the "
            f"{FormatCountWithNoun(data['chunkImageCount'], 'image', 'images')} of the open chunk.")


def _SharedClustersSentence(data):
    """The clusters of images that share no 3D tie point, or that all
    images with a shared 3D tie point form one cluster ("" without
    one)."""
    imageGroups = data["imageGroups"]
    if len(imageGroups) > 1:
        clusterTexts = [f"{ImageNumbersText(imageGroup)} ({FormatCountWithNoun(len(imageGroup), 'image', 'images')})"
                        for imageGroup in imageGroups[:NAMED_EXTREME_COUNT]]
        if len(imageGroups) > NAMED_EXTREME_COUNT:
            clusterTexts.append(FormatCountWithNoun(len(imageGroups) - NAMED_EXTREME_COUNT, "smaller cluster",
                                                    "smaller clusters"))
        return (f"The images with a shared 3D tie point form {len(imageGroups)} clusters with no shared 3D tie point "
                f"between them: {_JoinedWithAnd(clusterTexts)}.")
    if imageGroups:
        return "All images with a shared 3D tie point are connected in one cluster."
    return ""


def _FewestSharedPointsSentence(data):
    """The images with the fewest shared 3D tie points summed over their
    image pairs, only those below the median of the images with one, so
    that typical images are not named ("" when no image is below it)."""
    sharedPointsByImage = data["robustMatchesByImage"]
    if not data["imagesByFewestRobustMatches"]:
        return ""
    medianSharedPoints = _Median([sharedPointsByImage[imageIndex] for imageIndex in data["imagesByFewestRobustMatches"]])
    imageTexts = []
    for imageIndex in data["imagesByFewestRobustMatches"]:
        if sharedPointsByImage[imageIndex] >= medianSharedPoints:
            break
        pairCount = data["pairCountByImage"][imageIndex]
        imageTexts.append(SharedImageText(data, imageIndex, f"{_FormatNumber(sharedPointsByImage[imageIndex])} with "
                                          f"{FormatCountWithNoun(pairCount, 'other image', 'other images')}"))
    if not imageTexts:
        return ""
    return (f"Fewest shared 3D tie points, summed over the image pairs of an image (median "
            f"{_FormatNumber(int(round(medianSharedPoints)))}): {_RankedList(imageTexts)}.")


# What a light red row and column of the shared 3D tie point graph say
# of an image, and of several images.
SHARES_NO_TIE_POINT_TEXT = "that shares no 3D tie point with another image"
SHARE_NO_TIE_POINT_TEXT = "that share no 3D tie point with another image"


def SharedTiePointShortNote(data):
    """Note painted under the shared 3D tie point graph: the images and
    pairs, the images of the chunk that it leaves out, the clusters of
    images that share no 3D tie point, and the images with the fewest
    shared 3D tie points.  The images without a shared 3D tie point are
    listed in the tooltip (SharedTiePointNote), and the note says how
    many there are."""
    sentences = [_SharedPairsSentence(data), _SharedImagesSentence(data), _SharedClustersSentence(data),
                 _FewestSharedPointsSentence(data)]
    if data["imagesWithoutPairLine"]:
        sentences.append("Listed in the tooltip (hover over the graph): "
                         + FormatCountWithNoun(len(data["imagesWithoutPairLine"]), "image " + SHARES_NO_TIE_POINT_TEXT,
                                               "images " + SHARE_NO_TIE_POINT_TEXT) + ".")
    return " ".join(sentence for sentence in sentences if sentence)


def SharedTiePointNote(data):
    """Full note of the shared 3D tie point graph, its tooltip: that of
    SharedTiePointShortNote, with the images that share no 3D tie point
    with another image, by number and label, up to
    DETAIL_LIST_MAXIMUM_PART_COUNT."""
    sentences = [_SharedPairsSentence(data), _SharedImagesSentence(data)]
    if data["imagesWithoutPairLine"]:
        imageTexts = [SharedImageText(data, imageIndex) for imageIndex in data["imagesWithoutPairLine"]]
        imagesText = ("Image " + SHARES_NO_TIE_POINT_TEXT if len(imageTexts) == 1
                      else "Images " + SHARE_NO_TIE_POINT_TEXT)
        sentences.append(f"{imagesText}: {_RankedList(imageTexts, DETAIL_LIST_MAXIMUM_PART_COUNT)}.")
    sentences.extend([_SharedClustersSentence(data), _FewestSharedPointsSentence(data)])
    return " ".join(sentence for sentence in sentences if sentence)


def SharedTiePointSourceSentence(openChunk=None):
    """The sentence of the explanation of the shared 3D tie point graph
    that names its source: "The matrix and its bars come from the open
    chunk “Chunk 1” of the project C:/projects/test.psx, not from the
    log.", without the name of the chunk when no open chunk that was read
    is given."""
    if openChunk is not None and IsOpenChunkRead(openChunk):
        return f"The matrix and its bars come from the open chunk {OpenChunkNameText(openChunk)}, not from the log."
    return "The matrix and its bars come from the open chunk, not from the log."


def SharedTiePointExplanation(data, openChunk=None, run=None):
    """Line above the shared 3D tie point graph, in the words of the image
    pairs graph: the open chunk that the matrix shows, by its label and
    its project (*openChunk*, ReadOpenChunk, SharedTiePointSourceSentence),
    the note that the match of the log of *run* with the open chunk cannot
    be confirmed (OpenChunkNoteSentence), what a coloured cell, a white
    cell, a light red row and column, and the bars on the right mean,
    where the matrix has them, and how the matrix differs from that of
    the log."""
    sentences = ["Which images of the open chunk share valid 3D tie points, and how many.",
                 SharedTiePointSourceSentence(openChunk)]
    if openChunk is not None and run is not None:
        noteSentence = OpenChunkNoteSentence(run, openChunk)
        if noteSentence:
            sentences.append(noteSentence)
    sentences.append("A coloured cell is an image pair whose images both observe at least one valid 3D tie point, "
                     "coloured by the number of such 3D tie points.")
    if HasWhiteImagePair(data):
        sentences.append("A white cell is an image pair that shares no valid 3D tie point.")
    if data["imagesWithoutPairLine"]:
        sentences.append(f"A light red row and column mark an image {SHARES_NO_TIE_POINT_TEXT}.")
    sentences.append("Two images can share a 3D tie point without a match between them, through the other images of "
                     "its track.")
    sentences.append("The matrix numbers the enabled, oriented images of the open chunk from 0 in chunk order, so its "
                     "image numbers need not be those of the log.")
    hasImageWithoutSharedPoint = 0 in PairCountsOfImagesOfTheLog(data).values()
    sentences.append("The bars on the right count, for each image, the other images with which it shares at least one "
                     "valid 3D tie point" + (", and a red mark at zero flags an image with none"
                                             if hasImageWithoutSharedPoint else "") + ".")
    sentences.append("Look for images that share 3D tie points with few other images and for clusters of images "
                     "without shared 3D tie points between them.")
    return " ".join(sentences)


def HasWhiteImagePair(data):
    """True when an image pair of two images outside the light red rows
    and columns (the images for which the log reports no image pair) is
    not reported in the log, so that the matrix of single images shows a
    white cell.  Every reported image pair joins two such images and is
    stored once, with the smaller image number first."""
    unflaggedImageCount = data["imageCount"] - len(data["imagesWithoutPairLine"])
    return len(data["pairs"]) < unflaggedImageCount * (unflaggedImageCount - 1) // 2


def MatrixShowsWhiteCell(data, blockSize, blockCells=None):
    """True when the matrix shows a white cell: in groups of *blockSize*
    consecutive images (ConnectivityBlockCells gives *blockCells*), a cell
    outside the light red rows and columns without a reported image pair,
    also on the diagonal, and for single images (*blockSize* 1) an image
    pair as in HasWhiteImagePair."""
    if blockSize == 1:
        return HasWhiteImagePair(data)
    slotCount = (data["imageCount"] + blockSize - 1) // blockSize
    flaggedSlots = {imageNumber // blockSize for imageNumber in data["imagesWithoutPairLine"]}
    unflaggedSlotCount = slotCount - len(flaggedSlots)
    colouredCellCount = sum(1 for rowSlot, columnSlot in blockCells
                            if rowSlot <= columnSlot and rowSlot not in flaggedSlots and columnSlot not in flaggedSlots)
    return colouredCellCount < unflaggedSlotCount * (unflaggedSlotCount + 1) // 2


def PairConnectivityExplanation(data):
    """Line above the image pairs graph, in plain words (Geert: "the
    wording is very strange, and I do not get it"): which image pairs the
    log reports, and what a coloured cell, a white cell, a light red row
    and column, and the bars on the right mean.  The white cells, the
    light red rows, and the red marks at zero are named only when the
    matrix of the run has them, the white cells when an image pair outside
    the light red rows and columns is not reported in the log
    (HasWhiteImagePair), which is exactly when the matrix of single images
    shows one.  In groups, such an image pair can lie in a coloured cell
    of a group, but the explanation is written before the size of the
    matrix, and so the group size, is known (the legend names the white
    cells only when the matrix shows one).  The sentences hold for single
    images and for groups of images alike, whose axis title, colour
    scale, and legend say that they show groups."""
    sentences = ["Which image pairs the log reports, and how strongly.",
                 "Metashape reports an image pair, with a line “pair i and j: K robust from M”, when it "
                 "evaluates the pair while it orients the images, but not every matched pair.",
                 "A coloured cell is an image pair that the log reports, coloured by its robust matches (K of its M "
                 "matches)."]
    if HasWhiteImagePair(data):
        sentences.append("A white cell is an image pair that the log does not report.")
    if data["imagesWithoutPairLine"]:
        sentences.append("A light red row and column mark an image for which the log reports no image pair. Such "
                         "images can still be matched and oriented, through the tie points that they share with "
                         "other images.")
    hasImageWithoutRobustMatch = 0 in PairCountsOfImagesOfTheLog(data).values()
    sentences.append("The bars on the right count, for each image, the other images with which the log reports at "
                     "least one robust match" + (", and a red mark at zero flags an image with none"
                                                 if hasImageWithoutRobustMatch else "") + ".")
    sentences.append("Look for images that share robust matches with few other images, for clusters of images "
                     "without robust matches between them, and for image pairs with many candidate matches but few "
                     "robust ones.")
    return " ".join(sentences)


def TiePointGrowthNote(data):
    """Note under the graph of the growth of the 3D tie points, in plain
    words: the steps, the added 3D tie points, and the far points of the
    phase before sigma filtering, the steps and far points of the phase
    during sigma filtering (whose points are not summed), the final count
    of the log, and the step with the most far points."""
    hasFarPoints = any(farPointCount is not None for farPointCount in data["farPointCounts"])
    cumulativeCounts = data["cumulativeAddedPointCountsBeforeSigmaFiltering"]
    phaseTexts = [FormatCountWithNoun(len(cumulativeCounts), "triangulation step", "triangulation steps")]
    if cumulativeCounts:
        phaseTexts.append(FormatCountWithNoun(cumulativeCounts[-1], "3D tie point", "3D tie points") + " added")
    if hasFarPoints:
        phaseTexts.append(FormatCountWithNoun(data["farPointSumBeforeSigmaFiltering"], "far point", "far points")
                          + " rejected")
    firstSigmaFilteringStep = data["firstSigmaFilteringStep"]
    sigmaFilteringStepCount = len(data["addedPointStepsDuringSigmaFiltering"])
    if firstSigmaFilteringStep is None:
        sentences = [", ".join(phaseTexts) + ".", "The log has no sigma filtering line."]
    else:
        sentences = [f"Before sigma filtering: {', '.join(phaseTexts)}."] if cumulativeCounts else []
        if sigmaFilteringStepCount:
            phaseTexts = [FormatCountWithNoun(sigmaFilteringStepCount, "triangulation step", "triangulation steps")]
            if hasFarPoints:
                phaseTexts.append(FormatCountWithNoun(data["farPointSumDuringSigmaFiltering"], "far point",
                                                      "far points") + " rejected")
            phaseTexts.append("the points of each step not summed")
            sentences.append(f"During sigma filtering (from step {firstSigmaFilteringStep}): {', '.join(phaseTexts)}.")
        else:
            sentences.append("The sigma filtering line follows the last step, so no step is logged during sigma "
                             "filtering.")
    if data["finalLogInlierCount"] is not None:
        sentences.append(f"Final count in the log: {_FormatNumber(data['finalLogInlierCount'])} valid 3D tie points.")
    else:
        sentences.append(f"The log has no {POINT_TUPLE_LINE_FORM} line, so the valid 3D tie points are not drawn.")
    knownFarPointSteps = [(farPointCount, stepNumber) for stepNumber, farPointCount
                          in enumerate(data["farPointCounts"], 1) if farPointCount is not None]
    if not knownFarPointSteps:
        sentences.append("The adding lines give no far points.")
    else:
        mostFarPoints = max(farPointCount for farPointCount, _ in knownFarPointSteps)
        if mostFarPoints == 0:
            sentences.append("No far points were rejected.")
        else:
            stepWithMostFarPoints = min(stepNumber for farPointCount, stepNumber in knownFarPointSteps
                                        if farPointCount == mostFarPoints)
            sentences.append(f"Most far points in one step: {_FormatNumber(mostFarPoints)} "
                             f"(step {stepWithMostFarPoints}).")
    return " ".join(sentences)


def KeypointChartNote(data):
    """Note under the graph of the keypoints per image: the images with
    the fewest keypoints (only those below the median, so that typical
    images are not named when most images reach the limit), the images
    with the most, and the images at the keypoint limit."""
    imageNumbers = data["imageNumbers"]
    keypointCountByImage = dict(zip(imageNumbers, data["keypointCounts"]))
    medianKeypointCount = _Median(data["keypointCounts"])
    fewestTexts = [f"image {imageNumber} ({_FormatNumber(keypointCountByImage[imageNumber])})"
                   for imageNumber in data["imagesByFewestKeypoints"]
                   if keypointCountByImage[imageNumber] < medianKeypointCount]
    mostKeypoints = max(data["keypointCounts"])
    imagesWithMostKeypoints = [imageNumber for imageNumber in imageNumbers
                               if keypointCountByImage[imageNumber] == mostKeypoints]
    sentences = [FormatCountWithNoun(len(imageNumbers), "image", "images") + "."]
    if fewestTexts:
        sentences.append(f"Fewest keypoints (median {_FormatNumber(int(round(medianKeypointCount)))}): "
                         f"{_RankedList(fewestTexts)}.")
    sentences.append(f"Most keypoints: {_FormatNumber(mostKeypoints)} ({ImageNumbersText(imagesWithMostKeypoints)}).")
    keypointLimit = data["keypointLimit"]
    if keypointLimit is None:
        sentences.append("No keypoint limit in the log (keypoint_limit of the AlignPhotos line), so none is drawn.")
    elif keypointLimit <= 0:
        sentences.append(f"keypoint_limit = {keypointLimit} means no limit in Metashape, so none is drawn.")
    elif data["imagesAtLimit"]:
        sentences.append(f"At the keypoint limit ({_FormatNumber(keypointLimit)}): "
                         f"{_FormatNumber(len(data['imagesAtLimit']))} of "
                         f"{FormatCountWithNoun(len(imageNumbers), 'image', 'images')}.")
    else:
        sentences.append(f"Keypoint limit {_FormatNumber(keypointLimit)}: no image reaches it.")
    imagesWithSeveralLines = data["imagesWithSeveralDetectionLines"]
    if imagesWithSeveralLines:
        sentences.append(f"{FormatCountWithNoun(len(imagesWithSeveralLines), 'image has', 'images have')} more than "
                         f"one detection line in this run ({ImageNumbersText(imagesWithSeveralLines)}), and the last "
                         "count of each is used.")
    return " ".join(sentences)


def RunQualifiers(runEntry):
    """What the runs graph says of a run besides its number: "selected"
    for the run chosen in the list of runs, and "without a final tie
    point count" when the log holds none."""
    qualifiers = []
    if runEntry["isSummarised"]:
        qualifiers.append("selected")
    if not runEntry["isComplete"]:
        qualifiers.append(RUN_WITHOUT_FINAL_COUNT_TEXT)
    return qualifiers


def PipelineFunnelNote(stages):
    """Note under the pipeline funnel: how the percentages compare the
    stages (when there are any), the largest drop, and the stages whose
    lines are missing."""
    sentences = []
    if any(stage["shareOfStageAbove"] is not None for stage in stages):
        sentences.append("Percentages compare a stage with the stage above it where both count the same things "
                         "(matches, or tracks and 3D tie points).")
    comparedStages = [stage for stage in stages
                      if stage["shareOfStageAbove"] is not None and stage["shareOfStageAbove"] < 1.0]
    if comparedStages:
        largestDropStage = min(comparedStages, key=lambda stage: stage["shareOfStageAbove"])
        sentences.append(f"Largest drop: {largestDropStage['label']}, "
                         f"{100.0 * largestDropStage['shareOfStageAbove']:.1f} % of the stage above.")
    missingStageTexts = [f"{stage['label']} ({stage['lineForm']})" for stage in stages if stage["value"] is None]
    if missingStageTexts:
        sentences.append("Not in the log: " + ", ".join(missingStageTexts) + ".")
    return " ".join(sentences)


def RunComparisonNote(data):
    """Note under the runs graph: the start and the settings of every run,
    which run is selected in the list of runs, and which runs have no
    final tie point count in the log."""
    sentences = []
    for runEntry in data["runs"]:
        qualifiers = [runEntry["startText"]] + RunQualifiers(runEntry)
        sentences.append(f"Run {runEntry['runNumber']} ({', '.join(qualifiers)}): {runEntry['settingsText']}.")
    if any(not runEntry["isComplete"] for runEntry in data["runs"]):
        sentences.append(f"Hatched bars: runs {RUN_WITHOUT_FINAL_COUNT_TEXT} in the log "
                         f"({RUN_WITHOUT_FINAL_COUNT_REASONS}).")
    if any(value is None for runEntry in data["runs"] for value in runEntry["values"]):
        sentences.append("n/a: not in the log of the run.")
    return " ".join(sentences)


# -------- Scales and labels --------

def LabelStep(slotSize, minimumLabelSpacing):
    """Every how many images (or steps) a label is drawn when each takes
    *slotSize* screen pixels: the smallest of 1, 2, 5, 10, 20, 50, and so
    on whose labels are at least *minimumLabelSpacing* pixels apart, so
    that the labels thin out as the cells and bars shrink."""
    if not slotSize > 0:
        return 1
    for exponent in range(12):
        for mantissa in (1, 2, 5):
            labelStep = mantissa * 10 ** exponent
            if labelStep * slotSize >= minimumLabelSpacing:
                return labelStep
    return 10 ** 12


def TickValues(maximumValue, maximumIntervalCount):
    """Ticks of a count axis from zero: multiples of the smallest whole
    interval of the 1, 2, 5 sequence that covers *maximumValue* in at most
    *maximumIntervalCount* intervals, up to the first multiple at or above
    the maximum ([0, 1] for a maximum of zero or less)."""
    if not maximumValue > 0:
        return [0, 1]
    smallestInterval = maximumValue / maximumIntervalCount
    exponent = math.floor(math.log10(smallestInterval))
    interval = 10 ** (exponent + 1)
    for mantissa in (1, 2, 5):
        if mantissa * 10 ** exponent >= smallestInterval * (1.0 - 1e-9):
            interval = mantissa * 10 ** exponent
            break
    interval = max(1, int(round(interval)))
    intervalCount = max(1, int(math.ceil(maximumValue / interval - 1e-9)))
    return [interval * tickIndex for tickIndex in range(intervalCount + 1)]


def RobustMatchColourFraction(robustMatchCount, robustMinimum, robustMaximum):
    """Position (0 to 1) of a robust match count on the logarithmic colour
    scale of the connectivity matrix, which runs from the fewest to the
    most robust matches of the pairs of the run, so that pairs with few
    robust matches stay apart.  Counts outside the scale are clamped, and
    a scale without range gives 1."""
    if robustMatchCount <= 0:
        return 0.0
    if robustMaximum <= robustMinimum:
        return 1.0
    fraction = ((math.log(robustMatchCount) - math.log(robustMinimum))
                / (math.log(robustMaximum) - math.log(robustMinimum)))
    return min(1.0, max(0.0, fraction))


# The viridis-like colour ramp of the heat map of the GSD Visualiser,
# from low to high.
_COLOUR_RAMP_STOPS = [
    (0.00, (68, 1, 84)),
    (0.25, (59, 82, 139)),
    (0.50, (33, 144, 140)),
    (0.75, (93, 201, 99)),
    (1.00, (253, 231, 37)),
]


def RampColourComponents(fraction):
    """(red, green, blue) of the colour ramp at *fraction* (0 to 1),
    interpolated linearly between its stops."""
    if fraction <= 0.0:
        return _COLOUR_RAMP_STOPS[0][1]
    if fraction >= 1.0:
        return _COLOUR_RAMP_STOPS[-1][1]
    for stopIndex in range(len(_COLOUR_RAMP_STOPS) - 1):
        lowerPosition, lowerColour = _COLOUR_RAMP_STOPS[stopIndex]
        upperPosition, upperColour = _COLOUR_RAMP_STOPS[stopIndex + 1]
        if lowerPosition <= fraction <= upperPosition:
            blendFraction = (fraction - lowerPosition) / (upperPosition - lowerPosition)
            return tuple(int(lowerComponent + blendFraction * (upperComponent - lowerComponent))
                         for lowerComponent, upperComponent in zip(lowerColour, upperColour))
    return _COLOUR_RAMP_STOPS[-1][1]


# ===================================================================
# Graphs: drawing (QPainter, in the style of the plots of the other tools)
# ===================================================================

# Fonts and colours as in the plots of the Gradual Selection Filter, the
# GSD Visualiser, and the Interior Orientation Viewer: bold 9 pt titles
# in black, 7 pt tick labels in dark grey, axis titles in grey, dotted
# light grey grid lines, and grey messages where there is nothing to draw.
GRAPH_TITLE_POINT_SIZE = 9
GRAPH_TEXT_POINT_SIZE = 8
GRAPH_TICK_POINT_SIZE = 7
TITLE_COLOUR = (0, 0, 0)
TEXT_COLOUR = (60, 60, 60)
TICK_COLOUR = (80, 80, 80)
AXIS_COLOUR = (80, 80, 80)
AXIS_TITLE_COLOUR = (100, 100, 100)
MESSAGE_COLOUR = (120, 120, 120)
GRID_COLOUR = (225, 225, 225)
FRAME_COLOUR = (60, 60, 60)
TIE_POINT_COLOUR = (46, 84, 150)
# The hollow diamonds of the steps during sigma filtering, a clear orange
# (a red orange reads as brown in small markers).
ADDED_POINT_COLOUR = (225, 115, 20)
# The 3D tie points added so far before sigma filtering: a wide pale
# orange band under the blue line of the valid count, so that it stays
# visible where the two counts are equal (a dashed line over the blue line
# would be hidden there).  Width in screen pixels.
ADDED_POINT_BAND_COLOUR = (250, 200, 150)
ADDED_POINT_BAND_WIDTH = 10.0
FAR_POINT_COLOUR = (160, 40, 40)
SIGMA_MARKER_COLOUR = (70, 80, 100)
# The span of the steps during sigma filtering, light blue so that it
# stays visible on the light grey background of a dialog.
SIGMA_FILTERING_SHADE_COLOUR = (218, 228, 243)
KEYPOINT_COLOUR = (46, 84, 150)
KEYPOINT_AT_LIMIT_COLOUR = (200, 120, 20)
# Blocks of images: the range of a block (fewest to most) in a light
# tone, its median in the dark tone of the series.
RANGE_COLOUR = (140, 168, 206)
PAIRS_PER_IMAGE_COLOUR = (46, 84, 150)
SELECTED_RUN_OUTLINE_COLOUR = (0, 0, 0)
LIMIT_LINE_COLOUR = (160, 40, 40)
LOW_SHARE_OUTLINE_COLOUR = (200, 0, 0)
# The warnings of a graph, in bold under its title (SfmGraphWidget.WarningText), in the red that flags images and
# image pairs, so that they stand out.
WARNING_COLOUR = LOW_SHARE_OUTLINE_COLOUR
NO_PAIR_LINE_COLOUR = (250, 250, 250)
IMAGE_WITHOUT_PAIR_LINE_COLOUR = (250, 222, 222)
DIAGONAL_COLOUR = (215, 215, 215)
ENTITY_COLOURS = {"keypoints": (70, 110, 60), "matches": (46, 84, 150), "tracks": (120, 70, 160),
                  "3D tie points": (40, 130, 140)}
RUN_COLOURS = ((46, 84, 150), (200, 80, 20), (40, 130, 140), (120, 70, 160), (160, 40, 40), (70, 110, 60),
               (150, 110, 30), (90, 90, 90))

# Layout of a graph widget: the title, the explanation, the graph area,
# and the note at the bottom.  The heights of the explanation and of the
# note follow the length of their texts, the note up to
# GRAPH_NOTE_MAXIMUM_LINE_COUNT lines (the full note is the tooltip of
# the graph).  Each graph sits in a scroll area, so its minimum size
# never hides the controls of the dialog.
GRAPH_MINIMUM_WIDTH = 680
GRAPH_MINIMUM_HEIGHT = 500
GRAPH_SIDE_MARGIN = 12
GRAPH_TITLE_BASELINE = 18
GRAPH_EXPLANATION_TOP = 26
GRAPH_TEXT_LINE_HEIGHT = 15
GRAPH_NOTE_MAXIMUM_LINE_COUNT = 6
# Below a plot: its tick labels, its axis title, and up to two rows of
# legend items.
LEGEND_ROW_HEIGHT = 14
LEGEND_SWATCH_AND_GAP_WIDTH = 52.0
GRAPH_BOTTOM_SPACE = 60
# A generous estimate of the width of a character, in screen pixels per
# point of font size, with which notes and legends are wrapped without
# the font metrics of a painter.  Qt places the text itself.
ESTIMATED_CHARACTER_WIDTH_PER_POINT = 0.75
# Screen pixels between two image or step labels at least, and the
# smallest matrix cell whose low robust share is outlined, three times as
# wide as the outline is thick (LOW_SHARE_OUTLINE_WIDTH).
MINIMUM_IMAGE_LABEL_SPACING = 22.0
MINIMUM_STEP_LABEL_SPACING = 28.0
LOW_SHARE_OUTLINE_WIDTH = 2
MINIMUM_OUTLINED_CELL_SIZE = 3.0 * LOW_SHARE_OUTLINE_WIDTH
# The smallest cell of the connectivity matrix, bar of the keypoints per
# image, and slot of a triangulation step, in screen pixels.  With more
# images or steps than fit at this size, consecutive images or steps are
# shown in blocks (BlockSize), so that no graph paints more cells or
# bars than it has room for.
MINIMUM_MATRIX_CELL_SIZE = 4.0
MINIMUM_BAR_SLOT_WIDTH = 2.0
MINIMUM_STEP_SLOT_WIDTH = 2.0
# The smallest slot of a step in which its markers are drawn one by one.
MINIMUM_MARKER_SLOT_WIDTH = 4.0
# Room above the highest bar or point for its label.
COUNT_AXIS_HEADROOM = 1.08
# The free row at the top of the upper panel of the growth graph, in
# screen pixels, for the label of the span of sigma filtering (its text
# and half the width of the band below it).
SIGMA_FILTERING_LABEL_ROW_HEIGHT = 22.0

NO_LOG_MESSAGE = "No log parsed yet. Choose an input method above."
# The caption row above the matrix and the bars of the image pairs graph:
# one line, for the title of the bars, which always takes one line
# (PerImageTitleLeft), and the title of the colour scale.  The row ends 4
# screen pixels above the matrix.
PAIRS_CAPTION_HEIGHT = 16
# Screen pixels at least between the end of the title of the bars and the
# title of the colour scale, which starts at the colour scale.
CAPTION_TITLE_GAP = 12.0
# A second line of an axis title, in screen pixels: the image axes of the
# image pairs graph take one where the matrix is narrower than their
# title (blocks of images, small graphs).
AXIS_TITLE_LINE_HEIGHT = 12
# The image pairs graph is taller at least than the other graphs: its
# explanation in plain words takes up to eight lines at the smallest
# width, and the matrix and its axis titles must stay legible.  The
# scroll area shows the rest on a small screen.
PAIRS_GRAPH_MINIMUM_HEIGHT = 600
# The image numbers are Metashape's own numbers in the log, counted
# within each Align Photos run, so the axis says whose numbers they are
# (Geert: "is image 15 of run one also image 15 of run 2?").
AXIS_TEXT_IMAGE = "image (number in the log of the selected run)"


def _Colour(colourComponents):
    return QtGui.QColor(*colourComponents)


def _Pen(colourComponents, width=1, style=None):
    """QPen of the colour (red, green, blue), solid unless *style* is given."""
    if style is None:
        return QtGui.QPen(QtGui.QColor(*colourComponents), width)
    return QtGui.QPen(QtGui.QColor(*colourComponents), width, style)


def _Font(pointSize, isBold=False):
    font = QtGui.QFont()
    font.setPointSize(pointSize)
    font.setBold(isBold)
    return font


def _WrappedTextOption(alignment=None):
    """QTextOption of wrapped text: wrapped at word boundaries and placed by
    the Qt alignment flags (top left by default)."""
    textOption = QtGui.QTextOption(QtCore.Qt.AlignLeft | QtCore.Qt.AlignTop if alignment is None else alignment)
    textOption.setWrapMode(QtGui.QTextOption.WordWrap)
    return textOption


def _DrawWrappedText(painter, left, top, width, height, text, alignment=None):
    """Draw *text* in the rectangle, wrapped at word boundaries and placed
    by the Qt alignment flags (top left by default).  Qt clips the text to
    the rectangle."""
    painter.drawText(QtCore.QRectF(left, top, max(width, 10.0), max(height, 10.0)), text,
                     _WrappedTextOption(alignment))


def _FiniteNumber(value):
    """*value* as a float when it is a finite number of zero or more,
    otherwise None (the Qt stand-ins of the offline tests give none)."""
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0:
        return float(value)
    return None


def _MeasuredTextHeight(painter, width, text):
    """Height in screen pixels of *text* wrapped at *width* in the font of
    *painter*, as _DrawWrappedText draws it (QPainter.boundingRect with the
    same text option), or None when the painter gives no number."""
    try:
        measuredHeight = painter.boundingRect(QtCore.QRectF(0.0, 0.0, max(width, 10.0), 1.0e6), text,
                                              _WrappedTextOption()).height()
    except Exception as measureError:
        ReportSuppressedError("sfmLogParser._MeasuredTextHeight", measureError)
        return None
    return _FiniteNumber(measuredHeight)


def _MeasuredTextWidth(painter, text, pointSize):
    """Width in screen pixels of *text* on one line in the font of
    *painter* (QPainter.boundingRect), or, when the painter gives no
    number, a generous estimate from the characters of the text at
    *pointSize* (ESTIMATED_CHARACTER_WIDTH_PER_POINT)."""
    try:
        measuredWidth = _FiniteNumber(painter.boundingRect(QtCore.QRectF(0.0, 0.0, 1.0e6, 100.0),
                                                           QtCore.Qt.AlignLeft | QtCore.Qt.AlignTop, text).width())
    except Exception as measureError:
        ReportSuppressedError("sfmLogParser._MeasuredTextWidth", measureError)
        measuredWidth = None
    if measuredWidth is None:
        return len(text) * pointSize * ESTIMATED_CHARACTER_WIDTH_PER_POINT
    return measuredWidth


def _PainterLineSpacing(painter):
    """Screen pixels from one line to the next in the font of *painter*
    (QFontMetrics.lineSpacing), or None when the painter gives no
    number."""
    try:
        return _FiniteNumber(painter.fontMetrics().lineSpacing())
    except Exception as measureError:
        ReportSuppressedError("sfmLogParser._PainterLineSpacing", measureError)
        return None


def _DrawTextInRectangle(painter, left, top, width, height, alignment, text):
    """Draw *text* in the rectangle, placed by the Qt alignment flags (Qt
    clips it to the rectangle)."""
    painter.drawText(QtCore.QRectF(left, top, width, height), alignment, text)


def _DrawVerticalText(painter, centreX, centreY, length, text, maximumLineCount=1):
    """Draw *text* turned to read upwards, centred on (centreX, centreY),
    as the title of a vertical axis, in up to *maximumLineCount* lines of
    at most *length* screen pixels, wrapped at word boundaries."""
    painter.save()
    painter.translate(centreX, centreY)
    painter.rotate(-90)
    if maximumLineCount > 1:
        thickness = 16.0 + AXIS_TITLE_LINE_HEIGHT * (maximumLineCount - 1)
        _DrawWrappedText(painter, -length / 2.0, -thickness / 2.0, length, thickness, text, QtCore.Qt.AlignCenter)
    else:
        painter.drawText(QtCore.QRectF(-length / 2.0, -8.0, length, 16.0), QtCore.Qt.AlignCenter, text)
    painter.restore()


def _DrawLine(painter, startX, startY, endX, endY):
    painter.drawLine(QtCore.QPointF(startX, startY), QtCore.QPointF(endX, endY))


def _ValueY(plotTop, plotHeight, scaleMaximum, value):
    """Screen y of *value* on a count axis from zero (bottom of the plot)
    to *scaleMaximum* (top)."""
    return plotTop + plotHeight - value / scaleMaximum * plotHeight


def _DrawCountAxis(painter, plotLeft, plotTop, plotWidth, plotHeight, tickValues, tickLabelWidth):
    """Dotted grid lines, tick labels (with thousands separators), and the
    left and bottom axis lines of a count axis from zero to the last tick."""
    painter.setFont(_Font(GRAPH_TICK_POINT_SIZE))
    for tickValue in tickValues:
        tickY = _ValueY(plotTop, plotHeight, tickValues[-1], tickValue)
        if tickValue > 0:
            painter.setPen(_Pen(GRID_COLOUR, 1, QtCore.Qt.DotLine))
            _DrawLine(painter, plotLeft, tickY, plotLeft + plotWidth, tickY)
        painter.setPen(_Pen(TICK_COLOUR))
        _DrawTextInRectangle(painter, plotLeft - tickLabelWidth, tickY - 6, tickLabelWidth - 4, 12,
                         QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter, _FormatNumber(tickValue))
    painter.setPen(_Pen(AXIS_COLOUR))
    _DrawLine(painter, plotLeft, plotTop, plotLeft, plotTop + plotHeight)
    _DrawLine(painter, plotLeft, plotTop + plotHeight, plotLeft + plotWidth, plotTop + plotHeight)


def _Diamond(centreX, centreY, radius):
    """QPolygonF of a diamond (a square standing on a corner) around the
    centre, the marker of the points of a step during sigma filtering."""
    return QtGui.QPolygonF([QtCore.QPointF(centreX, centreY - radius), QtCore.QPointF(centreX + radius, centreY),
                            QtCore.QPointF(centreX, centreY + radius), QtCore.QPointF(centreX - radius, centreY)])


def _DrawLegendSwatch(painter, swatchKind, colourComponents):
    """Swatch of a legend item at the origin: a line, a dashed line, a line
    with a round marker, a wide band, a vertical line, a hollow diamond, a
    filled, outlined, hatched, or shaded box, a range bar or a bar, a
    median mark, or the box of a selected run (filled or hatched, with a
    dark frame)."""
    if swatchKind == "band":
        bandPen = _Pen(colourComponents, 8)
        bandPen.setCapStyle(QtCore.Qt.FlatCap)
        painter.setPen(bandPen)
        painter.drawLine(0, 0, 14, 0)
        return
    if swatchKind == "verticalLine":
        painter.setPen(_Pen(colourComponents, 1.5))
        painter.drawLine(7, -5, 7, 5)
        return
    if swatchKind in ("line", "dashedLine", "lineMarker"):
        painter.setPen(_Pen(colourComponents, 2, QtCore.Qt.DashLine if swatchKind == "dashedLine"
                            else QtCore.Qt.SolidLine))
        painter.drawLine(0, 0, 14, 0)
        if swatchKind == "lineMarker":
            painter.setBrush(_Colour(colourComponents))
            painter.drawEllipse(QtCore.QPointF(7.0, 0.0), 3.0, 3.0)
        return
    if swatchKind == "diamond":
        painter.setPen(_Pen(colourComponents, 1.5))
        painter.setBrush(QtCore.Qt.NoBrush)
        painter.drawPolygon(_Diamond(7.0, 0.0, 4.5))
        return
    if swatchKind in ("rangeBar", "bar"):
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(_Colour(colourComponents))
        painter.drawRect(QtCore.QRectF(4.0, -6.0, 6.0, 12.0))
        return
    if swatchKind == "medianMark":
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(_Colour(colourComponents))
        painter.drawRect(QtCore.QRectF(2.0, -1.5, 10.0, 3.0))
        return
    if swatchKind == "outline":
        painter.setPen(_Pen(colourComponents, 2))
        painter.setBrush(QtCore.Qt.NoBrush)
    elif swatchKind in ("hatched", "selectedHatched"):
        painter.setPen(_Pen(SELECTED_RUN_OUTLINE_COLOUR, 2) if swatchKind == "selectedHatched"
                       else _Pen(colourComponents))
        painter.setBrush(QtGui.QBrush(_Colour(colourComponents), QtCore.Qt.BDiagPattern))
    elif swatchKind == "shade":
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(_Colour(colourComponents))
    elif swatchKind == "selectedBox":
        painter.setPen(_Pen(SELECTED_RUN_OUTLINE_COLOUR, 2))
        painter.setBrush(_Colour(colourComponents))
    else:
        painter.setPen(_Pen(FRAME_COLOUR))
        painter.setBrush(_Colour(colourComponents))
    painter.drawRect(QtCore.QRectF(1.0, -5.0, 12.0, 10.0))


def EstimatedLineCount(text, width, pointSize):
    """Lines that *text* takes when wrapped at *width* screen pixels, from
    a generous estimate of the width of its characters (0 for no text)."""
    if not text:
        return 0
    return max(1, int(math.ceil(len(text) * pointSize * ESTIMATED_CHARACTER_WIDTH_PER_POINT / max(width, 1.0))))


def NoteBandHeight(noteText, width, measuredHeight=None, lineSpacing=None):
    """Height in screen pixels of the band of the note under a graph: the
    height of the wrapped note in the font that draws it (*measuredHeight*,
    _MeasuredTextHeight), at most GRAPH_NOTE_MAXIMUM_LINE_COUNT lines of
    *lineSpacing* pixels, and a gap of 4 pixels below it, so that the band
    holds the note and nothing more.  Without a measurement (the Qt
    stand-ins of the offline tests), as many lines of
    GRAPH_TEXT_LINE_HEIGHT as the note needs at *width* by an estimate of
    the width of its characters (EstimatedLineCount), at most
    GRAPH_NOTE_MAXIMUM_LINE_COUNT."""
    if measuredHeight is not None:
        maximumHeight = GRAPH_NOTE_MAXIMUM_LINE_COUNT * (lineSpacing or GRAPH_TEXT_LINE_HEIGHT)
        return int(math.ceil(min(measuredHeight, maximumHeight))) + 4
    lineCount = min(GRAPH_NOTE_MAXIMUM_LINE_COUNT, EstimatedLineCount(noteText, width, GRAPH_TEXT_POINT_SIZE))
    return lineCount * GRAPH_TEXT_LINE_HEIGHT + 4


def ExplanationBandHeight(explanationText, width, measuredHeight=None):
    """Height in screen pixels of the band of the explanation under the
    title: the height of the wrapped explanation in the font that draws it
    (*measuredHeight*, _MeasuredTextHeight) and a gap of 2 pixels, so that
    the graph starts where the explanation ends, at any font and display
    scaling, and a long explanation on a narrow graph never runs into the
    plot.  Without a measurement (the Qt stand-ins of the offline tests),
    as many lines of GRAPH_TEXT_LINE_HEIGHT as the explanation needs at
    *width* by an estimate of the width of its characters
    (EstimatedLineCount)."""
    if measuredHeight is not None:
        return int(math.ceil(measuredHeight)) + 2
    return max(1, EstimatedLineCount(explanationText, width, GRAPH_TEXT_POINT_SIZE)) * GRAPH_TEXT_LINE_HEIGHT + 2


def WarningBandHeight(warningText, width, measuredHeight=None):
    """Height in screen pixels of the band of the warnings under the title
    of a graph (SfmGraphWidget.WarningText): the height of the wrapped
    warnings in the bold font that draws them (*measuredHeight*) and a gap
    of 4 pixels, estimated as for the explanation without a measurement,
    and 0 without warnings."""
    if not warningText:
        return 0
    if measuredHeight is not None:
        return int(math.ceil(measuredHeight)) + 4
    return max(1, EstimatedLineCount(warningText, width, GRAPH_TEXT_POINT_SIZE)) * GRAPH_TEXT_LINE_HEIGHT + 4


def PerImageTitleLeft(titleWidth, panelLeft, colourScaleLeft):
    """Left end, in screen pixels, of the title of the bars of the image
    pairs graph (and of the shared 3D tie point graph), *titleWidth*
    pixels long on one line in the caption row: above the bars, which
    start at *panelLeft*, while it ends CAPTION_TITLE_GAP pixels before
    the colour scale, which starts at *colourScaleLeft* with its own
    title, so that it may use the width of the bars and of the gap to the
    colour scale, and otherwise moved to the left by as much as it needs,
    above the gap between the matrix and the bars and, if need be, above
    the right end of the matrix."""
    return min(panelLeft, colourScaleLeft - CAPTION_TITLE_GAP - titleWidth)


def LegendRows(legendItems, availableWidth):
    """The legend items (swatch kind, colour, label) split into rows that
    fit *availableWidth* screen pixels, with estimated label widths."""
    legendRows = [[]]
    rowWidth = 0.0
    for legendItem in legendItems:
        itemWidth = (LEGEND_SWATCH_AND_GAP_WIDTH
                     + len(legendItem[2]) * GRAPH_TICK_POINT_SIZE * ESTIMATED_CHARACTER_WIDTH_PER_POINT)
        if legendRows[-1] and rowWidth + itemWidth > availableWidth:
            legendRows.append([])
            rowWidth = 0.0
        legendRows[-1].append(legendItem)
        rowWidth += itemWidth
    return legendRows


def LegendBottomSpace(legendItems, availableWidth):
    """Height in screen pixels below a plot for its tick labels, its axis
    title, and the rows of its legend (LegendRows), so that a legend that
    wraps into a third row on a narrow graph never runs into the
    note."""
    return 34 + max(1, len(LegendRows(legendItems, availableWidth))) * LEGEND_ROW_HEIGHT


def ImageLabelSpacing(largestLabelNumber):
    """Screen pixels between two image labels at least: the estimated
    width of the longest label plus a gap, and never less than
    MINIMUM_IMAGE_LABEL_SPACING, so that five-digit image numbers do not
    overlap."""
    return max(MINIMUM_IMAGE_LABEL_SPACING,
               len(str(largestLabelNumber)) * GRAPH_TICK_POINT_SIZE * ESTIMATED_CHARACTER_WIDTH_PER_POINT + 10.0)


def _DrawLegend(painter, left, baselineY, legendItems, availableWidth):
    """Draw the legend items (swatch kind, colour, label) in rows from
    *left* (LegendRows), each swatch followed by its label, as in the
    profile plots of the Interior Orientation Viewer.  Within a row the
    painter moves past each label by its width in the current font."""
    painter.setFont(_Font(GRAPH_TICK_POINT_SIZE))
    for rowIndex, rowItems in enumerate(LegendRows(legendItems, availableWidth)):
        painter.save()
        painter.translate(left, baselineY + rowIndex * LEGEND_ROW_HEIGHT)
        for swatchKind, colourComponents, label in rowItems:
            _DrawLegendSwatch(painter, swatchKind, colourComponents)
            painter.setPen(_Pen(TITLE_COLOUR))
            painter.drawText(18, 4, label)
            painter.translate(painter.fontMetrics().horizontalAdvance(label), 0)
            painter.translate(LEGEND_SWATCH_AND_GAP_WIDTH - 18, 0)
        painter.restore()


def _DrawSeries(painter, points, colourComponents, markerRadius, lineStyle=None):
    """Line through *points* (screen x, y) in the colour, solid unless
    *lineStyle* is given, with round markers of *markerRadius* pixels
    (none for 0)."""
    if not points:
        return
    painter.setPen(_Pen(colourComponents, 2, lineStyle))
    painter.setBrush(QtCore.Qt.NoBrush)
    path = QtGui.QPainterPath()
    path.moveTo(points[0][0], points[0][1])
    for pointX, pointY in points[1:]:
        path.lineTo(pointX, pointY)
    painter.drawPath(path)
    if markerRadius > 0:
        painter.setBrush(_Colour(colourComponents))
        for pointX, pointY in points:
            painter.drawEllipse(QtCore.QPointF(pointX, pointY), markerRadius, markerRadius)


def _DrawBand(painter, points, colourComponents, bandWidth, slotWidth):
    """Wide band through *points* (screen x, y), with flat ends at its
    first and last point, so that it never reaches into the next slot.
    A single point gets a short band across most of its slot of
    *slotWidth* screen pixels."""
    if not points:
        return
    if len(points) == 1:
        halfLength = max(slotWidth * 0.35, 1.0)
        points = [(points[0][0] - halfLength, points[0][1]), (points[0][0] + halfLength, points[0][1])]
    bandPen = _Pen(colourComponents, bandWidth)
    bandPen.setCapStyle(QtCore.Qt.FlatCap)
    bandPen.setJoinStyle(QtCore.Qt.RoundJoin)
    painter.setPen(bandPen)
    painter.setBrush(QtCore.Qt.NoBrush)
    path = QtGui.QPainterPath()
    path.moveTo(points[0][0], points[0][1])
    for pointX, pointY in points[1:]:
        path.lineTo(pointX, pointY)
    painter.drawPath(path)


def _MatrixCell(plotLeft, plotTop, slotSize, cellGap, rowImage, columnImage):
    """Rectangle of the matrix cell of the images in a row and a column,
    at least one screen pixel wide."""
    cellSize = max(slotSize - cellGap, 1.0)
    return QtCore.QRectF(plotLeft + columnImage * slotSize + cellGap / 2.0,
                         plotTop + rowImage * slotSize + cellGap / 2.0, cellSize, cellSize)


def _DrawColourScale(painter, barLeft, plotTop, barHeight, robustMinimum, robustMaximum, isBlockView=False,
                     title="Robust matches"):
    """Colour bar of the connectivity matrix: logarithmic, the fewest
    robust matches (or shared 3D tie points) at the bottom, with the values
    at its ends and its middle, its *title* in the caption row above the
    plot, and below it the scale, with "sum over the pairs of a cell" when
    the matrix shows groups of images."""
    painter.setFont(_Font(GRAPH_TEXT_POINT_SIZE, True))
    painter.setPen(_Pen(TITLE_COLOUR))
    _DrawTextInRectangle(painter, barLeft, plotTop - PAIRS_CAPTION_HEIGHT - 4, 160, PAIRS_CAPTION_HEIGHT,
                         QtCore.Qt.AlignLeft | QtCore.Qt.AlignBottom, title)
    painter.setFont(_Font(GRAPH_TICK_POINT_SIZE))
    barTop = plotTop
    rampStepCount = 64
    painter.setPen(QtCore.Qt.NoPen)
    for rampStepIndex in range(rampStepCount):
        painter.setBrush(_Colour(RampColourComponents(rampStepIndex / (rampStepCount - 1))))
        painter.drawRect(QtCore.QRectF(barLeft, barTop + barHeight - (rampStepIndex + 1) * barHeight / rampStepCount,
                                       14.0, barHeight / rampStepCount + 0.6))
    painter.setBrush(QtCore.Qt.NoBrush)
    painter.setPen(_Pen(FRAME_COLOUR))
    painter.drawRect(QtCore.QRectF(barLeft, barTop, 14.0, barHeight))
    if robustMinimum is None:
        return
    painter.setPen(_Pen(TICK_COLOUR))
    labelLeft = int(barLeft + 18)
    painter.drawText(labelLeft, int(barTop + 8), _FormatNumber(robustMaximum))
    if robustMaximum > robustMinimum:
        painter.drawText(labelLeft, int(barTop + barHeight / 2.0 + 4),
                         _FormatNumber(int(round(math.sqrt(robustMinimum * robustMaximum)))))
        painter.drawText(labelLeft, int(barTop + barHeight), _FormatNumber(robustMinimum))
    painter.setPen(_Pen(AXIS_TITLE_COLOUR))
    scaleLines = ["sum over the pairs", "of a cell", "(logarithmic)"] if isBlockView else ["(logarithmic)"]
    for lineIndex, scaleLine in enumerate(scaleLines):
        painter.drawText(int(barLeft), int(barTop + barHeight + 14 + 12 * lineIndex), scaleLine)


class SfmGraphWidget(QtWidgets.QWidget):
    """Base of the graphs of the SfM Log Parser, drawn with QPainter in the
    style of the plots of the other tools: a bold title (with the run
    shown when the log holds several runs), a line that says what the
    graph shows and what to look for, the graph, and a short note that
    names its extremes, with the full note (DetailText) as the tooltip
    of the graph.  Before a log is parsed, and when the log lacks the
    lines of the graph, a message that names them takes the place of the
    graph."""

    TAB_LABEL = ""
    TITLE = ""
    EXPLANATION = ""
    MISSING_LINES_MESSAGE = ""
    MINIMUM_HEIGHT = GRAPH_MINIMUM_HEIGHT
    # True for a graph that shows every run of the log (Runs side by side).
    SHOWS_ALL_RUNS = False
    # True for a graph that shows the open chunk, not a run of the log
    # (Shared tie points).
    SHOWS_OPEN_CHUNK = False

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(GRAPH_MINIMUM_WIDTH, self.MINIMUM_HEIGHT)
        self.runs = None
        self.summarisedRunIndex = None
        self.graphData = None
        self.valueCache = {}

    def SetRuns(self, runs, summarisedRunIndex):
        """Take the runs of a parsed log (ParseSfmRuns) and the index of the
        run chosen in the list of runs, compute the values of the graph,
        and repaint."""
        self.runs = runs
        self.summarisedRunIndex = summarisedRunIndex
        self.valueCache = {}
        self.graphData = self.GraphData(runs, summarisedRunIndex)
        self.setToolTip(self.DetailText() if self.graphData is not None else self.MissingLinesMessage())
        self.update()

    def MissingLinesMessage(self):
        """The message in the place of the graph when its data are missing
        (MISSING_LINES_MESSAGE)."""
        return self.MISSING_LINES_MESSAGE

    def CachedValue(self, cacheKey, computeFunction, *arguments):
        """computeFunction(*arguments), computed once per parsed run and
        *cacheKey*: the paint event runs at every resize, and the blocks of
        a log of 50,000 images take a noticeable time to compute."""
        valueCache = getattr(self, "valueCache", None)
        if valueCache is None:
            valueCache = self.valueCache = {}
        if cacheKey not in valueCache:
            valueCache[cacheKey] = computeFunction(*arguments)
        return valueCache[cacheKey]

    def GraphData(self, runs, summarisedRunIndex):
        """Values of the graph, or None when the log lacks its lines."""
        return None

    def NoteText(self):
        """Note under the graph, which names its extremes."""
        return ""

    def DetailText(self):
        """Full note, the tooltip of the graph: the note under the graph,
        unless a graph keeps lists out of that note."""
        return self.NoteText()

    def ExplanationText(self, areaWidth):
        """Line above the graph of a parsed run that has the lines of the
        graph, drawn *areaWidth* screen pixels wide: EXPLANATION, unless a
        graph builds it from what it draws."""
        return self.EXPLANATION

    def TitleText(self):
        """Title, with the run that the graph shows when the log holds
        several runs."""
        if self.runs and len(self.runs) > 1:
            return f"{self.TITLE} (run {self.summarisedRunIndex + 1} of {len(self.runs)})"
        return self.TITLE

    def DrawGraph(self, painter, areaLeft, areaTop, areaWidth, areaHeight):
        """Draw the graph in the area between the explanation and the note."""

    def WarningText(self):
        """Warnings of a parsed log, drawn in bold and in WARNING_COLOUR
        under the title, so that they stand out (none for the graphs of
        the log, see SharedTiePointGraph.WarningText)."""
        return ""

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        width = self.width()
        height = self.height()
        painter.setFont(_Font(GRAPH_TITLE_POINT_SIZE, True))
        painter.setPen(_Pen(TITLE_COLOUR))
        painter.drawText(GRAPH_SIDE_MARGIN, GRAPH_TITLE_BASELINE, self.TitleText())
        areaWidth = width - 2 * GRAPH_SIDE_MARGIN
        # The bands of the warnings, the explanation, and the note are as
        # tall as the painter measures their wrapped texts in the fonts
        # that draw them, so that no band is left empty, on the screen and
        # in the picture of Copy graph and Save graph as PNG alike.
        explanationTop = GRAPH_EXPLANATION_TOP
        warningText = self.WarningText() if self.runs is not None else ""
        if warningText:
            painter.setFont(_Font(GRAPH_TEXT_POINT_SIZE, True))
            painter.setPen(_Pen(WARNING_COLOUR))
            warningHeight = WarningBandHeight(warningText, areaWidth,
                                              _MeasuredTextHeight(painter, areaWidth, warningText))
            _DrawWrappedText(painter, GRAPH_SIDE_MARGIN, explanationTop, areaWidth, warningHeight, warningText)
            explanationTop += warningHeight
        painter.setFont(_Font(GRAPH_TEXT_POINT_SIZE))
        painter.setPen(_Pen(TEXT_COLOUR))
        # Before a log is parsed and when the log lacks the lines of the
        # graph, the explanation says in general what the graph shows,
        # otherwise it may name what is drawn (ExplanationText).
        explanationText = (self.EXPLANATION if self.runs is None or self.graphData is None
                           else self.ExplanationText(areaWidth))
        explanationHeight = ExplanationBandHeight(explanationText, areaWidth,
                                                  _MeasuredTextHeight(painter, areaWidth, explanationText))
        _DrawWrappedText(painter, GRAPH_SIDE_MARGIN, explanationTop, areaWidth, explanationHeight,
                         explanationText)
        areaTop = explanationTop + explanationHeight + 6
        if self.runs is None or self.graphData is None:
            painter.setFont(_Font(GRAPH_TITLE_POINT_SIZE))
            painter.setPen(_Pen(MESSAGE_COLOUR))
            _DrawWrappedText(painter, GRAPH_SIDE_MARGIN + 8, areaTop + 8, areaWidth - 16,
                             120, NO_LOG_MESSAGE if self.runs is None else self.MissingLinesMessage())
            painter.end()
            return
        noteText = self.NoteText()
        noteHeight = NoteBandHeight(noteText, areaWidth, _MeasuredTextHeight(painter, areaWidth, noteText),
                                    _PainterLineSpacing(painter))
        areaHeight = height - areaTop - noteHeight - 6
        if areaWidth >= 200 and areaHeight >= 120:
            self.DrawGraph(painter, GRAPH_SIDE_MARGIN, areaTop, areaWidth, areaHeight)
        painter.setFont(_Font(GRAPH_TEXT_POINT_SIZE))
        painter.setPen(_Pen(TEXT_COLOUR))
        # The note fills its band but for the gap below it, so that a note
        # longer than the band ends after a whole line.
        _DrawWrappedText(painter, GRAPH_SIDE_MARGIN, height - noteHeight, areaWidth, noteHeight - 4, noteText)
        painter.end()


class PairConnectivityGraph(SfmGraphWidget):
    """Graph 1: which image pairs the log of the selected run reports,
    and how strongly: the image pair connectivity matrix, with, in the
    rows of the matrix, the other images with which the log reports at
    least one robust match for each image.  With more images than fit as
    cells of MINIMUM_MATRIX_CELL_SIZE screen pixels, the matrix and the
    bars show groups of consecutive image numbers.  The words of the
    axes, the colour scale, the bars, and the legend are class
    attributes, so that the graph of the shared 3D tie points of the open
    chunk (SharedTiePointGraph) draws its own matrix in the same way."""

    TAB_LABEL = "Image pairs"
    TITLE = "Image pair connectivity"
    # In general, before a log is parsed and when the log lacks the lines
    # of the graph (PairConnectivityExplanation names what is drawn).
    EXPLANATION = ("Which image pairs the log reports, and how strongly: a matrix of the image pairs that the log "
                   "reports, coloured by their robust matches, with bars that count, for each image, the other images "
                   "with which the log reports at least one robust match. Look for images that share robust matches "
                   "with few other images, for clusters of images without robust matches between them, and for image "
                   "pairs with many candidate matches but few robust ones.")
    MISSING_LINES_MESSAGE = ("No image pair reported in the log of this run, so there is nothing to draw. The About "
                             "names the log lines that this graph reads.")
    MINIMUM_HEIGHT = PAIRS_GRAPH_MINIMUM_HEIGHT
    AXIS_TEXT = AXIS_TEXT_IMAGE
    COLOUR_SCALE_TITLE = "Robust matches"
    PER_IMAGE_TITLE = "Other images with robust matches"
    WHITE_CELL_TEXT = "image pair not reported in the log"
    WHITE_GROUP_CELL_TEXT = "no reported image pair between two groups"
    FLAGGED_IMAGE_TEXT = "image " + NO_REPORTED_IMAGE_PAIR_TEXT
    FLAGGED_GROUP_TEXT = "group with an image " + NO_REPORTED_IMAGE_PAIR_TEXT
    NO_OTHER_IMAGE_TEXT = "no other image with robust matches"

    def GraphData(self, runs, summarisedRunIndex):
        return PairConnectivityData(runs[summarisedRunIndex]["counts"])

    def ExplanationText(self, areaWidth):
        return PairConnectivityExplanation(self.graphData)

    def NoteText(self):
        return PairConnectivityShortNote(self.graphData)

    def DetailText(self):
        return PairConnectivityNote(self.graphData)

    def DrawGraph(self, painter, areaLeft, areaTop, areaWidth, areaHeight):
        data = self.graphData
        imageCount = data["imageCount"]
        axisTitleWidth = 16
        tickLabelWidth = 40
        captionHeight = PAIRS_CAPTION_HEIGHT
        panelGap = 18
        colourScaleWidth = 104
        perImagePanelWidth = min(220.0, max(110.0, areaWidth * 0.2))
        legendWidth = areaWidth - axisTitleWidth - tickLabelWidth
        # Room below the plot for a second line of the axis title and the
        # longer of the two legends (single images or blocks), which
        # LegendBottomSpace sizes by its rows.
        bottomSpace = AXIS_TITLE_LINE_HEIGHT + max(
            LegendBottomSpace(self.PairLegendItems(False, True, True, True), legendWidth),
            LegendBottomSpace(self.PairLegendItems(True, False, True, True), legendWidth))
        plotSize = min(areaWidth - axisTitleWidth - tickLabelWidth - 2 * panelGap - perImagePanelWidth
                       - colourScaleWidth, areaHeight - captionHeight - bottomSpace - 4)
        if plotSize < 60:
            return
        plotLeft = areaLeft + axisTitleWidth + tickLabelWidth
        plotTop = areaTop + captionHeight + 4
        # Images in groups of consecutive image numbers when single images
        # would get cells of fewer than MINIMUM_MATRIX_CELL_SIZE pixels.
        blockSize = BlockSize(imageCount, plotSize, MINIMUM_MATRIX_CELL_SIZE)
        isBlockView = blockSize > 1
        slotCount = (imageCount + blockSize - 1) // blockSize
        slotSize = plotSize / slotCount
        cellGap = 1.0 if slotSize >= 8.0 else 0.0
        imagesWithoutPairLine = set(data["imagesWithoutPairLine"])
        slotsWithoutPairLine = sorted({imageNumber // blockSize for imageNumber in imagesWithoutPairLine})

        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(_Colour(NO_PAIR_LINE_COLOUR))
        painter.drawRect(QtCore.QRectF(plotLeft, plotTop, plotSize, plotSize))
        painter.setBrush(_Colour(IMAGE_WITHOUT_PAIR_LINE_COLOUR))
        for slotIndex in slotsWithoutPairLine:
            painter.drawRect(QtCore.QRectF(plotLeft, plotTop + slotIndex * slotSize, plotSize, slotSize))
            painter.drawRect(QtCore.QRectF(plotLeft + slotIndex * slotSize, plotTop, slotSize, plotSize))
        outlinesLowShares = False
        if isBlockView:
            # A cell holds the pairs between two groups of images (on the
            # diagonal, the pairs within one group), coloured by the sum
            # of their robust matches.
            blockCells = self.CachedValue(("blockCells", blockSize), ConnectivityBlockCells, data, blockSize)
            positiveSums = [robustMatches for _, robustMatches in blockCells.values() if robustMatches > 0]
            colourMinimum = min(positiveSums) if positiveSums else None
            colourMaximum = max(positiveSums) if positiveSums else None
            for (rowSlot, columnSlot), (_, robustMatches) in blockCells.items():
                painter.setBrush(_Colour(RampColourComponents(
                    RobustMatchColourFraction(robustMatches, colourMinimum or 1, colourMaximum or 1))))
                painter.drawRect(_MatrixCell(plotLeft, plotTop, slotSize, cellGap, rowSlot, columnSlot))
            showsWhiteCell = MatrixShowsWhiteCell(data, blockSize, blockCells)
        else:
            showsWhiteCell = HasWhiteImagePair(data)
            colourMinimum, colourMaximum = data["robustMinimum"], data["robustMaximum"]
            painter.setBrush(_Colour(DIAGONAL_COLOUR))
            for imageNumber in range(imageCount):
                painter.drawRect(_MatrixCell(plotLeft, plotTop, slotSize, cellGap, imageNumber, imageNumber))
            # The pairs with the most robust matches are drawn last.
            for firstImage, secondImage, robustMatchCount, _ in sorted(data["pairs"], key=lambda pair: pair[2]):
                painter.setBrush(_Colour(RampColourComponents(
                    RobustMatchColourFraction(robustMatchCount, colourMinimum or 1, colourMaximum or 1))))
                painter.drawRect(_MatrixCell(plotLeft, plotTop, slotSize, cellGap, firstImage, secondImage))
                painter.drawRect(_MatrixCell(plotLeft, plotTop, slotSize, cellGap, secondImage, firstImage))
            outlinesLowShares = slotSize >= MINIMUM_OUTLINED_CELL_SIZE and bool(data["lowSharePairs"])
            if outlinesLowShares:
                painter.setBrush(QtCore.Qt.NoBrush)
                painter.setPen(_Pen(LOW_SHARE_OUTLINE_COLOUR, LOW_SHARE_OUTLINE_WIDTH))
                for firstImage, secondImage, _, _ in data["lowSharePairs"]:
                    painter.drawRect(_MatrixCell(plotLeft, plotTop, slotSize, cellGap, firstImage, secondImage))
                    painter.drawRect(_MatrixCell(plotLeft, plotTop, slotSize, cellGap, secondImage, firstImage))
        painter.setBrush(QtCore.Qt.NoBrush)
        painter.setPen(_Pen(FRAME_COLOUR))
        painter.drawRect(QtCore.QRectF(plotLeft, plotTop, plotSize, plotSize))

        # Image numbers (of the first image of a group): every image or
        # group while the labels fit, then every 2nd, 5th, 10th, and so on.
        # Images without a pair line in red.
        painter.setFont(_Font(GRAPH_TICK_POINT_SIZE))
        labelStep = LabelStep(slotSize, ImageLabelSpacing((slotCount - 1) * blockSize))
        flaggedSlots = set(slotsWithoutPairLine)
        for slotIndex in range(0, slotCount, labelStep):
            painter.setPen(_Pen(LOW_SHARE_OUTLINE_COLOUR if slotIndex in flaggedSlots and not isBlockView
                                else TICK_COLOUR))
            slotCentre = (slotIndex + 0.5) * slotSize
            labelText = str(slotIndex * blockSize)
            _DrawTextInRectangle(painter, plotLeft + slotCentre - 24, plotTop + plotSize + 2, 48, 12,
                                 QtCore.Qt.AlignHCenter | QtCore.Qt.AlignTop, labelText)
            _DrawTextInRectangle(painter, plotLeft - tickLabelWidth, plotTop + slotCentre - 6, tickLabelWidth - 4, 12,
                                 QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter, labelText)
        axisText = (f"{self.AXIS_TEXT}, in groups of {blockSize} consecutive images" if isBlockView
                    else self.AXIS_TEXT)
        # The titles of both image axes take a second line where the
        # matrix is narrower than they are.
        painter.setPen(_Pen(AXIS_TITLE_COLOUR))
        _DrawWrappedText(painter, plotLeft - 40, plotTop + plotSize + 15, plotSize + 80, 14 + AXIS_TITLE_LINE_HEIGHT,
                         axisText, QtCore.Qt.AlignHCenter | QtCore.Qt.AlignTop)
        _DrawVerticalText(painter, areaLeft + 7, plotTop + plotSize / 2.0, plotSize + 40, axisText, 2)

        panelLeft = plotLeft + plotSize + panelGap
        colourScaleLeft = panelLeft + perImagePanelWidth + panelGap
        hasImageWithoutRobustPair = self.DrawPairsPerImage(painter, panelLeft, plotTop, perImagePanelWidth,
                                                           plotSize, blockSize, slotSize, colourScaleLeft)
        _DrawColourScale(painter, colourScaleLeft, plotTop, min(plotSize - 50, 240.0),
                         colourMinimum, colourMaximum, isBlockView, self.COLOUR_SCALE_TITLE)

        _DrawLegend(painter, plotLeft, plotTop + plotSize + 40 + AXIS_TITLE_LINE_HEIGHT,
                    self.PairLegendItems(isBlockView, outlinesLowShares, hasImageWithoutRobustPair, showsWhiteCell),
                    legendWidth)

    def PairLegendItems(self, isBlockView, outlinesLowShares, hasImageWithoutRobustPair, showsWhiteCell):
        """Legend of the image pairs graph: the encodings that it shows, the
        white cells only when the matrix shows one (MatrixShowsWhiteCell)
        and the red outlines only when one is drawn."""
        legendItems = []
        if showsWhiteCell:
            legendItems.append(("box", NO_PAIR_LINE_COLOUR,
                                self.WHITE_GROUP_CELL_TEXT if isBlockView else self.WHITE_CELL_TEXT))
        if self.graphData["imagesWithoutPairLine"]:
            legendItems.append(("box", IMAGE_WITHOUT_PAIR_LINE_COLOUR,
                                self.FLAGGED_GROUP_TEXT if isBlockView else self.FLAGGED_IMAGE_TEXT))
        if outlinesLowShares:
            legendItems.append(("outline", LOW_SHARE_OUTLINE_COLOUR, LowShareLegendText()))
        if isBlockView:
            legendItems.append(("rangeBar", RANGE_COLOUR, "range over the images of a group"))
            legendItems.append(("medianMark", PAIRS_PER_IMAGE_COLOUR, "median of a group"))
        if hasImageWithoutRobustPair:
            legendItems.append(("box", LOW_SHARE_OUTLINE_COLOUR, self.NO_OTHER_IMAGE_TEXT))
        return legendItems

    def DrawPairsPerImage(self, painter, panelLeft, plotTop, panelWidth, plotSize, blockSize, slotSize,
                          colourScaleLeft):
        """Horizontal bars in the rows of the matrix: for each image of the
        log, the other images with which it has at least one robust match
        (its image pairs with robust matches), or, for groups of images,
        the range (fewest to most) and the median of the images of each
        group.  An image without any, or a group that holds one, gets a
        red mark at zero.  The title of the bars takes one line in the
        caption row, before the colour scale at *colourScaleLeft*
        (PerImageTitleLeft).  Returns whether any image has none."""
        blocks = self.CachedValue(("pairBlocks", blockSize), PairCountBlocks, self.graphData, blockSize)
        tickValues = TickValues(max([block["maximum"] for block in blocks] + [1]) * COUNT_AXIS_HEADROOM,
                                max(2, int(panelWidth // 30)))
        scaleMaximum = tickValues[-1]
        painter.setFont(_Font(GRAPH_TEXT_POINT_SIZE, True))
        painter.setPen(_Pen(TITLE_COLOUR))
        # The title on one line, in the caption row above the plot, as wide
        # as the painter measures it, on the baseline of the title of the
        # colour scale (_DrawColourScale).
        titleWidth = _MeasuredTextWidth(painter, self.PER_IMAGE_TITLE, GRAPH_TEXT_POINT_SIZE)
        _DrawTextInRectangle(painter, PerImageTitleLeft(titleWidth, panelLeft, colourScaleLeft),
                             plotTop - PAIRS_CAPTION_HEIGHT - 4, titleWidth + 4, PAIRS_CAPTION_HEIGHT,
                             QtCore.Qt.AlignLeft | QtCore.Qt.AlignBottom, self.PER_IMAGE_TITLE)
        painter.setFont(_Font(GRAPH_TICK_POINT_SIZE))
        for tickValue in tickValues:
            tickX = panelLeft + tickValue / scaleMaximum * panelWidth
            if tickValue > 0:
                painter.setPen(_Pen(GRID_COLOUR, 1, QtCore.Qt.DotLine))
                _DrawLine(painter, tickX, plotTop, tickX, plotTop + plotSize)
            painter.setPen(_Pen(TICK_COLOUR))
            _DrawTextInRectangle(painter, tickX - 20, plotTop + plotSize + 2, 40, 12,
                                 QtCore.Qt.AlignHCenter | QtCore.Qt.AlignTop, _FormatNumber(tickValue))
        painter.setPen(QtCore.Qt.NoPen)
        hasImageWithoutRobustPair = False
        for block in blocks:
            slotTop = plotTop + block["blockIndex"] * slotSize
            if blockSize == 1:
                barHeight = slotSize * 0.7 if slotSize >= 4.0 else slotSize
                barTop = slotTop + (slotSize - barHeight) / 2.0
                painter.setBrush(_Colour(PAIRS_PER_IMAGE_COLOUR))
                painter.drawRect(QtCore.QRectF(panelLeft, barTop, block["maximum"] / scaleMaximum * panelWidth,
                                               barHeight))
            else:
                barHeight = max(1.0, slotSize * 0.6)
                barTop = slotTop + (slotSize - barHeight) / 2.0
                rangeLeft = panelLeft + block["minimum"] / scaleMaximum * panelWidth
                rangeRight = panelLeft + block["maximum"] / scaleMaximum * panelWidth
                painter.setBrush(_Colour(RANGE_COLOUR))
                painter.drawRect(QtCore.QRectF(rangeLeft, barTop, max(rangeRight - rangeLeft, 1.5), barHeight))
                painter.setBrush(_Colour(PAIRS_PER_IMAGE_COLOUR))
                painter.drawRect(QtCore.QRectF(panelLeft + block["median"] / scaleMaximum * panelWidth - 1.0,
                                               slotTop + slotSize * 0.05, 2.0, max(1.0, slotSize * 0.9)))
            if block["minimum"] == 0:
                hasImageWithoutRobustPair = True
                painter.setBrush(_Colour(LOW_SHARE_OUTLINE_COLOUR))
                painter.drawRect(QtCore.QRectF(panelLeft, barTop, 3.0, barHeight))
        painter.setPen(_Pen(AXIS_COLOUR))
        _DrawLine(painter, panelLeft, plotTop, panelLeft, plotTop + plotSize)
        _DrawLine(painter, panelLeft, plotTop + plotSize, panelLeft + panelWidth, plotTop + plotSize)
        painter.setPen(_Pen(AXIS_TITLE_COLOUR))
        _DrawTextInRectangle(painter, panelLeft - 10, plotTop + plotSize + 15, panelWidth + 20, 14,
                             QtCore.Qt.AlignHCenter | QtCore.Qt.AlignTop, "other images (count)")
        return hasImageWithoutRobustPair


class SharedTiePointGraph(PairConnectivityGraph):
    """Graph 6: which enabled, oriented images of the open chunk share
    valid 3D tie points, and how many (SharedTiePointData), read from the
    open chunk when a log is parsed.  It draws its matrix and its bars
    as the image pairs graph does, in its own words, and shows the same
    matrix whichever run is chosen in the list of runs."""

    TAB_LABEL = "Shared tie points"
    TITLE = "Shared 3D tie points of the open chunk"
    EXPLANATION = ("Which images of the open chunk share valid 3D tie points, and how many: a matrix of the image "
                   "pairs, coloured by the valid 3D tie points that both images observe, with bars that count, for "
                   "each image, the other images with which it shares at least one. The matrix is read from the open "
                   "chunk when a log is parsed, and it does not depend on the log.")
    MISSING_LINES_MESSAGE = ("The open project has no active chunk with tie points on at least two enabled, oriented "
                             "images, so there is nothing to draw.")
    AXIS_TEXT = "image (number among the enabled, oriented images of the open chunk)"
    COLOUR_SCALE_TITLE = "Shared 3D tie points"
    PER_IMAGE_TITLE = "Other images with shared 3D tie points"
    WHITE_CELL_TEXT = "image pair without a shared 3D tie point"
    WHITE_GROUP_CELL_TEXT = "no shared 3D tie point between two groups"
    FLAGGED_IMAGE_TEXT = "image " + SHARES_NO_TIE_POINT_TEXT
    FLAGGED_GROUP_TEXT = "group with an image " + SHARES_NO_TIE_POINT_TEXT
    NO_OTHER_IMAGE_TEXT = "no other image with shared 3D tie points"
    # The graph shows the open chunk, not a run of the log
    # (GraphWidgetConsoleName and the file name of Save graph as PNG).
    SHOWS_OPEN_CHUNK = True
    # The open chunk (ReadOpenChunk or LeftOutOpenChunk), its
    # SharedTiePointData, and the error in words when it could not be read,
    # set by HandOpenChunkToGraphs.
    openChunk = None
    openChunkData = None
    openChunkReadFailure = None
    READ_FAILURE_MESSAGE = ("The open chunk could not be read (the Console names the error), so there is nothing to "
                            "draw.")
    LEFT_OUT_MESSAGE = (f"The open chunk is left out, so there is nothing to draw. Untick {LEAVE_OUT_OPEN_CHUNK_TEXT} "
                        "above the tabs to include it.")

    def GraphData(self, runs, summarisedRunIndex):
        return self.openChunkData

    def MissingLinesMessage(self):
        """LEFT_OUT_MESSAGE when the open chunk is left out, READ_FAILURE_MESSAGE
        when it could not be read, otherwise MISSING_LINES_MESSAGE."""
        if self.openChunk is not None and self.openChunk["isLeftOut"]:
            return self.LEFT_OUT_MESSAGE
        return self.READ_FAILURE_MESSAGE if self.openChunkReadFailure is not None else self.MISSING_LINES_MESSAGE

    def ShownRun(self):
        """The run chosen in the list of runs, or None before a log is
        parsed."""
        if not self.runs or self.summarisedRunIndex is None or not 0 <= self.summarisedRunIndex < len(self.runs):
            return None
        return self.runs[self.summarisedRunIndex]

    def WarningText(self):
        """The warnings that the run chosen in the list of runs may belong to
        another chunk or project than the open chunk (OpenChunkWarnings),
        one sentence each, drawn in red under the title."""
        shownRun = self.ShownRun()
        if shownRun is None or self.openChunk is None:
            return ""
        return " ".join(OpenChunkFindingSentence(warning) for warning in OpenChunkWarnings(shownRun, self.openChunk))

    def SetRuns(self, runs, summarisedRunIndex):
        """As for every graph (SfmGraphWidget.SetRuns), but the values that
        the matrix computed, such as its groups of images, are kept while
        the open chunk data stay the same, because they do not depend on
        the run."""
        keptCache = (self.valueCache if self.openChunkData is not None and self.graphData is self.openChunkData
                     else None)
        PairConnectivityGraph.SetRuns(self, runs, summarisedRunIndex)
        if keptCache is not None:
            self.valueCache = keptCache

    def TitleText(self):
        return self.TITLE

    def ExplanationText(self, areaWidth):
        return SharedTiePointExplanation(self.graphData, self.openChunk, self.ShownRun())

    def NoteText(self):
        return SharedTiePointShortNote(self.graphData)

    def DetailText(self):
        return SharedTiePointNote(self.graphData)


def _StepCentreX(plotLeft, slotWidth, firstStep, stepNumber):
    """Screen x of the centre of the slot of a triangulation step (the
    first slot belongs to *firstStep*, 0 when a pt: line comes before the
    first step)."""
    return plotLeft + (stepNumber - firstStep + 0.5) * slotWidth


# The words with which the explanation above the growth graph and its
# legend name its elements, by colour and shape, so that both use the
# same words (Geert: "Maybe say blue dots, brown dots").  The colour
# names fit the RGB values of TIE_POINT_COLOUR, ADDED_POINT_BAND_COLOUR,
# SIGMA_FILTERING_SHADE_COLOUR, ADDED_POINT_COLOUR, and FAR_POINT_COLOUR.
TIE_POINT_GROWTH_ELEMENT_WORDS = {
    "validLine": "blue line",
    "validDots": "blue dots",
    "addedBand": "pale orange band",
    "sigmaSpan": "light blue span",
    "sigmaDiamonds": "hollow orange diamonds",
    "sigmaRangeLines": "orange vertical lines",
    "farPointBars": "dark red bars",
}


def TiePointGrowthDrawnElements(data, plotWidth):
    """What the graph of the growth of the 3D tie points draws for the
    values *data* of a run in a plot *plotWidth* screen pixels wide: the
    first step of the step axis (0 when a pt: line comes before the
    first step), the width of the slot of a step, the steps per block
    (1 for single steps), and, as booleans (TIE_POINT_GROWTH_ELEMENT_WORDS),
    the blue line of the valid 3D tie points, its blue dots (only when
    the slots are at least MINIMUM_MARKER_SLOT_WIDTH pixels wide) and
    whether the line runs outside and inside the light blue span, the
    pale orange band of the 3D tie points added so far, the light blue
    span of sigma filtering, its hollow orange diamonds (single steps) or
    orange vertical lines (blocks of steps), and the dark red bars of the
    far points.  The drawing, the explanation, and the legend all follow
    it, so that the texts name exactly what is drawn."""
    validPointSteps = data["validPointSteps"]
    firstStep = 0 if any(stepNumber == 0 for stepNumber, _ in validPointSteps) else 1
    slotCount = data["stepCount"] - firstStep + 1
    slotWidth = plotWidth / slotCount
    stepBlockSize = BlockSize(slotCount, plotWidth, MINIMUM_STEP_SLOT_WIDTH)
    firstSigmaFilteringStep = data["firstSigmaFilteringStep"]
    hasSigmaFilteringSpan = firstSigmaFilteringStep is not None and bool(data["addedPointStepsDuringSigmaFiltering"])
    return {
        "firstStep": firstStep,
        "slotWidth": slotWidth,
        "stepBlockSize": stepBlockSize,
        "validLine": bool(validPointSteps),
        "validDots": bool(validPointSteps) and slotWidth >= MINIMUM_MARKER_SLOT_WIDTH,
        "validLineOutsideSpan": any(not hasSigmaFilteringSpan or stepNumber < firstSigmaFilteringStep
                                    for stepNumber, _ in validPointSteps),
        "validLineInsideSpan": hasSigmaFilteringSpan and any(stepNumber >= firstSigmaFilteringStep
                                                             for stepNumber, _ in validPointSteps),
        "addedBand": bool(data["cumulativeAddedPointCountsBeforeSigmaFiltering"]),
        "sigmaSpan": hasSigmaFilteringSpan,
        "sigmaDiamonds": hasSigmaFilteringSpan and stepBlockSize == 1,
        "sigmaRangeLines": hasSigmaFilteringSpan and stepBlockSize > 1,
        "farPointBars": any(farPointCount for farPointCount in data["farPointCounts"] if farPointCount is not None),
    }


def TiePointGrowthExplanation(data, drawnElements):
    """Line above the growth graph, built from what it draws
    (TiePointGrowthDrawnElements): it names each element by its colour
    and shape (TIE_POINT_GROWTH_ELEMENT_WORDS) and leaves out what is not
    drawn, such as the band without a step before sigma filtering or the
    dots of steps that are too close."""
    words = TIE_POINT_GROWTH_ELEMENT_WORDS
    hasSigmaFilteringLine = data["firstSigmaFilteringStep"] is not None
    stepBlockSize = drawnElements["stepBlockSize"]
    sentences = ["The 3D tie points at each triangulation step (one step per line “adding N points” of the log)."]
    if drawnElements["validLine"]:
        if drawnElements["validDots"]:
            subjectText = (f"The {words['validLine']} and its {words['validDots']}, one dot per valid count in "
                           "the log, give")
        else:
            subjectText = f"The {words['validLine']} gives"
        placeTexts = []
        if drawnElements["sigmaSpan"]:
            placeTexts = [placeText for placeText, isDrawnThere in (
                ("outside", drawnElements["validLineOutsideSpan"]), ("inside", drawnElements["validLineInsideSpan"]))
                if isDrawnThere]
        placeText = f", {' and '.join(placeTexts)} the {words['sigmaSpan']}" if placeTexts else ""
        sentences.append(f"{subjectText} the valid 3D tie points after each step{placeText}.")
    if drawnElements["addedBand"]:
        sentences.append(f"The {words['addedBand']}"
                         + (f" under the {words['validLine']}" if drawnElements["validLine"] else "")
                         + " gives the 3D tie points added so far"
                         + (" before sigma filtering" if hasSigmaFilteringLine else "") + ".")
    if drawnElements["sigmaSpan"]:
        spanText = f"The {words['sigmaSpan']} is sigma filtering, from step {data['firstSigmaFilteringStep']}"
        if drawnElements["sigmaDiamonds"]:
            spanText += (f", and its {words['sigmaDiamonds']} give the 3D tie points of each of its steps, which are "
                         "not summed")
        elif drawnElements["sigmaRangeLines"]:
            spanText += (f", and its {words['sigmaRangeLines']} give the fewest to the most 3D tie points of the "
                         f"steps in each group of {stepBlockSize} steps, which are not summed")
        sentences.append(spanText + ".")
    if drawnElements["farPointBars"]:
        sentences.append(f"The {words['farPointBars']} below give the far points rejected in "
                         + ("each step." if stepBlockSize == 1 else f"each group of {stepBlockSize} steps."))
    lookForTexts = []
    if drawnElements["farPointBars"]:
        lookForTexts.append("steps with many far points")
    if drawnElements["validLine"]:
        lookForTexts.append("how the valid count changes during sigma filtering" if drawnElements["sigmaSpan"]
                            else "how the valid count grows from step to step")
    elif drawnElements["addedBand"]:
        lookForTexts.append("how the 3D tie points grow from step to step")
    if lookForTexts:
        sentences.append("Look for " + " and for ".join(lookForTexts) + ".")
    return " ".join(sentences)


def TiePointGrowthLegendItems(data, drawnElements):
    """Legend of the growth graph (swatch kind, colour, label): the
    elements that it draws (TiePointGrowthDrawnElements), each named by
    the colour and shape words of the explanation."""
    words = TIE_POINT_GROWTH_ELEMENT_WORDS
    legendItems = []
    if drawnElements["validLine"]:
        if drawnElements["validDots"]:
            legendItems.append(("lineMarker", TIE_POINT_COLOUR,
                                f"{words['validLine']} and {words['validDots']}: valid 3D tie points"))
        else:
            legendItems.append(("line", TIE_POINT_COLOUR, f"{words['validLine']}: valid 3D tie points"))
    if drawnElements["addedBand"]:
        legendItems.append(("band", ADDED_POINT_BAND_COLOUR, f"{words['addedBand']}: added so far"
                            + (" before sigma filtering" if data["firstSigmaFilteringStep"] is not None else "")))
    if drawnElements["sigmaSpan"]:
        legendItems.append(("shade", SIGMA_FILTERING_SHADE_COLOUR, f"{words['sigmaSpan']}: sigma filtering"))
    if drawnElements["sigmaDiamonds"]:
        legendItems.append(("diamond", ADDED_POINT_COLOUR,
                            f"{words['sigmaDiamonds']}: points of each step, not summed"))
    if drawnElements["sigmaRangeLines"]:
        legendItems.append(("verticalLine", ADDED_POINT_COLOUR,
                            f"{words['sigmaRangeLines']}: range over a group of steps"))
    if drawnElements["farPointBars"]:
        legendItems.append(("bar", FAR_POINT_COLOUR, f"{words['farPointBars']}: far points rejected"))
    return legendItems


class TiePointGrowthGraph(SfmGraphWidget):
    """Graph 2: the 3D tie points of the selected run over its
    triangulation steps, one step per adding line: the valid count of
    each pt: line (blue line and dots), the points added up to each step
    before sigma filtering (pale orange band under the blue line), the
    points of each step during sigma filtering (hollow orange diamonds in
    the light blue span, not summed), and, below, the far points rejected
    in each step (dark red bars).  The explanation and the legend name
    only what is drawn (TiePointGrowthDrawnElements)."""

    TAB_LABEL = "Tie point growth"
    TITLE = "Growth of the 3D tie points during image orientation"
    # In general, before a log is parsed and when the log lacks the lines
    # of the graph (TiePointGrowthExplanation names what is drawn).
    EXPLANATION = ("The 3D tie points at each triangulation step of image orientation (one step per line "
                   "“adding N points” of the log), with sigma filtering and the far points rejected in each step. "
                   "Look for steps with many far points and for how the valid count changes during sigma filtering.")
    MISSING_LINES_MESSAGE = (f"No triangulation step in the log of this run ({ADDING_LINE_FORM}), so there is "
                             "nothing to draw.")
    # Screen pixels left of the plot (axis title and tick labels) and
    # right of it.
    AXIS_TITLE_WIDTH = 16
    TICK_LABEL_WIDTH = 48
    PLOT_RIGHT_MARGIN = 10

    def GraphData(self, runs, summarisedRunIndex):
        return TiePointGrowthData(runs[summarisedRunIndex]["counts"])

    def NoteText(self):
        return TiePointGrowthNote(self.graphData)

    def DrawnElements(self, areaWidth):
        """TiePointGrowthDrawnElements for a graph area *areaWidth* screen
        pixels wide."""
        return TiePointGrowthDrawnElements(self.graphData, areaWidth - self.AXIS_TITLE_WIDTH - self.TICK_LABEL_WIDTH
                                           - self.PLOT_RIGHT_MARGIN)

    def ExplanationText(self, areaWidth):
        return TiePointGrowthExplanation(self.graphData, self.DrawnElements(areaWidth))

    def DrawGraph(self, painter, areaLeft, areaTop, areaWidth, areaHeight):
        data = self.graphData
        stepCount = data["stepCount"]
        validPointSteps = data["validPointSteps"]
        cumulativeCounts = data["cumulativeAddedPointCountsBeforeSigmaFiltering"]
        sigmaFilteringSteps = data["addedPointStepsDuringSigmaFiltering"]
        firstSigmaFilteringStep = data["firstSigmaFilteringStep"]
        # What is drawn is decided once, for the drawing, the explanation,
        # and the legend alike.
        drawnElements = self.DrawnElements(areaWidth)
        firstStep = drawnElements["firstStep"]
        axisTitleWidth = self.AXIS_TITLE_WIDTH
        tickLabelWidth = self.TICK_LABEL_WIDTH
        plotLeft = areaLeft + axisTitleWidth + tickLabelWidth
        plotWidth = areaWidth - axisTitleWidth - tickLabelWidth - self.PLOT_RIGHT_MARGIN
        hasSigmaFilteringSpan = drawnElements["sigmaSpan"]
        legendItems = TiePointGrowthLegendItems(data, drawnElements)
        legendWidth = areaLeft + areaWidth - plotLeft
        # The gap between the panels holds the caption of the lower panel.
        captionGap = 22
        mainTop = areaTop + 4
        panelsHeight = areaHeight - LegendBottomSpace(legendItems, legendWidth) - 4 - captionGap
        mainHeight = panelsHeight * 0.7
        lowerHeight = panelsHeight - mainHeight
        if plotWidth < 120 or lowerHeight < 30:
            return
        lowerTop = mainTop + mainHeight + captionGap
        slotWidth = drawnElements["slotWidth"]
        # With more steps than fit at MINIMUM_STEP_SLOT_WIDTH pixels, the
        # far points and the points during sigma filtering are shown per
        # block of consecutive steps.
        stepBlockSize = drawnElements["stepBlockSize"]
        drawsMarkers = drawnElements["validDots"]

        # Sigma filtering: one shaded span across both panels, from its
        # first step to the last step.
        spanLeft = max(plotLeft, plotLeft + (firstSigmaFilteringStep - firstStep) * slotWidth
                       if hasSigmaFilteringSpan else plotLeft)
        if hasSigmaFilteringSpan:
            painter.setPen(QtCore.Qt.NoPen)
            painter.setBrush(_Colour(SIGMA_FILTERING_SHADE_COLOUR))
            painter.drawRect(QtCore.QRectF(spanLeft, mainTop, plotLeft + plotWidth - spanLeft,
                                           lowerTop + lowerHeight - mainTop))

        # Upper panel: the points added so far before sigma filtering, the
        # valid count of every pt: line, and the points of each step
        # during sigma filtering.
        upperValues = (cumulativeCounts + [validPointCount for _, validPointCount in validPointSteps]
                       + [addedPointCount for _, addedPointCount in sigmaFilteringSteps])
        # The label of the span of sigma filtering lies at the top of the
        # plot, inside the span or left of it, where the valid count is
        # often highest, so the axis keeps a free row for it, also in a
        # small graph.
        headroom = COUNT_AXIS_HEADROOM
        if hasSigmaFilteringSpan:
            headroom = max(headroom, mainHeight / max(mainHeight - SIGMA_FILTERING_LABEL_ROW_HEIGHT, 1.0))
        tickValues = TickValues(max(upperValues) * headroom, 5)
        _DrawCountAxis(painter, plotLeft, mainTop, plotWidth, mainHeight, tickValues, tickLabelWidth)
        scaleMaximum = tickValues[-1]
        if hasSigmaFilteringSpan:
            # One label for the span, inside it when it is wide enough,
            # otherwise to its left.
            painter.setFont(_Font(GRAPH_TEXT_POINT_SIZE, True))
            painter.setPen(_Pen(SIGMA_MARKER_COLOUR))
            spanLabel = f"sigma filtering, from step {firstSigmaFilteringStep}"
            if plotLeft + plotWidth - spanLeft >= 190:
                _DrawTextInRectangle(painter, spanLeft + 6, mainTop + 2, 220, 14,
                                     QtCore.Qt.AlignLeft | QtCore.Qt.AlignTop, spanLabel)
            else:
                _DrawTextInRectangle(painter, spanLeft - 226, mainTop + 2, 220, 14,
                                     QtCore.Qt.AlignRight | QtCore.Qt.AlignTop, spanLabel)
        # The points added so far are a wide band under the valid count:
        # the two coincide until points are removed, and a line drawn over
        # the blue line hid it there.
        if drawnElements["addedBand"]:
            _DrawBand(painter, [(_StepCentreX(plotLeft, slotWidth, firstStep, stepNumber),
                                 _ValueY(mainTop, mainHeight, scaleMaximum, cumulativeCount))
                                for stepNumber, cumulativeCount in enumerate(cumulativeCounts, 1)],
                      ADDED_POINT_BAND_COLOUR, ADDED_POINT_BAND_WIDTH, slotWidth)
        validPoints = [(_StepCentreX(plotLeft, slotWidth, firstStep, stepNumber),
                        _ValueY(mainTop, mainHeight, scaleMaximum, validPointCount))
                       for stepNumber, validPointCount in validPointSteps]
        _DrawSeries(painter, validPoints, TIE_POINT_COLOUR, (3 if slotWidth >= 8.0 else 2) if drawsMarkers else 0)
        painter.setPen(_Pen(ADDED_POINT_COLOUR, 1.5))
        painter.setBrush(QtCore.Qt.NoBrush)
        if stepBlockSize == 1:
            diamondRadius = 4.0 if slotWidth >= 8.0 else 2.5
            for stepNumber, addedPointCount in sigmaFilteringSteps:
                painter.drawPolygon(_Diamond(_StepCentreX(plotLeft, slotWidth, firstStep, stepNumber),
                                             _ValueY(mainTop, mainHeight, scaleMaximum, addedPointCount),
                                             diamondRadius))
        else:
            # Per block of steps: the smallest to the largest points of
            # its steps during sigma filtering, as a vertical line.
            for blockIndex, _, sigmaPointRange in self.CachedValue(("stepBlocks", stepBlockSize), StepBlockValues,
                                                                     data, stepBlockSize, firstStep):
                if sigmaPointRange is not None:
                    blockCentreX = plotLeft + (blockIndex + 0.5) * stepBlockSize * slotWidth
                    _DrawLine(painter, blockCentreX, _ValueY(mainTop, mainHeight, scaleMaximum, sigmaPointRange[0]),
                              blockCentreX, _ValueY(mainTop, mainHeight, scaleMaximum, sigmaPointRange[1]))
        if validPoints:
            # The last valid count goes on the side of its point that the
            # line leaves free: above it while the count still rises,
            # below it once the count stays level or falls.
            painter.setFont(_Font(GRAPH_TICK_POINT_SIZE))
            painter.setPen(_Pen(TIE_POINT_COLOUR))
            lastPointX, lastPointY = validPoints[-1]
            if len(validPoints) > 1 and validPoints[-2][1] > lastPointY:
                _DrawTextInRectangle(painter, lastPointX - 104, lastPointY - 20, 100, 14,
                                     QtCore.Qt.AlignRight | QtCore.Qt.AlignBottom, _FormatNumber(validPointSteps[-1][1]))
            else:
                _DrawTextInRectangle(painter, lastPointX - 104, lastPointY + 6, 100, 14,
                                     QtCore.Qt.AlignRight | QtCore.Qt.AlignTop, _FormatNumber(validPointSteps[-1][1]))

        # Lower panel: the far points rejected in each step (or block of
        # steps), with its caption in the gap above it.
        painter.setFont(_Font(GRAPH_TEXT_POINT_SIZE, True))
        painter.setPen(_Pen(TITLE_COLOUR))
        lowerCaption = ("Far points rejected in each step" if stepBlockSize == 1
                        else f"Far points rejected in each group of {stepBlockSize} steps")
        _DrawTextInRectangle(painter, plotLeft, lowerTop - captionGap + 3, plotWidth, 16,
                             QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, lowerCaption)
        if stepBlockSize == 1:
            farPointBars = [(_StepCentreX(plotLeft, slotWidth, firstStep, stepNumber), farPointCount)
                            for stepNumber, farPointCount in enumerate(data["farPointCounts"], 1)
                            if farPointCount is not None]
            barWidth = max(1.0, slotWidth * 0.6)
        else:
            farPointBars = [(plotLeft + (blockIndex + 0.5) * stepBlockSize * slotWidth, farPointSum)
                            for blockIndex, farPointSum, _ in self.CachedValue(
                                ("stepBlocks", stepBlockSize), StepBlockValues, data, stepBlockSize, firstStep)
                            if farPointSum is not None]
            barWidth = max(1.0, stepBlockSize * slotWidth * 0.6)
        farTickValues = TickValues(max([farPointCount for _, farPointCount in farPointBars] + [0])
                                   * COUNT_AXIS_HEADROOM, 2)
        _DrawCountAxis(painter, plotLeft, lowerTop, plotWidth, lowerHeight, farTickValues, tickLabelWidth)
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(_Colour(FAR_POINT_COLOUR))
        for barCentreX, farPointCount in farPointBars:
            if farPointCount:
                barTop = _ValueY(lowerTop, lowerHeight, farTickValues[-1], farPointCount)
                painter.drawRect(QtCore.QRectF(barCentreX - barWidth / 2.0, barTop, barWidth,
                                               lowerTop + lowerHeight - barTop))
        if not farPointBars:
            painter.setFont(_Font(GRAPH_TICK_POINT_SIZE))
            painter.setPen(_Pen(MESSAGE_COLOUR))
            painter.drawText(int(plotLeft + 8), int(lowerTop + 16), "The adding lines give no far points.")

        # Step numbers, thinned out for many steps, and the axis titles.
        painter.setFont(_Font(GRAPH_TICK_POINT_SIZE))
        painter.setPen(_Pen(TICK_COLOUR))
        labelStep = LabelStep(slotWidth, MINIMUM_STEP_LABEL_SPACING)
        for stepNumber in range(firstStep, stepCount + 1):
            if stepNumber % labelStep == 0:
                _DrawTextInRectangle(painter, _StepCentreX(plotLeft, slotWidth, firstStep, stepNumber) - 20,
                                     lowerTop + lowerHeight + 2, 40, 12, QtCore.Qt.AlignHCenter | QtCore.Qt.AlignTop,
                                     str(stepNumber))
        painter.setPen(_Pen(AXIS_TITLE_COLOUR))
        _DrawTextInRectangle(painter, plotLeft, lowerTop + lowerHeight + 15, plotWidth, 14,
                             QtCore.Qt.AlignHCenter | QtCore.Qt.AlignTop,
                             "triangulation step (one per line “adding N points” of the log)")
        _DrawVerticalText(painter, areaLeft + 7, mainTop + mainHeight / 2.0, mainHeight, "3D tie points (count)")
        _DrawVerticalText(painter, areaLeft + 7, lowerTop + lowerHeight / 2.0, lowerHeight + 20, "far points (count)")
        _DrawLegend(painter, plotLeft, lowerTop + lowerHeight + 40, legendItems, legendWidth)


class KeypointGraph(SfmGraphWidget):
    """Graph 3: the keypoints detected in each image of the selected run
    against the keypoint limit of its Align Photos line.  With more images
    than fit as bars of MINIMUM_BAR_SLOT_WIDTH screen pixels, blocks of
    consecutive image numbers show the range and the median of their
    keypoints, and a lower panel the share of their images at the
    keypoint limit."""

    TAB_LABEL = "Keypoints per image"
    TITLE = "Keypoints per image and the keypoint limit"
    EXPLANATION = ("The keypoints detected in each image (for many images, the range and median of each group of "
                   "images), with the keypoint limit of Align Photos as a dashed line. Look for images far below the "
                   "others (blur, low texture, masks, or overexposure) and for the images that reach the limit "
                   "(amber).")
    MISSING_LINES_MESSAGE = (f"No keypoint detection line in the log of this run ({DETECTION_LINE_FORM}), so there "
                             "is nothing to draw.")

    def GraphData(self, runs, summarisedRunIndex):
        return KeypointChartData(runs[summarisedRunIndex]["counts"])

    def NoteText(self):
        return KeypointChartNote(self.graphData)

    def DrawGraph(self, painter, areaLeft, areaTop, areaWidth, areaHeight):
        data = self.graphData
        imageNumbers = data["imageNumbers"]
        keypointCounts = data["keypointCounts"]
        keypointLimit = data["keypointLimit"]
        hasPositiveLimit = keypointLimit is not None and keypointLimit > 0
        axisTitleWidth = 16
        tickLabelWidth = 48
        plotLeft = areaLeft + axisTitleWidth + tickLabelWidth
        plotWidth = areaWidth - axisTitleWidth - tickLabelWidth - 10
        plotTop = areaTop + 4
        isBlockView = len(imageNumbers) * MINIMUM_BAR_SLOT_WIDTH > plotWidth
        legendItems = self.KeypointLegendItems(isBlockView)
        legendWidth = areaLeft + areaWidth - plotLeft
        plotHeight = areaHeight - LegendBottomSpace(legendItems, legendWidth) - 4
        if plotWidth < 120 or plotHeight < 60:
            return
        if isBlockView:
            self.DrawKeypointBlocks(painter, areaLeft, plotLeft, plotWidth, plotTop, plotHeight)
            _DrawLegend(painter, plotLeft, plotTop + plotHeight + 40, legendItems, legendWidth)
            return
        tickValues = TickValues(max(keypointCounts + ([keypointLimit] if hasPositiveLimit else []))
                                * COUNT_AXIS_HEADROOM, 5)
        _DrawCountAxis(painter, plotLeft, plotTop, plotWidth, plotHeight, tickValues, tickLabelWidth)
        slotWidth = plotWidth / len(imageNumbers)
        barWidth = slotWidth * 0.75 if slotWidth >= 4.0 else max(slotWidth, 1.0)
        imagesAtLimit = set(data["imagesAtLimit"])
        painter.setPen(QtCore.Qt.NoPen)
        for slotIndex, (imageNumber, keypointCount) in enumerate(zip(imageNumbers, keypointCounts)):
            painter.setBrush(_Colour(KEYPOINT_AT_LIMIT_COLOUR if imageNumber in imagesAtLimit else KEYPOINT_COLOUR))
            barTop = _ValueY(plotTop, plotHeight, tickValues[-1], keypointCount)
            painter.drawRect(QtCore.QRectF(plotLeft + (slotIndex + 0.5) * slotWidth - barWidth / 2.0, barTop, barWidth,
                                           plotTop + plotHeight - barTop))
        if hasPositiveLimit:
            self.DrawKeypointLimit(painter, plotLeft, plotWidth, _ValueY(plotTop, plotHeight, tickValues[-1],
                                                                         keypointLimit))

        # Image numbers: every image while the labels fit, then every 2nd,
        # 5th, 10th image, and so on.
        painter.setFont(_Font(GRAPH_TICK_POINT_SIZE))
        painter.setPen(_Pen(TICK_COLOUR))
        labelStep = LabelStep(slotWidth, ImageLabelSpacing(imageNumbers[-1]))
        for slotIndex in range(0, len(imageNumbers), labelStep):
            _DrawTextInRectangle(painter, plotLeft + (slotIndex + 0.5) * slotWidth - 20, plotTop + plotHeight + 2, 40, 12,
                                 QtCore.Qt.AlignHCenter | QtCore.Qt.AlignTop, str(imageNumbers[slotIndex]))
        painter.setPen(_Pen(AXIS_TITLE_COLOUR))
        _DrawTextInRectangle(painter, plotLeft, plotTop + plotHeight + 15, plotWidth, 14,
                             QtCore.Qt.AlignHCenter | QtCore.Qt.AlignTop, AXIS_TEXT_IMAGE)
        _DrawVerticalText(painter, areaLeft + 7, plotTop + plotHeight / 2.0, plotHeight, "keypoints (count)")
        _DrawLegend(painter, plotLeft, plotTop + plotHeight + 40, legendItems, legendWidth)

    def KeypointLegendItems(self, isBlockView):
        """Legend of the keypoint graph: the encodings that it shows."""
        data = self.graphData
        hasPositiveLimit = data["keypointLimit"] is not None and data["keypointLimit"] > 0
        if isBlockView:
            legendItems = [("rangeBar", RANGE_COLOUR, "range of the keypoints in a group"),
                           ("medianMark", KEYPOINT_COLOUR, "median of a group")]
        else:
            legendItems = [("box", KEYPOINT_COLOUR, "keypoints detected")]
            if data["imagesAtLimit"]:
                legendItems.append(("box", KEYPOINT_AT_LIMIT_COLOUR, "at the keypoint limit"))
        if hasPositiveLimit:
            legendItems.append(("dashedLine", LIMIT_LINE_COLOUR, "keypoint limit (keypoint_limit of Align Photos)"))
        return legendItems

    def DrawKeypointLimit(self, painter, plotLeft, plotWidth, limitY):
        """Dashed line of the keypoint limit with its value."""
        painter.setFont(_Font(GRAPH_TICK_POINT_SIZE))
        painter.setPen(_Pen(LIMIT_LINE_COLOUR, 2, QtCore.Qt.DashLine))
        _DrawLine(painter, plotLeft, limitY, plotLeft + plotWidth, limitY)
        painter.setPen(_Pen(LIMIT_LINE_COLOUR))
        _DrawTextInRectangle(painter, plotLeft + plotWidth - 204, limitY - 16, 200, 14,
                             QtCore.Qt.AlignRight | QtCore.Qt.AlignBottom,
                             f"keypoint limit {_FormatNumber(self.graphData['keypointLimit'])}")

    def DrawKeypointBlocks(self, painter, areaLeft, plotLeft, plotWidth, plotTop, plotHeight):
        """The keypoints in blocks of consecutive image numbers: for each
        block a bar from its fewest to its most keypoints with a mark at
        the median, and, with a keypoint limit, a lower panel with the
        share of the images of each block at the limit."""
        data = self.graphData
        keypointLimit = data["keypointLimit"]
        hasPositiveLimit = keypointLimit is not None and keypointLimit > 0
        tickLabelWidth = plotLeft - areaLeft - 16
        imageNumberCount = data["imageNumbers"][-1] + 1
        blockSize = BlockSize(imageNumberCount, plotWidth, MINIMUM_BAR_SLOT_WIDTH)
        slotCount = (imageNumberCount + blockSize - 1) // blockSize
        slotWidth = plotWidth / slotCount
        blocks = self.CachedValue(("keypointBlocks", blockSize), KeypointBlockStatistics, data, blockSize)
        captionGap = 22
        shareHeight = max(40.0, plotHeight * 0.22) if hasPositiveLimit else 0.0
        mainHeight = plotHeight - (shareHeight + captionGap if hasPositiveLimit else 0.0)
        tickValues = TickValues(max([block["maximum"] for block in blocks] + ([keypointLimit] if hasPositiveLimit
                                                                              else [])) * COUNT_AXIS_HEADROOM, 5)
        _DrawCountAxis(painter, plotLeft, plotTop, plotWidth, mainHeight, tickValues, tickLabelWidth)
        barWidth = slotWidth * 0.75 if slotWidth >= 4.0 else max(slotWidth, 1.0)
        painter.setPen(QtCore.Qt.NoPen)
        for block in blocks:
            barLeft = plotLeft + (block["blockIndex"] + 0.5) * slotWidth - barWidth / 2.0
            rangeTop = _ValueY(plotTop, mainHeight, tickValues[-1], block["maximum"])
            rangeBottom = _ValueY(plotTop, mainHeight, tickValues[-1], block["minimum"])
            painter.setBrush(_Colour(RANGE_COLOUR))
            painter.drawRect(QtCore.QRectF(barLeft, rangeTop, barWidth, max(rangeBottom - rangeTop, 1.5)))
            painter.setBrush(_Colour(KEYPOINT_COLOUR))
            painter.drawRect(QtCore.QRectF(barLeft, _ValueY(plotTop, mainHeight, tickValues[-1], block["median"]) - 1.0,
                                           barWidth, 2.0))
        if hasPositiveLimit:
            self.DrawKeypointLimit(painter, plotLeft, plotWidth,
                                   _ValueY(plotTop, mainHeight, tickValues[-1], keypointLimit))
            shareTop = plotTop + mainHeight + captionGap
            painter.setFont(_Font(GRAPH_TEXT_POINT_SIZE, True))
            painter.setPen(_Pen(TITLE_COLOUR))
            _DrawTextInRectangle(painter, plotLeft, shareTop - captionGap + 3, plotWidth, 16,
                                 QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter,
                                 "Share of the images at the keypoint limit in each group")
            _DrawCountAxis(painter, plotLeft, shareTop, plotWidth, shareHeight, [0, 50, 100], tickLabelWidth)
            painter.setPen(QtCore.Qt.NoPen)
            painter.setBrush(_Colour(KEYPOINT_AT_LIMIT_COLOUR))
            for block in blocks:
                if block["atLimitCount"]:
                    shareTopY = _ValueY(shareTop, shareHeight, 100, 100.0 * block["atLimitCount"] / block["itemCount"])
                    painter.drawRect(QtCore.QRectF(plotLeft + (block["blockIndex"] + 0.5) * slotWidth - barWidth / 2.0,
                                                   shareTopY, barWidth, shareTop + shareHeight - shareTopY))
            painter.setFont(_Font(GRAPH_TICK_POINT_SIZE))
            painter.setPen(_Pen(AXIS_TITLE_COLOUR))
            _DrawVerticalText(painter, areaLeft + 7, shareTop + shareHeight / 2.0, shareHeight + 20, "share (%)")

        # The first image number of a block, thinned out.
        painter.setFont(_Font(GRAPH_TICK_POINT_SIZE))
        painter.setPen(_Pen(TICK_COLOUR))
        labelStep = LabelStep(slotWidth, ImageLabelSpacing((slotCount - 1) * blockSize))
        for slotIndex in range(0, slotCount, labelStep):
            _DrawTextInRectangle(painter, plotLeft + (slotIndex + 0.5) * slotWidth - 24, plotTop + plotHeight + 2, 48, 12,
                                 QtCore.Qt.AlignHCenter | QtCore.Qt.AlignTop, str(slotIndex * blockSize))
        painter.setPen(_Pen(AXIS_TITLE_COLOUR))
        _DrawTextInRectangle(painter, plotLeft, plotTop + plotHeight + 15, plotWidth, 14,
                             QtCore.Qt.AlignHCenter | QtCore.Qt.AlignTop,
                             f"{AXIS_TEXT_IMAGE}, in groups of {blockSize} consecutive images")
        _DrawVerticalText(painter, areaLeft + 7, plotTop + mainHeight / 2.0, mainHeight, "keypoints (count)")


class PipelineFunnelGraph(SfmGraphWidget):
    """Graph 4: the counts of the SfM pipeline of the summarised run from
    keypoints to valid 3D tie points, on a logarithmic scale."""

    TAB_LABEL = "Pipeline funnel"
    TITLE = "SfM pipeline funnel"
    EXPLANATION = ("The counts of the summary from the keypoints to the valid 3D tie points, on a logarithmic "
                   "scale. Look for the stage at which the count falls most.")
    MISSING_LINES_MESSAGE = ("No count of the SfM pipeline in the log of this run (photo N: X points, N matches "
                             "found, filtered X out of Y matches, setting point indices... N, generated N tie points, "
                             "selected N tracks out of M, or pt: (total, valid, weak)), so there is nothing to draw.")

    def GraphData(self, runs, summarisedRunIndex):
        return PipelineFunnelData(runs[summarisedRunIndex]["counts"])

    def NoteText(self):
        return PipelineFunnelNote(self.graphData)

    def DrawGraph(self, painter, areaLeft, areaTop, areaWidth, areaHeight):
        stages = self.graphData
        axisTitleWidth = 16
        # Room for the longest stage label at 8 pt and for the value and
        # share after the longest bar.
        stageLabelWidth = 200.0
        valueTextWidth = 190.0
        plotLeft = areaLeft + axisTitleWidth + stageLabelWidth + 8
        plotWidth = areaWidth - axisTitleWidth - stageLabelWidth - 8 - valueTextWidth
        plotTop = areaTop + 4
        plotHeight = areaHeight - GRAPH_BOTTOM_SPACE - 4
        if plotWidth < 100 or plotHeight < 80:
            return
        positiveValues = [stage["value"] for stage in stages if stage["value"]]
        lowerExponent = int(math.floor(math.log10(min(positiveValues)))) if positiveValues else 0
        upperExponent = int(math.ceil(math.log10(max(positiveValues)))) if positiveValues else 1
        if upperExponent <= lowerExponent:
            upperExponent = lowerExponent + 1
        exponentSpan = upperExponent - lowerExponent

        # Decades of the logarithmic count axis.
        painter.setFont(_Font(GRAPH_TICK_POINT_SIZE))
        for exponent in range(lowerExponent, upperExponent + 1):
            decadeX = plotLeft + (exponent - lowerExponent) / exponentSpan * plotWidth
            if exponent > lowerExponent:
                painter.setPen(_Pen(GRID_COLOUR, 1, QtCore.Qt.DotLine))
                _DrawLine(painter, decadeX, plotTop, decadeX, plotTop + plotHeight)
            painter.setPen(_Pen(TICK_COLOUR))
            _DrawTextInRectangle(painter, decadeX - 40, plotTop + plotHeight + 2, 80, 12,
                             QtCore.Qt.AlignHCenter | QtCore.Qt.AlignTop, _FormatNumber(10 ** exponent))
        rowHeight = plotHeight / len(stages)
        barHeight = min(24.0, rowHeight * 0.62)
        for stageIndex, stage in enumerate(stages):
            rowCentre = plotTop + (stageIndex + 0.5) * rowHeight
            painter.setFont(_Font(GRAPH_TEXT_POINT_SIZE))
            painter.setPen(_Pen(TITLE_COLOUR))
            _DrawTextInRectangle(painter, plotLeft - 8 - stageLabelWidth, rowCentre - 8, stageLabelWidth, 16,
                             QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter, stage["label"])
            painter.setFont(_Font(GRAPH_TICK_POINT_SIZE))
            value = stage["value"]
            if value is None:
                painter.setPen(_Pen(MESSAGE_COLOUR))
                painter.drawText(int(plotLeft + 6), int(rowCentre + 4), f"not in the log ({stage['lineForm']})")
                continue
            barEnd = (plotLeft + (math.log10(value) - lowerExponent) / exponentSpan * plotWidth if value > 0
                      else plotLeft)
            painter.setPen(QtCore.Qt.NoPen)
            painter.setBrush(_Colour(ENTITY_COLOURS[stage["entity"]]))
            painter.drawRect(QtCore.QRectF(plotLeft, rowCentre - barHeight / 2.0, max(barEnd - plotLeft, 1.0),
                                           barHeight))
            valueText = _FormatNumber(value)
            if stage["shareOfStageAbove"] is not None:
                valueText += f" ({100.0 * stage['shareOfStageAbove']:.1f} % of the stage above)"
            painter.setPen(_Pen(TEXT_COLOUR))
            painter.drawText(int(barEnd + 6), int(rowCentre + 4), valueText)
        painter.setPen(_Pen(AXIS_COLOUR))
        _DrawLine(painter, plotLeft, plotTop, plotLeft, plotTop + plotHeight)
        _DrawLine(painter, plotLeft, plotTop + plotHeight, plotLeft + plotWidth, plotTop + plotHeight)
        painter.setPen(_Pen(AXIS_TITLE_COLOUR))
        _DrawTextInRectangle(painter, plotLeft, plotTop + plotHeight + 15, plotWidth, 14,
                         QtCore.Qt.AlignHCenter | QtCore.Qt.AlignTop, "count (logarithmic scale)")
        _DrawVerticalText(painter, areaLeft + 7, plotTop + plotHeight / 2.0, plotHeight, "stage of the SfM pipeline")
        _DrawLegend(painter, plotLeft, plotTop + plotHeight + 40,
                    [("box", ENTITY_COLOURS[countedThings], countedThings)
                     for countedThings in ("keypoints", "matches", "tracks", "3D tie points")],
                    areaLeft + areaWidth - plotLeft)


class RunComparisonGraph(SfmGraphWidget):
    """Graph 5: the main counts of every run of the log side by side, one
    panel per count and one bar per run, with the run chosen in the list
    of runs outlined."""

    TAB_LABEL = "Runs side by side"
    TITLE = "SfM runs side by side"
    SHOWS_ALL_RUNS = True
    EXPLANATION = ("The main counts of every Align Photos run in the log, one bar per run and one panel per count. "
                   "Compare runs with different settings, for example downscale 1 and 2, and look for runs without "
                   "a final tie point count (hatched bars). The run selected in the list of runs above the tabs is "
                   "outlined.")
    MISSING_LINES_MESSAGE = (f"No Align Photos run in the log ({PARAMETER_LINE_FORM} and the lines after it), so "
                             "there is nothing to draw.")

    def GraphData(self, runs, summarisedRunIndex):
        return RunComparisonData(runs, summarisedRunIndex)

    def NoteText(self):
        return RunComparisonNote(self.graphData)

    def TitleText(self):
        if self.runs and len(self.runs) > 1:
            return f"{self.TITLE} ({len(self.runs)} runs)"
        return self.TITLE

    def DrawGraph(self, painter, areaLeft, areaTop, areaWidth, areaHeight):
        data = self.graphData
        measures = data["measures"]
        runEntries = data["runs"]
        # One panel per count, two panels side by side and two rows.
        axisTitleWidth = 16
        tickLabelWidth = 48
        panelGap = 22
        columnCount = 2
        rowCount = (len(measures) + columnCount - 1) // columnCount
        legendItems = []
        for runIndex, runEntry in enumerate(runEntries[:len(RUN_COLOURS)]):
            qualifiers = RunQualifiers(runEntry)
            swatchKind = "box" if runEntry["isComplete"] else "hatched"
            if runEntry["isSummarised"]:
                swatchKind = "selectedBox" if runEntry["isComplete"] else "selectedHatched"
            legendItems.append((swatchKind, RUN_COLOURS[runIndex],
                                f"Run {runEntry['runNumber']}" + (f" ({', '.join(qualifiers)})" if qualifiers else "")))
        legendLeft = areaLeft + axisTitleWidth + tickLabelWidth
        legendWidth = areaLeft + areaWidth - legendLeft
        panelWidth = (areaWidth - axisTitleWidth - panelGap * (columnCount - 1)) / columnCount
        panelHeight = (areaHeight - LegendBottomSpace(legendItems, legendWidth) + 14 - panelGap * (rowCount - 1)) / rowCount
        panelTitleHeight = 18
        valueLabelHeight = 14
        plotWidth = panelWidth - tickLabelWidth
        plotHeight = panelHeight - panelTitleHeight - valueLabelHeight - 14
        if plotWidth < 60 or plotHeight < 40:
            return
        slotWidth = plotWidth / len(runEntries)
        barWidth = min(36.0, slotWidth * 0.7)
        runLabelStep = LabelStep(slotWidth, MINIMUM_IMAGE_LABEL_SPACING)
        for measureIndex, (measureLabel, _) in enumerate(measures):
            plotLeft = (areaLeft + axisTitleWidth + (measureIndex % columnCount) * (panelWidth + panelGap)
                        + tickLabelWidth)
            panelTop = areaTop + (measureIndex // columnCount) * (panelHeight + panelGap)
            plotTop = panelTop + panelTitleHeight + valueLabelHeight
            painter.setFont(_Font(GRAPH_TEXT_POINT_SIZE, True))
            painter.setPen(_Pen(TITLE_COLOUR))
            _DrawTextInRectangle(painter, plotLeft, panelTop, plotWidth, panelTitleHeight,
                             QtCore.Qt.AlignHCenter | QtCore.Qt.AlignTop, measureLabel)
            values = [runEntry["values"][measureIndex] for runEntry in runEntries]
            knownValues = [value for value in values if value is not None]
            tickValues = TickValues(max(knownValues) * COUNT_AXIS_HEADROOM if knownValues else 0, 4)
            _DrawCountAxis(painter, plotLeft, plotTop, plotWidth, plotHeight, tickValues, tickLabelWidth)
            painter.setFont(_Font(GRAPH_TICK_POINT_SIZE))
            for runIndex, (runEntry, value) in enumerate(zip(runEntries, values)):
                slotCentre = plotLeft + (runIndex + 0.5) * slotWidth
                colourComponents = RUN_COLOURS[runIndex % len(RUN_COLOURS)]
                if value is None:
                    painter.setPen(_Pen(MESSAGE_COLOUR))
                    _DrawTextInRectangle(painter, slotCentre - 30, plotTop + plotHeight - 16, 60, 14,
                                     QtCore.Qt.AlignHCenter | QtCore.Qt.AlignBottom, "n/a")
                else:
                    barTop = _ValueY(plotTop, plotHeight, tickValues[-1], value)
                    painter.setPen(_Pen(SELECTED_RUN_OUTLINE_COLOUR, 2) if runEntry["isSummarised"]
                                   else _Pen(colourComponents))
                    painter.setBrush(_Colour(colourComponents) if runEntry["isComplete"]
                                     else QtGui.QBrush(_Colour(colourComponents), QtCore.Qt.BDiagPattern))
                    painter.drawRect(QtCore.QRectF(slotCentre - barWidth / 2.0, barTop, barWidth,
                                                   plotTop + plotHeight - barTop))
                    valueText = _FormatNumber(value)
                    if measureLabel == IMAGES_ORIENTED_LABEL and runEntry["inputImageCount"] is not None:
                        valueText += f" of {_FormatNumber(runEntry['inputImageCount'])}"
                    painter.setPen(_Pen(TEXT_COLOUR))
                    _DrawTextInRectangle(painter, slotCentre - 50, barTop - 14, 100, 12,
                                     QtCore.Qt.AlignHCenter | QtCore.Qt.AlignBottom, valueText)
                if runIndex % runLabelStep == 0 or runEntry["isSummarised"]:
                    painter.setFont(_Font(GRAPH_TICK_POINT_SIZE, runEntry["isSummarised"]))
                    painter.setPen(_Pen(TITLE_COLOUR if runEntry["isSummarised"] else TICK_COLOUR))
                    _DrawTextInRectangle(painter, slotCentre - 20, plotTop + plotHeight + 2, 40, 12,
                                     QtCore.Qt.AlignHCenter | QtCore.Qt.AlignTop, str(runEntry["runNumber"]))
                    painter.setFont(_Font(GRAPH_TICK_POINT_SIZE))
        panelsBottom = areaTop + rowCount * panelHeight + (rowCount - 1) * panelGap
        painter.setPen(_Pen(AXIS_TITLE_COLOUR))
        _DrawTextInRectangle(painter, areaLeft + axisTitleWidth, panelsBottom + 1, areaWidth - axisTitleWidth, 14,
                         QtCore.Qt.AlignHCenter | QtCore.Qt.AlignTop, "run (as in the run list of the summary)")
        _DrawVerticalText(painter, areaLeft + 7, areaTop + (panelsBottom - areaTop) / 2.0, panelsBottom - areaTop,
                          "count")
        _DrawLegend(painter, legendLeft, panelsBottom + 26, legendItems, legendWidth)


def SharedTiePointConsoleText(data, readSeconds, readFailure=None, isLeftOut=False):
    """What the Console says of the shared 3D tie points of the open chunk
    (SharedTiePointData), read in *readSeconds*: "28 enabled, oriented
    images of the open chunk, 86 image pairs that share at least one valid
    3D tie point, read in 0.4 s", that the open project gives none, with
    *readFailure*, that the open chunk could not be read, or, *isLeftOut*,
    that the open chunk is left out."""
    if isLeftOut:
        return "not read, because the open chunk is left out"
    if readFailure is not None:
        return "not read, because the open chunk could not be read ({})".format(readFailure)
    if data is None:
        return ("none in the open project (no active chunk with tie points on at least two enabled, oriented "
                "images)")
    pairCount = len(data["pairs"])
    readText = "less than 0.1 s" if readSeconds < 0.05 else "{:.1f} s".format(readSeconds)
    return "{} of the open chunk, {} {} at least one valid 3D tie point, read in {}".format(
        FormatCountWithNoun(data["imageCount"], "enabled, oriented image", "enabled, oriented images"),
        FormatCountWithNoun(pairCount, "image pair", "image pairs"), "that shares" if pairCount == 1 else "that share",
        readText)


def ParsedLogConsoleLines(runs, source, shownRunIndex, sharedTiePointText=None, openChunkLines=None):
    """The Console block of a parsed log: its source (the path of a log
    file, or clipboard), every run with its main counts (RunCountsText)
    and whether the log holds its final tie point count, the run shown
    with the reason (DefaultRunReason), and, when given, what was read of
    the shared 3D tie points of the open chunk (SharedTiePointConsoleText)
    and the lines of the open chunk for the run shown, with the warnings
    and the note on its match with the log (OpenChunkConsoleLines)."""
    lines = ConsoleBlockStart("SfM Log Parser")
    lines.append(ConsoleLabelledLine("Source:", source))
    if not HoldsSfmRun(runs):
        # As the Summary: a text without a single line of an SfM run holds
        # no run (Geert, 21 October 2026).
        lines.append(ConsoleLabelledLine("SfM runs in the log:", 0))
        lines.append(ConsoleLabelledLine("Run status:", f"{NO_SFM_RUN_TEXT} ({NO_SFM_RUN_REASON})"))
    else:
        lines.append(ConsoleLabelledLine("SfM runs in the log:", len(runs)))
        for runIndex, run in enumerate(runs):
            lines.append(f"  Run {runIndex + 1} ({RunStartText(run)}): {RunCountsText(run['counts'])}, "
                         f"{RunStatusText(run['counts'])}")
        lines.append(ConsoleLabelledLine("Run shown:",
                                         f"Run {shownRunIndex + 1} of {len(runs)}, {DefaultRunReason(runs)}"))
    pythonRunsNote = PythonRunsNote(runs)
    if pythonRunsNote is not None:
        lines.append(ConsoleLabelledLine("Runs started from Python:", " ".join(pythonRunsNote)))
    if sharedTiePointText is not None:
        lines.append(ConsoleLabelledLine("Shared 3D tie points:", sharedTiePointText))
    lines.extend(openChunkLines or [])
    lines.extend(ConsoleBlockEnd())
    return lines


def RunChosenConsoleLine(runs, runIndex):
    """The Console line of a run chosen in the list of runs."""
    return f"SfM Log Parser: {RunChoiceText(runs, runIndex)}, chosen in the list of runs."


def OpenChunkSwitchConsoleLines(isLeftOut, shownRun, openChunk):
    """The Console lines of the box Leave out the open chunk: that the open
    chunk is left out, or that it is included again, and read unless the
    read failed, with what was read of it (SharedTiePointConsoleText and
    OpenChunkConsoleLines for
    *shownRun*, the run shown).  Before a log is parsed (*shownRun* None),
    that the open chunk is read when a log is parsed."""
    if isLeftOut:
        return ["SfM Log Parser: the open chunk is left out, so the Summary and the tab Shared tie points give no "
                "values of it."]
    if shownRun is None:
        return ["SfM Log Parser: the open chunk is included, and it is read when a log is parsed."]
    sharedTiePointText = SharedTiePointConsoleText(openChunk["sharedTiePointData"], openChunk["readSeconds"],
                                                   openChunk["readFailure"])
    includedText = "included again and read" if openChunk["readFailure"] is None else "included again"
    return (["SfM Log Parser: the open chunk is {}, without parsing the log again.".format(includedText),
             ConsoleLabelledLine("Shared 3D tie points:", sharedTiePointText)]
            + OpenChunkConsoleLines(shownRun, openChunk))


def SummaryCopiedConsoleLine(formatName, runs, runIndex):
    """The Console line of the summary copied as *formatName* (plain text
    or HTML)."""
    if not runs or runIndex is None:
        return f"SfM Log Parser: the empty summary was copied as {formatName}, because no log has been parsed yet."
    return f"SfM Log Parser: summary of Run {runIndex + 1} of {len(runs)} copied as {formatName}."


def GraphConsoleName(tabLabel, runs, runIndex, showsNoSingleRun=False):
    """A graph as the Console names it: "graph Image pairs of Run 2 of
    3", "graph Runs side by side" and "graph Shared tie points" for the
    graphs that show every run and the open chunk (*showsNoSingleRun*,
    ShowsNoSingleRun), and the graph alone before a log is parsed."""
    name = f"graph {tabLabel}"
    if runs and runIndex is not None and not showsNoSingleRun:
        name += f" of Run {runIndex + 1} of {len(runs)}"
    return name


def ShowsNoSingleRun(graphWidget):
    """True for a graph that shows every run of the log (Runs side by
    side) or the open chunk (Shared tie points), whose Console name and
    file name name no run."""
    return getattr(graphWidget, "SHOWS_ALL_RUNS", False) or getattr(graphWidget, "SHOWS_OPEN_CHUNK", False)


def GraphWidgetConsoleName(graphWidget):
    """The graph of *graphWidget* as the Console names it
    (GraphConsoleName), with the runs and the run that it shows."""
    return GraphConsoleName(graphWidget.TAB_LABEL, graphWidget.runs, graphWidget.summarisedRunIndex,
                            ShowsNoSingleRun(graphWidget))


def _ShowWaitCursor(isWaiting):
    """Show the wait cursor of Qt while a log and the open chunk are read
    (*isWaiting*), or the cursor of before.  Stand-ins of QApplication
    without these functions (the offline tests) are left as they are."""
    application = getattr(QtWidgets, "QApplication", None)
    if isWaiting:
        setOverrideCursor = getattr(application, "setOverrideCursor", None)
        if setOverrideCursor is not None:
            setOverrideCursor(QtGui.QCursor(QtCore.Qt.WaitCursor))
        return
    restoreOverrideCursor = getattr(application, "restoreOverrideCursor", None)
    if restoreOverrideCursor is not None:
        restoreOverrideCursor()


def HandOpenChunkToGraphs(dialog, openChunk):
    """Keep *openChunk* (ReadOpenChunk or LeftOutOpenChunk) for the summary
    of *dialog* (dialog.openChunk) and hand it to the graphs that show the
    open chunk (SharedTiePointGraph), with its shared 3D tie points and
    its read failure."""
    dialog.openChunk = openChunk
    for graphWidget in getattr(dialog, "graphWidgets", None) or []:
        if getattr(graphWidget, "SHOWS_OPEN_CHUNK", False):
            graphWidget.openChunk = openChunk
            graphWidget.openChunkData = openChunk["sharedTiePointData"]
            graphWidget.openChunkReadFailure = openChunk["readFailure"]


def ShowParsedLog(dialog, text, label, consoleSource=None):
    """Parse the log *text* once, read the open chunk once (ReadOpenChunk),
    unless it is left out (dialog.isOpenChunkLeftOut), fill the list of
    runs above the tabs of *dialog* (dialog.runComboBox, when it has one)
    with every run of the log, select the default run
    (SelectSummarisedRunIndex), show it (ShowSelectedRun), and echo the
    runs and the open chunk to the Console (ParsedLogConsoleLines), with
    the wait cursor meanwhile.  *label* is the source of the log in the
    summary, *consoleSource* in the Console (the label when None)."""
    _ShowWaitCursor(True)
    try:
        runs = ParseSfmRuns(text)
        dialog.parsedRuns = runs
        dialog.parsedText = text
        dialog.sourceLabel = label
        # The open chunk, read once per parsed log for the summary and the
        # graphs that show it (SharedTiePointGraph), unless it is left out.
        openChunk = LeftOutOpenChunk() if getattr(dialog, "isOpenChunkLeftOut", False) else ReadOpenChunk()
        HandOpenChunkToGraphs(dialog, openChunk)
        selectedRunIndex = SelectSummarisedRunIndex(runs)
        runComboBox = getattr(dialog, "runComboBox", None)
        if runComboBox is not None:
            # Filling the list must not show every run in turn.
            wereSignalsBlocked = runComboBox.blockSignals(True)
            runComboBox.clear()
            for runChoiceText in RunChoiceTexts(runs):
                runComboBox.addItem(runChoiceText)
            runComboBox.setCurrentIndex(selectedRunIndex)
            runComboBox.setEnabled(len(runs) > 1)
            runComboBox.blockSignals(wereSignalsBlocked)
        ShowSelectedRun(dialog, selectedRunIndex)
    finally:
        _ShowWaitCursor(False)
    sharedTiePointText = SharedTiePointConsoleText(openChunk["sharedTiePointData"], openChunk["readSeconds"],
                                                   openChunk["readFailure"], openChunk["isLeftOut"])
    PrintConsoleLines(ParsedLogConsoleLines(runs, consoleSource or label, selectedRunIndex, sharedTiePointText,
                                            OpenChunkConsoleLines(runs[selectedRunIndex], openChunk)))


def ShowSelectedRun(dialog, runIndex):
    """Show the summary of the run *runIndex* of the parsed log of *dialog*
    in its summary tab, with the open chunk that the dialog read
    (dialog.openChunk, HandOpenChunkToGraphs), and hand the runs and that
    index to its graphs (dialog.graphWidgets): graphs 1 to 4 show that
    run, graph 5 marks it, and graph 6 shows the open chunk whichever run
    is chosen, with the warnings and the note for that run."""
    runs = dialog.parsedRuns
    dialog.shownRunIndex = runIndex
    dialog.outputTextEdit.setHtml(SummariseHtml(dialog.parsedText, label=dialog.sourceLabel, runs=runs,
                                                runIndex=runIndex, openChunk=getattr(dialog, "openChunk", None)))
    for graphWidget in dialog.graphWidgets:
        graphWidget.SetRuns(runs, runIndex)


def SetOpenChunkLeftOut(dialog, isLeftOut):
    """Leave the open chunk out of the Summary and the tab Shared tie
    points of *dialog* (*isLeftOut*), for comparing logs of other
    projects, or include it again.  With a parsed log, the open chunk is
    read again when it is included (ReadOpenChunk, with the wait cursor
    meanwhile), and the run shown is shown again at once
    (ShowSelectedRun), without parsing the log again.  Before a log is
    parsed, the next parsed log follows the box.  The Console says which
    (OpenChunkSwitchConsoleLines)."""
    dialog.isOpenChunkLeftOut = isLeftOut
    runs = getattr(dialog, "parsedRuns", None)
    shownRunIndex = getattr(dialog, "shownRunIndex", None)
    if not runs or shownRunIndex is None:
        PrintConsoleLines(OpenChunkSwitchConsoleLines(isLeftOut, None, None))
        return
    if isLeftOut:
        openChunk = LeftOutOpenChunk()
    else:
        _ShowWaitCursor(True)
        try:
            openChunk = ReadOpenChunk()
        finally:
            _ShowWaitCursor(False)
    HandOpenChunkToGraphs(dialog, openChunk)
    ShowSelectedRun(dialog, shownRunIndex)
    PrintConsoleLines(OpenChunkSwitchConsoleLines(isLeftOut, runs[shownRunIndex], openChunk))


def GraphFileName(tabLabel, runNumber):
    """Default name of the PNG file of a graph, from its tab label and the
    number of the run it shows (None for a single run or for the runs
    graph), for example sfmLogGraph_KeypointsPerImage_run2.png."""
    graphName = "".join(word[:1].upper() + word[1:] for word in tabLabel.split())
    return f"sfmLogGraph_{graphName}" + (f"_run{runNumber}" if runNumber is not None else "") + ".png"


# Copy graph and Save graph as PNG draw the graph with this many image
# pixels per screen pixel, so that a figure made from it stays sharp in
# print, also when it is scaled to the width of a column.
GRAPH_EXPORT_PIXEL_RATIO = 3

# The dots per metre of the metadata of a picture (QImage.setDotsPerMeterX
# and setDotsPerMeterY) for a screen of 96 dots per inch at a display
# scaling of 100 %, with which a program that reads them shows the
# picture as large as on such a screen.
SCREEN_DOTS_PER_METRE = 96.0 / 0.0254


def ScreenPixelRatio(widget):
    """The screen pixels per device-independent pixel of *widget*
    (QWidget.devicePixelRatioF), for example 1 at a display scaling of
    100 % and 1.5 where Qt draws at 150 %.  1 for a widget that gives no
    positive number."""
    devicePixelRatio = getattr(widget, "devicePixelRatioF", None)
    if devicePixelRatio is None:
        return 1.0
    try:
        ratio = float(devicePixelRatio())
    except Exception as suppressedError:
        ReportSuppressedError("sfmLogParser.ScreenPixelRatio", suppressedError)
        return 1.0
    return ratio if ratio > 0 else 1.0


def GraphPicture(graphWidget, pixelRatio=GRAPH_EXPORT_PIXEL_RATIO):
    """The picture of *graphWidget*, with its title, axes, legend, and
    note, for Copy graph and Save graph as PNG: drawn into a QImage with
    *pixelRatio* image pixels per screen pixel (QWidget.render with that
    device pixel ratio times the screen pixels per device-independent
    pixel, ScreenPixelRatio), on the background of the window, as on the
    screen.  The picture so has *pixelRatio* times as many pixel columns
    and rows as the graph on the screen at any display scaling.  Its dots
    per metre are those of SCREEN_DOTS_PER_METRE times the same ratio, so
    that a program that reads them shows the picture as large as the graph
    on a screen of 96 dots per inch at a display scaling of 100 %."""
    imageRatio = pixelRatio * ScreenPixelRatio(graphWidget)
    image = QtGui.QImage(int(round(graphWidget.width() * imageRatio)), int(round(graphWidget.height() * imageRatio)),
                         QtGui.QImage.Format_ARGB32)
    image.setDevicePixelRatio(imageRatio)
    image.setDotsPerMeterX(int(round(imageRatio * SCREEN_DOTS_PER_METRE)))
    image.setDotsPerMeterY(int(round(imageRatio * SCREEN_DOTS_PER_METRE)))
    image.fill(QtGui.QColor(255, 255, 255))
    graphWidget.render(image)
    return image


# The message of Copy graph and Save graph as PNG on the Summary tab.
_NO_GRAPH_ON_THE_SUMMARY_TAB_TEXT = "The Summary tab holds no graph. Choose the tab of a graph first."

# Size of the dialog: its default, the smallest size it is fitted to,
# and the room it leaves on the available screen (for the frame of the
# window and the task bar).
DIALOG_DEFAULT_WIDTH = 980
DIALOG_DEFAULT_HEIGHT = 860
DIALOG_SMALLEST_FITTED_WIDTH = 640
DIALOG_SMALLEST_FITTED_HEIGHT = 480
SCREEN_MARGIN_WIDTH = 40
SCREEN_MARGIN_HEIGHT = 60


def FittedDialogSize(availableWidth, availableHeight):
    """(width, height) of the dialog when it opens: the default size,
    reduced to fit the available screen with a margin, but not below
    DIALOG_SMALLEST_FITTED_WIDTH and DIALOG_SMALLEST_FITTED_HEIGHT unless
    the screen itself is smaller, so that the controls of the dialog
    stay on a small screen."""
    width = max(min(DIALOG_DEFAULT_WIDTH, availableWidth - SCREEN_MARGIN_WIDTH),
                min(DIALOG_SMALLEST_FITTED_WIDTH, availableWidth))
    height = max(min(DIALOG_DEFAULT_HEIGHT, availableHeight - SCREEN_MARGIN_HEIGHT),
                 min(DIALOG_SMALLEST_FITTED_HEIGHT, availableHeight))
    return width, height


# ===================================================================
# GUI
# ===================================================================

_ABOUT_HTML = """
<h2>SfM Log Parser</h2>
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

<h3>Overview</h3>
<p>This tool parses the log of a Metashape structure-from-motion (SfM) run, that is, of the Align
Photos command. The log can be pasted from the clipboard or read from a saved log file (TXT, LOG, or
HTML). The parser summarises every stage of the SfM pipeline, in text and in five graphs. A sixth
graph, ‘Shared tie points’, shows which images of the open chunk share valid 3D tie points, which
the log does not say.</p>

<h3>What is summarised</h3>
<p>The parser summarises the following:</p>
<ul>
<li><b>SfM runs</b>: when the log holds more than one Align Photos
run, a list of the runs with their main counts and the run that the
summary covers (see Several runs in one log).</li>
<li><b>SfM parameters</b>: the parameters that the log line <code>AlignPhotos:</code> lists
(downscale factor, keypoint limit, tie point limit, preselection, and others), each with a
description and its Metashape parameter name.</li>
<li><b>Keypoint detection</b>: the keypoints (interest points) detected in all images and their
average per image. Detection log lines count whichever device processed the image (GPU, CPU, or no
device tag). An image with more than one detection log line in a run counts once, using the count of
its last detection log line. The summary also reports how many images have multiple detection log
lines.</li>
<li><b>Matching</b>: raw matches &rarr; unique and mutually consistent
matches &rarr; geometrically consistent matches, with counts at each
stage.</li>
<li><b>Track building</b>: unique indices, generated tracks, cleanup
(multiple indices and tracks removed), selected tracks, and total 2D
tie points.</li>
<li><b>Orientation estimation</b>: the image pairs that the log reports, with the robust matches of
each (the first 1,000 pairs of a large image set, every pair in the ‘Image pairs’ graph). The
triangulation of the 3D tie points is summarised in counts and sums, with one triangulation step per
log line &ldquo;adding N points&rdquo;. Before sigma filtering, these are the triangulation steps,
the 3D tie points that they add, the far points rejected, and the valid 3D tie points. During sigma
filtering, these are the triangulation steps and the far points rejected, followed by the valid 3D
tie points at the end of the run. During Align Photos, Metashape prints at several stages a log line
that holds <code>pt:</code> and three numbers in parentheses. The parser reads them as the counts of
all 3D tie points at that moment, of the valid ones, and of the weak ones, and it uses the valid
count (the second number). Agisoft does not document this log line. Metashape prints the <code>pt:</code>
log line of each step of sigma filtering before that step rejects its far points. The valid count at
the end of the run is therefore that of the last <code>pt:</code> log line minus the far points
rejected after it. The parser subtracts them when the same sigma filtering has at least one earlier
step, and every earlier step agrees with this order. Further conditions are that no points are added
after the last <code>pt:</code> log line and that the far points do not outnumber its valid points.
Otherwise, the parser gives the count of the last <code>pt:</code> log line as the last reported
valid count and says why. As the log does not say whether a step during sigma filtering adds new 3D
tie points or triangulates 3D tie points again, the points of the steps during sigma filtering are
not summed. The values of every step are in the ‘Tie point growth’ graph.</li>
<li><b>Final counts</b>: the 3D tie points (inliers) of the log and,
when a project is open, the valid tie points of the open chunk, with
the difference between the two (see The open chunk).</li>
<li><b>SfM flowchart</b>: a text summary of the whole pipeline,
from keypoints to final tie points.</li>
<li><b>Graphs</b>: six graphs on the tabs next to the summary (see
Graphs).</li>
</ul>

<h3>Reading the counts</h3>
<p>A count shows n/a when the log contains no log line for it, for example when only part of the log
was copied. In other words, n/a means ‘not found in the log’, not zero.</p>
<p>The inlier count of the log is the valid count (the second number) of the last <code>pt:</code>
log line of the run plus the points of the log lines &ldquo;adding N points&rdquo; that follow it
before sigma filtering. These points are added as long as that sum does not exceed the number of
selected tracks. Alternatively, the inlier count is that valid count minus the far points of the log
lines &ldquo;adding N points&rdquo; that follow the last <code>pt:</code> log line during sigma
filtering. These far points are subtracted when the log confirms that they are rejected at the end
of the run (see Orientation estimation above). During sigma filtering, a log line &ldquo;adding N
points&rdquo; may report new 3D tie points or 3D tie points that are triangulated again, and the log
does not say which. When such log lines follow the last <code>pt:</code> log line, the parser
therefore does not add their points to the count of that <code>pt:</code> log line. The summary says
how many points these log lines hold. The tracks given with the inlier count are those of the log
line &ldquo;selected N tracks out of M&rdquo;.</p>
<p>The count of the open chunk is the number of tie points of the open chunk whose valid flag is
set, read from Metashape when the log is parsed. Every valid tie point counts, including one seen on
fewer than two enabled, oriented images, which the count ‘Tie points in the adjustment’ of the
Interior Orientation Viewer leaves out. The two counts agree when the log belongs to the open chunk
and its tie points have not changed since the run, but equal counts alone cannot establish either.
The open chunk holds fewer valid tie points
when tie points were removed after the run, for example after gradual selection (also by the Gradual
Selection Filter) or by hand. The open chunk holds more valid tie points when tie points were added,
for example by orienting further images. The counts also differ when the chunk was oriented again in
a run that is not in the parsed text, or when the log belongs to another chunk or project. The
summary and the flowchart give the difference with these reasons.</p>

<h3>The open chunk</h3>
<p>The open chunk is the active chunk of the open project. The Summary describes the log, and
wherever it names the open chunk or the open project, it compares the log with the open chunk. The
tab ‘Shared tie points’ shows the open chunk itself: its valid 3D tie points and the images that
share them. Both name the chunk by its label and the project by its file, or say that the project
has not been saved. The parser reads the open chunk once, when it parses a log.</p>
<p>A log can belong to another chunk or project, for example, when logs are compared. The parser
therefore compares the selected run with the open chunk. A warning gives both numbers when the
selected run oriented more or fewer images, according to the log, than the open chunk holds enabled,
oriented images. When the log gives no count of oriented images, the images of the run are compared.
When the log names a project other than the open one, a warning gives the path in the log, and also
that of the open project if it has been saved. The project of a run is that of the last <code>LoadProject:</code>
or <code>SaveProject:</code> log line before the run. Otherwise, it is that of the first <code>SaveProject:</code>
log line after the run, unless a <code>LoadProject:</code> log line comes first. The paths are
compared without regard to case or to the direction of the slashes. Without a warning, a note says
that the log does not name its chunk, so that the match with the open chunk cannot be confirmed. The
note also says when the log names no project. The Summary shows the warnings and the note at the
top, the ‘Shared tie points’ tab above the matrix, and the Console as well.</p>
<p>To compare logs of other projects, tick <b>Leave out the open chunk</b> above the tabs. The
parser then does not read the open chunk. The tab ‘Shared tie points’ says that the open chunk is
left out, and the Summary gives no values of it and says so. Untick the box to read the open chunk
again and to show its values at once, without parsing the log again.</p>

<h3>Several runs in one log</h3>
<p>A Console log can hold several Align Photos runs, for example when the images were oriented again
with other settings. The parser splits the text at every parameter log line of Align Photos (<code>AlignPhotos:</code>)
and summarises one run only, so that no count mixes two runs. By default, this is the last run whose
final tie-point count (its last <code>pt:</code> log line) is in the log, or the last run when no
run has one. No Metashape 2.3.2 log has yet shown which log line ends a run. Until then, a run is
described only as &ldquo;with a final tie point count in the log&rdquo; or as &ldquo;without a final
tie point count&rdquo; (cancelled, failed, still running, or copied only in part). When the text
holds more than one run, the summary starts with a list of all runs. The list gives the log line and
time at which each run starts, its main counts, and whether the log holds its final tie-point count.
The summary also names the run that it covers. To summarise another run, choose it in the list of
runs above the tabs. When the log lines before the first parameter log line of Align Photos include
log lines of an SfM run, they form a run whose start is not in the text. A text without any such
parameter log line is read as one run. A text without a single log line of an SfM run holds no run,
and the Summary, the list of runs, and the Console say that no SfM run was found.</p>
<p>Only the parameter log line of Align Photos (<code>AlignPhotos:</code>) starts a run. Image
matching started from Python (Chunk.matchPhotos) prints a <code>MatchPhotos:</code> log line but no
such parameter log line. The runs of a script are therefore not separated, and the counts of a run
can then combine several runs. When a <code>MatchPhotos:</code> log line follows the counts of an
earlier image matching in the same run, the summary and the Console name that run (as ‘Runs started
from Python’). Logs of the Align Photos command are the logs that the parser supports.</p>

<h3>Image numbers</h3>
<p>The summary and the graphs number the images as the log does. The log lines that the parser reads
name an image only by the number that Metashape gives it in the log of the Align Photos run, never
by its label or file name. As a result, the parser cannot match the images of two runs. Runs of
different image sets can therefore be compared only by their counts, for example in the ‘Runs side
by side’ graph. Two runs of the same images can be compared image by image only when Metashape
numbered the images in the same order in both runs.</p>

<h3>Graphs</h3>
<p>The tabs next to the summary hold five graphs of the same log and a sixth of the open chunk. The
first four show the run selected in the list of runs above the tabs, the fifth all runs (with the
selected run outlined), and the sixth the open chunk. A graph with missing log lines says so in its
place. Below each graph, a short note names the extremes (e.g. the weakest images), and the graph’s
tooltip holds the full note with its lists. With many images, the image pairs, the shared tie
points, and the keypoints are shown in groups of consecutive image numbers (2, 5, 10, 20, 50 images,
and so on, stated on the image axis). In this way, no cell or bar is narrower than a few screen
pixels, and only every second, fifth, or tenth label (and so on) is drawn.</p>
<p>Each graph sits in a scroll area, so that it keeps a legible size on a small screen. On the tab
of a graph, <b>Copy graph</b> and <b>Save graph as PNG</b> copy that graph, with its title, axes,
legend, and note, to the clipboard or save it as a PNG file. Both draw the graph with three times as
many pixel columns and rows as on the screen, so that a figure made from it stays sharp in print.</p>
<p><b>Image pairs</b>: which image pairs the log reports, and how strongly, in a square matrix of
the images, numbered as in the log (see Image numbers). Metashape reports an image pair, with a log
line &ldquo;pair i and j: K robust from M&rdquo;, when it evaluates the pair while orienting the
images, but not every matched pair. A coloured cell is an image pair that the log reports, coloured
on a logarithmic scale by its robust matches (K robust matches of its M candidate matches). A white
cell is an image pair that the log does not report. A light-red row and column mark an image for
which the log reports no image pair. Such images can still be matched and oriented, through the tie
points that they share with other images (see ‘Shared tie points’). A red outline marks an image
pair whose robust matches are fewer than half of its candidate matches, when its cell is at least
three times as wide as the outline is thick.</p>
<p>The bars on the right count, for the image of each row, the other images with which the log
reports at least one robust match. Weakly connected images thus stand out even when the matrix holds
few cells, and a red mark at zero flags an image with none. In groups, a cell holds the image pairs
between the images of two groups, coloured by the sum of their robust matches. The bars give, for
the images of a group, the fewest, the median, and the most other images with robust matches. The
log lines of the image pairs can be found by searching the log for &ldquo;robust from&rdquo;.
Attention should go to images that share robust matches with few other images and to clusters of
images without robust matches between them, which the note names. The same holds for image pairs
with many candidate matches but few robust ones (repetitive texture or moving objects), where image
orientation tends to fail.</p>
<p><b>Tie point growth</b>: the 3D tie points over the triangulation steps, one step per log line
&ldquo;adding N points&rdquo;. In the upper panel, the blue line and its blue dots (one dot per
valid count in the log) give the valid 3D tie points after each step, before and during sigma
filtering. The pale-orange band under the blue line gives the 3D tie points added so far before
sigma filtering, and it stays visible where the two counts are equal. Sigma filtering is the shaded
span in light blue, labelled with its first step. Its hollow orange diamonds show the 3D tie points
for each step, which are not summed. The dark-red bars in the lower panel show the far points
rejected at each step.</p>
<p>With many steps, the dots are left out. With still more steps, the dark-red bars give the far
points of groups of consecutive steps, and orange vertical lines replace the diamonds. These lines
run from the fewest to the most 3D tie points of a group’s steps during sigma filtering. The blue
line and the band, however, keep one point per step. The explanation above the graph names only what
is drawn. Attention should go to steps with many far points and to the way in which the valid count
changes during sigma filtering. The count of 3D tie points of the log in the summary follows from
the last valid count, as described in Reading the counts.</p>
<p><b>Keypoints per image</b>: the keypoints detected in each image (&ldquo;photo N: X points&rdquo;,
the last log line of an image with several), in the order of the image numbers. A horizontal dashed
line in the graph marks the ‘Key point limit’ of Align Photos. The parser reads this value from the
log line that starts with <code>AlignPhotos:</code>, where Metashape lists the settings of the run.
The bars of images whose keypoint count reaches this limit are amber. Metashape keeps at most that
many keypoints per image, so these images may have had more. In groups, a bar spans the fewest to
the most keypoints of the images of a group, with a mark at the median. In that case, a lower panel
gives the share of the images of each group at the keypoint limit. Images far below the others (e.g.
through blur, low texture, masks, or overexposure) should be identified, so that a weakly oriented
image can be traced back to keypoint detection.</p>
<p><b>Pipeline funnel</b>: the counts of the summary from keypoints to valid 3D tie points as bars
on a logarithmic scale. The bars show keypoints, then raw, unique, geometrically consistent, and
indexed matches. They are followed by generated and selected tracks and finally by 3D tie points. A
stage is given as a percentage of the stage above it where both count the same things (matches, or
tracks and 3D tie points). Attention should go to the stage at which the count falls most.</p>
<p><b>Runs side by side</b>: the keypoints, selected tracks, 3D tie points of the log, and oriented
images of every run, one panel per count and one bar per run. Bars for a run without a final
tie-point count in the log are hatched, and the run selected in the list of runs is outlined. The
note lists the settings of each run (downscale, keypoint limit, and tie point limit), so that runs
with different settings (e.g. downscale 1 and 2) can be compared in one view.</p>
<p><b>Shared tie points</b>: which enabled, oriented images of the open chunk share valid 3D tie
points, and how many, in a matrix like that of ‘Image pairs’. A coloured cell is an image pair whose
images both observe at least one valid 3D tie point, which means that both have a 2D tie point for
that track. The cell is coloured on a logarithmic scale by the number of such 3D tie points. Note
that two images can share a 3D tie point without a match between them, through the other images of
its track. A white cell is an image pair that shares no valid 3D tie point, and a light-red row and
column mark an image that shares none with another image. The bars count, for each image, the other
images with which it shares at least one.</p>
<p>The matrix is read from the active chunk of the open project, not from the log (see The open
chunk). It numbers the enabled, oriented images of the chunk from 0 in chunk order, and its note and
tooltip also give their labels. The log numbers its images per run, so the numbers of the two
matrices need not agree. Attention should go to images that share 3D tie points with few other
images and to clusters of images with no shared 3D tie points between them.</p>

<h3>Usage</h3>
<p><b>Parse from clipboard:</b> copy the SfM part (Align Photos) of
the Metashape Console log (or the whole log, see Several runs in one
log) to the clipboard, then click
<b>Parse from clipboard</b>.</p>
<p><b>Open log file:</b> choose a saved Metashape log file (TXT, LOG,
or HTML).</p>
<p><b>SfM run:</b> when the log holds several runs, choose the run that
the summary and the first four graphs show.</p>
<p><b>Leave out the open chunk:</b> tick the box to compare logs of
other projects without the values of the open chunk, and untick it to
read the open chunk again (see The open chunk).</p>
<p>The buttons at the bottom of the window follow the current tab. The Summary tab shows <b>Copy as
plain text</b> and <b>Copy as HTML</b>, and the graph tab shows <b>Copy graph</b> and <b>Save graph
as PNG</b>. <b>About</b> and <b>Close</b> are on every tab.</p>
<p>The Console pane lists every parsed log with its source and every run with its main counts. It
also lists the run shown and the shared 3D tie points of the open chunk, along with the time it took
to read them. The Console pane also echoes the run chosen in the list, copies of the summary and
graphs, and the saved graphs. It names the open chunk, gives the warnings and the note on its match
with the log, and says when the open chunk is left out or included again.</p>
"""


class SfmLogParserDialog(QtWidgets.QDialog):
    """Dialog for parsing and summarising Metashape SfM logs."""

    # The runs of the parsed log and the run shown (ShowParsedLog and
    # ShowSelectedRun), None before a log is parsed.
    parsedRuns = None
    shownRunIndex = None
    # The open chunk read for the parsed log (ReadOpenChunk or
    # LeftOutOpenChunk, HandOpenChunkToGraphs), None before a log is
    # parsed, and whether the box Leave out the open chunk is ticked
    # (SetOpenChunkLeftOut).
    openChunk = None
    isOpenChunkLeftOut = False

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("SfM Log Parser")
        self.setWindowFlags(
            self.windowFlags()
            & ~QtCore.Qt.WindowContextHelpButtonHint)
        # The dialog opens at its default size, or smaller on a small
        # screen, so that its buttons stay on the screen.
        dialogWidth, dialogHeight = DIALOG_DEFAULT_WIDTH, DIALOG_DEFAULT_HEIGHT
        try:
            screen = parent.screen() if parent is not None and hasattr(parent, "screen") else None
            if screen is None:
                screen = QtGui.QGuiApplication.primaryScreen()
            availableGeometry = screen.availableGeometry()
            dialogWidth, dialogHeight = FittedDialogSize(availableGeometry.width(), availableGeometry.height())
        except Exception as suppressedError:
            ReportSuppressedError("sfmLogParser.SfmLogParserDialog (available screen size)", suppressedError)
        self.resize(dialogWidth, dialogHeight)
        self._BuildUi()

    def _BuildUi(self):
        mainLayout = QtWidgets.QVBoxLayout(self)

        # --- Input buttons ---
        inputRow = QtWidgets.QHBoxLayout()
        self.buttonClipboard = QtWidgets.QPushButton(
            "Parse from clipboard")
        self.buttonClipboard.clicked.connect(self._OnParseClipboard)
        self.buttonOpenFile = QtWidgets.QPushButton(
            "Open log file\u2026")
        self.buttonOpenFile.clicked.connect(self._OnOpenFile)
        inputRow.addWidget(self.buttonClipboard)
        inputRow.addWidget(self.buttonOpenFile)
        # Leaves the open chunk out of the Summary and the tab Shared tie
        # points, for comparing logs of other projects (unticked by
        # default).
        self.leaveOutOpenChunkCheckBox = QtWidgets.QCheckBox(LEAVE_OUT_OPEN_CHUNK_TEXT)
        self.leaveOutOpenChunkCheckBox.setChecked(False)
        self.leaveOutOpenChunkCheckBox.setToolTip(
            "Leave out the active chunk of the open project, for example to compare logs of other projects. The "
            "Summary and the tab Shared tie points then give no values of the open chunk. Untick the box to read "
            "the open chunk again.")
        self.leaveOutOpenChunkCheckBox.toggled.connect(self._OnLeaveOutOpenChunkToggled)
        inputRow.addWidget(self.leaveOutOpenChunkCheckBox)
        mainLayout.addLayout(inputRow)

        # --- List of the runs of the log: the run that the summary and
        # graphs 1 to 4 show ---
        runRow = QtWidgets.QHBoxLayout()
        runRow.addWidget(QtWidgets.QLabel("SfM run:"))
        self.runComboBox = QtWidgets.QComboBox()
        self.runComboBox.addItem("No log parsed yet")
        self.runComboBox.setEnabled(False)
        self.runComboBox.setToolTip(
            "The run of the log that the summary and the graphs Image pairs, Tie point growth, Keypoints per image, "
            "and Pipeline funnel show. Runs side by side shows every run and outlines this one. Shared tie points "
            "shows the open chunk.")
        # A long entry must not make the dialog wider than a small screen.
        self.runComboBox.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.runComboBox.setMinimumContentsLength(24)
        self.runComboBox.currentIndexChanged.connect(self._OnRunChosen)
        runRow.addWidget(self.runComboBox, 1)
        mainLayout.addLayout(runRow)

        # --- Output area: the summary and the graphs, one tab each.  Each
        # graph sits in a scroll area, so that its minimum size never
        # hides the controls of the dialog on a small screen. ---
        self.outputTextEdit = QtWidgets.QTextEdit()
        self.outputTextEdit.setReadOnly(True)
        self.outputTextEdit.setPlaceholderText(
            "Choose an input method above\u2026")
        self.graphWidgets = [PairConnectivityGraph(), TiePointGrowthGraph(), KeypointGraph(),
                             PipelineFunnelGraph(), RunComparisonGraph(), SharedTiePointGraph()]
        self.graphScrollAreas = []
        self.outputTabs = QtWidgets.QTabWidget()
        self.outputTabs.addTab(self.outputTextEdit, "Summary")
        for graphWidget in self.graphWidgets:
            graphScrollArea = QtWidgets.QScrollArea()
            graphScrollArea.setWidgetResizable(True)
            graphScrollArea.setWidget(graphWidget)
            self.graphScrollAreas.append(graphScrollArea)
            self.outputTabs.addTab(graphScrollArea, graphWidget.TAB_LABEL)
        # At the smallest size of the dialog, the summary and the visible
        # part of a graph keep a usable height.
        self.outputTabs.setMinimumHeight(260)
        mainLayout.addWidget(self.outputTabs, 1)

        # --- Bottom buttons: About and Close on every tab, the copy
        # buttons only on the tabs that they act on (_OnTabChanged) ---
        buttonLayout = QtWidgets.QHBoxLayout()

        aboutButton = QtWidgets.QPushButton("About")
        aboutButton.clicked.connect(self._ShowAbout)
        buttonLayout.addWidget(aboutButton)

        buttonLayout.addStretch()

        self.copyGraphButton = QtWidgets.QPushButton("Copy graph")
        self.copyGraphButton.setToolTip("Copy the graph of the current tab, with its title, axes, legend, and note.")
        self.copyGraphButton.clicked.connect(self._OnCopyGraph)
        buttonLayout.addWidget(self.copyGraphButton)

        self.saveGraphButton = QtWidgets.QPushButton("Save graph as PNG")
        self.saveGraphButton.setToolTip("Save the graph of the current tab, with its title, axes, legend, and note, "
                                        "as a PNG file.")
        self.saveGraphButton.clicked.connect(self._OnSaveGraph)
        buttonLayout.addWidget(self.saveGraphButton)

        self.copyPlainButton = QtWidgets.QPushButton("Copy as plain text")
        self.copyPlainButton.clicked.connect(self._OnCopyPlain)
        buttonLayout.addWidget(self.copyPlainButton)

        self.copyHtmlButton = QtWidgets.QPushButton("Copy as HTML")
        self.copyHtmlButton.clicked.connect(self._OnCopyHtml)
        buttonLayout.addWidget(self.copyHtmlButton)

        closeButton = QtWidgets.QPushButton("Close")
        closeButton.clicked.connect(self.reject)
        buttonLayout.addWidget(closeButton)

        mainLayout.addLayout(buttonLayout)

        # The buttons follow the current tab from the start.  The signal is
        # connected only now, because QTabWidget emits it as soon as its
        # first tab is added, before the buttons exist.
        # All tabs are added, so a change of the current tab now comes from
        # the user (a click, Ctrl+Tab, or the mouse wheel) and is echoed.
        self.outputTabs.currentChanged.connect(self._OnTabChangedByUser)
        self._OnTabChanged(self.outputTabs.currentIndex())

    def _OnParseClipboard(self):
        clipboardText = QtWidgets.QApplication.clipboard().text()
        if not clipboardText.strip():
            emptyClipboardText = "The clipboard is empty. Copy the Metashape log text first."
            print("SfM Log Parser: " + emptyClipboardText)
            QtWidgets.QMessageBox.information(self, "SfM Log Parser", emptyClipboardText)
            return
        text = NormaliseText(clipboardText)
        ShowParsedLog(self, text, "clipboard")

    def _OnOpenFile(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Open Metashape log file", "",
            "Log files (*.txt *.log *.htm *.html);;All files (*.*)")
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8",
                      errors="ignore") as logFile:
                logFileText = logFile.read()
            text = NormaliseText(logFileText, hintPath=path)
            ShowParsedLog(self, text, os.path.basename(path), consoleSource=path)
        except Exception as readError:
            print("SfM Log Parser: could not read or parse the log file {}: {}".format(
                path, DescribeException(readError)))
            QtWidgets.QMessageBox.warning(
                self, "SfM Log Parser",
                "Could not read or parse the log file:\n{}".format(DescribeException(readError)))

    def _OnCopyPlain(self):
        QtWidgets.QApplication.clipboard().setText(
            self.outputTextEdit.toPlainText())
        print(SummaryCopiedConsoleLine("plain text", self.parsedRuns, self.shownRunIndex))

    def _OnCopyHtml(self):
        QtWidgets.QApplication.clipboard().setText(
            self.outputTextEdit.toHtml())
        print(SummaryCopiedConsoleLine("HTML", self.parsedRuns, self.shownRunIndex))

    def _OnRunChosen(self, runIndex):
        """Show the run chosen in the list of runs in the summary and in
        graphs 1 to 4, and outline it in graph 5.  The Console also gives
        the warnings and the note on the match of the chosen run with the
        open chunk (OpenChunkFindingConsoleLines)."""
        runs = self.parsedRuns
        if runs and 0 <= runIndex < len(runs):
            ShowSelectedRun(self, runIndex)
            print(RunChosenConsoleLine(runs, runIndex))
            openChunk = getattr(self, "openChunk", None)
            if openChunk is not None:
                PrintConsoleLines(OpenChunkFindingConsoleLines(runs[runIndex], openChunk))

    def _OnLeaveOutOpenChunkToggled(self, isChecked):
        """Leave the open chunk out, or include it again, as the box Leave
        out the open chunk says (SetOpenChunkLeftOut)."""
        SetOpenChunkLeftOut(self, bool(isChecked))

    def _OnTabChangedByUser(self, tabIndex):
        """Echo the tab that the user shows and show its buttons
        (_OnTabChanged).  The echo does not come from tabBarClicked, which
        the press of any mouse button sends, also on the tab already
        shown."""
        if tabIndex >= 0:
            print("SfM Log Parser: tab {} shown.".format(self.outputTabs.tabText(tabIndex)))
        self._OnTabChanged(tabIndex)

    def _OnTabChanged(self, tabIndex):
        """Show the buttons that act on the tab *tabIndex* and hide the
        others, so that no tab offers a button that does nothing there:
        Copy as plain text and Copy as HTML on the Summary tab, Copy graph
        and Save graph as PNG on the tab of a graph.  About and Close stay
        on every tab."""
        isGraphTab = 1 <= tabIndex <= len(self.graphWidgets)
        for graphButton in (self.copyGraphButton, self.saveGraphButton):
            graphButton.setVisible(isGraphTab)
        for summaryButton in (self.copyPlainButton, self.copyHtmlButton):
            summaryButton.setVisible(not isGraphTab)

    def CurrentGraphWidget(self):
        """The graph of the current tab, or None on the summary tab."""
        tabIndex = self.outputTabs.currentIndex()
        if 1 <= tabIndex <= len(self.graphWidgets):
            return self.graphWidgets[tabIndex - 1]
        return None

    def _OnCopyGraph(self):
        """Copy the picture of the graph of the current tab, with its title,
        axes, legend, and note, to the clipboard (GraphPicture)."""
        graphWidget = self.CurrentGraphWidget()
        if graphWidget is None:
            print("SfM Log Parser: " + _NO_GRAPH_ON_THE_SUMMARY_TAB_TEXT)
            QtWidgets.QMessageBox.information(self, "SfM Log Parser", _NO_GRAPH_ON_THE_SUMMARY_TAB_TEXT)
            return
        QtWidgets.QApplication.clipboard().setImage(GraphPicture(graphWidget))
        print("SfM Log Parser: {} copied.".format(GraphWidgetConsoleName(graphWidget)))

    def _OnSaveGraph(self):
        """Save the picture of the graph of the current tab, with its title,
        axes, legend, and note, as a PNG file (GraphPicture)."""
        graphWidget = self.CurrentGraphWidget()
        if graphWidget is None:
            print("SfM Log Parser: " + _NO_GRAPH_ON_THE_SUMMARY_TAB_TEXT)
            QtWidgets.QMessageBox.information(self, "SfM Log Parser", _NO_GRAPH_ON_THE_SUMMARY_TAB_TEXT)
            return
        runs = getattr(graphWidget, "runs", None)
        runNumber = None
        if runs and len(runs) > 1 and not ShowsNoSingleRun(graphWidget):
            runNumber = graphWidget.summarisedRunIndex + 1
        path, filterUnused = QtWidgets.QFileDialog.getSaveFileName(
            self, "Save graph as PNG", GraphFileName(graphWidget.TAB_LABEL, runNumber), "PNG images (*.png)")
        if not path:
            return
        if not path.lower().endswith(".png"):
            path += ".png"
        if not GraphPicture(graphWidget).save(path, "PNG"):
            print("SfM Log Parser: could not save the {} as {}.".format(GraphWidgetConsoleName(graphWidget), path))
            QtWidgets.QMessageBox.warning(self, "SfM Log Parser", f"Could not save the graph as {path}.")
            return
        print("SfM Log Parser: {} saved as {}.".format(GraphWidgetConsoleName(graphWidget), path))

    def _ShowAbout(self):
        aboutDialog = QtWidgets.QDialog(self)
        aboutDialog.setWindowTitle(
            "About: SfM Log Parser")
        aboutDialog.setWindowFlags(
            aboutDialog.windowFlags()
            & ~QtCore.Qt.WindowContextHelpButtonHint)
        aboutDialog.setMinimumSize(520, 480)
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

_MENU_LABEL = "MetaFOCUS/SfM Process Diagnostics/SfM Log Parser"
_ICON_NAME = "sfm_log_parser_32.png"


def _ShowParserDialog():
    SfmLogParserDialog(
        QtWidgets.QApplication.activeWindow()).exec_()


def RegisterMenuItem():
    """Register the menu item.  Called by the MetaFOCUS loader."""
    try:
        Metashape.app.removeMenuItem(_MENU_LABEL)
    except Exception as suppressedError:
        ReportSuppressedError("sfmLogParser.RegisterMenuItem", suppressedError)
    iconPath = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "icons", _ICON_NAME)
    if os.path.exists(iconPath):
        Metashape.app.addMenuItem(
            _MENU_LABEL, _ShowParserDialog, icon=iconPath)
    else:
        Metashape.app.addMenuItem(_MENU_LABEL, _ShowParserDialog)
