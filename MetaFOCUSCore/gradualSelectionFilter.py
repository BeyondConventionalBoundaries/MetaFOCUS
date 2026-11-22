"""
Gradual Selection Filter
========================

Iteratively filters the sparse tie-point cloud using Metashape's
gradual selection criteria (Reconstruction Uncertainty, Projection
Accuracy, Reprojection Error, and Image Count) and re-runs Optimize
Cameras after each iteration.  This automates the standard
"gradual selection -> optimise -> repeat" workflow.

Two removal modes are offered for the first three criteria:

  * Absolute threshold (stepped): in each iteration, lower the
    working threshold by one step from the current worst value,
    never below the target, and remove the points above it, until
    the worst value reaches the target (the rigorous approach used
    in most published SfM quality-control protocols).
  * Percentile: in each iteration, remove a fixed fraction of the
    worst points by each criterion (rounded to whole points and at
    least one, points with equal values in the order of the tie
    points), but never a point that already meets the target (a
    quick, convenience approach).

Image Count is reversed (points seen in fewer than a minimum number
of images are removed) and is applied once, in either mode.

Before it removes any tie point, the tool re-adjusts the chunk with the
Optimize Cameras settings of the dialog (iteration 0), so that every
iteration is adjusted with the same settings.  It records quality metrics
for the chunk as found, for iteration 0, and after every iteration (the
tie-point count, the RMS reprojection error, the largest and smallest
per-image RMS reprojection error, the worst value of each criterion, the
mean and the median tie-point standard deviation, an approximate sigma
nought, the largest movement of an image and of a marker, the images no
longer oriented, and, when measured markers have reference coordinates,
the control-point and check-point RMSE)
and presents them in a results dialog with a table and line graphs,
so that the analyst can judge each intermediate state and, if
preferred, keep an earlier iteration, or the chunk as found, rather
than the final iteration.

Author:     Geert J. Verhoeven (ORCID: 0000-0003-4825-9604)
            Based on an original script by Martin Wieser
            (ORCID: 0009-0005-9870-6494).
Contact:    geert.verhoeven@univie.ac.at
Version:    1.0.1 (2026-11-22)
Licence:    GPL-3.0-or-later WITH GPL-3.0-linking-exception
Requires:   Agisoft Metashape Professional 2.x
Developed for: Agisoft Metashape Professional 2.3.2
"""

import array
import html
import math
import os
import statistics
import sys
from .photogrammetryUtilities import (
    ReportSuppressedError, IsInspectionMarker,
    ConsoleBlockEnd,
    ConsoleBlockStart,
    DescribeException,
    FormatCountWithNoun,
    FormatSignificantDigits,
    JoinWithAnd,
    MetadataValue,
    PrintConsoleLines,
    GetChunkScale,
    IsChunkScalePhysical,
    ComputeRedundancyBreakdown,
    ImageKeys,
    CountProjectionsPerTrack,
    CountTiePointNetwork,
    GetImageCalibration,
    TiePointResidual,
    TrackTakesPartInAdjustment,
    UnknownsLeavingWithImages,
    IsOrientedEnabledCamera,
    DisabledOrientedImagesWithObservations,
    DisabledOrientedImagesText,
    VectorFromMetashape,
    MarkerReferenceDiscrepancy,
    ValidatedCovarianceElements,
)
import Metashape
from PySide2 import QtWidgets, QtCore, QtGui
import functools


# ===================================================================
# Menu metadata
# ===================================================================

_MENU_LABEL = ("MetaFOCUS/Filtering and Project Management/"
               "Gradual Selection Filter")
_ICON_NAME = "gradual_selection_filter_32.png"


# ===================================================================
# Filtering logic
# ===================================================================

FILTER_CRITERIA = {
    "Reconstruction Uncertainty":
        Metashape.TiePoints.Filter.ReconstructionUncertainty,
    "Projection Accuracy":
        Metashape.TiePoints.Filter.ProjectionAccuracy,
    "Reprojection Error":
        Metashape.TiePoints.Filter.ReprojectionError,
}

# The tooltips of the criteria, on the tick box of each criterion and on
# the fields of its target and step, and the tooltip of the stop on the
# RMS reprojection error in pixels.  Each says what its value is, for one
# 3D tie point or for the whole chunk, with its unit, and what a target
# removes.
CRITERION_TOOLTIPS = {
    "Reconstruction Uncertainty": (
        "The Reconstruction Uncertainty criterion of Metashape's Clean\n"
        "Tie Points: the shape ratio of the error ellipsoid of each 3D\n"
        "tie point, its largest semi-axis divided by its smallest, a\n"
        "value without a unit. A target of 10 removes the 3D tie points\n"
        "whose largest semi-axis is more than ten times their smallest."),
    "Projection Accuracy": (
        "The Projection Accuracy criterion of Metashape's Clean Tie\n"
        "Points: the mean keypoint size of the 2D tie points of each 3D\n"
        "tie point, a size, not an accuracy. A target of 3 removes the\n"
        "3D tie points whose 2D tie points have a mean keypoint size\n"
        "above 3, which were detected mainly on coarse levels of the\n"
        "image pyramid."),
    "Reprojection Error": (
        "The Reprojection Error criterion of Metashape's Clean Tie\n"
        "Points: one value per 3D tie point, without a unit. The\n"
        "residual of each of its 2D tie points is divided by the\n"
        "keypoint size of that 2D tie point, and the largest result\n"
        "is kept. A target of 0.3 removes every 3D tie point that has\n"
        "a 2D tie point whose residual exceeds 0.3 times its keypoint\n"
        "size. This is not the RMS reprojection error in pixels of\n"
        "the setting \u2018Stop when RMS reprojection error \u2264\u2019 above."),
    "Image Count": (
        "Remove points seen in fewer than the target number of\n"
        "images. Unlike the others, larger values are preferred here, and\n"
        "because a point's image count does not change as other\n"
        "points are removed, this is applied once (not stepped\n"
        "and not by percentile)."),
}

# What a disabled image that is still oriented and has 2D tie points or
# marker projections means for the values of the filter, after the
# sentence that names such images in the question before a run
# (DisabledOrientedImagesText): for one image and for more.
DISABLED_IMAGE_METRICS_TEXT = (
    "Optimize Cameras, which each iteration runs, still adjusts such an image, but the RMS reprojection error, the "
    "redundancy, and the approximate \u03c3\u2080 of the filter count the enabled images only. Enabling the image, "
    "or resetting its orientation, removes this difference.")
DISABLED_IMAGES_METRICS_TEXT = (
    "Optimize Cameras, which each iteration runs, still adjusts such images, but the RMS reprojection error, the "
    "redundancy, and the approximate \u03c3\u2080 of the filter count the enabled images only. Enabling these "
    "images, or resetting their orientation, removes this difference.")

RMS_STOP_TOOLTIP = (
    "An additional stopping condition on the overall RMS\n"
    "reprojection error in pixels: the root mean square of the\n"
    "residuals of the 2D tie points of the valid 3D tie points on\n"
    "the enabled, oriented images whose residuals can be evaluated,\n"
    "meant to match the value in brackets after \u2018RMS reprojection\n"
    "error\u2019 in the Metashape chunk information. The filter leaves\n"
    "out the 2D tie points of a disabled image that is still\n"
    "oriented, which Metashape includes. It is not the Reprojection Error\n"
    "criterion, which has one value per 3D tie point and no\n"
    "unit. The run also still stops at the criteria targets,\n"
    "the iteration limit, or the safety cap.")

# Typical absolute-threshold defaults from common SfM quality-control
# protocols.
DEFAULT_ABSOLUTE_TARGETS = {
    "Reconstruction Uncertainty": 10.0,
    "Projection Accuracy": 3.0,
    "Reprojection Error": 0.3,
}

# Hard ceiling on iterations when the user chooses "unlimited" (0),
# to guarantee the loop always terminates.
UNLIMITED_ITERATION_CEILING = 200

# Auto-stop (used only in unlimited percentile mode): if the RMS
# reprojection error improves by less than this relative amount for
# this many consecutive iterations, the run is considered converged.
CONVERGENCE_RELATIVE_IMPROVEMENT = 0.0005   # 0.05 %
CONVERGENCE_PATIENCE = 2

# Fraction of the total reprojection-error reduction that the
# recommended iteration should capture.
RECOMMENDATION_IMPROVEMENT_FRACTION = 0.95

# The iteration of the first row of the results: the chunk as the user
# left it (the state as found).  Iteration 0 follows it: the chunk
# re-adjusted with the Optimize Cameras settings of the dialog before any
# tie point is removed.
STATE_AS_FOUND = "as found"

# The level of the one-sided Wilcoxon signed-rank test with which another
# state must be better than the default state to be suggested
# (RecommendIteration).
SIGNIFICANCE_LEVEL = 0.05


def IsStateAsFound(row):
    """True for the row of the chunk as found (STATE_AS_FOUND)."""
    return row.get("iteration") == STATE_AS_FOUND


def StateName(iteration):
    """"the chunk as found" or "iteration 3", for the texts of the run."""
    return "the chunk as found" if iteration == STATE_AS_FOUND else "iteration {}".format(iteration)


def StateCellText(iteration):
    """The text of the Iteration column of the results table: "As found"
    for the chunk as found, otherwise the number of the iteration."""
    return "As found" if iteration == STATE_AS_FOUND else str(iteration)


def ConsoleStatePrefix(iteration):
    """"[as found]" or "[iteration 03]": the start of the Console line of
    the metrics of a state."""
    return "[as found]" if iteration == STATE_AS_FOUND else "[iteration {:02d}]".format(iteration)


def PreviousStateText(row):
    """The state from which the movements and the images no longer oriented
    of *row* are measured: "the chunk as found" for iteration 0, which is a
    new adjustment, and "the previous iteration" for a later one."""
    return "the chunk as found" if row.get("iteration") == 0 else "the previous iteration"


# Metashape's placeholder for a criterion value that it cannot give a tie
# point: the square root of the largest double, about 1.34e154.  It takes
# no part in the worst value of a criterion or in the test of its target
# (it was the Reprojection Error of one valid tie point of Geert's chunk
# of 193,451).  The tie points that carry it are still
# selected by a percentile or a threshold of that criterion, because the
# placeholder lies above every threshold.
METASHAPE_PLACEHOLDER_VALUE = math.sqrt(sys.float_info.max)
METASHAPE_PLACEHOLDER_TEXT = "1.34 \u00d7 10\u00b9\u2075\u2074"


def IsMetashapePlaceholder(value):
    """True for Metashape's placeholder value (METASHAPE_PLACEHOLDER_VALUE)
    and anything as large."""
    return value >= METASHAPE_PLACEHOLDER_VALUE * (1.0 - 1e-9)


def ValidityOfTiePoints(chunk):
    """[True or False] of every tie point of the chunk (point.valid), read
    once for all criteria, or None without tie points."""
    points = getattr(getattr(chunk, "tie_points", None), "points", None)
    if points is None:
        return None
    return [bool(point.valid) for point in points]


def CriterionValueCensus(chunk, criterion, validity=None):
    """(sorted values, placeholder count) of one gradual-selection
    criterion over the valid tie points.  NaN, values that are not finite,
    and values that are not positive are dropped, because Metashape gives
    them to points that carry no valid criterion value, and Metashape's
    placeholder (IsMetashapePlaceholder) is counted apart.  The values of
    invalid tie points take no part, as in SelectByPercentile.
    *validity* (ValidityOfTiePoints) spares a pass over the tie points
    when several criteria are read.  When Metashape gives a number of
    values that differs from the number of tie points, all values are
    used."""
    tiePointFilter = Metashape.TiePoints.Filter()
    tiePointFilter.init(chunk, criterion=criterion)
    values = list(tiePointFilter.values)
    if validity is None:
        validity = ValidityOfTiePoints(chunk)
    if validity is not None and len(validity) == len(values):
        values = [value for isValid, value in zip(validity, values) if isValid]
    keptValues, placeholderCount = [], 0
    for value in values:
        if not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0.0:
            continue
        if IsMetashapePlaceholder(value):
            placeholderCount += 1
            continue
        keptValues.append(value)
    keptValues.sort()
    return keptValues, placeholderCount


def GetCriterionValues(chunk, criterion):
    """Sorted values of one gradual-selection criterion over the valid tie
    points, without NaN, values that are not positive, and Metashape's
    placeholder (CriterionValueCensus)."""
    return CriterionValueCensus(chunk, criterion)[0]


def CountPointsToRemoveByPercentile(candidateCount, percentToRemove):
    """Number of points that one percentile step removes from
    *candidateCount* points: the requested percentage, rounded to the
    nearest whole point (a half rounds up) and at least one, so that a
    step always makes progress while points lie above the target.
    10 % of 100,000 points is 10,000, 10 % of 10 points is 1."""
    if candidateCount <= 0 or percentToRemove <= 0:
        return 0
    return max(1, int(math.floor(candidateCount * percentToRemove / 100.0 + 0.5)))


def SelectByPercentile(chunk, criterion, percentToRemove, targetValue=None):
    """Select the worst *percentToRemove* % of points by criterion.

    The percentage refers to the valid tie points with a valid
    (positive) value of the criterion, and the number of points follows
    from CountPointsToRemoveByPercentile.  The points with the largest
    values are selected directly, points with equal values in the order
    of the tie points, so that the selection is exact and deterministic
    also when many points share a value: a threshold on the value alone
    (Filter.selectPoints) selects only the values above it, which
    under-removes and selects nothing when the values at the threshold
    are tied.  A point that already meets the target (a value at or
    below *targetValue*, when given) is never selected, so fewer points
    are selected when fewer lie above the target.

    Returns (thresholdValue, selectedCount), where thresholdValue is
    the smallest selected value, or (None, 0) when no point can be
    selected.  Raises RuntimeError when the criterion values cannot be
    matched with the tie points.
    """
    points = chunk.tie_points.points
    for point in points:
        point.selected = False

    tiePointFilter = Metashape.TiePoints.Filter()
    tiePointFilter.init(chunk, criterion=criterion)
    values = list(tiePointFilter.values)
    if len(values) != len(points):
        raise RuntimeError(
            "the {:,} values of the gradual-selection criterion do not "
            "match the {:,} tie points".format(len(values), len(points)))
    candidateIndices = [
        pointIndex for pointIndex, point in enumerate(points)
        if point.valid and not math.isnan(values[pointIndex])
        and values[pointIndex] > 0.0]
    removalCount = CountPointsToRemoveByPercentile(
        len(candidateIndices), percentToRemove)
    if targetValue is not None:
        candidateIndices = [
            pointIndex for pointIndex in candidateIndices
            if values[pointIndex] > targetValue]
    # The sort is stable, so points with equal values keep the order of
    # the tie points.
    candidateIndices.sort(key=values.__getitem__, reverse=True)
    selectedIndices = candidateIndices[:removalCount]
    if not selectedIndices:
        return None, 0
    for pointIndex in selectedIndices:
        points[pointIndex].selected = True
    return values[selectedIndices[-1]], len(selectedIndices)


def SelectByAbsoluteThreshold(chunk, criterion, targetValue):
    """Select all points with criterion value greater than target.

    Returns selectedCount.
    """
    tiePoints = chunk.tie_points
    for point in tiePoints.points:
        point.selected = False

    tiePointFilter = Metashape.TiePoints.Filter()
    tiePointFilter.init(chunk, criterion=criterion)
    tiePointFilter.selectPoints(targetValue)
    return sum(1 for point in tiePoints.points if point.selected)


def ClearTiePointSelection(chunk):
    """Deselect every tie point of the chunk, so that a removal the
    safety cap blocked does not stay selected in the project."""
    for point in chunk.tie_points.points:
        point.selected = False


def ComputeMinimumRetainedPointCount(initialPointCount, safetyCapPercent):
    """Fewest valid tie points that the safety cap lets a run leave: the
    initial count minus the largest whole number of points within the
    cap (rounded down), so that a run never removes more than the cap.
    With 11 points and a cap of 50 %, at most 5 points may be removed and
    at least 6 remain (rounding the remaining count down instead would
    allow 6 removals, 54.5 %)."""
    maximumRemovedCount = int(math.floor(
        initialPointCount * safetyCapPercent / 100.0))
    return initialPointCount - maximumRemovedCount


def GetMinimumImageCount(chunk):
    """Return the smallest image count among valid tie points.

    The image count of a point is the number of images in which it
    is measured.  Unlike the other criteria, a low value is bad
    (a point seen in few images is weakly constrained).
    """
    tiePoints = getattr(chunk, "tie_points", None)
    if tiePoints is None or not tiePoints.points:
        return None
    tiePointFilter = Metashape.TiePoints.Filter()
    tiePointFilter.init(
        chunk, criterion=Metashape.TiePoints.Filter.ImageCount)
    values = tiePointFilter.values
    points = tiePoints.points
    if len(values) != len(points):
        return None
    smallest = None
    for point, value in zip(points, values):
        if not point.valid:
            continue
        if smallest is None or value < smallest:
            smallest = value
    return smallest


def SelectByImageCountBelow(chunk, minimumImages):
    """Select valid points seen in fewer than *minimumImages* images.

    Image count is reversed relative to the other criteria (low is
    bad), and a point's image count does not change as other points
    are removed, so this is a one-shot removal rather than a stepped
    or percentile one.  Returns selectedCount, or None if the filter
    values cannot be aligned with the points.
    """
    tiePoints = chunk.tie_points
    tiePointFilter = Metashape.TiePoints.Filter()
    tiePointFilter.init(
        chunk, criterion=Metashape.TiePoints.Filter.ImageCount)
    values = tiePointFilter.values
    points = tiePoints.points
    if len(values) != len(points):
        return None
    selectedCount = 0
    for point, value in zip(points, values):
        below = point.valid and value < minimumImages
        point.selected = below
        if below:
            selectedCount += 1
    return selectedCount


# ===================================================================
# Quality metrics
# ===================================================================

def _EmptyReprojectionStatistics(sigmaNoughtProjectionCount=0, projectionCounts=None):
    """The statistics of a chunk without residuals (see
    ComputeReprojectionStatistics)."""
    return {"rms": float("nan"),
            "maximumImageRms": float("nan"),
            "minimumImageRms": float("nan"),
            "sumOfSquaredNormalisedResiduals": 0.0,
            "normalisedResidualCount": 0,
            "sigmaNoughtProjectionCount": sigmaNoughtProjectionCount,
            "projectionCounts": projectionCounts,
            "sigmaNoughtProjectionCounts": projectionCounts,
            "populationCount": sigmaNoughtProjectionCount,
            "unevaluatedResidualCount": 0,
            "withoutKeypointSizeCount": 0,
            "leftWithTheirTrackCount": 0,
            "imagesLeftCount": 0,
            "imagesWithoutTiePointsCount": 0,
            "tiePointImageKeys": None,
            "unknownsLeavingWithImages": 0}


def _ZeroArray(typeCode, length):
    """An array of *typeCode* with *length* zeros."""
    values = array.array(typeCode)
    values.frombytes(bytes(values.itemsize * length))
    return values


def SplitImagesBySum(imageRanges, normalisedTrackIds, sigmaNoughtProjectionCounts):
    """(keptImages, lostImages) of the images of the tie-point network:
    *imageRanges* holds (camera, start, end) for every enabled, oriented
    image with a 2D tie point of a track in the adjustment, and
    normalisedTrackIds[start:end] are the tracks of its 2D tie points whose
    normalised residuals were formed.  An image keeps 2D tie points in the
    sum of sigma nought when one of these tracks still takes part with the
    counts *sigmaNoughtProjectionCounts*, and otherwise it leaves the
    network.  The enabled, oriented images without such a 2D tie point
    leave the network as well (ComputeReprojectionStatistics)."""
    keptImages = []
    lostImages = []
    for camera, start, end in imageRanges:
        isKept = False
        for index in range(start, end):
            if TrackTakesPartInAdjustment(sigmaNoughtProjectionCounts, normalisedTrackIds[index]):
                isKept = True
                break
        (keptImages if isKept else lostImages).append(camera)
    return keptImages, lostImages


def _ImageProjections(tiePoints, camera):
    """The projections of the image *camera*, or None when it has none (a
    failure to read them is reported once in the Console)."""
    try:
        return tiePoints.projections[camera]
    except KeyError:
        return None  # an image without projections
    except Exception as suppressedError:
        ReportSuppressedError("gradualSelectionFilter.ComputeReprojectionStatistics.projections", suppressedError)
        return None


def _CountLeftOut(leftOutCounts, takesPart, trackId):
    """Count a 2D tie point of the track *trackId* as left out of sigma
    nought when its track takes part in the adjustment; return 1 when it
    was counted, else 0."""
    if not takesPart:
        return 0
    leftOutCounts[trackId] += 1
    return 1


