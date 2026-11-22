"""
Tie Point Covariance Exporter for Agisoft Metashape Professional
================================================================

Exports the formal 3D position covariance matrix that Metashape
estimates for each tie point, with the standard deviations and the
error ellipsoid derived from it.  The matrices are conditional on the
observations, weights, constraints, and model of the adjustment, and
they are not a complete uncertainty budget.
Coordinates and covariance matrices are transformed with the chunk
transform into the world coordinates of the chunk (geocentric, that is
Earth-centred and Earth-fixed, in a georeferenced chunk) or, optionally
and only in a georeferenced chunk with a physical scale, into local
east-north-up (ENU) coordinates in metres with their origin at the
region centre.

Exportable quantities (each can be switched on or off):
    1. Per-axis standard deviations (sigma_X, sigma_Y, sigma_Z):
       square roots of the diagonal of the transformed covariance
       matrix.

    2. Upper-triangle covariance (C_XX, C_XY, C_XZ, C_YY, C_YZ,
       C_ZZ): the six unique elements of the symmetric 3x3
       covariance matrix after the transformation, written in
       scientific notation.

    3. Error ellipsoid semi-axes and orientation: the three
       eigenvalues of the matrix yield the semi-axis lengths of the 3D
       error ellipsoid.  The corresponding eigenvectors give the
       orientation of each axis in the output coordinates.

All three describe one matrix: every matrix that the check accepts is
replaced by its symmetric part, and a negative eigenvalue that the check
attributes to rounding is set to zero
(DecomposeCovarianceMatrixWithRepair).

Author:      Geert J. Verhoeven (ORCID: 0000-0003-4825-9604)
Version:     1.0.1 (2026-11-22)
Date:        2026-03-24
Licence:     GPL-3.0-or-later WITH GPL-3.0-linking-exception
Contact:     geert.verhoeven@univie.ac.at
Requires:    Agisoft Metashape Professional 2.x+
Developed for: Agisoft Metashape Professional 2.3.2

Dependencies:
    - photogrammetryUtilities.py (in the same MetaFOCUSCore package).

Usage:
    Loaded by the MetaFOCUS loader (MetaFOCUS.py).  Open the dialog
    from MetaFOCUS > SfM Process Diagnostics > Tie Point Covariance
    Exporter.

Notes:
    - The covariance matrix stored by Metashape for each tie point
      (point.cov) is in internal chunk coordinates.  It is propagated
      through the linear part of the chunk transform:
      C_out = R * C_chunk * R^T, where R is the 3x3
      rotation-times-scale component of the chunk transform.
    - The chunk transform includes a scale, the chunk scale.  Since
      R = T.rotation() * T.scale(), the transformed covariance is in
      the square of the world length unit: m^2 in a chunk with a
      physical scale, also in a local CRS whose unit is not the metre
      (only CoordinateSystem.project gives coordinates in that unit),
      and chunk units squared otherwise.  In a non-georeferenced chunk
      the columns are labelled [m] and [m^2], or [chunk units] and
      [chunk units^2] without a physical scale.
    - In a georeferenced chunk (HasGeocentricWorldCoordinates: a
      transform with a physical scale, a translation, and a rotation, not
      from Region Tools) local east-north-up coordinates can
      optionally be applied: coordinates become offsets in metres
      from the region centre, along east, north, and up.  Without them,
      the world coordinates of a georeferenced chunk with a geographic
      or projected CRS are geocentric (Earth-centred, Earth-fixed, ECEF)
      in metres, not coordinates of that CRS,
      because only CoordinateSystem.project turns them into those (as
      in LocalFrameOriginComment).  The columns are labelled [m,
      geocentric ECEF] and a comment line says so.  A chunk with such a
      CRS that is not georeferenced (no reference coordinates, Metashape
      scale bars alone, or a Region Tools transform) has local world
      coordinates,
      labelled [m] or chunk units by its scale, with eigenvectors in
      [world coordinates].
    - Every transformed matrix is checked before it is decomposed: it
      must be finite, symmetric, and positive semi-definite
      (ValidatedCovarianceElements, with signed eigenvalues).  A point
      whose matrix fails is skipped and named in the Console (the
      first twenty), because absolute values of a negative variance,
      or the singular values of an indefinite matrix, give plausible
      but meaningless standard deviations and semi-axes.
    - Every tie point that is not exported (an invalid tie point, no
      covariance, a point at infinity, or an invalid covariance
      matrix) is written with its reason to a companion file next to
      the main file, with the same extension, CSV or TXT
      (CompanionCsvFilePath: _notExported added to the file name), so
      that the main file holds numbers only.  Both files are written to
      temporary files next to them and then replace the two files
      together, or neither (ReplaceFilesTogether), which refuses a
      folder at either path before anything moves.  An existing file
      that the save dialog did not confirm, such as a companion file
      that is not that of an earlier export to the same file, is
      replaced only after a question (FilesToConfirm).
    - The table is written to a CSV file or, when the path chosen in
      the save dialog ends in .txt, to a text file
      (ExportFilePathWithExtension), with the delimiter and the decimal
      separator of the shared choice (CSV_FORMAT_CHOICES): with the
      decimal comma every number of the data rows gets a comma, whereas
      the comment lines, the header, and the reasons keep their text.
"""

import Metashape
import math
import csv
import os
import contextlib
import tempfile

from PySide2 import QtWidgets, QtCore

from .photogrammetryUtilities import ConvertToCamelCase, TransformHomogeneousPoint
from .photogrammetryUtilities import (
    CrsKind, DescribeException, FormatCountWithNoun, IsChunkScalePhysical, IsGeoreferencedChunk, ReportSuppressedError,
    EARTH_BOUND_CRS_KINDS,
    SmallestEigenvalueOfSymmetric3x3, SymmetricEigendecomposition3x3, ValidatedCovarianceElements,
    CSV_FORMAT_ROW_LABEL_TEXT, FillCsvFormatComboBox, ChosenCsvFormat, DataRowWithDecimalSeparator,
    ConsoleLabelledLine, CsvFormatName)


_TOOL_NAME = "Tie Point Covariance Exporter"

# Smallest closed-form eigenvalue, relative to the largest element in
# magnitude, above which a symmetric matrix is certainly positive
# definite: the closed form resolves eigenvalues near a repeated one only
# to about 1e-8 of that element.  A matrix below it goes to the cyclic
# Jacobi method, which decides whether an eigenvalue is negative.
_CLEARLY_POSITIVE_RELATIVE_EIGENVALUE = 1e-6

# Number of tie points with an invalid covariance matrix that the
# Console names one by one (the others are counted).
_LISTED_INVALID_COVARIANCE_COUNT = 20


# ===================================================================
# Coordinate and covariance transformation
# ===================================================================

def HasGeocentricWorldCoordinates(chunk):
    """True when the world coordinates of *chunk* are geocentric
    (Earth-centred, Earth-fixed) in metres: a georeferenced chunk
    (IsGeoreferencedChunk), whose transform has a physical scale, a
    translation, and a rotation that do not come from Region Tools."""
    return IsGeoreferencedChunk(chunk)


def HasArbitraryWorldCoordinates(chunk):
    """True when the chunk names a geographic, projected, or geocentric
    CRS but its world coordinates are not geocentric
    (HasGeocentricWorldCoordinates): local coordinates of a chunk without
    reference coordinates or of a Region Tools transform, whose axes are
    not those of that CRS, so that the eigenvectors are labelled [world
    coordinates] (BuildCsvHeader)."""
    return CrsKind(getattr(chunk, "crs", None)) in EARTH_BOUND_CRS_KINDS and not HasGeocentricWorldCoordinates(chunk)


def BuildCrsTransformMatrices(chunk, applyLocalFrame):
    """Build the 4x4 point transform T and the 3x3 covariance
    rotation matrix R from the active chunk's transform.

    Parameters
    ----------
    chunk : Metashape.Chunk
        The active chunk.
    applyLocalFrame : bool
        If True, apply local east-north-up coordinates at the region
        centre.  They are applied only in a georeferenced chunk
        (geographic, projected, or geocentric CRS) with a physical scale,
        whose world coordinates are geocentric
        (HasGeocentricWorldCoordinates).

    Returns
    -------
    transformMatrix : Metashape.Matrix (4x4)
        Transforms homogeneous internal chunk coordinates to the world
        coordinates of the chunk, or to the local east-north-up
        coordinates (metres, origin at the region centre) when they
        are applied.
    scaledRotation : Metashape.Matrix (3x3)
        Rotation (including scale) for covariance propagation:
        C_out = scaledRotation * C_chunk * scaledRotation^T.
    localFrameApplied : bool
        True when the local east-north-up coordinates were applied.
    """
    transformMatrix = chunk.transform.matrix

    localFrameApplied = bool(applyLocalFrame) and HasGeocentricWorldCoordinates(chunk)
    if localFrameApplied:
        localFrame = chunk.crs.localframe(
            transformMatrix.mulp(chunk.region.center))
        transformMatrix = localFrame * transformMatrix

    # R = rotation * scale, so that R * C_chunk * R^T is in the square
    # of the world length unit (m^2 in a georeferenced chunk).
    scaledRotation = transformMatrix.rotation() * transformMatrix.scale()

    return transformMatrix, scaledRotation, localFrameApplied


def TransformPointCovariance(internalCovariance, scaledRotation):
    """Propagate a 3x3 covariance matrix from internal chunk
    coordinates to the output coordinates (the world coordinates of
    the chunk or the local east-north-up coordinates).

    Parameters
    ----------
    internalCovariance : Metashape.Matrix (3x3)
        Covariance in internal chunk coordinates.
    scaledRotation : Metashape.Matrix (3x3)
        The rotation-times-scale matrix from BuildCrsTransformMatrices.

    Returns
    -------
    transformedCovariance : Metashape.Matrix (3x3)
        Covariance in the output coordinates.
    """
    return scaledRotation * internalCovariance * scaledRotation.t()


