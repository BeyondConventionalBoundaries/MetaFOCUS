"""
Unpinned Marker Projection Remover for Agisoft Metashape Professional
=====================================================================

Removes the unpinned (blue-flagged) marker projections of the active
chunk while preserving the pinned (green-flagged) projections.

In Metashape, marker projections can be either:
  - Pinned (green flag):  placed or confirmed by the user.
  - Unpinned (blue flag): estimated automatically by Metashape, for
    example in the other images that see a marker placed in one
    image of a chunk with oriented images, or created on the
    triangular mesh.

Unpinned projections may be inaccurate and can degrade the
georeferencing solution.  This tool removes them in bulk, leaving
the pinned projections.  Markers that would keep fewer than two
usable projections (valid projections on enabled images with an
estimated orientation), too few to triangulate their position from
the images alone, are listed before anything is removed.  The tool
counts no other projection, which errs on the side of keeping
unpinned projections: a pinned projection on a disabled image that is
still oriented does not count, although Optimize Cameras used one in
a check in Metashape 2.3.2.
By default every listed marker keeps all its projections, because
the removal cannot be undone.  The dialog can
instead keep the projections of the markers without any usable
pinned projection only, or remove the unpinned projections of every
marker.  Fiducial marks of scanned film images are never changed and
are left out of the marker and projection counts and of the list:
they are measured for the interior orientation and are not object
points of the adjustment.  The dialog and the confirmation give their
number apart.

Author:      Geert J. Verhoeven (ORCID: 0000-0003-4825-9604)
Version:     1.0.0 (2026-11-18)
Date:        2026-03-24
Licence:     GPL-3.0-or-later WITH GPL-3.0-linking-exception
Contact:     geert.verhoeven@univie.ac.at
Requires:    Agisoft Metashape Professional 2.x+
Developed for: Agisoft Metashape Professional 2.3.2

Usage:
    Loaded automatically by the MetaFOCUS loader.  MetaFOCUS >
    Control Measurements and Residuals > Unpinned Marker Projection
    Remover opens the dialog.
"""

import Metashape
import os

from PySide2 import QtWidgets, QtCore
from .photogrammetryUtilities import (
    FormatCountWithNoun, IsActiveMarkerProjection, IsObjectSpaceMarker, ReportSuppressedError)


# ===================================================================
# Core logic
# ===================================================================

def SkipPolicyLabel(skipPolicy):
    """The label of *skipPolicy* in the dialog (SKIP_POLICY_CHOICES)."""
    for choicePolicy, policyLabel in SKIP_POLICY_CHOICES:
        if choicePolicy == skipPolicy:
            return policyLabel
    return str(skipPolicy)


def RemovalStartConsoleLines(chunkLabel, policyLabel):
    """The lines that open the Console block of a removal: the chunk and
    the policy for markers that would keep fewer than two usable
    projections, as the dialog names it."""
    return ["", "=" * 60, "Unpinned Marker Projection Remover", "=" * 60,
            "Chunk: {}".format(chunkLabel), "Policy: {}".format(policyLabel), ""]


# What happens to a marker that would keep fewer than two usable
# projections (too few to triangulate its position from the images
# alone) if all its unpinned projections were removed.  By default
# every such marker keeps all its projections: the removal cannot be
# undone, and a marker with a single usable pinned projection would
# otherwise keep one ray (a check point then drops out of the
# image-based check, and a tie-only marker loses its position).
# The drop-down list of the dialog offers the policies in this order,
# the default first.
SKIP_MARKERS_WITHOUT_PINNED_PROJECTIONS = "withoutPinned"
SKIP_MARKERS_LEFT_WITH_FEWER_THAN_TWO_PROJECTIONS = "fewerThanTwo"
SKIP_NO_MARKERS = "none"
DEFAULT_SKIP_POLICY = SKIP_MARKERS_LEFT_WITH_FEWER_THAN_TWO_PROJECTIONS
SKIP_POLICY_CHOICES = (
    (SKIP_MARKERS_LEFT_WITH_FEWER_THAN_TWO_PROJECTIONS,
     "Keep the projections of every marker listed above"),
    (SKIP_MARKERS_WITHOUT_PINNED_PROJECTIONS,
     "Keep the projections of markers without any usable pinned projection"),
    (SKIP_NO_MARKERS,
     "Remove the unpinned projections of every marker"),
)