def ComputeReprojectionStatistics(chunk):
    """Compute the RMS reprojection error and the largest and smallest
    per-image RMS reprojection error over the enabled, oriented images
    (never the keyframes of an animation track), together with the sums
    for sigma nought.  They are the images of the adjustment only while
    no disabled image is still oriented and has 2D tie points or marker
    projections, which Metashape adjusts as well, so the question before
    a run names such images (DisabledOrientedImagesWithObservations).

    Returns a dict with the keys:
      rms                               RMS reprojection error in pixels
      maximumImageRms, minimumImageRms  largest and smallest per-image
                                        RMS reprojection error in pixels
      sumOfSquaredNormalisedResiduals   sum of the squared residuals
                                        divided by the squared keypoint size
      normalisedResidualCount           number of projections in that
                                        sum
      sigmaNoughtProjectionCount        number of projections that the
                                        redundancy of sigma nought counts
                                        (two observations each): the sum is
                                        complete only when it equals
                                        normalisedResidualCount
      projectionCounts                  projections per track on
                                        enabled, oriented images (None
                                        when there are no tie points)
      sigmaNoughtProjectionCounts       the same without the projections
                                        left out of sigma nought, for its
                                        redundancy
      populationCount                   projections of the tie-point
                                        network before any was left out
      unevaluatedResidualCount,         projections left out of sigma
      withoutKeypointSizeCount,         nought: residual not evaluable,
      leftWithTheirTrackCount           no keypoint size, and evaluable
                                        ones whose track kept fewer than
                                        two projections
      imagesLeftCount                   images left without projections
                                        in the sum
      imagesWithoutTiePointsCount       enabled, oriented images without
                                        projections in the adjustment
      tiePointImageKeys                 the keys of the images with
                                        projections in the adjustment
      unknownsLeavingWithImages         the unknowns that leave the
                                        network with the images of
                                        imagesLeftCount
                                        (UnknownsLeavingWithImages)

    Every residual comes from Camera.error (TiePointResidual), which
    evaluates the whole camera model of its image, rolling-shutter motion
    included, as in the Interior Orientation Viewer.  rms,
    maximumImageRms, and minimumImageRms use every projection of a valid
    tie point on such an image whose residual can be evaluated, so that
    the RMS stays comparable with Metashape's chunk information (NaN when
    there is none).  sumOfSquaredNormalisedResiduals and
    normalisedResidualCount use only the projections of the tracks that
    take part in the adjustment (TrackTakesPartInAdjustment), the same
    projections as the tie-point observations of the shared redundancy
    (ComputeRedundancyBreakdown).

    A projection whose normalised residual cannot be formed is left out of
    the sum and out of the redundancy of sigma nought, rather than
    withholding sigma nought (Geert's decision of 14 October 2026): a
    residual that
    Camera.error does not give (None, an
    error, or a value that is not finite, as for two 2D tie points of
    Geert's Project4, very likely those of the 3D tie point with
    Metashape's placeholder Reprojection Error), every projection of an
    image without a calibration, and a projection without a finite,
    positive keypoint size (a projection without the size attribute
    counts as one without a keypoint size).  A track left with fewer
    than two projections leaves the network with its other projections
    and its unknowns.  An enabled, oriented image without projections in
    the sum, because they were left out or because it had none in the
    adjustment, leaves the network with its rolling-shutter unknowns, a
    camera station none of whose images keeps projections in the sum
    leaves it with its exterior orientation unknowns, and an interior
    orientation group none of whose images keeps projections in the sum
    with its interior orientation unknowns.  The shared count leaves out
    the unknowns of the images without
    projections in the adjustment itself (ComputeRedundancyBreakdown with
    tiePointImageKeys), so unknownsLeavingWithImages holds only those of
    the images whose projections were all left out.  sigmaNoughtProjectionCounts,
    tiePointImageKeys, and
    unknownsLeavingWithImages carry this to ComputeSigmaNought, so that it
    needs no second pass over the projections, and the counts name them
    in the Console (DescribeSigmaNoughtExclusions).  An image without projections adds no
    residual and is counted in imagesWithoutTiePointsCount, and any other
    failure to read the projections of an image or to evaluate a residual
    is reported once in the Console.
    """
    tiePoints = getattr(chunk, "tie_points", None)
    if tiePoints is None or not tiePoints.points:
        return _EmptyReprojectionStatistics()

    pointByTrackId = {}
    for point in tiePoints.points:
        if point.valid:
            pointByTrackId[point.track_id] = point
    projectionCounts = CountProjectionsPerTrack(chunk)
    # The tie-point observations of the redundancy are two per projection
    # of the tie points in the adjustment, so sigma nought needs a
    # residual for each of these projections.
    populationCount = CountTiePointNetwork(chunk, projectionCounts)[0] // 2
    trackCount = len(projectionCounts)
    # Per track: the normalised squared residuals summed, the projections
    # in that sum, and the projections left out of sigma nought.
    trackSums = _ZeroArray("d", trackCount)
    trackNormalisedCounts = _ZeroArray("I", trackCount)
    leftOutCounts = _ZeroArray("I", trackCount)
    unevaluatedResidualCount = 0
    withoutKeypointSizeCount = 0

    overallSumSquared = 0.0
    overallProjectionCount = 0
    perImageRms = []
    # The tracks of the normalised 2D tie points, image by image, with
    # (camera, start, end) of every image with a 2D tie point of a track in
    # the adjustment (SplitImagesBySum), and the enabled, oriented images
    # without such a 2D tie point, which leave the network with their
    # unknowns as well.
    normalisedTrackIds = array.array("I")
    imageRanges = []
    imagesWithoutTiePoints = []

    for camera in chunk.cameras:
        if not IsOrientedEnabledCamera(camera):
            continue
        projections = _ImageProjections(tiePoints, camera)
        if projections is None:
            imagesWithoutTiePoints.append(camera)
            continue
        # An image without a calibration has no camera model: its
        # projections are left out of sigma nought.
        hasCalibration = GetImageCalibration(camera) is not None

        cameraSumSquared = 0.0
        cameraProjectionCount = 0
        imageTakesPart = False
        normalisedStart = len(normalisedTrackIds)
        for projection in projections:
            point = pointByTrackId.get(projection.track_id)
            if point is None:
                continue
            trackId = projection.track_id
            takesPart = TrackTakesPartInAdjustment(projectionCounts, trackId)
            imageTakesPart = imageTakesPart or takesPart
            if not hasCalibration:
                unevaluatedResidualCount += _CountLeftOut(leftOutCounts, takesPart, trackId)
                continue
            try:
                # Reprojection error in pixels from the camera model of
                # the image, rolling-shutter motion included
                # (TiePointResidual).  The homogeneous coordinates of the
                # 3D tie point also cover points at infinity (w = 0).
                errorVector = TiePointResidual(camera, point, projection)
            except Exception as suppressedError:
                ReportSuppressedError("gradualSelectionFilter.ComputeReprojectionStatistics", suppressedError)
                errorVector = None
            # Camera.error gives no residual for a 3D tie point that the
            # image cannot project (Metashape 2.3.2 in Geert's Project4).
            squared = None if errorVector is None else errorVector.norm() ** 2
            if squared is None or not math.isfinite(squared):
                unevaluatedResidualCount += _CountLeftOut(leftOutCounts, takesPart, trackId)
                continue
            cameraSumSquared += squared
            cameraProjectionCount += 1
            if not takesPart:
                continue  # seen on fewer than two images of the adjustment
            # Without a finite, positive keypoint size the residual
            # cannot be normalised: it is left out of sigma nought.  A
            # projection without the size attribute has no keypoint size
            # either (it is not taken as a size of 1).
            keypointSize = getattr(projection, "size", None)
            if not (keypointSize is not None and math.isfinite(keypointSize) and keypointSize > 0):
                withoutKeypointSizeCount += _CountLeftOut(leftOutCounts, takesPart, trackId)
                continue
            trackSums[trackId] += squared / (keypointSize * keypointSize)
            trackNormalisedCounts[trackId] += 1
            normalisedTrackIds.append(trackId)
        if imageTakesPart:
            imageRanges.append((camera, normalisedStart, len(normalisedTrackIds)))
        else:
            imagesWithoutTiePoints.append(camera)

        if cameraProjectionCount > 0:
            overallSumSquared += cameraSumSquared
            overallProjectionCount += cameraProjectionCount
            perImageRms.append(
                math.sqrt(cameraSumSquared / cameraProjectionCount))

    # The network of sigma nought: the counts without the projections
    # left out, and the tracks that still take part with their sums.
    sigmaNoughtProjectionCounts = array.array("I", projectionCounts)
    for trackId in range(trackCount):
        if leftOutCounts[trackId]:
            sigmaNoughtProjectionCounts[trackId] -= leftOutCounts[trackId]
    sumOfSquaredNormalisedResiduals = 0.0
    normalisedResidualCount = 0
    leftWithTheirTrackCount = 0
    for trackId in pointByTrackId:
        if not TrackTakesPartInAdjustment(projectionCounts, trackId):
            continue
        if TrackTakesPartInAdjustment(sigmaNoughtProjectionCounts, trackId):
            sumOfSquaredNormalisedResiduals += trackSums[trackId]
            normalisedResidualCount += trackNormalisedCounts[trackId]
        else:
            leftWithTheirTrackCount += sigmaNoughtProjectionCounts[trackId]
    keptImages, lostImages = SplitImagesBySum(imageRanges, normalisedTrackIds, sigmaNoughtProjectionCounts)
    statistics = _EmptyReprojectionStatistics(
        CountTiePointNetwork(chunk, sigmaNoughtProjectionCounts)[0] // 2, projectionCounts)
    # The shared count leaves out the unknowns of
    # the images without 2D tie points in the adjustment itself
    # (ComputeRedundancyBreakdown with tiePointImageKeys, the images of
    # imageRanges), so only the images whose 2D tie points were all left
    # out take unknowns with them here.
    statistics.update(
        imagesLeftCount=len(lostImages),
        imagesWithoutTiePointsCount=len(imagesWithoutTiePoints),
        tiePointImageKeys=ImageKeys(camera for camera, _, _ in imageRanges),
        unknownsLeavingWithImages=UnknownsLeavingWithImages(lostImages, keptImages) if lostImages else 0)
    statistics.update(
        sumOfSquaredNormalisedResiduals=sumOfSquaredNormalisedResiduals,
        normalisedResidualCount=normalisedResidualCount,
        sigmaNoughtProjectionCounts=sigmaNoughtProjectionCounts,
        populationCount=populationCount,
        unevaluatedResidualCount=unevaluatedResidualCount,
        withoutKeypointSizeCount=withoutKeypointSizeCount,
        leftWithTheirTrackCount=leftWithTheirTrackCount)
    if overallProjectionCount > 0:
        statistics.update(
            rms=math.sqrt(overallSumSquared / overallProjectionCount),
            maximumImageRms=max(perImageRms) if perImageRms else float("nan"),
            minimumImageRms=min(perImageRms) if perImageRms else float("nan"))
    return statistics


# Key of the chunk metadata (chunk.meta) under which Metashape stores the
# sigma nought of its last adjustment as a string, for example
# "0.000153596198603095".  Align Photos writes it as well.
METASHAPE_SIGMA_NOUGHT_METADATA_KEY = "OptimizeCameras/sigma0"


def ReadMetashapeSigmaNought(chunk):
    """The sigma nought that Metashape stored for the last adjustment of
    *chunk* (METASHAPE_SIGMA_NOUGHT_METADATA_KEY), or None when the chunk
    metadata hold no positive finite number under that key.  Metashape
    does not document which observations and redundancy it uses, so the
    value need not equal the approximation of ComputeSigmaNought (0.07 %
    lower in a chunk with tie points only).  The filter shows both
    (Geert, 13 October 2026)."""
    metadata = getattr(chunk, "meta", None)
    if metadata is None:
        return None
    try:
        value = metadata[METASHAPE_SIGMA_NOUGHT_METADATA_KEY]
    except KeyError:
        return None
    except Exception as suppressedError:
        ReportSuppressedError("gradualSelectionFilter.ReadMetashapeSigmaNought", suppressedError)
        return None
    if value is None:
        return None
    try:
        number = float(str(value).strip())
    except ValueError:
        return None
    return number if math.isfinite(number) and number > 0.0 else None


def _StoredSigmaNoughtText(metadata):
    """The value stored in *metadata* under
    METASHAPE_SIGMA_NOUGHT_METADATA_KEY, or None when there is none.
    Errors other than a missing key are raised."""
    try:
        return metadata[METASHAPE_SIGMA_NOUGHT_METADATA_KEY]
    except KeyError:
        return None


def ForgetMetashapeSigmaNought(chunk):
    """Remove the sigma nought that Metashape stored in the metadata of
    *chunk* before an Optimize Cameras of the filter, so that a value read
    after it was written by that adjustment, also one equal to the earlier
    value (Metashape 2.3.2 writes the value again at every Optimize
    Cameras that changes the adjustment).  Returns (storedText, isForgotten): the
    value stored before (None without one) and whether the metadata no
    longer hold it, False when it could not be removed."""
    metadata = getattr(chunk, "meta", None)
    if metadata is None:
        return None, False
    try:
        storedText = _StoredSigmaNoughtText(metadata)
        if storedText is not None:
            del metadata[METASHAPE_SIGMA_NOUGHT_METADATA_KEY]
    except Exception as suppressedError:
        ReportSuppressedError("gradualSelectionFilter.ForgetMetashapeSigmaNought", suppressedError)
        return None, False
    return storedText, True


def RestoreUnwrittenMetashapeSigmaNought(chunk, storedText, isForgotten):
    """After the Optimize Cameras that followed ForgetMetashapeSigmaNought:
    True when it wrote a sigma nought into the metadata of *chunk*, and
    otherwise False, with *storedText*, the value stored before, put back
    so that the metadata keep it.  None when that is not known, because
    the value could not be removed (*isForgotten* False) or the metadata
    cannot be read."""
    if not isForgotten:
        return None
    metadata = getattr(chunk, "meta", None)
    try:
        if _StoredSigmaNoughtText(metadata) is not None:
            return True
        if storedText is not None:
            metadata[METASHAPE_SIGMA_NOUGHT_METADATA_KEY] = storedText
    except Exception as suppressedError:
        ReportSuppressedError("gradualSelectionFilter.RestoreUnwrittenMetashapeSigmaNought", suppressedError)
        return None
    return False


def SigmaNoughtOfThisAdjustment(storedBefore, storedAfter, isWritten=None):
    """The sigma nought that Metashape stored at an Optimize Cameras of the
    filter (ReadMetashapeSigmaNought after it, *storedAfter*), or None
    when that adjustment wrote none.  *isWritten*
    (RestoreUnwrittenMetashapeSigmaNought) says whether it wrote one.
    When that is not known (None), a value equal to the one stored before
    (*storedBefore*) is not taken, because it may belong to an earlier
    adjustment."""
    if isWritten is not None:
        return storedAfter if isWritten else None
    if storedAfter is not None and storedAfter == storedBefore:
        return None
    return storedAfter


def UnshownSigmaNoughtNote(storedBefore, storedAfter, isWritten=None):
    """Why SigmaNoughtOfThisAdjustment gives no value for an Optimize
    Cameras of the filter, for the Console, or None when it gives one: "not
    written by its Optimize Cameras" when that adjustment wrote none
    (*isWritten* False, the earlier value is then in the metadata again),
    "not known, because it equals the value before its Optimize Cameras"
    when it is not known whether it wrote one, METADATA_UNREADABLE_TEXT for
    a written value that is no positive finite number, and otherwise
    METADATA_MISSING_TEXT."""
    if SigmaNoughtOfThisAdjustment(storedBefore, storedAfter, isWritten) is not None:
        return None
    if isWritten is False:
        return "not written by its Optimize Cameras"
    if isWritten is None and storedAfter is not None:
        return "not known, because it equals the value before its Optimize Cameras"
    if isWritten is True:
        return METADATA_UNREADABLE_TEXT
    return METADATA_MISSING_TEXT


# Keys of the chunk metadata under which Metashape stores settings of the
# last Optimize Cameras of a chunk: the fitted parameters as text, for
# example "f cx cy k1 k2 k3 p1 p2", and whether the camera model was fitted
# adaptively.
METASHAPE_FIT_FLAGS_METADATA_KEY = "OptimizeCameras/fit_flags"
METASHAPE_ADAPTIVE_FITTING_METADATA_KEY = "OptimizeCameras/adaptive_fitting"

# The arguments of Chunk.optimizeCameras that fit an interior orientation
# parameter, with the name of that parameter, in the order of the camera
# calibration of Metashape.
FIT_PARAMETER_ARGUMENTS = (("fit_f", "f"), ("fit_cx", "cx"), ("fit_cy", "cy"), ("fit_b1", "b1"), ("fit_b2", "b2"),
                           ("fit_k1", "k1"), ("fit_k2", "k2"), ("fit_k3", "k3"), ("fit_k4", "k4"),
                           ("fit_p1", "p1"), ("fit_p2", "p2"))

# The comparison of the adjustment of the chunk as found with that of
# iteration 0 flags a difference when their sigma nought of Metashape
# differ by more than this factor (the larger divided by the smaller), or
# when their fitted parameters or their adaptive camera model fitting
# differ.  Without such a difference, their results can still differ
# (AdjustmentComparisonLines).
ADJUSTMENT_SIGMA_NOUGHT_FACTOR = 2.0
# The sentence of the Console after a comparison that flags no difference.
SAME_RESULTS_NOT_SHOWN_SENTENCE = "This does not show that their results are the same."

# What the Console says of a setting that the chunk metadata do not give.
METADATA_MISSING_TEXT = "not in the metadata"
METADATA_UNREADABLE_TEXT = "not readable in the metadata"


def ParseFitFlags(value):
    """The names of the fitted parameters in a value of
    METASHAPE_FIT_FLAGS_METADATA_KEY, in lower case and in their order,
    for example ("f", "cx", "cy", "k1"): from text with the names separated
    by spaces or commas, or from a list or tuple of names.  None for any
    other value, None included."""
    if isinstance(value, str):
        return tuple(name.lower() for name in value.replace(",", " ").split())
    if isinstance(value, (list, tuple)) and all(isinstance(name, str) for name in value):
        return tuple(name.strip().lower() for name in value if name.strip())
    return None


def ParseMetadataBoolean(value):
    """True or False for a value of the chunk metadata that holds a switch:
    a bool, an integer (0 is False), or text such as "true", "false", "1",
    or "0" in any case.  None for any other value, None included."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    if isinstance(value, str):
        text = value.strip().lower()
        if text in ("true", "1", "yes", "on"):
            return True
        if text in ("false", "0", "no", "off"):
            return False
    return None


def MetadataNote(parsedValue, storedValue):
    """Why a setting of ReadOptimisationSettings has no value: None when it
    has one (*parsedValue*), METADATA_MISSING_TEXT when the metadata hold
    none (*storedValue* None), and METADATA_UNREADABLE_TEXT when they hold
    a value that cannot be read."""
    if parsedValue is not None:
        return None
    return METADATA_MISSING_TEXT if storedValue is None else METADATA_UNREADABLE_TEXT


def ReadOptimisationSettings(chunk):
    """The settings of the last Optimize Cameras of *chunk*, from its
    metadata (METASHAPE_FIT_FLAGS_METADATA_KEY and
    METASHAPE_ADAPTIVE_FITTING_METADATA_KEY): a dict with fitFlags (the
    names of ParseFitFlags, or None), adaptiveFitting (True, False, or
    None), and fitFlagsNote and adaptiveFittingNote, which say why a value
    is None (MetadataNote).  A key that is missing or cannot be read
    (MetadataValue) and a value of another type give None."""
    metadata = getattr(chunk, "meta", None)
    fitFlagsValue = MetadataValue(metadata, METASHAPE_FIT_FLAGS_METADATA_KEY)
    adaptiveFittingValue = MetadataValue(metadata, METASHAPE_ADAPTIVE_FITTING_METADATA_KEY)
    fitFlags = ParseFitFlags(fitFlagsValue)
    adaptiveFitting = ParseMetadataBoolean(adaptiveFittingValue)
    return {"fitFlags": fitFlags, "fitFlagsNote": MetadataNote(fitFlags, fitFlagsValue),
            "adaptiveFitting": adaptiveFitting,
            "adaptiveFittingNote": MetadataNote(adaptiveFitting, adaptiveFittingValue)}


def FilterOptimisationSettings(optimiseArguments):
    """The settings of the Optimize Cameras of the filter, from the
    arguments of Chunk.optimizeCameras (_BuildOptimiseArguments), in the
    form of ReadOptimisationSettings."""
    return {"fitFlags": tuple(name for argumentName, name in FIT_PARAMETER_ARGUMENTS
                              if optimiseArguments.get(argumentName)),
            "fitFlagsNote": None,
            "adaptiveFitting": bool(optimiseArguments.get("adaptive_fitting")),
            "adaptiveFittingNote": None}


def DescribeOptimisationSettings(settings):
    """"fitted parameters f cx cy k1 k2 k3 p1 p2, adaptive camera model
    fitting off" for the settings of ReadOptimisationSettings, with the
    note of a value that the metadata do not give, for example "fitted
    parameters not in the metadata"."""
    settings = settings or {}
    fitFlags = settings.get("fitFlags")
    if fitFlags is None:
        fitPart = "fitted parameters " + (settings.get("fitFlagsNote") or METADATA_MISSING_TEXT)
    else:
        fitPart = "fitted parameters " + " ".join(fitFlags) if fitFlags else "no fitted parameters"
    adaptiveFitting = settings.get("adaptiveFitting")
    if adaptiveFitting is None:
        adaptivePart = settings.get("adaptiveFittingNote") or METADATA_MISSING_TEXT
    else:
        adaptivePart = "on" if adaptiveFitting else "off"
    return "{}, adaptive camera model fitting {}".format(fitPart, adaptivePart)


def MetashapeSigmaNoughtText(value, missingText=None):
    """"sigma nought of Metashape 0.1312", or without a value "sigma nought
    of Metashape" with *missingText* (UnshownSigmaNoughtNote), by default
    "not in the metadata"."""
    if value is None:
        return "sigma nought of Metashape " + (missingText or METADATA_MISSING_TEXT)
    return "sigma nought of Metashape {:.4f}".format(value)


def AreSigmaNoughtValuesApart(firstValue, secondValue):
    """True when two values of the sigma nought of Metashape, both positive
    and finite, differ by more than ADJUSTMENT_SIGMA_NOUGHT_FACTOR (the
    larger divided by the smaller), and False otherwise, also without both
    values."""
    if not all(isinstance(value, (int, float)) and math.isfinite(value) and value > 0.0
               for value in (firstValue, secondValue)):
        return False
    return max(firstValue, secondValue) / min(firstValue, secondValue) > ADJUSTMENT_SIGMA_NOUGHT_FACTOR


def AdjustmentDifferences(asFoundSigmaNought, iterationZeroSigmaNought, asFoundSettings, filterSettings):
    """The differences that the comparison of the adjustment of the chunk
    as found with that of iteration 0 flags, as sentences without a final
    full stop: sigma nought of Metashape more than
    ADJUSTMENT_SIGMA_NOUGHT_FACTOR apart (AreSigmaNoughtValuesApart), other
    fitted parameters (compared as a set, whatever their order), or
    another adaptive camera model fitting.  An empty list when it flags
    none of these, also when a value is missing, so that it cannot show a
    difference.  An empty list does not show that the two adjustments
    give the same results: anything else that the adjustment uses may
    differ from what the last Optimize Cameras of the chunk as found used,
    or that adjustment may not have converged."""
    differences = []
    if AreSigmaNoughtValuesApart(asFoundSigmaNought, iterationZeroSigmaNought):
        differences.append(
            "The sigma nought of Metashape is {:.4f} for the chunk as found and {:.4f} for iteration 0, more than "
            "a factor of {:g} apart".format(asFoundSigmaNought, iterationZeroSigmaNought, ADJUSTMENT_SIGMA_NOUGHT_FACTOR))
    asFoundFitFlags = (asFoundSettings or {}).get("fitFlags")
    filterFitFlags = (filterSettings or {}).get("fitFlags")
    if asFoundFitFlags is not None and filterFitFlags is not None and set(asFoundFitFlags) != set(filterFitFlags):
        differences.append("The last Optimize Cameras of the chunk as found fitted {}, and the filter fits {}".format(
            " ".join(asFoundFitFlags) or "no parameter", " ".join(filterFitFlags) or "no parameter"))
    asFoundAdaptiveFitting = (asFoundSettings or {}).get("adaptiveFitting")
    filterAdaptiveFitting = (filterSettings or {}).get("adaptiveFitting")
    if (asFoundAdaptiveFitting is not None and filterAdaptiveFitting is not None
            and asFoundAdaptiveFitting != filterAdaptiveFitting):
        differences.append("The last Optimize Cameras of the chunk as found {} adaptive camera model fitting, and "
                           "the filter {}".format("used" if asFoundAdaptiveFitting else "did not use",
                                                  "uses it" if filterAdaptiveFitting else "does not"))
    return differences


def AreAdjustmentSettingsKnown(asFoundSigmaNought, iterationZeroSigmaNought, asFoundSettings):
    """True when the values that AdjustmentDifferences compares are all
    known: both values of the sigma nought of Metashape and the fitted
    parameters and the adaptive camera model fitting of the chunk as
    found."""
    asFoundSettings = asFoundSettings or {}
    return (asFoundSigmaNought is not None and iterationZeroSigmaNought is not None
            and asFoundSettings.get("fitFlags") is not None and asFoundSettings.get("adaptiveFitting") is not None)


def AdjustmentComparisonLines(asFoundRow, iterationZeroRow):
    """The Console lines, after the metrics of iteration 0, that compare its
    adjustment with that of the chunk as found (the rows of SummaryRow):
    the sigma nought of Metashape of both, the settings of the last
    Optimize Cameras of the chunk as found against those of the filter,
    and the differences that the comparison flags (AdjustmentDifferences).
    When it flags none, the last line says so and that this does not show
    that their results are the same, also when not every value is known.
    Without a sigma nought of iteration 0, its line says why
    (UnshownSigmaNoughtNote, the "metashapeSigma0Note" of its row)."""
    asFoundSigmaNought = asFoundRow.get("metashapeSigma0")
    iterationZeroSigmaNought = iterationZeroRow.get("metashapeSigma0")
    asFoundSettings = asFoundRow.get("optimisationSettings")
    lines = ["Adjustment of the chunk as found (its last Optimize Cameras, from the chunk metadata): {}, {}.".format(
                 MetashapeSigmaNoughtText(asFoundSigmaNought), DescribeOptimisationSettings(asFoundSettings)),
             "Adjustment of iteration 0 (the settings of the filter): {}, {}.".format(
                 MetashapeSigmaNoughtText(iterationZeroSigmaNought, iterationZeroRow.get("metashapeSigma0Note")),
                 DescribeOptimisationSettings(iterationZeroRow.get("optimisationSettings")))]
    differences = AdjustmentDifferences(asFoundSigmaNought, iterationZeroSigmaNought, asFoundSettings,
                                        iterationZeroRow.get("optimisationSettings"))
    if differences:
        lines.append("The two adjustments differ. {}.".format(". ".join(differences)))
    elif AreAdjustmentSettingsKnown(asFoundSigmaNought, iterationZeroSigmaNought, asFoundSettings):
        lines.append("The comparison of the two adjustments flags no difference: their values of the sigma nought "
                     "of Metashape are within a factor of {:g}, and their fitted parameters and adaptive camera model "
                     "fitting agree. {}".format(ADJUSTMENT_SIGMA_NOUGHT_FACTOR, SAME_RESULTS_NOT_SHOWN_SENTENCE))
    else:
        lines.append("The comparison of the two adjustments flags no difference, but not every value that it "
                     "compares is known. {}".format(SAME_RESULTS_NOT_SHOWN_SENTENCE))
    return lines


def DescribeIncompleteSigmaNought(reprojectionStatistics):
    """Why ComputeSigmaNought gives no value although residuals were
    summed, for the Console: the sum does not hold a residual for every
    projection that the redundancy counts.  The
    projections whose normalised residuals cannot be formed are left out
    of both (DescribeSigmaNoughtExclusions), so this happens only when
    the counts disagree.  None when the sum is complete or when the
    statistics do not state its population."""
    expectedCount = reprojectionStatistics.get("sigmaNoughtProjectionCount")
    evaluatedCount = reprojectionStatistics.get("normalisedResidualCount", 0)
    if expectedCount is None or evaluatedCount == expectedCount:
        return None
    # No count stands right before "2D", where "10 2D" would read as one
    # number.
    if evaluatedCount > expectedCount:
        return ("the residuals of more 2D tie points were summed ({:,}) than "
                "the redundancy counts ({:,})".format(evaluatedCount, expectedCount))
    return ("the residuals of {:,} of the 2D tie points in the adjustment, "
            "{:,} in all, could not be evaluated".format(
                expectedCount - evaluatedCount, expectedCount))


def SigmaNoughtExclusionCounts(reprojectionStatistics):
    """The 2D tie points that sigma nought leaves out, for example "2 of
    456,925, whose residuals could not be
    evaluated", or None when it leaves out none.  The count is that of
    the projections of the tie-point network before any was left out
    (populationCount of ComputeReprojectionStatistics).  No count stands
    right before "2D", where "10 2D" would read as one number, so the
    tie points of a track that kept fewer than two 2D tie points are
    those "left with fewer than two of them"."""
    reasons = [(reprojectionStatistics.get("unevaluatedResidualCount", 0), "whose residuals could not be evaluated"),
               (reprojectionStatistics.get("withoutKeypointSizeCount", 0), "without a keypoint size"),
               (reprojectionStatistics.get("leftWithTheirTrackCount", 0),
                "whose 3D tie point was left with fewer than two of them")]
    reasons = [(count, reason) for count, reason in reasons if count]
    if not reasons:
        return None
    leftOutCount = sum(count for count, _ in reasons)
    populationCount = reprojectionStatistics.get("populationCount", 0)
    if len(reasons) == 1:
        reasonText = reasons[0][1]
    else:
        parts = ["{:,} {}".format(count, reason) for count, reason in reasons]
        reasonText = (" and ".join(parts) if len(parts) == 2
                      else ", ".join(parts[:-1]) + ", and " + parts[-1])
    return "{:,} of {:,}, {}".format(leftOutCount, populationCount, reasonText)


def ImagesLeavingText(reprojectionStatistics):
    """", so that 1 image leaves the network with its unknowns" for the
    images left without 2D tie points in the sum of sigma nought, or an
    empty string without them."""
    imagesLeftCount = reprojectionStatistics.get("imagesLeftCount", 0)
    if not imagesLeftCount:
        return ""
    if imagesLeftCount == 1:
        return ", so that 1 image leaves the network with its unknowns"
    return ", so that {:,} images leave the network with their unknowns".format(imagesLeftCount)


def ImagesWithoutTiePointsText(reprojectionStatistics):
    """"Enabled, oriented images without 2D tie points in the adjustment,
    which leave the network with their unknowns: 3", or None without
    them."""
    imageCount = reprojectionStatistics.get("imagesWithoutTiePointsCount", 0)
    if not imageCount:
        return None
    return ("Enabled, oriented images without 2D tie points in the adjustment, which leave the network with their "
            "unknowns: {:,}".format(imageCount))


def DescribeSigmaNoughtExclusions(reprojectionStatistics):
    """What sigma nought leaves out, for the Console and the tooltip of
    the table: the 2D tie points left out, for
    example "2D tie points left out: 2 of 456,925, whose residuals could
    not be evaluated", with the images that leave the network with them
    (ImagesLeavingText), and the images without 2D tie points in the
    adjustment (ImagesWithoutTiePointsText), as sentences, or None when it
    leaves out neither."""
    parts = []
    counts = SigmaNoughtExclusionCounts(reprojectionStatistics)
    if counts is not None:
        parts.append("2D tie points left out: " + counts + ImagesLeavingText(reprojectionStatistics))
    imagesWithoutTiePoints = ImagesWithoutTiePointsText(reprojectionStatistics)
    if imagesWithoutTiePoints is not None:
        parts.append(imagesWithoutTiePoints)
    return ". ".join(parts) if parts else None


def DescribeMissingSigmaNought(reprojectionStatistics, redundancy):
    """Why sigma nought has no value, for the Console and the tooltip of
    the table, or None when it has one.  *redundancy* is that of
    ComputeSigmaNoughtAndRedundancy, None when it was not counted.  The
    reasons, in this order: no 3D tie point in the adjustment, every 2D
    tie point left out, counts of the sum and of the redundancy that
    disagree (DescribeIncompleteSigmaNought), and a redundancy that is not
    positive.  The 2D tie points left out are named in parentheses when
    the reason does not name them already."""
    normalisedResidualCount = reprojectionStatistics.get("normalisedResidualCount", 0)
    exclusions = DescribeSigmaNoughtExclusions(reprojectionStatistics)
    if normalisedResidualCount <= 0:
        counts = SigmaNoughtExclusionCounts(reprojectionStatistics)
        if counts is None:
            return "no 3D tie point is seen on two or more enabled, oriented images"
        return "every 2D tie point of the adjustment is left out: " + counts
    reason = DescribeIncompleteSigmaNought(reprojectionStatistics)
    if reason is None and redundancy is not None and redundancy <= 0:
        reason = "the estimated redundancy, {:,}, is not positive".format(redundancy)
    if reason is None:
        return None
    return reason + (" ({})".format(exclusions) if exclusions else "")


def ComputeSigmaNought(chunk, reprojectionStatistics, tiePointAccuracy):
    """Approximate sigma nought of the tie-point network, or None (see
    ComputeSigmaNoughtAndRedundancy)."""
    return ComputeSigmaNoughtAndRedundancy(chunk, reprojectionStatistics, tiePointAccuracy)[0]


def ComputeSigmaNoughtAndRedundancy(chunk, reprojectionStatistics, tiePointAccuracy):
    """Approximate sigma nought (the reference standard deviation, the
    square root of the reference variance) of the tie-point network, with
    the redundancy that it divides by.

    sigma0 = sqrt( sum( (residual / (accuracy * keypoint size))^2 )
                   / redundancy )

    The sum runs over the projections, on enabled, oriented images, of
    the valid tie points whose tracks take part in the adjustment
    (seen on at least two such images, TrackTakesPartInAdjustment), as
    summed by ComputeReprojectionStatistics.  The redundancy is the
    tie-point redundancy of the shared counting machinery,
    ComputeRedundancyBreakdown(chunk, includeControl=False), counted
    from the same projections: the scalar tie-point observations (two per
    projection) minus the exterior orientation, interior orientation,
    tie-point, and additional model unknowns of the camera stations, groups,
    and images with 2D tie points in the adjustment, plus the datum defect
    of a free network (7).  When no projection is left out, it is the
    redundancy that the Interior Orientation Viewer reports as "Estimated
    DoF (tie points)".

    Control, reference-distance, and prior observations are ignored, so
    this is an approximation of the network's sigma nought, not the
    value that Metashape stores in the chunk metadata after Optimize
    Cameras (OptimizeCameras/sigma0, ReadMetashapeSigmaNought), for
    which Metashape does not document the observations and the
    redundancy that it uses.  With control or
    priors in the adjustment, the residuals come from that constrained
    adjustment while the redundancy is that of the tie-point network
    alone, so the value is a descriptive indicator rather than an
    unbiased estimate of the variance factor.  The redundancy itself is
    an estimate from counts (see ComputeRedundancyBreakdown).

    The projections whose normalised residuals cannot be formed are left
    out of the sum and out of the redundancy (Geert's decision of 14
    October 2026): the redundancy is counted from
    sigmaNoughtProjectionCounts of the statistics, the projection counts
    without them, in which a track left with fewer than two projections
    no longer takes part (its unknowns go with it), an enabled, oriented
    image without projections in the sum takes its rolling-shutter
    unknowns with it, a camera station none of whose images keeps
    projections in the sum its exterior orientation unknowns, and a group
    none of whose images keeps projections in the sum its interior
    orientation unknowns (unknownsLeavingWithImages of the statistics).
    A partial sum divided by the full redundancy would understate sigma
    nought.  The sum must still hold a residual for every projection that this
    redundancy counts (sigmaNoughtProjectionCount of the statistics):
    otherwise it gives None, and DescribeMissingSigmaNought says why.
    Returns (sigma0, redundancy): sigma0 is None when there are no
    projections, the counts disagree, or the redundancy is not positive,
    and the redundancy is None when it was not counted.
    """
    normalisedResidualCount = reprojectionStatistics.get("normalisedResidualCount", 0)
    if normalisedResidualCount <= 0:
        return None, None
    if normalisedResidualCount != reprojectionStatistics.get(
            "sigmaNoughtProjectionCount", normalisedResidualCount):
        return None, None
    accuracy = tiePointAccuracy if tiePointAccuracy and \
        tiePointAccuracy > 0 else 1.0
    weightedSumSquared = (
        reprojectionStatistics.get("sumOfSquaredNormalisedResiduals", 0.0)
        / (accuracy * accuracy))

    projectionCounts = reprojectionStatistics.get("sigmaNoughtProjectionCounts")
    if projectionCounts is None:
        projectionCounts = reprojectionStatistics.get("projectionCounts")
    # The images with 2D tie points in the adjustment, found with the
    # counts before any projection was left out (ComputeReprojectionStatistics
    # gives them with sigmaNoughtProjectionCounts): the reduced counts would
    # take an image whose own projections were all left out for one with 2D
    # tie points.
    redundancy = ComputeRedundancyBreakdown(
        chunk, includeControl=False, projectionCounts=projectionCounts,
        tiePointImageKeys=reprojectionStatistics.get("tiePointImageKeys"))["redundancy"]
    redundancy += reprojectionStatistics.get("unknownsLeavingWithImages", 0)
    if redundancy <= 0:
        return None, redundancy
    return math.sqrt(weightedSumSquared / redundancy), redundancy


def AccuracyComponentsOf(accuracyVector):
    """The three components of an accuracy vector (a value of Metashape's
    Accuracy field) as floats, or None when the vector is missing or cannot
    be read (the rule of the Marker Residual Inspector)."""
    if accuracyVector is None:
        return None
    try:
        return tuple(float(accuracyVector[index]) for index in range(3))
    except Exception as suppressedError:
        ReportSuppressedError("gradualSelectionFilter.AccuracyComponentsOf", suppressedError)
        return None


def MarkerAPrioriRootSumOfSquares(chunk, marker):
    """The root sum of squares, sqrt(sigma_X^2 + sigma_Y^2 + sigma_Z^2),
    of the a priori standard deviations of the reference coordinates of
    *marker*, which Metashape's Accuracy fields hold: those of the marker,
    or else the default of the chunk (marker_location_accuracy), as in the
    Marker Residual Inspector.  None when neither can be read."""
    try:
        markerAccuracy = getattr(getattr(marker, "reference", None), "accuracy", None)
    except Exception as suppressedError:
        ReportSuppressedError("gradualSelectionFilter.MarkerAPrioriRootSumOfSquares", suppressedError)
        markerAccuracy = None
    components = AccuracyComponentsOf(markerAccuracy)
    if components is None:
        components = AccuracyComponentsOf(getattr(chunk, "marker_location_accuracy", None))
    if components is None:
        return None
    return math.sqrt(sum(component * component for component in components))


def _ComputeMarkerReferenceErrors(chunk, useControlPoints, contextLabel):
    """RMSE of the measured markers that have a reference location:
    the control points (reference ticked) when *useControlPoints* is
    True, otherwise the check points (reference unticked).  A marker
    counts as measured when Metashape estimated its position from its
    projections.

    Returns (rmseMetres, count, pointErrors) or (None, 0, {}).
    pointErrors holds, by the index of the marker in chunk.markers, (the 3D
    error in metres, the root sum of squares of the a priori standard
    deviations of the reference coordinates, MarkerAPrioriRootSumOfSquares,
    or None) of every marker in the RMSE.  The index pairs a marker of one
    state of the run with the same marker of another state, because the
    copies of the iterations keep the order of the markers, as the
    positions of the markers do (ImageAndMarkerPositions), while a copy need
    not keep Marker.key.  The error is
    evaluated in local east-north-up coordinates so that it is in
    metres for both projected and geographic CRSs.  The reference
    coordinates are read in the marker CRS (chunk.marker_crs when it
    is set, otherwise the chunk CRS) and brought into the geocentric
    coordinates of the chunk CRS by a coordinate transformation
    (MarkerReferenceDiscrepancy).  A marker whose CRS cannot be
    related to the chunk CRS is reported and left out.
    """
    crs = chunk.crs
    transformMatrix = chunk.transform.matrix
    if crs is None or transformMatrix is None:
        return None, 0, {}

    sumSquared = 0.0
    count = 0
    pointErrors = {}
    for markerIndex, marker in enumerate(chunk.markers):
        # An inspection marker, which the GSD Value Exporter recorded as its
        # own in its metadata, has model-derived reference coordinates, so
        # it is neither a control nor a check point.
        if IsInspectionMarker(marker):
            continue
        referenceLocation = marker.reference.location
        if referenceLocation is None:
            continue
        if bool(marker.reference.enabled) != useControlPoints:
            continue
        if marker.position is None:
            continue
        try:
            estimatedGeocentric = transformMatrix.mulp(marker.position)
            errorVector = MarkerReferenceDiscrepancy(chunk, referenceLocation, estimatedGeocentric)
            errorLength = errorVector.norm()
            sumSquared += errorLength ** 2
            count += 1
        except Exception as suppressedError:
            ReportSuppressedError(contextLabel, suppressedError)
            continue
        pointErrors[markerIndex] = (errorLength, MarkerAPrioriRootSumOfSquares(chunk, marker))

    if count == 0:
        return None, 0, {}
    return math.sqrt(sumSquared / count), count, pointErrors


def ComputeControlPointErrors(chunk):
    """(rmseMetres, count, pointErrors) of the control points, or (None, 0,
    {}) (_ComputeMarkerReferenceErrors and ComputeControlPointRmse)."""
    return _ComputeMarkerReferenceErrors(
        chunk, True, "gradualSelectionFilter.ComputeControlPointRmse")


def ComputeCheckPointErrors(chunk):
    """(rmseMetres, count, pointErrors) of the check points, or (None, 0, {})
    (_ComputeMarkerReferenceErrors and ComputeCheckPointRmse)."""
    return _ComputeMarkerReferenceErrors(
        chunk, False, "gradualSelectionFilter.ComputeCheckPointRmse")


def ComputeControlPointRmse(chunk):
    """Compute the RMSE of the control points (measured markers with a
    ticked reference).  The control points are observations of the
    adjustment, so this is a fit statistic, not an independent check.

    Returns (rmseMetres, count) or (None, 0).
    """
    return ComputeControlPointErrors(chunk)[:2]


def ComputeCheckPointRmse(chunk):
    """Compute the RMSE of the check points (measured markers with a
    reference location and an unticked reference). The reference coordinates of the check points take no part in the adjustment, so this is an independent check of
    the accuracy of one iteration, but not of an iteration chosen by it
    (see RecommendIteration).

    Returns (rmseMetres, count) or (None, 0).
    """
    return ComputeCheckPointErrors(chunk)[:2]


def APrioriRmse(pointErrors):
    """(RMSE, count) that the a priori standard deviations of the reference
    coordinates alone give for the points of *pointErrors*
    (_ComputeMarkerReferenceErrors): the square root of the mean, over the
    points with a root sum of squares of their a priori standard
    deviations, of sigma_X^2 + sigma_Y^2 + sigma_Z^2.  For check points,
    it is the RMSE to expect if they were as accurate as their a priori
    standard deviations say and the 3D model had no error.  It does not
    depend on the iteration.  (None, 0) without such a point."""
    rootSumsOfSquares = [pointError[1] for pointError in (pointErrors or {}).values()
                         if isinstance(pointError[1], (int, float)) and math.isfinite(pointError[1])]
    if not rootSumsOfSquares:
        return None, 0
    return (math.sqrt(sum(value * value for value in rootSumsOfSquares) / len(rootSumsOfSquares)),
            len(rootSumsOfSquares))


def APrioriCheckPointRmseText(summaryRows):
    """"The a priori standard deviations of the 11 check points alone give
    an RMSE of 0.008660 m." (APrioriRmse) for the check points of the first
    row of *summaryRows* that has check points, the chunk as found in a
    run, with the significant digits of the RMSE values, or None without
    a priori standard deviations of check points."""
    pointErrors = next((row.get("checkPointErrors") for row in summaryRows if row.get("checkPointErrors")), None)
    rmse, count = APrioriRmse(pointErrors)
    if rmse is None:
        return None
    return "The a priori standard deviations of the {} alone give an RMSE of {} m.".format(
        FormatCountWithNoun(count, "check point", "check points"), FormatSignificantDigits(rmse))


def CountValidPoints(chunk):
    """Count valid tie points."""
    tiePoints = getattr(chunk, "tie_points", None)
    if tiePoints is None or not tiePoints.points:
        return 0
    return sum(1 for point in tiePoints.points if point.valid)


def ComputeCriterionCensus(chunk):
    """{criterion name: (worst value, number of valid tie points with that
    value, number of valid tie points with Metashape's placeholder)} of
    the current cloud, the worst value NaN when no tie point has one
    (CriterionValueCensus)."""
    census = {}
    validity = ValidityOfTiePoints(chunk)
    for name, criterionEnum in FILTER_CRITERIA.items():
        values, placeholderCount = CriterionValueCensus(chunk, criterionEnum, validity)
        worst = values[-1] if values else float("nan")
        worstCount = sum(1 for value in values if value == worst) if values else 0
        census[name] = (worst, worstCount, placeholderCount)
    return census


def ComputeCriterionMaxima(chunk):
    """Return the worst (maximum) value of each gradual-selection
    criterion for the current cloud, as a dict keyed by criterion name,
    without Metashape's placeholder.
    """
    return {name: entry[0] for name, entry in ComputeCriterionCensus(chunk).items()}


# The largest Reconstruction Uncertainty that Metashape appears to give:
# in two chunks, 4 and 70 valid tie points had exactly 1000 and none had
# more.
RECONSTRUCTION_UNCERTAINTY_CAP = 1000.0
CAPPED_RECONSTRUCTION_UNCERTAINTY_TEXT = ("Metashape appears to cap the Reconstruction Uncertainty at 1000, so {} "
                                          "can be larger.")


def CriterionCensusLines(metrics):
    """Console lines on the worst values of *metrics* (CollectMetrics):
    the tie points whose value is Metashape's placeholder, which the worst
    value leaves out, and the number of tie points that share a worst
    value, when more than one does, for example a Reconstruction
    Uncertainty of 1000 in two projects.  A worst Reconstruction Uncertainty
    of exactly RECONSTRUCTION_UNCERTAINTY_CAP adds that Metashape appears
    to cap it."""
    lines = []
    placeholderCounts = metrics.get("criterionPlaceholderCounts") or {}
    worstCounts = metrics.get("criterionWorstCounts") or {}
    worstValues = metrics.get("criterionWorstValues") or {}
    for name in FILTER_CRITERIA:
        placeholderCount = placeholderCounts.get(name, 0)
        if placeholderCount:
            lines.append("{} with Metashape's placeholder value {} as {} {} left out of the worst value.".format(
                FormatCountWithNoun(placeholderCount, "3D tie point", "3D tie points"), METASHAPE_PLACEHOLDER_TEXT,
                "its" if placeholderCount == 1 else "their", name))
        worstCount = worstCounts.get(name, 0)
        worstValue = worstValues.get(name)
        isCapped = name == "Reconstruction Uncertainty" and worstValue == RECONSTRUCTION_UNCERTAINTY_CAP
        if worstCount > 1 and worstValue is not None and math.isfinite(worstValue):
            line = "The worst {}, {:g}, is shared by {}.".format(
                name, worstValue, FormatCountWithNoun(worstCount, "3D tie point", "3D tie points"))
            if isCapped:
                line += " " + CAPPED_RECONSTRUCTION_UNCERTAINTY_TEXT.format("their values")
            lines.append(line)
        elif isCapped:
            lines.append("The worst {} is {:g}. {}".format(
                name, worstValue, CAPPED_RECONSTRUCTION_UNCERTAINTY_TEXT.format("its value")))
    return lines




# The note of a chunk whose 3D tie points all have a covariance matrix of
# zeros, and of one without any.
ZERO_COVARIANCE_NOTE = ("every 3D tie point has a covariance matrix of zeros, as after Align Photos or after an "
                        "Optimize Cameras without Estimate tie point covariance")
NO_COVARIANCE_NOTE = "no 3D tie point has a covariance matrix"


def TiePointStandardDeviationNote(zeroCount, invalidCount, missingCount):
    """Why no tie-point sigma can be given, from the numbers of valid 3D
    tie points with a covariance matrix of zeros, with one that is not a
    covariance, and without one."""
    if zeroCount and not invalidCount and not missingCount:
        return ZERO_COVARIANCE_NOTE
    if not zeroCount and not invalidCount:
        return NO_COVARIANCE_NOTE
    return ("no 3D tie point has a covariance matrix that can give one: {:,} with a matrix of zeros, {:,} with one that "
            "is not a covariance, and {:,} without one".format(zeroCount, invalidCount, missingCount))


def ComputeTiePointStandardDeviationStatistics(chunk):
    """Mean and median of the 3D positional standard deviation of the
    valid tie points, with the counts behind them.

    Uses each point's covariance matrix (available only after an
    Optimize Cameras run with tie-point covariance estimation).  The
    per-point value is sqrt(trace(C)), the total positional standard
    deviation, that is, the root mean square length of the centred
    error vector, not the standard deviation of that length, multiplied
    by the chunk scale (GetChunkScale), so it is in metres when the
    chunk is scaled and in chunk units otherwise (LengthUnitLabel).

    A covariance matrix of zeros takes no part and is counted
    (zeroCount): Metashape stores one for every 3D tie point when the
    last Optimize Cameras did not estimate the tie point covariance
    (two chunks of 349,311 and 1,849,088 tie points).  A
    trace that rounding makes slightly negative counts as zero.  A matrix
    that is not a covariance (not finite, symmetric, and positive
    semi-definite, ValidatedCovarianceElements) is left out and counted
    (invalidCount), because a positive trace can hide a negative
    variance: diag(-1, 4, 9) has the trace 12.  The median is given
    beside the mean, because a few poorly determined points can govern
    the mean: in one of Geert's chunks the largest 1 % of the points made
    up 46 % of the sum (mean 0.060 m, median 0.0034 m).

    Returns a dict with mean, median (both None without a value), count,
    zeroCount, invalidCount, missingCount, and note (None, or why there is
    no value, TiePointStandardDeviationNote).
    """
    result = dict(mean=None, median=None, count=0, zeroCount=0, invalidCount=0, missingCount=0,
                  note=NO_COVARIANCE_NOTE)
    tiePoints = getattr(chunk, "tie_points", None)
    if tiePoints is None or not tiePoints.points:
        return result
    scale = GetChunkScale(chunk) or 1.0
    standardDeviations = []
    for point in tiePoints.points:
        if not point.valid:
            continue
        covariance = getattr(point, "cov", None)
        if covariance is None:
            result["missingCount"] += 1
            continue
        try:
            covarianceElements = ValidatedCovarianceElements(covariance)
        except ValueError:
            result["invalidCount"] += 1
            continue
        except Exception as suppressedError:
            ReportSuppressedError("gradualSelectionFilter.ComputeTiePointStandardDeviationStatistics",
                                  suppressedError)
            continue
        if all(element == 0.0 for rowElements in covarianceElements for element in rowElements):
            result["zeroCount"] += 1
            continue
        traceValue = covarianceElements[0][0] + covarianceElements[1][1] + covarianceElements[2][2]
        standardDeviations.append(math.sqrt(max(traceValue, 0.0)) * scale)
    result["count"] = len(standardDeviations)
    if standardDeviations:
        result["mean"] = sum(standardDeviations) / len(standardDeviations)
        result["median"] = statistics.median(standardDeviations)
        result["note"] = None
    else:
        result["note"] = TiePointStandardDeviationNote(result["zeroCount"], result["invalidCount"],
                                                       result["missingCount"])
    return result


def ComputeMeanTiePointStandardDeviation(chunk):
    """Mean 3D positional standard deviation of the valid tie points, or
    None (ComputeTiePointStandardDeviationStatistics), with the counts of
    the matrices left out in the Console."""
    statisticsOfChunk = ComputeTiePointStandardDeviationStatistics(chunk)
    PrintCovarianceCounts(statisticsOfChunk)
    return statisticsOfChunk["mean"]


def PrintCovarianceCounts(statisticsOfChunk):
    """Console lines on the covariance matrices that the mean and the
    median of the tie-point standard deviation leave out."""
    if statisticsOfChunk["zeroCount"] and statisticsOfChunk["count"]:
        print("  {} with a covariance matrix of zeros left out of the tie-point \u03c3.".format(
            FormatCountWithNoun(statisticsOfChunk["zeroCount"], "3D tie point", "3D tie points")))
    if statisticsOfChunk["invalidCount"]:
        print("  {} with a covariance matrix that is not a valid covariance (not finite, symmetric, and "
              "positive semi-definite) left out of the tie-point \u03c3.".format(
                  FormatCountWithNoun(statisticsOfChunk["invalidCount"], "3D tie point", "3D tie points")))


def LengthUnitLabel(chunk):
    """Unit of the lengths derived from chunk coordinates: metres when
    the chunk has a physical scale (IsChunkScalePhysical), otherwise
    chunk units, also with the transform that Region Tools records for
    a chunk without one, whose scale 1 is arbitrary."""
    return "m" if IsChunkScalePhysical(chunk) else "chunk units"


def WorldPosition(chunkMatrix, internalPosition):
    """(x, y, z) of an internal position in the world coordinates of the
    chunk (chunk.transform.matrix), or the internal coordinates without a
    matrix, or None without a position.  The components are read as x, y,
    and z (VectorFromMetashape), or by position from a sequence."""
    if internalPosition is None:
        return None
    position = chunkMatrix.mulp(internalPosition) if chunkMatrix is not None else internalPosition
    components = VectorFromMetashape(position)
    if components is None:
        components = tuple(position)[:3]
    return tuple(float(value) for value in components)


def ImageAndMarkerPositions(chunk):
    """{("image", index): (x, y, z), ("marker", index): (x, y, z)} of the
    perspective centres of the enabled, oriented images and the estimated
    positions of the markers, in the world coordinates of the chunk
    (metres with a physical scale, chunk units without one), by their
    index in chunk.cameras and chunk.markers, which the copies of the
    iterations keep."""
    transform = getattr(chunk, "transform", None)
    chunkMatrix = getattr(transform, "matrix", None) if transform is not None else None
    positions = {}
    for index, camera in enumerate(getattr(chunk, "cameras", None) or []):
        if not IsOrientedEnabledCamera(camera):
            continue
        try:
            position = WorldPosition(chunkMatrix, getattr(camera, "center", None))
        except Exception as suppressedError:
            ReportSuppressedError("gradualSelectionFilter.ImageAndMarkerPositions.image", suppressedError)
            continue
        if position is not None:
            positions[("image", index)] = position
    for index, marker in enumerate(getattr(chunk, "markers", None) or []):
        try:
            position = WorldPosition(chunkMatrix, getattr(marker, "position", None))
        except Exception as suppressedError:
            ReportSuppressedError("gradualSelectionFilter.ImageAndMarkerPositions.marker", suppressedError)
            continue
        if position is not None:
            positions[("marker", index)] = position
    return positions


def OrientedImageLabels(chunk):
    """{index: label} of the enabled, oriented images of *chunk*, by their
    index in chunk.cameras, which the copies of the iterations keep: the
    images whose orientation an iteration can cost
    (ImagesNoLongerOriented)."""
    labels = {}
    for index, camera in enumerate(getattr(chunk, "cameras", None) or []):
        if IsOrientedEnabledCamera(camera):
            labels[index] = str(getattr(camera, "label", "") or "image {}".format(index + 1))
    return labels


def ImagesNoLongerOriented(previousLabels, labels):
    """The labels of the images that were enabled and oriented in
    *previousLabels* and are not in *labels* (OrientedImageLabels), in the
    order of the chunk.  Metashape no longer
    orients an image whose tie points were all removed when Optimize Cameras
    runs again, and says so (Geert, 15 October 2026), so that an iteration
    can cost orientations.  Such an image drops out of the largest movement
    (LargestMovements), which compares the images that have a position in
    both iterations."""
    return [previousLabels[index] for index in sorted(previousLabels or {}) if index not in labels]


# Labels of images no longer oriented that the tooltip of the results
# table and the sentence under it name before they count the rest, which
# the Console names (LostOrientationConsoleText).
MAXIMUM_LISTED_LOST_IMAGES = 10


def LostOrientationLabelsText(labels):
    """"IMG_0003 and IMG_0007", or the first MAXIMUM_LISTED_LOST_IMAGES
    labels and how many more, for the results dialog: the Console names
    them all."""
    if len(labels) <= MAXIMUM_LISTED_LOST_IMAGES:
        return JoinWithAnd(list(labels))
    shown = list(labels[:MAXIMUM_LISTED_LOST_IMAGES])
    return "{}, and {:,} more (all named in the Console)".format(
        ", ".join(shown), len(labels) - MAXIMUM_LISTED_LOST_IMAGES)


def LostOrientationConsoleText(row):
    """"images no longer oriented since the previous iteration: 2
    (IMG_0003 and IMG_0007)" of a row of SummaryRow, with every label, or
    "... : 0", since the chunk as found for iteration 0
    (PreviousStateText)."""
    labels = row.get("imagesNoLongerOriented") or []
    if not labels:
        return "images no longer oriented since {}: 0".format(PreviousStateText(row))
    return "images no longer oriented since {}: {:,} ({})".format(
        PreviousStateText(row), len(labels), JoinWithAnd(list(labels)))


def LostOrientationSummaryText(summaryRows):
    """The line of the results dialog on the images that the run left
    without an orientation, one sentence per iteration that left any,
    iteration 0 included, or an empty text when it left none."""
    parts = []
    totalCount = 0
    for row in summaryRows:
        if IsStateAsFound(row):
            continue
        labels = row.get("imagesNoLongerOriented") or []
        if labels:
            totalCount += len(labels)
            parts.append("After iteration {}: {}.".format(row["iteration"], LostOrientationLabelsText(labels)))
    if not parts:
        return ""
    return "{} no longer oriented after an iteration. {}".format(
        FormatCountWithNoun(totalCount, "image was", "images were"), " ".join(parts))


def LargestMovements(previousPositions, positions):
    """(largest movement of an image, largest movement of a marker) from
    *previousPositions* to *positions* (ImageAndMarkerPositions), each
    None when no image or marker has both positions."""
    largest = {"image": None, "marker": None}
    for key, position in positions.items():
        previous = (previousPositions or {}).get(key)
        if previous is None:
            continue
        distance = math.sqrt(sum((value - previousValue) ** 2 for value, previousValue in zip(position, previous)))
        if largest[key[0]] is None or distance > largest[key[0]]:
            largest[key[0]] = distance
    return largest["image"], largest["marker"]


def CollectMetrics(chunk, tiePointAccuracy=1.0):
    """Collect all quality metrics for the current chunk state, with the
    errors of the single control points and check points
    (controlPointErrors and checkPointErrors, see
    _ComputeMarkerReferenceErrors)."""
    reprojectionStatistics = ComputeReprojectionStatistics(chunk)
    controlRmse, controlCount, controlPointErrors = ComputeControlPointErrors(chunk)
    checkRmse, checkCount, checkPointErrors = ComputeCheckPointErrors(chunk)
    criterionCensus = ComputeCriterionCensus(chunk)
    criterionMaxima = {name: entry[0] for name, entry in criterionCensus.items()}
    standardDeviations = ComputeTiePointStandardDeviationStatistics(chunk)
    PrintCovarianceCounts(standardDeviations)
    sigmaNought, sigmaNoughtRedundancy = ComputeSigmaNoughtAndRedundancy(
        chunk, reprojectionStatistics, tiePointAccuracy)
    return {
        "points": CountValidPoints(chunk),
        "rms": reprojectionStatistics["rms"],
        "maximumImageRms": reprojectionStatistics["maximumImageRms"],
        "minimumImageRms": reprojectionStatistics["minimumImageRms"],
        "controlRmse": controlRmse,
        "controlCount": controlCount,
        "controlPointErrors": controlPointErrors,
        "checkRmse": checkRmse,
        "checkCount": checkCount,
        "checkPointErrors": checkPointErrors,
        "reconstructionUncertaintyMaximum": criterionMaxima["Reconstruction Uncertainty"],
        "projectionAccuracyMaximum": criterionMaxima["Projection Accuracy"],
        "reprojectionErrorMaximum": criterionMaxima["Reprojection Error"],
        "meanTiePointStandardDeviation": standardDeviations["mean"],
        "medianTiePointStandardDeviation": standardDeviations["median"],
        "zeroCovarianceCount": standardDeviations["zeroCount"],
        "tiePointStandardDeviationNote": standardDeviations["note"],
        "criterionWorstValues": criterionMaxima,
        "criterionWorstCounts": {name: entry[1] for name, entry in criterionCensus.items()},
        "criterionPlaceholderCounts": {name: entry[2] for name, entry in criterionCensus.items()},
        "positions": ImageAndMarkerPositions(chunk),
        "orientedImages": OrientedImageLabels(chunk),
        "lengthUnit": LengthUnitLabel(chunk),
        "minimumImageCount": GetMinimumImageCount(chunk),
        "sigma0": sigmaNought,
        "sigma0Note": None if sigmaNought is not None else DescribeMissingSigmaNought(
            reprojectionStatistics, sigmaNoughtRedundancy),
        "sigma0Exclusions": DescribeSigmaNoughtExclusions(reprojectionStatistics),
        "metashapeSigma0": ReadMetashapeSigmaNought(chunk),
    }


def SummaryRow(iteration, label, chunkKey, removed, metrics, previousRow=None):
    """The row of one state of the run for the results: the chunk as found
    (*iteration* STATE_AS_FOUND, the first row), iteration 0 (the chunk
    re-adjusted with the settings of the filter before any tie point is
    removed), or a later iteration.  It holds the metrics of
    CollectMetrics, the median tie-point standard deviation, the errors of
    the single control points and check points, the settings of the
    adjustment (optimisationSettings, when the metrics hold them), and the
    largest movement of an image and of a marker since *previousRow* (None
    for the chunk as found or without positions), in the world coordinates
    of the chunk.  Iteration 0 is measured from the chunk as found, because
    it is a new adjustment that can move images.  The row also holds the
    labels of the images that were enabled and oriented in *previousRow*
    and are no longer oriented (imagesNoLongerOriented, None without
    *previousRow*)."""
    imageMovement = markerMovement = None
    lostLabels = None
    if previousRow is not None:
        imageMovement, markerMovement = LargestMovements(previousRow.get("positions"), metrics.get("positions") or {})
        # The images that the iteration left without an orientation.
        if previousRow.get("orientedImages") is not None and metrics.get("orientedImages") is not None:
            lostLabels = ImagesNoLongerOriented(previousRow["orientedImages"], metrics["orientedImages"])
    return {
        "iteration": iteration,
        "label": label,
        "chunkKey": chunkKey,
        "removed": removed,
        "points": metrics["points"],
        "rms": metrics["rms"],
        "maximumImageRms": metrics["maximumImageRms"],
        "minimumImageRms": metrics["minimumImageRms"],
        "controlRmse": metrics["controlRmse"],
        "controlCount": metrics["controlCount"],
        "controlPointErrors": metrics.get("controlPointErrors") or {},
        "checkRmse": metrics["checkRmse"],
        "checkCount": metrics["checkCount"],
        "checkPointErrors": metrics.get("checkPointErrors") or {},
        "reconstructionUncertaintyMaximum": metrics["reconstructionUncertaintyMaximum"],
        "projectionAccuracyMaximum": metrics["projectionAccuracyMaximum"],
        "reprojectionErrorMaximum": metrics["reprojectionErrorMaximum"],
        "meanTiePointStandardDeviation": metrics["meanTiePointStandardDeviation"],
        "medianTiePointStandardDeviation": metrics.get("medianTiePointStandardDeviation"),
        "tiePointStandardDeviationNote": metrics.get("tiePointStandardDeviationNote"),
        "lengthUnit": metrics["lengthUnit"],
        "minimumImageCount": metrics["minimumImageCount"],
        "sigma0": metrics["sigma0"],
        "sigma0Note": metrics.get("sigma0Note"),
        "sigma0Exclusions": metrics.get("sigma0Exclusions"),
        "metashapeSigma0": metrics.get("metashapeSigma0"),
        "metashapeSigma0Note": metrics.get("metashapeSigma0Note"),
        "optimisationSettings": metrics.get("optimisationSettings"),
        "positions": metrics.get("positions") or {},
        "hasPreviousState": previousRow is not None,
        "largestImageMovement": imageMovement,
        "largestMarkerMovement": markerMovement,
        "orientedImages": metrics.get("orientedImages"),
        "imagesNoLongerOriented": lostLabels,
    }


def _IsFinite(value):
    """True for a number that is neither None, NaN, nor infinite."""
    return value is not None and math.isfinite(value)


# A difference of the check-point or control-point RMSE between the
# iterations that singles out an iteration: 0.1 % of the smallest value,
# and greater than zero.  A test of more than zero alone would let
# rounding decide.  A threshold of 0.1 mm would hide a real
# change in a close-range project, for example from 0.30 mm to 0.25 mm,
# and no
# floor such as 1 nm applies (Geert: 1 nm is absurd as a length, and his
# macro projects have RMSE values in micrometres or below).
MEANINGFUL_RMSE_DIFFERENCE_SHARE = 0.001


def MeaningfulRmseDifference(values):
    """The smallest difference between the largest and the smallest of
    *values* (metres) that singles out an iteration:
    MEANINGFUL_RMSE_DIFFERENCE_SHARE of the smallest value, without a
    floor."""
    return MEANINGFUL_RMSE_DIFFERENCE_SHARE * min(values)


def RmseSinglesOutAnIteration(values):
    """True when the RMSE *values* of the states differ by at least
    MeaningfulRmseDifference and by more than zero, so that a lower one
    can count as lower (IsMeaningfullyLower)."""
    difference = max(values) - min(values)
    return difference > 0.0 and difference >= MeaningfulRmseDifference(values)


def IsMeaningfullyLower(value, defaultValue):
    """The guard of the suggestion against rounding: True when the RMSE
    *value* of a state is lower than *defaultValue*, that of the default
    state, by at least MEANINGFUL_RMSE_DIFFERENCE_SHARE of the lower of
    the two and by more than zero (RmseSinglesOutAnIteration)."""
    return value < defaultValue and RmseSinglesOutAnIteration([value, defaultValue])


def RmseGuardText(measureName, value, defaultValue, defaultName):
    """Why the RMSE *value* of a state is not lower than *defaultValue*,
    that of the default state *defaultName*, by IsMeaningfullyLower: "its
    check-point RMSE is not lower than that of the chunk as found", or
    "its check-point RMSE is lower by only 5e-13 m, less than 0.1 % of the
    lower value", with two significant digits."""
    difference = defaultValue - value
    if difference <= 0.0:
        return "its {} is not lower than that of {}".format(measureName, defaultName)
    return "its {} is lower by only {:.2g} m, less than {:g} % of the lower value".format(
        measureName, difference, 100.0 * MEANINGFUL_RMSE_DIFFERENCE_SHARE)


def SecondItem(pair):
    """The second item of *pair*, as the key of min and max."""
    return pair[1]


# ===================================================================
# The one-sided Wilcoxon signed-rank test of the suggestion
# ===================================================================

def DoubledAverageRanks(values):
    """Twice the rank of each of *values* among them all, the ranks
    counted from 1 in ascending order, with tied values sharing the
    average of their ranks.  Twice an average rank is an integer, also for
    ties: [0.5, 2.0, 2.0] has the ranks 1, 2.5, and 2.5 and gives
    [2, 5, 5]."""
    order = sorted(range(len(values)), key=values.__getitem__)
    doubledRanks = [0] * len(values)
    start = 0
    while start < len(order):
        end = start
        while end + 1 < len(order) and values[order[end + 1]] == values[order[start]]:
            end += 1
        # The ranks start + 1 to end + 1 share their average, whose double
        # is start + end + 2.
        for position in range(start, end + 1):
            doubledRanks[order[position]] = start + end + 2
        start = end + 1
    return doubledRanks


def CountSignAssignmentsUpTo(doubledRanks, doubledLimit):
    """How many of the 2^n assignments of a sign to each of the n ranks of
    *doubledRanks* (DoubledAverageRanks) give positive ranks whose doubled
    sum is at most *doubledLimit*.  Under the null hypothesis of the
    Wilcoxon signed-rank test, every assignment has the probability 2^-n,
    so this count over 2^n is the lower tail of the exact distribution of
    the sum of the positive ranks, also with tied ranks.  It is counted
    exactly, with integers, over the sums up to *doubledLimit* only (a
    rank above it is positive in no counted assignment)."""
    doubledLimit = int(doubledLimit)
    if doubledLimit < 0:
        return 0
    counts = [1] + [0] * doubledLimit
    for doubledRank in doubledRanks:
        if doubledRank > doubledLimit:
            continue
        # The counts with this rank positive added to those with it
        # negative, from the counts before this rank.
        counts[doubledRank:] = [withoutRank + withRank for withoutRank, withRank in zip(counts[doubledRank:], counts)]
    return sum(counts)


# The largest number of differences whose p-value the Wilcoxon signed-rank
# test of the suggestion counts exactly (WilcoxonSignedRankLowerP).  The
# count takes a time of about n^3 for each state compared: 0.04 s for 100
# differences, 0.26 s for 200, and 2.9 s for 400 in Python 3.13.  Above it,
# the test takes the normal approximation with the correction for ties.
# Its relative error grows in the far tail: for 101 differences that are
# all negative and different, it gives 1.3e-18, and the exact count
# 3.9e-31.
EXACT_WILCOXON_MAXIMUM_COUNT = 100


def WilcoxonStandardScore(doubledRanks, doubledPositiveRankSum):
    """The standard score of the sum of the positive ranks, W+, with the
    correction for ties: the mean of W+ is n(n + 1)/4, and its variance
    n(n + 1)(2n + 1)/24 minus the sum of t^3 - t over the groups of t tied
    ranks, divided by 48.  *doubledRanks* are the doubled ranks of the n
    differences (DoubledAverageRanks), and *doubledPositiveRankSum* twice
    W+.  Tied absolute values share a doubled rank, and different values
    never do, so the groups of equal doubled ranks are the groups of
    ties."""
    count = len(doubledRanks)
    groupSizes = {}
    for doubledRank in doubledRanks:
        groupSizes[doubledRank] = groupSizes.get(doubledRank, 0) + 1
    tieCorrection = sum(size ** 3 - size for size in groupSizes.values()) / 48.0
    variance = count * (count + 1) * (2 * count + 1) / 24.0 - tieCorrection
    return (doubledPositiveRankSum / 2.0 - count * (count + 1) / 4.0) / math.sqrt(variance)


def WilcoxonNormalApproximationLowerP(doubledRanks, doubledPositiveRankSum):
    """The lower tail of the sum of the positive ranks, W+, by the normal
    approximation of its standard score (WilcoxonStandardScore), without
    a continuity correction (as SciPy's method "approx")."""
    standardScore = WilcoxonStandardScore(doubledRanks, doubledPositiveRankSum)
    return 0.5 * math.erfc(-standardScore / math.sqrt(2.0))


def UsedWilcoxonDifferences(differences):
    """(the differences that the Wilcoxon signed-rank test uses, their
    doubled ranks, twice the sum of the positive ranks W+): following
    Wilcoxon, a difference of zero is left out, and so is a value that is
    not a finite number, and the absolute values of the others are ranked
    (DoubledAverageRanks)."""
    usedDifferences = [value for value in differences if _IsFinite(value) and value != 0.0]
    doubledRanks = DoubledAverageRanks([abs(value) for value in usedDifferences])
    doubledPositiveRankSum = sum(doubledRank for doubledRank, value in zip(doubledRanks, usedDifferences)
                                 if value > 0.0)
    return usedDifferences, doubledRanks, doubledPositiveRankSum


def WilcoxonNormalScore(differences):
    """The standard score of W+ of *differences* (WilcoxonStandardScore)
    when the test takes the normal approximation for them
    (IsWilcoxonPValueExact), otherwise None.  It orders p-values that the
    approximation gives as equal, as two that are zero in floating-point
    arithmetic (IsSmallerPValue)."""
    usedDifferences, doubledRanks, doubledPositiveRankSum = UsedWilcoxonDifferences(differences)
    if not usedDifferences or IsWilcoxonPValueExact(len(usedDifferences)):
        return None
    return WilcoxonStandardScore(doubledRanks, doubledPositiveRankSum)


def IsWilcoxonPValueExact(usedCount):
    """True when WilcoxonSignedRankLowerP counts the p-value of *usedCount*
    differences exactly, False when it takes the normal approximation."""
    return usedCount <= EXACT_WILCOXON_MAXIMUM_COUNT


def WilcoxonSignedRankLowerP(differences):
    """The p-value of the one-sided Wilcoxon signed-rank test of the
    paired *differences* (for example the errors of a state minus those of
    the default state), with the alternative that the differences tend to
    be negative, so that the state has the smaller errors.  It is exact up
    to EXACT_WILCOXON_MAXIMUM_COUNT differences, and above that the normal
    approximation (WilcoxonNormalApproximationLowerP).

    Following Wilcoxon, a difference of zero is left out, and so is a
    value that is not a finite number.  The absolute values of the other n
    differences are ranked from 1, and tied absolute values get the
    average of their ranks.  W+ is the sum of the ranks of the positive
    differences.  Under the null hypothesis, each difference is positive
    or negative with the probability 1/2, independently of its rank, so
    each of the 2^n assignments of signs has the probability 2^-n.  The
    p-value is the probability of a sum of the positive ranks at most W+.
    It is counted exactly over the doubled ranks, which are integers also
    with ties (CountSignAssignmentsUpTo), without SciPy or NumPy, which the
    Python of Metashape need not have.  The count runs over the smaller
    tail: the distribution is symmetric about half the sum of all ranks.
    The smallest p-value of n differences is 2^-n, so that fewer than 5
    cannot reach 0.05.  The count takes a time of about n^3, so above
    EXACT_WILCOXON_MAXIMUM_COUNT differences the p-value is the normal
    approximation (IsWilcoxonPValueExact).

    Returns (p, n, W+): (1.0, 0, 0.0) without a difference to use."""
    usedDifferences, doubledRanks, doubledPositiveRankSum = UsedWilcoxonDifferences(differences)
    usedCount = len(usedDifferences)
    if usedCount == 0:
        return 1.0, 0, 0.0
    if not IsWilcoxonPValueExact(usedCount):
        return (WilcoxonNormalApproximationLowerP(doubledRanks, doubledPositiveRankSum), usedCount,
                doubledPositiveRankSum / 2.0)
    # By the symmetry of the distribution, the count of the sums at most
    # w equals 2^n minus the count of the sums at most T - w - 1, where T
    # is the doubled sum of all ranks.
    complementLimit = sum(doubledRanks) - doubledPositiveRankSum - 1
    if complementLimit < doubledPositiveRankSum:
        lowerCount = 2 ** usedCount - CountSignAssignmentsUpTo(doubledRanks, complementLimit)
    else:
        lowerCount = CountSignAssignmentsUpTo(doubledRanks, doubledPositiveRankSum)
    return lowerCount / 2 ** usedCount, usedCount, doubledPositiveRankSum / 2.0


def CanShowSignificance(usedCount):
    """True when a one-sided Wilcoxon signed-rank test on *usedCount*
    differences can give a p-value at or below SIGNIFICANCE_LEVEL: its
    smallest p-value is 2^-n, so that fewer than 5 differences cannot
    show a difference at 5 %."""
    return usedCount > 0 and 0.5 ** usedCount <= SIGNIFICANCE_LEVEL


# Below this value the suggestion gives a p-value as less than 1e-300, as
# the Interior Orientation Viewer does.  Floating-point numbers lose digits
# below about 2.2e-308 and become zero below about 4.9e-324.  When all
# check points have smaller errors, the normal approximation
# (WilcoxonNormalApproximationLowerP) falls below 1e-300 from 1,373 check
# points when all the differences have the same absolute value, and from
# 1,830 when no two of them are equal (one pair of equal values gives the
# same).  It is zero from 1,481 check points and from 1,974.  Such
# p-values are ordered by their standard scores (IsSmallerPValue).
SMALLEST_SHOWN_P_VALUE = 1e-300


def FormatPValue(pValue):
    """A p-value with two significant digits ("0.0024", "0.50", "1.0"), or
    "less than 1e-300" below SMALLEST_SHOWN_P_VALUE."""
    if pValue < SMALLEST_SHOWN_P_VALUE:
        return "less than {:g}".format(SMALLEST_SHOWN_P_VALUE)
    return "{:#.2g}".format(pValue)


def NormalApproximationText(comparison):
    """" by the normal approximation" when the test did not count the
    p-value of *comparison* (CompareWithDefaultState) exactly
    (IsWilcoxonPValueExact), and an empty text when it did."""
    if IsWilcoxonPValueExact(comparison["usedCount"]):
        return ""
    return " by the normal approximation"


def PValueText(comparison):
    """The p-value of *comparison* (CompareWithDefaultState) with two
    significant digits (FormatPValue), followed by "by the normal
    approximation" when the test did not count it exactly
    (NormalApproximationText)."""
    return FormatPValue(comparison["pValue"]) + NormalApproximationText(comparison)


def PValueRelationText(comparison):
    """"p = " and the p-value of *comparison* (PValueText), or "p < 1e-300"
    below SMALLEST_SHOWN_P_VALUE, followed by "by the normal
    approximation" when the test did not count it exactly
    (NormalApproximationText)."""
    if comparison["pValue"] < SMALLEST_SHOWN_P_VALUE:
        return "p < {:g}".format(SMALLEST_SHOWN_P_VALUE) + NormalApproximationText(comparison)
    return "p = " + PValueText(comparison)


def SignificanceLevelText():
    """"5 %": the level of the test (SIGNIFICANCE_LEVEL)."""
    return "{:g} %".format(100.0 * SIGNIFICANCE_LEVEL)


def PValueAgainstLevelText(pValue):
    """"below the level of 5 %", "at the level of 5 %", or "above the
    level of 5 %" for *pValue*."""
    if pValue < SIGNIFICANCE_LEVEL:
        position = "below"
    elif pValue == SIGNIFICANCE_LEVEL:
        position = "at"
    else:
        position = "above"
    return "{} the level of {}".format(position, SignificanceLevelText())


def APrioriVariance(pointError):
    """The square of the root sum of squares of the a priori standard
    deviations of a point of _ComputeMarkerReferenceErrors ((3D error, root
    sum of squares)), or None when it is not a positive, finite number."""
    rootSumOfSquares = pointError[1] if pointError is not None else None
    if not (isinstance(rootSumOfSquares, (int, float)) and math.isfinite(rootSumOfSquares)):
        return None
    variance = rootSumOfSquares * rootSumOfSquares
    return variance if 0.0 < variance < math.inf else None


def PairedSquaredErrorDifferences(statePointErrors, defaultPointErrors):
    """The paired differences of the Wilcoxon signed-rank test of a state
    against the default state: for every point that both hold (by its
    pairing key, _ComputeMarkerReferenceErrors, in the order of
    *defaultPointErrors*), d_i =
    (|error of the state, i|^2 - |error of the default state, i|^2) /
    sigma_i^2, where sigma_i is the root sum of squares of the a priori
    standard deviations of the reference coordinates of the point (that of
    the default state, APrioriVariance).  When a compared point has no
    positive sigma_i, the differences of all points are left undivided.

    Returns (differences, pointsWithoutAPrioriCount), the second the number
    of the compared points without a positive sigma_i (0 when the
    differences are divided)."""
    keys = [key for key in defaultPointErrors if key in statePointErrors]
    variances = [APrioriVariance(defaultPointErrors[key]) for key in keys]
    pointsWithoutAPrioriCount = sum(1 for variance in variances if variance is None)
    differences = []
    for key, variance in zip(keys, variances):
        stateError = statePointErrors[key][0]
        defaultError = defaultPointErrors[key][0]
        difference = stateError * stateError - defaultError * defaultError
        differences.append(difference if pointsWithoutAPrioriCount else difference / variance)
    return differences, pointsWithoutAPrioriCount


def SharedPointRmse(pointErrors, keys):
    """The RMSE of the errors of *pointErrors* (by the pairing keys of
    _ComputeMarkerReferenceErrors) at *keys*, or None without a key."""
    if not keys:
        return None
    return math.sqrt(sum(pointErrors[key][0] * pointErrors[key][0] for key in keys) / len(keys))


def RmseOverDefaultPoints(statePointErrors, defaultPointErrors):
    """The RMSE of a state over the points of the default state, with each
    point that the state lacks counted at its error in the default state,
    as unchanged (the point errors of _ComputeMarkerReferenceErrors), or
    None when the default state has no point.  A point that only the state
    holds takes no part."""
    if not defaultPointErrors:
        return None
    errors = [statePointErrors[key][0] if key in statePointErrors else defaultError[0]
              for key, defaultError in defaultPointErrors.items()]
    return math.sqrt(sum(error * error for error in errors) / len(errors))


def CompareWithDefaultState(row, defaultRow, rmseKey, errorsKey):
    """The comparison of the state of *row* with the default state of
    *defaultRow* (rows of SummaryRow) by the RMSE of *rmseKey* and the
    errors of the single points of *errorsKey*: a dict with the iteration,
    the RMSE of the state ("rmse"), isLower (IsMeaningfullyLower), the
    p-value of WilcoxonSignedRankLowerP with the number of differences it
    used, the number of compared points, pointsWithoutAPrioriCount
    (PairedSquaredErrorDifferences), and isSignificantlyBetter, which needs
    both isLower and a p-value at or below SIGNIFICANCE_LEVEL.

    Both conditions use the points that the two states share.  When they
    hold the same points, isLower compares their RMSE values.  When one
    state lacks a point of the other, for example a check point without an
    estimated position, isLower compares the RMSE values over the shared
    points (SharedPointRmse): otherwise a state that lost its worst point
    would pass the guard by that loss alone.  "comparedRmse" and
    "comparedDefaultRmse" are the values that isLower compares, and
    "pointCount" and "defaultPointCount" the points of each state.
    "rankingRmse" is the RMSE by which BestOfBetterStates ranks the state:
    its RMSE when it holds the points of the default, and otherwise its RMSE
    over the points of the default with each point that it lacks counted
    at its error in the default (RmseOverDefaultPoints).  "lackedCount" is
    the number of these lacked points."""
    statePointErrors = row.get(errorsKey) or {}
    defaultPointErrors = defaultRow.get(errorsKey) or {}
    differences, pointsWithoutAPrioriCount = PairedSquaredErrorDifferences(statePointErrors, defaultPointErrors)
    pValue, usedCount, _ = WilcoxonSignedRankLowerP(differences)
    standardScore = WilcoxonNormalScore(differences)
    sharedKeys = [key for key in defaultPointErrors if key in statePointErrors]
    if len(sharedKeys) == len(statePointErrors) == len(defaultPointErrors):
        comparedRmse, comparedDefaultRmse = row[rmseKey], defaultRow[rmseKey]
        rankingRmse = row[rmseKey]
    else:
        comparedRmse = SharedPointRmse(statePointErrors, sharedKeys)
        comparedDefaultRmse = SharedPointRmse(defaultPointErrors, sharedKeys)
        rankingRmse = RmseOverDefaultPoints(statePointErrors, defaultPointErrors)
    isLower = comparedRmse is not None and IsMeaningfullyLower(comparedRmse, comparedDefaultRmse)
    return {"iteration": row["iteration"], "rmse": row[rmseKey], "isLower": isLower, "pValue": pValue,
            "usedCount": usedCount, "comparedCount": len(differences),
            "comparedRmse": comparedRmse, "comparedDefaultRmse": comparedDefaultRmse, "rankingRmse": rankingRmse,
            "lackedCount": sum(1 for key in defaultPointErrors if key not in statePointErrors),
            "pointCount": len(statePointErrors), "defaultPointCount": len(defaultPointErrors),
            "pointsWithoutAPrioriCount": pointsWithoutAPrioriCount, "standardScore": standardScore,
            "isSignificantlyBetter": isLower and usedCount > 0 and pValue <= SIGNIFICANCE_LEVEL}


def IsSmallerPValue(comparison, otherComparison):
    """True when the p-value of *comparison* is smaller than that of
    *otherComparison* (CompareWithDefaultState).  Two equal p-values of the
    normal approximation, such as two that are zero in floating-point
    arithmetic, are ordered by their standard scores (WilcoxonNormalScore):
    the lower tail is smaller for the smaller score."""
    if comparison["pValue"] != otherComparison["pValue"]:
        return comparison["pValue"] < otherComparison["pValue"]
    standardScore, otherStandardScore = comparison["standardScore"], otherComparison["standardScore"]
    return standardScore is not None and otherStandardScore is not None and standardScore < otherStandardScore


def HoldsTheDefaultPoints(comparison):
    """True when the state of *comparison* (CompareWithDefaultState) holds
    exactly the points of the default state."""
    return comparison["pointCount"] == comparison["defaultPointCount"] == comparison["comparedCount"]


def BestOfBetterStates(betterComparisons):
    """The state to suggest of the significantly better states of
    *betterComparisons* (CompareWithDefaultState, in the order of the
    states), or None without one.  It is the earliest state whose
    "rankingRmse" is within MEANINGFUL_RMSE_DIFFERENCE_SHARE of the lowest
    (IsMeaningfullyLower), so that rounding does not decide.  The ranking
    RMSE of a state does not depend on the other states.  A state gains
    nothing by a lost point, because the point counts at its error in the
    default.  Of states within 0.1 % of each other, the earliest is kept,
    so in such near ties the choice can depend on the states between them."""
    if not betterComparisons:
        return None
    lowestRmse = min(comparison["rankingRmse"] for comparison in betterComparisons)
    return next(comparison for comparison in betterComparisons
                if not IsMeaningfullyLower(lowestRmse, comparison["rankingRmse"]))


def LowerStateText(bestComparison, betterComparisons):
    """The sentence of the reason when another significantly better state
    of *betterComparisons* has a lower ranking RMSE than the suggested
    state of *bestComparison* (BestOfBetterStates), by less than 0.1 %:
    "Iteration 3 is lower by less than 0.1 % (0.008990 m), so the earlier
    state is suggested", naming the lowest of them.  None without one."""
    lowerComparisons = [comparison for comparison in betterComparisons
                        if comparison["rankingRmse"] < bestComparison["rankingRmse"]]
    if not lowerComparisons:
        return None
    lowestComparison = lowerComparisons[0]
    for comparison in lowerComparisons[1:]:
        if comparison["rankingRmse"] < lowestComparison["rankingRmse"]:
            lowestComparison = comparison
    stateName = StateName(lowestComparison["iteration"])
    return "{} is lower by less than {:g} % ({} m), so the earlier state is suggested".format(
        stateName[:1].upper() + stateName[1:], 100.0 * MEANINGFUL_RMSE_DIFFERENCE_SHARE,
        FormatSignificantDigits(lowestComparison["rankingRmse"]))


def SharedPointsText(comparison, defaultName, measureName, pointNouns):
    """The sentence of the reason when the state of *comparison*
    (CompareWithDefaultState) and the default state *defaultName* do not
    hold the same points, so that the test and the guard used only the
    shared ones: "Iteration 1 has 10 check points and the chunk as found
    has 11, so the test and the comparison of the check-point RMSE use only
    their 10 shared check points".  None when they hold the same points."""
    pointCount, defaultPointCount = comparison["pointCount"], comparison["defaultPointCount"]
    sharedCount = comparison["comparedCount"]
    if pointCount == defaultPointCount == sharedCount:
        return None
    stateName = StateName(comparison["iteration"])
    return "{} has {} and {} has {:,}, so the test and the comparison of the {} use only their {}".format(
        stateName[:1].upper() + stateName[1:], FormatCountWithNoun(pointCount, *pointNouns), defaultName,
        defaultPointCount, measureName,
        FormatCountWithNoun(sharedCount, "shared " + pointNouns[0], "shared " + pointNouns[1]))


def UndividedDifferencesText(comparison, pointNouns):
    """The sentence of the reason on differences that the test left
    undivided (PairedSquaredErrorDifferences), or None when it divided
    them."""
    withoutCount = comparison["pointsWithoutAPrioriCount"]
    if not withoutCount:
        return None
    return ("The test does not divide the differences of the squared errors by the a priori variances, because {} "
            "of the {} {} no positive a priori standard deviation".format(
                withoutCount, FormatCountWithNoun(comparison["comparedCount"], *pointNouns),
                "has" if withoutCount == 1 else "have"))


def NoBetterStateTexts(comparisons, defaultRow, rmseKey, measureName, pointNouns):
    """The sentences of the reason when no state is significantly better
    than the default state of *defaultRow*: the smallest p-value of the
    comparisons (CompareWithDefaultState) and its state, that a test on
    fewer than 5 points cannot show a difference at 5 %
    (CanShowSignificance), or why a state with a p-value at or below the
    level is not lower by the RMSE of *rmseKey* over the shared points
    (RmseGuardText).  Returns (sentences, the comparison of the smallest
    p-value or None)."""
    defaultName = StateName(defaultRow["iteration"])
    testedComparisons = [comparison for comparison in comparisons if comparison["usedCount"] > 0]
    if not testedComparisons:
        if any(comparison["comparedCount"] > 0 for comparison in comparisons):
            return ["The {} have the same errors in every state".format(pointNouns[1])], None
        return ["No other state shares a {} with {}".format(pointNouns[0], defaultName)], None
    smallestComparison = testedComparisons[0]
    for comparison in testedComparisons[1:]:
        if IsSmallerPValue(comparison, smallestComparison):
            smallestComparison = comparison
    largestUsedCount = max(comparison["usedCount"] for comparison in testedComparisons)
    if not CanShowSignificance(largestUsedCount):
        return ["With {}, a one-sided Wilcoxon signed-rank test cannot show a difference at the level of {}".format(
                    FormatCountWithNoun(largestUsedCount, *pointNouns), SignificanceLevelText()),
                "The smallest p-value of the states is {}, for {}".format(
                    PValueText(smallestComparison), StateName(smallestComparison["iteration"]))], \
            smallestComparison
    sentences = ["The smallest p-value of a one-sided Wilcoxon signed-rank test is {}, for {} on {}, {}".format(
        PValueText(smallestComparison), StateName(smallestComparison["iteration"]),
        FormatCountWithNoun(smallestComparison["usedCount"], *pointNouns),
        PValueAgainstLevelText(smallestComparison["pValue"]))]
    if smallestComparison["pValue"] <= SIGNIFICANCE_LEVEL and not smallestComparison["isLower"]:
        guardText = RmseGuardText(measureName, smallestComparison["comparedRmse"],
                                  smallestComparison["comparedDefaultRmse"], defaultName)
        sentences.append(guardText[:1].upper() + guardText[1:])
    return sentences, smallestComparison


def RecommendIteration(summaryRows):
    """Suggest the state of the run that offers the best quality: the
    chunk as found (STATE_AS_FOUND), iteration 0, or a later iteration.

    Strategy:
      * If the check-point RMSE is available for two or more states, the
        check points decide (ReferencePointSuggestion).  The default is
        the first of these states, the chunk as found, or iteration 0 when
        the chunk as found has no check-point RMSE.  Another state is
        suggested only when it is significantly better than the default:
        over the check points that both hold, its RMSE is lower by at
        least MEANINGFUL_RMSE_DIFFERENCE_SHARE of the lower value
        (IsMeaningfullyLower), and a one-sided Wilcoxon signed-rank test of
        the squared errors of the paired check points gives a p-value at
        or below SIGNIFICANCE_LEVEL (CompareWithDefaultState).  Of these
        states, the earliest whose RMSE over the points of the default is
        within 0.1 % of the lowest is suggested (BestOfBetterStates), and
        without one the default.  The reference coordinates of the check points take no
        part in the adjustment, and their RMSE can rise again when the
        cloud is over-thinned.  Choosing by it, however, uses the check
        points to select the state, so the reason says that they are then
        no longer an independent check of the suggested state.  When the
        default is the chunk as found and its adjustment differs from that
        of iteration 0 (AdjustmentDifferences), the reason says so.
      * Otherwise, if the control-point RMSE is available for two or more
        states, the control points decide in the same way, described as
        what their RMSE is: a fit statistic of the adjustment, not an
        independent check.
      * Otherwise, recommend the earliest iteration that captures at
        least RECOMMENDATION_IMPROVEMENT_FRACTION of the largest
        reduction of the RMS reprojection error, from iteration 0 to the
        lowest RMS of the run (_RecommendIterationByRms), over iterations
        0 to N only: the chunk as found takes no part, because its RMS
        reprojection error comes from another adjustment.

    Returns (iteration, reason) or (None, None).
    """
    if len(summaryRows) < 2:
        return None, None
    checkPointSuggestion = ReferencePointSuggestion(summaryRows, True)
    if checkPointSuggestion is not None:
        return checkPointSuggestion
    checkRows = [row for row in summaryRows if _IsFinite(row.get("checkRmse"))]
    controlPointSuggestion = ReferencePointSuggestion(summaryRows, False, MissingCheckPointText(checkRows))
    if controlPointSuggestion is not None:
        return controlPointSuggestion
    return _RecommendIterationByRms([row for row in summaryRows if not IsStateAsFound(row)])


def MissingCheckPointText(checkRows):
    """Why the check-point RMSE does not choose the state when the
    control-point RMSE does, for *checkRows*, the rows with a check-point
    RMSE: "There are no check points" without any, and "Only iteration 3
    has a check-point RMSE" or "Only the chunk as found has a check-point
    RMSE" with one."""
    if len(checkRows) == 1:
        return "Only {} has a check-point RMSE".format(StateName(checkRows[0]["iteration"]))
    return "There are no check points"


def CheckPointCaveatText(suggestedNoun):
    """The caveat of a suggestion by the check points: they were used to
    make the choice, so their RMSE is no longer an independent check of
    the suggested state (*suggestedNoun*, "iteration" or "state")."""
    return ("The reference coordinates of the check points take "
            "no part in the adjustment, but the check points were used to make this choice, so this RMSE is no "
            "longer an independent check of the suggested {}. For an independent assessment, use check points that "
            "played no part in the choice".format(suggestedNoun))


def ControlPointCaveatText(missingCheckPointText):
    """The caveat of a suggestion by the control points, whose RMSE is a fit
    statistic of the adjustment (*missingCheckPointText*,
    MissingCheckPointText)."""
    return ("{}, and the control-point RMSE is a fit statistic of the adjustment rather than an independent check, "
            "so weigh it against the other metrics".format(missingCheckPointText))


def AdjustmentCaveatText(summaryRows):
    """The sentence of the reason when the default state is the chunk as
    found and the comparison of its adjustment with that of iteration 0
    flags a difference (AdjustmentDifferences of the rows of
    *summaryRows*), or None when it flags none."""
    asFoundRow = next((row for row in summaryRows if IsStateAsFound(row)), None)
    iterationZeroRow = next((row for row in summaryRows if row.get("iteration") == 0), None)
    if asFoundRow is None or iterationZeroRow is None:
        return None
    differences = AdjustmentDifferences(asFoundRow.get("metashapeSigma0"), iterationZeroRow.get("metashapeSigma0"),
                                        asFoundRow.get("optimisationSettings"),
                                        iterationZeroRow.get("optimisationSettings"))
    if not differences:
        return None
    return ("The chunk as found was adjusted differently. {}. A difference between it and another state may "
            "therefore come from the adjustment rather than from the filtering. Iteration 0 is the chunk "
            "re-adjusted with the settings of the filter".format(". ".join(differences)))


def ReferencePointSuggestion(summaryRows, isCheckPoints, missingCheckPointText=None):
    """The suggestion of RecommendIteration by the check points
    (*isCheckPoints* True) or by the control points, as (iteration,
    reason), or None when fewer than two states have that RMSE.

    The candidates are the states with that RMSE, and the default is the
    first of them.  Each other candidate is compared with it
    (CompareWithDefaultState).  Of the significantly better states,
    BestOfBetterStates chooses the one to suggest, with the test that shows
    it.  The reason names its ranking RMSE, over the points of the default
    when the states do not all hold them, and says when a later state is
    lower by less than 0.1 % (LowerStateText).  Without one, the default is
    suggested, with the
    smallest p-value and its state (NoBetterStateTexts).  When the tested
    state and the default do not hold the same points, the reason says so
    (SharedPointsText).  The reason ends with the caveat of the points
    (CheckPointCaveatText, or ControlPointCaveatText with
    *missingCheckPointText*) and, when the default is the chunk as found,
    AdjustmentCaveatText."""
    if isCheckPoints:
        rmseKey, errorsKey, measureName = "checkRmse", "checkPointErrors", "check-point RMSE"
        pointNouns = ("check point", "check points")
    else:
        rmseKey, errorsKey, measureName = "controlRmse", "controlPointErrors", "control-point RMSE"
        pointNouns = ("control point", "control points")
    candidateRows = [row for row in summaryRows if _IsFinite(row.get(rmseKey))]
    if len(candidateRows) < 2:
        return None
    defaultRow = candidateRows[0]
    defaultName = StateName(defaultRow["iteration"])
    comparisons = [CompareWithDefaultState(row, defaultRow, rmseKey, errorsKey) for row in candidateRows[1:]]
    betterComparisons = [comparison for comparison in comparisons if comparison["isSignificantlyBetter"]]
    bestComparison = BestOfBetterStates(betterComparisons)
    if bestComparison is not None:
        suggestedIteration = bestComparison["iteration"]
        lowerStateText = LowerStateText(bestComparison, betterComparisons)
        measureText = measureName
        if not all(HoldsTheDefaultPoints(comparison) for comparison in betterComparisons):
            measureText += " over the {} of {}".format(pointNouns[1], defaultName)
        if lowerStateText:
            measureText += " to within {:g} %".format(100.0 * MEANINGFUL_RMSE_DIFFERENCE_SHARE)
        valueText = "{} m".format(FormatSignificantDigits(bestComparison["rankingRmse"]))
        lackedCount = bestComparison["lackedCount"]
        if lackedCount:
            valueText += ", with the {} that it lacks counted at {} error in {}".format(
                FormatCountWithNoun(lackedCount, *pointNouns), "its" if lackedCount == 1 else "their", defaultName)
        sentences = ["lowest {} ({}) of the states that are significantly better than {}".format(
                         measureText, valueText, defaultName),
                     "A one-sided Wilcoxon signed-rank test on {} gives {}, {}".format(
                         FormatCountWithNoun(bestComparison["usedCount"], *pointNouns),
                         PValueRelationText(bestComparison), PValueAgainstLevelText(bestComparison["pValue"]))]
        if lowerStateText:
            sentences.append(lowerStateText)
        testedComparison = bestComparison
    else:
        suggestedIteration = defaultRow["iteration"]
        detailSentences, testedComparison = NoBetterStateTexts(comparisons, defaultRow, rmseKey, measureName,
                                                               pointNouns)
        sentences = ["no state is significantly better than {}".format(defaultName)] + detailSentences
    sharedText = (SharedPointsText(testedComparison, defaultName, measureName, pointNouns) if testedComparison
                  else None)
    if sharedText:
        sentences.append(sharedText)
    undividedText = UndividedDifferencesText(testedComparison, pointNouns) if testedComparison else None
    if undividedText:
        sentences.append(undividedText)
    if isCheckPoints:
        sentences.append(CheckPointCaveatText("state" if suggestedIteration == STATE_AS_FOUND else "iteration"))
    else:
        sentences.append(ControlPointCaveatText(missingCheckPointText or "There are no check points"))
    if IsStateAsFound(defaultRow):
        adjustmentCaveat = AdjustmentCaveatText(summaryRows)
        if adjustmentCaveat:
            sentences.append(adjustmentCaveat)
    return suggestedIteration, ". ".join(sentences)


def _RecommendIterationByRms(summaryRows):
    """The rule of the RMS reprojection error of RecommendIteration, over
    the rows of iterations 0 to N (*summaryRows* without the chunk as
    found, whose RMS reprojection error comes from another adjustment)."""

    rmsRows = [
        (row["iteration"], row["rms"])
        for row in summaryRows if _IsFinite(row["rms"])]
    if len(rmsRows) < 2:
        return None, None

    firstRms = rmsRows[0][1]
    # The lowest RMS of the run, at its first occurrence.
    lowestIteration, lowestRms = min(rmsRows, key=SecondItem)
    largestImprovement = firstRms - lowestRms
    if largestImprovement <= 0:
        return rmsRows[0][0], (
            "filtering did not reduce the RMS reprojection error, so "
            "the unfiltered solution is preferable")

    thresholdRms = firstRms - (
        RECOMMENDATION_IMPROVEMENT_FRACTION * largestImprovement)
    lastIteration = rmsRows[-1][0]
    for rowIndex, (iteration, rms) in enumerate(rmsRows):
        if rms > thresholdRms:
            continue
        if iteration == lastIteration:
            # The 95 % point was not reached until the final computed
            # iteration, so the curve was still descending when the run
            # ended (e.g. stopped by the safety cap or the iteration
            # limit): later iterations need not gain little.
            return iteration, (
                "the lowest RMS reprojection error among the "
                "computed iterations. The error was still falling "
                "when the run ended, so consider whether an earlier "
                "iteration with nearly the same error but more "
                "retained points is preferable")
        lowestRowIndex = [rowIteration for rowIteration, rowRms in rmsRows].index(lowestIteration)
        riseAfterLowestText = RiseOfErrorText(rmsRows[lowestRowIndex + 1:], lowestRms)
        if iteration == lowestIteration:
            if riseAfterLowestText:
                return iteration, (
                    "the lowest RMS reprojection error of the run. Later "
                    "iterations removed more points, and the error rose "
                    "again after iteration {}, {}".format(iteration, riseAfterLowestText))
            return iteration, (
                "the lowest RMS reprojection error of the run. Later "
                "iterations removed more points without reducing the "
                "error further")
        reason = (
            "the earliest iteration capturing {:.0f} % of the largest "
            "reduction of the RMS reprojection error in the run (from "
            "iteration {} to its lowest value, at iteration {}), so later "
            "iterations removed more points for little further gain".format(
                100.0 * RECOMMENDATION_IMPROVEMENT_FRACTION, rmsRows[0][0], lowestIteration))
        riseBeforeLowestText = RiseOfErrorText(rmsRows[rowIndex + 1:lowestRowIndex], rms)
        if riseBeforeLowestText:
            reason += ". Between iterations {} and {} the error rose {}".format(
                iteration, lowestIteration, riseBeforeLowestText)
        if riseAfterLowestText:
            reason += ". The error rose again after iteration {}, {}".format(lowestIteration, riseAfterLowestText)
        return iteration, reason
    return rmsRows[-1][0], "final iteration"


def RiseOfErrorText(laterRmsRows, referenceRms):
    """"to 0.9000 px at iteration 3", the highest RMS reprojection error
    of *laterRmsRows* ((iteration, RMS) pairs) when it exceeds
    *referenceRms*, or an empty text."""
    if not laterRmsRows:
        return ""
    highestIteration, highestRms = max(laterRmsRows, key=SecondItem)
    if highestRms <= referenceRms:
        return ""
    return "to {:.4f} px at iteration {}".format(highestRms, highestIteration)


# ===================================================================
# Simple line-chart widget (QPainter, no matplotlib dependency)
# ===================================================================

# Values from this size on are written with an exponent in the table and on
# the axes, so that no value takes 155 digits.
EXPONENT_FROM = 1e9
# The default smallest range of the values of a graph that is drawn as a
# change, as a share of the largest absolute value: a smaller range, such as
# a mean tie-point sigma that changes by 1e-12 m, is drawn as a flat line.
# The graphs of the control-point and the
# check-point RMSE use MeaningfulRmseDifference instead, and the graphs of
# counts 0, because a count changes only by whole numbers.
MINIMUM_RELATIVE_AXIS_SPAN = 1e-6


# The half range of the value axis of a graph that does not change, shown
# as the change from iteration 0 in % (ShowResultChartsAs): 15 % on either
# side of the flat line, so that the four labels of its axis are whole
# numbers (15, 5, -5, and -15), instead of a range as small as the
# smallest span drawn as a change, such as 0.0001 %.
PERCENT_FLAT_PAD = 15.0


def PlotValueRange(values, minimumSpan=None, flatPad=None):
    """(minimum, maximum) of the value axis of a graph of *values*: their
    range when it is at least *minimumSpan* (by default
    MINIMUM_RELATIVE_AXIS_SPAN of the largest absolute value) and not 0,
    otherwise a range around their middle that draws them as a flat line:
    *flatPad* on either side when it is given (PERCENT_FLAT_PAD for a
    graph of the change from iteration 0 in %), and otherwise 10 % of the
    middle or *minimumSpan* on either side, whichever is larger, and 1
    when both are 0.  Without such a range, a control-point RMSE that
    changes by about 1e-12 m between the iterations would fill the height
    of the graph."""
    low, high = min(values), max(values)
    if minimumSpan is None:
        minimumSpan = MINIMUM_RELATIVE_AXIS_SPAN * max(abs(low), abs(high))
    if high - low > 0.0 and high - low >= minimumSpan:
        return low, high
    middle = 0.5 * (low + high)
    pad = flatPad if flatPad is not None else (max(0.1 * abs(middle), minimumSpan) or 1.0)
    return middle - pad, middle + pad


def FormatAxisTickValue(value, tickInterval):
    """Axis tick label with the number of decimals chosen from the tick
    interval: one decimal more than the first significant decimal of
    the interval, so that neighbouring ticks never print the same text
    and sub-millimetre values never collapse to 0.000.  Intervals of
    two or more get no decimals: ticks that far apart still round to
    different whole numbers, and counts such as the number of tie
    points are not given spurious decimals."""
    if not (tickInterval and math.isfinite(tickInterval) and tickInterval > 0):
        return "{:.4g}".format(value)
    if abs(value) >= EXPONENT_FROM or tickInterval >= EXPONENT_FROM:
        return "{:.4g}".format(value)
    if tickInterval >= 2.0:
        return "{:.0f}".format(value)
    decimals = int(math.ceil(-math.log10(tickInterval))) + 1
    decimals = max(0, min(decimals, 12))
    return "{:.{}f}".format(value, decimals)


def _MapValueToPlotX(marginLeft, plotWidth, xMaximum, xMinimum, valueX):
    return marginLeft + (valueX - xMinimum) / (xMaximum - xMinimum) * plotWidth


def _MapValueToPlotY(marginTop, plotHeight, yMaximum, yMinimum, valueY):
    return (marginTop + plotHeight
            - (valueY - yMinimum) / (yMaximum - yMinimum) * plotHeight)


def _IsPlottedValue(value):
    """True for a value that a graph draws: not None and not NaN."""
    return value is not None and not (isinstance(value, float) and math.isnan(value))


def LineSegmentsOfPresentValues(values):
    """(index, index + 1) of every two neighbouring *values* that both
    have a value, so that a graph leaves a gap for a missing value
    instead of joining the values on either side of it."""
    return [(index, index + 1) for index in range(len(values) - 1)
            if _IsPlottedValue(values[index]) and _IsPlottedValue(values[index + 1])]


class LineChartWidget(QtWidgets.QWidget):
    """Minimal line chart for one metric series over iterations, on an x
    axis of all iterations, also those without a value."""

    # The smallest range drawn as a change (PlotValueRange), None for the
    # default.
    minimumSpan = None
    # The half range of the axis of a flat graph (PlotValueRange), None for
    # the default.
    flatPad = None

    def __init__(self, title, xValues, yValues,
                 colour=QtGui.QColor(46, 84, 150), parent=None):
        super().__init__(parent)
        self.setMinimumSize(230, 160)
        self.title = title
        self.xValues = list(xValues)
        self.yValues = list(yValues)
        self.colour = colour
        self.highlightX = None

    def SetHighlight(self, xValue):
        """Emphasise the marker at the given x (iteration) value."""
        self.highlightX = xValue
        self.update()

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)

        width = self.width()
        height = self.height()
        # Room for a tick label of seven characters.
        marginLeft = 58
        marginRight = 12
        marginTop = 26
        marginBottom = 30
        plotWidth = width - marginLeft - marginRight
        plotHeight = height - marginTop - marginBottom
        if plotWidth < 30 or plotHeight < 30:
            painter.end()
            return

        pairs = [(valueX, valueY) for valueX, valueY in zip(self.xValues, self.yValues)
                 if _IsPlottedValue(valueY)]

        titleFont = QtGui.QFont()
        titleFont.setPointSize(9)
        titleFont.setBold(True)
        painter.setFont(titleFont)
        painter.setPen(QtGui.QPen(QtGui.QColor(0, 0, 0), 1))
        painter.drawText(marginLeft, marginTop - 10, self.title)

        painter.setPen(QtGui.QPen(QtGui.QColor(0, 0, 0), 1))
        painter.drawRect(marginLeft, marginTop, plotWidth, plotHeight)

        if not pairs:
            painter.setPen(QtGui.QPen(QtGui.QColor(120, 120, 120), 1))
            painter.drawText(marginLeft + 8, marginTop + 20, "no data")
            painter.end()
            return

        valuesX = [pair[0] for pair in pairs]
        valuesY = [pair[1] for pair in pairs]
        # The x axis of all iterations, also those without a value.
        xMinimum, xMaximum = min(self.xValues), max(self.xValues)
        yMinimum, yMaximum = PlotValueRange(valuesY, self.minimumSpan, self.flatPad)
        if xMaximum == xMinimum:
            xMaximum = xMinimum + 1

        mapValueToPlotX = functools.partial(_MapValueToPlotX, marginLeft, plotWidth, xMaximum, xMinimum)

        mapValueToPlotY = functools.partial(_MapValueToPlotY, marginTop, plotHeight, yMaximum, yMinimum)

        smallFont = QtGui.QFont()
        smallFont.setPointSize(7)
        painter.setFont(smallFont)
        painter.setPen(QtGui.QPen(
            QtGui.QColor(225, 225, 225), 1, QtCore.Qt.DotLine))
        for gridLineIndex in range(4):
            gridLineY = marginTop + gridLineIndex / 3.0 * plotHeight
            painter.drawLine(int(marginLeft), int(gridLineY),
                             int(marginLeft + plotWidth), int(gridLineY))
        painter.setPen(QtGui.QPen(QtGui.QColor(80, 80, 80), 1))
        tickInterval = (yMaximum - yMinimum) / 3.0
        for gridLineIndex in range(4):
            value = yMaximum - gridLineIndex * tickInterval
            gridLineY = marginTop + gridLineIndex / 3.0 * plotHeight
            painter.drawText(2, int(gridLineY) + 4,
                             FormatAxisTickValue(value, tickInterval))

        for valueX in self.xValues:
            painter.drawText(int(mapValueToPlotX(valueX)) - 4,
                             int(marginTop + plotHeight) + 14,
                             str(int(valueX)))
        painter.drawText(marginLeft, height - 4, "iteration")

        # The points in screen coordinates without rounding, so that values
        # drawn as a flat line (PlotValueRange) do not step by a screen
        # pixel.
        painter.setPen(QtGui.QPen(self.colour, 2))
        for startIndex, endIndex in LineSegmentsOfPresentValues(self.yValues):
            painter.drawLine(
                QtCore.QPointF(mapValueToPlotX(self.xValues[startIndex]), mapValueToPlotY(self.yValues[startIndex])),
                QtCore.QPointF(mapValueToPlotX(self.xValues[endIndex]), mapValueToPlotY(self.yValues[endIndex])))

        painter.setBrush(self.colour)
        for valueX, valueY in zip(valuesX, valuesY):
            painter.drawEllipse(
                QtCore.QPointF(mapValueToPlotX(valueX), mapValueToPlotY(valueY)), 3.0, 3.0)

        # Emphasise the selected iteration's marker.
        if self.highlightX is not None:
            for valueX, valueY in zip(valuesX, valuesY):
                if valueX == self.highlightX:
                    centre = QtCore.QPointF(mapValueToPlotX(valueX), mapValueToPlotY(valueY))
                    painter.setBrush(QtCore.Qt.NoBrush)
                    painter.setPen(QtGui.QPen(
                        QtGui.QColor(20, 120, 40), 2.5))
                    painter.drawEllipse(centre, 7.0, 7.0)
                    painter.setBrush(QtGui.QColor(20, 120, 40))
                    painter.setPen(QtCore.Qt.NoPen)
                    painter.drawEllipse(centre, 3.0, 3.0)
                    break

        painter.end()