def DecomposeCovarianceMatrixWithRepair(transformedCovariance):
    """Per-axis standard deviations, upper-triangle elements, and
    eigendecomposition of a 3x3 covariance matrix in the output
    coordinates,
    and whether the matrix had to be repaired.

    The matrix is checked first (ValidatedCovarianceElements): it must
    be finite, symmetric, and positive semi-definite.  The absolute
    value of a negative variance, or the singular values of an
    indefinite matrix, would give plausible but meaningless standard
    deviations and semi-axes, so such a matrix raises ValueError
    instead.  The check accepts asymmetry and negative eigenvalues
    within COVARIANCE_RELATIVE_TOLERANCE as rounding.  Every matrix is
    replaced by its symmetric part, because R C R^T in floating point is
    seldom exactly symmetric, and that is not counted as a repair.  A
    symmetric part with a negative eigenvalue is repaired: it is
    decomposed with the cyclic Jacobi method
    (SymmetricEigendecomposition3x3), its negative eigenvalues are set
    to zero, and every returned quantity comes from the repaired matrix
    that the remaining eigenvalues and eigenvectors rebuild, so that the
    standard deviations, the upper triangle, and the semi-axes describe
    one covariance matrix.  Whether an eigenvalue is negative is decided
    by the Jacobi method whenever the closed-form smallest eigenvalue is
    not clearly positive (_CLEARLY_POSITIVE_RELATIVE_EIGENVALUE), so that
    neither the unit of the matrix nor the rounding of the closed form
    near a repeated eigenvalue decides it.  The eigendecomposition of a clearly
    positive definite matrix comes from the singular value decomposition
    of its symmetric part, whose singular values and left singular
    vectors are then its eigenvalues and eigenvectors.

    Parameters
    ----------
    transformedCovariance : Metashape.Matrix (3x3)
        Covariance in the output coordinates.

    Returns
    -------
    perAxisSigma : tuple of 3 floats
        (sigma_X, sigma_Y, sigma_Z): square roots of the
        diagonal elements.
    upperTriangle : tuple of 6 floats
        (C_XX, C_XY, C_XZ, C_YY, C_YZ, C_ZZ).
    semiAxisLengths : tuple of 3 floats
        Semi-axis lengths of the error ellipsoid (square roots of
        the eigenvalues), sorted largest first.
    eigenVectors : tuple of 3 tuples
        Each inner tuple is (vx, vy, vz): the eigenvector
        (orientation in the output coordinates) corresponding to each
        eigenvalue, in the same order as semiAxisLengths.
    repaired : bool
        True when the symmetric part had a negative eigenvalue that was
        set to zero, so that the repaired matrix was used.

    Raises
    ------
    ValueError
        When the matrix is not a covariance matrix (the message names
        the problem).
    """
    covarianceElements = ValidatedCovarianceElements(transformedCovariance)
    symmetrisedElements = [[0.5 * (covarianceElements[rowIndex][columnIndex]
                                   + covarianceElements[columnIndex][rowIndex])
                            for columnIndex in range(3)] for rowIndex in range(3)]
    largestMagnitude = max(abs(element) for rowElements in symmetrisedElements for element in rowElements)
    repaired = False
    exportedElements = symmetrisedElements
    if (SmallestEigenvalueOfSymmetric3x3(symmetrisedElements)
            > _CLEARLY_POSITIVE_RELATIVE_EIGENVALUE * largestMagnitude):
        # A positive definite symmetric part: its singular values are its
        # eigenvalues and its left singular vectors its eigenvectors.  A
        # matrix that is exactly symmetric is its own symmetric part and
        # is decomposed as given.  Otherwise a matrix of the same class
        # (Metashape.Matrix) is built from the rows of the symmetric
        # part, because an asymmetry accepted as rounding can lengthen
        # small semi-axes by several per cent when the matrix is
        # decomposed as given.
        if symmetrisedElements == covarianceElements:
            symmetricMatrix = transformedCovariance
        else:
            symmetricMatrix = type(transformedCovariance)(symmetrisedElements)
        leftSingularVectors, singularValues, rightSingularVectorsUnused = symmetricMatrix.svd()
        eigenvaluesAndVectors = [
            (max(float(singularValues[axisIndex]), 0.0),
             tuple(float(leftSingularVectors[rowIndex, axisIndex]) for rowIndex in range(3)))
            for axisIndex in range(3)]
    else:
        eigenpairs = SymmetricEigendecomposition3x3(symmetrisedElements)
        repaired = any(eigenvalue < 0.0 for eigenvalue, eigenvector in eigenpairs)
        eigenvaluesAndVectors = [(max(eigenvalue, 0.0), eigenvector) for eigenvalue, eigenvector in eigenpairs]
        if repaired:
            exportedElements = [[sum(eigenvalue * eigenvector[rowIndex] * eigenvector[columnIndex]
                                     for eigenvalue, eigenvector in eigenvaluesAndVectors)
                                 for columnIndex in range(3)] for rowIndex in range(3)]
            # The rebuilt matrix is symmetric up to rounding: one value
            # for each pair of elements.
            for rowIndex, columnIndex in ((0, 1), (0, 2), (1, 2)):
                exportedElements[columnIndex][rowIndex] = exportedElements[rowIndex][columnIndex]

    # Per-axis standard deviations.
    perAxisSigma = tuple(math.sqrt(max(exportedElements[axisIndex][axisIndex], 0.0))
                         for axisIndex in range(3))

    # Upper triangle of the symmetric matrix (6 unique elements).
    upperTriangle = (
        exportedElements[0][0],  # C_XX
        exportedElements[0][1],  # C_XY
        exportedElements[0][2],  # C_XZ
        exportedElements[1][1],  # C_YY
        exportedElements[1][2],  # C_YZ
        exportedElements[2][2],  # C_ZZ
    )

    # Largest first (the sort is stable, so equal eigenvalues keep their
    # order).
    eigenvaluesAndVectors.sort(key=lambda eigenvalueAndVector: eigenvalueAndVector[0], reverse=True)
    semiAxisLengths = tuple(math.sqrt(eigenvalue) for eigenvalue, eigenvector in eigenvaluesAndVectors)
    eigenVectors = tuple(eigenvector for eigenvalue, eigenvector in eigenvaluesAndVectors)

    return perAxisSigma, upperTriangle, semiAxisLengths, eigenVectors, repaired


def DecomposeCovarianceMatrix(transformedCovariance):
    """DecomposeCovarianceMatrixWithRepair without the repair flag:
    (perAxisSigma, upperTriangle, semiAxisLengths, eigenVectors), all
    from one covariance matrix, the repaired one when the matrix had to
    be repaired.  A variance that rounding makes slightly negative gives
    a standard deviation of zero, and a negative eigenvalue that the
    check accepts as rounding is set to zero, so that it gives a
    semi-axis of zero."""
    return DecomposeCovarianceMatrixWithRepair(transformedCovariance)[:4]


# ===================================================================
# CSV export
# ===================================================================

# Row and column indices of the six unique covariance elements, in the
# order of the CSV columns (C_XX, C_XY, C_XZ, C_YY, C_YZ, C_ZZ).
_UPPER_TRIANGLE_INDICES = ((0, 0), (0, 1), (0, 2), (1, 1), (1, 2), (2, 2))


def BuildCsvHeader(exportPerAxisSigma, exportUpperTriangle,
                   exportEigenDecomposition, localFrameApplied,
                   geocentricFrame=False, physicalScale=True, arbitraryWorldCoordinates=False):
    """Assemble the CSV column header list.

    Parameters
    ----------
    exportPerAxisSigma : bool
    exportUpperTriangle : bool
    exportEigenDecomposition : bool
    localFrameApplied : bool
        True when the values are in local east-north-up coordinates:
        axes E, N, and U, in metres, coordinates measured from the
        region centre.  Otherwise axes X, Y, and Z.
    geocentricFrame : bool
        True when, without local east-north-up coordinates, the values
        are the world coordinates of a georeferenced chunk: geocentric
        (Earth-centred, Earth-fixed) in metres, labelled
        [m, geocentric ECEF].
    physicalScale : bool
        For a non-georeferenced chunk: True when the chunk has a
        physical scale (IsChunkScalePhysical), so that its world
        coordinates and lengths are metres, also in a local CRS whose
        unit is not the metre, because only CoordinateSystem.project
        gives coordinates in that unit.  The labels are then [m] and [m^2],
        and otherwise [chunk units] and [chunk units^2] (Geert, 13
        October 2026).  The eigenvectors are labelled [CRS] in both
        cases, the axes of the chunk CRS.
    arbitraryWorldCoordinates : bool
        True when the world coordinates are local coordinates although
        the chunk names a geographic, projected, or geocentric CRS
        (HasArbitraryWorldCoordinates: a chunk without reference
        coordinates, or a Region Tools transform): the eigenvectors are
        then labelled [world coordinates], because their components are
        not along the axes of that CRS, with a physical scale as well.

    Returns
    -------
    header : list of str
    """
    if localFrameApplied:
        axisLabels = ("E", "N", "U")
        coordinateUnit = "m, local ENU at region centre"
        lengthUnit = "m"
        areaUnit = "m^2"
        directionFrame = "local ENU"
    elif geocentricFrame:
        # The chunk transform maps internal coordinates to geocentric
        # coordinates in a georeferenced chunk (CoordinateSystem.project
        # would be needed for coordinates of its CRS).
        axisLabels = ("X", "Y", "Z")
        coordinateUnit = "m, geocentric ECEF"
        lengthUnit = "m"
        areaUnit = "m^2"
        directionFrame = "geocentric ECEF"
    elif physicalScale:
        axisLabels = ("X", "Y", "Z")
        coordinateUnit = "m"
        lengthUnit = "m"
        areaUnit = "m^2"
        directionFrame = "world coordinates" if arbitraryWorldCoordinates else "CRS"
    else:
        axisLabels = ("X", "Y", "Z")
        coordinateUnit = "chunk units"
        lengthUnit = "chunk units"
        areaUnit = "chunk units^2"
        directionFrame = "world coordinates" if arbitraryWorldCoordinates else "CRS"

    header = ["TrackId"]
    header.extend("{} [{}]".format(axisLabel, coordinateUnit)
                  for axisLabel in axisLabels)

    if exportPerAxisSigma:
        header.extend("sigma_{} [{}]".format(axisLabel, lengthUnit)
                      for axisLabel in axisLabels)

    if exportUpperTriangle:
        header.extend(
            "C_{}{} [{}]".format(axisLabels[rowIndex], axisLabels[columnIndex], areaUnit)
            for rowIndex, columnIndex in _UPPER_TRIANGLE_INDICES)

    if exportEigenDecomposition:
        header.extend("semiAxis_{} [{}]".format(axisNumber, lengthUnit)
                      for axisNumber in (1, 2, 3))
        header.extend(
            "eigenVector{}_{} [{}]".format(axisNumber, axisLabel, directionFrame)
            for axisNumber in (1, 2, 3) for axisLabel in axisLabels)

    return header


