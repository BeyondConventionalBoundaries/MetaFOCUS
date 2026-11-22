"""
Check for Updates for MetaFOCUS
===============================

Checks GitHub for a newer release of MetaFOCUS, shows what changed since
the version in use, and installs the new release after a click.

- MetaFOCUS > Check for Updates asks the GitHub API for the latest
  release (LATEST_RELEASE_URL).  When it is newer than the version in
  use, the check downloads its ZIP file from the MetaFOCUS repository
  (ReleaseAsset), verifies its SHA-256 checksum against the digest that
  GitHub gives for the file, checks the package (CheckReleasePackage),
  and reads the entries of its changelog that are newer than the version
  in use, without running any of its code (ChangelogHtmlOfLoader).  The
  window of the result (UpdateDialog) shows them.  Install reads the ZIP
  file again, checks its checksum and its package again, unpacks the
  checked bytes (UnpackPackage), and installs them with the installer of
  the loader (_InstallToolboxIntoDirectory of MetaFOCUS.py), as an
  installation from a downloaded ZIP file does.  Metashape must then be
  restarted.
- At the first load of this version, MetaFOCUS asks once whether it may
  check automatically (AskAboutAutomaticCheck).  When allowed, it checks
  when MetaFOCUS starts, at most once a day
  (AUTOMATIC_CHECK_INTERVAL_SECONDS), and shows a new version once.  The
  window of Check for Updates changes this choice.
- The checks run in a background thread (RunCheck), so that Metashape
  does not wait for the network, and a timer of the main thread shows
  their result (PollCheck).  A check counts as running until its result
  is shown, also across a reload of this module, and the folders that
  earlier checks left behind are removed (RemoveStaleTemporaryFolders).
- Nothing is downloaded without a check that the user asks for or
  allows, and nothing is installed without a click on Install.  The
  settings are kept with QSettings (SETTINGS_ORGANISATION and
  SETTINGS_APPLICATION, in the registry on Windows).  The setting
  TEST_INSTALLED_VERSION_SETTING, when present, replaces the version in
  use in the check, so that the path of an update can be tried
  before a newer release exists.  The Console names it whenever it is
  used.

Author:      Geert J. Verhoeven (ORCID: 0000-0003-4825-9604)
Version:     1.1.0 (2026-11-22)
Date:        2026-11-19
Licence:     GPL-3.0-or-later WITH GPL-3.0-linking-exception
Contact:     geert.verhoeven@univie.ac.at
Requires:    Agisoft Metashape Professional 2.x+
Developed for: Agisoft Metashape Professional 2.3.2

Usage:
    Loaded by the MetaFOCUS loader (MetaFOCUS.py), which adds
    MetaFOCUS > Check for Updates and starts the automatic check.
"""

import ast
import datetime
import hashlib
import io
import json
import os
import re
import shutil
import tempfile
import threading
import time
import urllib.request
import zipfile

from PySide2 import QtWidgets, QtCore, QtGui

from . import _METAFOCUS_VERSION
from .photogrammetryUtilities import DescribeException, ReportSuppressedError

_TOOL_NAME = "MetaFOCUS: Check for Updates"

# The repository of MetaFOCUS on GitHub, the API address of its latest
# release (not a draft and not a prerelease), the page of its releases,
# and the start of the address of every file of a release.  A check
# downloads only from such an address and opens only a page of the
# releases.
REPOSITORY = "BeyondConventionalBoundaries/MetaFOCUS"
LATEST_RELEASE_URL = "https://api.github.com/repos/{}/releases/latest".format(REPOSITORY)
RELEASES_PAGE_URL = "https://github.com/{}/releases".format(REPOSITORY)
RELEASE_DOWNLOAD_URL_START = RELEASES_PAGE_URL + "/download/"

# A version of three numbers separated by points, with or without the v of
# a tag.
VERSION_PATTERN = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")

# The package folder of the toolbox, next to its loader MetaFOCUS.py.
PACKAGE_NAME = "MetaFOCUSCore"

# The heading of the changelog of the loader, and one of its entries: the
# heading of a version with its date and what follows up to the next one.
CHANGELOG_HEADING = "<h2>MetaFOCUS Changelog</h2>"
CHANGELOG_ENTRY_PATTERN = re.compile(r"<h3>v(\d+\.\d+\.\d+) \(([^)]*)\)</h3>(.*?)(?=<h3>v\d+\.\d+\.\d+ \(|$)", re.S)

# The time that a request may take, and the largest answer of the API,
# ZIP file, and unpacked content of a ZIP file that a check accepts, in
# bytes.  The ZIP file of a release has less than 1 MB.
NETWORK_TIMEOUT_SECONDS = 30
LARGEST_RELEASE_DATA_BYTES = 2000000
LARGEST_PACKAGE_BYTES = 50000000
LARGEST_UNPACKED_BYTES = 200000000

# The beginnings of the names of the temporary folders of a download and
# of the unpacked files of Install, and the age at which a check removes
# such a folder that an earlier check left behind, for example when
# Metashape was closed before the result of the check was shown.
DOWNLOAD_FOLDER_PREFIX = "MetaFOCUS_download_"
UNPACKED_FOLDER_PREFIX = "MetaFOCUS_unpacked_"
STALE_FOLDER_AGE_SECONDS = 24 * 60 * 60

