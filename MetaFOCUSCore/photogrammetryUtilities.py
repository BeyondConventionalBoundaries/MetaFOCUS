"""
Photogrammetry Utilities for Agisoft Metashape Professional
============================================================

Shared functions of the MetaFOCUS toolbox.  The GSD engine serves the
Image Footprint Generator, the Image Parameter Exporter, and the GSD
Value Exporter, the observation and unknown counts serve the Interior
Orientation Viewer and the Gradual Selection Filter, and every module
reports tolerated errors through ReportSuppressedError.

Author:      Geert J. Verhoeven (ORCID: 0000-0003-4825-9604)
Version:     1.0.0 (2026-11-18)
Date:        2026-06-12
Licence:     GPL-3.0-or-later WITH GPL-3.0-linking-exception
Contact:     geert.verhoeven@univie.ac.at
Requires:    Agisoft Metashape Professional 2.x+
Developed for: Agisoft Metashape Professional 2.3.2 (tested offline, see README.md)
             PySide2 (bundled with Metashape)

Contents:
    - 3D vector operations
    - Least-squares plane fitting
    - Bounding volume hierarchy (BVH) construction and ray-triangle
      intersection (with barycentric coordinates for the normal
      interpolation)
    - N-ring vertex normal averaging
    - Tie-point track map and valid projection extraction
    - GSD at sampled pixel positions with the auxiliary-ray
      construction on the tangent plane of the surface hit:
        * plane mode: the fitted plane is its own tangent plane
        * triangular mesh mode: tangent plane from interpolated,
          N-ring-averaged vertex normals
        * raster DEM mode: tangent plane from the raster DEM heights
          one cell either side of the hit
    - GSD statistics (percentiles, coverage)
    - Chunk scale, the physical chunk scale (the transform that Region
      Tools records in the chunk metadata has an arbitrary scale), the
      unit texts of the GSD tools, kinds of CRS, and coordinate
      transformations
    - Raster DEM misses by reason and their Console texts
    - Images, keyframes, master images, and the rotation angle names
      of the chunk
    - Calibrations: the calibration of an interior orientation group
      or of an image, and all lens distortion coefficients and the
      terms b1 and b2 set to zero
    - Observation and unknown counts of the bundle adjustment, the
      datum defect, and the redundancy (one participation rule for
      every count)
    - Homogeneous points and covariance propagation
    - Reference panel data extraction (via exportReference CSV)
    - Text helpers (file name parts, counted nouns, exception
      descriptions, the Console restore command of a transform), the
      blocks that the tools print to the Console pane, and
      ReportSuppressedError
    - The delimiter and the decimal separator of the CSV exports: the
      one choice of the four exporters and the decimal comma of the
      numbers of their data rows

Notes:
    - GSD: the photosite pitch projected onto the surface.  At every
      sampled pixel position, four auxiliary rays through the midpoints
      of the edges of the corresponding photosite (+-1/2 along the row
      and the column in pixel coordinates) are intersected with the
      local tangent plane at the central ray's surface hit.  The two
      central differences are the projected row and column steps a and
      b, with the lengths GSD_row = |a| and GSD_column = |b| and the
      angle phi between them, and the value of that position is the
      area-equivalent GSD sqrt(|a x b|) = sqrt(GSD_row * GSD_column *
      sin(phi)), the side of the square with the area that the two
      steps span.  It equals the geometric
      mean sqrt(GSD_row * GSD_column)
      when the steps are perpendicular.  Unlike the geometric mean, it
      depends only on the area that the two steps span, not on their
      directions, so that, to first order, the value of a surface point
      does not change when the camera is rolled about its camera axis,
      provided that the camera model, apart from its affinity and
      non-orthogonality, is symmetric about that axis.  The camera model of a
      frame or fisheye camera is symmetric in this sense with the decentring
      distortion coefficients set to zero, without additional corrections,
      and without rolling-shutter compensation.
    - Plane mode uses the normal of the plane fitted to the tie
      points of each image.
    - Triangular mesh mode uses vertex normals averaged over an
      N-ring neighbourhood (default 3 rings), interpolated via
      barycentric coordinates at the hit point.  This provides a
      smoothly varying surface normal that is robust against
      individual facet noise.
    - Raster DEM mode works in local east-north-up coordinates in a
      georeferenced chunk, otherwise in the world coordinates of the
      chunk (see DemSurface), and queries the raster DEM through its
      own projection, reading heights only
      through the documented Elevation.altitude(): each ray is
      followed in steps of at most one raster DEM cell horizontally (in
      any direction, so
      upward rays meet rising terrain) to the first crossing at the
      cell size of the raster DEM (see SolveRayDemIntersection), that
      crossing is refined until the height difference is below one
      hundredth of the raster DEM cell size, and the normal follows
      from the raster DEM heights one cell either side of the hit.
      The missed rays are counted by reason
      (DemSurface.missCountsByReason).  No BVH is needed.
    - Every mode rejects rays beyond the grazing threshold
      (GRAZING_COSINE_THRESHOLD, cos(theta) < 0.1), and the sampling
      grid is centred in the frame (SamplePixelGrid).
    - The counts follow one participation rule: an image takes part
      when it is enabled and oriented (keyframes never do), a valid
      tie point when its track has projections on at least two such
      images, a marker (never a fiducial mark) when it has valid
      projections on two such images, or on one plus an enabled
      reference location.  The redundancy counts
      the unknowns of a camera station, an interior orientation group,
      and an image, and the priors of the images of a station, only when
      the observations of the estimate involve them
      (ComputeRedundancyBreakdown).  The redundancy that follows from these
      counts is an estimate: it assumes one connected block of images,
      no rank deficiency beyond the datum defect and no further
      constraints among the counted unknowns, and control points that
      are not collinear.
"""

import Metashape
import re
import math
import csv
import os
import tempfile
import functools
import array


# ===================================================================
# 3-vector helpers
# ===================================================================

def VectorSubtract(vectorA, vectorB):
    return (vectorA[0]-vectorB[0], vectorA[1]-vectorB[1], vectorA[2]-vectorB[2])

def VectorAdd(vectorA, vectorB):
    return (vectorA[0]+vectorB[0], vectorA[1]+vectorB[1], vectorA[2]+vectorB[2])

def VectorScale(vector, scalar):
    return (vector[0]*scalar, vector[1]*scalar, vector[2]*scalar)

def VectorDot(vectorA, vectorB):
    return vectorA[0]*vectorB[0] + vectorA[1]*vectorB[1] + vectorA[2]*vectorB[2]

def VectorCross(vectorA, vectorB):
    return (vectorA[1]*vectorB[2] - vectorA[2]*vectorB[1],
            vectorA[2]*vectorB[0] - vectorA[0]*vectorB[2],
            vectorA[0]*vectorB[1] - vectorA[1]*vectorB[0])

def VectorLength(vector):
    return math.sqrt(vector[0]**2 + vector[1]**2 + vector[2]**2)

def VectorNormalize(vector):
    length = VectorLength(vector)
    if length < 1e-30: return (0.0, 0.0, 0.0)
    return (vector[0]/length, vector[1]/length, vector[2]/length)


def SafeReciprocal(value):
    """Reciprocal of *value*, or 1e30 when *value* is (almost) zero.
    Used for the inverse ray direction of the bounding-box slab test,
    where a zero direction component means the ray never leaves the
    slab of that axis."""
    return 1.0 / value if abs(value) > 1e-30 else 1e30

def VectorFromMetashape(metashapeVector):
    """Convert a Metashape.Vector to a plain 3-tuple, or None."""
    if metashapeVector is None: return None
    try: return (metashapeVector.x, metashapeVector.y, metashapeVector.z)
    except AttributeError: return None


# ===================================================================
# Plane fitting
# ===================================================================

def _ComputeEigenvaluesSymmetric3x3(covarianceXX, covarianceXY, covarianceXZ, covarianceYY, covarianceYZ, covarianceZZ):
    """Eigenvalues of a real symmetric 3 x 3 matrix, sorted smallest
    first, in closed form.  The elements are first scaled exactly by a
    power of two, so that the largest lies between 0.5 and 1: the
    shortcut for a diagonal matrix and the rounding then do not depend
    on the unit of the matrix."""
    elements = (covarianceXX, covarianceXY, covarianceXZ, covarianceYY, covarianceYZ, covarianceZZ)
    if not all(math.isfinite(element) for element in elements):
        return _ComputeEigenvaluesOfScaledSymmetric3x3(*elements)
    largestMagnitude = max(abs(element) for element in elements)
    if largestMagnitude == 0.0:
        return [0.0, 0.0, 0.0]
    exponent = math.frexp(largestMagnitude)[1]
    eigenvalues = _ComputeEigenvaluesOfScaledSymmetric3x3(*[math.ldexp(element, -exponent) for element in elements])
    return [math.ldexp(eigenvalue, exponent) for eigenvalue in eigenvalues]

def _ComputeEigenvaluesOfScaledSymmetric3x3(covarianceXX, covarianceXY, covarianceXZ, covarianceYY, covarianceYZ,
                                            covarianceZZ):
    """_ComputeEigenvaluesSymmetric3x3 for a matrix whose largest
    element in magnitude lies between 0.5 and 1."""
    offDiagonalSquareSum = covarianceXY*covarianceXY + covarianceXZ*covarianceXZ + covarianceYZ*covarianceYZ
    if offDiagonalSquareSum < 1e-30: return sorted([covarianceXX, covarianceYY, covarianceZZ])
    meanEigenvalue = (covarianceXX+covarianceYY+covarianceZZ) / 3.0
    shiftedXX, shiftedYY, shiftedZZ = covarianceXX-meanEigenvalue, covarianceYY-meanEigenvalue, covarianceZZ-meanEigenvalue
    shiftedMatrixNormSquared = shiftedXX**2 + shiftedYY**2 + shiftedZZ**2 + 2.0*offDiagonalSquareSum
    scaleFactor = math.sqrt(shiftedMatrixNormSquared / 6.0)
    inverseScale = 1.0 / scaleFactor
    b00 = shiftedXX*inverseScale; b01 = covarianceXY*inverseScale; b02 = covarianceXZ*inverseScale
    b11 = shiftedYY*inverseScale; b12 = covarianceYZ*inverseScale; b22 = shiftedZZ*inverseScale
    determinant = (b00*(b11*b22 - b12*b12) - b01*(b01*b22 - b12*b02)
                   + b02*(b01*b12 - b11*b02))
    halfDeterminant = max(-1.0, min(1.0, determinant / 2.0))
    phi = math.acos(halfDeterminant) / 3.0
    lambda0 = meanEigenvalue + 2.0*scaleFactor*math.cos(phi + 2.0*math.pi/3.0)
    lambda2 = meanEigenvalue + 2.0*scaleFactor*math.cos(phi)
    lambda1 = 3.0*meanEigenvalue - lambda0 - lambda2
    return sorted([lambda0, lambda1, lambda2])

def _ComputeEigenvectorSymmetric3x3(covarianceXX, covarianceXY, covarianceXZ, covarianceYY, covarianceYZ, covarianceZZ, eigenvalue):
    row0 = (covarianceXX-eigenvalue, covarianceXY, covarianceXZ)
    row1 = (covarianceXY, covarianceYY-eigenvalue, covarianceYZ)
    row2 = (covarianceXZ, covarianceYZ, covarianceZZ-eigenvalue)
    cross01 = VectorCross(row0, row1)
    cross02 = VectorCross(row0, row2)
    cross12 = VectorCross(row1, row2)
    lengthSquared01 = VectorDot(cross01, cross01)
    lengthSquared02 = VectorDot(cross02, cross02)
    lengthSquared12 = VectorDot(cross12, cross12)
    if lengthSquared01 >= lengthSquared02 and lengthSquared01 >= lengthSquared12:
        return VectorNormalize(cross01)
    elif lengthSquared02 >= lengthSquared12:
        return VectorNormalize(cross02)
    else:
        return VectorNormalize(cross12)

def SmallestEigenvalueOfSymmetric3x3(symmetricElements):
    """Smallest eigenvalue of a real symmetric 3 x 3 matrix
    (*symmetricElements*, nested lists row by row), in closed form."""
    return _ComputeEigenvaluesSymmetric3x3(
        symmetricElements[0][0], symmetricElements[0][1], symmetricElements[0][2],
        symmetricElements[1][1], symmetricElements[1][2], symmetricElements[2][2])[0]


# Largest number of sweeps of the cyclic Jacobi method: a symmetric 3 x 3
# matrix converges to rounding in a few sweeps, so the limit only stops
# a matrix that is not a number.
_JACOBI_MAXIMUM_SWEEPS = 50


def SymmetricEigendecomposition3x3(symmetricElements):
    """Eigenvalues and orthonormal eigenvectors of a real symmetric 3 x 3
    matrix (*symmetricElements*, nested lists row by row), by cyclic
    Jacobi rotations: a list of three (eigenvalue, eigenvector) pairs in
    no particular order, each eigenvector a 3-tuple of unit length.  The
    rotations keep the eigenvectors orthonormal also for repeated
    eigenvalues and for eigenvalues of opposite sign and equal
    magnitude, where the singular value decomposition loses the sign
    and its singular vectors need not be eigenvectors (eigenvalues +5e-7
    and -5e-7 give, with the SVD, two small semi-axes along the
    coordinate axes)."""
    matrix = [[float(symmetricElements[rowIndex][columnIndex]) for columnIndex in range(3)]
              for rowIndex in range(3)]
    vectors = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
    for _ in range(_JACOBI_MAXIMUM_SWEEPS):
        offDiagonal = matrix[0][1] ** 2 + matrix[0][2] ** 2 + matrix[1][2] ** 2
        if offDiagonal == 0.0 or not math.isfinite(offDiagonal):
            break
        for first, second in ((0, 1), (0, 2), (1, 2)):
            element = matrix[first][second]
            if element == 0.0:
                continue
            # The rotation that zeroes element (first, second): tangent
            # of the rotation angle, the smaller root of t^2 + 2 theta t
            # - 1 = 0, for accuracy.
            theta = (matrix[second][second] - matrix[first][first]) / (2.0 * element)
            tangent = (1.0 if theta >= 0.0 else -1.0) / (abs(theta) + math.sqrt(theta * theta + 1.0))
            cosine = 1.0 / math.sqrt(tangent * tangent + 1.0)
            sine = tangent * cosine
            for index in range(3):
                firstValue, secondValue = matrix[index][first], matrix[index][second]
                matrix[index][first] = cosine * firstValue - sine * secondValue
                matrix[index][second] = sine * firstValue + cosine * secondValue
            for index in range(3):
                firstValue, secondValue = matrix[first][index], matrix[second][index]
                matrix[first][index] = cosine * firstValue - sine * secondValue
                matrix[second][index] = sine * firstValue + cosine * secondValue
            matrix[first][second] = matrix[second][first] = 0.0
            for index in range(3):
                firstValue, secondValue = vectors[index][first], vectors[index][second]
                vectors[index][first] = cosine * firstValue - sine * secondValue
                vectors[index][second] = sine * firstValue + cosine * secondValue
    return [(matrix[index][index], (vectors[0][index], vectors[1][index], vectors[2][index]))
            for index in range(3)]


# Smallest gap between the smallest and the middle eigenvalue of the
# scatter matrix, relative to the largest, at which the points still
# define a plane normal.  Points on one line have a gap of zero, which
# the closed-form eigenvalues resolve only to about 2e-8 (rounding near
# a repeated eigenvalue), and there the cross products of the
# eigenvector construction return a zero or an arbitrary normal.  The
# tie points of an image or the vertices of a footprint, spread over an
# area with an aspect ratio below about 1000:1, lie far above this gap.
_PLANE_MINIMUM_RELATIVE_EIGENVALUE_GAP = 1e-6

# Smallest spread of the points across their line of best fit, relative
# to the magnitude of their coordinates, below which that spread is
# rounding noise (points that coincide up to rounding).
_PLANE_MINIMUM_RELATIVE_SPREAD = 1e-12


def FitPlaneToPoints(pointTuples):
    """Fit a least-squares plane through a list of 3-tuples.
    Returns (centroid, unit normal) as tuples, the normal being the
    eigenvector of the smallest eigenvalue of the scatter matrix.

    Three points are necessary but not sufficient: the normal is None
    when the points do not define a plane, that is, fewer than three
    points, points that coincide or lie on one line (the gap between
    the smallest and the middle eigenvalue is below
    _PLANE_MINIMUM_RELATIVE_EIGENVALUE_GAP of the largest), or a
    coordinate that is not finite (then the centroid is None as
    well).  A fitted plane does not show that the scene is planar."""
    pointCount = len(pointTuples)
    if pointCount == 0:
        return None, None
    sumX = sumY = sumZ = 0.0
    for point in pointTuples:
        sumX += point[0]; sumY += point[1]; sumZ += point[2]
    if not (math.isfinite(sumX) and math.isfinite(sumY) and math.isfinite(sumZ)):
        return None, None
    centroidX = sumX / pointCount
    centroidY = sumY / pointCount
    centroidZ = sumZ / pointCount
    centroid = (centroidX, centroidY, centroidZ)
    if pointCount < 3:
        return centroid, None
    scatterXX = scatterXY = scatterXZ = scatterYY = scatterYZ = scatterZZ = 0.0
    for point in pointTuples:
        deltaX = point[0] - centroidX
        deltaY = point[1] - centroidY
        deltaZ = point[2] - centroidZ
        scatterXX += deltaX*deltaX; scatterXY += deltaX*deltaY; scatterXZ += deltaX*deltaZ
        scatterYY += deltaY*deltaY; scatterYZ += deltaY*deltaZ; scatterZZ += deltaZ*deltaZ
    smallestEigenvalue, middleEigenvalue, largestEigenvalue = _ComputeEigenvaluesSymmetric3x3(
        scatterXX, scatterXY, scatterXZ, scatterYY, scatterYZ, scatterZZ)
    if not largestEigenvalue > 0.0:
        return centroid, None
    if middleEigenvalue - smallestEigenvalue <= _PLANE_MINIMUM_RELATIVE_EIGENVALUE_GAP * largestEigenvalue:
        return centroid, None
    coordinateMagnitude = (max(abs(centroidX), abs(centroidY), abs(centroidZ))
                           + math.sqrt(largestEigenvalue / pointCount))
    if math.sqrt(max(0.0, middleEigenvalue) / pointCount) <= _PLANE_MINIMUM_RELATIVE_SPREAD * coordinateMagnitude:
        return centroid, None
    normalVector = _ComputeEigenvectorSymmetric3x3(
        scatterXX, scatterXY, scatterXZ, scatterYY, scatterYZ, scatterZZ, smallestEigenvalue)
    if not VectorLength(normalVector) > 0.5:
        return centroid, None
    return centroid, normalVector

def IntersectRayWithPlane(rayOrigin, rayDirection, planePoint, planeNormal):
    """Returns the ray parameter t at the intersection, or None."""
    denominator = VectorDot(rayDirection, planeNormal)
    if abs(denominator) < 1e-12: return None
    return VectorDot(VectorSubtract(planePoint, rayOrigin), planeNormal) / denominator


# ===================================================================
# Auxiliary-ray GSD on a tangent plane
# ===================================================================

def ComputeProjectedSamplingSteps(originTuple, auxiliaryRayDirections,
                                  planePointTuple, planeNormalTuple,
                                  scaleFactor, auxiliaryRayOrigins=None):
    """Projected row and column steps of a sampled pixel position: the
    vectors a and b between the hits of opposite auxiliary rays on the
    local tangent plane.

    The four *auxiliaryRayDirections* are the auxiliary viewing rays
    through the pixel coordinates +-1/2 from the sampled position along
    the row and the column (left, right, up, down), that is, through the
    midpoints of the edges of the corresponding photosite.  Each is
    intersected with the local tangent plane defined by *planePointTuple*
    and *planeNormalTuple*, and the two central differences are the
    steps: the photosite pitch projected onto the tangent plane along
    the image row (a, from the left to the right hit) and along the
    image column (b, from the upper to the lower hit).  All arithmetic is
    camera-centred (relative to *originTuple*), so no large-coordinate
    cancellation can occur.  With *auxiliaryRayOrigins*, each auxiliary
    ray starts at its own origin, as the rays of an image with
    rolling-shutter compensation do (ImageRays), and otherwise all start
    at *originTuple*.

    Returns (rowStep, columnStep) as 3-tuples in object units (after
    applying *scaleFactor*, the chunk scale), or (None, None) for a
    degenerate or back-facing configuration."""
    relativePlanePoint = VectorSubtract(planePointTuple, originTuple)
    numerator = VectorDot(planeNormalTuple, relativePlanePoint)
    relativeOrigins = ([VectorSubtract(origin, originTuple) for origin in auxiliaryRayOrigins]
                       if auxiliaryRayOrigins is not None else [(0.0, 0.0, 0.0)] * len(auxiliaryRayDirections))
    relativeHitPoints = []
    for direction, relativeOrigin in zip(auxiliaryRayDirections, relativeOrigins):
        denominator = VectorDot(planeNormalTuple, direction)
        if abs(denominator) < 1e-15:
            return (None, None)
        rayParameter = (numerator - VectorDot(planeNormalTuple, relativeOrigin)) / denominator
        if rayParameter <= 0:
            return (None, None)
        relativeHitPoints.append(VectorAdd(relativeOrigin, VectorScale(direction, rayParameter)))
    rowStep = VectorScale(VectorSubtract(relativeHitPoints[1], relativeHitPoints[0]), scaleFactor)
    columnStep = VectorScale(VectorSubtract(relativeHitPoints[3], relativeHitPoints[2]), scaleFactor)
    return (rowStep, columnStep)


def ComputeDirectionalGsd(originTuple, auxiliaryRayDirections,
                          planePointTuple, planeNormalTuple,
                          scaleFactor):
    """Ground sampling distances along the image row and column
    directions: the lengths of the projected row and column steps (see
    ComputeProjectedSamplingSteps).

    Returns (gsdRow, gsdColumn) in object units (after applying
    *scaleFactor*, the chunk scale), or (None, None) for a degenerate or
    back-facing configuration."""
    rowStep, columnStep = ComputeProjectedSamplingSteps(
        originTuple, auxiliaryRayDirections, planePointTuple, planeNormalTuple, scaleFactor)
    if rowStep is None:
        return (None, None)
    return (VectorLength(rowStep), VectorLength(columnStep))


def GsdFromProjectedSamplingSteps(rowStep, columnStep):
    """The GSD measures of the projected row step a and column step b
    (see ComputeProjectedSamplingSteps): gsdRow = |a|, gsdColumn = |b|,
    projectedStepAngleDegrees, the angle phi between a and b (0 to 180
    degrees), gsdAreaEquivalent = sqrt(|a x b|) = sqrt(gsdRow * gsdColumn
    * sin(phi)), the side of the square with the area that the two
    steps span, and gsdGeometricMean =
    sqrt(gsdRow * gsdColumn).

    The area-equivalent GSD is the GSD of MetaFOCUS: it depends only on
    the area that the two steps span, not on their directions, whereas
    the geometric mean also depends on
    the directions of the image rows and columns on the surface and
    exceeds it by the factor 1/sqrt(sin(phi)) when the steps are not
    perpendicular.  Therefore, to first order, the area-equivalent GSD
    of a surface point does not change when the camera is rolled about
    its camera axis, provided that the camera model, apart from its
    affinity and non-orthogonality, is symmetric about that axis.  The
    camera model of a frame or fisheye camera is symmetric in this sense
    with the decentring distortion coefficients set to zero, without
    additional corrections, and without rolling-shutter compensation.  A roll
    moves the image of the point
    about the principal point, and a decentring distortion maps the new
    position with another area per sample (for example, 5.8 % more after
    a roll of 180 degrees with p1 = 0.01 and p2 = 0.02), whereas the affinity and
    non-orthogonality act on the whole image as one linear map, which
    changes all areas by the same factor.  The
    angle comes from atan2(|a x b|, a . b), which stays accurate near 0,
    90, and 180 degrees."""
    crossProduct = VectorCross(rowStep, columnStep)
    spannedArea = VectorLength(crossProduct)
    gsdRow = VectorLength(rowStep)
    gsdColumn = VectorLength(columnStep)
    return dict(gsdRow=gsdRow, gsdColumn=gsdColumn,
                gsdGeometricMean=math.sqrt(gsdRow * gsdColumn),
                gsdAreaEquivalent=math.sqrt(spannedArea),
                projectedStepAngleDegrees=math.degrees(math.atan2(
                    spannedArea, VectorDot(rowStep, columnStep))))


# ===================================================================
# Bounding volume hierarchy (BVH): ray-triangle intersection
# ===================================================================

# Relative tolerances of the ray-triangle intersections, so that they do
# not depend on the unit of the chunk coordinates, which is arbitrary
# until the chunk is scaled.  The determinant edge1 . (direction x
# edge2) is at most |edge1| |edge2| |direction|, and a ray whose
# determinant is below _RAY_TRIANGLE_PARALLEL_TOLERANCE of that bound is
# parallel to the triangle up to rounding.  A hit nearer to the origin
# than _RAY_TRIANGLE_ORIGIN_TOLERANCE of the distance from the origin to
# the first vertex is the origin itself.
_RAY_TRIANGLE_PARALLEL_TOLERANCE = 1e-12
_RAY_TRIANGLE_ORIGIN_TOLERANCE = 1e-8


def _IntersectRayWithTriangleDetailed(origin, direction,
                                      vertex0, vertex1, vertex2):
    """Moeller-Trumbore intersection: (ray parameter t, barycentric u,
    barycentric v) or None.  The hit point is origin + t * direction =
    (1 - u - v) * vertex0 + u * vertex1 + v * vertex2, so t is a
    distance only for a unit direction.  The tests for a parallel ray
    and for a hit at the origin are relative
    (_RAY_TRIANGLE_PARALLEL_TOLERANCE, _RAY_TRIANGLE_ORIGIN_TOLERANCE), so
    the result does not change when the scene is rescaled.  Every test
    is written so that it passes only for a number, so a ray or a
    triangle that is not finite gives no hit."""
    edge1 = VectorSubtract(vertex1, vertex0)
    edge2 = VectorSubtract(vertex2, vertex0)
    hVector = VectorCross(direction, edge2)
    determinant = VectorDot(edge1, hVector)
    determinantBoundSquared = VectorDot(edge1, edge1) * VectorDot(edge2, edge2) * VectorDot(direction, direction)
    if not determinant * determinant > (_RAY_TRIANGLE_PARALLEL_TOLERANCE ** 2) * determinantBoundSquared:
        return None
    inverseDeterminant = 1.0 / determinant
    sVector = VectorSubtract(origin, vertex0)
    barycentricU = inverseDeterminant * VectorDot(sVector, hVector)
    if not 0.0 <= barycentricU <= 1.0:
        return None
    qVector = VectorCross(sVector, edge1)
    barycentricV = inverseDeterminant * VectorDot(direction, qVector)
    if not (barycentricV >= 0.0 and barycentricU + barycentricV <= 1.0):
        return None
    hitParameter = inverseDeterminant * VectorDot(edge2, qVector)
    if not (hitParameter > 0.0 and math.isfinite(hitParameter)):
        return None
    if not (hitParameter * hitParameter * VectorDot(direction, direction)
            > (_RAY_TRIANGLE_ORIGIN_TOLERANCE ** 2) * VectorDot(sVector, sVector)):
        return None
    return (hitParameter, barycentricU, barycentricV)


def _IntersectRayWithTriangle(origin, direction, vertex0, vertex1, vertex2):
    """Moeller-Trumbore intersection: the ray parameter t > 0 of the
    hit (origin + t * direction, a distance only for a unit
    direction), or None, with the relative tests of
    _IntersectRayWithTriangleDetailed."""
    intersection = _IntersectRayWithTriangleDetailed(origin, direction, vertex0, vertex1, vertex2)
    return None if intersection is None else intersection[0]


# ===================================================================
# Bounding volume hierarchy (BVH): tree structure
# ===================================================================

class _BvhNode:
    # The root node of BuildMeshBvh also holds the vertex normals of the
    # triangular mesh (vertexNormals), which the other nodes leave None.
    __slots__ = ("minimumBounds", "maximumBounds", "leftChild", "rightChild",
                 "triangleFaces", "vertexNormals")
    def __init__(self):
        self.minimumBounds = self.maximumBounds = None
        self.leftChild = self.rightChild = None
        self.triangleFaces = None
        self.vertexNormals = None

def _ComputeAxisAlignedBounds(triangleList):
    minimumBounds = [1e30, 1e30, 1e30]
    maximumBounds = [-1e30, -1e30, -1e30]
    for triangleData in triangleList:
        # triangleData: (coord0, coord1, coord2, idx0, idx1, idx2)
        for vertex in (triangleData[0], triangleData[1], triangleData[2]):
            for axis in range(3):
                if vertex[axis] < minimumBounds[axis]:
                    minimumBounds[axis] = vertex[axis]
                if vertex[axis] > maximumBounds[axis]:
                    maximumBounds[axis] = vertex[axis]
    return tuple(minimumBounds), tuple(maximumBounds)

def _BuildBvhRecursive(triangleList, depth=0):
    node = _BvhNode()
    node.minimumBounds, node.maximumBounds = _ComputeAxisAlignedBounds(triangleList)
    if len(triangleList) <= 32 or depth >= 25:
        node.triangleFaces = triangleList
        return node
    extents = [node.maximumBounds[index] - node.minimumBounds[index] for index in range(3)]
    splitAxis = extents.index(max(extents))
    triangleList.sort(key=lambda triangleData: (
        triangleData[0][splitAxis] + triangleData[1][splitAxis] + triangleData[2][splitAxis]))
    splitIndex = len(triangleList) // 2
    node.leftChild = _BuildBvhRecursive(triangleList[:splitIndex], depth + 1)
    node.rightChild = _BuildBvhRecursive(triangleList[splitIndex:], depth + 1)
    return node


# ===================================================================
# Bounding volume hierarchy (BVH): queries
# ===================================================================

# The exit parameter of the bounding-box test is widened by 2 gamma(3),
# the bound on the relative rounding error of (bound - origin) times the
# reciprocal, so that rounding alone never rejects a ray that grazes a
# face, an edge, or a corner of a box (Ize 2013, Robust BVH ray
# traversal).
_SLAB_EXIT_WIDENING = 1.0 + 2.0 * (3.0 * 2.0 ** -53) / (1.0 - 3.0 * 2.0 ** -53)


def _RayIntersectsAxisAlignedBounds(rayOrigin, rayDirection, inverseDirection,
                                    minimumBounds, maximumBounds):
    """True when the ray rayOrigin + t * rayDirection, t >= 0, meets the
    closed axis-aligned box.  A direction component of (almost) zero,
    the components for which SafeReciprocal returns its finite stand-in,
    means that the ray runs parallel to the slab of that axis: it meets
    the box only when its origin lies within that slab, bounds included,
    and that axis then sets no limit on t.  This is decided from the
    direction itself, because multiplying by the stand-in turns an
    origin on the upper bound into an exit at t = 0, which would reject
    genuine hits of axis-parallel rays."""
    latestEntryParameter = 0.0
    earliestExitParameter = math.inf
    for axis in range(3):
        if abs(rayDirection[axis]) <= 1e-30:
            if (rayOrigin[axis] < minimumBounds[axis] or
                    rayOrigin[axis] > maximumBounds[axis]):
                return False
            continue
        entryParameter = (minimumBounds[axis] - rayOrigin[axis]) * inverseDirection[axis]
        exitParameter = (maximumBounds[axis] - rayOrigin[axis]) * inverseDirection[axis]
        if entryParameter > exitParameter: entryParameter, exitParameter = exitParameter, entryParameter
        if entryParameter > latestEntryParameter: latestEntryParameter = entryParameter
        if exitParameter < earliestExitParameter: earliestExitParameter = exitParameter
        if latestEntryParameter > earliestExitParameter * _SLAB_EXIT_WIDENING: return False
    return True

