"""
Scale Bar A Priori Uncertainty
==============================

Part of MetaFOCUS.  Assigns a distance-dependent a priori standard
uncertainty to each reference distance that was measured with a tape
and entered in Metashape as a scale bar, written to the "Accuracy"
field of that Metashape scale bar, from the maximum permissible error
(MPE) of the tape class of OIML R 35-1.  A scale bar in the strict
sense is a physical bar, whereas Metashape calls the representation
of any known distance a scale bar, and the tool is named after these
Metashape objects.
Loaded by the MetaFOCUS loader and opened from MetaFOCUS > Control
Measurements and Residuals > Scale Bar A Priori Uncertainty.

Author:     Geert J. Verhoeven (ORCID: 0000-0003-4825-9604)
Contact:    geert.verhoeven@univie.ac.at
Version:    1.0.0 (2026-11-18)
Licence:    GPL-3.0-or-later WITH GPL-3.0-linking-exception
Requires:   Agisoft Metashape Professional 2.x
Developed for: Agisoft Metashape Professional 2.3.2 (tested offline, see README.md)

Background
----------
When reference distances are measured with a tape measure, the
maximum permissible error (MPE) depends on the accuracy class
of the tape (OIML R 35-1) and the measured distance.  For the
nominal length and any distance between two non-consecutive scale
marks (OIML R 35-1, clause 4.2.1):

    MPE (mm) = a + b × ⌈distance (m)⌉

where ⌈⌉ denotes rounding up to the next whole metre, and:

    Class I  :  a = 0.1 mm,  b = 0.1 mm/m
    Class II :  a = 0.3 mm,  b = 0.2 mm/m
    Class III:  a = 0.6 mm,  b = 0.4 mm/m

When a distance begins at an end surface of the tape (for example
its hook), so that its terminal scale interval is bounded by a
surface, the MPE is increased by c = 0.1, 0.2, or 0.3 mm for Class I,
II, or III
(Directive 2014/32/EU, Annex MI-008, Table 1). For a tape in service
the MPE is doubled (OIML R 35-1, clause 4.3). Both options are off
by default.

If there is an additional measurement uncertainty (for example from
reading the tape, placing it on the target centres, or the
uncertainty of a correction for sag or temperature), the assigned
standard uncertainty is:

    u_MPE (mm) = MPE / √3          (rectangular Type B, GUM 4.3.7)
    σ (mm)     = √(u_MPE² + additional²)

The MPE is a symmetric ± bound (a half-width), not a standard
deviation.  The rectangular Type B evaluation of the GUM
(JCGM 100:2008, clause 4.3.7) converts it before combination.
The additional term must itself already be a standard uncertainty.

This tool computes these standard uncertainties and writes them to
the "Accuracy" field of the Metashape scale bars.

Future work: distance-based cross-validation or conformity screening
--------------------------------------------------------------------
When a distance-based leave-one-out or conformity module is
built, comparing a distance's apparent error against its raw
MPE must be presented as a screening rule, not as a formal
conformity decision: the apparent error also contains
photogrammetric prediction error, and a formal decision needs
the machinery of JCGM 106:2012 (requirement, measurement
uncertainty, decision rule).  That module may additionally
report a standardised discrepancy, the apparent error divided
by the combined predictive uncertainty.  The screening
threshold uses the raw MPE, never MPE/√3.
"""

import math
import os
import Metashape
from PySide2 import QtWidgets, QtCore, QtGui

from .photogrammetryUtilities import DescribeException, FormatLengthWithDecimals, ReportSuppressedError


# ===================================================================
# Menu label: used for both adding and removing
# ===================================================================

_MENU_LABEL = "MetaFOCUS/Control Measurements and Residuals/Scale Bar A Priori Uncertainty"
_ICON_NAME = "scale_bar_a_priori_uncertainty_32.png"


# ===================================================================
# Tape class coefficients: OIML R 35-1 (2007), clauses 4.2.1 and
# 4.2.4, and Directive 2014/32/EU, Annex MI-008, Table 1
# ===================================================================

TAPE_CLASSES = {
    "Class I":   {"a": 0.1, "b": 0.1, "c": 0.1,
                  "description": "MPE = 0.1 mm + 0.1 mm per "
                                 "metre"},
    "Class II":  {"a": 0.3, "b": 0.2, "c": 0.2,
                  "description": "MPE = 0.3 mm + 0.2 mm per "
                                 "metre"},
    "Class III": {"a": 0.6, "b": 0.4, "c": 0.3,
                  "description": "MPE = 0.6 mm + 0.4 mm per "
                                 "metre"},
}


# ===================================================================
# Computation
# ===================================================================

def ComputeScaleBarAPrioriUncertainty(distanceMetres, tapeClassName,
                        additionalUncertaintyMillimetres,
                        fromEndSurface=False, inService=False):
    """Compute the a priori standard uncertainty of a taped reference
    distance from the tape class and the distance.

    Parameters
    ----------
    distanceMetres : float
        Measured distance in metres (must be > 0).
    tapeClassName : str
        One of "Class I", "Class II", "Class III".
    additionalUncertaintyMillimetres : float
        Extra standard uncertainty in mm (>= 0).
    fromEndSurface : bool
        The distance begins at an end surface of the tape (for
        example its hook): the MPE is increased by the class
        increment c (Directive
        2014/32/EU, Annex MI-008, Table 1).
    inService : bool
        Use the MPE in service, twice the MPE on initial
        verification (OIML R 35-1, clause 4.3).

    Returns
    -------
    mpeMillimetres : float
        Maximum permissible error in mm (a symmetric bound).
    standardUncertaintyMillimetres : float
        MPE converted to a standard uncertainty, MPE/√3
        (rectangular Type B evaluation, JCGM 100:2008 clause
        4.3.7), in mm.
    combinedStandardUncertaintyMillimetres : float
        Combined standard uncertainty in mm.
    combinedStandardUncertaintyMetres : float
        Combined standard uncertainty in metres.
    """
    coefficients = TAPE_CLASSES[tapeClassName]
    constantTermMillimetres = coefficients["a"]
    lengthCoefficientMillimetresPerMetre = coefficients["b"]

    roundedLengthMetres = math.ceil(distanceMetres)
    mpeMillimetres = constantTermMillimetres + lengthCoefficientMillimetresPerMetre * roundedLengthMetres
    if fromEndSurface:
        mpeMillimetres += coefficients["c"]
    if inService:
        mpeMillimetres *= 2.0
    standardUncertaintyMillimetres = mpeMillimetres / math.sqrt(3.0)
    combinedStandardUncertaintyMillimetres = math.sqrt(
        standardUncertaintyMillimetres ** 2 + additionalUncertaintyMillimetres ** 2)
    combinedStandardUncertaintyMetres = combinedStandardUncertaintyMillimetres / 1000.0

    return (mpeMillimetres, standardUncertaintyMillimetres, combinedStandardUncertaintyMillimetres,
            combinedStandardUncertaintyMetres)


