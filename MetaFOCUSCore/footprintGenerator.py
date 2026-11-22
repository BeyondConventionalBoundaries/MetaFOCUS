"""
Image Footprint Generator for Agisoft Metashape Professional
=============================================================

Generates image footprints by projecting the image border onto a
plane fitted through the tie points of each image, the triangular
mesh, or the raster DEM, and computes their area, perimeter, and
per-image GSD statistics.

Author:      Geert J. Verhoeven (ORCID: 0000-0003-4825-9604)
Version:     1.0.0 (2026-11-18)
Date:        2026-06-12
Licence:     GPL-3.0-or-later WITH GPL-3.0-linking-exception
Contact:     geert.verhoeven@univie.ac.at
Requires:    Agisoft Metashape Professional 2.x+
Developed for: Agisoft Metashape Professional 2.3.2

Dependencies:
    - photogrammetryUtilities.py (shared module of the MetaFOCUSCore
      package).
    - PySide2 (bundled with Metashape).

Usage:
    MetaFOCUS > Surface-Derived Project Outputs > Image Footprint
    Generator (the MetaFOCUS loader registers the menu item).

Functionality:
    1. Generate 3D footprints: the image border is sampled every
       boundary step and projected onto the plane, the triangular
       mesh, or the raster DEM.
    2. Compute per-image GSD statistics on a dense grid of pixel
       positions: the area-equivalent GSD, from the row and column
       steps of the auxiliary rays on the local tangent plane.
    3. Compute the area (best-fit plane and/or raster DEM) and the 3D
       perimeter.
    4. Project existing 3D footprints to 2D.

Notes:
    - Raster-DEM-based area (shape.area()) computes the full polygon
      area with slope corrections where the raster DEM has coverage.
      Where the footprint extends beyond the raster DEM, Metashape falls
      back to the flat projected area for those portions: it does
      not clip the polygon to the raster DEM extent.  This makes the
      best-fit plane area the more consistent choice when footprints
      extend significantly beyond the raster DEM.  MetaFOCUS computes the
      best-fit plane area from the vertices (FootprintAreaOnBestFitPlane),
      because Metashape 2.3.2 raises "Null orthomosaic" from
      Shape.areaFitted() in a chunk without an orthomosaic.
    - At every sampled pixel position, four auxiliary rays through the
      midpoints of the edges of the corresponding photosite (+-1/2 along
      the row and the column in pixel coordinates) are intersected with
      the local tangent plane at the central ray's surface hit, and the
      area-equivalent GSD, sqrt(GSD_row x GSD_column x sin(phi)) with phi
      the angle between the projected row and column steps, is the value
      of that position entering the statistics.
    - Plane, triangular mesh, and raster DEM modes supply the fitted
      plane normal, the interpolated 3-ring vertex normal, or the
      normal from the raster DEM gradient that defines the local
      tangent plane.  No bounding volume hierarchy (BVH) is needed in
      raster DEM mode.
    - Every mode samples the whole image border, because the lens
      distortion bends the projected border even on a plane.  The
      footprint vertices are written in the CRS of the shape
      layer.  A footprint whose border rays partly miss the
      surface carries the attribute 00_PartialFootprint and gets no
      average GSD from area.  On a triangular mesh, a border ray that
      meets the mesh beyond the grazing limit counts as a miss, and a
      border pixel position without a hit takes as its vertex the hit of
      a pixel position further inwards whose ray meets the mesh within
      that limit (EdgeHitTowards).
    - The average GSD from area divides the area by (number of pixel
      columns - 1) x (number of pixel rows - 1), the sampling intervals
      that the footprint through the outermost pixel positions spans.
      When the footprint lies on a plane and its area is taken on that
      plane (the best-fit plane area, used when no raster DEM area is
      available), the value is, to first order and up to the sampling
      of the border, the root mean square, not the mean, of the local
      area-equivalent GSD on that plane, averaged uniformly in pixel
      coordinates over the rectangle that the outermost pixel
      positions span, a root mean square that the values of the dense
      grid can only approximate.
    - The attribute names carry no unit: the attribute 00_Units gives
      it, metres or, in a chunk without a physical scale (see
      PhysicalChunkScale), chunk units.  The check boxes of the
      properties name the unit of the active chunk.
"""

import Metashape
import functools
import math
import os
import importlib

from PySide2 import QtWidgets, QtCore

# ---- Import shared utilities ----------------------------------------
from . import photogrammetryUtilities
importlib.reload(photogrammetryUtilities)

from .photogrammetryUtilities import (
    RollingShutterConsoleLines, RollingShutterModelName, RollingShutterRayText,
    ReportSuppressedError, FormatCountWithNoun, JoinWithAnd, DescribeException, IsMasterImage, CountMasterImages,
    VectorSubtract, VectorAdd, VectorScale, VectorDot, VectorCross, VectorLength, VectorNormalize, VectorFromMetashape,
    FitPlaneToPoints, IntersectRayWithPlane,
    BuildMeshBvh, QueryBvhClosestHit,
    BuildTrackIdToPointMap, GetValidProjections, HomogeneousToCartesian3D,
    TransformChunkPointToCrs, CrsKind, IsGeoreferencedChunk,
    ComputeDenseGridGsdOnPlane, ComputeDenseGridGsdOnMesh,
    ComputeDenseGridGsdOnDem, DemSurface, MeshSurface, GRAZING_COSINE_THRESHOLD,
    ImageRays, CastCheckedRay, ComputeWithOneKindOfRays, ExactRayText,
    PhysicalChunkScale, IsChunkScalePhysical, DescribeMissingPhysicalScale, LengthUnitsText,
    RecordRasterDemReport, DescribeRasterDemMisses, AddRasterDemMissCounts, PerspectiveCentreBelowRasterDemNote,
    FormatLengthWithDecimals, FormatLengthForCsv,
    PlaneSpreadOfImage, PlaneSpreadWarningLine, PlaneSpreadSummaryText, ChunkHasTriangularMesh,
    ConsoleBlockEnd, ConsoleBlockStartOfChunk, PrintConsoleLines,
    SelectedMasterImages, SelectedImagesStatusText, SelectedImagesConsoleLines, NoSelectedImageText,
    )


# ===================================================================
# Boundary sampling
# ===================================================================

def SampleBoundaryPixels(pixelColumnCount, pixelRowCount, pixelStep):
    """Pixel positions sampled every *pixelStep* pixels along the four
    image borders, forming the footprint outline.  When the number of
    pixel columns or rows is one more than a multiple of the step, a corner ends
    one border and starts the next: it is listed once.  Only the first
    pixel can come back at the end of the list (a number of pixel rows
    one more than a multiple of the step), and CreateFootprintShapes drops
    that closing vertex."""
    pixelList = []
    for pixelColumn in range(0, pixelColumnCount, pixelStep):
        pixelList.append((float(pixelColumn)+0.5, 0.5))
    for pixelRow in range(0, pixelRowCount, pixelStep):
        pixelList.append((float(pixelColumnCount)-0.5, float(pixelRow)+0.5))
    for pixelColumn in range(pixelColumnCount, 0, -pixelStep):
        pixelList.append((float(pixelColumn)-0.5, float(pixelRowCount)-0.5))
    for pixelRow in range(pixelRowCount, 0, -pixelStep):
        pixelList.append((0.5, float(pixelRow)-0.5))
    return [pixel for pixelIndex, pixel in enumerate(pixelList)
            if pixelIndex == 0 or pixel != pixelList[pixelIndex - 1]]


# ===================================================================
# Footprint computation
# ===================================================================

# Prefix of the Console lines of the tool, also of the line of an image
# whose rays fell back to the rays from the perspective centre (ImageRays).
FOOTPRINT_CONSOLE_PREFIX = "[Image Footprint Generator]"


def FootprintImageRays(camera, contextName="footprintGenerator.FootprintImageRays"):
    """ImageRays of *camera* for the footprint and the GSD of one image,
    whose Console line carries the prefix of the tool."""
    return ImageRays(camera, contextName, FOOTPRINT_CONSOLE_PREFIX)


def _PlaneHitPoint(planeCentroid, planeNormal, ray):
    """Where the ray (origin, direction) meets the plane in front of its
    origin, or None."""
    originTuple, rayDirection = ray
    hitParameter = IntersectRayWithPlane(originTuple, rayDirection, planeCentroid, planeNormal)
    if hitParameter is None or hitParameter <= 0:
        return None
    return VectorAdd(originTuple, VectorScale(rayDirection, hitParameter))


def _MeshHit(bvhRootNode, chunkScale, ray):
    """(hit point, slant distance) where the ray (origin, direction) meets
    the triangular mesh first, or None.  Used for a root node without
    vertex normals (see BuildMeshBvh), for which the grazing limit cannot
    be tested."""
    originTuple, rayDirection = ray
    inverseDirection = tuple(photogrammetryUtilities.SafeReciprocal(component) for component in rayDirection)
    hitParameter = QueryBvhClosestHit(bvhRootNode, originTuple, rayDirection, inverseDirection)
    if hitParameter is None:
        return None
    return (VectorAdd(originTuple, VectorScale(rayDirection, hitParameter)),
            hitParameter * VectorLength(rayDirection) * chunkScale)


# On a triangular mesh, a border pixel position whose ray misses the
# mesh, meets it beyond the grazing limit of the dense grid GSD
# (GRAZING_COSINE_THRESHOLD, an incidence above about 84 degrees), or that
# has no ray takes as its vertex the hit of a pixel position on the line
# from it to the reference pixel position (ReferencePixelPosition), whose
# ray meets the mesh within the limit (EdgeHitTowards).  The line is
# followed from the border in at most EDGE_SEARCH_STEP_COUNT equal steps,
# and within the first step that ends at such a hit the edge is found by
# bisection to EDGE_SEARCH_TOLERANCE_PIXELS.
EDGE_SEARCH_STEP_COUNT = 16
EDGE_SEARCH_TOLERANCE_PIXELS = 1.0

# The reference pixel position is the image centre when its ray meets the
# mesh within the limit, otherwise the pixel position nearest the image
# centre whose ray does, among REFERENCE_GRID_SIZE by REFERENCE_GRID_SIZE
# pixel positions spread evenly over the image.
REFERENCE_GRID_SIZE = 9


def _MeshHitWithinGrazingLimit(surface, ray):
    """(hit point, slant distance) where the ray (origin, direction) first
    meets the triangular mesh of *surface* (MeshSurface), or None when it
    misses the mesh or meets it beyond the grazing limit
    (GRAZING_COSINE_THRESHOLD).  The incidence is that of the dense grid
    GSD, with the interpolated vertex normal, and a hit whose
    interpolated normals cancel counts as a miss, as there."""
    hit = surface.Hit(ray[1], ray[0])
    if hit is None:
        return None
    hitPoint, normal, slantDistance = hit
    if abs(VectorDot(VectorNormalize(ray[1]), normal)) < GRAZING_COSINE_THRESHOLD:
        return None
    return hitPoint, slantDistance


def _HitAtPixel(imageRays, castRay, findHit, chunkPointOfHit, pixel):
    """The hit (findHit) of the ray through *pixel*, (pixelX, pixelY), or
    None for a miss or a pixel position without a ray (CastCheckedRay)."""
    return CastCheckedRay(imageRays, pixel[0], pixel[1], castRay, findHit, chunkPointOfHit)[1]


def _SquaredPixelDistance(firstPixel, secondPixel):
    return (firstPixel[0] - secondPixel[0]) ** 2 + (firstPixel[1] - secondPixel[1]) ** 2


def _PixelOnLine(startPixel, endPixel, fraction):
    """The pixel position at *fraction* of the way from *startPixel* to
    *endPixel*."""
    return (startPixel[0] + fraction * (endPixel[0] - startPixel[0]),
            startPixel[1] + fraction * (endPixel[1] - startPixel[1]))


def ReferencePixelPosition(pixelColumnCount, pixelRowCount, hitAtPixel):
    """The pixel position towards which EdgeHitTowards moves the vertices
    of the border pixel positions without a hit: the image centre when
    hitAtPixel(position) gives a hit there, otherwise the pixel position
    nearest the image centre with a hit on a grid of REFERENCE_GRID_SIZE by
    REFERENCE_GRID_SIZE pixel positions spread evenly over the image, or
    None when none of them has one."""
    imageCentre = (0.5 * pixelColumnCount, 0.5 * pixelRowCount)
    if hitAtPixel(imageCentre) is not None:
        return imageCentre
    gridPositions = [((column + 0.5) * pixelColumnCount / REFERENCE_GRID_SIZE,
                      (row + 0.5) * pixelRowCount / REFERENCE_GRID_SIZE)
                     for row in range(REFERENCE_GRID_SIZE) for column in range(REFERENCE_GRID_SIZE)]
    gridPositions.sort(key=functools.partial(_SquaredPixelDistance, imageCentre))
    for gridPosition in gridPositions:
        if gridPosition != imageCentre and hitAtPixel(gridPosition) is not None:
            return gridPosition
    return None


def EdgeHitTowards(borderPixel, referencePixel, hitAtPixel):
    """The hit of a pixel position whose ray gives one (hitAtPixel), on
    the line from *borderPixel*, which gives none, to *referencePixel*.
    The line is followed from the border in at most EDGE_SEARCH_STEP_COUNT
    equal steps, and within the first step that ends at a hit the edge
    between pixel positions with and without one is found by bisection to
    EDGE_SEARCH_TOLERANCE_PIXELS.  A part of the surface narrower than a
    step can therefore be passed, and where the line crosses the edge
    more than once within that step, the bisection finds one of the
    crossings.  Returns the hit at the inner end of the last interval, or
    None when no pixel position of the steps gives one."""
    lineLength = math.sqrt(_SquaredPixelDistance(borderPixel, referencePixel))
    if not lineLength > 0.0:
        return None
    stepCount = max(1, min(EDGE_SEARCH_STEP_COUNT, int(math.ceil(lineLength / EDGE_SEARCH_TOLERANCE_PIXELS))))
    outerFraction = 0.0
    for stepIndex in range(1, stepCount + 1):
        innerFraction = float(stepIndex) / stepCount
        innerHit = hitAtPixel(_PixelOnLine(borderPixel, referencePixel, innerFraction))
        if innerHit is None:
            outerFraction = innerFraction
            continue
        while (innerFraction - outerFraction) * lineLength > EDGE_SEARCH_TOLERANCE_PIXELS:
            middleFraction = 0.5 * (outerFraction + innerFraction)
            middleHit = hitAtPixel(_PixelOnLine(borderPixel, referencePixel, middleFraction))
            if middleHit is None:
                outerFraction = middleFraction
            else:
                innerFraction, innerHit = middleFraction, middleHit
        return innerHit
    return None


def _CastMeshBorderRays(imageRays, boundaryPixels, findHit, pixelColumnCount, pixelRowCount, meshReport):
    """The hits (hit point, slant distance) of findHit(ray) of the rays
    through the border pixel positions on the triangular mesh (within the
    grazing limit, see _MeshHitWithinGrazingLimit).  A border pixel
    position without one gets the hit of EdgeHitTowards towards the
    reference pixel position (ReferencePixelPosition), and None stays
    where no hit is found.  *meshReport* (a dict) receives
    movedVertexCount, the border pixel positions whose vertex was moved
    inwards, and withoutRayCount, the border pixel positions without a
    ray (counted as in ImageRays).  The rays of the search inwards are not
    counted in the ray report of the image (ImageRays.rayReport), so that
    00_Rays and the Console count the pixel positions of the border, as
    00_PartialFootprint does, and those of the dense grid GSD."""
    hitAtPixel = functools.partial(_HitAtPixel, imageRays, imageRays.Ray, findHit, _FirstOfHit)
    withoutRayCountBefore = imageRays.rayReport["withoutRay"]
    hits = [hitAtPixel(pixel) for pixel in boundaryPixels]
    meshReport["withoutRayCount"] = imageRays.rayReport["withoutRay"] - withoutRayCountBefore
    meshReport["movedVertexCount"] = 0
    if all(hit is not None for hit in hits) or not pixelColumnCount or not pixelRowCount:
        return hits
    reportOfTheBorder = dict(imageRays.rayReport)
    try:
        referencePixel = ReferencePixelPosition(pixelColumnCount, pixelRowCount, hitAtPixel)
        if referencePixel is None:
            return hits
        for pixelIndex, pixel in enumerate(boundaryPixels):
            if hits[pixelIndex] is None:
                hits[pixelIndex] = EdgeHitTowards(pixel, referencePixel, hitAtPixel)
                if hits[pixelIndex] is not None:
                    meshReport["movedVertexCount"] += 1
    finally:
        imageRays.rayReport.clear()
        imageRays.rayReport.update(reportOfTheBorder)
    return hits


