"""
Manual and Terminology for Agisoft Metashape Professional
=========================================================

Shows all About sections of MetaFOCUS, that of the toolbox and those of
its tools in the order of the MetaFOCUS menu, and the terminology of
MetaFOCUS in one window, taken from the code of the version in use.
In the running text of an About section, the first appearance of a term
of the terminology links to its entry, and Back returns to where the
reader was (headings, links, code, and names such as those of the tools
stay without links).  Save as
PDF saves everything as one PDF file, and Save Terminology as PDF the
terminology alone (Geert, 10 October 2026).

The PDF is written by Qt itself (a QTextDocument laid out on the pages of
a QPdfWriter, A4 with margins of about 2 cm), so no further package is
needed.  Every About section and the terminology start on a new page,
and the pages are numbered at the bottom.  The PDF of the manual has no
internal links and no anchors, which Qt's PDF writer turns into links to
other files that a PDF reader warns about (Geert, 12 October 2026), and
its contents list gives the page of every section.

Author:      Geert J. Verhoeven (ORCID: 0000-0003-4825-9604)
Version:     1.0.1 (2026-11-22)
Date:        2026-10-09
Licence:     GPL-3.0-or-later WITH GPL-3.0-linking-exception
Contact:     geert.verhoeven@univie.ac.at
Requires:    Agisoft Metashape Professional 2.x+
Developed for: Agisoft Metashape Professional 2.3.2

Usage:
    MetaFOCUS > Manual and Terminology (registered by the MetaFOCUS
    loader, which passes the About of the toolbox).
"""

import functools
import importlib
import os
import re

from PySide2 import QtWidgets, QtCore, QtGui

from . import _METAFOCUS_VERSION, _METAFOCUS_DATE
from .photogrammetryUtilities import (DeclinedReplacingText, DescribeException, ReplaceUnconfirmedFileQuestionText,
                                      ReplacesUnconfirmedFile)

_TOOL_NAME = "MetaFOCUS: Manual and Terminology"

# The About constant of every tool, in the order of the MetaFOCUS menu:
# (menu group, ((module, name of the constant), ...)).
ABOUT_PAGE_SOURCES = (
    ("SfM Process Diagnostics", (
        ("sfmLogParser", "_ABOUT_HTML"), ("interiorOrientationViewer", "_ABOUT_HTML"),
        ("tiePointCovarianceExporter", "_ABOUT_HTML"),
        ("imageParameterExporter", "_IMAGE_PARAMETER_EXPORTER_ABOUT_HTML"))),
    ("Control Measurements and Residuals", (
        ("markerResidualInspector", "_ABOUT_HTML"), ("unpinnedMarkerProjectionRemover", "_ABOUT_HTML"),
        ("scaleBarAPrioriUncertainty", "_ABOUT_HTML"))),
    ("Filtering and Project Management", (
        ("gradualSelectionFilter", "_ABOUT_HTML"), ("imageSubsetSelector", "_ABOUT_HTML"),
        ("imageFilter", "_ABOUT_HTML"), ("regionTools", "_ABOUT_HTML"))),
    ("Surface-Derived Project Outputs", (
        ("footprintGenerator", "_FOOTPRINT_ABOUT_TEXT"), ("gsdValueExporter", "_GSD_EXPORT_ABOUT_HTML"),
        ("gsdVisualiser", "_ABOUT_HTML"), ("depthRenderExporter", "_ABOUT_HTML"))),
)

# Style that starts a block on a new page of the PDF (Qt's rich text
# supports it for paragraphs and other blocks), before every About
# section and before the terminology.  The window ignores it.
PDF_PAGE_BREAK_STYLE = "page-break-before: always"

# Anchor at the very start of Manual and Terminology: the window opens
# its history there (PrepareManualBrowser), so that Back is available
# after the first link.
MANUAL_START_ANCHOR = "manual-start"

# Margins of the PDF writer.  The text is laid out with margins of 2 cm
# of its own inside them (PDF_PAGE_MARGIN_CENTIMETRES), so the writer has
# none and the pages have margins of 2 cm.
PDF_WRITER_MARGIN_MILLIMETRES = 0.0