def IsMarkerSkipped(usablePinnedCount, unpinnedCount, skipPolicy):
    """True when the unpinned projections of a marker with these counts
    (usable pinned projections, see IsActiveMarkerProjection, and stored
    unpinned projections) are kept under the skip policy."""
    if unpinnedCount == 0:
        return False
    if skipPolicy == SKIP_MARKERS_WITHOUT_PINNED_PROJECTIONS:
        return usablePinnedCount == 0
    if skipPolicy == SKIP_MARKERS_LEFT_WITH_FEWER_THAN_TWO_PROJECTIONS:
        return usablePinnedCount < 2
    return False


def FindMarkersLeftWithFewerThanTwoProjections(markerDetails):
    """Entries of CountMarkerProjections whose marker would keep fewer
    than two usable projections, too few to triangulate its position
    from the images alone, if all its unpinned projections were
    removed."""
    return [detail for detail in markerDetails
            if detail["unpinned"] > 0 and detail["usablePinned"] < 2]


def CountRemovableProjections(markerDetails, skipPolicy):
    """Number of unpinned projections that the removal deletes under
    the skip policy."""
    return sum(detail["unpinned"] for detail in markerDetails
               if not IsMarkerSkipped(detail["usablePinned"], detail["unpinned"],
                                      skipPolicy))


def _JoinLabels(labels, shownCount=20):
    """Comma-separated labels, shortened to the first shownCount."""
    if len(labels) <= shownCount:
        return ", ".join(labels)
    # The rest counts as the last item, with the serial comma after two or
    # more labels.
    separator = ", and " if shownCount >= 2 else " and "
    return "{}{}{} more".format(", ".join(labels[:shownCount]), separator, len(labels) - shownCount)


def BuildRemovalConfirmationText(markerDetails, skipPolicy, fiducialMarkCount=0):
    """Confirmation question: how many unpinned projections are removed,
    which markers are skipped, which markers are left with fewer than
    two projections, and how many fiducial marks the removal does not
    change (they are not in markerDetails, see CountMarkerProjections)."""
    removableCount = CountRemovableProjections(markerDetails, skipPolicy)
    markersAtRisk = FindMarkersLeftWithFewerThanTwoProjections(markerDetails)
    skippedLabels = [detail["label"] for detail in markersAtRisk
                     if IsMarkerSkipped(detail["usablePinned"], detail["unpinned"],
                                        skipPolicy)]
    weakenedLabels = [detail["label"] for detail in markersAtRisk
                      if not IsMarkerSkipped(detail["usablePinned"],
                                             detail["unpinned"], skipPolicy)]
    paragraphs = ["Remove {}?".format(FormatCountWithNoun(
        removableCount, "unpinned (blue-flagged) marker projection",
        "unpinned (blue-flagged) marker projections"))]
    if skippedLabels:
        paragraphs.append("Skipped markers (their unpinned projections "
                          "are kept): {}.".format(_JoinLabels(skippedLabels)))
    if weakenedLabels:
        paragraphs.append("Markers left with fewer than two usable projections, "
                          "too few to triangulate their position from the "
                          "images alone: {}.".format(_JoinLabels(weakenedLabels)))
    if fiducialMarkCount:
        paragraphs.append("Not changed: {} of scanned film images.".format(FormatCountWithNoun(
            fiducialMarkCount, "fiducial mark", "fiducial marks")))
    paragraphs.append("This cannot be undone.")
    return "\n\n".join(paragraphs)


def _StoredProjections(marker):
    """(camera, projection) pairs of the stored projections of a
    marker."""
    storedProjections = []
    for camera in list(marker.projections.keys()):
        projection = marker.projections[camera]
        if projection is not None:
            storedProjections.append((camera, projection))
    return storedProjections