def ComputeRelativeScaleUncertaintyPpm(distanceSigmaPairs):
    """A priori scale-only indicator, in ppm: the relative standard
    uncertainty of the scale estimated from the reference distances
    alone, with the shape of the network held fixed.

    Under the linearised model delta_d_i = d_i * delta_s + e_i, with
    mutually independent errors e_i of standard uncertainty sigma_i and
    all other geometry fixed, the least-squares estimate of the
    relative scale correction delta_s has the standard uncertainty

        u_rel = sqrt(1 / sum((d_i / sigma_i)**2))

    reported here in parts per million.  It is not the scale
    uncertainty of the whole adjustment.  There the shape q is
    estimated together with the scale, so that the marginal scale
    variance involves (N_ss - N_sq * inverse(N_qq) * N_qs)^-1, which is
    larger where the shape is weakly determined, and other scale
    information (control points, camera positions) can make it
    smaller.  Only a joint covariance of the adjustment gives that
    value.  With a covariance matrix C_d of the distances, the
    scale-only information would be d^T * inverse(C_d) * d instead of
    the sum.  Shared error components (a common realised tape-scale
    error, temperature, tension, shared coded targets) are outside
    this model.

    Parameters
    ----------
    distanceSigmaPairs : iterable of (float, float)
        Pairs of (reference distance in metres, assigned standard
        uncertainty in metres) for the enabled Metashape scale bars.

    Returns
    -------
    float or None
        Scale-only relative standard uncertainty in ppm, or None when
        the iterable is empty.
    """
    sumOfSquaredRatios = 0.0
    pairCount = 0
    for distanceMetres, sigmaMetres in distanceSigmaPairs:
        sumOfSquaredRatios += (distanceMetres / sigmaMetres) ** 2
        pairCount += 1
    if pairCount == 0:
        return None
    return math.sqrt(1.0 / sumOfSquaredRatios) * 1.0e6


def GetScaleBarDistance(scaleBar):
    """Get the reference distance of a Metashape scale bar in metres.
    None if invalid.  A Metashape scale bar without a reference
    distance is a normal case, not an error."""
    try:
        referenceDistance = scaleBar.reference.distance
        if referenceDistance is None:
            return None
        distanceMetres = float(referenceDistance)
        return distanceMetres if distanceMetres > 0 else None
    except Exception as suppressedError:
        ReportSuppressedError("scaleBarAPrioriUncertainty.GetScaleBarDistance", suppressedError)
        return None


def GetScaleBarCurrentAccuracy(scaleBar, chunkDefaultAccuracy=None):
    """Get the current value of the "Accuracy" field in metres.

    If the Metashape scale bar has an individually set value, return
    that.
    Otherwise, fall back to the chunk-level default
    (chunk.scalebar_accuracy), which is the value set in
    Metashape's Reference Settings dialog.
    """
    try:
        value = scaleBar.reference.accuracy
        if value is not None:
            return float(value)
    except Exception as suppressedError:
        ReportSuppressedError("scaleBarAPrioriUncertainty.GetScaleBarCurrentAccuracy", suppressedError)
    # Fall back to the chunk-level default.
    if chunkDefaultAccuracy is not None:
        return float(chunkDefaultAccuracy)
    return None


# ===================================================================
# GUI: Preview table
# ===================================================================

class SortablePreviewItem(QtWidgets.QTableWidgetItem):
    """Table item that sorts numerically when the value is a number."""

    def __init__(self, displayText, sortValue=None):
        super().__init__(displayText)
        if sortValue is not None:
            self._sortValue = sortValue
        else:
            # Try to parse as float for numeric sorting.
            try:
                self._sortValue = float(displayText)
            except (ValueError, TypeError):
                self._sortValue = displayText

    def __lt__(self, other):
        if isinstance(other, SortablePreviewItem):
            try:
                return self._sortValue < other._sortValue
            except TypeError:
                return str(self._sortValue) < str(other._sortValue)
        return super().__lt__(other)


