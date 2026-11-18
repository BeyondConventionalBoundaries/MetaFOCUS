"""
Image Parameter Exporter for Agisoft Metashape Professional
============================================================

Exports per-image parameters to CSV from within the Metashape GUI,
one row per master image of the active chunk (no slave images of a
multi-camera rig and no keyframes of an animation track).

Author:      Geert J. Verhoeven (ORCID: 0000-0003-4825-9604)
Version:     1.0.0 (2026-11-18)
Date:        2026-06-12
Licence:     GPL-3.0-or-later WITH GPL-3.0-linking-exception
Contact:     geert.verhoeven@univie.ac.at
Requires:    Agisoft Metashape Professional 2.x+
Developed for: Agisoft Metashape Professional 2.3.2 (tested offline, see README.md)
             PySide2 (bundled with Metashape)

Usage:
    MetaFOCUS > SfM Process Diagnostics > Image Parameter Exporter
    (installed by the MetaFOCUS loader).

Exportable categories:
    1.  Exif metadata (selectable fields).
    2.  Orientation status.
    3.  Interior orientation group and image collection.
    4.  Pixel count (numbers of pixel columns and rows).
    5.  Principal distance (mm), c_pix times the photosite pitch along
        the columns (pixel_size.y).
    6.  2D tie-point count (= Metashape's "Projections" column).
    7.  Matched keypoint count.
    8.  Reprojection error (RMS and maximum, px).
    9.  Reference panel data (selectable fields: coordinates,
        Accuracy values (a priori standard deviations), estimated
        positions, errors, standard deviations, rotation angles in the
        convention of the chunk).
    10. Per-image GSD via the dense auxiliary-ray tangent-plane method:
        minimum, 2nd percentile, mean, median, 98th percentile, and maximum.
        Three surface modes: plane, triangular mesh (3-ring),
        or raster DEM.
    11. Image scale number m (image scale 1:m, from the GSD and the
        geometric mean of the photosite pitches along the rows and the
        columns).

Notes:
    - At every sampled pixel position, four auxiliary rays through the
      midpoints of the edges of the corresponding photosite (+-1/2 along
      the row and the column in pixel coordinates) are intersected with
      the local tangent plane at the central ray's surface hit, and the
      area-equivalent GSD, sqrt(GSD_row x GSD_column x sin(phi)) with phi
      the angle between the projected row and column steps, is the value
      of that position entering the statistics.
    - Plane, triangular mesh, and raster DEM modes supply the
      fitted, interpolated, or derived surface normal that defines
      the local tangent plane.  In raster DEM mode the Console counts
      the missed GSD rays by reason and notes an image whose
      perspective centre lies below the raster DEM surface.
    - The GSD and the image scale number require a physical chunk
      scale (see PhysicalChunkScale): without one, and while the chunk
      transform is the one that Region Tools recorded for a chunk
      without a transform, the export stops with a message.
    - Reference panel data are extracted via chunk.exportReference()
      using the column cipher, matching the values shown in the
      Metashape Reference panel.  The rotation columns carry the
      angle names of the chunk's convention, X and Y are in degrees
      in a geographic CRS, images whose photos share a file name get
      empty Reference panel cells, and the export stops when the data
      cannot be exported.  The row of an image is found by the file
      name of its photo, by which Metashape's export named the row of a
      PNG image, and otherwise by its label
      (ReferenceRowOfImage).

Dependencies:
    - photogrammetryUtilities.py (must be in the same directory).
"""

import Metashape
import csv
import decimal
import math
import os
import re
import importlib

from PySide2 import QtWidgets, QtCore

# Force reload to avoid stale .pyc cache in Metashape.
from . import photogrammetryUtilities
importlib.reload(photogrammetryUtilities)

from .photogrammetryUtilities import (
    ReportSuppressedError,
    DescribeException,
    SharedGroupLabels, GroupLabelWithKey,
    ConvertToCamelCase,
    FormatCountWithNoun,
    NonFiniteResidualsText,
    PropagateCovarianceDiagonal,
    ValidatedCovarianceElements,
    BuildMeshBvh, BuildTrackIdToPointMap, GetValidProjections,
    PlaneSpreadOfImage, PlaneSpreadWarningLine, PlaneSpreadSummaryText, ChunkHasTriangularMesh,
    PhysicalChunkScale, IsChunkScalePhysical, HasArbitraryRegionToolsScale, HasRegionToolsPlacement,
    ComputeDenseGridGsdOnPlane, ComputeDenseGridGsdOnMesh,
    ComputeDenseGridGsdOnDem,
    DescribeRasterDemMisses, AddRasterDemMissCounts, PerspectiveCentreBelowRasterDemNote,
    ExportReferenceData, CameraReferenceCrs, ACCURACY_UNIT_FIELDS,
    CrsKind, CrsUnitNames, IsGeoreferencedChunk, EARTH_BOUND_CRS_KINDS,
    RotationAngleNames,
    IsMasterImage,
    CountMasterImages,
    CSV_FORMAT_ROW_LABEL_TEXT, CSV_FORMAT_SUITABILITY_TEXT, FillCsvFormatComboBox, ChosenCsvFormat,
    DataRowWithDecimalSeparator,
    ConsoleBlockEnd, ConsoleBlockStart, ConsoleLabelledLine, PrintConsoleLines,
    CsvFormatName, RollingShutterConsoleLines, ImageRays, ExactRayText,
    FormatLengthForCsv, LengthCsvDecimals,
    )
import functools

ALL_EXIF_FIELDS = [
    ("Camera Make",                      "Exif/Make",                    False),
    ("Camera Model",                     "Exif/Model",                   False),
    ("Lens Model",                       "Exif/LensModel",               False),
    ("Focal Length (mm)",                "Exif/FocalLength",             True),
    ("35mm Equivalent Focal Length (mm)","Exif/FocalLengthIn35mmFilm",  True),
    ("Exposure Time (s)",                "Exif/ExposureTime",            True),
    ("F-Number",                         "Exif/FNumber",                 True),
    ("ISO",                              "Exif/ISOSpeedRatings",         True),
    ("Date/Time Original",               "Exif/DateTimeOriginal",        False),
    ("GPS Latitude",                     "Exif/GPSLatitude",             False),
    ("GPS Longitude",                    "Exif/GPSLongitude",            False),
    ("GPS Altitude (m)",                 "Exif/GPSAltitude",             True),
    ("Software",                         "Exif/Software",                False),
]

GSD_MODE_PLANE = "plane"
GSD_MODE_MESH  = "mesh"
GSD_MODE_DEM   = "dem"

# Reference panel fields available for export.
# Order and names match the Metashape Reference panel columns.
# Fields marked "computed" are derived from the raw export data.
# Each entry: (column header, field key or "computed", name of the
# compute function in _REFERENCE_COMPUTE_FUNCTIONS or None).  The
# headers name the rotation angles with the whole words Yaw, Pitch, and
# Roll, which ApplyRotationAngleNames replaces by the names of the
# chunk's rotation angle convention.
ALL_REFERENCE_FIELDS = [
    ("X/Easting_(m)",                                  "X_reference",        None),
    ("Y/Northing_(m)",                                 "Y_reference",        None),
    ("Z/Altitude_(m)",                                 "Z_reference",        None),
    ("Accuracy_X/Y/Z_(m)",                             "computed",           "_ComputeCombinedAccuracyXyz"),
    ("Error_(m)",                                      "computed",           "_ComputeTotalErrorMetres"),
    ("Yaw_(deg)",                                      "Yaw_reference",      None),
    ("Pitch_(deg)",                                    "Pitch_reference",    None),
    ("Roll_(deg)",                                     "Roll_reference",     None),
    ("Accuracy_Yaw/Pitch/Roll_(deg)",                  "computed",           "_ComputeCombinedAccuracyYawPitchRoll"),
    ("Error_Yaw_Pitch_Roll_Root_Sum_of_Squares_(deg)", "computed",           "_ComputeTotalErrorDegrees"),
    ("X_error_(m)",                                    "X_error",            None),
    ("Y_error_(m)",                                    "Y_error",            None),
    ("Z_error_(m)",                                    "Z_error",            None),
    ("Yaw_error_(deg)",                                "Yaw_error",          None),
    ("Pitch_error_(deg)",                              "Pitch_error",        None),
    ("Roll_error_(deg)",                               "Roll_error",         None),
    ("X_estimated_(m)",                                "X_estimated",        None),
    ("Y_estimated_(m)",                                "Y_estimated",        None),
    ("Z_estimated_(m)",                                "Z_estimated",        None),
    ("Yaw_estimated_(deg)",                            "Yaw_estimated",      None),
    ("Pitch_estimated_(deg)",                          "Pitch_estimated",    None),
    ("Roll_estimated_(deg)",                           "Roll_estimated",     None),
    ("X_standard_deviation_(m)",                       "X_stdDeviation",     None),
    ("Y_standard_deviation_(m)",                       "Y_stdDeviation",     None),
    ("Z_standard_deviation_(m)",                       "Z_stdDeviation",     None),
    ("Yaw_standard_deviation_(deg)",                   "Yaw_stdDeviation",   None),
    ("Pitch_standard_deviation_(deg)",                 "Pitch_stdDeviation", None),
    ("Roll_standard_deviation_(deg)",                  "Roll_stdDeviation",  None),
    ("Coordinate_System",                              "CRS",                None),
]

# Column headers of the text columns of the CSV: the image label and
# path, the Exif fields that are text (ALL_EXIF_FIELDS), the orientation
# status, the interior orientation group, the image collection, and the
# CRS of the Reference panel data.  Every other column holds
# numbers, which the semicolon choice writes with a decimal comma (see
# DataRowWithDecimalSeparator), whereas a text cell keeps its text, also
# an image labelled 1.25.
IMAGE_PARAMETER_TEXT_COLUMN_HEADERS = frozenset(
    ["Image Label", "Image Path", "Oriented", "Interior Orientation Group", "Image Collection"]
    + [columnHeader for columnHeader, exifKeyUnused, isNumeric in ALL_EXIF_FIELDS if not isNumeric]
    + [columnHeader for columnHeader, fieldKey, computeFunctionNameUnused in ALL_REFERENCE_FIELDS
       if fieldKey == "CRS"])

# Tooltip of the combo box of the delimiter and the decimal separator:
# this CSV has no comment lines, but text cells that keep their text.
_CSV_FORMAT_TOOLTIP_TEXT = (CSV_FORMAT_SUITABILITY_TEXT
                            + "\nThe header row and text cells such as image labels and file paths\n"
                            "keep their text unchanged.")


# File extensions that an image label may lack where the reference export
# names the row of its image by the file name of the photo
# (ReferenceRowOfImage): those of the common image files that Metashape
# imports, in upper and lower case.
REFERENCE_ROW_EXTENSIONS = (".TIF", ".tif", ".TIFF", ".tiff", ".JPG", ".jpg", ".JPEG", ".jpeg", ".PNG", ".png",
                            ".DNG", ".dng", ".RAW", ".raw")


def PhotoFileName(camera):
    """The file name of the photo of *camera*: the last part of
    camera.photo.path after a forward slash or a backslash, or an empty
    text without a photo or a path."""
    photo = getattr(camera, "photo", None)
    path = str(getattr(photo, "path", "") or "") if photo is not None else ""
    return re.split(r"[\\/]", path)[-1] if path else ""


