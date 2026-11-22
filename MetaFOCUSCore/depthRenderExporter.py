"""
Depth & Render Export
=====================

Two related exports from a finished project, in one window:

  * Depth maps tab: saves the per-image depth maps that Metashape
    computes during depth map generation (chunk.depth_maps), as
    32-bit float depth multiplied by the chunk scale (metres in a
    scaled chunk) or as normalised 8-bit or 16-bit greyscale with 0 for
    pixels without depth.

  * Render from triangular mesh tab: renders the triangular mesh with
    the rendering functions of the Metashape Python interface
    (Model.renderImage, renderDepth, and renderMask), producing an
    image, a depth image, and a mask, in any combination, from the
    viewpoint of each oriented image or of each keyframe of the
    animation track (through a virtual camera).
    Only the keyframes are rendered, not the interpolated frames of an
    animation movie.

The two depths are different quantities: the Depth maps tab saves the
multi-view stereo depth maps of the images (with the pixel count that
the depth map quality sets), whereas the render tab synthesises depth from the
triangular mesh surface, in internal chunk units.

Metashape's own Export Depth... command (context menu of an image)
exports a diffuse map, a depth map, and a normal map and needs a
triangular mesh for them (Metashape manual, version 2.3, p. 85), which
indicates that its depth map comes from the mesh, and the manual documents no
command that exports the depth maps of depth map generation.

Both tabs name the files after the image label, with the image key
appended when the label is shared within the chunk, and never write
the same file name, so both exports can share a folder.

Author:     Geert J. Verhoeven (ORCID: 0000-0003-4825-9604)
            The depth-map export is based on an earlier script by
            the author.
Contact:    geert.verhoeven@univie.ac.at
Version:    1.0.1 (2026-11-22)
Licence:    GPL-3.0-or-later WITH GPL-3.0-linking-exception
Requires:   Agisoft Metashape Professional 2.x
Developed for: Agisoft Metashape Professional 2.3.2
"""

import collections
import datetime
import functools
import json
import math
import ntpath
import os
import posixpath
import re
import shutil
import struct
import sys
import tempfile
import zlib
from xml.etree import ElementTree
import Metashape
from PySide2 import QtWidgets, QtCore

from .photogrammetryUtilities import (
    ReportSuppressedError,
    DescribeException,
    FormatCountWithNoun,
    PhysicalChunkScale,
    DescribeMissingPhysicalScale,
    SetLensDistortionAndAffinityToZero,
    TransformMatrixElements,
    CrsKind,
    HasArbitraryRegionToolsScale,
    HasRegionToolsPlacement,
    IsGeoreferencedChunk,
    RollingShutterModelName,
    ConsoleBlockEnd,
    ConsoleBlockStartOfChunk,
    PrintConsoleLines,
    SelectedImagesStatusText,
    SelectedImagesConsoleLines,
    NoSelectedImageText,
)

try:
    import numpy
    _NUMPY_AVAILABLE = True
except Exception:
    _NUMPY_AVAILABLE = False


# The article of Agisoft's knowledge base on installing a Python module into
# the Python of Metashape, whose commands MetaFOCUS follows: python.exe in the
# Python folder of Metashape on Windows, and bin/python3.X in it on macOS and
# Linux, each with -m pip install.
NUMPY_INSTALL_ARTICLE = "How to install external Python module to Metashape Professional package"


def MetashapePythonInterpreter(prefix, platform, pythonVersion, isFile=os.path.isfile):
    """Path of the interpreter of the Python that runs MetaFOCUS, from its
    *prefix* (sys.prefix, the Python folder of Metashape): python.exe in it
    on Windows and bin/python3.X in it on macOS and Linux, as in Agisoft's
    knowledge base (NUMPY_INSTALL_ARTICLE), or None when that file does not
    exist.  On Windows the path has backslashes only: Metashape gives
    sys.prefix with forward slashes."""
    if not prefix:
        return None
    if platform.startswith("win"):
        candidate = ntpath.normpath(ntpath.join(prefix, "python.exe"))
    else:
        candidate = posixpath.join(prefix, "bin", "python{}.{}".format(pythonVersion[0], pythonVersion[1]))
    return candidate if isFile(candidate) else None


def NumpyInstallCommand(interpreterPath):
    """The command that installs NumPy into the Python of *interpreterPath*,
    for the Command Prompt of Windows and the shells of macOS and Linux."""
    return '"{}" -m pip install numpy'.format(interpreterPath)


def NumpyInstallCommands(platform, interpreterPath):
    """[(shell, command)] that install NumPy into the Python of
    *interpreterPath*: on Windows one line for the Command Prompt and one
    for PowerShell, which needs the call operator & before a quoted path
    (without it, the command fails in PowerShell with "Unexpected token
    '-m'"), elsewhere one line for the terminal, and none without an
    interpreter."""
    if interpreterPath is None:
        return []
    command = NumpyInstallCommand(interpreterPath)
    if platform.startswith("win"):
        return [("Command Prompt", command), ("PowerShell", "& " + command)]
    return [("Terminal", command)]


def NumpyMissingTexts(platform, interpreterPath):
    """(explanation, [(shell, command)]) of the note and the message when
    NumPy is missing (NumpyInstallCommands), the list empty when the
    interpreter is not known."""
    explanation = ("Normalised greyscale needs NumPy. Metashape runs scripts with its own Python installation, "
                   "which does not include NumPy. The 32-bit float export needs no NumPy.")
    commands = NumpyInstallCommands(platform, interpreterPath)
    if not commands:
        return (explanation + " To install NumPy, follow the article \u201c{}\u201d of the Agisoft knowledge base "
                "with numpy as the module, then restart Metashape.".format(NUMPY_INSTALL_ARTICLE)), []
    if platform.startswith("win"):
        instruction = (" To install NumPy, open a Command Prompt or Windows PowerShell as administrator, run the "
                       "command for it, then restart Metashape:")
    elif platform == "darwin":
        instruction = " To install NumPy, run this command in Terminal, then restart Metashape:"
    else:
        instruction = " To install NumPy, run this command in a terminal, then restart Metashape:"
    return explanation + instruction, commands


def NumpyCommandsMessageText(commands):
    """The commands for the message box, each under its shell: "Command
    Prompt:" and the command on the next line."""
    return "\n\n".join("{}:\n{}".format(shell, command) for shell, command in commands)


def NumpyCommandsConsoleLines(commands):
    """The commands for the Console, each after its shell on a line of its
    own, so that copying one does not take the other with it."""
    return ["{}: {}".format(shell, command) for shell, command in commands]


def CurrentNumpyMissingTexts():
    """NumpyMissingTexts for the Python that runs MetaFOCUS."""
    return NumpyMissingTexts(sys.platform, MetashapePythonInterpreter(sys.prefix, sys.platform, sys.version_info))


# ===================================================================
# Menu metadata
# ===================================================================

# A single "&" in a menu entry marks the shortcut key in Qt, so the entry
# writes "&&" to show "&".
_MENU_LABEL = ("MetaFOCUS/Surface-Derived Project Outputs/"
               "Depth && Render Export")
_ICON_NAME = "depth_render_export_32.png"


# ===================================================================
# Introductions of the two tabs
# ===================================================================

DEPTH_MAPS_INTRODUCTION_TEXT = (
    "Save the depth maps that Metashape computed for the images "
    "during depth map generation, for all images or for the selected "
    "ones. The tab saves level 0 of each depth map alone, the most detailed "
    "of the levels that Metashape keeps. It writes 32-bit float depth "
    "multiplied by the chunk scale (metres in a scaled chunk) or "
    "normalised 8-bit or 16-bit greyscale. Metashape\u2019s own Export "
    "Depth\u2026 (context menu of an image) needs a triangular mesh, which "
    "indicates that its depth map comes from the mesh, as in the Render "
    "from triangular mesh tab, and not from depth map generation.")

# The level of a depth map that the Depth maps tab exports.  Metashape keeps
# a depth map in several levels: DepthMap.image(level) and
# getCalibration(level=0) take a "Level index" (Metashape Python
# Reference), "zero level is the most detailed" (PolarNick239 in issue 28,
# "Export all depth map levels", of Agisoft's metashape-scripts repository
# on GitHub), and the level of setImage "is equal to zero by default, but
# there can be multiple levels (typically three)" (PolarNick on the Agisoft
# forum, 13 April 2022).  The photo view shows the levels one by one,
# numbered from 1, or all of them (All Levels and Level 1 to Level 3), and
# no source says which of these is level 0.  The tab asks for level 0 by its
# number, with the calibration of that level.  Without a level,
# DepthMap.image() does not give level 0 alone: in checks in Metashape 2.3.2
# it filled pixels without depth at level 0 with depths of level 1, so that
# a row [1, 0, 0, 4] of level 0 became [1, 7.5, 12.5, 4], and 1,458 pixels
# of a depth map of 200 x 150 pixels gained a depth.  The same checks found
# level 0 with 200 x 150 pixels and level 1 with 100 x 75, so level 0 is
# the most detailed there too.
DEPTH_MAP_LEVEL = 0
DEPTH_MAP_LEVEL_NOTE = ("Level 0 alone, in the numbering of Metashape's Python API (DepthMap.image(level) and "
                        "getCalibration(level)), the most detailed level. Without a level, DepthMap.image() also "
                        "fills pixels without depth at level 0 with depths of coarser levels, which this export "
                        "does not. Metashape's photo view shows the levels of a depth map one by one, numbered "
                        "from 1, or all together, and the Metashape documentation does not say which of them is "
                        "level 0.")


# DepthMap.image(level) gives None for a level that Metashape does not give:
# in Metashape 2.3.2 it did so for levels 2 to 5 of depth maps with levels 0
# and 1.  When level 0 gives None, the Console says so, and nothing is
# written or removed for that image.


class MissingDepthMapLevelError(Exception):
    """DepthMap.image(level) gave None: Metashape gave no image of that
    level of the depth map (MissingDepthMapLevelText)."""


def MissingDepthMapLevelText(level):
    """What the Console says of a depth map of which Metashape gives no
    image of *level*, without a full stop, like the other failure lines
    of the export."""
    return "Metashape gave no image of level {0} of its depth map (DepthMap.image({0}) returned None)".format(level)


def DepthMapImageOfLevel(depthMap, level):
    """The image of *level* of *depthMap* (DepthMap.image(level)).
    Raises a MissingDepthMapLevelError when Metashape gives none."""
    depthImage = depthMap.image(level)
    if depthImage is None:
        raise MissingDepthMapLevelError(MissingDepthMapLevelText(level))
    return depthImage


def DescribeDepthMapFailure(error):
    """The reason in the Console line of a depth map that could not be
    exported: the text of a MissingDepthMapLevelError, which says what
    happened, and "Type: message" of any other exception
    (DescribeException)."""
    if isinstance(error, MissingDepthMapLevelError):
        return str(error)
    return DescribeException(error)


# Model.renderImage, renderDepth, and renderMask return an image.  A return
# of None has not been observed in Metashape 2.3.2.  Each render is checked
# before anything is removed or written for it, as the Depth maps tab does
# for a depth map without level 0.

# What Metashape gives none of, by the method of Model that renders it.
_RENDER_KIND_BY_METHOD = {"renderImage": "rendered image", "renderDepth": "depth image", "renderMask": "mask"}


class MissingRenderError(Exception):
    """Model.renderImage, renderDepth, or renderMask gave None: Metashape
    gave no render of that kind for the viewpoint (MissingRenderText)."""


def MissingRenderText(methodName):
    """What the Console says of a viewpoint for which Model.<methodName>
    gives no render, without a full stop, like the other failure lines of
    the export."""
    return "Metashape gave no {} (Model.{} returned None)".format(_RENDER_KIND_BY_METHOD[methodName], methodName)


def CheckedRender(render, methodName):
    """*render*, what Model.<methodName> returned for a viewpoint.
    Raises a MissingRenderError when Metashape gave none."""
    if render is None:
        raise MissingRenderError(MissingRenderText(methodName))
    return render


def DescribeRenderFailure(error):
    """The reason in the Console line of a viewpoint that could not be
    rendered: the text of a MissingRenderError, which says what happened,
    and "Type: message" of any other exception (DescribeException)."""
    if isinstance(error, MissingRenderError):
        return str(error)
    return DescribeException(error)


MESH_RENDER_INTRODUCTION_TEXT = (
    "Render the triangular mesh from the viewpoint of each oriented "
    "image or of each keyframe of the animation track, producing a "
    "rendered image, a depth image, and a mask, in any combination. "
    "Only the keyframes are rendered, not the interpolated frames of "
    "an animation movie. Metashape\u2019s own Export Depth\u2026 "
    "(context menu of an image) also needs the triangular mesh and "
    "exports a diffuse map, a depth map, and a normal map of an image.")


# ===================================================================
# Helpers
# ===================================================================

def ComputePrincipalDistancePixels(principalDistanceMillimetres,
                                   sensorWidthMillimetres, pixelColumnCount):
    """Principal distance in pixels of a virtual frame camera: the
    principal distance in millimetres divided by the pitch of its virtual
    photosites (the sensor width divided by the number of virtual
    photosite columns)."""
    return principalDistanceMillimetres / (sensorWidthMillimetres / pixelColumnCount)


def BuildVirtualCalibration(pixelColumnCount, pixelRowCount, principalDistanceMillimetres,
                            sensorWidthMillimetres):
    """Metashape.Calibration of the virtual camera: a frame camera with
    the given numbers of virtual photosite columns and rows (so that its
    rendered images have as many pixel columns and rows), the principal distance
    in pixels from ComputePrincipalDistancePixels, the principal point
    at the image centre (cx = cy = 0), and all lens distortion
    coefficients and the affinity and non-orthogonality terms b1 and b2
    set to zero by SetLensDistortionAndAffinityToZero (defined as zero,
    not left to the defaults of the host)."""
    calibration = Metashape.Calibration()
    calibration.width = pixelColumnCount
    calibration.height = pixelRowCount
    calibration.f = ComputePrincipalDistancePixels(
        principalDistanceMillimetres, sensorWidthMillimetres, pixelColumnCount)
    calibration.cx = 0.0
    calibration.cy = 0.0
    SetLensDistortionAndAffinityToZero(calibration)
    calibration.type = Metashape.Sensor.Type.Frame
    return calibration


def RenderedDepthUnitsNote(chunkScale):
    """Console note on the units of rendered depth images: Model.renderDepth
    returns depth in internal chunk coordinate units, which are written
    unchanged (a documented decision), and *chunkScale*, the physical
    chunk scale (PhysicalChunkScale, None for a chunk without a physical
    scale), converts them to metres.  World lengths of a scaled chunk
    are metres whatever the unit of the chunk CRS (degrees, feet): only
    CoordinateSystem.project gives coordinates in that unit."""
    if chunkScale is None:
        return ("Rendered depth values are in internal chunk units. The "
                "chunk has no physical scale, so they cannot be converted to "
                "metres.")
    return ("Rendered depth values are in internal chunk units. Multiply "
            "them by the chunk scale {:.9g} to convert them to "
            "metres.".format(chunkScale))


def _SafeName(label):
    """Turn an image or keyframe label into a file-system-safe base name
    ("camera" for an empty label)."""
    safeCharacters = []
    for character in str(label):
        if character.isalnum() or character in ("-", "_", ".", " "):
            safeCharacters.append(character)
        else:
            safeCharacters.append("_")
    result = "".join(safeCharacters).strip().rstrip(".")
    return result or "camera"


# Output formats of the Depth maps tab, in the order of the Format
# combo box items.
DEPTH_MAP_OUTPUT_FORMATS = ("float32", "uint16", "uint8")


def DepthMapOutputFileName(baseName, outputFormat):
    """File name of a depth map exported by the Depth maps tab in
    *outputFormat* (one of DEPTH_MAP_OUTPUT_FORMATS).  The names never
    coincide with those of the Render from triangular mesh tab, so
    both exports can share a folder."""
    if outputFormat == "float32":
        return baseName + "_depthmap32.tif"
    if outputFormat == "uint16":
        return baseName + "_depthmap16.tif"
    return baseName + "_depthmap8.png"


