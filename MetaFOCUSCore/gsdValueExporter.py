"""
GSD Value Exporter for Agisoft Metashape Professional
======================================================

Exports the ground sampling distance (GSD, also called ground sample
distance or ground sampled distance) at the sampled pixel positions of
one or more images as CSV files for analysis in MATLAB, Excel, or similar
software.  Optionally creates two inspection markers per image, at the
locations of the maximum and minimum GSD, for visual inspection.

Processes a single image, all oriented images, or the selected
oriented images of the active chunk.  Only master images are
processed: no slave images of a multi-camera rig and no keyframes of
an animation track.

For each sampled pixel position, exports:
    - Pixel coordinates (column, row)
    - 3D hit point in the coordinates of the chunk CRS (easting,
      northing, and height, or longitude, latitude, and height in a
      geographic CRS)
    - Slant distance from the perspective centre (for an image with
      rolling-shutter compensation, from the point of the ray nearest
      to Camera.center)
    - Cosine of the incidence angle (cos(theta))
    - Incidence angle theta (degrees)
    - Ground sampling distances along the image row and column, the
      angle phi between the two projected steps (degrees), their
      geometric mean, and the area-equivalent GSD, the GSD of MetaFOCUS

The lengths are in metres, or in chunk units in a chunk without a
physical scale, as the Units comment line of the CSV says: the column
names carry no unit.  Every file name contains the camera key of its
image, so that a name never passes from one image to another, and the
comment lines record the camera key as well.

Works in plane, triangular mesh, or raster DEM mode with the engine
shared with the Image Footprint Generator and the Image Parameter
Exporter, so the three tools give identical statistics for the same
image, surface mode, and pixel step.

Author:      Geert J. Verhoeven (ORCID: 0000-0003-4825-9604)
Version:     1.0.1 (2026-11-22)
Date:        2026-06-12
Licence:     GPL-3.0-or-later WITH GPL-3.0-linking-exception
Contact:     geert.verhoeven@univie.ac.at
Requires:    Agisoft Metashape Professional 2.x+
Developed for: Agisoft Metashape Professional 2.3.2

Dependencies:
    - photogrammetryUtilities.py (must be in the same directory).

Usage:
    MetaFOCUS > Surface-Derived Project Outputs > GSD Value Exporter
    (installed by the MetaFOCUS loader).

Notes:
    - The GSD is the photosite pitch projected onto the surface.  At
      every sampled pixel position, four auxiliary rays through the
      midpoints of the edges of the corresponding photosite (+-1/2 along
      the row and the column in pixel coordinates) are intersected with
      the local tangent plane at the central ray's hit point.  The two
      central differences are the projected row and column steps a and
      b, whose lengths are the ground sampling distances along the image
      row and column directions (GSD_row and GSD_column) and whose angle
      is phi (Phi_deg).  The GSD is the area-equivalent GSD
      (GSD_areaEquivalent), sqrt(|a x b|) = sqrt(GSD_row x GSD_column x
      sin(phi)), the side of the square with the area that the two
      steps span.  Their geometric mean
      sqrt(GSD_row x GSD_column) (GSD_geometricMean) equals it for
      perpendicular steps and exceeds it otherwise.
    - Triangular mesh and raster DEM modes use the interpolated or
      derived surface normal to define the local tangent plane for
      the auxiliary-ray intersections.
    - Every surface mode rejects rays with cos(theta) < 0.1, that is
      incidence angles beyond about 84.3 deg, where the projected
      photosite pitch is stretched more than tenfold.
    - The inspection markers have their reference location set but
      disabled, so they never act as control points, and no image
      measurements, so they are no check points either.  They are kept
      in the marker group "GSD extremes".
"""

import Metashape
import collections
import csv
import math
import os
import re
import tempfile
import importlib

from PySide2 import QtWidgets, QtCore

# --------------- shared utilities import ----------------------------
from . import photogrammetryUtilities
importlib.reload(photogrammetryUtilities)

from . import _METAFOCUS_VERSION
from .photogrammetryUtilities import (
    ReportSuppressedError, INSPECTION_MARKER_GROUP_LABEL, INSPECTION_MARKER_META_KEY,
    RollingShutterConsoleLines, RollingShutterModelName, RollingShutterRayText,
    FormatCountWithNoun, JoinWithAnd, NormalisedProjectPath, IsReplaceableInspectionMarker,
    DescribeException,
    IsMasterImage,
    ConvertToCamelCase,
    BuildMeshBvh, BuildTrackIdToPointMap, GetValidProjections,
    PlaneSpreadOfImage, PlaneSpreadWarningLine, PlaneSpreadSummaryText, ChunkHasTriangularMesh,
    PhysicalChunkScale, DescribeMissingPhysicalScale, LengthUnitsText, ComputePercentile, CrsKind,
    GRAZING_COSINE_THRESHOLD, SamplePixelGrid, CollectGsdAtSampledPositions,
    IsCameraUsable, PlaneSurface, MeshSurface, DemSurface, ImageRays, ExactRayText,
    RecordRasterDemReport, DescribeRasterDemMisses, PerspectiveCentreBelowRasterDemNote,
    MarkerReferenceLocationFromChunkCoordinates,
    CSV_FORMAT_ROW_LABEL_TEXT, FillCsvFormatComboBox, ChosenCsvFormat, DataRowWithDecimalSeparator,
    FormatLengthWithDecimals, FormatLengthForCsv, DecimalsToResolve,
    ConsoleBlockEnd, ConsoleBlockStartOfChunk, PrintConsoleLines,
    SelectedMasterImages, SelectedImagesStatusText, SelectedImagesConsoleLines, NoSelectedImageText,
    SelectedImageKeys, ConsolePath, CrsUnitNames,
    DeclinedReplacingText, ReplaceUnconfirmedFileQuestionText, ReplacesUnconfirmedFile,
)

# Label of the marker group that holds the MaxGSD and MinGSD markers.
_GSD_MARKER_GROUP_LABEL = INSPECTION_MARKER_GROUP_LABEL

# Metadata of the inspection markers (photogrammetryUtilities.IsInspectionMarker).
INSPECTION_MARKER_META_VALUE = "GSD extremum (GSD Value Exporter)"

# Comment line of the GSD CSV of an image whose rays follow the
# rolling-shutter motion: from where SlantDistance is measured.
ROLLING_SHUTTER_SLANT_DISTANCE_TEXT = (
    "from the point of each ray nearest to the perspective centre of the image (Camera.center), a convention, "
    "because the line of a ray of an image with rolling-shutter compensation does not show where on it the "
    "perspective centre of its row lies")

# The coordinates of a GSD record resolve its GSD in the Console and a
# tenth of it in the CSV where the decimals of CoordinateDecimals do not.
COORDINATE_STEP_IN_GSD_CONSOLE = 1.0
COORDINATE_STEP_IN_GSD_CSV = 0.1

# No degree of latitude or longitude on the Earth is longer, in metres
# (111,694 m for a degree of latitude at a pole, 111,320 m for a degree of
# longitude on the equator, WGS 84), so that the step of a coordinate in
# degrees is never longer on the ground than the step that it stands for.
LONGEST_DEGREE_METRES = 111700.0


# ===================================================================
# Images, file names, and output coordinates
# ===================================================================

def SurfaceModeName(useMesh, useDem):
    """Name of the surface mode written to the CSV, the Console, and the
    file names: "Raster DEM", "Triangular mesh", or "Plane"."""
    if useDem:
        return "Raster DEM"
    if useMesh:
        return "Triangular mesh"
    return "Plane"


def DuplicatedImageLabels(cameras):
    """Labels carried by more than one image in *cameras* (images with
    the same file name from two cards or folders).  Computed once per
    list, so that labelling every image stays linear in the number of
    images."""
    labelCounts = collections.Counter(camera.label for camera in cameras)
    return {label for label, labelCount in labelCounts.items() if labelCount > 1}


def UniqueImageLabel(camera, duplicatedLabels):
    """Label of the image *camera*, followed by its key when its label is
    one of *duplicatedLabels* (see DuplicatedImageLabels), so that the
    image list, the marker labels, the CSV comment lines, and the Console
    keep images with the same label apart.  The file names carry the key
    of every image (see BuildGsdCsvFileName)."""
    if camera.label in duplicatedLabels:
        return "{} (key {})".format(camera.label, camera.key)
    return camera.label


def SafeFileNamePart(text):
    """*text* for use inside a file name on any system: every run of
    characters other than letters (including letters with diacritics
    and other Unicode letters), digits, '.', '_', and '-' becomes one
    underscore.  This removes every character that Windows forbids in
    file names and keeps labels that differ only in such letters
    apart."""
    safeText = re.sub(r"[^\w.-]+", "_", text or "").strip("_")
    return safeText or "unnamed"


def BuildGsdCsvFileName(imageLabel, imageKey, projectName, chunkName, surfaceModeName):
    """File name of the GSD CSV of one image,
    gsdValueExport_<label>_key<N>_<project>_<chunk>_<surface mode>.csv,
    from the image label (Metashape's label, see SafeFileNamePart), its
    camera key N, the project, the chunk (ChunkFileNameLabel, with the
    chunk key), and the surface mode.  The key
    keeps the files of two images apart, also when their labels are
    equal or become equal in a file name, so the "(key N)" that
    UniqueImageLabel adds to a label that other images share is not
    repeated in the file name.  The project, the chunk, and the surface
    mode keep the exports of other projects, chunks, and surface modes
    into the same folder apart."""
    return "gsdValueExport_{}_key{}_{}_{}_{}.csv".format(
        SafeFileNamePart(imageLabel), imageKey,
        ConvertToCamelCase(projectName) if projectName else "project",
        ConvertToCamelCase(chunkName) if chunkName else "chunk",
        ConvertToCamelCase(surfaceModeName))


def MasterImagesOfChunk(chunk):
    """Every master image of *chunk* (see IsMasterImage), oriented or
    not, in chunk order: the images among which BuildGsdCsvFileNamesByKey
    checks the CSV file names, so that the name of an image depends
    neither on the images that a run exports nor on the images that are
    oriented."""
    return [camera for camera in chunk.cameras if IsMasterImage(camera)]


def BuildGsdCsvFileNamesByKey(cameras, projectName, chunkName, surfaceModeName):
    """File name per camera key (see BuildGsdCsvFileName) of *cameras*,
    all the master images of the chunk (MasterImagesOfChunk): an image
    gets the same name in every run, whichever images the run exports,
    and as the default name of a single-image run.  Every name contains
    the camera key, which no other image of the chunk has, so a name
    never passes from one image of a project to another when images are
    removed or renamed: a renamed image gets a new name, but no export of
    the project replaces the file of another of its images (a copy of the
    project with the same file name gives the same names, see
    ForeignExistingGsdFiles).  Labels that become equal in a file name
    ("A B" and "A_B") or differ only in case ("IMG_1" and "img_1", which
    Windows and macOS do not tell apart) stay apart through the key.  As
    a safeguard, a name that an image earlier in the chunk already took,
    compared without case, gets the camera key appended again until it
    is free."""
    fileNameByKey = {}
    takenNames = set()
    for camera in cameras:
        fileName = BuildGsdCsvFileName(
            camera.label, camera.key, projectName, chunkName, surfaceModeName)
        while fileName.lower() in takenNames:
            fileName = "{}_key{}.csv".format(
                os.path.splitext(fileName)[0], camera.key)
        takenNames.add(fileName.lower())
        fileNameByKey[camera.key] = fileName
    return fileNameByKey


def ChunkFileNameLabel(chunk):
    """Label of *chunk* for the CSV file names: its label followed by its
    key, "Chunk 1 (key 3)" (Chunk1key3 in the file name), "chunk (key 3)"
    for a chunk without a label.  Every name carries the chunk key, which
    Metashape gives a copied chunk anew, so that a name never passes from
    one chunk to another.  A chunk without a key keeps its label."""
    chunkKey = getattr(chunk, "key", None)
    if chunkKey is None:
        return chunk.label or ""
    return "{} (key {})".format(chunk.label or "chunk", chunkKey)


def SelectExportCameras(chunk, selectionMode, cameraKey=None):
    """Images to export, in chunk order: for "single" the oriented
    master image whose key is *cameraKey* (an empty list when there is
    none), for "selected" the selected oriented master images, and for
    "all" every oriented master image."""
    orientedMasterCameras = [camera for camera in chunk.cameras
                             if IsMasterImage(camera)
                             and camera.transform is not None]
    if selectionMode == "single":
        return [camera for camera in orientedMasterCameras
                if camera.key == cameraKey][:1]
    if selectionMode == "selected":
        return [camera for camera in orientedMasterCameras if camera.selected]
    return orientedMasterCameras


def SelectionClearedConsoleLine():
    """The Console line of a run that added inspection markers while images
    were selected.  Adding markers clears the selection of images that
    Metashape reports, and Metashape clears it again after the run, so the
    tool cannot select the images again (observed in Metashape 2.3.2 on
    17 November 2026).  A click on an image that the Photos pane still
    highlights does not select it in Metashape, so the line says to click
    an image that is not highlighted first."""
    return ("Adding the inspection markers clears the selection of images in Metashape, although the Photos "
            "pane may still highlight them. Before the next run on selected images, click an image that is not "
            "highlighted, and then select the images again, because a click on an image that the pane still "
            "highlights does not select it in Metashape.")


# The number of replaced files that the Console names by file name.
REPLACED_FILE_NAME_LIMIT = 20


def ReplacedFilesConsoleLine(replacedFilePaths, limit=REPLACED_FILE_NAME_LIMIT):
    """The Console line after an export that replaced existing files: their
    number and the names of at most *limit* of them, for example "2
    existing files replaced: a.csv and b.csv."."""
    names = [os.path.basename(path) for path in replacedFilePaths]
    if len(names) <= limit:
        listedNames = JoinWithAnd(names)
    else:
        listedNames = "{}, and {:,} more".format(", ".join(names[:limit]), len(names) - limit)
    return "{} replaced: {}.".format(FormatCountWithNoun(len(names), "existing file", "existing files"), listedNames)