def LocalFrameOriginComment(chunk):
    """Comment line that opens an export in local east-north-up
    coordinates: their origin (the region centre) in the chunk CRS, so
    that the offsets can be placed again."""
    crs = chunk.crs
    crsName = getattr(crs, "name", "") or "the chunk CRS"
    try:
        originCrs = crs.project(chunk.transform.matrix.mulp(chunk.region.center))
        if CrsKind(crs) == "geographic":
            originText = "{:.9f}, {:.9f}, {:.6f}".format(
                originCrs[0], originCrs[1], originCrs[2])
        else:
            originText = "{:.6f}, {:.6f}, {:.6f}".format(
                originCrs[0], originCrs[1], originCrs[2])
    except Exception as suppressedError:
        ReportSuppressedError("tiePointCovarianceExporter.LocalFrameOriginComment", suppressedError)
        originText = "unavailable"
    return ("# Local east-north-up coordinates in metres, origin at the region "
            "centre: {} ({})".format(originText, crsName))


def GeocentricFrameComment(chunk):
    """Comment line that opens an export in the geocentric world
    coordinates of a georeferenced chunk, so that the X, Y, and Z columns
    are not taken for coordinates of the chunk CRS."""
    crsName = getattr(chunk.crs, "name", "") or "the chunk CRS"
    return ("# Geocentric (Earth-centred, Earth-fixed) coordinates in metres: "
            "the world coordinates of the chunk transform, not coordinates of "
            "{}".format(crsName))


# Reasons written to the companion file for the tie points that are not
# exported (see ExportTiePointCovariances).
_NOT_EXPORTED_INVALID_TIE_POINT = "invalid tie point"
_NOT_EXPORTED_WITHOUT_COVARIANCE = "no covariance"
_NOT_EXPORTED_AT_INFINITY = "point at infinity"
_NOT_EXPORTED_INVALID_COVARIANCE = "invalid covariance matrix: {}"

# Text column of the companion file, the reason: the track ID is a
# number (see DataRowWithDecimalSeparator).
_COMPANION_TEXT_COLUMN_INDICES = frozenset({1})


def HasNoCovariance(internalCovariance):
    """True for the covariance matrix *internalCovariance* of a tie point
    without covariance: None, or a matrix of zeros, which Metashape stores
    for every 3D tie point after Align Photos and when the last Optimize
    Cameras did not estimate the tie point covariance (all 1,356,514 3D
    tie points of an export in Metashape 2.3.2).  The elements are read
    row by row until one is not zero, so that a matrix with covariance
    usually takes one read: its first element is a variance."""
    if internalCovariance is None:
        return True
    for rowIndex in range(3):
        for columnIndex in range(3):
            if float(internalCovariance[rowIndex, columnIndex]) != 0.0:
                return False
    return True


def CompanionCsvFilePath(outputFilePath):
    """Path of the companion file next to the main file, which lists
    every tie point that is not exported with the reason: the main file
    name with _notExported added before the extension, so that the
    companion file has the same extension as the main file, .csv or
    .txt."""
    pathWithoutExtension, extension = os.path.splitext(outputFilePath)
    return "{}_notExported{}".format(pathWithoutExtension, extension or ".csv")


def CompanionFirstLine(outputFilePath):
    """First line of the companion file of the main file *outputFilePath*,
    which names the main file."""
    return "# Tie points not exported to {}, with the reason".format(os.path.basename(outputFilePath))


def IsCompanionOf(companionFilePath, outputFilePath):
    """True when the file *companionFilePath* is the companion file of an
    earlier export to *outputFilePath*: its first line names that main file
    (CompanionFirstLine).  False for a file that cannot be read."""
    try:
        with open(companionFilePath, encoding="utf-8", errors="replace") as fileHandle:
            return fileHandle.readline().rstrip("\r\n") == CompanionFirstLine(outputFilePath)
    except OSError:
        return False


def FilesToConfirm(chosenFilePath, outputFilePath):
    """The existing files that an export to *outputFilePath* would replace
    although the save dialog, where *chosenFilePath* was chosen, did not
    confirm them: the main file when .csv was added to the chosen name
    (ExportFilePathWithExtension), and the companion file
    (CompanionCsvFilePath) unless it is that of an earlier export to the
    same main file (IsCompanionOf)."""
    filePaths = []
    if os.path.normcase(os.path.abspath(outputFilePath)) != os.path.normcase(os.path.abspath(chosenFilePath)) \
            and os.path.exists(outputFilePath):
        filePaths.append(outputFilePath)
    companionFilePath = CompanionCsvFilePath(outputFilePath)
    if os.path.exists(companionFilePath) and not IsCompanionOf(companionFilePath, outputFilePath):
        filePaths.append(companionFilePath)
    return filePaths


def OutputPathsThatAreNoFiles(outputFilePath):
    """The paths of an export to *outputFilePath* and of its companion file
    (CompanionCsvFilePath) at which something exists that is not a file,
    such as a folder, which the export refuses before it asks about
    replacing files."""
    return [filePath for filePath in (outputFilePath, CompanionCsvFilePath(outputFilePath))
            if os.path.lexists(filePath) and not os.path.isfile(filePath)]


def NoFileOutputPathsText(filePaths):
    """The message when the export refuses the paths *filePaths*
    (OutputPathsThatAreNoFiles)."""
    if len(filePaths) == 1:
        return "{} exists and is not a file (for example a folder), so nothing was exported.".format(filePaths[0])
    return "{} exist and are not files (for example folders), so nothing was exported.".format(
        " and ".join(filePaths))


def ReplaceFilesQuestionText(filePaths):
    """The question before an export replaces the files *filePaths*
    (FilesToConfirm), which the save dialog did not confirm."""
    if len(filePaths) == 1:
        return ("The export writes {}, a file that exists already and that the save dialog did not ask "
                "about.\n\nReplace it?".format(filePaths[0]))
    return ("The export writes {}, files that exist already and that the save dialog did not ask "
            "about.\n\nReplace them?".format(" and ".join(filePaths)))


def TemporarySiblingPath(filePath, suffix):
    """Path of a new, empty temporary file next to *filePath*, whose name
    starts with the file name of *filePath* and ends with *suffix*.  The
    file is created by this call, so that no other file gets that name."""
    fileDescriptor, temporaryFilePath = tempfile.mkstemp(
        dir=os.path.dirname(os.path.abspath(filePath)), prefix=os.path.basename(filePath) + ".", suffix=suffix)
    os.close(fileDescriptor)
    return temporaryFilePath


def RemoveFileQuietly(filePath):
    """Remove the file *filePath* when it exists (a failure is reported
    once in the Console).  Returns False when the file could not be
    removed, True otherwise."""
    try:
        if filePath is not None and os.path.exists(filePath):
            os.remove(filePath)
    except OSError as suppressedError:
        ReportSuppressedError("tiePointCovarianceExporter.RemoveFileQuietly", suppressedError)
        return False
    return True


@contextlib.contextmanager
def TemporaryFilesRemovedOnFailure(temporaryFilePaths):
    """Context in which the temporary files *temporaryFilePaths* are
    written: when it ends with an exception, they are removed."""
    try:
        yield
    except BaseException:
        for temporaryFilePath in temporaryFilePaths:
            RemoveFileQuietly(temporaryFilePath)
        raise


def RollBackReplacedFiles(placedFinalPaths, backupPathByFinalPath):
    """Give the final files of a failed ReplaceFilesTogether their earlier
    content back: remove each placed final file *placedFinalPaths* that
    did not exist before, and move each backup of
    *backupPathByFinalPath* back onto its final file.  Returns a sentence
    for each final file that stayed changed, naming the backup that keeps
    its earlier content, which is then kept (an empty list when every
    final file got its earlier content back)."""
    changedFileSentences = []
    for finalFilePath in reversed(placedFinalPaths):
        if finalFilePath not in backupPathByFinalPath and not RemoveFileQuietly(finalFilePath):
            changedFileSentences.append("{} was written and could not be removed.".format(finalFilePath))
    for finalFilePath, backupFilePath in backupPathByFinalPath.items():
        try:
            os.replace(backupFilePath, finalFilePath)
        except OSError as suppressedError:
            ReportSuppressedError("tiePointCovarianceExporter.ReplaceFilesTogether", suppressedError)
            changedFileSentences.append("{} could not get its earlier content back, which is kept in {}.".format(
                finalFilePath, backupFilePath))
    return changedFileSentences