class PreviewTableDialog(QtWidgets.QDialog):
    """Shows the computed standard uncertainties in a table before
    they are applied."""

    def __init__(self, previewRows, tapeClassName, additionalMillimetres,
                 parent=None, optionsDescription=""):
        super().__init__(parent)
        self.setWindowTitle(
            "Preview: Scale Bar A Priori Uncertainty")
        self.setWindowFlags(
            self.windowFlags()
            & ~QtCore.Qt.WindowContextHelpButtonHint)
        self.resize(780, 450)

        layout = QtWidgets.QVBoxLayout(self)

        validCount = sum(
            1 for row in previewRows if row[-1] != "Skipped")
        summaryLabel = QtWidgets.QLabel(
            "Tape class: <b>{}</b> &nbsp;|&nbsp; "
            "Additional standard uncertainty: <b>{:.3f} mm</b> &nbsp;|&nbsp; "
            "Metashape scale bars to update: <b>{}</b>{}".format(
                tapeClassName, additionalMillimetres, validCount,
                optionsDescription))
        summaryLabel.setTextFormat(QtCore.Qt.RichText)
        layout.addWidget(summaryLabel)

        columnHeaders = [
            "Metashape scale bar", "Distance (m)", "\u2308Distance\u2309 (m)",
            "MPE (mm)", "MPE/\u221a3 (mm)", "Additional (mm)",
            "\u03c3 (mm)",
            "New \u201cAccuracy\u201d (m)", "Current \u201cAccuracy\u201d (m)", "Status"]

        table = QtWidgets.QTableWidget(
            len(previewRows), len(columnHeaders))
        table.setHorizontalHeaderLabels(columnHeaders)
        table.setAlternatingRowColors(True)
        table.setEditTriggers(
            QtWidgets.QAbstractItemView.NoEditTriggers)
        table.setSelectionMode(
            QtWidgets.QAbstractItemView.NoSelection)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSectionResizeMode(
            QtWidgets.QHeaderView.ResizeToContents)

        for rowIndex, row in enumerate(previewRows):
            for columnIndex, value in enumerate(row):
                item = SortablePreviewItem(str(value))
                item.setTextAlignment(QtCore.Qt.AlignCenter)
                if row[-1] == "Skipped":
                    item.setForeground(QtGui.QColor(150, 150, 150))
                table.setItem(rowIndex, columnIndex, item)

        table.setSortingEnabled(True)
        layout.addWidget(table)

        buttonRow = QtWidgets.QHBoxLayout()
        applyButton = QtWidgets.QPushButton("Apply to Metashape")
        applyButton.setDefault(True)
        cancelButton = QtWidgets.QPushButton("Cancel")
        buttonRow.addStretch()
        buttonRow.addWidget(applyButton)
        buttonRow.addWidget(cancelButton)
        layout.addLayout(buttonRow)

        applyButton.clicked.connect(self.accept)
        cancelButton.clicked.connect(self.reject)


# ===================================================================
# GUI: Main dialog
# ===================================================================

