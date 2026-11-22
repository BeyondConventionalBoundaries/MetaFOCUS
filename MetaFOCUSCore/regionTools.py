"""
Region Tools for Agisoft Metashape Professional
===============================================

Two utilities for working with the chunk region (bounding box):

1. Align Coordinate Axes to Region: replaces the chunk transform so that the
   coordinate axes follow the region orientation and the origin lies
   at the region centre, while preserving the physical scale of an
   existing transform.  A chunk without a physical scale gets the
   arbitrary scale 1 and stays in arbitrary units: the Console says so.
   Every new transform is recorded in the chunk metadata (see
   RecordRegionToolsTransform), so that the GSD tools report chunk
   units for an arbitrary scale while it is unchanged, and so that no
   tool takes its local coordinates for georeferenced ones (also with
   a physical scale).  The setting of the coordinate reference system
   (CRS) of the chunk (chunk.crs) is not changed, but world coordinates
   are then measured from the new origin along the new axes, so their
   values can change.  Useful when the region has been rotated by hand
   to align with a structure and the coordinate axes should follow it.
   In a georeferenced chunk this replaces the georeference, so the
   dialog asks for a second confirmation.  The previous transform and
   the records of Region Tools in the chunk metadata are printed to
   the Console as a line that restores them (see ChunkTransformState).

2. Copy Region to Other Chunks: copies the active chunk's region
   (position, rotation, and size) to selected target chunks,
   accounting for differences in chunk transforms and, between two
   georeferenced chunks in different CRSs, for the coordinate
   transformation between them.  Target chunks that cannot
   receive the region are skipped and listed with their reason.

Both functions originate from the Agisoft Metashape scripts
repository (https://github.com/agisoft-llc/metashape-scripts)
and have been adapted for the MetaFOCUS toolbox.  The scripts of that
repository are licensed under the MIT License, whose notice applies to
the adapted parts and is kept here and in THIRD_PARTY_NOTICES.txt:

    MIT License

    Copyright (c) 2017 Agisoft LLC

    Permission is hereby granted, free of charge, to any person obtaining a copy
    of this software and associated documentation files (the "Software"), to deal
    in the Software without restriction, including without limitation the rights
    to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
    copies of the Software, and to permit persons to whom the Software is
    furnished to do so, subject to the following conditions:

    The above copyright notice and this permission notice shall be included in all
    copies or substantial portions of the Software.

    THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
    IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
    FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
    AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
    LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
    OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
    SOFTWARE.

Author:      Geert J. Verhoeven (ORCID: 0000-0003-4825-9604)
Version:     1.0.1 (2026-11-22)
Date:        2026-03-24
Licence:     GPL-3.0-or-later WITH GPL-3.0-linking-exception
Contact:     geert.verhoeven@univie.ac.at
Requires:    Agisoft Metashape Professional 2.x+
Developed for: Agisoft Metashape Professional 2.3.2

Usage:
    Loaded by the MetaFOCUS loader (MetaFOCUS.py).  Open the dialog
    from MetaFOCUS > Filtering and Project Management > Region Tools.
"""

import Metashape
import math
import os

from PySide2 import QtWidgets, QtCore
from .photogrammetryUtilities import (
    CrsKind, DescribeException, DescribeMissingPhysicalScale, HasArbitraryRegionToolsScale, IsChunkScalePhysical,
    IsGeoreferencedChunk, MetadataValue, RecordRegionToolsTransform, ReportSuppressedError, EARTH_BOUND_CRS_KINDS,
    REGION_TOOLS_SCALE_METADATA_KEY, REGION_TOOLS_TRANSFORM_METADATA_KEY)


_TOOL_NAME = "Region Tools"


# ===================================================================
# Core logic
# ===================================================================

def ChunkTransformState(chunk):
    """What Align Coordinate Axes to Region replaces in *chunk*, as plain
    numbers and texts, so that TransformRestoreCommand can restore it: the
    rotation (three rows of three elements), the translation (three
    elements), and the scale of chunk.transform, each None when the
    transform has none, and the values of the two entries of Region Tools
    in the chunk metadata (REGION_TOOLS_TRANSFORM_METADATA_KEY and
    REGION_TOOLS_SCALE_METADATA_KEY), each None when absent.  The matrix
    alone would not do: Metashape 2.3.2 gives a chunk without reference
    data a transform without a rotation, a translation, and a scale, whose
    matrix is the identity, and setting that matrix gives the chunk the
    scale 1, which MetaFOCUS takes for a physical scale."""
    transform = chunk.transform
    rotation = transform.rotation
    translation = transform.translation
    scale = transform.scale
    metadata = getattr(chunk, "meta", None)
    return {
        "rotation": None if rotation is None else [
            [float(rotation[rowIndex, columnIndex]) for columnIndex in range(3)] for rowIndex in range(3)],
        "translation": None if translation is None else [float(translation[index]) for index in range(3)],
        "scale": None if scale is None else float(scale),
        "transformRecord": MetadataValue(metadata, REGION_TOOLS_TRANSFORM_METADATA_KEY),
        "scaleRecord": MetadataValue(metadata, REGION_TOOLS_SCALE_METADATA_KEY),
    }


def ConsoleRotationExpression(rotationRows):
    """Python expression for the Console that recreates the rotation
    *rotationRows* of ChunkTransformState exactly (every element written
    with repr), or "None" for a transform without a rotation."""
    if rotationRows is None:
        return "None"
    return "Metashape.Matrix([{}])".format(", ".join(
        "[" + ", ".join(repr(element) for element in row) + "]" for row in rotationRows))


def ConsoleTranslationExpression(translationElements):
    """Python expression for the Console that recreates the translation
    *translationElements* of ChunkTransformState exactly, or "None" for a
    transform without a translation."""
    if translationElements is None:
        return "None"
    return "Metashape.Vector([{}])".format(", ".join(repr(element) for element in translationElements))