# The settings of the check (QSettings): whether MetaFOCUS may check
# automatically ("yes" or "no", absent until the question is answered),
# the time of the last automatic check, the version that an automatic
# check showed last, and the version that replaces the one in use in a
# trial of the path of an update.
SETTINGS_ORGANISATION = "MetaFOCUS"
SETTINGS_APPLICATION = "MetaFOCUS"
AUTOMATIC_SETTING = "updates/automatic"
LAST_CHECK_SETTING = "updates/lastCheck"
ANNOUNCED_VERSION_SETTING = "updates/announcedVersion"
TEST_INSTALLED_VERSION_SETTING = "updates/testInstalledVersion"

# The automatic check runs at most once in this time, the question about
# it comes this long after the load, so that the main window of
# Metashape is shown first, and the timer looks this often for the
# result of a check.
AUTOMATIC_CHECK_INTERVAL_SECONDS = 24 * 60 * 60
QUESTION_DELAY_MILLISECONDS = 5000
POLL_INTERVAL_MILLISECONDS = 300

# The results of a check (CheckForUpdate): a newer release whose ZIP file
# is downloaded and verified, the version in use is the latest, it is
# newer than the latest release, an automatic check found the release that
# it showed already, and a failure.
STATUS_AVAILABLE = "available"
STATUS_CURRENT = "current"
STATUS_NEWER = "newer"
STATUS_ANNOUNCED = "announced"
STATUS_FAILED = "failed"

AUTOMATIC_CHECK_QUESTION = (
    "MetaFOCUS can ask GitHub for a new version when it starts, at most once a day. A new version is then shown "
    "with its changes, and it is installed only after a click on Install. MetaFOCUS > Check for Updates checks at "
    "any time and changes this choice.\n\nCheck automatically?")
AUTOMATIC_CHECK_BOX_TEXT = "Check for a new version when MetaFOCUS starts (at most once a day)"

# The installer of the loader and the scripts directory, which the loader
# gives at its load (StartAutomaticCheck), and the check that runs, whose
# record a reload of this module keeps (importlib.reload runs this module
# again in the same namespace), so that its result is still shown and its
# folder removed.
_INSTALLER = {"installFunction": None, "scriptsDirectory": ""}
_RUNNING_CHECK = globals().get("_RUNNING_CHECK") or {
    "thread": None, "timer": None, "showAlways": False, "result": None, "downloadFolder": None}
# Whether this session asked the question on the automatic check
# (AskAboutAutomaticCheck), a record that a reload of this module keeps as
# that of the running check, so that MetaFOCUS loaded twice within
# QUESTION_DELAY_MILLISECONDS asks it once.
_AUTOMATIC_CHECK_QUESTION = globals().get("_AUTOMATIC_CHECK_QUESTION") or {"isAsked": False}


# ===================================================================
# Versions, releases, and changelogs
# ===================================================================

def ParseVersion(text):
    """(major, minor, patch) of a version of three numbers separated by
    points, with or without the v of a tag, or None for any other text."""
    match = VERSION_PATTERN.match(str(text or "").strip())
    return tuple(int(part) for part in match.groups()) if match else None


def ReleaseAssetName(versionText):
    """The name of the ZIP file of the release of version *versionText*:
    MetaFOCUS_ and the version with hyphens for its points, as in
    MetaFOCUS_1-1-0.zip."""
    return "MetaFOCUS_{}.zip".format(versionText.replace(".", "-"))


def ReleaseAsset(releaseData, versionText):
    """(download address, SHA-256 checksum in lower-case hexadecimal or
    None) of the ZIP file of the release of version *versionText*
    (ReleaseAssetName), from its data of the GitHub API, or None unless
    the release has exactly one file of that name, so that the order of
    its files does not matter.  The checksum is the digest "sha256:..."
    that GitHub gives for every file of a release."""
    expectedName = ReleaseAssetName(versionText)
    matchingAssets = [asset for asset in releaseData.get("assets") or []
                      if str(asset.get("name", "")) == expectedName]
    if len(matchingAssets) != 1:
        return None
    digest = str(matchingAssets[0].get("digest") or "")
    checksum = digest[len("sha256:"):].lower() if digest.lower().startswith("sha256:") else None
    return matchingAssets[0].get("browser_download_url"), checksum


def ReleasePageUrl(releaseData):
    """The page of the release in its data of the GitHub API, when it is a
    page of the releases of MetaFOCUS (RELEASES_PAGE_URL), otherwise the
    page of the releases."""
    pageUrl = str(releaseData.get("html_url") or "")
    return pageUrl if pageUrl.startswith(RELEASES_PAGE_URL + "/") else RELEASES_PAGE_URL


def ChangelogHtmlOfLoader(loaderSource):
    """The HTML of the changelog of a loader MetaFOCUS.py, read from its
    source *loaderSource* without running it: the longest string of the
    source that starts with CHANGELOG_HEADING, adjacent strings joined as
    Python joins them, or None without one."""
    candidates = [node.value for node in ast.walk(ast.parse(loaderSource))
                  if isinstance(node, ast.Constant) and isinstance(node.value, str)
                  and node.value.startswith(CHANGELOG_HEADING)]
    return max(candidates, key=len) if candidates else None


def ChangelogEntriesSince(changelogHtml, installedVersion):
    """The entries of the changelog *changelogHtml* (CHANGELOG_ENTRY_PATTERN)
    of the versions newer than *installedVersion*, in the order of the
    changelog, newest first, as HTML, or an empty text without one."""
    installed = ParseVersion(installedVersion)
    if installed is None:
        return ""
    entries = []
    for match in CHANGELOG_ENTRY_PATTERN.finditer(changelogHtml or ""):
        entryVersion = ParseVersion(match.group(1))
        if entryVersion is not None and entryVersion > installed:
            entries.append(match.group(0))
    return "".join(entries)