def MarkerProjectionCounts(label, storedProjections):
    """Counts of one marker from its stored (camera, projection) pairs,
    with the keys label, pinned and unpinned (stored projections), and
    usablePinned (pinned projections that the tool counts as usable
    rays: valid, on enabled, oriented images, see
    IsActiveMarkerProjection, the rule that the observation counts of
    the other tools use as well).  The count errs on the side of keeping
    unpinned projections: it leaves out a pinned projection on a
    disabled image that is still oriented, although Optimize Cameras
    used one in a check in Metashape 2.3.2."""
    return {
        "label": label,
        "pinned": sum(1 for camera, projection in storedProjections if projection.pinned),
        "unpinned": sum(1 for camera, projection in storedProjections if not projection.pinned),
        "usablePinned": sum(1 for camera, projection in storedProjections
                            if projection.pinned and IsActiveMarkerProjection(camera, projection)),
    }


def CountFiducialMarks(chunk):
    """Number of fiducial marks of scanned film images in the chunk,
    which the tool never changes and leaves out of the marker and
    projection counts and of the list, but gives apart in the dialog
    and the confirmation (see IsObjectSpaceMarker)."""
    return sum(1 for marker in chunk.markers if not IsObjectSpaceMarker(marker))


def CountMarkerProjections(chunk):
    """Count pinned and unpinned projections across all markers.
    Fiducial marks of scanned film images are left out: they are
    measured for the interior orientation, not object points of the
    adjustment, and the tool never changes them.

    Returns
    -------
    markerCount : int
    pinnedCount : int
        Stored pinned projections.
    unpinnedCount : int
        Stored unpinned projections.
    markerDetails : list of dict
        Per-marker breakdown with keys: label, pinned, unpinned, and
        usablePinned (see MarkerProjectionCounts).
    """
    markerCount = 0
    pinnedCount = 0
    unpinnedCount = 0
    markerDetails = []

    for marker in chunk.markers:
        if not IsObjectSpaceMarker(marker):
            continue
        markerCount += 1
        detail = MarkerProjectionCounts(marker.label, _StoredProjections(marker))
        pinnedCount += detail["pinned"]
        unpinnedCount += detail["unpinned"]
        markerDetails.append(detail)

    return markerCount, pinnedCount, unpinnedCount, markerDetails


def RemoveUnpinnedProjections(chunk, skipPolicy=DEFAULT_SKIP_POLICY):
    """Remove the unpinned (blue-flagged) marker projections.

    Markers whose unpinned projections are kept under the skip policy
    (by default every marker that would keep fewer than two usable
    projections) are left unchanged, and so are the fiducial marks of
    scanned film images, whatever the policy.

    Returns
    -------
    removedCount : int
        Total number of unpinned projections removed.
    affectedMarkerCount : int
        Number of markers that had at least one removal.
    """
    removedCount = 0
    affectedMarkerCount = 0

    for marker in chunk.markers:
        if not IsObjectSpaceMarker(marker):
            continue
        storedProjections = _StoredProjections(marker)
        detail = MarkerProjectionCounts(marker.label, storedProjections)
        unpinnedCameras = [camera for camera, projection in storedProjections
                           if not projection.pinned]
        if IsMarkerSkipped(detail["usablePinned"], len(unpinnedCameras), skipPolicy):
            print("  {}: skipped, {} kept ({} pinned, {} usable)".format(
                marker.label,
                FormatCountWithNoun(len(unpinnedCameras), "unpinned projection",
                                    "unpinned projections"),
                detail["pinned"], detail["usablePinned"]))
            continue
        for camera in unpinnedCameras:
            marker.projections[camera] = None
        markerRemovedCount = len(unpinnedCameras)
        if markerRemovedCount > 0:
            removedCount += markerRemovedCount
            affectedMarkerCount += 1
            print("  {}: unpinned projections removed ({})".format(
                marker.label, markerRemovedCount))

    return removedCount, affectedMarkerCount


# ===================================================================
# GUI
# ===================================================================

