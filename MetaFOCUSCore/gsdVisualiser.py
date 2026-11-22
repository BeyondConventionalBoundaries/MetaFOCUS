"""
GSD Visualiser
==============

Loads one or more ground sampling distance (GSD) CSVs produced by
the MetaFOCUS GSD Value Exporter and visualises them in a separate
window with one tab per image plus an overview tab covering all
loaded images.

Image tab:
  * the image, surface mode, CRS, unit of the lengths, and number of
    records read from the comment lines of the CSV, with the number of
    rows read where it differs (InformationLineText)
  * summary statistics (count, minimum, 2nd percentile, mean, median,
    98th percentile, maximum, standard deviation) of the GSD, in the units of the
    file: the area-equivalent GSD (GSD_areaEquivalent) or, in a file
    without that column, the geometric mean of the row and column
    sampling distances (GSD_mean or GSD_mean_m, then
    GSD_geometricMean), which the tab then states
  * a histogram of the GSD distribution
  * a binned heat map in which a chosen value (GSD, incidence
    angle, or slant distance) is averaged into a grid and colour
    coded, either across the image (pixel column and row) or across
    the surface (CRS X and Y).

Overview tab (all loaded images):
  * a table of per-image GSD statistics
  * a bar chart of each image's mean GSD with its minimum to maximum
    range
  * a combined histogram of the GSD across all loaded images
  * a warning when the loaded files differ in units (every wording of
    the Units line counts as metres or chunk units), in surface mode, or
    in the definition of the GSD, and when files in chunk units come
    from different chunks, whose chunk units are not comparable.

Every axis of the charts is named, with its unit where the file gives
one (ColumnUnitSymbol), and the title of the heat map names its colour
column with the unit of its values.  The GSD decimals box of the window shows every GSD
value (the statistics, the overview, and the labels of the charts) with
automatic decimals or with 1 to 8 decimals (GSD_DECIMAL_CHOICES).

Each export covers one image, so each CSV becomes one image tab.
The expected columns are PixelColumn, PixelRow, CRS_X, CRS_Y,
CRS_Z, SlantDistance, CosineIncidence, Theta_deg, Phi_deg, and the GSD
columns GSD_row, GSD_column, GSD_geometricMean, and GSD_areaEquivalent,
preceded by # comment lines, whose Units line gives the unit of the
lengths.  Files with GSD_mean, the geometric mean, instead of
GSD_geometricMean and GSD_areaEquivalent, and without Phi_deg, or with
the length columns SlantDistance_m, GSD_row_m, GSD_column_m, and
GSD_mean_m (or GSD_m), also in chunk units, are read as well.  The
comment keys Image and Surface mode, and Camera and GSD mode, are all
read.  Columns are detected by
name.  The delimiter (a comma, a semicolon, or a tab) is detected from
the header row, and in a file with a semicolon or a tab as delimiter a
number with a decimal comma (1,25 or 2,5e-09) is read as well.  The
heat map axes and colour column can be changed by hand,
the GSD column of the statistics and histograms cannot.  Rows whose
GSD value is not a positive finite number are left out of the
statistics and the charts, and the loading names their lines.

Author:     Geert J. Verhoeven (ORCID: 0000-0003-4825-9604)
Contact:    geert.verhoeven@univie.ac.at
Version:    1.0.1 (2026-11-22)
Licence:    GPL-3.0-or-later WITH GPL-3.0-linking-exception
Requires:   Agisoft Metashape Professional 2.x
Developed for: Agisoft Metashape Professional 2.3.2
"""

import csv
import math
import os
import re
import Metashape
from PySide2 import QtWidgets, QtCore, QtGui

from .photogrammetryUtilities import (ConsoleBlockEnd, ConsoleBlockStart, ConsoleLabelledLine, DescribeException,
                                     FormatCountWithNoun, FormatLengthWithDecimals, JoinWithAnd, NormalisedProjectPath,
                                     PrintConsoleLines, ReportSuppressedError)

# The decimals of the GSD statistics in the texts of the visualiser, or
# four significant digits where they give fewer (FormatGsdText).
GSD_TEXT_DECIMALS = 8

# The choices of the GSD decimals box of the window: None for automatic
# decimals (FormatGsdText in the texts, AxisLabelDecimals on the axes),
# otherwise the decimals of every GSD value of the window.
GSD_DECIMAL_CHOICES = (None, 1, 2, 3, 4, 5, 6, 7, 8)
AUTOMATIC_DECIMALS_TEXT = "Automatic"
GSD_DECIMALS_TOOLTIP = (
    "Decimals of the GSD values in the statistics, the overview, and the labels of the charts. Automatic gives "
    "eight decimals in the texts, or four significant digits where these give fewer, and as many decimals on the "
    "axes as the spacing of their labels needs.")

# The units that an axis or the colour bar of the heat map writes after
# the name of a column (ColumnUnitSymbol): degrees for an angle, and px for
# a pixel position.
DEGREE_SIGN = "°"
PIXEL_UNIT_SYMBOL = "px"


# ===================================================================
# Menu metadata
# ===================================================================

_MENU_LABEL = ("MetaFOCUS/Surface-Derived Project Outputs/"
               "GSD Visualiser")
_ICON_NAME = "gsd_visualiser_32.png"


# ===================================================================
# CSV parsing
# ===================================================================

class GsdCsvRecord(dict):
    """One data row of a GSD CSV, its values keyed by column name, with
    the number of its line in the file (lineNumber), so that a message
    can name the row."""

    def __init__(self, lineNumber):
        super().__init__()
        self.lineNumber = lineNumber


# A number with a decimal comma, as the semicolon choice of the GSD Value
# Exporter writes it: exactly one comma and no point, digits on at least
# one side of the comma, and optionally an exponent (1,25 and 2,5e-09).
_DECIMAL_COMMA_NUMBER_PATTERN = re.compile(r"[+-]?(?:\d+,\d*|,\d+)(?:[eE][+-]?\d+)?")


def DecimalCommaNumber(cellText):
    """The number that *cellText* writes with a decimal comma (see
    _DECIMAL_COMMA_NUMBER_PATTERN), or None for any other text."""
    if not _DECIMAL_COMMA_NUMBER_PATTERN.fullmatch(cellText):
        return None
    return float(cellText.replace(",", "."))


def ParseGsdCsv(path):
    """Parse a GSD Value Exporter CSV.

    Returns (metadata, columnNames, rows), the rows being GsdCsvRecord
    objects.  A cell becomes a number only when it is a finite number:
    infinite and NaN cells (inf, nan, -Infinity) keep their text, like
    any other text, so that they never enter the statistics or the
    charts.  The delimiter is that of the header row (_DetectDelimiter).
    In a file with a semicolon or a tab as delimiter, a cell that is a
    number with a decimal comma (exactly one comma and no point,
    optionally with an exponent: 1,25 or 2,5e-09) becomes that number.
    A comma-delimited file has no decimal commas.  Raises ValueError on
    a malformed file.
    """
    with open(path, "r", encoding="utf-8-sig") as handle:
        rawLines = handle.read().splitlines()

    metadata = {}
    headerIndex = None
    for index, line in enumerate(rawLines):
        stripped = line.strip()
        if not stripped:
            continue
        # Semicolon-delimited exports may leave csv-quoted comment
        # lines.  Strip surrounding quotes before the test, so that
        # their comment lines parse as metadata rather than as the
        # header.
        unquoted = stripped.strip('"').strip()
        if unquoted.startswith("#"):
            body = unquoted.lstrip("#").strip()
            if ":" in body:
                key, value = body.split(":", 1)
                metadata[key.strip()] = value.strip()
            continue
        headerIndex = index
        break

    if headerIndex is None:
        raise ValueError("No header row found in the CSV.")

    headerLine = rawLines[headerIndex]
    delimiter = _DetectDelimiter(headerLine)
    columnNames = [name.strip()
                   for name in headerLine.split(delimiter)]

    # The semicolon choice of the GSD Value Exporter writes its numbers
    # with a decimal comma, which a comma-delimited file cannot hold.
    readsDecimalComma = delimiter != ","
    rows = []
    reader = csv.reader(rawLines[headerIndex + 1:], delimiter=delimiter)
    for fields in reader:
        if not fields or (len(fields) == 1 and not fields[0].strip()):
            continue
        if fields[0].strip().strip('"').strip().startswith("#"):
            continue
        # The header is line headerIndex + 1 of the file, and the reader
        # counts the lines after it.
        record = GsdCsvRecord(headerIndex + 1 + reader.line_num)
        for columnIndex, name in enumerate(columnNames):
            value = (fields[columnIndex].strip()
                     if columnIndex < len(fields) else "")
            try:
                number = float(value)
            except (ValueError, TypeError):
                number = DecimalCommaNumber(value) if readsDecimalComma else None
                if number is None:
                    record[name] = value
                    continue
            record[name] = number if math.isfinite(number) else value
        rows.append(record)

    if not rows:
        raise ValueError("No data rows found in the CSV.")
    return metadata, columnNames, rows


def _DetectDelimiter(headerLine):
    bestDelimiter = ","
    bestFieldCount = 0
    for candidate in (",", ";", "\t"):
        fieldCount = len(headerLine.split(candidate))
        if fieldCount > bestFieldCount:
            bestFieldCount = fieldCount
            bestDelimiter = candidate
    return bestDelimiter


def AutodetectColumn(columnNames, keywords, fallbackIndex=None):
    """Name of the column matched by the first keyword (in keyword
    order) that any column name contains, case-insensitively,
    otherwise the column at *fallbackIndex*, otherwise None."""
    lowercaseNamePairs = [(name, name.lower()) for name in columnNames]
    for keyword in keywords:
        for name, lowercaseName in lowercaseNamePairs:
            if keyword in lowercaseName:
                return name
    if fallbackIndex is not None and fallbackIndex < len(columnNames):
        return columnNames[fallbackIndex]
    return None


def AutodetectAxisColumn(columnNames, keywords, excludedSubstrings=()):
    """Name of the column for a heat map axis: as AutodetectColumn, but
    columns whose lower-case name contains one of *excludedSubstrings*
    are never taken, so that a GSD column (GSD_row_m, GSD_column_m)
    never becomes a position axis.  Returns None when nothing matches."""
    candidateNames = [name for name in columnNames
                      if not any(excluded in name.lower()
                                 for excluded in excludedSubstrings)]
    return AutodetectColumn(candidateNames, keywords)


# Definitions of the GSD column of a file (see GsdScalarColumn).
GSD_DEFINITION_AREA_EQUIVALENT = "area-equivalent"
GSD_DEFINITION_GEOMETRIC_MEAN = "geometric mean of the row and column sampling distances"


# The names of the GSD columns that GsdScalarColumn recognises, in lower
# case and in the order in which it prefers them, with their definitions.
_RECOGNISED_GSD_COLUMNS = (("gsd_areaequivalent", GSD_DEFINITION_AREA_EQUIVALENT),
                           ("gsd_mean", GSD_DEFINITION_GEOMETRIC_MEAN),
                           ("gsd_mean_m", GSD_DEFINITION_GEOMETRIC_MEAN),
                           ("gsd_geometricmean", GSD_DEFINITION_GEOMETRIC_MEAN))


def GsdScalarColumn(columnNames):
    """(column name, definition) of the GSD that the statistics, the
    histograms, and the heat map use: the area-equivalent GSD in
    GSD_areaEquivalent (definition GSD_DEFINITION_AREA_EQUIVALENT), or,
    in a file without that column, the geometric mean of the row and
    column sampling distances in GSD_mean or GSD_mean_m (definition
    GSD_DEFINITION_GEOMETRIC_MEAN), and a file with only
    GSD_geometricMean is read as such as well.  These names must match
    as a whole, in any case, so that an added column such as
    GSD_areaEquivalent_uncertainty is not taken for them.  Only without
    them is any other column whose name contains GSD (GSD_m, for example)
    used, with the definition None, which the file does not state.
    (None, None) without a GSD column."""
    for recognisedName, definition in _RECOGNISED_GSD_COLUMNS:
        for columnName in columnNames:
            if columnName.strip().lower() == recognisedName:
                return columnName, definition
    return AutodetectColumn(columnNames, ["gsd"]), None