def ExportSelectionStatusText(chunk):
    """The line below Selected images only: how many master images
    Metashape reports as selected in *chunk* (SelectedMasterImages) and how
    many of them are oriented, or that there is no active chunk."""
    if chunk is None:
        return "No active chunk."
    selectedImages = SelectedMasterImages(chunk)
    return SelectedImagesStatusText(len(selectedImages),
                                    sum(1 for camera in selectedImages if camera.transform is not None))


def OutputCoordinateKind(chunk):
    """Kind of the coordinates in which the hit points are written (see
    CrsKind): the kind of the chunk CRS when the chunk has a transform,
    otherwise "none", because without a transform the hit points are
    internal chunk coordinates."""
    if getattr(chunk.transform, "matrix", None) is None:
        return "none"
    return CrsKind(chunk.crs)


def DescribeOutputCoordinateSystem(chunk):
    """Name of the CRS of the hit points for the CSV
    header.  Without a chunk transform the hit points are internal chunk
    coordinates, whatever CRS the reference settings name."""
    if getattr(chunk.transform, "matrix", None) is None:
        return "none (internal chunk coordinates)"
    if chunk.crs is None:
        return "none (local coordinates)"
    return chunk.crs.name


def CoordinateDecimals(coordinateKind, forCsv, gsdValue=None):
    """Decimals of (horizontal, height) coordinates.  Longitude and
    latitude in degrees need nine decimals (0.1 mm), metric coordinates
    and heights get four on screen (0.1 mm) and six in CSV files.  Each
    gets more where these do not resolve *gsdValue* on screen or a tenth
    of it in CSV files (0.1 mm and 1 um are more than the GSD of a macro
    project), with a degree taken as
    LONGEST_DEGREE_METRES.  A CRS of an unknown kind is treated as
    geographic, which costs only a few extra digits when its coordinates
    are in metres."""
    metricDecimals = 6 if forCsv else 4
    angularDecimals = 9
    if gsdValue is not None:
        coordinateStep = (COORDINATE_STEP_IN_GSD_CSV if forCsv else COORDINATE_STEP_IN_GSD_CONSOLE) * gsdValue
        metricDecimals = max(metricDecimals, DecimalsToResolve(coordinateStep))
        angularDecimals = max(angularDecimals, DecimalsToResolve(coordinateStep / LONGEST_DEGREE_METRES))
    if coordinateKind in ("geographic", "unknown"):
        return angularDecimals, metricDecimals
    return metricDecimals, metricDecimals


def FormatCrsPoint(record, coordinateKind):
    """Hit point of a GSD record as "(X, Y, Z)" for the Console, with the
    decimals of CoordinateDecimals for the GSD of the record."""
    horizontalDecimals, heightDecimals = CoordinateDecimals(
        coordinateKind, forCsv=False, gsdValue=record.get("gsdValue"))
    return "({:.{}f}, {:.{}f}, {:.{}f})".format(
        record["crsX"], horizontalDecimals,
        record["crsY"], horizontalDecimals,
        record["crsZ"], heightDecimals)


def BuildBatchExportSummary(totalCount, attemptedCount, successCount,
                            cancelled, failures, outputDirectory):
    """Final message of a GSD export: how many images were processed (or
    after how many of how many the run was cancelled), how many were
    exported, which images failed and why, and where the CSV files are."""
    if cancelled:
        lines = ["Cancelled after {} of {}, {} exported.".format(
            attemptedCount, FormatCountWithNoun(totalCount, "image", "images"),
            successCount)]
    else:
        lines = ["Processed {}, {} exported.".format(
            FormatCountWithNoun(attemptedCount, "image", "images"), successCount)]
    if failures:
        lines.append("")
        lines.append("Failed ({}):".format(len(failures)))
        lines.extend(failures[:10])
        if len(failures) > 10:
            lines.append("... and {} more (see the Console)".format(
                len(failures) - 10))
    lines.append("")
    lines.append("CSV files saved to:")
    lines.append(outputDirectory)
    return "\n".join(lines)


# ===================================================================
# Grazing incidence
# ===================================================================
# Every surface mode rejects rays meeting the surface beyond the
# shared GRAZING_COSINE_THRESHOLD (cos(theta) < 0.1, about 84.3
# degrees), where the projected photosite pitch is stretched more than
# tenfold.
# Values beyond the threshold are exact on the plane but describe the
# plane extrapolated towards the horizon, so plane mode rejects them as
# well.


# ===================================================================
# GSD at sampled positions: (records, grazing-rejected ray count)
# ===================================================================

def CollectGsdAtSampledPositionsOnPlane(camera, validProjections, chunkScale,
                              pixelStep, chunk, imageRays=None):
    """GSD records at the sampled positions on the tie-point plane of
    this image, from the shared engine, with the rays of *imageRays*
    (ImageRays of the image by default).  Returns (recordList,
    thresholdRejectedCount), with None as the count when no plane could
    be fitted or the image cannot be used, so that no ray was cast."""
    if not IsCameraUsable(camera):
        return ([], None)
    surface = PlaneSurface(camera, validProjections, chunkScale, chunk, imageRays)
    if not surface.isUsable:
        return ([], None)
    records, thresholdRejectedCount, _ = CollectGsdAtSampledPositions(
        camera, surface, pixelStep, includeCrs=True)
    return (records, thresholdRejectedCount)


def CollectGsdAtSampledPositionsOnMesh(camera, bvhData, chunkScale,
                             pixelStep, chunk, imageRays=None):
    """GSD records at the sampled positions on the triangular mesh, from
    the shared engine, with the rays of *imageRays* (ImageRays of the
    image by default).  Returns (recordList, thresholdRejectedCount),
    with None as the count when no surface could be built or the image
    cannot be used, so that no ray was cast."""
    if not IsCameraUsable(camera):
        return ([], None)
    surface = MeshSurface(camera, bvhData, chunkScale, chunk, imageRays)
    if not surface.isUsable:
        return ([], None)
    records, thresholdRejectedCount, _ = CollectGsdAtSampledPositions(
        camera, surface, pixelStep, includeCrs=True)
    return (records, thresholdRejectedCount)


def CollectGsdAtSampledPositionsOnDem(camera, chunk, chunkScale, pixelStep, rasterDemReport=None, imageRays=None):
    """GSD records at the sampled positions on the raster DEM, from the
    shared engine (work coordinates of DemSurface, first crossing at the cell size
    of the raster DEM refined to convergence, normal from the raster DEM
    gradient).  Returns (recordList, thresholdRejectedCount), with None
    as the count when no surface could be built or the image cannot be
    used, so that no ray was cast.  The misses of the raster DEM by
    reason, and whether the perspective centre lies below the raster DEM
    surface, are added to *rasterDemReport* when it is a dict (see
    RecordRasterDemReport).  *chunkScale* is not used: the raster DEM
    distances are those of the work coordinates, in metres in a scaled
    chunk and in chunk units otherwise.  Unlike the plane and
    triangular mesh functions, this one takes *chunk* second.  The rays
    are those of *imageRays* (ImageRays of the image by default)."""
    if not IsCameraUsable(camera):
        return ([], None)
    surface = DemSurface(camera, chunk, imageRays)
    if not surface.isUsable:
        return ([], None)
    records, thresholdRejectedCount, _ = CollectGsdAtSampledPositions(
        camera, surface, pixelStep, includeCrs=True)
    RecordRasterDemReport(surface, rasterDemReport)
    return (records, thresholdRejectedCount)


# ===================================================================
# CSV export
# ===================================================================

# Columns of the GSD CSV.  Phi_deg is the angle between the projected row
# and column steps, GSD_areaEquivalent the GSD of MetaFOCUS, and
# GSD_geometricMean the geometric mean of the row and column sampling
# distances.
GSD_CSV_COLUMN_NAMES = (
    "PixelColumn", "PixelRow", "CRS_X", "CRS_Y", "CRS_Z",
    "SlantDistance", "CosineIncidence", "Theta_deg", "Phi_deg",
    "GSD_row", "GSD_column", "GSD_geometricMean", "GSD_areaEquivalent")

# The Project line of a CSV of a project that was never saved, whose
# chunk the GSD Visualiser therefore cannot identify.
UNSAVED_PROJECT_TEXT = "(not saved)"


def ReadGsdCsvProvenance(filePath):
    """(project, chunk key, image key) of an existing GSD CSV, from its
    leading comment lines Project, Chunk key, and Image key, each None
    when the file does not record it."""
    values = {}
    with open(filePath, encoding="utf-8", errors="replace") as fileHandle:
        for line in fileHandle:
            if not line.startswith("#"):
                break
            key, separator, value = line[1:].partition(":")
            if separator:
                values.setdefault(key.strip(), value.strip())
    return values.get("Project"), values.get("Chunk key"), values.get("Image key")


def IsSameProjectPath(firstPath, secondPath):
    """True when two project paths name the same saved project file, after
    normalising case (on Windows), separators, and . and .. parts.  A
    project that was never saved matches none."""
    if not firstPath or not secondPath or UNSAVED_PROJECT_TEXT in (firstPath, secondPath):
        return False
    return NormalisedProjectPath(firstPath) == NormalisedProjectPath(secondPath)


def ForeignExistingGsdFiles(targetPathByKey, projectPath, chunkKey):
    """The existing files among *targetPathByKey* (image key: path) whose
    comment lines do not show this project file, this chunk key, and the
    image key of the target, in the order of the targets.  Chunk and
    camera keys are unique only within a project, so two copies of a
    project with the same file name in different folders, or two
    projects that were never saved, give the same file names.  A file
    that cannot be read counts as foreign."""
    foreignPaths = []
    for imageKey, path in targetPathByKey.items():
        if not os.path.exists(path):
            continue
        try:
            fileProject, fileChunkKey, fileImageKey = ReadGsdCsvProvenance(path)
        except OSError:
            foreignPaths.append(path)
            continue
        if not (IsSameProjectPath(fileProject, projectPath) and fileChunkKey == str(chunkKey)
                and fileImageKey == str(imageKey)):
            foreignPaths.append(path)
    return foreignPaths


def ReplaceForeignFilesQuestion(foreignPaths, targetCount):
    """Question before a batch replaces the *foreignPaths* among its
    *targetCount* files (ForeignExistingGsdFiles)."""
    if len(foreignPaths) == 1:
        existingText = "The file {} already exists".format(os.path.basename(foreignPaths[0]))
    else:
        existingText = "{} of the {} files to be written already exist, for example {},".format(
            len(foreignPaths), targetCount, os.path.basename(foreignPaths[0]))
    return ("{} in the chosen folder and {} another project, chunk, or image, or none. Chunk and camera keys "
            "are unique only within a project, so a copy of this project with the same file name gives the same "
            "names.\n\nReplace {}?").format(
                existingText, "records" if len(foreignPaths) == 1 else "record",
                "it" if len(foreignPaths) == 1 else "them")


def ForeignFilesConsoleLine(foreignFileCount, outputDirectory, isReplaced):
    """The Console line of the answer to ReplaceForeignFilesQuestion: the
    files of *outputDirectory* that record another project, chunk, or
    image, or none, replaced (*isReplaced*) or, when replacing was
    declined, why nothing was exported."""
    filesText = FormatCountWithNoun(foreignFileCount, "file", "files")
    recordText = "records" if foreignFileCount == 1 else "record"
    if isReplaced:
        return ("[GSD Value Exporter] Replacing {} in {} that {} another project, chunk, or image, or none, was "
                "confirmed. The end of the run names the files that it replaced.".format(
                    filesText, outputDirectory, recordText))
    return ("[GSD Value Exporter] Nothing exported: {} in {} {} another project, chunk, or image, or none, and "
            "replacing was declined.".format(filesText, outputDirectory, recordText))


# Comment line of the CSV that defines the GSD columns, in the words of
# the About sections.
GSD_CSV_DEFINITION_TEXT = (
    "sampling interval of the image projected onto the surface (the "
    "photosite pitch projected onto the surface for an image that keeps "
    "the sampling of the sensor), "
    "as central-difference sampling distances along the image row and "
    "column directions (four auxiliary rays at +/-0.5 px along the row and "
    "the column, through the midpoints of the photosite edges for an image "
    "that keeps the sampling of the sensor, intersected with the local "
    "tangent plane), Phi_deg = angle between the projected row and column steps, "
    "GSD_areaEquivalent = sqrt(GSD_row x GSD_column x sin(Phi_deg)), the "
    "side of the square with the area that the two "
    "steps span (the GSD of MetaFOCUS), GSD_geometricMean = "
    "sqrt(GSD_row x GSD_column)")


def GsdCsvUnitsText(chunkIsScaled):
    """Value of the Units comment line of the GSD CSV: the unit of the
    lengths (LengthUnitsText, the text of the footprint attribute
    00_Units) and the columns that it covers."""
    return ("{} for SlantDistance and the GSD columns (CRS_X to CRS_Z in the units of the CRS units line, "
            "Theta_deg and Phi_deg in degrees)".format(LengthUnitsText(chunkIsScaled)))


# Unit names of the CRS units line of the GSD CSV beyond those of a WKT:
# the internal chunk coordinates, and a unit that MetaFOCUS cannot tell.
CHUNK_UNIT_NAME = "chunk unit"
UNKNOWN_UNIT_NAME = "unknown"
CRS_COLUMN_NAMES = ("CRS_X", "CRS_Y", "CRS_Z")