def MeshRenderOutputFileName(baseName, outputKind, imageExtension):
    """File name of one output of the Render from triangular mesh tab:
    the rendered image ("image"), depth ("depth"), or mask ("mask")."""
    if outputKind == "image":
        return baseName + "_render" + imageExtension
    if outputKind == "depth":
        return baseName + "_render_depth.tif"
    return baseName + "_render_mask.png"


def UniqueOutputBaseNames(labels, keys):
    """Filesystem-safe base names for images with the given labels and
    Metashape keys, one per image, in the same order.  A name that
    occurs more than once (ignoring case, as Windows does) gets the
    image key appended, so images with the same label do not
    overwrite each other's files.  The key is appended again while the
    result still equals another name (an image labelled IMG_0001_1
    next to two images labelled IMG_0001), so no two images ever share
    a base name."""
    safeNames = [_SafeName(label) for label in labels]
    nameCounts = collections.Counter(safeName.lower() for safeName in safeNames)
    usedNames = {safeName.lower() for safeName in safeNames
                 if nameCounts[safeName.lower()] == 1}
    baseNames = []
    for safeName, key in zip(safeNames, keys):
        if nameCounts[safeName.lower()] == 1:
            baseNames.append(safeName)
            continue
        candidateName = "{}_{}".format(safeName, key)
        while candidateName.lower() in usedNames:
            candidateName += "_{}".format(key)
        usedNames.add(candidateName.lower())
        baseNames.append(candidateName)
    return baseNames


def OutputBaseNamesByKey(cameras, regularCameraType):
    """Base names of the regular images among *cameras* (all images of
    the chunk), keyed by their Metashape key.  The names are made unique
    over all regular images of the chunk, not over the images of one
    export, so an image keeps the same base name in every export and in
    both tabs."""
    regularCameras = [camera for camera in cameras
                      if camera.type == regularCameraType]
    keys = [camera.key for camera in regularCameras]
    return dict(zip(keys, UniqueOutputBaseNames(
        [camera.label for camera in regularCameras], keys)))


def RenderBaseName(index, camera, useKeyframes, baseNameByKey):
    """Base name of the files of one viewpoint of the Render from
    triangular mesh tab: the base name of the image (OutputBaseNamesByKey)
    or, for the *index*-th keyframe (from 0), its number and label."""
    if useKeyframes:
        return "{:04d}_{}".format(index + 1, _SafeName(camera.label))
    return baseNameByKey[camera.key]


def OutputFolderConsoleLine(outputFolder):
    """The Console line of the folder of an export or a render."""
    return "   Folder: {}".format(outputFolder)


RENDER_OUTPUT_NAMES = {"image": "rendered image", "depth": "depth", "mask": "mask"}


def RenderOptionsConsoleLine(outputKinds, imageFormat, cullBackFaces, addAlphaChannel, cameraText):
    """The Console line of the options of a render: the outputs (the
    rendered image with its format), the back faces, the alpha channel,
    and the camera (*cameraText*)."""
    outputs = [RENDER_OUTPUT_NAMES[outputKind] + (" ({})".format(imageFormat) if outputKind == "image" else "")
               for outputKind in outputKinds]
    return "   Outputs: {}, back faces {}, {}, camera: {}".format(
        ", ".join(outputs), "culled" if cullBackFaces else "kept", "alpha channel" if addAlphaChannel
        else "no alpha channel", cameraText)


def ExistingTargetPaths(paths):
    """The paths of *paths* that already exist, in their order."""
    return [path for path in paths if os.path.exists(path)]


def ExistingFilesText(existingPaths, targetCount):
    """"the file IMG_1_depthmap32.tif already exists" when the export
    writes one file, "1 of the 3 files to be written,
    IMG_1_depthmap32.tif, already exists", or "2 of the 3 files to be
    written already exist"."""
    if len(existingPaths) == 1 and targetCount == 1:
        return "the file {} already exists".format(os.path.basename(existingPaths[0]))
    if len(existingPaths) == 1:
        return "1 of the {} files to be written, {}, already exists".format(
            targetCount, os.path.basename(existingPaths[0]))
    return "{} of the {} to be written already exist".format(
        len(existingPaths), FormatCountWithNoun(targetCount, "file", "files"))


def RemovedCalibrationFilesText(existingRemovedPaths):
    """"the calibration file IMG_1_depthmap32.json of a depth image to be
    written already exists" or "2 calibration files of depth images to be
    written already exist"."""
    if len(existingRemovedPaths) == 1:
        return "the calibration file {} of a depth image to be written already exists".format(
            os.path.basename(existingRemovedPaths[0]))
    return "{} calibration files of depth images to be written already exist".format(len(existingRemovedPaths))


def ReplaceExistingFilesQuestion(existingPaths, targetCount, existingRemovedPaths=()):
    """Question before an export replaces the *existingPaths* among its
    *targetCount* files and removes the *existingRemovedPaths*, the
    calibration files next to depth images to be written when it writes
    none (RemoveExistingCalibrationFile)."""
    sentences = []
    if existingPaths:
        existingText = ExistingFilesText(existingPaths, targetCount)
        if len(existingPaths) > 1:
            existingText += " in the chosen folder, for example {}".format(os.path.basename(existingPaths[0]))
        else:
            existingText += " in the chosen folder"
        sentences.append(existingText[0].upper() + existingText[1:] + ".")
    if existingRemovedPaths:
        removedText = RemovedCalibrationFilesText(existingRemovedPaths) + " in the chosen folder"
        if len(existingRemovedPaths) > 1:
            removedText += ", for example {}".format(os.path.basename(existingRemovedPaths[0]))
        singleFile = len(existingRemovedPaths) == 1
        sentences.append("{}. The option Write a calibration file is off, so {} would be removed, because {} would "
                         "not describe the new depth {}.".format(removedText[0].upper() + removedText[1:],
                                                    "it" if singleFile else "they", "it" if singleFile else "they",
                                                    "image" if singleFile else "images"))
    fileCount = len(existingPaths) + len(existingRemovedPaths)
    if existingPaths and existingRemovedPaths:
        action = "Replace and remove them?"
    else:
        action = "{} {}?".format("Replace" if existingPaths else "Remove", "it" if fileCount == 1 else "them")
    return ("{}\n\n{} Such files can belong to another chunk or to another export, because the names are unique "
            "only among the images of the active chunk.").format(" ".join(sentences), action)


def ConfirmReplacingExistingFiles(parent, targetPaths, removedPaths=()):
    """True when none of *targetPaths* and *removedPaths* exists, or when
    the user agrees to replace those of *targetPaths* and to remove those
    of *removedPaths* that do.  Image.save replaces a file without asking,
    so an export of another chunk, or a later export, into the same folder
    could otherwise replace the files of different images.  *removedPaths*
    are the calibration files next to the depth images to be written when
    the export writes none.  The Console records the answer."""
    existingPaths = ExistingTargetPaths(targetPaths)
    existingRemovedPaths = ExistingTargetPaths(removedPaths)
    if not existingPaths and not existingRemovedPaths:
        return True
    reply = QtWidgets.QMessageBox.question(
        parent, "Depth & Render Export",
        ReplaceExistingFilesQuestion(existingPaths, len(targetPaths), existingRemovedPaths),
        QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No, QtWidgets.QMessageBox.No)
    folder = os.path.dirname((existingPaths or existingRemovedPaths)[0])
    texts, actions = [], []
    if existingPaths:
        texts.append(ExistingFilesText(existingPaths, len(targetPaths)))
        actions.append("replacing")
    if existingRemovedPaths:
        texts.append(RemovedCalibrationFilesText(existingRemovedPaths))
        actions.append("removing")
    if reply != QtWidgets.QMessageBox.Yes:
        print("[Depth & Render Export] Nothing exported: {} in {}, and {} {} declined.".format(
            " and ".join(texts), folder, " and ".join(actions), "was" if len(actions) == 1 else "were"))
        return False
    if existingPaths:
        print("[Depth & Render Export] {} in {} and {} replaced, as confirmed.".format(
            texts[0], folder, "is" if len(existingPaths) == 1 else "are"))
    if existingRemovedPaths:
        print("[Depth & Render Export] {} in {} and {} removed, as confirmed.".format(
            texts[-1], folder, "is" if len(existingRemovedPaths) == 1 else "are"))
    return True


def SelectDepthMapCameras(cameras, depthMapCameras, selectedOnly,
                          regularCameraType):
    """The cameras of *cameras* that are of *regularCameraType*, have a
    depth map (are among *depthMapCameras*), and, with *selectedOnly*,
    are selected.  Cameras are matched by their Metashape key, because
    two wrapper objects of one camera need not hash alike."""
    depthMapKeys = {camera.key for camera in depthMapCameras}
    selectedCameras = []
    for camera in cameras:
        if camera.type != regularCameraType:
            continue
        if camera.key not in depthMapKeys:
            continue
        if selectedOnly and not camera.selected:
            continue
        selectedCameras.append(camera)
    return selectedCameras


def SelectedRegularImages(chunk):
    """The images of *chunk* of the Regular type (the slave images of a rig
    included, the keyframes of an animation track not) that Metashape
    reports as selected (Camera.selected): the images that both tabs take
    with their option for the selected images."""
    regularType = Metashape.Camera.Type.Regular
    return [camera for camera in getattr(chunk, "cameras", []) or []
            if camera.type == regularType and getattr(camera, "selected", False)]


def DepthMapKeys(chunk):
    """The keys of the images of *chunk* that have a depth map, or an
    empty set when the chunk has no depth maps."""
    depthMaps = getattr(chunk, "depth_maps", None)
    if depthMaps is None:
        return set()
    try:
        return {camera.key for camera in depthMaps.keys()}
    except Exception as listingError:
        ReportSuppressedError("depthRenderExporter.DepthMapKeys", listingError)
        return set()


def ShowSelectionStatus(tab, statusText):
    """Put *statusText* into the selection line of *tab*, which it has once
    it is built."""
    label = getattr(tab, "selectionStatusLabel", None)
    if label is not None:
        label.setText(statusText)


def DepthMapSelectionStatusText(chunk):
    """The line below Selected images only of the Depth maps tab: how many
    images Metashape reports as selected (SelectedRegularImages) and how
    many of them have a depth map, or that there is no active chunk."""
    if chunk is None:
        return "No active chunk."
    selectedImages = SelectedRegularImages(chunk)
    depthMapKeys = DepthMapKeys(chunk)
    return SelectedImagesStatusText(len(selectedImages),
                                    sum(1 for camera in selectedImages if camera.key in depthMapKeys),
                                    "with a depth map", "without a depth map")


def RenderSelectionStatusText(chunk):
    """The line below Selected images only of the tab Render from
    triangular mesh: how many images Metashape reports as selected
    (SelectedRegularImages) and how many of them are oriented, or that
    there is no active chunk."""
    if chunk is None:
        return "No active chunk."
    selectedImages = SelectedRegularImages(chunk)
    return SelectedImagesStatusText(len(selectedImages),
                                    sum(1 for camera in selectedImages if camera.transform is not None))


def NormaliseDepthValuesToGreyLevels(depthValues, invertGreyscale,
                                     maximumGreyLevel):
    """Grey levels for depth values stretched to their own range.
    Pixels without depth (zero, negative, or not finite) get level 0,
    valid depths levels 1 to *maximumGreyLevel*, so no valid depth
    shares the no-data level.  With *invertGreyscale*, nearer depths
    are brighter."""
    # float32 keeps depth maps with many pixels small and is exact for
    # every 8 and 16-bit grey level.
    depthValues = numpy.asarray(depthValues, dtype=numpy.float32)
    validPixelMask = numpy.isfinite(depthValues) & (depthValues > 0)
    greyLevels = numpy.zeros(depthValues.shape, dtype=numpy.float32)
    validRange = ValidDepthRange(depthValues)
    if validRange is None:
        return greyLevels
    validDepths = depthValues[validPixelMask]
    minimumDepth, maximumDepth = validRange
    depthRange = maximumDepth - minimumDepth if maximumDepth > minimumDepth else 1.0
    fractions = (validDepths - minimumDepth) / depthRange
    if invertGreyscale:
        fractions = 1.0 - fractions
    greyLevels[validPixelMask] = 1.0 + numpy.rint(fractions * (maximumGreyLevel - 1))
    return greyLevels


# The single-channel greyscale files of the normalised export, which
# MetaFOCUS writes itself: Metashape.Image with the layout "RGB", the one
# documented for it, would give files with three equal channels.
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
# The filter type Sub of the PNG standard: each byte minus the byte of the
# pixel to its left, which suits the smooth levels of a depth map.
PNG_FILTER_SUB = 1
# TIFF field types and the tags of a single-channel greyscale image.
TIFF_SHORT, TIFF_LONG, TIFF_RATIONAL = 3, 4, 5
TIFF_COMPRESSION_DEFLATE = 8
TIFF_PREDICTOR_HORIZONTAL = 2
# Resolution unit 1: no absolute unit, because an image has a pixel count,
# not a size.
TIFF_RESOLUTION_UNIT_NONE = 1


def _PngChunk(chunkType, data):
    """One chunk of a PNG file: length, type, data, and CRC."""
    return (struct.pack(">I", len(data)) + chunkType + data
            + struct.pack(">I", zlib.crc32(chunkType + data) & 0xFFFFFFFF))


def GreyscalePngBytes(levels, width, height, bitDepth):
    """A single-channel greyscale PNG (colour type 0) of *levels*, the grey
    levels of *width* x *height* pixels row by row, with *bitDepth* 8 or
    16 bits per sample, every row with the filter Sub, compressed with
    zlib."""
    sampleBytes = bitDepth // 8
    sampleType = ">u2" if bitDepth == 16 else "u1"
    rows = numpy.ascontiguousarray(numpy.asarray(levels).reshape(height, width).astype(sampleType))
    rowBytes = rows.view(numpy.uint8).reshape(height, width * sampleBytes)
    filtered = numpy.empty((height, width * sampleBytes + 1), dtype=numpy.uint8)
    filtered[:, 0] = PNG_FILTER_SUB
    filtered[:, 1:1 + sampleBytes] = rowBytes[:, :sampleBytes]
    # Unsigned bytes wrap around, as the filter requires.
    filtered[:, 1 + sampleBytes:] = rowBytes[:, sampleBytes:] - rowBytes[:, :-sampleBytes]
    header = struct.pack(">IIBBBBB", width, height, bitDepth, 0, 0, 0, 0)
    return (PNG_SIGNATURE + _PngChunk(b"IHDR", header)
            + _PngChunk(b"IDAT", zlib.compress(filtered.tobytes(), 6)) + _PngChunk(b"IEND", b""))


def _TiffEntry(tag, fieldType, value):
    """One entry of a TIFF directory with one value of *fieldType*: a SHORT
    or a LONG in the entry itself, a RATIONAL by the offset of its two
    LONGs."""
    if fieldType == TIFF_SHORT:
        return struct.pack("<HHIHH", tag, fieldType, 1, value, 0)
    return struct.pack("<HHII", tag, fieldType, 1, value)