def ReplaceFilesTogether(filePathPairs):
    """Move each temporary file onto its final file, for the pairs
    (temporary path, final path) of *filePathPairs* in their order, all or
    none.  Before anything moves, each final path must be a file or
    absent: when one is a folder, or anything else that is not a file,
    the temporary files are removed and an OSError names it, so that
    nothing at a final path changes, whichever operating system renames
    what.  An existing final file is first moved to a backup next to it.
    When a move fails, for example because the file is open in another
    program, the final files get back their earlier content (a final file
    that did not exist is removed, see RollBackReplacedFiles), the
    temporary files are removed, and an OSError names the file that
    failed.  When a final file cannot get its earlier content back, the
    OSError names it and the backup that keeps that content.  The backups
    are removed after the last move."""
    for temporaryFilePath, finalFilePath in filePathPairs:
        if os.path.lexists(finalFilePath) and not os.path.isfile(finalFilePath):
            for temporaryFilePathToRemove, finalFilePathUnused in filePathPairs:
                RemoveFileQuietly(temporaryFilePathToRemove)
            raise OSError("{} exists and is not a file (for example a folder), so neither file was "
                          "written".format(finalFilePath))
    backupPathByFinalPath = {}
    placedFinalPaths = []
    try:
        for temporaryFilePath, finalFilePath in filePathPairs:
            failedFilePath = finalFilePath
            if os.path.exists(finalFilePath):
                backupFilePath = TemporarySiblingPath(finalFilePath, ".previous")
                try:
                    os.replace(finalFilePath, backupFilePath)
                except OSError:
                    RemoveFileQuietly(backupFilePath)
                    raise
                backupPathByFinalPath[finalFilePath] = backupFilePath
            os.replace(temporaryFilePath, finalFilePath)
            placedFinalPaths.append(finalFilePath)
    except OSError as replaceError:
        changedFileSentences = RollBackReplacedFiles(placedFinalPaths, backupPathByFinalPath)
        for temporaryFilePath, finalFilePathUnused in filePathPairs:
            RemoveFileQuietly(temporaryFilePath)
        if changedFileSentences:
            raise OSError("{} could not be written ({}). {}".format(
                failedFilePath, DescribeException(replaceError), " ".join(changedFileSentences))) from replaceError
        raise OSError("{} could not be written ({}), so neither file was changed".format(
            failedFilePath, DescribeException(replaceError))) from replaceError
    for backupFilePath in backupPathByFinalPath.values():
        RemoveFileQuietly(backupFilePath)


# Extensions of the exported file, in any case: a CSV file or a text
# file with the same table.
_EXPORT_FILE_EXTENSIONS = (".csv", ".txt")


def ExportFilePathWithExtension(chosenFilePath):
    """Path of the exported table for the path chosen in the save
    dialog: a path that ends in .csv or .txt, in any case, stays as it
    is, so that a text file gets the same table as a CSV file, and any
    other path gets .csv appended."""
    if chosenFilePath.lower().endswith(_EXPORT_FILE_EXTENSIONS):
        return chosenFilePath
    return chosenFilePath + ".csv"


def ExportTiePointCovariances(chunk, outputFilePath,
                              exportPerAxisSigma,
                              exportUpperTriangle,
                              exportEigenDecomposition,
                              applyLocalFrame,
                              coordinateDecimalPlaces=6,
                              uncertaintyDecimalPlaces=8,
                              csvDelimiter=",",
                              decimalSeparator=".",
                              skippedCountByReason=None):
    """Export tie-point positions and covariance data to a CSV or text file.

    Every tie point of the chunk that is not exported is written with
    its reason (an invalid tie point, no covariance (HasNoCovariance), a
    point at infinity, or an invalid covariance matrix with the reason that
    ValidatedCovarianceElements gives) to the companion file next to the
    main file (CompanionCsvFilePath), so that the main file holds numbers
    only.  The companion file is written on every export, also when it
    lists no tie point, so that it never keeps the tie points of an
    earlier export to the same file.  Both files are first written to
    temporary files next to them (TemporarySiblingPath), which then
    replace the two files together (ReplaceFilesTogether): a failure
    leaves both files as they were.

    Parameters
    ----------
    chunk : Metashape.Chunk
    outputFilePath : str
    exportPerAxisSigma : bool
    exportUpperTriangle : bool
    exportEigenDecomposition : bool
    applyLocalFrame : bool
        Request local east-north-up coordinates at the region centre
        (applied only in a georeferenced chunk).
    coordinateDecimalPlaces : int
        Decimal places for the coordinates (default 6).
    uncertaintyDecimalPlaces : int
        Decimal places for the standard deviations, semi-axes, and
        eigenvector components (default 8).  Covariance elements are
        written in scientific notation with seven significant digits,
        so that sub-millimetre variances keep their significant
        digits.
    csvDelimiter : str
        Field delimiter (default comma).
    decimalSeparator : str
        Decimal separator of the numbers of the data rows (default
        point): a comma for the semicolon choice of CSV_FORMAT_CHOICES.
        The comment lines and the header keep their text.  The
        companion file follows the same choice, and its reasons keep
        their text as well.
    skippedCountByReason : dict or None
        When given, receives the number of the points that skippedCount
        counts for each of their reasons in the companion file (an
        invalid tie point, no covariance, or a point at infinity), so
        that the completion message gives a hint only for the reason
        that applies.

    Returns
    -------
    exportedCount : int
        Number of valid tie points exported.
    skippedCount : int
        Number of points skipped because the tie point is invalid, has
        no covariance, or lies at infinity.
    localFrameApplied : bool
        True when the values are in local east-north-up coordinates.
    invalidCovariances : list of (track_id, str)
        The points skipped because their transformed matrix is not a
        covariance matrix (see DecomposeCovarianceMatrix), each with
        the reason.
    """
    transformMatrix, scaledRotation, localFrameApplied = BuildCrsTransformMatrices(
        chunk, applyLocalFrame)

    geocentricFrame = (not localFrameApplied) and HasGeocentricWorldCoordinates(chunk)
    header = BuildCsvHeader(exportPerAxisSigma, exportUpperTriangle,
                            exportEigenDecomposition, localFrameApplied,
                            geocentricFrame, IsChunkScalePhysical(chunk),
                            HasArbitraryWorldCoordinates(chunk))

    exportedCount = 0
    skippedCount = 0
    repairedCount = 0
    invalidCovariances = []
    skippedReasonCounts = {}

    coordinateFormat = "{{:.{}f}}".format(coordinateDecimalPlaces)
    uncertaintyFormat = "{{:.{}f}}".format(uncertaintyDecimalPlaces)

    # UTF-8 whatever the system encoding: the comment line carries the
    # name of the CRS.  The companion file is written while
    # the points are read, so that a chunk with many skipped points does
    # not hold them in memory.  Both go to temporary files first.
    companionFilePath = CompanionCsvFilePath(outputFilePath)
    mainTemporaryFilePath = TemporarySiblingPath(outputFilePath, ".partial")
    try:
        companionTemporaryFilePath = TemporarySiblingPath(companionFilePath, ".partial")
    except OSError:
        RemoveFileQuietly(mainTemporaryFilePath)
        raise
    temporaryFilePaths = (mainTemporaryFilePath, companionTemporaryFilePath)
    with TemporaryFilesRemovedOnFailure(temporaryFilePaths), \
            open(temporaryFilePaths[0], "w", newline="", encoding="utf-8") as csvFile, \
            open(temporaryFilePaths[1], "w", newline="", encoding="utf-8") as companionFile:
        if localFrameApplied:
            csvFile.write(LocalFrameOriginComment(chunk) + "\r\n")
        elif geocentricFrame:
            csvFile.write(GeocentricFrameComment(chunk) + "\r\n")
        writer = csv.writer(csvFile, delimiter=csvDelimiter)
        writer.writerow(header)

        companionFile.write(CompanionFirstLine(outputFilePath) + "\r\n")
        companionWriter = csv.writer(companionFile, delimiter=csvDelimiter)
        companionWriter.writerow(["TrackId", "Reason"])

        diagnosticCount = 0

        for point in chunk.tie_points.points:
            if not point.valid:
                skippedCount += 1
                skippedReasonCounts[_NOT_EXPORTED_INVALID_TIE_POINT] = (
                    skippedReasonCounts.get(_NOT_EXPORTED_INVALID_TIE_POINT, 0) + 1)
                companionWriter.writerow(DataRowWithDecimalSeparator(
                    [point.track_id, _NOT_EXPORTED_INVALID_TIE_POINT], decimalSeparator,
                    _COMPANION_TEXT_COLUMN_INDICES))
                continue

            internalCovariance = point.cov
            if HasNoCovariance(internalCovariance):
                skippedCount += 1
                skippedReasonCounts[_NOT_EXPORTED_WITHOUT_COVARIANCE] = (
                    skippedReasonCounts.get(_NOT_EXPORTED_WITHOUT_COVARIANCE, 0) + 1)
                companionWriter.writerow(DataRowWithDecimalSeparator(
                    [point.track_id, _NOT_EXPORTED_WITHOUT_COVARIANCE], decimalSeparator,
                    _COMPANION_TEXT_COLUMN_INDICES))
                continue

            # Homogeneous point: divide by w, then transform.
            transformedCoordinate = TransformHomogeneousPoint(
                transformMatrix, point.coord)
            if transformedCoordinate is None:
                skippedCount += 1
                skippedReasonCounts[_NOT_EXPORTED_AT_INFINITY] = (
                    skippedReasonCounts.get(_NOT_EXPORTED_AT_INFINITY, 0) + 1)
                companionWriter.writerow(DataRowWithDecimalSeparator(
                    [point.track_id, _NOT_EXPORTED_AT_INFINITY], decimalSeparator,
                    _COMPANION_TEXT_COLUMN_INDICES))
                continue

            # Propagate the covariance to the output coordinates.
            transformedCovariance = TransformPointCovariance(
                internalCovariance, scaledRotation)

            # Diagnostic: print the first 3 covariance matrices.
            if _PRINT_COVARIANCE_DIAGNOSTICS and diagnosticCount < 3:
                print("--- Tie point {} (track_id={}) ---".format(
                    diagnosticCount + 1, point.track_id))
                print("  Chunk covariance (raw):")
                for index in range(3):
                    print("    [{:14.8e}  {:14.8e}  {:14.8e}]".format(
                        internalCovariance[index, 0],
                        internalCovariance[index, 1],
                        internalCovariance[index, 2]))
                print("  Transformed covariance:")
                for index in range(3):
                    print("    [{:14.8e}  {:14.8e}  {:14.8e}]".format(
                        transformedCovariance[index, 0],
                        transformedCovariance[index, 1],
                        transformedCovariance[index, 2]))
                leftSingularVectors, singularValues, rightSingularVectorsUnused = transformedCovariance.svd()
                print("  Singular values (singular value decomposition): [{}, {}, {}]".format(
                    singularValues[0], singularValues[1],
                    singularValues[2]))
                print("  Left singular vectors (U):")
                for index in range(3):
                    print("    [{:12.8f}  {:12.8f}  {:12.8f}]".format(
                        leftSingularVectors[index, 0], leftSingularVectors[index, 1],
                        leftSingularVectors[index, 2]))
                print("")
                diagnosticCount += 1

            # Check and decompose.  A matrix that is not a covariance
            # matrix is skipped and named rather than written with
            # plausible but meaningless values.
            try:
                perAxisSigma, upperTriangle, semiAxisLengths, eigenVectors, repaired = \
                    DecomposeCovarianceMatrixWithRepair(transformedCovariance)
            except ValueError as covarianceError:
                invalidCovariances.append((point.track_id, str(covarianceError)))
                companionWriter.writerow(DataRowWithDecimalSeparator(
                    [point.track_id, _NOT_EXPORTED_INVALID_COVARIANCE.format(str(covarianceError))],
                    decimalSeparator, _COMPANION_TEXT_COLUMN_INDICES))
                continue

            # Build row.
            row = [
                point.track_id,
                coordinateFormat.format(transformedCoordinate[0]),
                coordinateFormat.format(transformedCoordinate[1]),
                coordinateFormat.format(transformedCoordinate[2]),
            ]

            if exportPerAxisSigma:
                row.extend([
                    uncertaintyFormat.format(perAxisSigma[0]),
                    uncertaintyFormat.format(perAxisSigma[1]),
                    uncertaintyFormat.format(perAxisSigma[2]),
                ])

            if exportUpperTriangle:
                # Scientific notation: a variance of (0.05 mm)^2 is
                # 2.5e-09 m^2, which fixed decimals would write as 0.
                row.extend(["{:.6e}".format(value) for value in upperTriangle])

            if exportEigenDecomposition:
                row.extend([
                    uncertaintyFormat.format(semiAxisLengths[0]),
                    uncertaintyFormat.format(semiAxisLengths[1]),
                    uncertaintyFormat.format(semiAxisLengths[2]),
                ])
                for eigenvector in eigenVectors:
                    row.extend([
                        uncertaintyFormat.format(eigenvector[0]),
                        uncertaintyFormat.format(eigenvector[1]),
                        uncertaintyFormat.format(eigenvector[2]),
                    ])

            # Every column of the main file holds numbers.
            writer.writerow(DataRowWithDecimalSeparator(row, decimalSeparator))
            exportedCount += 1
            if repaired:
                repairedCount += 1

    # The companion file first, so that a companion file that cannot be
    # replaced leaves the main file untouched.
    ReplaceFilesTogether([(temporaryFilePaths[1], companionFilePath), (temporaryFilePaths[0], outputFilePath)])
    if skippedCountByReason is not None:
        skippedCountByReason.update(skippedReasonCounts)
    if repairedCount:
        print("  {} with a negative eigenvalue attributed to rounding repaired before the export (all "
              "columns describe the symmetric part with that eigenvalue set to zero).".format(
                  FormatCountWithNoun(repairedCount, "covariance matrix", "covariance matrices")))
    return exportedCount, skippedCount, localFrameApplied, invalidCovariances