def RequiredModulesOfLoader(loaderSource):
    """The modules that the loader *loaderSource* requires of its package
    (its _REQUIRED_CORE_MODULES), read without running it, or None
    without such a list of texts."""
    for node in ast.parse(loaderSource).body:
        if isinstance(node, ast.Assign) and any(getattr(target, "id", None) == "_REQUIRED_CORE_MODULES"
                                                for target in node.targets):
            try:
                value = ast.literal_eval(node.value)
            except ValueError:
                return None
            if isinstance(value, (tuple, list)) and value and all(isinstance(item, str) for item in value):
                return tuple(value)
    return None


def PackageVersion(initSource):
    """The version of the toolbox in the source *initSource* of the
    __init__.py of its package (its line _METAFOCUS_VERSION = "..."), or
    None."""
    match = re.search(r'^_METAFOCUS_VERSION = "([^"]+)"', initSource, re.M)
    return match.group(1) if match else None


def ImportedPackageModules(source, isPackageModule):
    """The file names of the modules of the package MetaFOCUSCore that the
    Python source *source* imports, read without running it: from
    MetaFOCUSCore import x, from MetaFOCUSCore.x import ..., and import
    MetaFOCUSCore.x, and in a module of the package (*isPackageModule*)
    also from . import x and from .x import ....  A name that starts with
    an underscore, such as _METAFOCUS_VERSION, is a name of __init__.py,
    not a module."""
    moduleNames = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            moduleNames.update(alias.name.split(".")[1] for alias in node.names
                               if alias.name.startswith(PACKAGE_NAME + "."))
        elif isinstance(node, ast.ImportFrom):
            importsNames = ((node.level == 0 and node.module == PACKAGE_NAME)
                            or (isPackageModule and node.level == 1 and node.module is None))
            if importsNames:
                moduleNames.update(alias.name for alias in node.names if not alias.name.startswith("_"))
            elif node.level == 0 and str(node.module).startswith(PACKAGE_NAME + "."):
                moduleNames.add(node.module.split(".")[1])
            elif isPackageModule and node.level == 1:
                moduleNames.add(node.module.split(".")[0])
    return {moduleName + ".py" for moduleName in moduleNames}


# ===================================================================
# The ZIP file of a release
# ===================================================================

def PackageMembers(packageFile):
    """The members of the ZIP file *packageFile* (zipfile.ZipFile), after
    the checks that make it safe to unpack: no name that is absolute,
    starts with a drive, or holds .., no symbolic link, and at most
    LARGEST_UNPACKED_BYTES unpacked.  Raises ValueError otherwise."""
    members = packageFile.infolist()
    for member in members:
        nameParts = member.filename.replace("\\", "/").split("/")
        if member.filename.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", member.filename) or ".." in nameParts:
            raise ValueError("the ZIP file holds the name {}, which leads outside its folder".format(member.filename))
        if (member.external_attr >> 16) & 0o170000 == 0o120000:
            raise ValueError("the ZIP file holds the symbolic link {}".format(member.filename))
    if sum(member.file_size for member in members) > LARGEST_UNPACKED_BYTES:
        raise ValueError("the ZIP file unpacks to more than {:,} bytes".format(LARGEST_UNPACKED_BYTES))
    return members


def LoaderMemberName(memberNames):
    """The name of the loader MetaFOCUS.py among the names of the members of
    a ZIP file, *memberNames*: at the top of the file or in one folder at
    its top (MetaFOCUS/MetaFOCUS.py, as in the ZIP file of a release), next
    to the folder MetaFOCUSCore, or None.  Raises ValueError for a ZIP file
    with more than one, which leaves open which one is the toolbox."""
    loaderNames = []
    for memberName in memberNames:
        nameParts = memberName.split("/")
        if nameParts[-1] == "MetaFOCUS.py" and len(nameParts) <= 2:
            folderPrefix = memberName[:-len("MetaFOCUS.py")]
            if any(otherName.startswith(folderPrefix + PACKAGE_NAME + "/") for otherName in memberNames):
                loaderNames.append(memberName)
    if len(loaderNames) > 1:
        raise ValueError("the ZIP file holds more than one MetaFOCUS.py next to a folder MetaFOCUSCore: {}".format(
            ", ".join(sorted(loaderNames))))
    return loaderNames[0] if loaderNames else None


