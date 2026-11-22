"""
Image Subset Selector for Agisoft Metashape Professional
========================================================

Enables or disables images at regular intervals.  Useful for
thinning a dense image set (e.g. video frames) or for selecting
image subsets for validation experiments.

Operates on the active chunk.  The user specifies an interval N,
a start offset, and an action:
  - Disable every Nth image (leave the others unchanged).
  - Enable only every Nth image (disable the rest).
  - Enable all images (reset).

Only single images and the master images of multi-camera rigs are
counted (the secondary images of a rig and the keyframes of an
animation track are skipped), in the order of the chunk's camera
list in Metashape.

Author:      Geert J. Verhoeven (ORCID: 0000-0003-4825-9604)
Version:     1.0.0 (2026-11-18)
Date:        2026-03-24
Licence:     GPL-3.0-or-later WITH GPL-3.0-linking-exception
Contact:     geert.verhoeven@univie.ac.at
Requires:    Agisoft Metashape Professional 2.x+
Developed for: Agisoft Metashape Professional 2.3.2

Usage:
    Loaded automatically by the MetaFOCUS loader.  Open the dialog
    with MetaFOCUS > Filtering and Project Management > Image
    Subset Selector.
"""

import Metashape
import os

from PySide2 import QtWidgets, QtCore

from .photogrammetryUtilities import FormatCountWithNoun, IsMasterImage, ReportSuppressedError


# ===================================================================
# Core logic
# ===================================================================

def DisableEveryNthImage(chunk, interval, startOffset=0):
    """Disable every Nth master image (leave the others unchanged).

    Parameters
    ----------
    chunk : Metashape.Chunk
    interval : int
        The interval N: every Nth master image is disabled.
    startOffset : int
        0-based position, within each group of N images, of the
        image to disable (default 0, the first image of each group).

    Returns
    -------
    disabledCount : int
        Number of selected images, all now disabled (also those
        that were disabled already).
    masterImageCount : int
        Number of master images.
    """
    disabledCount = 0
    masterIndex = 0

    for camera in chunk.cameras:
        if not IsMasterImage(camera):
            continue
        if IsSelectedSubsetPosition(masterIndex, interval, startOffset):
            camera.enabled = False
            disabledCount += 1
        masterIndex += 1

    Metashape.app.update()
    return disabledCount, masterIndex


def EnableOnlyEveryNthImage(chunk, interval, startOffset=0):
    """Enable only every Nth master image (disable the rest).

    Parameters
    ----------
    chunk : Metashape.Chunk
    interval : int
        The interval N: every Nth master image is enabled.
    startOffset : int
        0-based position, within each group of N images, of the
        image to enable (default 0, the first image of each group).

    Returns
    -------
    enabledCount : int
        Number of master images now enabled.
    disabledCount : int
        Number of master images now disabled.
    masterImageCount : int
        Number of master images.
    """
    enabledCount = 0
    disabledCount = 0
    masterIndex = 0

    for camera in chunk.cameras:
        if not IsMasterImage(camera):
            continue
        if IsSelectedSubsetPosition(masterIndex, interval, startOffset):
            camera.enabled = True
            enabledCount += 1
        else:
            camera.enabled = False
            disabledCount += 1
        masterIndex += 1

    Metashape.app.update()
    return enabledCount, disabledCount, masterIndex


def EnableAllImages(chunk):
    """Enable every master image.

    Returns
    -------
    enabledCount : int
        Number of images that were disabled and are now enabled.
    masterImageCount : int
        Number of master images.
    """
    enabledCount = 0
    masterImageCount = 0

    for camera in chunk.cameras:
        if not IsMasterImage(camera):
            continue
        masterImageCount += 1
        if not camera.enabled:
            camera.enabled = True
            enabledCount += 1

    Metashape.app.update()
    return enabledCount, masterImageCount


def IsSelectedSubsetPosition(masterIndex, interval, startOffset):
    """True when the master image at *masterIndex* (0-based, in the
    chunk's camera order) is one of every *interval*-th images counted
    from *startOffset*: the selection rule of every action."""
    return (masterIndex - startOffset) % interval == 0


def MasterImageEnabledStates(chunk):
    """Enabled state of every master image of the chunk, in the
    chunk's camera order (secondary images of a rig and keyframes of
    an animation track are skipped)."""
    if not chunk:
        return []
    return [bool(camera.enabled) for camera in chunk.cameras
            if IsMasterImage(camera)]