def QueryBvhClosestHit(node, rayOrigin, rayDirection, inverseDirection):
    """Ray parameter t of the closest hit below *node*, or None.  Used
    by the Image Footprint Generator for a root node without vertex
    normals (_MeshHit)."""
    if not _RayIntersectsAxisAlignedBounds(
            rayOrigin, rayDirection, inverseDirection,
            node.minimumBounds, node.maximumBounds):
        return None
    if node.triangleFaces is not None:
        closestHitParameter = None
        for triangleData in node.triangleFaces:
            hitParameter = _IntersectRayWithTriangle(
                rayOrigin, rayDirection,
                triangleData[0], triangleData[1], triangleData[2])
            if hitParameter is not None and (closestHitParameter is None
                                           or hitParameter < closestHitParameter):
                closestHitParameter = hitParameter
        return closestHitParameter
    leftHit = QueryBvhClosestHit(
        node.leftChild, rayOrigin, rayDirection, inverseDirection)
    rightHit = QueryBvhClosestHit(
        node.rightChild, rayOrigin, rayDirection, inverseDirection)
    if leftHit is None: return rightHit
    if rightHit is None: return leftHit
    return min(leftHit, rightHit)

def _QueryBvhClosestHitDetailed(node, rayOrigin, rayDirection,
                                inverseDirection):
    """Closest hit below *node*: (ray parameter t, vertex index 0,
    vertex index 1, vertex index 2, barycentric u, barycentric v) or
    None.  The vertex indices and barycentric coordinates give the
    interpolated vertex normal, which defines the tangent plane of the
    GSD at a sampled pixel position."""
    if not _RayIntersectsAxisAlignedBounds(
            rayOrigin, rayDirection, inverseDirection,
            node.minimumBounds, node.maximumBounds):
        return None
    if node.triangleFaces is not None:
        bestResult = None
        for triangleData in node.triangleFaces:
            triangleIntersection = _IntersectRayWithTriangleDetailed(
                rayOrigin, rayDirection,
                triangleData[0], triangleData[1], triangleData[2])
            if triangleIntersection is not None:
                hitParameter, barycentricU, barycentricV = triangleIntersection
                if bestResult is None or hitParameter < bestResult[0]:
                    bestResult = (hitParameter,
                                 triangleData[3], triangleData[4],
                                 triangleData[5],
                                 barycentricU, barycentricV)
        return bestResult
    leftResult = _QueryBvhClosestHitDetailed(
        node.leftChild, rayOrigin, rayDirection, inverseDirection)
    rightResult = _QueryBvhClosestHitDetailed(
        node.rightChild, rayOrigin, rayDirection, inverseDirection)
    if leftResult is None: return rightResult
    if rightResult is None: return leftResult
    return leftResult if leftResult[0] <= rightResult[0] else rightResult


# ===================================================================
# N-ring vertex normal computation
# ===================================================================

def _ComputeFaceNormal(coordinate0, coordinate1, coordinate2):
    """Compute face normal (unnormalised: magnitude = 2 * area).
    Larger faces contribute more when averaged."""
    edge1 = VectorSubtract(coordinate1, coordinate0)
    edge2 = VectorSubtract(coordinate2, coordinate0)
    return VectorCross(edge1, edge2)

def _BuildVertexAdjacency(faceList):
    """Build vertex-to-vertex adjacency from a list of face tuples.
    Each face tuple: (coord0, coord1, coord2, idx0, idx1, idx2).
    Returns {vertexIndex: set(neighbourVertexIndices)}."""
    adjacency = {}
    for faceData in faceList:
        index0, index1, index2 = faceData[3], faceData[4], faceData[5]
        for vertexIndex in (index0, index1, index2):
            if vertexIndex not in adjacency:
                adjacency[vertexIndex] = set()
        adjacency[index0].add(index1); adjacency[index0].add(index2)
        adjacency[index1].add(index0); adjacency[index1].add(index2)
        adjacency[index2].add(index0); adjacency[index2].add(index1)
    return adjacency

def _BuildVertexToFaces(faceList):
    """Build vertex-to-face-indices mapping.
    Returns {vertexIndex: [faceListIndex, ...]}."""
    vertexToFaces = {}
    for faceListIndex, faceData in enumerate(faceList):
        index0, index1, index2 = faceData[3], faceData[4], faceData[5]
        for vertexIndex in (index0, index1, index2):
            if vertexIndex not in vertexToFaces:
                vertexToFaces[vertexIndex] = []
            vertexToFaces[vertexIndex].append(faceListIndex)
    return vertexToFaces

def _ExpandToNRing(vertexIndex, adjacency, ringCount):
    """Find all vertices within *ringCount* edge-hops of *vertexIndex*.
    Returns a set of vertex indices (including the vertex itself)."""
    currentRing = {vertexIndex}
    allVertices = {vertexIndex}
    for _ in range(ringCount):
        nextRing = set()
        for currentVertex in currentRing:
            neighbours = adjacency.get(currentVertex, set())
            for neighbour in neighbours:
                if neighbour not in allVertices:
                    nextRing.add(neighbour)
                    allVertices.add(neighbour)
        currentRing = nextRing
        if not currentRing: break
    return allVertices

def ComputeNRingVertexNormals(faceList, faceNormals, vertexToFaces,
                              adjacency, ringCount):
    """Compute per-vertex normals averaged over N-ring neighbourhood.

    For each vertex:
      1. Find all vertices within *ringCount* edge-hops.
      2. Collect all faces that contain any of those vertices.
      3. Average the (area-weighted) face normals.
      4. Normalise the result.

    Returns {vertexIndex: (nx, ny, nz)}."""
    vertexNormals = {}
    allVertexIndices = list(adjacency.keys())
    processedCount = 0

    for vertexIndex in allVertexIndices:
        # Expand to N-ring neighbourhood.
        neighbourhoodVertices = _ExpandToNRing(
            vertexIndex, adjacency, ringCount)

        # Collect all faces touching any vertex in the neighbourhood.
        neighbourhoodFaceIndices = set()
        for neighbourVertex in neighbourhoodVertices:
            faceIndices = vertexToFaces.get(neighbourVertex, [])
            for faceIndex in faceIndices:
                neighbourhoodFaceIndices.add(faceIndex)

        # Average face normals (area-weighted: unnormalised face
        # normals have magnitude proportional to face area).
        sumNormalX = 0.0; sumNormalY = 0.0; sumNormalZ = 0.0
        for faceIndex in neighbourhoodFaceIndices:
            faceNormal = faceNormals[faceIndex]
            sumNormalX += faceNormal[0]
            sumNormalY += faceNormal[1]
            sumNormalZ += faceNormal[2]

        vertexNormals[vertexIndex] = VectorNormalize(
            (sumNormalX, sumNormalY, sumNormalZ))

        processedCount += 1
        if processedCount % 100000 == 0:
            print("    Vertex normals: {:,} / {:,} computed...".format(
                processedCount, len(allVertexIndices)))

    return vertexNormals

def InterpolateVertexNormal(vertexNormals, vertexIndex0, vertexIndex1,
                            vertexIndex2, barycentricU, barycentricV):
    """Interpolate vertex normals using barycentric coordinates, with
    the hit point (1 - u - v) * vertex0 + u * vertex1 + v * vertex2.
    Returns None when a vertex has no normal, or when the interpolated
    normals cancel (opposite vertex normals across a fold or flipped
    faces), so that the hit counts as a degenerate sample rather than
    as a ray rejected for grazing incidence."""
    normal0 = vertexNormals.get(vertexIndex0)
    normal1 = vertexNormals.get(vertexIndex1)
    normal2 = vertexNormals.get(vertexIndex2)
    if normal0 is None or normal1 is None or normal2 is None:
        return None
    barycentricW = 1.0 - barycentricU - barycentricV
    interpolatedNormal = (
        barycentricW * normal0[0] + barycentricU * normal1[0] + barycentricV * normal2[0],
        barycentricW * normal0[1] + barycentricU * normal1[1] + barycentricV * normal2[1],
        barycentricW * normal0[2] + barycentricU * normal1[2] + barycentricV * normal2[2])
    unitNormal = VectorNormalize(interpolatedNormal)
    return unitNormal if VectorLength(unitNormal) > 0.5 else None


# ===================================================================
# The frame of a triangular mesh
# ===================================================================

def MultiplyMatrixElements(firstElements, secondElements):
    """The sixteen elements, row by row, of the product of two 4 x 4
    matrices given by their sixteen elements row by row (the first
    matrix applied after the second)."""
    return [sum(firstElements[rowIndex * 4 + innerIndex] * secondElements[innerIndex * 4 + columnIndex]
                for innerIndex in range(4))
            for rowIndex in range(4) for columnIndex in range(4)]


def InvertMatrixElements(elements):
    """The sixteen elements, row by row, of the inverse of a 4 x 4 matrix
    given by its sixteen elements row by row (Gauss-Jordan elimination
    with partial pivoting).  Raises ValueError for a singular matrix."""
    rows = [list(elements[rowIndex * 4:rowIndex * 4 + 4]) + [1.0 if columnIndex == rowIndex else 0.0
                                                              for columnIndex in range(4)]
            for rowIndex in range(4)]
    largestElement = max(abs(element) for element in elements) or 1.0
    for columnIndex in range(4):
        pivotIndex = max(range(columnIndex, 4), key=functools.partial(_PivotMagnitude, rows, columnIndex))
        if abs(rows[pivotIndex][columnIndex]) <= 1e-14 * largestElement:
            raise ValueError("the matrix cannot be inverted")
        rows[columnIndex], rows[pivotIndex] = rows[pivotIndex], rows[columnIndex]
        pivot = rows[columnIndex][columnIndex]
        rows[columnIndex] = [value / pivot for value in rows[columnIndex]]
        for rowIndex in range(4):
            if rowIndex != columnIndex and rows[rowIndex][columnIndex] != 0.0:
                factor = rows[rowIndex][columnIndex]
                rows[rowIndex] = [value - factor * pivotValue
                                  for value, pivotValue in zip(rows[rowIndex], rows[columnIndex])]
    return [rows[rowIndex][4 + columnIndex] for rowIndex in range(4) for columnIndex in range(4)]


def _PivotMagnitude(rows, columnIndex, rowIndex):
    return abs(rows[rowIndex][columnIndex])


def ApplyMatrixElements(elements, point):
    """The point (x, y, z) taken through the 4 x 4 matrix given by its
    sixteen elements row by row, as homogeneous coordinates (the
    Matrix.mulp of Metashape), or None when the homogeneous coordinate
    becomes zero."""
    pointX, pointY, pointZ = point
    transformed = [elements[rowIndex * 4] * pointX + elements[rowIndex * 4 + 1] * pointY
                   + elements[rowIndex * 4 + 2] * pointZ + elements[rowIndex * 4 + 3] for rowIndex in range(4)]
    if transformed[3] == 0.0:
        return None
    if transformed[3] == 1.0:
        return (transformed[0], transformed[1], transformed[2])
    return (transformed[0] / transformed[3], transformed[1] / transformed[3], transformed[2] / transformed[3])


def IsSameCoordinateSystem(firstCoordinateSystem, secondCoordinateSystem):
    """True when two CRSs (Metashape.CoordinateSystem) have the same,
    non-empty WKT."""
    firstWellKnownText = str(getattr(firstCoordinateSystem, "wkt", "") or "")
    secondWellKnownText = str(getattr(secondCoordinateSystem, "wkt", "") or "")
    return bool(firstWellKnownText) and firstWellKnownText == secondWellKnownText


def CoordinateSystemName(coordinateSystem):
    """The name of a CRS (Metashape.CoordinateSystem), or "unnamed"."""
    return str(getattr(coordinateSystem, "name", "") or "") or "unnamed"


# The largest difference of an element of the transform of a triangular
# mesh from the identity matrix that still counts as the identity matrix
# (IsIdentityMatrixElements).
IDENTITY_MATRIX_TOLERANCE = 1e-9


def IsIdentityMatrixElements(elements):
    """True when the sixteen *elements*, row by row, are those of the 4 x 4
    identity matrix, each within IDENTITY_MATRIX_TOLERANCE."""
    return all(abs(element - (1.0 if elementIndex % 5 == 0 else 0.0)) <= IDENTITY_MATRIX_TOLERANCE
               for elementIndex, element in enumerate(elements))


def HasFrameOfItsOwn(model):
    """True when the triangular mesh *model* is stored in a frame of its
    own: with a transform (Model.transform of Metashape 2.x) and a CRS of
    its own (Model.crs), or with a transform other than the identity
    matrix, as a mesh imported without a CRS of its own can have
    (ModelToInternalElements).  A mesh that Metashape builds in the chunk
    has the identity matrix as its transform and no CRS of its own
    (Metashape 2.3.2), so its vertices are internal coordinates of the
    chunk.  A transform that cannot be read counts as a frame of its own,
    so that ModelToInternalElements says why it cannot be used."""
    modelTransform = getattr(model, "transform", None)
    if modelTransform is None:
        return False
    if getattr(model, "crs", None) is not None:
        return True
    modelElements = TransformMatrixElements(modelTransform)
    return modelElements is None or not IsIdentityMatrixElements(modelElements)


def ModelToInternalElements(model, chunk):
    """The sixteen elements, row by row, of the 4 x 4 matrix that takes
    the stored vertex coordinates of the triangular mesh *model* into the
    internal coordinates of *chunk*, or None when they are internal
    coordinates already: the mesh has no frame of its own
    (HasFrameOfItsOwn), as a mesh that Metashape builds in the chunk.

    A triangular mesh can be stored in a frame of its own (Model.transform
    and Model.crs of Metashape 2.x).  Without a CRS of its own, its
    transform takes the stored coordinates to the internal coordinates of
    the chunk, whatever the chunk transform.  Metashape 2.3.2 stores the
    vertices of an OBJ file imported without a CRS of its own relative to
    the first vertex, whose internal coordinates form the translation of
    its transform.  In a chunk in a local CRS, the chunk transform then
    takes the transformed vertices to the coordinates of the file, also
    when it scales and rotates.  The script align_model_to_model.py of
    Agisoft's metashape-scripts repository, for Metashape 2.3, likewise
    takes the chunk transform times the transform of such an asset to
    world coordinates.  With a CRS of its own, the transform takes the
    stored coordinates to geocentric coordinates on the geodetic datum of
    that CRS.  When its CRS is not the chunk CRS, the coordinate
    transformation into the geocentric coordinates of the chunk CRS
    follows (Metashape.CoordinateSystem.datumTransform, as Region Tools
    uses it between chunks), and the inverse chunk transform then gives
    the internal coordinates.  Raises ValueError, with the reason, when the
    transform of the mesh cannot be read.  For a mesh with a CRS of its
    own, it also raises when the chunk has no transform, or when the chunk
    transform cannot be read or inverted, when the mesh has a CRS other
    than the chunk CRS and one of the two is local, or the chunk has no
    CRS, and when the coordinate transformation between the two CRSs
    cannot be read.  Exceptions of the host are passed on."""
    if not HasFrameOfItsOwn(model):
        return None
    modelElements = TransformMatrixElements(getattr(model, "transform", None))
    if modelElements is None:
        raise ValueError("the transform of the triangular mesh cannot be read")
    modelCoordinateSystem = getattr(model, "crs", None)
    if modelCoordinateSystem is None:
        return modelElements
    chunkMatrix = getattr(getattr(chunk, "transform", None), "matrix", None)
    if chunkMatrix is None:
        raise ValueError("the triangular mesh has a transform and a CRS of its own, and the chunk has no transform "
                         "that relates them to the internal coordinates of the chunk")
    chunkElements = TransformMatrixElements(chunkMatrix)
    if chunkElements is None:
        raise ValueError("the chunk transform cannot be read")
    chunkCoordinateSystem = getattr(chunk, "crs", None)
    worldElements = modelElements
    if not IsSameCoordinateSystem(modelCoordinateSystem, chunkCoordinateSystem):
        if (CrsKind(modelCoordinateSystem) in ("none", "local")
                or CrsKind(chunkCoordinateSystem) in ("none", "local")):
            raise ValueError(
                "the CRS of the triangular mesh ({}) and the chunk CRS ({}) cannot be related, because one of them "
                "is local or missing".format(CoordinateSystemName(modelCoordinateSystem),
                                             CoordinateSystemName(chunkCoordinateSystem)
                                             if chunkCoordinateSystem is not None else "none"))
        datumElements = TransformMatrixElements(Metashape.CoordinateSystem.datumTransform(
            modelCoordinateSystem, chunkCoordinateSystem))
        if datumElements is None:
            raise ValueError("the coordinate transformation from the CRS of the triangular mesh ({}) to the chunk "
                             "CRS ({}) cannot be read".format(CoordinateSystemName(modelCoordinateSystem),
                                                              CoordinateSystemName(chunkCoordinateSystem)))
        worldElements = MultiplyMatrixElements(datumElements, modelElements)
    try:
        internalFromWorldElements = InvertMatrixElements(chunkElements)
    except ValueError:
        raise ValueError("the chunk transform cannot be inverted") from None
    return MultiplyMatrixElements(internalFromWorldElements, worldElements)


def ModelFrameConsoleLine(model, chunk):
    """The Console line of a triangular mesh stored in a frame of its own
    (HasFrameOfItsOwn, ModelToInternalElements): with a CRS of its own, or
    without one and with a transform other than the identity matrix.  None
    for a mesh in the internal coordinates of the chunk, and for a mesh
    without a CRS of its own whose transform cannot be read, which
    ModelToInternalElements stops."""
    if not HasFrameOfItsOwn(model):
        return None
    modelCoordinateSystem = getattr(model, "crs", None)
    if modelCoordinateSystem is None:
        if TransformMatrixElements(getattr(model, "transform", None)) is None:
            return None
        return ("  Triangular mesh: stored with a transform of its own and no CRS of its own. Its transform "
                "converts its vertices to the internal coordinates of the chunk.")
    if IsSameCoordinateSystem(modelCoordinateSystem, getattr(chunk, "crs", None)):
        stored = "stored with a transform of its own, in the chunk CRS ({})".format(
            CoordinateSystemName(modelCoordinateSystem))
    else:
        stored = "stored with a transform of its own, in its own CRS ({}), while the chunk CRS is {}".format(
            CoordinateSystemName(modelCoordinateSystem), CoordinateSystemName(getattr(chunk, "crs", None)))
    return "  Triangular mesh: {}. Its vertices are converted to the internal coordinates of the chunk.".format(stored)


def StoredVertexCoordinates(modelVertices, toInternalElements):
    """The coordinates of every vertex of a triangular mesh, in the
    internal coordinates of the chunk, as tuples indexed as the vertices:
    as stored, or taken through *toInternalElements*
    (ModelToInternalElements) when the mesh has a frame of its own.  None
    for a vertex whose coordinates cannot be read.  Each vertex is read
    once, and the faces that share it share its tuple."""
    coordinates = []
    for vertexIndex in range(len(modelVertices)):
        point = VectorFromMetashape(modelVertices[vertexIndex].coord)
        if point is not None and toInternalElements is not None:
            point = ApplyMatrixElements(toInternalElements, point)
        coordinates.append(point)
    return coordinates


# ===================================================================
# Bounding volume hierarchy (BVH): construction
# ===================================================================

def BuildMeshBvh(model, normalAveragingRings=3, chunk=None):
    """Build a bounding volume hierarchy (BVH) from the full triangular
    mesh, with N-ring averaged vertex normals, in the internal
    coordinates of *chunk*: a mesh stored in a frame of its own is taken
    into them first (ModelToInternalElements), and the Console says so.

    Returns (bvhRootNode, vertexNormals) where vertexNormals is
    {vertexIndex: (nx, ny, nz)}, or (None, None) on failure.  The root
    node holds the vertex normals as well (bvhRootNode.vertexNormals), so
    that a function given the root node alone can use them, as the
    footprints of the Image Footprint Generator do.  Raises ValueError
    (ModelToInternalElements) when the frame of the mesh cannot be
    related to the internal coordinates of the chunk, also when the mesh
    has a CRS of its own and no chunk is given."""
    modelFaces = model.faces
    modelVertices = model.vertices
    if modelFaces is None or modelVertices is None: return (None, None)
    faceCount = len(modelFaces)
    if faceCount == 0: return (None, None)

    toInternalElements = ModelToInternalElements(model, chunk)
    frameLine = ModelFrameConsoleLine(model, chunk)
    if frameLine is not None:
        print(frameLine)

    # Load triangles with vertex indices.
    print("  Bounding volume hierarchy (BVH): loading {:,} triangular mesh faces...".format(faceCount))
    vertexCoordinates = StoredVertexCoordinates(modelVertices, toInternalElements)
    triangleList = []
    for face in modelFaces:
        vertexIndices = face.vertices
        coordinate0 = vertexCoordinates[vertexIndices[0]]
        coordinate1 = vertexCoordinates[vertexIndices[1]]
        coordinate2 = vertexCoordinates[vertexIndices[2]]
        if coordinate0 and coordinate1 and coordinate2:
            triangleList.append((coordinate0, coordinate1, coordinate2,
                                vertexIndices[0], vertexIndices[1],
                                vertexIndices[2]))
    if not triangleList: return (None, None)
    print("  BVH: {:,} valid triangular mesh faces loaded.".format(len(triangleList)))

    # Compute face normals (area-weighted = unnormalised cross product).
    print("  Computing face normals...")
    faceNormals = []
    for triangleData in triangleList:
        faceNormals.append(_ComputeFaceNormal(
            triangleData[0], triangleData[1], triangleData[2]))

    # Build adjacency structures.
    print("  Building vertex adjacency...")
    vertexToFaces = _BuildVertexToFaces(triangleList)
    adjacency = _BuildVertexAdjacency(triangleList)
    print("  {:,} unique vertices.".format(len(adjacency)))

    # Compute N-ring averaged vertex normals.
    print("  Computing {}-ring averaged vertex normals...".format(
        normalAveragingRings))
    vertexNormals = ComputeNRingVertexNormals(
        triangleList, faceNormals, vertexToFaces,
        adjacency, normalAveragingRings)
    print("  {:,} vertex normals computed.".format(len(vertexNormals)))

    # Build the BVH tree.
    print("  Building the BVH tree...")
    bvhRootNode = _BuildBvhRecursive(triangleList)
    bvhRootNode.vertexNormals = vertexNormals
    print("  BVH complete.")

    return (bvhRootNode, vertexNormals)


# ===================================================================
# Tie-point helpers
# ===================================================================

def BuildTrackIdToPointMap(tiePoints):
    """Build a {track_id: point} dictionary for valid tie points."""
    pointByTrackId = {}
    for point in tiePoints.points:
        if point is None or not point.valid: continue
        pointByTrackId[point.track_id] = point
    return pointByTrackId

def GetValidProjections(camera, tiePoints, pointByTrackId):
    """Return [(point, image coordinates of its projection), ...] for
    the projections of valid tie points on *camera*.  An image without
    projections gives an empty list silently, and any other failure to
    read the projections is reported in the Console."""
    try:
        cameraProjections = tiePoints.projections[camera]
    except KeyError:
        return []
    except Exception as suppressedError:
        ReportSuppressedError("photogrammetryUtilities.GetValidProjections", suppressedError)
        return []
    if cameraProjections is None:
        return []
    validProjections = []
    for projection in cameraProjections:
        point = pointByTrackId.get(projection.track_id)
        if (point is None or point.coord is None
                or projection.coord is None):
            continue
        validProjections.append((point, projection.coord))
    return validProjections

def HomogeneousToCartesian3D(homogeneousCoordinate):
    """Convert a 4-element homogeneous coordinate to a 3-tuple."""
    if len(homogeneousCoordinate) >= 4:
        wComponent = homogeneousCoordinate[3]
        if abs(wComponent) < 1e-30: return None
        return (homogeneousCoordinate[0] / wComponent,
                homogeneousCoordinate[1] / wComponent,
                homogeneousCoordinate[2] / wComponent)
    return (homogeneousCoordinate[0], homogeneousCoordinate[1],
            homogeneousCoordinate[2])


# ===================================================================
# Plane mode: the spread of the tie points about the fitted plane
# ===================================================================

# Plane mode stands for the surface of an image by the least-squares plane
# through its valid tie points.  It warns for an image whose tie points
# lie off that plane by a root mean square distance of more than this
# share of their median distance from the perspective centre (Geert, 17
# October 2026: 5 %).  Such an image sees more than one plane (a wall and
# the ground, for example), so that its plane, and its footprint and GSD
# values in plane mode, can be far off.  In Geert's Project 4 (a wall and
# the ground) the share is 24 % for the median image and above 5 % for 750
# of 777 images, while its two most planar images give 0.7 % and 1.3 %.
PLANE_SPREAD_WARNING_SHARE = 0.05


def TiePointCoordinatesOfProjections(validProjections):
    """The coordinates (3-tuples, internal chunk coordinates) of the 3D
    tie points of *validProjections* (GetValidProjections), as plane mode
    fits its plane to them.  An entry that is not a pair of a point and
    its projection is left out."""
    coordinates = []
    for entry in (validProjections or []):
        try:
            cartesian = HomogeneousToCartesian3D(entry[0].coord)
        except (TypeError, AttributeError, IndexError):
            continue
        if cartesian:
            coordinates.append(cartesian)
    return coordinates


def PlaneSpreadShare(pointTuples, perspectiveCentre):
    """The root mean square distance of *pointTuples* from their
    least-squares plane (FitPlaneToPoints), divided by their median
    distance from *perspectiveCentre*: how far the tie points of an image
    lie off one plane, relative to the distance at which the image sees
    them.  The share does not depend on the unit or the scale of the
    coordinates.  None when no plane can be fitted, without a
    perspective centre, or when the median distance is zero."""
    if perspectiveCentre is None:
        return None
    centroid, normal = FitPlaneToPoints(pointTuples)
    if normal is None:
        return None
    sumSquaredDistance = 0.0
    for point in pointTuples:
        offset = VectorDot(VectorSubtract(point, centroid), normal)
        sumSquaredDistance += offset * offset
    rootMeanSquareDistance = math.sqrt(sumSquaredDistance / len(pointTuples))
    medianDistance = ComputePercentile(
        sorted(VectorLength(VectorSubtract(point, perspectiveCentre)) for point in pointTuples), 50.0)
    if not medianDistance or not medianDistance > 0.0:
        return None
    return rootMeanSquareDistance / medianDistance


def PlaneSpreadOfImage(camera, validProjections):
    """PlaneSpreadShare of the valid tie points of an image
    (*validProjections*) about the plane that plane mode fits to them,
    seen from its perspective centre (Camera.center)."""
    return PlaneSpreadShare(TiePointCoordinatesOfProjections(validProjections),
                            VectorFromMetashape(getattr(camera, "center", None)))


def PlaneSpreadWarningLine(imageLabel, share):
    """The Console line of an image whose tie points lie off their plane
    by more than PLANE_SPREAD_WARNING_SHARE (PlaneSpreadShare), or None
    for a share at most that (or none)."""
    if share is None or not share > PLANE_SPREAD_WARNING_SHARE:
        return None
    return ("{}: plane mode warning: the root mean square distance of its tie points from the plane fitted to them "
            "is {:.1f} % of their median distance from the perspective centre (the warning threshold is {:g} %), so "
            "the plane can misplace the surface.".format(imageLabel, 100.0 * share,
                                                         100.0 * PLANE_SPREAD_WARNING_SHARE))


def PlaneSpreadSummaryText(warnedImageCount, imageCount, pointsToConsole=False):
    """The sentences of a Console summary or a final message on the images
    of a plane-mode run whose tie points lie far from their plane
    (PlaneSpreadWarningLine), or an empty text without any.  With
    *pointsToConsole*, for a final message, the threshold sentence ends
    with "(see the Console)", where each image has its warning line."""
    if not warnedImageCount:
        return ""
    if warnedImageCount == 1 and imageCount == 1:
        subject, consequence = "the tie points of the image lie", "its values"
    elif warnedImageCount == 1:
        subject, consequence = "the tie points of 1 of the {:,} images lie".format(imageCount), "its values"
    else:
        subject, consequence = ("the tie points of {:,} of the {:,} images lie".format(warnedImageCount, imageCount),
                                "their values")
    return ("Plane mode warning: {} far from one plane, so {} in plane mode can be wrong. Their root mean square "
            "distance from it exceeds {:g} % of their median distance from the perspective centre{}. Triangular "
            "mesh mode follows the surface.".format(subject, consequence, 100.0 * PLANE_SPREAD_WARNING_SHARE,
                                                    " (see the Console)" if pointsToConsole else ""))


def ChunkHasTriangularMesh(chunk):
    """True when *chunk* has a triangular mesh (its default model): the
    rule by which the Image Footprint Generator, the GSD Value Exporter,
    and the Image Parameter Exporter offer and preselect triangular mesh
    mode."""
    return chunk is not None and getattr(chunk, "model", None) is not None


# ===================================================================
# Chunk helpers
# ===================================================================

def GetChunkScale(chunk):
    """Return the chunk scale (the scale of the chunk transform), or None.
    Metashape 2.3.2 gives a chunk without Metashape scale bars and
    reference coordinates the identity matrix with no scale (None), so
    the scale, not the matrix, tells whether the chunk is scaled."""
    transformMatrix = chunk.transform.matrix
    if transformMatrix is None: return None
    scaleValue = chunk.transform.scale
    if scaleValue and scaleValue > 0 and math.isfinite(scaleValue):
        return scaleValue
    return None


# Key of the chunk metadata (chunk.meta) under which Align Coordinate Axes
# to Region of Region Tools records the transform that it gives a chunk:
# for a chunk without a physical scale that transform has an arbitrary
# scale, so the GSD tools report chunk units while the chunk transform
# still equals the record (see PhysicalChunkScale), and for every chunk
# its local coordinates aligned with the region are not georeferenced
# (see HasRegionToolsPlacement).
REGION_TOOLS_TRANSFORM_METADATA_KEY = "MetaFOCUS/RegionToolsTransform"

# Key of the chunk metadata under which Region Tools records
# whether the scale of the recorded transform is
# arbitrary (REGION_TOOLS_ARBITRARY_SCALE_TEXT) or the physical scale of
# the chunk (REGION_TOOLS_PHYSICAL_SCALE_TEXT).  A record without it reads
# as one with an arbitrary scale.
REGION_TOOLS_SCALE_METADATA_KEY = "MetaFOCUS/RegionToolsScale"
REGION_TOOLS_ARBITRARY_SCALE_TEXT = "arbitrary"
REGION_TOOLS_PHYSICAL_SCALE_TEXT = "physical"