def CrsColumnUnitNames(chunk, chunkIsScaled):
    """Units of the columns CRS_X, CRS_Y, and CRS_Z of the GSD CSV, as
    TransformChunkPointToCrs gives the coordinates: chunk units for the
    internal chunk coordinates of a chunk without a transform, and for the
    world coordinates of a chunk without a physical scale (*chunkIsScaled*
    False) that has no CRS or a local CRS, metres for a chunk with a
    physical scale and without a CRS, and otherwise the units of the CRS
    (CrsUnitNames), also for a local CRS of a chunk with a physical scale,
    for example foot, foot, and unknown.  A local CRS keeps the world coordinates of the chunk
    as they are, so without a physical scale they are in chunk units,
    whatever unit its WKT names (Metashape 2.3.2 gives a chunk without
    references the CRS Local Coordinates (m) and a transform without a
    scale).  CRS_X and CRS_Y take the horizontal unit, for example the
    degree of a geographic CRS.  CRS_Z takes the unit of the vertical
    component of a compound CRS, the metre for a geographic CRS or a CRS
    in metres without one, and is unknown otherwise.  A CRS of an unknown
    kind gives three unknown units."""
    if getattr(chunk.transform, "matrix", None) is None:
        return (CHUNK_UNIT_NAME,) * 3
    coordinateSystem = getattr(chunk, "crs", None)
    crsKind = CrsKind(coordinateSystem)
    if not chunkIsScaled and crsKind in ("none", "local"):
        return (CHUNK_UNIT_NAME,) * 3
    if coordinateSystem is None:
        return ("metre",) * 3
    if crsKind not in ("local", "geographic", "projected", "geocentric"):
        return (UNKNOWN_UNIT_NAME,) * 3
    horizontalUnit, verticalUnit = CrsUnitNames(coordinateSystem)
    horizontalUnit = horizontalUnit or UNKNOWN_UNIT_NAME
    if verticalUnit:
        heightUnit = verticalUnit
    elif crsKind == "geographic" or horizontalUnit == "metre":
        heightUnit = "metre"
    else:
        heightUnit = UNKNOWN_UNIT_NAME
    return horizontalUnit, horizontalUnit, heightUnit


def CrsUnitsLineText(unitNames):
    """Value of the CRS units comment line of the GSD CSV for the unit names
    *unitNames* of CRS_X, CRS_Y, and CRS_Z (CrsColumnUnitNames), for example
    "CRS_X degree, CRS_Y degree, CRS_Z metre"."""
    return ", ".join("{} {}".format(columnName, unitName)
                     for columnName, unitName in zip(CRS_COLUMN_NAMES, unitNames))


def _RemoveTemporaryFile(temporaryFilePath):
    """Remove the temporary file of a CSV write that failed, if it was
    created and still exists (a failure to remove it is reported once in
    the Console)."""
    if temporaryFilePath is None or not os.path.exists(temporaryFilePath):
        return
    try:
        os.remove(temporaryFilePath)
    except OSError as suppressedError:
        ReportSuppressedError("gsdValueExporter._RemoveTemporaryFile", suppressedError)


def ExportGsdAtSampledPositionsToCsv(outputFilePath, cameraLabel,
                           sampledGsdRecords, gsdMode,
                           coordinateSystemName,
                           csvDelimiter=",",
                           chunkUnitsNote=False,
                           coordinateKind="unknown",
                           pixelStep=None, sampledPixelCount=None,
                           rejectedRayCount=None, chunkLabel=None,
                           imageKey=None, decimalSeparator=".",
                           chunkKey=None, projectPath=None, rollingShutterModel=None,
                           rollingShutterRayFailure=None, rayText="", crsUnitNames=None):
    """Write the GSD records of the sampled positions to CSV, with the
    columns GSD_CSV_COLUMN_NAMES and the definition line
    GSD_CSV_DEFINITION_TEXT.

    The comment lines name the image, the surface mode, the CRS, the
    units, and, when given, the camera key of the image, the
    chunk, the pixel step, and the numbers of sampled pixels and
    grazing-rejected rays, so that a file can be matched with its image
    and with the statistics of the Image Parameter Exporter and the
    Image Footprint Generator.  The length columns carry no unit in
    their names: the Units line gives it (GsdCsvUnitsText), and says
    which columns it covers.  With *crsUnitNames* (CrsColumnUnitNames), the
    CRS units line gives the units of CRS_X to CRS_Z (CrsUnitsLineText),
    which the GSD Visualiser writes on its axes.  Coordinates get the decimals
    of CoordinateDecimals for *coordinateKind* and the smallest GSD of the
    records, the same for every row, and the lengths at least four
    significant digits (FormatLengthForCsv).  Every
    column holds numbers.  With *decimalSeparator* a comma (the semicolon
    choice of
    CSV_FORMAT_CHOICES), every number of the data rows is written with a
    decimal comma, while the comment lines and the header row keep their
    text.  The file is written
    next to *outputFilePath* first, under a name that the write creates
    for itself (tempfile.mkstemp), and then put in its place, so that a
    write that fails part way leaves an earlier file of the image intact
    instead of a truncated one, and no other file is touched.
    Raises PermissionError with a user-friendly message if the
    file is locked (for example open in Excel)."""
    positiveGsdValues = [record["gsdValue"] for record in sampledGsdRecords
                         if math.isfinite(record["gsdValue"]) and record["gsdValue"] > 0.0]
    horizontalDecimals, heightDecimals = CoordinateDecimals(
        coordinateKind, forCsv=True, gsdValue=min(positiveGsdValues) if positiveGsdValues else None)
    temporaryFilePath = None
    try:
        temporaryFileDescriptor, temporaryFilePath = tempfile.mkstemp(
            dir=os.path.dirname(os.path.abspath(outputFilePath)),
            prefix=os.path.basename(outputFilePath) + ".", suffix=".partial")
        with os.fdopen(temporaryFileDescriptor, "w", newline="",
                       encoding="utf-8") as fileHandle:
            writer = csv.writer(fileHandle, delimiter=csvDelimiter)
            # Comment lines are written raw: csv.writer would quote
            # them whenever the metadata text contains the chosen
            # delimiter, and a quoted first line is then mistaken
            # for the header by downstream parsers.
            fileHandle.write("# Image: {}\r\n".format(cameraLabel))
            if imageKey is not None:
                fileHandle.write("# Image key: {}\r\n".format(imageKey))
            fileHandle.write("# Surface mode: {}\r\n".format(gsdMode))
            fileHandle.write("# CRS: {}\r\n".format(
                coordinateSystemName))
            fileHandle.write("# Units: {}\r\n".format(
                GsdCsvUnitsText(not chunkUnitsNote)))
            if crsUnitNames is not None:
                fileHandle.write("# CRS units: {}\r\n".format(CrsUnitsLineText(crsUnitNames)))
            if chunkLabel is not None:
                fileHandle.write("# Chunk: {}\r\n".format(chunkLabel))
            # The project and the chunk key identify the chunk, whose
            # label need not be unique: the GSD Visualiser compares them
            # for files in chunk units.
            if chunkKey is not None:
                fileHandle.write("# Chunk key: {}\r\n".format(chunkKey))
            if projectPath is not None:
                fileHandle.write("# Project: {}\r\n".format(projectPath or UNSAVED_PROJECT_TEXT))
            # An image with rolling-shutter compensation: its rays follow
            # the camera motion during the readout (ImageRays), or, with
            # *rollingShutterRayFailure*, the line says why every ray of
            # the image starts at its perspective centre.
            if rollingShutterModel:
                fileHandle.write("# Rolling shutter: {}\r\n".format(
                    RollingShutterRayText(rollingShutterModel, rollingShutterRayFailure)))
                if not rollingShutterRayFailure:
                    fileHandle.write("# Slant distance: {}\r\n".format(ROLLING_SHUTTER_SLANT_DISTANCE_TEXT))
            # The rays of Camera.unproject that MetaFOCUS refined and the
            # pixel positions without a ray (ExactRayText).
            if rayText:
                fileHandle.write("# {}\r\n".format(rayText))
            if pixelStep is not None:
                fileHandle.write("# Pixel step: {} px\r\n".format(pixelStep))
            if sampledPixelCount is not None:
                fileHandle.write("# Sampled pixels: {}\r\n".format(
                    sampledPixelCount))
            if rejectedRayCount is not None:
                fileHandle.write(
                    "# Rejected rays (cos(theta) < {}): {}\r\n".format(
                        GRAZING_COSINE_THRESHOLD, rejectedRayCount))
            fileHandle.write("# Records: {}\r\n".format(
                len(sampledGsdRecords)))
            fileHandle.write("# MetaFOCUS version: {}\r\n".format(
                _METAFOCUS_VERSION))
            fileHandle.write("# GSD definition: {}\r\n".format(GSD_CSV_DEFINITION_TEXT))
            # No unit in the names of the length columns: they hold
            # metres or, in a chunk without a physical scale, chunk units,
            # as the Units line says.
            writer.writerow(list(GSD_CSV_COLUMN_NAMES))
            for record in sampledGsdRecords:
                thetaDegrees = math.degrees(
                    math.acos(min(1.0, record["cosineIncidence"])))
                # Phi_deg with four decimals, so that GSD_areaEquivalent
                # can be recomputed from the file to about eight
                # significant digits.  Every column holds numbers.  The
                # lengths keep at least four significant digits (eight
                # decimals would write a GSD of 0.7512 um as 0.00000075).
                writer.writerow(DataRowWithDecimalSeparator([
                    record["pixelColumn"],
                    record["pixelRow"],
                    "{:.{}f}".format(record["crsX"], horizontalDecimals),
                    "{:.{}f}".format(record["crsY"], horizontalDecimals),
                    "{:.{}f}".format(record["crsZ"], heightDecimals),
                    FormatLengthForCsv(record["slantDistance"], 6),
                    "{:.8f}".format(record["cosineIncidence"]),
                    "{:.2f}".format(thetaDegrees),
                    "{:.4f}".format(record["projectedStepAngleDegrees"]),
                    FormatLengthForCsv(record["gsdRow"], 8),
                    FormatLengthForCsv(record["gsdColumn"], 8),
                    FormatLengthForCsv(record["gsdGeometricMean"], 8),
                    FormatLengthForCsv(record["gsdValue"], 8)], decimalSeparator))
        os.replace(temporaryFilePath, outputFilePath)
    except PermissionError:
        _RemoveTemporaryFile(temporaryFilePath)
        raise PermissionError(
            "Cannot write to '{}': the file may be open in "
            "another program (such as Excel or MATLAB). Close it "
            "and try again.".format(outputFilePath))
    except Exception:
        _RemoveTemporaryFile(temporaryFilePath)
        raise
    return len(sampledGsdRecords)


# ===================================================================
# Inspection markers at the maximum and minimum GSD
# ===================================================================

def _GsdMarkerGroup(chunk):
    """Marker group "GSD extremes" of *chunk*, created when it does not
    exist yet, or None when the Metashape API offers no marker groups
    (the failure is reported once in the Console)."""
    try:
        for markerGroup in chunk.marker_groups:
            if markerGroup.label == _GSD_MARKER_GROUP_LABEL:
                return markerGroup
        markerGroup = chunk.addMarkerGroup()
        markerGroup.label = _GSD_MARKER_GROUP_LABEL
        return markerGroup
    except Exception as suppressedError:
        ReportSuppressedError("gsdValueExporter._GsdMarkerGroup", suppressedError)
        return None


def _CreateGsdMarker(chunk, markerLabel, record, createdMarkerLabels=None):
    """Create an inspection marker at a GSD record location, for visual
    inspection.  Its reference location is set but disabled, so it is
    never a control point (neither for Metashape nor for the counts of
    the other MetaFOCUS tools), and it is kept in the marker group
    "GSD extremes".  The record holds the point in the chunk CRS, and
    the reference location is written in the marker CRS of the
    Reference Settings when the chunk has a separate one
    (MarkerReferenceLocationFromChunkCoordinates).  When it cannot be
    written there, no marker is created, any marker with the label stays
    as it is, the Console says so, and None is returned.  The same holds
    when the metadata record of the new marker cannot be written: the new
    marker is removed again, as without its record it would not be an
    inspection marker that a later export replaces.  Earlier
    inspection markers with the same label that carry the metadata record
    of the tool and have no marker projection and no enabled reference
    location (IsReplaceableInspectionMarker) are removed once the new
    marker is in place.  Any other marker with that label, for example a
    control point that someone named so and moved into the group, is kept
    as it is, and the Console says so (KeptMarkersConsoleLine).  The label
    is added to the list *createdMarkerLabels*, when one is given, as soon
    as the marker exists: a replaced marker leaves the number of markers
    unchanged, yet adding it clears the selection of images all the
    same."""
    crsPoint = Metashape.Vector([
        record["crsX"], record["crsY"], record["crsZ"]])
    try:
        referenceLocation = MarkerReferenceLocationFromChunkCoordinates(chunk, crsPoint)
    except ValueError as conversionError:
        print("  The inspection marker ‘{}’ was not created, because its location cannot be written in the "
              "marker CRS: {}. Any earlier marker with this label is kept.".format(
                  markerLabel, DescribeException(conversionError)))
        return None
    sameLabelMarkers = [marker for marker in chunk.markers if marker.label == markerLabel]
    staleMarkers = [marker for marker in sameLabelMarkers if IsReplaceableInspectionMarker(marker)]
    keptMarkerCount = len(sameLabelMarkers) - len(staleMarkers)
    if keptMarkerCount:
        print("  " + KeptMarkersConsoleLine(markerLabel, keptMarkerCount))
    markerGroup = _GsdMarkerGroup(chunk)

    marker = chunk.addMarker()
    if createdMarkerLabels is not None:
        createdMarkerLabels.append(markerLabel)
    marker.label = markerLabel
    marker.reference.location = referenceLocation
    marker.reference.enabled = False
    # The provenance of the marker in its metadata, so that a renamed or
    # moved marker stays an inspection marker.
    try:
        marker.meta[INSPECTION_MARKER_META_KEY] = INSPECTION_MARKER_META_VALUE
    except Exception as metaError:
        try:
            chunk.remove([marker])
        except Exception as suppressedError:
            ReportSuppressedError("gsdValueExporter._CreateGsdMarker.remove", suppressedError)
        print("  The inspection marker ‘{}’ was not created, because its metadata record cannot be written: {}. "
              "Any earlier marker with this label is kept.".format(markerLabel, DescribeException(metaError)))
        return None
    if markerGroup is not None:
        try:
            marker.group = markerGroup
        except Exception as suppressedError:
            ReportSuppressedError("gsdValueExporter._CreateGsdMarker", suppressedError)
    if staleMarkers:
        chunk.remove(staleMarkers)
    return marker


