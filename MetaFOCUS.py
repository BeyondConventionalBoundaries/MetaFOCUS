"""
MetaFOCUS: Metashape Functions for Optimising, Characterising,
            and Understanding Structure from Motion
=====================================================================

Loader script for the MetaFOCUS toolbox.

When run from outside the scripts directory (first run or update),
offers to install MetaFOCUS permanently into Metashape's auto-load
scripts directory so it loads automatically on every startup.  Alternatively, the user can choose to load it
only for the current session.

If already installed in the scripts directory, it loads silently.

It adds MetaFOCUS > Check for Updates (MetaFOCUSCore/updateCheck.py),
which installs a new release with the same installer
(_InstallToolboxIntoDirectory), and starts the automatic check when the
user allows it.

Author:      Geert J. Verhoeven (ORCID: 0000-0003-4825-9604)
Contact:     geert.verhoeven@univie.ac.at
Licence:     GPL-3.0-or-later WITH GPL-3.0-linking-exception
Requires:    Agisoft Metashape Professional 2.x+
Developed for: Agisoft Metashape Professional 2.3.2
"""

import Metashape
import importlib
import os
import re
import sys
import shutil
import tempfile
import traceback


# ===================================================================
# Determine paths
# ===================================================================

_THIS_FILE = os.path.abspath(__file__)
_THIS_DIRECTORY = os.path.dirname(_THIS_FILE)
_CORE_FOLDER_NAME = "MetaFOCUSCore"
_CORE_SOURCE = os.path.join(_THIS_DIRECTORY, _CORE_FOLDER_NAME)

# Metashape auto-load scripts directory.
if sys.platform == "win32":
    _SCRIPTS_DIRECTORY = os.path.join(
        os.environ.get("LOCALAPPDATA", ""),
        "Agisoft", "Metashape Pro", "scripts")
elif sys.platform == "darwin":
    _SCRIPTS_DIRECTORY = os.path.expanduser(
        "~/Library/Application Support/Agisoft/Metashape Pro/scripts")
else:
    _SCRIPTS_DIRECTORY = os.path.expanduser(
        "~/.local/share/Agisoft/Metashape Pro/scripts")


_REPORTED_SUPPRESSED_ERRORS = set()


def _ReportSuppressedError(contextLabel, error):
    """Print a deliberately tolerated error once per location and session."""
    if contextLabel in _REPORTED_SUPPRESSED_ERRORS:
        return
    _REPORTED_SUPPRESSED_ERRORS.add(contextLabel)
    # "Type: message", or the bare type name when the error carries no
    # message, so that the line never ends in an empty colon.
    errorMessage = str(error)
    errorDescription = ("{}: {}".format(type(error).__name__, errorMessage)
                        if errorMessage else type(error).__name__)
    print("  [MetaFOCUS] {}: {} (tolerated, shown once)".format(
        contextLabel, errorDescription))


def _IsInstalledInScriptsDirectory():
    """Check if this loader is running from the scripts directory."""
    return os.path.normcase(os.path.normpath(_THIS_DIRECTORY)) == \
           os.path.normcase(os.path.normpath(_SCRIPTS_DIRECTORY))


# The modules of the package folder, which every installation needs.  An
# installation whose package lacks one of them is refused before anything
# is replaced (_InstallToolboxIntoDirectory).
_REQUIRED_CORE_MODULES = (
    "__init__.py", "aboutPages.py", "depthRenderExporter.py", "footprintGenerator.py",
    "gradualSelectionFilter.py", "gsdValueExporter.py", "gsdVisualiser.py", "imageFilter.py",
    "imageParameterExporter.py", "imageSubsetSelector.py", "interiorOrientationReport.py",
    "interiorOrientationViewer.py", "markerResidualInspector.py", "photogrammetryUtilities.py",
    "regionTools.py", "scaleBarAPrioriUncertainty.py", "sfmLogParser.py", "terminology.py",
    "tiePointCovarianceExporter.py", "unpinnedMarkerProjectionRemover.py", "updateCheck.py")


def _InstallToolboxIntoDirectory(loaderPath, coreSourceFolder,
                                 targetDirectory, requiredCoreFiles=()):
    """Copy the loader *loaderPath* and the package folder
    *coreSourceFolder* into *targetDirectory*.

    Both are first copied into a new folder that this installation
    creates in *targetDirectory* (tempfile.mkdtemp), and the copied
    package must hold every file of *requiredCoreFiles*.  Then the old
    package is moved into that folder, the new one is moved into place,
    and the loader is replaced last.  If any step fails, or a required
    file is missing, the previous installation is restored, so a failed
    update never leaves a new loader with a missing or partial package.
    The folder of the installation is removed at the end, and no other
    file or folder of *targetDirectory* is touched.  A file, or anything
    else that is not a folder, with the name of the package folder is
    refused before anything moves, because the installation would move it
    aside and remove it.  The error text says whether the installation was
    left as it was or the previous installation was restored.  When the
    previous installation cannot be restored, the folder is kept, and the
    error text names the folder that holds the previous package and where
    it goes back before Metashape is started again
    (_RestoreFailureText).  Return (True, targetDirectory) on success or
    (False, error text)."""
    coreFolderName = os.path.basename(os.path.normpath(coreSourceFolder))
    destinationCoreFolder = os.path.join(targetDirectory, coreFolderName)
    destinationLoaderPath = os.path.join(targetDirectory, os.path.basename(loaderPath))
    installationFolder = None
    previousCoreFolder = None
    previousCoreSetAside = False
    newCorePlaced = False
    try:
        os.makedirs(targetDirectory, exist_ok=True)
        if os.path.lexists(destinationCoreFolder) and not os.path.isdir(destinationCoreFolder):
            raise RuntimeError("{} is not a folder, so the installation was left as it was.".format(
                destinationCoreFolder))
        installationFolder = tempfile.mkdtemp(prefix="MetaFOCUS_installation_", dir=targetDirectory)
        stagingCoreFolder = os.path.join(installationFolder, coreFolderName)
        previousCoreFolder = os.path.join(installationFolder, coreFolderName + "_previous")
        stagingLoaderPath = os.path.join(installationFolder, os.path.basename(loaderPath))
        shutil.copytree(coreSourceFolder, stagingCoreFolder)
        shutil.copy2(loaderPath, stagingLoaderPath)
        missingFiles = [fileName for fileName in requiredCoreFiles
                        if not os.path.isfile(os.path.join(stagingCoreFolder, fileName))]
        if missingFiles:
            raise RuntimeError("The folder {} lacks {}, so the installation was left as it was.".format(
                coreSourceFolder, ", ".join(missingFiles)))
        if os.path.exists(destinationCoreFolder):
            os.rename(destinationCoreFolder, previousCoreFolder)
            previousCoreSetAside = True
        os.rename(stagingCoreFolder, destinationCoreFolder)
        newCorePlaced = True
        os.replace(stagingLoaderPath, destinationLoaderPath)
    except Exception as installError:
        problems = [str(installError)]
        try:
            if newCorePlaced:
                os.rename(destinationCoreFolder, os.path.join(installationFolder, coreFolderName + "_failed"))
            if previousCoreSetAside:
                os.rename(previousCoreFolder, destinationCoreFolder)
        except Exception as restoreError:
            problems.append(_RestoreFailureText(restoreError, previousCoreFolder if previousCoreSetAside else None,
                                                destinationCoreFolder, installationFolder))
            return False, "\n".join(problems)
        _RemoveInstallationFolder(installationFolder)
        if newCorePlaced or previousCoreSetAside:
            problems.append("The previous installation was restored.")
        elif "left as it was" not in problems[0]:
            problems.append("The installation was left as it was.")
        return False, "\n".join(problems)
    _RemoveInstallationFolder(installationFolder)
    return True, targetDirectory


def _RestoreFailureText(restoreError, previousCoreFolder, destinationCoreFolder, installationFolder):
    """The end of the error text of an installation whose previous
    installation could not be restored (_InstallToolboxIntoDirectory): the
    error *restoreError*, the folder *previousCoreFolder* that holds the
    previous package (None when there was none), and what has to happen
    with it at *destinationCoreFolder* before Metashape is started
    again."""
    text = "Restoring the previous installation also failed: {}.".format(restoreError)
    if previousCoreFolder is None:
        return text + " The new package is left in {}, and the folder {} is kept.".format(
            destinationCoreFolder, installationFolder)
    if os.path.lexists(destinationCoreFolder):
        return text + (" The previous package is kept in {}, and {} holds the new one. Replace the new package by "
                       "the previous one before Metashape is started again.".format(previousCoreFolder,
                                                                                    destinationCoreFolder))
    return text + " The previous package is kept in {}. Move it back to {} before Metashape is started again.".format(
        previousCoreFolder, destinationCoreFolder)