def GreyscaleTiffBytes(levels, width, height, bitDepth):
    """A little-endian single-channel greyscale TIFF (BlackIsZero) of
    *levels*, the grey levels of *width* x *height* pixels row by row,
    with *bitDepth* 8 or 16 bits per unsigned sample, in one strip
    compressed with Deflate after horizontal differencing (Predictor 2),
    and a resolution without an absolute unit."""
    sampleType = "<u2" if bitDepth == 16 else "u1"
    rows = numpy.ascontiguousarray(numpy.asarray(levels).reshape(height, width).astype(sampleType))
    differences = rows.copy()
    # Unsigned samples wrap around, as the predictor requires.
    differences[:, 1:] = rows[:, 1:] - rows[:, :-1]
    strip = zlib.compress(differences.tobytes(), 6)
    entryCount = 15
    directoryOffset = 8
    rationalOffset = directoryOffset + 2 + 12 * entryCount + 4
    stripOffset = rationalOffset + 16
    entries = [
        _TiffEntry(256, TIFF_LONG, width),                        # ImageWidth
        _TiffEntry(257, TIFF_LONG, height),                       # ImageLength
        _TiffEntry(258, TIFF_SHORT, bitDepth),                    # BitsPerSample
        _TiffEntry(259, TIFF_SHORT, TIFF_COMPRESSION_DEFLATE),    # Compression
        _TiffEntry(262, TIFF_SHORT, 1),                           # PhotometricInterpretation: BlackIsZero
        _TiffEntry(273, TIFF_LONG, stripOffset),                  # StripOffsets
        _TiffEntry(277, TIFF_SHORT, 1),                           # SamplesPerPixel
        _TiffEntry(278, TIFF_LONG, height),                       # RowsPerStrip
        _TiffEntry(279, TIFF_LONG, len(strip)),                   # StripByteCounts
        _TiffEntry(282, TIFF_RATIONAL, rationalOffset),           # XResolution
        _TiffEntry(283, TIFF_RATIONAL, rationalOffset + 8),       # YResolution
        _TiffEntry(284, TIFF_SHORT, 1),                           # PlanarConfiguration: chunky
        _TiffEntry(296, TIFF_SHORT, TIFF_RESOLUTION_UNIT_NONE),   # ResolutionUnit
        _TiffEntry(317, TIFF_SHORT, TIFF_PREDICTOR_HORIZONTAL),   # Predictor
        _TiffEntry(339, TIFF_SHORT, 1)]                           # SampleFormat: unsigned integer
    assert len(entries) == entryCount
    return (b"II" + struct.pack("<HI", 42, directoryOffset)
            + struct.pack("<H", entryCount) + b"".join(entries) + struct.pack("<I", 0)
            + struct.pack("<IIII", 1, 1, 1, 1) + strip)


def ValidDepthRange(depthValues):
    """(minimum, maximum) of the valid depths among *depthValues* (positive
    finite numbers, taken as float32 as NormaliseDepthValuesToGreyLevels
    takes them), or None when there is no valid depth.  The calibration
    file of a normalised export records them, so that the depths of its
    grey levels can be recovered."""
    depthValues = numpy.asarray(depthValues, dtype=numpy.float32)
    validPixelMask = numpy.isfinite(depthValues) & (depthValues > 0)
    if not numpy.any(validPixelMask):
        return None
    validDepths = depthValues[validPixelMask]
    return float(validDepths.min()), float(validDepths.max())


# ===================================================================
# Calibration files
# ===================================================================

# Version of the layout of the calibration files: a later layout that
# renames or removes a key gets a higher number.
DEPTH_CALIBRATION_FILE_VERSION = 1

# Coefficients of a Metashape.Calibration besides c_pix, cx, and cy.
CALIBRATION_COEFFICIENT_NAMES = ("b1", "b2", "k1", "k2", "k3", "k4", "p1", "p2", "p3", "p4")

# Kinds of CRS (CrsKind) whose chunk transform leads to
# geocentric coordinates in metres when the chunk is georeferenced.
GEOCENTRIC_COORDINATE_SYSTEM_KINDS = ("geographic", "projected", "geocentric")

DEPTH_MAP_SOURCE_TEXT = "depth map of depth map generation (chunk.depth_maps)"
RENDERED_DEPTH_SOURCE_TEXT = "rendered from the triangular mesh (Model.renderDepth)"
DEPTH_MAP_QUANTITY_TEXT = (
    "the depth of the stored depth map: the Metashape 2.3 manual does not say whether it is the depth along the "
    "camera axis or the slant distance from the perspective centre, and a check in Metashape 2.3.2 indicates the "
    "depth along the camera axis for a frame camera (other camera models have not been checked)")
RENDERED_DEPTH_QUANTITY_TEXT = "depth along the camera axis"
INTERNAL_CHUNK_UNIT_TEXT = "internal chunk unit"
GREY_LEVEL_UNIT_TEXT = "grey level (depth.grey_levels gives the depths)"

PIXEL_COORDINATES_TEXT = (
    "(0, 0) is the top-left corner of the image, so the pixel in column i and row j, counted from 0 at the left and "
    "the top, lies at (i + 0.5, j + 0.5), as in Metashape")

CAMERA_COORDINATES_TEXT = (
    "x to the right, y down, and z along the camera axis, away from the camera, in internal chunk units")

MATRIX_LAYOUT_TEXT = (
    "Each transform is written as four rows of four numbers and applies to points as column vectors: the point (X, "
    "Y, Z) goes to (X', Y', Z'), with (X', Y', Z', 1) = M (X, Y, Z, 1) and M the matrix of the four rows.")

FRAME_CAMERA_EQUATIONS_TEXT = (
    "Frame camera of Metashape (Metashape 2.3 manual, Appendix D): a point (X, Y, Z) in camera coordinates has x = "
    "X / Z, y = Y / Z, r^2 = x^2 + y^2, x' = x (1 + k1 r^2 + k2 r^4 + k3 r^6 + k4 r^8) + (p1 (r^2 + 2 x^2) + 2 p2 x y) "
    "(1 + p3 r^2 + p4 r^4) and y' = y (1 + k1 r^2 + k2 r^4 + k3 r^6 + k4 r^8) + (p2 (r^2 + 2 y^2) + 2 p1 x y) (1 + "
    "p3 r^2 + p4 r^4), and the pixel coordinates u = pixel_columns / 2 + cx + x' (c_pix + b1) + y' b2 and v = "
    "pixel_rows / 2 + cy + y' c_pix. For the other camera models of Metashape, see the same appendix. The equations "
    "leave out additional corrections (see additional_corrections).")

ADDITIONAL_CORRECTIONS_NOTE_TEXT = (
    "The equations leave the additional corrections out, so that the image position of a ray computed with the "
    "equations alone can differ from that of Metashape by up to the size of the corrections, which the Corrections "
    "mode of the Interior Orientation Viewer shows. Agisoft does not document their model, and the Metashape Python "
    "reference does not say whether Calibration.unproject applies them.")

RENDERED_DEPTH_DISTORTION_NOTE_TEXT = (
    "The Metashape Python reference does not say whether Model.renderDepth applies the lens distortion of the "
    "calibration exactly at every pixel position, so that, where the distortion is large, the depth of a pixel need "
    "not belong exactly to the ray through its pixel position.")

DEPTH_OF_A_LEVEL_TEXT = (
    "depth = minimum_depth + t (maximum_depth - minimum_depth), with t = (level - lowest_level) / (highest_level - "
    "lowest_level), or t = 1 - (level - lowest_level) / (highest_level - lowest_level) when inverted. The export "
    "rounds to the nearest level, so that a depth recovered from a level differs from the exported depth by at "
    "most half a level step, (maximum_depth - minimum_depth) / (2 (highest_level - lowest_level)).")


def DepthCalibrationFileName(depthFileName):
    """Name of the calibration file of the depth image *depthFileName*:
    the same name with the extension .json, which no other file of the
    two tabs has."""
    return os.path.splitext(depthFileName)[0] + ".json"