# Margins of the text on every page of a PDF, as QTextDocument.print_()
# gives a document without a page size of its own.  MetaFOCUS lays out
# and prints the pages itself (LaidOutPdfDocument and
# PrintLaidOutDocument), with these margins and the page number at the
# bottom right of every page, as print_() prints them, so that the
# contents of the PDF can give the page of every section.
PDF_PAGE_MARGIN_CENTIMETRES = 2.0

# The sentence on what the manual holds (Geert, 12 October 2026), in his
# words of the About of the toolbox (16 November 2026).
MANUAL_CONTENTS_SENTENCE = (
    "This manual holds all About sections of MetaFOCUS (i.e. the toolbox and its tools, in the order of the "
    "MetaFOCUS menu), and a dedicated terminology.")


def ToolAboutPages():
    """[(menu group, title, html)] of the About of every tool, in the
    order of the MetaFOCUS menu, read from the module constants."""
    pages = []
    for groupName, sources in ABOUT_PAGE_SOURCES:
        for moduleName, constantName in sources:
            module = importlib.import_module("." + moduleName, __package__)
            html = getattr(module, constantName).strip()
            titleMatch = re.search(r"<h2>(.*?)</h2>", html)
            pages.append((groupName, titleMatch.group(1) if titleMatch else moduleName, html))
    return pages


def ManualHtml(toolboxAboutHtml, toolAboutPages, terminologyHtml, version=None, date=None):
    """One page with a title, a sentence on what it holds, a linked
    contents list, the About of the toolbox, the About of every tool,
    and the terminology, each starting on a new page of the PDF
    (PDF_PAGE_BREAK_STYLE).  In each About section the first appearance
    of every term of the terminology links to its entry
    (terminology.LinkTerminologyTerms), which carries an anchor
    (terminology.TerminologyHtmlWithAnchors).  The version and the date
    are those of the package unless given."""
    from . import terminology
    version = version or _METAFOCUS_VERSION
    date = date or _METAFOCUS_DATE
    contentsLines = ['<a href="#about-0">About MetaFOCUS</a>']
    sections = ['<div style="{}"><a name="about-0"></a>{}</div>'.format(
        PDF_PAGE_BREAK_STYLE, terminology.LinkTerminologyTerms(toolboxAboutHtml.strip()))]
    previousGroupName = None
    for pageIndex, (groupName, title, html) in enumerate(toolAboutPages, start=1):
        if groupName != previousGroupName:
            contentsLines.append("<b>{}</b>".format(groupName))
            previousGroupName = groupName
        contentsLines.append('&nbsp;&nbsp;&nbsp;&nbsp;<a href="#about-{}">{}</a>'.format(pageIndex, title))
        sections.append('<div style="{}"><a name="about-{}"></a>{}</div>'.format(
            PDF_PAGE_BREAK_STYLE, pageIndex, terminology.LinkTerminologyTerms(html)))
    contentsLines.append('<a href="#terminology">Terminology</a>')
    sections.append('<div style="{}"><a name="terminology"></a>{}</div>'.format(
        PDF_PAGE_BREAK_STYLE, terminology.TerminologyHtmlWithAnchors(terminologyHtml).strip()))
    # Geert's sentence first and the version on a line of its own.
    return ('<a name="{}"></a><h1>MetaFOCUS {}: Manual and Terminology</h1>\n'
            .format(MANUAL_START_ANCHOR, version) +
            "<p>{} In the running text of an About section, the first appearance of a term of the terminology "
            "links to its entry, and Back returns to where the reader was. Headings, links, code, and names such as "
            "those of the tools stay without links.</p>\n<p>Version {} ({})</p>\n"
            "<h3>Contents</h3>\n<p>{}</p>\n{}").format(MANUAL_CONTENTS_SENTENCE, version, date,
                                                      "<br>\n".join(contentsLines), "\n".join(sections))