def _DemHit(surface, ray):
    """(hit point in the work coordinates, slant distance) where the ray
    (origin, unit direction) of the raster DEM surface meets it, or
    None."""
    return surface.Intersect(ray[1], ray[0])


def _HitPoint(hit):
    return hit


def _FirstOfHit(hit):
    return hit[0]


def _ChunkPointOfDemHit(surface, hit):
    return surface.ToChunk(hit[0])


def _CastBorderRays(imageRays, boundaryPixels, castRay, findHit, chunkPointOfHit):
    """The hits of the border rays (CastCheckedRay), None for a miss."""
    return [CastCheckedRay(imageRays, pixelX, pixelY, castRay, findHit, chunkPointOfHit)[1]
            for pixelX, pixelY in boundaryPixels]


def ComputeFootprintOnPlane(camera, validProjections, boundaryPixels, imageRays=None):
    """Intersect the rays through the border pixel positions with the
    tie-point plane of the image.  The rays are those of *imageRays*
    (ImageRays of the image by default), which follow the camera motion
    of an image with rolling-shutter compensation, and when they fall
    back part of the way, every border ray is cast again
    (ComputeWithOneKindOfRays).
    Returns (hitPointList, hitCount, missCount), hits in chunk
    coordinates (None for misses)."""
    if camera.center is None: return None, 0, 0
    if imageRays is None:
        imageRays = FootprintImageRays(camera, "footprintGenerator.ComputeFootprintOnPlane")
    if imageRays.perspectiveCentre is None: return None, 0, 0
    tiePointCoordinates = []
    for point, _ in validProjections:
        cartesian = HomogeneousToCartesian3D(point.coord)
        if cartesian: tiePointCoordinates.append(cartesian)
    if len(tiePointCoordinates) < 3: return None, 0, 0
    planeCentroid, planeNormal = FitPlaneToPoints(tiePointCoordinates)
    if planeNormal is None: return None, 0, 0
    hitPointList = ComputeWithOneKindOfRays(functools.partial(
        _CastBorderRays, imageRays, boundaryPixels, imageRays.Ray,
        functools.partial(_PlaneHitPoint, planeCentroid, planeNormal), _HitPoint))
    imageRays.ReportFallBack()
    missCount = sum(1 for hitPoint in hitPointList if hitPoint is None)
    return hitPointList, len(hitPointList) - missCount, missCount

def ComputeFootprintOnMesh(camera, bvhRootNode, boundaryPixels, chunkScale, imageRays=None, meshReport=None):
    """Intersect the rays through the border pixel positions with the
    triangular mesh through the bounding volume hierarchy (BVH).  A
    border ray that meets the mesh beyond the grazing limit counts as a
    miss, with the vertex normals that BuildMeshBvh gives the root node
    (without them every hit counts), and a border pixel position without
    a hit gets the vertex moved inwards, where one is found
    (_CastMeshBorderRays).
    Returns (hitPointList, hitCount, missCount, distanceList): hitCount
    counts the border pixel positions whose rays meet the mesh within
    the grazing limit and missCount the others, the slant distances are
    in metres (in chunk units for an unscaled chunk), and both lists
    hold None where no vertex is found.  *meshReport*, when a dict,
    receives movedVertexCount and withoutRayCount.  The rays are those of
    *imageRays* (ImageRays of the image by default), as in
    ComputeFootprintOnPlane."""
    if camera.center is None: return None, 0, 0, None
    if imageRays is None:
        imageRays = FootprintImageRays(camera, "footprintGenerator.ComputeFootprintOnMesh")
    if imageRays.perspectiveCentre is None: return None, 0, 0, None
    vertexNormals = getattr(bvhRootNode, "vertexNormals", None)
    if vertexNormals is not None:
        findHit = functools.partial(_MeshHitWithinGrazingLimit, MeshSurface(
            camera, (bvhRootNode, vertexNormals), chunkScale, rays=imageRays))
    else:
        findHit = functools.partial(_MeshHit, bvhRootNode, chunkScale)
    calibration = getattr(camera, "calibration", None)
    report = meshReport if meshReport is not None else {}
    hits = ComputeWithOneKindOfRays(functools.partial(
        _CastMeshBorderRays, imageRays, boundaryPixels, findHit,
        getattr(calibration, "width", None), getattr(calibration, "height", None), report))
    imageRays.ReportFallBack()
    hitPointList = [hit[0] if hit is not None else None for hit in hits]
    distanceList = [hit[1] if hit is not None else None for hit in hits]
    hitCount = sum(1 for hit in hits if hit is not None) - report["movedVertexCount"]
    return hitPointList, hitCount, len(hits) - hitCount, distanceList

def ComputeFootprintOnDem(camera, chunk, boundaryPixels, chunkScale, rasterDemReport=None, imageRays=None):
    """Intersect the rays through the border pixel positions with the
    raster DEM through the shared raster DEM engine: work coordinates
    of DemSurface, first crossing at the cell size of the raster DEM refined to
    convergence, raster DEM read through its own projection. Returns
    (hitPointList, hitCount, missCount, distanceList) where hitPointList
    contains chunk-coordinate 3-tuples and distanceList slant distances
    in metres (in chunk units for a chunk without a physical scale), with
    None in both lists for a miss.  The misses by reason are added to
    *rasterDemReport* when it is a dict (see RecordRasterDemReport).
    The rays are those of *imageRays* (ImageRays of the image by
    default), as in ComputeFootprintOnPlane, and the misses of rays cast
    again are counted once.  *chunkScale* is accepted for interface
    symmetry."""
    if camera.center is None or getattr(chunk, "elevation", None) is None:
        return None, 0, 0, None
    if imageRays is None:
        imageRays = FootprintImageRays(camera, "footprintGenerator.ComputeFootprintOnDem")
    surface = DemSurface(camera, chunk, rays=imageRays)
    if not surface.isUsable:
        return None, 0, 0, None
    hits = ComputeWithOneKindOfRays(
        functools.partial(_CastBorderRays, imageRays, boundaryPixels, surface.Ray,
                          functools.partial(_DemHit, surface), functools.partial(_ChunkPointOfDemHit, surface)),
        surface.ResetMissCounts)
    imageRays.ReportFallBack()
    hitPointList = [surface.ToChunk(hit[0]) if hit is not None else None for hit in hits]
    distanceList = [hit[1] if hit is not None else None for hit in hits]
    missCount = sum(1 for hit in hits if hit is None)
    RecordRasterDemReport(surface, rasterDemReport)
    return hitPointList, len(hits) - missCount, missCount, distanceList


def ComputeImageFootprint(camera, surfaceMode, chunk, boundaryStep,
                          pointByTrackId=None, bvhRootNode=None, chunkScale=1.0, imageRays=None):
    """Footprint of one image on the surface of *surfaceMode* ("plane",
    "mesh", or "dem"), with the rays of *imageRays* (ImageRays of the
    image by default) and, in triangular mesh mode, the root node of the
    BVH of BuildMeshBvh.

    Every surface mode samples the whole image border every
    *boundaryStep* pixels: the lens distortion bends the projected
    border even on a plane, so four corners are not enough.  Returns
    a dictionary with hitPointList (chunk coordinates, None for a
    miss, or None when the image was skipped), hitCount, missCount,
    boundaryPixelCount, withoutRayCount (the border pixel positions
    without a ray, counted among the misses), movedVertexCount (in
    triangular mesh mode the border pixel positions whose vertex was
    moved inwards, see ComputeFootprintOnMesh, otherwise 0),
    pixelColumnCount, pixelRowCount, validProjections (the
    tie-point projections of the image in plane mode), rasterDemReport
    (the misses of the border rays in raster DEM mode, see
    RecordRasterDemReport, otherwise None), and skipReason (None when the
    footprint was computed, otherwise the reason the image was
    skipped)."""
    footprintResult = dict(hitPointList=None, hitCount=0, missCount=0,
                           boundaryPixelCount=0, withoutRayCount=0, movedVertexCount=0,
                           pixelColumnCount=None, pixelRowCount=None, validProjections=None,
                           rasterDemReport=None, skipReason=None)
    calibration = camera.calibration
    if calibration is None:
        footprintResult["skipReason"] = "no calibration"
        return footprintResult
    pixelColumnCount = calibration.width; pixelRowCount = calibration.height
    if not pixelColumnCount or not pixelRowCount:
        footprintResult["skipReason"] = "no pixel count"
        return footprintResult
    footprintResult.update(pixelColumnCount=pixelColumnCount, pixelRowCount=pixelRowCount)
    if camera.center is None:
        footprintResult["skipReason"] = "no perspective centre"
        return footprintResult
    boundaryPixels = SampleBoundaryPixels(pixelColumnCount, pixelRowCount, boundaryStep)
    if imageRays is None:
        imageRays = FootprintImageRays(camera, "footprintGenerator.ComputeImageFootprint")
    withoutRayCountBefore = imageRays.rayReport["withoutRay"]
    meshReport = None
    if surfaceMode == "dem":
        footprintResult["rasterDemReport"] = {}
        hitPointList, hitCount, missCount, _ = ComputeFootprintOnDem(
            camera, chunk, boundaryPixels, chunkScale, footprintResult["rasterDemReport"], imageRays)
        skipReason = "the raster DEM could not be used for this image"
    elif surfaceMode == "mesh":
        meshReport = {}
        hitPointList, hitCount, missCount, _ = ComputeFootprintOnMesh(
            camera, bvhRootNode, boundaryPixels, chunkScale, imageRays, meshReport)
        skipReason = "no perspective centre"
    else:
        validProjections = (GetValidProjections(camera, chunk.tie_points, pointByTrackId)
                            if pointByTrackId else [])
        footprintResult["validProjections"] = validProjections
        hitPointList, hitCount, missCount = ComputeFootprintOnPlane(
            camera, validProjections, boundaryPixels, imageRays)
        skipReason = ("the valid tie points in this image do not define a plane "
                      "(fewer than three, or all on one line)")
    if hitPointList is None:
        footprintResult["skipReason"] = skipReason
        return footprintResult
    footprintResult.update(hitPointList=hitPointList, hitCount=hitCount,
                           missCount=missCount,
                           boundaryPixelCount=len(boundaryPixels),
                           withoutRayCount=imageRays.rayReport["withoutRay"] - withoutRayCountBefore)
    if meshReport:
        # The search inwards casts rays of its own, so the border pixel
        # positions without a ray are those of the border rays alone.
        footprintResult.update(withoutRayCount=meshReport["withoutRayCount"],
                               movedVertexCount=meshReport["movedVertexCount"])
    return footprintResult


# ===================================================================
# Properties definition: order matters for GUI and attributes
# ===================================================================

# Each entry: (internal key, dialog label, shape attribute name, Console
# header).  The attribute names carry no unit, because the values are in
# chunk units in a chunk without scale: the attribute
# UNITS_ATTRIBUTE_NAME gives the unit.  The internal keys of the lengths
# and areas end in _m and _m2, but they are identifiers only.
ALL_COMPUTABLE_PROPERTIES = [
    ("AreaBestFitPlane_m2",    "Area, best-fit plane (m\u00b2)",            "01_AreaBestFitPlane",      "Area, best-fit plane (m\u00b2)"),
    ("AreaRasterDEM_m2",       "Area, raster DEM projection (m\u00b2)",     "02_AreaRasterDEM",         "Area, raster DEM (m\u00b2)"),
    ("Perimeter3D_m",    "Perimeter, 3D (m)",                         "03_Perimeter3D",           "Perimeter, 3D (m)"),
    ("GSD_averageFromArea_m",    "Average GSD from area (m)",                "04_GSD_averageFromArea",   "GSD from area (m)"),
    ("GSD_minimum_m",        "GSD minimum, dense grid (m)",              "05_GSD_minimum",           "GSD minimum (m)"),
    ("GSD_percentile02_m",        "GSD 2nd percentile, dense grid (m)",       "06_GSD_percentile02",      "GSD 2nd percentile (m)"),
    ("GSD_average_m",    "GSD average, dense grid (m)",              "07_GSD_average",           "GSD average (m)"),
    ("GSD_median_m",     "GSD median, dense grid (m)",               "08_GSD_median",            "GSD median (m)"),
    ("GSD_percentile98_m",        "GSD 98th percentile, dense grid (m)",      "09_GSD_percentile98",      "GSD 98th percentile (m)"),
    ("GSD_maximum_m",        "GSD maximum, dense grid (m)",              "10_GSD_maximum",           "GSD maximum (m)"),
    ("GSD_coverage_percent", "GSD coverage, dense grid (%)",              "11_GSD_coverage_percent",  "GSD coverage (%)"),
]

# Internal keys for property selection (used in dialogs and logic).
PROPERTY_KEY_ORDER = [propertyDefinition[0] for propertyDefinition in ALL_COMPUTABLE_PROPERTIES]

# Map: internal key → Metashape attribute name (with number prefix for ordering).
PROPERTY_ATTRIBUTE_NAMES = {propertyDefinition[0]: propertyDefinition[2] for propertyDefinition in ALL_COMPUTABLE_PROPERTIES}

# Properties whose values carry no length unit (a percentage).
UNITLESS_PROPERTY_KEYS = {"GSD_coverage_percent"}

# Shape attribute that gives the unit of the lengths, the areas being in
# its square: written on every footprint that stores a length or an area.
UNITS_ATTRIBUTE_NAME = "00_Units"

# Map: internal key → Console column header.
PROPERTY_CONSOLE_HEADERS = {propertyDefinition[0]: propertyDefinition[3] for propertyDefinition in ALL_COMPUTABLE_PROPERTIES}

# The one property that is neither a length nor an area.
COVERAGE_PROPERTY_KEY = "GSD_coverage_percent"

DENSE_GRID_PROPERTY_KEYS = {
    "GSD_minimum_m", "GSD_percentile02_m", "GSD_average_m",
    "GSD_median_m", "GSD_percentile98_m", "GSD_maximum_m", "GSD_coverage_percent"}


# ===================================================================
# Property values, labels, and Console summary
# ===================================================================

def ComputePropertyValues(footprintData):
    """Value of every computable property of one footprint, keyed by
    the internal property key (None where it was not computed)."""
    denseGridGsdStatistics = footprintData.get("denseGridGsd")
    return {
        "AreaBestFitPlane_m2": footprintData.get("areaFitted"),
        "AreaRasterDEM_m2": footprintData.get("areaDem"),
        "Perimeter3D_m": footprintData.get("perimeter3d"),
        "GSD_averageFromArea_m": footprintData.get("averageGsdFromArea"),
        "GSD_minimum_m": denseGridGsdStatistics.get("minimum") if denseGridGsdStatistics else None,
        "GSD_percentile02_m": denseGridGsdStatistics.get("percentile02") if denseGridGsdStatistics else None,
        "GSD_average_m": denseGridGsdStatistics.get("average") if denseGridGsdStatistics else None,
        "GSD_median_m": denseGridGsdStatistics.get("median") if denseGridGsdStatistics else None,
        "GSD_percentile98_m": denseGridGsdStatistics.get("percentile98") if denseGridGsdStatistics else None,
        "GSD_maximum_m": denseGridGsdStatistics.get("maximum") if denseGridGsdStatistics else None,
        "GSD_coverage_percent": (100.0*denseGridGsdStatistics["validSampleCount"]/denseGridGsdStatistics["totalSampleCount"]
                             if denseGridGsdStatistics and denseGridGsdStatistics.get("totalSampleCount",0)>0
                             else None),
    }