def FiniteNumberOrNone(value):
    """*value* as a float when it is a finite number, otherwise None (JSON
    has no NaN or infinity)."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def WholeNumberOrNone(value):
    """*value* as an int when it is a whole finite number, otherwise None."""
    number = FiniteNumberOrNone(value)
    return int(number) if number is not None and number == int(number) else None


def _SensorTypeName(calibrationType):
    """The name of a Sensor.Type (its name, or the last part of its text)."""
    name = getattr(calibrationType, "name", None)
    return str(name) if name else str(calibrationType).rsplit(".", 1)[-1]


def IsFrameCalibration(calibration):
    """True when the type of *calibration* is Metashape.Sensor.Type.Frame,
    compared with the member as the Interior Orientation Viewer compares
    it (CalibrationModel), or, when Metashape has no such member, named
    Frame."""
    calibrationType = getattr(calibration, "type", None)
    if calibrationType is None:
        return False
    frameType = getattr(getattr(getattr(Metashape, "Sensor", None), "Type", None), "Frame", None)
    if frameType is not None and calibrationType == frameType:
        return True
    return _SensorTypeName(calibrationType) == "Frame"


def CameraModelName(calibration):
    """Name of the camera model of *calibration*: Frame for a frame camera
    (IsFrameCalibration), otherwise the last part of the name of its
    Sensor.Type (for example Fisheye), or None without a type."""
    calibrationType = getattr(calibration, "type", None)
    if calibrationType is None:
        return None
    return "Frame" if IsFrameCalibration(calibration) else _SensorTypeName(calibrationType)


def CameraModelNote(modelName):
    """None for the frame camera, otherwise what the calibration file has
    not checked for the camera model *modelName* (None when the model
    could not be read)."""
    if modelName == "Frame":
        return None
    if modelName is None:
        return ("The camera model of the calibration could not be read, so the file cannot say whether the equations "
                "of the frame camera apply. The steps of three_d_point have been checked for the frame camera only.")
    return ("The equations are those of the frame camera, but the camera model is {}, whose ray follows the "
            "equations of that model (Metashape 2.3 manual, Appendix D). The steps of three_d_point have been "
            "checked for the frame camera only, and a depth along the camera axis cannot describe a ray 90 degrees "
            "or more from the camera axis.").format(modelName)


def CalibrationXmlText(calibration):
    """(text, None) of the calibration file that Metashape writes itself
    (Calibration.save, XML), which also holds additional corrections, or
    (None, the reason in words) when it cannot be written or read.  It is
    written to a temporary folder, which is removed with everything in
    it."""
    temporaryFolder = tempfile.mkdtemp(prefix="MetaFOCUS_calibration_")
    try:
        calibrationPath = os.path.join(temporaryFolder, "calibration.xml")
        calibration.save(calibrationPath)
        with open(calibrationPath, encoding="utf-8") as calibrationFile:
            return calibrationFile.read(), None
    except Exception as savingError:
        return None, "Metashape's calibration XML could not be written or read ({})".format(
            DescribeException(savingError))
    finally:
        shutil.rmtree(temporaryFolder, ignore_errors=True)


def _ElementNumbers(element, path):
    """The numbers of the text of the subelement *path* of *element*, each
    a float or None (FiniteNumberOrNone)."""
    return [FiniteNumberOrNone(text) for text in (element.findtext(path) or "").split()]


def AdditionalCorrectionsRecord(xmlText):
    """Whether Metashape's calibration XML *xmlText* holds additional
    corrections and, when it does, their type, the number of their
    coefficients, and their extent ((minimum x, minimum y) and (maximum x,
    maximum y) in pixel coordinates), read from the elements that the
    Interior Orientation Viewer reads (ReadAdditionalCorrections), with
    the note that the equations of the calibration file leave them out.
    Raises when the XML cannot be parsed."""
    correctionsElement = ElementTree.fromstring(xmlText).find("corrections")
    if correctionsElement is None:
        return {"present": False}
    return {"present": True, "type": correctionsElement.get("type"),
            "coefficient_count": len((correctionsElement.findtext("coeffs") or "").split()),
            "extent": {"minimum": _ElementNumbers(correctionsElement, "extent/min"),
                       "maximum": _ElementNumbers(correctionsElement, "extent/max")},
            "note": ADDITIONAL_CORRECTIONS_NOTE_TEXT}


def CalibrationRecord(calibration, calibrationSource, depthImage):
    """(record, problems): the calibration part of a calibration file,
    with where the calibration comes from, the camera model, the numbers
    of pixel columns and rows, c_pix, cx, cy, and the coefficients (None
    where they are not finite numbers), the equations of the frame camera,
    a note for another camera model, Metashape's own XML and its
    additional corrections, and the problems in words: a camera model that
    cannot be read, no positive finite c_pix, no positive numbers of pixel
    columns and rows, numbers that differ from those of *depthImage*, and
    an XML that cannot be written or read."""
    problems = []
    modelName = CameraModelName(calibration)
    pixelColumnCount = WholeNumberOrNone(getattr(calibration, "width", None))
    pixelRowCount = WholeNumberOrNone(getattr(calibration, "height", None))
    principalDistancePixels = FiniteNumberOrNone(getattr(calibration, "f", None))
    record = {"source": calibrationSource, "camera_model": modelName, "pixel_columns": pixelColumnCount,
              "pixel_rows": pixelRowCount, "c_pix": principalDistancePixels,
              "cx": FiniteNumberOrNone(getattr(calibration, "cx", None)),
              "cy": FiniteNumberOrNone(getattr(calibration, "cy", None))}
    for coefficientName in CALIBRATION_COEFFICIENT_NAMES:
        record[coefficientName] = FiniteNumberOrNone(getattr(calibration, coefficientName, None))
    record["equations"] = FRAME_CAMERA_EQUATIONS_TEXT
    cameraModelNote = CameraModelNote(modelName)
    if cameraModelNote is not None:
        record["camera_model_note"] = cameraModelNote
    xmlText, xmlProblem = CalibrationXmlText(calibration)
    record["metashape_calibration_xml"] = xmlText
    record["additional_corrections"] = None
    if xmlProblem is not None:
        problems.append(xmlProblem + ", so the file does not say whether the calibration has additional corrections")
    else:
        try:
            record["additional_corrections"] = AdditionalCorrectionsRecord(xmlText)
        except Exception as parsingError:
            problems.append("Metashape's calibration XML could not be read ({}), so the file does not say whether the "
                            "calibration has additional corrections".format(DescribeException(parsingError)))
    if modelName is None:
        problems.append("the camera model of the calibration could not be read")
    if principalDistancePixels is None or principalDistancePixels <= 0:
        problems.append("the calibration has no positive principal distance in pixels (c_pix)")
    if pixelColumnCount is None or pixelRowCount is None or pixelColumnCount <= 0 or pixelRowCount <= 0:
        problems.append("the calibration has no positive numbers of pixel columns and rows")
    else:
        depthPixelColumnCount = WholeNumberOrNone(getattr(depthImage, "width", None))
        depthPixelRowCount = WholeNumberOrNone(getattr(depthImage, "height", None))
        if (depthPixelColumnCount is not None and depthPixelRowCount is not None
                and (depthPixelColumnCount, depthPixelRowCount) != (pixelColumnCount, pixelRowCount)):
            problems.append("the calibration has {} pixel columns and {} pixel rows and the depth image {} and {}, so "
                            "the calibration does not describe the depth image".format(
                                pixelColumnCount, pixelRowCount, depthPixelColumnCount, depthPixelRowCount))
    return record, problems


def MatrixRows(matrix):
    """The four rows of a 4 x 4 matrix (TransformMatrixElements) as lists
    of floats, or None."""
    elements = TransformMatrixElements(matrix)
    if elements is None:
        return None
    return [elements[rowIndex * 4:rowIndex * 4 + 4] for rowIndex in range(4)]


def CoordinateSystemRecord(coordinateSystem):
    """Name and well-known text (WKT) of the CRS of the chunk, or
    None."""
    if coordinateSystem is None:
        return None
    name = getattr(coordinateSystem, "name", None)
    wellKnownText = getattr(coordinateSystem, "wkt", None)
    return {"name": str(name) if name else None, "well_known_text": str(wellKnownText) if wellKnownText else None}


def LocalCoordinateSystemUnit(coordinateSystem):
    """(name, metres per unit) of the unit of a local CRS, read from the
    first UNIT element after LOCAL_CS in its well-known text (WKT), or
    None when there is none or its factor is not a positive finite
    number."""
    wellKnownText = str(getattr(coordinateSystem, "wkt", "") or "")
    localStart = wellKnownText.upper().find("LOCAL_CS")
    unitMatch = re.search(r'UNIT\[\s*"([^"]*)"\s*,\s*([-+0-9.eE]+)', wellKnownText[max(localStart, 0):])
    if unitMatch is None:
        return None
    metresPerUnit = FiniteNumberOrNone(unitMatch.group(2))
    if metresPerUnit is None or metresPerUnit <= 0:
        return None
    return unitMatch.group(1), metresPerUnit


def PhysicalLengthUnitText(chunk):
    """Unit of internal chunk lengths multiplied by the physical chunk
    scale: the metre, whatever the unit of the chunk CRS.  In Metashape
    2.3.2 a Metashape scale bar of 10 gave a world distance of 10 also in
    a local CRS in feet, and only CoordinateSystem.project gave 32.808
    feet."""
    return "metre"


def PhysicalChunkScaleNote(chunk, physicalScale):
    """What the physical chunk scale *physicalScale* (None without one)
    gives, for the calibration file."""
    if physicalScale is None:
        return "The chunk has no physical scale ({}), so lengths stay in internal chunk units.".format(
            DescribeMissingPhysicalScale(chunk))
    return "Internal chunk units multiplied by this scale give lengths in this unit: {}.".format(
        PhysicalLengthUnitText(chunk))


def WorldCoordinatesText(chunk, chunkMatrix, chunkTransformRows):
    """What the chunk transform of *chunk* (*chunkMatrix*, read as
    *chunkTransformRows*) leads to: nothing without a transform or when it
    cannot be read, arbitrary local coordinates for the transform that
    Region Tools records for a chunk without a physical scale
    (HasArbitraryRegionToolsScale), local coordinates aligned with the
    region for the axes and the origin that Region Tools gave a chunk with
    a physical scale (HasRegionToolsPlacement), local coordinates for a chunk with a
    geographic, projected, or geocentric CRS without a placement on the
    Earth (IsGeoreferencedChunk), and otherwise what the kind of the
    chunk CRS (CrsKind) implies, geocentric coordinates only as long as
    the transform of a georeferenced chunk is fitted to the reference
    data, and otherwise world coordinates in metres when the chunk has a
    physical scale, which CoordinateSystem.project converts into the
    unit of a local CRS."""
    if chunkMatrix is None:
        return ("The chunk has no transform, so its internal coordinates are the only coordinates that the file "
                "holds.")
    if chunkTransformRows is None:
        return "The chunk transform could not be read, so the file holds internal coordinates only."
    kind = CrsKind(getattr(chunk, "crs", None))
    geocentricMeaninglessText = (" CoordinateSystem.project takes these coordinates as geocentric coordinates on the "
                                 "geodetic datum of the chunk CRS, so the coordinates that it gives are meaningless.")
    if HasArbitraryRegionToolsScale(chunk):
        text = ("The chunk transform is the one that Align Coordinate Axes to Region of Region Tools gave the chunk "
                "without a physical scale, so it leads to arbitrary local coordinates with an arbitrary scale, which "
                "are not georeferenced.")
        if kind in GEOCENTRIC_COORDINATE_SYSTEM_KINDS:
            text += geocentricMeaninglessText
        return text
    if HasRegionToolsPlacement(chunk):
        # Region Tools records its transform also for a chunk with a
        # physical scale.
        text = ("The axes of the world coordinates are those that Align Coordinate Axes to Region of Region Tools "
                "gave the chunk, so the chunk transform leads to local coordinates aligned with the region, in "
                "metres when the chunk has a physical scale (see orientation.physical_chunk_scale_note), which are "
                "not georeferenced.")
        if kind in GEOCENTRIC_COORDINATE_SYSTEM_KINDS:
            text += geocentricMeaninglessText
        return text
    if kind in GEOCENTRIC_COORDINATE_SYSTEM_KINDS and not IsGeoreferencedChunk(chunk):
        # Metashape 2.3.2 sets no translation and no rotation without
        # reference coordinates, also for a chunk scaled by Metashape
        # scale bars.
        return ("The chunk transform has no placement on the Earth (no translation and no rotation from reference "
                "coordinates), so it leads to local coordinates, in metres when the chunk has a physical scale (see "
                "orientation.physical_chunk_scale_note), which are not georeferenced." + geocentricMeaninglessText)
    if kind in GEOCENTRIC_COORDINATE_SYSTEM_KINDS:
        # A chunk with such a CRS and a transform is georeferenced by
        # definition, so what decides is whether the transform is fitted
        # to the reference data.
        return ("The chunk transform leads to geocentric coordinates in metres on the geodetic datum of the chunk "
                "CRS, which Metashape converts into coordinates of that CRS with CoordinateSystem.project, as long "
                "as the transform is fitted to the reference data. A transform that replaced it without being "
                "fitted to them, for example that of Align Coordinate Axes to Region of Region Tools, leads to "
                "coordinates that are not geocentric, although CoordinateSystem.project takes them as such, so the "
                "coordinates that it gives are meaningless.")
    if kind == "local":
        unit = LocalCoordinateSystemUnit(getattr(chunk, "crs", None))
        return ("The chunk transform leads to the world coordinates of the chunk, in metres when the chunk has a "
                "physical scale (see orientation.physical_chunk_scale_note). CoordinateSystem.project converts them "
                "into coordinates of its local CRS, in the unit of that CRS{}.".format(
                    " ({})".format(unit[0]) if unit is not None else ""))
    # The lengths of the world coordinates of a scaled chunk are metres
    # whatever its CRS, so only their datum can be unknown.
    if kind == "none":
        return ("The chunk names no CRS, so the datum of the world coordinates that the chunk transform leads to "
                "is not known. They are in metres when the chunk has a physical scale (see "
                "orientation.physical_chunk_scale_note).")
    return ("The chunk transform leads to the world coordinates of the chunk, in metres when the chunk has a "
            "physical scale (see orientation.physical_chunk_scale_note), in a CRS of a kind that MetaFOCUS does "
            "not recognise (see orientation.coordinate_system).")


def GreyLevelRecord(validRange, maximumGreyLevel, invertGreyscale):
    """The grey-level part of the calibration file of a normalised export:
    the levels and the depths of the lowest and the highest valid level
    (in internal chunk units, as NormaliseDepthValuesToGreyLevels
    stretches them), so that a depth can be recovered from its level."""
    minimumDepth, maximumDepth = validRange if validRange is not None else (None, None)
    return {"bits": 16 if maximumGreyLevel > 255 else 8, "level_without_depth": 0, "lowest_level": 1,
            "highest_level": maximumGreyLevel, "inverted": bool(invertGreyscale),
            "minimum_depth": minimumDepth, "maximum_depth": maximumDepth, "depth_unit": INTERNAL_CHUNK_UNIT_TEXT,
            "depth_of_a_level": DEPTH_OF_A_LEVEL_TEXT if validRange is not None
            else "The depth map holds no valid depth, so every pixel has the grey level 0."}


def RollingShutterCompensationText(modelName):
    """How a note names the rolling-shutter compensation *modelName*
    (RollingShutterModelName): the model, or the switch of older Metashape
    versions ("on")."""
    if modelName == "on":
        return "rolling-shutter compensation switched on (the switch of older Metashape versions)"
    if modelName in ("Regularized", "Full"):
        return "the rolling-shutter model {}".format(modelName)
    return "the rolling-shutter model {}, which MetaFOCUS does not recognise".format(modelName)


def RowMotionText(modelName):
    """What the rolling-shutter model *modelName* gives each image row: the
    Regularized model shifts the camera along x and y in the image plane,
    the Full model also rotates it (Agisoft's support, as the About of
    the Interior Orientation Viewer quotes it), and the switch of older
    versions or a model that is not recognised can do both."""
    if modelName == "Regularized":
        return ("each image row has a perspective centre of its own, shifted along x and y in the image plane, and "
                "the rotation of the image")
    if modelName == "Full":
        return "each image row has a perspective centre and a rotation of its own"
    return "each image row can have a perspective centre and a rotation of its own"


def DepthMapRollingShutterNote(modelName):
    """Rolling-shutter part of the calibration file of a stored depth map,
    or None without compensation."""
    if modelName is None:
        return None
    return {"model": modelName, "note": (
        "The interior orientation group of this image has {}, so that {}. This file holds the static camera "
        "transform only, and whether the stored depth map follows the camera motion during the readout is not "
        "documented, so 3D points computed with this file can be displaced by that motion.").format(
            RollingShutterCompensationText(modelName), RowMotionText(modelName))}


def RenderedDepthRollingShutterNote(modelName):
    """Rolling-shutter part of the calibration file of a rendered depth,
    or None without compensation."""
    if modelName is None:
        return None
    return {"model": modelName, "note": (
        "The interior orientation group of this image has {}, so that {}. Model.renderDepth received the static "
        "camera transform of this file and no camera motion, so the camera motion during the readout is not part of "
        "the rendered depth, and a pixel of the rendered depth need not show the surface point that the same pixel "
        "of the original image shows.").format(RollingShutterCompensationText(modelName), RowMotionText(modelName))}


def RenderedDepthQuantityText(modelName):
    """What the depth rendered with a calibration of the camera model
    *modelName* is: the depth along the camera axis for the frame camera
    (Metashape 2.3.2), and for another model a text that says it has not
    been checked."""
    if modelName == "Frame":
        return RENDERED_DEPTH_QUANTITY_TEXT
    return ("depth along the camera axis for a frame camera (Metashape 2.3.2), not checked for the camera model "
            "{}".format(modelName if modelName else "of this calibration, which could not be read"))


def RenderedPixelsWithoutDepthText(cullBackFaces, addAlphaChannel):
    """The pixels of a rendered depth that have no depth, with the options
    of the render."""
    text = "pixels where the view meets no part of the triangular mesh"
    if cullBackFaces:
        text += " or, with back faces culled, only back faces of it"
    if addAlphaChannel:
        text += ", which have alpha 0 in the alpha channel"
    return text


def ThreeDimensionalPointText(depthAlongTheAxis, normalised):
    """How to compute the 3D point of a pixel from a depth image and its
    calibration file."""
    if normalised:
        steps = ["Take the depth d that the grey level of the pixel stands for (depth.grey_levels), in internal "
                 "chunk units."]
    else:
        steps = ["Divide the value of the pixel by depth.factor_applied to obtain its depth d in internal chunk "
                 "units."]
    steps.append("Find the normalised coordinates (x, y) of the ray through the pixel position (i + 0.5, j + 0.5) "
                 "of the pixel in column i and row j by inverting the equations of the calibration. In Metashape, "
                 "Calibration.unproject gives the ray of a pixel position as a vector (X, Y, Z) in camera "
                 "coordinates, and x = X / Z and y = Y / Z.")
    if depthAlongTheAxis:
        steps.append("The point in camera coordinates is (d x, d y, d).")
    else:
        steps.append("If d is the depth along the camera axis, the point in camera coordinates is (d x, d y, d), and "
                     "if d is the slant distance from the perspective centre, it is d (x, y, 1) / sqrt(x^2 + y^2 + "
                     "1).")
    steps.append("The camera transform takes the point, as the column vector (X, Y, Z, 1), to internal chunk "
                 "coordinates, and the chunk transform takes those to world coordinates (orientation.matrix_layout "
                 "and orientation.world_coordinates).")
    steps.append("These steps hold for a frame camera (calibration.camera_model_note says what holds for another "
                 "camera model) and leave out additional corrections (calibration.additional_corrections).")
    return " ".join(steps)


def DepthRecord(depthSource, depthQuantity, depthUnit, factorApplied, pixelsWithoutDepth, greyLevels=None,
                rendering=None, depthMapLevel=None):
    """The depth part of a calibration file: where the depth comes from,
    what it is, the unit and the factor of the values (None for the grey
    levels of a normalised export), the pixels without depth, the depths
    of the grey levels, for a rendered depth how it was rendered, and for
    a stored depth map its level."""
    record = {"source": depthSource, "quantity": depthQuantity, "unit": depthUnit, "factor_applied": factorApplied,
              "pixels_without_depth": pixelsWithoutDepth, "grey_levels": greyLevels}
    if depthMapLevel is not None:
        record["depth_map_level"] = depthMapLevel
        record["depth_map_level_note"] = DEPTH_MAP_LEVEL_NOTE
    if rendering is not None:
        record["rendering"] = rendering
    return record


# Notes of every calibration file and of one of a normalised export.
CALIBRATION_FILE_SCOPE_NOTE = (
    "This file records the geometry and the decoding of its depth image as far as MetaFOCUS can read them. It does "
    "not guarantee that every depth image can be back-projected in the same way: a camera model other than the frame "
    "camera, additional corrections, the motion of a rolling shutter, or a depth convention that the Metashape "
    "documentation leaves open can need further information or another mapping.")
QUANTISED_DEPTH_NOTE = (
    "The grey levels of a normalised 8-bit or 16-bit export are quantised depths: decoding them with grey_levels "
    "gives each depth to within half a level step and does not restore the floating-point depths exactly.")


def DepthCalibrationRecord(depthFileName, projectPath, chunk, viewpoint, depth, depthImage, calibration,
                           calibrationSource, cameraTransform, rollingShutter, calibrationProblem=None):
    """Everything that the depth image *depthFileName* needs to give 3D
    points outside Metashape, as a dict for its calibration file: the
    project, the chunk, the viewpoint, the depth part (DepthRecord),
    the calibration (CalibrationRecord), the camera and chunk transforms
    with their layout, the physical chunk scale, the CRS,
    the note on rolling shutter, and how to compute a 3D point.  What could
    not be read or does not fit is listed under problems
    (*calibrationProblem* when there is no calibration)."""
    from . import _METAFOCUS_VERSION
    problems = []
    calibrationRecord = None
    if calibration is None:
        problems.append(calibrationProblem or "the {} could not be read".format(calibrationSource))
    else:
        calibrationRecord, calibrationProblems = CalibrationRecord(calibration, calibrationSource, depthImage)
        problems += calibrationProblems
    cameraTransformRows = MatrixRows(cameraTransform)
    if cameraTransform is None:
        problems.append("the {} has no camera transform".format(viewpoint.get("kind", "image")))
    elif cameraTransformRows is None:
        problems.append("the camera transform could not be read")
    chunkMatrix = getattr(getattr(chunk, "transform", None), "matrix", None)
    chunkTransformRows = MatrixRows(chunkMatrix)
    if chunkMatrix is not None and chunkTransformRows is None:
        problems.append("the chunk transform could not be read")
    physicalScale = PhysicalChunkScale(chunk)
    record = {
        "file": "MetaFOCUS depth calibration file", "file_version": DEPTH_CALIBRATION_FILE_VERSION,
        "written_by": "MetaFOCUS {} (Depth & Render Export)".format(_METAFOCUS_VERSION),
        "written_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "depth_file": depthFileName, "project": projectPath,
        "chunk": {"label": str(getattr(chunk, "label", "")), "key": getattr(chunk, "key", None)},
        "viewpoint": viewpoint,
        "depth": depth,
        "pixel_coordinates": PIXEL_COORDINATES_TEXT,
        "depth_image": {"pixel_columns": WholeNumberOrNone(getattr(depthImage, "width", None)),
                        "pixel_rows": WholeNumberOrNone(getattr(depthImage, "height", None))},
        "calibration": calibrationRecord,
        "orientation": {
            "camera_coordinates": CAMERA_COORDINATES_TEXT, "matrix_layout": MATRIX_LAYOUT_TEXT,
            "camera_transform": cameraTransformRows, "chunk_transform": chunkTransformRows,
            "physical_chunk_scale": physicalScale,
            "physical_chunk_scale_note": PhysicalChunkScaleNote(chunk, physicalScale),
            "coordinate_system": CoordinateSystemRecord(getattr(chunk, "crs", None)),
            "world_coordinates": WorldCoordinatesText(chunk, chunkMatrix, chunkTransformRows)},
        "rolling_shutter": rollingShutter,
        "three_d_point": ThreeDimensionalPointText(depth["quantity"] == RENDERED_DEPTH_QUANTITY_TEXT,
                                                   depth["grey_levels"] is not None),
        "notes": [CALIBRATION_FILE_SCOPE_NOTE] + ([QUANTISED_DEPTH_NOTE] if depth["grey_levels"] is not None else [])}
    if problems:
        record["problems"] = problems
    return record


def RemovePartialFile(path):
    """Remove the file *path*, which could not be written to the end (a
    failure is reported once in the Console)."""
    try:
        if os.path.isfile(path):
            os.remove(path)
    except OSError as suppressedError:
        ReportSuppressedError("depthRenderExporter.RemovePartialFile", suppressedError)


def WriteDepthCalibrationFile(path, record):
    """Write *record* as JSON to *path*.  The text is made before the file
    is opened, so that a value that JSON cannot hold leaves no file, and a
    file that was opened but could not be written to the end is removed."""
    text = json.dumps(record, indent=2, ensure_ascii=False, allow_nan=False)
    calibrationFile = open(path, "w", encoding="utf-8", newline="\n")
    try:
        with calibrationFile:
            calibrationFile.write(text + "\n")
    except Exception:
        RemovePartialFile(path)
        raise


def RemoveExistingCalibrationFile(calibrationPath):
    """Remove the calibration file *calibrationPath* that an earlier export
    left next to a depth image that is about to be replaced (the user
    agreed in ConfirmReplacingExistingFiles), so that it can never
    describe the new depth image, also when the option is off or the new
    file cannot be written.  Raises OSError, naming the file, when it
    cannot be removed, so that the depth image is not written either.  The
    exports call it just before they save the depth image, so that a depth
    image that cannot be made keeps its calibration file."""
    if not os.path.lexists(calibrationPath):
        return
    try:
        os.remove(calibrationPath)
    except OSError as removalError:
        raise OSError("the existing calibration file {} could not be removed ({}), so the depth image was not "
                      "written".format(os.path.basename(calibrationPath), DescribeException(removalError)))


def EarlierCalibrationFileNote(calibrationPath, calibrationFileExisted):
    """", and its earlier calibration file IMG_1_depthmap32.json was
    removed" for the Console line of a depth image that could not be
    saved after RemoveExistingCalibrationFile removed the calibration file
    at *calibrationPath*, which existed before (*calibrationFileExisted*),
    or an empty string."""
    if calibrationFileExisted and not os.path.lexists(calibrationPath):
        return ", and its earlier calibration file {} was removed".format(os.path.basename(calibrationPath))
    return ""


def WriteCalibrationFileOfDepthImage(depthPath, viewpointLabel, buildRecord):
    """Make the record of the calibration file of the depth image
    *depthPath* with *buildRecord* (a function without arguments) and
    write it next to the depth image, with a Console line for each
    problem that the record lists.  True when the file was written,
    otherwise False with a Console line, so that the export counts the
    depth image as written and carries on."""
    calibrationFileName = DepthCalibrationFileName(os.path.basename(depthPath))
    calibrationPath = os.path.join(os.path.dirname(depthPath), calibrationFileName)
    try:
        record = buildRecord()
        for problem in record.get("problems", []):
            print("   {}: {} (calibration file {})".format(viewpointLabel, problem, calibrationFileName))
        WriteDepthCalibrationFile(calibrationPath, record)
    except Exception as writingError:
        # An incomplete file that RemovePartialFile could not remove is
        # named.
        print("   {}: the calibration file {} could not be written ({}), {}".format(
            viewpointLabel, calibrationFileName, DescribeException(writingError),
            "and the incomplete file could not be removed and should be deleted" if os.path.lexists(calibrationPath)
            else "so the depth image has none"))
        return False
    return True


def CalibrationFilesConsoleLine(writeCalibrationFiles, depthImageName):
    """The Console line of the calibration files of an export or a render."""
    if writeCalibrationFiles:
        return ("   Calibration files: a JSON file with the same name next to each {}, with its calibration, "
                "orientation, and scale".format(depthImageName))
    return ("   Calibration files: not written, and an existing one next to a {} to be written is "
            "removed".format(depthImageName))


def CalibrationFailuresText(calibrationFailureCount):
    """", 1 calibration file not written" for the last Console line of an
    export or a render, or an empty string."""
    if not calibrationFailureCount:
        return ""
    return ", {} not written".format(FormatCountWithNoun(calibrationFailureCount, "calibration file",
                                                         "calibration files"))


def CalibrationFailuresMessage(calibrationFailureCount):
    """The sentence of the final message on calibration files that could
    not be written, or an empty string."""
    if not calibrationFailureCount:
        return ""
    return "\n\n{} could not be written (see Console).".format(
        FormatCountWithNoun(calibrationFailureCount, "calibration file", "calibration files"))


# ===================================================================
# Depth maps tab
# ===================================================================

class DepthMapsTab(QtWidgets.QWidget):
    """Export the depth maps of the chunk (chunk.depth_maps), one file
    per image."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QtWidgets.QVBoxLayout(self)

        introductionLabel = QtWidgets.QLabel(DEPTH_MAPS_INTRODUCTION_TEXT)
        introductionLabel.setWordWrap(True)
        layout.addWidget(introductionLabel)

        self.chunkInformationLabel = QtWidgets.QLabel("")
        self.chunkInformationLabel.setTextFormat(QtCore.Qt.RichText)
        self.chunkInformationLabel.setWordWrap(True)
        layout.addWidget(self.chunkInformationLabel)

        scopeGroup = QtWidgets.QGroupBox("Images")
        scopeLayout = QtWidgets.QVBoxLayout(scopeGroup)
        self.allImagesRadioButton = QtWidgets.QRadioButton(
            "All images with a depth map")
        self.allImagesRadioButton.setChecked(True)
        self.selectedImagesRadioButton = QtWidgets.QRadioButton(
            "Selected images only")
        scopeLayout.addWidget(self.allImagesRadioButton)
        scopeLayout.addWidget(self.selectedImagesRadioButton)
        # What Metashape reports as selected (DepthMapSelectionStatusText),
        # refreshed by the window and when the export starts.
        self.selectionStatusLabel = QtWidgets.QLabel("")
        self.selectionStatusLabel.setWordWrap(True)
        scopeLayout.addWidget(self.selectionStatusLabel)
        self.RefreshSelectionStatus()
        layout.addWidget(scopeGroup)

        formatGroup = QtWidgets.QGroupBox("Format")
        formatLayout = QtWidgets.QGridLayout(formatGroup)
        formatLayout.addWidget(QtWidgets.QLabel("Output:"), 0, 0)
        self.outputFormatComboBox = QtWidgets.QComboBox()
        self.outputFormatComboBox.addItem("32-bit float, multiplied by the chunk scale")
        self.outputFormatComboBox.addItem("16-bit greyscale, normalised")
        self.outputFormatComboBox.addItem("8-bit greyscale, normalised")
        formatLayout.addWidget(self.outputFormatComboBox, 0, 1)
        self.invertGreyscaleCheckBox = QtWidgets.QCheckBox(
            "Invert greyscale (nearer is brighter)")
        self.invertGreyscaleCheckBox.setChecked(True)
        formatLayout.addWidget(self.invertGreyscaleCheckBox, 1, 0, 1, 2)
        # Without NumPy, the note says how to install it and gives the
        # commands in fields that can be copied, each under its shell (the
        # Command Prompt and PowerShell on Windows).
        numpyExplanation, numpyCommands = ("", []) if _NUMPY_AVAILABLE else CurrentNumpyMissingTexts()
        self.numpyNote = QtWidgets.QLabel(numpyExplanation)
        self.numpyNote.setStyleSheet("color: #a05000;")
        self.numpyNote.setWordWrap(True)
        formatLayout.addWidget(self.numpyNote, 2, 0, 1, 2)
        self.numpyCommandFields = []
        nextRow = 3
        for shell, command in numpyCommands:
            formatLayout.addWidget(QtWidgets.QLabel("{}:".format(shell)), nextRow, 0)
            commandField = QtWidgets.QLineEdit(command)
            commandField.setReadOnly(True)
            commandField.setToolTip("Select the command and copy it.")
            formatLayout.addWidget(commandField, nextRow, 1)
            self.numpyCommandFields.append(commandField)
            nextRow += 1
        self.writeCalibrationFilesCheckBox = QtWidgets.QCheckBox(
            "Write a calibration file (.json) next to each depth map")
        self.writeCalibrationFilesCheckBox.setChecked(True)
        self.writeCalibrationFilesCheckBox.setToolTip(
            "A JSON file with the same name as the depth map holds the calibration of the depth map, the camera "
            "and chunk transforms, the physical chunk scale, and, for a normalised export, the depths of its grey "
            "levels, so that the depth map can give 3D points outside Metashape.")
        formatLayout.addWidget(self.writeCalibrationFilesCheckBox, nextRow, 0, 1, 2)
        layout.addWidget(formatGroup)

        self.progressBar = QtWidgets.QProgressBar()
        self.progressBar.setVisible(False)
        layout.addWidget(self.progressBar)

        runRow = QtWidgets.QHBoxLayout()
        self.exportButton = QtWidgets.QPushButton("Export depth maps\u2026")
        self.exportButton.clicked.connect(self._OnExport)
        runRow.addStretch()
        runRow.addWidget(self.exportButton)
        layout.addLayout(runRow)
        layout.addStretch()

        self._RefreshChunkInformation()

    def _RefreshChunkInformation(self):
        chunk = Metashape.app.document.chunk if \
            Metashape.app.document else None
        if chunk is None:
            self.chunkInformationLabel.setText("<i>No active chunk.</i>")
            self.exportButton.setEnabled(False)
            return
        depthMaps = getattr(chunk, "depth_maps", None)
        depthMapCount = 0
        if depthMaps is not None:
            try:
                depthMapCount = len(depthMaps.keys())
            except Exception as listingError:
                ReportSuppressedError(
                    "depthRenderExporter.DepthMapsTab._RefreshChunkInformation", listingError)
                depthMapCount = 0
        self.chunkInformationLabel.setText(
            "Active chunk: <b>{}</b> &nbsp;|&nbsp; images with a "
            "depth map: <b>{}</b>".format(chunk.label, depthMapCount))
        self.exportButton.setEnabled(depthMapCount > 0)

    def RefreshSelectionStatus(self):
        """Show how many images Metashape reports as selected in the active
        chunk and how many of them have a depth map."""
        document = Metashape.app.document
        ShowSelectionStatus(self, DepthMapSelectionStatusText(document.chunk if document else None))

    def _OnExport(self):
        document = Metashape.app.document
        chunk = document.chunk if document else None
        ShowSelectionStatus(self, DepthMapSelectionStatusText(chunk))
        if chunk is None or getattr(chunk, "depth_maps", None) is None:
            QtWidgets.QMessageBox.warning(
                self, "Depth & Render Export",
                "The active chunk has no depth maps.")
            return

        formatText = self.outputFormatComboBox.currentText()
        outputFormat = DEPTH_MAP_OUTPUT_FORMATS[self.outputFormatComboBox.currentIndex()]
        isFloat = outputFormat == "float32"
        if not isFloat and not _NUMPY_AVAILABLE:
            numpyExplanation, numpyCommands = CurrentNumpyMissingTexts()
            print("Depth & Render Export: " + numpyExplanation)
            for commandLine in NumpyCommandsConsoleLines(numpyCommands):
                print("Depth & Render Export: " + commandLine)
            if not numpyCommands:
                consoleNote = ""
            elif len(numpyCommands) == 1:
                consoleNote = "\n\nThe command is also in the Console."
            else:
                consoleNote = "\n\nThe commands are also in the Console."
            QtWidgets.QMessageBox.warning(
                self, "Depth & Render Export",
                numpyExplanation + ("" if not numpyCommands else "\n\n" + NumpyCommandsMessageText(numpyCommands))
                + consoleNote)
            return

        depthMaps = chunk.depth_maps
        try:
            depthMapCameras = list(depthMaps.keys())
        except Exception as listingError:
            ReportSuppressedError(
                "depthRenderExporter.DepthMapsTab._OnExport", listingError)
            depthMapCameras = []

        cameraList = SelectDepthMapCameras(
            chunk.cameras, depthMapCameras,
            self.selectedImagesRadioButton.isChecked(),
            Metashape.Camera.Type.Regular)

        selectedOnly = self.selectedImagesRadioButton.isChecked()
        if not cameraList:
            if selectedOnly:
                noSelectionText = NoSelectedImageText("image with a depth map")
                print("Depth & Render Export: " + noSelectionText.split("\n")[0])
                QtWidgets.QMessageBox.warning(self, "Depth & Render Export", noSelectionText)
            else:
                QtWidgets.QMessageBox.information(
                    self, "Depth & Render Export",
                    "None of the chosen images has a depth map.")
            return

        # Metres only for a physical scale (PhysicalChunkScale): the
        # transform that Region Tools records for a chunk without one has
        # the arbitrary scale 1, which GetChunkScale would return.
        scale = PhysicalChunkScale(chunk)
        if not isFloat:
            depthUnit = GREY_LEVEL_UNIT_TEXT
        elif scale is not None:
            depthUnit = PhysicalLengthUnitText(chunk)
        else:
            depthUnit = INTERNAL_CHUNK_UNIT_TEXT
        if isFloat and scale is None:
            reply = QtWidgets.QMessageBox.question(
                self, "Depth & Render Export",
                "The chunk has no physical scale ({}), so the 32-bit depth "
                "cannot be in metres.\n\nExport with a scale of 1.0 (values "
                "in internal chunk units)?".format(DescribeMissingPhysicalScale(chunk)),
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.No)
            if reply != QtWidgets.QMessageBox.Yes:
                return
            scale = 1.0

        outputFolder = Metashape.app.getExistingDirectory(
            "Choose the folder for the depth maps")
        if not outputFolder:
            return

        # Names over all regular images of the chunk, so that an image
        # keeps its file name whichever images are exported.
        baseNameByKey = OutputBaseNamesByKey(
            chunk.cameras, Metashape.Camera.Type.Regular)
        # The calibration file next to a depth map that is written is
        # replaced or, with the option off, removed, so that an earlier
        # one never describes the new depth map.
        writeCalibrationFiles = self.writeCalibrationFilesCheckBox.isChecked()
        depthPaths = [os.path.join(outputFolder, DepthMapOutputFileName(baseNameByKey[camera.key], outputFormat))
                      for camera in cameraList]
        calibrationPaths = [os.path.join(outputFolder, DepthCalibrationFileName(os.path.basename(depthPath)))
                            for depthPath in depthPaths]
        # The run is one Console block (ConsoleBlockStartOfChunk), which
        # every way out of the run closes.
        selectionLines = (SelectedImagesConsoleLines(len(SelectedRegularImages(chunk)), cameraList,
                                                     "with a depth map", "without a depth map")
                          if selectedOnly else [])
        PrintConsoleLines(ConsoleBlockStartOfChunk("Depth & Render Export", chunk) + selectionLines)
        try:
            if not ConfirmReplacingExistingFiles(
                    self, depthPaths + (calibrationPaths if writeCalibrationFiles else []),
                    [] if writeCalibrationFiles else calibrationPaths):
                return

            self.progressBar.setVisible(True)
            self.progressBar.setRange(0, len(cameraList))
            self.progressBar.setValue(0)
            self.exportButton.setEnabled(False)
            QtWidgets.QApplication.processEvents()

            print("[Depth & Render Export] Exporting {} ({})".format(
                FormatCountWithNoun(len(cameraList), "depth map", "depth maps"),
                formatText))
            print(OutputFolderConsoleLine(outputFolder))
            print(CalibrationFilesConsoleLine(writeCalibrationFiles, "depth map"))
            projectPath = str(getattr(document, "path", "") or "")

            exportedCount = 0
            failureCount = 0
            calibrationFailureCount = 0
            for index, camera in enumerate(cameraList):
                calibrationFileExisted = os.path.lexists(calibrationPaths[index])
                try:
                    depthMap = depthMaps[camera]
                    depthImage = DepthMapImageOfLevel(depthMap, DEPTH_MAP_LEVEL)
                    baseName = baseNameByKey[camera.key]
                    greyLevels = None
                    if isFloat:
                        outputImage = depthImage * scale
                        path = depthPaths[index]
                        RemoveExistingCalibrationFile(calibrationPaths[index])
                        outputImage.save(path)
                    else:
                        path, greyLevels = self._SaveNormalised(
                            depthImage, outputFormat, outputFolder, baseName, calibrationPaths[index])
                    exportedCount += 1
                except Exception as cameraError:
                    failureCount += 1
                    print("   {} failed: {}{}".format(
                        camera.label, DescribeDepthMapFailure(cameraError),
                        EarlierCalibrationFileNote(calibrationPaths[index], calibrationFileExisted)))
                else:
                    # A calibration file that cannot be written leaves the
                    # depth map exported (WriteCalibrationFileOfDepthImage).
                    # The grey levels of a normalised export have no factor.
                    if writeCalibrationFiles and not WriteDepthMapCalibrationFile(
                            path, depthMap, depthImage, camera, chunk, projectPath,
                            depthUnit, scale if isFloat else None, greyLevels):
                        calibrationFailureCount += 1
                self.progressBar.setValue(index + 1)
                QtWidgets.QApplication.processEvents()

            self.progressBar.setVisible(False)
            self.exportButton.setEnabled(True)
            print("[Depth & Render Export] Done: {} exported, {} "
                  "failed{}.".format(exportedCount, failureCount, CalibrationFailuresText(calibrationFailureCount)))
        finally:
            PrintConsoleLines(ConsoleBlockEnd())
        QtWidgets.QMessageBox.information(
            self, "Depth & Render Export",
            "Exported {} to:\n{}{}{}".format(
                FormatCountWithNoun(exportedCount, "depth map", "depth maps"),
                outputFolder,
                "\n\n{} failed (see Console).".format(
                    FormatCountWithNoun(failureCount, "image", "images"))
                if failureCount else "", CalibrationFailuresMessage(calibrationFailureCount)))

    def _SaveNormalised(self, depthImage, outputFormat, outputFolder,
                        baseName, calibrationPath=None):
        """Normalise a depth image to 8-bit or 16-bit greyscale (0 for
        pixels without depth) and save it as a single-channel greyscale
        file (GreyscalePngBytes, GreyscaleTiffBytes), removing the earlier
        calibration file at *calibrationPath* just before
        (RemoveExistingCalibrationFile).  Return the path written and
        the grey levels with the depths of the lowest and the highest
        valid level (GreyLevelRecord), for the calibration file."""
        depthValues = numpy.frombuffer(
            depthImage.tostring(), dtype=numpy.float32)
        invertGreyscale = self.invertGreyscaleCheckBox.isChecked()
        maximumGreyLevel = 65535 if outputFormat == "uint16" else 255
        if outputFormat == "uint16":
            greyscale = NormaliseDepthValuesToGreyLevels(
                depthValues, invertGreyscale,
                65535).astype(numpy.uint16)
        else:
            greyscale = NormaliseDepthValuesToGreyLevels(
                depthValues, invertGreyscale,
                255).astype(numpy.uint8)
        greyLevels = GreyLevelRecord(ValidDepthRange(depthValues), maximumGreyLevel, invertGreyscale)

        # A single-channel greyscale file that MetaFOCUS writes itself.
        width = depthImage.width
        height = depthImage.height
        if outputFormat == "uint16":
            fileBytes = GreyscaleTiffBytes(greyscale, width, height, 16)
        else:
            fileBytes = GreyscalePngBytes(greyscale, width, height, 8)
        path = os.path.join(
            outputFolder, DepthMapOutputFileName(baseName, outputFormat))
        if calibrationPath is not None:
            RemoveExistingCalibrationFile(calibrationPath)
        with open(path, "wb") as fileHandle:
            fileHandle.write(fileBytes)
        return path, greyLevels