def TransformRestoreCommand(chunkKey, transformState):
    """One Console line that gives the chunk with key *chunkKey* the state
    *transformState* (ChunkTransformState) again, whichever chunk is
    active when the line is run: the rotation, the translation, and the
    scale of its transform, each also when it was absent, and the two
    entries of Region Tools in its metadata (ConsoleMetadataStatements).
    The statements are separated by semicolons, and the line is meant to
    be printed without indentation, because the Console rejects an
    indented line."""
    statements = [
        "restoredChunk = [candidateChunk for candidateChunk in Metashape.app.document.chunks "
        "if candidateChunk.key == {!r}][0]".format(chunkKey),
        "restoredChunk.transform.rotation = {}".format(ConsoleRotationExpression(transformState["rotation"])),
        "restoredChunk.transform.translation = {}".format(
            ConsoleTranslationExpression(transformState["translation"])),
        "restoredChunk.transform.scale = {}".format(
            "None" if transformState["scale"] is None else repr(transformState["scale"])),
    ]
    statements += ConsoleMetadataStatements(REGION_TOOLS_TRANSFORM_METADATA_KEY, transformState["transformRecord"])
    statements += ConsoleMetadataStatements(REGION_TOOLS_SCALE_METADATA_KEY, transformState["scaleRecord"])
    return "; ".join(statements)


def ConsoleMetadataStatements(metadataKey, recordedValue):
    """The statements of TransformRestoreCommand that give the entry
    *metadataKey* of the chunk metadata its value *recordedValue* of
    ChunkTransformState again: the value, also an empty text, or, for an
    entry that was absent (None), its deletion.  The entry is set before
    it is deleted, so that the deletion works also when the entry is
    absent and the line can be run more than once (a check in Metashape
    2.3.2 deleted an entry of the chunk metadata with del)."""
    if recordedValue is None:
        return ["restoredChunk.meta[{!r}] = ''".format(metadataKey), "del restoredChunk.meta[{!r}]".format(metadataKey)]
    return ["restoredChunk.meta[{!r}] = {!r}".format(metadataKey, str(recordedValue))]


def IsSameCoordinateSystem(firstCoordinateSystem, secondCoordinateSystem):
    """True when two CRSs have the same, non-empty WKT."""
    firstWellKnownText = str(getattr(firstCoordinateSystem, "wkt", "") or "")
    secondWellKnownText = str(getattr(secondCoordinateSystem, "wkt", "") or "")
    return bool(firstWellKnownText) and firstWellKnownText == secondWellKnownText


def RegionCopyRelativeTransform(sourceChunk, targetChunk):
    """4x4 transform from the internal coordinates of *sourceChunk* to
    those of *targetChunk*, through world coordinates.

    Georeferenced chunks have geocentric world coordinates on the
    geodetic datum of their own CRS, so between two different CRSs the
    coordinate transformation from the source to the target CRS that
    Metashape provides (Metashape.CoordinateSystem.datumTransform) is
    included.  For one and the same CRS it is the identity and is not
    requested.  Raises when the coordinate transformation cannot be
    obtained."""
    relativeTransform = targetChunk.transform.matrix.inv()
    if (IsGeoreferencedChunk(sourceChunk) and IsGeoreferencedChunk(targetChunk)
            and not IsSameCoordinateSystem(sourceChunk.crs, targetChunk.crs)):
        relativeTransform = relativeTransform * Metashape.CoordinateSystem.datumTransform(
            sourceChunk.crs, targetChunk.crs)
    return relativeTransform * sourceChunk.transform.matrix


def RegionCopyIncompatibility(sourceChunk, targetChunk):
    """Reason why the region of *sourceChunk* cannot be placed in
    *targetChunk*, or None when it can.  Both chunks need a transform,
    and their world coordinates must be of the same kind: geocentric
    for georeferenced chunks, local coordinates otherwise."""
    if sourceChunk.transform.matrix is None:
        return "the source chunk has no transform"
    if targetChunk.transform.matrix is None:
        return "the chunk has no transform"
    if IsGeoreferencedChunk(sourceChunk) != IsGeoreferencedChunk(targetChunk):
        return ("one of the two chunks is georeferenced and the other "
                "uses local coordinates")
    return None


def RegionCopySummaryText(copiedCount, skippedChunkReasons, sourceIsGeoreferenced):
    """Message after a region copy: the number of chunks that received
    the region, every skipped chunk with its reason, and for chunks in
    local coordinates the condition under which the copy is right."""
    lines = ["Region copied to {:,} {}.".format(
        copiedCount, "chunk" if copiedCount == 1 else "chunks")]
    if skippedChunkReasons:
        lines.append("")
        lines.append("Not copied to:")
        for chunkLabel, reason in skippedChunkReasons:
            lines.append("{}: {}".format(chunkLabel or "(unnamed)", reason))
    if copiedCount > 0 and not sourceIsGeoreferenced:
        lines.append("")
        lines.append(
            "These chunks use local coordinates. The region is in the "
            "same place only if their coordinates refer to the same "
            "local system, for example through common reference points.")
    return "\n".join(lines)


def FlipRegionRotationColumnsZUp(rotationRows):
    """Negate columns 0 and 2 of a 3x3 rotation given as row lists.

    The columns of the region rotation are the region axes in internal
    chunk coordinates.  When the region's z axis (third column) points
    towards negative internal z, reversing the region's x and z axes
    (the COLUMNS) keeps the new axes aligned with the region and
    right-handed (determinant +1), with z towards positive internal z.
    That is up only when the internal axes are upright.  Negating rows
    instead rotates the axes away from the region axes.
    """
    return [[-row[0], row[1], -row[2]] for row in rotationRows]