def ReferenceRowOfImage(referenceDataMap, camera):
    """The row of *camera* in the Reference panel values of
    ExportReferenceData, or None without one.  Metashape's reference export
    named the row of an image labelled plane_00 by the file name of its
    photo, plane_00.png, so the
    file name (PhotoFileName) is tried first, then the label, the label
    with a common extension (REFERENCE_ROW_EXTENSIONS), and the label
    without its extension.  The first name in *referenceDataMap* ends the
    search: a name shared by several images maps to an empty row, which
    gives empty cells rather than the row of another image."""
    label = str(getattr(camera, "label", "") or "")
    candidates = [PhotoFileName(camera), label]
    if label:
        candidates.extend(label + extension for extension in REFERENCE_ROW_EXTENSIONS)
        candidates.append(os.path.splitext(label)[0])
    for candidate in candidates:
        if candidate and candidate in referenceDataMap:
            return referenceDataMap[candidate]
    return None


def _ComputeCombinedAccuracyXyz(referenceValues):
    """Combined X/Y/Z accuracy: single value if all equal, else X/Y/Z."""
    accuracyX = referenceValues.get("Accuracy_X", "")
    accuracyY = referenceValues.get("Accuracy_Y", "")
    accuracyZ = referenceValues.get("Accuracy_Z", "")
    if accuracyX == accuracyY == accuracyZ and accuracyX != "":
        return accuracyX
    elif accuracyX or accuracyY or accuracyZ:
        return "{}/{}/{}".format(accuracyX, accuracyY, accuracyZ)
    return ""

def _ComputeCombinedAccuracyYawPitchRoll(referenceValues):
    """Combined accuracy of the three rotation angles (yaw, pitch, and
    roll, or the angles of the chunk's convention): single value if all
    equal, else the three values separated by slashes."""
    accuracyYaw = referenceValues.get("Accuracy_Yaw", "")
    accuracyPitch = referenceValues.get("Accuracy_Pitch", "")
    accuracyRoll = referenceValues.get("Accuracy_Roll", "")
    if accuracyYaw == accuracyPitch == accuracyRoll and accuracyYaw != "":
        return accuracyYaw
    elif accuracyYaw or accuracyPitch or accuracyRoll:
        return "{}/{}/{}".format(accuracyYaw, accuracyPitch, accuracyRoll)
    return ""

def _NumberTextDecimals(text):
    """The decimals of a number written as *text*, 6 for "0.012345" and 9
    for "2.134e-06", or None when *text* is not a finite number."""
    try:
        exponent = decimal.Decimal(str(text).strip()).as_tuple().exponent
    except (decimal.InvalidOperation, ValueError):
        return None
    if not isinstance(exponent, int):
        return None
    return max(0, -exponent)


def _ComputeTotalErrorMetres(referenceValues):
    """Total 3D position error = sqrt(ex^2 + ey^2 + ez^2), with six
    decimals or at least four significant digits (LengthCsvDecimals:
    six decimals write an error of 2.4 um as
    0.000002).  Beyond six, it gets no more decimals than the most that
    the three errors have as Metashape writes them, so that it claims no
    digits that they lack."""
    errorTexts = [referenceValues.get(key, "") for key in ("X_error", "Y_error", "Z_error")]
    try:
        errorX, errorY, errorZ = (float(errorText) for errorText in errorTexts)
    except (ValueError, TypeError):
        return ""
    totalError = math.sqrt(errorX**2 + errorY**2 + errorZ**2)
    decimals = LengthCsvDecimals(totalError, 6)
    componentDecimals = [_NumberTextDecimals(errorText) for errorText in errorTexts]
    if None not in componentDecimals:
        decimals = max(6, min(decimals, max(componentDecimals)))
    return "{:.{}f}".format(totalError, decimals)

def _ComputeTotalErrorDegrees(referenceValues):
    """Total rotation angle error, the root sum of squares
    sqrt(eyaw^2 + epitch^2 + eroll^2) of the errors of the three angles
    (yaw, pitch, and roll, or the angles of the chunk's convention)."""
    try:
        errorYaw = float(referenceValues.get("Yaw_error", ""))
        errorPitch = float(referenceValues.get("Pitch_error", ""))
        errorRoll = float(referenceValues.get("Roll_error", ""))
        return "{:.6f}".format(math.sqrt(errorYaw**2 + errorPitch**2 + errorRoll**2))
    except (ValueError, TypeError):
        return ""

# Map function names to actual functions for computed fields.
_REFERENCE_COMPUTE_FUNCTIONS = {
    "_ComputeCombinedAccuracyXyz": _ComputeCombinedAccuracyXyz,
    "_ComputeCombinedAccuracyYawPitchRoll": _ComputeCombinedAccuracyYawPitchRoll,
    "_ComputeTotalErrorMetres": _ComputeTotalErrorMetres,
    "_ComputeTotalErrorDegrees": _ComputeTotalErrorDegrees,
}

# Column headers of the horizontal reference and estimated coordinates
# when the CRS of the reference data is geographic: exportReference
# then writes longitude and latitude in degrees, not metres.
_GEOGRAPHIC_REFERENCE_HEADERS = {
    "X_reference": "X/Longitude_(deg)",
    "Y_reference": "Y/Latitude_(deg)",
    "X_estimated": "X_estimated_(deg)",
    "Y_estimated": "Y_estimated_(deg)",
}

# Column headers of the reference and estimated coordinates with the
# place of their unit, and the axis (0 for X, 1 for Y, 2 for Z) whose unit
# they take (ReferenceCoordinateUnits).
_REFERENCE_COORDINATE_HEADERS = {
    "X_reference": ("X/Easting_({})", 0),
    "Y_reference": ("Y/Northing_({})", 1),
    "Z_reference": ("Z/Altitude_({})", 2),
    "X_estimated": ("X_estimated_({})", 0),
    "Y_estimated": ("Y_estimated_({})", 1),
    "Z_estimated": ("Z_estimated_({})", 2),
}

# Symbols of units in the column headers, by the unit names of
# CrsUnitNames.  Metashape 2.3.2 named the unit of the CRS EPSG:2263, the
# US survey foot, ft in the headers of the 'Accuracy' values of its export.
_REFERENCE_UNIT_SYMBOLS = {"metre": "m", "degree": "deg", "foot": "ft", "US survey foot": "ftUS"}


def ReferenceUnitSymbol(unitName):
    """The symbol of the unit *unitName* (CrsUnitNames) in a column header:
    m, deg, ft, or ftUS, any other name unchanged, and m without a name."""
    if not unitName:
        return "m"
    return _REFERENCE_UNIT_SYMBOLS.get(unitName, unitName)


def ReferenceCoordinateUnits(coordinateSystem):
    """The symbols of the units of the X, Y, and Z reference and estimated
    coordinates in the CRS of the reference data *coordinateSystem*
    (CameraReferenceCrs): deg for the longitude and latitude of a geographic
    CRS, otherwise the unit of the CRS, and for Z the unit of the vertical
    component of a compound CRS, m for a geographic CRS without one, and
    otherwise the unit of the CRS.  In a check in Metashape 2.3.2 the export
    wrote X, Y, and Z in US survey feet for the CRS EPSG:2263."""
    horizontalUnit, verticalUnit = CrsUnitNames(coordinateSystem)
    if CrsKind(coordinateSystem) == "geographic":
        return "deg", "deg", ReferenceUnitSymbol(verticalUnit or "metre")
    horizontalSymbol = ReferenceUnitSymbol(horizontalUnit)
    return horizontalSymbol, horizontalSymbol, ReferenceUnitSymbol(verticalUnit or horizontalUnit)


def ReferenceAccuracyUnitSymbols(coordinateSystem, exportedUnits=None):
    """The symbols of the units of the 'Accuracy' values of X, Y, and Z in
    the CRS of the reference data *coordinateSystem* (CameraReferenceCrs).
    Metashape's reference export names them in its header (*exportedUnits*,
    ExportedAccuracyUnits): in a check in Metashape 2.3.2 it wrote metres,
    with (m), for a geographic CRS and for a projected CRS in metres, and
    for EPSG:2263 US survey feet, with (ft).  A unit other than m is taken
    as the unit of the coordinates of its axis (ReferenceCoordinateUnits),
    which tells the US survey foot from the foot, unless these are in
    metres or degrees.  Without a unit of the export, the unit is m for X
    and Y in a geographic CRS and otherwise that of the coordinates."""
    coordinateUnits = ReferenceCoordinateUnits(coordinateSystem)
    if CrsKind(coordinateSystem) == "geographic":
        expectedUnits = ("m", "m", coordinateUnits[2])
    else:
        expectedUnits = coordinateUnits
    symbols = []
    for axisIndex, expectedUnit in enumerate(expectedUnits):
        exportedUnit = exportedUnits[axisIndex] if exportedUnits else ""
        if not exportedUnit:
            symbols.append(expectedUnit)
        elif exportedUnit == "m" or expectedUnit in ("m", "deg"):
            symbols.append(exportedUnit)
        else:
            symbols.append(expectedUnit)
    return tuple(symbols)


def ExportedAccuracyUnits(referenceDataMap):
    """The units of the 'Accuracy' values of X, Y, and Z that the header of
    Metashape's reference export gave (the fields of ACCURACY_UNIT_FIELDS of
    ExportReferenceData), from the first row of *referenceDataMap* that has
    them, or None."""
    for rowFields in (referenceDataMap or {}).values():
        if not rowFields:
            continue
        units = tuple(rowFields.get(fieldName, "") for fieldName in ACCURACY_UNIT_FIELDS)
        if any(units):
            return units
    return None


# Rotation angle names of ALL_REFERENCE_FIELDS (the yaw, pitch, and roll
# convention), replaced by the names of the chunk's convention.
_YAW_PITCH_ROLL = ("Yaw", "Pitch", "Roll")
_ROTATION_NAME_PATTERN = re.compile(r"Yaw|Pitch|Roll|YPR")


def ApplyRotationAngleNames(columnHeader, rotationAngleNames):
    """*columnHeader* with yaw, pitch, and roll replaced by the three
    names in *rotationAngleNames* (see RotationAngleNames) and "YPR"
    by their initials, because chunk.exportReference() writes the
    angles in the rotation angle convention of the chunk."""
    replacementByName = dict(zip(_YAW_PITCH_ROLL, rotationAngleNames))
    replacementByName["YPR"] = "".join(angleName[0] for angleName in rotationAngleNames)
    return _ROTATION_NAME_PATTERN.sub(
        functools.partial(_ReplaceRotationAngleName, replacementByName), columnHeader)


def _ReplaceRotationAngleName(replacementByName, match):
    """Replacement of one rotation angle name matched by
    _ROTATION_NAME_PATTERN (see ApplyRotationAngleNames)."""
    return replacementByName[match.group(0)]


def ReferenceColumnHeader(fieldIndex, coordinateSystem, rotationAngleNames=_YAW_PITCH_ROLL,
                          exportedAccuracyUnits=None):
    """CSV header of the Reference panel field *fieldIndex* of
    ALL_REFERENCE_FIELDS for the CRS of the reference data
    *coordinateSystem* (CameraReferenceCrs), which can differ from the CRS
    of the chunk.  The reference and estimated coordinates carry the units
    of ReferenceCoordinateUnits, with longitude and latitude in degrees in
    a geographic CRS (see CrsKind), and the 'Accuracy' values of X, Y, and
    Z those of ReferenceAccuracyUnitSymbols for the units of the export
    *exportedAccuracyUnits*: one unit when the three are equal, otherwise
    the three separated by slashes.  The errors stay in metres, as the
    export writes them also for a CRS in feet (Metashape 2.3.2).  The
    rotation angles carry the names in *rotationAngleNames*."""
    columnHeader, fieldKey, computeFunctionName = ALL_REFERENCE_FIELDS[fieldIndex]
    if fieldKey in _REFERENCE_COORDINATE_HEADERS:
        if CrsKind(coordinateSystem) == "geographic" and fieldKey in _GEOGRAPHIC_REFERENCE_HEADERS:
            columnHeader = _GEOGRAPHIC_REFERENCE_HEADERS[fieldKey]
        else:
            headerTemplate, axisIndex = _REFERENCE_COORDINATE_HEADERS[fieldKey]
            columnHeader = headerTemplate.format(ReferenceCoordinateUnits(coordinateSystem)[axisIndex])
    elif computeFunctionName == "_ComputeCombinedAccuracyXyz":
        accuracyUnits = ReferenceAccuracyUnitSymbols(coordinateSystem, exportedAccuracyUnits)
        unitText = accuracyUnits[0] if len(set(accuracyUnits)) == 1 else "/".join(accuracyUnits)
        columnHeader = "Accuracy_X/Y/Z_({})".format(unitText)
    return ApplyRotationAngleNames(columnHeader, rotationAngleNames)