# Decimals of each property: GSD values in metres keep eight
# (a 10 micrometre GSD keeps four significant digits), perimeters four
# in the Console and six in the shape attributes (0.1 mm and 1
# micrometre), areas in square metres six and eight.  A length or an
# area gets at least four significant digits (FormatConsoleValue and
# FormatAttributeValue), the coverage keeps its decimals.
PROPERTY_CONSOLE_DECIMALS = {
    "AreaBestFitPlane_m2": 6, "AreaRasterDEM_m2": 6, "Perimeter3D_m": 4,
    "GSD_averageFromArea_m": 8, "GSD_minimum_m": 8, "GSD_percentile02_m": 8,
    "GSD_average_m": 8, "GSD_median_m": 8, "GSD_percentile98_m": 8,
    "GSD_maximum_m": 8, "GSD_coverage_percent": 1}
PROPERTY_ATTRIBUTE_DECIMALS = {
    "AreaBestFitPlane_m2": 8, "AreaRasterDEM_m2": 8, "Perimeter3D_m": 6,
    "GSD_averageFromArea_m": 8, "GSD_minimum_m": 8, "GSD_percentile02_m": 8,
    "GSD_average_m": 8, "GSD_median_m": 8, "GSD_percentile98_m": 8,
    "GSD_maximum_m": 8, "GSD_coverage_percent": 6}

def FootprintRunConsoleLine(imageCount, surfaceMode, boundaryStep, propertyAttributeNames, gsdPixelStep,
                            asPolylines):
    """The Console line that opens a run of 3D footprints with its
    settings: the images, the surface mode ("plane", "mesh", or "dem"), the
    border step, the pixel step of the dense grid GSD when a property
    needs it (*gsdPixelStep*, None otherwise), whether the footprints
    become polylines, and the attribute names of the properties chosen.
    The attribute names carry no unit, which the attribute 00_Units gives
    (the labels of the dialog name metres, also for a chunk without a
    physical scale, and their commas made the list hard to read)."""
    settings = ["{} mode".format(SURFACE_MODE_LABELS.get(surfaceMode, surfaceMode)),
                "border sampled every {} px".format(boundaryStep)]
    if gsdPixelStep is not None:
        settings.append("GSD pixel step {} px".format(gsdPixelStep))
    settings.append("polylines" if asPolylines else "polygons")
    return "[Image Footprint Generator] Generating {}: {}. Properties: {}.".format(
        FormatCountWithNoun(imageCount, "footprint", "footprints"), ", ".join(settings),
        JoinWithAnd(list(propertyAttributeNames)) if propertyAttributeNames else "none")


# Shape attribute written on footprints whose border was not hit in full.
PARTIAL_FOOTPRINT_ATTRIBUTE_NAME = "00_PartialFootprint"

# Shape attribute written on the footprints of images with rolling-shutter
# compensation, which says how their rays are cast: they follow the
# camera motion during the readout (ImageRays), or, when one of them
# could not be found, it says why every ray of the image starts at its
# perspective centre (RollingShutterRayText).
ROLLING_SHUTTER_ATTRIBUTE_NAME = "00_RollingShutter"

# Shape attribute written on the footprints of images some of whose rays
# of Camera.unproject MetaFOCUS refined by Newton's method, or that have
# pixel positions without a ray (ExactRayText).
RAYS_ATTRIBUTE_NAME = "00_Rays"

# Words for the surface modes and 2D projection methods in labels.
SURFACE_MODE_LABELS = {"plane": "plane", "mesh": "triangular mesh",
                       "dem": "raster DEM"}
PROJECTION_METHOD_LABELS = {"horizontal": "local horizontal",
                            "bestfit": "best-fit plane",
                            "custom": "custom normal"}

# Shortest custom normal accepted for a 2D projection.
MINIMUM_CUSTOM_NORMAL_LENGTH = 1e-9


def SelectFootprintImages(chunk, onlySelected):
    """Oriented master images of the chunk, in chunk order, for which
    footprints are generated: all of them, or only the selected ones."""
    return [camera for camera in chunk.cameras
            if IsMasterImage(camera) and camera.transform is not None
            and (camera.selected or not onlySelected)]


def FootprintSelectionStatusText(chunk):
    """The line below Only process selected images: how many master
    images Metashape reports as selected in *chunk* (SelectedMasterImages)
    and how many of them are oriented, or that there is no active chunk."""
    if chunk is None:
        return "No active chunk."
    selectedImages = SelectedMasterImages(chunk)
    return SelectedImagesStatusText(len(selectedImages),
                                    sum(1 for camera in selectedImages if camera.transform is not None))


def IsPartialFootprint(footprintData):
    """True when some border rays of the footprint missed the
    surface (in triangular mesh mode also those that met it beyond the
    grazing limit), or some border pixel positions had no ray (counted
    among the misses), so the polygon covers only part of the image."""
    return (footprintData.get("missCount") or 0) > 0


def RaysAttributeText(rayText):
    """The value of RAYS_ATTRIBUTE_NAME: the text of ExactRayText without
    its "Rays: " and with a capital at its start."""
    text = rayText[len("Rays: "):] if rayText.startswith("Rays: ") else rayText
    return text[:1].upper() + text[1:]


def DescribePartialFootprint(footprintData):
    """Attribute text of a partial footprint, for example "61 of 100
    border rays hit the surface", or, when some border pixel positions
    have no ray (withoutRayCount), "918 of 924 border pixel positions
    give a ray that hits the surface, and 6 give no ray".  In triangular
    mesh mode (surfaceMode "mesh") the rays hit the surface within the
    grazing limit, and the sentence ends with the vertices moved inwards
    (movedVertexCount), if any: "180 of 278 border rays hit the
    surface within the grazing limit, and 98 vertices are moved inwards",
    or "918 of 924 border pixel positions give a ray that hits the
    surface within the grazing limit, 6 give no ray, and 6 vertices are
    moved inwards"."""
    hitCount = footprintData.get("hitCount", 0)
    boundaryPixelCount = footprintData.get("boundaryPixelCount", 0)
    withoutRayCount = footprintData.get("withoutRayCount") or 0
    surfaceText = ("the surface within the grazing limit" if footprintData.get("surfaceMode") == "mesh"
                   else "the surface")
    movedVertexCount = footprintData.get("movedVertexCount") or 0
    movedText = "{} {} moved inwards".format(movedVertexCount, "vertex is" if movedVertexCount == 1
                                             else "vertices are")
    if not withoutRayCount:
        text = "{} of {} border rays hit {}".format(hitCount, boundaryPixelCount, surfaceText)
        return text + ", and " + movedText if movedVertexCount else text
    text = "{} of {} border pixel positions {} a ray that hits {}".format(
        hitCount, boundaryPixelCount, "gives" if hitCount == 1 else "give", surfaceText)
    withoutRayText = "{} {} no ray".format(withoutRayCount, "gives" if withoutRayCount == 1 else "give")
    if movedVertexCount:
        return "{}, {}, and {}".format(text, withoutRayText, movedText)
    return "{}, and {}".format(text, withoutRayText)


def FormatAttributeValue(propertyKey, value):
    """Shape attribute text of one property value: a length or an area
    with its decimals of PROPERTY_ATTRIBUTE_DECIMALS or at least four
    significant digits in positional notation (FormatLengthForCsv: eight
    decimals would write a GSD of 0.7512 um as 0.00000075), the coverage
    with its decimals."""
    if isinstance(value, float):
        if propertyKey == COVERAGE_PROPERTY_KEY:
            return "{:.{}f}".format(value, PROPERTY_ATTRIBUTE_DECIMALS[propertyKey])
        return FormatLengthForCsv(value, PROPERTY_ATTRIBUTE_DECIMALS[propertyKey])
    return str(value)


def FormatConsoleValue(propertyKey, value):
    """Console text of one property value ("-" when it is missing): a
    length or an area with its decimals of PROPERTY_CONSOLE_DECIMALS or
    four significant digits where these give fewer
    (FormatLengthWithDecimals), the coverage with its decimals."""
    if value is None:
        return "-"
    if propertyKey == COVERAGE_PROPERTY_KEY:
        return "{:.{}f}".format(value, PROPERTY_CONSOLE_DECIMALS[propertyKey])
    return FormatLengthWithDecimals(value, PROPERTY_CONSOLE_DECIMALS[propertyKey])


def FootprintUnitsText(chunkIsScaled):
    """Value of the attribute UNITS_ATTRIBUTE_NAME: the unit of the
    lengths, the areas being in its square, with the text of the Units
    line of the GSD CSV (see LengthUnitsText)."""
    return LengthUnitsText(chunkIsScaled)


def _InChunkUnits(label):
    """*label* with "(m)" and "(m\u00b2)" replaced by chunk units and
    square chunk units."""
    return label.replace("(m\u00b2)", "(square chunk units)").replace("(m)", "(chunk units)")


def ConsoleHeader(propertyKey, chunkIsScaled=True):
    """Console column header of a property: in metres and square metres,
    or, in a chunk without a physical scale, in chunk units and square
    chunk units, so that the Console never labels chunk units as
    metres."""
    header = PROPERTY_CONSOLE_HEADERS[propertyKey]
    return header if chunkIsScaled else _InChunkUnits(header)


def PropertyCheckBoxLabel(propertyKey, chunkIsScaled=True):
    """Label of the check box of a property in the dialog "Properties to
    compute": in metres and square metres for a chunk with a physical
    scale, otherwise in chunk units and square chunk units, so that no
    check box names metres for a chunk without a physical scale."""
    label = next(propertyDefinition[1] for propertyDefinition in ALL_COMPUTABLE_PROPERTIES
                 if propertyDefinition[0] == propertyKey)
    return label if chunkIsScaled else _InChunkUnits(label)


def BuildConsoleSummaryLines(footprintDataList, selectedPropertyKeys, chunkIsScaled=True):
    """Lines of the Console summary table: header, rule, one row per
    footprint with the selected properties, the source of the average
    GSD from area when it was computed, and the partial footprints.
    The headers name chunk units when *chunkIsScaled* is False (see
    ConsoleHeader).  Every column is at least two characters wider than
    its header and its values, so headers never touch and values stay
    aligned."""
    selectedKeys = [propertyKey for propertyKey in PROPERTY_KEY_ORDER
                    if propertyKey in selectedPropertyKeys]
    headerByPropertyKey = {propertyKey: ConsoleHeader(propertyKey, chunkIsScaled)
                           for propertyKey in selectedKeys}
    imageColumnWidth = max([28] + [len(str(footprintData["cameraLabel"])) + 2
                                   for footprintData in footprintDataList])
    valueTextRows = []
    for footprintData in footprintDataList:
        valueByPropertyKey = ComputePropertyValues(footprintData)
        valueTextRows.append({propertyKey: FormatConsoleValue(propertyKey, valueByPropertyKey.get(propertyKey))
                              for propertyKey in selectedKeys})
    columnWidths = {}
    for propertyKey in selectedKeys:
        columnWidths[propertyKey] = max(
            [16, len(headerByPropertyKey[propertyKey]) + 2]
            + [len(valueTexts[propertyKey]) + 2 for valueTexts in valueTextRows])
    headerLine = "Image".ljust(imageColumnWidth) + "".join(
        headerByPropertyKey[propertyKey].rjust(columnWidths[propertyKey])
        for propertyKey in selectedKeys)
    summaryLines = [headerLine, "-" * len(headerLine)]
    for footprintData, valueTexts in zip(footprintDataList, valueTextRows):
        summaryLines.append(str(footprintData["cameraLabel"]).ljust(imageColumnWidth) + "".join(
            valueTexts[propertyKey].rjust(columnWidths[propertyKey])
            for propertyKey in selectedKeys))
    if "GSD_averageFromArea_m" in selectedPropertyKeys:
        areaGsdSources = set(eachFootprintData.get("areaGsdSource") for eachFootprintData in footprintDataList if eachFootprintData.get("areaGsdSource"))
        if areaGsdSources:
            summaryLines.append("\n  Average GSD computed from: {}".format(", ".join(sorted(areaGsdSources))))
    partialFootprints = [footprintData for footprintData in footprintDataList if IsPartialFootprint(footprintData)]
    if partialFootprints:
        # Triangular mesh mode counts a border ray beyond the grazing limit
        # as a miss as well.
        missText = ("missed the surface or met it beyond the grazing limit,"
                    if any(footprintData.get("surfaceMode") == "mesh" for footprintData in partialFootprints)
                    else "missed the surface")
        summaryLines.append(
            "\n  Partial footprints (some border rays {} or some border pixel "
            "positions have no ray, so no average GSD from area is given): {}".format(
                missText, ", ".join(str(footprintData["cameraLabel"]) for footprintData in partialFootprints)))
    summaryLines.extend(RasterDemMissSummaryLines(footprintDataList))
    return summaryLines


def RasterDemMissSummaryLines(footprintDataList):
    """Lines of the Console summary that count the missed rays of a run
    in raster DEM mode by reason, over all images: those of the
    border rays (perimeterRasterDemMissCounts of each footprint) and
    those of the rays of the dense grid GSD
    (denseGridRasterDemMissCounts).  No line where nothing missed."""
    summaryLines = []
    for dataKey, raysDescription in (("perimeterRasterDemMissCounts", "the border rays"),
                                     ("denseGridRasterDemMissCounts", "the dense grid GSD rays")):
        totalMissCounts = {}
        for footprintData in footprintDataList:
            AddRasterDemMissCounts(totalMissCounts, footprintData.get(dataKey) or {})
        missDescription = DescribeRasterDemMisses(totalMissCounts)
        if missDescription:
            summaryLines.append("\n  Raster DEM misses of {}: {}.".format(raysDescription, missDescription))
    return summaryLines


def DescribeRasterDemAvailability(resolution):
    """Availability text of the raster DEM for the dialog header, with
    the cell size in significant digits."""
    return "Yes (cell size {:.4g} m)".format(resolution) if resolution else "Yes"


def FootprintGroupLabel(surfaceMode):
    """Label of the shape group that receives the footprints."""
    return "Footprints ({})".format(SURFACE_MODE_LABELS[surfaceMode])


def _HitSurfaceText(surfaceMode):
    """The surface of *surfaceMode* in the hit line, with the grazing
    limit that triangular mesh mode applies to the border rays."""
    surfaceText = SURFACE_MODE_LABELS[surfaceMode]
    return surfaceText + " within the grazing limit" if surfaceMode == "mesh" else surfaceText


def FootprintHitLine(cameraLabel, surfaceMode, hitCount, boundaryPixelCount, withoutRayCount=0,
                     movedVertexCount=0):
    """Console line reporting how many border rays hit the surface (in
    triangular mesh mode within the grazing limit), and, when some border
    pixel positions have no ray, how many of the border pixel positions
    give a ray that hits it and how many give no ray.  In triangular mesh
    mode it ends with the vertices moved inwards (ComputeFootprintOnMesh),
    if any."""
    if withoutRayCount:
        line = "{:<28s}  border pixel positions whose ray hits {}: {}/{} ({} without a ray)".format(
            cameraLabel, _HitSurfaceText(surfaceMode), hitCount, boundaryPixelCount, withoutRayCount)
    else:
        line = "{:<28s}  border rays hit {}: {}/{}".format(
            cameraLabel, _HitSurfaceText(surfaceMode), hitCount, boundaryPixelCount)
    if movedVertexCount:
        line += ", {} moved inwards".format(FormatCountWithNoun(movedVertexCount, "vertex", "vertices"))
    return line


def FootprintSkipLine(cameraLabel, skipReason):
    """Console line naming an image that got no footprint, and why."""
    return "{:<28s}  skipped: {}".format(cameraLabel, skipReason)


def FootprintFailureLine(cameraLabel, error):
    """Console line naming an image whose footprint or GSD could not be
    computed because of an unexpected error, which is named with its
    type (see DescribeException)."""
    return "{:<28s}  failed: {}".format(cameraLabel, DescribeException(error))