# Largest difference between an element of the chunk transform and the
# recorded one, relative to the largest recorded element in magnitude,
# at which the transform still counts as the recorded one.  Referencing,
# Metashape scale bars, or an imported transform change it by far more.
_RECORDED_TRANSFORM_RELATIVE_TOLERANCE = 1e-9

# Unit of the lengths of the GSD tools, the areas being in its square:
# the attribute 00_Units of the Image Footprint Generator and the Units
# line of the GSD CSV give the same text.
METRES_TEXT = "metres"
CHUNK_UNITS_TEXT = "chunk units (chunk not scaled)"


def LengthUnitsText(chunkIsScaled):
    """Unit of the lengths of the GSD tools: METRES_TEXT for a chunk
    with a physical scale, CHUNK_UNITS_TEXT otherwise."""
    return METRES_TEXT if chunkIsScaled else CHUNK_UNITS_TEXT


def TransformMatrixElements(transformMatrix):
    """The sixteen elements of a 4 x 4 matrix (indexable as [row,
    column]), row by row, as floats, or None when there is no matrix or
    its elements cannot be read as finite numbers (reported once in the
    Console)."""
    if transformMatrix is None:
        return None
    try:
        elements = [float(transformMatrix[rowIndex, columnIndex])
                    for rowIndex in range(4) for columnIndex in range(4)]
    except Exception as suppressedError:
        ReportSuppressedError("photogrammetryUtilities.TransformMatrixElements", suppressedError)
        return None
    return elements if all(math.isfinite(element) for element in elements) else None


def RecordRegionToolsTransform(chunk, transformMatrix, hasArbitraryScale=True):
    """Record *transformMatrix*, the transform that Align Coordinate Axes
    to Region gave *chunk*, in the chunk metadata under
    REGION_TOOLS_TRANSFORM_METADATA_KEY: its sixteen elements row by row,
    written with repr, so that they read back exactly, and separated by
    spaces.  REGION_TOOLS_SCALE_METADATA_KEY records whether its scale is
    arbitrary (*hasArbitraryScale*, a chunk without a physical scale) or
    the physical scale of the chunk.  The scale is recorded first: a
    transform with a physical scale whose scale cannot be recorded is not
    recorded either, because a record without it reads as one with an
    arbitrary scale.  Returns True when the record was written.  A chunk
    without metadata and a failure of the host are reported in the
    Console."""
    elements = TransformMatrixElements(transformMatrix)
    if elements is None:
        return False
    metadata = getattr(chunk, "meta", None)
    if metadata is None:
        ReportSuppressedError("photogrammetryUtilities.RecordRegionToolsTransform.metadata",
                              AttributeError("the chunk has no metadata (chunk.meta)"))
        return False
    try:
        metadata[REGION_TOOLS_SCALE_METADATA_KEY] = (REGION_TOOLS_ARBITRARY_SCALE_TEXT if hasArbitraryScale
                                                     else REGION_TOOLS_PHYSICAL_SCALE_TEXT)
    except Exception as suppressedError:
        ReportSuppressedError("photogrammetryUtilities.RecordRegionToolsTransform.scale", suppressedError)
        if not hasArbitraryScale:
            return False
    try:
        metadata[REGION_TOOLS_TRANSFORM_METADATA_KEY] = " ".join(repr(element) for element in elements)
    except Exception as suppressedError:
        ReportSuppressedError("photogrammetryUtilities.RecordRegionToolsTransform", suppressedError)
        return False
    return True


def RecordedRegionToolsTransformElements(chunk):
    """The sixteen elements of the transform that Region Tools recorded
    for *chunk* (see RecordRegionToolsTransform), or None when the chunk
    metadata hold no readable record."""
    metadata = getattr(chunk, "meta", None)
    if metadata is None:
        return None
    try:
        recordText = metadata[REGION_TOOLS_TRANSFORM_METADATA_KEY]
    except KeyError:
        return None
    except Exception as suppressedError:
        ReportSuppressedError("photogrammetryUtilities.RecordedRegionToolsTransformElements", suppressedError)
        return None
    try:
        elements = [float(text) for text in str(recordText or "").split()]
    except ValueError:
        return None
    if len(elements) != 16 or not all(math.isfinite(element) for element in elements):
        return None
    return elements


def IsRegionToolsTransform(chunk):
    """True when the chunk transform still equals the transform that
    Region Tools recorded for the chunk (see RecordRegionToolsTransform),
    within _RECORDED_TRANSFORM_RELATIVE_TOLERANCE of the largest recorded
    element.  Once the transform has changed (referencing, Metashape
    scale bars, an imported transform), the record no longer applies."""
    recordedElements = RecordedRegionToolsTransformElements(chunk)
    if recordedElements is None:
        return False
    currentElements = TransformMatrixElements(getattr(getattr(chunk, "transform", None), "matrix", None))
    if currentElements is None:
        return False
    tolerance = _RECORDED_TRANSFORM_RELATIVE_TOLERANCE * max(abs(element) for element in recordedElements)
    return all(abs(currentElement - recordedElement) <= tolerance
               for currentElement, recordedElement in zip(currentElements, recordedElements))


def RecordedRegionToolsScaleIsArbitrary(chunk):
    """False when Region Tools recorded that the scale of its transform is
    the physical scale of the chunk (REGION_TOOLS_SCALE_METADATA_KEY),
    True otherwise: a record without such an entry belongs to a chunk
    without a physical scale."""
    metadata = getattr(chunk, "meta", None)
    if metadata is None:
        return True
    try:
        scaleText = metadata[REGION_TOOLS_SCALE_METADATA_KEY]
    except KeyError:
        return True
    except Exception as suppressedError:
        ReportSuppressedError("photogrammetryUtilities.RecordedRegionToolsScaleIsArbitrary", suppressedError)
        return True
    return str(scaleText or "").strip() != REGION_TOOLS_PHYSICAL_SCALE_TEXT


def HasArbitraryRegionToolsScale(chunk):
    """True while the chunk transform is the one that Region Tools
    recorded (IsRegionToolsTransform) for a chunk without a physical
    scale (RecordedRegionToolsScaleIsArbitrary), whose scale is
    arbitrary."""
    return IsRegionToolsTransform(chunk) and RecordedRegionToolsScaleIsArbitrary(chunk)


# Largest difference between an element of the rotation of the chunk
# transform and that of the transform that Region Tools recorded at which
# the world coordinates still have the axes that Region Tools gave them
# (HasRegionToolsPlacement).  A transform fitted to reference coordinates
# usually turns them by far more.
_RECORDED_ROTATION_TOLERANCE = 1e-9

# Largest difference between an element of the translation of the chunk
# transform and that of the recorded transform, relative to the larger of
# 1 and the largest recorded element in magnitude, at which the world
# coordinates still have the origin that Region Tools gave them
# (HasRegionToolsPlacement).  A scale from Metashape scale bars keeps the
# translation, and a transform fitted to reference coordinates moves the
# origin by far more, also when it keeps the rotation.
_RECORDED_TRANSLATION_RELATIVE_TOLERANCE = 1e-9


def _RotationElements(elements):
    """The nine elements of the rotation of a 4 x 4 similarity given by
    its sixteen *elements* (TransformMatrixElements): its upper left 3 x 3
    block divided by its scale, the length of the first row, or None for
    a block without a scale."""
    scale = math.sqrt(elements[0] ** 2 + elements[1] ** 2 + elements[2] ** 2)
    if not (scale > 0.0 and math.isfinite(scale)):
        return None
    return [elements[rowIndex * 4 + columnIndex] / scale for rowIndex in range(3) for columnIndex in range(3)]


def _TranslationElements(elements):
    """The three elements of the translation of a 4 x 4 transform given by
    its sixteen *elements* (TransformMatrixElements): its last column
    without its last row."""
    return [elements[3], elements[7], elements[11]]


def HasRegionToolsPlacement(chunk):
    """True when the world coordinates of *chunk* still have the axes and
    the origin that Align Coordinate Axes to Region of Region Tools gave
    them: the rotation and the translation of the chunk transform equal
    those of the recorded transform (RecordRegionToolsTransform), whatever
    its scale, so that a later scale from Metashape scale bars does not
    count as a placement on the Earth.  A transform fitted to reference
    coordinates since then has another translation, and usually another
    rotation."""
    recordedElements = RecordedRegionToolsTransformElements(chunk)
    if recordedElements is None:
        return False
    currentElements = TransformMatrixElements(getattr(getattr(chunk, "transform", None), "matrix", None))
    if currentElements is None:
        return False
    recordedRotation, currentRotation = _RotationElements(recordedElements), _RotationElements(currentElements)
    if recordedRotation is None or currentRotation is None:
        return False
    if not all(abs(currentElement - recordedElement) <= _RECORDED_ROTATION_TOLERANCE
               for currentElement, recordedElement in zip(currentRotation, recordedRotation)):
        return False
    recordedTranslation = _TranslationElements(recordedElements)
    tolerance = _RECORDED_TRANSLATION_RELATIVE_TOLERANCE * max(1.0, max(abs(element) for element in recordedTranslation))
    return all(abs(currentElement - recordedElement) <= tolerance
               for currentElement, recordedElement in zip(_TranslationElements(currentElements), recordedTranslation))


def PhysicalChunkScale(chunk):
    """The chunk scale (see GetChunkScale) when it is physical, otherwise
    None.  It is not physical without a chunk transform, with a scale
    that is not a positive finite number (Metashape 2.3.2 gives a chunk
    without Metashape scale bars and reference coordinates a transform
    without a scale), and while the transform is the one that Region
    Tools recorded for a chunk without a physical scale
    (HasArbitraryRegionToolsScale), whose scale is arbitrary.  A
    transform that Metashape derived from Metashape scale bars or
    reference coordinates, or that was imported, counts as physical, also
    with a scale of exactly 1.  The GSD Value Exporter, the Image
    Footprint Generator, and the Image Parameter Exporter report metres
    only for a physical scale, otherwise chunk units."""
    chunkScale = GetChunkScale(chunk)
    if chunkScale is None or HasArbitraryRegionToolsScale(chunk):
        return None
    return chunkScale


def IsChunkScalePhysical(chunk):
    """True when the chunk has a physical scale (see PhysicalChunkScale),
    so that the GSD tools report metres, False when they report chunk
    units."""
    return PhysicalChunkScale(chunk) is not None


def DescribeMissingPhysicalScale(chunk):
    """Why *chunk* has no physical scale (see PhysicalChunkScale), for the
    messages of the GSD tools, or an empty string when it has one."""
    if IsChunkScalePhysical(chunk):
        return ""
    if HasArbitraryRegionToolsScale(chunk):
        return "its transform comes from Align Coordinate Axes to Region and has an arbitrary scale"
    transform = getattr(chunk, "transform", None)
    # A transform without a scale is what Metashape 2.3.2 gives a chunk
    # without Metashape scale bars, reference coordinates, and an imported
    # chunk transform.
    if getattr(transform, "matrix", None) is None or getattr(transform, "scale", None) is None:
        return "no Metashape scale bars, no reference coordinates, and no imported chunk transform"
    return "the scale of its transform is not a positive number"


def CrsKind(coordinateSystem):
    """Kind of a CRS of Metashape (Metashape.CoordinateSystem), read
    from the leading keyword of its WKT: "none" (no CRS), "local",
    "geographic", "projected", "geocentric", or "unknown".  For a
    compound CRS the horizontal component decides."""
    if coordinateSystem is None:
        return "none"
    wellKnownText = str(getattr(coordinateSystem, "wkt", "") or "").lstrip().upper()
    if wellKnownText.startswith("COMPD_CS"):
        horizontalStart = min((position for position in (
            wellKnownText.find("PROJCS"), wellKnownText.find("GEOGCS"),
            wellKnownText.find("LOCAL_CS"), wellKnownText.find("GEOCCS"))
            if position >= 0), default=-1)
        wellKnownText = wellKnownText[horizontalStart:] if horizontalStart >= 0 else ""
    for keyword, kind in (("LOCAL_CS", "local"), ("GEOGCS", "geographic"),
                          ("PROJCS", "projected"), ("GEOCCS", "geocentric")):
        if wellKnownText.startswith(keyword):
            return kind
    return "unknown"


def _WktTokens(text):
    """The tokens of a WKT text as (kind, value): "word" (a keyword, a
    number, or an enumeration), "string" (a quoted text, with a doubled
    quote read as one), "open", "close", and "comma"."""
    tokens = []
    position = 0
    length = len(text)
    while position < length:
        character = text[position]
        if character == '"':
            end = text.find('"', position + 1)
            while end != -1 and end + 1 < length and text[end + 1] == '"':
                end = text.find('"', end + 2)
            if end == -1:
                end = length
            tokens.append(("string", text[position + 1:end].replace('""', '"')))
            position = end + 1
        elif character in "[(":
            tokens.append(("open", character))
            position += 1
        elif character in "])":
            tokens.append(("close", character))
            position += 1
        elif character == ",":
            tokens.append(("comma", character))
            position += 1
        elif character.isspace():
            position += 1
        else:
            end = position
            while end < length and text[end] not in '"[](),' and not text[end].isspace():
                end += 1
            tokens.append(("word", text[position:end]))
            position = end
    return tokens


def ParseWkt(text):
    """The WKT *text* as nested lists [KEYWORD, arguments]: each argument is
    a text (a quoted string, a number, or an enumeration) or such a list,
    for example ["UNIT", ["metre", "1", ["AUTHORITY", ["EPSG", "9001"]]]].
    None for a text without an element."""
    root = None
    openElements = []
    pendingWord = None
    for kind, value in _WktTokens(text or ""):
        if kind == "word":
            pendingWord = value
        elif kind == "open":
            element = [(pendingWord or "").upper(), []]
            if openElements:
                openElements[-1][1].append(element)
            elif root is None:
                root = element
            openElements.append(element)
            pendingWord = None
        elif kind in ("close", "comma"):
            if pendingWord is not None and openElements:
                openElements[-1][1].append(pendingWord)
            pendingWord = None
            if kind == "close" and openElements:
                openElements.pop()
        elif kind == "string" and openElements:
            openElements[-1][1].append(value)
    return root


def _WktUnitName(element):
    """The name of the UNIT that is a direct child of the WKT *element*
    (ParseWkt), or None without one: the linear unit of a PROJCS, a
    LOCAL_CS, a GEOCCS, or a VERT_CS, and the angular unit of a GEOGCS."""
    if element is None:
        return None
    unitElements = [child for child in element[1] if isinstance(child, list) and child[0] == "UNIT"]
    if unitElements and unitElements[-1][1] and isinstance(unitElements[-1][1][0], str):
        return unitElements[-1][1][0]
    return None


# Unit names of a WKT that mean the same unit, by their lower-case form.
_WKT_UNIT_NAMES = {"metre": "metre", "meter": "metre", "metres": "metre", "meters": "metre",
                   "degree": "degree", "degrees": "degree"}


def NormalisedUnitName(unitName):
    """The unit name *unitName* of a WKT as MetaFOCUS writes it: "metre"
    for metre or meter, "degree" for degree, any other name unchanged, and
    None for none."""
    if not unitName:
        return None
    return _WKT_UNIT_NAMES.get(unitName.strip().lower(), unitName.strip())


def CrsUnitNames(coordinateSystem):
    """(horizontal unit, vertical unit) of a CRS of Metashape, read from
    the UNIT elements of its WKT (ParseWkt) and normalised
    (NormalisedUnitName): the unit of the CRS itself, or for a compound CRS
    (COMPD_CS) that of its horizontal component, and the unit of its
    vertical component (VERT_CS), None without one.  (None, None) without
    a CRS or a readable WKT."""
    if coordinateSystem is None:
        return None, None
    root = ParseWkt(str(getattr(coordinateSystem, "wkt", "") or ""))
    if root is None:
        return None, None
    horizontalElement = root
    verticalElement = None
    if root[0] == "COMPD_CS":
        componentElements = [child for child in root[1] if isinstance(child, list)]
        horizontalElement = next((child for child in componentElements
                                  if child[0] in ("PROJCS", "GEOGCS", "LOCAL_CS", "GEOCCS")), None)
        verticalElement = next((child for child in componentElements if child[0] == "VERT_CS"), None)
    return NormalisedUnitName(_WktUnitName(horizontalElement)), NormalisedUnitName(_WktUnitName(verticalElement))


# Kinds of CRS (CrsKind) whose world coordinates are geocentric in a
# georeferenced chunk.
EARTH_BOUND_CRS_KINDS = ("geographic", "projected", "geocentric")


def IsGeoreferencedChunk(chunk):
    """True when the world coordinates of the chunk are placed on the
    Earth, so that they are geocentric: a geographic, projected, or
    geocentric CRS (EARTH_BOUND_CRS_KINDS) and a chunk transform with a
    physical scale (IsChunkScalePhysical), a translation, and a rotation
    that are not those that Align Coordinate Axes to Region gave it
    (HasRegionToolsPlacement, the axes and the origin).  Metashape 2.3.2
    sets no translation and no
    rotation without reference coordinates: a chunk scaled by Metashape
    scale bars alone has a scale but no placement on the Earth, whatever
    its CRS."""
    transform = getattr(chunk, "transform", None)
    if getattr(transform, "matrix", None) is None:
        return False
    if CrsKind(getattr(chunk, "crs", None)) not in EARTH_BOUND_CRS_KINDS:
        return False
    if getattr(transform, "translation", None) is None or getattr(transform, "rotation", None) is None:
        return False
    return IsChunkScalePhysical(chunk) and not HasRegionToolsPlacement(chunk)


# Names of the three rotation angles per Metashape.EulerAngles value,
# in the order of the Reference panel columns.
_ROTATION_ANGLE_NAMES = (
    ("EulerAnglesYPR", ("Yaw", "Pitch", "Roll")),
    ("EulerAnglesOPK", ("Omega", "Phi", "Kappa")),
    ("EulerAnglesPOK", ("Phi", "Omega", "Kappa")),
    ("EulerAnglesANK", ("Alpha", "Nu", "Kappa")),
)


def RotationAngleNames(chunk):
    """Names of the three rotation angles in the convention of the
    chunk (chunk.euler_angles), in the order in which the Reference
    panel and chunk.exportReference() give them: (yaw, pitch, roll),
    (omega, phi, kappa), (phi, omega, kappa), or (alpha, nu, kappa).  The
    convention is compared with the members of
    Metashape.EulerAngles and with the module-level constants
    (Metashape.EulerAnglesOPK and so on).  Yaw, pitch, and roll when
    the chunk has no convention, and also, with a Console report,
    when the convention is not recognised."""
    convention = getattr(chunk, "euler_angles", None)
    if convention is None:
        return ("Yaw", "Pitch", "Roll")
    eulerAngles = getattr(Metashape, "EulerAngles", None)
    for enumerationName, angleNames in _ROTATION_ANGLE_NAMES:
        for candidateValue in (getattr(eulerAngles, enumerationName, None),
                               getattr(Metashape, enumerationName, None)):
            if candidateValue is not None and convention == candidateValue:
                return angleNames
    ReportSuppressedError("photogrammetryUtilities.RotationAngleNames", ValueError(
        "unrecognised rotation angle convention {}, the angles are labelled "
        "yaw, pitch, and roll".format(convention)))
    return ("Yaw", "Pitch", "Roll")


def TransformChunkPointToCrs(chunkPointTuple, chunk):
    """Metashape.Vector of a chunk-coordinate 3-tuple in the chunk's
    CRS: projected with chunk.crs after the chunk transform, the
    world (transformed) coordinates when the chunk has no CRS, and the
    unchanged chunk coordinates when it has no transform."""
    transformMatrix = chunk.transform.matrix
    if transformMatrix is None:
        return Metashape.Vector(list(chunkPointTuple))
    worldPoint = transformMatrix.mulp(Metashape.Vector(list(chunkPointTuple)))
    if chunk.crs is not None:
        return chunk.crs.project(worldPoint)
    return worldPoint


# ===================================================================
# Dense grid GSD: statistics
# ===================================================================

def ComputePercentile(sortedValues, percentile):
    """Compute the *percentile*-th value (0-100) from a sorted list."""
    count = len(sortedValues)
    position = (percentile / 100.0) * (count - 1)
    lowerIndex = int(math.floor(position))
    upperIndex = min(lowerIndex + 1, count - 1)
    fraction = position - lowerIndex
    return (sortedValues[lowerIndex]
            + fraction * (sortedValues[upperIndex] - sortedValues[lowerIndex]))

def BuildGsdStatistics(gsdValues, totalSampledCount):
    """Build a statistics dict from a list of GSD values.
    Returns a dict with the keys minimum, percentile02, average,
    median, percentile98, maximum, validSampleCount, and
    totalSampleCount, or None if gsdValues is empty."""
    if not gsdValues: return None
    gsdValues = sorted(gsdValues)
    validCount = len(gsdValues)
    return dict(
        minimum=gsdValues[0],
        percentile02=ComputePercentile(gsdValues, 2),
        average=sum(gsdValues) / validCount,
        median=ComputePercentile(gsdValues, 50),
        percentile98=ComputePercentile(gsdValues, 98),
        maximum=gsdValues[-1],
        validSampleCount=validCount,
        totalSampleCount=totalSampledCount)


# ===================================================================
# GSD engine at sampled positions, shared by every tool and every
# surface mode
# ===================================================================

# Rays that meet the surface beyond about 84.3 degrees incidence
# (cos(theta) < 0.1) stretch the projected photosite pitch more than
# tenfold.
# Such values are rejected and counted rather than extrapolated, in
# every surface mode alike: on a fitted plane they are geometrically
# exact but physically meaningless (the plane extrapolated towards
# the horizon), on a mesh they are dominated by normal noise.
GRAZING_COSINE_THRESHOLD = 0.1

# Elevation values at or below this sentinel are treated as no-data.
_DEM_NODATA = -30000.0

# Largest ray parameter (metres along a unit ray) of the search for a
# raster DEM crossing.
_DEM_MAXIMUM_RAY_PARAMETER = 1.0e7

# Safeguard on the number of steps of one ray towards the raster DEM,
# for a raster DEM that reports neither its height range nor its
# extent (raised for a raster DEM with more columns and rows).  A
# crossing beyond it is missed (RASTER_DEM_MISS_STEP_LIMIT).
_DEM_MAXIMUM_MARCH_STEP_COUNT = 200000

# Reasons why a ray misses the raster DEM (DemSurface.missCountsByReason
# and SolveRayDemIntersectionWithMissReason).
RASTER_DEM_MISS_PERSPECTIVE_CENTRE_BELOW_SURFACE = "perspectiveCentreBelowSurface"
RASTER_DEM_MISS_CROSSING_WITHOUT_DATA = "crossingWithoutData"
RASTER_DEM_MISS_NO_CROSSING_WITHIN_EXTENT = "noCrossingWithinExtent"
RASTER_DEM_MISS_STEP_LIMIT = "stepLimitReached"

# The reasons in the order and the words of the Console.
_RASTER_DEM_MISS_DESCRIPTIONS = (
    (RASTER_DEM_MISS_PERSPECTIVE_CENTRE_BELOW_SURFACE, "with the perspective centre below the raster DEM surface"),
    (RASTER_DEM_MISS_CROSSING_WITHOUT_DATA, "with a crossing where the raster DEM has no data"),
    (RASTER_DEM_MISS_NO_CROSSING_WITHIN_EXTENT, "without a crossing within the raster DEM extent"),
    (RASTER_DEM_MISS_STEP_LIMIT, "without a crossing within the step limit of the search"),
)


def JoinWithAnd(parts):
    """The parts joined with commas and a final "and" (shared with the
    GSD Visualiser): "a", "a and b", or "a, b, and c", with the serial
    comma before the "and" of three or more parts."""
    if len(parts) <= 1:
        return "".join(parts)
    if len(parts) == 2:
        return "{} and {}".format(parts[0], parts[1])
    return "{}, and {}".format(", ".join(parts[:-1]), parts[-1])


def DescribeRasterDemMisses(missCountsByReason):
    """The missed rays of *missCountsByReason* (reason: count, see the
    RASTER_DEM_MISS constants) in words, for example "12 with the
    perspective centre below the raster DEM surface and 3 with a
    crossing where the raster DEM has no data", or an empty string when
    there is no miss."""
    return JoinWithAnd(["{:,} {}".format(missCountsByReason[reason], description)
                         for reason, description in _RASTER_DEM_MISS_DESCRIPTIONS
                         if missCountsByReason.get(reason, 0) > 0])


def AddRasterDemMissCounts(totalMissCounts, missCountsByReason):
    """Add the counts of *missCountsByReason* to *totalMissCounts* (both
    reason: count) and return *totalMissCounts*."""
    for reason, missCount in missCountsByReason.items():
        totalMissCounts[reason] = totalMissCounts.get(reason, 0) + missCount
    return totalMissCounts


def PerspectiveCentreBelowRasterDemNote(imageLabel):
    """Console note on an image whose perspective centre lies below the
    raster DEM surface (see DemSurface.IsPerspectiveCentreBelowSurface)."""
    return ("{}: the perspective centre lies below the raster DEM surface, so every ray of this image is a "
            "miss.".format(imageLabel))


def RecordRasterDemReport(surface, rasterDemReport):
    """Add the misses of the raster DEM surface *surface* (a DemSurface)
    to *rasterDemReport*, a dict of the caller (nothing happens for
    None): missCountsByReason, the counts by reason added to those
    already there, and perspectiveCentreBelowSurface, True once the
    perspective centre of an image lies below the raster DEM surface
    (see DemSurface.IsPerspectiveCentreBelowSurface), which is read
    only when a ray has missed for that reason."""
    if rasterDemReport is None:
        return
    AddRasterDemMissCounts(rasterDemReport.setdefault("missCountsByReason", {}), surface.missCountsByReason)
    belowSurface = (surface.missCountsByReason.get(RASTER_DEM_MISS_PERSPECTIVE_CENTRE_BELOW_SURFACE, 0) > 0
                    and surface.IsPerspectiveCentreBelowSurface())
    rasterDemReport["perspectiveCentreBelowSurface"] = (
        bool(rasterDemReport.get("perspectiveCentreBelowSurface")) or belowSurface)


def _SampleAxisPositions(step, size):
    if size <= 0:
        return []
    count = (size - 1) // step + 1
    offset = ((size - 1) - (count - 1) * step) // 2
    return [offset + index * step for index in range(count)]


def SamplePixelGrid(pixelColumnCount, pixelRowCount, pixelStep):
    """Pixel indices (column, row) sampled every *pixelStep* pixels.

    The grid is centred in the frame, so the unsampled margins at
    opposite edges differ by at most one pixel.  The number of
    samples per axis is (size - 1) // pixelStep + 1.  A step of 1
    returns every pixel."""
    step = max(1, int(pixelStep))

    sampleAxisPositions = functools.partial(_SampleAxisPositions, step)

    columns = sampleAxisPositions(int(pixelColumnCount))
    return [(column, row) for row in sampleAxisPositions(int(pixelRowCount))
            for column in columns]


def SolveRayDemIntersection(heightAboveDem, verticalRate, *, stepParameter,
                            startParameter=0.0, tolerance=1e-3,
                            minimumDescentPerStep=0.0,
                            maximumParameter=_DEM_MAXIMUM_RAY_PARAMETER,
                            maximumStepCount=_DEM_MAXIMUM_MARCH_STEP_COUNT,
                            maximumNoDataStepCount=None):
    """Ray parameter t at which a ray first meets a height field.

    *heightAboveDem(t)* returns the height of the ray point above the
    surface, None where the surface has no data, and a value that is
    not finite where the ray can no longer meet the surface.
    *verticalRate* is d(ray height)/dt.

    The ray is followed in increasing t from *startParameter*, and no
    step exceeds *stepParameter*, the ray parameter over which the ray
    advances one raster DEM cell horizontally.  The first step that
    ends below the surface brackets the first crossing at the cell size
    of the raster DEM: a crossing over which the ray stays below the
    surface for at least one cell horizontally is never passed, a
    shorter one (a ray clipping a crest by a small amount) can be,
    because the ray can enter and leave the surface between two
    samples.  A root of the height difference alone, which a
    fixed-point iteration finds, can lie behind a ridge that hides it
    from the perspective centre.  A descending ray descends at most
    twice its height above the surface per step, or
    *minimumDescentPerStep* when that is more, so a steep ray reaches
    the surface in a few steps without passing far below it.  Illinois
    regula falsi then refines the bracket until the height difference
    is within *tolerance*.  Upward and horizontal rays are followed in
    the same way, because they can meet rising terrain (for example
    from a camera below a slope).

    Points without data carry no surface, so the march continues
    across them.  A hit beyond points without data is the first
    crossing found on the raster DEM data, because a gap is no evidence
    of a free view.  A ray that is below the surface at its first sample
    (a perspective centre below the surface), or that reappears below
    the surface after points without data (it met the surface where
    the raster DEM has no data), is a miss.  Once the ray has passed
    over data, a run of more than *maximumNoDataStepCount* steps
    without data (the cells of the raster DEM diagonal) shows that it
    has left the raster DEM, which also ends the search, and so does
    the step limit *maximumStepCount*.
    Returns t, or None for a miss, whose reason
    SolveRayDemIntersectionWithMissReason gives."""
    return SolveRayDemIntersectionWithMissReason(
        heightAboveDem, verticalRate, stepParameter=stepParameter, startParameter=startParameter,
        tolerance=tolerance, minimumDescentPerStep=minimumDescentPerStep, maximumParameter=maximumParameter,
        maximumStepCount=maximumStepCount, maximumNoDataStepCount=maximumNoDataStepCount)[0]