def KeptMarkersConsoleLine(markerLabel, keptMarkerCount):
    """The Console line when *keptMarkerCount* markers labelled
    *markerLabel* are not inspection markers that an export may replace
    (IsReplaceableInspectionMarker), so that a new inspection marker
    leaves them as they are (_CreateGsdMarker)."""
    reason = ("because an export replaces only inspection markers that MetaFOCUS recorded as its own and that have "
              "no marker projection and no enabled reference location. The new inspection marker gets the same "
              "label.")
    if keptMarkerCount == 1:
        return "The marker \u2018{}\u2019 is kept, {}".format(markerLabel, reason)
    return "{:,} markers labelled \u2018{}\u2019 are kept, {}".format(keptMarkerCount, markerLabel, reason)


def CreateMaximumGsdMarker(chunk, cameraLabel, maximumGsdRecord, coordinateKind="unknown", lengthUnit="m",
                           createdMarkerLabels=None):
    """Create the inspection marker MaxGSD_<image> at the location of the
    largest area-equivalent GSD (see _CreateGsdMarker, which adds its
    label to *createdMarkerLabels*) and report it in the Console, in
    *lengthUnit* ("m", or "chunk units" in a chunk without a physical
    scale).  Returns the marker, or None when it was not created."""
    markerLabel = "MaxGSD_{}".format(cameraLabel)
    marker = _CreateGsdMarker(chunk, markerLabel, maximumGsdRecord, createdMarkerLabels)
    if marker is None:
        return None
    thetaDegrees = math.degrees(
        math.acos(min(1.0, maximumGsdRecord["cosineIncidence"])))
    print("  Created inspection marker '{}' at CRS {}".format(
        markerLabel, FormatCrsPoint(maximumGsdRecord, coordinateKind)))
    print("    GSD, area-equivalent = {} {}, cos(theta) = {:.4f}, "
          "theta = {:.1f} deg, pixel = ({}, {})".format(
        FormatLengthWithDecimals(maximumGsdRecord["gsdValue"], 8), lengthUnit, maximumGsdRecord["cosineIncidence"],
        thetaDegrees,
        maximumGsdRecord["pixelColumn"], maximumGsdRecord["pixelRow"]))
    return marker


def CreateMinimumGsdMarker(chunk, cameraLabel, minimumGsdRecord, coordinateKind="unknown", lengthUnit="m",
                           createdMarkerLabels=None):
    """Create the inspection marker MinGSD_<image> at the location of the
    smallest area-equivalent GSD (see _CreateGsdMarker, which adds its
    label to *createdMarkerLabels*) and report it in the Console, in
    *lengthUnit* ("m", or "chunk units" in a chunk without a physical
    scale).  Returns the marker, or None when it was not created."""
    markerLabel = "MinGSD_{}".format(cameraLabel)
    marker = _CreateGsdMarker(chunk, markerLabel, minimumGsdRecord, createdMarkerLabels)
    if marker is None:
        return None
    thetaDegrees = math.degrees(
        math.acos(min(1.0, minimumGsdRecord["cosineIncidence"])))
    print("  Created inspection marker '{}' at CRS {}".format(
        markerLabel, FormatCrsPoint(minimumGsdRecord, coordinateKind)))
    print("    GSD, area-equivalent = {} {}, cos(theta) = {:.4f}, "
          "theta = {:.1f} deg, pixel = ({}, {})".format(
        FormatLengthWithDecimals(minimumGsdRecord["gsdValue"], 8), lengthUnit, minimumGsdRecord["cosineIncidence"],
        thetaDegrees,
        minimumGsdRecord["pixelColumn"], minimumGsdRecord["pixelRow"]))
    return marker


# ===================================================================
# Console output for one image
# ===================================================================

def DescribeDirectionalGsd(record, lengthUnit):
    """The row and column sampling distances of a GSD record, the angle
    between the projected steps, and their geometric mean, for the
    Console."""
    return ("Row and column sampling distances: {} and {} {}, angle between the projected steps "
            "{:.2f} degrees, geometric mean {} {}".format(
                FormatLengthWithDecimals(record["gsdRow"], 8), FormatLengthWithDecimals(record["gsdColumn"], 8),
                lengthUnit, record["projectedStepAngleDegrees"],
                FormatLengthWithDecimals(record["gsdGeometricMean"], 8), lengthUnit))


# The statistics of the Console summary of one image, in the order of
# StatisticsText.
CONSOLE_STATISTIC_NAMES = ("minimum", "2nd percentile", "median", "mean", "98th percentile", "maximum")


def StatisticsText(values, decimals):
    """The six statistics of a length in the Console summary of one image
    (minimum, 2nd percentile, median, mean, 98th percentile, and maximum,
    CONSOLE_STATISTIC_NAMES) as "minimum=...  2nd percentile=...", each
    with *decimals* decimals or four significant digits
    (FormatLengthWithDecimals)."""
    return "  ".join("{}={}".format(statisticName, FormatLengthWithDecimals(value, decimals))
                     for statisticName, value in zip(CONSOLE_STATISTIC_NAMES, values))


def PrintRasterDemMisses(cameraLabel, rasterDemReport):
    """Print the raster DEM part of the Console summary of one image:
    the note on a perspective centre below the raster DEM surface and the
    missed rays by reason (see RecordRasterDemReport), nothing when
    *rasterDemReport* is None or holds no miss."""
    if not rasterDemReport:
        return
    if rasterDemReport.get("perspectiveCentreBelowSurface"):
        print("  " + PerspectiveCentreBelowRasterDemNote(cameraLabel))
    missDescription = DescribeRasterDemMisses(rasterDemReport.get("missCountsByReason", {}))
    if missDescription:
        print("  Raster DEM misses: {}.".format(missDescription))


def PrintCameraGsdSummary(cameraLabel, gsdModeString, pixelStep,
                          sampledGsdRecords, totalPixelCount,
                          cosineThreshold, thresholdRejectedCount,
                          coordinateKind="unknown", lengthUnit="m",
                          rasterDemReport=None):
    """Print the diagnostic summary of one image to the Console: the six
    statistics of the area-equivalent GSD, the GSD of MetaFOCUS, and of
    the geometric mean of the row and column sampling distances, of the
    slant distance, and of cos(theta), and the locations of the largest
    and smallest GSD.  GSD values get eight decimals and slant distances
    four, or four significant digits where these give fewer
    (FormatLengthWithDecimals), and coordinates
    the decimals of CoordinateDecimals for *coordinateKind* and the GSD of
    the location.  Lengths are labelled with *lengthUnit* ("m", or
    "chunk units" in a chunk without a physical scale).  A
    *thresholdRejectedCount* of None means that no surface could be
    built for the image, so that no ray was cast.  In raster DEM mode,
    *rasterDemReport* (see RecordRasterDemReport) adds the missed rays
    by reason and a note on a perspective centre below the raster DEM
    surface."""
    if not sampledGsdRecords and thresholdRejectedCount is None:
        print("  {}: no valid GSD values, because no surface could be "
              "built for this image (the image must be oriented and "
              "calibrated, and plane mode needs at least three valid tie "
              "points in it that do not all lie on one line).".format(cameraLabel))
        return
    if not sampledGsdRecords:
        print("  {}: no valid GSD values ({} sampled pixels, {} rejected "
              "for grazing incidence, {} misses or degenerate "
              "samples).".format(
                  cameraLabel, totalPixelCount, thresholdRejectedCount,
                  max(0, totalPixelCount - thresholdRejectedCount)))
        PrintRasterDemMisses(cameraLabel, rasterDemReport)
        return

    gsdValues = sorted(record["gsdValue"] for record in sampledGsdRecords)
    geometricMeanValues = sorted(record["gsdGeometricMean"] for record in sampledGsdRecords)
    distanceValues = sorted(record["slantDistance"] for record in sampledGsdRecords)
    cosineIncidenceValues = sorted(record["cosineIncidence"] for record in sampledGsdRecords)
    validCount = len(gsdValues)
    coveragePercent = 100.0 * validCount / totalPixelCount if totalPixelCount else 0
    missedRayCount = totalPixelCount - validCount - thresholdRejectedCount

    # Header.
    print("")
    print("GSD export for image: {}".format(cameraLabel))
    print("  Surface mode: {}".format(gsdModeString))
    print("  Pixel step: {} px".format(pixelStep))
    print("  Sampled pixels: {}  |  valid: {} ({:.1f} %)  |  "
          "ray misses or degenerate samples: {}  |  rejected for grazing "
          "incidence: {}".format(
        totalPixelCount, validCount, coveragePercent,
        missedRayCount, thresholdRejectedCount))
    PrintRasterDemMisses(cameraLabel, rasterDemReport)

    # The same six statistics (with the 2nd and the 98th percentile) for
    # four distributions: the area-equivalent GSD (the GSD of MetaFOCUS),
    # the geometric mean (which exceeds it where the steps are sheared),
    # the slant distance, and cos(theta).
    gsdP02 = ComputePercentile(gsdValues, 2)
    gsdMedian = ComputePercentile(gsdValues, 50)
    gsdMean = sum(gsdValues) / validCount
    gsdP98 = ComputePercentile(gsdValues, 98)
    print("  GSD, area-equivalent ({}):                   {}".format(lengthUnit, StatisticsText(
        (gsdValues[0], gsdP02, gsdMedian, gsdMean, gsdP98, gsdValues[-1]), 8)))
    print("  Geometric mean of row and column ({}):       {}".format(lengthUnit, StatisticsText(
        (geometricMeanValues[0], ComputePercentile(geometricMeanValues, 2),
         ComputePercentile(geometricMeanValues, 50), sum(geometricMeanValues) / validCount,
         ComputePercentile(geometricMeanValues, 98), geometricMeanValues[-1]), 8)))

    distanceP02 = ComputePercentile(distanceValues, 2)
    distanceMedian = ComputePercentile(distanceValues, 50)
    distanceMean = sum(distanceValues) / validCount
    distanceP98 = ComputePercentile(distanceValues, 98)
    print("  Slant distance ({}):                         {}".format(lengthUnit, StatisticsText(
        (distanceValues[0], distanceP02, distanceMedian, distanceMean, distanceP98, distanceValues[-1]), 4)))

    cosineIncidenceP02 = ComputePercentile(cosineIncidenceValues, 2)
    cosineIncidenceMedian = ComputePercentile(cosineIncidenceValues, 50)
    cosineIncidenceMean = sum(cosineIncidenceValues) / validCount
    cosineIncidenceP98 = ComputePercentile(cosineIncidenceValues, 98)
    print("  cos(theta):                                 minimum={:.4f}  "
          "2nd percentile={:.4f}  median={:.4f}  mean={:.4f}  98th percentile={:.4f}  maximum={:.4f}"
          .format(cosineIncidenceValues[0], cosineIncidenceP02, cosineIncidenceMedian, cosineIncidenceMean,
                  cosineIncidenceP98, cosineIncidenceValues[-1]))

    # The grazing threshold applies to every surface mode, and the
    # guard only protects the division.
    if cosineThreshold > 0.0:
        print("  cos(theta) threshold:                       {:.2f} (rays "
              "beyond {:.1f} deg incidence rejected, "
              "projected sampling interval stretched more than {:.1f}-fold)".format(
            cosineThreshold,
            math.degrees(math.acos(cosineThreshold)),
            1.0 / cosineThreshold))

    # Maximum GSD location.
    maximumGsdRecord = max(sampledGsdRecords, key=lambda record: record["gsdValue"])
    maximumGsdIncidenceDegrees = math.degrees(
        math.acos(min(1.0, maximumGsdRecord["cosineIncidence"])))
    print("")
    print("  Maximum GSD location:")
    print("    Pixel: ({}, {})".format(
        maximumGsdRecord["pixelColumn"], maximumGsdRecord["pixelRow"]))
    print("    CRS: {}".format(FormatCrsPoint(maximumGsdRecord, coordinateKind)))
    print("    Slant distance: {} {}".format(
        FormatLengthWithDecimals(maximumGsdRecord["slantDistance"], 4), lengthUnit))
    print("    cos(theta) = {:.6f}  (theta = {:.1f} deg)".format(
        maximumGsdRecord["cosineIncidence"], maximumGsdIncidenceDegrees))
    print("    GSD, area-equivalent: {} {}".format(
        FormatLengthWithDecimals(maximumGsdRecord["gsdValue"], 8), lengthUnit))
    print("    " + DescribeDirectionalGsd(maximumGsdRecord, lengthUnit))

    # Minimum GSD location.
    minimumGsdRecord = min(sampledGsdRecords, key=lambda record: record["gsdValue"])
    minimumGsdIncidenceDegrees = math.degrees(
        math.acos(min(1.0, minimumGsdRecord["cosineIncidence"])))
    print("")
    print("  Minimum GSD location:")
    print("    Pixel: ({}, {})".format(
        minimumGsdRecord["pixelColumn"], minimumGsdRecord["pixelRow"]))
    print("    CRS: {}".format(FormatCrsPoint(minimumGsdRecord, coordinateKind)))
    print("    Slant distance: {} {}".format(
        FormatLengthWithDecimals(minimumGsdRecord["slantDistance"], 4), lengthUnit))
    print("    cos(theta) = {:.6f}  (theta = {:.1f} deg)".format(
        minimumGsdRecord["cosineIncidence"], minimumGsdIncidenceDegrees))
    print("    GSD, area-equivalent: {} {}".format(
        FormatLengthWithDecimals(minimumGsdRecord["gsdValue"], 8), lengthUnit))
    print("    " + DescribeDirectionalGsd(minimumGsdRecord, lengthUnit))
    print("")


# ===================================================================
# Count total sampled pixels for coverage calculation
# ===================================================================

def _CountSampledPixels(pixelColumnCount, pixelRowCount, pixelStep):
    """Number of pixels on the (centred) sampling grid."""
    return len(SamplePixelGrid(pixelColumnCount, pixelRowCount, pixelStep))


# ===================================================================
# Process one image (single-image and batch runs)
# ===================================================================