def ReadDepthMapCalibration(depthMap):
    """(calibration, None) of a stored depth map, that of the level that
    the tab exports (DepthMap.getCalibration(DEPTH_MAP_LEVEL)) or, when
    that gives none or raises, DepthMap.calibration, or (None, the problem
    in words) when the depth map has none or it cannot be read."""
    calibration = None
    levelError = None
    try:
        getCalibration = getattr(depthMap, "getCalibration", None)
        if getCalibration is not None:
            calibration = getCalibration(DEPTH_MAP_LEVEL)
    except Exception as readingError:
        levelError = readingError
    if calibration is None:
        try:
            calibration = getattr(depthMap, "calibration", None)
        except Exception as readingError:
            levelError = levelError or readingError
    if calibration is None and levelError is not None:
        return None, "the calibration of the depth map could not be read ({})".format(DescribeException(levelError))
    if calibration is None:
        return None, "the depth map has no calibration"
    if levelError is not None:
        ReportSuppressedError("depthRenderExporter.ReadDepthMapCalibration", levelError)
    return calibration, None


def DepthMapCalibrationRecord(depthPath, depthMap, depthImage, camera, chunk, projectPath, depthUnit, factorApplied,
                              greyLevels):
    """The record of the calibration file of the depth map of *camera*,
    exported to *depthPath* (DepthCalibrationRecord)."""
    calibration, calibrationProblem = ReadDepthMapCalibration(depthMap)
    depth = DepthRecord(
        DEPTH_MAP_SOURCE_TEXT, DEPTH_MAP_QUANTITY_TEXT, depthUnit, factorApplied,
        "the grey level 0" if greyLevels is not None else "values of 0 or below and values that are not finite numbers",
        greyLevels, depthMapLevel=DEPTH_MAP_LEVEL)
    return DepthCalibrationRecord(
        os.path.basename(depthPath), projectPath, chunk, {"kind": "image", "label": str(camera.label), "key": camera.key},
        depth, depthImage, calibration, "calibration of the depth map", getattr(camera, "transform", None),
        DepthMapRollingShutterNote(RollingShutterModelName(getattr(camera, "sensor", None))), calibrationProblem)