# ===================================================================
# Default file name
# ===================================================================



def ExportSettingsConsoleLines(exportPerAxisSigma, exportUpperTriangle, exportEigenDecomposition,
                               coordinateDecimalPlaces, uncertaintyDecimalPlaces, csvDelimiter, decimalSeparator):
    """The lines of the Console block of an export that give its settings:
    the column groups, the decimal places, and the CSV format."""
    columnGroups = [name for chosen, name in (
        (exportPerAxisSigma, "per-axis standard deviations"),
        (exportUpperTriangle, "covariance elements"),
        (exportEigenDecomposition, "error ellipsoid")) if chosen]
    return [ConsoleLabelledLine("Columns:", ", ".join(columnGroups) or "coordinates only"),
            ConsoleLabelledLine("Decimal places:", "{} for the coordinates, {} for the uncertainties".format(
                coordinateDecimalPlaces, uncertaintyDecimalPlaces)),
            ConsoleLabelledLine("CSV format:", CsvFormatName(csvDelimiter, decimalSeparator))]


def _BuildDefaultCsvFilename(document, chunk):
    """Build a default CSV file name from the project and chunk names."""
    projectName = ""
    if document.path:
        projectName = os.path.splitext(
            os.path.basename(document.path))[0]
    chunkName = chunk.label or ""
    return "tiePointCovarianceExport_{}_{}.csv".format(
        ConvertToCamelCase(projectName) if projectName else "project",
        ConvertToCamelCase(chunkName) if chunkName else "chunk")


# ===================================================================
# GUI
# ===================================================================