# ===================================================================
# About text
# ===================================================================

_ABOUT_HTML = """
<h2>Gradual Selection Filter</h2>
<p><b>Version:</b> 1.0.1 (2026-11-22)<br>
<b>Author:</b> Geert J. Verhoeven
(ORCID: <a href="https://orcid.org/0000-0003-4825-9604">
0000-0003-4825-9604</a>)<br>
based on an original script by Martin Wieser
(ORCID: <a href="https://orcid.org/0009-0005-9870-6494">
0009-0005-9870-6494</a>).<br>
<b>Contact:</b>
<a href="mailto:geert.verhoeven@univie.ac.at">
geert.verhoeven@univie.ac.at</a><br>
<b>Licence:</b> GNU General Public License, version 3 or any later version (GPL-3.0-or-later),
with an additional permission to convey MetaFOCUS combined with Agisoft Metashape and its
Python module (see LICENSE and ADDITIONAL_PERMISSION.txt in the MetaFOCUS ZIP file)</p>

<h3>What this tool does</h3>
<p>This tool automates the iterative &lsquo;gradual selection &rarr; optimise &rarr; repeat&rsquo;
workflow for cleaning the sparse tie-point cloud. In each iteration it removes the worst tie points
by one or more criteria and then re-runs <i>Optimize Cameras</i>. Before it removes any tie point,
it re-adjusts the chunk with the settings of the dialog: this is iteration 0. It records quality
metrics of the chunk as found, of iteration 0, and after every later iteration. Metashape 2.3 offers
gradual selection as <i>Clean Tie Points</i> (Tools &gt; Tie Points), whereas earlier versions offer
it as <i>Gradual Selection</i>. The four criteria below are the criteria of this Metashape dialog.</p>
<p>A disabled image can still be oriented and have 2D tie points of valid 3D tie points or marker
projections. In a check in Metashape 2.3.2, <i>Optimize Cameras</i> adjusted such an image with the
others, also with its reference location disabled. Its 2D tie points entered the reprojection error
of the processing report, and its marker projections alone also changed the adjustment. Each
iteration runs <i>Optimize Cameras</i>, which adjusts such an image as well, but the root mean square
(RMS) reprojection error, the redundancy, and the approximate &sigma;&#8320; of the filter count the
enabled images only.
The question before the run names these images. Enabling them, or resetting their orientation, before
the run removes this difference. The redundancy and &sigma;&#8320; remain the approximations described
below.</p>

<h3>Keypoint size</h3>
<p>Two of the criteria below use the keypoint size. Metashape detects keypoints in an image pyramid,
a series of copies of each image that are blurred more and more and reduced in size. The keypoint
size is the standard deviation (sigma) of the Gaussian blur of the pyramid level at which a keypoint
was found (Metashape manual), in pixels. It therefore describes how coarse the image detail is on
which the keypoint was found. A keypoint on a fine, sharp detail, such as the corner of a printed
letter, has a small size, for example 2 pixels. A keypoint on a broad, soft feature, such as the
edge of a shadow, has a large size, for example 8 pixels, and its position is less precise.
Metashape therefore weights the residual of each 2D tie point by its keypoint size: the a priori
standard deviation of a 2D tie point is the &lsquo;Tie point accuracy&rsquo; of the chunk times its
keypoint size. The chunk information of Metashape gives a mean keypoint size, which its manual
describes as the average over all keypoints. In a check in Metashape 2.3.2, without keypoints kept
after the matching, it agreed to four decimals with the mean keypoint size of the 2D tie points of
the valid 3D tie points.</p>

<h3>The criteria</h3>
<p>The tool offers four criteria:</p>
<ul>
<li><b>Reconstruction Uncertainty:</b> the shape ratio of the error ellipsoid of each 3D tie point
(the ratio of its largest to its smallest semi-axis). It describes the shape of the ellipsoid, not
its size. High values mark 3D tie points whose error ellipsoid is strongly elongated, so that the
imaging geometry constrains their position poorly in one direction (for example through a small
parallax). The Metashape manual says that the ellipsoid corresponds to the uncertainty of the point
triangulation alone, without the propagated uncertainties of the interior and exterior orientation
parameters. The criterion is therefore a different quantity from the formal covariance of the bundle
adjustment behind the mean tie-point &sigma; below.</li>
<li><b>Projection Accuracy:</b> the mean keypoint size of the 2D tie points of each 3D tie point,
which the Metashape manual calls the average image scale used for measuring them. It is a size, not
an accuracy. High values mark 3D tie points whose 2D tie points were detected on coarse (downscaled)
levels of the image pyramid.</li>
<li><b>Reprojection Error:</b> the largest residual in keypoint size units of each 3D tie point.
A residual belongs to a 2D tie point, but the criterion gives one value per 3D tie point, because
gradual selection removes 3D tie points. For each 2D tie point of a 3D tie point, Metashape divides
the length of its residual by its keypoint size, both in pixels. The largest of these quotients is
the value of the 3D tie point (Metashape manual). The value therefore has no unit, and it is a
maximum, not a root mean square. With a target of 0.3, the filter removes every 3D tie point
that has a 2D tie point whose residual exceeds 0.3 times its keypoint size. For a keypoint size of
2 pixels, that is a residual above 0.6 pixels, and for 8 pixels a residual above 2.4 pixels. All
2D tie points of a removed 3D tie point go with it, also those with small residuals. As a maximum,
the value follows the worst 2D tie point of a 3D tie point, such as a single false match. The
target is in the units of the criterion &lsquo;Reprojection error&rsquo; of Metashape&rsquo;s Clean Tie
Points, not in pixels. The column and the graph of its worst value are labelled &lsquo;Maximum reprojection error
(criterion)&rsquo;. The next section compares this criterion with the other reprojection errors of
Metashape and MetaFOCUS.</li>
<li><b>Image Count:</b> the number of images in which the point is measured. Unlike the other three,
larger values are preferred here: more images usually strengthen the determination of a point,
although their geometry matters as much as their number. Its target is a minimum, and points seen in
fewer images are removed. Because a point&rsquo;s image count does not change as other points are
removed or as <i>Optimize Cameras</i> runs again, this is applied <b>once</b> rather than stepped or
taken by percentile.<br>
<b>Note on the convention:</b> here the target is the minimum number of images to <i>keep</i>, so
setting it to &lsquo;&ge; 3 images&rsquo; removes points seen in only 2. This differs from Metashape&rsquo;s
own Clean Tie Points, where the value <i>N</i> removes tie points seen in fewer than <i>N</i>+1
images. Metashape&rsquo;s 2 thus removes points seen in only 2, and its 3 removes points seen in
fewer than 4. The MetaFOCUS target &lsquo;&ge; <i>N</i>&rsquo; is therefore equivalent to Metashape&rsquo;s
value <i>N</i>&minus;1.</li>
</ul>

<h3>Reprojection errors in Metashape and in MetaFOCUS</h3>
<p>Metashape and MetaFOCUS report several reprojection errors. They differ in what they cover (one
3D tie point, one image, one marker, or the whole chunk), in how they combine the residuals, as a
maximum or as a root mean square, and in their unit. A value in keypoint size units divides each
residual by the keypoint size of its 2D tie point and has no unit, whereas a value in pixels does
not divide it. The Metashape manual says that the chunk information and the processing report give
two reprojection errors: one in keypoint size units, which the bundle adjustment minimises, and one
in pixels. It gives no formula for these values, and it does not say how the &lsquo;Max reprojection
error&rsquo; of the chunk information is formed.</p>
<table border="1" cellpadding="4" cellspacing="0">
<tr><th>Where Metashape shows it</th><th>What it is</th><th>Unit</th></tr>
<tr><td>Clean Tie Points, Reprojection error</td><td>Per 3D tie point, the largest residual of its
2D tie points divided by their keypoint size (formula in the manual)</td><td>none</td></tr>
<tr><td>Chunk information, RMS reprojection error, first value</td><td>Over all 2D tie points, the
root mean square of the residuals in keypoint size units, which the bundle adjustment
minimises</td><td>none</td></tr>
<tr><td>Chunk information, RMS reprojection error, value in brackets</td><td>The same in
pixels</td><td>px</td></tr>
<tr><td>Chunk information, Max reprojection error, first value</td><td>Not described in the manual.
If it is the largest residual in keypoint size units of all 2D tie points, it equals the largest
Reprojection error of Clean Tie Points</td><td>none</td></tr>
<tr><td>Chunk information, Max reprojection error, value in brackets</td><td>Not described in the
manual. A largest residual in pixels</td><td>px</td></tr>
<tr><td>Processing report, Reprojection error</td><td>The root mean square of the residuals of all
2D tie points on all images. The report also gives the two values of the chunk
information.</td><td>px</td></tr>
<tr><td>Reference pane, images, Error (pix)</td><td>Per image, the root mean square of the residuals
of its valid 2D tie points</td><td>px</td></tr>
<tr><td>Reference pane, markers, Error (pix)</td><td>Per marker, the root mean square of its
residuals over all images where it is placed</td><td>px</td></tr>
<tr><td>Distortion Plot, Residuals</td><td>Per interior orientation group, the residuals of the 2D
tie points averaged in the cells of a grid. The manual names no weights. The Interior Orientation
Viewer reproduces the plot with the inverse keypoint size as weight.</td><td>px</td></tr>
</table>
<p>MetaFOCUS shows the following reprojection errors.</p>
<table border="1" cellpadding="4" cellspacing="0">
<tr><th>Where MetaFOCUS shows it</th><th>What it is</th><th>Unit</th></tr>
<tr><td>Gradual Selection Filter, Target and Step of Reprojection Error</td><td>Thresholds of the
Reprojection error of Clean Tie Points</td><td>none</td></tr>
<tr><td>Gradual Selection Filter, Maximum reprojection error (criterion)</td><td>The largest
Reprojection error of Clean Tie Points of the valid 3D tie points</td><td>none</td></tr>
<tr><td>Gradual Selection Filter, RMS reprojection error (px), and Stop when RMS reprojection error
&le; X px</td><td>The root mean square of the residuals of the 2D tie points of the valid 3D tie
points on the enabled, oriented images whose residuals can be evaluated. Without a disabled image that
is still oriented and has 2D tie points, the value is meant to match the value in brackets after
&lsquo;RMS reprojection error&rsquo; in the chunk information.</td><td>px</td></tr>
<tr><td>Gradual Selection Filter, largest and smallest per-image RMS reprojection error</td><td>Per
image, the same root mean square, like Error (pix) of the images in the Reference
pane</td><td>px</td></tr>
<tr><td>Gradual Selection Filter, approximate &sigma;&#8320;</td><td>The residuals divided by
&lsquo;Tie point accuracy&rsquo; times their keypoint size, combined with the redundancy (see
Tracked metrics)</td><td>none</td></tr>
<tr><td>Image Parameter Exporter, Reprojection Error RMS (px) and Reprojection Error Maximum
(px)</td><td>Per image, the root mean square and the largest residual of its valid 2D tie
points</td><td>px</td></tr>
<tr><td>Interior Orientation Viewer, Residuals</td><td>The residual vectors of the 2D tie points,
as cell means with or without the weights of the Distortion Plot</td><td>px</td></tr>
<tr><td>Marker Residual Inspector</td><td>The residual of each marker projection, and per marker
their root mean square, like Error (pix) of the markers in the Reference pane</td><td>px</td></tr>
</table>
<p>A target of the Reprojection Error criterion, such as 0.3, therefore corresponds to no fixed
number of pixels. The same value is a fraction of a pixel for a 2D tie point with a small keypoint
size and more than a pixel for one with a large keypoint size. If the aim is to keep the overall RMS
reprojection error below one pixel, the criterion should not be converted. The <i>RMS reprojection
error (px)</i> column of the results should be read instead, or <b>Stop when RMS reprojection error
&le; X px</b> used. Filtering on the Reprojection Error criterion removes the 3D tie points with the
worst single 2D tie points, whereas the RMS in pixels describes all 2D tie points together.</p>

<h3>Removal modes</h3>
<p><b>Absolute threshold: step down to a target</b> (recommended). For each of the first three
criteria, the dialog takes a <i>target</i> (the value that the worst point should reach) and a <i>step</i>
(how far the threshold descends in each iteration). In every iteration the tool lowers the working
threshold by one step from the current worst value (never below the target), removes the points
above it, and re-optimises. For example, take a Reconstruction Uncertainty target of 10 and a step
of 10. A cloud whose worst value is 105 is filtered to 95, then 85, then 75, and so on, with a
re-optimisation at each stage, until the target of 10 is reached. This limits how far the threshold
drops in each iteration, which is the point of gradual selection. How many points a step removes,
however, depends on how their values are distributed. The counts of removed points and the safety
cap should therefore be watched. Setting the step larger than the gap between the worst value and
the target removes everything above the target in one go.</p>
<p><b>Percentile: remove a fixed fraction in each iteration.</b> In each iteration the tool removes
the chosen percentage of the worst points <i>by each enabled criterion in turn</i> and re-optimises
afterwards. With two criteria enabled at 10&nbsp;%, 10&nbsp;% is therefore removed by the first and
then 10&nbsp;% of the survivors by the second. The percentage refers to the valid tie points with a
valid value of the criterion at that moment. The number of points is rounded to the nearest whole
point and at least one: 10&nbsp;% of 100,000 points removes 10,000, and 10&nbsp;% of 10 points
removes 1. The points with the worst values are taken first, and points with equal values in the
order of the tie points, so tied values never stall the removal. A point that already meets the
target is never removed, so fewer points are removed when fewer lie above the target, and a
criterion that has already reached its target is left alone. The run stops once every enabled
criterion is at or below its target (or at the iteration limit or the safety cap). Percentile mode
is quick and useful for exploratory trimming or when no defensible absolute targets are available,
but it does not aim at a defined quality level. For principled filtering the absolute-threshold mode
is usually the better choice. (Image Count, being applied once, removes every point below its
minimum in either mode.)</p>

<h3>Targets and stopping</h3>
<p>In both modes the per-criterion <i>target</i> acts as the
stopping value.  A run continues while <b>any</b> enabled
criterion is still above its target (or, for Image Count, below its
minimum), and stops only once <b>all</b> of them are satisfied.  A
criterion that has already reached its target is left alone
in subsequent iterations while the others continue to be filtered.
The <i>step</i> column is used only in absolute mode.</p>
<p>Setting <b>Maximum iterations</b> to <b>0</b> (shown as &lsquo;No limit&rsquo;) keeps the run
iterating until all targets are reached, up to a safety ceiling of 200 iterations. In percentile
mode such a run also <b>auto-stops</b> when the RMS reprojection error stops improving (by less than
0.05&nbsp;% in two consecutive iterations). Further percentile steps would then remove points for no
gain, although the targets are not yet reached. The stepped absolute mode is never stopped this way,
because its small steps can barely move the RMS while the targets are still being approached. The
auto-stop is based on the RMS reprojection error because it is the one quality measure that is
always available (control points and tie-point covariances are optional) and that generally
decreases as points are removed.</p>
<p>Ticking <b>Stop when RMS reprojection error &le; X px</b> also stops the run as soon as the
overall RMS reprojection error in pixels reaches the chosen target. This value covers the 2D tie
points of the valid 3D tie points on the enabled, oriented images whose residuals can be evaluated.
Unless a disabled image is still oriented and has 2D tie points (see above), it is meant to match the
value in brackets after &lsquo;RMS reprojection error&rsquo; in the chunk information of Metashape.
The value is not the Reprojection Error criterion.
This additional stopping condition is layered on top of the per-criterion targets, the iteration
limit, and the safety cap. It is useful when the only goal is to &lsquo;keep the RMS below one pixel&rsquo;.
Because the reprojection-error <i>criterion</i> is unitless (see above), this pixel-based stop is
the straightforward way to control the result in pixels. At least one criterion (or Image Count)
must still be enabled to drive the removal.</p>

<h3>Safety cap</h3>
<p>A global cap limits the total fraction of points that may be removed across all iterations. The
number of points that may be removed is rounded down to a whole point, so a run never removes more
than the chosen percentage (with 11 points and a cap of 50&nbsp;%, at most 5). If a removal would
push the cloud below the resulting floor, that removal is skipped (the points it selected are
deselected again) and the run stops, protecting the cloud from over-thinning. If the run stops at
the cap almost immediately in absolute mode, a smaller step should be used, so that each iteration
removes less.</p>

<h3>Stop, Cancel, and saving</h3>
<p>While a run is active, the settings, <b>About</b>, <b>Run</b>,
and <b>Close</b> are disabled, and Escape and the close button of
the window are ignored.  <b>Stop</b> ends the run after the current
iteration.  The iterations completed so far are kept and shown in
the results, and the reason for the end of the run says
&ldquo;Stopped after <i>N</i> of <i>M</i> iterations&rdquo;.</p>
<p><b>Cancel</b> in a Metashape progress window during the run (for example during <i>Optimize
Cameras</i>) also ends the run. So does an error in any step of an iteration (for example in <i>Optimize
Cameras</i> or in the copy of the chunk) or in the saving of the project. The results then show the
completed iterations, with a red line that names the operation, the iteration, and the reason. When
iterations are kept as separate chunks, the unfinished copy of the interrupted iteration is removed
and the chunk of the last completed iteration (or the original chunk) becomes active again. If the
copy cannot be removed, the red line says so, and the copy can be deleted by hand in the Workspace
pane. When the run works in place and the interrupted iteration had already changed the chunk, the
red line says what the chunk now holds (for example tie points removed without re-optimisation). In
that case, every row of the results is marked as not kept, because none of them describes the chunk
any more. If the quality metrics of the chunk as found cannot be computed, the run does not start, a
message says why, and the chunk is not changed.</p>
<p><b>Save project after each iteration</b> needs a project that has been saved before: in a project
that was never saved, the run is refused until the project is saved or the option is unticked. The
project is also saved after iteration 0. If saving fails or is cancelled, the run stops after the
completed iteration, which then has not been saved.</p>

<h3>Optimize Cameras parameters</h3>
<p>The interior orientation parameters selected here are adjusted in iteration 0 and during the
optimisation after each filtering step. These match the check boxes of the same names in Metashape&rsquo;s
<i>Optimize Cameras</i> dialog, as do <i>Adaptive camera model fitting</i> and <i>Estimate tie point
covariance</i>. The dialog&rsquo;s <i>Fit additional corrections</i> is not offered: the tool does
not pass it, so Metashape&rsquo;s default for scripts applies.</p>

<h3>Tracked metrics and choosing an iteration</h3>
<p>For the chunk as found, for iteration 0, and after each later iteration, the tool records the
valid tie-point count, the overall RMS reprojection error, and the largest and smallest per-image
RMS reprojection error. It also records the worst value of each criterion (the largest
Reconstruction Uncertainty, Projection Accuracy, and Reprojection Error, and the smallest Image
Count). When tie-point covariance is estimated, it records the mean and the median tie-point
positional standard deviation. In addition, it records the approximate &sigma;&#8320; and the
largest movement of an image and of a marker since the previous iteration (for iteration 0, since
the chunk as found). When measured markers have a reference location, it records the control-point
root mean square error (RMSE) and the check-point RMSE as well. Control points are markers with a
ticked reference, whereas check points are markers with a reference location and an unticked
reference. A marker counts only when Metashape has estimated its position. The inspection markers that
the GSD Value Exporter recorded as its own in their metadata, whose reference coordinates come from the
surface model, count as neither.
Both RMSE values are the root mean square of the 3D distances between the estimated and the
reference positions. They are evaluated in a local east-north-up coordinate reference system (CRS),
so they are metric for both projected and geographic CRSs.</p>
<p>The results dialog shows these metrics as a table and as line graphs over the iterations. The
table starts with the chunk as found, labelled &lsquo;As found&rsquo; in its &lsquo;Iteration&rsquo;
column. A star marks the suggested state in the table, in the banner above it, and in the list of
iterations below the graphs. A coloured square in the header of a column has the colour of its
graph. The graphs show iterations 0 to <i>N</i>, without the chunk as found, which only the table
shows. A switch above the graphs shows them in values or as the change from iteration 0 in percent:
100&nbsp;&times;&nbsp;(value&nbsp;&minus;&nbsp;value at iteration 0)&nbsp;/&nbsp;value at iteration
0. A graph without a finite, non-zero value at iteration 0 then stays in values, and its title says
so. Shown as the change from iteration 0, a graph whose values do not change is drawn on an axis
from &minus;15&nbsp;% to +15&nbsp;%. The table always shows values.</p>
<p>The axis labels of the graphs get as many decimals as the spacing of their ticks needs (none when
the ticks are two or more apart, as for counts). A graph draws values that differ by less than a
millionth of their largest absolute value as a flat line. This prevents a change of about 10<sup>&minus;12</sup>&nbsp;m,
as rounding can give, from filling the height of the graph. The graphs of counts show every change,
and the graph of the control-point or the check-point RMSE is flat when its values differ by less
than 0.1&nbsp;% of the smallest value (see below). A graph keeps the iterations without a value on
its axis and leaves a gap for them. The table and the axes write values from 10<sup>9</sup> on with
an exponent, and the table writes the movements with three significant digits. The table and the
Console write the control-point and the check-point RMSE and the mean and the median tie-point
&sigma; with four significant digits. Values below 10<sup>&minus;4</sup> are written in exponent
notation, so that an RMSE of 2&nbsp;&micro;m reads 2.000e-06&nbsp;m. Selecting an iteration
highlights its row and its point on every graph. All of this is also written to the Console pane.</p>
<p><b>The chunk as found and iteration 0.</b> The chunk as found keeps the adjustment of its last
<i>Optimize Cameras</i> (or <i>Align Photos</i>), which may have used settings other than those of
the dialog. Its metrics can then differ from those of the iterations because of the adjustment, not
because of the filtering. Iteration 0 therefore re-adjusts the chunk with the settings of the dialog
before any tie point is removed, so that every iteration is adjusted with the same settings. When
the iterations are kept as separate chunks, iteration 0 is a copy of the chunk, and the chunk itself
stays as found. When the run works in place, the chunk itself is re-adjusted, so that the state as
found remains only in the results and the Console. After the metrics of iteration 0, the Console
compares the two adjustments. It gives the sigma nought of Metashape of both. It also gives the
fitted parameters and the adaptive camera model fitting of the last <i>Optimize Cameras</i> of the
chunk as found (from the chunk metadata) and those of the filter. It says when a value is not in the
metadata, or when the <i>Optimize Cameras</i> of iteration 0 wrote none. The comparison flags a
difference when the sigma nought of Metashape differs by more than a factor of 2 between the two
adjustments, or when their fitted parameters or their adaptive camera model fitting differ.
Iteration 0 can also change the results of the adjustment when the comparison flags no difference.
This can happen when anything else that the adjustment uses differs from what the last <i>Optimize
Cameras</i> of the chunk as found used. Examples are removed 3D tie points, a control point that
became a check point, and changed a priori standard deviations. It can also happen when the last
adjustment of the chunk as found had not converged. A change from the chunk as found to iteration 0
comes from the settings of the dialog alone only when neither is the case. Likewise, a change from
one iteration to the next comes from the removed 3D tie points alone only when the adjustment of the
iteration before had converged.</p>
<p>The worst value of a criterion is taken over the valid tie points only. It leaves out a
placeholder of about 1.34&nbsp;&times;&nbsp;10<sup>154</sup>, the square root of the largest
double-precision number. Metashape can return it as the Reprojection Error of a valid tie point. The
placeholder also takes no part in the test of the target. In an iteration that filters by that
criterion, a percentile selects such a tie point first, because its value is the largest. When no
other tie point lies above the target of that criterion, however, the run can end with such tie
points left. The Console says how many tie points carry the placeholder, and how many tie points
share a worst value, for example a Reconstruction Uncertainty of exactly 1000. Metashape appears to
cap it at 1000. When the worst Reconstruction Uncertainty is 1000, the Console says so.</p>
<p>The <b>largest movement</b> of an image is the largest distance
between the positions of the perspective centre of an enabled, oriented
image in two consecutive iterations.  The largest movement of a marker is
the same for the estimated marker positions.  Both are in metres when the
chunk is scaled and in chunk units otherwise.  Iteration 0 is a new
adjustment, so it can move images: its movements are measured from the
chunk as found.  They show whether
<i>Optimize Cameras</i> moved anything, for example when the
control-point RMSE does not change, and the results dialog gives the
largest of them under the table.</p>
<p>An iteration can also cost orientations. When all tie points of an image are removed, the next
<i>Optimize Cameras</i> no longer orients it (Metashape 2.3.2, in the author&rsquo;s projects, where
Metashape also says so). The last column of the table, <i>Images no longer oriented</i>, counts for
every iteration the images that were enabled and oriented before it and are no longer oriented after
it. For iteration 0, they are counted from the chunk as found. The Console names them all, whereas
the tooltip of the count and a line under the table name up to ten per iteration and count the rest.
The largest movement leaves such an image out, because it compares only the images that have a
position in both iterations.</p>
<p>The tracked metrics are additional diagnostics, not independent
measures of quality.  The RMS reprojection errors, the worst criterion
values, the mean tie-point &sigma;, and &sigma;&#8320; come from the
same adjustment as the filtering itself, and their observations and
points overlap with those that the filtering uses.</p>
<p>Removing points usually lowers the reported reprojection error, so a falling error is <i>not</i>
by itself proof of a better-estimated geometry. Removing points by a threshold is not a statistical
outlier test. Yet even the rejection of outliers by such a test biases the estimated standard
deviations downwards, so that the precision looks better than it is, unless the bias is corrected
(ISO 5725-2:2025, 8.3.7, Note). Instead, the point to watch for is the one at which the check-point
RMSE stops improving: the reference coordinates of the check points take no part in the adjustment,
although their image measurements do. For any one iteration, their RMSE therefore checks the
estimated geometry against reference coordinates that took no part in it, and it can rise again when
the cloud is over-thinned. Once an iteration is chosen by its check-point RMSE, however, the check
points have become validation data for that choice. The lowest value of a run tends to be optimistic
for the chosen iteration, so an independent final assessment needs further check points that played
no part in the choice. The check-point RMSE also contains the uncertainty of the reference
coordinates themselves. It should therefore be compared with the RMSE that their a priori standard
deviations alone give, which the results dialog shows (see below). The control-point RMSE is a fit
statistic, because the reference coordinates of the control points are observations of the
adjustment. The iteration where the metrics stabilise should be preferred to the most aggressively
filtered one.</p>
<p>The <b>mean tie-point &sigma;</b> is the average, over the valid tie points with a covariance, of
the total positional standard deviation &radic;(trace&nbsp;of&nbsp;the&nbsp;point&rsquo;s&nbsp;covariance),
multiplied by the chunk scale. It is in metres when the chunk is scaled and in chunk units
otherwise, as the column and the graph say. The total positional standard deviation of a point (the
square root of the sum of its three variances) is the root mean square length of the centred error
vector, not the standard deviation of that length. Each point&rsquo;s covariance comes from the
bundle adjustment (so it is only available when &lsquo;Estimate tie point covariance&rsquo; is on),
and it describes how well that point&rsquo;s 3D position is determined by the geometry. It is a
different quantity from the reprojection error: the reprojection error measures how well the points
<i>fit</i> the images, whereas the mean tie-point &sigma; measures how <i>well-determined</i> the 3D
tie points are. Removing weakly constrained points usually lowers it. If the removal continues,
however, the network supporting the survivors weakens, and the mean &sigma; can start to <b>rise
again</b>. This rise is a useful sign of over-filtering even while the reprojection error keeps
falling. Because the mean is taken over the points that remain, it can also fall merely because the
points with the largest &sigma; were removed, without any improvement of the points that remain. In
short, a rising tie-point &sigma; is often the earliest hint that enough points have been removed,
whereas a falling one is not by itself proof of better-determined points.</p>
<p>The <b>median tie-point &sigma;</b> is given beside the mean, because a few poorly determined tie
points can govern the mean. A covariance matrix of zeros takes part in neither. Metashape stores one
for every 3D tie point after <i>Align Photos</i> and when the last <i>Optimize Cameras</i> did not
estimate the tie-point covariance. The table then shows a dash, with the reason in its tooltip.</p>
<p><b>Is this sigma nought?</b> No. Sigma nought (&sigma;&#8320;, the square root of the reference
variance, also called the a posteriori standard deviation of unit weight) is a single global number
for the whole adjustment. Broadly, it expresses whether the residuals match the a priori standard
deviations of the observations, and it is computed from the weighted residuals and the redundancy.
The mean tie-point &sigma; reported here is instead the average <i>positional</i> standard deviation
of the 3D tie points, in object units. The two are related: in a least-squares adjustment, the a
posteriori covariances are scaled by &sigma;&#8320;&sup2;. Checks support that Metashape scales its
tie-point covariances in this way, as the Tie Point Covariance Exporter section describes. However,
&sigma;&#8320;, computed from residuals divided by their a priori standard deviations, is a
dimensionless overall fit-to-weights factor, whereas the mean tie-point &sigma; is a 3D positional
uncertainty of the points.</p>
<p>The tool reports an <b>approximate &sigma;&#8320;</b> as a tracked metric. It is computed from
the 2D tie points as &radic;( &Sigma;(residual / (tie-point accuracy &times; keypoint size))&sup2; /
redundancy ). The tie-point accuracy is the chunk&rsquo;s &lsquo;Tie point accuracy&rsquo; setting,
read as the a priori standard deviation of each image coordinate of a 2D tie point at keypoint size
1 (Metashape scales it with the keypoint size). Only the tie points in the adjustment enter it: the
valid tie points seen on at least two enabled, oriented images, with their 2D tie points on such
images. The redundancy is the number of scalar observations minus the number of unknowns plus the
datum defect. The number of scalar observations is 2&nbsp;&times;&nbsp;(2D tie points of these tie
points). The number of unknowns is 3&nbsp;&times;&nbsp;(these tie points) + 6&nbsp;&times;&nbsp;(camera
stations with an image that has such 2D tie points) + (adjusted interior orientation parameters of
the interior orientation groups with such an image). The rolling-shutter unknowns add 2 per such
image with the Regularized model and 6 with the Full model. The datum defect is that of a free
network, 7.</p>
<p>When no 2D tie point is left out, the redundancy is the tie-point redundancy, or degrees of
freedom (DoF), that the Interior Orientation Viewer reports as &lsquo;Estimated DoF (tie points)&rsquo;.
The viewer, too, leaves out the unknowns of the camera stations, groups, and images without 2D tie
points in the adjustment, which no observation of the count involves. Like that value, it is an
estimate from counts. It assumes one connected block of images, no rank deficiency beyond the datum
defect, and no further constraints. Every residual is computed with the camera model of its image
(Metashape&rsquo;s Camera.error, which also applies the rolling-shutter motion of the image), as in
the Interior Orientation Viewer.</p>
<p>A 2D tie point whose residual cannot be evaluated, or whose keypoint size is missing, is left out
of the sum. It is left out of the redundancy as well, because a sum over fewer 2D tie points than
the redundancy counts would understate &sigma;&#8320;. A 3D tie point left with fewer than two 2D
tie points leaves the network with its unknowns. An enabled, oriented image without 2D tie points in
the sum (because they were left out or because it had none in the adjustment) leaves the network
with its rolling-shutter unknowns. The exterior orientation unknowns of its camera station leave
with it when no image of that station keeps 2D tie points in the sum. An interior orientation group
none of whose images keeps 2D tie points in the sum leaves the network with its interior orientation
parameters. The Console and the tooltip of the table say how many 2D tie points are left out and
why, and how many images leave the network.</p>
<p>The approximate &sigma;&#8320; is not given when no 3D tie point is seen on two or more enabled,
oriented images, when every 2D tie point is left out, or when the estimated redundancy is not
positive. The table then shows a dash, with the reason in its tooltip. The RMS reprojection error
and the per-image values, by contrast, use every 2D tie point of a valid tie point on an enabled,
oriented image whose residual can be evaluated. They thus stay comparable with Metashape&rsquo;s chunk information. Disabled images
and the keyframes of an animation track take no part in either.</p>
<p>The &sigma;&#8320; is labelled <i>approximate</i> because it ignores any control,
reference-distance (Metashape scale bar), and prior observations. Metashape stores its own &sigma;&#8320;
in the chunk metadata after Optimize Cameras (OptimizeCameras/sigma0), but does not document which
observations and redundancy it uses. The table gives it beside the approximation as &sigma;&#8320;
(Metashape), and the Console as sigma nought of Metashape. It is read after each Optimize Cameras of
the filter, iteration 0 included. For the chunk as found, it is read from its last adjustment,
possibly Align Photos, which writes it as well. Before each Optimize Cameras of the filter,
MetaFOCUS removes the stored value, so that a value read afterwards was written by that adjustment,
even when it equals the earlier one. When Optimize Cameras writes none, the earlier value is put
back and not shown. When the stored value cannot be removed, a value equal to it is not shown,
because it may belong to an earlier adjustment.</p>
<p>The two values need not agree. With control or prior observations, the residuals come from that
constrained adjustment, whereas the redundancy is that of the tie-point network alone. The
approximation is then a descriptive indicator rather than the estimated reference standard deviation
of the whole adjustment. Filtering by residuals also selects the observations that enter it, which
tends to lower it. A value near 1 shows agreement with the assumed a priori standard deviations only
under these conditions. It is not a measure of 3D accuracy. Lowering it, or bringing it to 1 by
changing the &lsquo;Tie point accuracy&rsquo;, does not by itself improve the geometry. It should be
treated as a consistent indicator of how well the residuals match the assumed standard deviations of
the observations (and of how that changes during filtering), not as an exact figure to compare
against another package.</p>
<p>The results dialog highlights a <b>suggested iteration</b>, which can also be the chunk as found.
The candidates are the chunk as found, iteration 0, and every later iteration. When at least two
states have a check-point RMSE, the check points decide. The default is the chunk as found, or
iteration 0 when the chunk as found has no check-point RMSE. Another state is suggested only when it
is significantly better than the default. Of these states, the earliest whose check-point RMSE is
within 0.1&nbsp;% of the lowest is suggested. When their check points differ from those of the
default, the RMSE is taken over the check points of the default. Each check point that a state lacks
is then counted at its error in the default. Without a significantly better state, the default is
suggested, and the suggestion says so and gives the smallest p-value and its state.</p>
<p>A state is significantly better when two conditions hold. First, its check-point RMSE is lower
than that of the default by at least 0.1&nbsp;% of the lower value and by more than zero. A test of
more than zero alone would let rounding decide. Second, a one-sided Wilcoxon signed-rank test
(Wilcoxon, 1945) shows at the level of 5&nbsp;% that its errors are smaller. The test needs no
normal distribution. It pairs each check point with itself in the two states. When the two states do
not hold the same check points, both conditions use only those that they share, and the suggestion
says so. For each check point, the test takes the difference of the squared 3D errors, divided by
the sum of the a priori variances of its reference coordinates. These variances are the squares of
the a priori standard deviations that Metashape&rsquo;s &lsquo;Accuracy&rsquo; fields hold. When a
check point has no positive a priori standard deviation, the differences are not divided, and the
suggestion says so. Differences of zero are left out, and tied differences get their average rank.
Up to 100 differences, the p-value is exact. Above that, it comes from the normal approximation with
the correction for ties, and the suggestion says so. A p-value below 10<sup>&minus;300</sup> is given as
less than 1e-300. With fewer than five check points, no p-value can be below 5&nbsp;%, and the
suggestion says so.</p>
<p>Each state is compared with the default on its own, without a correction for the number of states
compared. Choosing a state in this way uses the check points, so for the suggested iteration they
are no longer an independent check (see above), and the suggestion says so. The RMSE also weights
all coordinate differences equally and ignores their correlations, so a lower RMSE alone does not
show a better result (F&ouml;rstner and Wrobel, 2016). When the default is the chunk as found and
the comparison flags a difference between the two adjustments (see above), the suggestion adds that
a difference may come from the adjustment rather than from the filtering.</p>
<p>When fewer than two states have a check-point RMSE, but at least two have a control-point RMSE,
the control points decide in the same way. The suggestion then says that the control-point RMSE is a
fit statistic to weigh against the other metrics. It also says whether there are no check points or
only one state has a check-point RMSE. Otherwise, the suggested iteration is the earliest iteration
that captures most (95&nbsp;%) of the largest reduction of the RMS reprojection error in the run.
That reduction runs from iteration 0 (the chunk re-adjusted before any tie point is removed) to the
lowest RMS reprojection error of the run. The chunk as found takes no part in this rule, because its
RMS reprojection error comes from another adjustment. The rule is a heuristic, not a measure of the
curvature of the curve, so that few points are removed for most of the benefit. When the error rises
again after its lowest value, or between the suggested iteration and its lowest value, the
suggestion names the highest error of that rise. If a run is cut short (for instance by the safety
cap) while the error is still falling, the suggestion can land on that last iteration. In that case,
the user should weigh it against the tie-point &sigma; and the point count. The suggestion is a
starting point, not a substitute for inspecting the table and graphs.</p>
<p>Under the suggestion, the results dialog gives the RMSE that the a
priori standard deviations of the check points alone give:
&radic;(mean over the check points of
&sigma;<sub>X</sub>&sup2;&nbsp;+&nbsp;&sigma;<sub>Y</sub>&sup2;&nbsp;+&nbsp;&sigma;<sub>Z</sub>&sup2;).
It is the RMSE to expect if the check points were as accurate as their
a priori standard deviations say and the 3D model had no error.  It
does not depend on the iteration, and the Console gives it at the
start of the run.</p>

<h3>Preserving iterations</h3>
<p>If &lsquo;Keep each iteration as a separate chunk&rsquo; is ticked, every iteration is performed
on its own copy (named &hellip;_iteration-00, _iteration-01, &hellip;), so that all intermediate
states are preserved. Iteration 0 copies the chunk, which stays as found, and every later iteration
copies the one before it. A copy of a later iteration is made only when it has points to remove, and
a copy whose removal the safety cap blocks is deleted again. From the results dialog, any iteration
or the chunk as found can then be activated, or the iteration chunks that are not to be kept can be
deleted. The chunk itself is never deleted. Note that copying duplicates the entire chunk, including
any triangular mesh or dense point cloud, which can be slow and disk-intensive.</p>
<p>Without this option, the run filters the active chunk in place, so only the final state exists
afterwards. The results mark every earlier state as not kept, including the chunk as found. After a
run, the dialog shows the chunk that is active then.</p>

<h3>A note of caution</h3>
<p>Aggressive gradual selection can remove genuine, well-measured points and bias the solution.
Filtering should therefore be conservative, preferably in the absolute-threshold mode with sensible
targets, and the tracked metrics should be inspected before a result is accepted.</p>

<h3>References</h3>
<p>Agisoft LLC (2026). <i>Agisoft Metashape User Manual: Professional
Edition, Version 2.3.</i>
<a href="https://www.agisoft.com/pdf/metashape-pro_2_3_en.pdf">
https://www.agisoft.com/pdf/metashape-pro_2_3_en.pdf</a></p>
<p>F&ouml;rstner, W. and Wrobel, B.P. (2016). <i>Photogrammetric
Computer Vision: Statistics, Geometry, Orientation and
Reconstruction</i>. Geometry and Computing 11. Springer, Cham.
<a href="https://doi.org/10.1007/978-3-319-11550-4">doi:10.1007/978-3-319-11550-4</a></p>
<p>International Organization for Standardization (2025). <i>ISO
5725-2:2025, Accuracy (trueness and precision) of measurement methods
and results, Part 2: Basic method for the determination of
repeatability and reproducibility of a standard measurement
method.</i> ISO, Geneva.</p>
<p>Wilcoxon, F. (1945). Individual comparisons by ranking methods.
<i>Biometrics Bulletin</i> 1(6), 80&ndash;83.
<a href="https://doi.org/10.2307/3001968">doi:10.2307/3001968</a></p>
"""