# What the scale of the transform that Align Coordinate Axes to Region
# gives a chunk follows from (PreviousScaleKind): the physical scale of its
# transform, the arbitrary scale of the transform that Region Tools
# recorded, or neither, for a chunk without a transform or with a
# transform without a physical scale, as Metashape 2.3.2 gives a chunk
# without Metashape scale bars and reference coordinates.
PREVIOUS_SCALE_PHYSICAL = "physical"
PREVIOUS_SCALE_REGION_TOOLS = "Region Tools"
PREVIOUS_SCALE_WITHOUT_TRANSFORM = "without a transform"
PREVIOUS_SCALE_WITHOUT_PHYSICAL_SCALE = "without a physical scale"


def PreviousScaleKind(chunk):
    """The kind of the scale of the transform of *chunk* before Align
    Coordinate Axes to Region replaces it: PREVIOUS_SCALE_PHYSICAL
    (IsChunkScalePhysical), PREVIOUS_SCALE_REGION_TOOLS (the arbitrary
    scale that Region Tools recorded, HasArbitraryRegionToolsScale),
    PREVIOUS_SCALE_WITHOUT_TRANSFORM, or
    PREVIOUS_SCALE_WITHOUT_PHYSICAL_SCALE."""
    if IsChunkScalePhysical(chunk):
        return PREVIOUS_SCALE_PHYSICAL
    if HasArbitraryRegionToolsScale(chunk):
        return PREVIOUS_SCALE_REGION_TOOLS
    if chunk.transform.matrix is None:
        return PREVIOUS_SCALE_WITHOUT_TRANSFORM
    return PREVIOUS_SCALE_WITHOUT_PHYSICAL_SCALE


def KeepsArbitraryScale(chunk):
    """True when Align Coordinate Axes to Region gives *chunk* a
    transform with an arbitrary scale: every chunk without a physical
    scale (IsChunkScalePhysical), so a chunk without a transform or with
    a transform without a physical scale (the new transform gets the
    scale 1), and a chunk whose transform is still the one that Region
    Tools recorded for a chunk without a physical scale, whose arbitrary
    scale the new transform keeps.  Metashape 2.3.2 gives a chunk without
    reference data the identity matrix without a scale."""
    return PreviousScaleKind(chunk) != PREVIOUS_SCALE_PHYSICAL


def NewTransformScale(chunk):
    """The scale of the transform that Align Coordinate Axes to Region
    gives *chunk*: that of its transform matrix (the length of its first
    row) for a physical scale and for the arbitrary scale that Region
    Tools recorded, and 1 for a chunk without a transform or without a
    physical scale."""
    if PreviousScaleKind(chunk) not in (PREVIOUS_SCALE_PHYSICAL, PREVIOUS_SCALE_REGION_TOOLS):
        return 1.0
    transformMatrix = chunk.transform.matrix
    return math.sqrt(transformMatrix[0, 0] ** 2 + transformMatrix[0, 1] ** 2 + transformMatrix[0, 2] ** 2)


def ArbitraryScaleWarningLines(previousScaleKind, missingScaleReason=""):
    """Console lines of Align Coordinate Axes to Region for a chunk
    that keeps an arbitrary scale (see KeepsArbitraryScale and
    PreviousScaleKind): the chunk stays in arbitrary units, and the GSD
    tools report chunk units.  *missingScaleReason* says why a transform
    has no physical scale (DescribeMissingPhysicalScale)."""
    if previousScaleKind == PREVIOUS_SCALE_REGION_TOOLS:
        firstLine = ("Warning: the previous transform had the arbitrary scale that Align Coordinate Axes to Region "
                     "gives a chunk without a physical scale, and the new transform keeps it, so the chunk stays in "
                     "arbitrary units.")
    elif previousScaleKind == PREVIOUS_SCALE_WITHOUT_TRANSFORM:
        firstLine = ("Warning: the chunk had no transform, so the new transform has the scale 1 and the chunk "
                     "stays in arbitrary units.")
    else:
        firstLine = ("Warning: the chunk transform had no physical scale{}, so the new transform has the arbitrary "
                     "scale 1 and the chunk stays in arbitrary units.".format(
                         " ({})".format(missingScaleReason) if missingScaleReason else ""))
    return [firstLine,
            "The GSD tools of MetaFOCUS report chunk units, not metres, until the transform changes (Metashape "
            "scale bars, reference coordinates, or an imported chunk transform)."]


def AlignmentQuestionText(previousScaleKind):
    """What the question of Align Coordinate Axes to Region says about the
    new transform, by the kind of the previous scale (PreviousScaleKind):
    a chunk without a transform gets one, every other chunk gets its
    transform replaced, and a chunk without a physical scale stays in
    arbitrary units."""
    if previousScaleKind == PREVIOUS_SCALE_WITHOUT_TRANSFORM:
        return ("The chunk gets a transform: the coordinate axes follow the region and the origin moves to the "
                "region centre. World coordinates are then measured from the new origin along the new axes, so "
                "their values can change, while the CRS setting of the chunk stays as it is.\n\n"
                "The chunk has no transform, so the new transform gets the arbitrary scale 1 and the chunk stays in "
                "arbitrary units.")
    if previousScaleKind == PREVIOUS_SCALE_WITHOUT_PHYSICAL_SCALE:
        return ("The chunk transform is replaced: the coordinate axes follow the region and the origin moves to the "
                "region centre. World coordinates are then measured from the new origin along the new axes, so "
                "their values can change, while the CRS setting of the chunk stays as it is.\n\n"
                "The chunk has no physical scale, so the new transform gets the arbitrary scale 1 and the chunk "
                "stays in arbitrary units.")
    text = ("The chunk transform is replaced: the coordinate axes follow the region and the origin moves to the "
            "region centre. Scale is preserved. World coordinates are then measured from the new origin along the "
            "new axes, so their values can change, while the CRS setting of the chunk stays as it is.")
    if previousScaleKind == PREVIOUS_SCALE_REGION_TOOLS:
        text += ("\n\nThe chunk transform has the arbitrary scale that Align Coordinate Axes to Region gave it, so "
                 "the chunk stays in arbitrary units.")
    return text