def _RemoveInstallationFolder(installationFolder):
    """Remove the folder of one installation (_InstallToolboxIntoDirectory)
    with everything in it, all of which that installation created or moved
    there.  A failure is reported once in the Console."""
    if installationFolder is None:
        return
    try:
        shutil.rmtree(installationFolder)
    except Exception as cleanupError:
        _ReportSuppressedError("MetaFOCUS._InstallToolboxIntoDirectory", cleanupError)


def _InstallToScriptsDirectory():
    """Copy MetaFOCUS.py and MetaFOCUSCore/ to the scripts directory,
    with every module of _REQUIRED_CORE_MODULES."""
    return _InstallToolboxIntoDirectory(_THIS_FILE, _CORE_SOURCE, _SCRIPTS_DIRECTORY, _REQUIRED_CORE_MODULES)


# ===================================================================
# First-run install prompt
# ===================================================================

def _ReadToolboxVersion(coreFolder):
    """Version of the toolbox in *coreFolder*, read as text from the
    line _METAFOCUS_VERSION = "..." of its __init__.py, because the
    welcome dialog appears before the package can be imported.
    Returns "" when the file or the line is missing."""
    try:
        with open(os.path.join(coreFolder, "__init__.py"), encoding="utf-8") as initFile:
            versionMatch = re.search(r'^_METAFOCUS_VERSION = "([^"]+)"', initFile.read(), re.M)
    except OSError:
        return ""
    return versionMatch.group(1) if versionMatch else ""


def _WelcomeText(toolboxVersion, scriptsDirectory):
    """Rich text of the welcome dialog, with the version of the toolbox
    in the heading when it is known."""
    heading = ("Welcome to MetaFOCUS v{}!".format(toolboxVersion) if toolboxVersion
               else "Welcome to MetaFOCUS!")
    return ("<h3>{}</h3>"
            "<p><b>Metashape Functions for Optimising, Characterising, "
            "and Understanding Structure from Motion.</b></p>"
            "<p>Would you like to install MetaFOCUS permanently so it "
            "loads automatically every time you start Metashape?</p>"
            "<p><small>Files will be copied to:<br>"
            "<code>{}</code></small></p>").format(heading, scriptsDirectory)


def _OfferInstallation():
    """Show a dialog asking whether to install permanently."""
    from PySide2 import QtWidgets, QtCore

    messageBox = QtWidgets.QMessageBox(
        QtWidgets.QApplication.activeWindow())
    messageBox.setWindowTitle("MetaFOCUS: Installation")
    messageBox.setTextFormat(QtCore.Qt.RichText)
    messageBox.setText(_WelcomeText(_ReadToolboxVersion(_CORE_SOURCE), _SCRIPTS_DIRECTORY))

    buttonInstall = messageBox.addButton(
        "Install permanently", QtWidgets.QMessageBox.AcceptRole)
    messageBox.addButton(
        "Load this session only", QtWidgets.QMessageBox.RejectRole)
    messageBox.exec_()

    if messageBox.clickedButton() == buttonInstall:
        success, detail = _InstallToScriptsDirectory()
        if success:
            QtWidgets.QMessageBox.information(
                QtWidgets.QApplication.activeWindow(),
                "MetaFOCUS: Installation",
                "MetaFOCUS has been installed to:\n{}\n\n"
                "It will load automatically on the next "
                "Metashape startup.\n\n"
                "Loading now for this session...".format(detail))
        else:
            QtWidgets.QMessageBox.warning(
                QtWidgets.QApplication.activeWindow(),
                "MetaFOCUS: Installation",
                "Could not install MetaFOCUS:\n{}\n\n"
                "Loading for this session only.".format(detail))


# ===================================================================
# Load the toolbox
# ===================================================================