def GeometricMeanDefinitionNote(columnName):
    """Note of the image tab on a file whose GSD values are geometric
    means of the row and column sampling distances (see
    GsdScalarColumn)."""
    return ("The GSD values of this file (column {}) are geometric means of the row and column sampling distances, "
            "not area-equivalent GSD values.".format(columnName))


def MetadataValue(metadata, keys):
    """Value of the first of *keys* present in the CSV comment metadata,
    or None.  Lets the visualiser read the keys "Image" and "Surface
    mode" and also "Camera" and "GSD mode"."""
    for key in keys:
        if key in metadata:
            return metadata[key]
    return None


def DatasetUnits(metadata):
    """Units of the values of a GSD CSV: the "Units" comment line, or
    metres for a file without one, because only a file of a chunk with a
    physical scale can lack the line."""
    return MetadataValue(metadata, ("Units",)) or "metres"


def DatasetUnitCategory(metadata):
    """Unit category of the lengths of a GSD CSV: "metres" or "chunk
    units", whatever the wording of the Units line ("chunk units (project
    not scaled)" or "chunk units (chunk not scaled) for SlantDistance and
    the GSD columns (...)"), otherwise the text of the
    line up to its first remark."""
    unitsText = DatasetUnits(metadata).strip()
    for category in ("metres", "chunk units"):
        if unitsText == category or unitsText.startswith(category + " "):
            return category
    return unitsText.split(" (")[0]


def DatasetUnitSymbol(metadata):
    """Unit of the values of a GSD CSV as written after a statistic or
    in a column header: "m" for metres, otherwise the unit category of
    DatasetUnitCategory (for example "chunk units")."""
    unitCategory = DatasetUnitCategory(metadata)
    if unitCategory == "metres":
        return "m"
    return unitCategory


def ShortUnitsText(metadata):
    """The unit of the lengths of a GSD CSV as the information line of an
    image tab shows it: the Units comment line without the columns that it
    covers ("metres" or "chunk units (chunk not scaled)" of a line "...
    for SlantDistance and the GSD columns (...)"), or None for a file
    without the line."""
    unitsText = MetadataValue(metadata, ("Units",))
    if unitsText is None:
        return None
    return unitsText.split(" for ", 1)[0].strip()


def RecordCountText(recordsText, readCount):
    """The number of records in the information line of an image tab: the
    Records comment line (*recordsText*, None for a file without it) with
    thousands separators, followed by the number of rows read
    (*readCount*) in brackets where it differs, or the number of rows read
    for a file without the line."""
    if recordsText is None:
        return "{:,}".format(readCount)
    try:
        recordCount = int(recordsText)
    except ValueError:
        return "{} ({:,} read)".format(recordsText, readCount)
    if recordCount == readCount:
        return "{:,}".format(recordCount)
    return "{:,} ({:,} read)".format(recordCount, readCount)


# The parts of the information line of an image tab before the unit: the
# label and the comment keys of current files and of files of earlier
# versions of the GSD Value Exporter.
INFORMATION_LINE_KEYS = (("Image", ("Image", "Camera")), ("Surface mode", ("Surface mode", "GSD mode")),
                         ("Coordinate reference system", ("CRS",)))


def InformationLineText(metadata, readCount):
    """The information line of an image tab: the image, the surface mode,
    and the CRS of the comment lines of the GSD CSV, the unit of its
    lengths (ShortUnitsText), and its number of records (RecordCountText,
    with *readCount* rows read).  A file writes "Image" and "Surface mode",
    or "Camera" and "GSD mode", and both are read.  The unit is shown, so
    that values in chunk units are not taken for metres."""
    parts = []
    for displayKey, metadataKeys in INFORMATION_LINE_KEYS:
        metadataValue = MetadataValue(metadata, metadataKeys)
        if metadataValue is not None:
            parts.append("<b>{}:</b> {}".format(displayKey, metadataValue))
    unitsText = ShortUnitsText(metadata)
    if unitsText is not None:
        parts.append("<b>Units:</b> {}".format(unitsText))
    parts.append("<b>Records:</b> {}".format(RecordCountText(MetadataValue(metadata, ("Records",)), readCount)))
    return "&nbsp;&nbsp;|&nbsp;&nbsp;".join(parts)


def IsGsdColumn(columnName):
    """True for a column of GSD values: a name that contains GSD, in any
    case (GSD_row, GSD_areaEquivalent, GSD_mean_m)."""
    return "gsd" in columnName.lower()


def GsdDecimalsFor(columnName, gsdDecimals):
    """The decimals of the labels of an axis or a colour bar of the column
    *columnName*: the decimals of the GSD decimals box (*gsdDecimals*, None
    for automatic) for a GSD column (IsGsdColumn), and automatic decimals
    (None) for any other column."""
    return gsdDecimals if IsGsdColumn(columnName) else None


def CrsColumnUnitName(columnName, metadata):
    """The unit name of the column *columnName* (CRS_X, CRS_Y, or CRS_Z) in
    the CRS units comment line of a GSD CSV ("CRS_X degree, CRS_Y degree,
    CRS_Z metre"), or None for a file without the line or a column that
    it does not name."""
    unitsText = MetadataValue(metadata, ("CRS units",))
    if not unitsText:
        return None
    for item in unitsText.split(","):
        itemParts = item.strip().split(" ", 1)
        if len(itemParts) == 2 and itemParts[0].lower() == columnName.lower():
            return itemParts[1].strip()
    return None


# Symbols of the unit names of the CRS units line on an axis or the colour
# bar.  Any other unit name, such as US survey foot, is written as it is.
CRS_UNIT_SYMBOLS = {"metre": "m", "degree": DEGREE_SIGN, "chunk unit": "chunk units"}


def ColumnUnitSymbol(columnName, metadata):
    """Unit of the values of the column *columnName* of a GSD CSV, as an
    axis or the colour bar of the heat map writes it: DEGREE_SIGN for an
    angle (a name that ends in _deg), the unit of the lengths
    (DatasetUnitSymbol) for SlantDistance and the GSD columns,
    PIXEL_UNIT_SYMBOL for a pixel position, the unit of the CRS units line
    for CRS_X to CRS_Z (CrsColumnUnitName, CRS_UNIT_SYMBOLS), and None for
    a column whose unit the file does not give (a file without the CRS
    units line, or a unit given as unknown) or that has none
    (CosineIncidence)."""
    lowercaseName = columnName.lower()
    if lowercaseName.endswith("_deg"):
        return DEGREE_SIGN
    if IsGsdColumn(columnName) or lowercaseName.startswith("slantdistance"):
        return DatasetUnitSymbol(metadata)
    if lowercaseName.startswith(("pixel", "px_")):
        return PIXEL_UNIT_SYMBOL
    if lowercaseName in ("crs_x", "crs_y", "crs_z"):
        unitName = CrsColumnUnitName(columnName, metadata)
        if not unitName or unitName == "unknown":
            return None
        return CRS_UNIT_SYMBOLS.get(unitName, unitName)
    return None


def AxisCaption(name, unitSymbol):
    """The caption of an axis: *name*, followed by *unitSymbol* in brackets
    where there is one, for example "Theta_deg (°)"."""
    return "{} ({})".format(name, unitSymbol) if unitSymbol else name


def GsdDecimalsChoiceText(decimals):
    """The text of a choice of the GSD decimals box: AUTOMATIC_DECIMALS_TEXT
    for None, otherwise the number of decimals."""
    return AUTOMATIC_DECIMALS_TEXT if decimals is None else str(decimals)


def CommonUnitSymbol(metadataList):
    """Unit shared by all loaded GSD CSVs (see DatasetUnitSymbol),
    "mixed units" when they differ, or "m" when none is loaded."""
    unitSymbols = sorted({DatasetUnitSymbol(metadata) for metadata in metadataList})
    if len(unitSymbols) > 1:
        return "mixed units"
    return unitSymbols[0] if unitSymbols else "m"


def OverviewTableHeaders(unitSymbol):
    """Column headers of the overview table, each statistic followed by
    *unitSymbol*, for example "Mean (m)"."""
    statisticHeaders = ["{} ({})".format(statisticName, unitSymbol)
                        for statisticName in ("Minimum", "2nd percentile", "Mean", "Median",
                                              "98th percentile", "Maximum", "Standard deviation")]
    return ["Image", "Count"] + statisticHeaders


def DatasetConsistencyWarning(metadataList, gsdDefinitions=()):
    """Warning for the overview when the loaded CSVs differ in their unit
    category (see DatasetUnitCategory), in surface mode, or in the
    definition of their GSD (*gsdDefinitions*, one per file, see
    GsdScalarColumn, None where the file does not state it), so that
    their values are not comparable, and when files in chunk units come
    from different chunks (their "Chunk" comment lines), whose chunk
    units are not comparable either.  An empty string otherwise."""
    unitCategories = sorted({DatasetUnitCategory(metadata) for metadata in metadataList})
    surfaceModes = sorted({MetadataValue(metadata, ("Surface mode", "GSD mode"))
                           for metadata in metadataList} - {None})
    definitionNames = sorted({definition or "not stated" for definition in gsdDefinitions})
    differences = []
    if len(unitCategories) > 1:
        differences.append("units ({})".format(", ".join(unitCategories)))
    if len(surfaceModes) > 1:
        differences.append("surface mode ({})".format(", ".join(surfaceModes)))
    if len(definitionNames) > 1:
        differences.append("the definition of the GSD ({})".format(", ".join(definitionNames)))
    warnings = []
    if differences:
        warnings.append("Warning: the loaded CSVs differ in {}, so the overview pools values that are not "
                        "comparable.".format(JoinWithAnd(differences)))
    warnings.extend(ChunkUnitWarnings([metadata for metadata in metadataList
                                       if DatasetUnitCategory(metadata) == "chunk units"]))
    return " ".join(warnings)


# The Project line of a CSV of a project that was never saved (the
# UNSAVED_PROJECT_TEXT of the GSD Value Exporter).
UNSAVED_PROJECT_TEXT = "(not saved)"


def ChunkIdentity(metadata):
    """(project, chunk key) of a GSD CSV, from its Project and Chunk key
    comment lines, or None when either is missing or the project was
    never saved.  The project path is normalised (NormalisedProjectPath),
    so two spellings of one project file are one project."""
    projectPath = MetadataValue(metadata, ("Project",))
    chunkKey = MetadataValue(metadata, ("Chunk key",))
    if not projectPath or projectPath == UNSAVED_PROJECT_TEXT or chunkKey in (None, ""):
        return None
    return NormalisedProjectPath(projectPath), chunkKey


def ChunkDisplayName(metadata, withProject=False):
    """The chunk of a GSD CSV as the warnings name it: its label, with
    its key when the file records one and, with *withProject*, the path
    of its project."""
    label = MetadataValue(metadata, ("Chunk",)) or "an unknown chunk"
    chunkKey = MetadataValue(metadata, ("Chunk key",))
    projectPath = MetadataValue(metadata, ("Project",)) if withProject else None
    details = [part for part in (("key " + chunkKey) if chunkKey else "", projectPath or "") if part]
    return "{} ({})".format(label, ", ".join(details)) if details else label