_ABOUT_HTML = """
<h2>Unpinned Marker Projection Remover</h2>
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
<p>This tool removes the unpinned (blue-flagged) marker projections of the active chunk and keeps
the pinned (green-flagged) projections. Markers that would keep fewer than two usable projections
(too few to triangulate their position from the images alone) are listed before anything is removed.
By default, the projections of every listed marker are kept. A projection is usable when it is valid
and lies on an enabled image with an estimated orientation. The tool counts no other projection, which
errs on the side of keeping unpinned projections. A pinned projection on a disabled image that is
still oriented is therefore not counted, although Optimize Cameras used one in a check in Metashape
2.3.2. Fiducial marks of scanned film images are measured for the interior orientation and are not object points of
the adjustment. The tool never changes their projections, and it leaves them out of the marker and
projection counts and of the list, giving their number separately.</p>

<h3>Background</h3>
<p>In Metashape, a marker placed in one image of a chunk with
oriented images,
or created on the triangular mesh, is projected automatically into
the other images that see it.  These estimated positions appear
with a <b>blue flag</b> and are called <i>unpinned</i> projections.
Positions placed or confirmed by the user have a <b>green flag</b>
and are <i>pinned</i>.</p>

<p>Unpinned projections can be problematic because they are positions that Metashape estimated
automatically and that the user may never have inspected: poor texture, partial occlusion, motion
blur, or a position near the image edge can misplace them. <b>Metashape&rsquo;s documentation does
not say whether the bundle adjustment weights them differently from pinned projections</b>.
Consequently, they cannot be assumed to be down-weighted, and an inaccurate unpinned projection can
degrade the georeferencing.</p>

<p>Removing unpinned projections before running <i>Optimize Cameras</i> means that only pinned
projections enter the bundle adjustment, apart from the projections that the chosen option keeps. By
default, these are the projections of every marker that would keep fewer than two usable
projections. In the author&rsquo;s experience, the removal leads to a more reliable georeferencing
solution. Note that &lsquo;pinned&rsquo; means placed or confirmed by the user. This does not by
itself guarantee the quality of a measurement, so pinned projections deserve inspection as well (see
the Marker Residual Inspector).</p>
<p>A marker needs at least two usable projections for its position to be triangulated from the
images. A control point with one usable projection can still take part in the adjustment through its
reference coordinates, but its position then rests on them. Two usable projections are a minimum,
not a guarantee of a good intersection geometry. A marker whose projections are all unpinned would
lose all its image observations. That is why it should first be refined and pinned in at least two
images.</p>

<h3>Usage</h3>
<p>The dialog shows the number of markers, of pinned and unpinned projections, and of usable pinned
projections in the active chunk. It also shows the number of fiducial marks, which are not changed,
when there are any. Markers that would keep fewer than two usable projections are listed with their
pinned, usable pinned, and unpinned counts. The drop-down list below them sets what happens to these
markers:</p>
<ul>
<li><i>Keep the projections of every marker listed above</i> (the
default): no listed marker loses a projection.</li>
<li><i>Keep the projections of markers without any usable pinned
projection</i>: a marker created on the triangular mesh, for example,
keeps all its projections.  A listed marker with one usable pinned
projection, however, keeps that single ray.  A check point then drops
out of the image-based check, and a marker without reference
coordinates loses its position.</li>
<li><i>Remove the unpinned projections of every marker</i>: a
listed marker can be left with one usable projection or none, too
few to triangulate its position from the images alone.</li>
</ul>
<p>The <b>Remove</b> button shows how many unpinned projections the removal deletes under that
choice. The confirmation names the skipped markers and the markers left with fewer than two usable
projections, and it gives the number of fiducial marks, which are not changed. The Console lists
every marker that was changed or skipped, with its pinned and usable pinned counts. The operation
cannot be undone: if needed, the project should be saved first.</p>
"""