def UsesLocalEastNorthUp(chunk):
    """True when the image location uncertainty of *chunk* is given along
    east, north, and up of local east-north-up coordinates of its CRS
    (ComputeCameraLocationUncertaintyLocal): a chunk with a CRS whose
    world coordinates do not have the axes and the origin that Region
    Tools gave them (HasRegionToolsPlacement) and, for a geographic, projected, or
    geocentric CRS, are placed on the Earth (IsGeoreferencedChunk)."""
    crs = getattr(chunk, "crs", None)
    if crs is None or HasRegionToolsPlacement(chunk):
        return False
    if CrsKind(crs) in EARTH_BOUND_CRS_KINDS:
        return IsGeoreferencedChunk(chunk)
    return True


def LocationUncertaintyHeaders(chunk):
    """Column headers of the image location uncertainty, matching
    ComputeCameraLocationUncertainty: east, north, and up in metres for a
    chunk with a physical scale (see PhysicalChunkScale) whose
    uncertainty is given in local east-north-up coordinates
    (UsesLocalEastNorthUp), X, Y, and Z of the world (local) coordinates
    of the chunk in metres for any other chunk with a physical scale,
    and X, Y, and Z in chunk units for a chunk without a physical scale:
    of the internal chunk coordinates without a transform, of the world
    coordinates of the transform that Region Tools recorded."""
    if not IsChunkScalePhysical(chunk):
        return ["Location Uncertainty X (1-sigma, chunk units)",
                "Location Uncertainty Y (1-sigma, chunk units)",
                "Location Uncertainty Z (1-sigma, chunk units)"]
    if UsesLocalEastNorthUp(chunk):
        return ["Location Uncertainty East (1-sigma, m)",
                "Location Uncertainty North (1-sigma, m)",
                "Location Uncertainty Up (1-sigma, m)"]
    return ["Location Uncertainty X (1-sigma, m)",
            "Location Uncertainty Y (1-sigma, m)",
            "Location Uncertainty Z (1-sigma, m)"]


def _NormaliseExifValue(rawValue, isNumeric):
    if rawValue is None or rawValue == "": return ""
    if not isNumeric: return str(rawValue)
    stringValue = str(rawValue).strip()
    if "/" in stringValue:
        try:
            numerator, denominator = stringValue.split("/", 1)
            return str(float(numerator) / float(denominator))
        except (ValueError, ZeroDivisionError): return stringValue
    try: return str(float(stringValue))
    except ValueError: return stringValue

def _BuildDefaultCsvFilename(document, chunk):
    projectName = ""
    if document.path:
        projectName = os.path.splitext(os.path.basename(document.path))[0]
    chunkName = chunk.label or ""
    return "imageParameterExport_{}_{}.csv".format(
        ConvertToCamelCase(projectName) if projectName else "project",
        ConvertToCamelCase(chunkName) if chunkName else "chunk")

def DescribeScaling(chunk):
    """Scaling of *chunk* for the dialog and the messages.  A transform
    that Region Tools recorded for a chunk without a physical scale (see
    HasArbitraryRegionToolsScale) has an arbitrary scale, and a chunk
    whose transform has no physical scale is neither scaled nor
    georeferenced: Metashape 2.3.2 gives a chunk without reference data
    a transform without a scale."""
    if HasArbitraryRegionToolsScale(chunk):
        return ("Arbitrary scale (the chunk transform comes from Align Coordinate Axes to Region in a chunk "
                "without a physical scale)")
    if chunk.transform.matrix is None or not IsChunkScalePhysical(chunk): return "Not scaled or georeferenced"
    if HasRegionToolsPlacement(chunk):
        return "Scaled, with the axes that Align Coordinate Axes to Region gave the chunk (not georeferenced)"
    # Only a geographic, projected, or geocentric CRS with a placement on
    # the Earth georeferences the chunk: a local CRS (Local Coordinates
    # (m), for example) with a transform is scaled, by Metashape scale
    # bars or by reference coordinates in that local CRS.
    if IsGeoreferencedChunk(chunk): return "Georeferenced ({})".format(chunk.crs.name)
    for scaleBar in chunk.scalebars:
        if scaleBar.reference.distance is not None and scaleBar.reference.enabled:
            return "Scaled by Metashape scale bars"
    if CrsKind(chunk.crs) == "local":
        return "Scaled in a local CRS ({})".format(chunk.crs.name)
    return "Scaled (chunk transform present, source unknown)"


def ComputeReprojectionError(camera, validProjections):
    """RMS and maximum reprojection error (pixels) of the 2D tie points of
    the valid tie points on one image, and their count.  The residuals
    come from Camera.error (as in TiePointResidual), which applies the
    rolling-shutter motion of the image.  A residual that is not a finite
    number is left out of all three and counted in the Console, so that
    the RMS, the maximum, and the count always describe the same
    residuals."""
    if camera.transform is None or not validProjections: return (None, None, 0)
    sumSquaredError = 0.0; maximumSquaredError = 0.0; pointCount = 0
    nonFiniteCount = 0
    for point, projection2D in validProjections:
        try:
            errorVector = camera.error(point.coord, projection2D)
            if errorVector is None: continue
            squaredError = errorVector.norm() ** 2
        except Exception as suppressedError:
            ReportSuppressedError("imageParameterExporter.ComputeReprojectionError", suppressedError)
            continue
        if not math.isfinite(squaredError):
            nonFiniteCount += 1
            continue
        sumSquaredError += squaredError
        if squaredError > maximumSquaredError: maximumSquaredError = squaredError
        pointCount += 1
    if nonFiniteCount:
        print("  {}: {} left out of the reprojection error.".format(
            camera.label, NonFiniteResidualsText(nonFiniteCount)))
    if pointCount == 0: return (None, None, 0)
    return (math.sqrt(sumSquaredError / pointCount), math.sqrt(maximumSquaredError), pointCount)

def GeometricMeanPhotositePitchMetres(photositePitchVector):
    """Divisor of the image scale number, in metres: the geometric mean
    sqrt(px * py) of the photosite pitches along the rows (x) and the
    columns (y) of *photositePitchVector* (Metashape's pixel_size of the
    interior orientation group, in millimetres).  The GSD is the
    area-equivalent GSD, so the square of the quotient GSD / divisor is
    the area that the projected row and column steps span divided by
    px * py, the area that the two pitches span
    on the sensor, whatever the shape of the photosites.  When the
    projected steps are perpendicular, the quotient is the geometric
    mean of the image scale numbers along the rows (row GSD / px) and
    the columns (column GSD / py).  Dividing by px alone gives
    sqrt(py / px) times that number.  For square photosites
    the divisor is the photosite pitch.  The photosites are taken to be
    square when the y pitch is missing or not a positive number.  None
    when the x pitch is missing or not a positive number."""
    if not photositePitchVector:
        return None
    try:
        pitchAlongRows = float(photositePitchVector.x)
    except (AttributeError, TypeError, ValueError):
        return None
    if not (math.isfinite(pitchAlongRows) and pitchAlongRows > 0):
        return None
    try:
        pitchAlongColumns = float(photositePitchVector.y)
    except (AttributeError, TypeError, ValueError):
        pitchAlongColumns = pitchAlongRows
    if not (math.isfinite(pitchAlongColumns) and pitchAlongColumns > 0):
        pitchAlongColumns = pitchAlongRows
    if pitchAlongColumns == pitchAlongRows:
        return pitchAlongRows * 1e-3
    return math.sqrt(pitchAlongRows * pitchAlongColumns) * 1e-3


def GetPrincipalDistanceMillimetres(camera):
    """Calibrated principal distance c in millimetres: the principal
    distance in pixels c_pix (Metashape's calibration attribute f) times
    the photosite pitch along the columns (pixel_size.y), which
    Metashape calls the pixel size of the interior orientation group,
    or None.  In Metashape's frame camera model (Appendix D of its
    manual, p. 232), v = h * 0.5 + cy + y'f, so f scales the y image
    coordinate exactly, whereas u = w * 0.5 + cx + x'f + x'b1 + y'b2
    scales x by f + b1: the pitch along the columns gives c also when
    the two pitches differ.  The pitch along the rows
    (pixel_size.x) stands in when pixel_size.y is missing or not a
    positive number."""
    try:
        calibration = camera.calibration; sensor = camera.sensor
        if not calibration or not sensor: return None
        principalDistancePixels = calibration.f
        if not principalDistancePixels or principalDistancePixels <= 0: return None
        photositePitchVector = sensor.pixel_size
        if not photositePitchVector: return None
        photositePitch = getattr(photositePitchVector, "y", None)
        if not (photositePitch and photositePitch > 0):
            photositePitch = photositePitchVector.x
        if not (photositePitch and photositePitch > 0): return None
        return principalDistancePixels * photositePitch
    except Exception as suppressedError:
        ReportSuppressedError("imageParameterExporter.GetPrincipalDistanceMillimetres", suppressedError)
        return None

def GetMatchedKeypointCount(camera, tiePoints):
    """Number of projections of *camera* in the tie-point cloud, 0 when
    the image has none.  Failures other than a missing image are
    reported once in the Console."""
    try:
        cameraProjections = tiePoints.projections[camera]
    except KeyError:
        return 0
    except Exception as suppressedError:
        ReportSuppressedError("imageParameterExporter.GetMatchedKeypointCount", suppressedError)
        return 0
    return len(cameraProjections) if cameraProjections else 0

def ComputeCameraLocationUncertaintyLocal(camera, chunk):
    """Image location standard deviations (1 sigma) in metres,
    propagated from the location covariance through the chunk
    transform: for a chunk with a CRS along east, north, and up of the
    local east-north-up coordinates at the perspective centre, for a
    chunk without one along the axes of its world (local) coordinates.
    With the axes and the origin that Region Tools gave the world
    coordinates, and in a
    chunk with a geographic, projected, or geocentric CRS that is not
    placed on the Earth (UsesLocalEastNorthUp), they are along the axes
    of its world coordinates, in chunk units for the arbitrary scale of
    Region Tools, because local east-north-up coordinates of a CRS need a
    georeference.  Returns None if the covariance, the perspective
    centre, or the transform is unavailable."""
    covarianceMatrix = camera.location_covariance
    perspectiveCentre = camera.center
    transformMatrix = chunk.transform.matrix
    if covarianceMatrix is None or perspectiveCentre is None or transformMatrix is None:
        return None
    combinedMatrix = transformMatrix
    if UsesLocalEastNorthUp(chunk):
        perspectiveCentreWorld = transformMatrix.mulp(perspectiveCentre)
        combinedMatrix = chunk.crs.localframe(perspectiveCentreWorld) * transformMatrix
    jacobianRows = [[combinedMatrix[row, column] for column in range(3)]
                    for row in range(3)]
    return PropagateCovarianceDiagonal(jacobianRows, covarianceMatrix)