# ===================================================================
# Main dialog
# ===================================================================

def _CollectIterationMetrics(tiePointAccuracy, chunk):
    return CollectMetrics(chunk, tiePointAccuracy)


def DescribeCriterionCount(criteriaCount):
    """"1 criterion" or "N criteria", for the confirmation message."""
    return FormatCountWithNoun(criteriaCount, "criterion", "criteria")


def DescribeCriteria(enabledCriteria, isPercentile, imageCountEnabled,
                     minimumImages):
    """Console description of the enabled criteria with their targets
    (and steps in absolute mode), Image Count included."""
    descriptions = []
    for criterionName, criterionEnum, targetValue, stepValue in enabledCriteria:
        if isPercentile:
            descriptions.append("{} (target {:g})".format(
                criterionName, targetValue))
        else:
            descriptions.append("{} (target {:g}, step {:g})".format(
                criterionName, targetValue, stepValue))
    if imageCountEnabled:
        descriptions.append(
            "Image Count (at least {} images)".format(minimumImages))
    return ", ".join(descriptions)


def DescribeStoppedRun(completedIterationCount, iterationCount,
                       isUnlimited, cause):
    """Reason for a run that ended early (Stop, or Cancel or an error in
    a Metashape operation): "stopped after N of M iterations (cause)",
    or "stopped after N iterations (cause)" in unlimited mode."""
    if isUnlimited:
        progress = FormatCountWithNoun(
            completedIterationCount, "iteration", "iterations")
    else:
        progress = "{} of {}".format(
            completedIterationCount,
            FormatCountWithNoun(iterationCount, "iteration", "iterations"))
    return "stopped after {} ({})".format(progress, cause)