def _ShowChangelog():
    from MetaFOCUSCore import _METAFOCUS_VERSION
    from PySide2 import QtWidgets, QtCore
    changelogDialog = QtWidgets.QDialog(
        QtWidgets.QApplication.activeWindow())
    changelogDialog.setWindowTitle("MetaFOCUS Changelog (v{})".format(_METAFOCUS_VERSION))
    changelogDialog.setWindowFlags(
        changelogDialog.windowFlags()
        & ~QtCore.Qt.WindowContextHelpButtonHint)
    changelogDialog.setMinimumSize(560, 520)
    layout = QtWidgets.QVBoxLayout(changelogDialog)
    textBrowser = QtWidgets.QTextBrowser()
    textBrowser.setOpenExternalLinks(True)
    textBrowser.setHtml(
        "<h2>MetaFOCUS Changelog</h2>"
        "<h3>v1.1.0 (2026-11-22)</h3>"
        "<ul>"
        "<li><b>New (Interior Orientation Viewer):</b> Export HTML writes a report of every interior orientation "
        "group, one file per group.</li>"
        "<li><b>New:</b> MetaFOCUS &gt; Check for Updates shows the changes of a new version and installs it. "
        "When allowed, MetaFOCUS checks at most once a day.</li>"
        "<li><b>Fixed:</b> the GSD Value Exporter keeps control points labelled like inspection markers, and the "
        "Console line that undoes Region Tools restores rotation, translation, and scale.</li>"
        "<li><b>Fixed:</b> units and axes of the Reference panel data, covariance exports beside folders, the SfM "
        "Log Parser on complete, combined, incomplete, and long logs, and p-values below 1e-300.</li>"
        "<li><b>Changed:</b> clearer texts in several tools, a shorter README, CITATION.cff, and the module "
        "headers.</li>"
        "</ul>"
        "<h3>v1.0.0 (2026-11-18)</h3>"
        "<ul>"
        "<li><b>Fixed:</b> the Image Parameter Exporter names the CRS and units of the reference data of the "
        "images.</li>"
        "<li><b>Fixed:</b> the GSD Value Exporter keeps markers that it did not create, names the files that it "
        "replaces, and gives chunk units in a local CRS without a physical scale.</li>"
        "<li><b>Fixed:</b> the Interior Orientation Viewer and the Gradual Selection Filter name disabled images "
        "that Metashape still adjusts.</li>"
        "<li><b>Fixed:</b> the GSD Visualiser shows all colour bar labels.</li>"
        "<li><b>Fixed:</b> the Tie Point Covariance Exporter skips covariance matrices of zeros.</li>"
        "<li><b>Fixed:</b> the installer and the exporters no longer delete or replace other files without "
        "asking.</li>"
        "<li><b>Changed:</b> version 1.0.0 for every module, About texts, README, and CITATION.cff.</li>"
        "</ul>"
        "<h3>v0.56.1 (2026-11-17)</h3>"
        "<ul>"
        "<li><b>Fixed:</b> the GSD Value Exporter no longer selects the "
        "images of before the export again, because Metashape cleared the "
        "selection once more after the run. Its Console now says that adding "
        "the inspection markers clears the selection, so the images must be "
        "selected again.</li>"
        "<li><b>Changed:</b> the tooltips and the About section of the Gradual "
        "Selection Filter put the labels of Metashape and of the filter in "
        "quotation marks.</li>"
        "</ul>"
        "<h3>v0.56.0 (2026-11-17)</h3>"
        "<ul>"
        "<li><b>Added:</b> tooltips for every criterion of the Gradual "
        "Selection Filter and its fields, and an About section that explains "
        "the keypoint size and compares the reprojection errors of Metashape "
        "and MetaFOCUS in two tables.</li>"
        "<li><b>Added:</b> the GSD Value Exporter writes the units of CRS_X to "
        "CRS_Z, which the GSD Visualiser 0.13.0 shows on its axes.</li>"
        "<li><b>Fixed:</b> the GSD Value Exporter selects again the images "
        "that adding its inspection markers deselected, and its Console paths "
        "have one kind of slash.</li>"
        "<li><b>Changed:</b> the message without a selected image says what "
        "clears the selection, and the README follows the About sections.</li>"
        "</ul>"
        "<h3>v0.55.0 (2026-11-17)</h3>"
        "<ul>"
        "<li><b>Changed:</b> every About section revised, and two terminology "
        "labels in single quotation marks.</li>"
        "<li><b>Added:</b> the Image Footprint Generator, the GSD Value "
        "Exporter, and Depth &amp; Render Export show and list the images that "
        "Metashape reports as selected, and say what to do when it reports "
        "none.</li>"
        "<li><b>Fixed:</b> the SfM Log Parser says in the Console and the list "
        "of runs that a text holds no SfM run, as the Summary does.</li>"
        "<li><b>Changed:</b> the GSD Visualiser 0.12.0 (layout, units, GSD "
        "decimals), and the version of the GSD Value Exporter from 1.15.2 to "
        "0.15.3.</li>"
        "<li><b>Fixed:</b> single spaces between sentences in texts of the "
        "Gradual Selection Filter and the Image Footprint Generator.</li>"
        "</ul>"
        "<h3>v0.54.0 (2026-10-21)</h3>"
        "<ul>"
        "<li><b>Fixed:</b> the GSD and footprint tools apply the transform of "
        "a triangular mesh without a CRS of its own, instead of stopping.</li>"
        "<li><b>Fixed:</b> the SfM Log Parser reads the whole project path "
        "when a folder name in it holds .psx and a comma, so it no longer "
        "warns wrongly that the projects differ.</li>"
        "<li><b>Changed:</b> the Gradual Selection Filter says that its "
        "comparison of two adjustments flags no difference, not that they do "
        "not differ. Its About section says what else can change iteration 0 "
        "and no longer gives two examples of one project.</li>"
        "</ul>"
        "<h3>v0.53.1 (2026-10-20)</h3>"
        "<ul>"
        "<li><b>Changed:</b> the About section of the Gradual Selection "
        "Filter no longer gives the sigma nought of one project as an "
        "example.</li>"
        "</ul>"
        "<h3>v0.53.0 (2026-10-20)</h3>"
        "<ul>"
        "<li><b>Fixed:</b> the GSD and footprint tools read a triangular mesh "
        "built in Metashape, whose transform is the identity matrix, in "
        "internal chunk coordinates again.</li>"
        "<li><b>Changed:</b> the SfM Log Parser names the open chunk with its "
        "project and says that the summary compares the log with it.</li>"
        "<li><b>Changed:</b> the Marker Residual Inspector names its Residual "
        "threshold and starts every diagnosis with a capital.</li>"
        "<li><b>New:</b> the Gradual Selection Filter marks the suggested "
        "state with a star in its table, shows each graph's colour in its "
        "column header, and draws a flat graph in % on a fixed axis.</li>"
        "<li><b>New:</b> the README and CITATION.cff ask for the citation of "
        "the MetaFOCUS paper.</li>"
        "</ul>"
        "<h3>v0.52.0 (2026-10-17)</h3>"
        "<ul>"
        "<li><b>Fixed:</b> the GSD and footprint tools read a triangular mesh "
        "with its own transform and CRS. The best-fit plane area needs no "
        "orthomosaic.</li>"
        "<li><b>New:</b> a plane-mode warning for tie points far from one "
        "plane, and triangular mesh mode preselected when the chunk has a "
        "mesh.</li>"
        "<li><b>New:</b> the Gradual Selection Filter first re-adjusts the "
        "chunk and, with check or control points, suggests another state "
        "only when significantly better.</li>"
        "<li><b>New:</b> the SfM Log Parser names the open chunk, warns when "
        "the log may not belong to it, and can leave it out.</li>"
        "<li><b>Changed:</b> Console blocks for four more tools, the terms of "
        "the spaces and the 3D model, and the entire changelog.</li>"
        "</ul>"
        "<h3>v0.51.0 (2026-10-16)</h3>"
        "<ul>"
        "<li><b>New:</b> the SfM Log Parser shows which images of the open "
        "chunk share valid 3D tie points, says which image pairs the log "
        "reports, and saves graphs with three times as many pixel columns "
        "and rows as on the screen.</li>"
        "<li><b>Changed:</b> on a triangular mesh, the Image Footprint "
        "Generator moves a border vertex without a hit within the grazing "
        "limit inwards, to where the rays meet the mesh.</li>"
        "<li><b>Fixed:</b> an update in a running Metashape reloads each "
        "module after those it imports. Depth &amp; Render Export names a "
        "render that Metashape does not give.</li>"
        "<li><b>Changed:</b> clearer terminology, About sections without "
        "version history, and a Console block for the Image Filter.</li>"
        "</ul>"
        "<h3>v0.50.1 (2026-10-16)</h3>"
        "<ul>"
        "<li><b>Renamed:</b> the Camera Subset Selector and the Camera Filter "
        "are now the Image Subset Selector and the Image Filter, because "
        "they act on images.</li>"
        "<li><b>Fixed:</b> Depth &amp; Render Export says so when Metashape "
        "gives no image of level 0 of a depth map, instead of a TypeError or "
        "an AttributeError.</li>"
        "<li><b>Changed:</b> the terminology and Scale Bar A Priori "
        "Uncertainty give two reasons why the Accuracy fields of Metashape "
        "are misnamed. The Gradual Selection Filter notes that a lower RMSE "
        "alone does not show a better result, ISO 5725 is cited more fully, "
        "and long sentences are split.</li>"
        "</ul>"
        "<h3>v0.50.0 (2026-10-15)</h3>"
        "<ul>"
        "<li><b>New:</b> the page of Export HTML shows the values under the "
        "pointer, and the Gradual Selection Filter counts "
        "the images that an iteration leaves no longer oriented, naming them in "
        "the Console.</li>"
        "<li><b>Fixed:</b> the Reference panel data of PNG images in "
        "the Image Parameter Exporter, the origin in the record of Region Tools, "
        "and the r of 2D tie points that the decentring distortion carries "
        "beyond the largest distorted radius.</li>"
        "<li><b>Changed:</b> the estimated degrees of freedom omit unknowns that "
        "no observation involves, Scale Bar A Priori Uncertainty keeps four "
        "significant digits in millimetres, and texts on depth map levels, "
        "coverage shares, georeferenced chunks, and check points are "
        "corrected.</li>"
        "</ul>"
        "<h3>v0.49.1 (2026-10-15)</h3>"
        "<ul>"
        "<li><b>New (Interior Orientation Viewer):</b> a direction for the "
        "coverage by radius, chosen in the Coverage map, also in Export HTML, "
        "a legend of the Coverage mode, and a more compact layout.</li>"
        "<li><b>Fixed:</b> residuals that cannot be evaluated are left out of "
        "the sigma nought of the Gradual Selection Filter, Coordinate_System "
        "of the Image Parameter Exporter gives the name of the CRS, and the "
        "menu shows the &amp; of Depth &amp; Render Export.</li>"
        "<li><b>Changed:</b> lengths with at least four significant digits, "
        "single-channel greyscale files and the named level of each depth "
        "map, the NumPy command for PowerShell, the decentring "
        "distortion in the coverage radius, and the serial comma in all "
        "texts.</li>"
        "</ul>"
        "<h3>v0.49.0 (2026-10-14)</h3>"
        "<ul>"
        "<li><b>New (Interior Orientation Viewer):</b> a Coverage mode and the "
        "coverage by radius under the profiles, in place of the table of the "
        "profile values, which Export CSV writes.</li>"
        "<li><b>Fixed (Gradual Selection Filter):</b> Metashape&rsquo;s "
        "placeholder of 1.34&nbsp;&times;&nbsp;10<sup>154</sup> left out of the "
        "worst Reprojection Error, no tie-point &sigma; from covariance "
        "matrices of zeros, and no suggested iteration from an RMSE that "
        "hardly changes.</li>"
        "<li><b>New (Gradual Selection Filter):</b> the median tie-point "
        "&sigma; and the largest movement of the images and markers.</li>"
        "<li><b>Changed:</b> the command that installs NumPy, the key of "
        "interior orientation groups that share a label, and clearer texts on "
        "the GIFOV and on the datum in Region Tools.</li>"
        "</ul>"
        "<h3>v0.48.0 (2026-10-13)</h3>"
        "<ul>"
        "<li><b>New (Interior Orientation Viewer):</b> a table of the profile "
        "values beside the table of the vector fields, and the values under "
        "the pointer.</li>"
        "<li><b>Changed (Interior Orientation Viewer):</b> the Decentring "
        "angle only with Magnitude at the angle, also in Export HTML, and no "
        "fold of the radial distortion.</li>"
        "<li><b>Fixed:</b> metres for a chunk without a physical scale after "
        "Region Tools, and east-north-up and geocentric labels only for a "
        "chunk placed on the Earth.</li>"
        "<li><b>Fixed (Camera Filter):</b> a message when no image "
        "matches.</li>"
        "<li><b>Fixed:</b> Metashape&rsquo;s sigma nought when an adjustment "
        "repeats it, and the last reported count of the SfM Log Parser.</li>"
        "</ul>"
        "<h3>v0.47.0 (2026-10-13)</h3>"
        "<ul>"
        "<li><b>Changed (terminology, About sections):</b> clearer texts, "
        "with pixel coordinates, px, CRSs, and measurement errors.</li>"
        "<li><b>Fixed (GSD tools):</b> exact rays where Metashape&rsquo;s "
        "unprojection stops early.</li>"
        "<li><b>Fixed:</b> PDF files of Manual and Terminology without links "
        "that trigger security warnings, with page numbers in the contents of "
        "the manual.</li>"
        "<li><b>Changed (Interior Orientation Viewer):</b> Export HTML (was "
        "Export report), a black dotted azimuth mean, and the radial "
        "distortion option only with the radial profile.</li>"
        "<li><b>Renamed (Image Parameter Exporter):</b> 2D tie-point count "
        "(Projections), also in the CSV.</li>"
        "<li><b>Added (Gradual Selection Filter):</b> Metashape&rsquo;s sigma "
        "nought.</li>"
        "<li><b>Fixed:</b> Type Auto Difference, final count of the SfM Log "
        "Parser, metres and chunk units in covariance and depth files.</li>"
        "</ul>"
        "<h3>v0.46.0 (2026-10-10)</h3>"
        "<ul>"
        "<li><b>New (Interior Orientation Viewer):</b> Export report, an "
        "interactive HTML page that prints to PDF.</li>"
        "<li><b>Fixed (Interior Orientation Viewer):</b> exact rays where "
        "Metashape&rsquo;s unprojection stops early, points beyond a fold of "
        "the radial distortion reported, parameter names in capitals.</li>"
        "<li><b>Changed (Interior Orientation Viewer):</b> distinct colours of "
        "the decentring overlays, and a tooltip naming where the magnitude "
        "equals the profile function.</li>"
        "<li><b>New:</b> Manual and Terminology replaces All About Pages and "
        "Terminology, with links to the terminology.</li>"
        "<li><b>Changed (terminology, About sections):</b> clearer terms.</li>"
        "<li><b>Fixed:</b> inspection markers left out of the check-point "
        "statistics of MetaFOCUS.</li>"
        "<li><b>Added:</b> slant-distance convention of rolling-shutter rays "
        "in the GSD CSV, and notes in depth calibration files.</li>"
        "</ul>"
        "<h3>v0.45.0 (2026-10-09)</h3>"
        "<ul>"
        "<li><b>New (Interior Orientation Viewer):</b> options for the "
        "Difference against a reference calibration, residuals as cell means "
        "without weights or cell medians, and the balanced radial distortion, "
        "with Metashape&rsquo;s definitions as default.</li>"
        "<li><b>New (Interior Orientation Viewer):</b> a table of all RMS and "
        "maximum values, a CSV export, and the total magnification.</li>"
        "<li><b>New:</b> All About Pages with the Terminology page as an "
        "appendix, both with Save as PDF.</li>"
        "<li><b>Changed (About pages):</b> clearer statements.</li>"
        "<li><b>Renamed (Image Parameter Exporter):</b> Raw keypoint count is now "
        "Matched keypoint count, also in the CSV.</li>"
        "<li><b>Renamed (GSD Value Exporter):</b> check markers are now inspection "
        "markers.</li>"
        "<li><b>Added (Region Tools):</b> MIT notice of the adapted Agisoft "
        "scripts.</li>"
        "</ul>"
        "<h3>v0.44.1 (2026-10-09)</h3>"
        "<ul>"
        "<li><b>Changed (changelog):</b> the entries are shorter and name the "
        "main changes for the user.</li>"
        "</ul>"
        "<h3>v0.44.0 (2026-10-09)</h3>"
        "<ul>"
        "<li><b>New (Depth &amp; Render Export):</b> an option, on by default, "
        "writes a JSON calibration file next to each depth image, with the "
        "depth unit, the calibration, the camera and chunk transforms, the "
        "physical chunk scale, and the coordinate system, so that 3D points can "
        "be computed outside Metashape.</li>"
        "<li><b>Changed (licence):</b> GNU General Public License, version 3 "
        "or any later version, with an additional permission to convey "
        "MetaFOCUS combined with Agisoft Metashape and its Python module "
        "(ADDITIONAL_PERMISSION.txt).</li>"
        "</ul>"
        "<h3>v0.43.0 (2026-10-08)</h3>"
        "<ul>"
        "<li><b>Changed (Interior Orientation Viewer):</b> the vector fields, "
        "the profiles, and their RMS and maximum values follow the definitions "
        "of Metashape&rsquo;s Distortion Plot, including the additional "
        "corrections.</li>"
        "<li><b>Fixed (GSD Value Exporter, Image Footprint Generator, and Image "
        "Parameter Exporter):</b> the rays of an image with rolling-shutter "
        "compensation follow the camera motion during the readout that "
        "Metashape estimates.</li>"
        "<li><b>Changed (Terminology):</b> Align Photos runs the "
        "structure-from-motion procedure, and Metashape&rsquo;s aligned images "
        "are oriented images.</li>"
        "</ul>"
        "<h3>v0.42.1 (2026-10-08)</h3>"
        "<ul>"
        "<li><b>Fixed (Tie Point Covariance Exporter):</b> a positive definite "
        "matrix is decomposed as its symmetric part, so that rounding no "
        "longer changes its semi-axes.</li>"
        "<li><b>Changed (GSD Value Exporter and Terminology):</b> the shapes "
        "of the GIFOV and the value H/c<sub>pix</sub> of a nadir image also "
        "need the affinity and non-orthogonality coefficients set to zero, and "
        "the About explains PixelColumn and PixelRow.</li>"
        "</ul>"
        "<h3>v0.42.0 (2026-10-08)</h3>"
        "<ul>"
        "<li><b>Fixed (Interior Orientation Viewer and Gradual Selection "
        "Filter):</b> residuals come from Metashape&rsquo;s Camera.error, so "
        "that estimated rolling-shutter motion is no longer counted as "
        "residual, and the Regularized model counts two unknowns per image "
        "(was six).</li>"
        "<li><b>Fixed (several tools):</b> consistent covariance matrices, "
        "file names by chunk key, a question before existing files are "
        "replaced, and corrections in the SfM Log Parser.</li>"
        "<li><b>New (ten tools):</b> more user actions are echoed in the "
        "Console pane.</li>"
        "<li><b>Changed (GSD tools and Terminology):</b> the roll invariance "
        "of the area-equivalent GSD is qualified, because decentring "
        "distortion breaks it.</li>"
        "<li><b>New (LICENSE):</b> the release carries the text of the GNU "
        "General Public License.</li>"
        "</ul>"
        "<h3>v0.41.0 (2026-10-08)</h3>"
        "<ul>"
        "<li><b>New (Terminology):</b> the entry Tie point, 3D tie point, and "
        "2D tie point, and a table that maps the names of Metashape to the "
        "terms of MetaFOCUS.</li>"
        "<li><b>Changed (several tools):</b> the texts follow the tie point "
        "convention, and the Gradual Selection Filter names Clean Tie Points "
        "and describes its criteria by what they measure.</li>"
        "<li><b>New (GSD Visualiser, Marker Residual Inspector, SfM Log Parser, "
        "and Image Parameter Exporter):</b> their actions and results are "
        "written to the Console pane.</li>"
        "</ul>"
        "<h3>v0.40.0 (2026-10-07)</h3>"
        "<ul>"
        "<li><b>Changed (GSD tools and Terminology):</b> the GSD is described "
        "as the side of the square with the area that the projected row and "
        "column steps span.</li>"
        "<li><b>New (four exporters):</b> the option Delimiter and decimal "
        "separator, which also offers a decimal comma, and the GSD Visualiser "
        "reads all three choices.</li>"
        "<li><b>New (Tie Point Covariance Exporter):</b> the table can also be "
        "saved as a text file.</li>"
        "<li><b>Changed (SfM Log Parser):</b> clearer graphs, and buttons that "
        "follow the tab.</li>"
        "<li><b>Fixed (Image Parameter Exporter):</b> a chunk in a local "
        "coordinate system is no longer called georeferenced.</li>"
        "</ul>"
        "<h3>v0.39.0 (2026-10-07)</h3>"
        "<ul>"
        "<li><b>Changed (GSD tools):</b> the GSD is the area-equivalent GSD, "
        "not the geometric mean, and the CSV replaces GSD_mean by "
        "GSD_geometricMean and GSD_areaEquivalent and adds Phi_deg.</li>"
        "<li><b>Changed (Region Tools):</b> a chunk to which it gives a first "
        "transform counts as not scaled in the other tools.</li>"
        "<li><b>Changed (Interior Orientation Viewer):</b> the columns "
        "estimate/SE and p-value (two-sided), and a side panel.</li>"
        "<li><b>New (SfM Log Parser):</b> a list of the runs of a log.</li>"
        "<li><b>Changed (Marker Residual Inspector):</b> the object-space "
        "status uses twice the root sum of squares of the a priori standard "
        "deviations.</li>"
        "<li><b>Changed (Unpinned Marker Projection Remover):</b> every listed "
        "marker keeps its projections by default.</li>"
        "</ul>"
        "<h3>v0.38.0 (2026-10-06)</h3>"
        "<ul>"
        "<li><b>New (SfM Log Parser):</b> five graphs next to the "
        "summary.</li>"
        "<li><b>Changed (notation):</b> c is the principal distance, "
        "c<sub>pix</sub> = c/p the principal distance in pixels, and f only "
        "the focal length.</li>"
        "<li><b>Changed (Interior Orientation Viewer):</b> without an initial "
        "calibration, the Difference mode compares with one with all lens "
        "distortion coefficients, b1, and b2 set to zero.</li>"
        "</ul>"
        "<h3>v0.37.0 (2026-10-06)</h3>"
        "<ul>"
        "<li><b>Fixed (raster DEM mode):</b> a ray meets the raster DEM at its "
        "first crossing at the cell size of the raster DEM, and upward and "
        "horizontal rays are followed as well.</li>"
        "<li><b>Fixed (several tools):</b> the calibration of each image, the "
        "marker coordinate system, and only valid observations are used, and "
        "invalid covariance matrices are skipped.</li>"
        "<li><b>Fixed (SfM Log Parser):</b> a log with several Align Photos "
        "runs is split into its runs.</li>"
        "<li><b>Changed (all tools):</b> an image has no size, only a pixel "
        "count.</li>"
        "<li><b>Changed (GSD tools):</b> column and attribute names carry no "
        "unit, which the line # Units and the attribute 00_Units name.</li>"
        "</ul>"
        "<h3>v0.36.5 (2026-10-06)</h3>"
        "<ul>"
        "<li><b>Changed (Interior Orientation Viewer and Terminology):</b> a "
        "principal distance in pixels is not a length but the principal "
        "distance divided by the photosite pitch.</li>"
        "</ul>"
        "<h3>v0.36.4 (2026-10-06)</h3>"
        "<ul>"
        "<li><b>Changed (several tools):</b> no text relates a pixel quantity "
        "to a length, and the auxiliary rays pass at ±½ in pixel "
        "coordinates.</li>"
        "</ul>"
        "<h3>v0.36.3 (2026-10-06)</h3>"
        "<ul>"
        "<li><b>Changed (Terminology and GSD Value Exporter):</b> the GIFOV is "
        "cited to Schowengerdt (2007).</li>"
        "</ul>"
        "<h3>v0.36.2 (2026-10-05)</h3>"
        "<ul>"
        "<li><b>Changed (Terminology and several tools):</b> the projection of "
        "a photosite is called only the GIFOV, and the terms sampling cell and "
        "scale factor are no longer used.</li>"
        "</ul>"
        "<h3>v0.36.1 (2026-10-05)</h3>"
        "<ul>"
        "<li><b>Changed (Terminology):</b> the photosite, the instantaneous "
        "field of view (IFOV), about p/c on the camera axis, and the "
        "ground-projected instantaneous field of view (GIFOV) follow Verhoeven "
        "(2018a, 2018b).</li>"
        "</ul>"
        "<h3>v0.36.0 (2026-10-05)</h3>"
        "<ul>"
        "<li><b>Changed (toolbox):</b> MetaFOCUS provides fifteen tools in "
        "four groups, and the menu, the About page, the Console summary, and "
        "README.md list these fifteen.</li>"
        "</ul>"
        "<h3>v0.35.0 (2026-10-05)</h3>"
        "<ul>"
        "<li><b>New (Terminology):</b> a page in the MetaFOCUS menu defines "
        "the terms of the tools, their About pages, and the MetaFOCUS paper, "
        "with their sources.</li>"
        "<li><b>Changed (all tools):</b> perspective centre instead of "
        "projection centre, structure from motion in lower case, and keypoint "
        "as one word.</li>"
        "</ul>"
        "<h3>v0.34.0 (2026-10-05)</h3>"
        "<ul>"
        "<li><b>Fixed (Tie Point Covariance Exporter):</b> without the local "
        "east-north-up frame, the columns of a georeferenced chunk say that "
        "the coordinates are geocentric (Earth-centred, Earth-fixed), in "
        "metres.</li>"
        "<li><b>Changed (Scale Bar A Priori Uncertainty, Tie Point Covariance "
        "Exporter, and Marker Residual Inspector):</b> the texts follow OIML R "
        "35-1, the VIM, and the GUM, and Outlier ratio is now Maximum-to-median "
        "ratio.</li>"
        "<li><b>Changed (code):</b> identifiers follow the terminology of "
        "0.33.1 and 0.33.2.</li>"
        "</ul>"
        "<h3>v0.33.2 (2026-10-05)</h3>"
        "<ul>"
        "<li><b>Renamed (Scale Bar A Priori Uncertainty):</b> Scale Bar "
        "Accuracy is named after the a priori standard uncertainty that it "
        "assigns.</li>"
        "<li><b>Changed (Image Parameter Exporter):</b> the image scale factor "
        "M is now the image scale number m.</li>"
        "</ul>"
        "<h3>v0.33.1 (2026-10-05)</h3>"
        "<ul>"
        "<li><b>Changed (GSD tools):</b> the texts follow the photosite "
        "terminology, and GSD values are given in metres, not metres per "
        "pixel.</li>"
        "<li><b>Changed (several tools):</b> image orientation is no longer "
        "called alignment, decentring is spelt the UK way, and Align CRS to "
        "Region is now Align Coordinate Axes to Region.</li>"
        "</ul>"
        "<h3>v0.33.0 (2026-10-03)</h3>"
        "<ul>"
        "<li><b>Fixed (Interior Orientation Viewer and Gradual Selection "
        "Filter):</b> the counts of observations, unknowns, and degrees of "
        "freedom include only what takes part in the bundle adjustment.</li>"
        "<li><b>Fixed (Interior Orientation Viewer):</b> multi-camera rigs, "
        "other camera models, and the residuals of every image.</li>"
        "<li><b>Added (Gradual Selection Filter):</b> a Stop button and the "
        "check-point RMSE.</li>"
        "<li><b>Fixed (most tools):</b> many corrections, among them "
        "footprints along the whole image border in plane mode and keyframes "
        "no longer treated as images.</li>"
        "<li><b>Changed (all tools):</b> written-out labels, and messages "
        "titled with the name of the tool.</li>"
        "<li><b>Fixed (installation):</b> a failed update restores the "
        "previous installation.</li>"
        "</ul>"
        "<h3>v0.32.0 (2026-10-02)</h3>"
        "<ul>"
        "<li><b>Fixed (raster DEM mode):</b> very oblique rays that meet the "
        "surface are no longer reported as misses.</li>"
        "<li><b>Added (Scale Bar Accuracy):</b> options for distances measured "
        "from the end of the tape and for a tape in service.</li>"
        "<li><b>Changed (all tools):</b> variable names and column labels are "
        "written out.</li>"
        "</ul>"
        "<h3>v0.31.0 (2026-10-02)</h3>"
        "<ul>"
        "<li><b>Fixed (Interior Orientation Viewer):</b> the statistics line "
        "and the most correlated pairs are shown again.</li>"
        "<li><b>Changed (all tools):</b> the automated splitting of long "
        "functions in 0.29.1 is undone.</li>"
        "</ul>"
        "<h3>v0.30.0 (2026-10-02)</h3>"
        "<ul>"
        "<li><b>Fixed (Interior Orientation Viewer and GSD Visualiser):</b> "
        "variables that the renaming of 0.29.0 had merged are separated again, "
        "which restores the residual and distortion fields, the correlation "
        "table, and the heat maps.</li>"
        "</ul>"
        "<h3>v0.29.1 (2026-10-02)</h3>"
        "<ul>"
        "<li><b>Changed (all tools):</b> long functions are split into named "
        "helpers. Results are unchanged.</li>"
        "</ul>"
        "<h3>v0.29.0 (2026-10-02)</h3>"
        "<ul>"
        "<li><b>Fixed (Tie Point Covariance Exporter):</b> homogeneous "
        "coordinates are divided by w before they are transformed.</li>"
        "<li><b>Fixed (Image Parameter Exporter):</b> the uncertainty of the "
        "perspective centre is given in metres in every coordinate "
        "system.</li>"
        "<li><b>Fixed (Interior Orientation Viewer and Gradual Selection "
        "Filter):</b> one shared set of counts of observations and "
        "unknowns.</li>"
        "<li><b>Changed (all tools):</b> code conventions and "
        "terminology.</li>"
        "</ul>"
        "<h3>v0.28.0 (2026-10-02)</h3>"
        "<ul>"
        "<li><b>Fixed (raster DEM mode):</b> the mode runs, with "
        "Elevation.altitude() only and in a local metric frame.</li>"
        "<li><b>Fixed (GSD tools):</b> one shared computation for every tool "
        "and mode, with the sampling grid centred.</li>"
        "</ul>"
        "<h3>v0.27.0 (2026-07-27)</h3>"
        "<ul>"
        "<li><b>Fixed (Marker Residual Inspector):</b> the object-space error "
        "is evaluated in a local east-north-up frame.</li>"
        "<li><b>Fixed (several tools):</b> the residual RMS of the Interior "
        "Orientation Viewer, the region axes of Region Tools, and raster DEM "
        "footprints of chunks without a georeference.</li>"
        "</ul>"
        "<h3>v0.26.1 (2026-06-12)</h3>"
        "<ul>"
        "<li><b>Improved (Scale Bar Accuracy):</b> the About says that the "
        "values treat the distances as independent, and the Console reports "
        "the implied a priori relative scale uncertainty.</li>"
        "</ul>"
        "<h3>v0.26.0 (2026-06-12)</h3>"
        "<ul>"
        "<li><b>Changed (Scale Bar Accuracy):</b> the maximum permissible "
        "error of the tape is converted to a standard uncertainty (divided by "
        "√3) before it is combined with the additional uncertainty.</li>"
        "</ul>"
        "<h3>v0.25.1 (2026-06-12)</h3>"
        "<ul>"
        "<li><b>Improved (Interior Orientation Viewer):</b> the decentring "
        "field and profiles include the factor (1 + P3 r² + P4 r⁴).</li>"
        "<li><b>Added:</b> an offline test suite.</li>"
        "</ul>"
        "<h3>v0.25.0 (2026-06-12)</h3>"
        "<ul>"
        "<li><b>Fixed (GSD tools):</b> the GSD comes from four auxiliary rays "
        "intersected with the tangent plane, as row and column sampling "
        "distances and their geometric mean (the single-ray value "
        "overestimated it away from the camera axis).</li>"
        "<li><b>New (Interior Orientation Viewer):</b> mean curves on the "
        "decentring profile.</li>"
        "</ul>"
        "<h3>v0.24.0 (2026-06-12)</h3>"
        "<ul>"
        "<li><b>Renamed:</b> Camera Parameter Exporter is now the Image "
        "Parameter Exporter, because its rows describe images.</li>"
        "</ul>"
        "<h3>v0.21.0 (2026-06-09)</h3>"
        "<ul>"
        "<li><b>New (Depth &amp; Render Export):</b> exports the stored depth "
        "maps and renders images, depth, and masks from the triangular "
        "mesh.</li>"
        "</ul>"
        "<h3>v0.20.0 (2026-06-09)</h3>"
        "<ul>"
        "<li><b>New (GSD Visualiser):</b> statistics, histograms, and heat maps "
        "of the GSD CSV files, per image and in an overview.</li>"
        "</ul>"
        "<h3>v0.19.0 (2026-06-08)</h3>"
        "<ul>"
        "<li><b>New (menu):</b> an icon for every tool, and four named "
        "groups.</li>"
        "<li><b>New (Gradual Selection Filter):</b> Image Count as a fourth "
        "criterion, a stepped absolute-threshold mode, a stop at a chosen RMS "
        "reprojection error, more tracked metrics, and a suggested "
        "iteration.</li>"
        "<li><b>Fixed:</b> reloading no longer duplicates menu entries, and "
        "the GSD Value Exporter runs in a chunk without a scale.</li>"
        "</ul>"
        "<h3>v0.18.0 (2026-06-07)</h3>"
        "<ul>"
        "<li><b>New (Gradual Selection Filter):</b> iteratively removes the "
        "worst tie points and optimises the cameras after each step, based on "
        "an original script by Martin Wieser.</li>"
        "</ul>"
        "<h3>v0.17.0 (2026-05-14)</h3>"
        "<ul>"
        "<li><b>New (Marker Residual Inspector):</b> four tabs of marker "
        "residuals, with review flags and CSV export.</li>"
        "</ul>"
        "<h3>v0.16.0 (2026-05-13)</h3>"
        "<ul>"
        "<li><b>New (Scale Bar Accuracy):</b> values for Metashape scale bars "
        "from the tape class of OIML R 35-1.</li>"
        "<li><b>Improved (several tools):</b> vector scale and decentring "
        "curves in the Interior Orientation Viewer, a delimiter choice for CSV "
        "files, and Copy Region to Other Chunks.</li>"
        "</ul>"
        "<h3>v0.15.0 (2026-03-25)</h3>"
        "<ul>"
        "<li><b>New (Interior Orientation Viewer):</b> the Corrections, "
        "Difference, and Residuals modes.</li>"
        "<li><b>Changed (Interior Orientation Viewer):</b> f is called c "
        "(principal distance).</li>"
        "</ul>"
        "<h3>v0.14.0 (2026-03-25)</h3>"
        "<ul>"
        "<li><b>New:</b> Interior Orientation Viewer: estimates, standard "
        "errors, test statistics, correlations, and degrees of freedom.</li>"
        "<li><b>Fixed (Region Tools):</b> the Z axis points upwards after "
        "Align CRS to Region.</li>"
        "</ul>"
        "<h3>v0.13.0 (2026-03-24)</h3>"
        "<ul>"
        "<li><b>New:</b> Region Tools and the Changelog menu item.</li>"
        "</ul>"
        "<h3>v0.12.0 (2026-03-24)</h3>"
        "<ul>"
        "<li><b>New:</b> SfM Log Parser and Camera Filter.</li>"
        "</ul>"
        "<h3>v0.11.0 (2026-03-24)</h3>"
        "<ul>"
        "<li><b>New:</b> Unpinned Marker Projection Remover and Camera Subset "
        "Selector.</li>"
        "<li><b>Fixed:</b> dialogs open faster.</li>"
        "</ul>"
        "<h3>v0.10.0 (2026-03-24)</h3>"
        "<ul>"
        "<li><b>New:</b> Tie Point Covariance Exporter.</li>"
        "</ul>"
        "<h3>v0.9.0 (2026-03-24)</h3>"
        "<ul>"
        "<li><b>New:</b> the first release, with the loader and its "
        "installation, Camera Parameter Exporter, Image Footprint Generator, "
        "GSD Value Exporter, and the shared module.</li>"
        "</ul>"
    )
    layout.addWidget(textBrowser)
    closeButton = QtWidgets.QPushButton("Close")
    closeButton.clicked.connect(changelogDialog.accept)
    layout.addWidget(closeButton)
    changelogDialog.exec_()