def AlignmentResultScaleText(previousScaleKind):
    """What the message after Align Coordinate Axes to Region says about
    the scale, by the kind of the previous scale (PreviousScaleKind)."""
    if previousScaleKind == PREVIOUS_SCALE_PHYSICAL:
        return "The origin is at the region centre and the scale has been preserved."
    if previousScaleKind == PREVIOUS_SCALE_WITHOUT_TRANSFORM:
        return ("The origin is at the region centre. The chunk had no transform, so the new transform has the "
                "arbitrary scale 1 and the chunk stays in arbitrary units (see the Console).")
    if previousScaleKind == PREVIOUS_SCALE_WITHOUT_PHYSICAL_SCALE:
        return ("The origin is at the region centre. The chunk had no physical scale, so the new transform has the "
                "arbitrary scale 1 and the chunk stays in arbitrary units (see the Console).")
    return ("The origin is at the region centre. The new transform keeps the arbitrary scale of the previous one, "
            "so the chunk stays in arbitrary units (see the Console).")


def GeocentricFrameNote(crsName):
    """Console note of Align Coordinate Axes to Region for a chunk that
    was not georeferenced but names a geographic, projected, or
    geocentric CRS *crsName*: with a transform, Metashape reads the new
    local coordinates as geocentric coordinates."""
    return ("The CRS of the chunk, {}, is not a local one, so Metashape reads the new local coordinates as "
            "geocentric coordinates near the centre of the Earth or of another celestial body, and the values "
            "in the Reference pane and the exports in that CRS are meaningless. Choose a local CRS, for example Local Coordinates (m), in the "
            "Reference Settings to work in the new coordinates.".format(crsName))


def AlignCoordinateAxesToRegion(chunk):
    """Align the chunk's coordinate axes with the region.

    The chunk transform is replaced by a similarity whose axes are the
    region axes (with z towards positive internal z, see
    FlipRegionRotationColumnsZUp) and whose origin is the region
    centre.  The scale of the previous transform is preserved (scale 1
    when the chunk had no physical scale).  chunk.crs is not changed, but
    world coordinates are then measured from the new origin along the new
    axes, so their values can change.  In a georeferenced chunk they are
    read as geocentric, so the georeference is lost until the next Update
    or Optimize Cameras with enough enabled reference coordinates, or
    until the line that the dialog prints (TransformRestoreCommand)
    restores the previous transform.

    A chunk without a physical scale gets the arbitrary scale 1, and a
    chunk whose transform is still the one that Region Tools recorded
    for a chunk without a physical scale keeps its arbitrary scale (see
    KeepsArbitraryScale and NewTransformScale).  The Console then warns
    that the chunk stays in arbitrary units.  The new transform is
    recorded in the chunk metadata under
    REGION_TOOLS_TRANSFORM_METADATA_KEY, with whether its scale is
    arbitrary under REGION_TOOLS_SCALE_METADATA_KEY (see
    RecordRegionToolsTransform), so that the GSD tools report chunk units
    while the transform is unchanged, and so that no tool takes its
    local coordinates for georeferenced ones (HasRegionToolsPlacement).
    A chunk that was not georeferenced but names a geographic, projected,
    or geocentric CRS gets a Console note that Metashape reads the new
    local coordinates as geocentric coordinates (see GeocentricFrameNote).

    Parameters
    ----------
    chunk : Metashape.Chunk

    Returns
    -------
    success : bool
    """
    regionRotation = chunk.region.rot
    regionCentre = chunk.region.center
    # Evaluated before the transform is replaced: afterwards the chunk
    # has a new transform, and a recorded one no longer matches it.
    previousScaleKind = PreviousScaleKind(chunk)
    keepsArbitraryScale = previousScaleKind != PREVIOUS_SCALE_PHYSICAL
    missingScaleReason = DescribeMissingPhysicalScale(chunk)
    wasGeoreferenced = IsGeoreferencedChunk(chunk)
    scale = NewTransformScale(chunk)
    scaleMatrix = Metashape.Matrix().Diag([scale, scale, scale, 1])

    # The region's rotation matrix can have its z axis pointing
    # towards negative internal z.  The COLUMNS of the matrix are the
    # region axes in internal chunk coordinates, so when element
    # [2, 2] is negative the region's x and z axes (columns 0 and 2)
    # are reversed: the new axes stay aligned with the region and
    # right-handed (determinant +1), with z towards positive internal z.
    rotationRows = [[regionRotation[index, 0], regionRotation[index, 1],
                     regionRotation[index, 2]] for index in range(3)]
    if rotationRows[2][2] < 0:
        rotationRows = FlipRegionRotationColumnsZUp(rotationRows)

    # Build a 4x4 matrix from the (possibly flipped) rotation and the
    # region centre.
    regionTransform = Metashape.Matrix([
        [rotationRows[0][0], rotationRows[0][1],
         rotationRows[0][2], regionCentre[0]],
        [rotationRows[1][0], rotationRows[1][1],
         rotationRows[1][2], regionCentre[1]],
        [rotationRows[2][0], rotationRows[2][1],
         rotationRows[2][2], regionCentre[2]],
        [0, 0, 0, 1]])

    chunk.transform.matrix = scaleMatrix * regionTransform.inv()

    if keepsArbitraryScale:
        for warningLine in ArbitraryScaleWarningLines(previousScaleKind, missingScaleReason):
            print(warningLine)
    # Every new transform is recorded, also one with a physical scale, so
    # that its local coordinates are not taken for georeferenced ones.
    print(RegionToolsRecordLine(RecordRegionToolsTransform(chunk, chunk.transform.matrix, keepsArbitraryScale),
                                keepsArbitraryScale))
    if not wasGeoreferenced and CrsKind(chunk.crs) in EARTH_BOUND_CRS_KINDS:
        print(GeocentricFrameNote(getattr(chunk.crs, "name", "") or "(unnamed)"))

    Metashape.app.update()
    return True