def CheckReleasePackage(packageFile, versionText):
    """Check the ZIP file of the release of version *versionText*,
    *packageFile* (zipfile.ZipFile), before any of it is offered or
    installed: its members can be unpacked safely (PackageMembers), it
    holds one MetaFOCUS.py next to a folder MetaFOCUSCore
    (LoaderMemberName), its package has this version (PackageVersion), its
    loader lists its modules (RequiredModulesOfLoader), and every module
    of the package that the loader or a module imports
    (ImportedPackageModules) is a file of the package and in that list.
    The installer requires the listed modules, so a release cannot lack a
    module that it needs, even one missing from its list.  Returns (the
    name of the loader, its source, the listed modules), or raises
    ValueError."""
    memberNames = [member.filename for member in PackageMembers(packageFile)]
    loaderName = LoaderMemberName(memberNames)
    if loaderName is None:
        raise ValueError("the ZIP file of the release holds no MetaFOCUS.py next to a folder MetaFOCUSCore")
    corePrefix = loaderName[:-len("MetaFOCUS.py")] + PACKAGE_NAME + "/"
    loaderSource = packageFile.read(loaderName).decode("utf-8")
    initName = corePrefix + "__init__.py"
    packageVersion = PackageVersion(packageFile.read(initName).decode("utf-8") if initName in memberNames else "")
    if packageVersion != versionText:
        raise ValueError("the ZIP file of the release {} holds version {}".format(versionText, packageVersion))
    requiredModules = RequiredModulesOfLoader(loaderSource)
    if not requiredModules:
        raise ValueError("the MetaFOCUS.py of the release {} does not list its modules".format(versionText))
    moduleNames = {memberName[len(corePrefix):] for memberName in memberNames
                   if memberName.startswith(corePrefix) and memberName.endswith(".py")
                   and "/" not in memberName[len(corePrefix):]}
    importedModules = ImportedPackageModules(loaderSource, False)
    for moduleName in sorted(moduleNames):
        importedModules |= ImportedPackageModules(packageFile.read(corePrefix + moduleName).decode("utf-8"), True)
    missingModules = sorted(importedModules - moduleNames)
    if missingModules:
        raise ValueError("the ZIP file of the release {} lacks {}, which its modules import".format(
            versionText, ", ".join(missingModules)))
    unlistedModules = sorted(importedModules - set(requiredModules))
    if unlistedModules:
        raise ValueError("the MetaFOCUS.py of the release {} does not list {}, which its modules import".format(
            versionText, ", ".join(unlistedModules)))
    return loaderName, loaderSource, requiredModules


def UnpackPackage(packageFile, folder):
    """Unpack the members of the ZIP file *packageFile* (zipfile.ZipFile,
    PackageMembers) into *folder*, file by file, and return the path of its
    MetaFOCUS.py (LoaderMemberName).  Raises ValueError for a ZIP file
    without one, or with a member that would land outside *folder*."""
    folderPrefix = os.path.join(os.path.realpath(folder), "")
    members = PackageMembers(packageFile)
    loaderName = LoaderMemberName([member.filename for member in members])
    if loaderName is None:
        raise ValueError("the ZIP file holds no MetaFOCUS.py next to a folder MetaFOCUSCore")
    for member in members:
        targetPath = os.path.join(folder, *member.filename.split("/"))
        if not os.path.realpath(targetPath).startswith(folderPrefix):
            raise ValueError("the ZIP file holds the name {}, which leads outside its folder".format(member.filename))
        if member.is_dir():
            os.makedirs(targetPath, exist_ok=True)
            continue
        os.makedirs(os.path.dirname(targetPath), exist_ok=True)
        with packageFile.open(member) as sourceFile, open(targetPath, "wb") as targetFile:
            shutil.copyfileobj(sourceFile, targetFile)
    return os.path.join(folder, *loaderName.split("/"))


def RemoveFolderQuietly(folder):
    """Remove the folder *folder* with its content, when there is one (a
    failure is reported once in the Console)."""
    if not folder or not os.path.isdir(folder):
        return
    try:
        shutil.rmtree(folder)
    except OSError as suppressedError:
        ReportSuppressedError("updateCheck.RemoveFolderQuietly", suppressedError)


def RemoveStaleTemporaryFolders(now):
    """Remove the folders of downloads and of unpacked files of earlier
    checks (DOWNLOAD_FOLDER_PREFIX and UNPACKED_FOLDER_PREFIX) from the
    temporary folder of the system when they are older than
    STALE_FOLDER_AGE_SECONDS at *now* (time.time()), as a check leaves its
    folder when Metashape is closed before its result is shown.  A
    temporary folder that cannot be found or read is reported once in the
    Console, and the check then fails as it makes its own folder
    (StartCheck)."""
    try:
        temporaryFolder = tempfile.gettempdir()
        entryNames = os.listdir(temporaryFolder)
    except OSError as suppressedError:
        ReportSuppressedError("updateCheck.RemoveStaleTemporaryFolders.list", suppressedError)
        return
    for entryName in entryNames:
        if not entryName.startswith((DOWNLOAD_FOLDER_PREFIX, UNPACKED_FOLDER_PREFIX)):
            continue
        folder = os.path.join(temporaryFolder, entryName)
        try:
            isStale = (os.path.isdir(folder) and not os.path.islink(folder)
                       and now - os.path.getmtime(folder) > STALE_FOLDER_AGE_SECONDS)
        except OSError as suppressedError:
            ReportSuppressedError("updateCheck.RemoveStaleTemporaryFolders.age", suppressedError)
            continue
        if isStale:
            RemoveFolderQuietly(folder)


# ===================================================================
# The check
# ===================================================================

def ReadUrl(url, openUrl, largestByteCount):
    """The content of *url*, read with *openUrl* (urllib.request.urlopen,
    or a stand-in), with MetaFOCUS as the user agent, which the GitHub API
    requires.  Raises ValueError for content above *largestByteCount*
    bytes, and passes on the errors of the network."""
    request = urllib.request.Request(url, headers={"User-Agent": "MetaFOCUS/" + _METAFOCUS_VERSION,
                                                   "Accept": "application/vnd.github+json, */*"})
    with openUrl(request, timeout=NETWORK_TIMEOUT_SECONDS) as response:
        content = response.read(largestByteCount + 1)
    if len(content) > largestByteCount:
        raise ValueError("{} gave more than {:,} bytes".format(url, largestByteCount))
    return content