class TiePointCovarianceDialog(QtWidgets.QDialog):
    """Main dialog for the Tie Point Covariance Exporter."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Tie Point Covariance Exporter")

        # Remove the ? help button from the title bar.
        self.setWindowFlags(
            self.windowFlags()
            & ~QtCore.Qt.WindowContextHelpButtonHint)

        self.setMinimumWidth(440)
        self._BuildUi()

    def _BuildUi(self):
        mainLayout = QtWidgets.QVBoxLayout(self)

        # --- Chunk info ------------------------------------------------
        activeChunkGroup = QtWidgets.QGroupBox("Active chunk")
        activeChunkLayout = QtWidgets.QFormLayout(activeChunkGroup)

        chunk = Metashape.app.document.chunk

        chunkLabel = chunk.label if chunk else "(none)"
        self.labelChunkName = QtWidgets.QLabel(chunkLabel)
        activeChunkLayout.addRow("Chunk:", self.labelChunkName)

        if chunk and chunk.tie_points and chunk.tie_points.points:
            totalPointCount = len(chunk.tie_points.points)
            validPointCount = sum(
                1 for point in chunk.tie_points.points if point.valid)
        else:
            totalPointCount = 0
            validPointCount = 0
        self.labelPointCount = QtWidgets.QLabel(
            "{:,} valid / {:,} total".format(validPointCount, totalPointCount))
        activeChunkLayout.addRow("Tie points:", self.labelPointCount)

        crsName = "(not set)"
        if chunk and chunk.crs:
            crsName = chunk.crs.name
        self.labelCrsName = QtWidgets.QLabel(crsName)
        activeChunkLayout.addRow("Coordinate reference system:", self.labelCrsName)

        mainLayout.addWidget(activeChunkGroup)

        # --- Export options --------------------------------------------
        exportGroup = QtWidgets.QGroupBox("Quantities to export")
        exportLayout = QtWidgets.QVBoxLayout(exportGroup)

        self.perAxisSigmaCheckBox = QtWidgets.QCheckBox(
            "Per-axis standard deviations (\u03c3_X, \u03c3_Y, \u03c3_Z)")
        self.perAxisSigmaCheckBox.setChecked(True)
        self.perAxisSigmaCheckBox.setToolTip(
            "Square roots of the diagonal elements of the "
            "transformed covariance matrix.\n"
            "These are the standard deviations (1\u03c3) along each "
            "coordinate axis.")
        exportLayout.addWidget(self.perAxisSigmaCheckBox)

        self.upperTriangleCheckBox = QtWidgets.QCheckBox(
            "Full covariance upper triangle "
            "(C_XX, C_XY, C_XZ, C_YY, C_YZ, C_ZZ)")
        self.upperTriangleCheckBox.setChecked(False)
        self.upperTriangleCheckBox.setToolTip(
            "The six unique elements of the symmetric 3\u00d73 "
            "covariance matrix,\nwritten in scientific notation. Their "
            "unit is the square of the\ncoordinate unit (m\u00b2 in "
            "local east-north-up coordinates).")
        exportLayout.addWidget(self.upperTriangleCheckBox)

        self.eigenDecompositionCheckBox = QtWidgets.QCheckBox(
            "Error ellipsoid "
            "(semi-axes and eigenvector orientations)")
        self.eigenDecompositionCheckBox.setChecked(False)
        self.eigenDecompositionCheckBox.setToolTip(
            "Eigendecomposition of the covariance matrix.\n"
            "Semi-axes = \u221a(eigenvalue), sorted largest first.\n"
            "Eigenvectors give the orientation of each axis along "
            "the output coordinate axes.")
        exportLayout.addWidget(self.eigenDecompositionCheckBox)

        mainLayout.addWidget(exportGroup)

        # --- Coordinate reference system -------------------------------
        frameGroup = QtWidgets.QGroupBox("Coordinate reference system")
        frameLayout = QtWidgets.QVBoxLayout(frameGroup)

        self.localFrameCheckBox = QtWidgets.QCheckBox(
            "Express in local east-north-up (ENU) coordinates at the region centre")
        self.localFrameCheckBox.setChecked(False)
        # East, north, and up exist only in a georeferenced chunk whose
        # world coordinates are geocentric.
        localFrameAvailable = bool(chunk) and HasGeocentricWorldCoordinates(chunk)
        self.localFrameCheckBox.setEnabled(localFrameAvailable)
        self.localFrameCheckBox.setToolTip(
            "Expresses coordinates and covariances in local\n"
            "east-north-up coordinates in metres, with their origin at\n"
            "the centre of the chunk region. The columns are then\n"
            "labelled E, N, and U, and the first line of the file\n"
            "records the origin in the chunk CRS."
            + ("" if localFrameAvailable else
               "\n\nAvailable only for a georeferenced chunk:\n"
               "a geographic, projected, or geocentric CRS and\n"
               "a transform placed on the Earth, as reference\n"
               "coordinates give it, not from Region Tools."))
        frameLayout.addWidget(self.localFrameCheckBox)

        mainLayout.addWidget(frameGroup)

        # --- Decimal places --------------------------------------------
        decimalPlacesGroup = QtWidgets.QGroupBox("Output decimal places")
        decimalPlacesLayout = QtWidgets.QFormLayout(decimalPlacesGroup)

        self.coordinateDecimalPlacesSpinBox = QtWidgets.QSpinBox()
        self.coordinateDecimalPlacesSpinBox.setRange(0, 12)
        self.coordinateDecimalPlacesSpinBox.setValue(6)
        self.coordinateDecimalPlacesSpinBox.setToolTip(
            "Number of decimal places for the coordinates.")
        decimalPlacesLayout.addRow("Coordinate decimal places:",
                                   self.coordinateDecimalPlacesSpinBox)

        self.uncertaintyDecimalPlacesSpinBox = QtWidgets.QSpinBox()
        self.uncertaintyDecimalPlacesSpinBox.setRange(0, 12)
        self.uncertaintyDecimalPlacesSpinBox.setValue(8)
        self.uncertaintyDecimalPlacesSpinBox.setToolTip(
            "Number of decimal places for the standard deviations,\n"
            "semi-axes, and eigenvector components. Covariance elements\n"
            "are written in scientific notation with seven significant\n"
            "digits (for example 2.500000e-09).")
        decimalPlacesLayout.addRow("Uncertainty decimal places:",
                                   self.uncertaintyDecimalPlacesSpinBox)

        mainLayout.addWidget(decimalPlacesGroup)

        # --- Delimiter and decimal separator ----
        delimiterRow = QtWidgets.QHBoxLayout()
        delimiterRow.addWidget(QtWidgets.QLabel(CSV_FORMAT_ROW_LABEL_TEXT))
        self.delimiterComboBox = QtWidgets.QComboBox()
        FillCsvFormatComboBox(self.delimiterComboBox)
        delimiterRow.addWidget(self.delimiterComboBox)
        delimiterRow.addStretch()
        mainLayout.addLayout(delimiterRow)
        mainLayout.addSpacing(4)

        # --- Buttons ---------------------------------------------------
        buttonLayout = QtWidgets.QHBoxLayout()

        self.buttonAbout = QtWidgets.QPushButton("About")
        self.buttonAbout.clicked.connect(self._ShowAbout)
        buttonLayout.addWidget(self.buttonAbout)

        buttonLayout.addStretch()

        self.buttonExport = QtWidgets.QPushButton("Export\u2026")
        self.buttonExport.setDefault(True)
        self.buttonExport.clicked.connect(self._OnExport)
        buttonLayout.addWidget(self.buttonExport)

        self.buttonClose = QtWidgets.QPushButton("Close")
        self.buttonClose.clicked.connect(self.reject)
        buttonLayout.addWidget(self.buttonClose)

        mainLayout.addLayout(buttonLayout)

    # ---------------------------------------------------------------
    # Export
    # ---------------------------------------------------------------

    def _OnExport(self):
        """Validate inputs and run the export."""
        chunk = Metashape.app.document.chunk
        if not chunk:
            QtWidgets.QMessageBox.warning(
                self, "Tie Point Covariance Exporter",
                "There is no active chunk in the document.")
            return

        if not chunk.tie_points or not chunk.tie_points.points:
            QtWidgets.QMessageBox.warning(
                self, "Tie Point Covariance Exporter",
                "The active chunk has no tie points.\n"
                "Run Align Photos first.")
            return

        if chunk.transform.matrix is None:
            QtWidgets.QMessageBox.warning(
                self, "Tie Point Covariance Exporter",
                "The chunk has no transform yet.\n"
                "Orient the images (Align Photos) and, if needed, scale or reference the chunk.")
            return

        exportPerAxisSigma = self.perAxisSigmaCheckBox.isChecked()
        exportUpperTriangle = self.upperTriangleCheckBox.isChecked()
        exportEigenDecomposition = self.eigenDecompositionCheckBox.isChecked()

        if not (exportPerAxisSigma or exportUpperTriangle
                or exportEigenDecomposition):
            QtWidgets.QMessageBox.warning(
                self, "Tie Point Covariance Exporter",
                "Select at least one set of quantities to export.")
            return

        # The axis labels follow the coordinates actually applied (see
        # BuildCrsTransformMatrices and BuildCsvHeader).
        applyLocalFrame = self.localFrameCheckBox.isChecked()

        # File-save dialog: a CSV file or a text file with the same table.
        document = Metashape.app.document
        defaultDirectory = os.path.dirname(
            document.path) if document.path else ""
        defaultFilename = _BuildDefaultCsvFilename(document, chunk)
        outputFilePath = Metashape.app.getSaveFileName(
            "Save CSV or TXT",
            dir=os.path.join(defaultDirectory, defaultFilename),
            filter="CSV files (*.csv);;Text files (*.txt)")
        if not outputFilePath:
            return  # User cancelled.
        chosenFilePath = outputFilePath
        outputFilePath = ExportFilePathWithExtension(outputFilePath)
        # A folder at an output path is refused before any question about
        # replacing (ReplaceFilesTogether refuses it again just before the
        # files are placed).
        outputPathsThatAreNoFiles = OutputPathsThatAreNoFiles(outputFilePath)
        if outputPathsThatAreNoFiles:
            print("[{}] Nothing exported: {}".format(_TOOL_NAME, NoFileOutputPathsText(outputPathsThatAreNoFiles)))
            QtWidgets.QMessageBox.warning(self, _TOOL_NAME, NoFileOutputPathsText(outputPathsThatAreNoFiles))
            return
        # A file that the save dialog did not confirm is replaced only
        # after a question (FilesToConfirm).
        filesToConfirm = FilesToConfirm(chosenFilePath, outputFilePath)
        if filesToConfirm and QtWidgets.QMessageBox.question(
                self, _TOOL_NAME, ReplaceFilesQuestionText(filesToConfirm),
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.No) != QtWidgets.QMessageBox.Yes:
            print("[{}] Nothing exported: replacing {} was declined.".format(_TOOL_NAME, " and ".join(filesToConfirm)))
            return

        coordinateDecimalPlaces = self.coordinateDecimalPlacesSpinBox.value()
        uncertaintyDecimalPlaces = self.uncertaintyDecimalPlacesSpinBox.value()
        csvDelimiter, decimalSeparator = ChosenCsvFormat(self.delimiterComboBox)

        print("")
        print("=" * 60)
        print("Tie Point Covariance Exporter")
        print("=" * 60)
        print("Chunk:                     {}".format(chunk.label))
        print("CRS:                       {}".format(
            chunk.crs.name if chunk.crs else "(not set)"))
        print("Output:                    {}".format(outputFilePath))
        for settingsLine in ExportSettingsConsoleLines(
                exportPerAxisSigma, exportUpperTriangle, exportEigenDecomposition, coordinateDecimalPlaces,
                uncertaintyDecimalPlaces, csvDelimiter, decimalSeparator):
            print(settingsLine)
        print("")

        skippedCountByReason = {}
        try:
            exportedCount, skippedCount, localFrameApplied, invalidCovariances = ExportTiePointCovariances(
                chunk=chunk,
                outputFilePath=outputFilePath,
                exportPerAxisSigma=exportPerAxisSigma,
                exportUpperTriangle=exportUpperTriangle,
                exportEigenDecomposition=exportEigenDecomposition,
                applyLocalFrame=applyLocalFrame,
                coordinateDecimalPlaces=coordinateDecimalPlaces,
                uncertaintyDecimalPlaces=uncertaintyDecimalPlaces,
                csvDelimiter=csvDelimiter,
                decimalSeparator=decimalSeparator,
                skippedCountByReason=skippedCountByReason,
            )
        except Exception as exportError:
            # For example a CSV file that is open in another program.
            print("Export failed: {}".format(DescribeException(exportError)))
            print("=" * 60)
            print("")
            QtWidgets.QMessageBox.critical(
                self, _TOOL_NAME,
                "The export failed:\n\n{}".format(DescribeException(exportError)))
            return

        if invalidCovariances:
            print("Tie points skipped because their covariance matrix is not valid:")
            for trackId, reason in invalidCovariances[:_LISTED_INVALID_COVARIANCE_COUNT]:
                print("  Tie point {}: {}".format(trackId, reason))
            if len(invalidCovariances) > _LISTED_INVALID_COVARIANCE_COUNT:
                print("  and {:,} more".format(
                    len(invalidCovariances) - _LISTED_INVALID_COVARIANCE_COUNT))
            print("")
        print("East-north-up coordinates: {}".format(
            "Yes" if localFrameApplied else "No"))
        if not localFrameApplied and HasGeocentricWorldCoordinates(chunk):
            print("Coordinates:               geocentric (Earth-centred, "
                  "Earth-fixed), metres")
        print("Exported:                  {}".format(
            FormatCountWithNoun(exportedCount, "tie point", "tie points")))
        # Every tie point that is not exported, with the reason, is in the
        # companion file (the Console lists the first twenty invalid
        # covariance matrices only).
        companionFilePath = CompanionCsvFilePath(outputFilePath)
        print("Skipped:                   {:,} (invalid tie point, no covariance, or point at "
              "infinity)".format(skippedCount))
        print("Invalid covariance matrix: {:,}{}".format(
            len(invalidCovariances), " (skipped, listed above)" if invalidCovariances else ""))
        print("File:                      {}".format(outputFilePath))
        print("Tie points not exported:   {} (each with the reason)".format(companionFilePath))
        print("=" * 60)
        print("")

        completionMessage = ("Exported {}.\nSkipped {:,} (invalid tie point, no covariance, or point at "
                             "infinity).\n".format(FormatCountWithNoun(exportedCount, "tie point", "tie points"),
                                                   skippedCount))
        if invalidCovariances:
            completionMessage += "Skipped {} with an invalid covariance matrix.\n".format(
                FormatCountWithNoun(len(invalidCovariances), "tie point", "tie points"))
        completionMessage += "\nFile: {}".format(outputFilePath)
        completionMessage += "\nTie points not exported, with the reason for each: {}".format(companionFilePath)
        # The hint on the tie-point covariances only when some points had
        # none (not when every point was invalid or at infinity).
        if exportedCount == 0 and skippedCountByReason.get(_NOT_EXPORTED_WITHOUT_COVARIANCE, 0):
            completionMessage += (
                "\n\nNothing was exported: tie-point covariances "
                "exist only after Optimize Cameras has been run "
                "with 'Estimate tie point covariance' ticked.")
        QtWidgets.QMessageBox.information(
            self, "Tie Point Covariance Exporter", completionMessage)

    # ---------------------------------------------------------------
    # About
    # ---------------------------------------------------------------

    def _ShowAbout(self):
        """Show an About dialog with tool documentation."""
        AboutDialog(self).exec_()


_ABOUT_HTML = """
<h2>Tie Point Covariance Exporter</h2>
<p><b>Version:</b> 1.0.1 (2026-11-22)<br>
<b>Author:</b> Geert J. Verhoeven
(ORCID: <a href="https://orcid.org/0000-0003-4825-9604">
0000-0003-4825-9604</a>)<br>
<b>Contact:</b>
<a href="mailto:geert.verhoeven@univie.ac.at">
geert.verhoeven@univie.ac.at</a><br>
<b>Licence:</b> GNU General Public License, version 3 or any later version (GPL-3.0-or-later),
with an additional permission to convey MetaFOCUS combined with Agisoft Metashape and its
Python module (see LICENSE and ADDITIONAL_PERMISSION.txt in the MetaFOCUS ZIP file)</p>