def ProcessOneCamera(camera, chunk, chunkScale, useMesh, useDem,
                     pixelStep, bvhData, pointByTrackId,
                     outputDirectory, createMarkers,
                     outputFilePath=None, csvDelimiter=",",
                     chunkIsScaled=True, imageLabel=None, projectName="",
                     decimalSeparator=".", projectPath="", planeWarnings=None, createdMarkerLabels=None,
                     replacedFilePaths=None):
    """Process one image: collect the GSD at the sampled positions,
    print the Console summary, write the CSV, and optionally create the
    two inspection markers, whose labels are added to the list
    *createdMarkerLabels* when one is given. Returns True when a CSV was
    written.  The path of a CSV that replaces an existing file is added
    to the list *replacedFilePaths* when one is given, once the file is
    written (ReplacedFilesConsoleLine).  In plane mode, an image whose tie points lie far off their
    plane gets a warning line in the Console (PlaneSpreadWarningLine),
    and its label is added to the list *planeWarnings* when one is given.

    If *outputFilePath* is given, the CSV is written to that exact
    path.  Otherwise the file name of the image among all master images
    of the chunk (BuildGsdCsvFileNamesByKey) is used inside
    *outputDirectory*, or, for an image that is no master image, the
    name from BuildGsdCsvFileName with its label and key.  *imageLabel*
    (default UniqueImageLabel) names the image in the CSV, the markers,
    and the Console.  *chunkIsScaled* False (a chunk without a physical
    scale, see PhysicalChunkScale) marks the values as chunk units.
    *csvDelimiter* and *decimalSeparator* are those of the choice of
    CSV_FORMAT_CHOICES (see ExportGsdAtSampledPositionsToCsv).
    Markers are only created when the chunk has a transform, because
    otherwise the hit points are internal chunk coordinates that
    Metashape would misread as coordinates of the reference CRS."""
    gsdModeString = SurfaceModeName(useMesh, useDem)
    if imageLabel is None:
        imageLabel = UniqueImageLabel(camera, DuplicatedImageLabels(chunk.cameras))
    calibration = camera.calibration
    if calibration is None:
        print("  {}: skipped (no calibration).".format(imageLabel))
        return False
    pixelColumnCount = calibration.width
    pixelRowCount = calibration.height

    cosineThreshold = GRAZING_COSINE_THRESHOLD

    # Collect the GSD at the sampled positions.  In raster DEM mode the
    # misses are reported by reason.  The rays (ImageRays) say whether
    # they follow the camera motion of an image with rolling-shutter
    # compensation, for the CSV.
    rasterDemReport = {} if useDem else None
    planeWarningLine = None
    imageRays = ImageRays(camera, "gsdValueExporter.ProcessOneCamera")
    if useDem:
        sampledGsdRecords, thresholdRejectedCount = \
            CollectGsdAtSampledPositionsOnDem(
                camera, chunk, chunkScale, pixelStep, rasterDemReport, imageRays=imageRays)
    elif useMesh:
        sampledGsdRecords, thresholdRejectedCount = \
            CollectGsdAtSampledPositionsOnMesh(
                camera, bvhData, chunkScale, pixelStep, chunk, imageRays=imageRays)
    else:
        validProjections = GetValidProjections(
            camera, chunk.tie_points, pointByTrackId)
        sampledGsdRecords, thresholdRejectedCount = \
            CollectGsdAtSampledPositionsOnPlane(
                camera, validProjections, chunkScale, pixelStep, chunk, imageRays=imageRays)
        planeWarningLine = PlaneSpreadWarningLine(imageLabel, PlaneSpreadOfImage(camera, validProjections))
        if planeWarningLine is not None and planeWarnings is not None:
            planeWarnings.append(imageLabel)

    totalPixelCount = _CountSampledPixels(
        pixelColumnCount, pixelRowCount, pixelStep)
    coordinateKind = OutputCoordinateKind(chunk)
    lengthUnit = "m" if chunkIsScaled else "chunk units"

    # Console summary, with the rays that MetaFOCUS refined and the pixel
    # positions without a ray.
    PrintCameraGsdSummary(
        imageLabel, gsdModeString, pixelStep,
        sampledGsdRecords, totalPixelCount, cosineThreshold,
        thresholdRejectedCount, coordinateKind=coordinateKind,
        lengthUnit=lengthUnit, rasterDemReport=rasterDemReport)
    rayText = ExactRayText(imageRays.rayReport)
    if rayText:
        print("  {}: {}".format(imageLabel, rayText))
    # The plane-mode warning, in the section of the image.
    if planeWarningLine is not None:
        print("  " + planeWarningLine)

    if not sampledGsdRecords:
        return False

    # CSV path: the name of the image among all master images of the
    # chunk, the name that the dialog gives it in every run.
    if outputFilePath is None:
        chunkFileNameLabel = ChunkFileNameLabel(chunk)
        fileNameByKey = BuildGsdCsvFileNamesByKey(
            MasterImagesOfChunk(chunk), projectName, chunkFileNameLabel, gsdModeString)
        outputFilePath = os.path.join(
            outputDirectory, fileNameByKey.get(camera.key) or BuildGsdCsvFileName(
                camera.label, camera.key, projectName, chunkFileNameLabel, gsdModeString))

    fileExisted = os.path.exists(outputFilePath)
    ExportGsdAtSampledPositionsToCsv(
        outputFilePath, imageLabel, sampledGsdRecords,
        gsdModeString, DescribeOutputCoordinateSystem(chunk), csvDelimiter,
        chunkUnitsNote=not chunkIsScaled,
        coordinateKind=coordinateKind, pixelStep=pixelStep,
        sampledPixelCount=totalPixelCount,
        rejectedRayCount=thresholdRejectedCount, chunkLabel=chunk.label,
        imageKey=camera.key, decimalSeparator=decimalSeparator,
        chunkKey=getattr(chunk, "key", None), projectPath=projectPath,
        rollingShutterModel=RollingShutterModelName(getattr(camera, "sensor", None)),
        rollingShutterRayFailure=imageRays.failure, rayText=rayText,
        crsUnitNames=CrsColumnUnitNames(chunk, chunkIsScaled))
    # One kind of slash: the folder of Metashape's dialog has forward
    # slashes, and os.path.join adds a backslash on Windows.
    print("  Exported to: {}".format(ConsolePath(outputFilePath)))
    if fileExisted and replacedFilePaths is not None:
        replacedFilePaths.append(outputFilePath)

    # Inspection markers.
    if createMarkers:
        if getattr(chunk.transform, "matrix", None) is None:
            print("  {}: no markers created, because the chunk has no "
                  "transform and the hit points are internal chunk "
                  "coordinates.".format(imageLabel))
        else:
            maximumGsdRecord = max(sampledGsdRecords, key=lambda record: record["gsdValue"])
            minimumGsdRecord = min(sampledGsdRecords, key=lambda record: record["gsdValue"])
            # The CSV is written, so a failure of the optional markers is
            # reported for the image and the image still counts as exported.
            try:
                CreateMaximumGsdMarker(chunk, imageLabel, maximumGsdRecord, coordinateKind, lengthUnit,
                                       createdMarkerLabels=createdMarkerLabels)
                CreateMinimumGsdMarker(chunk, imageLabel, minimumGsdRecord, coordinateKind, lengthUnit,
                                       createdMarkerLabels=createdMarkerLabels)
            except Exception as markerError:
                print("  {}: the CSV file was written, but the inspection markers could not be created: "
                      "{}".format(imageLabel, DescribeException(markerError)))

    return True


# ===================================================================
# About dialog
# ===================================================================