def PlanSubsetAction(enabledStates, action, interval, startOffset):
    """Enabled states after *action* ("all", "disable", or "enable"),
    computed without touching the chunk, with the selection rule of
    the actions: "disable" disables the selected images and leaves the
    others as they are, "enable" enables the selected images and
    disables all others."""
    if action == "all":
        return [True] * len(enabledStates)
    plannedStates = []
    for masterIndex, isEnabled in enumerate(enabledStates):
        isSelected = IsSelectedSubsetPosition(masterIndex, interval, startOffset)
        if action == "disable":
            plannedStates.append(isEnabled and not isSelected)
        else:
            plannedStates.append(isSelected)
    return plannedStates


def SummariseSubsetChange(enabledStatesBefore, enabledStatesAfter,
                          interval, startOffset):
    """Counts describing a change of enabled states: the images in the
    selection pattern, those of them already disabled before, the
    images newly disabled and newly enabled, and the enabled and
    disabled images before and after."""
    total = len(enabledStatesBefore)
    selectedPositions = [
        masterIndex for masterIndex in range(total)
        if IsSelectedSubsetPosition(masterIndex, interval, startOffset)]
    enabledAfter = sum(1 for isEnabled in enabledStatesAfter if isEnabled)
    return dict(
        total=total,
        selected=len(selectedPositions),
        alreadyDisabledSelected=sum(
            1 for masterIndex in selectedPositions
            if not enabledStatesBefore[masterIndex]),
        newlyDisabled=sum(
            1 for wasEnabled, isEnabled in zip(enabledStatesBefore, enabledStatesAfter)
            if wasEnabled and not isEnabled),
        newlyEnabled=sum(
            1 for wasEnabled, isEnabled in zip(enabledStatesBefore, enabledStatesAfter)
            if isEnabled and not wasEnabled),
        disabledBefore=sum(1 for isEnabled in enabledStatesBefore if not isEnabled),
        enabledAfter=enabledAfter,
        disabledAfter=len(enabledStatesAfter) - enabledAfter)


def FormatOrdinalNumber(number):
    """English ordinal of a positive whole number: 1st, 2nd, 3rd, 4th,
    11th, 12th, 13th, 21st, 22nd, 23rd, 101st, 111th."""
    if 10 <= number % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(number % 10, "th")
    return "{}{}".format(number, suffix)


def FormatEnabledCount(enabledStates):
    """Total and enabled master images, for the dialog's chunk panel."""
    return "{:,} total, {:,} enabled".format(
        len(enabledStates), sum(1 for isEnabled in enabledStates if isEnabled))


def _DescribeAlreadyDisabled(summary, verbForms):
    """"N of the M selected images are already disabled" clause, with
    the singular and plural verb of *verbForms*, or "" when none of the
    selected images was disabled."""
    alreadyDisabled = summary["alreadyDisabledSelected"]
    if not alreadyDisabled:
        return ""
    return "{:,} of the {:,} selected image{} {} already disabled".format(
        alreadyDisabled, summary["selected"],
        "" if summary["selected"] == 1 else "s",
        verbForms[0] if alreadyDisabled == 1 else verbForms[1])


def DescribeSubsetPreview(action, summary):
    """Preview text of an action, from SummariseSubsetChange of the
    current and the planned enabled states."""
    if summary["total"] == 0:
        return "The chunk has no images."
    if action == "all":
        if summary["total"] == 1:
            if summary["disabledBefore"] == 0:
                return "The only image is already enabled."
            return "The only image will be enabled (it is currently disabled)."
        if summary["disabledBefore"] == 0:
            return "All {} are already enabled.".format(
                FormatCountWithNoun(summary["total"], "image", "images"))
        return "All {} will be enabled ({:,} currently disabled).".format(
            FormatCountWithNoun(summary["total"], "image", "images"), summary["disabledBefore"])
    if action == "disable":
        alreadyDisabled = _DescribeAlreadyDisabled(summary, ("is", "are"))
        return "{} will be disabled{}. {:,} of {} will remain enabled.".format(
            FormatCountWithNoun(summary["newlyDisabled"], "image", "images"),
            " ({})".format(alreadyDisabled) if alreadyDisabled else "",
            summary["enabledAfter"], FormatCountWithNoun(summary["total"], "image", "images"))
    return "{:,} of {} will be enabled and {:,} disabled.".format(
        summary["enabledAfter"], FormatCountWithNoun(summary["total"], "image", "images"),
        summary["disabledAfter"])