def _ShowToolboxAbout(returnHtmlOnly=False):
    """Show the About of the toolbox, or, with *returnHtmlOnly*, return
    its HTML for Manual and Terminology."""
    from MetaFOCUSCore import _METAFOCUS_VERSION, _METAFOCUS_DATE
    aboutText = (
        "<h2>MetaFOCUS</h2>"
        "<p><b>Metashape Functions for Optimising, Characterising, "
        "and Understanding Structure from Motion</b></p>"
        "<p>Version {} ({})</p>"
        "<p><b>Author:</b> Geert J. Verhoeven (ORCID: "
        "<a href='https://orcid.org/0000-0003-4825-9604'>0000-0003-4825-9604</a>)<br>"
        "<b>Contact:</b> <a href='mailto:geert.verhoeven@univie.ac.at'>geert.verhoeven@univie.ac.at</a><br>"
        "<b>Licence:</b> GNU General Public License, version 3 or any later version "
        "(GPL-3.0-or-later), with an additional permission to convey MetaFOCUS "
        "combined with Agisoft Metashape and its Python module (the texts are in the "
        "files LICENSE and ADDITIONAL_PERMISSION.txt of the MetaFOCUS ZIP file)<br>"
        "<b>Adapted code:</b> Region Tools adapts two scripts, licensed under the MIT "
        "License, of Agisoft&rsquo;s metashape-scripts repository, Copyright (c) 2017 "
        "Agisoft LLC (the notice is in the file THIRD_PARTY_NOTICES.txt of the "
        "MetaFOCUS ZIP file)</p>"
        "<hr>"
        "<h3>Modules</h3>"
        "<p><b>SfM Process Diagnostics</b></p>"
        "<ul>"
        "<li>SfM Log Parser</li>"
        "<li>Interior Orientation Viewer</li>"
        "<li>Tie Point Covariance Exporter</li>"
        "<li>Image Parameter Exporter</li>"
        "</ul>"
        "<p><b>Control Measurements and Residuals</b></p>"
        "<ul>"
        "<li>Marker Residual Inspector</li>"
        "<li>Unpinned Marker Projection Remover</li>"
        "<li>Scale Bar A Priori Uncertainty</li>"
        "</ul>"
        "<p><b>Filtering and Project Management</b></p>"
        "<ul>"
        "<li>Gradual Selection Filter</li>"
        "<li>Image Subset Selector</li>"
        "<li>Image Filter</li>"
        "<li>Region Tools</li>"
        "</ul>"
        "<p><b>Surface-Derived Project Outputs</b></p>"
        "<ul>"
        "<li>Image Footprint Generator</li>"
        "<li>GSD Value Exporter</li>"
        "<li>GSD Visualiser</li>"
        "<li>Depth &amp; Render Export</li>"
        "</ul>"
        "<p><i>Photogrammetry Utilities (shared functions, "
        "not a menu item)</i></p>"
        "<p><b>Manual and Terminology</b></p>"
        "<p><b>Check for Updates</b></p>"
        "<h3>Installation</h3>"
        "<p>When <code>MetaFOCUS.py</code> is first run (Tools &rarr; Run Script), "
        "MetaFOCUS offers to install itself. Choosing <b>Install permanently</b> "
        "copies <code>MetaFOCUS.py</code> and the <code>MetaFOCUSCore/</code> folder "
        "into the Metashape scripts folder, after which MetaFOCUS loads automatically "
        "on every startup. Alternatively, choosing <b>Load this session only</b> "
        "loads it for the current session.</p>"
        "<p>To install MetaFOCUS manually instead, copy both items into the Metashape "
        "scripts folder. On Windows, this folder is:<br>"
        "<code>%LOCALAPPDATA%&#92;Agisoft&#92;Metashape Pro&#92;scripts</code><br>"
        "that is, <code>C:&#92;Users&#92;&lt;username&gt;&#92;AppData&#92;Local&#92;Agisoft&#92;Metashape "
        "Pro&#92;scripts</code>. (macOS: <code>~/Library/Application "
        "Support/Agisoft/Metashape Pro/scripts</code>, Linux: <code>~/.local/share/Agisoft/Metashape "
        "Pro/scripts</code>.)"
        "</p>"
        "<h3>Updates</h3>"
        "<p><b>Check for Updates</b> in the MetaFOCUS menu asks GitHub for the latest MetaFOCUS release. When it "
        "is newer, MetaFOCUS downloads it, verifies its checksum, and lists its changes. <b>Install</b> puts it "
        "into the Metashape scripts folder, and Metashape must then be restarted. Nothing is installed without "
        "clicking Install. When allowed, MetaFOCUS also checks for updates when it starts (at most once a day). "
        "The window of Check for Updates changes this choice.</p>"
    ).format(_METAFOCUS_VERSION, _METAFOCUS_DATE)
    if returnHtmlOnly:
        return aboutText

    # A window that scrolls and fits the screen, because the text is
    # longer than a message box can show on a small screen.
    from PySide2 import QtWidgets, QtCore
    aboutDialog = QtWidgets.QDialog(QtWidgets.QApplication.activeWindow())
    aboutDialog.setWindowTitle("About: MetaFOCUS")
    aboutDialog.setWindowFlags(aboutDialog.windowFlags() & ~QtCore.Qt.WindowContextHelpButtonHint)
    layout = QtWidgets.QVBoxLayout(aboutDialog)
    textBrowser = QtWidgets.QTextBrowser()
    textBrowser.setOpenExternalLinks(True)
    textBrowser.setHtml(aboutText)
    layout.addWidget(textBrowser)
    closeButton = QtWidgets.QPushButton("Close")
    closeButton.clicked.connect(aboutDialog.accept)
    layout.addWidget(closeButton)
    aboutDialog.resize(*_DialogSizeOnScreen(_ABOUT_DIALOG_WIDTH, _ABOUT_DIALOG_HEIGHT))
    aboutDialog.exec_()