def RegionToolsRecordLine(isRecorded, hasArbitraryScale):
    """Console line of Align Coordinate Axes to Region on the record of
    the new transform in the chunk metadata (RecordRegionToolsTransform):
    what the record lets the other tools recognise, or that they cannot,
    because the record could not be written."""
    keys = "{} and {}".format(REGION_TOOLS_TRANSFORM_METADATA_KEY, REGION_TOOLS_SCALE_METADATA_KEY)
    if hasArbitraryScale:
        if isRecorded:
            return ("The new transform is recorded in the chunk metadata ({}), so that the GSD tools recognise its "
                    "arbitrary scale.".format(keys))
        return ("Warning: the new transform could not be recorded in the chunk metadata, so the GSD tools cannot "
                "recognise its arbitrary scale and give its values in metres.")
    if isRecorded:
        return ("The new transform is recorded in the chunk metadata ({}), so that the tools of MetaFOCUS do not "
                "take its coordinates for georeferenced ones.".format(keys))
    return ("Warning: the new transform could not be recorded in the chunk metadata, so the tools of MetaFOCUS can "
            "take its coordinates for georeferenced ones in a chunk whose CRS is not a local one.")


def CopyRegionToSelectedChunks(sourceChunk, targetChunks):
    """Copy the active chunk's region to selected target chunks.

    Accounts for the differences between the chunk transforms and,
    between georeferenced chunks in different CRSs, for the coordinate
    transformation between them, so the region occupies the same volume
    in world coordinates in each chunk.  A target chunk is skipped when
    it is incompatible with the source chunk (see
    RegionCopyIncompatibility) or when the coordinate transformation
    cannot be obtained.

    Parameters
    ----------
    sourceChunk : Metashape.Chunk
        The source chunk whose region will be copied.
    targetChunks : list of Metashape.Chunk
        Target chunks to receive the region.

    Returns
    -------
    copiedCount : int
        Number of chunks that received the copied region.
    skippedChunkReasons : list of (str, str)
        Label and reason of every target chunk that was skipped
        (see RegionCopyIncompatibility and RegionCopyRelativeTransform).
    """
    skippedChunkReasons = []
    sourceTransformMatrix = sourceChunk.transform.matrix
    if sourceTransformMatrix is None:
        print("[MetaFOCUS] Region copy: the source chunk has no "
              "transform, so its region cannot be placed in other "
              "chunks.")
        for targetChunk in targetChunks:
            if targetChunk != sourceChunk:
                skippedChunkReasons.append(
                    (targetChunk.label, "the source chunk has no transform"))
        return 0, skippedChunkReasons

    sourceRegion = sourceChunk.region
    sourceRotation = sourceRegion.rot
    sourceCentre = sourceRegion.center
    sourceSize = sourceRegion.size

    copiedCount = 0

    for targetChunk in targetChunks:
        if targetChunk == sourceChunk:
            continue

        incompatibility = RegionCopyIncompatibility(sourceChunk, targetChunk)
        if incompatibility is not None:
            print("[MetaFOCUS] Region copy: skipped chunk {}: {}.".format(
                targetChunk.label, incompatibility))
            skippedChunkReasons.append((targetChunk.label, incompatibility))
            continue

        # Relative transform from the internal coordinates of the source
        # chunk to those of the target chunk, with the coordinate
        # transformation between the CRSs.
        try:
            relativeTransform = RegionCopyRelativeTransform(sourceChunk, targetChunk)
        except Exception as suppressedError:
            # Only this chunk is skipped: the region must not be placed
            # without the coordinate transformation.
            ReportSuppressedError(
                "regionTools.CopyRegionToSelectedChunks.datumTransform", suppressedError)
            reason = ("no coordinate transformation between the two CRSs "
                      "({})".format(DescribeException(suppressedError)))
            print("[MetaFOCUS] Region copy: skipped chunk {}: {}.".format(
                targetChunk.label, reason))
            skippedChunkReasons.append((targetChunk.label, reason))
            continue

        # Extract the 3x3 rotation with scale from the relative transform.
        rotationWithScale = Metashape.Matrix([
            [relativeTransform[0, 0], relativeTransform[0, 1],
             relativeTransform[0, 2]],
            [relativeTransform[1, 0], relativeTransform[1, 1],
             relativeTransform[1, 2]],
            [relativeTransform[2, 0], relativeTransform[2, 1],
             relativeTransform[2, 2]]])

        scale = rotationWithScale.row(0).norm()
        pureRotation = rotationWithScale * (1.0 / scale)

        newRegion = Metashape.Region()
        newRegion.rot = pureRotation * sourceRotation
        newRegion.center = relativeTransform.mulp(sourceCentre)
        newRegion.size = sourceSize * scale
        targetChunk.region = newRegion

        copiedCount += 1
        print("  Region copied to chunk: {}".format(
            targetChunk.label))

    Metashape.app.update()
    return copiedCount, skippedChunkReasons


# ===================================================================
# GUI
# ===================================================================

