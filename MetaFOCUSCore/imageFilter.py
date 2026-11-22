"""
Image Filter for Agisoft Metashape Professional
===============================================

Filters the Photos pane and the image selection to show only the
enabled or only the disabled images.  Useful for quickly inspecting
which images are currently enabled or disabled.

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
    Filter.
"""

import Metashape
import os

from PySide2 import QtWidgets, QtCore

from .photogrammetryUtilities import (ConsoleBlockEnd, ConsoleBlockStart, FormatCountWithNoun, IsMasterImage,
                                      PrintConsoleLines, ReportSuppressedError)

_TOOL_NAME = "Image Filter"

# The action line of the Console block of Clear filter.
_CLEAR_ACTION = "Clear the filter"


# ===================================================================
# Core logic
# ===================================================================

def CountMatchingImages(chunk, showEnabled):
    """(matchingCount, masterImageCount): the enabled (*showEnabled*) or
    the disabled master images of *chunk*, and all its master images."""
    masterImages = [camera for camera in chunk.cameras if IsMasterImage(camera)]
    return sum(1 for camera in masterImages if camera.enabled == showEnabled), len(masterImages)


def FilterImages(chunk, showEnabled):
    """Select the enabled or the disabled master images and filter the
    Photos pane to show only them.  When no master image matches, nothing
    is changed: the selection and the Photos pane stay as they are.

    Parameters
    ----------
    chunk : Metashape.Chunk
    showEnabled : bool
        If True, select the enabled images.
        If False, select the disabled images.

    Returns
    -------
    matchingCount : int
        Number of images matching the filter.
    masterImageCount : int
        Total number of master images.
    photosPaneFiltered : bool
        False when the Photos pane could not be filtered (the failure
        is reported once in the Console), so that only the selection
        was made, and when no image matches, so that nothing was
        changed.
    """
    matchingCount, masterImageCount = CountMatchingImages(chunk, showEnabled)
    if matchingCount == 0:
        return 0, masterImageCount, False

    matchingImages = []
    for camera in chunk.cameras:
        if not IsMasterImage(camera):
            continue
        if camera.enabled == showEnabled:
            camera.selected = True
            matchingImages.append(camera)
        else:
            camera.selected = False

    # Filter the Photos pane.  A failure is reported and returned, so
    # that the user learns that only the selection was made.
    try:
        Metashape.app.photos_pane.setFilter(matchingImages)
        photosPaneFiltered = True
    except Exception as suppressedError:
        ReportSuppressedError("imageFilter.FilterImages", suppressedError)
        photosPaneFiltered = False

    Metashape.app.update()
    return len(matchingImages), masterImageCount, photosPaneFiltered


def DescribeFilterResult(matchingCount, totalCount, state, photosPaneFiltered):
    """The result line of the Console block after filtering: how many
    *state* ("enabled" or "disabled") images the Photos pane shows, or,
    when the Photos pane could not be filtered, that they are only
    selected."""
    matchingText = FormatCountWithNoun(matchingCount, state + " image", state + " images")
    if photosPaneFiltered:
        return "Showing {} (of {:,} in total).".format(matchingText, totalCount)
    return ("The Photos pane could not be filtered, so the {} "
            "(of {:,} in total) {} only selected.".format(
                matchingText, totalCount, "is" if matchingCount == 1 else "are"))


def DescribeEmptyFilter(state, totalCount):
    """What the Console block and the message say when no master image
    is *state* ("enabled" or "disabled"): the Photos pane and the
    selection are left as they are, because there is no image to show."""
    if totalCount == 0:
        return "The chunk has no images, so there is nothing to show."
    if totalCount == 1:
        return ("The only image of the chunk is not {}, so the Photos pane and the selection are left "
                "as they are.".format(state))
    return ("None of the {:,} images of the chunk is {}, so the Photos pane and the selection are left "
            "as they are.".format(totalCount, state))


def DescribeClearResult(totalCount, photosPaneReset):
    """The result line of the Console block after clearing the filter."""
    if photosPaneReset:
        return "Filter cleared."
    # A chunk without images.
    if totalCount == 0:
        return "The Photos pane filter could not be reset, and the chunk has no images."
    if totalCount == 1:
        return ("The Photos pane filter could not be reset, "
                "but the only image is deselected.")
    return ("The Photos pane filter could not be reset, but "
            "all {:,} images are deselected.".format(totalCount))


def FilterAction(showEnabled):
    """The action line of the Console block of Apply filter."""
    return "Show the {} images only".format("enabled" if showEnabled else "disabled")