def ChunkUnitWarnings(chunkUnitMetadata):
    """Warnings for the files in chunk units of *chunkUnitMetadata*, whose
    values are comparable only when they come from one chunk, because
    every chunk without a physical scale has its own arbitrary unit of
    length.  The chunk of a file is its project and chunk key
    (ChunkIdentity).  Files of different chunks give a warning that names
    them, and when files do not record their chunk (files without the
    Project and Chunk key comment lines, or of an unsaved project), a
    warning says that their chunk cannot be established, unless their
    labels already show different chunks."""
    if len(chunkUnitMetadata) < 2:
        return []
    knownIdentities = {ChunkIdentity(metadata) for metadata in chunkUnitMetadata} - {None}
    unidentifiedCount = sum(1 for metadata in chunkUnitMetadata if ChunkIdentity(metadata) is None)
    labels = {MetadataValue(metadata, ("Chunk",)) or "an unknown chunk" for metadata in chunkUnitMetadata}
    differentChunks = len(knownIdentities) > 1 or (unidentifiedCount > 0 and len(labels) > 1)
    if differentChunks:
        # Chunks of different projects can share the label and the key:
        # then the warning names the projects as well.
        identitiesByName = {}
        for metadata in chunkUnitMetadata:
            identitiesByName.setdefault(ChunkDisplayName(metadata), set()).add(ChunkIdentity(metadata))
        withProject = any(len(identities) > 1 for identities in identitiesByName.values())
        chunkNames = sorted({ChunkDisplayName(metadata, withProject) for metadata in chunkUnitMetadata})
        return ["Warning: the loaded CSVs in chunk units come from different chunks ({}), whose chunk units "
                "are not comparable.".format(", ".join(chunkNames))]
    if unidentifiedCount:
        return ["Warning: {} of the loaded CSVs in chunk units {} no project and chunk key (for example files of "
                "a project that was never saved), so it cannot be established that they come from one chunk, and "
                "the chunk units of different chunks are not comparable.".format(
                    unidentifiedCount, "records" if unidentifiedCount == 1 else "record")]
    return []


def HeatmapBinCounts(xValues, yValues, binTarget):
    """Numbers of heat map cells along x and y.  At most *binTarget*
    along x and about one cell per sample (square root of the sample
    count), the y count following the aspect ratio of the data, and
    never more cells on an axis than distinct positions on it, so that
    a regular sampling grid fills every cell instead of leaving empty
    stripes between the samples."""
    sampleCount = len(xValues)
    xRange = (max(xValues) - min(xValues)) or 1.0
    yRange = (max(yValues) - min(yValues)) or 1.0
    distinctXCount = len(set(xValues))
    distinctYCount = len(set(yValues))
    densityLimit = max(10, int(math.sqrt(sampleCount)))
    binCountX = max(1, min(binTarget, densityLimit, distinctXCount))
    proportionalBinCountY = int(round(binCountX * yRange / xRange))
    binCountY = max(1, min(binTarget,
                           max(min(20, distinctYCount), proportionalBinCountY),
                           distinctYCount))
    return binCountX, binCountY


def HistogramRange(values):
    """(lowest, highest) value of a histogram axis: the data range, or
    for constant data a narrow range centred on the value (one part in
    a million, at least 1e-12), never a fixed unit that would dwarf
    sub-millimetre values."""
    valueMinimum = min(values)
    valueMaximum = max(values)
    if valueMaximum <= valueMinimum:
        padding = max(abs(valueMinimum) * 1e-6, 1e-12)
        return valueMinimum - padding, valueMaximum + padding
    return valueMinimum, valueMaximum


def AxisLabelDecimals(interval, value=None):
    """Decimals for axis and colour-bar labels that are *interval* apart:
    enough to show the interval with two significant digits (between 0
    and 12), so that sub-millimetre values never collapse to the same
    label.  Without a usable interval: four significant digits of
    *value*, or two decimals."""
    if interval is not None and math.isfinite(interval) and interval > 0.0:
        return max(0, min(12, 1 - int(math.floor(math.log10(interval)))))
    if value is not None and math.isfinite(value) and value != 0.0:
        return max(0, min(12, 3 - int(math.floor(math.log10(abs(value))))))
    return 2