def NotKeptNoteText(suggestedIteration):
    """The sentence of the suggestion of an in-place run on a state that the
    chunk no longer holds: an iteration, or the chunk as found, which
    iteration 0 re-adjusted in place."""
    if suggestedIteration == STATE_AS_FOUND:
        return (" This state was not kept, because the filter re-adjusted the chunk in place. With \u201cKeep each "
                "iteration as a separate chunk\u201d, the chunk itself stays as found.")
    return (" This state was not kept, because the filter ran in "
            "place: rerun with \u201cKeep each iteration as a "
            "separate chunk\u201d to obtain it.")


def RunPlanText(keepIterations):
    """The end of the confirmation question before a run: the chunk, or its
    copy when the iterations are kept, is first re-adjusted with the
    Optimize Cameras settings of the dialog (iteration 0), and where the
    iterations go."""
    if keepIterations:
        return ("First, a copy of the chunk is re-adjusted with these Optimize Cameras settings (iteration 0). "
                "Each iteration will be kept as a separate chunk, and the chunk itself stays as found.")
    return ("First, the chunk is re-adjusted with these Optimize Cameras settings (iteration 0). "
            "Filtering will then modify the active chunk in place.")


def FormatIterationChunkLabel(row, isFinalRow, keepIterations):
    """Chunk label shown for a results row.  After an in-place run the
    chunk holds only the final state, so every earlier row is marked
    as not kept."""
    if keepIterations or isFinalRow:
        return row["label"]
    return "{}, not kept".format(row["label"])