def DescribeSubsetResult(action, summary, interval, startOffset):
    """Result text of an applied action, from SummariseSubsetChange of
    the enabled states before and after."""
    if summary["total"] == 0:
        return "The chunk has no images."
    if action == "all":
        if summary["total"] == 1:
            if summary["newlyEnabled"]:
                return "Enabled the only image (it was disabled before)."
            return "The only image was already enabled."
        return "Enabled all {} ({:,} {} disabled before).".format(
            FormatCountWithNoun(summary["total"], "image", "images"), summary["newlyEnabled"],
            "was" if summary["newlyEnabled"] == 1 else "were")
    pattern = "every {} image, offset {}".format(
        FormatOrdinalNumber(interval), startOffset)
    if action == "disable":
        alreadyDisabled = _DescribeAlreadyDisabled(summary, ("was", "were"))
        return "Disabled {} ({}).{} {:,} of {} remain enabled.".format(
            FormatCountWithNoun(summary["newlyDisabled"], "image", "images"), pattern,
            " {}.".format(alreadyDisabled[:1].upper() + alreadyDisabled[1:])
            if alreadyDisabled else "",
            summary["enabledAfter"], FormatCountWithNoun(summary["total"], "image", "images"))
    return "Enabled {:,} and disabled {:,} of {} ({}).".format(
        summary["enabledAfter"], summary["disabledAfter"],
        FormatCountWithNoun(summary["total"], "image", "images"), pattern)


# ===================================================================
# GUI
# ===================================================================

_ABOUT_HTML = """
<h2>Image Subset Selector</h2>
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
<p>This tool enables or disables images at regular intervals, which is useful for three purposes:</p>
<ul>
<li><b>Thinning dense image sets</b>: for example, video frames captured at high frequency, where
every Nth frame is sufficient.</li>
<li><b>Subsets for validation experiments</b>: selecting a
systematic subset of images for a separately designed fitting and
evaluation.  Disabling images after an adjustment does not undo their
contribution to the estimated parameters, and it does not by itself
create an independent hold-out test.</li>
<li><b>Processing speed</b>: temporarily disabling images
to speed up test runs with a reduced image set.</li>
</ul>

<h3>Actions</h3>
<p><b>Disable every Nth image:</b> disables the images at positions
1, N+1, 2N+1, &hellip; (shifted by the start offset).  All other
images keep their current state, so images that were disabled
before stay disabled.  This is useful for thinning.</p>

<p><b>Enable only every Nth image:</b> enables the images at
positions 1, N+1, 2N+1, &hellip; (shifted by the start offset) and
<i>disables all others</i>.  This creates a sparse subset, useful
for quick test runs.</p>

<p><b>Enable all images:</b> enables every image that the tool
counts.  No interval is needed.</p>

<h3>Parameters</h3>
<p><b>Interval (N):</b> the step size.  N = 2 affects every 2nd
image, N = 5 every 5th, and so on.</p>
<p><b>Start offset:</b> shifts which image within each group of N is affected. Offset 0 selects the
1st, offset 1 the 2nd, and so on. This makes it possible to create complementary subsets (e.g.
offset 0 and offset 1 with N = 2 give two non-overlapping halves).</p>

<h3>Notes</h3>
<p>Three notes concern the images that the tool counts, the preview, and the image order:</p>
<ul>
<li>Only single images and the master images of multi-camera rigs
are counted.  The secondary images of a rig and the keyframes of an
animation track are skipped and never changed.</li>
<li>Before anything is applied, the preview at the bottom shows how many images will be enabled or
disabled, taking into account the images that are already disabled. After <b>Apply</b>, a message
and the Console report the changes that were made, and the counts in the dialog are updated.</li>
<li>The image order is the order of the chunk&rsquo;s camera list in
Metashape (usually the import order).</li>
</ul>
"""