def FormatAxisValue(value, interval, decimals=None):
    """Label of *value* on an axis or colour bar whose labels are
    *interval* apart, with *decimals* decimals (the GSD decimals box), or
    for None with as many as AxisLabelDecimals gives.  None and NaN give
    the placeholder "–"."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "–"
    labelDecimals = AxisLabelDecimals(interval, value) if decimals is None else decimals
    return "{:.{}f}".format(value, labelDecimals)


def FormatGsdText(value, decimals=None):
    """A GSD statistic in the texts of the visualiser (the image tab, the
    overview, and the Console) with *decimals* decimals (the GSD decimals
    box), or for None with GSD_TEXT_DECIMALS decimals, or with four
    significant digits where these give fewer (FormatLengthWithDecimals:
    eight decimals would write a GSD of 0.7512 um as 0.00000075)."""
    if decimals is None:
        return FormatLengthWithDecimals(value, GSD_TEXT_DECIMALS)
    return "{:.{}f}".format(value, decimals)


def ImageStatisticsText(unitSymbol, statistics, decimals=None):
    """The statistics line of an image tab: the count, minimum, 2nd
    percentile, mean, median, 98th percentile, maximum, and standard
    deviation of the GSD, in *unitSymbol*, with *decimals* decimals (None
    for automatic, see FormatGsdText)."""
    return ("<b>GSD ({})</b>: count {:,} &nbsp;|&nbsp; "
            "minimum {} &nbsp;|&nbsp; 2nd percentile {} &nbsp;|&nbsp; "
            "mean {} &nbsp;|&nbsp; median {} "
            "&nbsp;|&nbsp; 98th percentile {} &nbsp;|&nbsp; "
            "maximum {} &nbsp;|&nbsp; "
            "standard deviation {}".format(
                unitSymbol, statistics["count"], FormatGsdText(statistics["minimum"], decimals),
                FormatGsdText(statistics["percentile02"], decimals), FormatGsdText(statistics["mean"], decimals),
                FormatGsdText(statistics["median"], decimals), FormatGsdText(statistics["percentile98"], decimals),
                FormatGsdText(statistics["maximum"], decimals),
                FormatGsdText(statistics["standardDeviation"], decimals)))


# ===================================================================
# Statistics
# ===================================================================

def _Percentile(sortedValues, percentile):
    count = len(sortedValues)
    if count == 0:
        return float("nan")
    if count == 1:
        return sortedValues[0]
    position = (percentile / 100.0) * (count - 1)
    lowerIndex = int(math.floor(position))
    upperIndex = min(lowerIndex + 1, count - 1)
    fraction = position - lowerIndex
    return (sortedValues[lowerIndex]
            + fraction
            * (sortedValues[upperIndex] - sortedValues[lowerIndex]))


def ComputeStatistics(values):
    """Summary statistics of the finite values: infinite and NaN entries
    and anything that is not a number are ignored (see _IsNumber)."""
    finiteValues = [value for value in values if _IsNumber(value)]
    if not finiteValues:
        return None
    sortedValues = sorted(finiteValues)
    count = len(sortedValues)
    mean = sum(sortedValues) / count
    variance = (sum((value - mean) ** 2 for value in sortedValues) / count
                if count > 1 else 0.0)
    return {
        "count": count,
        "minimum": sortedValues[0],
        "percentile02": _Percentile(sortedValues, 2),
        "mean": mean,
        "median": _Percentile(sortedValues, 50),
        "percentile98": _Percentile(sortedValues, 98),
        "maximum": sortedValues[-1],
        "standardDeviation": math.sqrt(variance),
    }


# ===================================================================
# Colour ramp (viridis-like, perceptual, low -> high)
# ===================================================================

_RAMP_STOPS = [
    (0.00, (68, 1, 84)),
    (0.25, (59, 82, 139)),
    (0.50, (33, 144, 140)),
    (0.75, (93, 201, 99)),
    (1.00, (253, 231, 37)),
]


def ColorRamp(fraction):
    """Colour of the heat map ramp at *fraction* (0 to 1), interpolated
    linearly between the ramp stops."""
    if fraction <= 0.0:
        return QtGui.QColor(*_RAMP_STOPS[0][1])
    if fraction >= 1.0:
        return QtGui.QColor(*_RAMP_STOPS[-1][1])
    for index in range(len(_RAMP_STOPS) - 1):
        lowerStopPosition, lowerStopColour = _RAMP_STOPS[index]
        upperStopPosition, upperStopColour = _RAMP_STOPS[index + 1]
        if lowerStopPosition <= fraction <= upperStopPosition:
            blendFraction = (fraction - lowerStopPosition) / (upperStopPosition - lowerStopPosition) if upperStopPosition > lowerStopPosition else 0.0
            red = int(lowerStopColour[0] + blendFraction * (upperStopColour[0] - lowerStopColour[0]))
            green = int(lowerStopColour[1] + blendFraction * (upperStopColour[1] - lowerStopColour[1]))
            blue = int(lowerStopColour[2] + blendFraction * (upperStopColour[2] - lowerStopColour[2]))
            return QtGui.QColor(red, green, blue)
    return QtGui.QColor(*_RAMP_STOPS[-1][1])


def _IsNumber(value):
    """True for a finite number: an infinite or NaN value would make the
    ranges, means, and bins of the charts infinite or undefined."""
    return isinstance(value, float) and math.isfinite(value)


def IsValidGsdValue(value):
    """True for a GSD value that can enter the statistics and the
    charts: a finite number above zero, because a sampling distance is
    positive."""
    return _IsNumber(value) and value > 0.0


def RejectedGsdRowsNote(fileName, lineNumbers, listedLineCount=10):
    """Message naming the rows of the GSD CSV *fileName* whose GSD value
    is not a positive finite number, so that they are left out of the
    statistics and the charts, with the numbers of their lines in the
    file (at most *listedLineCount*, then how many more)."""
    listedLines = ", ".join(str(lineNumber) for lineNumber in lineNumbers[:listedLineCount])
    if len(lineNumbers) > listedLineCount:
        # With the serial comma after two or more numbers.
        listedLines += "{}{} more".format(", and " if listedLineCount >= 2 else " and ",
                                          len(lineNumbers) - listedLineCount)
    return ("{}: {} without a valid GSD value (a positive finite number) {} left out of the "
            "statistics and the charts ({} {}).").format(
                fileName, FormatCountWithNoun(len(lineNumbers), "row", "rows"),
                "is" if len(lineNumbers) == 1 else "are",
                "line" if len(lineNumbers) == 1 else "lines", listedLines)


def GsdDatasetValues(columnNames, rows):
    """(GSD column, its definition, chart rows, rejected rows, GSD values,
    statistics) of a loaded GSD CSV: the GSD column and definition of
    GsdScalarColumn, the rows whose GSD value is valid (IsValidGsdValue)
    and those whose value is not, which are left out of the statistics,
    the charts, and the overview, and the statistics of the valid
    values (None without any)."""
    gsdColumn, gsdDefinition = GsdScalarColumn(columnNames)
    if gsdColumn:
        chartRows = [row for row in rows if IsValidGsdValue(row.get(gsdColumn))]
        rejectedRows = [row for row in rows if not IsValidGsdValue(row.get(gsdColumn))]
        gsdValues = [row.get(gsdColumn) for row in chartRows]
    else:
        chartRows = list(rows)
        rejectedRows = []
        gsdValues = []
    return gsdColumn, gsdDefinition, chartRows, rejectedRows, gsdValues, ComputeStatistics(gsdValues)


# ===================================================================
# Console
# ===================================================================

def DatasetConsoleLines(view):
    """The lines of one loaded GSD CSV in the Console: its image, rows,
    surface mode, and the unit of its lengths, the statistics of its GSD,
    and the path of the file.  *view* is a PerImageView (or an object
    with its data attributes and *path*)."""
    surfaceMode = MetadataValue(view.metadata, ("Surface mode", "GSD mode")) or "not stated"
    lines = ["  {}: {}, surface mode {}, lengths in {}".format(
        view.datasetName, FormatCountWithNoun(len(view.rows), "row", "rows"), surfaceMode,
        DatasetUnitCategory(view.metadata))]
    statistics = view.statistics
    if statistics is not None:
        lines.append("    GSD ({}, column {}): count {:,}, minimum {}, mean {}, median {}, "
                     "maximum {}".format(DatasetUnitSymbol(view.metadata), view.gsdColumn, statistics["count"],
                                         FormatGsdText(statistics["minimum"]), FormatGsdText(statistics["mean"]),
                                         FormatGsdText(statistics["median"]), FormatGsdText(statistics["maximum"])))
    elif view.gsdColumn:
        lines.append("    No valid GSD value in column {}.".format(view.gsdColumn))
    else:
        lines.append("    No GSD column.")
    if view.gsdDefinition == GSD_DEFINITION_GEOMETRIC_MEAN:
        lines.append("    " + GeometricMeanDefinitionNote(view.gsdColumn))
    lines.append("    File: {}".format(getattr(view, "path", "") or "not known"))
    return lines


def OverviewConsoleLines(views):
    """The statistics of the overview tab in the Console, with the
    warning of DatasetConsistencyWarning when the loaded files are not
    comparable, as the overview shows them."""
    datasetsWithStatistics = [view for view in views if view.statistics is not None]
    if not datasetsWithStatistics:
        return [ConsoleLabelledLine("Overview:", "no GSD value loaded")]
    unitSymbol = CommonUnitSymbol([view.metadata for view in datasetsWithStatistics])
    allValues = [gsdValue for view in datasetsWithStatistics for gsdValue in view.gsdValues
                 if IsValidGsdValue(gsdValue)]
    combinedStatistics = ComputeStatistics(allValues)
    lines = [ConsoleLabelledLine("Overview:", "{}, {:,} GSD samples in total, overall minimum {}, mean {}, "
                                              "maximum {} ({})".format(
        FormatCountWithNoun(len(datasetsWithStatistics), "image", "images"), combinedStatistics["count"],
        FormatGsdText(combinedStatistics["minimum"]), FormatGsdText(combinedStatistics["mean"]),
        FormatGsdText(combinedStatistics["maximum"]), unitSymbol))]
    consistencyWarning = DatasetConsistencyWarning([view.metadata for view in datasetsWithStatistics],
                                                   [view.gsdDefinition for view in datasetsWithStatistics])
    if consistencyWarning:
        lines.append(consistencyWarning)
    return lines


def LoadConsoleLines(action, chosenCount, loadedViews, failures, rejectedRowNotes, allViews, replacedCount=0):
    """The Console block of a load: *action* "Load" (Load CSV files, in
    place of the *replacedCount* files loaded before) or "Add" (Add CSV
    files), how many of the *chosenCount* files were loaded, each loaded
    file (DatasetConsoleLines), the files that could not be read, the
    rows left out, and the files loaded in all with the statistics of
    the overview (OverviewConsoleLines)."""
    if action == "Load":
        actionText = "Load CSV files"
        if replacedCount:
            actionText += ", replacing {} loaded before".format(
                FormatCountWithNoun(replacedCount, "GSD CSV", "GSD CSVs"))
    else:
        actionText = "Add CSV files"
    lines = ConsoleBlockStart("GSD Visualiser")
    lines.append(ConsoleLabelledLine("Action:", actionText))
    lines.append(ConsoleLabelledLine("Loaded:", "{} of {}".format(
        len(loadedViews), FormatCountWithNoun(chosenCount, "file chosen", "files chosen"))))
    for view in loadedViews:
        lines.extend(DatasetConsoleLines(view))
    if failures:
        lines.append("Not loaded:")
        lines.extend("  " + failure for failure in failures)
    if rejectedRowNotes:
        lines.append("Rows left out:")
        lines.extend("  " + note for note in rejectedRowNotes)
    lines.append(ConsoleLabelledLine("GSD CSVs loaded in all:", len(allViews)))
    lines.extend(OverviewConsoleLines(allViews))
    lines.extend(ConsoleBlockEnd())
    return lines


def ClearConsoleLine(removedCount):
    """The Console line of the Clear button."""
    if removedCount == 0:
        return "GSD Visualiser: Clear, no GSD CSV was loaded."
    return "GSD Visualiser: Clear removed {}.".format(FormatCountWithNoun(removedCount, "GSD CSV", "GSD CSVs"))


def GsdDecimalsConsoleLine(decimals):
    """The Console line of a choice in the GSD decimals box: *decimals*
    decimals, or automatic decimals for None."""
    if decimals is None:
        return "GSD Visualiser: GSD values shown with automatic decimals."
    return "GSD Visualiser: GSD values shown with {}.".format(FormatCountWithNoun(decimals, "decimal", "decimals"))


def HeatmapRowCount(valuesX, valuesY, values):
    """Number of rows that a heat map draws: those whose x, y, and colour
    values are all numbers, as HeatmapWidget.SetData takes them."""
    return sum(1 for positionX, positionY, value in zip(valuesX, valuesY, values)
               if _IsNumber(positionX) and _IsNumber(positionY) and _IsNumber(value))


def HeatmapConsoleLine(datasetName, xColumnName, yColumnName, valueColumnName, invertY, rowCount):
    """The Console line of a heat map that is built or rebuilt: its
    image, colour column, axes, orientation, and the number of rows that
    it draws (HeatmapRowCount)."""
    return ("GSD Visualiser: heat map of {}, colour by {}, x axis {}, y axis {}, {}, from {}.".format(
        datasetName, valueColumnName, xColumnName, yColumnName,
        "image orientation (row 0 at top)" if invertY else "y increasing upwards",
        FormatCountWithNoun(rowCount, "row", "rows")))


# ===================================================================
# Axis captions of the charts
# ===================================================================

# The left edge of the labels of a vertical axis, to the right of its
# caption (DrawAxisCaptions).
VERTICAL_AXIS_LABEL_LEFT = 16


def DrawAxisCaptions(painter, plotLeft, plotTop, plotWidth, plotHeight, widgetHeight, xCaption, yCaption):
    """Draw *xCaption* centred below a plot, on the line of the labels of
    its x axis, and *yCaption* upwards along the left edge of the widget,
    centred on the plot, with the font and the pen of *painter*.  A caption
    that is None or empty is not drawn."""
    if xCaption:
        painter.drawText(QtCore.QRectF(plotLeft, widgetHeight - 18, plotWidth, 16),
                         QtCore.Qt.AlignHCenter | QtCore.Qt.AlignBottom | QtCore.Qt.TextDontClip, xCaption)
    if yCaption:
        painter.save()
        painter.translate(2, plotTop + plotHeight / 2.0)
        painter.rotate(-90)
        painter.drawText(QtCore.QRectF(-plotHeight / 2.0, 0, plotHeight, VERTICAL_AXIS_LABEL_LEFT - 2),
                         QtCore.Qt.AlignHCenter | QtCore.Qt.AlignTop | QtCore.Qt.TextDontClip, yCaption)
        painter.restore()


# ===================================================================
# Histogram widget
# ===================================================================

class HistogramWidget(QtWidgets.QWidget):
    """QPainter histogram of a distribution of GSD values, with the name
    and the unit of its axes."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(160)
        self.values = []
        self.titleQualifier = ""
        self.unitSymbol = None
        self.decimals = None

    def SetValues(self, values, titleQualifier="", unitSymbol=None, decimals=None):
        """Show the GSD values *values* (their unit *unitSymbol*), with
        *decimals* decimals on the axis of the values (None for automatic,
        see FormatAxisValue)."""
        self.values = [value for value in values if _IsNumber(value)]
        self.titleQualifier = titleQualifier
        self.unitSymbol = unitSymbol
        self.decimals = decimals
        self.update()

    def SetDecimals(self, decimals):
        """Label the axis of the values with *decimals* decimals (None for
        automatic, see FormatAxisValue)."""
        self.decimals = decimals
        self.update()

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        width = self.width()
        height = self.height()
        marginLeft = 62
        marginRight = 14
        marginTop = 22
        marginBottom = 34
        plotWidth = width - marginLeft - marginRight
        plotHeight = height - marginTop - marginBottom
        if plotWidth < 40 or plotHeight < 30 or not self.values:
            if not self.values:
                painter.setPen(QtGui.QPen(QtGui.QColor(120, 120, 120), 1))
                painter.drawText(marginLeft, marginTop + 18,
                                 "No data")
            painter.end()
            return

        valueMinimum, valueMaximum = HistogramRange(self.values)
        binCount = max(10, min(60, int(plotWidth / 9)))
        binWidth = (valueMaximum - valueMinimum) / binCount
        counts = [0] * binCount
        for value in self.values:
            index = int((value - valueMinimum) / binWidth)
            index = min(max(index, 0), binCount - 1)
            counts[index] += 1
        maximumBinCount = max(counts) if counts else 1

        painter.setPen(QtGui.QPen(QtGui.QColor(80, 80, 80), 1))
        painter.drawLine(marginLeft, marginTop,
                         marginLeft, marginTop + plotHeight)
        painter.drawLine(marginLeft, marginTop + plotHeight,
                         marginLeft + plotWidth, marginTop + plotHeight)

        for index, count in enumerate(counts):
            if count <= 0:
                continue
            barHeight = (count / maximumBinCount) * plotHeight
            positionX = marginLeft + index / binCount * plotWidth
            barWidth = max(1.0, plotWidth / binCount - 1)
            painter.setBrush(ColorRamp((index + 0.5) / binCount))
            painter.setPen(QtCore.Qt.NoPen)
            painter.drawRect(
                QtCore.QRectF(positionX, marginTop + plotHeight - barHeight,
                              barWidth, barHeight))

        smallFont = QtGui.QFont()
        smallFont.setPointSize(8)
        painter.setFont(smallFont)
        painter.setPen(QtGui.QPen(QtGui.QColor(60, 60, 60), 1))
        painter.drawText(VERTICAL_AXIS_LABEL_LEFT, marginTop + 6, str(maximumBinCount))
        painter.drawText(VERTICAL_AXIS_LABEL_LEFT, marginTop + plotHeight, "0")
        painter.drawText(marginLeft, height - 6,
                         FormatAxisValue(valueMinimum, valueMaximum - valueMinimum, self.decimals))
        painter.drawText(marginLeft + plotWidth - 60, height - 6,
                         FormatAxisValue(valueMaximum, valueMaximum - valueMinimum, self.decimals))
        DrawAxisCaptions(painter, marginLeft, marginTop, plotWidth, plotHeight, height,
                         AxisCaption("GSD", self.unitSymbol), "Count")
        title = ("GSD distribution ({})".format(self.titleQualifier)
                 if self.titleQualifier else "Distribution")
        boldFont = QtGui.QFont()
        boldFont.setPointSize(9)
        boldFont.setBold(True)
        painter.setFont(boldFont)
        painter.setPen(QtGui.QPen(QtGui.QColor(0, 0, 0), 1))
        painter.drawText(marginLeft, marginTop - 8, title)
        painter.end()


# ===================================================================
# Heat map widget (binned grid + colour bar)
# ===================================================================

# The colour bar of the heat map, in screen pixels: the gap between the plot
# and the bar, the width of the bar, the gap between the bar and its
# labels, and the room after the widest label.  The right margin of the heat
# map is at least MINIMUM_HEATMAP_MARGIN_RIGHT, which leaves 42 screen pixels
# for the labels, and grows with them, so that a label with many decimals is
# not cut off at the edge of the window.
COLOUR_BAR_GAP = 16
COLOUR_BAR_WIDTH = 16
COLOUR_BAR_LABEL_GAP = 4
COLOUR_BAR_LABEL_PADDING = 4
MINIMUM_HEATMAP_MARGIN_RIGHT = 78


def MeasuredTextWidth(fontMetrics, text):
    """Width of *text* in screen pixels with *fontMetrics* (a
    QFontMetrics), or None when the font metrics give no number."""
    try:
        width = fontMetrics.horizontalAdvance(text)
    except Exception as suppressedError:
        ReportSuppressedError("gsdVisualiser.MeasuredTextWidth", suppressedError)
        return None
    if isinstance(width, (int, float)) and math.isfinite(width):
        return width
    return None