def SurfacePreparationFailureLine(error):
    """Console line reporting that the surface of a run could not be
    prepared (the bounding volume hierarchy of the triangular mesh or
    the tie-point map of plane mode)."""
    return "[Image Footprint Generator] Preparing the surface failed: {}".format(
        DescribeException(error))


def SurfacePreparationFailureMessage(error):
    """Text of the message that ends a run whose surface could not be
    prepared."""
    return "The surface could not be prepared:\n{}".format(DescribeException(error))


def CancelledRunSummary(processedImageCount, totalImageCount):
    """Sentence reporting a run that was cancelled part way."""
    return "The run was cancelled after {} of {}.".format(
        processedImageCount, FormatCountWithNoun(totalImageCount, "image", "images"))


def CompletionMessage(shapesCreatedCount, groupLabel, processedImageCount,
                      totalImageCount, failedImageCount=0, planeWarningCount=0):
    """Text of the message shown when footprint generation ends,
    including whether the run was cancelled part way, how many images
    failed (see FootprintFailureLine) and were skipped, and, in plane
    mode, how many images have tie points far off one plane
    (PlaneSpreadSummaryText)."""
    messageLines = ["Created {} in group '{}'.".format(
        FormatCountWithNoun(shapesCreatedCount, "footprint shape", "footprint shapes"),
        groupLabel)]
    if processedImageCount < totalImageCount:
        messageLines.append(CancelledRunSummary(processedImageCount, totalImageCount))
    if failedImageCount:
        messageLines.append("{} failed and {} skipped.".format(
            FormatCountWithNoun(failedImageCount, "image", "images"),
            "was" if failedImageCount == 1 else "were"))
    if planeWarningCount:
        messageLines.append(PlaneSpreadSummaryText(planeWarningCount, processedImageCount))
    messageLines.append("Check the Console for details.")
    return "\n".join(messageLines)


def CreateFootprintProgressDialog(parentWidget, imageCount):
    """Window-modal progress dialog of a footprint run over
    *imageCount* images.  Unlike Qt's default, it neither resets nor
    closes itself when the image loop reaches its maximum, so that it
    keeps the dialog's buttons blocked while the shapes and their
    metrics are written (the processEvents calls of that phase would
    otherwise accept a second click on Generate or Project).  The run
    closes it."""
    progressDialog = QtWidgets.QProgressDialog(
        "Generating footprints...", "Cancel", 0, imageCount, parentWidget)
    progressDialog.setWindowTitle("Image Footprint Generator")
    progressDialog.setWindowModality(QtCore.Qt.WindowModal)
    progressDialog.setMinimumDuration(0)
    progressDialog.setAutoReset(False)
    progressDialog.setAutoClose(False)
    return progressDialog


def AskWhetherToCreateCompletedFootprints(parentWidget, progressDialog,
                                          cancelledSummary, completedImageCount):
    """Ask whether the footprints of the *completedImageCount* images
    of a cancelled run are to be created.  The progress dialog is hidden
    while the question is shown and, for a Yes, shown again, so that it
    blocks the dialog while the shapes are written.  Returns True for a
    Yes."""
    progressDialog.hide()
    reply = QtWidgets.QMessageBox.question(
        parentWidget, "Image Footprint Generator",
        "{}\n\nCreate the footprints of the {} completed so far?".format(
            cancelledSummary,
            FormatCountWithNoun(completedImageCount, "image", "images")),
        QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
        QtWidgets.QMessageBox.No)
    if reply != QtWidgets.QMessageBox.Yes:
        return False
    progressDialog.show()
    return True


def TwoDimensionalGroupLabel(sourceGroupLabel, projectionMethod):
    """Label of the shape group that receives the 2D projections (it
    keeps "(2D" so that GetThreeDimensionalShapeGroups leaves it out)."""
    return "{} (2D {})".format(sourceGroupLabel or "shapes",
                               PROJECTION_METHOD_LABELS[projectionMethod])


def TwoDimensionalProjectionSummary(createdShapeCount, newGroupLabel):
    """Console line and message text reporting a 2D projection."""
    return ("Created {} in group '{}'.".format(
                FormatCountWithNoun(createdShapeCount, "2D shape", "2D shapes"), newGroupLabel),
            "Projected {} to 2D in group '{}'.".format(
                FormatCountWithNoun(createdShapeCount, "shape", "shapes"), newGroupLabel))


# ===================================================================
# Shape creation and area
# ===================================================================

def IsClosedRing(vertices):
    """True when the last vertex repeats the first one."""
    if len(vertices) < 2:
        return False
    firstVertex, lastVertex = vertices[0], vertices[-1]
    return all(abs(firstVertex[axis] - lastVertex[axis]) < 1e-12
               for axis in range(min(len(firstVertex), len(lastVertex))))


def RemoveClosingDuplicate(vertices):
    """The vertices without a last vertex that repeats the first one."""
    return list(vertices[:-1]) if IsClosedRing(vertices) else list(vertices)


def CloseRing(vertices):
    """The vertices with the first one repeated at the end, unless the
    ring is closed already."""
    vertexList = list(vertices)
    if vertexList and not IsClosedRing(vertexList):
        vertexList.append(vertexList[0])
    return vertexList


def ProjectChunkPointToShapeLayer(chunkPointTuple, chunk):
    """Coordinates of a chunk-coordinate point in the CRS of the
    chunk's shape layer.

    In a georeferenced chunk the world coordinates are geocentric and
    the shape layer's own CRS projects them (as Agisoft's footprint
    script does), so a shape layer whose CRS differs from the chunk
    CRS still receives correct vertices.  In any other chunk the point
    is transformed with the chunk's own CRS (TransformChunkPointToCrs),
    which gives local coordinates, or internal chunk coordinates when
    the chunk has no transform, whatever CRS the shape layer carries
    (a new layer receives the chunk CRS, which may be geographic even
    in a non-georeferenced chunk).  ShapeVerticesToLocalFrame reads
    the vertices back by the same rule."""
    shapes = getattr(chunk, "shapes", None)
    shapeCrs = getattr(shapes, "crs", None) if shapes is not None else None
    if shapeCrs is not None and IsGeoreferencedChunk(chunk):
        worldPoint = chunk.transform.matrix.mulp(Metashape.Vector(list(chunkPointTuple)))
        return shapeCrs.project(worldPoint)
    return TransformChunkPointToCrs(chunkPointTuple, chunk)


def CreateFootprintShapes(chunk, footprintDataList, groupLabel):
    """Create one polygon shape per footprint in a new shape group labelled
    *groupLabel*, with the vertices in the CRS of the shape layer."""
    if chunk.shapes is None:
        chunk.shapes = Metashape.Shapes()
        chunk.shapes.crs = chunk.crs
    if chunk.shapes.crs is None and chunk.crs:
        chunk.shapes.crs = chunk.crs
    shapeGroup = chunk.shapes.addGroup()
    shapeGroup.label = groupLabel
    shapeGroup.color = (255, 100, 0, 128)
    shapesCreatedCount = 0
    for footprintData in footprintDataList:
        crsCoordinates = [ProjectChunkPointToShapeLayer(hitPoint, chunk)
                          for hitPoint in footprintData["hitPointList"]
                          if hitPoint is not None]
        # The sampled border ends on its first pixel again when the
        # number of pixel rows is one more than a multiple of the boundary step
        # (4001 px at a step of 100 px, for example), which would
        # repeat the first vertex: drop it.
        crsCoordinates = RemoveClosingDuplicate(crsCoordinates)
        if len(crsCoordinates) < 3: continue
        newShape = chunk.shapes.addShape()
        newShape.label = footprintData["cameraLabel"]
        newShape.group = shapeGroup
        newShape.geometry = Metashape.Geometry.Polygon(crsCoordinates)
        footprintData["shapeKey"] = newShape.key
        shapesCreatedCount += 1
    return shapesCreatedCount, shapeGroup


def AverageGsdFromAreaDenominator(pixelColumnCount, pixelRowCount):
    """Number by which the footprint area is divided for the average GSD
    from area: (number of pixel columns - 1) x (number of pixel rows - 1).
    The footprint runs through the outermost pixel positions (see
    SampleBoundaryPixels), so along a row it spans one sampling interval
    fewer than the image has pixel columns, and along a column one fewer
    than it has pixel rows: on a flat surface seen square on, with all
    lens distortion coefficients set to zero, its area is this number
    times the square of the GSD.  Dividing by the pixel count instead
    would understate the GSD by the factor sqrt((C - 1)(R - 1) / (C R)),
    0.99 for 100 by 100 pixels.  0 for an image with a single pixel column or
    row."""
    return max(0, pixelColumnCount - 1) * max(0, pixelRowCount - 1)


def AreaOnBestFitPlane(points):
    """The area of the polygon through *points* (3-tuples in order, a
    ring without a closing repeat of the first point) projected onto the
    least-squares plane through them (FitPlaneToPoints): the shoelace
    formula in two orthogonal axes of that plane, in the square of the
    unit of the points.  A polygon that crosses itself counts its parts
    with the sign of their sense of rotation, as the shoelace formula
    does.  None for fewer than three points or when no plane can be
    fitted to them."""
    if len(points) < 3:
        return None
    centroid, normal = FitPlaneToPoints(points)
    if normal is None:
        return None
    referenceAxis = (1.0, 0.0, 0.0) if abs(normal[0]) < 0.9 else (0.0, 1.0, 0.0)
    firstAxis = VectorNormalize(VectorCross(normal, referenceAxis))
    secondAxis = VectorCross(normal, firstAxis)
    planeCoordinates = [(VectorDot(VectorSubtract(point, centroid), firstAxis),
                         VectorDot(VectorSubtract(point, centroid), secondAxis)) for point in points]
    twiceSignedArea = 0.0
    for index, (firstX, firstY) in enumerate(planeCoordinates):
        nextX, nextY = planeCoordinates[(index + 1) % len(planeCoordinates)]
        twiceSignedArea += firstX * nextY - nextX * firstY
    return 0.5 * abs(twiceSignedArea)


def FootprintAreaOnBestFitPlane(hitPointList, chunkScale=1.0):
    """The best-fit plane area of a footprint (01_AreaBestFitPlane): the
    area on the least-squares plane through the vertices of its shape
    (AreaOnBestFitPlane), that is, its hit points without the border pixel
    positions that have none and without a closing repeat of the first
    vertex, as CreateFootprintShapes writes them.  The hit points are in
    internal chunk coordinates, so the area is multiplied by the square
    of *chunkScale* (square metres for a physical chunk scale, square
    chunk units otherwise).  None when the shape has fewer than three
    vertices or no plane can be fitted."""
    vertices = RemoveClosingDuplicate([hitPoint for hitPoint in (hitPointList or ()) if hitPoint is not None])
    area = AreaOnBestFitPlane(vertices)
    return None if area is None else area * chunkScale * chunkScale


def ComputeAndStoreMetrics(chunk, footprintDataList, shapeGroup,
                           computeAreaFitted, computeAreaDem,
                           computePerimeter, chunkScale=1.0):
    """Compute the areas, the 3D perimeter, and the average GSD from area
    of the footprint shapes.  MetaFOCUS computes the best-fit plane area
    itself (FootprintAreaOnBestFitPlane, with *chunkScale*), the raster
    DEM area and the 3D perimeter come from Metashape (Shape.area and
    Shape.perimeter3D).  The average GSD from area divides the raster DEM
    area when there is one, otherwise the best-fit plane area, by
    AverageGsdFromAreaDenominator, and a partial footprint gets none."""
    footprintDataByShapeKey = {}
    for footprintData in footprintDataList:
        shapeKey = footprintData.get("shapeKey")
        if shapeKey is not None: footprintDataByShapeKey[shapeKey] = footprintData

    for shape in chunk.shapes.items():
        if shape.group is None: continue
        if shape.group.key != shapeGroup.key: continue
        footprintData = footprintDataByShapeKey.get(shape.key)
        if footprintData is None: continue

        if computeAreaFitted:
            areaFitted = FootprintAreaOnBestFitPlane(footprintData.get("hitPointList"), chunkScale)
            if areaFitted is not None and areaFitted > 0:
                footprintData["areaFitted"] = areaFitted

        if computeAreaDem:
            try:
                areaDem = shape.area()
                if areaDem is not None and areaDem > 0:
                    footprintData["areaDem"] = areaDem
            except Exception as suppressedError:
                ReportSuppressedError("footprintGenerator.ComputeAndStoreMetrics.area", suppressedError)

        if computePerimeter:
            try:
                perimeter3d = shape.perimeter3D()
                if perimeter3d is not None and perimeter3d > 0:
                    footprintData["perimeter3d"] = perimeter3d
            except Exception as suppressedError:
                ReportSuppressedError("footprintGenerator.ComputeAndStoreMetrics.perimeter3D", suppressedError)

        # A partial footprint covers only part of the image, so its area
        # divided by the sampling intervals of the whole image would
        # understate the GSD: no average GSD from area for it.
        if IsPartialFootprint(footprintData):
            continue

        # Average GSD from area: prefer the raster DEM area, fall back
        # to the best-fit plane area.
        areaForGsd = footprintData.get("areaDem")
        areaSource = "raster DEM"
        if areaForGsd is None:
            areaForGsd = footprintData.get("areaFitted")
            areaSource = "best-fit plane"
        pixelColumnCount = footprintData.get("pixelColumnCount")
        pixelRowCount = footprintData.get("pixelRowCount")
        # The footprint runs through the outermost pixel positions, so
        # the area is divided by the sampling intervals it spans, not by
        # the pixel count of the image.
        areaDenominator = (AverageGsdFromAreaDenominator(pixelColumnCount, pixelRowCount)
                           if pixelColumnCount and pixelRowCount else 0)
        if areaForGsd and areaForGsd > 0 and areaDenominator > 0:
            footprintData["averageGsdFromArea"] = math.sqrt(areaForGsd / areaDenominator)
            footprintData["areaGsdSource"] = areaSource


def StoreSelectedPropertiesToShapes(chunk, footprintDataList, shapeGroup,
                                    selectedPropertyKeys, chunkIsScaled=True):
    """Write selected properties as shape attributes in GUI order, give
    the unit of the lengths and areas in UNITS_ATTRIBUTE_NAME (see
    FootprintUnitsText) on a footprint that stores one, and flag a
    partial footprint with PARTIAL_FOOTPRINT_ATTRIBUTE_NAME.  The unit
    stays with the shapes, also when they are exported later without
    the Console of the run."""
    footprintDataByShapeKey = {}
    for footprintData in footprintDataList:
        shapeKey = footprintData.get("shapeKey")
        if shapeKey is not None: footprintDataByShapeKey[shapeKey] = footprintData

    for shape in chunk.shapes.items():
        if shape.group is None: continue
        if shape.group.key != shapeGroup.key: continue
        footprintData = footprintDataByShapeKey.get(shape.key)
        if footprintData is None: continue

        valueByPropertyKey = ComputePropertyValues(footprintData)
        storesLengthOrArea = False
        # Write in GUI order with numbered prefixes for Metashape sorting.
        for propertyKey in PROPERTY_KEY_ORDER:
            if propertyKey not in selectedPropertyKeys: continue
            value = valueByPropertyKey.get(propertyKey)
            if value is not None:
                attributeName = PROPERTY_ATTRIBUTE_NAMES[propertyKey]
                shape.attributes[attributeName] = FormatAttributeValue(propertyKey, value)
                storesLengthOrArea = storesLengthOrArea or propertyKey not in UNITLESS_PROPERTY_KEYS
        if storesLengthOrArea:
            shape.attributes[UNITS_ATTRIBUTE_NAME] = FootprintUnitsText(chunkIsScaled)
        if IsPartialFootprint(footprintData):
            shape.attributes[PARTIAL_FOOTPRINT_ATTRIBUTE_NAME] = DescribePartialFootprint(footprintData)
        if footprintData.get("rollingShutterModel"):
            shape.attributes[ROLLING_SHUTTER_ATTRIBUTE_NAME] = RollingShutterRayText(
                footprintData["rollingShutterModel"], footprintData.get("rollingShutterRayFailure"))
        if footprintData.get("rayText"):
            shape.attributes[RAYS_ATTRIBUTE_NAME] = RaysAttributeText(footprintData["rayText"])