_ABOUT_HTML = """
<h2>Region Tools</h2>
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
<p><b>Adapted code:</b> both functions adapt scripts of Agisoft&rsquo;s
repository of Python scripts for Metashape
(<a href="https://github.com/agisoft-llc/metashape-scripts">github.com/agisoft-llc/metashape-scripts</a>),
which are licensed under the MIT License, Copyright (c) 2017 Agisoft LLC.
Its notice is in THIRD_PARTY_NOTICES.txt in the MetaFOCUS ZIP file and in
the header of regionTools.py.</p>

<h3>Align Coordinate Axes to Region</h3>
<p>This function replaces the chunk transform so that the coordinate axes follow the region
orientation and the origin lies at the region centre. The scale of the chunk transform is preserved.
The setting of the coordinate reference system (CRS) of the chunk in the Reference Settings is not
changed. The transform between the internal chunk coordinates and the world coordinates is replaced,
however, so world coordinates are then measured from the new origin along the new axes, and their
values can change. The <i>Active chunk</i> box shows the CRS that is set for the chunk.</p>
<p>The new Z axis is the region axis that points towards positive internal chunk z: when the region&rsquo;s
z axis points the other way, its x and z axes are both reversed, which keeps the axes right-handed.
Internal chunk z is not necessarily up in the CRS of the chunk (for example in a chunk referenced
with markers), so the orientation of the result should be checked.</p>
<p>The function is useful when the coordinate axes are to follow the orientation of a region that
has been rotated by hand to line up with a building fa&ccedil;ade or an excavation trench.</p>
<p><b>Reference coordinates:</b> the reference coordinates of markers and images keep their values,
but the new coordinate axes no longer match them. Ground control points (GCPs) and image reference
positions then disagree with the estimated values. The next <i>Update</i> in the Reference pane, or
<i>Optimize Cameras</i> with reference data, fits the transform to them again, so that the axes no
longer follow the region. The function is therefore most useful in chunks without reference
coordinates, or when the intention is to assign new reference coordinates that refer to the new
axes.</p>
<p><b>Georeferenced chunks:</b> in a chunk whose transform is fitted to reference coordinates in a
geographic, projected, or geocentric CRS, the transform maps internal coordinates to geocentric
coordinates. Aligning the axes replaces it with a transform to local coordinates centred on the
region, while the chunk keeps its CRS. As a result, Metashape reads these local coordinates as
geocentric coordinates near the centre of the Earth or of another celestial body. The values in the
Reference pane, exports in that CRS, and the MetaFOCUS tools that use the georeference then give
meaningless values. In addition, an existing raster digital elevation model (DEM) or orthomosaic no longer matches the
oriented images and the 3D tie points. The tool therefore asks for a second confirmation that names
the CRS and these consequences. With enough enabled reference coordinates, the next <i>Update</i> in
the Reference pane, or <i>Optimize Cameras</i>, fits the transform to the reference data again, which
need not reproduce the previous transform exactly. The line that the tool prints to the Console
(below), however, restores the previous transform itself. Without enough enabled reference
coordinates, for example after an imported chunk transform, this line restores the georeference
without reference coordinates.</p>
<p><b>Restoring the previous transform:</b> before it replaces the transform, the tool prints a
single line to the Console that can be pasted into the Console as it is. The line restores the
rotation, the translation, and the scale of the previous chunk transform, also when the transform had
none of them, as in a chunk without a physical scale. It also restores the record of the tool in the
chunk metadata (below), so that the MetaFOCUS tools treat the scale and the georeference of the chunk
as before. The line addresses the chunk by its key, so it restores the right chunk whichever chunk is
active.</p>
<p><b>Chunks without a physical scale:</b> in a chunk without a physical scale, the transform has no
scale from Metashape scale bars, reference coordinates, or an imported chunk transform. (Metashape
2.3.2 gives such a chunk the identity transform without a scale.) In such a chunk, the new transform
has the scale 1 (an arbitrary scale), and the chunk stays in arbitrary units. The Console says so,
and the tool records the new transform in the chunk metadata (under <code>MetaFOCUS/RegionToolsTransform</code>,
and under <code>MetaFOCUS/RegionToolsScale</code> that its scale is arbitrary). With this record,
the GSD Value Exporter, the Image Footprint Generator, and the Image Parameter Exporter report chunk
units, not metres, as long as the transform is unchanged. The Image Parameter Exporter then exports
no ground sampling distance (GSD) and no image scale number. Applying the function again to such a
chunk keeps the arbitrary scale and records the new transform. Once the transform changes otherwise
(Metashape scale bars, reference coordinates, or an imported chunk transform), the record no longer
applies. When such a chunk names a geographic, projected, or geocentric CRS, Metashape reads the new
local coordinates as geocentric coordinates near the centre of the Earth or of another celestial
body, and the Console says so.
To work in the new coordinates, one should then choose a local CRS in the Reference Settings.</p>
<p><b>Not georeferenced afterwards:</b> the tool records every new transform, including one with a
physical scale. With this record, no MetaFOCUS tool takes the local coordinates for georeferenced
ones as long as the axes and the origin of the world coordinates are those that the tool gave them.
This also holds after a new scale from Metashape scale bars. A transform fitted to reference
coordinates moves the origin, and usually turns the axes, which ends the record.</p>
<p>The reverse operation, which rotates the region to the local axes of
the chunk CRS and leaves the chunk transform unchanged, is built into
Metashape: <i>Model &gt; Transform Region &gt; Rotate Region to Local
Frame</i>.</p>

<h3>Copy Region to Other Chunks</h3>
<p>This function copies the active chunk&rsquo;s region (bounding box) to selected target chunks.
The chunks that should receive the region are chosen in a selection dialog. The copy accounts for
the differences between the chunk transforms, so the region occupies the same volume in world
coordinates in each target chunk. Between two georeferenced chunks in different CRSs, it also
applies the coordinate transformation that Metashape provides between their world coordinates
(CoordinateSystem.datumTransform), which are geocentric on the datums of the two CRSs. Chunks in one
and the same CRS need none.</p>
<p>A target chunk is skipped, and keeps its region, when it has no transform, when one of the two
chunks is georeferenced and the other uses local coordinates, or when Metashape cannot provide this
coordinate transformation. The final message lists the skipped chunks with their reasons. For chunks
in local coordinates, the region lands in the same place only if their local CRSs are based on the
same datum, for example through common reference points. The tool cannot check this.</p>
<p>The function is useful when the same area is processed across several epochs (for example
monitoring campaigns), where each epoch is a separate chunk but the region of interest is the same.</p>


<h3>Origin</h3>
<p>Both functions originate from the
<a href="https://github.com/agisoft-llc/metashape-scripts">
Agisoft Metashape scripts repository</a> and have been adapted
for the MetaFOCUS toolbox.</p>
"""