def HeatmapMarginRight(labelWidths):
    """Right margin of the heat map in screen pixels: room for the colour
    bar and the widest of its labels (*labelWidths*, from
    MeasuredTextWidth, None for a width that is not known), at least
    MINIMUM_HEATMAP_MARGIN_RIGHT."""
    knownWidths = [width for width in labelWidths if width is not None]
    if not knownWidths:
        return MINIMUM_HEATMAP_MARGIN_RIGHT
    return max(MINIMUM_HEATMAP_MARGIN_RIGHT,
               COLOUR_BAR_GAP + COLOUR_BAR_WIDTH + COLOUR_BAR_LABEL_GAP + int(math.ceil(max(knownWidths)))
               + COLOUR_BAR_LABEL_PADDING)


class HeatmapWidget(QtWidgets.QWidget):
    """Bins (x, y, value) samples into a grid and colour codes the
    per-cell mean, with a colour bar, and names its axes and its colour
    column with their units."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(360, 300)
        self._gridSums = None
        self._gridCounts = None
        self._binCountX = 0
        self._binCountY = 0
        self._valueMinimum = 0.0
        self._valueMaximum = 1.0
        self._xLabel = ""
        self._yLabel = ""
        self._valueLabel = ""
        self._invertY = False
        self._units = (None, None, None)
        self._gsdDecimals = None

    def SetData(self, valuesX, valuesY, values, xLabel, yLabel, valueLabel,
                invertY=False, binTarget=120, units=(None, None, None), gsdDecimals=None):
        """Load (x, y, value) samples, bin them into a grid of at most
        *binTarget* cells per axis and never more cells than sampled
        positions on an axis (see HeatmapBinCounts), and repaint.  *units*
        are the units of x, y, and the values (ColumnUnitSymbol, None where
        there is none), and *gsdDecimals* the decimals of the labels of a
        GSD column (GsdDecimalsFor)."""
        self._xLabel = xLabel
        self._yLabel = yLabel
        self._valueLabel = valueLabel
        self._invertY = invertY
        self._units = tuple(units)
        self._gsdDecimals = gsdDecimals

        triples = [(positionX, positionY, value) for positionX, positionY, value in zip(valuesX, valuesY, values)
                   if _IsNumber(positionX) and _IsNumber(positionY) and _IsNumber(value)]
        if not triples:
            self._gridSums = None
            self.update()
            return

        xValues = [triple[0] for triple in triples]
        yValues = [triple[1] for triple in triples]
        colourValues = [triple[2] for triple in triples]
        self._xMinimum, self._xMaximum = min(xValues), max(xValues)
        self._yMinimum, self._yMaximum = min(yValues), max(yValues)
        self._valueMinimum, self._valueMaximum = min(colourValues), max(colourValues)
        if self._xMaximum <= self._xMinimum:
            self._xMaximum = self._xMinimum + 1.0
        if self._yMaximum <= self._yMinimum:
            self._yMaximum = self._yMinimum + 1.0
        if self._valueMaximum <= self._valueMinimum:
            self._valueMaximum = self._valueMinimum + 1e-9

        xRange = self._xMaximum - self._xMinimum
        yRange = self._yMaximum - self._yMinimum
        # Never more cells than samples can fill (see HeatmapBinCounts),
        # and each sample goes to the nearest cell, so that a regular
        # sampling grid leaves no empty stripes.
        self._binCountX, self._binCountY = HeatmapBinCounts(xValues, yValues, binTarget)

        self._gridSums = [[0.0] * self._binCountX
                          for _ in range(self._binCountY)]
        self._gridCounts = [[0] * self._binCountX
                            for _ in range(self._binCountY)]
        for positionX, positionY, sampleValue in triples:
            binIndexX = int(round((positionX - self._xMinimum) / xRange * (self._binCountX - 1)))
            binIndexY = int(round((positionY - self._yMinimum) / yRange * (self._binCountY - 1)))
            binIndexX = min(max(binIndexX, 0), self._binCountX - 1)
            binIndexY = min(max(binIndexY, 0), self._binCountY - 1)
            self._gridSums[binIndexY][binIndexX] += sampleValue
            self._gridCounts[binIndexY][binIndexX] += 1
        self.update()

    def SetGsdDecimals(self, gsdDecimals):
        """Label an axis and the colour bar of a GSD column with
        *gsdDecimals* decimals (None for automatic, see GsdDecimalsFor)."""
        self._gsdDecimals = gsdDecimals
        self.update()

    def ColourBarLabelTexts(self):
        """The labels of the colour bar: the largest, middle, and smallest
        value, with the decimals of the axis labels."""
        labelInterval = (self._valueMaximum - self._valueMinimum) / 2.0
        valueDecimals = GsdDecimalsFor(self._valueLabel, self._gsdDecimals)
        return [FormatAxisValue(value, labelInterval, valueDecimals)
                for value in (self._valueMaximum, (self._valueMaximum + self._valueMinimum) / 2.0,
                              self._valueMinimum)]

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, False)
        width = self.width()
        height = self.height()
        # The right margin makes room for the widest label of the colour
        # bar, measured in the font that draws it.
        labelFont = QtGui.QFont()
        labelFont.setPointSize(8)
        labelMetrics = QtGui.QFontMetrics(labelFont)
        colourBarLabels = self.ColourBarLabelTexts()
        marginRight = HeatmapMarginRight([MeasuredTextWidth(labelMetrics, label) for label in colourBarLabels])
        marginLeft = 76
        marginTop = 26
        marginBottom = 38
        plotWidth = width - marginLeft - marginRight
        plotHeight = height - marginTop - marginBottom
        if plotWidth < 40 or plotHeight < 40 or self._gridSums is None:
            painter.setPen(QtGui.QPen(QtGui.QColor(120, 120, 120), 1))
            painter.drawText(marginLeft, marginTop + 20,
                             "No data to display")
            painter.end()
            return

        cellWidth = plotWidth / self._binCountX
        cellHeight = plotHeight / self._binCountY
        valueRange = self._valueMaximum - self._valueMinimum

        painter.setPen(QtCore.Qt.NoPen)
        for binIndexY in range(self._binCountY):
            for binIndexX in range(self._binCountX):
                count = self._gridCounts[binIndexY][binIndexX]
                if count <= 0:
                    continue
                mean = self._gridSums[binIndexY][binIndexX] / count
                fraction = (mean - self._valueMinimum) / valueRange
                painter.setBrush(ColorRamp(fraction))
                if self._invertY:
                    cellTopY = marginTop + binIndexY * cellHeight
                else:
                    cellTopY = marginTop + plotHeight - (binIndexY + 1) * cellHeight
                painter.drawRect(
                    QtCore.QRectF(marginLeft + binIndexX * cellWidth, cellTopY,
                                  cellWidth + 0.6, cellHeight + 0.6))

        painter.setBrush(QtCore.Qt.NoBrush)
        painter.setPen(QtGui.QPen(QtGui.QColor(60, 60, 60), 1))
        painter.drawRect(marginLeft, marginTop, plotWidth, plotHeight)

        smallFont = QtGui.QFont()
        smallFont.setPointSize(8)
        painter.setFont(smallFont)
        topY = self._yMaximum if not self._invertY else self._yMinimum
        bottomY = self._yMinimum if not self._invertY else self._yMaximum
        yInterval = self._yMaximum - self._yMinimum
        xInterval = self._xMaximum - self._xMinimum
        yDecimals = GsdDecimalsFor(self._yLabel, self._gsdDecimals)
        xDecimals = GsdDecimalsFor(self._xLabel, self._gsdDecimals)
        painter.drawText(VERTICAL_AXIS_LABEL_LEFT, marginTop + 8, FormatAxisValue(topY, yInterval, yDecimals))
        painter.drawText(VERTICAL_AXIS_LABEL_LEFT, marginTop + plotHeight,
                         FormatAxisValue(bottomY, yInterval, yDecimals))
        painter.drawText(marginLeft, height - 6, FormatAxisValue(self._xMinimum, xInterval, xDecimals))
        painter.drawText(marginLeft + plotWidth - 60, height - 6,
                         FormatAxisValue(self._xMaximum, xInterval, xDecimals))
        DrawAxisCaptions(painter, marginLeft, marginTop, plotWidth, plotHeight, height,
                         AxisCaption(self._xLabel, self._units[0]), AxisCaption(self._yLabel, self._units[1]))

        # The axes are named below and beside the plot, so the title names
        # the colour column, with the unit of its values.
        boldFont = QtGui.QFont()
        boldFont.setPointSize(9)
        boldFont.setBold(True)
        painter.setFont(boldFont)
        painter.setPen(QtGui.QPen(QtGui.QColor(0, 0, 0), 1))
        painter.drawText(marginLeft, marginTop - 10, AxisCaption(self._valueLabel, self._units[2]))

        self._DrawColorBar(painter, width, marginTop, plotHeight, marginRight, colourBarLabels)
        painter.end()

    def _DrawColorBar(self, painter, width, marginTop, plotHeight, marginRight, labelTexts):
        barWidth = COLOUR_BAR_WIDTH
        barX = width - marginRight + COLOUR_BAR_GAP
        colourStepCount = 64
        for index in range(colourStepCount):
            fraction = index / (colourStepCount - 1)
            positionY = marginTop + plotHeight - (index + 1) * (plotHeight / colourStepCount)
            painter.setPen(QtCore.Qt.NoPen)
            painter.setBrush(ColorRamp(fraction))
            painter.drawRect(QtCore.QRectF(
                barX, positionY, barWidth, plotHeight / colourStepCount + 0.6))
        painter.setBrush(QtCore.Qt.NoBrush)
        painter.setPen(QtGui.QPen(QtGui.QColor(60, 60, 60), 1))
        painter.drawRect(barX, marginTop, barWidth, plotHeight)
        smallFont = QtGui.QFont()
        smallFont.setPointSize(8)
        painter.setFont(smallFont)
        labelX = barX + barWidth + COLOUR_BAR_LABEL_GAP
        largestText, middleText, smallestText = labelTexts
        painter.drawText(labelX, marginTop + 8, largestText)
        painter.drawText(labelX, marginTop + plotHeight / 2 + 4, middleText)
        painter.drawText(labelX, marginTop + plotHeight, smallestText)


# ===================================================================
# Per-image mean-GSD bar chart (overview)
# ===================================================================

class ImageBarChartWidget(QtWidgets.QWidget):
    """Bar chart of each image's mean GSD with a line from its minimum
    to its maximum GSD, with the name and the unit of its GSD axis."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(200)
        self._names = []
        self._means = []
        self._minimumValues = []
        self._maximumValues = []
        self._unitSymbol = None
        self._decimals = None

    def SetData(self, names, means, minimumValues, maximumValues, unitSymbol=None, decimals=None):
        """Show the mean, minimum, and maximum GSD of the images *names*
        (their unit *unitSymbol*), with *decimals* decimals on the GSD axis
        (None for automatic, see FormatAxisValue)."""
        self._names = list(names)
        self._means = list(means)
        self._minimumValues = list(minimumValues)
        self._maximumValues = list(maximumValues)
        self._unitSymbol = unitSymbol
        self._decimals = decimals
        self.update()

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        width = self.width()
        height = self.height()
        marginLeft = 76
        marginRight = 16
        marginTop = 24
        marginBottom = 54
        plotWidth = width - marginLeft - marginRight
        plotHeight = height - marginTop - marginBottom
        if plotWidth < 40 or plotHeight < 30 or not self._means:
            painter.end()
            return

        valueMaximum = max(self._maximumValues) if self._maximumValues else 1.0
        if valueMaximum <= 0:
            valueMaximum = 1.0
        imageCount = len(self._means)
        slotWidth = plotWidth / imageCount
        barWidth = min(slotWidth * 0.7, 40)
        meanMinimum = min(self._means)
        meanMaximum = max(self._means)
        meanRange = (meanMaximum - meanMinimum) if meanMaximum > meanMinimum else 1.0

        # Axes.
        painter.setPen(QtGui.QPen(QtGui.QColor(80, 80, 80), 1))
        painter.drawLine(marginLeft, marginTop,
                         marginLeft, marginTop + plotHeight)
        painter.drawLine(marginLeft, marginTop + plotHeight,
                         marginLeft + plotWidth, marginTop + plotHeight)

        smallFont = QtGui.QFont()
        smallFont.setPointSize(8)
        painter.setFont(smallFont)

        # Y ticks and the caption of the GSD axis.
        painter.setPen(QtGui.QPen(QtGui.QColor(60, 60, 60), 1))
        for stepIndex in range(4):
            value = valueMaximum * (1 - stepIndex / 3.0)
            positionY = marginTop + stepIndex / 3.0 * plotHeight
            painter.drawText(VERTICAL_AXIS_LABEL_LEFT, int(positionY) + 4,
                             FormatAxisValue(value, valueMaximum / 3.0, self._decimals))
        DrawAxisCaptions(painter, marginLeft, marginTop, plotWidth, plotHeight, height,
                         None, AxisCaption("GSD", self._unitSymbol))

        labelInterval = max(1, int(math.ceil(imageCount / 14.0)))
        for index in range(imageCount):
            centre = marginLeft + (index + 0.5) * slotWidth
            meanValue = self._means[index]
            minimumValue = self._minimumValues[index]
            maximumValue = self._maximumValues[index]
            barHeight = (meanValue / valueMaximum) * plotHeight
            fraction = ((meanValue - meanMinimum) / meanRange)
            painter.setBrush(ColorRamp(fraction))
            painter.setPen(QtCore.Qt.NoPen)
            painter.drawRect(QtCore.QRectF(
                centre - barWidth / 2,
                marginTop + plotHeight - barHeight,
                barWidth, barHeight))

            # Line from the minimum to the maximum.
            minimumValuePositionY = marginTop + plotHeight - (minimumValue / valueMaximum) * plotHeight
            maximumValuePositionY = marginTop + plotHeight - (maximumValue / valueMaximum) * plotHeight
            painter.setPen(QtGui.QPen(QtGui.QColor(40, 40, 40), 1))
            painter.drawLine(int(centre), int(maximumValuePositionY),
                             int(centre), int(minimumValuePositionY))
            painter.drawLine(int(centre - 3), int(maximumValuePositionY),
                             int(centre + 3), int(maximumValuePositionY))
            painter.drawLine(int(centre - 3), int(minimumValuePositionY),
                             int(centre + 3), int(minimumValuePositionY))

            if index % labelInterval == 0:
                name = self._names[index]
                shortName = (name if len(name) <= 10
                             else name[:9] + "\u2026")
                painter.save()
                painter.translate(centre, marginTop + plotHeight + 6)
                painter.rotate(40)
                painter.setPen(QtGui.QPen(QtGui.QColor(60, 60, 60), 1))
                painter.drawText(0, 6, shortName)
                painter.restore()

        boldFont = QtGui.QFont()
        boldFont.setPointSize(9)
        boldFont.setBold(True)
        painter.setFont(boldFont)
        painter.setPen(QtGui.QPen(QtGui.QColor(0, 0, 0), 1))
        painter.drawText(
            marginLeft, marginTop - 8,
            "Mean GSD per image (bar), minimum\u2013maximum range (line)")
        painter.end()