# The size of the About window of the toolbox on a large screen, in
# screen pixels.
_ABOUT_DIALOG_WIDTH = 640
_ABOUT_DIALOG_HEIGHT = 720

# The space that a window of the loader leaves free around it on a small
# screen, in screen pixels.
_SCREEN_MARGIN = 80


def _DialogSizeOnScreen(defaultWidth, defaultHeight):
    """(width, height) of a window of the loader: its default size,
    reduced to fit the available area of the primary screen with
    _SCREEN_MARGIN to spare.  The default size when the screen cannot be
    read (reported once in the Console)."""
    from PySide2 import QtGui
    try:
        availableGeometry = QtGui.QGuiApplication.primaryScreen().availableGeometry()
        return (max(1, min(defaultWidth, availableGeometry.width() - _SCREEN_MARGIN)),
                max(1, min(defaultHeight, availableGeometry.height() - _SCREEN_MARGIN)))
    except Exception as suppressedError:
        _ReportSuppressedError("MetaFOCUS._DialogSizeOnScreen", suppressedError)
        return defaultWidth, defaultHeight


def _ShowManualAndTerminology():
    """MetaFOCUS > Manual and Terminology: every About section, with the
    About of the toolbox first, and the terminology, in one window with
    links from the terms to their entries, Back, Save as PDF, and Save
    Terminology as PDF."""
    from MetaFOCUSCore import aboutPages
    aboutPages.ShowManualAndTerminology(_ShowToolboxAbout(returnHtmlOnly=True))