def FormatChunkInformation(chunk):
    """Rich text naming the active chunk and its tie-point count, for
    the dialog, and that count (0 without a chunk or tie points)."""
    if chunk and chunk.tie_points:
        pointCount = len(chunk.tie_points.points)
    else:
        pointCount = 0
    text = ("Active chunk: <b>{}</b><br>"
            "Tie points: <b>{:,}</b>".format(
                html.escape(chunk.label, quote=False) if chunk else "(none)", pointCount))
    return text, pointCount


def IsNeverSavedDocument(document):
    """True when the project has never been saved (Document.path is
    empty), so that Document.save() has no file to write to."""
    return hasattr(document, "path") and not document.path


def _RemoveChunksAddedSince(document, knownChunkKeys):
    """Remove the chunks that are not in *knownChunkKeys* (an unfinished
    copy left behind by an interrupted Chunk.copy) and return how many
    were removed.  Without known keys nothing is removed: the chunk
    being filtered always exists, so an empty set means that the keys
    were never read."""
    if not knownChunkKeys:
        return 0
    addedChunks = [candidate for candidate in document.chunks
                   if candidate.key not in knownChunkKeys]
    if addedChunks:
        document.remove(addedChunks)
    return len(addedChunks)


def _DiscardUnfinishedIterationChunk(document, previousChunk,
                                     unfinishedChunk, knownChunkKeys,
                                     iteration):
    """Make *previousChunk* active again and remove the copy of an
    interrupted iteration: *unfinishedChunk* when the copy was made, or
    otherwise any chunk added since *knownChunkKeys* (Chunk.copy itself
    was interrupted).  Returns the sentence for the results.  A failed
    removal is reported in that sentence and in the Console instead of
    being raised, so that the results of the completed iterations are
    still shown."""
    try:
        document.chunk = previousChunk
        if unfinishedChunk is not None:
            document.remove([unfinishedChunk])
            return (" The unfinished copy of iteration {} was "
                    "removed.".format(iteration))
        if _RemoveChunksAddedSince(document, knownChunkKeys):
            return " The incomplete copy was removed."
        return ""
    except Exception as cleanupError:
        ReportSuppressedError("gradualSelectionFilter._DiscardUnfinishedIterationChunk", cleanupError)
        return (" The unfinished copy of iteration {} could not be removed "
                "({}), so delete it in the Workspace pane.".format(
                    iteration, DescribeException(cleanupError)))


def _FindChunkAddedSince(document, knownChunkKeys):
    """The last chunk that is not in *knownChunkKeys*, or None."""
    addedChunks = [candidate for candidate in document.chunks
                   if candidate.key not in knownChunkKeys]
    return addedChunks[-1] if addedChunks else None


SIGMA_NOUGHT_HEADER = "\u03c3\u2080 (approximate)"
# Metashape's own sigma nought beside it (Geert, 13 October 2026).
METASHAPE_SIGMA_NOUGHT_HEADER = "\u03c3\u2080 (Metashape)"


def TiePointStandardDeviationHeader(statisticName, lengthUnit):
    """"Mean tie-point \u03c3 (m)" or "Median tie-point \u03c3 (m)"."""
    return "{} tie-point \u03c3 ({})".format(statisticName, lengthUnit)


def MovementHeader(objectName, lengthUnit):
    """"Largest image movement (m)" or "Largest marker movement (m)"."""
    return "Largest {} movement ({})".format(objectName, lengthUnit)


# The column of the results table that counts the images that an iteration
# left without an orientation.
LOST_ORIENTATION_HEADER = "Images no longer oriented"


def ResultsTableHeaders(lengthUnit):
    """The column headers of the table of the results dialog, with the
    median tie-point sigma after the mean and the largest movements of an
    image and a marker at the end, followed by the count of the images that
    the iteration left without an orientation."""
    return [
        "Iteration", "Chunk", "Tie points", "Removed",
        "Reconstruction uncertainty (criterion)", "Projection accuracy (criterion)",
        "Maximum reprojection error (criterion)", "Minimum image count (criterion)",
        "RMS reprojection error (px)", "Maximum per-image RMS (px)",
        "Minimum per-image RMS (px)",
        TiePointStandardDeviationHeader("Mean", lengthUnit), TiePointStandardDeviationHeader("Median", lengthUnit),
        SIGMA_NOUGHT_HEADER, METASHAPE_SIGMA_NOUGHT_HEADER,
        "Control-point RMSE (m)", "Control points",
        "Check-point RMSE (m)", "Check points",
        MovementHeader("image", lengthUnit), MovementHeader("marker", lengthUnit),
        LOST_ORIENTATION_HEADER]


# The colour of each graph of the results dialog (red, green, blue), by the
# name of its filtering criterion or quality metric.  The header of the
# column of the table that holds its values shows it as a key
# (ResultsHeaderKeyColours).
RESULTS_GRAPH_COLOURS = {
    "Reconstruction Uncertainty": (120, 70, 160),
    "Projection Accuracy": (40, 130, 140),
    "Reprojection Error": (160, 40, 40),
    "Image Count": (70, 110, 60),
    "Tie-point count": (46, 84, 150),
    "RMS reprojection error": (200, 80, 20),
    "Maximum per-image RMS reprojection error": (200, 120, 20),
    "Minimum per-image RMS reprojection error": (150, 110, 30),
    "Mean tie-point \u03c3": (90, 90, 90),
    "Median tie-point \u03c3": (130, 130, 130),
    "\u03c3\u2080, reference standard deviation (approximate)": (120, 100, 40),
    "Control-point RMSE": (40, 120, 60),
    "Check-point RMSE": (20, 90, 130),
}
# The colour of the graph of a filtering criterion that the run does not use,
# and of the key of its column.
UNUSED_CRITERION_COLOUR = (160, 160, 160)


def ResultsColumnGraphNames(lengthUnit):
    """{header: name of its graph in RESULTS_GRAPH_COLOURS} of the columns
    of the results table (ResultsTableHeaders) that have a graph."""
    return {
        "Tie points": "Tie-point count",
        "Reconstruction uncertainty (criterion)": "Reconstruction Uncertainty",
        "Projection accuracy (criterion)": "Projection Accuracy",
        "Maximum reprojection error (criterion)": "Reprojection Error",
        "Minimum image count (criterion)": "Image Count",
        "RMS reprojection error (px)": "RMS reprojection error",
        "Maximum per-image RMS (px)": "Maximum per-image RMS reprojection error",
        "Minimum per-image RMS (px)": "Minimum per-image RMS reprojection error",
        TiePointStandardDeviationHeader("Mean", lengthUnit): "Mean tie-point \u03c3",
        TiePointStandardDeviationHeader("Median", lengthUnit): "Median tie-point \u03c3",
        SIGMA_NOUGHT_HEADER: "\u03c3\u2080, reference standard deviation (approximate)",
        "Control-point RMSE (m)": "Control-point RMSE",
        "Check-point RMSE (m)": "Check-point RMSE",
    }


def ResultsHeaderKeyColours(lengthUnit, drawnGraphColours):
    """{column index: (red, green, blue)} of the columns of the results
    table whose graph the dialog draws, in the colour of that graph:
    *drawnGraphColours* gives the colour of every graph drawn, by its name
    (RESULTS_GRAPH_COLOURS, or UNUSED_CRITERION_COLOUR for a criterion
    that the run does not use)."""
    graphNames = ResultsColumnGraphNames(lengthUnit)
    return {columnIndex: drawnGraphColours[graphNames[header]]
            for columnIndex, header in enumerate(ResultsTableHeaders(lengthUnit))
            if graphNames.get(header) in drawnGraphColours}


def HeaderKeyIcon(colour):
    """The key of a column of the results table: a square of 12 by 12
    pixels in *colour* (red, green, blue) with a grey border, so that a
    light colour stands out against the header."""
    pixmap = QtGui.QPixmap(12, 12)
    pixmap.fill(QtGui.QColor(*colour))
    painter = QtGui.QPainter(pixmap)
    painter.setPen(QtGui.QPen(QtGui.QColor(90, 90, 90), 1))
    painter.drawRect(0, 0, 11, 11)
    painter.end()
    return QtGui.QIcon(pixmap)


def SetHeaderColourKeys(table, keyColours):
    """Give the header of each column of *table* in *keyColours*
    (ResultsHeaderKeyColours) its key (HeaderKeyIcon)."""
    for columnIndex, colour in keyColours.items():
        headerItem = table.horizontalHeaderItem(columnIndex)
        if headerItem is not None:
            headerItem.setIcon(HeaderKeyIcon(colour))


def ResultsChartMinimumSpan(title, values):
    """The smallest range of the values drawn as a change on the graph of
    the results dialog with *title* (PlotValueRange): 0 for the tie-point
    count, MeaningfulRmseDifference for the control-point and the
    check-point RMSE, and None (the default) for the other graphs."""
    if title == "Tie-point count":
        return 0.0
    if title in ("Control-point RMSE (m)", "Check-point RMSE (m)"):
        finiteValues = [value for value in values if _IsFinite(value)]
        return MeaningfulRmseDifference(finiteValues) if finiteValues else None
    return None


# The end of the title of a graph shown as the change from iteration 0, and
# that of a graph that stays in values in that view.
PERCENT_CHANGE_TITLE_SUFFIX = ", change from iteration 0 (%)"
VALUES_ONLY_TITLE_SUFFIX = ", in values (no finite, non-zero value at iteration 0)"


def ValueChartTitle(name, unit=None):
    """"RMS reprojection error (px)": the title of a graph in values, the
    *unit* in parentheses after the *name*."""
    return "{} ({})".format(name, unit) if unit else name


def PercentChangeChartTitle(name):
    """"RMS reprojection error, change from iteration 0 (%)": the title of
    a graph shown as the change from iteration 0, without the unit of its
    values."""
    return name + PERCENT_CHANGE_TITLE_SUFFIX


def _IsFiniteNumber(value):
    """True for a value that a graph draws (_IsPlottedValue) and that is a
    finite number."""
    return _IsPlottedValue(value) and math.isfinite(value)


def PercentChangeSeries(xValues, values):
    """100 x (value - value at iteration 0) / value at iteration 0 of each
    of *values*, the values of a graph over the iterations *xValues*, None
    for a value that is missing or not finite, or None (for the whole
    graph) when iteration 0 has no finite, non-zero value."""
    if 0 not in xValues:
        return None
    reference = values[list(xValues).index(0)]
    if not _IsFiniteNumber(reference) or reference == 0:
        return None
    return [100.0 * (value - reference) / reference if _IsFiniteNumber(value) else None for value in values]


def ChartView(valueTitle, percentTitle, xValues, valueSeries, valueMinimumSpan, isPercent):
    """(title, values, smallest span drawn as a change) of a graph of the
    results dialog: in values, or, when *isPercent*, as the change from
    iteration 0 in % (PercentChangeSeries) with *percentTitle*.  The
    smallest span is then that of the values (*valueMinimumSpan*, or
    MINIMUM_RELATIVE_AXIS_SPAN of the largest absolute value when None,
    as in PlotValueRange) in % of the value at iteration 0, so that a graph
    drawn flat in values stays flat.  A graph without a finite, non-zero
    value at iteration 0 stays in values, and its title says so
    (VALUES_ONLY_TITLE_SUFFIX)."""
    if not isPercent:
        return valueTitle, list(valueSeries), valueMinimumSpan
    percentValues = PercentChangeSeries(xValues, valueSeries)
    if percentValues is None:
        return valueTitle + VALUES_ONLY_TITLE_SUFFIX, list(valueSeries), valueMinimumSpan
    reference = valueSeries[list(xValues).index(0)]
    valueSpan = valueMinimumSpan
    if valueSpan is None:
        valueSpan = MINIMUM_RELATIVE_AXIS_SPAN * max(abs(value) for value in valueSeries if _IsFiniteNumber(value))
    return percentTitle, percentValues, 100.0 * valueSpan / abs(reference)


def PrepareResultsChart(chart, percentTitle):
    """Keep what the switch of the graphs needs on *chart* (a
    LineChartWidget of the results dialog): its title, values, and
    smallest span in values, and its title as the change from iteration 0
    (*percentTitle*).  Its tooltip gives its whole title, which a narrow
    graph can cut off."""
    chart.valueTitle = chart.title
    chart.valueSeries = list(chart.yValues)
    chart.valueMinimumSpan = chart.minimumSpan
    chart.percentTitle = percentTitle
    chart.setToolTip(chart.title)


def ShowResultChartsAs(charts, isPercent):
    """Redraw the graphs of the results dialog (PrepareResultsChart) in
    values or, when *isPercent*, as the change from iteration 0 in %
    (ChartView), the slot of the switch above them.  A graph shown as the
    change from iteration 0 that does not change is drawn on an axis of
    PERCENT_FLAT_PAD on either side of its flat line."""
    for chart in charts:
        title, values, minimumSpan = ChartView(chart.valueTitle, chart.percentTitle, chart.xValues,
                                               chart.valueSeries, chart.valueMinimumSpan, isPercent)
        chart.title = title
        chart.yValues = values
        chart.minimumSpan = minimumSpan
        chart.flatPad = PERCENT_FLAT_PAD if title == chart.percentTitle else None
        chart.setToolTip(title)
        chart.update()
    print(ResultsConsoleLine("graphs shown as the change from iteration 0 in %." if isPercent
                             else "graphs shown in values."))


def FormatMovement(value):
    """A movement with three significant digits ("0.0123", "4.56e-07"),
    "0" for none, and the en dash without a value."""
    if value is None:
        return "\u2013"
    if value == 0.0:
        return "0"
    return "{:.3g}".format(value)


# The star of the suggested state, in the table, the banner, and the list of
# iterations of the results dialog.
SUGGESTION_STAR = "\u2b50"


def ResultsTableValues(row, displayLabel, isSuggested=False):
    """The texts of one row of the table of the results dialog, in the
    order of ResultsTableHeaders, with "As found" in the Iteration column
    of the chunk as found (StateCellText), after the star
    (SUGGESTION_STAR) when the state *isSuggested*."""
    stateText = StateCellText(row["iteration"])
    return [
        "{} {}".format(SUGGESTION_STAR, stateText) if isSuggested else stateText,
        displayLabel,
        "{:,}".format(row["points"]),
        "{:,}".format(row["removed"]),
        _FormatSummaryValue(row["reconstructionUncertaintyMaximum"], 2),
        _FormatSummaryValue(row["projectionAccuracyMaximum"], 2),
        _FormatSummaryValue(row["reprojectionErrorMaximum"], 3),
        ("\u2013" if row.get("minimumImageCount") is None
         else str(int(row["minimumImageCount"]))),
        _FormatSummaryValue(row["rms"], 4),
        _FormatSummaryValue(row["maximumImageRms"], 4),
        _FormatSummaryValue(row["minimumImageRms"], 4),
        _FormatSummaryLength(row["meanTiePointStandardDeviation"]),
        _FormatSummaryLength(row.get("medianTiePointStandardDeviation")),
        _FormatSummaryValue(row.get("sigma0"), 4),
        _FormatSummaryValue(row.get("metashapeSigma0"), 4),
        _FormatSummaryLength(row["controlRmse"]),
        (str(row["controlCount"])
         if row["controlRmse"] is not None else "\u2013"),
        _FormatSummaryLength(row.get("checkRmse")),
        (str(row.get("checkCount"))
         if row.get("checkRmse") is not None else "\u2013"),
        FormatMovement(row.get("largestImageMovement")),
        FormatMovement(row.get("largestMarkerMovement")),
        ("\u2013" if row.get("imagesNoLongerOriented") is None
         else "{:,}".format(len(row["imagesNoLongerOriented"]))),
    ]


def TiePointStandardDeviationConsoleText(metrics):
    """"mean tie-point standard deviation 0.06000 m, median 0.003400 m",
    with four significant digits, or the reason why there is none."""
    mean = metrics.get("meanTiePointStandardDeviation")
    lengthUnit = metrics.get("lengthUnit", "m")
    if mean is None:
        note = metrics.get("tiePointStandardDeviationNote")
        return "mean and median tie-point standard deviation n/a{}".format(" ({})".format(note) if note else "")
    median = metrics.get("medianTiePointStandardDeviation")
    medianPart = "" if median is None else ", median {} {}".format(FormatSignificantDigits(median), lengthUnit)
    return "mean tie-point standard deviation {} {}{}".format(FormatSignificantDigits(mean), lengthUnit, medianPart)


def MovementConsoleText(row):
    """"largest movement since the previous iteration: image 0.0123 m,
    marker 0 m" of a row of SummaryRow, since the chunk as found for
    iteration 0 (PreviousStateText)."""
    lengthUnit = row.get("lengthUnit", "m")
    parts = []
    for key, name in (("largestImageMovement", "image"), ("largestMarkerMovement", "marker")):
        value = row.get(key)
        parts.append("{} n/a".format(name) if value is None else "{} {} {}".format(name, FormatMovement(value), lengthUnit))
    return "largest movement since {}: {}".format(PreviousStateText(row), ", ".join(parts))


def LargestMovementParts(movedRows, lengthUnit):
    """["0.0123 m for the perspective centre of an image", ...]: the
    largest movement of an image and of a marker in *movedRows*, for each
    that has one."""
    parts = []
    for key, objectText in (("largestImageMovement", "the perspective centre of an image"),
                            ("largestMarkerMovement", "the estimated position of a marker")):
        values = [row.get(key) for row in movedRows if row.get(key) is not None]
        if values:
            parts.append("{} {} for {}".format(FormatMovement(max(values)), lengthUnit, objectText))
    return parts


def MovementSummaryText(summaryRows):
    """The sentences of the results dialog on how far the images and the
    markers moved: from the chunk as found to iteration 0, which is a new
    adjustment, and between one iteration and the next, or an empty text
    without any movement."""
    if not summaryRows:
        return ""
    lengthUnit = summaryRows[0].get("lengthUnit", "m")
    sentences = []
    reAdjustmentParts = LargestMovementParts([row for row in summaryRows if row.get("iteration") == 0], lengthUnit)
    if reAdjustmentParts:
        sentences.append("Largest movement from the chunk as found to iteration 0: {}.".format(
            " and ".join(reAdjustmentParts)))
    iterationParts = LargestMovementParts(
        [row for row in summaryRows if not IsStateAsFound(row) and row.get("iteration") != 0], lengthUnit)
    if iterationParts:
        sentences.append("Largest movement between one iteration and the next: {}.".format(" and ".join(iterationParts)))
    return " ".join(sentences)


def _FormatSummaryValue(value, decimals):
    if value is None or (isinstance(value, float)
                         and math.isnan(value)):
        return "\u2013"
    if abs(value) >= EXPONENT_FROM:
        return "{:.4g}".format(value)
    return "{:.{}f}".format(value, decimals)