# ===================================================================
# Per-image view
# ===================================================================

class PerImageView(QtWidgets.QWidget):
    """One image's GSD: file information, statistics, histogram, and
    heat map."""

    # The decimals of the GSD values of the tab (GSD_DECIMAL_CHOICES, None
    # for automatic), which the GSD decimals box of the dialog sets
    # (SetGsdDecimals).
    gsdDecimals = None

    def __init__(self, datasetName, metadata, columnNames, rows,
                 parent=None):
        super().__init__(parent)
        self.datasetName = datasetName
        self.metadata = metadata
        self.columnNames = columnNames
        self.rows = rows
        self._heatmapBuilt = False

        # The area-equivalent GSD, or for a file without it the geometric
        # mean of the row and column sampling distances.  Rows
        # whose GSD value is not a positive finite number (see
        # IsValidGsdValue) are left out of the statistics, the histogram,
        # the heat map, and the overview, and the loading names them.
        (self.gsdColumn, self.gsdDefinition, self.chartRows, self.rejectedGsdRows, self.gsdValues,
         self.statistics) = GsdDatasetValues(columnNames, rows)
        # The file that the view shows (set by the dialog).
        self.path = ""

        self._BuildUserInterface()

    def _BuildUserInterface(self):
        layout = QtWidgets.QVBoxLayout(self)

        # The image, surface mode, CRS, unit of the lengths, and number of
        # records (InformationLineText).
        self.informationLabel = QtWidgets.QLabel(InformationLineText(self.metadata, len(self.rows)))
        self.informationLabel.setTextFormat(QtCore.Qt.RichText)
        self.informationLabel.setWordWrap(True)
        layout.addWidget(self.informationLabel)

        unitSymbol = DatasetUnitSymbol(self.metadata)
        if self.statistics is not None:
            self.statisticsLabel = QtWidgets.QLabel(ImageStatisticsText(unitSymbol, self.statistics,
                                                                        self.gsdDecimals))
            self.statisticsLabel.setTextFormat(QtCore.Qt.RichText)
            self.statisticsLabel.setWordWrap(True)
            layout.addWidget(self.statisticsLabel)

        if self.gsdDefinition == GSD_DEFINITION_GEOMETRIC_MEAN:
            definitionLabel = QtWidgets.QLabel(GeometricMeanDefinitionNote(self.gsdColumn))
            definitionLabel.setWordWrap(True)
            definitionLabel.setStyleSheet("color: #a04000;")
            layout.addWidget(definitionLabel)

        self.histogram = HistogramWidget()
        self.histogram.SetValues(self.gsdValues, self.gsdColumn or "GSD", unitSymbol=unitSymbol,
                                 decimals=self.gsdDecimals)
        layout.addWidget(self.histogram)

        # The labels in the first column, the X axis, Y axis, and Colour by
        # drop-downs below the button Across image (pixels), and the check
        # box of the image orientation beside Colour by.
        controlsGroup = QtWidgets.QGroupBox("Heat map")
        controlsLayout = QtWidgets.QGridLayout(controlsGroup)
        controlsLayout.addWidget(QtWidgets.QLabel("Preset:"), 0, 0)
        self.presetImageButton = QtWidgets.QPushButton(
            "Across image (pixels)")
        self.presetSurfaceButton = QtWidgets.QPushButton(
            "Across surface (CRS X/Y)")
        self.presetImageButton.clicked.connect(self._OnPresetImage)
        self.presetSurfaceButton.clicked.connect(self._OnPresetSurface)
        controlsLayout.addWidget(self.presetImageButton, 0, 1)
        controlsLayout.addWidget(self.presetSurfaceButton, 0, 2)
        controlsLayout.addWidget(QtWidgets.QLabel("X axis:"), 1, 0)
        self.xColumnComboBox = QtWidgets.QComboBox()
        controlsLayout.addWidget(self.xColumnComboBox, 1, 1)
        controlsLayout.addWidget(QtWidgets.QLabel("Y axis:"), 2, 0)
        self.yColumnComboBox = QtWidgets.QComboBox()
        controlsLayout.addWidget(self.yColumnComboBox, 2, 1)
        controlsLayout.addWidget(QtWidgets.QLabel("Colour by:"), 3, 0)
        self.valueColumnComboBox = QtWidgets.QComboBox()
        controlsLayout.addWidget(self.valueColumnComboBox, 3, 1)
        self.invertYCheckBox = QtWidgets.QCheckBox(
            "Image orientation (row 0 at top)")
        controlsLayout.addWidget(self.invertYCheckBox, 3, 2)
        controlsLayout.setColumnStretch(1, 1)
        controlsLayout.setColumnStretch(2, 1)
        layout.addWidget(controlsGroup)

        self.heatmap = HeatmapWidget()
        layout.addWidget(self.heatmap, 1)

        numericColumns = [name for name in self.columnNames
                          if self._ColumnIsNumeric(name)]
        for comboBox in (self.xColumnComboBox, self.yColumnComboBox, self.valueColumnComboBox):
            comboBox.blockSignals(True)
            comboBox.addItems(numericColumns)
            comboBox.blockSignals(False)
            comboBox.currentIndexChanged.connect(self._UpdateHeatmap)
        self.invertYCheckBox.stateChanged.connect(self._UpdateHeatmap)

        # Default to the image preset, without building the heat map
        # yet: that happens when the tab is first shown.
        self._SetPreset(
            ["pixelcolumn", "pixel_x", "px_x", "column", "col"],
            ["pixelrow", "pixel_y", "px_y", "row"], invertY=True, buildHeatmap=False,
            excludedSubstrings=("gsd",))

    def _ColumnIsNumeric(self, name):
        """True when the column *name* holds a number in any row of the
        file, so that a column whose first rows are empty, as they can be
        in a file of another program, is still offered."""
        return any(isinstance(row.get(name), float) for row in self.rows)

    def _SelectColumnInComboBox(self, comboBox, columnName):
        if columnName is None:
            return
        index = comboBox.findText(columnName)
        if index >= 0:
            comboBox.blockSignals(True)
            comboBox.setCurrentIndex(index)
            comboBox.blockSignals(False)

    def _SetPreset(self, xKeywords, yKeywords, invertY, buildHeatmap=True,
                   excludedSubstrings=()):
        """Select the axis columns by keyword (see AutodetectAxisColumn,
        columns containing one of *excludedSubstrings* are never axes),
        colour by the GSD, and set the orientation of the y axis."""
        self._SelectColumnInComboBox(self.xColumnComboBox,
                       AutodetectAxisColumn(self.columnNames, xKeywords,
                                            excludedSubstrings))
        self._SelectColumnInComboBox(self.yColumnComboBox,
                       AutodetectAxisColumn(self.columnNames, yKeywords,
                                            excludedSubstrings))
        self._SelectColumnInComboBox(self.valueColumnComboBox, self.gsdColumn)
        self.invertYCheckBox.blockSignals(True)
        self.invertYCheckBox.setChecked(invertY)
        self.invertYCheckBox.blockSignals(False)
        if buildHeatmap:
            self._UpdateHeatmap()

    def _OnPresetImage(self):
        self._SetPreset(
            ["pixelcolumn", "pixel_x", "px_x", "column", "col"],
            ["pixelrow", "pixel_y", "px_y", "row"], invertY=True,
            excludedSubstrings=("gsd",))

    def _OnPresetSurface(self):
        self._SetPreset(
            ["crs_x", "east", "longitude", "_x"],
            ["crs_y", "north", "latitude", "_y"], invertY=False,
            excludedSubstrings=("gsd", "pixel", "px_"))

    def _UpdateHeatmap(self):
        xColumnName = self.xColumnComboBox.currentText()
        yColumnName = self.yColumnComboBox.currentText()
        valueColumnName = self.valueColumnComboBox.currentText()
        if not xColumnName or not yColumnName or not valueColumnName:
            return
        valuesX = [row.get(xColumnName) for row in self.chartRows]
        valuesY = [row.get(yColumnName) for row in self.chartRows]
        colourValues = [row.get(valueColumnName) for row in self.chartRows]
        units = tuple(ColumnUnitSymbol(columnName, self.metadata)
                      for columnName in (xColumnName, yColumnName, valueColumnName))
        self.heatmap.SetData(
            valuesX, valuesY, colourValues, xColumnName, yColumnName, valueColumnName,
            invertY=self.invertYCheckBox.isChecked(), units=units, gsdDecimals=self.gsdDecimals)
        self._heatmapBuilt = True
        print(HeatmapConsoleLine(self.datasetName, xColumnName, yColumnName, valueColumnName,
                                 self.invertYCheckBox.isChecked(), HeatmapRowCount(valuesX, valuesY, colourValues)))

    def EnsureHeatmapBuilt(self):
        """Build the heat map the first time the tab is shown."""
        if not self._heatmapBuilt:
            self._UpdateHeatmap()

    def SetGsdDecimals(self, decimals):
        """Show the GSD values of the tab with *decimals* decimals, or with
        automatic decimals for None (GSD_DECIMAL_CHOICES): the statistics,
        the histogram, and the labels of a GSD column in the heat map."""
        self.gsdDecimals = decimals
        if self.statistics is not None:
            self.statisticsLabel.setText(ImageStatisticsText(DatasetUnitSymbol(self.metadata), self.statistics,
                                                             decimals))
        self.histogram.SetDecimals(decimals)
        self.heatmap.SetGsdDecimals(decimals)