def SolveRayDemIntersectionWithMissReason(heightAboveDem, verticalRate, *, stepParameter,
                                          startParameter=0.0, tolerance=1e-3,
                                          minimumDescentPerStep=0.0,
                                          maximumParameter=_DEM_MAXIMUM_RAY_PARAMETER,
                                          maximumStepCount=_DEM_MAXIMUM_MARCH_STEP_COUNT,
                                          maximumNoDataStepCount=None):
    """(t, None) for a hit and (None, reason) for a miss of the march of
    SolveRayDemIntersection, which describes the arguments.  The reason
    is RASTER_DEM_MISS_PERSPECTIVE_CENTRE_BELOW_SURFACE when the first
    sample, at the perspective centre (t = 0), lies below or on the
    surface, RASTER_DEM_MISS_CROSSING_WITHOUT_DATA when the ray is below
    the surface at its first sample farther along the ray (it met the
    surface before the searched range) or after points without data,
    falls below the lowest height of the raster DEM over points without
    data, or reaches points without data in the refinement,
    RASTER_DEM_MISS_NO_CROSSING_WITHIN_EXTENT when the search range ends,
    the ray leaves the raster DEM after a run without data, or rises
    above its highest height, and RASTER_DEM_MISS_STEP_LIMIT when the
    step limit ends the search."""
    rayParameter = startParameter
    lastParameterAbove = heightAtLastParameterAbove = None
    noDataStepCount = None
    for _ in range(maximumStepCount):
        if not rayParameter <= maximumParameter:
            return None, RASTER_DEM_MISS_NO_CROSSING_WITHIN_EXTENT
        heightAboveSurface = heightAboveDem(rayParameter)
        if heightAboveSurface is None:
            # No surface here, and no bracket across the gap.
            lastParameterAbove = heightAtLastParameterAbove = None
            if noDataStepCount is not None:
                noDataStepCount += 1
                if maximumNoDataStepCount is not None and noDataStepCount > maximumNoDataStepCount:
                    return None, RASTER_DEM_MISS_NO_CROSSING_WITHIN_EXTENT
            rayParameter += stepParameter
            continue
        noDataStepCount = 0
        if not math.isfinite(heightAboveSurface):
            # The height above the surface is infinite where the ray can no
            # longer meet the surface: above its highest height while
            # rising or level, below its lowest over points without data
            # while descending (where it reached the surface level).
            return None, (RASTER_DEM_MISS_CROSSING_WITHOUT_DATA if verticalRate < 0.0
                          else RASTER_DEM_MISS_NO_CROSSING_WITHIN_EXTENT)
        # A sample close above the surface is not accepted as the hit:
        # along a grazing ray the tolerance in height spans several
        # times its length, so the crossing is bracketed and refined.
        if heightAboveSurface == 0.0:
            if rayParameter > 0.0:
                return rayParameter, None
            return None, RASTER_DEM_MISS_PERSPECTIVE_CENTRE_BELOW_SURFACE
        if heightAboveSurface < 0.0:
            if lastParameterAbove is None:
                # Below the surface without a sample above it before: at
                # the perspective centre itself (t = 0, the parameter only
                # grows), or farther along the ray, after points without
                # data or where the searched range begins.
                return None, (RASTER_DEM_MISS_PERSPECTIVE_CENTRE_BELOW_SURFACE if rayParameter == 0.0
                              else RASTER_DEM_MISS_CROSSING_WITHOUT_DATA)
            refinedParameter = _RefineRayDemBracket(heightAboveDem, lastParameterAbove, heightAtLastParameterAbove,
                                                    rayParameter, heightAboveSurface, tolerance)
            if refinedParameter is None:
                return None, RASTER_DEM_MISS_CROSSING_WITHOUT_DATA
            return refinedParameter, None
        lastParameterAbove, heightAtLastParameterAbove = rayParameter, heightAboveSurface
        step = stepParameter
        if verticalRate < 0.0:
            step = min(step, max(2.0 * heightAboveSurface, minimumDescentPerStep) / -verticalRate)
        rayParameter += step
    return None, RASTER_DEM_MISS_STEP_LIMIT


def _RefineRayDemBracket(heightAboveDem, lowerParameter, heightAtLowerParameter,
                         upperParameter, heightAtUpperParameter, tolerance,
                         maximumIterations=160):
    """Illinois regula falsi in a bracket of the ray parameter whose
    lower end lies above the surface and whose upper end lies below
    it: t with the height difference within *tolerance* (or the bracket
    end at a step in the surface), or None where the bracket reaches
    points without data."""
    lastMovedBracketEnd = 0
    for _ in range(maximumIterations):
        rayParameter = ((lowerParameter * heightAtUpperParameter - upperParameter * heightAtLowerParameter)
                        / (heightAtUpperParameter - heightAtLowerParameter))
        if not lowerParameter < rayParameter < upperParameter:
            rayParameter = 0.5 * (lowerParameter + upperParameter)
        heightAboveSurface = heightAboveDem(rayParameter)
        if heightAboveSurface is None or not math.isfinite(heightAboveSurface):
            return None
        if abs(heightAboveSurface) <= tolerance or upperParameter - lowerParameter <= 1e-9 * max(1.0, upperParameter):
            return rayParameter
        if heightAboveSurface > 0.0:
            lowerParameter, heightAtLowerParameter = rayParameter, heightAboveSurface
            if lastMovedBracketEnd == 1:
                heightAtUpperParameter *= 0.5
            lastMovedBracketEnd = 1
        else:
            upperParameter, heightAtUpperParameter = rayParameter, heightAboveSurface
            if lastMovedBracketEnd == -1:
                heightAtLowerParameter *= 0.5
            lastMovedBracketEnd = -1
    return None


def DemNormalFromSurfacePoints(firstMinus, firstPlus, secondMinus,
                               secondPlus):
    """Unit normal of a height field from four surface points taken
    one step either side of the hit point along two independent
    grid directions (central differences).  The sign is arbitrary,
    which the auxiliary-ray construction does not need.  Returns None if
    the points are degenerate."""
    normal = VectorCross(VectorSubtract(firstPlus, firstMinus),
                         VectorSubtract(secondPlus, secondMinus))
    length = VectorLength(normal)
    if not length > 0.0:
        return None
    return VectorScale(normal, 1.0 / length)


def _VectorToTuple(point):
    if point is None:
        return None
    try:
        return (point.x, point.y, point.z)
    except AttributeError:
        return (point[0], point[1], point[2])


class PlaneSurface:
    """Least-squares plane through the tie points of one image, in
    chunk coordinates.  The plane is its own tangent plane, so the
    rays of the auxiliary-ray construction meet it exactly.  Not usable when the
    tie points do not define a plane (fewer than three, or all on one
    line, see FitPlaneToPoints).  The rays are those of *rays* (ImageRays
    of the image by default), which follow the camera motion of an image
    with rolling-shutter compensation."""

    def __init__(self, camera, validProjections, chunkScale, chunk=None, rays=None):
        self.isUsable = False
        self.camera, self.chunk, self.scale = camera, chunk, chunkScale
        self.rays = rays if rays is not None else ImageRays(camera, "photogrammetryUtilities.PlaneSurface.Ray")
        self.origin = self.rays.perspectiveCentre
        if self.origin is None:
            return
        coordinates = []
        for point, _ in (validProjections or []):
            cartesian = HomogeneousToCartesian3D(point.coord)
            if cartesian:
                coordinates.append(cartesian)
        if len(coordinates) < 3:
            return
        self.planePoint, self.planeNormal = FitPlaneToPoints(coordinates)
        self.isUsable = (self.planePoint is not None
                   and self.planeNormal is not None)

    # Ray gives the ray of every image.
    def Ray(self, pixelX, pixelY):
        """(origin, direction) of the ray through (pixelX, pixelY)
        (ImageRays), or None."""
        return self.rays.Ray(pixelX, pixelY)

    @staticmethod
    def ChunkPoint(hitPoint):
        """The hit point in chunk coordinates (it is in them already)."""
        return tuple(hitPoint)

    def Hit(self, direction, origin=None):
        origin = self.origin if origin is None else origin
        rayParameter = IntersectRayWithPlane(origin, direction,
                                  self.planePoint, self.planeNormal)
        if rayParameter is None or rayParameter <= 0:
            return None
        offset = VectorScale(direction, rayParameter)
        return (VectorAdd(origin, offset), self.planeNormal,
                VectorLength(offset) * self.scale)

    def ToCrs(self, hitPoint):
        return _VectorToTuple(TransformChunkPointToCrs(hitPoint, self.chunk))


class MeshSurface:
    """Triangular mesh with N-ring averaged vertex normals, closest
    hit via the BVH, in chunk coordinates.  The rays are those of *rays*
    (ImageRays of the image by default)."""

    def __init__(self, camera, bvhData, chunkScale, chunk=None, rays=None):
        self.isUsable = False
        self.camera, self.chunk, self.scale = camera, chunk, chunkScale
        self.rays = rays if rays is not None else ImageRays(camera, "photogrammetryUtilities.MeshSurface.Ray")
        self.origin = self.rays.perspectiveCentre
        if self.origin is None or not bvhData:
            return
        self.bvhRootNode, self.vertexNormals = bvhData
        self.isUsable = self.bvhRootNode is not None

    def Ray(self, pixelX, pixelY):
        """(origin, direction) of the ray through (pixelX, pixelY)
        (ImageRays), or None."""
        return self.rays.Ray(pixelX, pixelY)

    @staticmethod
    def ChunkPoint(hitPoint):
        """The hit point in chunk coordinates (it is in them already)."""
        return tuple(hitPoint)

    def Hit(self, direction, origin=None):
        """Closest surface hit of a ray from *origin* (the perspective
        centre by default): (hit point, unit normal, slant distance in
        metres in a scaled chunk, in chunk units otherwise), or None for
        a miss."""
        origin = self.origin if origin is None else origin
        inverseDirection = tuple(SafeReciprocal(component)
                        for component in direction)
        result = _QueryBvhClosestHitDetailed(
            self.bvhRootNode, origin, direction, inverseDirection)
        if result is None:
            return None
        rayParameter, index0, index1, index2, barycentricU, barycentricV = result
        offset = VectorScale(direction, rayParameter)
        normal = InterpolateVertexNormal(
            self.vertexNormals, index0, index1, index2, barycentricU, barycentricV)
        if normal is None:
            return None
        return (VectorAdd(origin, offset), normal,
                VectorLength(offset) * self.scale)

    def ToCrs(self, hitPoint):
        return _VectorToTuple(TransformChunkPointToCrs(hitPoint, self.chunk))


def _PointAlongRay(direction, origin, rayParameter):
    return VectorAdd(origin, VectorScale(direction, rayParameter))


def _ElevationHeightRange(elevation):
    """(lowest, highest) height of the raster DEM from its documented
    min and max, or None when it does not report them."""
    lowestHeight = getattr(elevation, "min", None)
    highestHeight = getattr(elevation, "max", None)
    if not (isinstance(lowestHeight, (int, float)) and isinstance(highestHeight, (int, float))):
        return None
    if not (math.isfinite(lowestHeight) and math.isfinite(highestHeight)
            and _DEM_NODATA < lowestHeight <= highestHeight):
        return None
    return float(lowestHeight), float(highestHeight)


def _ElevationExtent(elevation):
    """(lowest x, highest x, lowest y, highest y) of the raster DEM in
    its own coordinates, from its left, right, bottom, and top sides, or
    None when it does not report them."""
    sides = [getattr(elevation, sideName, None) for sideName in ("left", "right", "bottom", "top")]
    if not all(isinstance(side, (int, float)) and math.isfinite(side) for side in sides):
        return None
    left, right, bottom, top = sides
    extent = (min(left, right), max(left, right), min(bottom, top), max(bottom, top))
    if not (extent[1] > extent[0] and extent[3] > extent[2]):
        return None
    return extent


def _ElevationColumnAndRowCounts(elevation):
    """(number of columns, number of rows) of the raster DEM from its
    width and height, or None when it does not report them."""
    columnCount = getattr(elevation, "width", None)
    rowCount = getattr(elevation, "height", None)
    if isinstance(columnCount, int) and isinstance(rowCount, int) and columnCount > 0 and rowCount > 0:
        return columnCount, rowCount
    return None


def _RayParameterRangeOverRectangle(startPoint, ratePerParameter, rectangle):
    """(entry, exit) of the ray parameter t over which the point
    startPoint + t * ratePerParameter (first two coordinates) lies
    within the rectangle (lowest x, highest x, lowest y, highest y), or
    None when it never does."""
    entryParameter, exitParameter = -math.inf, math.inf
    for axis in (0, 1):
        lowerBound, upperBound = rectangle[2 * axis], rectangle[2 * axis + 1]
        if ratePerParameter[axis] == 0.0:
            if not lowerBound <= startPoint[axis] <= upperBound:
                return None
            continue
        firstParameter = (lowerBound - startPoint[axis]) / ratePerParameter[axis]
        secondParameter = (upperBound - startPoint[axis]) / ratePerParameter[axis]
        entryParameter = max(entryParameter, min(firstParameter, secondParameter))
        exitParameter = min(exitParameter, max(firstParameter, secondParameter))
    if entryParameter > exitParameter:
        return None
    return entryParameter, exitParameter


class DemSurface:
    """Raster DEM, handled in work coordinates.

    Work coordinates: the local east-north-up coordinates of the chunk
    CRS at the perspective centre (metres) in a georeferenced chunk,
    otherwise the world coordinates of the chunk (metres in a scaled
    chunk, chunk units otherwise).  The raster DEM is queried through
    its own projection (geographic, projected, or planar), so raster DEM
    and chunk may use different CRSs.  Heights come only
    from the documented Elevation.altitude(), and the reported
    resolution, height range (min, max), and extent (left, right,
    bottom, top) bound the search: each ray is followed in steps of at
    most one raster DEM cell horizontally to the first crossing at the
    cell size of the raster
    DEM (see SolveRayDemIntersection), which is then refined to
    convergence (see Intersect), and the normal follows from the raster
    DEM heights one cell either side of the hit point.  A raster DEM that
    reports neither its height range nor its extent nor its numbers of
    columns and rows is searched from the perspective centre for at most
    200,000 steps (_DEM_MAXIMUM_MARCH_STEP_COUNT), so a crossing farther
    along the ray is missed.  Every miss of Intersect is counted by its
    reason in missCountsByReason (the RASTER_DEM_MISS constants), and
    IsPerspectiveCentreBelowSurface tells whether every ray of the image
    is a miss.  The rays are those of *rays* (ImageRays of the image by
    default), which follow the camera motion of an image with
    rolling-shutter compensation (Ray)."""

    def __init__(self, camera, chunk, rays=None):
        self.isUsable = False
        self.camera, self.chunk, self.scale = camera, chunk, 1.0
        self.missCountsByReason = {}
        self._perspectiveCentreBelowSurface = None
        self.elevation = getattr(chunk, "elevation", None)
        self.rays = rays if rays is not None else ImageRays(camera, "photogrammetryUtilities.DemSurface.Ray")
        perspectiveCentreChunk = self.rays.perspectiveCentre
        if self.elevation is None or perspectiveCentreChunk is None:
            return
        transform = getattr(chunk, "transform", None)
        self.toWorld = getattr(transform, "matrix", None)
        self.fromWorld = (self.toWorld.inv()
                          if self.toWorld is not None else None)
        perspectiveCentreWorld = self._ChunkToWorld(perspectiveCentreChunk)
        self.toWork = self.fromWork = None
        crs = getattr(chunk, "crs", None)
        # Local east-north-up coordinates only in a georeferenced chunk
        # (IsGeoreferencedChunk), as the About sections say.
        if crs is not None and IsGeoreferencedChunk(chunk):
            try:
                frame = crs.localframe(Metashape.Vector(list(perspectiveCentreWorld)))
                self.toWork, self.fromWork = frame, frame.inv()
            except Exception as suppressedError:
                # The world coordinates of a georeferenced chunk are
                # geocentric, in metres as well, so the results do not
                # change, but the failure is reported.
                ReportSuppressedError("photogrammetryUtilities.DemSurface.__init__.localFrame", suppressedError)
                self.toWork = self.fromWork = None
        self.projection = getattr(self.elevation, "projection", None)
        resolution = getattr(self.elevation, "resolution", None)
        self.resolution = (float(resolution)
                           if isinstance(resolution, (int, float))
                           and resolution > 0 else None)
        self.heightRange = _ElevationHeightRange(self.elevation)
        self.extent = _ElevationExtent(self.elevation)
        columnAndRowCounts = _ElevationColumnAndRowCounts(self.elevation)
        # Enough steps to cross the raster DEM several times, and a run
        # without data longer than its diagonal lies outside it.
        self.maximumMarchStepCount = (max(_DEM_MAXIMUM_MARCH_STEP_COUNT, 4 * sum(columnAndRowCounts))
                                      if columnAndRowCounts else _DEM_MAXIMUM_MARCH_STEP_COUNT)
        self.maximumNoDataStepCount = (int(math.hypot(*columnAndRowCounts)) + 2
                                       if columnAndRowCounts else None)
        self.origin = self._WorldToWork(perspectiveCentreWorld)
        self.upwardDirection = self._UpwardDirection()
        self.isUsable = True

    # ---- conversions between chunk, world, and work coordinates ----
    @staticmethod
    def _Apply(matrix, point):
        if matrix is None:
            return tuple(point)
        return _VectorToTuple(matrix.mulp(Metashape.Vector(list(point))))

    def _ChunkToWorld(self, point):
        return self._Apply(self.toWorld, point)

    def _WorldToChunk(self, point):
        return self._Apply(self.fromWorld, point)

    def _WorldToWork(self, point):
        return self._Apply(self.toWork, point)

    def _WorkToWorld(self, point):
        return self._Apply(self.fromWork, point)

    def _WorkToDem(self, point):
        world = self._WorkToWorld(point)
        if self.projection is None:
            return world
        return _VectorToTuple(self.projection.project(Metashape.Vector(list(world))))

    def _DemToWork(self, point):
        if self.projection is None:
            return self._WorldToWork(point)
        world = _VectorToTuple(self.projection.unproject(Metashape.Vector(list(point))))
        return self._WorldToWork(world)

    def _DemHeight(self, demX, demY):
        try:
            altitude = self.elevation.altitude(Metashape.Vector([demX, demY]))
        except Exception as suppressedError:
            ReportSuppressedError("photogrammetryUtilities.DemSurface._DemHeight", suppressedError)
            return None
        if altitude is None or not math.isfinite(altitude) or altitude <= _DEM_NODATA:
            return None
        return float(altitude)

    # ---- surface interface ----
    def StaticDirection(self, pixelX, pixelY):
        """Unit direction, in the work coordinates, of the ray from the
        perspective centre through the point of Camera.unproject at the
        image position (pixelX, pixelY), refined
        where its projection misses the position
        (ImageRays.UnprojectedPoint): the ray of an image without
        rolling-shutter compensation, which Ray uses for such an image
        only."""
        pointChunk = self.rays.UnprojectedPoint(pixelX, pixelY)
        if pointChunk is None:
            return None
        direction = VectorSubtract(
            self._WorldToWork(self._ChunkToWorld(pointChunk)), self.origin)
        length = VectorLength(direction)
        if not length > 0.0:
            return None
        return VectorScale(direction, 1.0 / length)

    def Ray(self, pixelX, pixelY):
        """(origin, unit direction) in the work coordinates of the ray
        through (pixelX, pixelY) (ImageRays), or None.  Without
        rolling-shutter compensation, or once the rays of the image have
        fallen back, the origin is the perspective centre and the
        direction that of StaticDirection."""
        if not self.rays.SolvesRays():
            direction = self.StaticDirection(pixelX, pixelY)
            return None if direction is None else (self.origin, direction)
        chunkRay = self.rays.Ray(pixelX, pixelY)
        if chunkRay is None:
            return None
        chunkOrigin, chunkDirection = chunkRay
        origin = self._WorldToWork(self._ChunkToWorld(chunkOrigin))
        direction = VectorSubtract(self._WorldToWork(self._ChunkToWorld(VectorAdd(chunkOrigin, chunkDirection))), origin)
        length = VectorLength(direction)
        if not length > 0.0:
            return None
        return origin, VectorScale(direction, 1.0 / length)

    def Intersect(self, direction, origin=None):
        """(hit point in the work coordinates, slant distance in metres
        in a scaled chunk, in chunk units otherwise) of the
        first crossing of the ray at the cell size of the raster DEM (see
        SolveRayDemIntersection), or None for a miss, whose reason is
        counted in missCountsByReason.

        The ray is followed from the perspective centre, or from where
        it descends below the highest raster DEM height, in steps of at
        most one raster DEM cell horizontally, and the first step that
        ends below
        the surface is refined until the height difference is below one
        hundredth of the raster DEM cell size (SolveRayDemIntersection).
        Rays in any direction are followed, so an upward or horizontal
        ray meets rising terrain.  The search ends where the ray can no
        longer meet the surface: above the highest raster DEM height
        while rising or level, below the lowest while descending, or
        beyond the raster DEM extent.  A perspective centre below the
        surface, and a ray that reaches the surface where the raster DEM
        has no data, give a miss.  The ray starts at *origin*, the
        perspective centre by default (Ray)."""
        origin = self.origin if origin is None else origin
        pointAlongRay = functools.partial(_PointAlongRay, direction, origin)
        perspectiveCentreDem = self._WorkToDem(origin)
        unitStepDem = self._WorkToDem(pointAlongRay(1.0))
        verticalRate = unitStepDem[2] - perspectiveCentreDem[2]
        # Horizontal advance of the ray per unit of the ray parameter in
        # the work coordinates, whatever the unit of the raster DEM
        # heights.
        upwardRate = (VectorDot(direction, self.upwardDirection)
                      if self.upwardDirection is not None else verticalRate)
        horizontalRate = math.sqrt(max(0.0, VectorDot(direction, direction) - upwardRate * upwardRate))
        cellSize = self.resolution if self.resolution else self._FallbackCellSize(perspectiveCentreDem)
        stepParameter = cellSize / horizontalRate if horizontalRate > 0.0 else math.inf
        searchRange, missReason = self._SearchRange(perspectiveCentreDem, unitStepDem, verticalRate, cellSize)
        if searchRange is None:
            return self._Miss(missReason)
        startParameter, endParameter = searchRange
        heightAboveDemAlongRay = functools.partial(
            self._HeightAboveDemAlongRay, pointAlongRay, verticalRate, cellSize)
        # The height tolerance follows the raster DEM cell size, so a
        # close-range raster DEM with sub-millimetre cells is resolved
        # as finely as an aerial one (a fixed 1 mm tolerance would exceed
        # several cells there).
        heightTolerance = (0.01 * self.resolution if self.resolution
                           else 1e-6)
        rayParameter, missReason = SolveRayDemIntersectionWithMissReason(
            heightAboveDemAlongRay, verticalRate, stepParameter=stepParameter,
            startParameter=startParameter, tolerance=heightTolerance,
            minimumDescentPerStep=cellSize, maximumParameter=endParameter,
            maximumStepCount=self.maximumMarchStepCount,
            maximumNoDataStepCount=self.maximumNoDataStepCount)
        if rayParameter is None:
            return self._Miss(missReason)
        # Anchor on the raster DEM surface point under the converged
        # ray point, so the tangent plane lies on the surface itself
        # whatever the solver tolerance, and measure the slant distance
        # to that anchored point.
        demPoint = self._WorkToDem(pointAlongRay(rayParameter))
        surfaceHeight = self._DemHeight(demPoint[0], demPoint[1])
        if surfaceHeight is None:
            return self._Miss(RASTER_DEM_MISS_CROSSING_WITHOUT_DATA)
        anchoredPoint = self._DemToWork((demPoint[0], demPoint[1], surfaceHeight))
        return anchoredPoint, VectorLength(VectorSubtract(anchoredPoint, origin))

    def _Miss(self, missReason):
        """Count a miss of Intersect by its reason and return None."""
        self.missCountsByReason[missReason] = self.missCountsByReason.get(missReason, 0) + 1
        return None

    def ResetMissCounts(self):
        """Forget the misses counted so far, before the rays of the image
        are cast again (ComputeWithOneKindOfRays)."""
        self.missCountsByReason = {}

    def IsPerspectiveCentreBelowSurface(self):
        """True when the raster DEM has data under the perspective centre
        and the centre lies below that surface (or on it), so that every
        ray of the image is a miss: each ray is then below the surface at
        its first sample, or descends from below the lowest raster DEM
        height.  The height is read once, when first asked."""
        if self._perspectiveCentreBelowSurface is None:
            belowSurface = False
            if self.isUsable:
                perspectiveCentreDem = self._WorkToDem(self.origin)
                surfaceHeight = self._DemHeight(perspectiveCentreDem[0], perspectiveCentreDem[1])
                belowSurface = surfaceHeight is not None and perspectiveCentreDem[2] <= surfaceHeight
            self._perspectiveCentreBelowSurface = belowSurface
        return self._perspectiveCentreBelowSurface

    def _SearchRange(self, perspectiveCentreDem, unitStepDem, verticalRate, cellSize):
        """Where along the ray a crossing with the raster DEM is
        possible: ((start, end) of the ray parameter, None), from where
        the ray descends below the highest raster DEM height and comes
        over the raster DEM extent to where it leaves that extent, each
        with a margin, or (None, reason of the miss) when the ray cannot
        meet the raster DEM at all.  The search relies on the height
        range and the extent that the raster DEM reports, and a raster
        DEM that reports neither leaves the whole ray, up to the step
        limit of the search."""
        startParameter = 0.0
        endParameter = _DEM_MAXIMUM_RAY_PARAMETER
        rayHeight = perspectiveCentreDem[2]
        if self.heightRange is not None:
            lowestHeight = self.heightRange[0] - cellSize
            highestHeight = self.heightRange[1] + cellSize
            if verticalRate < 0.0:
                if rayHeight < lowestHeight:
                    return None, RASTER_DEM_MISS_PERSPECTIVE_CENTRE_BELOW_SURFACE
                if rayHeight > highestHeight:
                    # Over a curved Earth the ray lies at or above this
                    # straight-line estimate, so the search starts early
                    # rather than late.
                    startParameter = (rayHeight - highestHeight) / -verticalRate
            elif rayHeight > highestHeight:
                return None, RASTER_DEM_MISS_NO_CROSSING_WITHIN_EXTENT
        if self.extent is not None:
            # One per cent of the extent and of the ray parameter absorbs
            # the slight curvature of the straight ray in raster DEM
            # coordinates and keeps the border cells inside.
            eastMargin = 0.01 * (self.extent[1] - self.extent[0])
            northMargin = 0.01 * (self.extent[3] - self.extent[2])
            widenedExtent = (self.extent[0] - eastMargin, self.extent[1] + eastMargin,
                             self.extent[2] - northMargin, self.extent[3] + northMargin)
            horizontalRates = (unitStepDem[0] - perspectiveCentreDem[0], unitStepDem[1] - perspectiveCentreDem[1])
            parameterRange = _RayParameterRangeOverRectangle(perspectiveCentreDem, horizontalRates, widenedExtent)
            if parameterRange is None:
                return None, RASTER_DEM_MISS_NO_CROSSING_WITHIN_EXTENT
            entryParameter, exitParameter = parameterRange
            startParameter = max(startParameter, entryParameter - 0.01 * abs(entryParameter))
            endParameter = min(endParameter, exitParameter + 0.01 * abs(exitParameter))
        if not startParameter <= endParameter:
            return None, RASTER_DEM_MISS_NO_CROSSING_WITHIN_EXTENT
        return (startParameter, endParameter), None

    def _HeightAboveDemAlongRay(self, pointAlongRay, verticalRate, cellSize, rayParameter):
        """Height of the ray point above the raster DEM surface, None
        where the raster DEM has no data, and infinity where the ray can
        no longer meet the surface: more than one cell above the highest
        raster DEM height while rising or level, or more than one cell
        below the lowest over points without data while descending."""
        demPoint = self._WorkToDem(pointAlongRay(rayParameter))
        if (self.heightRange is not None and not verticalRate < 0.0
                and demPoint[2] > self.heightRange[1] + cellSize):
            return math.inf
        surfaceHeight = self._DemHeight(demPoint[0], demPoint[1])
        if surfaceHeight is not None:
            return demPoint[2] - surfaceHeight
        if (self.heightRange is not None and verticalRate < 0.0
                and demPoint[2] < self.heightRange[0] - cellSize):
            return math.inf
        return None

    def _UpwardDirection(self):
        """Unit vector of increasing raster DEM height at the
        perspective centre, in the work coordinates, or None."""
        try:
            perspectiveCentreDem = self._WorkToDem(self.origin)
            upwardStep = VectorSubtract(
                self._DemToWork((perspectiveCentreDem[0], perspectiveCentreDem[1], perspectiveCentreDem[2] + 1.0)),
                self._DemToWork(perspectiveCentreDem))
        except Exception as suppressedError:
            ReportSuppressedError("photogrammetryUtilities.DemSurface._UpwardDirection", suppressedError)
            return None
        upwardDirection = VectorNormalize(upwardStep)
        return upwardDirection if VectorLength(upwardDirection) > 0.5 else None

    def _FallbackCellSize(self, perspectiveCentreDem):
        """Step of the search when the raster DEM does not report its
        resolution: one thousandth of the height of the perspective
        centre above the raster DEM below it (or above the lowest
        raster DEM height), and 1 mm when neither is known."""
        groundHeight = self._DemHeight(perspectiveCentreDem[0], perspectiveCentreDem[1])
        if groundHeight is None and self.heightRange is not None:
            groundHeight = self.heightRange[0]
        if groundHeight is None:
            return 1e-3
        return max(1e-3 * abs(perspectiveCentreDem[2] - groundHeight), 1e-6)

    def Normal(self, hitPoint):
        """Unit normal of the raster DEM at *hitPoint*, from the heights
        one cell either side along both grid axes."""
        centre = self._WorkToDem(hitPoint)
        spacing = (self.resolution if self.resolution
                   else 0.01 * VectorLength(VectorSubtract(hitPoint, self.origin)))
        steps = []
        anchor = self._DemToWork(centre)
        for axis in (0, 1):
            probe = 1e-6 * max(1.0, abs(centre[axis]))
            shifted = list(centre)
            shifted[axis] += probe
            metresPerUnit = VectorLength(VectorSubtract(
                self._DemToWork(tuple(shifted)), anchor)) / probe
            if not metresPerUnit > 0.0:
                return None
            steps.append(spacing / metresPerUnit)
        points = []
        for offsetX, offsetY in ((-steps[0], 0.0), (steps[0], 0.0),
                       (0.0, -steps[1]), (0.0, steps[1])):
            demX, demY = centre[0] + offsetX, centre[1] + offsetY
            height = self._DemHeight(demX, demY)
            if height is None:
                return None
            points.append(self._DemToWork((demX, demY, height)))
        return DemNormalFromSurfacePoints(*points)

    def Hit(self, direction, origin=None):
        """First surface hit of a ray from *origin* (the perspective centre
        by default), found at the cell size of the raster DEM (see
        Intersect): (hit point, unit normal, slant distance in metres in a
        scaled chunk, in chunk units otherwise), or None for a miss."""
        intersection = self.Intersect(direction, origin)
        if intersection is None:
            return None
        hitPoint, slantDistance = intersection
        normal = self.Normal(hitPoint)
        if normal is None:
            return None
        return hitPoint, normal, slantDistance

    def ToChunk(self, hitPoint):
        return self._WorldToChunk(self._WorkToWorld(hitPoint))

    def ChunkPoint(self, hitPoint):
        """The hit point, given in the work coordinates, in chunk
        coordinates."""
        return self.ToChunk(hitPoint)

    def ToCrs(self, hitPoint):
        world = self._WorkToWorld(hitPoint)
        crs = getattr(self.chunk, "crs", None)
        # Without a chunk transform the world coordinates are the internal
        # chunk coordinates, which are not projected, as in
        # TransformChunkPointToCrs and the CRS line of the CSV.
        if crs is not None and self.toWorld is not None:
            return _VectorToTuple(crs.project(Metashape.Vector(list(world))))
        return tuple(world)


def CollectGsdAtSampledPositions(camera, surface, pixelStep, includeCrs=False):
    """GSD at the sampled positions of the sampling grid, for any
    surface.

    For every sampled pixel position the central ray is intersected
    with the surface, rays meeting it beyond the grazing threshold are
    rejected, and the four auxiliary rays, through the midpoints of the
    edges of the corresponding photosite (+-1/2 along the row and the
    column in pixel coordinates), are intersected with the tangent plane
    at the hit.
    Returns (records, rejectedCount, sampledCount).  Each record holds
    pixelColumn, pixelRow, slantDistance (m), cosineIncidence, the
    measures of GsdFromProjectedSamplingSteps (gsdRow, gsdColumn,
    gsdGeometricMean, and projectedStepAngleDegrees), and gsdValue, the
    area-equivalent GSD, which every statistic, chart, and inspection marker
    of the GSD tools uses, plus crsX/crsY/crsZ when *includeCrs* is set.
    A sample whose steps span no area (parallel steps) is degenerate and
    gives no record.

    The rays are those of the surface (ImageRays).  The residual of the
    central hit of an image whose rays follow the camera motion is checked
    (CastCheckedRay), and when the rays of such an image fall back to the
    rays from the perspective centre part of the way, every sample is
    computed again with those (ComputeWithOneKindOfRays), so that no
    sample mixes the two kinds of rays."""
    result = ComputeWithOneKindOfRays(functools.partial(_CollectGsdRecords, camera, surface, pixelStep, includeCrs),
                                      getattr(surface, "ResetMissCounts", None))
    rays = getattr(surface, "rays", None)
    if rays is not None:
        rays.ReportFallBack()
    return result