def _MoveDirectoryToFrontOfSearchPath(directory, searchPath):
    """Make *directory* the first entry of *searchPath* (sys.path), so
    that the MetaFOCUSCore package next to this loader is the one that
    is imported and reloaded, also when another copy was loaded
    earlier in the session.  Other spellings of the same folder are
    removed, and the list is changed in place."""
    normalisedDirectory = os.path.normcase(os.path.normpath(directory))
    searchPath[:] = [entry for entry in searchPath
                     if not (isinstance(entry, str) and entry
                             and os.path.normcase(os.path.normpath(entry)) == normalisedDirectory)]
    searchPath.insert(0, directory)


def _LoadMetaFOCUS():
    """Import and register all MetaFOCUS modules."""
    # Ensure the parent directory of MetaFOCUSCore is on sys.path.
    _MoveDirectoryToFrontOfSearchPath(_THIS_DIRECTORY, sys.path)

    import MetaFOCUSCore
    importlib.reload(MetaFOCUSCore)

    from MetaFOCUSCore import photogrammetryUtilities
    importlib.reload(photogrammetryUtilities)

    from MetaFOCUSCore import imageParameterExporter
    importlib.reload(imageParameterExporter)

    from MetaFOCUSCore import footprintGenerator
    importlib.reload(footprintGenerator)

    from MetaFOCUSCore import gsdValueExporter
    importlib.reload(gsdValueExporter)

    from MetaFOCUSCore import gsdVisualiser
    importlib.reload(gsdVisualiser)

    from MetaFOCUSCore import depthRenderExporter
    importlib.reload(depthRenderExporter)

    from MetaFOCUSCore import tiePointCovarianceExporter
    importlib.reload(tiePointCovarianceExporter)

    from MetaFOCUSCore import unpinnedMarkerProjectionRemover
    importlib.reload(unpinnedMarkerProjectionRemover)

    from MetaFOCUSCore import imageSubsetSelector
    importlib.reload(imageSubsetSelector)

    from MetaFOCUSCore import imageFilter
    importlib.reload(imageFilter)

    from MetaFOCUSCore import sfmLogParser
    importlib.reload(sfmLogParser)

    from MetaFOCUSCore import regionTools
    importlib.reload(regionTools)

    # The module of the Export HTML page before the viewer, which imports
    # its functions when it is loaded, so that a second load in the same
    # Metashape session gives the page of the version loaded last.
    from MetaFOCUSCore import interiorOrientationReport
    importlib.reload(interiorOrientationReport)

    from MetaFOCUSCore import interiorOrientationViewer
    importlib.reload(interiorOrientationViewer)

    from MetaFOCUSCore import scaleBarAPrioriUncertainty
    importlib.reload(scaleBarAPrioriUncertainty)

    from MetaFOCUSCore import markerResidualInspector
    importlib.reload(markerResidualInspector)

    from MetaFOCUSCore import gradualSelectionFilter
    importlib.reload(gradualSelectionFilter)

    from MetaFOCUSCore import terminology
    importlib.reload(terminology)

    from MetaFOCUSCore import aboutPages
    importlib.reload(aboutPages)

    from MetaFOCUSCore import updateCheck
    importlib.reload(updateCheck)

    # --- Register menu items in named submenu groups ---
    # Each module's _MENU_LABEL carries its submenu path, so the
    # four groups appear as labelled submenus under "MetaFOCUS".

    # Group 1: SfM Process Diagnostics
    sfmLogParser.RegisterMenuItem()
    interiorOrientationViewer.RegisterMenuItem()
    tiePointCovarianceExporter.RegisterMenuItem()
    imageParameterExporter.RegisterMenuItem()

    # Group 2: Control Measurements and Residuals
    markerResidualInspector.RegisterMenuItem()
    unpinnedMarkerProjectionRemover.RegisterMenuItem()
    scaleBarAPrioriUncertainty.RegisterMenuItem()

    # Group 3: Filtering and Project Management
    gradualSelectionFilter.RegisterMenuItem()
    imageSubsetSelector.RegisterMenuItem()
    imageFilter.RegisterMenuItem()
    regionTools.RegisterMenuItem()

    # Group 4: Surface-Derived Project Outputs
    footprintGenerator.RegisterMenuItem()
    gsdValueExporter.RegisterMenuItem()
    gsdVisualiser.RegisterMenuItem()
    depthRenderExporter.RegisterMenuItem()

    # Separator before the top-level Manual and Terminology / Changelog /
    # About entries.
    Metashape.app.addMenuSeparator("MetaFOCUS")

    # --- Manual and Terminology menu item ---
    manualMenuLabel = "MetaFOCUS/Manual and Terminology"
    try:
        Metashape.app.removeMenuItem(manualMenuLabel)
    except Exception as suppressedError:
        _ReportSuppressedError("MetaFOCUS._LoadMetaFOCUS.manualMenuItem", suppressedError)
    Metashape.app.addMenuItem(manualMenuLabel, _ShowManualAndTerminology)

    # --- Changelog menu item ---
    changelogMenuLabel = "MetaFOCUS/Changelog"
    try:
        Metashape.app.removeMenuItem(changelogMenuLabel)
    except Exception as suppressedError:
        _ReportSuppressedError("MetaFOCUS._LoadMetaFOCUS.changelogMenuItem", suppressedError)
    Metashape.app.addMenuItem(changelogMenuLabel, _ShowChangelog)

    # --- Check for Updates menu item ---
    updatesMenuLabel = "MetaFOCUS/Check for Updates"
    try:
        Metashape.app.removeMenuItem(updatesMenuLabel)
    except Exception as suppressedError:
        _ReportSuppressedError("MetaFOCUS._LoadMetaFOCUS.updatesMenuItem", suppressedError)
    Metashape.app.addMenuItem(updatesMenuLabel, updateCheck.CheckForUpdatesFromMenu)

    # --- About MetaFOCUS menu item ---
    aboutMenuLabel = "MetaFOCUS/About MetaFOCUS"
    try:
        Metashape.app.removeMenuItem(aboutMenuLabel)
    except Exception as suppressedError:
        _ReportSuppressedError("MetaFOCUS._LoadMetaFOCUS.aboutMenuItem", suppressedError)
    Metashape.app.addMenuItem(aboutMenuLabel, _ShowToolboxAbout)

    # --- Console summary ---
    from MetaFOCUSCore import _METAFOCUS_VERSION
    print("")
    print("=" * 56)
    print("  MetaFOCUS v{} loaded successfully.".format(
        _METAFOCUS_VERSION))
    print("  Metashape Functions for Optimising, Characterising,")
    print("  and Understanding Structure from Motion.")
    print("")
    print("  Modules:")
    print("  [SfM Process Diagnostics]")
    print("    SfM Log Parser")
    print("    Interior Orientation Viewer")
    print("    Tie Point Covariance Exporter")
    print("    Image Parameter Exporter")
    print("  [Control Measurements and Residuals]")
    print("    Marker Residual Inspector")
    print("    Unpinned Marker Projection Remover")
    print("    Scale Bar A Priori Uncertainty")
    print("  [Filtering and Project Management]")
    print("    Gradual Selection Filter")
    print("    Image Subset Selector")
    print("    Image Filter")
    print("    Region Tools")
    print("  [Surface-Derived Project Outputs]")
    print("    Image Footprint Generator")
    print("    GSD Value Exporter")
    print("    GSD Visualiser")
    print("    Depth & Render Export")
    print("  [Shared]")
    print("    Photogrammetry Utilities (not a menu item)")
    # Manual and Terminology, Changelog, Check for Updates, and About
    # MetaFOCUS are entries that the loader adds at the bottom of the menu,
    # not tools, so the summary does not list them (Geert, 16 October 2026).
    print("=" * 56)
    print("")

    # The automatic check, or the question whether it is allowed, with the
    # installer of this loader for Install.  A failure does not stop the
    # load.
    try:
        updateCheck.StartAutomaticCheck(_InstallToolboxIntoDirectory, _SCRIPTS_DIRECTORY)
    except Exception as suppressedError:
        _ReportSuppressedError("MetaFOCUS._LoadMetaFOCUS.updateCheck", suppressedError)


def _ReportLoadFailure(loadError):
    """Tell the user that loading MetaFOCUS failed: the full traceback
    (module, line, and call chain) in the Console, a short message in a
    message box."""
    print("MetaFOCUS: Failed to load: {}".format(loadError))
    print("".join(traceback.format_exception(
        type(loadError), loadError, loadError.__traceback__)), end="")
    try:
        Metashape.app.messageBox(
            "MetaFOCUS failed to load:\n\n{}\n\n"
            "The Console shows the full error with module and line. "
            "If a module is reported missing, make sure that the "
            "MetaFOCUSCore folder is in the same folder as "
            "MetaFOCUS.py.".format(loadError))
    except Exception as suppressedError:
        _ReportSuppressedError("MetaFOCUS._ReportLoadFailure", suppressedError)


# ===================================================================
# Main entry point
# ===================================================================

if not _IsInstalledInScriptsDirectory():
    # Running from a non-standard location: offer installation.
    if os.path.isdir(_CORE_SOURCE):
        _OfferInstallation()
    else:
        print("MetaFOCUS: MetaFOCUSCore/ folder not found in {}".format(
            _THIS_DIRECTORY))

try:
    _LoadMetaFOCUS()
except Exception as loadError:
    _ReportLoadFailure(loadError)