# ===================================================================
# Overview view (all images)
# ===================================================================

class OverviewView(QtWidgets.QWidget):
    """Summary table, per-image bar chart, and combined histogram."""

    # The decimals of the GSD values of the overview (GSD_DECIMAL_CHOICES,
    # None for automatic), and the image tabs that it shows, so that a new
    # choice of decimals shows them again (SetGsdDecimals).
    gsdDecimals = None
    _datasets = ()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QtWidgets.QVBoxLayout(self)

        self.summaryLabel = QtWidgets.QLabel(
            "Load one or more GSD CSVs to populate the overview.")
        self.summaryLabel.setTextFormat(QtCore.Qt.RichText)
        self.summaryLabel.setWordWrap(True)
        layout.addWidget(self.summaryLabel)

        self.table = QtWidgets.QTableWidget(0, 9)
        self.table.setHorizontalHeaderLabels(OverviewTableHeaders("m"))
        self.table.setEditTriggers(
            QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(
            QtWidgets.QHeaderView.ResizeToContents)
        self.table.setMaximumHeight(220)
        layout.addWidget(self.table)

        self.barChart = ImageBarChartWidget()
        layout.addWidget(self.barChart, 1)

        self.combinedHistogram = HistogramWidget()
        layout.addWidget(self.combinedHistogram, 1)

    def SetDatasets(self, datasets):
        """datasets: list of PerImageView.  The GSD values have the
        decimals of gsdDecimals (None for automatic)."""
        self._datasets = list(datasets)
        decimals = self.gsdDecimals
        datasetsWithStatistics = [dataset for dataset in datasets if dataset.statistics is not None]
        unitSymbol = CommonUnitSymbol([view.metadata for view in datasetsWithStatistics])

        self.table.setHorizontalHeaderLabels(OverviewTableHeaders(unitSymbol))
        self.table.setRowCount(len(datasetsWithStatistics))
        for rowIndex, view in enumerate(datasetsWithStatistics):
            statistics = view.statistics
            values = [
                view.datasetName,
                "{:,}".format(statistics["count"]),
                FormatGsdText(statistics["minimum"], decimals),
                FormatGsdText(statistics["percentile02"], decimals),
                FormatGsdText(statistics["mean"], decimals),
                FormatGsdText(statistics["median"], decimals),
                FormatGsdText(statistics["percentile98"], decimals),
                FormatGsdText(statistics["maximum"], decimals),
                FormatGsdText(statistics["standardDeviation"], decimals),
            ]
            for columnIndex, text in enumerate(values):
                item = QtWidgets.QTableWidgetItem(text)
                if columnIndex > 0:
                    item.setTextAlignment(QtCore.Qt.AlignCenter)
                self.table.setItem(rowIndex, columnIndex, item)

        if not datasetsWithStatistics:
            self.summaryLabel.setText(
                "Load one or more GSD CSVs to populate the overview.")
            self.barChart.SetData([], [], [], [])
            self.combinedHistogram.SetValues([], "")
            return

        names = [datasetWithStatistics.datasetName for datasetWithStatistics in datasetsWithStatistics]
        means = [datasetWithStatistics.statistics["mean"] for datasetWithStatistics in datasetsWithStatistics]
        minimumValues = [datasetWithStatistics.statistics["minimum"] for datasetWithStatistics in datasetsWithStatistics]
        maximumValues = [datasetWithStatistics.statistics["maximum"] for datasetWithStatistics in datasetsWithStatistics]
        self.barChart.SetData(names, means, minimumValues, maximumValues, unitSymbol=unitSymbol, decimals=decimals)

        allValues = []
        for view in datasetsWithStatistics:
            allValues.extend(
                gsdValue for gsdValue in view.gsdValues if IsValidGsdValue(gsdValue))
        combinedStatistics = ComputeStatistics(allValues)
        self.combinedHistogram.SetValues(allValues, "all images", unitSymbol=unitSymbol, decimals=decimals)

        if combinedStatistics is not None:
            summaryText = (
                "<b>{}</b> &nbsp;|&nbsp; "
                "<b>{:,}</b> GSD samples in total &nbsp;|&nbsp; "
                "overall minimum <b>{}</b>, mean <b>{}</b>, "
                "maximum <b>{}</b> ({})".format(
                    FormatCountWithNoun(len(datasetsWithStatistics), "image", "images"),
                    combinedStatistics["count"],
                    FormatGsdText(combinedStatistics["minimum"], decimals),
                    FormatGsdText(combinedStatistics["mean"], decimals),
                    FormatGsdText(combinedStatistics["maximum"], decimals), unitSymbol))
            consistencyWarning = DatasetConsistencyWarning(
                [view.metadata for view in datasetsWithStatistics],
                [view.gsdDefinition for view in datasetsWithStatistics])
            if consistencyWarning:
                summaryText += "<br><span style='color:#a04000;'>{}</span>".format(
                    consistencyWarning)
            self.summaryLabel.setText(summaryText)

    def SetGsdDecimals(self, decimals):
        """Show the GSD values of the overview with *decimals* decimals, or
        with automatic decimals for None (GSD_DECIMAL_CHOICES)."""
        self.gsdDecimals = decimals
        self.SetDatasets(self._datasets)


# ===================================================================
# About text
# ===================================================================

_ABOUT_HTML = """
<h2>GSD Visualiser</h2>
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

<h3>What this tool does</h3>
<p>This tool loads one or more ground sampling distance (GSD) files in the CSV format, written by
the MetaFOCUS <i>GSD Value Exporter</i>, and visualises them in a separate window. Each export
covers one image, so each CSV becomes one image tab. An additional overview tab summarises all the
loaded images together.</p>

<h3>Image tab</h3>
<p>Each image tab shows the following information and charts:</p>
<ul>
<li><b>File information:</b> the image, surface mode, coordinate reference system (CRS), unit of the
lengths, and number of records from the comment lines of the CSV, with the number of rows read where
it differs. Files that name the image &lsquo;Camera&rsquo; and the surface mode &lsquo;GSD mode&rsquo;
are read as well.</li>
<li><b>Statistics:</b> count, minimum, 2nd percentile, mean, median, 98th percentile, maximum, and
standard deviation of the GSD (its spread over the sampled positions, not an uncertainty). With <b>GSD
decimals</b> on Automatic, the statistics are given with eight decimals, in metres or, for a chunk
without a physical scale, in chunk units. A value with fewer than four significant digits in eight
decimals gets four, in exponent notation below 10<sup>&minus;4</sup>: a GSD of 0.7512&nbsp;&micro;m
reads 7.512e-07, not 0.00000075. The overview tab and the Console write the statistics in the same
way.</li>
<li><b>Histogram:</b> the distribution of GSD values.</li>
<li><b>Heat map:</b> a chosen value (GSD, incidence angle, or
slant distance) averaged into a grid and colour coded.  The heat map is
drawn either <i>across the image</i> (pixel column and row), which reveals how
GSD varies within the image, or <i>across the surface</i>
(columns CRS_X and CRS_Y).  The grid never has more cells along an axis than
the file has sampled positions on it, so that a regular sampling
grid fills every cell.</li>
</ul>
<p>The statistics, the histograms, and the heat map of the GSD use the
column GSD_areaEquivalent, the area-equivalent GSD of MetaFOCUS.  The
area-equivalent GSD is the
side of the square with the area that the projected row and column
steps span,
&radic;(GSD<sub>row</sub>&nbsp;&middot;&nbsp;GSD<sub>column</sub>&nbsp;&middot;&nbsp;sin&nbsp;&phi;).
Here &phi; is the angle between the steps (Phi_deg).  For a file
without the column GSD_areaEquivalent, a column GSD_mean or GSD_mean_m
is used instead, then the column GSD_geometricMean, and otherwise the
first column whose name contains GSD.  The image tab states that the
values of GSD_mean, GSD_mean_m, and GSD_geometricMean are geometric means
of the row and column sampling distances, which exceed the
area-equivalent GSD where the steps are not perpendicular.</p>
<p>Axis and colour-bar labels get as many decimals as the spacing of the labels needs, so that
sub-millimetre values stay apart. The histograms name both axes, the bar chart its GSD axis, and the
heat map both axes, each with its unit where the file gives one. The unit is px for the pixel
columns and rows, the unit of the lengths for SlantDistance and the GSD columns, and ° for Theta_deg
and Phi_deg. The coordinates CRS_X to CRS_Z take their units from the line &lsquo;CRS units&rsquo; of
the GSD Value Exporter, for example CRS_X (m), or CRS_X (&deg;) for a geographic CRS. A file without
that line, or with a unit given as &lsquo;unknown&rsquo;, shows no unit on these axes. The title of
the heat map names its colour column with its unit, for example SlantDistance (m).</p>

<h3>Overview tab (all images)</h3>
<p>The overview tab shows the following for all loaded images:</p>
<ul>
<li>A table of per-image GSD statistics, with the unit in the column
headers.</li>
<li>A bar chart of each image&rsquo;s mean GSD with its
minimum&ndash;maximum range, so images with an unusual scale stand out.</li>
<li>A combined histogram of the GSD across all loaded images.  It
pools the samples of all files, so each image counts with its number
of samples: it is neither a summary with equal weights per image nor
one with equal weights per unit of surface area.</li>
</ul>
<p>When the loaded files differ in units, in surface mode, or in the definition of the GSD
(area-equivalent, or the geometric mean), a warning appears. It says that the overview pools values
that are not comparable. The &lsquo;Units&rsquo; lines count as metres or as chunk units, &ldquo;chunk
units (project not scaled)&rdquo; and &ldquo;chunk units (chunk not scaled)&rdquo; alike. Chunk
units of different chunks are not comparable either, so a second warning names the chunks when the
files in chunk units come from more than one. A chunk is identified by its project and its chunk key
(the &lsquo;Project&rsquo; and &lsquo;Chunk key&rsquo; comment lines), because chunk labels need not
be unique: two unrelated chunks are often both called &lsquo;Chunk 1&rsquo;. When files in chunk
units do not record both (for example files of a project that was never saved), the warning says
that it cannot be established that they come from one chunk. It does not say so when their &lsquo;Chunk&rsquo;
lines already name different chunks.</p>

<h3>Loading</h3>
<p>Use <b>Load CSV files</b> to choose one or several files at once (this replaces what is loaded),
or <b>Add CSV files</b> to append more, and <b>Clear</b> to start over. Loading many large CSVs uses
memory and takes a moment. Each image&rsquo;s heat map is built the first time its tab is opened. A
file that cannot be read is named in a message, together with the reason, and the other files are
loaded. Rows whose GSD value is not a positive finite number (for example an infinite, missing, or
negative value) are left out of the statistics and the charts. A message names the file and the
lines of those rows.</p>
<p>The box <b>GSD decimals</b> in the top row of the window sets the decimals of every GSD value of
the window. It applies to the statistics, the overview table and summary, and the labels of the
histograms, of the bar chart, and of a heat map of a GSD column. Automatic gives eight decimals in
the texts, or four significant digits where these give fewer, and as many decimals on the axes as
the spacing of their labels needs. Files loaded later get the same choice. The Console lines of a
load keep automatic decimals.</p>
<p>The Console pane lists every load: the files chosen and loaded, and each loaded file with its
rows, surface mode, unit, GSD statistics, and path. It then names the files that could not be read
and the rows left out, and gives the statistics of the overview, with its warning. It also lists
every <b>Clear</b>, every heat map that is built, with its columns, and every choice of <b>GSD
decimals</b>.</p>

<h3>A note on workbooks</h3>
<p>The GSD Value Exporter writes plain CSVs (one per image)
rather than a multi-sheet spreadsheet for two reasons. Metashape runs
scripts with its own Python installation, which does not include a
spreadsheet-writing library, and CSV is the most portable format.
This visualiser is the intended way to bring those per-image files
back together.</p>

<h3>Input columns</h3>
<p>The input columns are PixelColumn, PixelRow, CRS_X, CRS_Y, CRS_Z, SlantDistance, CosineIncidence,
Theta_deg, Phi_deg, and the GSD columns GSD_row, GSD_column, GSD_geometricMean, and
GSD_areaEquivalent, preceded by # comment lines. The &lsquo;Units&rsquo; line of the comment lines
gives the unit of the lengths (metres, or chunk units for a chunk without a physical scale). Columns
are detected automatically by name. If a file differs, the heat map drop-downs allow the axes and
the colour column to be chosen by hand. The GSD column of the statistics, the histograms, and the
overview is always detected automatically, in the order given above.</p>
<p>The delimiter (a comma, a semicolon, or a tab) is detected from the header row, so that the files
of all three choices of ‘Delimiter and decimal separator’ of the GSD Value Exporter are read.
In a file with a semicolon or a tab as delimiter, a number with a decimal comma (1,25 or 2,5e-09) is
read as well as one with a decimal point.</p>
"""


# ===================================================================
# Main dialog
# ===================================================================

class GsdVisualiserDialog(QtWidgets.QDialog):
    """Tabbed window for visualising one or more GSD CSVs."""

    # The choice of the GSD decimals box (GSD_DECIMAL_CHOICES, None for
    # automatic), which the image tabs of files loaded later get as well.
    _gsdDecimals = None

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("GSD Visualiser")
        self.setWindowFlags(
            self.windowFlags()
            & ~QtCore.Qt.WindowContextHelpButtonHint)
        self.resize(940, 800)
        self._imageViews = []
        self._BuildUserInterface()

    def _BuildUserInterface(self):
        layout = QtWidgets.QVBoxLayout(self)

        loadRow = QtWidgets.QHBoxLayout()
        loadButton = QtWidgets.QPushButton("Load CSV files\u2026")
        loadButton.clicked.connect(self._OnLoadReplace)
        addButton = QtWidgets.QPushButton("Add CSV files\u2026")
        addButton.clicked.connect(self._OnAdd)
        clearButton = QtWidgets.QPushButton("Clear")
        clearButton.clicked.connect(self._OnClear)
        loadRow.addWidget(loadButton)
        loadRow.addWidget(addButton)
        loadRow.addWidget(clearButton)
        loadRow.addStretch()
        # The decimals of every GSD value of the window.
        decimalsLabel = QtWidgets.QLabel("GSD decimals:")
        self.gsdDecimalsComboBox = QtWidgets.QComboBox()
        for decimals in GSD_DECIMAL_CHOICES:
            self.gsdDecimalsComboBox.addItem(GsdDecimalsChoiceText(decimals))
        self.gsdDecimalsComboBox.setToolTip(GSD_DECIMALS_TOOLTIP)
        self.gsdDecimalsComboBox.currentIndexChanged.connect(self._OnGsdDecimalsChanged)
        loadRow.addWidget(decimalsLabel)
        loadRow.addWidget(self.gsdDecimalsComboBox)
        loadRow.addSpacing(16)
        self.countLabel = QtWidgets.QLabel("No files loaded.")
        self.countLabel.setStyleSheet("color: #555;")
        loadRow.addWidget(self.countLabel)
        layout.addLayout(loadRow)

        self.tabs = QtWidgets.QTabWidget()
        self.tabs.currentChanged.connect(self._OnTabChanged)
        self.overview = OverviewView()
        ChangeTabsWithoutEcho(self, self.tabs.addTab, self.overview, "Overview (all images)")
        layout.addWidget(self.tabs, 1)

        buttonRow = QtWidgets.QHBoxLayout()
        aboutButton = QtWidgets.QPushButton("About")
        aboutButton.clicked.connect(self._OnAbout)
        closeButton = QtWidgets.QPushButton("Close")
        closeButton.clicked.connect(self.close)
        buttonRow.addWidget(aboutButton)
        buttonRow.addStretch()
        buttonRow.addWidget(closeButton)
        layout.addLayout(buttonRow)

    def _OnAbout(self):
        aboutDialog = QtWidgets.QDialog(self)
        aboutDialog.setWindowTitle("About: GSD Visualiser")
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

    def _PickFiles(self):
        startDirectory = ""
        try:
            document = Metashape.app.document
            if document and document.path:
                startDirectory = os.path.dirname(document.path)
        except Exception as suppressedError:
            # Harmless: the file dialog then opens at its default folder.
            ReportSuppressedError("gsdVisualiser._PickFiles", suppressedError)
            startDirectory = ""
        paths, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self, "Open GSD CSV files", startDirectory,
            "CSV files (*.csv);;All files (*.*)")
        return paths

    def _UniqueName(self, baseName):
        existingNames = {view.datasetName for view in self._imageViews}
        if baseName not in existingNames:
            return baseName
        counter = 2
        while "{} ({})".format(baseName, counter) in existingNames:
            counter += 1
        return "{} ({})".format(baseName, counter)

    def _LoadPaths(self, paths, action="Add", replacedCount=0):
        """Load the GSD CSVs *paths* as image tabs and echo the load to the
        Console (LoadConsoleLines): *action* "Load" in place of the
        *replacedCount* files loaded before, or "Add"."""
        loadedCount = 0
        loadedViews = []
        failures = []
        rejectedRowNotes = []
        for path in paths:
            try:
                metadata, columnNames, rows = ParseGsdCsv(path)
            except Exception as parseError:
                failures.append("{} could not be read: {}".format(
                    os.path.basename(path), DescribeException(parseError)))
                continue
            baseName = (MetadataValue(metadata, ("Image", "Camera"))
                        or os.path.splitext(os.path.basename(path))[0])
            name = self._UniqueName(baseName)
            view = PerImageView(name, metadata, columnNames, rows)
            view.path = path
            # A tab shows automatic decimals until it gets a choice of the
            # GSD decimals box.
            if self._gsdDecimals is not None:
                view.SetGsdDecimals(self._gsdDecimals)
            self._imageViews.append(view)
            loadedViews.append(view)
            ChangeTabsWithoutEcho(self, self.tabs.addTab, view, name)
            loadedCount += 1
            if view.rejectedGsdRows:
                rejectedRowNotes.append(RejectedGsdRowsNote(
                    os.path.basename(path), [row.lineNumber for row in view.rejectedGsdRows]))

        self.overview.SetDatasets(self._imageViews)
        self._UpdateCountLabel()
        PrintConsoleLines(LoadConsoleLines(action, len(paths), loadedViews, failures, rejectedRowNotes,
                                           self._imageViews, replacedCount))

        if failures or rejectedRowNotes:
            QtWidgets.QMessageBox.warning(
                self, "GSD Visualiser",
                "\n".join(failures + rejectedRowNotes))
        if loadedCount and not failures:
            # Jump to the first newly added image tab.
            ChangeTabsWithoutEcho(self, self.tabs.setCurrentIndex, self.tabs.count() - loadedCount)

    def _OnLoadReplace(self):
        paths = self._PickFiles()
        if not paths:
            return
        replacedCount = self._ClearViews()
        self._LoadPaths(paths, action="Load", replacedCount=replacedCount)

    def _OnAdd(self):
        paths = self._PickFiles()
        if not paths:
            return
        self._LoadPaths(paths, action="Add")

    def _ClearViews(self):
        """Remove all image tabs (keep the overview at index 0) and return
        how many GSD CSVs were loaded."""
        removedCount = len(self._imageViews)
        # The overview first: removing the current image tab would make the
        # tab before it current and build the heat map of a file that is
        # being removed.
        ChangeTabsWithoutEcho(self, self.tabs.setCurrentIndex, 0)
        for index in range(self.tabs.count() - 1, 0, -1):
            ChangeTabsWithoutEcho(self, self.tabs.removeTab, index)
        for view in self._imageViews:
            view.deleteLater()
        self._imageViews = []
        self.overview.SetDatasets([])
        self._UpdateCountLabel()
        return removedCount

    def _OnClear(self):
        print(ClearConsoleLine(self._ClearViews()))

    def _UpdateCountLabel(self):
        count = len(self._imageViews)
        if count == 0:
            self.countLabel.setText("No files loaded.")
        else:
            self.countLabel.setText(
                "{} loaded.".format(FormatCountWithNoun(count, "GSD CSV", "GSD CSVs")))

    def _OnGsdDecimalsChanged(self, index):
        """Show every GSD value of the window (the image tabs and the
        overview) with the choice *index* of the GSD decimals box
        (GSD_DECIMAL_CHOICES), and echo it to the Console."""
        if not 0 <= index < len(GSD_DECIMAL_CHOICES):
            return
        self._gsdDecimals = GSD_DECIMAL_CHOICES[index]
        for view in self._imageViews:
            view.SetGsdDecimals(self._gsdDecimals)
        self.overview.SetGsdDecimals(self._gsdDecimals)
        print(GsdDecimalsConsoleLine(self._gsdDecimals))

    def _OnTabChanged(self, index):
        """Echo the tab that the user shows (a click, Ctrl+Tab, or the mouse
        wheel), but not the tab changes of Load, Add, and Clear
        (ChangeTabsWithoutEcho), and build the heat map of an image tab.
        The echo does not come from tabBarClicked, which the press of any
        mouse button sends, also on the tab already shown."""
        if index >= 0 and not getattr(self, "_isChangingTabs", False):
            print("GSD Visualiser: tab {} shown.".format(self.tabs.tabText(index)))
        widget = self.tabs.widget(index)
        if isinstance(widget, PerImageView):
            widget.EnsureHeatmapBuilt()