def ConsoleBlockOpening(chunkLabel, action):
    """The lines that open the Console block of one action, which the
    tool prints before it acts, as the Image Subset Selector does: an
    empty line, a rule, the name of the tool, a rule, the chunk, and the
    action.  A tolerated error of the action is thus reported inside the
    block."""
    return ConsoleBlockStart(_TOOL_NAME) + ["Chunk: {}".format(chunkLabel), "Action: {}".format(action)]


def ConsoleBlockClosing(result):
    """The lines that close the Console block of one action: the result,
    a rule, and an empty line."""
    return [result] + ConsoleBlockEnd()


def ClearFilter(chunk):
    """Clear the filter: deselect every master image and reset the
    Photos pane filter.

    Returns
    -------
    masterImageCount : int
    photosPaneReset : bool
        False when the Photos pane filter could not be reset (the
        failure is reported once in the Console).
    """
    masterImageCount = 0
    for camera in chunk.cameras:
        if not IsMasterImage(camera):
            continue
        masterImageCount += 1
        camera.selected = False

    try:
        Metashape.app.photos_pane.resetFilter()
        photosPaneReset = True
    except Exception as suppressedError:
        ReportSuppressedError("imageFilter.ClearFilter", suppressedError)
        photosPaneReset = False

    Metashape.app.update()
    return masterImageCount, photosPaneReset


# ===================================================================
# GUI
# ===================================================================

_ABOUT_HTML = """
<h2>Image Filter</h2>
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
<p>This tool filters the Photos pane to show only the enabled or only the disabled images. It also
selects the matching images in the chunk, which makes them easy to identify in the 3D view.</p>

<h3>Usage</h3>
<p>Select whether to show the enabled or the disabled images, then click <b>Apply filter</b>. As a
result, the Photos pane shows only the matching images, and those images are selected. Click <b>Clear
filter</b> to reset the Photos pane filter and deselect all images.</p>
<p>When no image of the chunk matches, for example when no image is disabled, the tool says so in a
message and in the Console. In that case, it leaves the Photos pane and the selection as they are.</p>

<h3>Notes</h3>
<p>Three notes concern the images that the tool counts and the reports in the Console:</p>
<ul>
<li>Only single images and the master images of multi-camera rigs
are counted and selected.  The secondary images of a rig and the
keyframes of an animation track are skipped.</li>
<li>The Console reports the result of every action.  If the Photos
pane cannot be filtered (or its filter cannot be reset), the
Console says so and only the selection is made (or cleared).  The
error itself is reported once per session.</li>
<li>The Console shows each action as a block, as the Image Subset Selector does: the name of the tool between two lines of =, then the chunk, the action, and the result.</li>
</ul>
"""