_ABOUT_HTML = """
<h2>Scale Bar A Priori Uncertainty</h2>
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

<h3>What this tool does</h3>
<p>The tool is meant for <b>reference distances</b> measured with a tape, usually between two
targets, and entered in Metashape as scale bars (hereafter Metashape scale bars). Strictly speaking,
a scale bar is a physical bar with targets at a calibrated distance. The Metashape 2.3 manual, by
contrast, calls a scale bar &ldquo;the representation of any known distance within the scene&rdquo;.
Still, the tool keeps the name Scale Bar A Priori Uncertainty because it writes to these
Metashape scale bars. MetaFOCUS &gt; Manual and Terminology defines the terms.</p>
<p>It assigns a distance-dependent <b>a priori standard
uncertainty</b> to each such Metashape scale bar, derived from the
maximum permissible error (MPE) that OIML R 35-1 specifies for the
accuracy class of the tape measure.</p>
<p>The tool derives the uncertainties from the specification of the accuracy class of the tape
measure. It neither calibrates nor verifies the tape, and it is not an acceptance test of the
photogrammetric measuring system. Other instruments, such as a steel rule specified by DIN 866 or a
scale bar with a calibration certificate, are outside its tape model. For such a bar, the expanded
uncertainty of the certificate divided by its coverage factor gives the standard uncertainty of the
calibrated distance (JCGM 100:2008, 4.3.3). This standard uncertainty applies to the reference
distance only under the conditions of the calibration. Further contributions from the use of the
bar, such as the temperature or the placement of the targets, are combined with it before it is
entered.</p>

<p>The MPE is a symmetric bound (a half-width) that a specification
permits, not a standard
deviation.  Before combination and assignment it is therefore
converted to a standard uncertainty by the rectangular Type B
evaluation of the Guide to the Expression of Uncertainty in
Measurement (GUM): <b>u = MPE/&radic;3</b>
(JCGM 100:2008, clause 4.3.7). This evaluation assumes that the tape is
used under its rated operating conditions and that nothing more is known
about its error within the bound, as for a tape without a calibration
certificate.</p>

<p>The result is written in metres to the &lsquo;Accuracy&rsquo; field of each Metashape scale bar (<code>reference.accuracy</code>).
Metashape uses this value to weight the reference distance during optimisation. Following standard
least-squares practice, the tool reads it as the a priori standard deviation &sigma; of the
reference distance. This standard deviation enters the adjustment through the weight <i>w</i>&nbsp;=&nbsp;1/&sigma;&sup2;,
so a reference distance with a smaller &sigma; receives a higher weight.</p>

<h3>Usage</h3>
<p>Choose the tape class, the additional standard uncertainty, and the options, then click <b>Preview
and apply&hellip;</b>. Before anything is written, the preview lists every Metashape scale bar with
its distance, MPE, MPE/&radic;3, and new standard uncertainty &sigma;. <b>Apply to Metashape</b>
then writes these values to the &lsquo;Accuracy&rsquo; fields. Metashape scale bars without a
reference distance are skipped and keep their value. With <i>Only update enabled Metashape scale
bars</i> ticked, disabled ones are left unchanged. The Console lists every Metashape scale bar and
reports an a priori scale-only indicator of the enabled reference distances (see below). The dialog,
the preview, and the Console show distances in metres with four decimals and standard uncertainties
in metres with six, or with four significant digits where these give fewer. The computed values in
millimetres have three decimals, or four significant digits where these give fewer, and the
additional standard uncertainty is shown as entered.</p>

<h3>Tape classes (OIML R 35-1, Directive 2014/32/EU)</h3>
<p>The OIML International Recommendation R 35-1 defines three accuracy classes for tape measures.
The MPE depends on the class and the measured length:</p>

<p><b>MPE (mm) = a + b &times; &lceil;L&rceil;</b></p>

<p>where &lceil;L&rceil; is the measured distance rounded
<b>up</b> to the next whole metre, and:</p>

<table border="1" cellpadding="4" cellspacing="0">
<tr><th>Class</th><th>a (mm)</th><th>b (mm/m)</th><th>c (mm)</th></tr>
<tr><td>I</td><td>0.1</td><td>0.1</td><td>0.1</td></tr>
<tr><td>II</td><td>0.3</td><td>0.2</td><td>0.2</td></tr>
<tr><td>III</td><td>0.6</td><td>0.4</td><td>0.3</td></tr>
</table>
<p>The class is the one marked on the tape (OIML R 35-1, clause 10.1) and given in its
specification. It is not a property of the material. Fibreglass tapes, for example, may conform to
any of the three classes (clauses 17.3 and 25.4), whereas folding and telescopic measures are
generally of Class II or III (clauses 18.4 and 19.4). A class marking alone does not establish that
a particular tape meets its class: that follows from its verification history.</p>

<p>The MPE also holds only under the rated operating conditions. Clause 4.4.1, as amended in 2014,
sets the temperature limits at &plusmn;8&nbsp;&deg;C of the reference temperature or of the
temperature indicated on the tape. The clause disregards these limits when a thermal expansion
coefficient is marked on the tape or the tape carries temperature correction information. The change
of length at the working temperature can then be calculated. Where a tension is marked on the tape,
the MPE holds under that tension. The tool applies no temperature correction: correct a significant
thermal expansion of the tape before entering the distance, and include the standard uncertainty of
that correction in the additional standard uncertainty (see below).</p>

<h3>Distances from an end surface, and tapes in service</h3>
<p>The formula applies to the nominal length and to any distance
between two non-consecutive scale marks (OIML R&nbsp;35-1, clause
4.2.1). A distance may
begin at an end surface of the tape, for example its hook.  The
terminal scale interval is then bounded by a surface (OIML R&nbsp;35-1,
clauses 2.2.1, 2.2.3, and 4.2.4).  For such a distance, Directive 2014/32/EU (Annex MI-008,
Table 1) increases the MPE by the increment c of the class in the
table. Tick <i>Distances measured from an end surface</i> to add it.
The coefficients are the maximum permissible errors on initial
verification. For a tape in service, the maximum permissible error
in service is twice that error (OIML R&nbsp;35-1, clause 4.3), so tick
<i>Tape in service</i> to double the MPE, including any end
increment. Both options are off by default, and both can only
increase the assigned uncertainty.</p>

<h3>Additional measurement uncertainty</h3>
<p>In practice, the uncertainty of a taped reference distance is larger than the MPE-derived
component alone. The &lsquo;additional uncertainty&rsquo; parameter carries the combined <b>standard
uncertainty</b> of the error sources that are not already represented by the instrument (MPE) term.
Express each source as a standard uncertainty of the distance first, multiplied by its sensitivity
coefficient when the source acts on the distance through another quantity, such as the temperature.
A &plusmn; bound is divided by &radic;3 when a rectangular distribution is justified (i.e. when no
value within the limits is more probable than another), and by &radic;6 when a triangular
distribution fits better. Then combine the sources by root sum of squares (RSS) before entering the
result. RSS assumes independent sources: correlated sources need their covariance (JCGM 100:2008,
5.2.2). Count each effect once, for example the reading either by its rounding or by the scatter of
repeated readings, not by both:</p>
<ul>
<li><b>Reading the tape:</b> rounding one reading to the nearest scale mark
of a 1&nbsp;mm scale interval contributes a standard uncertainty of
1/&radic;12 &asymp; 0.29&nbsp;mm.  This value holds if the rounding error is equally
likely anywhere within &plusmn;0.5 mm (JCGM 100:2008, F.2.2.1).
Interpolating to half a scale interval contributes about 0.14&nbsp;mm. This is the
rounding alone, not the whole uncertainty of a reading.  A distance
between two scale marks involves two readings, so both contributions
and any correlation between them count: two independent readings to
the nearest millimetre give about 0.41&nbsp;mm.  The reading
increment must be justified from the scale and the viewing
conditions.</li>
<li><b>Target centres:</b> in photogrammetric practice, reference
distances are measured between the centres of coded targets or
markers.  Placing the tape on each centre adds an uncertainty that
depends on the targets, the tape, and the procedure, and it is to be
evaluated for the actual set-up.</li>
<li><b>Systematic effects such as sag and temperature:</b> correct a
known significant effect first, for example the sag of an unsupported
tape.  The reading then exceeds the straight-line distance, so the
sag correction is negative.  Another example is the thermal expansion of the tape away
from its reference temperature.  Enter only the standard uncertainty
of the correction, propagated through its sensitivity
coefficient.</li>
<li><b>Operator technique:</b> pulling tension, alignment of the tape
with the line between the two targets, and parallax when reading the
tape.</li>
</ul>
<p>The additional standard uncertainty is combined with the
converted MPE term by RSS, assuming the error sources are
independent:</p>
<p><b>&sigma; (mm) = &radic;((MPE/&radic;3)&sup2; +
additional&sup2;)</b></p>
<p>The value depends on the tape, the targets, and the procedure, so
it is to be evaluated for each set-up rather than taken from a
typical range.</p>

<h3>Example</h3>
<p>A 3.75 m reference distance measured with a Class II tape and
1.0 mm additional standard uncertainty:</p>
<p>&lceil;3.75&rceil; = 4 m<br>
MPE = 0.3 + 0.2 &times; 4 = <b>1.1 mm</b><br>
u<sub>MPE</sub> = 1.1/&radic;3 = <b>0.635 mm</b><br>
&sigma; = &radic;(0.635&sup2; + 1.0&sup2;) = <b>1.185 mm</b>
= 0.001185 m</p>
<p>With the additional field at its default 0,
&sigma; = MPE/&radic;3 = 0.635 mm = 0.000635 m
(the MPE-only prior).</p>

<h3>Note on the &lsquo;Accuracy&rsquo; field in Metashape</h3>
<p>Metashape expects the value of the &lsquo;Accuracy&rsquo; field of a Metashape scale bar in <b>metres</b>.
It is stored in <code>scalebar.reference.accuracy</code> and weights the distance constraint during
the bundle adjustment: a smaller value gives the distance a higher weight and more influence on the
solution.</p>

<p>These values, one per Metashape scale bar, treat the reference distances as mutually independent.
Error components shared by all distances (a common realised tape-scale error, temperature, and
tension) or by distances sharing a coded target (centre-localisation error) cannot be expressed in
the single field of each Metashape scale bar. The weighting is therefore a diagonal approximation to
the full covariance of the distance set, and a large number of reference distances formed from a
modest number of targets carries less independent scale information than its count suggests.</p>

<h3>Scale-only indicator in the Console</h3>
<p>For the enabled reference distances d<sub>i</sub> with the assigned
standard uncertainties &sigma;<sub>i</sub>, the Console reports</p>
<p><b>u<sub>rel</sub> = [&Sigma; (d<sub>i</sub>/&sigma;<sub>i</sub>)&sup2;]<sup>&minus;1/2</sup></b>,
in parts per million.</p>
<p>This is the relative standard uncertainty of the scale estimated from these distances alone, with
the shape of the network held fixed and the distances treated as independent. It is an a priori
indicator of the scale information that the distances carry under their assigned uncertainties. It
is not the scale uncertainty of the whole adjustment. There, the shape is estimated together with
the scale, which raises the scale uncertainty where the shape is weakly determined. Other scale
information, such as control points or camera positions, can lower it. Only a joint covariance of
the adjustment gives that value. For correlated distances with the covariance matrix C<sub>d</sub>,
the scale-only information would be d<sup>T</sup>C<sub>d</sub><sup>&minus;1</sup>d rather than the
sum of the squared ratios.</p>

<h3>A note on terminology</h3>
<p>What Metashape calls &lsquo;Accuracy&rsquo; is read here as the <b>a priori standard deviation (&sigma;)</b>
of the distance observation, which enters the bundle adjustment through the observation weight <i>w</i>&nbsp;=&nbsp;1/&sigma;&sup2;.
A smaller &sigma; means a higher weight and more influence on the solution. The Metashape 2.3 manual
states that the adjustment is based on all available measurements and their corresponding
accuracies, the distances of the Metashape scale bars included. However, the manual does not define
the quantity (it speaks of the assumed measurement accuracy), so this reading is an interpretation
that follows standard least-squares practice.</p>
<p>In modern metrology (International Vocabulary of Metrology, VIM, JCGM 200:2012), the term <b>measurement
accuracy</b> (VIM 2.13) refers to the closeness of agreement between a measured value and a true
value. It is related to <b>trueness</b> (VIM 2.14: closeness of the average of an infinite number of
replicate measured values to a reference value, inversely related to systematic measurement error).
Measurement accuracy is also related to <b>precision</b> (VIM 2.15: closeness of agreement between
measured values obtained by replicate measurements under specified conditions, usually expressed by
a measure of imprecision such as a standard deviation). ISO 5725-1:2023 calls accuracy a combination
of trueness and precision (3.5, Note 3) and defines precision under stipulated conditions (3.12). It
also defines accuracy and trueness against the true value, for which the accepted reference value is
substituted in practice (3.5 and 3.6). Note that measurement accuracy is a concept, not a quantity,
and is given no numerical quantity value (VIM 2.13, Note 1). The previous edition of the VIM,
reproduced in JCGM 100:2008 (B.2.14, Note 1), called it a qualitative concept.</p>
<p>What the field holds is therefore neither accuracy nor
trueness.  It is read as the <b>a priori standard deviation</b> that
the stochastic model of the adjustment uses to weight the
observation.  This tool assigns to it a <b>standard uncertainty</b>
(VIM 2.30) evaluated from the tape specification and the additional
measurement contributions.  This standard uncertainty is a standard deviation that characterises
the dispersion of the values being attributed to the reference
distance, based on the information used (VIM 2.26).  It includes the
modelled effects of incomplete knowledge, such as an unknown error of
the tape scale that stays the same when the distance is read again.
The standard uncertainty is therefore not necessarily the standard deviation that repeated
readings of the same distance would show (JCGM 100:2008, 3.3.3 to
3.3.5).  Using it as the a priori standard deviation is a modelling
assignment, not a demonstration of repeatability.</p>
<p>Metashape&rsquo;s label &lsquo;Accuracy&rsquo; is therefore a misnomer, for two reasons. First,
the field holds a standard uncertainty, which can also include systematic effects, but the
adjustment treats it as a measure of precision, not of accuracy. Second, accuracy has no numerical
value (VIM 2.13, Note 1). The misnomer is harmless once understood, and this tool uses the word only
as the name of that field. The value it computes is an a priori standard uncertainty, obtained from
the MPE bound by the rectangular Type B evaluation of JCGM 100:2008, clause 4.3.7, and combined with
the additional standard uncertainty.</p>

<h3>References</h3>
<p>Agisoft LLC (2026). <i>Agisoft Metashape User Manual: Professional
Edition, Version 2.3.</i>
<a href="https://www.agisoft.com/pdf/metashape-pro_2_3_en.pdf">
https://www.agisoft.com/pdf/metashape-pro_2_3_en.pdf</a></p>
<p>Deutsches Institut f&uuml;r Normung (2006). <i>DIN 866:2006-12,
Geometrical product specifications (GPS) &ndash; Graduated rules,
rules for manufacturing purposes &ndash; Design, requirements.</i>
Beuth Verlag, Berlin.</p>
<p>European Union (2014). Directive 2014/32/EU of the European
Parliament and of the Council of 26 February 2014 on the harmonisation
of the laws of the Member States relating to the making available on
the market of measuring instruments (recast). <i>Official Journal of
the European Union</i> L 96, 149&ndash;250.</p>
<p>International Organization for Standardization (2023). <i>ISO
5725-1:2023, Accuracy (trueness and precision) of measurement methods
and results, Part 1: General principles and definitions.</i> ISO,
Geneva.</p>
<p>International Organization of Legal Metrology (2007, Amendment
2014). <i>OIML R&nbsp;35-1, Material measures of length for general
use, Part 1: Metrological and technical requirements</i>, Edition 2007
(E), including Amendment 2014. OIML, Paris.</p>
<p>Joint Committee for Guides in Metrology (2008). <i>Evaluation of
measurement data: Guide to the expression of uncertainty in
measurement</i> (JCGM 100:2008).</p>
<p>Joint Committee for Guides in Metrology (2012). <i>International
vocabulary of metrology: Basic and general concepts and associated
terms (VIM)</i>, 3rd edition (JCGM 200:2012).</p>
"""