def _SurfaceHit(surface, ray):
    """Hit of *surface* (its Hit) of the ray (origin, direction)."""
    return surface.Hit(ray[1], ray[0])


def _ChunkPointOfSurfaceHit(surface, hit):
    """The point of the hit (point, normal, slant distance) of *surface*
    in chunk coordinates."""
    return surface.ChunkPoint(hit[0])


def _CollectGsdRecords(camera, surface, pixelStep, includeCrs):
    """(records, rejectedCount, sampledCount) of
    CollectGsdAtSampledPositions, one pass over the sampled positions."""
    calibration = camera.calibration
    records = []
    rejectedCount = 0
    sampledPixels = SamplePixelGrid(calibration.width, calibration.height, pixelStep)
    findHit = functools.partial(_SurfaceHit, surface)
    chunkPointOfHit = functools.partial(_ChunkPointOfSurfaceHit, surface)
    for column, row in sampledPixels:
        centralRay, hit = CastCheckedRay(surface.rays, column + 0.5, row + 0.5, surface.Ray, findHit, chunkPointOfHit)
        if centralRay is None or hit is None:
            continue
        centralRayOrigin, centralRayDirection = centralRay
        hitPoint, normal, slantDistance = hit
        cosineIncidence = abs(VectorDot(VectorNormalize(centralRayDirection), normal))
        if cosineIncidence < GRAZING_COSINE_THRESHOLD:
            rejectedCount += 1
            continue
        auxiliaryRays = []
        for pixelX, pixelY in ((column, row + 0.5), (column + 1.0, row + 0.5),
                               (column + 0.5, row), (column + 0.5, row + 1.0)):
            auxiliaryRay = surface.Ray(pixelX, pixelY)
            if auxiliaryRay is None:
                break
            auxiliaryRays.append(auxiliaryRay)
        if len(auxiliaryRays) != 4:
            continue
        rowStep, columnStep = ComputeProjectedSamplingSteps(
            centralRayOrigin, [direction for _, direction in auxiliaryRays], hitPoint, normal, surface.scale,
            auxiliaryRayOrigins=[origin for origin, _ in auxiliaryRays])
        if rowStep is None:
            continue
        gsdMeasures = GsdFromProjectedSamplingSteps(rowStep, columnStep)
        if not gsdMeasures["gsdAreaEquivalent"] > 0.0:
            continue
        record = dict(pixelColumn=column, pixelRow=row,
                      slantDistance=slantDistance,
                      cosineIncidence=cosineIncidence,
                      gsdRow=gsdMeasures["gsdRow"], gsdColumn=gsdMeasures["gsdColumn"],
                      gsdGeometricMean=gsdMeasures["gsdGeometricMean"],
                      projectedStepAngleDegrees=gsdMeasures["projectedStepAngleDegrees"],
                      gsdValue=gsdMeasures["gsdAreaEquivalent"])
        if includeCrs:
            crsPoint = surface.ToCrs(hitPoint)
            record.update(crsX=crsPoint[0], crsY=crsPoint[1],
                          crsZ=crsPoint[2])
        records.append(record)
    return records, rejectedCount, len(sampledPixels)


def IsCameraUsable(camera):
    calibration = camera.calibration
    return (camera.transform is not None and calibration is not None
            and calibration.width and calibration.height
            and calibration.f and calibration.f > 0
            and camera.center is not None)


def ComputeDenseGridGsdOnPlane(camera, validProjections,
                               chunkScale, pixelStep, imageRays=None):
    """Per-image GSD statistics on the tie-point plane (exact on
    the plane), with the rays of *imageRays* (ImageRays of the image by
    default).  Returns a statistics dict or None."""
    if not IsCameraUsable(camera):
        return None
    surface = PlaneSurface(camera, validProjections, chunkScale, rays=imageRays)
    if not surface.isUsable:
        return None
    records, _, sampledCount = CollectGsdAtSampledPositions(camera, surface, pixelStep)
    return BuildGsdStatistics([record["gsdValue"] for record in records], sampledCount)


def ComputeDenseGridGsdOnMesh(camera, bvhData, chunkScale, pixelStep, imageRays=None):
    """Per-image GSD statistics on the triangular mesh (tangent plane
    from the interpolated N-ring vertex normal), with the rays of
    *imageRays* (ImageRays of the image by default).  *bvhData* is
    (bvhRootNode, vertexNormals) from BuildMeshBvh().  Returns a
    statistics dict or None."""
    if not IsCameraUsable(camera):
        return None
    surface = MeshSurface(camera, bvhData, chunkScale, rays=imageRays)
    if not surface.isUsable:
        return None
    records, _, sampledCount = CollectGsdAtSampledPositions(camera, surface, pixelStep)
    return BuildGsdStatistics([record["gsdValue"] for record in records], sampledCount)


def ComputeDenseGridGsdOnDem(camera, chunk, chunkScale, pixelStep, rasterDemReport=None, imageRays=None):
    """Per-image GSD statistics on the raster DEM, evaluated in the
    work coordinates of DemSurface, with the rays of *imageRays*
    (ImageRays of the image by default).  *chunkScale* is accepted
    for interface symmetry: the raster DEM distances are those of the
    work coordinates, in metres in a scaled chunk and in chunk units
    otherwise.  The misses of the raster DEM are added to
    *rasterDemReport* when it is a dict (see RecordRasterDemReport).
    Returns a statistics dict or None."""
    if not IsCameraUsable(camera):
        return None
    surface = DemSurface(camera, chunk, rays=imageRays)
    if not surface.isUsable:
        return None
    records, _, sampledCount = CollectGsdAtSampledPositions(camera, surface, pixelStep)
    RecordRasterDemReport(surface, rasterDemReport)
    return BuildGsdStatistics([record["gsdValue"] for record in records], sampledCount)


# ===================================================================
# Observation and unknown counts of the bundle adjustment (shared by
# the Interior Orientation Viewer and the Gradual Selection Filter,
# which therefore report the same redundancy)
# ===================================================================

# A tie-point network without control leaves the seven parameters of a
# 3D similarity transformation (three translations, three rotations,
# one scale) undetermined.
TIE_POINT_NETWORK_DATUM_DEFECT = 7

# A tie point, or a marker without a reference location, is determined
# only by rays from at least two images: with a single ray its three
# coordinates are not estimable, so it adds neither observations nor
# unknowns to the adjustment.
MINIMUM_RAYS_PER_OBJECT_POINT = 2


def GetSensorCalibration(sensor):
    """Calibration of an interior orientation group (Metashape sensor),
    adjusted if available."""
    return getattr(sensor, "calibration", None)


def GetImageCalibration(camera):
    """Calibration of one image: its own adjusted calibration
    (Camera.calibration), which can differ from that of its interior
    orientation group, or the calibration of the group when the image has
    none.  None when neither exists.  The Interior Orientation Viewer and
    the Gradual Selection Filter skip an image without one.  They
    compute the residuals not with it but
    with Camera.error (TiePointResidual), which also applies the
    rolling-shutter motion of the image."""
    imageCalibration = getattr(camera, "calibration", None)
    if imageCalibration is not None:
        return imageCalibration
    return GetSensorCalibration(getattr(camera, "sensor", None))


def TiePointResidual(camera, point, projection):
    """Residual of the 2D tie point *projection* of the 3D tie point
    *point* on the image *camera*, in pixels: Camera.error with the
    homogeneous coordinates of the 3D tie point in chunk coordinates
    (point.coord) and the measured position of the 2D tie point
    (projection.coord), as in the script of Agisoft's support (Agisoft
    forum, topic 11548, 22 November 2019).  Camera.error evaluates the
    whole camera model of the image, including the camera motion during
    the readout that Metashape estimates for an image with rolling-shutter
    compensation, which the calibration of the image with the static
    camera transform (Calibration.error) leaves out: in a probe in
    Metashape 2.3.2, that route gave
    21.09 px where Camera.error gave 1.7e-7 px.  The Interior Orientation
    Viewer and the Gradual Selection Filter compute their residuals with
    this function, and the Image Parameter Exporter calls Camera.error in
    the same way.  All three keep the 3D tie points at infinity (w = 0)
    and leave out the residuals that are not finite numbers."""
    return camera.error(point.coord, projection.coord)


# Coefficients of a Metashape.Calibration that a virtual calibration
# defines as zero: the lens distortion coefficients of the
# frame camera model, radial k1 to k4 and decentring p1 to p4
# (dimensionless), and the affinity and non-orthogonality terms b1 and
# b2 (in pixels), which are not lens distortion.
LENS_DISTORTION_COEFFICIENT_NAMES = ("k1", "k2", "k3", "k4", "p1", "p2", "p3", "p4")
AFFINITY_TERM_NAMES = ("b1", "b2")


def SetLensDistortionAndAffinityToZero(calibration):
    """Set all lens distortion coefficients of *calibration*, a
    Metashape.Calibration (radial k1 to k4 and decentring p1 to p4),
    and its affinity and non-orthogonality terms b1 and b2 to zero, so
    that they are defined as zero instead of being left to the defaults
    of the host.  The principal distance and the principal point are
    not changed.  The virtual camera of Depth & Render Export uses
    it.  Returns *calibration*."""
    for coefficientName in LENS_DISTORTION_COEFFICIENT_NAMES + AFFINITY_TERM_NAMES:
        setattr(calibration, coefficientName, 0.0)
    return calibration


def _EnumerationValue(className, enumerationName, memberName):
    """Metashape.<className>.<enumerationName>.<memberName>, or None
    when this version of the API does not have it."""
    return getattr(getattr(getattr(Metashape, className, None), enumerationName, None), memberName, None)


def _ObjectKey(item):
    """Identity of a Metashape object: its key, or the Python object
    itself when it has none (offline stand-ins)."""
    key = getattr(item, "key", None)
    return key if key is not None else id(item)


def IsKeyframe(camera):
    """True for a keyframe of an animation track: chunk.cameras holds
    keyframes next to the images, but they are viewpoints, not images,
    and take no part in the bundle adjustment."""
    keyframeType = _EnumerationValue("Camera", "Type", "Keyframe")
    cameraType = getattr(camera, "type", None)
    return keyframeType is not None and cameraType is not None and cameraType == keyframeType


def IsOrientedEnabledCamera(camera):
    """True for an enabled image with an estimated orientation, the
    images whose observations the MetaFOCUS counts of the bundle
    adjustment take (keyframes of an animation track never take part).
    Metashape also adjusts a disabled image that is still oriented and
    has 2D tie points or marker projections
    (DisabledOrientedImagesWithObservations), so these counts describe
    the adjustment only when there is no such image."""
    return (camera is not None and getattr(camera, "enabled", False)
            and getattr(camera, "transform", None) is not None
            and not IsKeyframe(camera))


def DisabledOrientedImagesWithTiePoints(chunk):
    """The disabled, oriented images of *chunk* (keyframes of an animation
    track left out) with at least one 2D tie point of a valid 3D tie
    point, in chunk order.  In a check in Metashape 2.3.2, Optimize
    Cameras adjusted such an image with the others, its reference
    location disabled as well, and its 2D tie points entered the RMS
    reprojection error of the processing report.  The counts and
    residuals of the enabled images (IsOrientedEnabledCamera) then do not
    describe the adjustment of Metashape.  A failure to read the
    projections of an image is reported in the Console, and the image
    counts as one without."""
    tiePoints = getattr(chunk, "tie_points", None)
    if tiePoints is None:
        return []
    # Usually no image is disabled, so the 3D tie points are read only
    # when there is a disabled, oriented image.
    disabledImages = [camera for camera in getattr(chunk, "cameras", []) or []
                      if camera is not None and not getattr(camera, "enabled", True)
                      and getattr(camera, "transform", None) is not None and not IsKeyframe(camera)]
    if not disabledImages:
        return []
    validTrackIds = set(getattr(point, "track_id", None) for point in getattr(tiePoints, "points", None) or []
                        if getattr(point, "valid", False))
    validTrackIds.discard(None)
    if not validTrackIds:
        return []
    images = []
    for camera in disabledImages:
        try:
            cameraProjections = tiePoints.projections[camera]
        except KeyError:
            continue
        except Exception as suppressedError:
            ReportSuppressedError("photogrammetryUtilities.DisabledOrientedImagesWithTiePoints", suppressedError)
            continue
        if any(getattr(projection, "track_id", None) in validTrackIds for projection in cameraProjections or []):
            images.append(camera)
    return images


def DisabledOrientedImagesWithObservations(chunk):
    """The disabled, oriented images of *chunk* (keyframes of an animation
    track left out) with at least one 2D tie point of a valid 3D tie point
    (DisabledOrientedImagesWithTiePoints) or at least one valid projection,
    pinned or unpinned, of a marker that is an object point
    (IsObjectSpaceMarker), in chunk order.  In a check in Metashape 2.3.2,
    Optimize Cameras adjusted a disabled, oriented image with nine pinned
    marker projections and no 2D tie point of a valid 3D tie point, and
    its marker projections and its enabled reference location changed the
    adjustment of the enabled images.  A failure to read the projections
    of a marker is reported in the Console, and the marker counts as one
    without."""
    disabledImages = [camera for camera in getattr(chunk, "cameras", []) or []
                      if camera is not None and not getattr(camera, "enabled", True)
                      and getattr(camera, "transform", None) is not None and not IsKeyframe(camera)]
    if not disabledImages:
        return []
    observedImages = list(DisabledOrientedImagesWithTiePoints(chunk))
    for marker in getattr(chunk, "markers", []) or []:
        if not IsObjectSpaceMarker(marker):
            continue
        try:
            projectionItems = list(_MarkerProjectionItems(marker))
        except Exception as suppressedError:
            ReportSuppressedError("photogrammetryUtilities.DisabledOrientedImagesWithObservations", suppressedError)
            continue
        for camera, projection in projectionItems:
            if (projection is not None and bool(getattr(projection, "valid", True))
                    and camera in disabledImages and camera not in observedImages):
                observedImages.append(camera)
    return [camera for camera in disabledImages if camera in observedImages]


DISABLED_ORIENTED_IMAGES_LABEL_LIMIT = 5


def DisabledOrientedImagesText(images, consequenceForOneImage, consequenceForImages,
                               limit=DISABLED_ORIENTED_IMAGES_LABEL_LIMIT):
    """The text that names the disabled images *images* that are still
    oriented and have 2D tie points or marker projections
    (DisabledOrientedImagesWithObservations), at most *limit* of them by
    label, followed by what such images mean for the values of the tool
    that shows the text: *consequenceForOneImage* for one image and
    *consequenceForImages* for more.  The tool names its own values,
    because the values that leave such images out differ from tool to
    tool.  An empty text without such images."""
    if not images:
        return ""
    labels = [str(getattr(image, "label", "")) for image in images]
    if len(labels) <= limit:
        listedLabels = JoinWithAnd(labels)
    else:
        listedLabels = "{}, and {:,} more".format(", ".join(labels[:limit]), len(labels) - limit)
    if len(images) == 1:
        return ("1 image is disabled but still oriented and has 2D tie points or marker projections ({}). "
                "{}".format(listedLabels, consequenceForOneImage))
    return ("{:,} images are disabled but still oriented and have 2D tie points or marker projections ({}). "
            "{}".format(len(images), listedLabels, consequenceForImages))


def IsMasterImage(camera):
    """True for an image that is its own master: a single image or the
    master image of a multi-camera rig station, never a slave image
    of a rig and never a keyframe of an animation track.  The tools
    that list, count, select, or switch images use this test."""
    return getattr(camera, "master", camera) == camera and not IsKeyframe(camera)


def CountMasterImages(chunk):
    """(master images, oriented master images) of the chunk: the images
    that the image tools list (see IsMasterImage)."""
    masterImages = [camera for camera in getattr(chunk, "cameras", []) or [] if IsMasterImage(camera)]
    return len(masterImages), sum(1 for camera in masterImages if camera.transform is not None)


# The selected images.  The Image Footprint Generator, the GSD Value
# Exporter, and Depth & Render Export read the selection from Metashape
# (Camera.selected) when they run.  In Geert's tests of 16 November 2026
# the Photos pane showed two images as selected while Metashape
# reported only one of them, and later none, so these tools show what
# Metashape reports (SelectedImagesStatusText), list the images of a run
# in the Console (SelectedImagesConsoleLines), and say what to do when no
# usable image is selected (NoSelectedImageText).  Geert's tests of 17
# November 2026 found the cause: Metashape clears its selection of images
# when another item of the Workspace pane is selected, and when markers
# are added through its Python interface, while the Photos pane keeps
# highlighting the images (WORKSPACE_SELECTION_NOTE).  In a later test of
# the same day, a click on an image that the Photos pane still highlighted
# did not select it in Metashape: after a click on the first of two such
# images and a Ctrl+click on the second, Metashape reported only the
# second.  After a click on another image first, it reported both
# (NO_SELECTED_IMAGE_ADVICE).
SELECTED_IMAGE_LABEL_LIMIT = 20
NO_SELECTED_IMAGE_ADVICE = ("If the Photos pane still shows images as selected, close this tool, click an image in "
                            "the Photos pane that is not highlighted, select the images again, and open the tool "
                            "again.")
WORKSPACE_SELECTION_NOTE = ("Selecting another item of the Workspace pane, such as the triangular mesh, the tie "
                            "points, or a folder, clears the selection of images that Metashape reports, although "
                            "the Photos pane may still highlight them.")


def SelectedMasterImages(chunk):
    """The master images of *chunk* (IsMasterImage) that Metashape reports
    as selected (Camera.selected), in chunk order."""
    return [camera for camera in getattr(chunk, "cameras", []) or []
            if IsMasterImage(camera) and getattr(camera, "selected", False)]


def SelectedImagesStatusText(selectedCount, usableCount, usablePhrase="oriented", unusablePhrase="not oriented"):
    """How many images Metashape reports as selected and how many of them a
    tool can use, for example "Metashape reports 2 selected images, 2 of
    them oriented." or "Metashape reports 1 selected image, without a depth
    map."."""
    if selectedCount == 0:
        return "Metashape reports no selected image."
    if selectedCount == 1:
        return "Metashape reports 1 selected image, {}.".format(usablePhrase if usableCount else unusablePhrase)
    usableText = "none of them" if usableCount == 0 else "{:,} of them".format(usableCount)
    return "Metashape reports {:,} selected images, {} {}.".format(selectedCount, usableText, usablePhrase)


def SelectedImagesConsoleLines(selectedCount, usedCameras, usablePhrase="oriented", unusablePhrase="not oriented",
                               limit=SELECTED_IMAGE_LABEL_LIMIT):
    """The Console lines of a run on the selected images: what Metashape
    reports (SelectedImagesStatusText), then the labels of the images that
    the run uses, at most *limit* of them."""
    lines = [ConsoleLabelledLine("Selection:", SelectedImagesStatusText(selectedCount, len(usedCameras), usablePhrase,
                                                                         unusablePhrase))]
    if not usedCameras:
        return lines
    labels = [str(camera.label) for camera in usedCameras]
    if len(labels) <= limit:
        listedLabels = JoinWithAnd(labels)
    else:
        listedLabels = "{}, and {:,} more".format(", ".join(labels[:limit]), len(labels) - limit)
    lines.append("  Used: " + listedLabels)
    return lines


def NoSelectedImageText(imageWords="oriented image"):
    """The message of a run on the selected images when Metashape reports
    no selected *imageWords* ("oriented image", "image with a depth map"):
    what clears the selection (WORKSPACE_SELECTION_NOTE) and what to do
    (NO_SELECTED_IMAGE_ADVICE)."""
    return "Metashape reports no selected {} in the active chunk.\n\n{} {}".format(
        imageWords, WORKSPACE_SELECTION_NOTE, NO_SELECTED_IMAGE_ADVICE)


def SelectedImageKeys(chunk):
    """The keys of the images of *chunk* that Metashape reports as selected
    (Camera.selected), as a set."""
    return {camera.key for camera in getattr(chunk, "cameras", []) or [] if getattr(camera, "selected", False)}


def ConsolePath(path, separator=os.sep):
    """*path* with forward slashes only, as Metashape writes its own paths
    and the dialogs of Metashape return them, where the operating system
    separates with backslashes (*separator*, Windows): a folder from a
    dialog joined with a file name otherwise mixes both kinds."""
    if separator == "\\":
        return path.replace("\\", "/")
    return path


def IsObjectSpaceMarker(marker):
    """False for the fiducial marks of scanned film images: they are
    measured on the images for the interior orientation, but they are
    not object points of the bundle adjustment."""
    fiducialType = _EnumerationValue("Marker", "Type", "Fiducial")
    markerType = getattr(marker, "type", None)
    return not (fiducialType is not None and markerType is not None and markerType == fiducialType)


def _MarkerProjectionItems(marker):
    """(image, projection) pairs of a marker, empty when it has none."""
    projections = getattr(marker, "projections", None)
    return projections.items() if hasattr(projections, "items") else []


def IsActiveMarkerProjection(camera, projection):
    """True when a marker projection counts as an image measurement of
    the adjustment in the counts of MetaFOCUS: it exists, it is valid
    (Marker.Projection.valid, which the Marker Residual Inspector clears
    when it invalidates a projection), and its image is enabled and
    oriented (IsOrientedEnabledCamera).  A projection without the valid
    flag counts as valid.  In a check in Metashape 2.3.2, Optimize Cameras
    also used a valid projection on a disabled image that was still
    oriented (DisabledOrientedImagesWithObservations)."""
    return (projection is not None and bool(getattr(projection, "valid", True))
            and IsOrientedEnabledCamera(camera))


def CountMarkerRays(marker):
    """Number of valid projections (pinned or unpinned) of the marker on
    enabled, oriented images (see IsActiveMarkerProjection): the image
    measurements of the marker that the counts of MetaFOCUS take."""
    return sum(1 for camera, projection in _MarkerProjectionItems(marker)
               if IsActiveMarkerProjection(camera, projection))


def HasEnabledReferenceLocation(marker):
    """True when the marker has a reference location and its reference
    is enabled, so that it acts as a control point."""
    reference = getattr(marker, "reference", None)
    return bool(reference is not None and getattr(reference, "enabled", False)
                and getattr(reference, "location", None) is not None)


def IsMarkerInAdjustment(marker):
    """True when the marker is an object point of the adjustment and
    so carries three unknowns: it is not a fiducial mark, and it has
    valid projections on at least two enabled, oriented images, or on
    one such image when an enabled reference location (a control point)
    supplies the missing observations."""
    if not IsObjectSpaceMarker(marker):
        return False
    rayCount = CountMarkerRays(marker)
    if rayCount >= MINIMUM_RAYS_PER_OBJECT_POINT:
        return True
    return rayCount >= 1 and HasEnabledReferenceLocation(marker)


def _IsScaleBarEndInAdjustment(end, observedStationKeys=None):
    """True when an end of a Metashape scale bar takes part in the
    adjustment: a marker in the adjustment, or an enabled, oriented
    image, and with *observedStationKeys* only an
    image of a camera station among them (ObservedStationKeys): the
    distance to an image involves the unknowns of its station."""
    if end is None:
        return False
    if hasattr(end, "projections"):
        return IsMarkerInAdjustment(end)
    return IsOrientedEnabledCamera(end) and (observedStationKeys is None
                                             or CameraStationKey(end) in observedStationKeys)


def CameraReferenceFlags(reference):
    """(location prior enabled, rotation prior enabled) of a camera
    reference.  Metashape keeps separate flags for the location
    (location_enabled, also reachable as enabled) and the rotation
    (rotation_enabled).  An older API without the rotation flag never
    used the rotation as a prior."""
    if reference is None:
        return False, False
    locationEnabled = bool(getattr(reference, "location_enabled", getattr(reference, "enabled", False)))
    rotationEnabled = bool(getattr(reference, "rotation_enabled", False))
    return locationEnabled, rotationEnabled


def CountMarkerImageObservations(chunk):
    """Scalar marker image observations: two per valid projection (pinned
    or unpinned) on an enabled, oriented image, of every marker in the
    adjustment (see IsMarkerInAdjustment)."""
    total = 0
    for marker in getattr(chunk, "markers", []) or []:
        try:
            if IsMarkerInAdjustment(marker):
                total += 2 * CountMarkerRays(marker)
        except Exception as suppressedError:
            ReportSuppressedError("photogrammetryUtilities.CountMarkerImageObservations", suppressedError)
    return total


def CountMarkerReferenceObservations(chunk):
    """Scalar marker reference observations: three per marker in the
    adjustment with an enabled reference location (a control point).
    A marker that takes no part in the adjustment constrains nothing."""
    total = 0
    for marker in getattr(chunk, "markers", []) or []:
        try:
            if HasEnabledReferenceLocation(marker) and IsMarkerInAdjustment(marker):
                total += 3
        except Exception as suppressedError:
            ReportSuppressedError("photogrammetryUtilities.CountMarkerReferenceObservations", suppressedError)
    return total


def CountScaleBarObservations(chunk, observedStationKeys=None):
    """Scalar reference-distance observations: one per Metashape scale
    bar with an enabled reference distance whose two ends take part in
    the adjustment (markers in the adjustment, or enabled, oriented
    images, with *observedStationKeys* only those of the camera stations
    among them, see _IsScaleBarEndInAdjustment)."""
    total = 0
    for scaleBar in getattr(chunk, "scalebars", []) or []:
        try:
            reference = getattr(scaleBar, "reference", None)
            if (reference and getattr(reference, "enabled", False)
                    and getattr(reference, "distance", None) is not None
                    and _IsScaleBarEndInAdjustment(getattr(scaleBar, "point0", None), observedStationKeys)
                    and _IsScaleBarEndInAdjustment(getattr(scaleBar, "point1", None), observedStationKeys)):
                total += 1
        except Exception as suppressedError:
            ReportSuppressedError("photogrammetryUtilities.CountScaleBarObservations", suppressedError)
    return total


def CountCameraPriorObservations(chunk, observedStationKeys=None):
    """Scalar camera prior observations of the enabled, oriented
    images: three per enabled reference location and three per
    enabled reference rotation, each with its own flag.  With
    *observedStationKeys* only those of the
    images of the camera stations among them (ObservedStationKeys): the
    priors of the images of a station that no image observation involves
    fix only the unknowns of that station, which the count leaves out.
    The prior of a rig master without image observations of its own
    still counts when a slave of its station has some."""
    total = 0
    try:
        for camera in getattr(chunk, "cameras", []) or []:
            if not IsOrientedEnabledCamera(camera):
                continue
            if observedStationKeys is not None and CameraStationKey(camera) not in observedStationKeys:
                continue
            reference = getattr(camera, "reference", None)
            locationEnabled, rotationEnabled = CameraReferenceFlags(reference)
            if locationEnabled and getattr(reference, "location", None) is not None:
                total += 3
            if rotationEnabled and getattr(reference, "rotation", None) is not None:
                total += 3
    except Exception as suppressedError:
        ReportSuppressedError("photogrammetryUtilities.CountCameraPriorObservations", suppressedError)
    return total

def CountInteriorOrientationUnknowns(chunk, observedImageKeys=None):
    """Adjusted interior orientation parameters (those in the
    calibration covariance), summed over the interior orientation
    groups with at least one enabled, oriented image: a group without
    such images takes no part in the adjustment.  With
    *observedImageKeys* only the groups with an
    image among them (ObservedImageKeys)."""
    usedSensorKeys = set()
    for camera in getattr(chunk, "cameras", []) or []:
        sensor = getattr(camera, "sensor", None)
        if sensor is not None and IsOrientedEnabledCamera(camera) and (
                observedImageKeys is None or _ObjectKey(camera) in observedImageKeys):
            usedSensorKeys.add(_ObjectKey(sensor))
    total = 0
    for sensor in getattr(chunk, "sensors", []) or []:
        if _ObjectKey(sensor) not in usedSensorKeys:
            continue
        total += InteriorOrientationUnknownsOfGroup(sensor)
    return total


def InteriorOrientationUnknownsOfGroup(sensor):
    """Adjusted interior orientation parameters of the interior
    orientation group *sensor*, those in its calibration covariance (0
    without them, and a failure to read them is reported once)."""
    try:
        calibration = GetSensorCalibration(sensor)
        parameterNames = getattr(calibration, "covariance_params", None) if calibration else None
        return len(parameterNames) if parameterNames else 0
    except Exception as suppressedError:
        ReportSuppressedError("photogrammetryUtilities.InteriorOrientationUnknownsOfGroup", suppressedError)
        return 0


def UnknownsLeavingWithImages(lostCameras, keptCameras):
    """The unknowns that leave the tie-point network with *lostCameras*,
    enabled, oriented images whose 2D tie points in the adjustment were
    all left out of the sum of sigma nought, while *keptCameras* keep some
    (for the sigma nought of the Gradual Selection
    Filter): six
    exterior orientation unknowns per camera station
    none of whose images is kept, the rolling-shutter unknowns of every
    lost image (RollingShutterUnknownsPerImage), and the adjusted interior
    orientation parameters of every interior orientation group none of
    whose images is kept (InteriorOrientationUnknownsOfGroup).  The counts
    of ComputeRedundancyBreakdown with the images of both as its
    tiePointImageKeys hold all of these, so subtracting them gives the
    unknowns of the network without the lost images.  A station or a group
    counts once, however many of its images are lost.  The lost images do
    not hold those without 2D tie points in the
    adjustment, which ComputeRedundancyBreakdown leaves out itself."""
    keptStationKeys = {_ObjectKey(getattr(camera, "master", None) or camera) for camera in keptCameras}
    keptSensorKeys = {_ObjectKey(camera.sensor) for camera in keptCameras
                      if getattr(camera, "sensor", None) is not None}
    lostStationKeys = set()
    lostSensorsByKey = {}
    unknowns = 0
    for camera in lostCameras:
        stationKey = _ObjectKey(getattr(camera, "master", None) or camera)
        if stationKey not in keptStationKeys:
            lostStationKeys.add(stationKey)
        sensor = getattr(camera, "sensor", None)
        if sensor is None:
            continue
        unknowns += RollingShutterUnknownsPerImage(sensor)
        if _ObjectKey(sensor) not in keptSensorKeys:
            lostSensorsByKey[_ObjectKey(sensor)] = sensor
    unknowns += 6 * len(lostStationKeys)
    for sensor in lostSensorsByKey.values():
        unknowns += InteriorOrientationUnknownsOfGroup(sensor)
    return unknowns


# Rolling-shutter parameters that Metashape estimates per image when
# compensation is enabled for its interior orientation group, as
# Agisoft's support described the two models (Agisoft forum, topic
# 14670, 31 July 2022): the Regularized model optimises "only two
# parameters" per image, the shift along x and y in the image plane, and
# the Full model six, the shift along and the rotation about three axes
# (Camera.shutter translation and rotation).  The Metashape 2.3 manual
# does not give these numbers.  The switch of older versions (True) and
# a model that is not recognised count as the Full model.
ROLLING_SHUTTER_UNKNOWNS_REGULARIZED = 2
ROLLING_SHUTTER_UNKNOWNS_FULL = 6