class ImageFilterDialog(QtWidgets.QDialog):
    """Dialog that shows the enabled or the disabled images in the
    Photos pane."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Image Filter")
        self.setWindowFlags(
            self.windowFlags()
            & ~QtCore.Qt.WindowContextHelpButtonHint)
        self.setMinimumWidth(400)
        self._BuildUi()

    def _BuildUi(self):
        mainLayout = QtWidgets.QVBoxLayout(self)

        chunk = Metashape.app.document.chunk

        # --- Chunk info ------------------------------------------------
        chunkInformationGroup = QtWidgets.QGroupBox("Active chunk")
        chunkInformationLayout = QtWidgets.QFormLayout(chunkInformationGroup)

        chunkLabel = chunk.label if chunk else "(none)"
        chunkInformationLayout.addRow("Chunk:", QtWidgets.QLabel(chunkLabel))

        masterImageCount = 0
        enabledCount = 0
        disabledCount = 0
        if chunk:
            for camera in chunk.cameras:
                if IsMasterImage(camera):
                    masterImageCount += 1
                    if camera.enabled:
                        enabledCount += 1
                    else:
                        disabledCount += 1

        chunkInformationLayout.addRow("Total images:",
            QtWidgets.QLabel("{:,}".format(masterImageCount)))
        chunkInformationLayout.addRow("Enabled:",
            QtWidgets.QLabel("{:,}".format(enabledCount)))
        chunkInformationLayout.addRow("Disabled:",
            QtWidgets.QLabel("{:,}".format(disabledCount)))

        mainLayout.addWidget(chunkInformationGroup)

        # --- Filter action ---------------------------------------------
        actionGroup = QtWidgets.QGroupBox("Show in the Photos pane")
        actionLayout = QtWidgets.QVBoxLayout(actionGroup)

        self.filterButtonGroup = QtWidgets.QButtonGroup(self)

        self.enabledImagesRadioButton = QtWidgets.QRadioButton(
            "Enabled images only ({:,})".format(enabledCount))
        self.enabledImagesRadioButton.setChecked(True)
        self.filterButtonGroup.addButton(self.enabledImagesRadioButton, 0)
        actionLayout.addWidget(self.enabledImagesRadioButton)

        self.disabledImagesRadioButton = QtWidgets.QRadioButton(
            "Disabled images only ({:,})".format(disabledCount))
        self.filterButtonGroup.addButton(self.disabledImagesRadioButton, 1)
        actionLayout.addWidget(self.disabledImagesRadioButton)

        mainLayout.addWidget(actionGroup)

        # --- Buttons ---------------------------------------------------
        buttonLayout = QtWidgets.QHBoxLayout()

        aboutButton = QtWidgets.QPushButton("About")
        aboutButton.clicked.connect(self._ShowAbout)
        buttonLayout.addWidget(aboutButton)

        buttonLayout.addStretch()

        applyButton = QtWidgets.QPushButton("Apply filter")
        applyButton.setDefault(True)
        applyButton.clicked.connect(self._OnApply)
        buttonLayout.addWidget(applyButton)

        clearButton = QtWidgets.QPushButton("Clear filter")
        clearButton.clicked.connect(self._OnClear)
        buttonLayout.addWidget(clearButton)

        closeButton = QtWidgets.QPushButton("Close")
        closeButton.clicked.connect(self.reject)
        buttonLayout.addWidget(closeButton)

        mainLayout.addLayout(buttonLayout)

    def _OnApply(self):
        chunk = Metashape.app.document.chunk
        if not chunk:
            return

        showEnabled = self.enabledImagesRadioButton.isChecked()
        state = "enabled" if showEnabled else "disabled"
        PrintConsoleLines(ConsoleBlockOpening(chunk.label, FilterAction(showEnabled)))
        # No matching image: say so, and change nothing.
        matchingCount, masterImageCount = CountMatchingImages(chunk, showEnabled)
        if matchingCount == 0:
            emptyText = DescribeEmptyFilter(state, masterImageCount)
            PrintConsoleLines(ConsoleBlockClosing(emptyText))
            QtWidgets.QMessageBox.information(self, _TOOL_NAME, emptyText)
            return
        matchingCount, masterImageCount, photosPaneFiltered = FilterImages(
            chunk, showEnabled)

        PrintConsoleLines(ConsoleBlockClosing(DescribeFilterResult(
            matchingCount, masterImageCount, state, photosPaneFiltered)))

    def _OnClear(self):
        chunk = Metashape.app.document.chunk
        if not chunk:
            return
        PrintConsoleLines(ConsoleBlockOpening(chunk.label, _CLEAR_ACTION))
        masterImageCount, photosPaneReset = ClearFilter(chunk)
        PrintConsoleLines(ConsoleBlockClosing(DescribeClearResult(
            masterImageCount, photosPaneReset)))

    def _ShowAbout(self):
        aboutDialog = QtWidgets.QDialog(self)
        aboutDialog.setWindowTitle("About: Image Filter")
        aboutDialog.setWindowFlags(
            aboutDialog.windowFlags()
            & ~QtCore.Qt.WindowContextHelpButtonHint)
        aboutDialog.setMinimumSize(480, 360)
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

_MENU_LABEL = "MetaFOCUS/Filtering and Project Management/Image Filter"
_ICON_NAME = "image_filter_32.png"


def _ShowFilterDialog():
    document = Metashape.app.document
    if not document or not document.chunk:
        QtWidgets.QMessageBox.information(
            QtWidgets.QApplication.activeWindow(), "Image Filter",
            "Open a project with at least one chunk first.")
        return
    ImageFilterDialog(
        QtWidgets.QApplication.activeWindow()).exec_()


def RegisterMenuItem():
    """Register the menu item.  Called by the MetaFOCUS loader."""
    try:
        Metashape.app.removeMenuItem(_MENU_LABEL)
    except Exception as suppressedError:
        ReportSuppressedError("imageFilter.RegisterMenuItem", suppressedError)
    iconPath = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "icons", _ICON_NAME)
    if os.path.exists(iconPath):
        Metashape.app.addMenuItem(
            _MENU_LABEL, _ShowFilterDialog, icon=iconPath)
    else:
        Metashape.app.addMenuItem(_MENU_LABEL, _ShowFilterDialog)