def DownloadPackage(downloadUrl, expectedChecksum, folder, openUrl):
    """Download the ZIP file of a release from *downloadUrl* into *folder*
    and return its path, after a check of its SHA-256 checksum against
    *expectedChecksum* (ReleaseAsset).  Raises ValueError when they differ,
    and nothing is written then."""
    content = ReadUrl(downloadUrl, openUrl, LARGEST_PACKAGE_BYTES)
    checksum = hashlib.sha256(content).hexdigest()
    if checksum != expectedChecksum:
        raise ValueError("the SHA-256 checksum of the downloaded ZIP file, {}, differs from that of the release, "
                         "{}".format(checksum, expectedChecksum))
    packagePath = os.path.join(folder, "MetaFOCUS_update.zip")
    with open(packagePath, "wb") as packageFile:
        packageFile.write(content)
    return packagePath


def FailedResult(installedVersion, errorText):
    """The result of a check that failed (STATUS_FAILED, see
    CheckForUpdate), with the version in use *installedVersion* and the
    reason *errorText*."""
    return {"status": STATUS_FAILED, "installedVersion": installedVersion, "latestVersion": None,
            "releasePageUrl": RELEASES_PAGE_URL, "releaseDate": "", "packagePath": None, "packageChecksum": None,
            "changesHtml": "", "error": errorText}


def CheckForUpdate(installedVersion, openUrl, downloadFolder, announcedVersion=None):
    """Check GitHub for a release of MetaFOCUS newer than *installedVersion*,
    the version in use, with *openUrl* (urllib.request.urlopen, or a
    stand-in).  Returns a dictionary with "status" (STATUS_AVAILABLE,
    STATUS_CURRENT, STATUS_NEWER, STATUS_ANNOUNCED, or STATUS_FAILED),
    "installedVersion", "latestVersion", "releasePageUrl"
    (ReleasePageUrl), and "releaseDate".  For a newer release it holds
    "packagePath", its ZIP file in *downloadFolder* with a verified
    checksum (DownloadPackage), "packageChecksum", that checksum, which
    Install checks again (InstallPackage), and "changesHtml", the entries
    of its changelog since the version in use.  For a failure it holds
    "error".  The ZIP file must be the file of the release named after its
    version (ReleaseAsset), come from the MetaFOCUS repository
    (RELEASE_DOWNLOAD_URL_START), and pass CheckReleasePackage.  A newer
    release that equals *announcedVersion*, the version that an automatic
    check showed already, gives STATUS_ANNOUNCED without a download."""
    result = FailedResult(installedVersion, "")
    try:
        releaseData = json.loads(ReadUrl(LATEST_RELEASE_URL, openUrl, LARGEST_RELEASE_DATA_BYTES).decode("utf-8"))
        latestVersionText = str(releaseData.get("tag_name", "")).strip().lstrip("vV")
        latestVersion, installed = ParseVersion(latestVersionText), ParseVersion(installedVersion)
        if latestVersion is None or installed is None:
            raise ValueError("the version of the latest release, {}, or the version in use, {}, cannot be "
                             "read".format(releaseData.get("tag_name"), installedVersion))
        result["latestVersion"] = latestVersionText
        result["releasePageUrl"] = ReleasePageUrl(releaseData)
        result["releaseDate"] = str(releaseData.get("published_at") or "")[:10]
        if latestVersion == installed:
            result["status"] = STATUS_CURRENT
            return result
        if latestVersion < installed:
            result["status"] = STATUS_NEWER
            return result
        if announcedVersion is not None and latestVersionText == announcedVersion:
            result["status"] = STATUS_ANNOUNCED
            return result
        asset = ReleaseAsset(releaseData, latestVersionText)
        if asset is None or not asset[0] or asset[1] is None:
            raise ValueError("the release {} gives no single ZIP file {} with a checksum".format(
                latestVersionText, ReleaseAssetName(latestVersionText)))
        if not str(asset[0]).startswith(RELEASE_DOWNLOAD_URL_START):
            raise ValueError("the ZIP file of the release {} is not a file of the MetaFOCUS repository on GitHub: "
                             "{}".format(latestVersionText, asset[0]))
        packagePath = DownloadPackage(asset[0], asset[1], downloadFolder, openUrl)
        with zipfile.ZipFile(packagePath) as packageFile:
            loaderName, loaderSource, requiredModules = CheckReleasePackage(packageFile, latestVersionText)
        result["changesHtml"] = ChangelogEntriesSince(ChangelogHtmlOfLoader(loaderSource), installedVersion)
        result["packagePath"] = packagePath
        result["packageChecksum"] = asset[1]
        result["status"] = STATUS_AVAILABLE
    except Exception as checkError:
        result["status"] = STATUS_FAILED
        result["error"] = DescribeException(checkError)
    return result


def InstallPackage(result, installFunction, scriptsDirectory):
    """Install the ZIP file of a release that a check found and verified
    (*result* of CheckForUpdate, STATUS_AVAILABLE).  The file is read
    again, and its SHA-256 checksum must still be that of the release
    ("packageChecksum") and its package must pass CheckReleasePackage
    again.  The checked bytes are unpacked into a new folder
    (UnpackPackage), and its MetaFOCUS.py and its folder MetaFOCUSCore are
    installed into *scriptsDirectory* with *installFunction*
    (_InstallToolboxIntoDirectory of the loader), which requires every
    module that the new loader lists and restores the previous
    installation when a step fails.  Returns (True, scriptsDirectory) or
    (False, error text), the text of the installer when it failed.  The
    unpacked folder is removed afterwards."""
    unpackFolder = None
    try:
        with open(result["packagePath"], "rb") as packageFile:
            content = packageFile.read(LARGEST_PACKAGE_BYTES + 1)
        if hashlib.sha256(content).hexdigest() != result["packageChecksum"]:
            return False, ("The SHA-256 checksum of the downloaded ZIP file is no longer that of the release, so "
                           "nothing was installed.")
        with zipfile.ZipFile(io.BytesIO(content)) as packageFile:
            loaderName, loaderSource, requiredModules = CheckReleasePackage(packageFile, result["latestVersion"])
            try:
                unpackFolder = tempfile.mkdtemp(prefix=UNPACKED_FOLDER_PREFIX)
            except OSError as folderError:
                return False, "No temporary folder could be made for the unpacked files: {}".format(
                    DescribeException(folderError))
            loaderPath = UnpackPackage(packageFile, unpackFolder)
        return installFunction(loaderPath, os.path.join(os.path.dirname(loaderPath), PACKAGE_NAME),
                               scriptsDirectory, requiredModules)
    except Exception as installError:
        return False, DescribeException(installError)
    finally:
        RemoveFolderQuietly(unpackFolder)