class UnpinnedMarkerProjectionRemoverDialog(QtWidgets.QDialog):
    """Dialog for removing unpinned marker projections."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Unpinned Marker Projection Remover")
        self.setWindowFlags(
            self.windowFlags()
            & ~QtCore.Qt.WindowContextHelpButtonHint)
        self.setMinimumWidth(460)
        self._BuildUi()

    def _BuildUi(self):
        mainLayout = QtWidgets.QVBoxLayout(self)

        chunk = Metashape.app.document.chunk

        # --- Chunk information -----------------------------------------
        chunkInformationGroup = QtWidgets.QGroupBox("Active chunk")
        chunkInformationLayout = QtWidgets.QFormLayout(chunkInformationGroup)

        chunkLabel = chunk.label if chunk else "(none)"
        chunkInformationLayout.addRow("Chunk:", QtWidgets.QLabel(chunkLabel))

        # Count projections (fiducial marks apart: they are never
        # changed, counted, or listed).
        markerDetails = []
        fiducialMarkCount = 0
        if chunk and chunk.markers:
            markerCount, pinnedCount, unpinnedCount, markerDetails = \
                CountMarkerProjections(chunk)
            fiducialMarkCount = CountFiducialMarks(chunk)
        else:
            markerCount = 0
            pinnedCount = 0
            unpinnedCount = 0
        self._markerDetails = markerDetails

        chunkInformationLayout.addRow("Markers:",
            QtWidgets.QLabel("{:,}".format(markerCount)))
        if fiducialMarkCount:
            fiducialMarkLabel = QtWidgets.QLabel("{:,}".format(fiducialMarkCount))
            fiducialMarkLabel.setToolTip(
                "Fiducial marks of scanned film images are measured for the\n"
                "interior orientation, not object points of the adjustment:\n"
                "the tool never changes their projections.")
            chunkInformationLayout.addRow(
                "Fiducial marks (not changed):", fiducialMarkLabel)
        chunkInformationLayout.addRow(
            "Pinned projections (green):",
            QtWidgets.QLabel("{:,}".format(pinnedCount)))

        # The tool counts the valid projections on enabled, oriented
        # images as rays, which errs on the side of keeping projections.
        usablePinnedLabel = QtWidgets.QLabel("{:,}".format(
            sum(detail["usablePinned"] for detail in markerDetails)))
        usablePinnedLabel.setToolTip(
            "Pinned projections that are valid and lie on enabled images\n"
            "with an estimated orientation, the rays that the tool counts\n"
            "once the unpinned projections are removed. A pinned projection\n"
            "on a disabled image that is still oriented does not count,\n"
            "although Metashape can adjust such an image.")
        chunkInformationLayout.addRow(
            "Usable pinned projections:", usablePinnedLabel)

        unpinnedLabel = QtWidgets.QLabel(
            "<b>{:,}</b>".format(unpinnedCount))
        chunkInformationLayout.addRow(
            "Unpinned projections (blue):",
            unpinnedLabel)

        mainLayout.addWidget(chunkInformationGroup)

        # --- Info text -------------------------------------------------
        if unpinnedCount == 0:
            nothingToRemoveLabel = QtWidgets.QLabel(
                "No unpinned (blue) projections found.\n"
                "Nothing to remove.")
            nothingToRemoveLabel.setWordWrap(True)
            mainLayout.addWidget(nothingToRemoveLabel)

        # --- Markers that would keep fewer than two usable projections -
        # Listed, with the choice whether their unpinned projections
        # are kept (default: every listed marker keeps them, see
        # DEFAULT_SKIP_POLICY).
        self.skipPolicyComboBox = QtWidgets.QComboBox()
        for skipPolicy, policyLabel in SKIP_POLICY_CHOICES:
            self.skipPolicyComboBox.addItem(policyLabel, skipPolicy)
        markersAtRisk = FindMarkersLeftWithFewerThanTwoProjections(
            markerDetails)
        if markersAtRisk:
            riskGroup = QtWidgets.QGroupBox(
                "Markers that would keep fewer than two usable projections "
                "({:,})".format(len(markersAtRisk)))
            riskLayout = QtWidgets.QVBoxLayout(riskGroup)
            riskList = QtWidgets.QListWidget()
            for detail in markersAtRisk:
                riskList.addItem("{}: {} pinned ({} usable), {} unpinned".format(
                    detail["label"], detail["pinned"], detail["usablePinned"],
                    detail["unpinned"]))
            riskList.setMaximumHeight(120)
            riskLayout.addWidget(riskList)
            riskLayout.addWidget(self.skipPolicyComboBox)
            mainLayout.addWidget(riskGroup)

        # --- Buttons ---------------------------------------------------
        buttonLayout = QtWidgets.QHBoxLayout()

        aboutButton = QtWidgets.QPushButton("About")
        aboutButton.clicked.connect(self._ShowAbout)
        buttonLayout.addWidget(aboutButton)

        buttonLayout.addStretch()

        self.removeButton = QtWidgets.QPushButton("")
        self._UpdateRemoveButton()
        self.removeButton.clicked.connect(self._OnRemove)
        self.skipPolicyComboBox.currentIndexChanged.connect(
            self._OnSkipPolicyChanged)
        buttonLayout.addWidget(self.removeButton)

        closeButton = QtWidgets.QPushButton("Close")
        closeButton.clicked.connect(self.reject)
        buttonLayout.addWidget(closeButton)

        mainLayout.addLayout(buttonLayout)

    def _SelectedSkipPolicy(self):
        """Skip policy chosen in the dialog."""
        return (self.skipPolicyComboBox.currentData()
                or DEFAULT_SKIP_POLICY)

    def _UpdateRemoveButton(self):
        """Show how many projections the removal deletes under the
        chosen skip policy, and allow it only when there are any."""
        removableCount = CountRemovableProjections(
            self._markerDetails, self._SelectedSkipPolicy())
        self.removeButton.setText("Remove {}".format(FormatCountWithNoun(
            removableCount, "unpinned projection", "unpinned projections")))
        self.removeButton.setEnabled(removableCount > 0)

    def _OnSkipPolicyChanged(self, selectedPolicyIndex):
        """Another skip policy was chosen: update the Remove button."""
        self._UpdateRemoveButton()

    def _OnRemove(self):
        """Confirm and remove unpinned projections."""
        skipPolicy = self._SelectedSkipPolicy()
        chunk = Metashape.app.document.chunk
        reply = QtWidgets.QMessageBox.question(
            self, "Unpinned Marker Projection Remover",
            BuildRemovalConfirmationText(self._markerDetails, skipPolicy,
                                         CountFiducialMarks(chunk)),
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No)

        if reply != QtWidgets.QMessageBox.Yes:
            return

        for consoleLine in RemovalStartConsoleLines(chunk.label, SkipPolicyLabel(skipPolicy)):
            print(consoleLine)

        removedCount, affectedMarkerCount = RemoveUnpinnedProjections(
            chunk, skipPolicy)

        # Refresh the Metashape UI.
        Metashape.app.update()

        print("")
        print("Unpinned projections removed: {:,}. Markers changed: "
              "{:,}.".format(removedCount, affectedMarkerCount))
        print("=" * 60)
        print("")

        QtWidgets.QMessageBox.information(
            self, "Unpinned Marker Projection Remover",
            "Unpinned projections removed: {:,}. Markers changed: "
            "{:,}.".format(removedCount, affectedMarkerCount))

        self.accept()

    def _ShowAbout(self):
        aboutDialog = QtWidgets.QDialog(self)
        aboutDialog.setWindowTitle(
            "About: Unpinned Marker Projection Remover")
        aboutDialog.setWindowFlags(
            aboutDialog.windowFlags()
            & ~QtCore.Qt.WindowContextHelpButtonHint)
        aboutDialog.setMinimumSize(520, 400)
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

_MENU_LABEL = "MetaFOCUS/Control Measurements and Residuals/Unpinned Marker Projection Remover"
_ICON_NAME = "unpinned_marker_projection_remover_32.png"
_TOOL_NAME = "Unpinned Marker Projection Remover"


def _ShowRemoverDialog():
    """Open the dialog, or tell (in a message box titled with the tool
    name) why it cannot be used yet."""
    document = Metashape.app.document
    parent = QtWidgets.QApplication.activeWindow()
    if not document:
        QtWidgets.QMessageBox.information(
            parent, _TOOL_NAME, "Open a project first.")
        return
    if not document.chunk:
        QtWidgets.QMessageBox.information(
            parent, _TOOL_NAME, "Open a project with at least one chunk first.")
        return
    if not document.chunk.markers:
        QtWidgets.QMessageBox.information(
            parent, _TOOL_NAME, "The active chunk has no markers.")
        return
    UnpinnedMarkerProjectionRemoverDialog(parent).exec_()


def RegisterMenuItem():
    """Register the menu item.  Called by the MetaFOCUS loader."""
    try:
        Metashape.app.removeMenuItem(_MENU_LABEL)
    except Exception as suppressedError:
        ReportSuppressedError("unpinnedMarkerProjectionRemover.RegisterMenuItem", suppressedError)
    iconPath = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "icons", _ICON_NAME)
    if os.path.exists(iconPath):
        Metashape.app.addMenuItem(
            _MENU_LABEL, _ShowRemoverDialog, icon=iconPath)
    else:
        Metashape.app.addMenuItem(_MENU_LABEL, _ShowRemoverDialog)