def WriteDepthMapCalibrationFile(depthPath, depthMap, depthImage, camera, chunk, projectPath, depthUnit,
                                 factorApplied, greyLevels):
    """Write the calibration file of the depth map of *camera*, exported to
    *depthPath* (DepthMapCalibrationRecord, WriteCalibrationFileOfDepthImage).
    True when it was written."""
    return WriteCalibrationFileOfDepthImage(depthPath, camera.label, functools.partial(
        DepthMapCalibrationRecord, depthPath, depthMap, depthImage, camera, chunk, projectPath, depthUnit,
        factorApplied, greyLevels))


def RenderedDepthCalibrationRecord(depthPath, renderedDepth, camera, index, useKeyframes, usesVirtualCamera,
                                   calibration, chunk, projectPath, cullBackFaces, addAlphaChannel):
    """The record of the calibration file of the depth rendered to
    *depthPath* from the viewpoint of *camera*, the *index*-th keyframe
    (from 0) or an oriented image, with *calibration*, the calibration of
    the image or the virtual camera, and the options of the render
    (DepthCalibrationRecord).  Keyframes have no rolling-shutter
    compensation of their own."""
    if useKeyframes:
        viewpoint = {"kind": "keyframe", "label": str(camera.label), "number": index + 1}
        rollingShutter = None
    else:
        viewpoint = {"kind": "image", "label": str(camera.label), "key": camera.key}
        rollingShutter = RenderedDepthRollingShutterNote(RollingShutterModelName(getattr(camera, "sensor", None)))
    depth = DepthRecord(
        RENDERED_DEPTH_SOURCE_TEXT, RenderedDepthQuantityText(CameraModelName(calibration)), INTERNAL_CHUNK_UNIT_TEXT,
        1.0, RenderedPixelsWithoutDepthText(cullBackFaces, addAlphaChannel), None,
        {"cull_back_faces": bool(cullBackFaces), "alpha_channel": bool(addAlphaChannel),
         "note": RENDERED_DEPTH_DISTORTION_NOTE_TEXT})
    return DepthCalibrationRecord(
        os.path.basename(depthPath), projectPath, chunk, viewpoint, depth, renderedDepth, calibration,
        "virtual camera" if usesVirtualCamera else "calibration of the image", getattr(camera, "transform", None),
        rollingShutter)


def WriteRenderedDepthCalibrationFile(depthPath, renderedDepth, camera, index, useKeyframes, usesVirtualCamera,
                                      calibration, chunk, projectPath, cullBackFaces, addAlphaChannel):
    """Write the calibration file of the depth rendered to *depthPath*
    (RenderedDepthCalibrationRecord, WriteCalibrationFileOfDepthImage).
    True when it was written."""
    return WriteCalibrationFileOfDepthImage(depthPath, camera.label, functools.partial(
        RenderedDepthCalibrationRecord, depthPath, renderedDepth, camera, index, useKeyframes, usesVirtualCamera,
        calibration, chunk, projectPath, cullBackFaces, addAlphaChannel))


# ===================================================================
# Render from triangular mesh tab
# ===================================================================