<p><b>Prerequisite:</b> tie-point covariances exist only after
<i>Optimize Cameras</i> has been run with <i>Estimate tie point
covariance</i> ticked. Otherwise Metashape stores a covariance matrix of zeros for every 3D tie
point, and the tool skips these points as points without covariance.</p>

<h3>Overview</h3>
<p>This tool exports the formal 3D position covariance of each tie
point and quantities derived from it.
Metashape stores a 3&times;3 covariance matrix per tie point in
internal chunk coordinates.  The tool propagates the coordinates and
these matrices through the chunk transform, optionally into a local
east-north-up (ENU) coordinate reference system (CRS), and decomposes
the matrices into
interpretable uncertainty quantities.</p>
<p>The exported matrices are the formal coordinate covariance matrices that Metashape supplies for
the tie points, transformed into the chosen CRS, and the standard deviations and the ellipsoid are
derived from them. They are conditional on the observations, the a priori standard deviations (the
weights), the constraints, and the functional model of the adjustment. Metashape appears to scale
them by a variance factor that it estimates from the residuals. It does not document this, but two
checks support it. Doubling every a priori standard deviation of a least-squares adjustment
quadruples its cofactor matrix and halves its estimated &sigma;&#8320;, so that a covariance matrix
scaled by &sigma;&#8320;&sup2; stays the same. When the ‘Tie point accuracy’ is doubled in a chunk
whose only observations are tie points, the &sigma;&#8320; that Metashape stores
(OptimizeCameras/sigma0) halves, while the tie-point covariances and the calibration covariance of
that chunk stay the same. Both effects were checked with two projects in Metashape 2.3.2.</p>
<p>A second check, with one of these projects, compared sizes. For 300 3D tie points, point.cov was
smaller than the covariance that the 2D tie points of the point give with the images held fixed.
This covariance uses the keypoint size times the ‘Tie point accuracy’ as the a priori standard
deviations of the 2D tie points. Holding the images fixed can only make a covariance smaller, so an
unscaled point.cov could never be smaller than that covariance. The check also excludes a covariance
computed without the ‘Tie point accuracy’, which would have stayed the same as well. Divided by
&sigma;&#8320;&sup2;, point.cov was 1.0 to 9.6 times that covariance in trace (median 1.7). It
therefore fits the 3&times;3 block of the covariance matrix of all estimated parameters, which also
contains the uncertainty of the image orientations and the calibration.</p>
<p>The exported matrices contain only what the functional and stochastic models of the bundle
adjustment contain, so systematic effects that the adjustment does not model, such as lens
distortion that the camera model does not describe, are not included. They are formal (a posteriori)
estimates of the dispersion, not a measurement of the scatter that independent repetitions of the
image acquisition and adjustment would show. They agree with that scatter only as far as the models
and the a priori standard deviations are adequate. They do not establish trueness.</p>
<p>Type A and Type B name two methods of evaluating an uncertainty
component, not two kinds of error (JCGM 100:2008, 3.3.3 and 3.3.4,
and VIM 2.28 and 2.29).  JCGM 100:2008 (4.2.5) places the variances of
parameters fitted by least squares, calculated by statistical
procedures from the observations, under the Type A evaluation.
Propagating the inputs of this adjustment through it does not make
the result a Type A evaluation as a whole, however. The a priori
standard deviations of the image observations are assigned in the
Reference Settings. Reference distances can carry standard
uncertainties from a Type B evaluation of the maximum permissible
error of a tape (see Scale Bar A Priori Uncertainty).  The export is
therefore not by itself a complete uncertainty budget.</p>

<h3>Quantities</h3>

<p><b>Per-axis standard deviations (&sigma;_X, &sigma;_Y,
&sigma;_Z)</b><br>
Square roots of the diagonal of the transformed covariance matrix
(of the repaired matrix when it was repaired, see Technical notes).
These are the marginal standard deviations (1&sigma;) along each
coordinate axis.  The covariances between the axes (correlations)
are not captured by these three values alone.</p>

<p><b>Full covariance upper triangle</b><br>
The six unique elements of the symmetric 3&times;3 covariance matrix (the repaired matrix, see
below, when the check repaired it): C_XX, C_XY, C_XZ, C_YY, C_YZ, and C_ZZ. Their unit is the square
of the coordinate unit (m&sup2; in the local ENU CRS). They are written in scientific notation with
seven significant digits. For example, a standard deviation of 0.05&nbsp;mm gives a variance of 2.5&times;10<sup>&minus;9</sup>&nbsp;m&sup2;,
written as 2.500000e-09, which a fixed number of decimal places would round to zero. Together they
give the complete second-moment description of the estimated dispersion, including correlations.</p>

<p><b>Error ellipsoid (semi-axes and eigenvectors)</b><br>
The eigendecomposition of the covariance matrix yields three eigenvalues and their corresponding
eigenvectors. The square roots of the eigenvalues give the semi-axis lengths of the 3D error
ellipsoid, sorted from largest to smallest. The eigenvectors give the orientation of each ellipsoid
axis in the output CRS. The ellipsoid is a second-moment description of the estimated dispersion,
not a coverage region. A coverage ellipsoid follows only under a centred three-dimensional normal
distribution with this covariance, of full rank (no semi-axis of zero). It is obtained by
multiplying the semi-axes by the square root of the chi-square quantile with three degrees of
freedom. The unscaled ellipsoid would then contain about 19.9&nbsp;% of the probability, not the
68.3&nbsp;% of a one-dimensional standard deviation, and a 95&nbsp;% ellipsoid would need the factor
2.80, not 1.96. A covariance with a semi-axis of zero describes a degenerate distribution on a plane
or a line, whose coverage needs the chi-square quantile with degrees of freedom equal to its rank.
Because the covariance appears to be scaled by a variance factor estimated from the residuals, a
confidence region strictly needs the F distribution instead of the chi-square quantile. The two,
however, give practically the same factor with the redundancy of a bundle adjustment. The covariance
alone establishes neither that distribution nor a coverage probability (JCGM 100:2008, 6.2 and Annex
G, and VIM 2.36 and 2.37).<br>
<br>
For a perfectly isotropic uncertainty, all three semi-axes are equal (sphere). A strongly elongated
ellipsoid indicates that uncertainty is concentrated along one particular direction, often the depth
axis (along the viewing direction of the images).</p>