def ChangeTabsWithoutEcho(dialog, tabMethod, *arguments):
    """Call *tabMethod* (addTab, setCurrentIndex, or removeTab of the tab
    widget of *dialog*) with *arguments*, so that a change of the current
    tab that it causes is not echoed as one of the user (_OnTabChanged)."""
    previousState = getattr(dialog, "_isChangingTabs", False)
    dialog._isChangingTabs = True
    try:
        return tabMethod(*arguments)
    finally:
        dialog._isChangingTabs = previousState


# ===================================================================
# Menu registration
# ===================================================================

def _ShowGsdVisualiserDialog():
    parent = QtWidgets.QApplication.activeWindow()
    dialog = GsdVisualiserDialog(parent)
    dialog.exec_()


def RegisterMenuItem():
    """Register this tool in the MetaFOCUS menu."""

    try:
        Metashape.app.removeMenuItem(_MENU_LABEL)
    except Exception as suppressedError:
        ReportSuppressedError("gsdVisualiser.RegisterMenuItem", suppressedError)
    iconPath = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "icons", _ICON_NAME)
    if os.path.exists(iconPath):
        Metashape.app.addMenuItem(
            _MENU_LABEL, _ShowGsdVisualiserDialog, icon=iconPath)
    else:
        Metashape.app.addMenuItem(_MENU_LABEL, _ShowGsdVisualiserDialog)