def _FormatSummaryLength(value):
    """A length of the table (an RMSE or a tie-point sigma) with four
    significant digits (FormatSignificantDigits), the en dash without a
    value."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "\u2013"
    return FormatSignificantDigits(value)


def _AddResultsSectionHeader(graphGrid, gridState, text):
    label = QtWidgets.QLabel(text)
    label.setStyleSheet(
        "font-weight: bold; color: #1f3864; margin-top: 6px;")
    graphGrid.addWidget(label, gridState["row"], 0, 1, 2)
    gridState["row"] += 1


def _FindChunkByKey(document, key):
    for candidate in document.chunks:
        if candidate.key == key:
            return candidate
    return None


def _GetSelectedChunkKey(iterationComboBox, summaryRows):
    return summaryRows[
        iterationComboBox.currentIndex()]["chunkKey"]


def ResultsConsoleLine(text):
    """An action of the results window as a line of the Console."""
    return "Gradual Selection Filter: " + text


def _EchoIterationChoice(summaryRows, displayLabels, itemIndex):
    """Echo the iteration chosen in the list of the results window (its
    activated signal, so only a choice of the user)."""
    if not 0 <= itemIndex < len(summaryRows):
        return
    print(ResultsConsoleLine("{} ({}) chosen in the results.".format(
        StateName(summaryRows[itemIndex]["iteration"]), displayLabels[itemIndex])))


def _ActivateSelectedChunk(dialog, document, findChunkByKey, getSelectedChunkKey):
    target = findChunkByKey(getSelectedChunkKey())
    if target is None:
        print(ResultsConsoleLine("the chunk of this iteration has been deleted, so it cannot be activated."))
        QtWidgets.QMessageBox.information(
            dialog, "Gradual Selection Filter",
            "The chunk of this iteration has been deleted, so it "
            "cannot be activated.")
        return
    document.chunk = target
    print(ResultsConsoleLine("active chunk set to {}.".format(target.label)))
    QtWidgets.QMessageBox.information(
        dialog, "Gradual Selection Filter",
        "Active chunk set to:\n{}".format(
            target.label))


def BaseChunkKey(summaryRows):
    """The key of the chunk that the run filtered (the base chunk): that of
    the row of the chunk as found, or of the first row without one."""
    baseRow = next((row for row in summaryRows if IsStateAsFound(row)), summaryRows[0] if summaryRows else None)
    return baseRow["chunkKey"] if baseRow is not None else None


def _DeleteOtherIterationChunks(deleteOthersButton, dialog, document, findChunkByKey, getSelectedChunkKey, summaryRows):
    """Delete every iteration chunk except the selected one, never the base
    chunk (BaseChunkKey), which holds the chunk as found when the
    iterations are kept, after a confirmation."""
    keepKey = getSelectedChunkKey()
    baseChunkKey = BaseChunkKey(summaryRows)
    toRemove = []
    for row in summaryRows:
        if row["chunkKey"] == baseChunkKey:
            continue  # never delete the base chunk
        if row["chunkKey"] == keepKey:
            continue
        candidate = findChunkByKey(row["chunkKey"])
        if candidate is not None:
            toRemove.append(candidate)
    if not toRemove:
        print(ResultsConsoleLine("there are no other iteration chunks to delete."))
        QtWidgets.QMessageBox.information(
            dialog, "Gradual Selection Filter",
            "There are no other iteration chunks "
            "to delete.")
        return
    confirm = QtWidgets.QMessageBox.question(
        dialog, "Gradual Selection Filter",
        "Delete every iteration chunk except the selected one ({} to "
        "delete)? This cannot be undone.".format(len(toRemove)),
        QtWidgets.QMessageBox.Yes
        | QtWidgets.QMessageBox.No,
        QtWidgets.QMessageBox.No)
    if confirm != QtWidgets.QMessageBox.Yes:
        print(ResultsConsoleLine("deleting {} was declined.".format(
            FormatCountWithNoun(len(toRemove), "other iteration chunk", "other iteration chunks"))))
        return
    keepChunk = findChunkByKey(keepKey)
    if keepChunk is not None:
        document.chunk = keepChunk
    removedLabels = [chunk.label for chunk in toRemove]
    document.remove(toRemove)
    deleteOthersButton.setEnabled(False)
    print(ResultsConsoleLine("iteration chunks deleted: {} ({}). Kept: {}.".format(
        len(removedLabels), ", ".join(removedLabels), keepChunk.label if keepChunk else "?")))
    QtWidgets.QMessageBox.information(
        dialog, "Gradual Selection Filter",
        "Iteration chunks deleted: {}. Kept:\n"
        "{}".format(
            len(toRemove),
            keepChunk.label if keepChunk else "?"))


class GradualSelectionDialog(QtWidgets.QDialog):
    """Dialog for iterative gradual selection filtering."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Gradual Selection Filter")
        self.setWindowFlags(
            self.windowFlags()
            & ~QtCore.Qt.WindowContextHelpButtonHint)
        self.setMinimumWidth(560)
        self._isRunning = False
        self._stopRequested = False
        self._BuildUi()

    def _BuildUi(self):
        mainLayout = QtWidgets.QVBoxLayout(self)

        chunk = Metashape.app.document.chunk

        descriptionLabel = QtWidgets.QLabel(
            "Iteratively remove the worst tie points by one or more "
            "criteria, re-running Optimize Cameras after each "
            "iteration and tracking quality metrics throughout.")
        descriptionLabel.setWordWrap(True)
        mainLayout.addWidget(descriptionLabel)
        mainLayout.addSpacing(6)

        chunkInformation, pointCount = FormatChunkInformation(chunk)
        self.chunkInformationLabel = QtWidgets.QLabel(chunkInformation)
        self.chunkInformationLabel.setTextFormat(QtCore.Qt.RichText)
        mainLayout.addWidget(self.chunkInformationLabel)
        mainLayout.addSpacing(8)

        # --- Mode and parameters (at the top) ---
        self.modeGroup = QtWidgets.QGroupBox("Removal mode")
        modeLayout = QtWidgets.QGridLayout(self.modeGroup)

        self.modeAbsoluteRadioButton = QtWidgets.QRadioButton(
            "Absolute threshold (step the worst value down to the "
            "target)")
        self.modeAbsoluteRadioButton.setChecked(True)
        self.modePercentileRadioButton = QtWidgets.QRadioButton(
            "Percentile (remove a fixed percentage in each iteration)")
        modeLayout.addWidget(self.modeAbsoluteRadioButton, 0, 0, 1, 2)
        modeLayout.addWidget(self.modePercentileRadioButton, 1, 0, 1, 2)

        self.percentLabel = QtWidgets.QLabel(
            "Percentage per criterion:")
        self.percentSpinBox = QtWidgets.QDoubleSpinBox()
        self.percentSpinBox.setRange(0.1, 50.0)
        self.percentSpinBox.setValue(10.0)
        self.percentSpinBox.setSingleStep(0.5)
        self.percentSpinBox.setDecimals(1)
        self.percentSpinBox.setSuffix(" %")
        self.percentSpinBox.setToolTip(
            "Percentile mode only: the percentage of the worst points\n"
            "removed by each enabled criterion in each iteration,\n"
            "rounded to the nearest whole point and at least one\n"
            "(never points that already meet the target).")
        modeLayout.addWidget(self.percentLabel, 2, 0)
        modeLayout.addWidget(self.percentSpinBox, 2, 1)

        iterationsLabel = QtWidgets.QLabel("Maximum iterations:")
        self.iterationsSpinBox = QtWidgets.QSpinBox()
        self.iterationsSpinBox.setRange(0, 200)
        self.iterationsSpinBox.setValue(10)
        self.iterationsSpinBox.setSpecialValueText(
            "No limit")
        self.iterationsSpinBox.setToolTip(
            "Upper bound on the number of iterations (a removal\n"
            "followed by Optimize Cameras). Set to 0 for no limit:\n"
            "the run then continues until all targets are reached,\n"
            "and in percentile mode it also stops when the RMS\n"
            "reprojection error no longer improves (auto-stop).\n"
            "The run always stops at the safety cap.")
        modeLayout.addWidget(iterationsLabel, 3, 0)
        modeLayout.addWidget(self.iterationsSpinBox, 3, 1)

        capLabel = QtWidgets.QLabel("Safety cap (maximum total removed):")
        self.safetyCapSpinBox = QtWidgets.QDoubleSpinBox()
        self.safetyCapSpinBox.setRange(1.0, 95.0)
        self.safetyCapSpinBox.setValue(50.0)
        self.safetyCapSpinBox.setDecimals(0)
        self.safetyCapSpinBox.setSuffix(" %")
        modeLayout.addWidget(capLabel, 4, 0)
        modeLayout.addWidget(self.safetyCapSpinBox, 4, 1)

        # Optional stop on the overall (pixel) RMS reprojection error.
        self.rmsStopCheckBox = QtWidgets.QCheckBox(
            "Stop when RMS reprojection error \u2264")
        self.rmsStopCheckBox.setToolTip(RMS_STOP_TOOLTIP)
        self.rmsStopSpinBox = QtWidgets.QDoubleSpinBox()
        self.rmsStopSpinBox.setToolTip(RMS_STOP_TOOLTIP)
        self.rmsStopSpinBox.setRange(0.05, 20.0)
        self.rmsStopSpinBox.setValue(1.0)
        self.rmsStopSpinBox.setSingleStep(0.05)
        self.rmsStopSpinBox.setDecimals(2)
        self.rmsStopSpinBox.setSuffix(" px")
        self.rmsStopSpinBox.setEnabled(False)
        self.rmsStopCheckBox.toggled.connect(
            self.rmsStopSpinBox.setEnabled)
        modeLayout.addWidget(self.rmsStopCheckBox, 5, 0)
        modeLayout.addWidget(self.rmsStopSpinBox, 5, 1)
        mainLayout.addWidget(self.modeGroup)

        # --- Criteria (target + step per criterion) ---
        self.criteriaGroup = QtWidgets.QGroupBox(
            "Filtering criteria (applied sequentially)")
        criteriaGrid = QtWidgets.QGridLayout(self.criteriaGroup)
        criteriaGrid.addWidget(QtWidgets.QLabel("<b>Enable</b>"), 0, 0)
        criteriaGrid.addWidget(
            QtWidgets.QLabel("<b>Target</b>"), 0, 1)
        self.stepHeaderLabel = QtWidgets.QLabel("<b>Step</b>")
        criteriaGrid.addWidget(self.stepHeaderLabel, 0, 2)

        self.reconstructionUncertaintyCheckBox = QtWidgets.QCheckBox(
            "Reconstruction Uncertainty")
        self.reconstructionUncertaintyCheckBox.setChecked(True)
        self.reconstructionUncertaintyTarget = QtWidgets.QDoubleSpinBox()
        self.reconstructionUncertaintyTarget.setRange(0.1, 10000.0)
        self.reconstructionUncertaintyTarget.setValue(
            DEFAULT_ABSOLUTE_TARGETS["Reconstruction Uncertainty"])
        self.reconstructionUncertaintyTarget.setDecimals(1)
        self.reconstructionUncertaintyStep = QtWidgets.QDoubleSpinBox()
        self.reconstructionUncertaintyStep.setRange(0.1, 10000.0)
        self.reconstructionUncertaintyStep.setValue(10.0)
        self.reconstructionUncertaintyStep.setDecimals(1)

        self.projectionAccuracyCheckBox = QtWidgets.QCheckBox(
            "Projection Accuracy")
        self.projectionAccuracyTarget = QtWidgets.QDoubleSpinBox()
        self.projectionAccuracyTarget.setRange(0.1, 10000.0)
        self.projectionAccuracyTarget.setValue(
            DEFAULT_ABSOLUTE_TARGETS["Projection Accuracy"])
        self.projectionAccuracyTarget.setDecimals(1)
        self.projectionAccuracyStep = QtWidgets.QDoubleSpinBox()
        self.projectionAccuracyStep.setRange(0.1, 10000.0)
        self.projectionAccuracyStep.setValue(2.0)
        self.projectionAccuracyStep.setDecimals(1)

        self.reprojectionErrorCheckBox = QtWidgets.QCheckBox(
            "Reprojection Error")
        self.reprojectionErrorTarget = QtWidgets.QDoubleSpinBox()
        self.reprojectionErrorTarget.setRange(0.01, 100.0)
        self.reprojectionErrorTarget.setValue(
            DEFAULT_ABSOLUTE_TARGETS["Reprojection Error"])
        self.reprojectionErrorTarget.setDecimals(2)
        self.reprojectionErrorStep = QtWidgets.QDoubleSpinBox()
        self.reprojectionErrorStep.setRange(0.01, 100.0)
        self.reprojectionErrorStep.setValue(0.10)
        self.reprojectionErrorStep.setDecimals(2)

        criteriaGrid.addWidget(
            self.reconstructionUncertaintyCheckBox, 1, 0)
        criteriaGrid.addWidget(
            self.reconstructionUncertaintyTarget, 1, 1)
        criteriaGrid.addWidget(
            self.reconstructionUncertaintyStep, 1, 2)
        criteriaGrid.addWidget(self.projectionAccuracyCheckBox, 2, 0)
        criteriaGrid.addWidget(self.projectionAccuracyTarget, 2, 1)
        criteriaGrid.addWidget(self.projectionAccuracyStep, 2, 2)
        criteriaGrid.addWidget(self.reprojectionErrorCheckBox, 3, 0)
        criteriaGrid.addWidget(self.reprojectionErrorTarget, 3, 1)
        criteriaGrid.addWidget(self.reprojectionErrorStep, 3, 2)

        # Image Count is reversed (keep points seen in at least N
        # images) and applied once, so it has a minimum target but no
        # step.
        self.imageCountCheckBox = QtWidgets.QCheckBox("Image Count")
        self.imageCountTarget = QtWidgets.QSpinBox()
        self.imageCountTarget.setRange(2, 50)
        self.imageCountTarget.setValue(3)
        self.imageCountTarget.setPrefix("\u2265 ")
        self.imageCountTarget.setSuffix(" images")
        imageCountStepLabel = QtWidgets.QLabel("(applied once)")
        imageCountStepLabel.setStyleSheet("color: #888;")
        criteriaGrid.addWidget(self.imageCountCheckBox, 4, 0)
        criteriaGrid.addWidget(self.imageCountTarget, 4, 1)
        criteriaGrid.addWidget(imageCountStepLabel, 4, 2)

        # Each criterion explains itself on its tick box and on the
        # fields of its target and step (CRITERION_TOOLTIPS).
        for criterionName, criterionWidgets in self.CriterionWidgets():
            for criterionWidget in criterionWidgets:
                criterionWidget.setToolTip(CRITERION_TOOLTIPS[criterionName])

        hintLabel = QtWidgets.QLabel(
            "Target: the stopping value (both modes). For the first "
            "three criteria, lower is better and points above the "
            "target are removed. Image Count is reversed (points seen "
            "in at least the target number of images are kept) and is "
            "applied once. Step: how far the threshold descends in "
            "each iteration (absolute mode only).")
        hintLabel.setStyleSheet("color: #555; font-size: 11px;")
        hintLabel.setWordWrap(True)
        criteriaGrid.addWidget(hintLabel, 5, 0, 1, 3)
        mainLayout.addWidget(self.criteriaGroup)

        self.modeAbsoluteRadioButton.toggled.connect(self._OnModeChanged)
        self._OnModeChanged()

        # --- Optimize Cameras parameters ---
        self.optimiseGroup = QtWidgets.QGroupBox(
            "Optimize Cameras parameters")
        optimiseGrid = QtWidgets.QGridLayout(self.optimiseGroup)
        self.fitF = QtWidgets.QCheckBox("f"); self.fitF.setChecked(True)
        self.fitCxCy = QtWidgets.QCheckBox("cx, cy"); self.fitCxCy.setChecked(True)
        self.fitK1 = QtWidgets.QCheckBox("k1"); self.fitK1.setChecked(True)
        self.fitK2 = QtWidgets.QCheckBox("k2"); self.fitK2.setChecked(True)
        self.fitK3 = QtWidgets.QCheckBox("k3"); self.fitK3.setChecked(True)
        self.fitK4 = QtWidgets.QCheckBox("k4")
        self.fitP1 = QtWidgets.QCheckBox("p1"); self.fitP1.setChecked(True)
        self.fitP2 = QtWidgets.QCheckBox("p2"); self.fitP2.setChecked(True)
        self.fitB1 = QtWidgets.QCheckBox("b1")
        self.fitB2 = QtWidgets.QCheckBox("b2")
        self.adaptiveFittingCheckBox = QtWidgets.QCheckBox(
            "Adaptive camera model fitting")
        # The label of Metashape's Optimize Cameras dialog, as the About
        # says.
        self.estimateTiePointCovarianceCheckBox = QtWidgets.QCheckBox(
            "Estimate tie point covariance")
        self.estimateTiePointCovarianceCheckBox.setChecked(True)
        optimiseGrid.addWidget(self.fitF, 0, 0)
        optimiseGrid.addWidget(self.fitCxCy, 0, 1)
        optimiseGrid.addWidget(self.fitK1, 0, 2)
        optimiseGrid.addWidget(self.fitK2, 0, 3)
        optimiseGrid.addWidget(self.fitK3, 1, 0)
        optimiseGrid.addWidget(self.fitK4, 1, 1)
        optimiseGrid.addWidget(self.fitP1, 1, 2)
        optimiseGrid.addWidget(self.fitP2, 1, 3)
        optimiseGrid.addWidget(self.fitB1, 2, 0)
        optimiseGrid.addWidget(self.fitB2, 2, 1)
        optimiseGrid.addWidget(self.adaptiveFittingCheckBox, 3, 0, 1, 4)
        optimiseGrid.addWidget(self.estimateTiePointCovarianceCheckBox, 4, 0, 1, 4)
        mainLayout.addWidget(self.optimiseGroup)

        # --- Options ---
        self.optionsGroup = QtWidgets.QGroupBox("Options")
        optionsLayout = QtWidgets.QVBoxLayout(self.optionsGroup)
        self.keepIterationsCheckBox = QtWidgets.QCheckBox(
            "Keep each iteration as a separate chunk "
            "(enables reverting to an earlier iteration)")
        self.keepIterationsCheckBox.setChecked(True)
        optionsLayout.addWidget(self.keepIterationsCheckBox)
        self.saveAfterEachCheckBox = QtWidgets.QCheckBox(
            "Save project after each iteration")
        optionsLayout.addWidget(self.saveAfterEachCheckBox)
        mainLayout.addWidget(self.optionsGroup)
        mainLayout.addSpacing(4)

        # Progress.
        self.progressBar = QtWidgets.QProgressBar()
        self.progressBar.setVisible(False)
        mainLayout.addWidget(self.progressBar)

        # Buttons.
        buttonRow = QtWidgets.QHBoxLayout()
        self.aboutButton = QtWidgets.QPushButton("About")
        self.runButton = QtWidgets.QPushButton("Run iterative filter")
        self.runButton.setDefault(True)
        self.runButton.setEnabled(pointCount > 0)
        self.stopButton = QtWidgets.QPushButton("Stop")
        self.stopButton.setToolTip(
            "Stop the run after the current iteration. The iterations\n"
            "completed so far are kept and shown in the results.")
        self.stopButton.setEnabled(False)
        self.closeButton = QtWidgets.QPushButton("Close")
        buttonRow.addWidget(self.aboutButton)
        buttonRow.addStretch()
        buttonRow.addWidget(self.runButton)
        buttonRow.addWidget(self.stopButton)
        buttonRow.addWidget(self.closeButton)
        mainLayout.addLayout(buttonRow)

        self.aboutButton.clicked.connect(self._OnAbout)
        self.runButton.clicked.connect(self._OnRun)
        self.stopButton.clicked.connect(self._OnStopRequested)
        self.closeButton.clicked.connect(self.reject)

    def reject(self):
        """Close the dialog, except while a run is active: Escape and
        the title-bar close button are then ignored (Stop ends the run
        after the current iteration)."""
        if self._isRunning:
            return
        super().reject()

    def _SetRunning(self, isRunning):
        """Switch between the idle and the running state.  While a run
        is active every input, About, Run, and Close are disabled, Stop
        is enabled, and the progress bar is shown."""
        self._isRunning = isRunning
        for widget in (self.modeGroup, self.criteriaGroup,
                       self.optimiseGroup, self.optionsGroup,
                       self.aboutButton, self.runButton, self.closeButton):
            widget.setEnabled(not isRunning)
        self.stopButton.setEnabled(isRunning)
        self.progressBar.setVisible(isRunning)

    def _OnStopRequested(self):
        """Ask the running filter to stop after the current iteration."""
        self._stopRequested = True
        self.stopButton.setEnabled(False)
        print("Gradual Selection Filter: Stop was pressed. The run ends "
              "after the current iteration.")

    def _RefreshChunkInformation(self):
        """Show the chunk that is active now (after a run it is the last
        iteration chunk, or the one activated in the results) and allow
        Run only when it has tie points."""
        chunkInformation, pointCount = FormatChunkInformation(
            Metashape.app.document.chunk)
        self.chunkInformationLabel.setText(chunkInformation)
        self.runButton.setEnabled(pointCount > 0)

    def CriterionWidgets(self):
        """(criterion name, widgets) of each criterion: its tick box and the
        fields of its target and step, which share its tooltip
        (CRITERION_TOOLTIPS).  Image Count has no step."""
        return (
            ("Reconstruction Uncertainty", (self.reconstructionUncertaintyCheckBox,
                                            self.reconstructionUncertaintyTarget,
                                            self.reconstructionUncertaintyStep)),
            ("Projection Accuracy", (self.projectionAccuracyCheckBox, self.projectionAccuracyTarget,
                                     self.projectionAccuracyStep)),
            ("Reprojection Error", (self.reprojectionErrorCheckBox, self.reprojectionErrorTarget,
                                    self.reprojectionErrorStep)),
            ("Image Count", (self.imageCountCheckBox, self.imageCountTarget)))

    def _OnModeChanged(self):
        isPercentile = self.modePercentileRadioButton.isChecked()
        # Percentage field only applies in percentile mode.
        self.percentLabel.setEnabled(isPercentile)
        self.percentSpinBox.setEnabled(isPercentile)
        # Step fields only apply in absolute mode.
        self.stepHeaderLabel.setEnabled(not isPercentile)
        for widget in (self.reconstructionUncertaintyStep,
                       self.projectionAccuracyStep,
                       self.reprojectionErrorStep):
            widget.setEnabled(not isPercentile)

    def _OnAbout(self):
        aboutDialog = QtWidgets.QDialog(self)
        aboutDialog.setWindowTitle(
            "About: Gradual Selection Filter")
        aboutDialog.setWindowFlags(
            aboutDialog.windowFlags()
            & ~QtCore.Qt.WindowContextHelpButtonHint)
        aboutDialog.resize(580, 600)
        aboutLayout = QtWidgets.QVBoxLayout(aboutDialog)
        textBrowser = QtWidgets.QTextBrowser()
        textBrowser.setOpenExternalLinks(True)
        textBrowser.setHtml(_ABOUT_HTML)
        aboutLayout.addWidget(textBrowser)
        closeButton = QtWidgets.QPushButton("Close")
        closeButton.clicked.connect(aboutDialog.close)
        row = QtWidgets.QHBoxLayout()
        row.addStretch()
        row.addWidget(closeButton)
        aboutLayout.addLayout(row)
        aboutDialog.exec_()

    def _GetEnabledCriteria(self):
        """Return [(name, enum, target, step), ...] for enabled criteria."""
        enabled = []
        if self.reconstructionUncertaintyCheckBox.isChecked():
            enabled.append((
                "Reconstruction Uncertainty",
                FILTER_CRITERIA["Reconstruction Uncertainty"],
                self.reconstructionUncertaintyTarget.value(),
                self.reconstructionUncertaintyStep.value()))
        if self.projectionAccuracyCheckBox.isChecked():
            enabled.append((
                "Projection Accuracy",
                FILTER_CRITERIA["Projection Accuracy"],
                self.projectionAccuracyTarget.value(),
                self.projectionAccuracyStep.value()))
        if self.reprojectionErrorCheckBox.isChecked():
            enabled.append((
                "Reprojection Error",
                FILTER_CRITERIA["Reprojection Error"],
                self.reprojectionErrorTarget.value(),
                self.reprojectionErrorStep.value()))
        return enabled

    def _BuildOptimiseArguments(self):
        return dict(
            fit_f=self.fitF.isChecked(),
            fit_cx=self.fitCxCy.isChecked(),
            fit_cy=self.fitCxCy.isChecked(),
            fit_k1=self.fitK1.isChecked(),
            fit_k2=self.fitK2.isChecked(),
            fit_k3=self.fitK3.isChecked(),
            fit_k4=self.fitK4.isChecked(),
            fit_p1=self.fitP1.isChecked(),
            fit_p2=self.fitP2.isChecked(),
            fit_b1=self.fitB1.isChecked(),
            fit_b2=self.fitB2.isChecked(),
            adaptive_fitting=self.adaptiveFittingCheckBox.isChecked(),
            tiepoint_covariance=self.estimateTiePointCovarianceCheckBox.isChecked())

    def _OnRun(self):
        document = Metashape.app.document
        chunk = document.chunk
        if not chunk or not chunk.tie_points:
            QtWidgets.QMessageBox.warning(
                self, "Gradual Selection Filter",
                "The active chunk has no tie points.")
            return

        enabledCriteria = self._GetEnabledCriteria()
        imageCountEnabled = self.imageCountCheckBox.isChecked()
        minimumImages = self.imageCountTarget.value()
        if not enabledCriteria and not imageCountEnabled:
            QtWidgets.QMessageBox.warning(
                self, "Gradual Selection Filter",
                "Please enable at least one filtering criterion.")
            return

        # Metashape adjusts a disabled image that is still oriented and has
        # 2D tie points or marker projections, whereas the metrics of the run
        # count the enabled images, so the question before the run names
        # such images.
        disabledImagesNote = DisabledOrientedImagesText(DisabledOrientedImagesWithObservations(chunk),
                                                        DISABLED_IMAGE_METRICS_TEXT, DISABLED_IMAGES_METRICS_TEXT)

        isPercentile = self.modePercentileRadioButton.isChecked()
        percentToRemove = self.percentSpinBox.value()
        requestedIterations = self.iterationsSpinBox.value()
        isUnlimited = (requestedIterations == 0)
        iterationCount = (UNLIMITED_ITERATION_CEILING
                          if isUnlimited else requestedIterations)
        safetyCapPercent = self.safetyCapSpinBox.value()
        keepIterations = self.keepIterationsCheckBox.isChecked()
        saveAfterEach = self.saveAfterEachCheckBox.isChecked()
        rmsStopEnabled = self.rmsStopCheckBox.isChecked()
        rmsStopValue = self.rmsStopSpinBox.value()

        # Saving after each iteration needs a project file.
        if saveAfterEach and IsNeverSavedDocument(document):
            QtWidgets.QMessageBox.warning(
                self, "Gradual Selection Filter",
                "The project has never been saved, so it cannot be saved "
                "after each iteration. Save the project first, or untick "
                "\u201cSave project after each iteration\u201d.")
            return

        criteriaCount = len(enabledCriteria) + (
            1 if imageCountEnabled else 0)
        modeDescription = (
            "percentile ({:.1f} % per criterion)".format(percentToRemove)
            if isPercentile else "absolute threshold (stepped)")
        if isUnlimited:
            iterationDescription = (
                "until the targets are reached or the RMS reprojection "
                "error stops improving" if isPercentile
                else "until the targets are reached")
        else:
            iterationDescription = "up to {}".format(FormatCountWithNoun(
                iterationCount, "iteration", "iterations"))
        reply = QtWidgets.QMessageBox.question(
            self, "Gradual Selection Filter",
            "Run {} in {} mode on the chunk \u201c{}\u201d, with {} "
            "enabled?\n\n{}{}".format(
                iterationDescription, modeDescription, chunk.label,
                DescribeCriterionCount(criteriaCount),
                RunPlanText(keepIterations),
                "\n\n" + disabledImagesNote if disabledImagesNote else ""),
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No)
        if reply != QtWidgets.QMessageBox.Yes:
            return

        self._RunIterativeFilter(
            document, chunk, enabledCriteria, isPercentile,
            percentToRemove, iterationCount, safetyCapPercent,
            keepIterations, saveAfterEach, isUnlimited,
            imageCountEnabled, minimumImages,
            rmsStopEnabled, rmsStopValue)

    @staticmethod
    def _IterationHasWork(chunk, enabledCriteria,
                          imageCountEnabled=False, minimumImages=0):
        """True if any enabled criterion still has points to remove.

        Used both as the per-iteration stop condition (all targets
        reached) and to avoid creating an identical iteration chunk
        when there is nothing left to remove.
        """
        for unusedName, criterionEnum, targetValue, unusedStep in \
                enabledCriteria:
            values = GetCriterionValues(chunk, criterionEnum)
            if values and values[-1] > targetValue:
                return True
        if imageCountEnabled:
            smallest = GetMinimumImageCount(chunk)
            if smallest is not None and smallest < minimumImages:
                return True
        return False

    def _RunIterativeFilter(self, document, baseChunk, enabledCriteria,
                            isPercentile, percentToRemove,
                            iterationCount, safetyCapPercent,
                            keepIterations, saveAfterEach,
                            isUnlimited=False,
                            imageCountEnabled=False, minimumImages=0,
                            rmsStopEnabled=False, rmsStopValue=1.0):
        optimiseArguments = self._BuildOptimiseArguments()
        filterSettings = FilterOptimisationSettings(optimiseArguments)
        baseLabel = baseChunk.label
        workingChunk = baseChunk
        tiePointAccuracy = getattr(baseChunk, "tiepoint_accuracy", 1.0)

        collectIterationMetrics = functools.partial(_CollectIterationMetrics, tiePointAccuracy)

        initialPointCount = CountValidPoints(baseChunk)
        minimumPointsAllowed = ComputeMinimumRetainedPointCount(
            initialPointCount, safetyCapPercent)

        self._stopRequested = False
        self._SetRunning(True)
        # In unlimited mode the total is unknown, so a busy bar is shown.
        if isUnlimited:
            self.progressBar.setRange(0, 0)
        else:
            self.progressBar.setRange(0, iterationCount)
            self.progressBar.setValue(0)
        try:
            QtWidgets.QApplication.processEvents()

            PrintConsoleLines(ConsoleBlockStart("Gradual Selection Filter"))
            print("Base chunk: {}".format(baseLabel))
            print("Mode: {}".format(
                "percentile ({:g} % per criterion)".format(percentToRemove)
                if isPercentile else "absolute threshold (stepped)"))
            print("Criteria: {}".format(DescribeCriteria(
                enabledCriteria, isPercentile, imageCountEnabled,
                minimumImages)))
            print("Initial valid tie points: {:,}".format(
                initialPointCount))
            print("Iterations: {}".format(
                "no limit (until the targets are reached{})".format(
                    " or the RMS reprojection error stops improving"
                    if isPercentile else "")
                if isUnlimited else iterationCount))
            print("Safety cap: {:.0f} %  (floor {:,} points)".format(
                safetyCapPercent, minimumPointsAllowed))
            if rmsStopEnabled:
                print("Stop at RMS reprojection error: \u2264 {:.2f} px".format(rmsStopValue))
            print("")

            # Nothing has been changed yet, so a failure here ends the
            # run before it starts (the finally block restores the
            # dialog).  The metrics and the settings of the last Optimize
            # Cameras are those of the chunk as the user left it (the
            # state as found), read before iteration 0 re-adjusts it.
            try:
                initialMetrics = collectIterationMetrics(baseChunk)
                initialMetrics["optimisationSettings"] = ReadOptimisationSettings(baseChunk)
            except Exception as metricsError:
                print("-> The run did not start: the quality metrics of "
                      "the chunk could not be computed ({}).".format(
                          DescribeException(metricsError)))
                PrintConsoleLines(ConsoleBlockEnd())
                QtWidgets.QMessageBox.warning(
                    self, "Gradual Selection Filter",
                    "The quality metrics of the chunk could not be "
                    "computed, so the run did not start ({}). The chunk "
                    "was not changed.".format(
                        DescribeException(metricsError)))
                return
            summaryRows = [SummaryRow(STATE_AS_FOUND, baseLabel, baseChunk.key, 0, initialMetrics)]
            self._PrintMetrics(STATE_AS_FOUND, initialMetrics, row=summaryRows[0])
            aPrioriRmseText = APrioriCheckPointRmseText(summaryRows)
            if aPrioriRmseText:
                print(aPrioriRmseText)

            if isUnlimited:
                stoppedReason = (
                    "safety ceiling of {} iterations reached".format(
                        iterationCount))
            else:
                stoppedReason = "maximum number of iterations reached"
            interruptionNote = None
            previousRms = initialMetrics["rms"]
            stallCount = 0  # consecutive iterations with negligible gain
            # False once an interrupted in-place iteration has left the
            # chunk in a state that no results row describes.
            finalStateRecorded = True

            # Iteration 0 re-adjusts the chunk (or, when the iterations are
            # kept, a copy of it) with the settings of the dialog before any
            # tie point is removed, so that every iteration starts from the
            # adjustment of the filter and not from the last adjustment of
            # the user, which may have used other settings.
            for iteration in range(0, iterationCount + 1):
                previousChunk = workingChunk
                madeCopy = False
                removedThisIteration = 0
                cappedOut = False
                knownChunkKeys = None
                operation = "the target check"
                try:
                    # Optional stop on the overall (pixel) RMS reprojection
                    # error, checked against the most recent state before
                    # doing (and recording) another iteration.
                    if (iteration > 0 and rmsStopEnabled and _IsFinite(previousRms)
                            and previousRms <= rmsStopValue):
                        stoppedReason = (
                            "RMS reprojection error target "
                            "(\u2264 {:.2f} px) reached".format(rmsStopValue))
                        print("-> {}".format(stoppedReason))
                        break

                    # Stop (without creating a new chunk) when every enabled
                    # criterion is already at or below its target.  This is
                    # also what prevents an identical trailing iteration chunk.
                    if iteration > 0 and not self._IterationHasWork(
                            workingChunk, enabledCriteria,
                            imageCountEnabled, minimumImages):
                        stoppedReason = (
                            "all targets reached after iteration "
                            "{}".format(iteration - 1))
                        print("-> {}".format(stoppedReason))
                        break

                    # Stop requested with the Stop button (processed while
                    # the previous iteration ended).  It is checked after
                    # the targets, so that a run that has just reached
                    # them is not reported as stopped.
                    if iteration > 0 and self._stopRequested:
                        stoppedReason = DescribeStoppedRun(
                            iteration - 1, iterationCount, isUnlimited,
                            "Stop was pressed")
                        interruptionNote = "{}{}.".format(
                            stoppedReason[:1].upper(), stoppedReason[1:])
                        print("-> {}".format(stoppedReason))
                        break

                    # Make the iteration copy only now that we know this
                    # iteration will do something.  Chunk.copy returns the
                    # copy, and the chunk added to the document is the
                    # fallback.  The keys of the existing chunks are read
                    # before the operation counts as the copy, so that the
                    # clean-up never mistakes an existing chunk for the copy.
                    # Iteration 0 copies the chunk as found, which then stays
                    # as found, and iteration k + 1 copies iteration k.
                    if keepIterations:
                        operation = "the preparation of the chunk copy"
                        knownChunkKeys = set(
                            candidate.key for candidate in document.chunks)
                        operation = "the chunk copy"
                        copiedChunk = workingChunk.copy()
                        iterationChunk = (
                            copiedChunk if copiedChunk is not None
                            else _FindChunkAddedSince(document, knownChunkKeys))
                        iterationChunk.label = "{}_iteration-{:02d}".format(
                            baseLabel, iteration)
                        document.chunk = iterationChunk
                        workingChunk = iterationChunk
                        madeCopy = True

                    # Iteration 0 removes no tie point: it only re-adjusts.
                    isRemovalIteration = iteration > 0
                    if isRemovalIteration:
                        operation = "the removal of tie points"
                    tiePoints = workingChunk.tie_points

                    # Image Count (applied once, reversed): remove points seen in
                    # fewer than the minimum.  A point's image count does not
                    # change as others are removed, so after the first pass
                    # nothing remains below the minimum and this contributes
                    # nothing to later iterations.
                    if isRemovalIteration and imageCountEnabled and not cappedOut:
                        smallest = GetMinimumImageCount(workingChunk)
                        if smallest is not None and smallest < minimumImages:
                            selectedCount = SelectByImageCountBelow(
                                workingChunk, minimumImages)
                            if selectedCount and selectedCount > 0:
                                projectedRemaining = (
                                    CountValidPoints(workingChunk)
                                    - selectedCount)
                                if projectedRemaining < minimumPointsAllowed:
                                    stoppedReason = (
                                        "safety cap reached (removing {:,} "
                                        "more by Image Count would drop below "
                                        "the {:,}-point floor)".format(
                                            selectedCount, minimumPointsAllowed))
                                    print("-> {}".format(stoppedReason))
                                    ClearTiePointSelection(workingChunk)
                                    cappedOut = True
                                else:
                                    tiePoints.removeSelectedPoints()
                                    removedThisIteration += selectedCount

                    for criterionName, criterionEnum, targetValue, stepValue \
                            in enabledCriteria:
                        if cappedOut or not isRemovalIteration:
                            break
                        values = GetCriterionValues(workingChunk, criterionEnum)
                        if not values:
                            continue
                        worstValue = values[-1]
                        if worstValue <= targetValue:
                            continue  # this criterion already satisfied

                        if isPercentile:
                            thresholdValue, selectedCount = SelectByPercentile(
                                workingChunk, criterionEnum, percentToRemove,
                                targetValue)
                            if thresholdValue is None:
                                continue
                        else:
                            # Step the threshold down towards the target.
                            thisThreshold = max(
                                worstValue - stepValue, targetValue)
                            selectedCount = SelectByAbsoluteThreshold(
                                workingChunk, criterionEnum, thisThreshold)

                        if selectedCount <= 0:
                            continue

                        projectedRemaining = (
                            CountValidPoints(workingChunk) - selectedCount)
                        if projectedRemaining < minimumPointsAllowed:
                            stoppedReason = (
                                "safety cap reached (removing {:,} more by "
                                "{} would drop below the {:,}-point "
                                "floor)".format(
                                    selectedCount, criterionName,
                                    minimumPointsAllowed))
                            print("-> {}".format(stoppedReason))
                            ClearTiePointSelection(workingChunk)
                            cappedOut = True
                            break

                        tiePoints.removeSelectedPoints()
                        removedThisIteration += selectedCount

                    if isRemovalIteration and removedThisIteration == 0:
                        # The cap (or an edge case) blocked every removal.
                        # Discard the now-identical copy and stop.
                        if madeCopy:
                            document.remove([workingChunk])
                            document.chunk = previousChunk
                            workingChunk = previousChunk
                        if not cappedOut:
                            stoppedReason = (
                                "nothing removed in iteration "
                                "{}".format(iteration))
                            print("-> {}".format(stoppedReason))
                        break

                    operation = "Optimize Cameras"
                    storedSigmaNoughtBefore = ReadMetashapeSigmaNought(workingChunk)
                    # Removed before and put back when Optimize Cameras
                    # writes none, also when it stops.
                    storedSigmaNoughtText, isSigmaNoughtForgotten = ForgetMetashapeSigmaNought(workingChunk)
                    try:
                        workingChunk.optimizeCameras(**optimiseArguments)
                    finally:
                        isSigmaNoughtWritten = RestoreUnwrittenMetashapeSigmaNought(
                            workingChunk, storedSigmaNoughtText, isSigmaNoughtForgotten)

                    operation = "the computation of the metrics"
                    metrics = collectIterationMetrics(workingChunk)
                    storedSigmaNoughtAfter = metrics.get("metashapeSigma0")
                    metrics["metashapeSigma0"] = SigmaNoughtOfThisAdjustment(
                        storedSigmaNoughtBefore, storedSigmaNoughtAfter, isSigmaNoughtWritten)
                    metrics["metashapeSigma0Note"] = UnshownSigmaNoughtNote(
                        storedSigmaNoughtBefore, storedSigmaNoughtAfter, isSigmaNoughtWritten)
                    metrics["optimisationSettings"] = filterSettings
                except Exception as runError:
                    # Cancel in Metashape's progress window (or an error)
                    # during this iteration: treat it as a stop request,
                    # restore the last completed state where possible, and
                    # keep the results of the completed iterations.
                    stateNote = ""
                    if operation == "the chunk copy" or madeCopy:
                        stateNote = _DiscardUnfinishedIterationChunk(
                            document, previousChunk,
                            workingChunk if madeCopy else None,
                            knownChunkKeys, iteration)
                        workingChunk = previousChunk
                    elif operation == "the computation of the metrics":
                        finalStateRecorded = False
                        stateNote = (
                            " The run worked in place: iteration {} is "
                            "complete in the chunk but missing from the "
                            "results.".format(iteration))
                    elif removedThisIteration > 0:
                        finalStateRecorded = False
                        stateNote = (
                            " The run worked in place: the {} removed in "
                            "iteration {} {} removed and the chunk was not "
                            "re-optimised.".format(
                                FormatCountWithNoun(removedThisIteration, "tie point", "tie points"),
                                iteration, "stays" if removedThisIteration == 1 else "stay"))
                    elif iteration == 0 and not keepIterations:
                        # The row of the chunk as found still describes it.
                        stateNote = (" The run worked in place, and the chunk "
                                     "was not re-adjusted, so it is unchanged.")
                    if iteration == 0 and keepIterations:
                        stateNote += " The chunk \u201c{}\u201d is unchanged.".format(baseLabel)
                    # Iteration 0 removes nothing, so an interruption in it
                    # leaves no completed iteration.
                    stoppedReason = DescribeStoppedRun(
                        max(iteration - 1, 0), iterationCount, isUnlimited,
                        "during {} in iteration {}: {}".format(
                            operation, iteration,
                            DescribeException(runError)))
                    interruptionNote = "{}{}.{}".format(
                        stoppedReason[:1].upper(), stoppedReason[1:],
                        stateNote)
                    print("-> {}".format(interruptionNote))
                    break

                summaryRows.append(SummaryRow(iteration, workingChunk.label, workingChunk.key, removedThisIteration,
                                              metrics, summaryRows[-1]))
                self._PrintMetrics(iteration, metrics,
                                   removed=removedThisIteration, row=summaryRows[-1])
                if iteration == 0:
                    PrintConsoleLines("  " + line for line in AdjustmentComparisonLines(summaryRows[0], summaryRows[-1]))

                if not isUnlimited:
                    self.progressBar.setValue(iteration)
                QtWidgets.QApplication.processEvents()

                if saveAfterEach:
                    try:
                        document.save()
                    except Exception as saveError:
                        # Cancel in the progress window of the save (or a
                        # failed save) stops the run after this completed
                        # iteration, like Cancel in Optimize Cameras.
                        stoppedReason = DescribeStoppedRun(
                            iteration, iterationCount, isUnlimited,
                            "while saving the project after iteration "
                            "{}: {}".format(
                                iteration, DescribeException(saveError)))
                        interruptionNote = (
                            "{}{}. Iteration {} is complete, but the project "
                            "was not saved after it.".format(
                                stoppedReason[:1].upper(), stoppedReason[1:],
                                iteration))
                        print("-> {}".format(interruptionNote))
                        break

                if cappedOut:
                    break

                # Auto-stop on convergence (unlimited percentile mode
                # only): if the RMS reprojection error no longer improves,
                # further percentile steps buy nothing.  The stepped
                # absolute mode is not stopped this way, because its
                # small steps barely move the RMS while the targets are
                # still being approached.  Iteration 0 removes nothing, so
                # its change from the chunk as found is no step.
                currentRms = metrics["rms"]
                if (iteration > 0 and isUnlimited and isPercentile and _IsFinite(currentRms)
                        and _IsFinite(previousRms) and previousRms > 0):
                    relativeImprovement = (
                        (previousRms - currentRms) / previousRms)
                    if relativeImprovement < CONVERGENCE_RELATIVE_IMPROVEMENT:
                        stallCount += 1
                    else:
                        stallCount = 0
                    if stallCount >= CONVERGENCE_PATIENCE:
                        stoppedReason = (
                            "auto-stop, as the RMS reprojection error no "
                            "longer improves (the targets are not yet "
                            "reached)")
                        print("-> {}".format(stoppedReason))
                        previousRms = currentRms
                        break
                previousRms = currentRms

            # The chunk that holds the final state (the last kept copy,
            # the previous chunk after an interrupted copy, or the
            # filtered chunk itself in place) is counted directly: after
            # an interrupted in-place iteration it differs from the last
            # results row.
            finalPointCount = CountValidPoints(workingChunk)
            print("")
            print("Finished: {}".format(stoppedReason))
            print("Final valid tie points: {:,} "
                  "(removed {:,} from initial {:,}).".format(
                      finalPointCount,
                      initialPointCount - finalPointCount,
                      initialPointCount))
            PrintConsoleLines(ConsoleBlockEnd())
        finally:
            self._SetRunning(False)

        enabledCriterionNames = set(
            name for name, _, _, _ in enabledCriteria)
        if imageCountEnabled:
            enabledCriterionNames.add("Image Count")
        self._ShowResultsSummary(
            document, summaryRows, initialPointCount,
            keepIterations, stoppedReason, enabledCriterionNames,
            interruptionNote=interruptionNote,
            finalPointCount=finalPointCount,
            finalStateRecorded=finalStateRecorded)
        self._RefreshChunkInformation()

    @staticmethod
    def _PrintMetrics(iteration, metrics, removed=None, row=None):
        """The Console lines of one state of the run, "[as found]" for the
        chunk as found and "[iteration 03]" for an iteration
        (ConsoleStatePrefix): the metrics of CollectMetrics on one line, the
        largest movement of an image and of a marker since the previous
        state and the images no longer oriented (from *row*, SummaryRow),
        since the chunk as found for iteration 0, and the lines of
        CriterionCensusLines below it."""
        controlPart = (
            "control-point RMSE n/a"
            if metrics["controlRmse"] is None else
            "control-point RMSE {} m (control points: {})".format(
                FormatSignificantDigits(metrics["controlRmse"]), metrics["controlCount"]))
        checkPart = (
            "check-point RMSE n/a"
            if metrics.get("checkRmse") is None else
            "check-point RMSE {} m (check points: {})".format(
                FormatSignificantDigits(metrics["checkRmse"]), metrics["checkCount"]))
        tiePart = TiePointStandardDeviationConsoleText(metrics)
        minimumImageCount = metrics.get("minimumImageCount")
        imagePart = ("minimum image count n/a" if minimumImageCount is None
                   else "minimum image count {}".format(int(minimumImageCount)))
        sigma0 = metrics.get("sigma0")
        sigmaNote = metrics.get("sigma0Note")
        if sigma0 is not None:
            sigmaPart = "approximate sigma nought {:.4f}".format(sigma0)
            # The 2D tie points left out of it.
            exclusions = metrics.get("sigma0Exclusions")
            if exclusions:
                sigmaPart += " ({})".format(exclusions)
        elif sigmaNote:
            # The reason names the 2D tie points left out as well
            # (DescribeMissingSigmaNought).
            sigmaPart = "approximate sigma nought n/a ({})".format(sigmaNote)
        else:
            sigmaPart = "approximate sigma nought n/a"
        # Metashape's own sigma nought of the last adjustment.
        metashapeSigma0 = metrics.get("metashapeSigma0")
        sigmaPart += (" | sigma nought of Metashape n/a" if metashapeSigma0 is None
                      else " | sigma nought of Metashape {:.4f}".format(metashapeSigma0))
        removedPart = (
            "" if removed is None
            else " removed {:,} |".format(removed))
        print("{}{} tie points {:,} | RMS reprojection "
              "error {:.4f} px | per-image RMS reprojection error maximum "
              "{:.4f} px, minimum {:.4f} px | maximum reconstruction "
              "uncertainty {:.2f}, projection accuracy {:.2f}, "
              "reprojection error {:.3f} | {} | {} | {} | {} | {}".format(
                  ConsoleStatePrefix(iteration), removedPart, metrics["points"],
                  metrics["rms"], metrics["maximumImageRms"],
                  metrics["minimumImageRms"], metrics["reconstructionUncertaintyMaximum"],
                  metrics["projectionAccuracyMaximum"], metrics["reprojectionErrorMaximum"],
                  imagePart, sigmaPart, tiePart, controlPart, checkPart))
        # The chunk as found has no previous state, and iteration 0 is
        # measured from the chunk as found.
        if row is not None and iteration != STATE_AS_FOUND and (iteration > 0 or row.get("hasPreviousState")):
            print("  " + MovementConsoleText(row))
            if row.get("imagesNoLongerOriented") is not None:
                print("  " + LostOrientationConsoleText(row))
        for line in CriterionCensusLines(metrics):
            print("  " + line)

    def _ShowResultsSummary(self, document, summaryRows,
                            initialCount, keepIterations,
                            stoppedReason, enabledCriterionNames=None,
                            interruptionNote=None, finalPointCount=None,
                            finalStateRecorded=True):
        """Results dialog of a run.  *finalPointCount* is the number of
        valid tie points in the chunk that holds the final state (the
        last row's count when not given), and *finalStateRecorded* is
        False when an interrupted in-place iteration left the chunk in a
        state that no row describes, so that every row is marked as not
        kept."""
        if enabledCriterionNames is None:
            enabledCriterionNames = set()
        finalCount = (summaryRows[-1]["points"] if finalPointCount is None
                      else finalPointCount)
        totalRemoved = initialCount - finalCount
        lengthUnit = summaryRows[0].get("lengthUnit", "m")
        displayLabels = [
            FormatIterationChunkLabel(
                row, finalStateRecorded and rowIndex == len(summaryRows) - 1,
                keepIterations)
            for rowIndex, row in enumerate(summaryRows)]

        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle(
            "Results: Gradual Selection Filter")
        dialog.setWindowFlags(
            dialog.windowFlags()
            & ~QtCore.Qt.WindowContextHelpButtonHint)
        dialog.resize(1120, 760)
        layout = QtWidgets.QVBoxLayout(dialog)

        headerLabel = QtWidgets.QLabel(
            "Removed <b>{:,}</b> of <b>{:,}</b> tie points "
            "(<b>{:.1f}&nbsp;%</b>).  Final: <b>{:,}</b>.  "
            "End of the run: {}.".format(
                totalRemoved, initialCount,
                100.0 * totalRemoved / max(initialCount, 1),
                finalCount, html.escape(stoppedReason, quote=False)))
        headerLabel.setTextFormat(QtCore.Qt.RichText)
        headerLabel.setWordWrap(True)
        layout.addWidget(headerLabel)

        # A run ended by Stop, Cancel, or an error is marked clearly.  The
        # note can quote an exception, so it is shown as plain text.
        if interruptionNote:
            interruptionLabel = QtWidgets.QLabel(interruptionNote)
            interruptionLabel.setTextFormat(QtCore.Qt.PlainText)
            interruptionLabel.setWordWrap(True)
            interruptionLabel.setStyleSheet(
                "color: #9c2a00; font-weight: bold;")
            layout.addWidget(interruptionLabel)

        # Recommendation banner.
        recommendedIteration, recommendationReason = RecommendIteration(
            summaryRows)
        if recommendedIteration is not None:
            recommendedIndex = next(
                (rowIndex for rowIndex, row in enumerate(summaryRows)
                 if row["iteration"] == recommendedIteration), None)
            recommendedLabel = (
                displayLabels[recommendedIndex]
                if recommendedIndex is not None else "?")
            notKeptNote = ""
            if (not keepIterations and recommendedIndex is not None
                    and (recommendedIndex != len(summaryRows) - 1
                         or not finalStateRecorded)):
                notKeptNote = NotKeptNoteText(recommendedIteration)
            print(ResultsConsoleLine("suggested {} ({}): {}.{}".format(
                StateName(recommendedIteration), recommendedLabel, recommendationReason, notKeptNote)))
            # The reason can quote the fitted parameters of the chunk
            # metadata, so it is escaped as well.
            recommendationBanner = QtWidgets.QLabel(
                "{} <b>Suggested: {} ({})</b>: "
                "{}.{}".format(
                    SUGGESTION_STAR, StateName(recommendedIteration), html.escape(recommendedLabel, quote=False),
                    html.escape(recommendationReason, quote=False), notKeptNote))
            recommendationBanner.setTextFormat(QtCore.Qt.RichText)
            recommendationBanner.setWordWrap(True)
            recommendationBanner.setStyleSheet(
                "background: #e8f5e9; border: 1px solid #66a06a; "
                "padding: 6px; border-radius: 3px;")
            layout.addWidget(recommendationBanner)

        # The RMSE that the a priori standard deviations of the check points
        # alone give, under the suggestion.
        aPrioriRmseText = APrioriCheckPointRmseText(summaryRows)
        if aPrioriRmseText:
            aPrioriRmseLabel = QtWidgets.QLabel(aPrioriRmseText)
            aPrioriRmseLabel.setTextFormat(QtCore.Qt.PlainText)
            aPrioriRmseLabel.setWordWrap(True)
            aPrioriRmseLabel.setStyleSheet("color: #444; font-size: 11px;")
            layout.addWidget(aPrioriRmseLabel)

        # Explanatory note: criteria versus tracked metrics.
        noteLabel = QtWidgets.QLabel(
            "The <b>filtering-criteria</b> columns and graphs show the "
            "worst remaining value of each gradual-selection criterion"
            ": the quantities the targets act on.  The "
            "<b>tracked-metric</b> columns and graphs are additional "
            "diagnostics from the same adjustment, not filtering "
            "criteria.  Note that the unitless "
            "<i>reprojection-error criterion</i> (a criterion of "
            "Metashape&rsquo;s Clean Tie Points) is <i>not</i> the same "
            "as the overall <i>RMS reprojection error</i> in pixels.  "
            "A coloured square in the header of a column has the colour of its graph.")
        noteLabel.setTextFormat(QtCore.Qt.RichText)
        noteLabel.setWordWrap(True)
        noteLabel.setStyleSheet("color: #444; font-size: 11px;")
        layout.addWidget(noteLabel)

        # --- Metrics table (columns grouped: criteria | metrics) ---
        columnHeaders = ResultsTableHeaders(lengthUnit)
        sigmaNoughtColumn = columnHeaders.index(SIGMA_NOUGHT_HEADER)
        lostOrientationColumn = columnHeaders.index(LOST_ORIENTATION_HEADER)
        tiePointColumns = {columnHeaders.index(TiePointStandardDeviationHeader("Mean", lengthUnit)),
                           columnHeaders.index(TiePointStandardDeviationHeader("Median", lengthUnit))}
        criterionColumns = {4, 5, 6, 7}   # for tinted header text
        table = QtWidgets.QTableWidget(
            len(summaryRows), len(columnHeaders))
        table.setHorizontalHeaderLabels(columnHeaders)
        table.setAlternatingRowColors(True)
        table.setEditTriggers(
            QtWidgets.QAbstractItemView.NoEditTriggers)
        # No native row selection: the selection is shown by recolouring.
        table.setSelectionMode(
            QtWidgets.QAbstractItemView.NoSelection)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSectionResizeMode(
            QtWidgets.QHeaderView.ResizeToContents)
        table.setMaximumHeight(240)

        # Tint the filtering-criteria header labels.
        criterionHeaderColour = QtGui.QColor(120, 70, 160)
        for column in criterionColumns:
            headerItem = table.horizontalHeaderItem(column)
            if headerItem is not None:
                headerItem.setForeground(
                    QtGui.QBrush(criterionHeaderColour))

        recommendedBrush = QtGui.QBrush(QtGui.QColor(232, 245, 233))
        boldFont = QtGui.QFont()
        boldFont.setBold(True)
        for rowIndex, row in enumerate(summaryRows):
            isRecommended = (row["iteration"] == recommendedIteration)
            values = ResultsTableValues(row, displayLabels[rowIndex], isSuggested=isRecommended)
            for columnIndex, value in enumerate(values):
                item = QtWidgets.QTableWidgetItem(value)
                item.setTextAlignment(QtCore.Qt.AlignCenter)
                if isRecommended:
                    item.setBackground(recommendedBrush)
                    item.setFont(boldFont)
                # A sigma nought without a value shows the dash, and its
                # tooltip gives the reason and the counts
                # (DescribeMissingSigmaNought).
                if (columnIndex == sigmaNoughtColumn and row.get("sigma0") is None
                        and row.get("sigma0Note")):
                    item.setToolTip("\u03c3\u2080 is not given: {}.".format(row["sigma0Note"]))
                # A sigma nought that leaves out 2D tie points names them in
                # its tooltip.
                if (columnIndex == sigmaNoughtColumn and row.get("sigma0") is not None
                        and row.get("sigma0Exclusions")):
                    item.setToolTip("{}.".format(row["sigma0Exclusions"]))
                # A missing tie-point sigma gives the reason in its tooltip.
                if (columnIndex in tiePointColumns and row.get("meanTiePointStandardDeviation") is None
                        and row.get("tiePointStandardDeviationNote")):
                    item.setToolTip("No tie-point \u03c3: {}.".format(row["tiePointStandardDeviationNote"]))
                # The images that the iteration left without an orientation
                # are named in the tooltip of their count.
                if columnIndex == lostOrientationColumn and row.get("imagesNoLongerOriented"):
                    item.setToolTip("No longer oriented: {}.".format(
                        LostOrientationLabelsText(row["imagesNoLongerOriented"])))
                table.setItem(rowIndex, columnIndex, item)
        layout.addWidget(table)

        # The images that the run left without an orientation.
        lostOrientationText = LostOrientationSummaryText(summaryRows)
        if lostOrientationText:
            print(ResultsConsoleLine(lostOrientationText))
            lostOrientationLabel = QtWidgets.QLabel(lostOrientationText)
            lostOrientationLabel.setTextFormat(QtCore.Qt.PlainText)
            lostOrientationLabel.setWordWrap(True)
            lostOrientationLabel.setStyleSheet("color: #444; font-size: 11px;")
            layout.addWidget(lostOrientationLabel)

        # How far the images and the markers moved between the iterations.
        movementText = MovementSummaryText(summaryRows)
        if movementText:
            print(ResultsConsoleLine(movementText))
            movementLabel = QtWidgets.QLabel(movementText)
            movementLabel.setTextFormat(QtCore.Qt.PlainText)
            movementLabel.setWordWrap(True)
            movementLabel.setStyleSheet("color: #444; font-size: 11px;")
            layout.addWidget(movementLabel)

        # --- Graphs in a scroll area, grouped into two sections ---
        # The graphs show iterations 0 to N.  The chunk as found, whose
        # adjustment can differ from that of the filter, is in the table.
        graphRows = [row for row in summaryRows if not IsStateAsFound(row)]
        iterations = [row["iteration"] for row in graphRows]
        hasControl = any(
            row["controlRmse"] is not None for row in graphRows)
        hasCheck = any(
            row.get("checkRmse") is not None for row in graphRows)
        hasCovariance = any(
            row["meanTiePointStandardDeviation"] is not None for row in graphRows)

        self._resultCharts = []
        # The colour of every graph drawn, by its name (RESULTS_GRAPH_COLOURS),
        # which the header of its column shows as a key.
        self._resultGraphColours = {}
        graphContainer = QtWidgets.QWidget()
        graphGrid = QtWidgets.QGridLayout(graphContainer)
        graphGrid.setSpacing(8)
        gridState = {"row": 0}

        addResultsSectionHeader = functools.partial(_AddResultsSectionHeader, graphGrid, gridState)

        addResultsCharts = functools.partial(self._AddResultsCharts, graphGrid, gridState)

        # Section 1: filtering criteria (worst value per iteration).
        addResultsSectionHeader(
            "Filtering criteria: worst remaining value per "
            "iteration (what the targets act on)")
        # The colour of each graph comes from RESULTS_GRAPH_COLOURS.
        criteriaSpecs = [
            ("Reconstruction Uncertainty",
             "Reconstruction uncertainty (worst)",
             [summaryRow["reconstructionUncertaintyMaximum"] for summaryRow in graphRows]),
            ("Projection Accuracy",
             "Projection accuracy (worst)",
             [summaryRow["projectionAccuracyMaximum"] for summaryRow in graphRows]),
            ("Reprojection Error",
             "Maximum reprojection error (criterion)",
             [summaryRow["reprojectionErrorMaximum"] for summaryRow in graphRows]),
            ("Image Count",
             "Image count (worst: the fewest images)",
             [summaryRow.get("minimumImageCount") for summaryRow in graphRows]),
        ]
        criteriaCharts = []
        for criterionName, title, series in criteriaSpecs:
            isUsed = criterionName in enabledCriterionNames
            unusedSuffix = "" if isUsed else "  (unused filtering criterion)"
            colour = RESULTS_GRAPH_COLOURS[criterionName] if isUsed else UNUSED_CRITERION_COLOUR
            self._resultGraphColours[criterionName] = colour
            criteriaChart = LineChartWidget(title + unusedSuffix, iterations, series, QtGui.QColor(*colour))
            if criterionName == "Image Count":
                criteriaChart.minimumSpan = 0.0
            PrepareResultsChart(criteriaChart, PercentChangeChartTitle(title) + unusedSuffix)
            criteriaCharts.append(criteriaChart)
        addResultsCharts(criteriaCharts)

        # Section 2: tracked quality metrics (not filtering criteria),
        # each with its name and the unit of its values.
        addResultsSectionHeader(
            "Tracked quality metrics (not filtering criteria)")
        metricSpecs = [
            ("Tie-point count", None,
             [summaryRow["points"] for summaryRow in graphRows]),
            ("RMS reprojection error", "px",
             [summaryRow["rms"] for summaryRow in graphRows]),
            ("Maximum per-image RMS reprojection error", "px",
             [summaryRow["maximumImageRms"] for summaryRow in graphRows]),
            ("Minimum per-image RMS reprojection error", "px",
             [summaryRow["minimumImageRms"] for summaryRow in graphRows]),
        ]
        if hasCovariance:
            metricSpecs.append((
                "Mean tie-point \u03c3", lengthUnit,
                [summaryRow["meanTiePointStandardDeviation"] for summaryRow in graphRows]))
            metricSpecs.append((
                "Median tie-point \u03c3", lengthUnit,
                [summaryRow.get("medianTiePointStandardDeviation") for summaryRow in graphRows]))
        if any(summaryRow.get("sigma0") is not None for summaryRow in graphRows):
            metricSpecs.append((
                "\u03c3\u2080, reference standard deviation (approximate)", None,
                [summaryRow.get("sigma0") for summaryRow in graphRows]))
        if hasControl:
            metricSpecs.append((
                "Control-point RMSE", "m",
                [summaryRow["controlRmse"] for summaryRow in graphRows]))
        if hasCheck:
            metricSpecs.append((
                "Check-point RMSE", "m",
                [summaryRow.get("checkRmse") for summaryRow in graphRows]))
        metricCharts = []
        for name, unit, series in metricSpecs:
            self._resultGraphColours[name] = RESULTS_GRAPH_COLOURS[name]
            chart = LineChartWidget(ValueChartTitle(name, unit), iterations, series,
                                    QtGui.QColor(*RESULTS_GRAPH_COLOURS[name]))
            chart.minimumSpan = ResultsChartMinimumSpan(chart.title, chart.yValues)
            PrepareResultsChart(chart, PercentChangeChartTitle(name))
            metricCharts.append(chart)
        addResultsCharts(metricCharts)
        # The header of each column with a graph shows its colour as a key.
        SetHeaderColourKeys(table, ResultsHeaderKeyColours(lengthUnit, self._resultGraphColours))

        # --- The switch of the graphs: values, or the change from
        # iteration 0 in % (the table always shows values) ---
        graphViewRow = QtWidgets.QHBoxLayout()
        graphViewRow.addWidget(QtWidgets.QLabel("Graphs:"))
        valuesRadioButton = QtWidgets.QRadioButton("Values")
        valuesRadioButton.setChecked(True)
        percentChangeRadioButton = QtWidgets.QRadioButton("Change from iteration 0 in %")
        percentChangeRadioButton.setToolTip(
            "Each graph shows 100 \u00d7 (value \u2212 value at iteration 0) / value at iteration 0.\n"
            "A graph without a finite, non-zero value at iteration 0 stays in values,\n"
            "and its title says so. The table always shows values.")
        graphViewRow.addWidget(valuesRadioButton)
        graphViewRow.addWidget(percentChangeRadioButton)
        graphViewRow.addStretch()
        layout.addLayout(graphViewRow)
        percentChangeRadioButton.toggled.connect(functools.partial(ShowResultChartsAs, list(self._resultCharts)))

        scrollArea = QtWidgets.QScrollArea()
        scrollArea.setWidgetResizable(True)
        scrollArea.setWidget(graphContainer)
        scrollArea.setMinimumHeight(360)
        layout.addWidget(scrollArea, 1)

        # --- Iteration selector (drives highlighting) ---
        manageRow = QtWidgets.QHBoxLayout()
        manageRow.addWidget(QtWidgets.QLabel("Iteration:"))
        iterationComboBox = QtWidgets.QComboBox()
        defaultItemIndex = len(summaryRows) - 1
        for itemIndex, row in enumerate(summaryRows):
            suffix = (" {} suggested".format(SUGGESTION_STAR)
                      if row["iteration"] == recommendedIteration
                      else "")
            iterationComboBox.addItem(
                "{}  ({}){}".format(
                    displayLabels[itemIndex],
                    "as found" if row["iteration"] == STATE_AS_FOUND else "iteration {}".format(row["iteration"]),
                    suffix),
                itemIndex)
            if row["iteration"] == recommendedIteration:
                defaultItemIndex = itemIndex
        manageRow.addWidget(iterationComboBox)

        # Recolour the selected iteration's numbers dark green and bold
        # (matching the highlighted dot on the graphs), keep the
        # recommended row bold, and show everything else plain.
        selectionGreen = QtGui.QBrush(QtGui.QColor(20, 120, 40))
        plainBlack = QtGui.QBrush(QtGui.QColor(0, 0, 0))
        plainFont = QtGui.QFont()
        emphasisFont = QtGui.QFont()
        emphasisFont.setBold(True)

        highlightSelectedIteration = functools.partial(self._HighlightSelectedIteration, emphasisFont, plainBlack, plainFont, recommendedIteration, selectionGreen, summaryRows, table)

        iterationComboBox.currentIndexChanged.connect(highlightSelectedIteration)
        # The activated signal comes only from a choice of the user, not
        # from the initial selection of the suggested iteration.
        iterationComboBox.activated.connect(functools.partial(_EchoIterationChoice, summaryRows, displayLabels))

        # --- Activate / delete (only when iterations are kept) ---
        if keepIterations and len(summaryRows) > 1:
            activateButton = QtWidgets.QPushButton("Activate selected")
            deleteOthersButton = QtWidgets.QPushButton(
                "Keep selected only (delete other iterations)")
            manageRow.addWidget(activateButton)
            manageRow.addWidget(deleteOthersButton)

            findChunkByKey = functools.partial(_FindChunkByKey, document)

            getSelectedChunkKey = functools.partial(_GetSelectedChunkKey, iterationComboBox, summaryRows)

            activateSelectedChunk = functools.partial(_ActivateSelectedChunk, dialog, document, findChunkByKey, getSelectedChunkKey)

            deleteOtherIterationChunks = functools.partial(_DeleteOtherIterationChunks, deleteOthersButton, dialog, document, findChunkByKey, getSelectedChunkKey, summaryRows)

            activateButton.clicked.connect(activateSelectedChunk)
            deleteOthersButton.clicked.connect(deleteOtherIterationChunks)

        manageRow.addStretch()
        layout.addLayout(manageRow)

        closeButton = QtWidgets.QPushButton("Close")
        closeButton.clicked.connect(dialog.close)
        buttonRow = QtWidgets.QHBoxLayout()
        buttonRow.addStretch()
        buttonRow.addWidget(closeButton)
        layout.addLayout(buttonRow)

        # Apply the initial highlight (the suggested iteration).
        iterationComboBox.setCurrentIndex(defaultItemIndex)
        highlightSelectedIteration(defaultItemIndex)

        dialog.exec_()

    def _HighlightSelectedIteration(self, emphasisFont, plainBlack, plainFont, recommendedIteration, selectionGreen, summaryRows, table, selectedItemIndex):
        if selectedItemIndex is None or selectedItemIndex < 0:
            return
        selectedIteration = summaryRows[selectedItemIndex]["iteration"]
        for rowIndex, rowData in enumerate(summaryRows):
            isSelected = (
                rowData["iteration"] == selectedIteration)
            isRecommended = (
                rowData["iteration"] == recommendedIteration)
            for columnIndex in range(table.columnCount()):
                item = table.item(rowIndex, columnIndex)
                if item is None:
                    continue
                if isSelected:
                    item.setForeground(selectionGreen)
                    item.setFont(emphasisFont)
                else:
                    item.setForeground(plainBlack)
                    item.setFont(
                        emphasisFont if isRecommended else plainFont)
        for chart in self._resultCharts:
            chart.SetHighlight(selectedIteration)

    def _AddResultsCharts(self, graphGrid, gridState, charts):
        column = 0
        for chart in charts:
            graphGrid.addWidget(chart, gridState["row"], column)
            self._resultCharts.append(chart)
            column += 1
            if column == 2:
                column = 0
                gridState["row"] += 1
        if column != 0:
            gridState["row"] += 1


# ===================================================================
# Menu registration
# ===================================================================

def _ShowGradualSelectionFilterDialog():
    parent = QtWidgets.QApplication.activeWindow()
    dialog = GradualSelectionDialog(parent)
    dialog.exec_()


def RegisterMenuItem():
    """Register this tool in the MetaFOCUS menu."""

    # Remove any existing item first, so reloading the toolbox
    # never creates a duplicate menu entry.
    try:
        Metashape.app.removeMenuItem(_MENU_LABEL)
    except Exception as suppressedError:
        ReportSuppressedError("gradualSelectionFilter.RegisterMenuItem", suppressedError)
    iconPath = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "icons", _ICON_NAME)
    if os.path.exists(iconPath):
        Metashape.app.addMenuItem(
            _MENU_LABEL, _ShowGradualSelectionFilterDialog, icon=iconPath)
    else:
        Metashape.app.addMenuItem(_MENU_LABEL, _ShowGradualSelectionFilterDialog)