def ManualPdfHtml(toolboxAboutHtml, toolAboutPages, terminologyHtml, pageNumbers=None, version=None, date=None):
    """The page of Manual and Terminology for the PDF: the sections of
    ManualHtml without internal links and anchors, because Qt's PDF writer
    turns an internal link into a URI action relative to the file and an
    anchor into an empty URI, which a PDF reader treats as links to other
    files (Geert, 12 October 2026), and with a contents list that gives
    the page of every section instead, *pageNumbers* (one per section, the
    About of the toolbox first and the terminology last, or 000 while they
    are not yet known, see WriteManualPdf).  Web links stay."""
    version = version or _METAFOCUS_VERSION
    date = date or _METAFOCUS_DATE
    sectionCount = len(toolAboutPages) + 2
    pageTexts = ([str(pageNumber) for pageNumber in pageNumbers] if pageNumbers is not None
                 else ["000"] * sectionCount)
    assert len(pageTexts) == sectionCount
    # The titles take most of the width, so that no tool name wraps.
    rows = ['<tr><td width="85%">About MetaFOCUS</td><td align="right">{}</td></tr>'.format(pageTexts[0])]
    sections = ['<div style="{}">{}</div>'.format(PDF_PAGE_BREAK_STYLE, toolboxAboutHtml.strip())]
    previousGroupName = None
    for pageIndex, (groupName, title, html) in enumerate(toolAboutPages, start=1):
        if groupName != previousGroupName:
            rows.append('<tr><td colspan="2"><b>{}</b></td></tr>'.format(groupName))
            previousGroupName = groupName
        rows.append('<tr><td width="85%">&nbsp;&nbsp;&nbsp;&nbsp;{}</td><td align="right">{}</td></tr>'.format(
            title, pageTexts[pageIndex]))
        sections.append('<div style="{}">{}</div>'.format(PDF_PAGE_BREAK_STYLE, html.strip()))
    rows.append('<tr><td width="85%">Terminology</td><td align="right">{}</td></tr>'.format(pageTexts[-1]))
    sections.append('<div style="{}">{}</div>'.format(PDF_PAGE_BREAK_STYLE, terminologyHtml.strip()))
    return ("<h1>MetaFOCUS {}: Manual and Terminology</h1>\n<p>{}</p>\n<p>Version {} ({})</p>\n"
            "<h3>Contents</h3>\n<table cellspacing=\"0\" cellpadding=\"2\" width=\"70%\">\n{}\n</table>\n{}").format(
                version, MANUAL_CONTENTS_SENTENCE, version, date, "\n".join(rows), "\n".join(sections))


def VersionForFileName(version):
    """A version with its dots replaced by hyphens, as in the names of the
    MetaFOCUS ZIP files."""
    return version.replace(".", "-")


def ManualPdfName(version):
    return "MetaFOCUS_{}_Manual_and_Terminology.pdf".format(VersionForFileName(version))


def TerminologyPdfName(version):
    return "MetaFOCUS_{}_Terminology.pdf".format(VersionForFileName(version))


def WithPdfExtension(path):
    """*path* with the extension .pdf when it has none."""
    return path if os.path.splitext(path)[1] else path + ".pdf"


def NewPdfWriter(path, title):
    """Qt's own PDF writer for the file *path*, A4 without margins of its
    own at 300 dpi, with *title* as the title of the PDF.  The page is set
    as a QPageLayout, because PySide2 5.15, which Metashape 2.3.2 bundles,
    has no QPdfWriter.setPageSize(QPageSize)."""
    pdfWriter = QtGui.QPdfWriter(path)
    pdfWriter.setTitle(title)
    pdfWriter.setCreator("MetaFOCUS {}".format(_METAFOCUS_VERSION))
    margins = QtCore.QMarginsF(PDF_WRITER_MARGIN_MILLIMETRES, PDF_WRITER_MARGIN_MILLIMETRES,
                               PDF_WRITER_MARGIN_MILLIMETRES, PDF_WRITER_MARGIN_MILLIMETRES)
    pdfWriter.setPageLayout(QtGui.QPageLayout(QtGui.QPageSize(QtGui.QPageSize.A4), QtGui.QPageLayout.Portrait,
                                              margins, QtGui.QPageLayout.Millimeter))
    pdfWriter.setResolution(300)
    return pdfWriter