_GSD_EXPORT_ABOUT_HTML = """
<h2>GSD Value Exporter</h2>
<p>Version 1.0.1 (2026-11-22)</p>
<p><b>Author:</b> Geert J. Verhoeven (ORCID: <a href="https://orcid.org/0000-0003-4825-9604">0000-0003-4825-9604</a>)<br>
<b>Contact:</b> <a href="mailto:geert.verhoeven@univie.ac.at">geert.verhoeven@univie.ac.at</a><br>
<b>Licence:</b> GNU General Public License, version 3 or any later version (GPL-3.0-or-later),
with an additional permission to convey MetaFOCUS combined with Agisoft Metashape and its
Python module (see LICENSE and ADDITIONAL_PERMISSION.txt in the MetaFOCUS ZIP file)</p>

<hr>
<h3>Overview</h3>
<p>This tool exports the ground sampling distance (GSD, also called ground sample distance or ground
sampled distance) at the sampled pixel positions of one or more images. It writes one CSV file per
image. Each row holds the column and row indices of the sampled pixel (PixelColumn and PixelRow) and
the 3D hit point in the coordinate reference system (CRS) of the chunk. Each row also holds the
slant distance from the perspective centre (see &lsquo;Rolling shutter&rsquo; for images with
rolling-shutter compensation), the cosine of the incidence angle, and the incidence angle in
degrees. The other columns are the angle between the projected row and column steps in degrees, the
ground sampling distances along the image row and column directions, their geometric mean, and the
area-equivalent GSD, the GSD of MetaFOCUS. The <i>GSD Visualiser</i> displays these files.</p>
<p>Only master images are processed: the slave images of a
multi-camera rig and the keyframes of an animation track are left
out.  A disabled image is processed when it is oriented.  For the
same image, surface mode, and pixel step, the statistics equal those
of the Image Parameter Exporter and the Image Footprint
Generator, which use the same computation, rays, and normals.</p>

<h4>Surface modes</h4>
<p><b>Surface mode</b> determines the surface that the rays are intersected with:</p>
<ul>
<li><b>Plane (tie points):</b> rays intersect a least-squares plane through the valid 3D tie points
of the image (at least three that are not collinear). The tangent plane for the four auxiliary rays
is that plane itself, so the rays meet it exactly. The GSD is, as in the other modes, the side of
the square with the area that the central differences span. For an image that keeps the sampling of
the sensor, this area equals, to first order, the area of the ground-projected instantaneous field
of view (GIFOV). Plane mode warns about an image whose tie points lie far from one plane, as when an
image sees a wall and the ground. It warns when their root mean square distance from the plane
exceeds 5&nbsp;% of their median distance from the perspective centre. The Console names each such
image, and the final message counts them.</li>
<li><b>Triangular mesh:</b> rays intersect the triangular mesh of the chunk, searched through a
bounding volume hierarchy (BVH). As in the other two GSD tools, the local tangent plane for the
auxiliary rays uses vertex normals averaged over a 3-ring neighbourhood and interpolated at the hit
point via barycentric coordinates. On a curved triangular mesh the tangent-plane construction is a
local planar approximation. Triangular mesh mode is preselected when the chunk has a triangular
mesh. A mesh that Metashape builds in the chunk has the identity matrix as its transform and no CRS
of its own, so MetaFOCUS reads its vertices as internal coordinates of the chunk. A mesh can also
have a transform and a CRS of its own. MetaFOCUS then converts its vertices to the internal
coordinates of the chunk through its CRS and the chunk CRS, and the Console says so. When a mesh has
no CRS of its own, MetaFOCUS applies its transform to obtain the internal coordinates of the chunk,
and the Console says so when that transform is not the identity matrix.</li>
<li><b>Raster DEM:</b> rays meet the raster digital elevation model (DEM) of the chunk. In a
georeferenced chunk, they meet it in a local east-north-up CRS in metres with its origin at the
perspective centre, and otherwise in the world coordinates of the chunk. Each ray is followed in
steps of at most one raster DEM cell, measured horizontally, and after each step MetaFOCUS checks
whether the ray has passed below the raster DEM surface. Within the first step that does, the
crossing is located by repeated linear interpolation until the heights of ray and raster DEM agree
to within one hundredth of the cell size. A crossing over which the ray stays below the surface for
at least one cell horizontally is therefore never passed, but a crest that the ray only clips can
be. A hit beyond cells without data is the first crossing found on the raster DEM data, because a
gap is no evidence of a free view. The raster DEM is read through its own projection (so it may use
a different CRS from the chunk), and the normal follows from the raster DEM heights one cell either
side of the hit. This mode is fast (no BVH needed) and smooth.</li>
</ul>
<p>In every mode, rays meeting the surface at an incidence angle above about 84&deg; (cos&nbsp;&theta;&nbsp;&lt;&nbsp;0.1)
are excluded by this chosen incidence limit and counted. At incidence angles this close to grazing,
the projected sampling interval is stretched strongly, and the values depend increasingly on small
errors of the surface and its normal. The sampling grid is centred in the image, and the pixel
sampling step can be set from 5 to 500&nbsp;px, the range of the other two GSD tools.</p>

<h4>What is reported</h4>
<p>GSD here means the <i>ground sampling distance</i>, the local sampling interval on the imaged
surface. It is the photosite pitch (i.e. the distance between the centres of neighbouring photosites
on the sensor) projected onto the surface. This assumes that one pixel position corresponds to one
photosite, which holds unless the image was binned, downsampled, or otherwise resampled. For a
resampled image, the rays still run through the pixel positions of the calibrated image. The values
are therefore the sampling distances of the image, which then no longer correspond to one photosite
pitch. MetaFOCUS reports the sampling distances along the image row and the image column and, as its
GSD, the area-equivalent GSD described below, which is in general neither of them. The photosite
pitch and the GSD are both often, and ambiguously, called &lsquo;pixel size&rsquo;. Strictly
speaking, however, a pixel is an element of the image, not of the sensor, and has no size
(Verhoeven, 2018a, 2018b). Metashape&rsquo;s pixel size of the interior orientation group is the
photosite pitch for an image that keeps the sampling of the sensor.</p>
<p>At each sampled pixel position, four auxiliary rays at &plusmn;&frac12; px along the row and the
column are intersected with the local tangent plane at the hit of the central ray. The auxiliary
rays pass through the midpoints of the edges of the corresponding photosite for an image that keeps
the sampling of the sensor. The distances between opposite hits are the central-difference sampling
distances, the sampling distances at the sampled pixel position itself. The distance between the
hits of the rays through two neighbouring pixel positions is nearly the same, but it belongs to the
point midway between them. PixelColumn and PixelRow are the indices of the sampled pixel, counted
from 0 at the left and at the top of the image. The central ray passes through the pixel coordinates
(PixelColumn&nbsp;+&nbsp;&frac12;, PixelRow&nbsp;+&nbsp;&frac12;), the position of that pixel in
Metashape&rsquo;s pixel coordinates. The auxiliary rays pass through (PixelColumn, PixelRow&nbsp;+&nbsp;&frac12;),
(PixelColumn&nbsp;+&nbsp;1, PixelRow&nbsp;+&nbsp;&frac12;), (PixelColumn&nbsp;+&nbsp;&frac12;,
PixelRow), and (PixelColumn&nbsp;+&nbsp;&frac12;, PixelRow&nbsp;+&nbsp;1).</p>
<p>Two directional values are written, along the image row (GSD_row) and along the image column
(GSD_column), together with the angle &phi; between the two projected steps (Phi_deg). The GSD of
MetaFOCUS is the <b>area-equivalent GSD</b> (GSD_areaEquivalent), the side of the square with the
area that the two projected steps span, &radic;(GSD<sub>row</sub>&nbsp;&middot;&nbsp;GSD<sub>column</sub>&nbsp;&middot;&nbsp;sin&nbsp;&phi;).
To first order, this area is that onto which one sampling interval along the row and one along the
column project on the tangent plane, whatever its shape. For an image that keeps the sampling of the
sensor, this area is, to first order, the area of the GIFOV, the projection of one photosite. For a
frame camera with all lens distortion coefficients and the affinity and non-orthogonality
coefficients set to zero, the GIFOV of a square photosite on flat, level terrain is a square in a
vertical image. Under the same conditions, the GIFOV is a trapezium in a tilted image whose rows or
columns are parallel to the horizon and another quadrilateral in any other tilted image.</p>
<p>When the steps are perpendicular, the area-equivalent GSD equals the geometric mean of the two
directional values, &radic;(GSD<sub>row</sub>&nbsp;&middot;&nbsp;GSD<sub>column</sub>), which is
written as GSD_geometricMean. Otherwise, the geometric mean exceeds it by the factor 1/&radic;(sin&nbsp;&phi;).
When the surface is tilted diagonally to the image rows and columns, for example, the excess is 3&nbsp;%
at an incidence angle of 45&deg; and 12&nbsp;% at 60&deg;. Unlike the geometric mean, the
area-equivalent GSD depends only on the area that the two steps span, not on their directions. To
first order, the value of a surface point therefore does not change when the camera is rolled about
its camera axis. This holds provided that the camera model, apart from its affinity and
non-orthogonality, is symmetric about that axis. The camera model of a frame or fisheye camera is
symmetric in this sense with the decentring distortion coefficients set to zero, without additional
corrections, and without rolling-shutter compensation. The statistics, the inspection markers, and
the Console use the area-equivalent GSD.</p>
<p>The projected sampling interval is anisotropic. For a frame camera with all lens distortion
coefficients and the affinity and non-orthogonality coefficients set to zero, when the plane of
incidence contains the radial direction, the radial and tangential sampling distances are Z&middot;cos&alpha;/(c<sub>pix</sub>&middot;cos&theta;)
and Z/c<sub>pix</sub>. Here Z is the depth along the camera axis, c<sub>pix</sub> the calibrated
principal distance in pixels, and &alpha; the off-axis angle of the central ray. For an image that
keeps the sampling of the sensor, c<sub>pix</sub> is the principal distance c divided by the
photosite pitch p. <i>Either sampling distance can be the larger one</i> (their ratio is cos&alpha;/cos&theta;).
On the image axes through the principal point they are the row and column values. A nadir image over
flat horizontal ground, taken from the height Z above it, therefore yields the same value Z/c<sub>pix</sub>
in both directions across the whole format: the built-in sanity case.</p>
<p>The projection of one photosite (centred on the sampled position, a
square one photosite pitch wide on a sensor with equal pitches) onto the
surface is its
ground-projected instantaneous field of view (GIFOV). Schowengerdt
(2007) defines the GIFOV with the detector width, the width of the
light-sensitive area, whose projection is smaller when the fill factor
is below one.  On a plane, and with all lens distortion coefficients set
to
zero, the four corner rays give its exact shape, whereas with lens
distortion or on a curved surface its edges are curved.  It is a
different quantity from the GSD, the spacing of neighbouring sampled
positions on the surface.  The GIFOV and the GSD are geometric
quantities.  The GIFOV does not equal the surface patch whose reflected
radiation contributes to one pixel value: that patch is larger and has
no sharp edge.  The GSD describes the sampling interval, not the spatial
resolution.  MetaFOCUS &gt; Manual and Terminology defines
these terms with their sources.</p>

<h4>Images and file names</h4>
<p><b>Single image</b> exports the image chosen in the list of the oriented images of the chunk. An
image that shares its label with other images is listed as &lsquo;label (key N)&rsquo;, with its
camera key. The export always uses the image chosen, never another one with the same label. Its CSV
is written to the file chosen in the save dialog. <b>All oriented images in the chunk</b> and <b>Selected
images only</b> write one CSV per image into the chosen folder.</p>
<p>A line below <b>Selected images only</b> gives how many images Metashape reports as selected and
how many of them are oriented. An export uses these images, whatever the Photos pane highlights. The
line is refreshed when the window is activated or deactivated and when the export starts. An export
of the selected images names up to 20 of them in the Console and gives the number of the others.
When Metashape reports no selected oriented image, a message says so and what to do. Selecting
another item of the Workspace pane, such as the triangular mesh, the tie points, or a folder, clears
the selection of images that Metashape reports, although the Photos pane may still highlight
them. Selecting these images again starts with a click on an image that is not highlighted, because
a click on an image that the Photos pane still highlights does not select it in Metashape.</p>
<p>Each file is named</p>
<p><code>gsdValueExport_&lt;image&gt;_key&lt;N&gt;_&lt;project&gt;_&lt;chunk&gt;key&lt;M&gt;_&lt;surface&nbsp;mode&gt;.csv</code></p>
<p>with the label of the image and its camera key N, and the label of the chunk and its chunk key M.
Every run of characters in the image label other than letters, digits, &lsquo;.&rsquo;, &lsquo;_&rsquo;,
and &lsquo;-&rsquo; becomes one underscore, and the &lsquo;(key N)&rsquo; of the image list is not
repeated. Because the camera key is part of every name, a name never passes from one image of a
project to another when images are removed or renamed. A renamed image gets a new name, but no
export of the project replaces the file of another of its images. Labels that become equal in a file
name, or differ only in upper and lower case, stay apart through the key. An image keeps its file
name from run to run, whichever images a run exports. The chunk key, which Metashape gives a copied
chunk anew, keeps the files of chunks with the same label apart, also when a chunk is removed,
renamed, or moved in the project.</p>
<p>The tool replaces a file of an earlier run with the same name without asking when the comment
lines of that file show the same project file, chunk key, and image key. A write that fails part way
leaves that earlier file intact. After the export, the Console names the files that it replaced, up to
20 of them, and gives the number of the others. Chunk and camera keys are unique only within a project, so two
copies of a project with the same file name in different folders, or two projects that were never
saved, give the same names. The tool therefore asks before a batch replaces a file that records
another project, chunk, or image, or none, and it exports nothing when the answer is &lsquo;No&rsquo;.
When only one image is to be exported, the save dialog of <b>Single image</b> opens instead of the
folder dialog, with the same name as the default.</p>

<h4>CSV comment lines</h4>
<p>Every CSV starts with the comment lines &lsquo;Image&rsquo;, &lsquo;Image key&rsquo;, &lsquo;Surface
mode&rsquo;, &lsquo;CRS&rsquo;, &lsquo;Units&rsquo;, &lsquo;CRS units&rsquo;, &lsquo;Chunk&rsquo;, &lsquo;Chunk key&rsquo;,
&lsquo;Project&rsquo;, &lsquo;Pixel step&rsquo;, &lsquo;Sampled pixels&rsquo;, &lsquo;Rejected rays
(cos(theta)&nbsp;&lt;&nbsp;0.1)&rsquo;, &lsquo;Records&rsquo;, &lsquo;MetaFOCUS version&rsquo;, and
&lsquo;GSD definition&rsquo;. The line &lsquo;Image key&rsquo; gives the camera key of the image in
Metashape, which stays the same when the image is renamed. The line &lsquo;Project&rsquo; gives the
path of the project file, or &ldquo;(not saved)&rdquo;. With these lines, a file can be matched with
its image and with the statistics of the other two GSD tools. The project and the chunk key identify
the chunk, whose label need not be unique, so that the GSD Visualiser can tell whether files in
chunk units come from one chunk.</p>

<h4>Units, coordinates, and decimals</h4>
<p>In a scaled or georeferenced chunk all distances are in metres. For a chunk without a physical
scale the tool asks whether to continue with an arbitrary chunk-unit scale of 1.0. The values are
then in chunk units, and the Units line says so, with the text of the attribute 00_Units of the
Image Footprint Generator. The line reads <code># Units: chunk units (chunk not scaled) for
SlantDistance and the GSD columns (CRS_X to CRS_Z in the units of the CRS units line, Theta_deg and
Phi_deg in degrees)</code>. The column names carry no unit: the Units line gives it for
SlantDistance and the GSD columns (GSD_row, GSD_column, GSD_geometricMean, and
GSD_areaEquivalent).</p>
<p>The line &lsquo;CRS units&rsquo; gives the units of CRS_X, CRS_Y, and CRS_Z, for example
<code># CRS units: CRS_X degree, CRS_Y degree, CRS_Z metre</code> for a geographic CRS. CRS_X and
CRS_Y take the unit of the CRS, read from its well-known text (WKT), and CRS_Z the unit of its
vertical component. Without a vertical component, CRS_Z is in metres for a geographic CRS and for a
CRS in metres, and &lsquo;unknown&rsquo; otherwise. Internal chunk coordinates are in &lsquo;chunk
unit&rsquo;, and so are the coordinates of a chunk without a physical scale that has a local CRS or
no CRS. A local CRS leaves the coordinates of the chunk as they are, so its unit applies only when
the chunk has a physical scale. Metashape 2.3.2 gives a chunk without references the local CRS
&lsquo;Local Coordinates (m)&rsquo;. The GSD Visualiser writes these units on the axes of its heat
map.</p>
<p>The tool takes the scale of the chunk transform to be metric: Metashape derives it from Metashape
scale bars or from reference coordinates of markers or images, in a geographic, projected, or local
CRS. An imported chunk transform is taken to be metric as well. Any other chunk transform, for
example one that Align Coordinate Axes to Region sets in a chunk without scale, has an arbitrary
scale. Region Tools records the transform that it gives such a chunk in the chunk metadata, and as
long as the chunk transform is unchanged the tool reports chunk units, not metres. A transform with
an arbitrary scale that is set otherwise, by a script for example, cannot be recognised, and its
values are then not in metres. Without a chunk transform the hit points are internal chunk
coordinates, whatever CRS the Reference Settings name: the CRS line then reads <code>none (internal
chunk coordinates)</code>, and no markers are created.</p>
<p>GSD values are written with eight decimals.  In the CSV, slant
distances, heights, and projected or local coordinates get six
decimals, and longitude and latitude in a geographic CRS nine, a step
of at most about 0.1&nbsp;mm on the ground.  In the Console, slant
distances, heights, and projected or local coordinates get four
decimals.  A GSD or a slant distance with fewer than four significant
digits in these decimals gets four: with more
decimals in the CSV, and in exponent notation below
10<sup>&minus;4</sup> in the Console.  A GSD of 0.7512&nbsp;&micro;m
thus reads 0.0000007512 and 7.512e-07, not 0.00000075.  Coordinates and
heights get more decimals where theirs do not resolve a tenth of the
smallest GSD of the image in the CSV, or the GSD at the location in the
Console.  The decimals set the step of the output, not the uncertainty
of the coordinates.</p>
<p>The option ‘Delimiter and decimal separator’ chooses a comma with
a decimal point (1.25), a semicolon with a decimal comma (1,25), or a tab
with a decimal point.  The semicolon with the decimal comma suits Excel
with regional settings that use the decimal comma, for example German
or French.  The comment lines that start with # keep their text unchanged.</p>

<h4>Rolling shutter</h4>
<p>For an image without rolling-shutter compensation, the rays start at the perspective centre of
the image (Metashape&rsquo;s Camera.center) and pass through the full calibrated camera model, lens
distortion included, as for a central projection. For such an image, MetaFOCUS takes the ray of
Camera.unproject when its projection (Camera.project) lies within 10<sup>&minus;6</sup> px of the
pixel position, and otherwise refines it by Newton&rsquo;s method. Like the Interior Orientation
Viewer, MetaFOCUS refines the ray because Metashape&rsquo;s unprojection can stop before it has
converged (up to 0.041 px for a DJI M4E calibration in Metashape 2.3.2). A pixel position whose ray
cannot be brought within 0.001 px has no ray. The Console and the comment line &lsquo;Rays&rsquo; of
the CSV file of an image say for how many pixel positions the ray of Metashape&rsquo;s unprojection
projected back more than 0.001 px from the pixel position, so that MetaFOCUS refined it. They also
say how many pixel positions have no ray.</p>
<p>For an image whose interior orientation group has rolling-shutter compensation (Regularized or
Full), Metashape also estimates the camera motion during the readout. As a result, the position of
the camera, and with the Full model also its rotation, can change from one image row to the next.
For such an image each ray is the line of the 3D points whose residual (Camera.error, which applies
the motion) is zero at the pixel position of the ray. MetaFOCUS finds two such points by Newton&rsquo;s
method, starting from the ray from the perspective centre through the point of Camera.unproject, at
the distance where the previous ray of the image met the surface and at twice that distance. It then
checks the residual where the ray meets the surface and solves the ray again at that distance when
the residual there exceeds 0.001&nbsp;px. In the offline tests, a ray takes about 20 to 30
evaluations of Camera.error in addition to the unprojection, about 95 to 145 for each GSD sample of
five rays. Such images therefore take longer.</p>
<p>When a ray of such an image cannot be found in this way, every ray of that image starts at its
perspective centre, and the Console says why. Such a ray is followed only from its point nearest to
Camera.center, so a surface nearer to the camera than the distance that the camera moved during the
readout can be missed. For such an image the slant distance is a convention. It is measured from the
point of the ray nearest to Camera.center, because the line of zero residual does not show where on
it the perspective centre of the row lies. The Console names the images with rolling-shutter
compensation, and the comment lines &lsquo;Rolling shutter&rsquo; of their CSV files say whether
their rays follow the camera motion. When they do, the comment line &lsquo;Slant distance&rsquo; of
the CSV file names the convention of the column SlantDistance.</p>

<h4>Inspection markers</h4>
<p>Optionally, the tool creates two markers per image, <b>MaxGSD_&lt;label&gt;</b> and <b>MinGSD_&lt;label&gt;</b>,
at the locations of the largest and smallest area-equivalent GSD values, for visual inspection in
the Model view. They are inspection markers, not check points: their coordinates come from the
surface model, not from an independent measurement. Their reference location is set but disabled, so
they never act as control points, either in Metashape&rsquo;s bundle adjustment or in the counts of
the other MetaFOCUS tools. They are created without image measurements, so they have no position
estimated from the images. When the location of a marker cannot be written in the separate CRS for
markers of the Reference Settings, for example because one of the two CRSs is local, the marker is
not created, and the Console says so. Each also carries its provenance in its metadata
(MetaFOCUS/InspectionMarker), so that it stays an inspection marker when it is renamed or moved. The
check-point root mean square error (RMSE) of the Gradual Selection Filter and the tables of the
Marker Residual Inspector therefore leave it out, also when someone measures it in images. They are
kept in the marker group &lsquo;GSD extremes&rsquo;, so that they can be hidden or removed together.
A new export replaces the inspection markers with the same label that carry this provenance and have
no marker projection and no enabled reference location. Any other marker with that label, such as a
control point, is kept as it is, and the Console says so.</p>
<p>Adding markers clears the selection of images that Metashape reports, although the Photos pane
may still highlight them. Metashape clears it again after the export, so the tool cannot select the
images again. After an export that adds markers, also when they replace the markers of an earlier
export, the Console therefore says that the images must be selected again before the next run on
selected images. It also says that a click on an image that is not highlighted comes first.</p>

<h4>Progress, Cancel, and failures</h4>
<p><b>Cancel</b> in the progress window stops the run before the next image. An image that fails
(for example because its CSV is open in another program) is reported in the Console and skipped, and
the other images are exported. After a batch run, the final message gives the number of images
processed and exported, or after how many of how many images the run was cancelled. It also lists
the images that failed with the reason and names the output folder. When the surface cannot be
prepared (for example a triangular mesh too large for the available memory), the run ends with a
message.</p>

<h4>Console output</h4>
<p>For each image, the Console shows the surface mode, the pixel
step, and the numbers of sampled pixels, valid values, ray misses, and
rays rejected for grazing incidence.  It also shows the minimum, 2nd percentile,
median, mean, 98th percentile, and maximum of the
area-equivalent GSD, of the geometric mean of the row and column
sampling distances, of the slant distance, and of cos&nbsp;&theta;.
For the locations of the maximum and minimum GSD, it shows the column and row
indices of
the pixel, the coordinates in the chunk CRS, the row and column sampling
distances,
and the angle between the projected steps.  In raster DEM mode it counts
the missed rays by reason.  It counts those with the perspective centre
below the raster DEM surface and those with a crossing where the raster
DEM has no data.  It also counts those without a crossing within the
raster DEM extent and those without a crossing within the step limit of
the search.  The Console also notes an image whose perspective centre
lies below the raster DEM surface, so that every ray of the image is a
miss.  When
no surface could be built for an image (an image that is not oriented
or not calibrated, or one with fewer than three valid tie points in
plane mode), the Console says so instead.</p>
"""