# The members of Metashape.Shutter.Model and the kind of setting that each
# stands for (RollingShutterSetting).
_ROLLING_SHUTTER_MEMBER_KINDS = (("Disabled", "disabled"), ("Regularized", "regularized"), ("Full", "full"))


def RollingShutterSetting(sensor):
    """(kind, name) of the rolling-shutter setting of the interior
    orientation group *sensor*: kind is "disabled", "regularized",
    "full", "switch" (True in versions where Sensor.rolling_shutter is a
    switch), or "unrecognised" (a model that none of the known members
    matches), and name is "Disabled", "Regularized", "Full", "on", or the
    last part of the name of an unrecognised model.  Only a value of the
    exact type bool is the switch of older versions, and every other
    value is compared with the members of Metashape.Shutter.Model, so
    the result does not depend on whether the enum of Metashape derives
    from int.  Without
    Shutter.Model the name of the value decides.  A setting that cannot
    be read is reported in the Console and counts as disabled."""
    try:
        shutterModel = getattr(sensor, "rolling_shutter", None)
    except Exception as suppressedError:
        ReportSuppressedError("photogrammetryUtilities.RollingShutterSetting", suppressedError)
        return "disabled", "Disabled"
    if shutterModel is None:
        return "disabled", "Disabled"
    if type(shutterModel) is bool:
        return ("switch", "on") if shutterModel else ("disabled", "Disabled")
    shutterModels = getattr(getattr(Metashape, "Shutter", None), "Model", None)
    for memberName, kind in _ROLLING_SHUTTER_MEMBER_KINDS:
        member = getattr(shutterModels, memberName, None)
        if member is not None and shutterModel == member:
            return kind, memberName
    lastNamePart = str(shutterModel).rsplit(".", 1)[-1]
    for memberName, kind in _ROLLING_SHUTTER_MEMBER_KINDS:
        if lastNamePart.lower() == kind:
            return kind, memberName
    return "unrecognised", lastNamePart or "on"


def IsRollingShutterCompensated(sensor):
    """True when rolling-shutter compensation is enabled for the
    interior orientation group *sensor* (RollingShutterSetting): the
    Regularized or the Full model, the switch of older versions, or a
    model that is not recognised."""
    return RollingShutterSetting(sensor)[0] != "disabled"


def RollingShutterUnknownsPerImage(sensor):
    """Rolling-shutter unknowns of one enabled, oriented image of the
    interior orientation group *sensor* (RollingShutterSetting): 0
    without compensation, ROLLING_SHUTTER_UNKNOWNS_REGULARIZED (2) for
    the Regularized model, and ROLLING_SHUTTER_UNKNOWNS_FULL (6) for the
    Full model, the switch of older versions, and a model that is not
    recognised."""
    kind = RollingShutterSetting(sensor)[0]
    if kind == "disabled":
        return 0
    return ROLLING_SHUTTER_UNKNOWNS_REGULARIZED if kind == "regularized" else ROLLING_SHUTTER_UNKNOWNS_FULL


def RollingShutterModelName(sensor):
    """Name of the rolling-shutter model of the interior orientation group
    *sensor* (RollingShutterSetting): "Regularized", "Full", "on" for the
    switch of older versions, the last part of the name of a model that
    is not recognised, or None without compensation."""
    kind, name = RollingShutterSetting(sensor)
    return None if kind == "disabled" else name


def ImagesWithRollingShutterCompensation(cameras):
    """The images of *cameras*, in their order, whose interior orientation
    group has rolling-shutter compensation (IsRollingShutterCompensated,
    read once per group)."""
    compensationBySensorKey = {}
    images = []
    for camera in cameras:
        sensor = getattr(camera, "sensor", None)
        if sensor is None:
            continue
        sensorKey = _ObjectKey(sensor)
        if sensorKey not in compensationBySensorKey:
            compensationBySensorKey[sensorKey] = IsRollingShutterCompensated(sensor)
        if compensationBySensorKey[sensorKey]:
            images.append(camera)
    return images


# The rays of an image with rolling-shutter compensation.  Metashape moves
# the camera of such an image during the readout, so each image row has a
# perspective centre
# of its own and, with the Full model, also a rotation of its own (the
# Regularized model shifts it along x and y in the image plane only), and
# Camera.unproject with two pixel coordinates gives one point per pixel
# position, not its ray.  The ray
# through a pixel position is the line of the points whose residual
# Camera.error, which applies the motion (TiePointResidual), is zero at
# that position (ImageRays), and when one ray of an image cannot be found
# in this way, every ray of that image starts at Camera.center, so
# that no result mixes the two kinds of rays (RollingShutterRayFailure).
ROLLING_SHUTTER_RAY_NOTE = ("their rays follow Metashape's camera model of each image, camera motion during the "
                            "readout included, each ray being the line of the points whose residual (Camera.error) is "
                            "zero at its pixel position, and when a ray of an image cannot be found in this way, every "
                            "ray of that image starts at its perspective centre and the Console says why")


def RollingShutterRayText(modelName, rayFailure=None):
    """Note of a GSD CSV line or a footprint attribute for an image whose
    interior orientation group has the rolling-shutter model *modelName*
    (RollingShutterModelName): whether its rays follow the camera motion
    during the readout, or, with *rayFailure* (ImageRays.failure), why
    every ray of the image starts at its perspective centre."""
    compensation = ("Rolling-shutter compensation in Metashape (switched on)" if modelName == "on"
                    else "{} compensation in Metashape".format(modelName))
    if rayFailure:
        return ("{}, not applied to the rays: {}, so every ray of the image starts at its perspective centre "
                "(Camera.center)".format(compensation, rayFailure))
    return ("{}, applied to the rays, each the line of the points whose residual (Camera.error) is zero at its "
            "pixel position, camera motion during the readout included".format(compensation))


# The near point of a ray is solved at the distance from Camera.center
# where the previous ray of the image met the surface, and the far point
# at twice that distance, so that the line is not extrapolated towards
# the scene: an error of a solved point grows in proportion to the ratio
# of its distance to that of the scene there.  The first ray of an image
# starts at ROLLING_SHUTTER_SOLVE_DISTANCES times the length of the
# vector to the point of Camera.unproject (about one chunk unit).  Any two
# points of a ray give the same line.
ROLLING_SHUTTER_SOLVE_DISTANCES = (100.0, 200.0)
ROLLING_SHUTTER_FAR_POINT_FACTOR = 2.0
# A point of a ray is solved when its residual is below the first value
# (pixels), and accepted when it stays below the second once a step no
# longer halves it, which allows for the precision of Camera.error.  The
# residual where the ray meets the surface (CastCheckedRay) may reach the
# third value before the ray is solved again at that distance.
ROLLING_SHUTTER_RESIDUAL_TOLERANCE_PIXELS = 1e-9
ROLLING_SHUTTER_ACCEPTED_RESIDUAL_PIXELS = 1e-4
ROLLING_SHUTTER_HIT_RESIDUAL_PIXELS = 1e-3
ROLLING_SHUTTER_MAXIMUM_ITERATIONS = 40
# A step is tried at most this many times, halved each time, while it
# does not reduce the residual, and only twice once the residual is
# below the accepted value.
ROLLING_SHUTTER_MAXIMUM_STEP_HALVINGS = 10
ROLLING_SHUTTER_ACCEPTED_STEP_HALVINGS = 2


class RollingShutterRayFailure(Exception):
    """Raised by ImageRays.FallBack at the first ray of an image with
    rolling-shutter compensation that cannot be found as a line of zero
    residual.  From then on every ray of the image starts at its
    perspective centre, and a computation that has cast rays of the image
    before casts them all again (ComputeWithOneKindOfRays), so that no
    result of the image mixes the two kinds of rays."""


def _ResidualAtPixel(camera, point, pixelX, pixelY):
    """Residual (Camera.error) of the chunk point *point* at the pixel
    position (pixelX, pixelY), with homogeneous coordinates as in
    TiePointResidual."""
    residual = camera.error(Metashape.Vector([point[0], point[1], point[2], 1.0]),
                            Metashape.Vector([pixelX, pixelY]))
    return float(residual[0]), float(residual[1])


def _ResidualJacobianRows(camera, point, pixelX, pixelY, residual, differenceStep):
    """Rows (d residual x / d point, d residual y / d point) of the
    numerical Jacobian of the residual at *point*."""
    columns = []
    for axis in range(3):
        shiftedPoint = list(point)
        shiftedPoint[axis] += differenceStep
        shiftedX, shiftedY = _ResidualAtPixel(camera, shiftedPoint, pixelX, pixelY)
        columns.append(((shiftedX - residual[0]) / differenceStep, (shiftedY - residual[1]) / differenceStep))
    return tuple(column[0] for column in columns), tuple(column[1] for column in columns)


def _MinimumNormStep(rowX, rowY, residual):
    """The smallest step J^T (J J^T)^-1 r that removes the residual r to
    first order (the step across the ray), or None."""
    productXX, productXY, productYY = VectorDot(rowX, rowX), VectorDot(rowX, rowY), VectorDot(rowY, rowY)
    determinant = productXX * productYY - productXY * productXY
    if not abs(determinant) > 0.0:
        return None
    multiplierX = (productYY * residual[0] - productXY * residual[1]) / determinant
    multiplierY = (-productXY * residual[0] + productXX * residual[1]) / determinant
    return VectorAdd(VectorScale(rowX, multiplierX), VectorScale(rowY, multiplierY))


def _SolveRayPoint(camera, pixelX, pixelY, startPoint, distance):
    """(point, None) for the point near *startPoint*, at about *distance*
    from the camera, whose residual at (pixelX, pixelY) is zero, or (None,
    the reason as a clause that starts with "because").  Newton's method
    with the smallest step that removes the residual, a numerical
    Jacobian, and a step that is halved while it does not reduce the
    residual, ROLLING_SHUTTER_MAXIMUM_STEP_HALVINGS times at most, and
    ROLLING_SHUTTER_ACCEPTED_STEP_HALVINGS times once the residual is
    below ROLLING_SHUTTER_ACCEPTED_RESIDUAL_PIXELS, below which the
    Jacobian of the last point above it is kept (the chord method), as it
    hardly changes over such small steps.  The iteration ends
    when the residual is below ROLLING_SHUTTER_RESIDUAL_TOLERANCE_PIXELS,
    or below ROLLING_SHUTTER_ACCEPTED_RESIDUAL_PIXELS once a step no longer
    halves it, because the precision of Camera.error then limits it (large
    coordinates, for example)."""
    point = startPoint
    residual = _ResidualAtPixel(camera, point, pixelX, pixelY)
    residualLength = math.hypot(*residual)
    differenceStep = 1e-6 * distance
    jacobianRows = None
    for _ in range(ROLLING_SHUTTER_MAXIMUM_ITERATIONS):
        if not math.isfinite(residualLength):
            return None, "because its residual is not a finite number"
        if residualLength < ROLLING_SHUTTER_RESIDUAL_TOLERANCE_PIXELS:
            return point, None
        if jacobianRows is None or residualLength >= ROLLING_SHUTTER_ACCEPTED_RESIDUAL_PIXELS:
            jacobianRows = _ResidualJacobianRows(camera, point, pixelX, pixelY, residual, differenceStep)
        step = _MinimumNormStep(*jacobianRows, residual)
        if step is None:
            break
        # No step longer than half the distance, and halved while the
        # residual does not fall.
        stepLength = VectorLength(step)
        if stepLength > 0.5 * distance:
            step = VectorScale(step, 0.5 * distance / stepLength)
        for _ in range(ROLLING_SHUTTER_ACCEPTED_STEP_HALVINGS if residualLength < ROLLING_SHUTTER_ACCEPTED_RESIDUAL_PIXELS
                       else ROLLING_SHUTTER_MAXIMUM_STEP_HALVINGS):
            candidatePoint = VectorSubtract(point, step)
            candidateResidual = _ResidualAtPixel(camera, candidatePoint, pixelX, pixelY)
            candidateLength = math.hypot(*candidateResidual)
            if math.isfinite(candidateLength) and candidateLength < residualLength:
                break
            step = VectorScale(step, 0.5)
        else:
            break
        previousLength = residualLength
        point, residual, residualLength = candidatePoint, candidateResidual, candidateLength
        if residualLength < ROLLING_SHUTTER_ACCEPTED_RESIDUAL_PIXELS and residualLength > 0.5 * previousLength:
            break
    if residualLength < ROLLING_SHUTTER_ACCEPTED_RESIDUAL_PIXELS:
        return point, None
    return None, "because its residual stays at {:.3g} px".format(residualLength)


def SolveRollingShutterRay(camera, pixelX, pixelY, perspectiveCentre, staticDirection, solveDistance=None):
    """((origin, direction), None) of the ray through the pixel position
    (pixelX, pixelY) of an image with rolling-shutter compensation, in
    chunk coordinates: the line through two points whose residual
    Camera.error is zero at that position (_SolveRayPoint), or (None, the
    reason as a clause that starts with "because").  The near point is
    found from the ray from the perspective centre along *staticDirection*
    (Camera.center and Camera.unproject), at *solveDistance* from it (in
    chunk units, ROLLING_SHUTTER_SOLVE_DISTANCES[0] times the length of
    *staticDirection* when None), and the far point at
    ROLLING_SHUTTER_FAR_POINT_FACTOR times that distance on the line from
    the perspective centre through the near point.  The origin is the
    point of the line nearest to the perspective centre, and the direction
    has the length of *staticDirection*.  Where the line meets the
    surface, CastCheckedRay checks it."""
    staticLength = VectorLength(staticDirection)
    if not staticLength > 0.0:
        return None, "because the point of Camera.unproject is the perspective centre itself"
    nearDistance = solveDistance if solveDistance else ROLLING_SHUTTER_SOLVE_DISTANCES[0] * staticLength
    farDistance = ROLLING_SHUTTER_FAR_POINT_FACTOR * nearDistance
    nearPoint, reason = _SolveRayPoint(
        camera, pixelX, pixelY, VectorAdd(perspectiveCentre, VectorScale(staticDirection, nearDistance / staticLength)),
        nearDistance)
    if nearPoint is None:
        return None, reason
    farPoint, reason = _SolveRayPoint(
        camera, pixelX, pixelY,
        VectorAdd(perspectiveCentre, VectorScale(VectorSubtract(nearPoint, perspectiveCentre), farDistance / nearDistance)),
        farDistance)
    if farPoint is None:
        return None, reason
    unitDirection = VectorNormalize(VectorSubtract(farPoint, nearPoint))
    if not VectorLength(unitDirection) > 0.5:
        return None, "because its two points coincide"
    origin = VectorAdd(nearPoint, VectorScale(
        unitDirection, VectorDot(VectorSubtract(perspectiveCentre, nearPoint), unitDirection)))
    return (origin, VectorScale(unitDirection, staticLength)), None


# ===================================================================
# Exact rays of Camera.unproject
# ===================================================================

# Metashape's Camera.unproject appears to invert the lens distortion by an
# iteration that stops when its steps become small, so that its ray need
# not project back onto its pixel position: 0.0064 px and 0.019 px at two
# corner cells of Geert's processing of Camillo Ressl's DJI M4E images (10
# October 2026), up to 0.041 px for that calibration, and up to 20 px for
# Camillo's own near the largest distorted radius of its radial
# distortion polynomial.
# Beyond that radius Metashape 2.3.2 keeps the radial factor of that
# radius for the calibration of an interior orientation group, which
# Camera.project uses, so that its projection keeps growing.  A
# Metashape.Calibration that belongs to no group follows the polynomial.
# A ray whose projection (Camera.project)
# lies within RAY_ACCEPTANCE_PIXELS of its position is taken as it is, any
# other is refined by Newton's method (ExactUnprojectedPoint), as the
# Interior Orientation Viewer refines the rays of its grid points,
# and a position whose ray cannot be brought within
# RAY_ROUND_TRIP_TOLERANCE_PIXELS has no ray.
RAY_ACCEPTANCE_PIXELS = 1e-6
RAY_ROUND_TRIP_TOLERANCE_PIXELS = 1e-3

# Newton's method of ExactUnprojectedPoint: at most this many iterations,
# the step of its numerical derivatives (in normalised image coordinates),
# and how close the projection must come to the position (px) for it to
# stop.  The chord method (_ChordRefinedCameraPoint) keeps the derivatives
# from step to step while each step brings the projection at least
# RAY_REFINEMENT_RENEWAL_CONTRACTION times closer, and renews them
# otherwise (to save calls of Camera.project).
RAY_REFINEMENT_MAXIMUM_ITERATIONS = 60
RAY_REFINEMENT_DERIVATIVE_STEP = 1e-6
RAY_REFINEMENT_STOP_PIXELS = 1e-9
RAY_REFINEMENT_RENEWAL_CONTRACTION = 0.1


def NewRayReport():
    """The counts of the rays of one image (ImageRays.rayReport): the rays
    checked by a round trip, those refined because Metashape's
    unprojection had not converged within RAY_ROUND_TRIP_TOLERANCE_PIXELS
    (with the largest of their round trips), those refined because the
    point of Metashape's unprojection could not be projected back, so that
    it has no round trip ("refinedWithoutRoundTrip"), and the pixel
    positions without a ray.  Rays
    that missed by less are refined without being counted, as in the
    Interior Orientation Viewer."""
    return {"checked": 0, "refined": 0, "refinedWithoutRoundTrip": 0, "withoutRay": 0,
            "largestUnrefinedRoundTripPixels": 0.0}


def FormatRoundTripPixels(value):
    """*value* (positive, in px) for ExactRayText: three significant
    digits below 1,000 and whole pixels from 1,000 on, with thousands
    separators, without an exponent, and without trailing zeros, for
    example 0.0194, 0.002, 153, and 1,535."""
    if not (value > 0.0 and math.isfinite(value)):
        return "{:g}".format(value)
    decimalCount = max(0, 2 - int(math.floor(math.log10(value))))
    text = "{:,.{}f}".format(value, decimalCount)
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def ExactRayText(rayReport):
    """The text on the rays of one image for the Console, the comment
    lines of a GSD CSV file, and the attribute 00_Rays of a footprint, or
    "" when every ray of Camera.unproject was taken or refined without
    being counted (NewRayReport)."""
    refinedCount = rayReport.get("refined", 0)
    refinedWithoutRoundTripCount = rayReport.get("refinedWithoutRoundTrip", 0)
    withoutRayCount = rayReport.get("withoutRay", 0)
    checkedText = FormatCountWithNoun(rayReport.get("checked", 0), "pixel position", "pixel positions")
    sentences = []
    if refinedCount:
        # The refined positions only: a position without a ray whose ray of
        # Camera.unproject also missed is counted in the last sentence.
        sentences.append("for {} of the {}, the {} of Metashape's unprojection (Camera.unproject) projected back more "
                         "than {} px from the pixel position (up to {} px), so MetaFOCUS refined {} by Newton's "
                         "method.".format(
                             "{:,}".format(refinedCount), checkedText,
                             "ray" if refinedCount == 1 else "rays",
                             FormatRoundTripPixels(RAY_ROUND_TRIP_TOLERANCE_PIXELS),
                             FormatRoundTripPixels(rayReport.get("largestUnrefinedRoundTripPixels", 0.0)),
                             "it" if refinedCount == 1 else "them"))
    if refinedWithoutRoundTripCount:
        sentences.append("for {} of the {}, the point of Metashape's unprojection (Camera.unproject) could not be "
                         "projected back, so MetaFOCUS refined {} by Newton's method.".format(
                             "{:,}".format(refinedWithoutRoundTripCount), checkedText,
                             "its ray" if refinedWithoutRoundTripCount == 1 else "their rays"))
    if withoutRayCount:
        # The sentence gives no fold as an example: beyond the largest
        # distorted radius of its radial distortion polynomial Metashape
        # 2.3.2 keeps the radial factor of that radius for the calibration
        # of an interior orientation group, so that the positions there
        # have rays.
        sentences.append("MetaFOCUS finds no ray for {}, so {} left out.".format(
            FormatCountWithNoun(withoutRayCount, "pixel position", "pixel positions"),
            "it is" if withoutRayCount == 1 else "they are"))
    if not sentences:
        return ""
    # Every sentence after the first starts with a capital letter.
    return "Rays: " + " ".join([sentences[0]] + [sentence[:1].upper() + sentence[1:] for sentence in sentences[1:]])


def _CameraProjectionAt(camera, cameraToChunk, normalisedX, normalisedY):
    """The pixel position (x, y) that Camera.project gives for the point
    (normalisedX, normalisedY, 1) of the camera coordinates, or None."""
    try:
        projected = camera.project(cameraToChunk.mulp(Metashape.Vector([normalisedX, normalisedY, 1.0])))
    except Exception as suppressedError:
        ReportSuppressedError("photogrammetryUtilities._CameraProjectionAt", suppressedError)
        return None
    if projected is None:
        return None
    try:
        projectedX, projectedY = float(projected[0]), float(projected[1])
    except (TypeError, ValueError, IndexError):
        return None
    if not (math.isfinite(projectedX) and math.isfinite(projectedY)):
        return None
    return projectedX, projectedY


def _ForwardDifferenceJacobian(camera, cameraToChunk, normalisedX, normalisedY, projection):
    """(dx/du, dx/dv, dy/du, dy/dv): the derivatives of the pixel position
    (x, y) of Camera.project with respect to the point (u, v, 1) of the
    camera coordinates, by forward differences from *projection*, the
    pixel position of the point itself (two calls of Camera.project), or
    None (for _ChordRefinedCameraPoint)."""
    step = RAY_REFINEMENT_DERIVATIVE_STEP
    alongU = _CameraProjectionAt(camera, cameraToChunk, normalisedX + step, normalisedY)
    alongV = _CameraProjectionAt(camera, cameraToChunk, normalisedX, normalisedY + step)
    if alongU is None or alongV is None:
        return None
    return ((alongU[0] - projection[0]) / step, (alongV[0] - projection[0]) / step,
            (alongU[1] - projection[1]) / step, (alongV[1] - projection[1]) / step)


def _CentralDifferenceJacobian(camera, cameraToChunk, normalisedX, normalisedY):
    """(dx/du, dx/dv, dy/du, dy/dv): the derivatives of the pixel position
    (x, y) of Camera.project with respect to the point (u, v, 1) of the
    camera coordinates, by central differences (four calls of
    Camera.project), or None (for _CarefulRefinedCameraPoint)."""
    step = RAY_REFINEMENT_DERIVATIVE_STEP
    columns = []
    for offsetX, offsetY in ((step, 0.0), (0.0, step)):
        plus = _CameraProjectionAt(camera, cameraToChunk, normalisedX + offsetX, normalisedY + offsetY)
        minus = _CameraProjectionAt(camera, cameraToChunk, normalisedX - offsetX, normalisedY - offsetY)
        if plus is None or minus is None:
            return None
        columns.append(((plus[0] - minus[0]) / (2.0 * step), (plus[1] - minus[1]) / (2.0 * step)))
    (derivativeXU, derivativeYU), (derivativeXV, derivativeYV) = columns
    return derivativeXU, derivativeXV, derivativeYU, derivativeYV


def _JacobianDeterminant(jacobian):
    """The determinant of the derivatives (_ForwardDifferenceJacobian,
    _CentralDifferenceJacobian), or 0.0 without them."""
    if jacobian is None:
        return 0.0
    derivativeXU, derivativeXV, derivativeYU, derivativeYV = jacobian
    return derivativeXU * derivativeYV - derivativeXV * derivativeYU


def _NewtonStep(jacobian, current, pixelX, pixelY):
    """(stepU, stepV): the Newton step from the point whose projection is
    *current* towards (pixelX, pixelY) with the derivatives *jacobian*,
    whose determinant is positive."""
    derivativeXU, derivativeXV, derivativeYU, derivativeYV = jacobian
    determinant = _JacobianDeterminant(jacobian)
    errorX, errorY = current[0] - pixelX, current[1] - pixelY
    return ((derivativeYV * errorX - derivativeXV * errorY) / determinant,
            (-derivativeYU * errorX + derivativeXU * errorY) / determinant)


def _ChordRefinedCameraPoint(camera, cameraToChunk, startX, startY, startProjection, pixelX, pixelY):
    """(u, v, distance) of the point (u, v, 1) of the camera coordinates
    whose projection (Camera.project) lies closest to (pixelX, pixelY), by
    the chord method started at (startX, startY), whose projection
    *startProjection* is known (or None): Newton's method whose
    derivatives (forward differences) are kept while each full step brings
    the projection at least RAY_REFINEMENT_RENEWAL_CONTRACTION times
    closer and renewed otherwise, with each step halved until the
    projection comes closer.  The determinant of the derivatives must be
    positive at the start, wherever they are renewed, and at the end, so
    that the point lies on the branch of the mapping that does not fold
    back (the raw radial distortion polynomial would fold back beyond its
    largest distorted radius, whereas Metashape 2.3.2 keeps the radial
    factor of that radius there for the calibration of an interior
    orientation group).  None when it is not or when the
    projection fails, also when only an intermediate point has a
    determinant that is not positive (_RefinedCameraPoint then falls back
    to _CarefulRefinedCameraPoint)."""
    normalisedX, normalisedY = startX, startY
    current = startProjection
    if current is None:
        current = _CameraProjectionAt(camera, cameraToChunk, normalisedX, normalisedY)
    if current is None:
        return None
    jacobian = _ForwardDifferenceJacobian(camera, cameraToChunk, normalisedX, normalisedY, current)
    if not _JacobianDeterminant(jacobian) > 0.0:
        return None
    isJacobianAtCurrentPoint = True
    distance = math.hypot(current[0] - pixelX, current[1] - pixelY)
    for _ in range(RAY_REFINEMENT_MAXIMUM_ITERATIONS):
        if distance <= RAY_REFINEMENT_STOP_PIXELS:
            break
        stepU, stepV = _NewtonStep(jacobian, current, pixelX, pixelY)
        stepScale = 1.0
        accepted = False
        while stepScale >= 1.0 / 4096.0:
            candidateX, candidateY = normalisedX - stepScale * stepU, normalisedY - stepScale * stepV
            candidate = _CameraProjectionAt(camera, cameraToChunk, candidateX, candidateY)
            if candidate is not None:
                candidateDistance = math.hypot(candidate[0] - pixelX, candidate[1] - pixelY)
                if candidateDistance < distance:
                    accepted = True
                    break
            stepScale *= 0.5
        if not accepted and isJacobianAtCurrentPoint:
            break
        if accepted:
            isFastStep = stepScale == 1.0 and candidateDistance <= RAY_REFINEMENT_RENEWAL_CONTRACTION * distance
            normalisedX, normalisedY = candidateX, candidateY
            current, distance = candidate, candidateDistance
            isJacobianAtCurrentPoint = False
            if isFastStep or distance <= RAY_REFINEMENT_STOP_PIXELS:
                continue
        # The derivatives of an earlier point no longer lead fast enough
        # to the position: renew them at the current point.
        jacobian = _ForwardDifferenceJacobian(camera, cameraToChunk, normalisedX, normalisedY, current)
        if not _JacobianDeterminant(jacobian) > 0.0:
            return None
        isJacobianAtCurrentPoint = True
    if not isJacobianAtCurrentPoint and not _JacobianDeterminant(
            _ForwardDifferenceJacobian(camera, cameraToChunk, normalisedX, normalisedY, current)) > 0.0:
        return None
    return normalisedX, normalisedY, distance


def _CarefulRefinedCameraPoint(camera, cameraToChunk, startX, startY, pixelX, pixelY):
    """(u, v, distance) of the point (u, v, 1) of the camera coordinates
    whose projection (Camera.project) lies closest to (pixelX, pixelY), by
    Newton's method started at (startX, startY) with derivatives by
    central differences at every point, with each step halved until the
    projection comes closer at a point where the determinant of the
    derivatives is positive, as RefinedRay of the Interior Orientation
    Viewer (about eleven
    calls of Camera.project per ray).  None when no such start exists."""
    normalisedX, normalisedY = startX, startY
    current = _CameraProjectionAt(camera, cameraToChunk, normalisedX, normalisedY)
    jacobian = _CentralDifferenceJacobian(camera, cameraToChunk, normalisedX, normalisedY)
    if current is None or not _JacobianDeterminant(jacobian) > 0.0:
        return None
    distance = math.hypot(current[0] - pixelX, current[1] - pixelY)
    for _ in range(RAY_REFINEMENT_MAXIMUM_ITERATIONS):
        if distance <= RAY_REFINEMENT_STOP_PIXELS:
            break
        stepU, stepV = _NewtonStep(jacobian, current, pixelX, pixelY)
        stepScale = 1.0
        accepted = False
        while stepScale >= 1.0 / 4096.0:
            candidateX, candidateY = normalisedX - stepScale * stepU, normalisedY - stepScale * stepV
            candidate = _CameraProjectionAt(camera, cameraToChunk, candidateX, candidateY)
            if candidate is not None:
                candidateDistance = math.hypot(candidate[0] - pixelX, candidate[1] - pixelY)
                if candidateDistance < distance:
                    candidateJacobian = _CentralDifferenceJacobian(camera, cameraToChunk, candidateX, candidateY)
                    if _JacobianDeterminant(candidateJacobian) > 0.0:
                        accepted = True
                        break
            stepScale *= 0.5
        if not accepted:
            break
        normalisedX, normalisedY = candidateX, candidateY
        current, distance, jacobian = candidate, candidateDistance, candidateJacobian
    return normalisedX, normalisedY, distance


def _RefinedCameraPoint(camera, cameraToChunk, startX, startY, startProjection, pixelX, pixelY):
    """(u, v, distance) of the point (u, v, 1) of the camera coordinates
    whose projection (Camera.project) lies closest to (pixelX, pixelY)
    where the determinant of the derivatives is positive, a locally
    converged inverse, started at (startX, startY),
    whose projection *startProjection* is known (or None), or None.  The
    chord method (_ChordRefinedCameraPoint) takes about six calls of
    Camera.project per ray.  When it does not come within
    RAY_ACCEPTANCE_PIXELS, for example because the determinant at an
    intermediate point is not positive, Newton's method with a check of
    the determinant at every step (_CarefulRefinedCameraPoint)
    runs from the same start, so that the chord method
    loses no ray that this method finds,
    and the closer of the two results is taken."""
    refined = _ChordRefinedCameraPoint(camera, cameraToChunk, startX, startY, startProjection, pixelX, pixelY)
    if refined is not None and refined[2] <= RAY_ACCEPTANCE_PIXELS:
        return refined
    careful = _CarefulRefinedCameraPoint(camera, cameraToChunk, startX, startY, pixelX, pixelY)
    if careful is None or (refined is not None and refined[2] <= careful[2]):
        return refined
    return careful