def ComputeCameraLocationUncertainty(camera, chunk):
    """Image location standard deviations (1 sigma) for the columns of
    LocationUncertaintyHeaders.  With a chunk transform they come from
    ComputeCameraLocationUncertaintyLocal (metres, east-north-up with a
    CRS, along the transformed chunk axes without one).  Without a
    transform they are the square roots of the covariance diagonal
    along the internal chunk axes, in chunk units, after the same check
    as with a transform (ValidatedCovarianceElements): a matrix that is
    not finite, symmetric, and positive semi-definite raises ValueError,
    which the export reports in the Console and turns into empty cells,
    instead of hiding a negative variance behind its absolute value.
    Returns None if the covariance (or the perspective centre) is
    unavailable."""
    if getattr(chunk.transform, "matrix", None) is not None:
        return ComputeCameraLocationUncertaintyLocal(camera, chunk)
    covarianceMatrix = camera.location_covariance
    if covarianceMatrix is None:
        return None
    covarianceElements = ValidatedCovarianceElements(covarianceMatrix)
    return tuple(math.sqrt(max(covarianceElements[axisIndex][axisIndex], 0.0))
                 for axisIndex in range(3))

def FormatImageScaleNumber(imageScaleNumber):
    """The image scale number in the CSV: a whole number from 1,000 on,
    and four significant digits below
    (FormatLengthForCsv: whole numbers write 0
    for a magnification of 2 or more, as in a macro project)."""
    return FormatLengthForCsv(imageScaleNumber, 0)


def ExportImageParametersCsv(
        chunk, outputFilePath, selectedExifIndices,
        includePixelCount, includePrincipalDistance,
        includeInteriorOrientationGroup, includeImageCollection,
        includeOrientation, includeTiePointCount,
        includeMatchedKeypointCount, includeReprojectionError,
        includeLocationUncertainty,
        selectedReferenceIndices,
        includeGsd, includeImageScaleNumber,
        gsdMode, gsdPixelStep,
        csvDelimiter=",",
        decimalSeparator=".",
        progressCallback=None,
        planeWarnings=None):

    """Write the selected per-image parameters to a CSV file, one row
    per master image (see IsMasterImage: no slave images of a
    multi-camera rig and no keyframes), oriented or not.  The GSD and the
    image scale number require a physical chunk scale (see
    PhysicalChunkScale).  In raster DEM mode the Console notes every
    image whose perspective centre lies below the raster DEM surface and
    counts the missed GSD rays of all images by reason.  With
    *decimalSeparator* a comma (the semicolon choice of
    CSV_FORMAT_CHOICES), the numbers of the numeric columns are written
    with a decimal comma, while the header row and the text columns
    (IMAGE_PARAMETER_TEXT_COLUMN_HEADERS) keep their text.  In plane mode,
    an image whose tie points lie far from their plane gets a warning
    line in the Console (PlaneSpreadWarningLine), and its label is added
    to the list *planeWarnings* when one is given.  Returns the number of
    rows written."""
    # The labels that two interior orientation groups share, which get
    # their keys.
    try:
        sharedGroupLabels = (SharedGroupLabels(list(getattr(chunk, "sensors", None) or ()))
                             if includeInteriorOrientationGroup else set())
    except Exception as suppressedError:
        ReportSuppressedError("imageParameterExporter.ExportImageParametersCsv.sharedGroupLabels", suppressedError)
        sharedGroupLabels = set()
    needTiePoints = (includeTiePointCount or includeMatchedKeypointCount
                     or includeReprojectionError
                     or ((includeGsd or includeImageScaleNumber) and gsdMode == GSD_MODE_PLANE))
    tiePoints = None; pointByTrackId = None; chunkScale = None; bvhData = None

    if needTiePoints:
        if chunk.tie_points is None or chunk.tie_points.points is None:
            raise RuntimeError("Tie points required: orient the images first.")
        tiePoints = chunk.tie_points; pointByTrackId = BuildTrackIdToPointMap(tiePoints)

    if includeGsd or includeImageScaleNumber:
        # Metres only for a physical scale, the rule of the GSD Value
        # Exporter and the Image Footprint Generator.
        chunkScale = PhysicalChunkScale(chunk)

    if includeGsd or includeImageScaleNumber:
        if chunkScale is None:
            raise RuntimeError("The GSD requires a chunk with a physical scale (from Metashape scale bars, "
                               "reference coordinates, or an imported chunk transform).\n{}".format(
                                   DescribeScaling(chunk)))
        if CountMasterImages(chunk)[1] == 0:
            raise RuntimeError("The GSD requires oriented images.")
        if gsdMode == GSD_MODE_MESH:
            if chunk.model is None:
                raise RuntimeError("The GSD in triangular mesh mode requires a triangular mesh.")
            if progressCallback: progressCallback(-1, 0)
            bvhData = BuildMeshBvh(chunk.model, chunk=chunk)
            if bvhData[0] is None:
                raise RuntimeError("The triangular mesh has no faces.")
        elif gsdMode == GSD_MODE_DEM:
            if chunk.elevation is None:
                raise RuntimeError("The GSD in raster DEM mode requires a raster DEM.")

    cameraList = [candidateCamera for candidateCamera in chunk.cameras if IsMasterImage(candidateCamera)]

    # Export reference panel data if requested.  A failed export (None,
    # the reason is in the Console) or an export without any image row
    # stops the run rather than writing empty reference columns.  A
    # row name shared by several images maps to an empty entry, so those
    # images get empty cells and the run goes on.
    referenceDataMap = {}
    if selectedReferenceIndices:
        referenceDataMap = ExportReferenceData(chunk)
        if referenceDataMap is None:
            raise RuntimeError(
                "Reference panel data could not be exported (see the "
                "Console). Untick Reference panel data to export the "
                "other columns.")
        if cameraList and not referenceDataMap:
            raise RuntimeError(
                "The exported Reference panel data contain no image rows. "
                "Untick Reference panel data to export the other columns.")

    gsdTag = "(plane)" if gsdMode == GSD_MODE_PLANE else (
        "(triangular mesh)" if gsdMode == GSD_MODE_MESH else "(raster DEM)")

    headerColumns = ["Image Label", "Image Path"]
    if includePixelCount: headerColumns.extend(["Pixel Columns", "Pixel Rows"])
    if selectedExifIndices:
        for index in selectedExifIndices: headerColumns.append(ALL_EXIF_FIELDS[index][0])
    if includeOrientation: headerColumns.append("Oriented")
    if includePrincipalDistance: headerColumns.append("Principal Distance (mm)")
    if includeInteriorOrientationGroup: headerColumns.append("Interior Orientation Group")
    if includeImageCollection: headerColumns.append("Image Collection")
    if includeTiePointCount: headerColumns.append("2D Tie Point Count (Projections)")
    if includeMatchedKeypointCount: headerColumns.append("Matched Keypoint Count")
    if includeReprojectionError: headerColumns.extend(["Reprojection Error RMS (px)", "Reprojection Error Maximum (px)"])
    if includeLocationUncertainty:
        headerColumns.extend(LocationUncertaintyHeaders(chunk))
    if selectedReferenceIndices:
        for index in selectedReferenceIndices:
            headerColumns.append(ReferenceColumnHeader(index, CameraReferenceCrs(chunk), RotationAngleNames(chunk),
                                                       ExportedAccuracyUnits(referenceDataMap)))
    if includeGsd:
        for statisticLabel in ["Minimum", "2nd Percentile", "Mean", "Median", "98th Percentile", "Maximum"]:
            headerColumns.append("GSD {} {} (m)".format(statisticLabel, gsdTag))
        headerColumns.append("GSD Coverage {} (%)".format(gsdTag))
    if includeImageScaleNumber:
        for statisticLabel in ["Minimum", "2nd Percentile", "Mean", "Median", "98th Percentile", "Maximum"]:
            headerColumns.append("Image Scale Number {}".format(statisticLabel))

    imageCount = len(cameraList); dataRows = []
    rasterDemMissCounts = {}

    for cameraIndex, camera in enumerate(cameraList):
        if progressCallback: progressCallback(cameraIndex, imageCount)
        row = [camera.label]
        try: row.append(camera.photo.path if camera.photo else "")
        except Exception as suppressedError:
            ReportSuppressedError("imageParameterExporter.ExportImageParametersCsv.imagePath", suppressedError)
            row.append("")

        if includePixelCount:
            try:
                calibration = camera.calibration
                if calibration and calibration.width and calibration.height:
                    row.extend([str(calibration.width), str(calibration.height)])
                elif camera.sensor:
                    row.extend([str(camera.sensor.width), str(camera.sensor.height)])
                else: row.extend(["", ""])
            except Exception as suppressedError:
                ReportSuppressedError("imageParameterExporter.ExportImageParametersCsv.pixelCount", suppressedError)
                row.extend(["", ""])

        if selectedExifIndices:
            metaData = {}
            try:
                if camera.photo and camera.photo.meta: metaData = camera.photo.meta
            except Exception as suppressedError:
                ReportSuppressedError("imageParameterExporter.ExportImageParametersCsv.exifMetadata", suppressedError)
            for index in selectedExifIndices:
                _, exifKey, isNumeric = ALL_EXIF_FIELDS[index]
                # A tag the image does not carry is expected and leaves
                # the cell empty, any other failure is reported.
                try: rawValue = metaData[exifKey]
                except KeyError: rawValue = ""
                except Exception as suppressedError:
                    ReportSuppressedError("imageParameterExporter.ExportImageParametersCsv.exifTag", suppressedError)
                    rawValue = ""
                row.append(_NormaliseExifValue(rawValue, isNumeric))

        if includeOrientation: row.append("Yes" if camera.transform else "No")

        if includePrincipalDistance:
            principalDistance = GetPrincipalDistanceMillimetres(camera)
            row.append("{:.4f}".format(principalDistance) if principalDistance else "")

        if includeInteriorOrientationGroup:
            try: interiorOrientationGroupLabel = (GroupLabelWithKey(camera.sensor, sharedGroupLabels)
                                                  if camera.sensor else "")
            except Exception as suppressedError:
                ReportSuppressedError("imageParameterExporter.ExportImageParametersCsv.interiorOrientationGroup", suppressedError)
                interiorOrientationGroupLabel = ""
            row.append(interiorOrientationGroupLabel)

        if includeImageCollection:
            try: imageCollectionLabel = camera.group.label if camera.group else ""
            except Exception as suppressedError:
                ReportSuppressedError("imageParameterExporter.ExportImageParametersCsv.imageCollection", suppressedError)
                imageCollectionLabel = ""
            row.append(imageCollectionLabel)

        validProjections = None
        if needTiePoints: validProjections = GetValidProjections(camera, tiePoints, pointByTrackId)
        if includeTiePointCount: row.append(str(len(validProjections)) if validProjections else "0")
        if includeMatchedKeypointCount: row.append(str(GetMatchedKeypointCount(camera, tiePoints)))

        if includeReprojectionError:
            rmsError, maximumError, _ = ComputeReprojectionError(camera, validProjections or [])
            if rmsError is not None: row.extend(["{:.4f}".format(rmsError), "{:.4f}".format(maximumError)])
            else: row.extend(["", ""])

        if includeLocationUncertainty:
            uncertainty = None
            try:
                uncertainty = ComputeCameraLocationUncertainty(camera, chunk)
            except Exception as suppressedError:
                ReportSuppressedError("imageParameterExporter.ExportImageParametersCsv.locationUncertainty", suppressedError)
            if uncertainty: row.extend([FormatLengthForCsv(uncertaintyValue, 6) for uncertaintyValue in uncertainty])
            else: row.extend(["", "", ""])

        if selectedReferenceIndices:
            # The row of the image (ReferenceRowOfImage): a name shared by
            # several images maps to an empty entry, which gives empty cells.
            cameraReferenceData = ReferenceRowOfImage(referenceDataMap, camera)
            for index in selectedReferenceIndices:
                _, fieldKey, computeFunctionName = ALL_REFERENCE_FIELDS[index]
                if cameraReferenceData:
                    if fieldKey == "computed" and computeFunctionName:
                        computeFunction = _REFERENCE_COMPUTE_FUNCTIONS.get(computeFunctionName)
                        row.append(computeFunction(cameraReferenceData) if computeFunction else "")
                    else:
                        row.append(cameraReferenceData.get(fieldKey, ""))
                else:
                    row.append("")

        gsdStatistics = None
        if includeGsd or includeImageScaleNumber:
            # The rays of the image, which report the rays of
            # Camera.unproject that MetaFOCUS refined and the pixel
            # positions without a ray.
            imageRays = ImageRays(camera, "imageParameterExporter.ExportImageParametersCsv")
            if gsdMode == GSD_MODE_MESH:
                gsdStatistics = ComputeDenseGridGsdOnMesh(camera, bvhData, chunkScale, gsdPixelStep,
                                                          imageRays=imageRays)
            elif gsdMode == GSD_MODE_DEM:
                rasterDemReport = {}
                gsdStatistics = ComputeDenseGridGsdOnDem(camera, chunk, chunkScale, gsdPixelStep, rasterDemReport,
                                                         imageRays=imageRays)
                AddRasterDemMissCounts(rasterDemMissCounts, rasterDemReport.get("missCountsByReason") or {})
                if rasterDemReport.get("perspectiveCentreBelowSurface"):
                    print(PerspectiveCentreBelowRasterDemNote(camera.label))
            else:
                gsdStatistics = ComputeDenseGridGsdOnPlane(camera, validProjections or [], chunkScale, gsdPixelStep,
                                                           imageRays=imageRays)
                planeWarningLine = PlaneSpreadWarningLine(camera.label, PlaneSpreadOfImage(camera, validProjections))
                if planeWarningLine is not None:
                    print(planeWarningLine)
                    if planeWarnings is not None:
                        planeWarnings.append(camera.label)
            rayText = ExactRayText(imageRays.rayReport)
            if rayText:
                print("{}: {}".format(camera.label, rayText))

        if includeGsd:
            if gsdStatistics:
                for key in ["minimum", "percentile02", "average", "median", "percentile98", "maximum"]:
                    row.append(FormatLengthForCsv(gsdStatistics[key], 8))
                if gsdStatistics["totalSampleCount"] > 0:
                    row.append("{:.1f}".format(100.0 * gsdStatistics["validSampleCount"] / gsdStatistics["totalSampleCount"]))
                else: row.append("")
            else: row.extend([""] * 7)

        if includeImageScaleNumber:
            if gsdStatistics:
                # The geometric mean of the pitches along the rows and the
                # columns, so that m squared is the area that the
                # projected row and column steps span divided by the area that the two pitches span on
                # the sensor (the geometric mean of the two directional
                # image scale numbers when the steps are perpendicular).
                geometricMeanPhotositePitchMetres = None
                try:
                    geometricMeanPhotositePitchMetres = GeometricMeanPhotositePitchMetres(camera.sensor.pixel_size)
                except Exception as suppressedError:
                    ReportSuppressedError("imageParameterExporter.ExportImageParametersCsv.photositePitch", suppressedError)
                if geometricMeanPhotositePitchMetres and geometricMeanPhotositePitchMetres > 0:
                    for key in ["minimum", "percentile02", "average", "median", "percentile98", "maximum"]:
                        row.append(FormatImageScaleNumber(gsdStatistics[key] / geometricMeanPhotositePitchMetres))
                else: row.extend([""] * 6)
            else: row.extend([""] * 6)

        dataRows.append(row)

    if progressCallback: progressCallback(imageCount, imageCount)
    rasterDemMissDescription = DescribeRasterDemMisses(rasterDemMissCounts)
    if rasterDemMissDescription:
        print("Raster DEM misses of the GSD rays: {}.".format(rasterDemMissDescription))
    textColumnIndices = {columnIndex for columnIndex, columnHeader in enumerate(headerColumns)
                         if columnHeader in IMAGE_PARAMETER_TEXT_COLUMN_HEADERS}
    with open(outputFilePath, "w", newline="", encoding="utf-8") as fileHandle:
        csvWriter = csv.writer(fileHandle, delimiter=csvDelimiter)
        csvWriter.writerow(headerColumns)
        csvWriter.writerows(DataRowWithDecimalSeparator(row, decimalSeparator, textColumnIndices)
                            for row in dataRows)
    return len(dataRows)