class ScaleBarAPrioriUncertaintyDialog(QtWidgets.QDialog):
    """Main dialog of Scale Bar A Priori Uncertainty."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(
            "Scale Bar A Priori Uncertainty")
        self.setWindowFlags(
            self.windowFlags()
            & ~QtCore.Qt.WindowContextHelpButtonHint)
        self.setMinimumWidth(540)
        self._BuildUi()

    def _BuildUi(self):
        mainLayout = QtWidgets.QVBoxLayout(self)

        chunk = Metashape.app.document.chunk

        # Info.
        introductionLabel = QtWidgets.QLabel(
            "Assigns a distance-dependent a priori standard "
            "uncertainty to each taped reference distance, written to "
            "the \u201cAccuracy\u201d field of its Metashape scale bar, "
            "from the maximum permissible "
            "error (MPE) of the OIML R 35-1 tape class and an optional "
            "additional standard uncertainty.")
        introductionLabel.setWordWrap(True)
        mainLayout.addWidget(introductionLabel)
        mainLayout.addSpacing(8)

        # Metashape scale bar count.
        if chunk and chunk.scalebars:
            self.totalCount = len(chunk.scalebars)
            validCount = sum(
                1 for scaleBar in chunk.scalebars
                if GetScaleBarDistance(scaleBar) is not None)
            enabledCount = sum(
                1 for scaleBar in chunk.scalebars
                if getattr(scaleBar.reference, 'enabled', True))
        else:
            self.totalCount = 0
            validCount = 0
            enabledCount = 0

        countLabel = QtWidgets.QLabel(
            "Active chunk: <b>{}</b><br>"
            "Metashape scale bars: <b>{}</b> total, "
            "<b>{}</b> with a valid distance, "
            "<b>{}</b> enabled<br>"
            "Default \u201cScale bar accuracy\u201d (Reference Settings): "
            "<b>{}</b>".format(
                chunk.label if chunk else "(none)",
                self.totalCount, validCount, enabledCount,
                "{} m".format(FormatLengthWithDecimals(chunk.scalebar_accuracy, 6))
                if chunk and hasattr(chunk, 'scalebar_accuracy')
                   and chunk.scalebar_accuracy is not None
                else "not set"))
        countLabel.setTextFormat(QtCore.Qt.RichText)
        mainLayout.addWidget(countLabel)
        mainLayout.addSpacing(8)

        if self.totalCount == 0:
            warningLabel = QtWidgets.QLabel(
                "\u26a0\ufe0f No Metashape scale bars found in the "
                "active chunk. Add them first.")
            warningLabel.setStyleSheet(
                "color: #c00; font-weight: bold;")
            mainLayout.addWidget(warningLabel)

        # Form.
        formLayout = QtWidgets.QFormLayout()

        self.tapeClassComboBox = QtWidgets.QComboBox()
        for className, classDefinition in TAPE_CLASSES.items():
            self.tapeClassComboBox.addItem(
                "{}: {}".format(
                    className, classDefinition["description"]),
                className)
        formLayout.addRow("Tape class:", self.tapeClassComboBox)

        self.additionalUncertaintySpinBox = QtWidgets.QDoubleSpinBox()
        self.additionalUncertaintySpinBox.setDecimals(3)
        self.additionalUncertaintySpinBox.setRange(0.0, 100.0)
        self.additionalUncertaintySpinBox.setSingleStep(0.1)
        self.additionalUncertaintySpinBox.setValue(0.0)
        self.additionalUncertaintySpinBox.setSuffix(" mm")
        self.additionalUncertaintySpinBox.setToolTip(
            "Additional standard uncertainty of the sources not "
            "already represented by the tape's maximum permissible "
            "error (MPE).\nExpress each source as a standard "
            "uncertainty first: divide the half-width of a \u00b1 "
            "bound by \u221a3 when a rectangular distribution is "
            "justified, or by \u221a6 for a triangular one.\nThen "
            "combine the sources by root-sum-of-squares (RSS) before "
            "entering.\nExamples:\n"
            "  \u2022 One reading to the nearest scale mark (1 mm "
            "scale interval): "
            "1/\u221a12 \u2248 0.29 mm (JCGM 100:2008 resolution "
            "treatment)\n"
            "  \u2022 Two independent readings, at both ends of a "
            "distance: \u2248 0.41 mm\n"
            "  \u2022 Interpolating to half a scale interval: "
            "\u2248 0.14 mm per reading\n"
            "  \u2022 Placement on the target centres, the "
            "uncertainty of sag and temperature corrections, operator "
            "technique\n\n"
            "Set to 0 to assign the MPE-only prior "
            "(\u03c3 = MPE/\u221a3).")
        formLayout.addRow("Additional uncertainty:",
                          self.additionalUncertaintySpinBox)

        self.endSurfaceCheckBox = QtWidgets.QCheckBox(
            "Distances measured from an end surface (adds the end "
            "increment c)")
        self.endSurfaceCheckBox.setChecked(False)
        self.endSurfaceCheckBox.setToolTip(
            "Tick when each distance begins at an end surface of the tape,\n"
            "for example its hook, so that the terminal scale interval is\n"
            "bounded by a surface (OIML R 35-1, clause 4.2.4).\n"
            "Directive 2014/32/EU (Annex MI-008, Table 1) then increases the\n"
            "MPE by c = 0.1, 0.2, or 0.3 mm for Class I, II, or III.")
        formLayout.addRow("", self.endSurfaceCheckBox)

        self.inServiceCheckBox = QtWidgets.QCheckBox(
            "Tape in service (doubles the MPE)")
        self.inServiceCheckBox.setChecked(False)
        self.inServiceCheckBox.setToolTip(
            "Tick for a tape in service rather than at initial verification.\n"
            "OIML R 35-1 (clause 4.3) sets the maximum permissible error in\n"
            "service at twice the maximum permissible error on initial\n"
            "verification, so the MPE, including any end increment, is\n"
            "doubled.")
        formLayout.addRow("", self.inServiceCheckBox)

        self.onlyEnabledCheckBox = QtWidgets.QCheckBox(
            "Only update enabled Metashape scale bars")
        self.onlyEnabledCheckBox.setChecked(False)
        formLayout.addRow("", self.onlyEnabledCheckBox)

        mainLayout.addLayout(formLayout)
        mainLayout.addSpacing(8)

        # Formula display.
        formulaLabel = QtWidgets.QLabel(
            "MPE (mm) = a + b \u00d7 "
            "\u2308distance (m)\u2309, plus c from an end surface, "
            "doubled in service<br>"
            "\u03c3 (mm) = "
            "\u221a((MPE/\u221a3)\u00b2 + additional\u00b2)")
        formulaLabel.setTextFormat(QtCore.Qt.RichText)
        formulaLabel.setStyleSheet(
            "color: #555; font-size: 9pt;")
        mainLayout.addWidget(formulaLabel)
        mainLayout.addSpacing(8)

        # Buttons.
        buttonRow = QtWidgets.QHBoxLayout()
        aboutButton = QtWidgets.QPushButton("About")
        previewButton = QtWidgets.QPushButton(
            "Preview and apply\u2026")
        previewButton.setDefault(True)
        previewButton.setEnabled(self.totalCount > 0)
        closeButton = QtWidgets.QPushButton("Close")

        buttonRow.addWidget(aboutButton)
        buttonRow.addStretch()
        buttonRow.addWidget(previewButton)
        buttonRow.addWidget(closeButton)
        mainLayout.addLayout(buttonRow)

        aboutButton.clicked.connect(self._OnAbout)
        previewButton.clicked.connect(self._OnPreview)
        closeButton.clicked.connect(self.reject)

    def _OnAbout(self):
        aboutDialog = QtWidgets.QDialog(self)
        aboutDialog.setWindowTitle(
            "About: Scale Bar A Priori Uncertainty")
        aboutDialog.setWindowFlags(
            aboutDialog.windowFlags()
            & ~QtCore.Qt.WindowContextHelpButtonHint)
        aboutDialog.resize(540, 560)
        aboutLayout = QtWidgets.QVBoxLayout(aboutDialog)
        textBrowser = QtWidgets.QTextBrowser()
        textBrowser.setOpenExternalLinks(True)
        textBrowser.setHtml(_ABOUT_HTML)
        aboutLayout.addWidget(textBrowser)
        closeButton = QtWidgets.QPushButton("Close")
        closeButton.clicked.connect(aboutDialog.close)
        buttonRow = QtWidgets.QHBoxLayout()
        buttonRow.addStretch()
        buttonRow.addWidget(closeButton)
        aboutLayout.addLayout(buttonRow)
        aboutDialog.exec_()

    def _OnPreview(self):
        chunk = Metashape.app.document.chunk
        if not chunk or not chunk.scalebars:
            QtWidgets.QMessageBox.warning(
                self, "Scale Bar A Priori Uncertainty",
                "No Metashape scale bars in the active chunk.")
            return

        tapeClassName = self.tapeClassComboBox.currentData()
        additionalMillimetres = self.additionalUncertaintySpinBox.value()
        onlyEnabled = self.onlyEnabledCheckBox.isChecked()
        fromEndSurface = self.endSurfaceCheckBox.isChecked()
        inService = self.inServiceCheckBox.isChecked()
        optionNotes = []
        if fromEndSurface:
            optionNotes.append("distances measured from an end surface "
                               "(end increment c added)")
        if inService:
            optionNotes.append("tape in service (MPE doubled)")
        optionsDescription = ("<br>Options: " + ", ".join(optionNotes)
                              if optionNotes else "")

        # Get the chunk-level default accuracy (set in Reference
        # Settings).  Used as fallback when individual Metashape scale bars
        # do not have their own accuracy value.
        chunkDefaultAccuracy = getattr(
            chunk, 'scalebar_accuracy', None)

        previewRows = []
        for scaleBar in chunk.scalebars:
            if onlyEnabled:
                isEnabled = getattr(
                    scaleBar.reference, 'enabled', True)
                if not isEnabled:
                    continue

            distance = GetScaleBarDistance(scaleBar)
            currentAccuracy = GetScaleBarCurrentAccuracy(
                scaleBar, chunkDefaultAccuracy)
            currentAccuracyText = (FormatLengthWithDecimals(currentAccuracy, 6)
                                   if currentAccuracy is not None
                                   else "\u2013")

            if distance is None:
                previewRows.append([
                    scaleBar.label,
                    "\u2013", "\u2013", "\u2013", "\u2013",
                    "\u2013", "\u2013", "\u2013", currentAccuracyText,
                    "Skipped"])
                continue

            (mpeMillimetres, standardUncertaintyMillimetres, combinedStandardUncertaintyMillimetres,
             combinedStandardUncertaintyMetres) = (
                ComputeScaleBarAPrioriUncertainty(
                    distance, tapeClassName, additionalMillimetres,
                    fromEndSurface=fromEndSurface, inService=inService))

            previewRows.append([
                scaleBar.label,
                FormatLengthWithDecimals(distance, 4),
                "{}".format(math.ceil(distance)),
                # The computed millimetres with three decimals or at least
                # four significant digits, as the metres.  The additional
                # standard uncertainty is shown as entered.
                FormatLengthWithDecimals(mpeMillimetres, 3),
                FormatLengthWithDecimals(standardUncertaintyMillimetres, 3),
                "{:.3f}".format(additionalMillimetres),
                FormatLengthWithDecimals(combinedStandardUncertaintyMillimetres, 3),
                FormatLengthWithDecimals(combinedStandardUncertaintyMetres, 6),
                currentAccuracyText,
                "Will update"])

        if not previewRows:
            QtWidgets.QMessageBox.information(
                self, "Scale Bar A Priori Uncertainty",
                "No Metashape scale bars match the current filter.")
            return

        previewDialog = PreviewTableDialog(
            previewRows, tapeClassName, additionalMillimetres, self,
            optionsDescription=optionsDescription)
        if previewDialog.exec_() == QtWidgets.QDialog.Accepted:
            self._ApplyAccuracies(
                tapeClassName, additionalMillimetres, onlyEnabled,
                fromEndSurface, inService)

    def _ApplyAccuracies(self, tapeClassName, additionalMillimetres,
                         onlyEnabled, fromEndSurface=False, inService=False):
        """Write the computed standard uncertainties to the "Accuracy"
        fields of the Metashape scale bars."""
        chunk = Metashape.app.document.chunk
        if not chunk or not chunk.scalebars:
            return

        updatedCount = 0
        skippedCount = 0
        enabledDistanceSigmaPairs = []

        print("")
        print("=" * 60)
        print("Scale Bar A Priori Uncertainty")
        print("=" * 60)
        print("Chunk: {}".format(chunk.label))
        print("Tape class: {}".format(tapeClassName))
        print("Additional uncertainty: {:.3f} mm".format(
            additionalMillimetres))
        print("Distances measured from an end surface (end increment "
              "c added): {}".format(
            "yes" if fromEndSurface else "no"))
        print("Tape in service (MPE doubled): {}".format(
            "yes" if inService else "no"))
        print("")

        for scaleBar in chunk.scalebars:
            if onlyEnabled:
                isEnabled = getattr(
                    scaleBar.reference, 'enabled', True)
                if not isEnabled:
                    skippedCount += 1
                    continue

            distance = GetScaleBarDistance(scaleBar)
            if distance is None:
                skippedCount += 1
                print("  {}: skipped (no valid distance)".format(
                    scaleBar.label))
                continue

            (mpeMillimetres, standardUncertaintyMillimetres, combinedStandardUncertaintyMillimetres,
             combinedStandardUncertaintyMetres) = (
                ComputeScaleBarAPrioriUncertainty(
                    distance, tapeClassName, additionalMillimetres,
                    fromEndSurface=fromEndSurface, inService=inService))

            try:
                scaleBar.reference.accuracy = combinedStandardUncertaintyMetres
                updatedCount += 1
                if getattr(scaleBar.reference, 'enabled', True):
                    enabledDistanceSigmaPairs.append(
                        (distance, combinedStandardUncertaintyMetres))
                print("  {}: distance={} m, "
                      "MPE={} mm, "
                      "MPE/\u221a3={} mm, "
                      "combined standard uncertainty "
                      "\u03c3={} m".format(
                          scaleBar.label, FormatLengthWithDecimals(distance, 4),
                          FormatLengthWithDecimals(mpeMillimetres, 3),
                          FormatLengthWithDecimals(standardUncertaintyMillimetres, 3),
                          FormatLengthWithDecimals(combinedStandardUncertaintyMetres, 6)))
            except Exception as exceptionDetails:
                skippedCount += 1
                print("  {}: skipped, the \u201cAccuracy\u201d field could not "
                      "be set ({})".format(
                    scaleBar.label, DescribeException(exceptionDetails)))

        scaleOnlyRelativeUncertaintyPpm = (
            ComputeRelativeScaleUncertaintyPpm(
                enabledDistanceSigmaPairs))
        if scaleOnlyRelativeUncertaintyPpm is not None:
            print("")
            print("A priori scale-only indicator: {:.1f} ppm".format(
                scaleOnlyRelativeUncertaintyPpm))
            print("(the relative standard uncertainty of the scale "
                  "estimated from the enabled reference distances alone, with "
                  "the network shape held fixed and the distances "
                  "independent: not the scale uncertainty of the whole "
                  "adjustment, and without shared error components such as "
                  "a common tape-scale error, temperature, tension, or "
                  "shared targets)")

        print("")
        print("Updated: {}  |  Skipped: {}".format(
            updatedCount, skippedCount))
        print("=" * 60)
        print("")

        QtWidgets.QMessageBox.information(
            self, "Scale Bar A Priori Uncertainty",
            "Metashape scale bars updated: {}.\n"
            "Metashape scale bars skipped: {}.\n\n"
            "The details are in the Console.".format(
                updatedCount, skippedCount))

        # Close the main dialog.
        self.accept()


# ===================================================================
# Dialog launcher and menu registration (with reload protection)
# ===================================================================

def _ShowDialog():
    """Show the Scale Bar A Priori Uncertainty dialog."""
    parent = QtWidgets.QApplication.activeWindow()
    dialog = ScaleBarAPrioriUncertaintyDialog(parent)
    dialog.exec_()


def RegisterMenuItem():
    """Register this tool in the MetaFOCUS menu."""
    # Remove any existing item first, so reloading the toolbox
    # never creates a duplicate menu entry.
    try:
        Metashape.app.removeMenuItem(_MENU_LABEL)
    except Exception as suppressedError:
        ReportSuppressedError("scaleBarAPrioriUncertainty.RegisterMenuItem", suppressedError)
    iconPath = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "icons", _ICON_NAME)
    if os.path.exists(iconPath):
        Metashape.app.addMenuItem(
            _MENU_LABEL, _ShowDialog, icon=iconPath)
    else:
        Metashape.app.addMenuItem(_MENU_LABEL, _ShowDialog)