def ExactUnprojectedPoint(camera, unprojectedPoint, pixelX, pixelY, cameraToChunk, chunkToCamera, rayReport):
    """The point of the ray of (pixelX, pixelY) in chunk coordinates: the
    point of Camera.unproject, *unprojectedPoint*, when its projection
    (Camera.project) lies within RAY_ACCEPTANCE_PIXELS of the position,
    otherwise the point (u, v, 1) of the camera coordinates refined by
    Newton's method (_RefinedCameraPoint) in chunk coordinates.  When no
    refined point comes within RAY_ROUND_TRIP_TOLERANCE_PIXELS, the point
    of Camera.unproject is kept if it does (a ray 90 degrees or more from
    the camera axis of a fisheye, which the refinement does not reach),
    and otherwise the position has no ray (None).  Without Camera.project or the camera
    transform (*cameraToChunk*, *chunkToCamera*) the point is taken as it
    is.  *rayReport* (NewRayReport) counts the rays."""
    if getattr(camera, "project", None) is None or cameraToChunk is None or chunkToCamera is None:
        return unprojectedPoint
    rayReport["checked"] += 1
    roundTrip = projectedPixel = None
    try:
        projected = camera.project(Metashape.Vector(list(unprojectedPoint)))
        if projected is not None:
            projectedPixel = (float(projected[0]), float(projected[1]))
            roundTrip = math.hypot(projectedPixel[0] - pixelX, projectedPixel[1] - pixelY)
    except Exception as suppressedError:
        ReportSuppressedError("photogrammetryUtilities.ExactUnprojectedPoint", suppressedError)
    if roundTrip is not None and not math.isfinite(roundTrip):
        roundTrip = projectedPixel = None
    if roundTrip is not None and roundTrip <= RAY_ACCEPTANCE_PIXELS:
        return unprojectedPoint
    hasConverged = roundTrip is not None and roundTrip <= RAY_ROUND_TRIP_TOLERANCE_PIXELS
    refined = None
    try:
        cameraPoint = VectorFromMetashape(chunkToCamera.mulp(Metashape.Vector(list(unprojectedPoint))))
    except Exception as suppressedError:
        ReportSuppressedError("photogrammetryUtilities.ExactUnprojectedPoint.camera", suppressedError)
        cameraPoint = None
    if cameraPoint is not None and cameraPoint[2] > 0.0:
        refined = _RefinedCameraPoint(camera, cameraToChunk, cameraPoint[0] / cameraPoint[2],
                                      cameraPoint[1] / cameraPoint[2], projectedPixel, pixelX, pixelY)
    if refined is None or not refined[2] <= RAY_ROUND_TRIP_TOLERANCE_PIXELS:
        if hasConverged:
            return unprojectedPoint
        rayReport["withoutRay"] += 1
        return None
    if not hasConverged:
        if roundTrip is None:
            # The point of Camera.unproject could not be projected back, so
            # there is no round trip to report: counted apart.
            rayReport["refinedWithoutRoundTrip"] = rayReport.get("refinedWithoutRoundTrip", 0) + 1
        else:
            rayReport["refined"] += 1
            rayReport["largestUnrefinedRoundTripPixels"] = max(rayReport["largestUnrefinedRoundTripPixels"], roundTrip)
    return VectorFromMetashape(cameraToChunk.mulp(Metashape.Vector([refined[0], refined[1], 1.0])))


class ImageRays:
    """Rays through the pixel positions of one image, in chunk
    coordinates, as (origin, direction).  Without rolling-shutter
    compensation the ray starts at the perspective centre (Camera.center)
    and passes through the point of Camera.unproject.  With compensation
    (IsRollingShutterCompensated) it is the
    line of the points whose residual Camera.error, which applies the
    camera motion during the readout, is zero at the pixel position
    (SolveRollingShutterRay), solved at the distance where the previous
    ray met the surface (solveDistance, set by ResidualAtPoint).  When a
    ray cannot be found in this way, FallBack records why in failure and
    every ray of the image starts at the perspective centre from then on,
    and ReportFallBack writes that to the Console once, after
    *consolePrefix*.  A failure of Camera.unproject is reported under
    *contextName*.

    The point of Camera.unproject of an image
    without rolling-shutter compensation is checked by a round trip
    through Camera.project and refined where it misses
    (ExactUnprojectedPoint), and *rayReport* (NewRayReport) counts the
    refined rays and the pixel positions without a ray (ExactRayText).
    For an image with compensation, Camera.project applies the camera
    motion, so the point is taken as the start of the solved ray."""

    def __init__(self, camera, contextName="photogrammetryUtilities.ImageRays.StaticDirection",
                 consolePrefix="  [MetaFOCUS]"):
        self.camera = camera
        self.contextName = contextName
        self.consolePrefix = consolePrefix
        self.perspectiveCentre = VectorFromMetashape(getattr(camera, "center", None))
        sensor = getattr(camera, "sensor", None)
        self.hasRollingShutterCompensation = sensor is not None and IsRollingShutterCompensated(sensor)
        self.solveDistance = None
        self.solvedRayCount = 0
        self.failure = None
        self.isFallBackReported = False
        self.rayReport = NewRayReport()
        # The camera transform and its inverse, for the refinement of the
        # rays of Camera.unproject (ExactUnprojectedPoint).
        self.cameraToChunk = self.chunkToCamera = None
        if getattr(camera, "project", None) is not None and not self.hasRollingShutterCompensation:
            transform = getattr(camera, "transform", None)
            if transform is not None and hasattr(transform, "mulp") and hasattr(transform, "inv"):
                try:
                    self.cameraToChunk, self.chunkToCamera = transform, transform.inv()
                except Exception as suppressedError:
                    ReportSuppressedError("photogrammetryUtilities.ImageRays.transform", suppressedError)

    def UnprojectedPoint(self, pixelX, pixelY):
        """The point of Camera.unproject at (pixelX, pixelY) in chunk
        coordinates, refined where its projection misses the position
        (ExactUnprojectedPoint), or None.  A failure of Camera.unproject
        is reported under contextName."""
        checksRays = not self.hasRollingShutterCompensation and self.cameraToChunk is not None
        try:
            unprojected = self.camera.unproject(Metashape.Vector([pixelX, pixelY]))
        except Exception as suppressedError:
            ReportSuppressedError(self.contextName, suppressedError)
            # A failure of Camera.unproject is a position without a ray as
            # well, and it is counted.
            if checksRays:
                self.rayReport["checked"] += 1
                self.rayReport["withoutRay"] += 1
            return None
        pointTuple = VectorFromMetashape(unprojected)
        if not checksRays:
            return pointTuple
        if pointTuple is None:
            # Camera.unproject gives no point: the position has no ray.
            self.rayReport["checked"] += 1
            self.rayReport["withoutRay"] += 1
            return None
        return ExactUnprojectedPoint(self.camera, pointTuple, pixelX, pixelY, self.cameraToChunk,
                                     self.chunkToCamera, self.rayReport)

    def SolvesRays(self):
        """True while the rays of the image follow the camera motion."""
        return self.hasRollingShutterCompensation and self.failure is None

    def StaticDirection(self, pixelX, pixelY):
        """Direction from the perspective centre to the point of
        Camera.unproject,
        refined where its projection misses the
        position (UnprojectedPoint)."""
        if self.perspectiveCentre is None:
            return None
        pointTuple = self.UnprojectedPoint(pixelX, pixelY)
        if pointTuple is None:
            return None
        return VectorSubtract(pointTuple, self.perspectiveCentre)

    def Ray(self, pixelX, pixelY):
        """(origin, direction) of the ray through (pixelX, pixelY), or
        None.  Raises RollingShutterRayFailure (FallBack) when the ray of
        an image whose rays follow the camera motion cannot be found."""
        staticDirection = self.StaticDirection(pixelX, pixelY)
        if staticDirection is None:
            return None
        if not self.SolvesRays():
            return self.perspectiveCentre, staticDirection
        try:
            ray, reason = SolveRollingShutterRay(self.camera, pixelX, pixelY, self.perspectiveCentre, staticDirection,
                                                 self.solveDistance)
        except Exception as solveError:
            ray, reason = None, "because of " + DescribeException(solveError)
        if ray is None:
            self.FallBack(pixelX, pixelY, reason)
        self.solvedRayCount += 1
        return ray

    def ResidualAtPoint(self, pixelX, pixelY, chunkPoint):
        """Length of the residual (Camera.error) of the chunk point
        *chunkPoint* at (pixelX, pixelY), in pixels.  Its distance from the
        perspective centre becomes the solve distance of the next rays.  A
        residual that cannot be evaluated ends the rays of the image
        (FallBack)."""
        self.solveDistance = VectorLength(VectorSubtract(chunkPoint, self.perspectiveCentre)) or None
        try:
            return math.hypot(*_ResidualAtPixel(self.camera, chunkPoint, pixelX, pixelY))
        except Exception as residualError:
            self.FallBack(pixelX, pixelY, "because of " + DescribeException(residualError))

    def FallBack(self, pixelX, pixelY, reason):
        """From now on every ray of the image starts at its perspective
        centre: record why in failure, with *reason* a clause that starts
        with "because", and raise RollingShutterRayFailure, so that a
        computation casts the rays it has cast before again."""
        self.failure = ("the ray through the pixel position ({:.1f}, {:.1f}) could not be found as a line of zero "
                        "residual (Camera.error) {}".format(pixelX, pixelY, reason))
        raise RollingShutterRayFailure(self.failure)

    def ReportFallBack(self):
        """One Console line, after consolePrefix, for an image whose rays
        fell back to the rays from the perspective centre, the first time
        it is asked."""
        if self.failure is None or self.isFallBackReported:
            return
        self.isFallBackReported = True
        print("{} Rolling shutter: image {}: {}, so every ray of the image starts at its perspective centre "
              "(Camera.center) and leaves out the camera motion during the "
              "readout.".format(self.consolePrefix, getattr(self.camera, "label", "") or "unnamed", self.failure))


def CastCheckedRay(imageRays, pixelX, pixelY, castRay, findHit, chunkPointOfHit):
    """(ray, hit) of the pixel position (pixelX, pixelY), with ray =
    castRay(pixelX, pixelY), (None, None) when there is no ray, and hit =
    findHit(ray), None for a miss.  For an image whose rays follow the
    camera motion (ImageRays.SolvesRays) the residual (Camera.error) of
    the hit, at chunkPointOfHit(hit) in chunk coordinates, is checked,
    because the ray is the line through two points solved at other
    distances: above ROLLING_SHUTTER_HIT_RESIDUAL_PIXELS the ray is solved
    again at the distance of the hit (ImageRays.ResidualAtPoint), and
    when the residual there stays above it, every ray of the image falls
    back (ImageRays.FallBack)."""
    ray = castRay(pixelX, pixelY)
    if ray is None:
        return None, None
    hit = findHit(ray)
    if hit is None or not imageRays.SolvesRays():
        return ray, hit
    if imageRays.ResidualAtPoint(pixelX, pixelY, chunkPointOfHit(hit)) <= ROLLING_SHUTTER_HIT_RESIDUAL_PIXELS:
        return ray, hit
    ray = castRay(pixelX, pixelY)
    hit = findHit(ray) if ray is not None else None
    if hit is None:
        return ray, None
    residualLength = imageRays.ResidualAtPoint(pixelX, pixelY, chunkPointOfHit(hit))
    if not residualLength <= ROLLING_SHUTTER_HIT_RESIDUAL_PIXELS:
        imageRays.FallBack(pixelX, pixelY, "because its residual where it meets the surface stays at {:.3g} px".format(
            residualLength))
    return ray, hit


def ComputeWithOneKindOfRays(computation, beforeRepeat=None):
    """The result of computation(), which casts rays of one image
    (ImageRays).  When the rays of the image fall back to the rays from
    the perspective centre part of the way (RollingShutterRayFailure),
    beforeRepeat() clears what the computation has counted so far and
    computation() runs again, now with those rays only, so that no result
    mixes the two kinds of rays."""
    try:
        return computation()
    except RollingShutterRayFailure:
        if beforeRepeat is not None:
            beforeRepeat()
        return computation()


def RollingShutterConsoleLines(cameras, maximumListedImageCount=20):
    """Console lines of a GSD tool that name the images of *cameras* with
    rolling-shutter compensation (ImagesWithRollingShutterCompensation)
    and say how their rays are cast (ROLLING_SHUTTER_RAY_NOTE), with the
    first *maximumListedImageCount* labels.  An empty list when there is
    no such image."""
    images = ImagesWithRollingShutterCompensation(cameras)
    if not images:
        return []
    groupLabels = sorted({str(getattr(image.sensor, "label", "") or "unnamed") for image in images})
    listedLabels = ", ".join(str(image.label) for image in images[:maximumListedImageCount])
    if len(images) > maximumListedImageCount:
        listedLabels += ", and {} more".format(len(images) - maximumListedImageCount)
    return ["Rolling shutter: {} of interior orientation groups with rolling-shutter compensation ({}): "
            "{}.".format(FormatCountWithNoun(len(images), "image", "images"), ", ".join(groupLabels),
                         ROLLING_SHUTTER_RAY_NOTE),
            "  " + listedLabels]


def CountAdditionalModelUnknowns(chunk, observedImageKeys=None):
    """Additional unknowns of extended camera models: the rolling-shutter
    parameters (RollingShutterUnknownsPerImage, 2 for the Regularized
    model and 6 for the Full model) of every enabled, oriented image whose
    interior orientation group has rolling-shutter compensation
    enabled, with *observedImageKeys* of every
    such image among them (ObservedImageKeys)."""
    unknownsBySensorKey = {}
    for sensor in getattr(chunk, "sensors", []) or []:
        unknownsPerImage = RollingShutterUnknownsPerImage(sensor)
        if unknownsPerImage:
            unknownsBySensorKey[_ObjectKey(sensor)] = unknownsPerImage
    if not unknownsBySensorKey:
        return 0
    total = 0
    for camera in getattr(chunk, "cameras", []) or []:
        if not IsOrientedEnabledCamera(camera):
            continue
        if observedImageKeys is not None and _ObjectKey(camera) not in observedImageKeys:
            continue
        sensor = getattr(camera, "sensor", None)
        if sensor is not None:
            total += unknownsBySensorKey.get(_ObjectKey(sensor), 0)
    return total


def CountProjectionsPerTrack(chunk):
    """Number of projections on enabled, oriented images per track, as
    an array indexed by track identifier (a track beyond its end has
    none).  An image without projections is skipped silently, any
    other failure to read the projections of an image is reported."""
    projectionCounts = array.array("I")
    tiePoints = getattr(chunk, "tie_points", None)
    if tiePoints is None:
        return projectionCounts
    try:
        trackCount = len(getattr(tiePoints, "tracks", None) or [])
    except TypeError:
        trackCount = 0
    projectionCounts.frombytes(bytes(projectionCounts.itemsize * trackCount))
    for camera in getattr(chunk, "cameras", []) or []:
        if not IsOrientedEnabledCamera(camera):
            continue
        try:
            cameraProjections = tiePoints.projections[camera]
        except KeyError:
            continue
        except Exception as suppressedError:
            ReportSuppressedError("photogrammetryUtilities.CountProjectionsPerTrack", suppressedError)
            continue
        for projection in cameraProjections or []:
            trackId = projection.track_id
            if trackId >= len(projectionCounts):
                missingCount = max(trackId + 1 - len(projectionCounts), len(projectionCounts))
                projectionCounts.frombytes(bytes(projectionCounts.itemsize * missingCount))
            projectionCounts[trackId] += 1
    return projectionCounts


def TrackTakesPartInAdjustment(projectionCounts, trackId):
    """True when the track has projections on at least two enabled,
    oriented images (see CountProjectionsPerTrack), so that its tie
    point, if valid, is determined by the adjustment."""
    return (0 <= trackId < len(projectionCounts)
            and projectionCounts[trackId] >= MINIMUM_RAYS_PER_OBJECT_POINT)


def CountTiePointNetwork(chunk, projectionCounts=None):
    """(scalar observations, unknowns) of the tie points that take part
    in the bundle adjustment.  A valid tie point whose track has
    projections on at least two enabled, oriented images adds two
    observations per such projection and three unknowns.  Projections
    of tracks without a valid tie point (never triangulated, or
    removed by gradual selection) add nothing, and neither do tie
    points seen on fewer than two such images (for example after
    disabling images).  Slave images of a multi-camera rig observe
    too, so they are included."""
    tiePoints = getattr(chunk, "tie_points", None)
    if tiePoints is None:
        return 0, 0
    if projectionCounts is None:
        projectionCounts = CountProjectionsPerTrack(chunk)
    observations = unknowns = 0
    for point in getattr(tiePoints, "points", None) or []:
        if not point.valid:
            continue
        trackId = point.track_id
        if TrackTakesPartInAdjustment(projectionCounts, trackId):
            observations += 2 * projectionCounts[trackId]
            unknowns += 3
    return observations, unknowns


def CountTiePointObservations(chunk):
    """Scalar tie-point image observations of the adjustment (see
    CountTiePointNetwork)."""
    return CountTiePointNetwork(chunk)[0]


def CountExteriorOrientationUnknowns(chunk, observedImageKeys=None):
    """Exterior orientation unknowns: six per camera station with at
    least one enabled, oriented image, with *observedImageKeys* with at
    least one such image among them
    (ObservedImageKeys).  The slave images of a multi-camera rig follow
    their master, so a rig station counts once, also when only its slave
    images are enabled or observed."""
    stationKeys = set()
    for camera in getattr(chunk, "cameras", []) or []:
        if IsOrientedEnabledCamera(camera) and (
                observedImageKeys is None or _ObjectKey(camera) in observedImageKeys):
            stationKeys.add(CameraStationKey(camera))
    return 6 * len(stationKeys)


def ImageKeys(cameras):
    """The keys (_ObjectKey) of *cameras*, as ImagesWithTiePointsKeys gives
    them."""
    return {_ObjectKey(camera) for camera in cameras}


def ImagesWithTiePointsKeys(chunk, projectionCounts=None):
    """Keys (_ObjectKey) of the enabled, oriented images with a 2D tie
    point of a tie point in the adjustment: a projection of a valid tie
    point whose track has projections on at least two enabled, oriented
    images (TrackTakesPartInAdjustment), the observations of
    CountTiePointNetwork (for
    ComputeRedundancyBreakdown).  *projectionCounts* (from
    CountProjectionsPerTrack) saves the pass over all projections when the
    caller already has it.  A failure to read the projections of an image
    is reported in the Console, and the image counts as one without."""
    tiePoints = getattr(chunk, "tie_points", None)
    if tiePoints is None:
        return set()
    if projectionCounts is None:
        projectionCounts = CountProjectionsPerTrack(chunk)
    tracksInAdjustment = bytearray(len(projectionCounts))
    for point in getattr(tiePoints, "points", None) or []:
        if point.valid and TrackTakesPartInAdjustment(projectionCounts, point.track_id):
            tracksInAdjustment[point.track_id] = 1
    imageKeys = set()
    for camera in getattr(chunk, "cameras", []) or []:
        if not IsOrientedEnabledCamera(camera):
            continue
        try:
            cameraProjections = tiePoints.projections[camera]
        except KeyError:
            continue
        except Exception as suppressedError:
            ReportSuppressedError("photogrammetryUtilities.ImagesWithTiePointsKeys", suppressedError)
            continue
        for projection in cameraProjections or []:
            trackId = projection.track_id
            if 0 <= trackId < len(tracksInAdjustment) and tracksInAdjustment[trackId]:
                imageKeys.add(_ObjectKey(camera))
                break
    return imageKeys


def ImagesWithMarkerProjectionsKeys(chunk):
    """Keys (_ObjectKey) of the enabled, oriented images with a valid
    projection of a marker in the adjustment (IsMarkerInAdjustment and
    IsActiveMarkerProjection), the observations of
    CountMarkerImageObservations."""
    imageKeys = set()
    for marker in getattr(chunk, "markers", []) or []:
        try:
            if not IsMarkerInAdjustment(marker):
                continue
            for camera, projection in _MarkerProjectionItems(marker):
                if IsActiveMarkerProjection(camera, projection):
                    imageKeys.add(_ObjectKey(camera))
        except Exception as suppressedError:
            ReportSuppressedError("photogrammetryUtilities.ImagesWithMarkerProjectionsKeys", suppressedError)
    return imageKeys


def CountTiePointUnknowns(chunk):
    """Object-space unknowns of the tie points in the adjustment (see
    CountTiePointNetwork)."""
    return CountTiePointNetwork(chunk)[1]


def CountMarkerUnknowns(chunk):
    """Object-space unknowns of the markers: three per marker in the
    adjustment (see IsMarkerInAdjustment)."""
    return 3 * sum(1 for marker in getattr(chunk, "markers", []) or []
                   if IsMarkerInAdjustment(marker))


def DetermineDatumDefect(controlPointCount, hasScaleConstraint,
                         hasRotationPrior):
    """Datum defect of a 3D similarity datum: translations, rotations,
    and scale left free by the control.

    Three or more control points (assumed not collinear) fix all seven
    parameters, two fix all but the rotation about the line joining
    them, one fixes the translation only.  Reference distances
    (Metashape scale bars) fix the scale, and camera rotation priors fix
    the rotations."""
    if controlPointCount >= 3:
        return 0
    freeTranslations = 3 if controlPointCount == 0 else 0
    freeRotations = (3, 3, 1)[controlPointCount]
    freeScale = 1 if controlPointCount < 2 else 0
    if hasScaleConstraint:
        freeScale = 0
    if hasRotationPrior:
        freeRotations = 0
    return freeTranslations + freeRotations + freeScale


def DetectDatumDefect(chunk, observedStationKeys=None):
    """Datum defect of the chunk from the reference data that take part
    in the adjustment: markers in the adjustment with an enabled
    reference location and enabled, oriented images with an enabled
    location prior act as control points, Metashape scale bars between
    ends in the adjustment fix the scale, and enabled camera rotation
    priors fix the rotations.  With *observedStationKeys*
    only the priors of the images of the camera stations among
    them count (ObservedStationKeys): the prior of an image of a station
    that no image observation involves places that station alone, not
    the network."""
    controlPointCount = 0
    hasRotationPrior = False
    for marker in getattr(chunk, "markers", []) or []:
        if HasEnabledReferenceLocation(marker) and IsMarkerInAdjustment(marker):
            controlPointCount += 1
    for camera in getattr(chunk, "cameras", []) or []:
        if not IsOrientedEnabledCamera(camera):
            continue
        if observedStationKeys is not None and CameraStationKey(camera) not in observedStationKeys:
            continue
        reference = getattr(camera, "reference", None)
        locationEnabled, rotationEnabled = CameraReferenceFlags(reference)
        if locationEnabled and getattr(reference, "location", None) is not None:
            controlPointCount += 1
        if rotationEnabled and getattr(reference, "rotation", None) is not None:
            hasRotationPrior = True
    hasScaleConstraint = CountScaleBarObservations(chunk, observedStationKeys) > 0
    return DetermineDatumDefect(controlPointCount, hasScaleConstraint, hasRotationPrior)


def ObservedImageKeys(chunk, includeControl, projectionCounts=None, tiePointImageKeys=None):
    """Keys (_ObjectKey) of the enabled, oriented images whose unknowns the
    observations of the redundancy involve: those
    with a 2D tie point in the adjustment (ImagesWithTiePointsKeys, or
    *tiePointImageKeys* when the caller has them), and with
    *includeControl* also those with a valid projection of a marker in the
    adjustment (ImagesWithMarkerProjectionsKeys)."""
    imageKeys = set(tiePointImageKeys) if tiePointImageKeys is not None else ImagesWithTiePointsKeys(
        chunk, projectionCounts)
    if includeControl:
        imageKeys |= ImagesWithMarkerProjectionsKeys(chunk)
    return imageKeys


def CameraStationKey(camera):
    """The key (_ObjectKey) of the camera station of an image: that of its
    master, which a slave image of a multi-camera rig follows, or its
    own."""
    return _ObjectKey(getattr(camera, "master", None) or camera)


def ObservedStationKeys(chunk, observedImageKeys):
    """Keys (CameraStationKey) of the camera stations with an enabled,
    oriented image among *observedImageKeys* (ObservedImageKeys), whose six
    exterior orientation unknowns the count holds (the priors, the control
    points, and the Metashape scale bars of the images of such a station
    involve these unknowns, also those of a rig master without image
    observations of its own)."""
    return {CameraStationKey(camera) for camera in getattr(chunk, "cameras", []) or []
            if IsOrientedEnabledCamera(camera) and _ObjectKey(camera) in observedImageKeys}


def ComputeRedundancyBreakdown(chunk, includeControl=True, projectionCounts=None, tiePointImageKeys=None):
    """Observations, unknowns, datum defect, and redundancy
    r = n - u + d of the bundle adjustment, together with the
    contributions they are made of (tiePointObservations,
    markerImageObservations, markerReferenceObservations,
    scaleBarObservations, cameraPriorObservations, tiePointUnknowns,
    exteriorOrientationUnknowns, interiorOrientationUnknowns,
    additionalModelUnknowns, markerUnknowns), so that a caller needs
    no second pass over the chunk.

    With *includeControl* false, only the tie-point network is
    counted (the marker, reference-distance, and prior contributions are zero
    and the datum defect is always seven), which is the consistent
    redundancy for a sigma nought built from tie-point residuals
    alone.  *projectionCounts* (from CountProjectionsPerTrack) saves
    the pass over all projections when the caller already has it, and
    *tiePointImageKeys* (ImagesWithTiePointsKeys) the pass that finds the
    images with 2D tie points in the adjustment.

    By Geert's decision of 15 October 2026, the
    unknowns of a camera station, an interior orientation group, and an
    image are counted only when the observations of the estimate involve
    them (ObservedImageKeys): the 2D tie points in the adjustment, and with
    *includeControl* also the marker projections in the adjustment.  An
    unknown that no observation involves adds one to the unknowns and one
    to the rank defect, so it leaves the redundancy unchanged and is not
    counted.  The priors of the images of a camera station that no such
    observation involves fix only the unknowns of that station, so they
    are left out with them: their location priors do not count as control
    (DetectDatumDefect), nor does a Metashape scale bar to such an image
    count (ObservedStationKeys).  The priors of a rig master without such
    observations of its own still count when a slave of its station has
    some.

    The result is an estimate from counts, not from the rank of the
    adjustment, which Metashape does not expose.  It assumes that the
    tie points connect all images into one block (each further block
    adds a datum defect of seven), that three or more control points
    are not collinear, that every counted camera station has six
    exterior orientation unknowns and every counted interior orientation
    group the unknowns in its calibration covariance, that the
    observations determine every counted unknown, and that no further
    constraints or unknowns apply (a
    common perspective centre of several images, estimated relative
    orientations within a multi-camera rig and per-image calibration
    parameters are not detected)."""
    if projectionCounts is None:
        projectionCounts = CountProjectionsPerTrack(chunk)
    tiePointObservations, tiePointUnknowns = CountTiePointNetwork(chunk, projectionCounts)
    observedImageKeys = ObservedImageKeys(chunk, includeControl, projectionCounts, tiePointImageKeys)
    # The priors, the control points, and the Metashape scale bars act on
    # the camera station of their image.
    observedStationKeys = ObservedStationKeys(chunk, observedImageKeys)
    breakdown = dict(
        tiePointObservations=tiePointObservations,
        markerImageObservations=0,
        markerReferenceObservations=0,
        scaleBarObservations=0,
        cameraPriorObservations=0,
        tiePointUnknowns=tiePointUnknowns,
        exteriorOrientationUnknowns=CountExteriorOrientationUnknowns(chunk, observedImageKeys),
        interiorOrientationUnknowns=CountInteriorOrientationUnknowns(chunk, observedImageKeys),
        additionalModelUnknowns=CountAdditionalModelUnknowns(chunk, observedImageKeys),
        markerUnknowns=0)
    if includeControl:
        breakdown.update(
            markerImageObservations=CountMarkerImageObservations(chunk),
            markerReferenceObservations=CountMarkerReferenceObservations(chunk),
            scaleBarObservations=CountScaleBarObservations(chunk, observedStationKeys),
            cameraPriorObservations=CountCameraPriorObservations(chunk, observedStationKeys),
            markerUnknowns=CountMarkerUnknowns(chunk))
        datumDefect = DetectDatumDefect(chunk, observedStationKeys)
    else:
        datumDefect = TIE_POINT_NETWORK_DATUM_DEFECT
    observations = (breakdown["tiePointObservations"] + breakdown["markerImageObservations"]
                    + breakdown["markerReferenceObservations"] + breakdown["scaleBarObservations"]
                    + breakdown["cameraPriorObservations"])
    unknowns = (breakdown["tiePointUnknowns"] + breakdown["exteriorOrientationUnknowns"]
                + breakdown["interiorOrientationUnknowns"] + breakdown["additionalModelUnknowns"]
                + breakdown["markerUnknowns"])
    breakdown.update(observations=observations, unknowns=unknowns, datumDefect=datumDefect,
                     redundancy=observations - unknowns + datumDefect,
                     observedImageCount=len(observedImageKeys))
    return breakdown


# ===================================================================
# Homogeneous points and covariance propagation
# ===================================================================

def TransformHomogeneousPoint(transformMatrix, homogeneousCoordinate):
    """Cartesian (x, y, z) of a homogeneous chunk point after a 4x4
    transform: the point is first divided by its w component, then
    transformed.  Returns None for a point at infinity."""
    cartesian = HomogeneousToCartesian3D(homogeneousCoordinate)
    if cartesian is None:
        return None
    if transformMatrix is None:
        return cartesian
    return _VectorToTuple(transformMatrix.mulp(Metashape.Vector(list(cartesian))))


def MarkerReferenceCoordinateSystem(chunk):
    """CRS of the reference coordinates of the markers: the separate
    marker CRS of the Reference Settings (chunk.marker_crs) when one
    is set, otherwise the chunk CRS (chunk.crs)."""
    markerCoordinateSystem = getattr(chunk, "marker_crs", None)
    if markerCoordinateSystem is not None:
        return markerCoordinateSystem
    return getattr(chunk, "crs", None)


def MarkerReferenceUsesChunkCoordinateSystem(chunk):
    """True when the reference coordinates of the markers are in the
    chunk CRS: no separate marker CRS is set, or it has the same
    well-known text (WKT) as the chunk CRS."""
    markerCoordinateSystem = getattr(chunk, "marker_crs", None)
    chunkCoordinateSystem = getattr(chunk, "crs", None)
    if markerCoordinateSystem is None or markerCoordinateSystem is chunkCoordinateSystem:
        return True
    markerWellKnownText = str(getattr(markerCoordinateSystem, "wkt", "") or "")
    chunkWellKnownText = str(getattr(chunkCoordinateSystem, "wkt", "") or "")
    return bool(markerWellKnownText) and markerWellKnownText == chunkWellKnownText