def LastCheckTime(lastCheckText):
    """The time of the last automatic check, *lastCheckText* (ISO 8601), as
    a local time without a time zone (datetime.datetime), or None when it
    cannot be read.  A time with a time zone is converted to local time."""
    try:
        lastCheck = datetime.datetime.fromisoformat(str(lastCheckText))
    except ValueError:
        return None
    if lastCheck.tzinfo is not None:
        lastCheck = lastCheck.astimezone().replace(tzinfo=None)
    return lastCheck


def IsAutomaticCheckDue(lastCheckText, now):
    """True when no automatic check ran in the AUTOMATIC_CHECK_INTERVAL_SECONDS
    before *now* (datetime.datetime, local time): never, or at a time that
    cannot be read (*lastCheckText*, LastCheckTime).  A time after *now*,
    as after a clock was set back, is not due: StartAutomaticCheck sets it
    to now, so that the automatic check runs at most once a day."""
    lastCheck = LastCheckTime(lastCheckText)
    if lastCheck is None:
        return True
    return (now - lastCheck).total_seconds() >= AUTOMATIC_CHECK_INTERVAL_SECONDS


def ResultConsoleLine(result):
    """The Console line of the result of a check (CheckForUpdate)."""
    status = result["status"]
    if status == STATUS_AVAILABLE:
        return "MetaFOCUS: version {} is available (in use: {}).".format(result["latestVersion"],
                                                                        result["installedVersion"])
    if status == STATUS_ANNOUNCED:
        return ("MetaFOCUS: version {} is available (in use: {}), as shown before. MetaFOCUS > Check for Updates "
                "shows it again, with its changes and Install.".format(result["latestVersion"],
                                                                         result["installedVersion"]))
    if status == STATUS_CURRENT:
        return "MetaFOCUS: version {} is the latest version.".format(result["installedVersion"])
    if status == STATUS_NEWER:
        return "MetaFOCUS: version {} is newer than the latest release, {}.".format(result["installedVersion"],
                                                                                  result["latestVersion"])
    return "MetaFOCUS: the check for a new version failed: {}".format(result["error"])


def ResultHtml(result):
    """The rich text of the window of the result of a check
    (UpdateDialog): what the check found and, for a newer release, the
    changes since the version in use.  A newer release whose ZIP file was
    not downloaded (no "packagePath") is named with its page only, so
    that the window never claims a download or changes that it does not
    have."""
    status = result["status"]
    if status in (STATUS_AVAILABLE, STATUS_ANNOUNCED):
        text = "<p><b>MetaFOCUS {} is available</b>{}. The version in use is {}.</p>".format(
            result["latestVersion"], " (released on {})".format(result["releaseDate"])
            if result["releaseDate"] else "", result["installedVersion"])
        if not result["packagePath"]:
            return text + "<p>Its ZIP file was not downloaded. The page of the release offers it.</p>"
        text += ("<p>Install replaces the installed MetaFOCUS by this version, whose ZIP file is downloaded and "
                 "verified already. Metashape must then be restarted.</p>")
        if result["changesHtml"]:
            text += "<h3>Changes since version {}</h3>{}".format(result["installedVersion"], result["changesHtml"])
        else:
            text += "<p>The changelog of the new version gives no entries since version {}.</p>".format(
                result["installedVersion"])
        return text
    if status == STATUS_CURRENT:
        return "<p>MetaFOCUS {} is the latest version.</p>".format(result["installedVersion"])
    if status == STATUS_NEWER:
        return "<p>The version in use, {}, is newer than the latest release on GitHub, {}.</p>".format(
            result["installedVersion"], result["latestVersion"])
    return ("<p>The check for a new version failed: {}</p><p>The page of the releases on GitHub offers every "
            "release.</p>").format(result["error"].replace("&", "&amp;").replace("<", "&lt;"))


# ===================================================================
# Settings, the background check, and the automatic check
# ===================================================================

def Settings():
    """The settings of MetaFOCUS (QSettings)."""
    return QtCore.QSettings(SETTINGS_ORGANISATION, SETTINGS_APPLICATION)


def SettingText(settings, key):
    """The value of the setting *key* as text, empty without one."""
    value = settings.value(key)
    return "" if value is None else str(value)


def InstalledVersion(settings):
    """The version of the toolbox in use, which a load for this session
    only can make another version than the installed one, or the version
    of the setting TEST_INSTALLED_VERSION_SETTING in its place, which the
    Console names."""
    testVersion = SettingText(settings, TEST_INSTALLED_VERSION_SETTING)
    if testVersion:
        print("MetaFOCUS: the check takes the version in use as {}, the setting {}.".format(
            testVersion, TEST_INSTALLED_VERSION_SETTING))
        return testVersion
    return _METAFOCUS_VERSION