def ConvertShapesToPolylines(chunk, shapeGroup):
    """Convert the polygon shapes of *shapeGroup* into closed polylines and
    return how many were converted."""
    convertedCount = 0
    for shape in chunk.shapes.items():
        if shape.group is None: continue
        if shape.group.key != shapeGroup.key: continue
        if shape.geometry is None: continue
        if shape.geometry.type != Metashape.Geometry.Type.PolygonType: continue
        # Extract outer ring from Polygon (coordinates is a list of rings).
        coordinates = _ExtractCoordinateVectors(shape)
        if len(RemoveClosingDuplicate(coordinates)) < 3: continue
        # Close the loop, unless the ring repeats its first vertex already.
        shape.geometry = Metashape.Geometry.LineString(CloseRing(coordinates))
        convertedCount += 1
    return convertedCount


# ===================================================================
# 2D projection
# ===================================================================

def GetThreeDimensionalShapeGroups(chunk):
    if chunk.shapes is None: return []
    groupList = []
    for group in chunk.shapes.groups:
        label = group.label or "(unnamed)"
        if "(2D" in label: continue
        groupList.append((label, group))
    return groupList

def _ExtractCoordinateVectors(shape):
    """Extract coordinate Vectors from a shape geometry.

    Metashape Polygon.coordinates returns a list of rings:
      [[Vector, Vector, ...]]   : outer ring is coordinates[0]
    LineString.coordinates returns a flat list:
      [Vector, Vector, ...]
    """
    geometry = shape.geometry
    if geometry is None:
        return []

    try:
        coordinates = geometry.coordinates
        if not coordinates or len(coordinates) == 0:
            return []

        firstElement = coordinates[0]

        # LineString: the coordinates are a flat list of vectors.
        if hasattr(firstElement, 'x'):
            return list(coordinates)

        # Polygon: the coordinates are a list of rings, each ring a
        # list of vectors.
        if isinstance(firstElement, (list, tuple)):
            outerRing = firstElement
            if outerRing and len(outerRing) >= 3 and hasattr(outerRing[0], 'x'):
                return list(outerRing)

    except Exception as extractionError:
        print("    The coordinates of a shape could not be read: {}".format(DescribeException(extractionError)))

    return []

def ValidateProjectionRequest(projectionMethod, customNormalVector):
    """Raise ValueError for an unknown projection method or for a
    custom normal of zero length."""
    if projectionMethod not in PROJECTION_METHOD_LABELS:
        raise ValueError("Unknown projection method: {}".format(projectionMethod))
    if (projectionMethod == "custom"
            and not VectorLength(tuple(customNormalVector)) > MINIMUM_CUSTOM_NORMAL_LENGTH):
        raise ValueError("The custom normal has zero length. Set at least one of "
                         "its East, North, and Up components to a non-zero value.")


def ShapeVerticesToLocalFrame(shapeCrs, vertexTuples, chunkIsGeoreferenced):
    """Shape vertices in local east-north-up coordinates in metres.

    Returns (localVertices, localFrame).  When the chunk is
    georeferenced and the shape layer has a geographic, projected, or
    geocentric CRS, the vertices are converted through geocentric
    coordinates into the local east-north-up coordinates at their
    centroid, and localFrame is that 4 x 4 matrix (geocentric to
    local).  Otherwise the vertices are local or internal chunk
    coordinates, whatever CRS the shape layer carries (see
    ProjectChunkPointToShapeLayer), so they are in metres or at least
    isotropic already: they are returned unchanged with localFrame
    None."""
    if (not chunkIsGeoreferenced
            or CrsKind(shapeCrs) not in ("geographic", "projected", "geocentric")):
        return [tuple(vertex) for vertex in vertexTuples], None
    geocentricVertices = [VectorFromMetashape(shapeCrs.unproject(Metashape.Vector(list(vertex))))
                          for vertex in vertexTuples]
    vertexCount = float(len(geocentricVertices))
    geocentricCentroid = tuple(sum(vertex[axis] for vertex in geocentricVertices) / vertexCount
                               for axis in range(3))
    localFrame = shapeCrs.localframe(Metashape.Vector(list(geocentricCentroid)))
    localVertices = [VectorFromMetashape(localFrame.mulp(Metashape.Vector(list(vertex))))
                     for vertex in geocentricVertices]
    return localVertices, localFrame


def LocalFrameToShapeVertices(shapeCrs, localFrame, localVertices):
    """Inverse of ShapeVerticesToLocalFrame: the vertices as Metashape
    vectors in the CRS of the shape layer."""
    if localFrame is None:
        return [Metashape.Vector(list(vertex)) for vertex in localVertices]
    inverseFrame = localFrame.inv()
    return [shapeCrs.project(inverseFrame.mulp(Metashape.Vector(list(vertex))))
            for vertex in localVertices]


def ProjectionPlaneForMethod(localVertices, projectionMethod, customNormalVector):
    """Point and unit normal of the plane that *projectionMethod*
    projects onto, in the local east-north-up coordinates of
    ShapeVerticesToLocalFrame: "horizontal" (through the
    centroid, normal up), "bestfit" (least-squares plane, with the
    normal None when the vertices lie on one line), or "custom"
    (through the centroid, with *customNormalVector* read as east,
    north, up)."""
    ValidateProjectionRequest(projectionMethod, customNormalVector)
    vertexCount = float(len(localVertices))
    centroid = tuple(sum(vertex[axis] for vertex in localVertices) / vertexCount
                     for axis in range(3))
    if projectionMethod == "horizontal":
        return centroid, (0.0, 0.0, 1.0)
    if projectionMethod == "bestfit":
        return FitPlaneToPoints(localVertices)
    return centroid, VectorNormalize(tuple(customNormalVector))


def ProjectPointsOntoPlane(points, planePoint, planeNormal):
    """Orthogonal projection of *points* onto the plane through
    *planePoint* with unit normal *planeNormal*."""
    projectedPoints = []
    for point in points:
        signedDistance = VectorDot(VectorSubtract(point, planePoint), planeNormal)
        projectedPoints.append(VectorSubtract(point, VectorScale(planeNormal, signedDistance)))
    return projectedPoints


def ProjectVerticesToMeanHeight(vertexTuples):
    """The vertices at their own horizontal position and at the mean
    height of all of them, as Metashape vectors ("Local horizontal" in
    a geographic, projected, or local CRS)."""
    meanHeight = sum(vertex[2] for vertex in vertexTuples) / float(len(vertexTuples))
    return [Metashape.Vector([vertex[0], vertex[1], meanHeight]) for vertex in vertexTuples]


def ProjectShapesTo2D(chunk, sourceGroup, projectionMethod,
                      customNormalVector, newGroupLabel):
    """Copy the shapes of *sourceGroup* into a new group projected onto a
    plane (horizontal, best fit, or custom normal) and return how many
    were created.

    "Local horizontal" keeps every vertex at its position and gives it
    the mean height of the shape, except in a geocentric layer, whose
    third coordinate is no height: there, and for the best-fit plane
    and the custom normal, the plane is fitted and the vertices are
    projected in local east-north-up coordinates in metres when the
    chunk is georeferenced, so a geographic shape layer is handled in
    metres and the custom normal is read as east, north, up.  In a
    non-georeferenced chunk the vertices are local or internal chunk
    coordinates and are used as they are (see
    ShapeVerticesToLocalFrame).  Raises ValueError for an unknown method
    or a custom normal of zero length, before any group is created."""
    if chunk.shapes is None: return 0
    ValidateProjectionRequest(projectionMethod, customNormalVector)

    # Collect source shapes.
    sourceShapes = []
    for shape in chunk.shapes.items():
        if shape.group is None: continue
        if shape.group.key != sourceGroup.key: continue
        if shape.geometry is None: continue
        sourceShapes.append(shape)

    print("  Project to 2D: found {} in source group '{}' (group key {}).".format(
        FormatCountWithNoun(len(sourceShapes), "shape", "shapes"),
        sourceGroup.label, sourceGroup.key))

    if not sourceShapes: return 0

    shapeCrs = chunk.shapes.crs
    chunkIsGeoreferenced = IsGeoreferencedChunk(chunk)
    projectsToMeanHeight = (projectionMethod == "horizontal"
                            and CrsKind(shapeCrs) != "geocentric")
    targetGroup = chunk.shapes.addGroup()
    targetGroup.label = newGroupLabel
    targetGroup.color = (0, 150, 255, 128)

    shapesCreatedCount = 0
    for shape in sourceShapes:
        coordinates = _ExtractCoordinateVectors(shape)
        pointTuples = RemoveClosingDuplicate(
            [(coordinate.x, coordinate.y, coordinate.z) for coordinate in coordinates])
        if len(pointTuples) < 3:
            print("    Skipped '{}': {} (geometry type {}).".format(
                shape.label, FormatCountWithNoun(len(coordinates), "coordinate", "coordinates"),
                shape.geometry.type))
            continue

        if projectsToMeanHeight:
            projectedCoordinates = ProjectVerticesToMeanHeight(pointTuples)
        else:
            localVertices, localFrame = ShapeVerticesToLocalFrame(
                shapeCrs, pointTuples, chunkIsGeoreferenced)
            planePoint, planeNormal = ProjectionPlaneForMethod(
                localVertices, projectionMethod, customNormalVector)
            if planeNormal is None:
                # Vertices on one line lie in every plane through that
                # line, so each best-fit plane keeps them where they are.
                print("    Kept '{}' unchanged: its vertices lie on one line, "
                      "which every best-fit plane contains.".format(shape.label))
                projectedLocalVertices = localVertices
            else:
                projectedLocalVertices = ProjectPointsOntoPlane(localVertices, planePoint, planeNormal)
            projectedCoordinates = LocalFrameToShapeVertices(
                shapeCrs, localFrame, projectedLocalVertices)

        newShape = chunk.shapes.addShape()
        newShape.label = shape.label + " (2D)"
        newShape.group = targetGroup

        isPolygon = (shape.geometry.type == Metashape.Geometry.Type.PolygonType)
        if isPolygon:
            newShape.geometry = Metashape.Geometry.Polygon(projectedCoordinates)
        else:
            newShape.geometry = Metashape.Geometry.LineString(CloseRing(projectedCoordinates))

        for attributeKey in shape.attributes.keys():
            newShape.attributes[attributeKey] = shape.attributes[attributeKey]
        shapesCreatedCount += 1

    return shapesCreatedCount


# ===================================================================
# Property selector dialog
# ===================================================================

class PropertySelectorDialog(QtWidgets.QDialog):
    """Dialog "Properties to compute".  The check boxes and the note name
    metres for a chunk with a physical scale (*chunkIsScaled*), chunk
    units otherwise (see PropertyCheckBoxLabel)."""

    def __init__(self, selectedKeys, hasDem, currentPixelStep, parent=None, chunkIsScaled=True):
        super().__init__(parent)
        self.setWindowTitle("Properties to Compute")
        self.setWindowFlags(self.windowFlags()
                           & ~QtCore.Qt.WindowContextHelpButtonHint)
        self.setMinimumWidth(440)
        mainLayout = QtWidgets.QVBoxLayout(self)

        explanationLabel = QtWidgets.QLabel(
            "The selected properties are computed for each image, printed "
            "in the Console, and stored as attributes of each "
            "polygon or polyline.")
        explanationLabel.setWordWrap(True)
        explanationLabel.setStyleSheet("font-size: 10pt; color: #555;")
        mainLayout.addWidget(explanationLabel)
        mainLayout.addSpacing(6)

        buttonRow = QtWidgets.QHBoxLayout()
        buttonSelectAll = QtWidgets.QPushButton("Select all")
        buttonSelectNone = QtWidgets.QPushButton("Select none")
        buttonRow.addWidget(buttonSelectAll); buttonRow.addWidget(buttonSelectNone)
        buttonRow.addStretch(); mainLayout.addLayout(buttonRow)

        self.checkBoxList = []
        for propertyKey in PROPERTY_KEY_ORDER:
            checkBox = QtWidgets.QCheckBox(PropertyCheckBoxLabel(propertyKey, chunkIsScaled))
            checkBox.setChecked(propertyKey in selectedKeys)
            checkBox.setProperty("propertyKey", propertyKey)
            if propertyKey == "AreaRasterDEM_m2" and not hasDem:
                checkBox.setEnabled(False); checkBox.setChecked(False)
                checkBox.setToolTip("No raster DEM available.")
            self.checkBoxList.append(checkBox)
            mainLayout.addWidget(checkBox)

        buttonSelectAll.clicked.connect(
            lambda: [eachCheckBox.setChecked(True) for eachCheckBox in self.checkBoxList if eachCheckBox.isEnabled()])
        buttonSelectNone.clicked.connect(
            lambda: [eachCheckBox.setChecked(False) for eachCheckBox in self.checkBoxList])

        mainLayout.addSpacing(8)
        stepRow = QtWidgets.QHBoxLayout()
        stepRow.addWidget(QtWidgets.QLabel("GSD pixel sampling step:"))
        self.gsdPixelStepSpinBox = QtWidgets.QSpinBox()
        self.gsdPixelStepSpinBox.setRange(5, 500)
        self.gsdPixelStepSpinBox.setValue(currentPixelStep)
        self.gsdPixelStepSpinBox.setSuffix(" px")
        self.gsdPixelStepSpinBox.setToolTip(
            "Step in pixels of the dense GSD grid.\nA smaller step gives more samples and takes longer.")
        stepRow.addWidget(self.gsdPixelStepSpinBox)
        stepRow.addStretch(); mainLayout.addLayout(stepRow)

        mainLayout.addSpacing(6)
        noteLabel = QtWidgets.QLabel(
            "Dense grid GSD intersects four auxiliary rays at ±½ px along "
            "the row and the column of every sampled pixel position, through "
            "the midpoints of the edges of the corresponding photosite for an "
            "image that keeps the sampling of the sensor, with "
            "the local tangent plane at the central ray's surface hit "
            "and reports the area-equivalent GSD, the side of the square "
            "with the area that the projected row "
            "and column steps span, with 8 decimals or at least four significant digits, in {}. "
            "Plane mode uses the "
            "fitted plane normal. Triangular mesh mode uses "
            "3-ring averaged vertex normals, interpolated at each hit "
            "point. Raster DEM mode uses the normal from the raster DEM "
            "gradient, evaluated in a local east-north-up CRS in a "
            "georeferenced chunk and otherwise in the world coordinates of "
            "the chunk. "
            "'Average GSD from area' is a separate estimate from the footprint area: "
            "\u221a(area / ((number of pixel columns \u2212 1) \u00d7 (number of pixel rows \u2212 1))), "
            "because the footprint runs through the outermost pixel "
            "positions. The raster DEM area is "
            "preferred when it is available, otherwise the best-fit plane "
            "area is used. A partial footprint (some border rays missed "
            "the surface, or on a triangular mesh met it beyond the "
            "grazing limit, or some border pixel positions have no ray) "
            "gets no average GSD from area and carries the "
            "attribute 00_PartialFootprint. Note: where a footprint "
            "extends beyond the raster DEM, Metashape uses the flat "
            "projected area for those portions, which makes the best-fit "
            "plane area more consistent in such cases.".format(
                "metres" if chunkIsScaled else "chunk units, because the chunk has no physical scale"))
        noteLabel.setWordWrap(True)
        noteLabel.setStyleSheet("font-size: 9pt; color: #666;")
        mainLayout.addWidget(noteLabel)

        mainLayout.addSpacing(8)
        okCancelRow = QtWidgets.QHBoxLayout()
        buttonOk = QtWidgets.QPushButton("OK"); buttonOk.setDefault(True)
        buttonCancel = QtWidgets.QPushButton("Cancel")
        okCancelRow.addStretch(); okCancelRow.addWidget(buttonOk)
        okCancelRow.addWidget(buttonCancel); mainLayout.addLayout(okCancelRow)
        buttonOk.clicked.connect(self.accept)
        buttonCancel.clicked.connect(self.reject)

    def GetSelectedPropertyKeys(self):
        return [checkBox.property("propertyKey") for checkBox in self.checkBoxList if checkBox.isChecked()]

    def GetGsdPixelStep(self):
        return self.gsdPixelStepSpinBox.value()