def MarkerReferenceWorldPoint(chunk, referenceLocation):
    """World coordinates (those of the chunk transform) of the
    reference location of a marker, which is given in
    MarkerReferenceCoordinateSystem(chunk).

    The location is unprojected with that CRS, which gives geocentric
    coordinates on its geodetic datum.  When it is not the chunk CRS,
    the coordinate transformation into the geocentric coordinates of
    the chunk CRS follows (Metashape.CoordinateSystem.datumTransform,
    as Region Tools uses it between chunks), because a georeferenced
    chunk has geocentric world coordinates on the geodetic datum of
    its own CRS.  The vertical datum of each CRS is applied by its
    own unproject.  Raises ValueError when the chunk has no CRS, and
    when the two CRSs differ and one of them is local or missing, so
    that its coordinates are not geocentric.
    Exceptions of the host are passed on."""
    chunkCoordinateSystem = getattr(chunk, "crs", None)
    markerCoordinateSystem = MarkerReferenceCoordinateSystem(chunk)
    if markerCoordinateSystem is None:
        raise ValueError("the chunk has no CRS")
    if MarkerReferenceUsesChunkCoordinateSystem(chunk):
        return markerCoordinateSystem.unproject(referenceLocation)
    if (CrsKind(markerCoordinateSystem) in ("none", "local")
            or CrsKind(chunkCoordinateSystem) in ("none", "local")):
        raise ValueError(
            "the marker CRS ({}) and the chunk CRS ({}) cannot be related, because one of them "
            "is local or missing".format(
                getattr(markerCoordinateSystem, "name", "") or "unnamed",
                getattr(chunkCoordinateSystem, "name", "") or "none"))
    coordinateTransformation = Metashape.CoordinateSystem.datumTransform(
        markerCoordinateSystem, chunkCoordinateSystem)
    return coordinateTransformation.mulp(markerCoordinateSystem.unproject(referenceLocation))


def MarkerReferenceLocationFromChunkCoordinates(chunk, chunkCoordinates):
    """Reference location, in MarkerReferenceCoordinateSystem(chunk), of
    a point given in the chunk CRS: the direction opposite to
    MarkerReferenceWorldPoint, for the markers that a tool creates
    (the MaxGSD and MinGSD markers of the GSD Value Exporter).  The point
    is returned unchanged when the markers use the chunk CRS, and
    otherwise transformed with Metashape.CoordinateSystem.transform,
    which applies the map projections and the coordinate
    transformation between the two CRSs.  Raises ValueError when the
    two CRSs differ and one of them is local or missing."""
    if MarkerReferenceUsesChunkCoordinateSystem(chunk):
        return chunkCoordinates
    chunkCoordinateSystem = getattr(chunk, "crs", None)
    markerCoordinateSystem = MarkerReferenceCoordinateSystem(chunk)
    if (CrsKind(markerCoordinateSystem) in ("none", "local")
            or CrsKind(chunkCoordinateSystem) in ("none", "local")):
        raise ValueError(
            "the marker CRS ({}) and the chunk CRS ({}) cannot be related, because one of them "
            "is local or missing".format(
                getattr(markerCoordinateSystem, "name", "") or "unnamed",
                getattr(chunkCoordinateSystem, "name", "") or "none"))
    return Metashape.CoordinateSystem.transform(chunkCoordinates, chunkCoordinateSystem, markerCoordinateSystem)


# Inspection markers: markers that a MetaFOCUS tool places at model-derived
# positions for visual inspection, the MaxGSD and MinGSD markers of the GSD
# Value Exporter.  Their reference coordinates come from the model, so they
# are neither control points nor check points.  They carry this metadata
# key, and markers without it are also recognised by their label in the
# marker group of the GSD Value Exporter.
INSPECTION_MARKER_META_KEY = "MetaFOCUS/InspectionMarker"
INSPECTION_MARKER_GROUP_LABEL = "GSD extremes"
INSPECTION_MARKER_LABEL_PATTERN = re.compile(r"^(?:MaxGSD|MinGSD)_")


def MetadataValue(metadata, key):
    """The value of *key* in a Metashape.MetaData (or a dictionary), or None
    when it has none or cannot be read."""
    if metadata is None:
        return None
    try:
        keys = metadata.keys()
    except Exception:
        keys = None
    try:
        if keys is not None and key not in list(keys):
            return None
        return metadata[key]
    except Exception:
        return None


def IsInspectionMarker(marker):
    """True for an inspection marker of MetaFOCUS: one with the metadata key
    INSPECTION_MARKER_META_KEY, or a MaxGSD_ or MinGSD_ marker in the marker
    group "GSD extremes".  The check-point RMSE of the Gradual Selection Filter and
    the Marker Residual Inspector leave such markers out, also when someone
    measured them in images."""
    if MetadataValue(getattr(marker, "meta", None), INSPECTION_MARKER_META_KEY):
        return True
    label = getattr(marker, "label", "") or ""
    groupLabel = getattr(getattr(marker, "group", None), "label", None)
    return bool(INSPECTION_MARKER_LABEL_PATTERN.match(str(label))) and groupLabel == INSPECTION_MARKER_GROUP_LABEL


def MarkerReferenceDiscrepancy(chunk, referenceLocation, estimatedWorld):
    """Estimated minus reference position of a marker, as a vector in
    the local east-north-up coordinates of the chunk CRS at the
    reference point (metres in a georeferenced chunk).  The reference
    location is read in the marker CRS and brought into the world
    coordinates of the chunk by MarkerReferenceWorldPoint, and
    *estimatedWorld* is the estimated position in world coordinates.
    Raises as MarkerReferenceWorldPoint does, and passes on the
    exceptions of the host when the local east-north-up coordinates
    cannot be built (CoordinateSystem.localframe)."""
    referenceWorld = MarkerReferenceWorldPoint(chunk, referenceLocation)
    localFrame = chunk.crs.localframe(referenceWorld)
    return localFrame.mulp(estimatedWorld) - localFrame.mulp(referenceWorld)


# Asymmetry and negative eigenvalues of a covariance matrix up to this
# fraction of its largest element in magnitude are attributed to
# rounding: the host may hold covariances with about seven significant
# digits (single precision), so that a variance that should be zero can
# come out slightly negative.  Larger departures make the matrix invalid.
COVARIANCE_RELATIVE_TOLERANCE = 1.0e-6


def ValidatedCovarianceElements(covarianceMatrix):
    """The nine elements of a 3 x 3 covariance matrix (indexable as
    [row, column]) as floats, row by row, after checking that it is a
    covariance matrix: finite, symmetric, and positive semi-definite.

    Raises ValueError, naming the problem, otherwise.  Absolute values
    or singular values would hide a negative variance: diag(-1, 4, 9)
    has the singular values 9, 4, and 1.  The signed eigenvalues come
    from the symmetric eigenvalue solver of the plane fit, applied to
    the symmetrised matrix divided by its largest element in magnitude,
    so that the check does not depend on the unit of the matrix.
    Asymmetry and negative eigenvalues within
    COVARIANCE_RELATIVE_TOLERANCE are rounding and are accepted: the
    callers set such a slightly negative variance to zero."""
    elements = [[float(covarianceMatrix[rowIndex, columnIndex]) for columnIndex in range(3)]
                for rowIndex in range(3)]
    if not all(math.isfinite(element) for rowElements in elements for element in rowElements):
        raise ValueError("an element of the covariance matrix is not a finite number")
    largestMagnitude = max(abs(element) for rowElements in elements for element in rowElements)
    if largestMagnitude == 0.0:
        return elements
    for rowIndex, columnIndex in ((0, 1), (0, 2), (1, 2)):
        if (abs(elements[rowIndex][columnIndex] - elements[columnIndex][rowIndex])
                > COVARIANCE_RELATIVE_TOLERANCE * largestMagnitude):
            raise ValueError("the covariance matrix is not symmetric")
    normalisedElements = [[element / largestMagnitude for element in rowElements] for rowElements in elements]
    smallestEigenvalue = _ComputeEigenvaluesSymmetric3x3(
        normalisedElements[0][0], 0.5 * (normalisedElements[0][1] + normalisedElements[1][0]),
        0.5 * (normalisedElements[0][2] + normalisedElements[2][0]), normalisedElements[1][1],
        0.5 * (normalisedElements[1][2] + normalisedElements[2][1]), normalisedElements[2][2])[0]
    if smallestEigenvalue < -COVARIANCE_RELATIVE_TOLERANCE:
        raise ValueError(
            "the covariance matrix is not positive semi-definite (its smallest eigenvalue is "
            "{:.3g} times its largest element)".format(smallestEigenvalue))
    return elements


def PropagateCovarianceDiagonal(jacobianRows, covarianceMatrix):
    """Standard deviations sqrt(diag(J C J^T)) of a linear(ised)
    mapping with Jacobian rows *jacobianRows* (row k holds the partial
    derivatives of output k) and the 3 x 3 input covariance
    *covarianceMatrix* (indexable as [row, column]).  Exactly three
    inputs are used: a longer Jacobian row is truncated.

    Raises ValueError when *covarianceMatrix* is not a covariance
    matrix (see ValidatedCovarianceElements): the square root of the
    absolute value of a negative variance is not a standard deviation.
    A variance that rounding makes slightly negative gives zero."""
    covarianceElements = ValidatedCovarianceElements(covarianceMatrix)
    standardDeviations = []
    for jacobianRow in jacobianRows:
        variance = sum(jacobianRow[rowIndex] * covarianceElements[rowIndex][columnIndex]
                       * jacobianRow[columnIndex]
                       for rowIndex in range(3) for columnIndex in range(3))
        standardDeviations.append(math.sqrt(max(variance, 0.0)))
    return tuple(standardDeviations)


def ConvertToCamelCase(name):
    """Join the words of a label into camelCase for use in file names
    (first word unchanged).  Letters with diacritics and other
    Unicode letters and digits are kept, every other character is
    dropped."""
    if not name:
        return "unnamed"
    parts = name.split()
    if not parts:
        return "unnamed"
    result = parts[0]
    for part in parts[1:]:
        if part:
            result += part[0].upper() + part[1:]
    result = re.sub(r'[^\w]', '', result)
    return result if result else "unnamed"


def NormalisedProjectPath(projectPath):
    """*projectPath* with forward slashes for backslashes, without . and
    .. parts, and, on Windows, in lower case, so that two spellings of
    one project file compare equal."""
    return os.path.normcase(os.path.normpath(projectPath.replace("\\", "/")))


def NonFiniteResidualsText(count):
    """"1 residual of a 2D tie point that is not a finite number" or
    "2 residuals of 2D tie points that are not finite numbers", for the
    Console lines of the tools that leave such residuals out."""
    if count == 1:
        return "1 residual of a 2D tie point that is not a finite number"
    return "{:,} residuals of 2D tie points that are not finite numbers".format(count)


def SharedGroupLabels(sensors):
    """The labels that two or more interior orientation groups of
    *sensors* share, compared by their keys, because Metashape gives a new
    Python object at every access of chunk.sensors."""
    keysByLabel = {}
    for sensor in sensors or ():
        label = getattr(sensor, "label", "") or ""
        if label:
            keysByLabel.setdefault(label, set()).add(getattr(sensor, "key", id(sensor)))
    return {label for label, keys in keysByLabel.items() if len(keys) > 1}


def GroupLabelWithKey(sensor, sharedLabels):
    """The label of an interior orientation group, with " (key N)" when
    another group has the same label (*sharedLabels*, SharedGroupLabels):
    two of Geert's projects have two groups of the same label after he
    refocused the lens, so that
    their tabs, Console lines, and CSV rows could not otherwise be told
    apart."""
    label = getattr(sensor, "label", "") or ""
    if label and label in sharedLabels:
        return "{} (key {})".format(label, getattr(sensor, "key", ""))
    return label


def FormatCountWithNoun(count, singularNoun, pluralNoun):
    """*count* with thousands separators, followed by the singular or
    the plural noun, for example "1 image" or "1,234 images"."""
    return "{:,} {}".format(count, singularNoun if count == 1 else pluralNoun)


# The significant digits of a length that MetaFOCUS writes without a fixed
# number of decimals: a fixed number of decimals
# writes an RMSE of 2 um as 0.0000 m, and Geert photographs objects of about
# 50 um with RMSE values in micrometres or below.
LENGTH_SIGNIFICANT_DIGITS = 4


def FormatSignificantDigits(value, digits=LENGTH_SIGNIFICANT_DIGITS):
    """*value* with *digits* significant digits and its trailing zeros
    ("0.009109", "1.500", "2.000e-06" for four), in exponent notation
    below 1e-4 and from 10 to the power of *digits*, without a trailing
    decimal point ("1000"), and "0" for zero."""
    if value == 0:
        return "0"
    text = "{:#.{}g}".format(value, digits)
    if text.endswith("."):
        text = text[:-1]
    return text


def FormatLengthWithDecimals(value, decimals):
    """A length in a text (a dialog, a table, the Console) with *decimals*
    decimals where these give at least LENGTH_SIGNIFICANT_DIGITS
    significant digits, and also for zero and a value that is not finite,
    otherwise with LENGTH_SIGNIFICANT_DIGITS significant digits
    (FormatSignificantDigits, in exponent notation below 1e-4), so that a
    GSD of 0.7512 um reads 7.512e-07 m and not 0.00000075 m with eight
    decimals."""
    if value == 0 or not math.isfinite(value) \
            or abs(value) >= 10.0 ** (LENGTH_SIGNIFICANT_DIGITS - 1 - decimals):
        return "{:.{}f}".format(value, decimals)
    return FormatSignificantDigits(value)


def LengthCsvDecimals(value, decimals):
    """The decimals of FormatLengthForCsv: *decimals*, or as many more as
    LENGTH_SIGNIFICANT_DIGITS significant digits of *value* need."""
    if value != 0 and math.isfinite(value):
        return max(decimals, LENGTH_SIGNIFICANT_DIGITS - 1 - int(math.floor(math.log10(abs(value)))))
    return decimals


def FormatLengthForCsv(value, decimals):
    """A length in a CSV file or a shape attribute in positional notation,
    with *decimals* decimals or as many more as LENGTH_SIGNIFICANT_DIGITS
    significant digits need (LengthCsvDecimals), so that a GSD of
    0.7512 um reads 0.0000007512 m and not 0.00000075 m with eight
    decimals.  Positional notation, because every
    program reads it as a number, also with the decimal comma."""
    return "{:.{}f}".format(value, LengthCsvDecimals(value, decimals))


def DecimalsToResolve(length):
    """The decimals whose step is no larger than *length*,
    ceil(-log10(length)) and at least zero, and zero for a length that is
    not a positive finite number."""
    if not (isinstance(length, (int, float)) and math.isfinite(length) and length > 0.0):
        return 0
    return max(0, int(math.ceil(-math.log10(length) - 1e-9)))


def FormatTransformMatrixForConsole(transformMatrix):
    """Python expression that recreates *transformMatrix* exactly
    (every element written with repr), so that a replaced chunk
    transform can be restored from the Console.  Returns "None" when
    the chunk had no transform."""
    if transformMatrix is None:
        return "None"
    rowTexts = []
    for rowIndex in range(4):
        rowTexts.append("[" + ", ".join(
            repr(float(transformMatrix[rowIndex, columnIndex]))
            for columnIndex in range(4)) + "]")
    return "Metashape.Matrix([" + ", ".join(rowTexts) + "])"


def DescribeException(exception):
    """"Type: message" of an exception, or the bare type name when the
    exception carries no message (Python's own MemoryError does not),
    so that a report never ends in an empty colon."""
    message = str(exception)
    if not message:
        return type(exception).__name__
    return "{}: {}".format(type(exception).__name__, message)


# ===================================================================
# Delimiter and decimal separator of the CSV exports
# ===================================================================

# The one choice of the four CSV exporters (GSD Value Exporter, Image
# Parameter Exporter, Marker Residual Inspector, and Tie Point Covariance
# Exporter): (label, delimiter, decimal separator), the first the
# default.  Excel takes the list separator of the Windows regional
# settings when it opens a CSV file by double-click: a comma with English
# settings, which use the decimal point, and a semicolon with the settings
# that use the decimal comma (German, Austrian, French, and most other
# continental European settings).  With the latter, Excel splits the
# columns of a semicolon-delimited file correctly but reads 1.5 as a date
# (the first of May) and other numbers with a decimal point as text.  Each
# delimiter occurs once, so that the delimiter, the item data of the combo
# box, identifies the choice (ChosenCsvFormat).
CSV_FORMAT_CHOICES = (
    ("Comma, decimal point (1.25)", ",", "."),
    ("Semicolon, decimal comma (1,25)", ";", ","),
    ("Tab, decimal point (1.25)", "\t", "."),
)

# Label of the dialog row of the combo box.
CSV_FORMAT_ROW_LABEL_TEXT = "Delimiter and decimal separator:"

# Tooltip of the combo box: the software that each choice suits and, for
# an exporter that writes comment lines, that these keep their text.
CSV_FORMAT_SUITABILITY_TEXT = (
    "Comma with a decimal point suits most software and Excel with English\n"
    "regional settings. Semicolon with a decimal comma suits Excel with\n"
    "regional settings that use the decimal comma, for example German or French.")
CSV_FORMAT_TOOLTIP_TEXT = (CSV_FORMAT_SUITABILITY_TEXT
                           + "\nComment lines that start with # keep their text unchanged.")


def FillCsvFormatComboBox(comboBox, toolTipText=None):
    """Fill *comboBox* with the labels of CSV_FORMAT_CHOICES, in their
    order and each with its delimiter as item data, select the first
    (the default), and give the combo box *toolTipText* as its tooltip
    (CSV_FORMAT_TOOLTIP_TEXT when None)."""
    for choiceLabel, delimiter, decimalSeparatorUnused in CSV_FORMAT_CHOICES:
        comboBox.addItem(choiceLabel, delimiter)
    comboBox.setCurrentIndex(0)
    comboBox.setToolTip(CSV_FORMAT_TOOLTIP_TEXT if toolTipText is None else toolTipText)


def CsvFormatName(csvDelimiter, decimalSeparator):
    """Label of the choice of CSV_FORMAT_CHOICES with *csvDelimiter* and
    *decimalSeparator*, for the Console, or the two characters when no
    choice has them."""
    for choiceLabel, delimiter, separator in CSV_FORMAT_CHOICES:
        if (delimiter, separator) == (csvDelimiter, decimalSeparator):
            return choiceLabel
    return "delimiter {!r}, decimal separator {!r}".format(csvDelimiter, decimalSeparator)


def ChosenCsvFormat(comboBox):
    """(delimiter, decimal separator) of the choice selected in
    *comboBox*, filled by FillCsvFormatComboBox: the choice of
    CSV_FORMAT_CHOICES whose delimiter is the item data of the selected
    item, or the default, the first choice, when no item of such a
    choice is selected."""
    chosenDelimiter = comboBox.currentData()
    for choiceLabelUnused, delimiter, decimalSeparator in CSV_FORMAT_CHOICES:
        if delimiter == chosenDelimiter:
            return delimiter, decimalSeparator
    choiceLabelUnused, delimiter, decimalSeparator = CSV_FORMAT_CHOICES[0]
    return delimiter, decimalSeparator


# A number as Python formats it and as Metashape writes it in its CSV
# export: an optional sign, digits with or without a decimal point (or a
# decimal point and digits), and an optional exponent, for example 1.25,
# -0.5, 42, 2.5e-09, and 2.500000e-09.
_DECIMAL_POINT_NUMBER_PATTERN = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?")


def WithDecimalSeparator(cellText, decimalSeparator):
    """*cellText*, a cell of a numeric column of a CSV export, with
    *decimalSeparator* in place of the decimal point of its numbers:
    a number (2,5e-09 for 2.5e-09 with a decimal comma), or numbers
    separated by slashes, some of them possibly empty (the combined
    Accuracy of the Image Parameter Exporter, 0.005/0.005/0.01).  Any
    other text keeps its text, a value that Metashape does not give as
    a number (24.0 mm) for example, and with a decimal point every cell
    does."""
    if decimalSeparator == "." or not all(
            part == "" or _DECIMAL_POINT_NUMBER_PATTERN.fullmatch(part) for part in cellText.split("/")):
        return cellText
    return cellText.replace(".", decimalSeparator)


def DataRowWithDecimalSeparator(cells, decimalSeparator, textColumnIndices=()):
    """The cells of one data row of a CSV export for *decimalSeparator*.
    The exporter decides by its columns which cells hold text, never by
    the text of a cell: the cells of *textColumnIndices* keep their text
    whatever it looks like (an image labelled 1.25), and every cell of
    the other, numeric columns goes through WithDecimalSeparator (a
    number that is not text, such as an integer, as str() writes it).
    With a decimal point the cells are returned as they are."""
    if decimalSeparator == ".":
        return list(cells)
    return [cell if columnIndex in textColumnIndices or cell is None
            else WithDecimalSeparator(cell if isinstance(cell, str) else str(cell), decimalSeparator)
            for columnIndex, cell in enumerate(cells)]


# ===================================================================
# Console blocks
# ===================================================================

# The rule and the label width of the blocks that the tools print to
# the Console pane (as the Tie Point Covariance Exporter prints them).
CONSOLE_RULE = "=" * 60
CONSOLE_LABEL_WIDTH = 27


def ConsoleBlockStart(toolName):
    """The lines that open a block of a tool in the Console pane: an
    empty line, a rule, the name of the tool, and a rule."""
    return ["", CONSOLE_RULE, toolName, CONSOLE_RULE]


def ConsoleBlockEnd():
    """The lines that close a block in the Console pane: a rule and an
    empty line."""
    return [CONSOLE_RULE, ""]


def ConsoleBlockStartOfChunk(toolName, chunk):
    """The lines that open the Console block of a run of *toolName* on
    *chunk*: ConsoleBlockStart and the label of the chunk."""
    return ConsoleBlockStart(toolName) + [ConsoleLabelledLine("Chunk:", getattr(chunk, "label", "") or "(unnamed)")]


def ConsoleLabelledLine(label, value, labelWidth=CONSOLE_LABEL_WIDTH):
    """One line of a Console block: *label* padded to *labelWidth*
    characters and then *value*, or the label and the value separated
    by one space when the label is at least that wide."""
    if len(label) >= labelWidth:
        return "{} {}".format(label, value)
    return label.ljust(labelWidth) + str(value)


def PrintConsoleLines(lines):
    """Print *lines* to the Console pane, one per line."""
    for line in lines:
        print(line)


# ===================================================================
# Tolerated errors
# ===================================================================

_REPORTED_SUPPRESSED_ERRORS = set()


def ReportSuppressedError(contextLabel, error):
    """Print a deliberately tolerated error once per location and
    session, so that a failure that is caught does not vanish
    without trace."""
    if contextLabel in _REPORTED_SUPPRESSED_ERRORS:
        return
    _REPORTED_SUPPRESSED_ERRORS.add(contextLabel)
    print("  [MetaFOCUS] {}: {} (tolerated, shown once)".format(
        contextLabel, DescribeException(error)))


# ===================================================================
# Reference panel data extraction
# ===================================================================

# Column cipher for chunk.exportReference().  Each letter maps to
# one column in the Metashape Reference panel CSV output:
#   n = Label
#   x,y,z = X/Easting, Y/Northing, Z/Altitude (reference)
#   X,Y,Z = Accuracy X, Accuracy Y, Accuracy Z
#   a,b,c = the three rotation angles (reference), in the rotation
#           angle convention of the chunk (see RotationAngleNames):
#           (yaw, pitch, roll), (omega, phi, kappa), and so on.  The
#           field names below use yaw, pitch, and roll for all of them.
#   A,B,C = Accuracy of the three rotation angles
#   u,v,w = X estimated, Y estimated, Z estimated
#   U,V,W = X error, Y error, Z error
#   d,e,f = the three rotation angles, estimated
#   D,E,F = the errors of the three rotation angles
#   p,q,r = X, Y, and Z standard deviation (the Metashape reference
#           calls them variances: to be confirmed in the trial run)
#   i,j,k = standard deviation of the three rotation angles (idem)
_REFERENCE_COLUMNS_STRING = "nxyzXYZabcABCuvwUVWdefDEFpqrijk"

# The keyword of a WKT and its first quoted string, the name of the CRS.
_WKT_NAME_PATTERN = re.compile(r'^\s*[A-Za-z_0-9]+\[\s*"([^"]*)"')


def CrsNameFromWkt(text):
    """The name of a CRS from its well-known text (WKT), the first quoted
    string after its keyword ("Local Coordinates (m)" of
    LOCAL_CS["Local Coordinates (m)",...]), or *text* itself when it is
    no WKT."""
    match = _WKT_NAME_PATTERN.match(text or "")
    return match.group(1) if match else (text or "").strip()


def CameraReferenceCrs(chunk):
    """The CRS of the reference data of the images of *chunk*: the camera
    reference CRS of the Reference Settings (chunk.camera_crs) when the
    chunk has a separate one, otherwise the chunk CRS, or None without
    either.  In a check in Metashape 2.3.2, chunk.exportReference() wrote
    the coordinates of the images in this CRS, while its comment line and
    its header named the chunk CRS."""
    cameraCrs = getattr(chunk, "camera_crs", None)
    return cameraCrs if cameraCrs is not None else getattr(chunk, "crs", None)


# A field of the header of Metashape's reference export with the unit of
# the 'Accuracy' values of one axis, for example Accuracy_X_(ft).
_ACCURACY_HEADER_PATTERN = re.compile(r"^Accuracy_([XYZ])_\((.+)\)$")


def ReferenceAccuracyUnits(headerFields):
    """The units of the 'Accuracy' values of X, Y, and Z that the header
    fields *headerFields* of Metashape's reference export give, for
    example ("ft", "ft", "ft"), or None when the header gives none of
    them.  In a check in Metashape 2.3.2 the export wrote these values in
    feet, with (ft) in their headers, for a CRS in US survey feet, and in
    metres, with (m), for a geographic or a projected CRS in metres."""
    unitByAxis = {}
    for field in headerFields:
        match = _ACCURACY_HEADER_PATTERN.match(field.strip())
        if match:
            unitByAxis[match.group(1)] = match.group(2).strip()
    if not unitByAxis:
        return None
    return tuple(unitByAxis.get(axis, "") for axis in "XYZ")


# Fields that ExportReferenceData adds to every row: the units of the
# 'Accuracy' values of X, Y, and Z (ReferenceAccuracyUnits).
ACCURACY_UNIT_FIELDS = ("Accuracy_X_unit", "Accuracy_Y_unit", "Accuracy_Z_unit")

_REFERENCE_RAW_HEADERS = [
    "Label",
    "X_reference", "Y_reference", "Z_reference",
    "Accuracy_X", "Accuracy_Y", "Accuracy_Z",
    "Yaw_reference", "Pitch_reference", "Roll_reference",
    "Accuracy_Yaw", "Accuracy_Pitch", "Accuracy_Roll",
    "X_estimated", "Y_estimated", "Z_estimated",
    "X_error", "Y_error", "Z_error",
    "Yaw_estimated", "Pitch_estimated", "Roll_estimated",
    "Yaw_error", "Pitch_error", "Roll_error",
    "X_stdDeviation", "Y_stdDeviation", "Z_stdDeviation",
    "Yaw_stdDeviation", "Pitch_stdDeviation", "Roll_stdDeviation",
]


def ExportReferenceData(chunk):
    """Extract all Reference panel values for every image via
    chunk.exportReference().

    Uses Metashape's CSV export with the full column cipher to
    obtain reference coordinates, accuracies, estimated positions,
    errors, and standard deviations: matching the values shown
    in the Metashape Reference panel.

    Returns a dict keyed by the name of the row of an image:
        {rowName: {fieldName: stringValue, ...}, ...}

    Field names are defined in _REFERENCE_RAW_HEADERS.  All values
    are strings exactly as Metashape writes them (empty string if
    the field has no value).  An additional "CRS" field contains the
    name of the CRS of the reference data (CameraReferenceCrs), or
    without a CRS object the name in the WKT of the comment line of the
    export (CrsNameFromWkt), and the fields of ACCURACY_UNIT_FIELDS the
    units of the 'Accuracy' values of X, Y, and Z as the header of the
    export gives them (ReferenceAccuracyUnits), for example "ft", or an
    empty text.

    The dict is keyed by the names of the rows of the export, which names
    the row of an image labelled plane_00 by the file name of its photo,
    plane_00.png (Metashape 2.3.2).  The rows
    of a name shared by several images cannot be matched to the right
    image: such a name maps to an empty dict (its images get empty
    cells, and no caller falls back to the row of another image), and
    the names are given in the Console.  A row that ends without its
    trailing empty fields is completed with empty values.

    Returns None if the export fails (the cause is printed in the
    Console)."""
    if chunk is None:
        return {}

    temporaryFilePath = None
    try:
        temporaryFileDescriptor, temporaryFilePath = tempfile.mkstemp(
            suffix=".csv", prefix="metashape_ref_")
        os.close(temporaryFileDescriptor)

        chunk.exportReference(
            path=temporaryFilePath,
            format=Metashape.ReferenceFormatCSV,
            items=Metashape.ReferenceItemsCameras,
            delimiter=",",
            columns=_REFERENCE_COLUMNS_STRING)

        referenceCrs = CameraReferenceCrs(chunk)
        crsName = getattr(referenceCrs, "name", None) or "Local Coordinates"
        accuracyUnits = None
        rowsByLabel = {}

        with open(temporaryFilePath, mode="r",
                  encoding="utf-8-sig") as fileHandle:
            lines = fileHandle.read().splitlines()
        # The CRS line holds a WKT with commas, so it is read before the
        # CSV reader splits the lines.
        dataLines = []
        for line in lines:
            if line.startswith("#") and "CoordinateSystem:" in line:
                # The comment names the chunk CRS, also when the images
                # have a camera reference CRS of their own, so it gives the
                # name only without a CRS object.
                if getattr(referenceCrs, "name", None) is None:
                    crsName = CrsNameFromWkt(line.split("CoordinateSystem:", 1)[1].strip())
            else:
                dataLines.append(line)
        for rowData in csv.reader(dataLines):
            if not rowData:
                continue

            # Skip comment and header lines, and read the units of the
            # 'Accuracy' values from the header.
            if rowData[0].lstrip("#").strip() == "Label":
                accuracyUnits = ReferenceAccuracyUnits(rowData) or accuracyUnits
                continue
            if (rowData[0].startswith("#") or
                    rowData[0] == "n"):
                continue

            # Parse data row, completing the empty trailing fields.
            missingFieldCount = len(_REFERENCE_RAW_HEADERS) - len(rowData)
            if missingFieldCount > 0:
                rowData = rowData + [""] * missingFieldCount
            rowFields = dict(zip(
                _REFERENCE_RAW_HEADERS,
                rowData[:len(_REFERENCE_RAW_HEADERS)]))
            rowsByLabel.setdefault(rowFields["Label"], []).append(rowFields)

        result = {}
        duplicatedLabels = []
        for imageLabel, rows in rowsByLabel.items():
            if len(rows) > 1:
                duplicatedLabels.append(imageLabel)
                result[imageLabel] = {}
                continue
            rows[0]["CRS"] = crsName
            for fieldName, unitName in zip(ACCURACY_UNIT_FIELDS, accuracyUnits or ("", "", "")):
                rows[0][fieldName] = unitName
            result[imageLabel] = rows[0]
        if duplicatedLabels:
            print("  [MetaFOCUS] Several images share the {} {} in Metashape's "
                  "reference export, so their Reference panel values cannot be "
                  "matched to an image and are left empty.".format(
                      "name" if len(duplicatedLabels) == 1 else "names",
                      JoinWithAnd(sorted(duplicatedLabels))))
        return result

    except Exception as exception:
        print("  [MetaFOCUS] The Reference panel values could not be "
              "exported: {}".format(DescribeException(exception)))
        return None

    finally:
        if temporaryFilePath is not None:
            try:
                os.remove(temporaryFilePath)
            except OSError as suppressedError:
                ReportSuppressedError("photogrammetryUtilities.ExportReferenceData", suppressedError)