def RunCheck(installedVersion, announcedVersion, downloadFolder):
    """The background thread of a check: CheckForUpdate with
    urllib.request.urlopen, whose result it keeps for PollCheck."""
    _RUNNING_CHECK["result"] = CheckForUpdate(installedVersion, urllib.request.urlopen, downloadFolder,
                                              announcedVersion)


def IsCheckPending():
    """True while a check runs or its result waits for the timer that shows
    it (PollCheck): its thread is alive, or it has ended and the timer is
    still active."""
    checkThread = _RUNNING_CHECK["thread"]
    if checkThread is None:
        return False
    pollTimer = _RUNNING_CHECK["timer"]
    return checkThread.is_alive() or (pollTimer is not None and pollTimer.isActive())


def StartCheck(showAlways):
    """Start a check for a new version in a background thread (RunCheck),
    unless a check is pending (IsCheckPending), whose result is then shown
    in any case when *showAlways*.  A check from the menu shows its result
    in any case, an automatic check only a new version that it did not
    show before.  A timer of the main thread waits for the result
    (PollCheck).  The folder of a check whose result can no longer be
    shown, and folders that earlier checks left behind
    (RemoveStaleTemporaryFolders), are removed first.  When no temporary
    folder can be made for the download, the check fails at once
    (ShowCheckResult)."""
    if IsCheckPending():
        _RUNNING_CHECK["showAlways"] = _RUNNING_CHECK["showAlways"] or showAlways
        return
    if _RUNNING_CHECK["thread"] is not None:
        RemoveFolderQuietly(_RUNNING_CHECK["downloadFolder"])
        _RUNNING_CHECK.update(thread=None, result=None, downloadFolder=None)
    settings = Settings()
    installedVersion = InstalledVersion(settings)
    announcedVersion = None if showAlways else (SettingText(settings, ANNOUNCED_VERSION_SETTING) or None)
    RemoveStaleTemporaryFolders(time.time())
    try:
        downloadFolder = tempfile.mkdtemp(prefix=DOWNLOAD_FOLDER_PREFIX)
    except OSError as folderError:
        ShowCheckResult(FailedResult(installedVersion, "no temporary folder could be made for the download: {}".format(
            DescribeException(folderError))), showAlways, None)
        return
    checkThread = threading.Thread(target=RunCheck, args=(installedVersion, announcedVersion, downloadFolder),
                                   daemon=True)
    _RUNNING_CHECK.update(thread=checkThread, showAlways=showAlways, result=None, downloadFolder=downloadFolder)
    checkThread.start()
    pollTimer = QtCore.QTimer()
    pollTimer.setInterval(POLL_INTERVAL_MILLISECONDS)
    pollTimer.timeout.connect(PollCheck)
    _RUNNING_CHECK["timer"] = pollTimer
    pollTimer.start()


def PollCheck():
    """Show the result of the check once its thread has ended
    (ShowCheckResult)."""
    checkThread = _RUNNING_CHECK["thread"]
    if checkThread is not None and checkThread.is_alive():
        return
    if _RUNNING_CHECK["timer"] is not None:
        _RUNNING_CHECK["timer"].stop()
    _RUNNING_CHECK["thread"] = None
    result = _RUNNING_CHECK["result"] or FailedResult(_METAFOCUS_VERSION, "the check ended without a result")
    ShowCheckResult(result, _RUNNING_CHECK["showAlways"], _RUNNING_CHECK["downloadFolder"])


def ShowCheckResult(result, showAlways, downloadFolder):
    """Name the result of a check in the Console (ResultConsoleLine) and
    show it in its window (UpdateDialog): a check from the menu
    (*showAlways*) in any case, an automatic check only a new version,
    which it records as shown.  The folder of the download is removed
    afterwards.  A check from the menu that joined an automatic check
    that found a version shown before, and so downloaded nothing
    (STATUS_ANNOUNCED), starts a check of its own instead, which
    downloads and verifies the ZIP file."""
    if showAlways and result["status"] == STATUS_ANNOUNCED:
        RemoveFolderQuietly(downloadFolder)
        StartCheck(showAlways=True)
        return
    print(ResultConsoleLine(result))
    try:
        if showAlways or result["status"] == STATUS_AVAILABLE:
            if not showAlways:
                Settings().setValue(ANNOUNCED_VERSION_SETTING, result["latestVersion"])
            UpdateDialog(result, QtWidgets.QApplication.activeWindow()).exec_()
    finally:
        RemoveFolderQuietly(downloadFolder)


def CheckForUpdatesFromMenu():
    """MetaFOCUS > Check for Updates: a check whose result is shown in any
    case."""
    StartCheck(showAlways=True)


def StartAutomaticCheck(installFunction, scriptsDirectory):
    """At the load of MetaFOCUS: keep the installer of the loader and its
    scripts directory for Install, then ask once whether MetaFOCUS may
    check automatically (AskAboutAutomaticCheck, after
    QUESTION_DELAY_MILLISECONDS), or start the automatic check when it is
    allowed and due (IsAutomaticCheckDue).  A time of the last check after
    now, as after a clock was set back, is set to now without a check."""
    _INSTALLER.update(installFunction=installFunction, scriptsDirectory=scriptsDirectory)
    settings = Settings()
    automaticSetting = SettingText(settings, AUTOMATIC_SETTING)
    if not automaticSetting:
        QtCore.QTimer.singleShot(QUESTION_DELAY_MILLISECONDS, AskAboutAutomaticCheck)
        return
    if automaticSetting != "yes":
        return
    now = datetime.datetime.now()
    lastCheckText = SettingText(settings, LAST_CHECK_SETTING)
    lastCheck = LastCheckTime(lastCheckText)
    if lastCheck is not None and lastCheck > now:
        settings.setValue(LAST_CHECK_SETTING, now.isoformat(timespec="seconds"))
        return
    if IsAutomaticCheckDue(lastCheckText, now):
        settings.setValue(LAST_CHECK_SETTING, now.isoformat(timespec="seconds"))
        StartCheck(showAlways=False)