def DefaultDotsPerInch():
    """(x, y) of the resolution in which Qt gives the lengths of a text
    document, such as its margins (Qt's qt_defaultDpiX and qt_defaultDpiY):
    that of the primary screen, rounded, or 96 without a screen."""
    screen = QtGui.QGuiApplication.primaryScreen()
    if screen is None:
        return 96, 96
    return int(round(screen.logicalDotsPerInchX())), int(round(screen.logicalDotsPerInchY()))


def PageMargins(pdfWriter):
    """(horizontal, vertical) margins of the text, PDF_PAGE_MARGIN_CENTIMETRES,
    in the units of the frame format of a text document."""
    dotsPerInchX, dotsPerInchY = DefaultDotsPerInch()
    return (int(PDF_PAGE_MARGIN_CENTIMETRES / 2.54 * dotsPerInchX),
            int(PDF_PAGE_MARGIN_CENTIMETRES / 2.54 * dotsPerInchY))


def LaidOutPdfDocument(html, pdfWriter):
    """A text document with *html*, laid out on the pages of *pdfWriter*
    as QTextDocument.print_() lays out a document without a page size of
    its own: on the device of the writer, with margins of 2 cm and the
    page size of the writer.  Every page of the document is then a page
    of the PDF (PrintLaidOutDocument), so that SectionStartPages gives the
    pages on which the sections are printed."""
    document = QtGui.QTextDocument()
    document.setHtml(html)
    document.documentLayout().setPaintDevice(pdfWriter)
    horizontalMargin, verticalMargin = PageMargins(pdfWriter)
    frameFormat = document.rootFrame().frameFormat()
    frameFormat.setLeftMargin(horizontalMargin)
    frameFormat.setRightMargin(horizontalMargin)
    frameFormat.setTopMargin(verticalMargin)
    frameFormat.setBottomMargin(verticalMargin)
    document.rootFrame().setFrameFormat(frameFormat)
    document.setPageSize(QtCore.QSizeF(pdfWriter.width(), pdfWriter.height()))
    return document