<h3>Coordinate reference system</h3>
<p><b>Local east-north-up coordinates:</b> available only for a georeferenced chunk (geographic,
projected, or geocentric CRS) with a physical scale, so not for the transform that Region Tools
records for a chunk without one. A georeferenced chunk also needs a placement on the Earth (i.e. a
translation and a rotation of its transform) as reference coordinates give it, not one that comes
from Region Tools. Local east-north-up coordinates are therefore not available for a chunk without
reference coordinates or scaled by Metashape scale bars alone, whatever its CRS. Metashape 2.3.2
gives such a chunk no translation and no rotation. When the option is ticked, coordinates and
covariances are expressed in a local ENU CRS in metres whose origin is the centre of the chunk
region. Consequently, the E, N, and U columns are offsets from the region centre. The headers carry
these units (for example <code>E [m, local ENU at region centre]</code>, <code>sigma_E [m]</code>,
and <code>C_EN [m^2]</code>). The first line of the file is a comment that records the origin in the
chunk CRS (longitude and latitude with nine decimal places in a geographic CRS). The offsets can
thus be placed again.</p>
<p><b>Without local east-north-up coordinates:</b> coordinates and
covariances are in
the world coordinates of the chunk transform, labelled X, Y, and Z.  In
a georeferenced chunk (geographic, projected, or geocentric CRS) these
are geocentric (Earth-centred, Earth-fixed, ECEF)
coordinates in metres.  They are not coordinates in a geographic or
projected chunk CRS (longitude and latitude, or a grid such as Universal Transverse Mercator (UTM)).
The units therefore read [m, geocentric ECEF], [m], and [m^2], and the
first line of the file is a comment that says so. The transform that
Region Tools records for a chunk without a physical scale leads to
arbitrary local coordinates also in a chunk with such a CRS.  These
coordinates are therefore labelled [chunk units] and [chunk units^2],
and the eigenvector components [world coordinates]. Every other
non-georeferenced chunk with such a CRS, without reference coordinates,
scaled by Metashape scale bars alone, or with the axes and the origin
that Region Tools gave it, has local world coordinates as well.  These coordinates
are labelled [m] and [m^2] with a physical scale and [chunk units] and
[chunk units^2] without one.  The eigenvector components of such a
chunk are labelled [world coordinates]. In a non-georeferenced chunk with a local
CRS or without a CRS the labels are [m] and [m^2] when the chunk has a
physical scale.  These labels also apply when the local CRS has another
unit, such as the foot.  Without a physical scale, the labels are
[chunk units] and [chunk units^2].  The eigenvector components are
labelled [CRS].</p>

<h3>Output</h3>
<p>Coordinates are written with the chosen number of coordinate decimal places (six by default), and
standard deviations, semi-axes, and eigenvector components with the chosen number of uncertainty
decimal places (eight by default). The save dialog offers a CSV file (.csv) and a text file (.txt),
which hold the same table. A file name without either extension gets .csv. The file is written in
UTF-8. The option ‘Delimiter and decimal separator’ chooses a comma with a decimal point
(1.25), a semicolon with a decimal comma (1,25, and 2,500000e-09 in scientific notation), or a tab
with a decimal point. The semicolon with the decimal comma suits Excel with regional settings that
use the decimal comma, for example German or French. A comment line at the top of the file keeps its
text unchanged. The completion message and the Console name the file that was written and its
companion file. When the export fails, for example because the file is open in another program, a
message names the error. Both files are written together or not at all: when one of them cannot be
written, the tool tries to give both files their earlier content back. A file that does not get it
back is named in the message, with the backup file that keeps that content. When a folder has the
name of either file, neither file is written and the folder stays as it is.</p>
<p>Every tie point of the chunk that is not exported is written, with the reason, to a companion
file next to the exported file. The companion file is named after the exported file with <code>_notExported</code>
added before the extension (for example <code>tiePointCovarianceExport_site_Chunk1_notExported.csv</code>,
or <code>tiePointCovarianceExport_site_Chunk1_notExported.txt</code> next to a text file). The
companion file has the same delimiter and decimal separator as the exported file. Each tie point in
it is an invalid tie point, a tie point without a covariance, a point at infinity, or a tie point
whose covariance matrix fails the check described below. For a tie point whose covariance matrix
fails the check, the companion file states the reason that the check gives. The exported file itself
thus holds numbers only. The companion file is written on every export, also when it lists no tie
point, so that it never keeps the tie points of an earlier export to the same file. The save dialog
asks only about the chosen file. The tool therefore asks before it replaces another existing file,
such as a file with the name of the companion file that an earlier export to the same file did not
write.</p>

<h3>Technical notes</h3>
<p>The following notes give the technical details of the export:</p>
<ul>
<li>The covariance is propagated with the linear part
J&nbsp;=&nbsp;s&nbsp;R of the transform,
C<sub>out</sub>&nbsp;=&nbsp;J&nbsp;C&nbsp;J<sup>T</sup>.  Here,
R&nbsp;=&nbsp;<code>T.rotation()</code> and
s&nbsp;=&nbsp;<code>T.scale()</code> are those of the chunk transform T
(combined with the conversion to the local ENU CRS when it is
applied).  The chunk scale therefore
enters the covariance squared and gives it the units of the output
coordinates.  The chunk transform and the conversion to the local ENU
CRS are taken as fixed:
the uncertainty of the transform itself, and its correlation with the
points, is not added.</li>
<li>Before the decomposition, each transformed matrix is checked: it
must be finite, symmetric, and positive semi-definite.  The signed
eigenvalues come from a symmetric eigenvalue solver, because the
absolute value of a negative variance, or the singular values of an
indefinite matrix, would give plausible but meaningless standard
deviations and semi-axes.  Asymmetry and negative eigenvalues smaller
than one millionth of the largest element in magnitude are attributed
to rounding (next item).  This tolerance is a rule of the export, not proof that rounding caused the discrepancy.  A point whose matrix fails the check is skipped,
counted in the completion message, named in the Console (the first
twenty, the others counted), and written with the reason to the
companion file.</li>
<li>Every matrix that passes the check is replaced by its symmetric part, because the transformed
matrix is seldom exactly symmetric in floating point. A symmetric part with a negative eigenvalue
attributed to rounding is repaired: it is decomposed with the cyclic Jacobi method, and the negative
eigenvalues are set to zero. Every column (the standard deviations, the covariance elements, the
semi-axes, and their directions) then describes the matrix that the remaining eigenvalues and
eigenvectors rebuild. The Console counts the repaired matrices. The Jacobi method also decides
whether an eigenvalue is negative when the smallest eigenvalue in closed form is below one millionth
of the largest element in magnitude. In this way, neither the unit of the matrix nor the rounding
near a repeated eigenvalue decides whether an eigenvalue is negative. Any other symmetric part,
which is unambiguously positive definite, is decomposed with the singular value decomposition (SVD),
whose singular values and left singular vectors are then its eigenvalues and eigenvectors. The SVD
is not used for a matrix with a negative eigenvalue: for eigenvalues of opposite sign and equal
magnitude (+5&times;10<sup>&minus;7</sup> and &minus;5&times;10<sup>&minus;7</sup>) its singular
vectors need not be eigenvectors, and it would give two small semi-axes instead of one.</li>
<li>Points without covariance (a covariance matrix that is <code>None</code> or a matrix of
zeros), that are flagged as invalid, or that lie at infinity are skipped. They are counted in the
completion message and in the Console summary, and written with the reason to the companion
file.</li>
</ul>
"""


class AboutDialog(QtWidgets.QDialog):
    """Scrollable About / Help dialog."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("About: Tie Point Covariance Exporter")
        self.setWindowFlags(
            self.windowFlags()
            & ~QtCore.Qt.WindowContextHelpButtonHint)
        self.setMinimumSize(520, 480)

        layout = QtWidgets.QVBoxLayout(self)

        textBrowser = QtWidgets.QTextBrowser()
        textBrowser.setOpenExternalLinks(True)
        textBrowser.setHtml(_ABOUT_HTML)
        layout.addWidget(textBrowser)

        closeButton = QtWidgets.QPushButton("Close")
        closeButton.clicked.connect(self.accept)
        layout.addWidget(closeButton)

    @staticmethod
    def _BuildHelpHtml():
        return _ABOUT_HTML


# ===================================================================
# Menu registration
# ===================================================================

_MENU_LABEL = "MetaFOCUS/SfM Process Diagnostics/Tie Point Covariance Exporter"

# Set to True to print the first three covariance matrices and
# their decompositions to the Console for debugging.
_PRINT_COVARIANCE_DIAGNOSTICS = False
_ICON_NAME = "tie_point_covariance_exporter_32.png"


def _ShowCovarianceDialog():
    document = Metashape.app.document
    if not document or not document.chunk:
        QtWidgets.QMessageBox.information(
            QtWidgets.QApplication.activeWindow(), _TOOL_NAME,
            "Open a project with at least one chunk first.")
        return
    TiePointCovarianceDialog(
        QtWidgets.QApplication.activeWindow()).exec_()


def RegisterMenuItem():
    """Register the menu item.  Called by the MetaFOCUS loader."""
    try:
        Metashape.app.removeMenuItem(_MENU_LABEL)
    except Exception as suppressedError:
        ReportSuppressedError("tiePointCovarianceExporter.RegisterMenuItem", suppressedError)
    iconPath = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "icons", _ICON_NAME)
    if os.path.exists(iconPath):
        Metashape.app.addMenuItem(
            _MENU_LABEL, _ShowCovarianceDialog, icon=iconPath)
    else:
        Metashape.app.addMenuItem(_MENU_LABEL, _ShowCovarianceDialog)