class ImageSubsetDialog(QtWidgets.QDialog):
    """Dialog for selecting image subsets."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Image Subset Selector")
        self.setWindowFlags(
            self.windowFlags()
            & ~QtCore.Qt.WindowContextHelpButtonHint)
        self.setMinimumWidth(440)
        self._BuildUi()

    def _BuildUi(self):
        mainLayout = QtWidgets.QVBoxLayout(self)

        chunk = Metashape.app.document.chunk

        # --- Chunk info ------------------------------------------------
        chunkInformationGroup = QtWidgets.QGroupBox("Active chunk")
        chunkInformationLayout = QtWidgets.QFormLayout(chunkInformationGroup)

        chunkLabel = chunk.label if chunk else "(none)"
        chunkInformationLayout.addRow("Chunk:", QtWidgets.QLabel(chunkLabel))

        enabledStates = MasterImageEnabledStates(chunk)
        masterImageCount = len(enabledStates)
        self.imageCountLabel = QtWidgets.QLabel(
            FormatEnabledCount(enabledStates))
        chunkInformationLayout.addRow("Images:", self.imageCountLabel)

        mainLayout.addWidget(chunkInformationGroup)

        # --- Action ----------------------------------------------------
        actionGroup = QtWidgets.QGroupBox("Action")
        actionLayout = QtWidgets.QVBoxLayout(actionGroup)

        self.actionButtonGroup = QtWidgets.QButtonGroup(self)

        self.disableEveryNthRadioButton = QtWidgets.QRadioButton(
            "Disable every Nth image (leave the others unchanged)")
        self.disableEveryNthRadioButton.setToolTip(
            "Disables every Nth image. The other images keep their\n"
            "current state.\n"
            "Example: N = 5 with offset 0 disables images 1, 6, 11, 16, \u2026")
        self.disableEveryNthRadioButton.setChecked(True)
        self.actionButtonGroup.addButton(self.disableEveryNthRadioButton, 0)
        actionLayout.addWidget(self.disableEveryNthRadioButton)

        self.enableOnlyEveryNthRadioButton = QtWidgets.QRadioButton(
            "Enable only every Nth image (disable the rest)")
        self.enableOnlyEveryNthRadioButton.setToolTip(
            "Enables every Nth image and disables all others.\n"
            "Example: N = 3 with offset 0 enables images 1, 4, 7, 10, \u2026")
        self.actionButtonGroup.addButton(self.enableOnlyEveryNthRadioButton, 1)
        actionLayout.addWidget(self.enableOnlyEveryNthRadioButton)

        self.enableAllRadioButton = QtWidgets.QRadioButton(
            "Enable all images (reset)")
        self.enableAllRadioButton.setToolTip(
            "Enables every image that this tool counts: the single\n"
            "images and the master images of multi-camera rigs.\n"
            "Secondary images of a rig and keyframes are not changed.")
        self.actionButtonGroup.addButton(self.enableAllRadioButton, 2)
        actionLayout.addWidget(self.enableAllRadioButton)

        mainLayout.addWidget(actionGroup)

        # --- Parameters ------------------------------------------------
        parameterGroup = QtWidgets.QGroupBox("Parameters")
        parameterLayout = QtWidgets.QFormLayout(parameterGroup)

        self.intervalSpinBox = QtWidgets.QSpinBox()
        self.intervalSpinBox.setRange(2, max(masterImageCount, 2))
        self.intervalSpinBox.setValue(2)
        self.intervalSpinBox.setToolTip(
            "The interval N: every Nth image is affected.")
        parameterLayout.addRow("Interval (N):", self.intervalSpinBox)

        self.startOffsetSpinBox = QtWidgets.QSpinBox()
        self.startOffsetSpinBox.setRange(0, 0)
        self.startOffsetSpinBox.setValue(0)
        self.startOffsetSpinBox.setToolTip(
            "Position of the selected image within each group of N\n"
            "images: 0 selects the first image of each group, 1 the\n"
            "second, and so on.")
        parameterLayout.addRow("Start offset:", self.startOffsetSpinBox)

        # Update offset range when interval changes.
        self.intervalSpinBox.valueChanged.connect(self._OnIntervalChanged)
        # Initialise the offset range for the default interval.  The
        # signal only fires on later changes, so without this call
        # the offset stays locked at 0 for N = 2.
        self._OnIntervalChanged(self.intervalSpinBox.value())

        mainLayout.addWidget(parameterGroup)

        # "Enable all images" needs no parameters, so the parameter
        # group is disabled while it is chosen.
        self.enableAllRadioButton.toggled.connect(
            lambda checked: parameterGroup.setEnabled(not checked))

        # --- Preview ---------------------------------------------------
        self.previewLabel = QtWidgets.QLabel("")
        self.previewLabel.setWordWrap(True)
        mainLayout.addWidget(self.previewLabel)

        # Update preview on any change.
        self.intervalSpinBox.valueChanged.connect(self._UpdatePreview)
        self.startOffsetSpinBox.valueChanged.connect(self._UpdatePreview)
        self.actionButtonGroup.buttonToggled.connect(
            self._UpdatePreview)
        self._UpdatePreview()

        # --- Buttons ---------------------------------------------------
        buttonLayout = QtWidgets.QHBoxLayout()

        aboutButton = QtWidgets.QPushButton("About")
        aboutButton.clicked.connect(self._ShowAbout)
        buttonLayout.addWidget(aboutButton)

        buttonLayout.addStretch()

        self.applyButton = QtWidgets.QPushButton("Apply")
        self.applyButton.setDefault(True)
        self.applyButton.clicked.connect(self._OnApply)
        buttonLayout.addWidget(self.applyButton)

        closeButton = QtWidgets.QPushButton("Close")
        closeButton.clicked.connect(self.reject)
        buttonLayout.addWidget(closeButton)

        mainLayout.addLayout(buttonLayout)

    def _OnIntervalChanged(self, value):
        self.startOffsetSpinBox.setRange(0, value - 1)

    def _SelectedAction(self):
        """The chosen action: "all", "disable", or "enable"."""
        if self.enableAllRadioButton.isChecked():
            return "all"
        if self.disableEveryNthRadioButton.isChecked():
            return "disable"
        return "enable"

    def _UpdatePreview(self):
        """Preview the effect of the chosen action on the current
        enabled states of the active chunk."""
        enabledStates = MasterImageEnabledStates(
            Metashape.app.document.chunk)
        action = self._SelectedAction()
        interval = self.intervalSpinBox.value()
        offset = self.startOffsetSpinBox.value()
        plannedStates = PlanSubsetAction(
            enabledStates, action, interval, offset)
        summary = SummariseSubsetChange(
            enabledStates, plannedStates, interval, offset)
        self.previewLabel.setText(DescribeSubsetPreview(action, summary))

    def _OnApply(self):
        chunk = Metashape.app.document.chunk
        if not chunk:
            return

        action = self._SelectedAction()
        interval = self.intervalSpinBox.value()
        offset = self.startOffsetSpinBox.value()
        enabledStatesBefore = MasterImageEnabledStates(chunk)

        print("")
        print("=" * 60)
        print("Image Subset Selector")
        print("=" * 60)
        print("Chunk: {}".format(chunk.label))

        if action == "all":
            print("Action: Enable all images")
            EnableAllImages(chunk)
        elif action == "disable":
            print("Action: Disable every {} image (offset {})".format(
                FormatOrdinalNumber(interval), offset))
            DisableEveryNthImage(chunk, interval, offset)
        else:
            print("Action: Enable only every {} image (offset {})".format(
                FormatOrdinalNumber(interval), offset))
            EnableOnlyEveryNthImage(chunk, interval, offset)

        enabledStatesAfter = MasterImageEnabledStates(chunk)
        summary = SummariseSubsetChange(
            enabledStatesBefore, enabledStatesAfter, interval, offset)
        message = DescribeSubsetResult(action, summary, interval, offset)
        print(message)
        print("=" * 60)
        print("")

        self.imageCountLabel.setText(FormatEnabledCount(enabledStatesAfter))
        self._UpdatePreview()
        QtWidgets.QMessageBox.information(
            self, "Image Subset Selector", message)

    def _ShowAbout(self):
        aboutDialog = QtWidgets.QDialog(self)
        aboutDialog.setWindowTitle(
            "About: Image Subset Selector")
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

_MENU_LABEL = "MetaFOCUS/Filtering and Project Management/Image Subset Selector"
_ICON_NAME = "image_subset_selector_32.png"


def _ShowSubsetDialog():
    document = Metashape.app.document
    if not document or not document.chunk:
        QtWidgets.QMessageBox.information(
            QtWidgets.QApplication.activeWindow(), "Image Subset Selector",
            "Open a project with at least one chunk first.")
        return
    ImageSubsetDialog(
        QtWidgets.QApplication.activeWindow()).exec_()


def RegisterMenuItem():
    """Register the menu item.  Called by the MetaFOCUS loader."""
    try:
        Metashape.app.removeMenuItem(_MENU_LABEL)
    except Exception as suppressedError:
        ReportSuppressedError("imageSubsetSelector.RegisterMenuItem", suppressedError)
    iconPath = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "icons", _ICON_NAME)
    if os.path.exists(iconPath):
        Metashape.app.addMenuItem(
            _MENU_LABEL, _ShowSubsetDialog, icon=iconPath)
    else:
        Metashape.app.addMenuItem(_MENU_LABEL, _ShowSubsetDialog)