class RegionToolsDialog(QtWidgets.QDialog):
    """Dialog for region (bounding box) operations."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Region Tools")
        self.setWindowFlags(
            self.windowFlags()
            & ~QtCore.Qt.WindowContextHelpButtonHint)
        self.setMinimumWidth(460)
        self._BuildUi()

    def _BuildUi(self):
        mainLayout = QtWidgets.QVBoxLayout(self)

        chunk = Metashape.app.document.chunk
        document = Metashape.app.document

        # --- Chunk info ------------------------------------------------
        activeChunkGroup = QtWidgets.QGroupBox("Active chunk")
        activeChunkLayout = QtWidgets.QFormLayout(activeChunkGroup)

        chunkLabel = chunk.label if chunk else "(none)"
        activeChunkLayout.addRow("Chunk:", QtWidgets.QLabel(chunkLabel))

        # The CRS set for the chunk in the Reference Settings (chunk.crs),
        # whether or not the chunk is georeferenced.
        crsName = "(not set)"
        if chunk and chunk.crs is not None:
            crsName = getattr(chunk.crs, "name", "") or "(unnamed)"
        activeChunkLayout.addRow("Coordinate reference system:", QtWidgets.QLabel(crsName))

        totalChunks = len(document.chunks) if document else 0
        activeChunkLayout.addRow("Chunks in document:",
            QtWidgets.QLabel("{:,}".format(totalChunks)))

        # Whether the chunk has a physical scale, which the alignment keeps
        # (Metashape 2.3.2 gives every chunk a transform).
        hasPhysicalScale = IsChunkScalePhysical(chunk) if chunk else False
        activeChunkLayout.addRow("Physical scale:",
            QtWidgets.QLabel("Yes" if hasPhysicalScale else "No"))

        mainLayout.addWidget(activeChunkGroup)

        # --- Actions ---------------------------------------------------
        actionGroup = QtWidgets.QGroupBox("Actions")
        actionLayout = QtWidgets.QVBoxLayout(actionGroup)

        self.buttonAlignCoordinateAxes = QtWidgets.QPushButton(
            "Align Coordinate Axes to Region")
        self.buttonAlignCoordinateAxes.setToolTip(
            "Replaces the chunk transform: the coordinate axes\n"
            "follow the region and the origin moves to the\n"
            "region centre. Scale is preserved. A georeferenced\n"
            "chunk asks for a second confirmation.")
        self.buttonAlignCoordinateAxes.clicked.connect(self._OnAlignCoordinateAxes)
        actionLayout.addWidget(self.buttonAlignCoordinateAxes)

        self.buttonCopyRegion = QtWidgets.QPushButton(
            "Copy Region to Other Chunks")
        self.buttonCopyRegion.setToolTip(
            "Copies the active chunk's region (position,\n"
            "rotation, and size) to selected chunks, accounting\n"
            "for their transforms and, between different\n"
            "CRSs, for the coordinate transformation.")
        self.buttonCopyRegion.setEnabled(totalChunks > 1)
        self.buttonCopyRegion.clicked.connect(self._OnCopyRegion)
        actionLayout.addWidget(self.buttonCopyRegion)

        mainLayout.addWidget(actionGroup)

        # --- Buttons ---------------------------------------------------
        buttonLayout = QtWidgets.QHBoxLayout()

        aboutButton = QtWidgets.QPushButton("About")
        aboutButton.clicked.connect(self._ShowAbout)
        buttonLayout.addWidget(aboutButton)

        buttonLayout.addStretch()

        closeButton = QtWidgets.QPushButton("Close")
        closeButton.clicked.connect(self.reject)
        buttonLayout.addWidget(closeButton)

        mainLayout.addLayout(buttonLayout)

    def _OnAlignCoordinateAxes(self):
        chunk = Metashape.app.document.chunk
        if not chunk:
            return

        previousScaleKind = PreviousScaleKind(chunk)
        reply = QtWidgets.QMessageBox.question(
            self, _TOOL_NAME,
            "Align the coordinate axes with the current region "
            "orientation?\n\n" + AlignmentQuestionText(previousScaleKind),
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No)
        if reply != QtWidgets.QMessageBox.Yes:
            return

        # A georeferenced chunk loses its georeference: ask again, and
        # name the consequences.
        isGeoreferenced = IsGeoreferencedChunk(chunk)
        if isGeoreferenced:
            crsName = getattr(chunk.crs, "name", "") or "its CRS"
            reply = QtWidgets.QMessageBox.warning(
                self, _TOOL_NAME,
                "This chunk is georeferenced in {}.\n\n"
                "Aligning the axes replaces the chunk transform with a transform to local "
                "coordinates centred on the region, but the chunk keeps this "
                "CRS, so Metashape reads these local coordinates "
                "as geocentric coordinates. The values in the Reference "
                "pane, exports in this CRS, and the "
                "MetaFOCUS tools that use the georeference then give "
                "meaningless values, and an existing raster DEM or "
                "orthomosaic no longer matches the oriented images and "
                "the tie points. With enough enabled reference "
                "coordinates, the next Update in the Reference pane, or "
                "Optimize Cameras, fits the transform to the reference "
                "data again, which need not give the previous axes "
                "exactly.\n\n"
                "The previous transform is printed to the Console as a "
                "line that restores it. Without enough enabled reference "
                "coordinates, for example after an imported chunk "
                "transform, this line restores the georeference without "
                "reference coordinates.\n\n"
                "Replace the chunk transform?".format(crsName),
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.No)
            if reply != QtWidgets.QMessageBox.Yes:
                return

        previousTransformState = ChunkTransformState(chunk)

        print("")
        print("=" * 60)
        print("Region Tools: Align Coordinate Axes to Region")
        print("=" * 60)
        print("Chunk: {}".format(chunk.label))
        print("To restore the previous chunk transform and the records of "
              "Region Tools in the chunk metadata, paste this line into "
              "the Console:")
        print(TransformRestoreCommand(chunk.key, previousTransformState))

        AlignCoordinateAxesToRegion(chunk)

        print("The coordinate axes now follow the region, with the origin "
              "at the region centre.")
        print("=" * 60)
        print("")

        QtWidgets.QMessageBox.information(
            self, _TOOL_NAME,
            "The coordinate axes now follow the region. {}{}".format(
                AlignmentResultScaleText(previousScaleKind),
                "\n\nThe Console shows a line that restores the previous, "
                "georeferenced transform." if isGeoreferenced else ""))

    def _OnCopyRegion(self):
        chunk = Metashape.app.document.chunk
        document = Metashape.app.document
        if not chunk or not document:
            return
        if chunk.transform.matrix is None:
            QtWidgets.QMessageBox.information(
                self, _TOOL_NAME,
                "The active chunk has no transform, so its region "
                "cannot be placed in other chunks.")
            return

        # Build the list of other chunks.
        otherChunks = [candidateChunk for candidateChunk in document.chunks if candidateChunk != chunk]
        if not otherChunks:
            QtWidgets.QMessageBox.information(
                self, "Region Tools",
                "There are no other chunks to copy the region to.")
            return

        # Show the chunk selection dialog.
        selectionDialog = QtWidgets.QDialog(self)
        selectionDialog.setWindowTitle("Region Tools")
        selectionDialog.setWindowFlags(
            selectionDialog.windowFlags()
            & ~QtCore.Qt.WindowContextHelpButtonHint)
        dialogLayout = QtWidgets.QVBoxLayout(selectionDialog)

        dialogLayout.addWidget(QtWidgets.QLabel(
            "Copy region from <b>{}</b> to:".format(
                chunk.label or "(unnamed)")))

        checkBoxes = []
        for otherChunk in otherChunks:
            checkBox = QtWidgets.QCheckBox(
                otherChunk.label or "(unnamed)")
            checkBox.setChecked(True)
            dialogLayout.addWidget(checkBox)
            checkBoxes.append((checkBox, otherChunk))

        # Select all / none buttons.
        selectRow = QtWidgets.QHBoxLayout()
        selectAllButton = QtWidgets.QPushButton("Select all")
        selectAllButton.clicked.connect(
            lambda: [optionCheckBox.setChecked(True) for optionCheckBox, _ in checkBoxes])
        selectNoneButton = QtWidgets.QPushButton("Select none")
        selectNoneButton.clicked.connect(
            lambda: [optionCheckBox.setChecked(False) for optionCheckBox, _ in checkBoxes])
        selectRow.addWidget(selectAllButton)
        selectRow.addWidget(selectNoneButton)
        selectRow.addStretch()
        dialogLayout.addLayout(selectRow)

        # OK/Cancel.
        buttonBox = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok
            | QtWidgets.QDialogButtonBox.Cancel)
        buttonBox.accepted.connect(selectionDialog.accept)
        buttonBox.rejected.connect(selectionDialog.reject)
        dialogLayout.addWidget(buttonBox)

        if selectionDialog.exec_() != QtWidgets.QDialog.Accepted:
            return

        selectedChunks = [candidateChunk for optionCheckBox, candidateChunk in checkBoxes if optionCheckBox.isChecked()]
        if not selectedChunks:
            return

        print("")
        print("=" * 60)
        print("Region Tools: Copy Region to Other Chunks")
        print("=" * 60)
        print("Source chunk: {}".format(chunk.label))
        print("Target chunks: {}".format(
            ", ".join(candidateChunk.label for candidateChunk in selectedChunks)))
        print("")

        copiedCount, skippedChunkReasons = CopyRegionToSelectedChunks(
            chunk, selectedChunks)
        summaryText = RegionCopySummaryText(
            copiedCount, skippedChunkReasons, IsGeoreferencedChunk(chunk))

        print("")
        print(summaryText)
        print("=" * 60)
        print("")

        QtWidgets.QMessageBox.information(self, _TOOL_NAME, summaryText)

    def _ShowAbout(self):
        aboutDialog = QtWidgets.QDialog(self)
        aboutDialog.setWindowTitle("About: Region Tools")
        aboutDialog.setWindowFlags(
            aboutDialog.windowFlags()
            & ~QtCore.Qt.WindowContextHelpButtonHint)
        aboutDialog.setMinimumSize(520, 440)
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

_MENU_LABEL = "MetaFOCUS/Filtering and Project Management/Region Tools"
_ICON_NAME = "region_tools_32.png"


def _ShowRegionToolsDialog():
    document = Metashape.app.document
    if not document or not document.chunk:
        QtWidgets.QMessageBox.information(
            QtWidgets.QApplication.activeWindow(), _TOOL_NAME,
            "Open a project with at least one chunk first.")
        return
    RegionToolsDialog(
        QtWidgets.QApplication.activeWindow()).exec_()


def RegisterMenuItem():
    """Register the menu item.  Called by the MetaFOCUS loader."""
    try:
        Metashape.app.removeMenuItem(_MENU_LABEL)
    except Exception as suppressedError:
        ReportSuppressedError("regionTools.RegisterMenuItem", suppressedError)
    iconPath = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "icons", _ICON_NAME)
    if os.path.exists(iconPath):
        Metashape.app.addMenuItem(
            _MENU_LABEL, _ShowRegionToolsDialog, icon=iconPath)
    else:
        Metashape.app.addMenuItem(_MENU_LABEL, _ShowRegionToolsDialog)