class MeshRenderTab(QtWidgets.QWidget):
    """Render the triangular mesh (image, depth, mask) from the
    viewpoint of each oriented image or keyframe."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QtWidgets.QVBoxLayout(self)

        introductionLabel = QtWidgets.QLabel(MESH_RENDER_INTRODUCTION_TEXT)
        introductionLabel.setWordWrap(True)
        layout.addWidget(introductionLabel)

        self.chunkInformationLabel = QtWidgets.QLabel("")
        self.chunkInformationLabel.setTextFormat(QtCore.Qt.RichText)
        self.chunkInformationLabel.setWordWrap(True)
        layout.addWidget(self.chunkInformationLabel)

        sourceGroup = QtWidgets.QGroupBox("Viewpoints")
        sourceLayout = QtWidgets.QVBoxLayout(sourceGroup)
        self.orientedImagesRadioButton = QtWidgets.QRadioButton(
            "Each oriented image")
        self.orientedImagesRadioButton.setChecked(True)
        self.keyframesRadioButton = QtWidgets.QRadioButton(
            "Each keyframe of the animation track")
        self.selectedImagesOnlyCheckBox = QtWidgets.QCheckBox(
            "Selected images only (oriented images)")
        sourceLayout.addWidget(self.orientedImagesRadioButton)
        sourceLayout.addWidget(self.keyframesRadioButton)
        sourceLayout.addWidget(self.selectedImagesOnlyCheckBox)
        # What Metashape reports as selected (RenderSelectionStatusText),
        # refreshed by the window and when the render starts.
        self.selectionStatusLabel = QtWidgets.QLabel("")
        self.selectionStatusLabel.setWordWrap(True)
        sourceLayout.addWidget(self.selectionStatusLabel)
        self.RefreshSelectionStatus()
        layout.addWidget(sourceGroup)

        outputGroup = QtWidgets.QGroupBox("Outputs")
        outputLayout = QtWidgets.QGridLayout(outputGroup)
        self.renderedImageCheckBox = QtWidgets.QCheckBox("Rendered image")
        self.renderedImageCheckBox.setChecked(True)
        self.renderedDepthCheckBox = QtWidgets.QCheckBox("Depth (32-bit, internal chunk units)")
        self.renderedMaskCheckBox = QtWidgets.QCheckBox("Mask")
        outputLayout.addWidget(self.renderedImageCheckBox, 0, 0)
        outputLayout.addWidget(self.renderedDepthCheckBox, 0, 1)
        outputLayout.addWidget(self.renderedMaskCheckBox, 0, 2)
        outputLayout.addWidget(QtWidgets.QLabel("Image format:"), 1, 0)
        self.imageFormatComboBox = QtWidgets.QComboBox()
        self.imageFormatComboBox.addItems(["PNG", "JPEG", "TIFF"])
        outputLayout.addWidget(self.imageFormatComboBox, 1, 1)
        self.cullBackFacesCheckBox = QtWidgets.QCheckBox("Cull back faces")
        self.cullBackFacesCheckBox.setChecked(True)
        self.addAlphaChannelCheckBox = QtWidgets.QCheckBox(
            "Add an alpha channel (rendered image and depth)")
        self.addAlphaChannelCheckBox.setChecked(True)
        outputLayout.addWidget(self.cullBackFacesCheckBox, 2, 0)
        outputLayout.addWidget(self.addAlphaChannelCheckBox, 2, 1)
        self.writeCalibrationFilesCheckBox = QtWidgets.QCheckBox(
            "Write a calibration file (.json) next to each depth image")
        self.writeCalibrationFilesCheckBox.setChecked(True)
        self.writeCalibrationFilesCheckBox.setToolTip(
            "A JSON file with the same name as the rendered depth image holds the calibration of the image or of "
            "the virtual camera, the camera transform, the chunk transform, and the physical chunk scale, so that "
            "the depth image can give 3D points outside Metashape.")
        outputLayout.addWidget(self.writeCalibrationFilesCheckBox, 3, 0, 1, 3)
        layout.addWidget(outputGroup)

        # Virtual camera: keyframes carry only a viewpoint and no usable
        # calibration, so one must be supplied to render them.  It can
        # also replace the oriented images' own calibrations to render
        # them all with the same pixel count and principal distance.
        virtualGroup = QtWidgets.QGroupBox("Virtual camera")
        virtualLayout = QtWidgets.QGridLayout(virtualGroup)
        virtualLayout.addWidget(QtWidgets.QLabel("Number of virtual photosite columns:"), 0, 0)
        self.virtualPixelColumnCountSpinBox = QtWidgets.QSpinBox()
        self.virtualPixelColumnCountSpinBox.setRange(16, 60000)
        self.virtualPixelColumnCountSpinBox.setValue(6000)
        virtualLayout.addWidget(self.virtualPixelColumnCountSpinBox, 0, 1)
        virtualLayout.addWidget(QtWidgets.QLabel("Number of virtual photosite rows:"), 0, 2)
        self.virtualPixelRowCountSpinBox = QtWidgets.QSpinBox()
        self.virtualPixelRowCountSpinBox.setRange(16, 60000)
        self.virtualPixelRowCountSpinBox.setValue(4000)
        virtualLayout.addWidget(self.virtualPixelRowCountSpinBox, 0, 3)
        virtualLayout.addWidget(
            QtWidgets.QLabel("Principal distance (mm):"), 1, 0)
        self.virtualPrincipalDistanceSpinBox = QtWidgets.QDoubleSpinBox()
        self.virtualPrincipalDistanceSpinBox.setRange(1.0, 2000.0)
        self.virtualPrincipalDistanceSpinBox.setDecimals(2)
        self.virtualPrincipalDistanceSpinBox.setValue(50.0)
        virtualLayout.addWidget(self.virtualPrincipalDistanceSpinBox, 1, 1)
        virtualLayout.addWidget(
            QtWidgets.QLabel("Sensor width (mm):"), 1, 2)
        self.virtualSensorWidthSpinBox = QtWidgets.QDoubleSpinBox()
        self.virtualSensorWidthSpinBox.setRange(1.0, 200.0)
        self.virtualSensorWidthSpinBox.setDecimals(2)
        self.virtualSensorWidthSpinBox.setValue(36.0)
        virtualLayout.addWidget(self.virtualSensorWidthSpinBox, 1, 3)
        self.useVirtualCameraForOrientedImagesCheckBox = QtWidgets.QCheckBox(
            "Use this virtual camera for the oriented images too "
            "(otherwise each image uses its own calibration)")
        virtualLayout.addWidget(self.useVirtualCameraForOrientedImagesCheckBox, 2, 0, 1, 4)
        self.virtualNote = QtWidgets.QLabel(
            "Keyframes are always rendered with the virtual camera "
            "(they have no calibration of their own).")
        self.virtualNote.setStyleSheet("color: #555; font-size: 11px;")
        self.virtualNote.setWordWrap(True)
        virtualLayout.addWidget(self.virtualNote, 3, 0, 1, 4)
        layout.addWidget(virtualGroup)

        self.progressBar = QtWidgets.QProgressBar()
        self.progressBar.setVisible(False)
        layout.addWidget(self.progressBar)

        runRow = QtWidgets.QHBoxLayout()
        self.renderButton = QtWidgets.QPushButton("Render\u2026")
        self.renderButton.clicked.connect(self._OnRender)
        runRow.addStretch()
        runRow.addWidget(self.renderButton)
        layout.addLayout(runRow)
        layout.addStretch()

        self._RefreshChunkInformation()

    def _RefreshChunkInformation(self):
        chunk = Metashape.app.document.chunk if \
            Metashape.app.document else None
        if chunk is None:
            self.chunkInformationLabel.setText("<i>No active chunk.</i>")
            self.renderButton.setEnabled(False)
            return
        hasModel = getattr(chunk, "model", None) is not None
        orientedImageCount = sum(
            1 for camera in chunk.cameras
            if camera.type == Metashape.Camera.Type.Regular
            and camera.transform is not None)
        keyframeCount = sum(
            1 for camera in chunk.cameras
            if camera.type == Metashape.Camera.Type.Keyframe)
        self.chunkInformationLabel.setText(
            "Active chunk: <b>{}</b> &nbsp;|&nbsp; triangular mesh: <b>{}</b> "
            "&nbsp;|&nbsp; oriented images: <b>{}</b> &nbsp;|&nbsp; "
            "keyframes: <b>{}</b>".format(
                chunk.label, "yes" if hasModel else "no",
                orientedImageCount, keyframeCount))
        self.renderButton.setEnabled(hasModel)

    def _BuildVirtualCalibration(self):
        """The calibration of the virtual camera (BuildVirtualCalibration)
        from the fields of the dialog: numbers of virtual photosite columns
        and rows, principal distance, and sensor width."""
        return BuildVirtualCalibration(
            int(self.virtualPixelColumnCountSpinBox.value()), int(self.virtualPixelRowCountSpinBox.value()),
            self.virtualPrincipalDistanceSpinBox.value(), self.virtualSensorWidthSpinBox.value())

    def RefreshSelectionStatus(self):
        """Show how many images Metashape reports as selected in the active
        chunk and how many of them are oriented."""
        document = Metashape.app.document
        ShowSelectionStatus(self, RenderSelectionStatusText(document.chunk if document else None))

    def _OnRender(self):
        document = Metashape.app.document
        chunk = document.chunk if document else None
        ShowSelectionStatus(self, RenderSelectionStatusText(chunk))
        model = getattr(chunk, "model", None) if chunk else None
        if model is None:
            QtWidgets.QMessageBox.warning(
                self, "Depth & Render Export",
                "The active chunk has no triangular mesh to render.")
            return

        if not (self.renderedImageCheckBox.isChecked()
                or self.renderedDepthCheckBox.isChecked()
                or self.renderedMaskCheckBox.isChecked()):
            QtWidgets.QMessageBox.warning(
                self, "Depth & Render Export",
                "Select at least one output (rendered image, depth, or mask).")
            return

        useKeyframes = self.keyframesRadioButton.isChecked()
        cameraList = []
        for camera in chunk.cameras:
            if useKeyframes:
                if camera.type != Metashape.Camera.Type.Keyframe:
                    continue
            else:
                if camera.type != Metashape.Camera.Type.Regular:
                    continue
                if camera.transform is None:
                    continue
                if self.selectedImagesOnlyCheckBox.isChecked() and \
                        not camera.selected:
                    continue
            cameraList.append(camera)

        selectedOnly = not useKeyframes and self.selectedImagesOnlyCheckBox.isChecked()
        if not cameraList:
            if selectedOnly:
                noSelectionText = NoSelectedImageText("oriented image")
                print("Depth & Render Export: " + noSelectionText.split("\n")[0])
                QtWidgets.QMessageBox.warning(self, "Depth & Render Export", noSelectionText)
            else:
                QtWidgets.QMessageBox.information(
                    self, "Depth & Render Export",
                    "No matching keyframes were found." if useKeyframes
                    else "No matching oriented images were found.")
            return

        outputFolder = Metashape.app.getExistingDirectory(
            "Choose the folder for the renders")
        if not outputFolder:
            return

        cullFaces = self.cullBackFacesCheckBox.isChecked()
        addAlpha = self.addAlphaChannelCheckBox.isChecked()
        imageExtension = {"PNG": ".png", "JPEG": ".jpg",
                          "TIFF": ".tif"}[
            self.imageFormatComboBox.currentText()]
        virtualCalibration = self._BuildVirtualCalibration()
        # Keyframes always use the virtual camera.  Oriented images use
        # it only if the override is ticked, otherwise their own.
        useVirtualForRegular = self.useVirtualCameraForOrientedImagesCheckBox.isChecked()
        # Names over all regular images of the chunk, the same as in
        # the Depth maps tab, whichever images are rendered.
        baseNameByKey = OutputBaseNamesByKey(
            chunk.cameras, Metashape.Camera.Type.Regular)
        outputKinds = [outputKind for outputKind, checkBox in (
            ("image", self.renderedImageCheckBox), ("depth", self.renderedDepthCheckBox),
            ("mask", self.renderedMaskCheckBox)) if checkBox.isChecked()]
        writeCalibrationFiles = "depth" in outputKinds and self.writeCalibrationFilesCheckBox.isChecked()
        targetPaths = [os.path.join(outputFolder, MeshRenderOutputFileName(
                           RenderBaseName(index, camera, useKeyframes, baseNameByKey), outputKind, imageExtension))
                       for index, camera in enumerate(cameraList) for outputKind in outputKinds]
        # The calibration file next to a depth that is rendered is
        # replaced or, with the option off, removed.
        calibrationPaths = [os.path.join(outputFolder, DepthCalibrationFileName(MeshRenderOutputFileName(
                                RenderBaseName(index, camera, useKeyframes, baseNameByKey), "depth", imageExtension)))
                            for index, camera in enumerate(cameraList)] if "depth" in outputKinds else []
        # The run is one Console block (ConsoleBlockStartOfChunk), which
        # every way out of the run closes.
        selectionLines = (SelectedImagesConsoleLines(len(SelectedRegularImages(chunk)), cameraList)
                          if selectedOnly else [])
        PrintConsoleLines(ConsoleBlockStartOfChunk("Depth & Render Export", chunk) + selectionLines)
        try:
            if not ConfirmReplacingExistingFiles(
                    self, targetPaths + (calibrationPaths if writeCalibrationFiles else []),
                    [] if writeCalibrationFiles else calibrationPaths):
                return

            self.progressBar.setVisible(True)
            self.progressBar.setRange(0, len(cameraList))
            self.progressBar.setValue(0)
            self.renderButton.setEnabled(False)
            QtWidgets.QApplication.processEvents()

            print("[Depth & Render Export] Rendering {} from the triangular "
                  "mesh".format(FormatCountWithNoun(
                      len(cameraList), "viewpoint", "viewpoints")))
            print(OutputFolderConsoleLine(outputFolder))
            if useKeyframes:
                cameraText = "virtual camera"
            elif useVirtualForRegular:
                cameraText = "virtual camera for the oriented images"
            else:
                cameraText = "own calibration of each image"
            print(RenderOptionsConsoleLine(outputKinds, self.imageFormatComboBox.currentText(), cullFaces, addAlpha,
                                           cameraText))
            if "depth" in outputKinds:
                print("   " + RenderedDepthUnitsNote(PhysicalChunkScale(chunk)))
                print(CalibrationFilesConsoleLine(writeCalibrationFiles, "depth image"))
            projectPath = str(getattr(document, "path", "") or "")

            renderedCount = 0
            failureCount = 0
            calibrationFailureCount = 0
            for index, camera in enumerate(cameraList):
                transform = camera.transform
                if useKeyframes or useVirtualForRegular:
                    calibration = virtualCalibration
                else:
                    calibration = camera.calibration
                baseName = RenderBaseName(index, camera, useKeyframes, baseNameByKey)
                if transform is None or calibration is None or \
                        getattr(calibration, "width", 0) <= 0:
                    failureCount += 1
                    print("   {} skipped (no orientation or no calibration)".format(
                        camera.label))
                    self.progressBar.setValue(index + 1)
                    continue
                # The outputs chosen before the loop decide, so that ticking
                # Depth during a render cannot make a later viewpoint fail.
                renderedDepth = None
                calibrationFileExisted = "depth" in outputKinds and os.path.lexists(calibrationPaths[index])
                try:
                    # Each render is checked before anything is removed or
                    # written for it (CheckedRender).
                    if "image" in outputKinds:
                        renderedImage = CheckedRender(model.renderImage(
                            transform, calibration,
                            cull_faces=cullFaces, add_alpha=addAlpha), "renderImage")
                        renderedImage.save(os.path.join(
                            outputFolder, MeshRenderOutputFileName(
                                baseName, "image", imageExtension)))
                    if "depth" in outputKinds:
                        depthPath = os.path.join(
                            outputFolder, MeshRenderOutputFileName(
                                baseName, "depth", imageExtension))
                        depthImage = CheckedRender(model.renderDepth(
                            transform, calibration,
                            cull_faces=cullFaces, add_alpha=addAlpha), "renderDepth")
                        RemoveExistingCalibrationFile(calibrationPaths[index])
                        depthImage.save(depthPath)
                        renderedDepth = depthImage
                    if "mask" in outputKinds:
                        renderedMask = CheckedRender(model.renderMask(
                            transform, calibration, cull_faces=cullFaces), "renderMask")
                        renderedMask.save(os.path.join(
                            outputFolder, MeshRenderOutputFileName(
                                baseName, "mask", imageExtension)))
                    renderedCount += 1
                except Exception as renderError:
                    failureCount += 1
                    print("   {} failed: {}{}".format(
                        camera.label, DescribeRenderFailure(renderError),
                        "" if renderedDepth is not None or "depth" not in outputKinds
                        else EarlierCalibrationFileNote(calibrationPaths[index], calibrationFileExisted)))
                # A saved depth gets its calibration file even when the mask
                # fails, and a calibration file that cannot be written stops
                # nothing.
                if renderedDepth is not None and writeCalibrationFiles and not WriteRenderedDepthCalibrationFile(
                        depthPath, renderedDepth, camera, index, useKeyframes, useKeyframes or useVirtualForRegular,
                        calibration, chunk, projectPath, cullFaces, addAlpha):
                    calibrationFailureCount += 1
                self.progressBar.setValue(index + 1)
                QtWidgets.QApplication.processEvents()

            self.progressBar.setVisible(False)
            self.renderButton.setEnabled(True)
            print("[Depth & Render Export] Done: {} rendered, {} "
                  "failed or skipped{}.".format(renderedCount, failureCount,
                                                CalibrationFailuresText(calibrationFailureCount)))
        finally:
            PrintConsoleLines(ConsoleBlockEnd())
        QtWidgets.QMessageBox.information(
            self, "Depth & Render Export",
            "Rendered {} to:\n{}{}{}".format(
                FormatCountWithNoun(renderedCount, "viewpoint", "viewpoints"),
                outputFolder,
                "\n\n{} could not be rendered (see Console).".format(
                    FormatCountWithNoun(failureCount, "viewpoint", "viewpoints"))
                if failureCount else "", CalibrationFailuresMessage(calibrationFailureCount)))


# ===================================================================
# About text
# ===================================================================

_ABOUT_HTML = """
<h2>Depth &amp; Render Export</h2>
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

<h3>Two different depths</h3>
<p>This tool offers two depth-related exports, and the two depths
involved must not be confused:</p>
<ul>
<li>The <b>Depth maps</b> tab saves the multi-view stereo depth maps that Metashape computes from
the images during depth-map generation and keeps in the project (<code>chunk.depth_maps</code>).
They share the viewpoint of the original images, with the numbers of pixel columns and rows that the
chosen depth-map quality sets (&lsquo;High&rsquo; halves those of the images, &lsquo;Medium&rsquo;
quarters them).</li>
<li>The <b>Render from triangular mesh</b> tab <i>synthesises</i> depth (and images and masks) from
the triangular mesh surface, a different, model-derived quantity. Its depth TIFF stays in internal
chunk units, and the Console prints the chunk scale that converts it to metres. By contrast, the
32-bit export of the Depth maps tab is already multiplied by the chunk scale.</li>
</ul>
<p>For the same image, the two depths also differ in their source, in
their gaps, and in their numbers of pixel columns and rows:</p>
<ul>
<li><b>Source:</b> dense stereo matching of the image with overlapping images gives pairwise depth
maps. Metashape merges the pairwise depth maps into the combined depth map of the image, filtering
wrong depth measurements and, with the chosen depth filtering mode, noise (Metashape manual, version
2.3). A rendered depth comes from the triangular mesh, which Metashape builds from such depth maps,
from a point cloud, or from other sources, and which can bridge gaps by interpolation (Metashape
manual, version 2.3).</li>
<li><b>Gaps:</b> the exported level of a depth map has no depth at the pixels where the matching or
the filtering left none at that level. A rendered depth covers every pixel at which the triangular
mesh is seen, including where the mesh bridges a gap. It has none where the view misses the mesh or,
with <b>Cull back faces</b>, meets only back faces of it.</li>
<li><b>Numbers of pixel columns and rows:</b> a depth map has those that its quality sets, and
&lsquo;Ultra High&rsquo; keeps those of the image (Metashape manual, version 2.3). A rendered depth
image has those of the image, or as many as the virtual camera has virtual photosite columns and
rows.</li>
</ul>

<h3>What the depth values are</h3>
<p>The depth of the <b>Render from triangular mesh</b> tab is the depth
along the camera axis, the Z coordinate of the surface point in camera
coordinates, not the slant distance from the perspective
centre to the surface point. In Metashape 2.3.2, a plane that a frame
camera views square on gets the same depth at every pixel, although
the slant distance from the perspective centre grows towards the
corners of the image. The Metashape manual (version 2.3) does not say
whether the stored depth maps of the <b>Depth maps</b> tab hold the
depth along the camera axis as well. A check in Metashape 2.3.2 with
the stored depth maps of a plane seen by a frame camera indicates that
they do. Read as depths along the camera axis, they put the points on
the plane. Read as slant distances, they do not. Other camera models
have not been checked, so the calibration file keeps both cases.</p>
<p>Metashape keeps a calibration with each stored depth map (the depth-map calibration of its Python
interface), which relates the pixel positions of the depth map to the directions of the rays. The
numbers of pixel columns and rows of the depth map do not suffice for that. A depth image alone
therefore does not suffice to compute 3D points or to register the depth to the original image. Both
also need the calibration (of the depth map, the image, or the virtual camera), the orientation of
the image or keyframe, and the chunk scale. For a normalised export, the depths that its lowest and
highest valid grey levels stand for are needed as well. A normalised export keeps at most 255
(8-bit) or 65,535 (16-bit) levels for the valid depths, so the depths decoded from its grey levels
are quantised and do not restore the floating-point depths exactly. Both tabs save these data in a
calibration file next to each depth image (see Calibration files). For a stored depth map, the file
holds the calibration of the depth map. This calibration can differ from that of the image (in
Metashape 2.3.2, it has all lens distortion coefficients set to zero, even for an image with lens
distortion). Registering the depth to the original image therefore also needs the calibration of the
image, which the file does not hold.</p>