def AskAboutAutomaticCheck():
    """Ask once whether MetaFOCUS may check for a new version when it starts
    (AUTOMATIC_CHECK_QUESTION), keep the answer, and check now when it is
    yes.  The question is asked once per session
    (_AUTOMATIC_CHECK_QUESTION), also when a second load of MetaFOCUS
    schedules it again while the first question is still open."""
    settings = Settings()
    if SettingText(settings, AUTOMATIC_SETTING) or _AUTOMATIC_CHECK_QUESTION["isAsked"]:
        return
    _AUTOMATIC_CHECK_QUESTION["isAsked"] = True
    reply = QtWidgets.QMessageBox.question(
        QtWidgets.QApplication.activeWindow(), _TOOL_NAME, AUTOMATIC_CHECK_QUESTION,
        QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No, QtWidgets.QMessageBox.Yes)
    isAllowed = reply == QtWidgets.QMessageBox.Yes
    settings.setValue(AUTOMATIC_SETTING, "yes" if isAllowed else "no")
    print("MetaFOCUS: the automatic check for a new version is {}. MetaFOCUS > Check for Updates changes this "
          "choice.".format("on" if isAllowed else "off"))
    if isAllowed:
        settings.setValue(LAST_CHECK_SETTING, datetime.datetime.now().isoformat(timespec="seconds"))
        StartCheck(showAlways=False)


# ===================================================================
# The window of the result
# ===================================================================

class UpdateDialog(QtWidgets.QDialog):
    """The result of a check (CheckForUpdate): what it found, the changes
    since the version in use, the box of the automatic check, and for a
    new version Install, Release page, and Later."""

    def __init__(self, result, parent=None):
        super().__init__(parent)
        self.result = result
        self.setWindowTitle(_TOOL_NAME)
        self.setWindowFlags(self.windowFlags() & ~QtCore.Qt.WindowContextHelpButtonHint)
        layout = QtWidgets.QVBoxLayout(self)
        textBrowser = QtWidgets.QTextBrowser()
        textBrowser.setOpenExternalLinks(False)
        textBrowser.setHtml(ResultHtml(result))
        layout.addWidget(textBrowser)
        self.automaticCheckBox = QtWidgets.QCheckBox(AUTOMATIC_CHECK_BOX_TEXT)
        self.automaticCheckBox.setChecked(SettingText(Settings(), AUTOMATIC_SETTING) == "yes")
        self.automaticCheckBox.toggled.connect(self._OnAutomaticToggled)
        layout.addWidget(self.automaticCheckBox)
        buttonLayout = QtWidgets.QHBoxLayout()
        isNewVersion = result["status"] in (STATUS_AVAILABLE, STATUS_ANNOUNCED) and result["packagePath"]
        if isNewVersion:
            installButton = QtWidgets.QPushButton("Install")
            installButton.clicked.connect(self._OnInstall)
            buttonLayout.addWidget(installButton)
        releasePageButton = QtWidgets.QPushButton("Release page")
        releasePageButton.clicked.connect(self._OnReleasePage)
        buttonLayout.addWidget(releasePageButton)
        buttonLayout.addStretch()
        closeButton = QtWidgets.QPushButton("Later" if isNewVersion else "Close")
        closeButton.clicked.connect(self.reject)
        buttonLayout.addWidget(closeButton)
        layout.addLayout(buttonLayout)
        self.resize(560, 480 if result["changesHtml"] else 260)

    def _OnAutomaticToggled(self, isChecked):
        Settings().setValue(AUTOMATIC_SETTING, "yes" if isChecked else "no")
        print("MetaFOCUS: the automatic check for a new version is {}.".format("on" if isChecked else "off"))

    def _OnReleasePage(self):
        QtGui.QDesktopServices.openUrl(QtCore.QUrl(self.result["releasePageUrl"]))

    def _OnInstall(self):
        installFunction = _INSTALLER["installFunction"]
        if installFunction is None:
            QtWidgets.QMessageBox.warning(self, _TOOL_NAME, "The installer of the loader is not available, so the "
                                          "page of the release offers the ZIP file to install by hand.")
            return
        success, detail = InstallPackage(self.result, installFunction, _INSTALLER["scriptsDirectory"])
        if not success:
            # The text of the installer says what happened to the previous
            # installation (_InstallToolboxIntoDirectory of the loader).
            print("MetaFOCUS: the installation of version {} failed: {}".format(
                self.result["latestVersion"], " ".join(detail.split())))
            QtWidgets.QMessageBox.warning(
                self, _TOOL_NAME, "The installation failed:\n\n{}\n\nThe page of the release offers the ZIP file to "
                "install by hand.".format(detail))
            return
        print("MetaFOCUS: version {} is installed in {}. Restart Metashape to use it.".format(
            self.result["latestVersion"], detail))
        QtWidgets.QMessageBox.information(
            self, _TOOL_NAME, "MetaFOCUS {} is installed in:\n{}\n\nRestart Metashape to use it.".format(
                self.result["latestVersion"], detail))
        self.accept()