# ===================================================================
# GUI
# ===================================================================

class GsdValueExporterDialog(QtWidgets.QDialog):

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("GSD Value Exporter")
        self.setWindowFlags(self.windowFlags()
                           & ~QtCore.Qt.WindowContextHelpButtonHint)
        self.setMinimumWidth(520)
        self._BuildUserInterface()

    def _BuildUserInterface(self):
        mainLayout = QtWidgets.QVBoxLayout(self)
        activeChunk = Metashape.app.document.chunk

        # ---- Header ----
        headerLabel = QtWidgets.QLabel(
            "<b>GSD Value Exporter</b>")
        headerLabel.setStyleSheet("font-size: 12pt;")
        mainLayout.addWidget(headerLabel)

        subtitleLabel = QtWidgets.QLabel(
            "Export GSD values at sampled pixel positions to CSV for analysis.")
        subtitleLabel.setStyleSheet("font-size: 10pt; color: #555;")
        mainLayout.addWidget(subtitleLabel)
        mainLayout.addSpacing(8)

        # ---- Chunk info ----
        activeChunkLabel = activeChunk.label if activeChunk else "(none)"
        hasMesh = ChunkHasTriangularMesh(activeChunk)
        hasDem = activeChunk.elevation is not None if activeChunk else False
        meshStatus = "Yes" if hasMesh else "No"
        if hasDem:
            demResolution = activeChunk.elevation.resolution
            demStatus = "Yes (cell size {:.4g} m)".format(
                demResolution) if demResolution else "Yes"
        else:
            demStatus = "No"
        chunkSummaryLabel = QtWidgets.QLabel(
            "Active chunk: <b>{}</b><br>"
            "Triangular mesh: {} &nbsp;|&nbsp; "
            "Raster DEM: {}".format(
                activeChunkLabel, meshStatus, demStatus))
        chunkSummaryLabel.setTextFormat(QtCore.Qt.RichText)
        chunkSummaryLabel.setWordWrap(True)
        mainLayout.addWidget(chunkSummaryLabel)
        mainLayout.addSpacing(4)

        # ---- Image selection group ----
        imageSelectionGroupBox = QtWidgets.QGroupBox("Image selection")
        imageSelectionLayout = QtWidgets.QVBoxLayout(imageSelectionGroupBox)

        self.singleImageRadioButton = QtWidgets.QRadioButton(
            "Single image")
        self.singleImageRadioButton.setChecked(True)
        self.allImagesRadioButton = QtWidgets.QRadioButton(
            "All oriented images in the chunk")
        self.selectedImagesRadioButton = QtWidgets.QRadioButton(
            "Selected images only")

        imageSelectionButtonGroup = QtWidgets.QButtonGroup(self)
        imageSelectionButtonGroup.addButton(self.singleImageRadioButton)
        imageSelectionButtonGroup.addButton(self.allImagesRadioButton)
        imageSelectionButtonGroup.addButton(self.selectedImagesRadioButton)

        imageSelectionLayout.addWidget(self.singleImageRadioButton)

        # Image list (for the single-image mode).
        dropdownRow = QtWidgets.QHBoxLayout()
        dropdownRow.addSpacing(20)
        self.imageComboBox = QtWidgets.QComboBox()
        self.imageComboBox.setMinimumWidth(300)
        if activeChunk:
            # Each entry carries the camera key, so that images with
            # the same label stay distinct (UniqueImageLabel shows the
            # key for them).
            duplicatedLabels = DuplicatedImageLabels(activeChunk.cameras)
            for camera in SelectExportCameras(activeChunk, "all"):
                self.imageComboBox.addItem(
                    UniqueImageLabel(camera, duplicatedLabels), camera.key)
        dropdownRow.addWidget(self.imageComboBox)
        dropdownRow.addStretch()
        imageSelectionLayout.addLayout(dropdownRow)

        imageSelectionLayout.addWidget(self.allImagesRadioButton)
        imageSelectionLayout.addWidget(self.selectedImagesRadioButton)
        # What Metashape reports as selected, which the Photos pane need not
        # show (ExportSelectionStatusText), refreshed when the activation of
        # the window changes and when the export starts.
        self.selectionStatusLabel = QtWidgets.QLabel("")
        self.selectionStatusLabel.setWordWrap(True)
        imageSelectionLayout.addWidget(self.selectionStatusLabel)
        self._RefreshSelectionStatus()

        # The image list is enabled in the single-image mode only.
        self.singleImageRadioButton.toggled.connect(
            self.imageComboBox.setEnabled)
        self.allImagesRadioButton.toggled.connect(
            lambda checked: self.imageComboBox.setEnabled(
                not checked and not self.selectedImagesRadioButton.isChecked()))
        self.selectedImagesRadioButton.toggled.connect(
            lambda checked: self.imageComboBox.setEnabled(
                not checked and not self.allImagesRadioButton.isChecked()))

        mainLayout.addWidget(imageSelectionGroupBox)

        # ---- Surface mode group ----
        surfaceGroup = QtWidgets.QGroupBox("Surface mode")
        surfaceLayout = QtWidgets.QVBoxLayout(surfaceGroup)

        self.planeRadioButton = QtWidgets.QRadioButton(
            "Plane (tie points)")
        self.triangularMeshRadioButton = QtWidgets.QRadioButton(
            "Triangular mesh")
        # Triangular mesh mode is the default when the chunk has a mesh,
        # because a plane stands for the surface only when the scene is
        # planar (see PLANE_SPREAD_WARNING_SHARE).
        (self.triangularMeshRadioButton if hasMesh else self.planeRadioButton).setChecked(True)
        self.rasterDemRadioButton = QtWidgets.QRadioButton(
            "Raster DEM")

        surfaceModeButtonGroup = QtWidgets.QButtonGroup(self)
        surfaceModeButtonGroup.addButton(self.planeRadioButton)
        surfaceModeButtonGroup.addButton(self.triangularMeshRadioButton)
        surfaceModeButtonGroup.addButton(self.rasterDemRadioButton)

        surfaceLayout.addWidget(self.planeRadioButton)
        surfaceLayout.addWidget(self.triangularMeshRadioButton)
        surfaceLayout.addWidget(self.rasterDemRadioButton)

        # Disable unavailable modes.
        if not hasMesh:
            self.triangularMeshRadioButton.setEnabled(False)
            self.triangularMeshRadioButton.setToolTip(
                "No triangular mesh available in this chunk.")
        if not hasDem:
            self.rasterDemRadioButton.setEnabled(False)
            self.rasterDemRadioButton.setToolTip(
                "No raster DEM available in this chunk.")

        # Pixel step.
        stepRow = QtWidgets.QHBoxLayout()
        self.stepLabel = QtWidgets.QLabel("GSD pixel sampling step:")
        stepRow.addWidget(self.stepLabel)
        self.stepSpinBox = QtWidgets.QSpinBox()
        self.stepSpinBox.setRange(5, 500)
        self.stepSpinBox.setValue(50)
        self.stepSpinBox.setSuffix(" px")
        self.stepSpinBox.setToolTip(
            "Sample every Nth pixel along the rows and the columns (5 to 500 px,\n"
            "as in the Image Parameter Exporter and the Image Footprint Generator).\n"
            "Smaller values give finer GSD maps but take longer.")
        stepRow.addWidget(self.stepSpinBox)
        stepRow.addStretch()
        surfaceLayout.addLayout(stepRow)

        mainLayout.addWidget(surfaceGroup)

        # ---- Options ----
        optionsGroup = QtWidgets.QGroupBox("Options")
        optionsLayout = QtWidgets.QVBoxLayout(optionsGroup)

        self.checkBoxCreateMarkers = QtWidgets.QCheckBox(
            "Create inspection markers at the maximum and minimum GSD locations")
        self.checkBoxCreateMarkers.setChecked(True)
        self.checkBoxCreateMarkers.setToolTip(
            "Places two inspection markers per image in the marker group \"GSD extremes\":\n"
            "MaxGSD_<label> and MinGSD_<label>. Their reference location is set\n"
            "but disabled, so they never act as control points, and they have no\n"
            "image measurements, so they are no check points either.\n"
            "Inspection markers of earlier exports with the same label are\n"
            "replaced, unless they have marker projections or an enabled\n"
            "reference location. Other markers with that label are kept.\n"
            "No markers are created when the chunk has no transform.")
        optionsLayout.addWidget(self.checkBoxCreateMarkers)

        mainLayout.addWidget(optionsGroup)
        mainLayout.addSpacing(4)

        # ---- Delimiter and decimal separator ----
        delimiterRow = QtWidgets.QHBoxLayout()
        delimiterRow.addWidget(QtWidgets.QLabel(CSV_FORMAT_ROW_LABEL_TEXT))
        self.delimiterComboBox = QtWidgets.QComboBox()
        FillCsvFormatComboBox(self.delimiterComboBox)
        delimiterRow.addWidget(self.delimiterComboBox)
        delimiterRow.addStretch()
        mainLayout.addLayout(delimiterRow)
        mainLayout.addSpacing(4)

        # ---- Buttons ----
        buttonRow = QtWidgets.QHBoxLayout()
        aboutButton = QtWidgets.QPushButton("About")
        exportButton = QtWidgets.QPushButton("Export\u2026")
        exportButton.setDefault(True)
        closeButton = QtWidgets.QPushButton("Close")
        buttonRow.addWidget(aboutButton)
        buttonRow.addStretch()
        buttonRow.addWidget(exportButton)
        buttonRow.addWidget(closeButton)
        mainLayout.addLayout(buttonRow)

        aboutButton.clicked.connect(self._ShowAbout)
        exportButton.clicked.connect(self._RunExport)
        closeButton.clicked.connect(self.reject)

    # ---- About ----

    def _RefreshSelectionStatus(self):
        """Show how many images Metashape reports as selected in the active
        chunk and how many of them are oriented."""
        self.selectionStatusLabel.setText(ExportSelectionStatusText(Metashape.app.document.chunk))

    def changeEvent(self, event):
        """Refresh the selection line when the window is activated or
        deactivated, for example when it comes back after a message."""
        # The window can change before the line exists, while it is built.
        if event.type() == QtCore.QEvent.ActivationChange and getattr(self, "selectionStatusLabel", None) is not None:
            self._RefreshSelectionStatus()
        super().changeEvent(event)

    def _ShowAbout(self):
        aboutDialog = QtWidgets.QDialog(self)
        aboutDialog.setWindowTitle("About: GSD Value Exporter")
        aboutDialog.setWindowFlags(
            aboutDialog.windowFlags()
            & ~QtCore.Qt.WindowContextHelpButtonHint)
        aboutDialog.setMinimumSize(520, 480)
        layout = QtWidgets.QVBoxLayout(aboutDialog)
        textBrowser = QtWidgets.QTextBrowser()
        textBrowser.setOpenExternalLinks(True)
        textBrowser.setHtml(_GSD_EXPORT_ABOUT_HTML)
        layout.addWidget(textBrowser)
        closeButton = QtWidgets.QPushButton("Close")
        closeButton.clicked.connect(aboutDialog.accept)
        layout.addWidget(closeButton)
        aboutDialog.exec_()

    # ---- Export ----

    def _RunExport(self):
        activeChunk = Metashape.app.document.chunk
        self._RefreshSelectionStatus()
        if not activeChunk:
            QtWidgets.QMessageBox.warning(
                self, "GSD Value Exporter", "No active chunk.")
            return

        useMesh = self.triangularMeshRadioButton.isChecked()
        useDem = self.rasterDemRadioButton.isChecked()
        pixelStep = self.stepSpinBox.value()
        createMarkers = self.checkBoxCreateMarkers.isChecked()

        if useMesh and activeChunk.model is None:
            QtWidgets.QMessageBox.warning(
                self, "GSD Value Exporter",
                "Triangular mesh mode requires a triangular mesh in the "
                "active chunk.")
            return

        if useDem and activeChunk.elevation is None:
            QtWidgets.QMessageBox.warning(
                self, "GSD Value Exporter",
                "Raster DEM mode requires a raster DEM in the active chunk.")
            return

        if (not useMesh and not useDem and
                (not activeChunk.tie_points or
                 not activeChunk.tie_points.points)):
            QtWidgets.QMessageBox.warning(
                self, "GSD Value Exporter",
                "Plane mode requires tie points in the active chunk.")
            return

        surfaceModeName = SurfaceModeName(useMesh, useDem)
        # Metres only for a physical scale: not without a chunk transform,
        # and not for the transform with an arbitrary scale that Align
        # Coordinate Axes to Region gave a chunk without one.
        chunkScale = PhysicalChunkScale(activeChunk)
        chunkIsScaled = chunkScale is not None
        # The notes of the choices, printed in the Console block of the
        # run, which opens once the images and the output are chosen.
        consoleNotes = []
        if not chunkIsScaled:
            reply = QtWidgets.QMessageBox.question(
                self, "GSD Value Exporter",
                "This chunk has no physical scale ({}), so ground sampling "
                "distances cannot be expressed in metres.\n\nProceed with an "
                "arbitrary chunk-unit scale of 1.0? The exported GSD values "
                "will then be in chunk units, not metres, and the CSV "
                "files say so.".format(DescribeMissingPhysicalScale(activeChunk)),
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.No)
            if reply != QtWidgets.QMessageBox.Yes:
                return
            chunkScale = 1.0
            consoleNotes.append("[GSD Value Exporter] Warning: the chunk is not scaled, "
                                "so the GSD values are in chunk units, not metres.")

        # Images to process.  The single image is identified by the
        # camera key stored with its list entry, never by its label,
        # which two images can share.
        duplicatedLabels = DuplicatedImageLabels(activeChunk.cameras)
        if self.singleImageRadioButton.isChecked():
            cameras = SelectExportCameras(
                activeChunk, "single", self.imageComboBox.currentData())
            if not cameras:
                QtWidgets.QMessageBox.warning(
                    self, "GSD Value Exporter",
                    "No oriented image is available in the list.")
                return

        elif self.selectedImagesRadioButton.isChecked():
            cameras = SelectExportCameras(activeChunk, "selected")
            if not cameras:
                noSelectionText = NoSelectedImageText("oriented image")
                print("[GSD Value Exporter] " + noSelectionText.split("\n")[0])
                QtWidgets.QMessageBox.warning(
                    self, "GSD Value Exporter", noSelectionText)
                return
            # What Metashape reports as selected opens the Console block.
            consoleNotes = SelectedImagesConsoleLines(len(SelectedMasterImages(activeChunk)), cameras) + consoleNotes

        else:  # All oriented images.
            cameras = SelectExportCameras(activeChunk, "all")
            if not cameras:
                QtWidgets.QMessageBox.warning(
                    self, "GSD Value Exporter",
                    "The chunk has no oriented image.")
                return

        # Output folder or file.
        document = Metashape.app.document
        projectName = ""
        if document.path:
            projectName = os.path.splitext(
                os.path.basename(document.path))[0]
        # One file name per master image of the chunk, whichever images
        # this run exports, so that a run never takes the name that
        # another image of the chunk gets in another run, with the chunk
        # key in every name.
        fileNameByKey = BuildGsdCsvFileNamesByKey(
            MasterImagesOfChunk(activeChunk), projectName, ChunkFileNameLabel(activeChunk), surfaceModeName)
        if len(cameras) == 1:
            defaultDirectory = os.path.dirname(document.path) if document.path else ""
            defaultName = fileNameByKey[cameras[0].key]
            outputPath = Metashape.app.getSaveFileName(
                "Save GSD CSV",
                dir=os.path.join(defaultDirectory, defaultName),
                filter="CSV (*.csv)")
            if not outputPath:
                return
            chosenPath = outputPath
            if not outputPath.lower().endswith(".csv"):
                outputPath += ".csv"
            # A file at the path with the added extension, which the save
            # dialog did not ask about, is replaced only after a question.
            if ReplacesUnconfirmedFile(chosenPath, outputPath) and QtWidgets.QMessageBox.question(
                    self, "GSD Value Exporter", ReplaceUnconfirmedFileQuestionText(outputPath),
                    QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                    QtWidgets.QMessageBox.No) != QtWidgets.QMessageBox.Yes:
                print("GSD Value Exporter: " + DeclinedReplacingText(outputPath))
                return
            outputDirectory = os.path.dirname(outputPath)
            # A single image is written to the path the user chose.
            outputFilePathByKey = {cameras[0].key: outputPath}
        else:
            outputDirectory = Metashape.app.getExistingDirectory(
                "Choose the output folder for the GSD CSV files")
            if not outputDirectory:
                return
            # The file name of each image of the batch among all master
            # images of the chunk.
            outputFilePathByKey = {
                camera.key: os.path.join(outputDirectory, fileNameByKey[camera.key])
                for camera in cameras}
            # A file of the same image of this chunk of this project is
            # replaced, any other file only when the user agrees.
            foreignPaths = ForeignExistingGsdFiles(
                outputFilePathByKey, document.path, getattr(activeChunk, "key", None))
            if foreignPaths:
                reply = QtWidgets.QMessageBox.question(
                    self, "GSD Value Exporter", ReplaceForeignFilesQuestion(foreignPaths, len(outputFilePathByKey)),
                    QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No, QtWidgets.QMessageBox.No)
                if reply != QtWidgets.QMessageBox.Yes:
                    declinedLine = ForeignFilesConsoleLine(len(foreignPaths), outputDirectory, isReplaced=False)
                    PrintConsoleLines(ConsoleBlockStartOfChunk("GSD Value Exporter", activeChunk) + consoleNotes
                                      + [declinedLine] + ConsoleBlockEnd())
                    return
                consoleNotes.append(ForeignFilesConsoleLine(len(foreignPaths), outputDirectory, isReplaced=True))

        # The images that Metashape reports as selected before the run, and
        # the inspection markers that the run creates, also those that
        # replace markers of an earlier run: adding a marker clears this
        # selection, which the Console then says (SelectionClearedConsoleLine).
        selectedKeysBefore = SelectedImageKeys(activeChunk)
        createdMarkerLabels = []
        # The files that the run replaces, named in the Console after the
        # run (ReplacedFilesConsoleLine), because a file of an earlier
        # export of the same image is replaced without a question.
        replacedFilePaths = []
        # The run is one Console block (ConsoleBlockStartOfChunk), which
        # every way out of the run closes.
        PrintConsoleLines(ConsoleBlockStartOfChunk("GSD Value Exporter", activeChunk) + consoleNotes)
        try:
            # Progress.
            progressDialog = QtWidgets.QProgressDialog(
                "Exporting GSD values...", "Cancel",
                0, len(cameras), self)
            progressDialog.setWindowTitle("GSD Value Exporter")
            progressDialog.setWindowModality(QtCore.Qt.WindowModal)
            progressDialog.setMinimumDuration(0)

            # Build the bounding volume hierarchy (BVH) in triangular mesh
            # mode and the track map in plane mode.  A failure here (for
            # example a shortage of memory on a very large triangular mesh)
            # ends the run with a message instead of leaving the busy
            # progress dialog open.
            bvhData = None
            pointByTrackId = None
            try:
                if useMesh:
                    progressDialog.setLabelText(
                        "Building the triangular mesh index (this may take a "
                        "moment)...")
                    progressDialog.setMaximum(0)
                    QtWidgets.QApplication.processEvents()

                    bvhData = BuildMeshBvh(activeChunk.model, chunk=activeChunk)
                    if bvhData[0] is None:
                        progressDialog.close()
                        QtWidgets.QMessageBox.critical(
                            self, "GSD Value Exporter",
                            "The triangular mesh index could not be built: "
                            "the triangular mesh has no faces.")
                        return

                if not useMesh and not useDem:
                    pointByTrackId = BuildTrackIdToPointMap(
                        activeChunk.tie_points)
            except Exception as exception:
                progressDialog.close()
                print("[GSD Value Exporter] Preparing the surface failed: "
                      "{}".format(DescribeException(exception)))
                QtWidgets.QMessageBox.critical(
                    self, "GSD Value Exporter",
                    "The surface could not be prepared:\n{}".format(
                        DescribeException(exception)))
                return

            progressDialog.setMaximum(len(cameras))
            progressDialog.setLabelText("Exporting GSD values...")

            csvDelimiter, decimalSeparator = ChosenCsvFormat(self.delimiterComboBox)
            for lineIndex, rollingShutterLine in enumerate(RollingShutterConsoleLines(cameras)):
                print(("[GSD Value Exporter] " if lineIndex == 0 else "") + rollingShutterLine)

            # One failing image (for example a CSV that is open in another
            # program) is reported and skipped, the others are exported.
            successCount = 0
            attemptedCount = 0
            cancelled = False
            failures = []
            planeWarnings = []
            for cameraIndex, camera in enumerate(cameras):
                if progressDialog.wasCanceled():
                    cancelled = True
                    break
                imageLabel = UniqueImageLabel(camera, duplicatedLabels)
                progressDialog.setValue(cameraIndex)
                progressDialog.setLabelText(
                    "Processing image {} of {}: {}".format(
                        cameraIndex + 1, len(cameras),
                        imageLabel))
                QtWidgets.QApplication.processEvents()

                # For a single image the user-chosen path is used, for a
                # batch the path from BuildGsdCsvFileNamesByKey.
                attemptedCount += 1
                try:
                    exportSucceeded = ProcessOneCamera(
                        camera, activeChunk, chunkScale, useMesh, useDem,
                        pixelStep, bvhData, pointByTrackId,
                        outputDirectory, createMarkers,
                        outputFilePath=outputFilePathByKey[camera.key],
                        csvDelimiter=csvDelimiter,
                        chunkIsScaled=chunkIsScaled, imageLabel=imageLabel,
                        projectName=projectName, decimalSeparator=decimalSeparator,
                        projectPath=document.path or "", planeWarnings=planeWarnings,
                        createdMarkerLabels=createdMarkerLabels, replacedFilePaths=replacedFilePaths)
                except Exception as exception:
                    failures.append("{}: {}".format(
                        imageLabel, DescribeException(exception)))
                    print("  {}: export failed: {}".format(
                        imageLabel, DescribeException(exception)))
                    continue
                if exportSucceeded:
                    successCount += 1

            progressDialog.close()

            # Plane mode: the images whose tie points lie far off their plane.
            planeWarningText = PlaneSpreadSummaryText(len(planeWarnings), attemptedCount)
            if planeWarningText:
                print("  " + planeWarningText)
        finally:
            if replacedFilePaths:
                print("  " + ReplacedFilesConsoleLine(replacedFilePaths))
            if createMarkers and createdMarkerLabels and selectedKeysBefore:
                print("  " + SelectionClearedConsoleLine())
            PrintConsoleLines(ConsoleBlockEnd())
        # The final message points to the warning lines of the images.
        planeWarningParagraph = ("\n\n" + PlaneSpreadSummaryText(len(planeWarnings), attemptedCount,
                                                                 pointsToConsole=True)
                                 if planeWarningText else "")
        if len(cameras) == 1 and not cancelled:
            if failures:
                QtWidgets.QMessageBox.critical(
                    self, "GSD Value Exporter",
                    "The export failed:\n{}".format(failures[0]))
            elif successCount:
                QtWidgets.QMessageBox.information(
                    self, "GSD Value Exporter",
                    "Exported GSD values for '{}'.{}".format(
                        UniqueImageLabel(cameras[0], duplicatedLabels), planeWarningParagraph))
            else:
                QtWidgets.QMessageBox.warning(
                    self, "GSD Value Exporter",
                    "No GSD values were computed for this image (see the "
                    "Console).")
        else:
            QtWidgets.QMessageBox.information(
                self, "GSD Value Exporter",
                BuildBatchExportSummary(
                    len(cameras), attemptedCount, successCount,
                    cancelled, failures, outputDirectory) + planeWarningParagraph)


# ===================================================================
# Menu entry point
# ===================================================================

_MENU_LABEL = "MetaFOCUS/Surface-Derived Project Outputs/GSD Value Exporter"
_ICON_NAME = "gsd_value_exporter_32.png"


def _ShowGsdValueExporterDialog():
    document = Metashape.app.document
    if not document or not document.chunk:
        QtWidgets.QMessageBox.information(
            QtWidgets.QApplication.activeWindow(), "GSD Value Exporter",
            "Open a project with at least one chunk first.")
        return
    GsdValueExporterDialog(QtWidgets.QApplication.activeWindow()).exec_()


def RegisterMenuItem():
    """Register the menu item.  Called by the MetaFOCUS loader."""
    try:
        Metashape.app.removeMenuItem(_MENU_LABEL)
    except Exception as suppressedError:
        ReportSuppressedError("gsdValueExporter.RegisterMenuItem", suppressedError)
    iconPath = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "icons", _ICON_NAME)
    if os.path.exists(iconPath):
        Metashape.app.addMenuItem(
            _MENU_LABEL, _ShowGsdValueExporterDialog, icon=iconPath)
    else:
        Metashape.app.addMenuItem(_MENU_LABEL, _ShowGsdValueExporterDialog)