# ===================================================================
# About dialog
# ===================================================================

_FOOTPRINT_ABOUT_TEXT = """
<h2>Image Footprint Generator</h2>
<p>Version 1.0.0 (2026-11-18)</p>
<p><b>Author:</b> Geert J. Verhoeven (ORCID: <a href="https://orcid.org/0000-0003-4825-9604">0000-0003-4825-9604</a>)<br>
<b>Contact:</b> <a href="mailto:geert.verhoeven@univie.ac.at">geert.verhoeven@univie.ac.at</a><br>
<b>Licence:</b> GNU General Public License, version 3 or any later version (GPL-3.0-or-later),
with an additional permission to convey MetaFOCUS combined with Agisoft Metashape and its
Python module (see LICENSE and ADDITIONAL_PERMISSION.txt in the MetaFOCUS ZIP file)</p>

<hr>

<h3>Overview</h3>
<p>This tool generates image footprints by projecting the border of each image onto a surface, and
computes their area, perimeter, and per-image statistics of the ground sampling distance (GSD). The
results are stored as attributes of the footprint shapes and printed in the Console. In a chunk
without a physical scale (no Metashape scale bars, no reference coordinates, and no imported chunk
transform), the tool asks whether to continue in chunk units instead of metres.</p>
<p>The tool takes the scale of the chunk transform to be metric: Metashape derives it from Metashape
scale bars or from reference coordinates of markers or images, in a geographic, projected, or local
coordinate reference system (CRS). An imported chunk transform is taken to be metric as well. Any
other chunk transform, for example one that Align Coordinate Axes to Region sets in a chunk without
scale, has an arbitrary scale. Region Tools records the transform that it gives such a chunk in the
chunk metadata, and as long as the chunk transform is unchanged the tool reports chunk units, not
metres. A transform with an arbitrary scale that is set otherwise, by a script for example, cannot
be recognised, and the values are then not in metres.</p>

<h3>1. Generate 3D footprints</h3>
<p>Footprints are generated for the oriented images of the active chunk, or only for the selected
ones (<b>Only process selected images</b>). The slave images of a multi-camera rig and the keyframes
of an animation track are left out. In every surface mode the border through the outermost pixel
positions, 0.5 px inside the edges of the image format, is sampled every <b>boundary sampling step</b>
(in pixels). The rays through these border pixel positions are intersected with the surface. A
smaller step gives a smoother outline and takes longer. The lens distortion bends the projected
border. That is why the border is sampled even on a plane.</p>
<p>A line below <b>Only process selected images</b> gives how many images Metashape reports as
selected and how many of them are oriented. A run uses these images, whatever the Photos pane
highlights. The line is refreshed when the window is activated or deactivated and when the
footprints are generated. A run on the selected images names up to 20 of them in the Console and
gives the number of the others. When Metashape
reports no selected oriented image, a message says so and what to do. Selecting another item of the
Workspace pane, such as the triangular mesh, the tie points, or a folder, clears the selection of
images that Metashape reports, although the Photos pane may still highlight them. Selecting these images again starts with a click on an image that is not highlighted, because
a click on an image that the Photos pane still highlights does not select it in Metashape.</p>
<p><b>Surface mode</b> determines the surface that the rays are
intersected with:</p>
<ul>
<li><b>Plane (tie points):</b> a least-squares plane through the valid 3D tie points of each image.
This mode is fast and works immediately after orientation. Plane mode warns about an image whose tie
points lie far from one plane, as when an image sees a wall and the ground. It warns when their root
mean square distance from the plane exceeds 5&nbsp;% of their median distance from the perspective
centre. The Console names each such image, and the final message counts them.</li>
<li><b>Triangular mesh:</b> the triangular mesh of the chunk, searched
through a bounding volume hierarchy (BVH).  The footprint follows the
surface topography.  Triangular mesh mode is preselected when the chunk has a triangular
mesh.  A mesh that Metashape builds in the chunk has the identity matrix
as its transform and no CRS of its own, so MetaFOCUS reads its vertices
as internal coordinates of the chunk.  A mesh can also have a transform
and a CRS of its own.  MetaFOCUS then
converts its vertices to the internal coordinates of the chunk through
its CRS and the chunk CRS, and the Console says so.  When a mesh has no
CRS of its own, MetaFOCUS applies its transform to obtain the internal
coordinates of the chunk, and the Console says so when that transform
is not the identity matrix.</li>
<li><b>Raster DEM:</b> the raster digital elevation model (DEM) of the chunk. Each ray is followed
in steps of at most one raster DEM cell, measured horizontally, and after each step MetaFOCUS checks
whether the ray has passed below the raster DEM surface. Within the first step that does, the
crossing is located by repeated linear interpolation until the heights of ray and raster DEM agree
to within one hundredth of the cell size. A crossing over which the ray stays below the surface for
at least one cell horizontally is therefore never passed, but a crest that the ray only clips can
be. A hit beyond cells without data is the first crossing found on the raster DEM data, because a
gap is no evidence of a free view. This mode is fast (no BVH needed), with the smooth surface
normals of the raster DEM grid. It requires a raster DEM.</li>
</ul>
<p>The tool skips an image without calibration or perspective centre and, in plane mode, an image
with fewer than three valid tie points that are not all on one line. The Console names each skipped
image and the reason. Some border rays may miss the surface (sky, a hole in the triangular mesh, the
edge of the raster DEM, or a plane seen beyond its horizon). When they do, or when some border pixel
positions have no ray (see &lsquo;Rolling shutter&rsquo;), the footprint is a <b>partial footprint</b>.
It carries the attribute <code>00_PartialFootprint</code> and gets no average GSD from area. In
plane and raster DEM mode the remaining hits form the partial footprint. It joins them in border
order, so it can bridge a gap and is not a map of the visible surface. The attribute reads, for
example, &ldquo;61 of 100 border rays hit the surface&rdquo;. With border pixel positions without a
ray, it reads, for instance, &ldquo;918 of 924 border pixel positions give a ray that hits the
surface, and 6 give no ray&rdquo;.</p>
<p>In triangular mesh mode a border ray that meets the mesh beyond the
grazing limit (incidence above about 84&deg;) also counts as a miss, as
in the dense grid GSD.  A border pixel position without a hit, or
without a ray, then takes the hit of another pixel position as its
vertex.  That pixel position lies on the line from the border pixel
position to a reference pixel position, and its ray meets the mesh
within the grazing limit.  The reference pixel position is the image
centre.  When the ray of the centre does not meet the mesh within the
limit, it is the pixel position nearest the centre, among 9 by 9 spread
evenly over the image, whose ray does.  When no ray of these pixel
positions does, the border pixel positions without a hit get no vertex.
The line is followed from the border in at most 16 equal steps.  Within
the first step that ends at such a hit, a bisection finds the edge
between pixel positions with and without such a hit to 1&nbsp;px.  A
part of the mesh narrower than a step can therefore be passed, and where
the line crosses that edge more than once within the step, the
bisection finds one of the crossings.
The partial footprint thus follows the outer edge of the part of the
image whose rays meet the mesh within the grazing limit.  It can still
bridge a gap between two parts of the mesh, so it is not a map of the
visible surface either.  The attribute then reads, for example,
&ldquo;180 of 278 border rays hit the surface within the grazing
limit, and 98 vertices are moved inwards&rdquo;.</p>
<p>In raster DEM mode the Console counts the missed border rays and the
missed rays of the dense grid GSD by reason.  It counts those with the
perspective centre below the raster DEM surface and those with a
crossing where the raster DEM has no data.  It also counts those
without a crossing within the raster DEM extent and those without a
crossing within the step limit of the search.  The Console
also notes an image whose perspective centre lies below the raster DEM
surface, so that every ray of the image is a miss.
The footprint vertices are written in the CRS of
the shape layer, which in a georeferenced chunk may differ from the
chunk CRS.</p>
<p><b>Cancel</b> in the progress window stops the run after the current image, and the tool then
asks whether the footprints of the images completed so far are to be created. An image whose
footprint or GSD cannot be computed because of an unexpected error is named in the Console with the
error and skipped. The other images are processed, and the final message counts the failed images.
When the surface cannot be prepared (for example a triangular mesh too large for the available
memory), the run ends with a message.</p>
<p><b>Shape type:</b> polygon (filled) or polyline (outline only).
The metrics are always computed on the polygon first.  If polyline is
chosen, the geometry is converted afterwards and keeps all computed
attributes.</p>

<h3>Rolling shutter</h3>
<p>For an image without rolling-shutter compensation, the rays start at the perspective centre of
the image (Metashape&rsquo;s Camera.center) and pass through the full calibrated camera model, lens
distortion included, as for a central projection. For such an image, MetaFOCUS takes the ray of
Camera.unproject when its projection (Camera.project) lies within 10<sup>&minus;6</sup> px of the
pixel position, and otherwise refines it by Newton&rsquo;s method. Like the Interior Orientation
Viewer, MetaFOCUS refines the ray because Metashape&rsquo;s unprojection can stop before it has
converged (up to 0.041 px for a DJI M4E calibration in Metashape 2.3.2). A pixel position whose ray
cannot be brought within 0.001 px has no ray. The Console and the attribute 00_Rays of the footprint
of an image say for how many pixel positions the ray of Metashape&rsquo;s unprojection projected
back more than 0.001 px from the pixel position, so that MetaFOCUS refined it. They also say how
many pixel positions have no ray. These counts cover the pixel positions of the border and of the
dense grid GSD, but not those that the search inwards on a triangular mesh adds. A border pixel
position without a ray makes the footprint partial.</p>
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
readout can be missed. The footprint and the GSD of an image use the same kind of rays. The Console
names the images with rolling-shutter compensation, and the attributes 00_RollingShutter of their
footprints say whether their rays follow the camera motion.</p>

<h3>Properties to compute</h3>
<p>Click &lsquo;Properties to compute&rsquo; to choose which metrics are computed for each image.
The selected properties are printed in the Console and stored as shape attributes. GSD values are
given in metres with eight decimals, perimeters in metres with four decimals in the Console and six
in the attributes, and areas in square metres with six and eight decimals. A length or an area with
fewer than four significant digits in these decimals gets four: with more decimals in the
attributes, and in exponent notation below 10<sup>&minus;4</sup> in the Console. A GSD of 0.7512&nbsp;&micro;m
is thus stored as 0.0000007512 and printed as 7.512e-07, not as 0.00000075. In a chunk without a
physical scale they are in chunk units and square chunk units, and the check boxes and the Console
headers say so. The attribute names carry no unit (01_AreaBestFitPlane to 10_GSD_maximum, and
11_GSD_coverage_percent). The attribute <code>00_Units</code> of each footprint gives the unit,
&ldquo;metres&rdquo; or &ldquo;chunk units (chunk not scaled)&rdquo;, with the areas in the square
of that unit. The unit therefore stays with the shapes when they are exported.</p>
<p>The following properties can be computed:</p>
<ul>
<li><b>Area, best-fit plane:</b> the area of the footprint projected onto its own best-fit plane,
the least-squares plane through its vertices. MetaFOCUS computes it itself, so that it needs neither
a raster DEM nor an orthomosaic. It is consistent regardless of raster DEM coverage.</li>
<li><b>Area, raster DEM projection:</b> the area computed with the raster DEM for slope correction
(Metashape&rsquo;s <code>area()</code>). <i>Note:</i> where the footprint extends beyond the raster
DEM, Metashape uses the flat projected area for those portions: it does not clip the polygon to the
raster DEM extent. This makes the best-fit plane area more reliable when footprints extend far
beyond the raster DEM.</li>
<li><b>Perimeter, 3D:</b> the total 3D boundary length of the
footprint (Metashape&rsquo;s <code>perimeter3D()</code>).</li>
<li><b>Average GSD from area</b> (04_GSD_averageFromArea): a quick GSD summary from the footprint
area, &radic;(area / ((number of pixel columns &minus; 1) &times; (number of pixel rows &minus;
1))). It is a separate computational consistency check, not an independent measurement. The
footprint runs through the outermost pixel positions, which correspond to the centres of the
outermost photosites for an image that keeps the sampling of the sensor. The footprint therefore
spans one sampling interval fewer than the image has pixel columns along each row, and one fewer
than it has pixel rows along each column. On a flat surface seen square on, with all lens distortion
coefficients set to zero, the value equals the GSD. In general it is the square root of the mean
surface area between the surface points imaged at four neighbouring pixel positions, which differs
from the mean of the dense grid values. Suppose that the footprint lies on a plane and that its area
is taken on that plane (the best-fit plane area, used when no raster DEM area is available). Then
the value is, to first order and up to the sampling of the border, the root mean square, not the
mean, of the local area-equivalent GSD on that plane. The mean in this root mean square is taken
uniformly in pixel coordinates over the rectangle that the outermost pixel positions span. The
values of the dense grid can only approximate this root mean square. A footprint along the outer
edge of the image format, with (number of pixel columns) &times; (number of pixel rows) in the
denominator, would differ little for most cameras (about 0.01&nbsp;% for 6,000&nbsp;&times;&nbsp;4,000
pixels). For low pixel counts with wide fields at high tilt, however, it would differ by up to about
2&nbsp;% (for example 160&nbsp;&times;&nbsp;120 pixels with a 57&deg; field). The raster DEM area is
preferred when it is available. Otherwise, the best-fit plane area is used. A partial footprint gets
no value.</li>
<li><b>Dense grid GSD (minimum, 2nd percentile, average, median, 98th percentile, maximum):</b> the
GSD at the pixel positions of a regular grid, computed with the auxiliary-ray tangent-plane method.
In this method, four auxiliary rays at &plusmn;&frac12; px along the row and the column are
intersected with the local tangent plane at the central ray&rsquo;s surface hit. The auxiliary rays
pass through the midpoints of the edges of the corresponding photosite for an image that keeps the
sampling of the sensor. The two central differences are the projected row and column steps, and the
value of that position is the area-equivalent GSD. The area-equivalent GSD is the side of the square
with the area that the projected row and column steps span, &radic;(GSD<sub>row</sub>&nbsp;&middot;&nbsp;GSD<sub>column</sub>&nbsp;&middot;&nbsp;sin&nbsp;&phi;),
with &phi; the angle between the two steps. It equals the geometric mean of the row and column
ground sampling distances when the steps are perpendicular, and the geometric mean exceeds it by the
factor 1/&radic;(sin&nbsp;&phi;) otherwise. Unlike the geometric mean, it depends only on the area
that the two steps span, not on their directions. To first order, the value of a surface point
therefore does not change when the camera is rolled about its camera axis. This holds provided that
the camera model, apart from its affinity and non-orthogonality, is symmetric about that axis. The
camera model of a frame or fisheye camera is symmetric in this sense with the decentring distortion
coefficients set to zero, without additional corrections, and without rolling-shutter compensation.
The GSD is the sampling interval of the image projected onto the surface, the photosite pitch
projected onto the surface for an image that keeps the sampling of the sensor. In <b>plane mode</b>
the fitted plane is the tangent plane, so the rays meet it exactly. The GSD is, as in the other
modes, the side of the square with the area that the central differences span. For an image that
keeps the sampling of the sensor, this area equals, to first order, the area of the ground-projected
instantaneous field of view (GIFOV). In <b>triangular mesh mode</b> vertex normals averaged over a
3-ring neighbourhood are interpolated at each hit point with barycentric coordinates. <b>Raster DEM
mode</b> works in a local east-north-up CRS with its origin at the perspective centre in a
georeferenced chunk and otherwise in the world coordinates of the chunk. Each ray is followed in
steps of at most one raster DEM cell, measured horizontally, and the crossing is located by repeated
linear interpolation. The normal follows from the raster DEM heights one cell either side of the
hit. The GSD pixel sampling step controls the grid density.</li>
<li><b>GSD coverage:</b> percentage of the grid sample points that hit
the surface within the grazing limit (incidence below about 84&deg;)
and yield a GSD value.</li>
</ul>

<h3>2. Project existing 3D footprints to 2D</h3>
<p>This function projects existing 3D footprint shapes onto a chosen plane to create flat 2D
versions. It offers three projection methods:</p>
<ul>
<li><b>Local horizontal:</b> keeps the horizontal position of every vertex and gives it the mean
height of its footprint, z = mean(z). This method suits nadir aerial surveys over flat terrain. In a
geocentric shape layer, whose third coordinate is no height, the footprint is levelled in its local
east-north-up CRS instead.</li>
<li><b>Best-fit plane:</b> projects onto the plane fitted through the footprint&rsquo;s own
vertices. It adapts automatically to the scene orientation (near-horizontal for terrain,
near-vertical for facades).</li>
<li><b>Custom normal:</b> projects onto the plane through the centroid of the footprint with a
user-defined normal (East, North, Up). For example, (0, 0, 1) gives a horizontal plane and (0, 1, 0)
a vertical plane facing north. A normal of zero length is refused.</li>
</ul>
<p>In a georeferenced chunk the best-fit plane and the custom normal
work in the local east-north-up CRS of each footprint, in metres,
also when the shape layer uses a geographic CRS.  The
custom normal is read in that local east-north-up CRS, not in the
coordinates of the
CRS of the shape layer.  In a non-georeferenced chunk the vertices are
local or internal chunk coordinates and are used as they are.</p>
<p>The 2D shapes appear in a new group named after the source group and the method, for example
&lsquo;Footprints (plane) (2D best-fit plane)&rsquo;, and inherit all attributes from the source
shapes. Note that their areas, perimeters, and GSD values describe the 3D footprint, not the
flattened shape.</p>
"""


class AboutDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("About: Image Footprint Generator")
        self.setWindowFlags(self.windowFlags()
                           & ~QtCore.Qt.WindowContextHelpButtonHint)
        self.setMinimumSize(520, 500)

        mainLayout = QtWidgets.QVBoxLayout(self)

        textBrowser = QtWidgets.QTextBrowser()
        textBrowser.setOpenExternalLinks(True)
        textBrowser.setHtml(_FOOTPRINT_ABOUT_TEXT)
        mainLayout.addWidget(textBrowser)

        buttonRow = QtWidgets.QHBoxLayout()
        buttonClose = QtWidgets.QPushButton("Close")
        buttonClose.clicked.connect(self.accept)
        buttonRow.addStretch()
        buttonRow.addWidget(buttonClose)
        mainLayout.addLayout(buttonRow)


# ===================================================================
# Main dialog
# ===================================================================

class FootprintDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Image Footprint Generator")
        # Remove the ? button from the title bar.
        self.setWindowFlags(self.windowFlags()
                           & ~QtCore.Qt.WindowContextHelpButtonHint)
        self.setMinimumWidth(600)
        self._shapeGroupList = []
        self._selectedPropertyKeys = [
            "AreaBestFitPlane_m2", "GSD_averageFromArea_m", "GSD_median_m", "GSD_maximum_m"]
        self._gsdPixelStep = 50
        self._BuildUserInterface()

    def _BuildUserInterface(self):
        mainLayout = QtWidgets.QVBoxLayout(self)
        activeChunk = Metashape.app.document.chunk

        if activeChunk:
            cameraCount, orientedCount = CountMasterImages(activeChunk)
            hasModel = ChunkHasTriangularMesh(activeChunk)
            self._hasDem = activeChunk.elevation is not None
            meshAvailabilityText = ("Yes" if hasModel else "No")
            if self._hasDem:
                demResolution = activeChunk.elevation.resolution
                demAvailabilityText = DescribeRasterDemAvailability(demResolution)
            else:
                demAvailabilityText = "No"
            chunkSummaryText = ("Active chunk: <b>{}</b><br>"
                                "Images: {:,} total, {:,} oriented<br>"
                                "Triangular mesh: {} &nbsp; Raster DEM: {}").format(
                activeChunk.label or "(unnamed)", cameraCount, orientedCount,
                meshAvailabilityText, demAvailabilityText)
        else:
            chunkSummaryText = "<i>No active chunk.</i>"
            self._hasDem = False

        chunkSummaryLabel = QtWidgets.QLabel(chunkSummaryText)
        chunkSummaryLabel.setTextFormat(QtCore.Qt.RichText); chunkSummaryLabel.setWordWrap(True)
        mainLayout.addWidget(chunkSummaryLabel); mainLayout.addSpacing(8)

        # === SECTION 1 ====================================================
        generateGroup = QtWidgets.QGroupBox("1. Generate 3D footprints")
        generateLayout = QtWidgets.QVBoxLayout(generateGroup)

        surfaceRow = QtWidgets.QHBoxLayout()
        surfaceRow.addWidget(QtWidgets.QLabel("Surface:"))
        self.radioButtonPlane = QtWidgets.QRadioButton("Plane (tie points)")
        self.radioButtonMesh = QtWidgets.QRadioButton("Triangular mesh")
        # Triangular mesh mode is the default when the chunk has a mesh,
        # because a plane stands for the surface only when the scene is
        # planar (see PLANE_SPREAD_WARNING_SHARE).
        if ChunkHasTriangularMesh(activeChunk):
            self.radioButtonMesh.setChecked(True)
        else:
            self.radioButtonPlane.setChecked(True)
            self.radioButtonMesh.setEnabled(False)
        self.radioButtonDem = QtWidgets.QRadioButton("Raster DEM")
        if not activeChunk or not activeChunk.elevation: self.radioButtonDem.setEnabled(False)
        surfaceRow.addWidget(self.radioButtonPlane); surfaceRow.addWidget(self.radioButtonMesh)
        surfaceRow.addWidget(self.radioButtonDem)
        surfaceRow.addStretch(); generateLayout.addLayout(surfaceRow)
        self.surfaceButtonGroup = QtWidgets.QButtonGroup(self)
        self.surfaceButtonGroup.addButton(self.radioButtonPlane)
        self.surfaceButtonGroup.addButton(self.radioButtonMesh)
        self.surfaceButtonGroup.addButton(self.radioButtonDem)

        stepRow = QtWidgets.QHBoxLayout(); stepRow.addSpacing(20)
        self.boundaryStepLabel = QtWidgets.QLabel("Boundary sampling step:")
        self.boundaryStepSpinBox = QtWidgets.QSpinBox()
        self.boundaryStepSpinBox.setRange(10,500); self.boundaryStepSpinBox.setValue(100)
        self.boundaryStepSpinBox.setSuffix(" px")
        stepRow.addWidget(self.boundaryStepLabel); stepRow.addWidget(self.boundaryStepSpinBox)
        stepRow.addStretch(); generateLayout.addLayout(stepRow)

        shapeRow = QtWidgets.QHBoxLayout()
        shapeRow.addWidget(QtWidgets.QLabel("Shape type:"))
        self.radioButtonPolygon = QtWidgets.QRadioButton("Polygon (filled)")
        self.radioButtonPolygon.setChecked(True)
        self.radioButtonPolyline = QtWidgets.QRadioButton("Polyline (outline)")
        shapeRow.addWidget(self.radioButtonPolygon); shapeRow.addWidget(self.radioButtonPolyline)
        shapeRow.addStretch(); generateLayout.addLayout(shapeRow)
        self.shapeTypeButtonGroup = QtWidgets.QButtonGroup(self)
        self.shapeTypeButtonGroup.addButton(self.radioButtonPolygon)
        self.shapeTypeButtonGroup.addButton(self.radioButtonPolyline)

        propertiesRow = QtWidgets.QHBoxLayout()
        self.buttonChooseProperties = QtWidgets.QPushButton("Properties to compute")
        self.buttonChooseProperties.clicked.connect(self._OnChooseProperties)
        propertiesRow.addWidget(self.buttonChooseProperties)
        propertiesRow.addStretch(); generateLayout.addLayout(propertiesRow)

        self.checkBoxSelectedOnly = QtWidgets.QCheckBox("Only process selected images")
        generateLayout.addWidget(self.checkBoxSelectedOnly)
        # What Metashape reports as selected, which the Photos pane need not
        # show (FootprintSelectionStatusText), refreshed when the activation
        # of the window changes and when the footprints are generated.
        self.selectionStatusLabel = QtWidgets.QLabel("")
        self.selectionStatusLabel.setWordWrap(True)
        generateLayout.addWidget(self.selectionStatusLabel)
        self._RefreshSelectionStatus()

        buttonGenerate = QtWidgets.QPushButton("Generate 3D footprints")
        buttonGenerate.clicked.connect(self._OnGenerateFootprints)
        generateLayout.addWidget(buttonGenerate)
        mainLayout.addWidget(generateGroup); mainLayout.addSpacing(4)

        # === SECTION 2 ====================================================
        projectGroup = QtWidgets.QGroupBox("2. Project existing 3D footprints to 2D")
        projectLayout = QtWidgets.QVBoxLayout(projectGroup)

        sourceRow = QtWidgets.QHBoxLayout()
        sourceRow.addWidget(QtWidgets.QLabel("Source group:"))
        self.sourceGroupComboBox = QtWidgets.QComboBox()
        self.sourceGroupComboBox.setMinimumWidth(250)
        self._RefreshGroupList()
        sourceRow.addWidget(self.sourceGroupComboBox)
        buttonRefresh = QtWidgets.QPushButton("Refresh")
        buttonRefresh.clicked.connect(self._RefreshGroupList)
        sourceRow.addWidget(buttonRefresh); sourceRow.addStretch()
        projectLayout.addLayout(sourceRow)

        projectionRow = QtWidgets.QHBoxLayout()
        projectionRow.addWidget(QtWidgets.QLabel("Projection:"))
        self.radioButtonHorizontal = QtWidgets.QRadioButton("Local horizontal")
        self.radioButtonHorizontal.setChecked(True)
        self.radioButtonBestFit = QtWidgets.QRadioButton("Best-fit plane")
        self.radioButtonCustomNormal = QtWidgets.QRadioButton("Custom normal")
        projectionRow.addWidget(self.radioButtonHorizontal)
        projectionRow.addWidget(self.radioButtonBestFit)
        projectionRow.addWidget(self.radioButtonCustomNormal)
        projectionRow.addStretch(); projectLayout.addLayout(projectionRow)
        self.projectionButtonGroup = QtWidgets.QButtonGroup(self)
        self.projectionButtonGroup.addButton(self.radioButtonHorizontal)
        self.projectionButtonGroup.addButton(self.radioButtonBestFit)
        self.projectionButtonGroup.addButton(self.radioButtonCustomNormal)

        customRow = QtWidgets.QHBoxLayout(); customRow.addSpacing(20)
        customRow.addWidget(QtWidgets.QLabel("Normal:"))
        self.customNormalEastSpinBox = QtWidgets.QDoubleSpinBox()
        self.customNormalEastSpinBox.setRange(-1,1); self.customNormalEastSpinBox.setValue(0)
        self.customNormalEastSpinBox.setDecimals(3); self.customNormalEastSpinBox.setPrefix("East: ")
        self.customNormalNorthSpinBox = QtWidgets.QDoubleSpinBox()
        self.customNormalNorthSpinBox.setRange(-1,1); self.customNormalNorthSpinBox.setValue(0)
        self.customNormalNorthSpinBox.setDecimals(3); self.customNormalNorthSpinBox.setPrefix("North: ")
        self.customNormalUpSpinBox = QtWidgets.QDoubleSpinBox()
        self.customNormalUpSpinBox.setRange(-1,1); self.customNormalUpSpinBox.setValue(1)
        self.customNormalUpSpinBox.setDecimals(3); self.customNormalUpSpinBox.setPrefix("Up: ")
        customRow.addWidget(self.customNormalEastSpinBox)
        customRow.addWidget(self.customNormalNorthSpinBox)
        customRow.addWidget(self.customNormalUpSpinBox)
        customRow.addStretch(); projectLayout.addLayout(customRow)
        self.radioButtonCustomNormal.toggled.connect(self.customNormalEastSpinBox.setEnabled)
        self.radioButtonCustomNormal.toggled.connect(self.customNormalNorthSpinBox.setEnabled)
        self.radioButtonCustomNormal.toggled.connect(self.customNormalUpSpinBox.setEnabled)
        self.customNormalEastSpinBox.setEnabled(False); self.customNormalNorthSpinBox.setEnabled(False)
        self.customNormalUpSpinBox.setEnabled(False)

        buttonProject = QtWidgets.QPushButton("Project to 2D")
        buttonProject.clicked.connect(self._OnProjectTo2D)
        projectLayout.addWidget(buttonProject)
        mainLayout.addWidget(projectGroup); mainLayout.addSpacing(8)

        closeRow = QtWidgets.QHBoxLayout()
        buttonAbout = QtWidgets.QPushButton("About")
        buttonAbout.clicked.connect(self._OnShowAbout)
        buttonClose = QtWidgets.QPushButton("Close")
        closeRow.addWidget(buttonAbout)
        closeRow.addStretch(); closeRow.addWidget(buttonClose)
        mainLayout.addLayout(closeRow)
        buttonClose.clicked.connect(self.reject)

    def _RefreshSelectionStatus(self):
        """Show how many images Metashape reports as selected in the active
        chunk and how many of them are oriented."""
        self.selectionStatusLabel.setText(FootprintSelectionStatusText(Metashape.app.document.chunk))

    def changeEvent(self, event):
        """Refresh the selection line when the window is activated or
        deactivated, for example when it comes back after a message."""
        # The window can change before the line exists, while it is built.
        if event.type() == QtCore.QEvent.ActivationChange and getattr(self, "selectionStatusLabel", None) is not None:
            self._RefreshSelectionStatus()
        super().changeEvent(event)

    def _OnShowAbout(self):
        AboutDialog(self).exec_()

    def _RefreshGroupList(self):
        self.sourceGroupComboBox.clear()
        self._shapeGroupList = []
        activeChunk = Metashape.app.document.chunk
        if not activeChunk or activeChunk.shapes is None: return
        for label, group in GetThreeDimensionalShapeGroups(activeChunk):
            self.sourceGroupComboBox.addItem(label)
            self._shapeGroupList.append(group)

    def _OnChooseProperties(self):
        activeChunk = Metashape.app.document.chunk
        dialog = PropertySelectorDialog(
            self._selectedPropertyKeys, self._hasDem, self._gsdPixelStep, self,
            chunkIsScaled=bool(activeChunk) and IsChunkScalePhysical(activeChunk))
        if dialog.exec_() == QtWidgets.QDialog.Accepted:
            self._selectedPropertyKeys = dialog.GetSelectedPropertyKeys()
            self._gsdPixelStep = dialog.GetGsdPixelStep()

    def _NeedsDenseGridGsd(self):
        return bool(DENSE_GRID_PROPERTY_KEYS.intersection(self._selectedPropertyKeys))

    def _OnGenerateFootprints(self):
        activeChunk = Metashape.app.document.chunk
        if not activeChunk:
            QtWidgets.QMessageBox.warning(self, "Image Footprint Generator", "No active chunk."); return

        useMesh = self.radioButtonMesh.isChecked()
        useDem = self.radioButtonDem.isChecked()
        boundaryStep = self.boundaryStepSpinBox.value()
        onlySelected = self.checkBoxSelectedOnly.isChecked()
        gsdPixelStep = self._gsdPixelStep
        needsDenseGsd = self._NeedsDenseGridGsd()
        computeAreaFitted = "AreaBestFitPlane_m2" in self._selectedPropertyKeys
        computeAreaDem = "AreaRasterDEM_m2" in self._selectedPropertyKeys and self._hasDem
        needsAreaGsd = "GSD_averageFromArea_m" in self._selectedPropertyKeys
        computePerimeter = "Perimeter3D_m" in self._selectedPropertyKeys
        if needsAreaGsd and not computeAreaFitted and not computeAreaDem:
            computeAreaFitted = True

        if useMesh and activeChunk.model is None:
            QtWidgets.QMessageBox.warning(self, "Image Footprint Generator", "Triangular mesh mode requires a triangular mesh in the active chunk."); return
        if useDem and activeChunk.elevation is None:
            QtWidgets.QMessageBox.warning(self, "Image Footprint Generator", "Raster DEM mode requires a raster DEM in the active chunk."); return

        # Metres only for a physical scale (see PhysicalChunkScale), the
        # rule of the GSD Value Exporter and the Image Parameter Exporter.
        physicalChunkScale = PhysicalChunkScale(activeChunk)
        chunkIsScaled = physicalChunkScale is not None
        chunkScale = physicalChunkScale if chunkIsScaled else 1.0
        consoleNotes = []
        if not chunkIsScaled:
            reply = QtWidgets.QMessageBox.question(
                self, "Image Footprint Generator",
                "This chunk has no physical scale ({}), so the GSD values "
                "and footprint sizes cannot be expressed in metres.\n\n"
                "Proceed using an arbitrary chunk-unit scale of 1.0? The "
                "areas, perimeters, and GSD values will then be in chunk "
                "units, not metres.".format(DescribeMissingPhysicalScale(activeChunk)),
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.No)
            if reply != QtWidgets.QMessageBox.Yes:
                return
            # Printed in the Console block of the run.
            consoleNotes.append("[Image Footprint Generator] Warning: the chunk is not "
                                "scaled. Areas, perimeters, and GSD values are in chunk "
                                "units, not metres.")

        self._RefreshSelectionStatus()
        cameraList = SelectFootprintImages(activeChunk, onlySelected)
        if not cameraList:
            if onlySelected:
                noSelectionText = NoSelectedImageText("oriented image")
                print("[Image Footprint Generator] " + noSelectionText.split("\n")[0])
                QtWidgets.QMessageBox.warning(self, "Image Footprint Generator", noSelectionText)
            else:
                QtWidgets.QMessageBox.warning(self, "Image Footprint Generator", "The active chunk has no oriented images.")
            return

        progressDialog = CreateFootprintProgressDialog(self, len(cameraList))
        # The run is one Console block (ConsoleBlockStartOfChunk), which
        # every way out of the run closes, as it closes the progress
        # dialog.
        selectionLines = (SelectedImagesConsoleLines(len(SelectedMasterImages(activeChunk)), cameraList)
                          if onlySelected else [])
        PrintConsoleLines(ConsoleBlockStartOfChunk("Image Footprint Generator", activeChunk) + selectionLines
                          + consoleNotes)
        # The progress dialog neither resets nor closes itself (it keeps
        # the dialog's buttons blocked while the shapes are written), so
        # every way out of the run must close it: an unexpected error
        # while the surface is prepared, while an image is computed, or
        # while the shapes are written included.
        try:
            bvhData = None; pointByTrackId = None
            # A failure while the surface is prepared (for example a
            # shortage of memory on a very large triangular mesh) ends the
            # run with a message, as in the GSD Value Exporter.
            try:
                if useMesh:
                    progressDialog.setLabelText("Building the triangular mesh index...")
                    progressDialog.setMaximum(0); QtWidgets.QApplication.processEvents()
                    bvhData = BuildMeshBvh(activeChunk.model, chunk=activeChunk)
                    if bvhData[0] is None:
                        progressDialog.close()
                        QtWidgets.QMessageBox.critical(self, "Image Footprint Generator", "The triangular mesh index could not be built."); return
                    progressDialog.setMaximum(len(cameraList)); progressDialog.setLabelText("Generating footprints...")
                elif not useDem:
                    if activeChunk.tie_points and activeChunk.tie_points.points:
                        pointByTrackId = BuildTrackIdToPointMap(activeChunk.tie_points)
                    else:
                        progressDialog.close()
                        QtWidgets.QMessageBox.critical(self, "Image Footprint Generator", "Plane mode requires tie points in the active chunk."); return
            except Exception as preparationError:
                progressDialog.close()
                print(SurfacePreparationFailureLine(preparationError))
                QtWidgets.QMessageBox.critical(
                    self, "Image Footprint Generator", SurfacePreparationFailureMessage(preparationError))
                return

            if useDem:
                surfaceModeString = "dem"
            elif useMesh:
                surfaceModeString = "mesh"
            else:
                surfaceModeString = "plane"
            footprintDataList = []
            processedImageCount = 0
            failedImageCount = 0
            # Plane mode: the images whose tie points lie far off their
            # plane (PlaneSpreadWarningLine).
            planeWarningCount = 0
            propertyAttributeNameByKey = PROPERTY_ATTRIBUTE_NAMES
            print(FootprintRunConsoleLine(
                len(cameraList), surfaceModeString, boundaryStep,
                [propertyAttributeNameByKey.get(propertyKey, propertyKey)
                 for propertyKey in self._selectedPropertyKeys],
                gsdPixelStep if needsDenseGsd else None, self.radioButtonPolyline.isChecked()))
            for lineIndex, rollingShutterLine in enumerate(RollingShutterConsoleLines(cameraList)):
                print(("[Image Footprint Generator] " if lineIndex == 0 else "") + rollingShutterLine)

            for cameraIndex, camera in enumerate(cameraList):
                if progressDialog.wasCanceled(): break
                progressDialog.setValue(cameraIndex); QtWidgets.QApplication.processEvents()
                processedImageCount = cameraIndex + 1

                # An image whose footprint or GSD cannot be computed (an
                # unexpected error of the Metashape API, for example) is
                # named in the Console and skipped, and the other images
                # are processed, as in the GSD Value Exporter: only the
                # footprints of completed images are created.
                # The footprint and the GSD of the image share their rays,
                # so that both follow the camera motion of an image with
                # rolling-shutter compensation or neither does.
                try:
                    imageRays = FootprintImageRays(camera)
                    footprintResult = ComputeImageFootprint(
                        camera, surfaceModeString, activeChunk, boundaryStep,
                        pointByTrackId, bvhData[0] if bvhData else None, chunkScale, imageRays)
                    hitPointList = footprintResult["hitPointList"]
                    denseGridGsd = None
                    denseGridRasterDemReport = {} if useDem else None
                    if hitPointList is not None and needsDenseGsd:
                        raysFollowedTheMotion = imageRays.SolvesRays()
                        if useDem:
                            denseGridGsd = ComputeDenseGridGsdOnDem(camera, activeChunk, chunkScale, gsdPixelStep,
                                                                    denseGridRasterDemReport, imageRays)
                        elif useMesh:
                            denseGridGsd = ComputeDenseGridGsdOnMesh(camera, bvhData, chunkScale, gsdPixelStep,
                                                                     imageRays)
                        else:
                            denseGridGsd = ComputeDenseGridGsdOnPlane(
                                camera, footprintResult["validProjections"], chunkScale, gsdPixelStep, imageRays)
                        if raysFollowedTheMotion and not imageRays.SolvesRays():
                            # The rays fell back while the GSD was computed:
                            # the footprint is cast again with the same rays.
                            footprintResult = ComputeImageFootprint(
                                camera, surfaceModeString, activeChunk, boundaryStep,
                                pointByTrackId, bvhData[0] if bvhData else None, chunkScale, imageRays)
                            hitPointList = footprintResult["hitPointList"]
                except Exception as imageError:
                    failedImageCount += 1
                    print(FootprintFailureLine(camera.label, imageError))
                    continue
                if hitPointList is None:
                    print(FootprintSkipLine(camera.label, footprintResult["skipReason"]))
                    continue

                perimeterRasterDemReport = footprintResult["rasterDemReport"] or {}
                footprintData = dict(
                    cameraLabel=camera.label, hitPointList=hitPointList,
                    pixelColumnCount=footprintResult["pixelColumnCount"],
                    pixelRowCount=footprintResult["pixelRowCount"],
                    hitCount=footprintResult["hitCount"],
                    missCount=footprintResult["missCount"],
                    boundaryPixelCount=footprintResult["boundaryPixelCount"],
                    withoutRayCount=footprintResult["withoutRayCount"],
                    movedVertexCount=footprintResult["movedVertexCount"], surfaceMode=surfaceModeString,
                    areaFitted=None, areaDem=None, perimeter3d=None,
                    averageGsdFromArea=None, areaGsdSource=None,
                    denseGridGsd=denseGridGsd, shapeKey=None,
                    perimeterRasterDemMissCounts=perimeterRasterDemReport.get("missCountsByReason"),
                    denseGridRasterDemMissCounts=(denseGridRasterDemReport or {}).get("missCountsByReason"),
                    rollingShutterModel=RollingShutterModelName(getattr(camera, "sensor", None)),
                    rollingShutterRayFailure=imageRays.failure,
                    rayText=ExactRayText(imageRays.rayReport))
                footprintDataList.append(footprintData)
                print(FootprintHitLine(camera.label, surfaceModeString,
                                       footprintResult["hitCount"],
                                       footprintResult["boundaryPixelCount"],
                                       footprintResult["withoutRayCount"],
                                       footprintResult["movedVertexCount"]))
                if footprintData["rayText"]:
                    print("{} {}: {}".format(FOOTPRINT_CONSOLE_PREFIX, camera.label, footprintData["rayText"]))
                if (perimeterRasterDemReport.get("perspectiveCentreBelowSurface")
                        or (denseGridRasterDemReport or {}).get("perspectiveCentreBelowSurface")):
                    print(PerspectiveCentreBelowRasterDemNote(camera.label))
                if surfaceModeString == "plane":
                    planeWarningLine = PlaneSpreadWarningLine(
                        camera.label, PlaneSpreadOfImage(camera, footprintResult["validProjections"]))
                    if planeWarningLine is not None:
                        planeWarningCount += 1
                        print("{} {}".format(FOOTPRINT_CONSOLE_PREFIX, planeWarningLine))

            progressDialog.setValue(len(cameraList))

            if processedImageCount < len(cameraList):
                cancelledSummary = CancelledRunSummary(processedImageCount, len(cameraList))
                print("[Image Footprint Generator] " + cancelledSummary)
                if not footprintDataList:
                    progressDialog.close()
                    QtWidgets.QMessageBox.information(
                        self, "Image Footprint Generator",
                        cancelledSummary + "\nNo footprints were created.")
                    return
                if not AskWhetherToCreateCompletedFootprints(
                        self, progressDialog, cancelledSummary, len(footprintDataList)):
                    progressDialog.close()
                    print("[Image Footprint Generator] No footprints were created.")
                    return

            if not footprintDataList:
                progressDialog.close()
                QtWidgets.QMessageBox.warning(
                    self, "Image Footprint Generator",
                    "No footprints were generated. The Console names each "
                    "skipped or failed image and the reason."); return

            progressDialog.setLabelText("Creating shapes..."); QtWidgets.QApplication.processEvents()
            groupLabel = FootprintGroupLabel(surfaceModeString)
            shapesCreatedCount, shapeGroup = CreateFootprintShapes(
                activeChunk, footprintDataList, groupLabel)

            needsShapeMetrics = (computeAreaFitted or computeAreaDem or
                                 needsAreaGsd or computePerimeter)
            if needsShapeMetrics:
                progressDialog.setLabelText("Computing metrics..."); QtWidgets.QApplication.processEvents()
                ComputeAndStoreMetrics(activeChunk, footprintDataList, shapeGroup,
                                       computeAreaFitted or needsAreaGsd,
                                       computeAreaDem, computePerimeter, chunkScale)

            StoreSelectedPropertiesToShapes(
                activeChunk, footprintDataList, shapeGroup, self._selectedPropertyKeys,
                chunkIsScaled)

            if self.radioButtonPolyline.isChecked():
                ConvertShapesToPolylines(activeChunk, shapeGroup)

            # Console summary: only selected columns.
            print("")
            for summaryLine in BuildConsoleSummaryLines(footprintDataList, self._selectedPropertyKeys,
                                                        chunkIsScaled):
                print(summaryLine)
            if planeWarningCount:
                print("  " + PlaneSpreadSummaryText(planeWarningCount, processedImageCount))
        finally:
            progressDialog.close()
            PrintConsoleLines(ConsoleBlockEnd())
        self._RefreshGroupList()
        QtWidgets.QMessageBox.information(self, "Image Footprint Generator",
            CompletionMessage(shapesCreatedCount, groupLabel,
                              processedImageCount, len(cameraList), failedImageCount, planeWarningCount))

    def _OnProjectTo2D(self):
        activeChunk = Metashape.app.document.chunk
        if not activeChunk or activeChunk.shapes is None:
            QtWidgets.QMessageBox.warning(self, "Image Footprint Generator", "The active chunk has no shapes."); return
        selectedIndex = self.sourceGroupComboBox.currentIndex()
        if selectedIndex < 0 or selectedIndex >= len(self._shapeGroupList):
            QtWidgets.QMessageBox.warning(self, "Image Footprint Generator", "Select a source group."); return
        sourceGroup = self._shapeGroupList[selectedIndex]

        if self.radioButtonHorizontal.isChecked(): projectionMethod = "horizontal"
        elif self.radioButtonBestFit.isChecked(): projectionMethod = "bestfit"
        else: projectionMethod = "custom"

        customNormal = (self.customNormalEastSpinBox.value(),
                       self.customNormalNorthSpinBox.value(),
                       self.customNormalUpSpinBox.value())
        # Validate first, so that only the request itself gives a
        # warning: any error of the projection is an error.
        try:
            ValidateProjectionRequest(projectionMethod, customNormal)
        except ValueError as exception:
            QtWidgets.QMessageBox.warning(self, "Image Footprint Generator", str(exception)); return
        newGroupLabel = TwoDimensionalGroupLabel(sourceGroup.label, projectionMethod)

        # The projection is one Console block (ConsoleBlockStartOfChunk),
        # closed before the message.
        PrintConsoleLines(ConsoleBlockStartOfChunk("Image Footprint Generator", activeChunk))
        try:
            createdShapeCount = ProjectShapesTo2D(activeChunk, sourceGroup, projectionMethod,
                                                  customNormal, newGroupLabel)
        except Exception as exception:
            PrintConsoleLines(ConsoleBlockEnd())
            QtWidgets.QMessageBox.critical(self, "Image Footprint Generator", DescribeException(exception)); return
        consoleLine, messageText = TwoDimensionalProjectionSummary(createdShapeCount, newGroupLabel)
        PrintConsoleLines([consoleLine] + ConsoleBlockEnd())
        self._RefreshGroupList()
        QtWidgets.QMessageBox.information(self, "Image Footprint Generator", messageText)


# ===================================================================
# Menu
# ===================================================================

_MENU_LABEL = "MetaFOCUS/Surface-Derived Project Outputs/Image Footprint Generator"
_ICON_NAME = "image_footprint_generator_32.png"

def _ShowFootprintDialog():
    document = Metashape.app.document
    if not document or not document.chunk:
        QtWidgets.QMessageBox.information(
            QtWidgets.QApplication.activeWindow(), "Image Footprint Generator",
            "Open a project with at least one chunk first.")
        return
    FootprintDialog(QtWidgets.QApplication.activeWindow()).exec_()

def RegisterMenuItem():
    """Register the menu item.  Called by the MetaFOCUS loader."""
    try: Metashape.app.removeMenuItem(_MENU_LABEL)
    except Exception as suppressedError:
        ReportSuppressedError("footprintGenerator.RegisterMenuItem", suppressedError)
    iconPath = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "icons", _ICON_NAME)
    if os.path.exists(iconPath):
        Metashape.app.addMenuItem(
            _MENU_LABEL, _ShowFootprintDialog, icon=iconPath)
    else:
        Metashape.app.addMenuItem(_MENU_LABEL, _ShowFootprintDialog)