<h3>Calibration files</h3>
<p>With <b>Write a calibration file (.json)</b>, which is on by
default, each depth image gets a JSON file with the same name next to
it, for example <code>IMG_0001_depthmap32.json</code> next to
<code>IMG_0001_depthmap32.tif</code> and
<code>IMG_0001_render_depth.json</code> next to
<code>IMG_0001_render_depth.tif</code>. The file holds:</p>
<ul>
<li>The project, the chunk (label and key), and the image (label and
key) or the keyframe (label and number).</li>
<li>What the depth values are, their unit, and the factor by which they were multiplied. The factor
is the chunk scale for the 32-bit export of the Depth maps tab, with values in metres, when the
chunk has a physical scale. Otherwise the factor is 1, as for a rendered depth, with values in
internal chunk units. The values of a normalised export are grey levels, which have no factor. For
such an export, the file gives the depths that its lowest and highest valid grey levels stand for,
and whether the levels are inverted.</li>
<li>The calibration with which the depth image was made: that of the depth map, of the image, or of
the virtual camera. For this calibration, the file holds the camera model, the numbers of pixel
columns and rows, c<sub>pix</sub>, cx, cy, b1, b2, k1 to k4, and p1 to p4. The file also holds the
equations of the frame camera and the calibration file that Metashape writes itself (XML). The file
states whether the calibration has additional corrections, with their type, the number of their
coefficients, and their extent. The equations leave additional corrections out. For a camera model
other than the frame camera, a note says that the equations and the steps have been checked for the
frame camera only.</li>
<li>The camera transform (from camera coordinates to internal chunk coordinates), the chunk
transform (from internal chunk coordinates to world coordinates), and how they apply to points. The
physical chunk scale and the coordinate reference system (CRS), with its name and well-known text.
The file says that the chunk transform leads to geocentric coordinates only in a georeferenced chunk
whose transform is fitted to the reference data. It also says that the transform that Align
Coordinate Axes to Region of Region Tools gives a chunk without a physical scale leads to arbitrary
local coordinates. In addition, it says that two kinds of chunk have local coordinates that are not
georeferenced. The first has a geographic, projected, or geocentric CRS but no placement on the
Earth (no translation and no rotation from reference coordinates, as with Metashape scale bars
alone). The second has the axes and the origin that Region Tools gave a chunk with a physical scale.</li>
<li>For an image with rolling-shutter compensation, what the file does not hold: it has the static
camera transform only. A rendered depth is rendered from that static transform, so the camera motion
during the readout is not part of it. The Metashape manual does not say whether a stored depth map
follows the motion.</li>
<li>For a rendered depth, whether back faces were culled and whether an
alpha channel was added. The Metashape Python reference does not say
whether <code>Model.renderDepth</code> applies the lens distortion of
the calibration exactly at every pixel position.</li>
<li>How to compute the 3D model point of a pixel: its depth d in internal chunk units and the
normalised coordinates (x, y) of the ray through its pixel position from the calibration. The
normalised coordinates are x&nbsp;=&nbsp;X/Z and y&nbsp;=&nbsp;Y/Z of the ray that <code>Calibration.unproject</code>
gives in Metashape. The steps continue with the point (d&nbsp;x, d&nbsp;y, d) in camera coordinates
for a depth along the camera axis and end with the two transforms. For a stored depth map, and for a
rendered depth of a camera model other than the frame camera, the file also gives the point for the
case in which its depth is the slant distance.</li>
<li>Notes, which say that the file records the geometry and the decoding of its depth image as far
as MetaFOCUS can read them. The notes also say that the file does not guarantee that every depth
image can be back-projected in the same way. The reason is that a camera model other than the frame
camera, additional corrections, the motion of a rolling shutter, or a depth convention that the
Metashape documentation leaves open can need further information or another mapping. For a
normalised export, the notes also say that its grey levels are quantised depths, which decoding does
not restore exactly.</li>
</ul>
<p>The pixel coordinates are those of Metashape: (0, 0) is the top-left corner of the image, and the
pixel in column i and row j, counted from 0, lies at (i&nbsp;+&nbsp;0.5, j&nbsp;+&nbsp;0.5). Values
that JSON cannot hold, such as a coefficient that is not a finite number, are written as null. What
could not be read or does not fit, for example a calibration whose numbers of pixel columns and rows
differ from those of the depth image, is listed under &lsquo;problems&rsquo; and named in the
Console.</p>
<p>Each tab asks before it replaces an existing calibration file, as for the depth images. With the
option off, it asks before it removes the calibration file next to a depth image that it writes,
which would not describe the new depth image. It removes an earlier calibration file immediately
before it saves the depth image, so that the file never describes another depth image. When the
depth image then cannot be saved, the Console names the removed calibration file. A calibration file
that cannot be written is named in the Console and leaves its depth image without one, counted
separately from the depth images that could not be written.</p>

<h3>What Metashape exports itself</h3>
<p>Metashape&rsquo;s own <b>Export Depth&hellip;</b> command, in the context menu of an image,
exports a diffuse map, a depth map, and a normal map of the image. The command needs a triangular
mesh in the chunk for all three (Metashape manual, version 2.3). The manual does not say how the
maps are made. Still, this requirement indicates that its depth map comes from the triangular mesh,
like that of the Render from triangular mesh tab, and not from depth-map generation. The manual
documents no command that exports the depth maps of depth-map generation. Metashape keeps these
depth maps in the project when &lsquo;Keep depth maps&rsquo; is enabled and shows them as an overlay
in the Photo view (Show Depth Maps). <b>Render Photos&hellip;</b> (Render Images&hellip; in the menu
reference) renders new frames from viewpoints set in the Model view, for example for lenticular
photography. According to the manual, Render Photos&hellip; works only in a non-georeferenced chunk.
The Animation pane renders the frames of a camera track as separate images or as a video. The manual
names no depth or mask output for either.</p>
<p>This tool adds the export of the stored depth maps themselves, for
all images that have one or for the selected images (Depth maps tab).
Its Render from triangular mesh tab renders the triangular mesh from
the viewpoints of the oriented images, all or the selected ones, or of
the keyframes of the animation track through a virtual camera. The
virtual camera can also replace the calibrations of the oriented images.
The tab writes a rendered image, a depth image, and a mask in any
combination. It writes no normal map, which Export Depth&hellip; does.</p>
<p>On both tabs, a line below the option for the selected images gives how many images Metashape
reports as selected and how many of them have a depth map (Depth maps tab) or are oriented (Render
from triangular mesh tab). An export uses these images, whatever the Photos pane highlights. The
lines are refreshed when the window is activated or deactivated and when a tab starts its export. An
export of the selected images names up to 20 of them in the Console and gives the number of the
others. When Metashape reports no selected image
that the tab can use, a message says so and what to do. Selecting another item of the Workspace
pane, such as the triangular mesh, the tie points, or a folder, clears the selection of images that
Metashape reports, although the Photos pane may still highlight them. Selecting these images again starts with a click on an image that is not highlighted, because
a click on an image that the Photos pane still highlights does not select it in Metashape.</p>

<h3>Depth maps tab</h3>
<p>Metashape keeps each depth map in several levels, which its Photo view shows one by one, numbered
from 1, or all together. The tab exports level 0 alone, in the numbering of the Python application
programming interface (API) of Metashape (DepthMap.image(level)), the most detailed level (Agisoft&rsquo;s metashape-scripts repository
on GitHub, issue 28: &ldquo;zero level is the most detailed&rdquo;). In a check in Metashape 2.3.2,
level 0 had 200 &times; 150 pixels and level 1 had 100 &times; 75. Without a level, DepthMap.image()
gives a different image: in the same check it filled pixels without depth at level 0 with depths of
level 1. The tab asks for level 0 by its number, so that a pixel without depth at level 0 stays
without depth. The Metashape documentation does not say which of the levels in the Photo view is
level 0. The calibration file names the level and gives the pixel count of the depth map. In the
same check, DepthMap.image(level) gave None for levels 2 to 5. When Metashape gives no image of
level 0 of a depth map, nothing is written or removed for that image. The Console says that
Metashape gave no image of level 0 of that depth map.</p>
<p>The tab exports the stored depth maps of all images that have one, or of the selected images
only, as:</p>
<ul>
<li><b>32-bit float</b>: the depth multiplied by the chunk scale. The values are therefore in metres
when the chunk is georeferenced, scaled with Metashape scale bars or with reference coordinates in a
local CRS, or given an imported chunk transform. Note that the values are in metres even when the
unit of that local CRS is not the metre. A chunk has no physical scale without Metashape scale bars,
reference coordinates, and an imported chunk transform, or with the transform that Region Tools
records for a chunk without one. For such a chunk the tool asks before it exports the values in
internal chunk units. This export does not need NumPy.</li>
<li><b>16-bit or 8-bit greyscale, normalised</b>: each depth map is
stretched to its own range from the minimum to the maximum depth,
optionally inverted so that nearer is brighter. These exports need
NumPy. Metashape runs scripts with its own Python installation, which
does not include NumPy. Without it, the tab follows the article
&ldquo;How to install external Python module to Metashape Professional
package&rdquo; of the Agisoft knowledge base. When it finds that Python
installation, it gives the command that installs NumPy into it.
Otherwise it names the article. On Windows it gives one line for the
Command Prompt and one for Windows PowerShell, which needs the call
operator &amp; before the quoted path. In both lines the path has
backslashes only. Metashape must be restarted afterwards.</li>
</ul>
<p>Pixels without depth (zero, negative, or not finite) are ignored
when normalising and are written as 0. Valid depths use the grey
levels from 1 to 255 (8-bit) or to 65,535 (16-bit), so no valid depth
shares the no-data level.  The normalised files have one channel: an
8-bit greyscale PNG and a 16-bit greyscale TIFF with Deflate
compression, written by MetaFOCUS itself.  The TIFF stores horizontal
differences of the levels (Predictor 2) and a resolution without an
absolute unit, because an image has a pixel count, not a size.</p>

<h3>Render from triangular mesh tab</h3>
<p>This tab uses the rendering functions of the Metashape Python interface (<code>Model.renderImage</code>,
<code>renderDepth</code>, and <code>renderMask</code>) to render the triangular mesh from each
viewpoint. It writes any combination of a rendered image, a 32-bit depth image, and a mask. Choose
the <b>oriented images</b> (rendered from their perspective centres with their own calibration) or
the <b>keyframes</b> of the animation track (numbered in the order in which the chunk lists them).
Only the keyframes themselves are rendered, not the interpolated frames of an animation movie: an
animation with 10 keyframes gives 10 viewpoints. Back-face culling and an alpha channel (added to
the rendered image and to the depth) are optional.</p>
<p>Because keyframes carry only a viewpoint and no calibration of their own, rendering them requires
a <b>virtual camera</b>, set in the tab by the numbers of its virtual photosite columns and rows,
the principal distance, and the sensor width. The principal distance in pixels is derived from these
values. The rendered images have as many pixel columns and rows as the virtual camera has virtual
photosite columns and rows. Every keyframe is rendered through the virtual camera. The virtual
camera is a frame camera with its principal point at the image centre and all lens distortion
coefficients set to zero. For example, 6,000 &times; 4,000 virtual photosites with a principal
distance of 50&nbsp;mm on a 36&nbsp;mm-wide sensor give a full-frame view, rendered as images of
6,000 pixel columns and 4,000 pixel rows. Oriented images use their own calibration by default, but
ticking the override renders them, from their own viewpoints, through the same virtual camera. All
renderings then have the same numbers of pixel columns and rows and the same principal distance, for
example when the images come from different cameras.</p>
<p>Should Metashape give no rendered image, depth image, or mask for a
viewpoint (<code>Model.renderImage</code>, <code>renderDepth</code>, or
<code>renderMask</code> returning None), the Console says so for that
viewpoint. An earlier depth image that is not replaced then keeps its
calibration file. Such a return has not been observed in Metashape
2.3.2.</p>

<h3>Output</h3>
<p>Files are written to a chosen folder and named after each image
(keyframe renders are prefixed with a frame number):</p>
<ul>
<li>Depth maps tab: <code>&lt;name&gt;_depthmap32.tif</code>,
<code>&lt;name&gt;_depthmap16.tif</code>, or
<code>&lt;name&gt;_depthmap8.png</code>, each with its calibration
file <code>&lt;name&gt;_depthmap32.json</code>,
<code>&lt;name&gt;_depthmap16.json</code>, or
<code>&lt;name&gt;_depthmap8.json</code>.</li>
<li>Render from triangular mesh tab:
<code>&lt;name&gt;_render.png</code> (or .jpg, .tif),
<code>&lt;name&gt;_render_depth.tif</code> with its calibration file
<code>&lt;name&gt;_render_depth.json</code>, and
<code>&lt;name&gt;_render_mask.png</code>.</li>
</ul>
<p>The two tabs never write the same file name, so both exports can share a folder. An image whose
label is shared with another image of the chunk gets its Metashape key appended (for example <code>DSC_0001_3</code>),
with the same name in both tabs and in every export, whichever images are exported. The names are
unique only among the images of the active chunk, so an export of another chunk, or a later export,
into the same folder can give files the names of files that are already there. Each tab therefore
asks before it replaces files that already exist in the chosen folder, and exports nothing when the
answer is &lsquo;No&rsquo;. Each export is echoed to the Console pane with its main settings, the
images that failed, and the result.</p>
"""


# ===================================================================
# Main dialog
# ===================================================================

class DepthRenderDialog(QtWidgets.QDialog):
    """Tabbed window for depth-map export and triangular mesh
    rendering."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Depth & Render Export")
        self.setWindowFlags(
            self.windowFlags()
            & ~QtCore.Qt.WindowContextHelpButtonHint)
        self.resize(560, 560)
        layout = QtWidgets.QVBoxLayout(self)

        tabs = QtWidgets.QTabWidget()
        self.depthMapsTab = DepthMapsTab()
        self.meshRenderTab = MeshRenderTab()
        tabs.addTab(self.depthMapsTab, "Depth maps")
        tabs.addTab(self.meshRenderTab, "Render from triangular mesh")
        layout.addWidget(tabs, 1)

        buttonRow = QtWidgets.QHBoxLayout()
        aboutButton = QtWidgets.QPushButton("About")
        aboutButton.clicked.connect(self._OnAbout)
        closeButton = QtWidgets.QPushButton("Close")
        closeButton.clicked.connect(self.close)
        buttonRow.addWidget(aboutButton)
        buttonRow.addStretch()
        buttonRow.addWidget(closeButton)
        layout.addLayout(buttonRow)

    def _RefreshSelectionStatus(self):
        """Show on both tabs how many images Metashape reports as selected."""
        self.depthMapsTab.RefreshSelectionStatus()
        self.meshRenderTab.RefreshSelectionStatus()

    def changeEvent(self, event):
        """Refresh the selection lines when the window is activated or
        deactivated, for example when it comes back after a message."""
        # The window can change before the tabs exist, while it is built.
        if event.type() == QtCore.QEvent.ActivationChange and getattr(self, "meshRenderTab", None) is not None:
            self._RefreshSelectionStatus()
        super().changeEvent(event)

    def _OnAbout(self):
        aboutDialog = QtWidgets.QDialog(self)
        aboutDialog.setWindowTitle("About: Depth & Render Export")
        aboutDialog.setWindowFlags(
            aboutDialog.windowFlags()
            & ~QtCore.Qt.WindowContextHelpButtonHint)
        aboutDialog.resize(580, 620)
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


# ===================================================================
# Menu registration
# ===================================================================

def _ShowDepthRenderExporterDialog():
    parent = QtWidgets.QApplication.activeWindow()
    dialog = DepthRenderDialog(parent)
    dialog.exec_()


def RegisterMenuItem():
    """Register this tool in the MetaFOCUS menu."""

    try:
        Metashape.app.removeMenuItem(_MENU_LABEL)
    except Exception as suppressedError:
        ReportSuppressedError("depthRenderExporter.RegisterMenuItem", suppressedError)
    iconPath = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "icons", _ICON_NAME)
    if os.path.exists(iconPath):
        Metashape.app.addMenuItem(
            _MENU_LABEL, _ShowDepthRenderExporterDialog, icon=iconPath)
    else:
        Metashape.app.addMenuItem(_MENU_LABEL, _ShowDepthRenderExporterDialog)