class ExifSelectorDialog(QtWidgets.QDialog):
    def __init__(self, selectedIndices, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Select Exif Fields")
        self.setWindowFlags(self.windowFlags() & ~QtCore.Qt.WindowContextHelpButtonHint)
        self.setMinimumWidth(380)
        mainLayout = QtWidgets.QVBoxLayout(self)
        buttonRow = QtWidgets.QHBoxLayout()
        buttonSelectAll = QtWidgets.QPushButton("Select all")
        buttonSelectNone = QtWidgets.QPushButton("Select none")
        buttonRow.addWidget(buttonSelectAll); buttonRow.addWidget(buttonSelectNone)
        buttonRow.addStretch(); mainLayout.addLayout(buttonRow)
        self.checkBoxList = []
        for index, (columnName, _, _) in enumerate(ALL_EXIF_FIELDS):
            checkBox = QtWidgets.QCheckBox(columnName)
            checkBox.setChecked(index in selectedIndices)
            self.checkBoxList.append(checkBox); mainLayout.addWidget(checkBox)
        buttonSelectAll.clicked.connect(lambda: [eachCheckBox.setChecked(True) for eachCheckBox in self.checkBoxList])
        buttonSelectNone.clicked.connect(lambda: [eachCheckBox.setChecked(False) for eachCheckBox in self.checkBoxList])
        mainLayout.addSpacing(8)
        okCancelRow = QtWidgets.QHBoxLayout()
        buttonOk = QtWidgets.QPushButton("OK"); buttonOk.setDefault(True)
        buttonCancel = QtWidgets.QPushButton("Cancel")
        okCancelRow.addStretch(); okCancelRow.addWidget(buttonOk); okCancelRow.addWidget(buttonCancel)
        mainLayout.addLayout(okCancelRow)
        buttonOk.clicked.connect(self.accept); buttonCancel.clicked.connect(self.reject)
    def GetSelectedIndices(self):
        return [index for index, checkBox in enumerate(self.checkBoxList) if checkBox.isChecked()]


class ReferenceSelectorDialog(QtWidgets.QDialog):
    """Dialog to select which Reference panel fields to export.  The
    check boxes carry *columnHeaders* (the CSV headers for the active
    chunk, see ReferenceColumnHeader) when given."""
    def __init__(self, selectedIndices, parent=None, columnHeaders=None):
        super().__init__(parent)
        self.setWindowTitle("Select Reference Panel Fields")
        self.setWindowFlags(self.windowFlags() & ~QtCore.Qt.WindowContextHelpButtonHint)
        self.setMinimumWidth(380)
        mainLayout = QtWidgets.QVBoxLayout(self)
        buttonRow = QtWidgets.QHBoxLayout()
        buttonSelectAll = QtWidgets.QPushButton("Select all")
        buttonSelectNone = QtWidgets.QPushButton("Select none")
        buttonRow.addWidget(buttonSelectAll); buttonRow.addWidget(buttonSelectNone)
        buttonRow.addStretch(); mainLayout.addLayout(buttonRow)
        self.checkBoxList = []
        for index, (columnName, _, _) in enumerate(ALL_REFERENCE_FIELDS):
            checkBox = QtWidgets.QCheckBox(columnHeaders[index] if columnHeaders else columnName)
            checkBox.setChecked(index in selectedIndices)
            self.checkBoxList.append(checkBox); mainLayout.addWidget(checkBox)
        buttonSelectAll.clicked.connect(lambda: [eachCheckBox.setChecked(True) for eachCheckBox in self.checkBoxList])
        buttonSelectNone.clicked.connect(lambda: [eachCheckBox.setChecked(False) for eachCheckBox in self.checkBoxList])
        mainLayout.addSpacing(8)
        okCancelRow = QtWidgets.QHBoxLayout()
        buttonOk = QtWidgets.QPushButton("OK"); buttonOk.setDefault(True)
        buttonCancel = QtWidgets.QPushButton("Cancel")
        okCancelRow.addStretch(); okCancelRow.addWidget(buttonOk); okCancelRow.addWidget(buttonCancel)
        mainLayout.addLayout(okCancelRow)
        buttonOk.clicked.connect(self.accept); buttonCancel.clicked.connect(self.reject)
    def GetSelectedIndices(self):
        return [index for index, checkBox in enumerate(self.checkBoxList) if checkBox.isChecked()]


_IMAGE_PARAMETER_EXPORTER_ABOUT_HTML = """
<h2>Image Parameter Exporter</h2>
<p>Version 1.0.0 (2026-11-18)</p>
<p><b>Author:</b> Geert J. Verhoeven (ORCID: <a href="https://orcid.org/0000-0003-4825-9604">0000-0003-4825-9604</a>)<br>
<b>Contact:</b> <a href="mailto:geert.verhoeven@univie.ac.at">geert.verhoeven@univie.ac.at</a><br>
<b>Licence:</b> GNU General Public License, version 3 or any later version (GPL-3.0-or-later),
with an additional permission to convey MetaFOCUS combined with Agisoft Metashape and its
Python module (see LICENSE and ADDITIONAL_PERMISSION.txt in the MetaFOCUS ZIP file)</p>
<hr>
<h3>Overview</h3>
<p>This tool exports per-image parameters to one CSV file, one row per image. Select which
categories to include, choose a surface mode for the ground sampling distance (GSD), and export. The
rows cover every master image of the active chunk, oriented or not, enabled or disabled: the slave
images of a multi-camera rig and the keyframes of an animation track are left out. Cells that do not
apply to an image, for example the GSD of an image that is not oriented, stay empty. The Console
pane names the chunk, the CSV file, and the settings of every export and then the number of exported
images, or the failure or the cancellation.</p>
<h3>Export options</h3>
<p>The following categories can be included:</p>
<ul>
<li><b>Pixel count:</b> the numbers of pixel columns and rows.</li>
<li><b>Exif metadata:</b> click &lsquo;Choose fields&rsquo; to select which Exif tags to export.
Values are normalised (rational fractions converted to decimals), and a tag that an image does not
carry leaves its cell empty.</li>
<li><b>Orientation status:</b> whether the image has an exterior orientation (Yes or No), that is,
whether it is oriented. The structure from motion (SfM) procedure usually estimated it, together
with the interior orientation unless that was fixed, but it can also have been imported. Metashape
calls running the SfM procedure aligning images (Align Photos) and an oriented image aligned.</li>
<li><b>Principal distance:</b> the calibrated principal distance c in mm, computed as calibration.f
&times; pixel_size.y. Here, Metashape&rsquo;s calibration attribute f holds the principal distance
in pixels c<sub>pix</sub>, and pixel_size is the photosite pitch p for an image that keeps the
sampling of the sensor, so that c = c<sub>pix</sub> &times; p. In Metashape&rsquo;s frame camera model (Appendix D of its manual), f scales the
y image coordinate exactly (v = h&middot;0.5 + cy + y&prime;f). The x coordinate, by contrast, is
scaled by f + b1. The pitch along the columns therefore gives c also when the two pitches differ.
For a binned or resampled image, c is correct only if pixel_size gives the distance on the sensor
that 1 px of that image represents. The principal distance is <i>not</i> the lens focal length.</li>
<li><b>Interior orientation group:</b> label of the interior orientation group (called &lsquo;sensor&rsquo;
in Metashape). When another group of the chunk has the same label, for example after the lens was
refocused, its key follows, as in &ldquo;NIKON D7100 (11 mm) (key 2)&rdquo;.</li>
<li><b>Image collection:</b> label of the image collection (called &lsquo;image group&rsquo; in
Metashape, &lsquo;camera group&rsquo; in its Python application programming interface, or API).</li>
<li><b>2D tie-point count (Projections):</b> number of 2D tie points of valid tie points in the
image, matching Metashape&rsquo;s &lsquo;Projections&rsquo; column.</li>
<li><b>Matched keypoint count:</b> number of all matched keypoints of the image in the tie-point
data of Metashape (its projections), including those of tracks without a valid 3D tie point. It is
not the number of keypoints detected in the image, which can be larger.</li>
<li><b>Reprojection error:</b> root mean square (RMS) and maximum
reprojection error of the 2D tie points of valid tie points in the
image, in pixels, computed with <code>camera.error()</code>.  A
residual that is not a finite number is left out and counted in the
Console, so that the RMS, the maximum, and the count describe the
same residuals.</li>
<li><b>Reference panel data:</b> click &lsquo;Choose fields&rsquo; to select which Reference panel
columns to export. These are reference coordinates, the values of the &lsquo;Accuracy&rsquo; columns
(read as a priori standard deviations), estimated positions, errors, standard deviations, and
rotation angles. They are shown in Metashape&rsquo;s Reference panel and extracted with <code>chunk.exportReference()</code>.
The rotation columns follow the rotation angle convention of the chunk (for example yaw, pitch, and
roll, or omega, phi, and kappa). The reference coordinates and the estimated positions are in the
coordinate reference system (CRS) of the reference data of the images, which can differ from the
CRS of the chunk. The column Coordinate_System gives the name of that CRS. The column headers give
the units of the coordinates: degrees for the longitude and latitude of a geographic CRS, and
otherwise the unit of the CRS, for example ftUS for the US survey foot. The headers of the
&lsquo;Accuracy&rsquo; values give the units in which Metashape&rsquo;s export writes them, metres in
a geographic CRS. The errors are in metres, also in a CRS in feet. The column Error_(m), the root
sum of squares of the X, Y, and Z errors,
has six decimals. It gets more where these give fewer than four significant digits, but no more than
the errors have as Metashape writes them. The field names in &lsquo;Choose fields&rsquo; are the
column headers of the CSV. In a check in Metashape 2.3.2, Metashape&rsquo;s export named the row of
an image labelled plane_00 by the file name of its photo, plane_00.png. The tool therefore finds the
row by the file name of the photo, and otherwise by the label, with or without a file extension.
Images whose photos share a file name get empty Reference panel cells, because their rows cannot be
told apart (the Console names those file names). When the Reference panel data cannot be exported,
the export stops with a message: untick the option to export the other columns.</li>
</ul>
<p>The option <i>Delimiter and decimal separator</i> chooses a comma with
a decimal point (1.25), a semicolon with a decimal comma (1,25), or a tab
with a decimal point.  The semicolon with the decimal comma suits Excel
with regional settings that use the decimal comma, for example German
or French.  The header row and text cells, such as image labels and
file paths, keep their text unchanged.</p>
<h3>GSD computation</h3>
<p>The GSD is the sampling interval of the image projected onto the surface, the photosite pitch
projected onto the surface for an image that keeps the sampling of the sensor. It is computed with
the dense auxiliary-ray method. The method uses a regular grid of pixel positions centred in the
image (pixel sampling step 5 to 500&nbsp;px, as in the other two GSD tools). At each pixel position,
four auxiliary rays at &plusmn;&frac12; px along the row and the column are cast through <code>camera.unproject()</code>.
For an image that keeps the sampling of the sensor, these rays pass through the midpoints of the
edges of the corresponding photosite. The rays are refined where their projection misses the pixel
position (see &lsquo;Rolling shutter&rsquo;) and intersected with the local tangent plane at the
central ray&rsquo;s surface hit. The two central differences are the projected row and column steps.</p>
<p>The value of that position entering the statistics is the area-equivalent GSD, the side of the
square with the area that the projected row and column steps span. The area-equivalent GSD is
&radic;(GSD<sub>row</sub>&nbsp;&middot;&nbsp;GSD<sub>column</sub>&nbsp;&middot;&nbsp;sin&nbsp;&phi;),
with &phi; the angle between the two steps. It equals the geometric mean of the row and column
ground sampling distances when the steps are perpendicular, and the geometric mean exceeds it by the
factor 1/&radic;(sin&nbsp;&phi;) otherwise. Unlike the geometric mean, it depends only on the area
that the two steps span, not on their directions. To first order, the value of a surface point
therefore does not change when the camera is rolled about its camera axis. This holds provided that
the camera model, apart from its affinity and non-orthogonality, is symmetric about that axis. The
camera model of a frame or fisheye camera is symmetric in this sense with the decentring distortion
coefficients set to zero, without additional corrections, and without rolling-shutter compensation.
Rays meeting the surface at an incidence angle above about 84&deg; are rejected.</p>
<p>The GSD requires a chunk with a physical scale. The tool takes the scale of the chunk transform
to be metric: Metashape derives it from Metashape scale bars or from reference coordinates of
markers or images, in a geographic, projected, or local CRS. An imported chunk transform is taken to
be metric as well. Any other chunk transform, for example one that Align Coordinate Axes to Region
sets in a chunk without scale, has an arbitrary scale. Region Tools records the transform that it
gives such a chunk in the chunk metadata, and as long as the chunk transform is unchanged the tool
exports no GSD and no image scale number. A transform with an arbitrary scale that is set otherwise,
by a script for example, cannot be recognised, and the values are then not in metres.</p>
<p><b>Surface mode</b> determines the surface that the rays are intersected with:</p>
<ul>
<li><b>Plane (tie points):</b> rays intersect a least-squares plane fitted through the valid 3D tie
points of the image (at least three that are not collinear). The plane is its own tangent plane, so
the rays meet it exactly. The GSD is, as in the other modes, the side of the square with the area
that the central differences span. For an image that keeps the sampling of the sensor, this area
equals, to first order, the area of the ground-projected instantaneous field of view (GIFOV). Plane
mode warns about an image whose tie points lie far from one plane, as when an image sees a wall and
the ground. It warns when their root mean square distance from the plane exceeds 5&nbsp;% of their
median distance from the perspective centre. The Console names each such image, and the final
message counts them.</li>
<li><b>Triangular mesh:</b> rays intersect the triangular mesh by ray
tracing through a bounding volume hierarchy (BVH).  The tangent plane
uses 3-ring averaged vertex normals interpolated at each hit
point.  Triangular mesh mode is preselected when the chunk has a triangular
mesh.  A mesh that Metashape builds in the chunk has the identity matrix
as its transform and no CRS of its own, so MetaFOCUS reads its vertices
as internal coordinates of the chunk.  A mesh can also have a transform
and a CRS of its own.  MetaFOCUS then
converts its vertices to the internal coordinates of the chunk through
its CRS and the chunk CRS, and the Console says so.  When a mesh has no
CRS of its own, MetaFOCUS applies its transform to obtain the internal
coordinates of the chunk, and the Console says so when that transform
is not the identity matrix.</li>
<li><b>Raster DEM:</b> rays meet the raster digital elevation model (DEM) of the chunk. In a
georeferenced chunk, they meet it in a local east-north-up CRS in metres with its origin at the
perspective centre, and otherwise in the world coordinates of the chunk. Each ray is followed in
steps of at most one raster DEM cell, measured horizontally, and after each step MetaFOCUS checks
whether the ray has passed below the raster DEM surface. Within the first step that does, the
crossing is located by repeated linear interpolation until the heights of ray and raster DEM agree
to within one hundredth of the cell size. A crossing over which the ray stays below the surface for
at least one cell horizontally is therefore never passed, but a crest that the ray only clips can
be. A hit beyond cells without data is the first crossing found on the raster DEM data, because a
gap is no evidence of a free view. The tangent plane follows from the raster DEM gradient. This mode
is fast (no BVH needed). The Console counts the missed rays by reason. It counts those with the
perspective centre below the raster DEM surface and those with a crossing where the raster DEM has
no data. It also counts those without a crossing within the raster DEM extent and those without a
crossing within the step limit of the search. The Console also notes an image whose perspective
centre lies below the raster DEM surface, so that every ray of the image is a miss.</li>
</ul>
<p>The reported statistics are the minimum, 2nd percentile, mean, median, 98th percentile, and
maximum, in metres with eight decimals, and the coverage (the percentage of grid points that give a
valid GSD). A GSD with fewer than four significant digits in eight decimals gets more decimals, so
that a GSD of 0.7512&nbsp;&micro;m reads 0.0000007512, not 0.00000075. For the same image, surface
mode, and pixel step, they equal the statistics of the GSD Value Exporter and the Image Footprint
Generator, which calls the mean &lsquo;average&rsquo;.</p>
<h3>Rolling shutter</h3>
<p>For an image without rolling-shutter compensation, the rays start at the perspective centre of
the image (Metashape&rsquo;s Camera.center) and pass through the full calibrated camera model, lens
distortion included, as for a central projection. For such an image, MetaFOCUS takes the ray of
Camera.unproject when its projection (Camera.project) lies within 10<sup>&minus;6</sup> px of the
pixel position, and otherwise refines it by Newton&rsquo;s method. Like the Interior Orientation
Viewer, MetaFOCUS refines the ray because Metashape&rsquo;s unprojection can stop before it has
converged (up to 0.041 px for a DJI M4E calibration in Metashape 2.3.2). A pixel position whose ray
cannot be brought within 0.001 px has no ray. For each image, the Console says for how many pixel
positions the ray of Metashape&rsquo;s unprojection projected back more than 0.001 px from the pixel
position, so that MetaFOCUS refined it. It also says how many pixel positions have no ray.</p>
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
readout can be missed. When the GSD or the image scale numbers are exported, the Console block of
the export names the images with rolling-shutter compensation. The reprojection errors come from
Camera.error as well.</p>

<h3>Image scale number</h3>
<p>The image scale number is m = area-equivalent GSD / Metashape&rsquo;s pixel size of the interior
orientation group, reported for each GSD statistic (minimum to maximum). This pixel size is the
photosite pitch for an image that keeps the sampling of the sensor. For a binned or resampled image,
this quotient is the image scale number only if Metashape&rsquo;s pixel size gives the distance on
the sensor that 1 px of that image represents. When the photosite pitches along the rows and the
columns differ (pixel_size.x and pixel_size.y), the divisor is their geometric mean. As a result, m
is the geometric mean of the image scale numbers along the rows and the columns when the projected
row and column steps are perpendicular. In general m<sup>2</sup> is the area that the projected
steps span divided by p<sub>x</sub>&nbsp;&middot;&nbsp;p<sub>y</sub>, the area that the photosite
pitches along the rows and the columns span on the sensor. The photosite pitch and the GSD are both
often, and ambiguously, called &lsquo;pixel size&rsquo;. Strictly speaking, however, a pixel is an
element of the image, not of the sensor, and has no size (Verhoeven, 2018a, 2018b). The image scale
number is a local value: an oblique or distorted image has no single image scale. A value of 1449
means that the image scale is 1:1449. Below 1,000 the value has four significant digits, so that a
macro image with a magnification of 5 gives 0.2000.</p>
"""

class AboutDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("About: Image Parameter Exporter")
        self.setWindowFlags(self.windowFlags() & ~QtCore.Qt.WindowContextHelpButtonHint)
        self.setMinimumSize(520, 500)
        mainLayout = QtWidgets.QVBoxLayout(self)
        textBrowser = QtWidgets.QTextBrowser()
        textBrowser.setOpenExternalLinks(True); textBrowser.setHtml(_IMAGE_PARAMETER_EXPORTER_ABOUT_HTML)
        mainLayout.addWidget(textBrowser)
        buttonRow = QtWidgets.QHBoxLayout()
        buttonClose = QtWidgets.QPushButton("Close"); buttonClose.clicked.connect(self.accept)
        buttonRow.addStretch(); buttonRow.addWidget(buttonClose); mainLayout.addLayout(buttonRow)


def ExportStartConsoleLines(chunkLabel, outputFilePath):
    """The lines that open the Console block of an export: the chunk and
    the CSV file.  The notes of the export on the raster DEM follow."""
    return ConsoleBlockStart("Image Parameter Exporter") + [
        ConsoleLabelledLine("Chunk:", chunkLabel), ConsoleLabelledLine("Output:", outputFilePath)]


# The column groups of an export as the Console names them, in the order
# of the CSV columns, with the option of ExportImageParametersCsv that
# includes each.
EXPORT_COLUMN_GROUP_NAMES = (
    ("includePixelCount", "pixel count"),
    ("includeOrientation", "orientation status"),
    ("includePrincipalDistance", "principal distance"),
    ("includeInteriorOrientationGroup", "interior orientation group"),
    ("includeImageCollection", "image collection"),
    ("includeTiePointCount", "2D tie-point count"),
    ("includeMatchedKeypointCount", "matched keypoint count"),
    ("includeReprojectionError", "reprojection error"),
    ("includeLocationUncertainty", "location uncertainty"),
)
GSD_MODE_NAMES = {GSD_MODE_PLANE: "plane", GSD_MODE_MESH: "triangular mesh", GSD_MODE_DEM: "raster DEM"}


def ExportSettingsConsoleLines(exportOptions, cameras):
    """The lines of the Console block of an export that give its settings:
    the column groups (*exportOptions*, the options of
    ExportImageParametersCsv), the surface mode and pixel step of the GSD,
    the CSV format, and, when the GSD or the image scale numbers are
    exported, the images among *cameras* (the images of the chunk) with
    rolling-shutter compensation (RollingShutterConsoleLines)."""
    columnGroups = []
    if exportOptions.get("selectedExifIndices"):
        columnGroups.append("Exif metadata ({})".format(
            FormatCountWithNoun(len(exportOptions["selectedExifIndices"]), "field", "fields")))
    columnGroups.extend(name for option, name in EXPORT_COLUMN_GROUP_NAMES if exportOptions.get(option))
    if exportOptions.get("selectedReferenceIndices"):
        columnGroups.append("reference panel data ({})".format(
            FormatCountWithNoun(len(exportOptions["selectedReferenceIndices"]), "field", "fields")))
    if exportOptions.get("includeGsd"):
        columnGroups.append("GSD statistics")
    if exportOptions.get("includeImageScaleNumber"):
        columnGroups.append("image scale numbers")
    lines = [ConsoleLabelledLine("Columns:", ", ".join(columnGroups))]
    needsGsd = exportOptions.get("includeGsd") or exportOptions.get("includeImageScaleNumber")
    if needsGsd:
        lines.append(ConsoleLabelledLine("GSD:", "{} mode, pixel step {} px".format(
            GSD_MODE_NAMES.get(exportOptions.get("gsdMode"), exportOptions.get("gsdMode")),
            exportOptions.get("gsdPixelStep"))))
    lines.append(ConsoleLabelledLine("CSV format:", CsvFormatName(
        exportOptions.get("csvDelimiter", ","), exportOptions.get("decimalSeparator", "."))))
    if needsGsd:
        lines.extend(RollingShutterConsoleLines(
            [camera for camera in cameras if IsMasterImage(camera) and getattr(camera, "transform", None) is not None]))
    return lines


def ExportEndConsoleLines(exportedCount=None, failure=None, cancelled=False):
    """The lines that close the Console block of an export: the images
    exported, the failure, or the cancellation."""
    if cancelled:
        lines = ["Export cancelled."]
    elif failure is not None:
        lines = ["Export failed: {}".format(failure)]
    else:
        lines = [ConsoleLabelledLine("Exported:", FormatCountWithNoun(exportedCount, "image", "images"))]
    return lines + ConsoleBlockEnd()


def _ReportExportProgress(exportCancelled, masterCameraCount, progressDialog, currentIndex, totalCount):
    if currentIndex == -1:
        progressDialog.setLabelText("Building the triangular mesh index...")
        progressDialog.setMaximum(0); QtWidgets.QApplication.processEvents(); return
    if progressDialog.maximum() != masterCameraCount:
        progressDialog.setMaximum(masterCameraCount); progressDialog.setLabelText("Exporting...")
    if progressDialog.wasCanceled(): exportCancelled[0] = True; raise RuntimeError("Cancelled.")
    progressDialog.setValue(currentIndex); QtWidgets.QApplication.processEvents()


class ImageParameterExporterDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Image Parameter Exporter")
        self.setWindowFlags(self.windowFlags() & ~QtCore.Qt.WindowContextHelpButtonHint)
        self.setMinimumWidth(600)
        self._exifFieldIndices = list(range(len(ALL_EXIF_FIELDS)))
        self._BuildUserInterface()

    def _BuildUserInterface(self):
        mainLayout = QtWidgets.QVBoxLayout(self)
        activeChunk = Metashape.app.document.chunk

        if activeChunk:
            imageCount, orientedImageCount = CountMasterImages(activeChunk)
            scalingDescription = DescribeScaling(activeChunk)
            if ChunkHasTriangularMesh(activeChunk): meshAvailabilityText = "Yes"
            else: meshAvailabilityText = "No"
            if activeChunk.elevation is not None:
                demResolution = activeChunk.elevation.resolution
                rasterDemAvailabilityText = "Yes (cell size {:.4g} m)".format(demResolution) if demResolution else "Yes"
            else:
                rasterDemAvailabilityText = "No"
            chunkSummaryText = ("Active chunk: <b>{}</b><br>Images: {:,} total, {:,} oriented<br>"
                        "Scaling: {}<br>Triangular mesh: {}<br>"
                        "Raster DEM: {}").format(
                activeChunk.label or "(unnamed)", imageCount, orientedImageCount,
                scalingDescription, meshAvailabilityText, rasterDemAvailabilityText)
        else: chunkSummaryText = "<i>No active chunk.</i>"

        chunkSummaryLabel = QtWidgets.QLabel(chunkSummaryText)
        chunkSummaryLabel.setTextFormat(QtCore.Qt.RichText); chunkSummaryLabel.setWordWrap(True)
        mainLayout.addWidget(chunkSummaryLabel); mainLayout.addSpacing(8)

        optionsGroup = QtWidgets.QGroupBox("Export options")
        optionsLayout = QtWidgets.QVBoxLayout(optionsGroup)

        selectRow = QtWidgets.QHBoxLayout()
        buttonSelectAll = QtWidgets.QPushButton("Select all")
        buttonSelectNone = QtWidgets.QPushButton("Select none")
        selectRow.addWidget(buttonSelectAll); selectRow.addWidget(buttonSelectNone)
        selectRow.addStretch(); optionsLayout.addLayout(selectRow)

        # --- Option check boxes ---
        self.checkBoxPixelCount = QtWidgets.QCheckBox("Pixel count"); self.checkBoxPixelCount.setChecked(True)
        optionsLayout.addWidget(self.checkBoxPixelCount)

        exifRow = QtWidgets.QHBoxLayout()
        self.checkBoxExif = QtWidgets.QCheckBox("Exif metadata"); self.checkBoxExif.setChecked(True)
        self.buttonExifChooseFields = QtWidgets.QPushButton("Choose fields"); self.buttonExifChooseFields.setFixedWidth(120)
        exifRow.addWidget(self.checkBoxExif); exifRow.addWidget(self.buttonExifChooseFields)
        exifRow.addStretch(); optionsLayout.addLayout(exifRow)
        self.buttonExifChooseFields.clicked.connect(self._OnChooseExifFields)
        self.checkBoxExif.toggled.connect(self.buttonExifChooseFields.setEnabled)

        self.checkBoxOrientation = QtWidgets.QCheckBox("Orientation status"); self.checkBoxOrientation.setChecked(True)
        optionsLayout.addWidget(self.checkBoxOrientation)

        self.checkBoxPrincipalDistance = QtWidgets.QCheckBox("Principal distance (mm)")
        optionsLayout.addWidget(self.checkBoxPrincipalDistance)

        self.checkBoxInteriorOrientationGroup = QtWidgets.QCheckBox("Interior orientation group")
        optionsLayout.addWidget(self.checkBoxInteriorOrientationGroup)

        self.checkBoxImageCollection = QtWidgets.QCheckBox("Image collection")
        optionsLayout.addWidget(self.checkBoxImageCollection)

        self.checkBoxTiePointCount = QtWidgets.QCheckBox("2D tie-point count (Projections)"); self.checkBoxTiePointCount.setChecked(True)
        optionsLayout.addWidget(self.checkBoxTiePointCount)

        self.checkBoxMatchedKeypointCount = QtWidgets.QCheckBox("Matched keypoint count")
        optionsLayout.addWidget(self.checkBoxMatchedKeypointCount)

        self.checkBoxReprojectionError = QtWidgets.QCheckBox("Reprojection error (RMS and maximum, px)"); self.checkBoxReprojectionError.setChecked(True)
        optionsLayout.addWidget(self.checkBoxReprojectionError)

        referenceRow = QtWidgets.QHBoxLayout()
        self.checkBoxReferenceData = QtWidgets.QCheckBox("Reference panel data")
        self.buttonReferenceChooseFields = QtWidgets.QPushButton("Choose fields"); self.buttonReferenceChooseFields.setFixedWidth(120)
        referenceRow.addWidget(self.checkBoxReferenceData); referenceRow.addWidget(self.buttonReferenceChooseFields)
        referenceRow.addStretch(); optionsLayout.addLayout(referenceRow)
        self.buttonReferenceChooseFields.clicked.connect(self._OnChooseReferenceFields)
        self.checkBoxReferenceData.toggled.connect(self.buttonReferenceChooseFields.setEnabled)
        self.buttonReferenceChooseFields.setEnabled(False)
        # Default: coordinates, total errors, orientation, per-axis errors, CRS.
        self._referenceFieldIndices = [0, 1, 2, 4, 5, 6, 7, 9, 10, 11, 12, 13, 14, 15, 28]

        self.checkBoxGsd = QtWidgets.QCheckBox("Per-image GSD (minimum, 2nd percentile, mean, median, 98th percentile, maximum)"); self.checkBoxGsd.setChecked(True)
        optionsLayout.addWidget(self.checkBoxGsd)

        self.checkBoxImageScaleNumber = QtWidgets.QCheckBox("Image scale number m (image scale 1:m, from the GSD and the photosite pitch)")
        optionsLayout.addWidget(self.checkBoxImageScaleNumber)

        if activeChunk and not IsChunkScalePhysical(activeChunk):
            self.checkBoxGsd.setChecked(False); self.checkBoxImageScaleNumber.setChecked(False)

        self._allOptionCheckBoxes = [
            self.checkBoxPixelCount, self.checkBoxExif,
            self.checkBoxOrientation,
            self.checkBoxPrincipalDistance,
            self.checkBoxInteriorOrientationGroup, self.checkBoxImageCollection,
            self.checkBoxTiePointCount, self.checkBoxMatchedKeypointCount,
            self.checkBoxReprojectionError, self.checkBoxReferenceData,
            self.checkBoxGsd, self.checkBoxImageScaleNumber]

        buttonSelectAll.clicked.connect(lambda: [checkBox.setChecked(True) for checkBox in self._allOptionCheckBoxes])
        buttonSelectNone.clicked.connect(lambda: [checkBox.setChecked(False) for checkBox in self._allOptionCheckBoxes])

        gsdModeRow = QtWidgets.QHBoxLayout(); gsdModeRow.addSpacing(20)
        self.gsdModeLabel = QtWidgets.QLabel("Surface mode:")
        self.radioButtonGsdPlane = QtWidgets.QRadioButton("Plane (tie points)")
        self.radioButtonGsdMesh = QtWidgets.QRadioButton("Triangular mesh")
        # Triangular mesh mode is the default when the chunk has a mesh,
        # because a plane stands for the surface only when the scene is
        # planar (see PLANE_SPREAD_WARNING_SHARE).
        (self.radioButtonGsdMesh if ChunkHasTriangularMesh(activeChunk)
         else self.radioButtonGsdPlane).setChecked(True)
        self.radioButtonGsdDem = QtWidgets.QRadioButton("Raster DEM")
        if not ChunkHasTriangularMesh(activeChunk): self.radioButtonGsdMesh.setEnabled(False)
        if not activeChunk or not activeChunk.elevation: self.radioButtonGsdDem.setEnabled(False)
        gsdModeRow.addWidget(self.gsdModeLabel); gsdModeRow.addWidget(self.radioButtonGsdPlane)
        gsdModeRow.addWidget(self.radioButtonGsdMesh); gsdModeRow.addWidget(self.radioButtonGsdDem)
        gsdModeRow.addStretch()
        optionsLayout.addLayout(gsdModeRow)

        self.gsdModeButtonGroup = QtWidgets.QButtonGroup(self)
        self.gsdModeButtonGroup.addButton(self.radioButtonGsdPlane)
        self.gsdModeButtonGroup.addButton(self.radioButtonGsdMesh)
        self.gsdModeButtonGroup.addButton(self.radioButtonGsdDem)

        gsdStepRow = QtWidgets.QHBoxLayout(); gsdStepRow.addSpacing(20)
        self.gsdStepLabel = QtWidgets.QLabel("GSD pixel sampling step:")
        self.gsdStepSpinBox = QtWidgets.QSpinBox(); self.gsdStepSpinBox.setRange(5, 500)
        self.gsdStepSpinBox.setValue(50); self.gsdStepSpinBox.setSuffix(" px")
        gsdStepRow.addWidget(self.gsdStepLabel); gsdStepRow.addWidget(self.gsdStepSpinBox)
        gsdStepRow.addStretch(); optionsLayout.addLayout(gsdStepRow)

        self.checkBoxGsd.toggled.connect(self._OnGsdToggled)
        self.checkBoxImageScaleNumber.toggled.connect(self._OnGsdToggled)
        self._OnGsdToggled()

        mainLayout.addWidget(optionsGroup); mainLayout.addSpacing(4)

        noteLabel = QtWidgets.QLabel(
            "Tangent plane at each sampled pixel position. Plane mode: the fitted plane "
            "itself. Triangular mesh mode: 3-ring averaged vertex normals. Raster DEM "
            "mode: the raster DEM gradient. Rays beyond about 84\u00b0 "
            "incidence are rejected.")
        noteLabel.setWordWrap(True); noteLabel.setStyleSheet("font-size: 9pt; color: #666;")
        mainLayout.addWidget(noteLabel); mainLayout.addSpacing(8)

        # Delimiter and decimal separator.
        delimiterRow = QtWidgets.QHBoxLayout()
        delimiterRow.addWidget(QtWidgets.QLabel(CSV_FORMAT_ROW_LABEL_TEXT))
        self.delimiterComboBox = QtWidgets.QComboBox()
        FillCsvFormatComboBox(self.delimiterComboBox, _CSV_FORMAT_TOOLTIP_TEXT)
        delimiterRow.addWidget(self.delimiterComboBox)
        delimiterRow.addStretch()
        mainLayout.addLayout(delimiterRow)
        mainLayout.addSpacing(4)

        buttonRow = QtWidgets.QHBoxLayout()
        buttonAbout = QtWidgets.QPushButton("About"); buttonAbout.clicked.connect(self._OnShowAbout)
        buttonExport = QtWidgets.QPushButton("Export CSV"); buttonExport.setDefault(True)
        buttonClose = QtWidgets.QPushButton("Close")
        buttonRow.addWidget(buttonAbout); buttonRow.addStretch()
        buttonRow.addWidget(buttonExport); buttonRow.addWidget(buttonClose)
        mainLayout.addLayout(buttonRow)
        buttonExport.clicked.connect(self._OnExport); buttonClose.clicked.connect(self.reject)

    def _OnGsdToggled(self, _=None):
        gsdEnabled = self.checkBoxGsd.isChecked() or self.checkBoxImageScaleNumber.isChecked()
        for widget in (self.gsdModeLabel, self.radioButtonGsdPlane, self.gsdStepLabel, self.gsdStepSpinBox):
            widget.setEnabled(gsdEnabled)
        activeChunk = Metashape.app.document.chunk
        hasMesh = ChunkHasTriangularMesh(activeChunk)
        self.radioButtonGsdMesh.setEnabled(gsdEnabled and hasMesh)
        hasDem = activeChunk is not None and activeChunk.elevation is not None
        self.radioButtonGsdDem.setEnabled(gsdEnabled and hasDem)

    def _OnShowAbout(self):
        AboutDialog(self).exec_()

    def _OnChooseExifFields(self):
        dialog = ExifSelectorDialog(self._exifFieldIndices, self)
        if dialog.exec_() == QtWidgets.QDialog.Accepted:
            self._exifFieldIndices = dialog.GetSelectedIndices()

    def _OnChooseReferenceFields(self):
        activeChunk = Metashape.app.document.chunk
        columnHeaders = [ReferenceColumnHeader(fieldIndex, CameraReferenceCrs(activeChunk),
                                               RotationAngleNames(activeChunk))
                         for fieldIndex in range(len(ALL_REFERENCE_FIELDS))]
        dialog = ReferenceSelectorDialog(self._referenceFieldIndices, self, columnHeaders=columnHeaders)
        if dialog.exec_() == QtWidgets.QDialog.Accepted:
            self._referenceFieldIndices = dialog.GetSelectedIndices()

    def _OnExport(self):
        activeChunk = Metashape.app.document.chunk
        if not activeChunk: QtWidgets.QMessageBox.warning(self, "Image Parameter Exporter", "No active chunk."); return
        if self.radioButtonGsdMesh.isChecked(): gsdMode = GSD_MODE_MESH
        elif self.radioButtonGsdDem.isChecked(): gsdMode = GSD_MODE_DEM
        else: gsdMode = GSD_MODE_PLANE
        csvDelimiter, decimalSeparator = ChosenCsvFormat(self.delimiterComboBox)
        exportOptions = dict(
            selectedExifIndices=self._exifFieldIndices if self.checkBoxExif.isChecked() else [],
            includePixelCount=self.checkBoxPixelCount.isChecked(),
            includePrincipalDistance=self.checkBoxPrincipalDistance.isChecked(),
            includeInteriorOrientationGroup=self.checkBoxInteriorOrientationGroup.isChecked(),
            includeImageCollection=self.checkBoxImageCollection.isChecked(),
            includeOrientation=self.checkBoxOrientation.isChecked(),
            includeTiePointCount=self.checkBoxTiePointCount.isChecked(),
            includeMatchedKeypointCount=self.checkBoxMatchedKeypointCount.isChecked(),
            includeReprojectionError=self.checkBoxReprojectionError.isChecked(),
            # The location uncertainty columns are not offered in the
            # dialog: ExportImageParametersCsv writes them only when
            # called from code with includeLocationUncertainty=True.
            includeLocationUncertainty=False,
            selectedReferenceIndices=self._referenceFieldIndices if self.checkBoxReferenceData.isChecked() else [],
            includeGsd=self.checkBoxGsd.isChecked(),
            includeImageScaleNumber=self.checkBoxImageScaleNumber.isChecked(),
            gsdMode=gsdMode, gsdPixelStep=self.gsdStepSpinBox.value(),
            csvDelimiter=csvDelimiter, decimalSeparator=decimalSeparator)
        hasAnything = (exportOptions["selectedExifIndices"]
                       or exportOptions["selectedReferenceIndices"]
                       or any(value for key, value in exportOptions.items() if key.startswith("include")))
        if not hasAnything:
            QtWidgets.QMessageBox.information(self, "Image Parameter Exporter", "Select at least one option."); return
        document = Metashape.app.document
        defaultDirectory = os.path.dirname(document.path) if document.path else ""
        defaultFilename = _BuildDefaultCsvFilename(document, activeChunk)
        outputFilePath = Metashape.app.getSaveFileName("Save CSV",
            dir=os.path.join(defaultDirectory, defaultFilename), filter="CSV (*.csv)")
        if not outputFilePath: return
        if not outputFilePath.lower().endswith(".csv"): outputFilePath += ".csv"
        masterCameraCount = sum(1 for camera in activeChunk.cameras if IsMasterImage(camera))
        progressDialog = QtWidgets.QProgressDialog("Exporting...", "Cancel", 0, masterCameraCount, self)
        progressDialog.setWindowTitle("Image Parameter Exporter"); progressDialog.setWindowModality(QtCore.Qt.WindowModal)
        progressDialog.setMinimumDuration(0); exportCancelled = [False]
        reportExportProgress = functools.partial(_ReportExportProgress, exportCancelled, masterCameraCount, progressDialog)
        PrintConsoleLines(ExportStartConsoleLines(activeChunk.label, outputFilePath)
                          + ExportSettingsConsoleLines(exportOptions, activeChunk.cameras))
        planeWarnings = []
        try: exportedCount = ExportImageParametersCsv(activeChunk, outputFilePath, progressCallback=reportExportProgress,
                                                      planeWarnings=planeWarnings, **exportOptions)
        except RuntimeError as error:
            progressDialog.close()
            if exportCancelled[0]:
                PrintConsoleLines(ExportEndConsoleLines(cancelled=True)); return
            PrintConsoleLines(ExportEndConsoleLines(failure=str(error)))
            QtWidgets.QMessageBox.critical(self, "Image Parameter Exporter", str(error)); return
        except Exception as error:
            progressDialog.close()
            PrintConsoleLines(ExportEndConsoleLines(failure=DescribeException(error)))
            QtWidgets.QMessageBox.critical(self, "Image Parameter Exporter", DescribeException(error)); return
        progressDialog.close()
        # Plane mode: the images whose tie points lie far from their plane.
        planeWarningText = PlaneSpreadSummaryText(len(planeWarnings), exportedCount)
        if planeWarningText:
            print(planeWarningText)
        PrintConsoleLines(ExportEndConsoleLines(exportedCount=exportedCount))
        # The final message points to the warning lines of the images.
        QtWidgets.QMessageBox.information(
            self, "Image Parameter Exporter", "Exported {} to:\n{}{}".format(
                FormatCountWithNoun(exportedCount, "image", "images"), outputFilePath,
                "\n\n" + PlaneSpreadSummaryText(len(planeWarnings), exportedCount, pointsToConsole=True)
                if planeWarningText else ""))


_MENU_LABEL = "MetaFOCUS/SfM Process Diagnostics/Image Parameter Exporter"
_ICON_NAME = "image_parameter_exporter_32.png"

def _ShowExporterDialog():
    document = Metashape.app.document
    if not document or not document.chunk:
        QtWidgets.QMessageBox.information(
            QtWidgets.QApplication.activeWindow(), "Image Parameter Exporter",
            "Open a project with at least one chunk first.")
        return
    ImageParameterExporterDialog(QtWidgets.QApplication.activeWindow()).exec_()

def RegisterMenuItem():
    """Register the menu item.  Called by the MetaFOCUS loader."""
    try: Metashape.app.removeMenuItem(_MENU_LABEL)
    except Exception as suppressedError:
        ReportSuppressedError("imageParameterExporter.RegisterMenuItem", suppressedError)
    iconPath = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "icons", _ICON_NAME)
    if os.path.exists(iconPath):
        Metashape.app.addMenuItem(
            _MENU_LABEL, _ShowExporterDialog, icon=iconPath)
    else:
        Metashape.app.addMenuItem(_MENU_LABEL, _ShowExporterDialog)