def SectionStartPages(document):
    """The page, counted from 1, on which every section of the laid-out
    *document* starts: the pages of its blocks that start a new page
    (PDF_PAGE_BREAK_STYLE), in their order."""
    pageHeight = document.pageSize().height()
    layout = document.documentLayout()
    startPages = []
    block = document.begin()
    while block.isValid():
        if int(block.blockFormat().pageBreakPolicy()) & int(QtGui.QTextFormat.PageBreak_AlwaysBefore):
            startPages.append(int(layout.blockBoundingRect(block).top() // pageHeight) + 1)
        block = block.next()
    return startPages


def PrintLaidOutDocument(document, pdfWriter):
    """Print every page of the laid-out *document* (LaidOutPdfDocument) to
    *pdfWriter*, with its number at the bottom right, as
    QTextDocument.print_() prints and numbers the pages of a document
    without a page size of its own.  Returns the number of pages.  Raises
    when the PDF file cannot be written."""
    painter = QtGui.QPainter(pdfWriter)
    if not painter.isActive():
        raise RuntimeError("the PDF file could not be opened for writing")
    pageWidth, pageHeight = document.pageSize().width(), document.pageSize().height()
    horizontalMargin, verticalMargin = PageMargins(pdfWriter)
    dotsPerInchX, dotsPerInchY = DefaultDotsPerInch()
    pageNumberFont = QtGui.QFont(document.defaultFont())
    ascent = QtGui.QFontMetrics(pageNumberFont, pdfWriter).ascent()
    pageNumberRight = pageWidth - horizontalMargin * pdfWriter.logicalDpiX() / dotsPerInchX
    pageNumberBaseline = (pageHeight - verticalMargin * pdfWriter.logicalDpiY() / dotsPerInchY + ascent
                          + 5 * pdfWriter.logicalDpiY() / 72.0)
    layout = document.documentLayout()
    pageCount = document.pageCount()
    for pageIndex in range(pageCount):
        if pageIndex > 0:
            pdfWriter.newPage()
        painter.save()
        painter.translate(0, -pageIndex * pageHeight)
        view = QtCore.QRectF(0, pageIndex * pageHeight, pageWidth, pageHeight)
        painter.setClipRect(view)
        context = QtGui.QAbstractTextDocumentLayout.PaintContext()
        context.clip = view
        context.palette.setColor(QtGui.QPalette.Text, QtCore.Qt.black)
        layout.draw(painter, context)
        painter.setClipping(False)
        painter.setFont(pageNumberFont)
        pageText = str(pageIndex + 1)
        painter.drawText(QtCore.QPointF(round(pageNumberRight - painter.fontMetrics().horizontalAdvance(pageText)),
                                        round(pageNumberBaseline + view.top())), pageText)
        painter.restore()
    painter.end()
    return pageCount


def SaveHtmlAsPdf(html, path, title):
    """Write *html* to the PDF file *path*, A4 with margins of 2 cm and
    numbered pages, with *title* as the title of the PDF, by Qt's own PDF
    writer (NewPdfWriter, LaidOutPdfDocument, and PrintLaidOutDocument).
    Returns the number of pages.  Raises when the file cannot be
    written."""
    pdfWriter = NewPdfWriter(path, title)
    return PrintLaidOutDocument(LaidOutPdfDocument(html, pdfWriter), pdfWriter)


def WriteManualPdf(toolboxAboutHtml, toolAboutPages, terminologyHtml, path, title, version=None, date=None):
    """Write Manual and Terminology to the PDF file *path*
    (ManualPdfHtml): the contents list first gets placeholders, then the
    pages on which the sections start in the laid-out document, until the
    pages that it gives are those on which the sections start (at most
    three layouts).  Returns these pages.  Raises when the file cannot be
    written."""
    pdfWriter = NewPdfWriter(path, title)
    pageNumbers = None
    for _ in range(3):
        document = LaidOutPdfDocument(ManualPdfHtml(toolboxAboutHtml, toolAboutPages, terminologyHtml,
                                                    pageNumbers, version, date), pdfWriter)
        startPages = SectionStartPages(document)
        if startPages == pageNumbers:
            break
        pageNumbers = startPages
    PrintLaidOutDocument(document, pdfWriter)
    return startPages


def SaveManualAsPdf(toolboxAboutHtml, path, title):
    """Write Manual and Terminology of the version in use, for the About
    of the toolbox that the loader passes, to the PDF file *path*."""
    from . import terminology
    return WriteManualPdf(toolboxAboutHtml, ToolAboutPages(), terminology._TERMINOLOGY_HTML, path, title)


def AskAndSavePdf(parent, html, defaultFileName, title, writePdf=None):
    """Ask for the PDF file (Save as PDF), write *html* to it, or call
    *writePdf* with the path and *title* when it is given, and name the
    file in the Console.  A failure is shown in a message and the Console.
    A file at the path with the extension .pdf that was added after the
    save dialog is replaced only after a question (ReplacesUnconfirmedFile).
    Returns the path written, or "" when nothing was written."""
    chosenPath, _ = QtWidgets.QFileDialog.getSaveFileName(parent, "Save as PDF", defaultFileName,
                                                          "PDF files (*.pdf);;All files (*)")
    if not chosenPath:
        return ""
    path = WithPdfExtension(chosenPath)
    if ReplacesUnconfirmedFile(chosenPath, path) and QtWidgets.QMessageBox.question(
            parent, title, ReplaceUnconfirmedFileQuestionText(path),
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No) != QtWidgets.QMessageBox.Yes:
        print("{}: {}".format(title, DeclinedReplacingText(path)))
        return ""
    try:
        if writePdf is not None:
            writePdf(path, title)
        else:
            SaveHtmlAsPdf(html, path, title)
    except Exception as saveError:
        message = "{}: the PDF file could not be written: {}".format(title, DescribeException(saveError))
        print(message)
        QtWidgets.QMessageBox.warning(parent, title, message)
        return ""
    print("{} saved as a PDF file: {}".format(title, path))
    return path


def CombinedManualHtml(toolboxAboutHtml):
    """The page of Manual and Terminology for the About of the toolbox
    that the loader passes."""
    from . import terminology
    return ManualHtml(toolboxAboutHtml, ToolAboutPages(), terminology._TERMINOLOGY_HTML)


def _OnSaveManualClicked(dialog, toolboxAboutHtml, *unusedArguments):
    """Save as PDF: the page for the PDF, without the links and anchors of
    the window (SaveManualAsPdf)."""
    AskAndSavePdf(dialog, None, ManualPdfName(_METAFOCUS_VERSION), "MetaFOCUS Manual and Terminology",
                  functools.partial(SaveManualAsPdf, toolboxAboutHtml))


def SaveTerminologyAsPdf(parent, *unusedArguments):
    """Save Terminology as PDF: the terminology alone, as a PDF file of
    its own."""
    from . import terminology
    AskAndSavePdf(parent, terminology._TERMINOLOGY_HTML, TerminologyPdfName(_METAFOCUS_VERSION),
                  "MetaFOCUS Terminology")


def PrepareManualBrowser(textBrowser, html):
    """Show *html* in *textBrowser*, with external links opened outside,
    and start its history at MANUAL_START_ANCHOR: QTextBrowser keeps no
    history for a page set with setHtml, so without this start Back
    would stay unavailable after the first link (found with the real Qt
    of Metashape)."""
    textBrowser.setOpenExternalLinks(True)
    textBrowser.setHtml(html)
    textBrowser.setSource(QtCore.QUrl("#" + MANUAL_START_ANCHOR))


def ShowManualAndTerminology(toolboxAboutHtml):
    """Open the window with every About section and the terminology, with
    Back, Save as PDF, and Save Terminology as PDF."""
    html = CombinedManualHtml(toolboxAboutHtml)
    dialog = QtWidgets.QDialog(QtWidgets.QApplication.activeWindow())
    dialog.setWindowTitle(_TOOL_NAME)
    dialog.setWindowFlags(dialog.windowFlags() & ~QtCore.Qt.WindowContextHelpButtonHint)
    dialog.setMinimumSize(720, 640)
    layout = QtWidgets.QVBoxLayout(dialog)
    textBrowser = QtWidgets.QTextBrowser()
    layout.addWidget(textBrowser)
    buttonRow = QtWidgets.QHBoxLayout()
    # Back returns from the entry of a term to the passage of the link.
    backButton = QtWidgets.QPushButton("Back")
    backButton.setToolTip("Return to the passage you came from, after following a link to the terminology or a "
                          "link of the contents.")
    backButton.setEnabled(False)
    backButton.clicked.connect(textBrowser.backward)
    textBrowser.backwardAvailable.connect(backButton.setEnabled)
    buttonRow.addWidget(backButton)
    saveButton = QtWidgets.QPushButton("Save as PDF…")
    saveButton.setToolTip("Save every About section and the terminology as one PDF file, each starting on a new "
                          "page.")
    saveButton.clicked.connect(functools.partial(_OnSaveManualClicked, dialog, toolboxAboutHtml))
    buttonRow.addWidget(saveButton)
    saveTerminologyButton = QtWidgets.QPushButton("Save Terminology as PDF…")
    saveTerminologyButton.setToolTip("Save the terminology alone as a PDF file of its own.")
    saveTerminologyButton.clicked.connect(functools.partial(SaveTerminologyAsPdf, dialog))
    buttonRow.addWidget(saveTerminologyButton)
    buttonRow.addStretch()
    closeButton = QtWidgets.QPushButton("Close")
    closeButton.clicked.connect(dialog.accept)
    buttonRow.addWidget(closeButton)
    layout.addLayout(buttonRow)
    PrepareManualBrowser(textBrowser, html)
    print("Manual and Terminology: {} About sections and the terminology shown.".format(
        1 + sum(len(sources) for _, sources in ABOUT_PAGE_SOURCES)))
    dialog.exec_()
