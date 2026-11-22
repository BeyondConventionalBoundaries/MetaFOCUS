"""
Terminology for Agisoft Metashape Professional
==============================================

The terminology of MetaFOCUS, which Manual and Terminology shows: the
terms that the tools, their About sections, the README, and the
MetaFOCUS paper use in one sense only, with the literature they follow.  The page keeps the
image (pixels), the sensor (photosites), and object space (the
physical scene) apart, and it records the terminology of the camera
model, of scale bars and reference distances, and of measurement
uncertainty, and it maps the names of Metashape to these terms.

Author:      Geert J. Verhoeven (ORCID: 0000-0003-4825-9604)
Version:     1.0.1 (2026-11-22)
Date:        2026-10-05
Licence:     GPL-3.0-or-later WITH GPL-3.0-linking-exception
Contact:     geert.verhoeven@univie.ac.at
Requires:    Agisoft Metashape Professional 2.x+
Developed for: Agisoft Metashape Professional 2.3.2

Usage:
    MetaFOCUS > Manual and Terminology (aboutPages.py) shows the page
    after the About sections, with an anchor at every entry that a term
    of an About section links to (TERMINOLOGY_LINKS,
    LinkTerminologyTerms), and Save Terminology as PDF writes the page as
    a PDF file of its own.
"""

import re

_TERMINOLOGY_HTML = """
<h2>MetaFOCUS Terminology</h2>
<p>Version 1.0.1 (2026-11-22)</p>
<p><b>Author:</b> Geert J. Verhoeven (ORCID: <a href="https://orcid.org/0000-0003-4825-9604">0000-0003-4825-9604</a>)<br>
<b>Contact:</b> <a href="mailto:geert.verhoeven@univie.ac.at">geert.verhoeven@univie.ac.at</a><br>
<b>Licence:</b> GNU General Public License, version 3 or any later version (GPL-3.0-or-later),
with an additional permission to convey MetaFOCUS combined with Agisoft Metashape and its
Python module (see LICENSE and ADDITIONAL_PERMISSION.txt in the MetaFOCUS ZIP file)</p>
<hr>
<p>The MetaFOCUS tools, their About sections, the README, and the
MetaFOCUS paper use the terms below in one sense only. Where the
literature uses a term in more than one sense, the entry says which
sense MetaFOCUS uses and why. Quotations carry page numbers, or clause
numbers for standards. The terms
keep the pixels of the digital image apart from the photosites of the
sensor, and they keep four further concepts apart. <i>Object space</i>
is the physical scene whose positions and geometry are described
mathematically. <i>Image space</i> is the two-dimensional domain of
positions in an analogue or digital image. In a digital image these
positions are given as pixel coordinates in px. <i>Optical image
space</i> is a qualified optical term for the image-side optical
domain, in which the optics form the image on the sensor. MetaFOCUS
uses it only where that domain is discussed. The <i>3D model</i> is the
mathematical representation, containing the model points, such as the
3D tie points, and the modelled camera geometry. A model point is an
estimate of an object point, and it stays a model point when the chunk
transform expresses it in the world coordinates of the chunk instead of
its internal coordinates: the transform changes its coordinates, not
what it is. The section <i>Metashape names and MetaFOCUS terms</i> at
the end of the page maps the names of Metashape to these terms.</p>

<h3>Image and sensor</h3>
<p><b>Pixel.</b> An element of a digital image, as its name says (pixel
is short for picture element), defined by Granshaw (2020) as the
smallest element in a digital image. That element is a point sample: one
value per channel, for example a grey value, three colour values, or a
depth, at one position of the image&rsquo;s sampling grid (Smith, 1995).
A pixel therefore has a position and a value, but no size, no shape, no
area, no edges, and no centre (Smith, 1995, 2021, and Verhoeven, 2018a). Its
value can carry a unit, a depth in metres for example, but the pixel
itself has no spatial extent. A pixel of a photograph is created when
the sensor samples the image that the lens forms (Schowengerdt, 2007):
the photosites generate the pixels, but a pixel belongs to the digital
image, not to the sensor. The signal of a photosite integrates the light
that reaches its light-sensitive area, blurred by the optics, so the
contributions to a pixel come from an area, whereas the pixel itself
remains a point sample (Smith, 1995, and Verhoeven, 2018a). Pixels can also
be computed, for example by resampling an image or by rendering a model
(Smith, 2021). For an image that keeps the sampling of the sensor, each
pixel position corresponds to the centre of one photosite, although
demosaicing computes the colour values from several photosites. A viewer
that zooms in shows each pixel as a little square by repeating its value
M &times; M times for a magnification M (Smith, 1995). The square
represents the pixel but is not its shape: only a pixel spread by a
reconstruction filter has a shape, and the little square is the spread
pixel of the simplest filter, the box filter (Smith, 1995, 2021, and
Verhoeven, 2018a).</p>
<p><b>Pixel indices and pixel coordinates.</b> A pixel is identified by
two integer indices, its column and its row. How they are counted is a
convention: Metashape and MetaFOCUS count from 0, the column i from 0 at
the left to W&nbsp;&minus;&nbsp;1 and the row j from 0 at the top to
H&nbsp;&minus;&nbsp;1, with W and H the numbers of pixel columns and
rows, whereas other programs count from 1 (Schowengerdt, 2007).
Positions in the image are given by continuous pixel coordinates (x, y),
with x to the right and y downwards. They can take any value, not only
those of the pixel positions: a keypoint, a projected point, or the
principal point usually has fractional coordinates. Such positions are
often called sub-pixel or intra-pixel positions, names that come from
the little-square model, in which a fractional position lies inside a
pixel&rsquo;s square, and MetaFOCUS does not use them. Which coordinates
a pixel receives is also a convention. Metashape places pixel (i, j) at
(i&nbsp;+&nbsp;0.5, j&nbsp;+&nbsp;0.5), so that the outermost pixel
positions lie at 0.5 and W &minus; 0.5 and at 0.5 and H &minus; 0.5, and
the pixel coordinates of the image run from 0 to W and from 0 to H
(Agisoft LLC, 2026, p. 231). MetaFOCUS follows it, and it adds 0.5 only
to turn the indices of a pixel into its position, never to a coordinate
that is already a position. Other programs place pixel (i, j) at (i, j),
as Smith (1995) suggests, who notes that the half-integer placement
suits only the box filter and the little-square model, or measure from
the principal point or the image centre, and computer vision also from
the bottom left of the image (Granshaw, 2020). MetaFOCUS uses the pixel
coordinates of an image as the camera model of Metashape defines them.
Whether Metashape applies the Exif orientation of an image file before
it does so has not been checked. MetaFOCUS writes pixel position, never
pixel centre.</p>
<p><b>px.</b> The unit of pixel coordinates, the coordinates of
positions in a digital image. Neighbouring pixel positions lie 1&nbsp;px apart
along a row and along a column, so a coordinate difference in px is
expressed in sampling intervals of the image. On the sensor, in the
optical image space, this step is the photosite pitch p, for an image
that keeps the sampling of the sensor, that is, one pixel position per
photosite (Luhmann et al., 2023), with the horizontal pitch along the
rows and the vertical pitch along the columns when the two differ. For
such an image, a value in px is therefore a value in photosite pitches:
a reprojection error of 0.3&nbsp;px corresponds to 0.3&nbsp;p, and the
principal distance in pixels c<sub>pix</sub>&nbsp;=&nbsp;c/p is the
principal distance c in photosite pitches. The number itself is
dimensionless, and multiplied by p it becomes a length on the sensor
(0.3 px corresponds to 1.5 &micro;m for p = 5 &micro;m). In a binned or
resampled image the step on the sensor changes, whereas the photosite
pitch does not: after halving the numbers of pixel columns and rows, 1
px corresponds to 2p on the sensor, a residual of 0.3 px to 0.6 p, and a
principal distance of 10 000 px becomes 5000 px. A virtual camera, such
as that of Depth &amp; Render Export, has virtual photosites, with as
many columns and rows as its rendered images have pixel columns and
rows, so that 1&nbsp;px in these images corresponds to the pitch of its
virtual photosites. The numbers of pixel columns and rows of an image
are counts, not values in px.</p>
<p><b>Photosite.</b> One of the individual photon-sensing sites that
make up the image sensor (Verhoeven, 2018a): the detector element of
Schowengerdt (2007), the detector of Fiete (2010) and Vollmerhausen et
al. (2010), and the
sensor element of Luhmann et al. (2023).  Its key component is the
photodetector, which collects the light that reaches it during the
exposure (Verhoeven, 2018a), and the read-out, the
demosaicing, and further processing turn these signals into the pixel
values of the image.  A pixel position corresponds to one photosite
only when the image keeps the sampling of the sensor, that is, when it
was not binned, downsampled, or otherwise resampled.  Luhmann et al.
(2023) state the same condition for the relation between a digital
image and its sensor.</p>
<p><b>Photosite pitch p.</b> The distance between the centres of
neighbouring photosites (Verhoeven, 2018a), the inter-detector spacing
of Schowengerdt (2007) and the detector pitch of Fiete (2010).  Data
sheets and textbooks also call it pixel
pitch, pixel spacing, or pixel size (Luhmann et al., 2023, write pixel
spacing &Delta;s&prime;), and Metashape stores it as the pixel size of
the interior orientation group (<code>Sensor.pixel_size</code> in its
Python application programming interface, or API).  MetaFOCUS writes photosite pitch and quotes
Metashape&rsquo;s name only when it refers to that field.  The pitches
along the rows and along the columns, p<sub>x</sub> and p<sub>y</sub>,
can differ. For an image
that keeps the sampling of the sensor, neighbouring pixel positions
correspond to the centres of neighbouring photosites, which lie one
photosite pitch apart on the sensor. Binning or resampling an image does
not change the photosite pitch, but it changes the distance on the
sensor between neighbouring pixel positions, which Metashape&rsquo;s
pixel size must then hold (see px).</p>
<p><b>Photosite size.</b> The width of one photosite.  The photosites
of a regular sensor abut, so the photosite size equals the photosite
pitch, as in Verhoeven (2018b), who obtains the length of one photosite
by dividing the length of the sensor by the number of photosites along it.
Not all of a photosite is light-sensitive, however: the photon-receiving
area of its photodetector can be smaller (Verhoeven, 2018a). The width
of that area is the aperture size &Delta;d&prime; of Luhmann
et al. (2023), the detector width of Schowengerdt (2007) and Fiete
(2010), and the detector size
of Vollmerhausen et al. (2010), who give an example with a detector size
of 31 &micro;m on a pitch of 38 &micro;m.  The ratio of the
light-sensitive area to the area of the photosite is the fill factor
(Luhmann et al., 2023, and Vollmerhausen et al., 2010), which back
illumination increases (Luhmann
et al., 2023). Microlenses increase the fill factor, because they
collect the light that falls on about the area of the photosite and
direct it onto its smaller light-sensitive area, so that the term is
used in two senses. MetaFOCUS means the area ratio, which microlenses do
not change. The aperture size determines how a photosite blurs the image
and the instantaneous field of view (IFOV) of its light-sensitive area,
whereas the pitch determines the IFOV of the whole photosite, the
sampling, and the ground sampling distance (see IFOV).  The photosite
pitch and the ground
sampling distance (GSD) are both
often, and ambiguously, called pixel size, although a pixel is an element of the
image, not of the sensor, and has no size (Verhoeven, 2018a, 2018b).
Verhoeven (2018b) therefore discards the term, Granshaw (2020) defines
the pixel size as the size of the radiation-sensitive elements of a
digital camera, and Metashape&rsquo;s pixel size is the pitch.  No
MetaFOCUS computation uses the aperture size.</p>

<h3>Angles and lengths in object space</h3>
<p><b>Instantaneous field of view (IFOV).</b> The angle that one
photosite subtends at the perspective centre: 2&nbsp;arctan(p/2c)
&asymp; p/c on the camera axis, with p the photosite pitch, which equals
the photosite size, and c the principal distance (Verhoeven, 2018b,
writes p/f&prime;). Verhoeven (2018b) calls it the angular version of
the GSD and gives detector angular subtense (DAS) as a synonym. The IFOV
can also be taken for the light-sensitive area of the photosite alone, 2
arctan(&Delta;d&prime;/2c) &asymp; &Delta;d&prime;/c, with
&Delta;d&prime; the aperture size (see Photosite size): Schowengerdt
(2007) and Fiete (2010) define it in this way with the detector width,
and Vollmerhausen et al. (2010) call this angle the detector angular
subtense, so that this name, too, is used in both senses. The two angles
coincide for a fill factor of one. MetaFOCUS means the IFOV of the whole
photosite and names the light-sensitive area when it means the other
angle. In the example of Vollmerhausen et al. (2010), a square detector
31 &micro;m wide on a pitch of 38 &micro;m with a focal length of 5 cm,
the IFOV of the light-sensitive area is 0.62 mrad and that of the whole
photosite 0.76 mrad, the angular sample spacing (1.32 samples per mrad).
For an image that keeps the sampling of the sensor, the IFOV of the
whole photosite is, to first order, the angle between the rays through
two neighbouring pixel positions, the angular sampling interval of the
image. Binning or resampling changes this sampling interval, but not the
IFOV. Granshaw (2020) defines the IFOV as the angle subtended by a pixel
at the perspective centre, which places the pixel on the sensor. In the
ideal camera model these angles are the same in the optical image space
and in object space (Schowengerdt, 2007), whereas in a real lens the
angles at which the rays enter and leave differ in general (Fiete,
2004). MetaFOCUS computes no IFOV.</p>
<p><b>Ground sampling distance (GSD).</b> The sampling interval of an
image on the imaged surface: the distance between the points at which
the rays through two neighbouring pixel positions meet the surface.
For an image that keeps the sampling of the sensor, it is the
photosite pitch projected onto the surface, the wording of the About
sections and of the
paper: the object-space conjugate of the photosite pitch of Verhoeven
(2018b) and the detector pitch projected onto the ground of Fiete
(2010).  MetaFOCUS computes it along the image row and
along the image column at sampled pixel positions, with four
auxiliary rays through the positions &frac12;&nbsp;px on either side of
the pixel position along the row and the column, which for an image that
keeps the sampling of the sensor are the midpoints of the edges of its
photosite, half a photosite pitch from its centre. This gives the
row and column sampling distances GSD<sub>row</sub> and
GSD<sub>column</sub> as central differences at the pixel position
itself.  It reports as the GSD the side of the square
with the area that the projected row and column steps span,
&radic;(GSD<sub>row</sub>&nbsp;&middot;&nbsp;GSD<sub>column</sub>&nbsp;&middot;&nbsp;sin&nbsp;&phi;),
with &phi; the angle between the two projected steps.  The GSD is a
length, given in metres, not in metres per pixel, because a pixel has no
extent that could be carried over to the surface.  Like the image scale
of analogue photographs, which it succeeds, it carries distances in the
image over to the surface, and like the image scale it has a local
value at every surface point and an average for the whole image.  Schowengerdt (2007) calls this
spacing the
ground-projected sample interval (GSI).  Three forms of the name are in
use, and Verhoeven (2018b) lists all three: ground sampling distance, which MetaFOCUS uses, ground sample
distance, the form that Granshaw (2020) recommends instead of sampled or
sampling, and ground sampled distance.  Luhmann et al. (2023) write both ground
sample distance and ground sampling distance, and Schowengerdt (2007)
and Verhoeven (2018b) also use ground sample distance or GSD as another
name for the GIFOV (see GIFOV).  For imaging that is not
aerial, Verhoeven (2018b) proposes the more general scene sampling
distance (SSD).  The GSD is a sampling
interval, not the spatial resolution: the detail that an image
resolves on the surface also depends on the optics, the aperture
size of the photodetectors, motion, and the processing (Granshaw, 2020,
and Verhoeven, 2018b), see Spatial resolution.</p>
<p><b>Spatial resolution.</b> The smallest distance between two object
points that are still resolved in the image (Verhoeven, 2018b), or more
generally the smallest detail that an image resolves under a stated
criterion, such as a separation, a spatial frequency at which the
modulation transfer function (MTF) falls to a stated value, or the
performance in a stated task (Fiete, 2010, and Vollmerhausen et al.,
2010). It depends on the optics, with their diffraction limit
(Verhoeven, 2019), on the aperture size of the photodetectors, the
sampling, noise, contrast, motion, and the processing, so neither the
pixel count, the photosite pitch, nor the GSD determines it alone.
Verhoeven (2018b) estimates it for an ideal sampled image as about three
times the GSD and lists the factors that this estimate leaves out, Fiete
(2010) shows that images with the same GSD can differ greatly in
quality, and ISO 10360-13:2021 (Annex F.1) rejects equating the pixel
grid with the resolution of an optical 3D measuring system. In
metrology, resolution is the smallest change of a measured quantity that
causes a perceptible change of the indication (VIM 4.14), a different
concept.</p>
<p><b>Ground-projected instantaneous field of view (GIFOV).</b> The
IFOV projected into
object space (Schowengerdt, 2007), so, like the IFOV, the projection
onto the surface of one photosite or of its light-sensitive area. 
MetaFOCUS means the projection of the whole photosite, a square one
photosite pitch wide on a sensor with equal pitches. Schowengerdt (2007)
defines the GIFOV with the
detector width, notes that sensors are commonly designed so that it
equals the ground-projected sample interval, and gives ground sample
distance as another name for it, and Verhoeven (2018b) writes that the
GSD is also known as the GIFOV. For an image that keeps the sampling of
the sensor, the width of the GIFOV of one photosite on a surface
parallel to the sensor is the GSD.  For a frame camera with all lens
distortion coefficients and the affinity and non-orthogonality
coefficients set to zero, the GIFOV of a square photosite on flat,
level terrain is a square in a vertical image, a trapezium in a tilted image
whose rows or columns are parallel to the horizon, and another
quadrilateral in any other tilted image, and lens distortion and
relief change its shape further.  MetaFOCUS does not trace this
outline: the projected row and column steps, whose lengths are the row
and column sampling distances, span to
first order the area onto which one sampling interval along the row and
one along the column project, whatever its shape, and for an image that
keeps the sampling of the sensor this is the GIFOV area. The GSD of
MetaFOCUS is the square
root of that area,
&radic;(GSD<sub>row</sub>&nbsp;&middot;&nbsp;GSD<sub>column</sub>&nbsp;&middot;&nbsp;sin&nbsp;&phi;),
with &phi; the angle between the two steps.  It equals the geometric
mean of the two sampling distances when the steps are perpendicular,
while the geometric mean exceeds it otherwise by the factor 1/&radic;(sin&nbsp;&phi;), for
example by 3&nbsp;% at an incidence angle of 45&deg; and by 12&nbsp;%
at 60&deg; when the surface is tilted diagonally to the image rows and
columns.  Unlike the geometric mean, it depends only on the area that
the two steps span, not on their directions, so that, to first order,
the value of a surface point does not change when the camera is
rolled about its camera axis, provided that the camera model, apart
from its affinity and non-orthogonality, is symmetric about that axis.
The camera model of a frame or fisheye camera is symmetric in this sense
with the decentring distortion coefficients set to zero,
without additional corrections, and without rolling-shutter compensation.
The GIFOV is a geometric
idealisation: it does not equal the surface patch whose reflected
radiation contributes to one pixel value.  The light that
contributes to a pixel value comes from a larger area without a
sharp edge, and these areas overlap from one pixel to the next.  The
optics, the photon-receiving area of the photodetector, any optical
low-pass filter, and motion all blur what one photosite receives.  The
point spread function of this blur, projected onto the surface, gives
that area.  Schowengerdt (2007) calls the size of this area the
effective GIFOV and notes that it is larger than the GIFOV, 40 to 45 m
against 30 m for Landsat TM.  Demosaicing, a separate step, then
computes the colour values of each pixel from several photosites.  The
signal acquired by one photosite is the result of integrating all the
photons that reach it.  That is why a pixel is often pictured as a
square, although the photon contribution is much closer to a Gaussian
(Verhoeven, 2018a).  A little square can only model the contributions
to a pixel in a low-order way, but it can never represent the pixel
itself (Smith, 1995).  The GIFOV is such a low-order model of the
contributions.</p>
<p><b>Image footprint.</b> The area of the surface that one image
covers, bounded by the rays through the outermost pixel positions of
the image, the border convention of the Image Footprint Generator: in
pixel coordinates the rectangle from 0.5 to W &minus; 0.5 and from 0.5
to H &minus; 0.5, half a sampling interval inside the edges of the image
format, which run from 0 to W and from 0 to H. Its average GSD from area
therefore divides the area by (W &minus; 1)(H &minus; 1).
These rays enclose an area.  A single pixel position gives one ray and
encloses none, so MetaFOCUS never
writes pixel footprint, pixel support, ground pixel, or pixel on the
ground, and never uses pixel size for a length on the surface.</p>
<p><b>Image scale number m.</b> The GSD divided by the photosite pitch
for an image that keeps the sampling of the sensor, and otherwise by the
distance on the sensor that 1 px of the image represents,
so that the image scale is 1:m (Luhmann et al., 2023, and Kraus, 2007).
In the fundamental imaging equation of Verhoeven (2018b), m =
s/f&prime;, the object distance divided by the focal length, which
equals the footprint width divided by the
sensor width. For unequal pitches, MetaFOCUS divides its area-equivalent
GSD by the geometric mean of the two pitches, so that m<sup>2</sup> is
the projected area of one pitch along the row and one along the column
divided by p<sub>x</sub>&nbsp;&middot;&nbsp;p<sub>y</sub>. The image
scale number is a local value: an oblique or distorted image has no
single image scale.
Schowengerdt (2007) uses m for the geometric magnification f/H, with H
the altitude, the
reciprocal of the image scale number.</p>

<h3>Camera model and processing</h3>
<p><b>Perspective centre O.</b> The point of the camera model through
which all image rays pass (Luhmann et al., 2023, and Granshaw, 2020).
Computer vision calls it the projection centre or the camera centre
(Granshaw, 2020), the Metashape documentation uses both names, and it is
the optical centre O of Verhoeven (2018b).
MetaFOCUS uses the perspective centre only as the origin of the rays
of the calibrated camera model.  Where this point lies in a real lens
is a separate question, which the camera model does not need to
answer: the principal points, the nodal points, and the centres of the
entrance and exit pupils of a lens are defined differently and need not
coincide (Fiete, 2004). For an image with rolling-shutter compensation,
Metashape also estimates the motion of the camera during the readout, so
that each image row has its own perspective centre, and the GSD tools
and the Image Footprint Generator then follow each ray through the
camera model of its row (see Rolling shutter in their About
sections).</p>
<p><b>Principal distance c.</b> The length of the normal from the
perspective centre to the image plane (Granshaw, 2020), determined by
calibration.  For a lens focused at infinity, as in aerial imaging, it
equals the focal length, the f&prime; of Verhoeven (2018b), up to the
part of the image scale that calibration assigns to the radial
distortion (Fiete, 2004), and Granshaw (2020) gives calibrated focal
length as its name in the United States.  The name
camera constant is avoided because the value is
not, in general, constant (Granshaw, 2020).  The <b>principal distance
in pixels</b> is c<sub>pix</sub> = c/p, with c and the photosite pitch p
in millimetres, for an image that keeps the sampling of the sensor (see
px).  Metashape stores c<sub>pix</sub> as the attribute f of
the calibration, and many structure-from-motion programs say focal
length where the principal distance is meant (Granshaw, 2020).
MetaFOCUS writes f and f&prime; only for the focal length of a lens and
names f otherwise only as Metashape&rsquo;s attribute.  Strictly,
c<sub>pix</sub> is the principal distance divided by the photosite
pitch, a number, and c<sub>pix</sub> = 5000&nbsp;px means a principal
distance of 5000 photosite pitches for an image that keeps the sampling
of the sensor.  Values of c<sub>pix</sub> nevertheless keep the label
px, as in Metashape and in photogrammetric practice, because px labels
values in pixel coordinates. For Metashape&rsquo;s fisheye models,
c<sub>pix</sub> is the principal distance of the fisheye projection, r =
c<sub>pix</sub> &theta; (equidistant) or r = 2 c<sub>pix</sub>
sin(&theta;/2) (equisolid), with &theta; the angle from the camera axis
(Agisoft LLC, 2026, Appendix D), as Luhmann et al. (2023) write the
fisheye projections with c.</p>
<p><b>Camera coordinates and normalised coordinates.</b>
Metashape&rsquo;s camera coordinates refer to a coordinate reference
system (CRS) tied to the camera, with its origin at the perspective
centre, the X axis to the right, the Y axis down, and the Z axis along
the camera axis in the viewing direction (Agisoft LLC, 2026, p. 231).
For a frame camera, the normalised coordinates x = X/Z and y = Y/Z of a
ray are dimensionless ratios, which the calibration maps to pixel
coordinates in px. They are neither pixel coordinates nor lengths on the
sensor. The principal point lies at the pixel coordinates (W/2 + cx, H/2
+ cy), so cx and cy are offsets from the centre of the image format
(Agisoft LLC, 2026, Appendix D).</p>
<p><b>Orientation.</b> Interior orientation (principal distance,
principal point, lens distortion, and, in Metashape, the affinity and
non-orthogonality terms b1 and b2) and exterior orientation (position
of the perspective centre and rotation of the camera) are the
photogrammetric terms for what the bundle adjustment determines
(Granshaw, 2020).  In Metashape, aligning images (Align Photos) means
running the structure-from-motion (SfM) procedure, and an aligned image
is an oriented image, one with an exterior orientation, which that
procedure usually estimated together with the interior orientation,
unless the interior orientation was fixed or the exterior orientation
imported.  MetaFOCUS writes SfM procedure, orientation,
oriented images, and SfM parameters, and keeps align only in the names of
Metashape&rsquo;s command and its settings.  An <b>interior orientation
group</b> is a set of images that share one camera calibration (a
sensor in Metashape, a calibration group in its manual), except for
any parameters selected as image-variant, which Metashape estimates
for each image.  An <b>image collection</b> is an image group in
Metashape (a camera group in its Python API).</p>
<p><b>Structure from motion (SfM) and multi-view stereo (MVS).</b>
Written in lower case and hyphenated only as an adjective, as in
structure-from-motion software (Granshaw, 2020).  The toolbox name
keeps its title capitals: Metashape Functions for Optimising,
Characterising, and Understanding Structure from Motion.</p>
<p><b>Keypoint.</b> A distinctive point that a feature detector finds in an image, with a position
in pixel coordinates, usually fractional, a size, the scale at which it was found, and a descriptor
by which it is matched with keypoints of other images (Lowe, 2004). A matched keypoint becomes a 2D
tie point. Its size describes the scale at which it was found, not a size of a pixel. Keypoint is
one word, also in compounds such as keypoint size and keypoint limit (Granshaw, 2020, and Lowe,
2004). Metashape&rsquo;s labels are quoted as they appear, for example the &lsquo;Key point limit&rsquo;
of Align Photos.</p>
<p><b>Tie point, 3D tie point, and 2D tie point.</b> A tie point is a
point that is measured in two or more images and ties them together:
Luhmann et al. (2023, p. 304) define tie points as measured, homologous
points in the images that represent the same object point.  MetaFOCUS
writes <b>3D tie point</b> for the point whose 3D
coordinates the bundle adjustment estimates, Metashape&rsquo;s tie
point, and <b>2D tie point</b>, also image tie point, for the measured
position of a tie point in one image, which Metashape calls a tie
point projection (Agisoft LLC, 2026, p. 90).  The 2D tie points of one
3D tie point form its track (TiePoints.tracks in Metashape&rsquo;s
Python API), the matched keypoints taken to show the same object point,
a hypothesis that a wrong match breaks. Tie point alone names
the point as a whole, for example in tie-point count, and Luhmann et
al. (2023, p. 371) speak in the same way of the 3D coordinates of tie
points. In
photogrammetric terms, a 2D tie point is an image point, a
2D point in image space (Granshaw, 2020, p. 204), here in a digital
image, and a 3D tie point is a model point (Granshaw, 2020, p. 223), a
point of the 3D model, whereas the object point is the 3D point in object space whose object
coordinates are to be determined (Granshaw, 2020, p. 230), the point of
the real object that the 3D tie point estimates. A 3D tie point stays a
model point when the chunk transform expresses it in the world
coordinates of the chunk.  The
residual of a 2D tie point is the difference between the position at
which its 3D tie point projects into the image and its measured
position, in pixel coordinates.  The Marker Residual Inspector gives
the residual of a marker projection with the opposite sign, as the
measured minus the projected position, which for an image with
rolling-shutter compensation can also differ by more than the sign when
the residual is not zero. A small residual shows that the
model fits the image measurements, not that the model points are
accurate in object space.  The points of the dense point cloud
are model points as well, but not 3D tie points, because the bundle
adjustment does not estimate them.  Metashape computes them after the
bundle adjustment, with the orientations that the bundle adjustment
estimated, from the depth maps of dense stereo matching: the combined
depth map of each image becomes a partial point cloud, and the partial
point clouds are merged, with further noise filtering in the
overlapping regions.  The points therefore stem from the pixel
positions of the combined depth maps that kept a depth after the
matching and the filtering, not from every pixel of the images, and
below the quality Ultra High the depth maps have fewer pixel columns
and rows than the images.  Build Point Cloud can also
take its points from a triangular mesh or a tiled model instead
(Agisoft LLC, 2026, pp. 45 to 47).</p>

<h3>Coordinate systems and datums</h3>
<p><b>Coordinate system, coordinate reference system (CRS), and
datum.</b> MetaFOCUS uses these terms as ISO 19111:2019 defines them. A
coordinate system is a &ldquo;set of mathematical rules for specifying
how coordinates are to be assigned to points&rdquo; (3.1.11). A
Cartesian coordinate system gives the position of points relative to two
or three mutually perpendicular straight axes that all have the same
unit of measure (3.1.2). A datum is a &ldquo;parameter or set of
parameters that realize the position of the origin, the scale, and the
orientation of a coordinate system&rdquo; (3.1.15), and a CRS is a
&ldquo;coordinate system that is related to an object by a datum&rdquo;
(3.1.9). A coordinate system alone therefore fixes neither the position
of its origin nor its scale nor the orientation of its axes, and
coordinates locate a point only together with their CRS. For a geodetic
or vertical datum, which ISO 19111:2019 also calls a reference frame,
the object is the Earth, or in planetary applications another celestial
body (3.1.9, Notes 1 and 2). An engineering datum
relates a coordinate system to a local reference (3.1.26), for example a
construction site, a moving object, or an image (3.1.25, Examples 1 to
3). A coordinate operation changes coordinates in one CRS into
coordinates in another (3.1.8). It is a coordinate conversion when the
two CRSs are based on the same datum (3.1.6), and a coordinate
transformation when they are based on different datums (3.1.12). A
change of coordinate epoch within one CRS, a point motion operation, is
a coordinate operation as well (3.1.8 and 3.1.48). ISO 19111:2019 calls
the colloquial name datum transformation erroneous, because a coordinate
transformation operates between two CRSs, not between two datums
(3.1.12, Note 2).</p>
<p>Metashape calls a CRS a coordinate system: the Coordinate System of
its Reference Settings (Metashape.CoordinateSystem in the Python API) is
a geographic, projected, geocentric, or local CRS, or a compound CRS with
heights on a vertical datum, and a local CRS of Metashape is an
engineering CRS. MetaFOCUS writes CRS for it, as in chunk CRS and marker
CRS, and keeps Coordinate System only as the name of the setting. It
does not call a CRS a frame, because ISO 19111:2019 admits reference
frame as another name for a datum (3.1.15). Frame keeps only its other
senses, as in frame camera, keyframe, full-frame, and the frames of a
video or an animation, and appears in quoted labels of Metashape such as
Rotate Region to Local Frame. The internal coordinates of a chunk refer
to a CRS whose datum Metashape chooses, because the images alone
determine the block only up to its position, orientation, and scale. The
chunk transform relates them to the world coordinates of the chunk.
MetaFOCUS calls a chunk georeferenced when it has a geographic,
projected, or geocentric CRS and a chunk transform that places it on the
Earth. Its transform then has a physical scale, a translation, and a
rotation, and the last two are not both those of Align Coordinate Axes
to Region of Region Tools. Metashape sets
no translation and no rotation without reference coordinates, so a chunk
scaled by Metashape scale bars alone is not georeferenced, whatever its
CRS. For a georeferenced chunk, Metashape takes the
world coordinates as geocentric coordinates on the geodetic datum of the
chunk CRS and derives the coordinates in the chunk CRS from them by a
coordinate conversion, with a coordinate transformation for heights on a
vertical datum. The world coordinates are such geocentric coordinates as
long as the chunk transform has not been replaced by one that is not
fitted to the reference data, as Align Coordinate Axes to Region of
Region Tools does. A local east-north-up CRS at a point is a derived CRS
(ISO 19111:2019, 3.1.18): a coordinate conversion of the geocentric
coordinates gives its coordinates, and it keeps the geodetic datum of
the chunk CRS. Camera coordinates refer to a CRS tied to the camera, and
pixel coordinates to a CRS tied to the image (see Camera coordinates and
normalised coordinates, and Pixel indices and pixel coordinates).</p>

<h3>Scale bars and reference distances</h3>
<p><b>Scale bar.</b> A physical bar, for example of steel or Invar,
that carries targets at a calibrated distance and is imaged to give the
project its scale (Granshaw, 2020).</p>
<p><b>Reference distance.</b> A distance between two identifiable
points, such as targets, that is
measured independently of the images, for example with a tape, with
stated endpoints, conditions, and uncertainty (Verhoeven and Ressl,
2026). It
enters the bundle adjustment as an observation when it serves as a
control distance, and it stays out of the adjustment when it is withheld
as a check distance.  The calibrated distance of a scale bar is one kind
of reference
distance.  A distance measured with a tape is a reference distance,
not a scale bar, although Metashape holds it in a Metashape scale bar.</p>
<p><b>Metashape scale bar.</b> The Metashape object that holds a
reference distance between two markers or the perspective centres of
two images: &ldquo;Scale bar
is the representation of any known distance within the scene&rdquo;
(Agisoft LLC, 2026, p. 113).  Physical scale bars and taped reference
distances are both entered as Metashape scale bars, and MetaFOCUS texts
say Metashape scale bar when they mean the object of the chunk.  The
tool Scale Bar A Priori Uncertainty keeps its name because it writes to
these objects.  The graphical bar that gives a length in the vector
plots of the Interior Orientation Viewer is called a reference vector,
not a scale bar.</p>

<h3>Measurement uncertainty</h3>
<p><b>Accuracy, trueness, and precision.</b> Measurement accuracy is the closeness of agreement
between a measured quantity value and a true quantity value of a measurand (VIM 2.13). It is a
concept, not a quantity, and is given no numerical quantity value: a measurement is more accurate
when its measurement error is smaller (VIM 2.13, Note 1). Numerical results are given instead as an
estimated bias, a precision statistic, a measurement uncertainty, or a statistic of measurement
errors, such as the root mean square of check discrepancies. Many standards and textbooks report
such a statistic as the accuracy itself. MetaFOCUS names the statistic instead. Trueness and
precision are defined in VIM 2.14 and 2.15. Accuracy, trueness, and precision are also defined in
ISO 5725-1:2023 (3.5, 3.6, and 3.12), which defines accuracy and trueness against the true value and
substitutes the accepted reference value in practice. The accuracy (trueness and precision) measures
of ISO 5725-1:2023 are usually a bias for trueness and a standard deviation for precision (4.1.1,
3.6, Note 1, and 3.12, Note 2). A standard deviation that the bundle adjustment derives from its
covariance matrix is a formal standard deviation, conditional on the model and its weights, not the
result of repeated measurements. MetaFOCUS reads the values of Metashape&rsquo;s Accuracy fields as
a priori standard deviations, an interpretation that follows standard least-squares practice, and
quotes &lsquo;Accuracy&rsquo; only as the name of the field. The name is a misnomer for two reasons.
The adjustment treats the value as a measure of precision, not of accuracy, even where it is a
standard uncertainty, which can also include systematic effects (VIM 2.26, Note 1). Moreover,
accuracy has no numerical value (VIM 2.13, Note 1).</p>
<p>The VIM writes <i>a</i> true quantity value, not <i>the</i> true
quantity value. No quantity can be defined in every detail. The length
of a scale bar, for example, also depends on its temperature, and a
definition that does not fix the temperature fits several values.
Several true values can therefore be consistent with one definition (VIM
2.11, Note 1). When this definitional uncertainty is negligible, as the
GUM assumes, the true value can be treated as essentially unique (VIM
2.11, Note 3).</p>
<p><b>Measurement error, residual, and check discrepancy.</b> A
measurement error is a measured quantity value minus a reference
quantity value (VIM 2.16), and the reference value has its own
uncertainty. A residual compares an observation with the value that the
adjusted model predicts for it, with the sign that the tool states
(Granshaw, 2020, p. 251). Metashape writes Error for both (see Error (m)
in the section Metashape names and MetaFOCUS terms): for a control point
it is a residual, because its reference coordinates took part in the
adjustment, and for a check point it is a check discrepancy, the
measurement error against reference coordinates that took no part in it.
A check point is still triangulated with an orientation that the control
points helped to determine, so its check discrepancy is not independent
of them. Measurement bias is an estimate of a systematic measurement
error (VIM 2.18), whereas ISO 5725-1:2023 (3.8) defines bias as the
expectation of the results minus a true value, which a finite set of
results can only estimate.</p>
<p><b>A priori standard deviation and standard uncertainty.</b> The a
priori standard deviation &sigma; of an observation fixes its weight in
the adjustment, which is inversely proportional to the variance
&sigma;&sup2; (Ghilani, 2010).  For a reference distance, MetaFOCUS
evaluates &sigma; as a standard uncertainty, a measurement uncertainty
expressed as a standard deviation (VIM 2.30).  A priori is written
without italics and without a hyphen (Granshaw, 2020).</p>
<p><b>Standard error (SE).</b> The standard deviation of an estimate,
for example of an adjusted parameter. The Interior Orientation Viewer
uses the term, because estimate/SE is the usual notation for the test
statistic. Elsewhere, MetaFOCUS speaks of the standard deviations of
adjusted coordinates. The GUM notes that the experimental standard
deviation of the mean &ldquo;is sometimes incorrectly called standard
error of the mean&rdquo; (JCGM 100:2008, B.2.17, Note 3, p. 36).</p>
<p><b>Maximum permissible error (MPE).</b> The extreme value of
measurement error, with respect to a known reference quantity value,
permitted by specifications or regulations (VIM 4.26).  OIML R&nbsp;35-1
and Directive 2014/32/EU give the MPE of a measuring tape by accuracy
class, and OIML R&nbsp;35-1 also gives the MPE in service, twice that on
initial verification (4.3).  The MPE is a bound, not
a standard deviation, and it becomes a standard uncertainty only
through an assumed probability distribution (JCGM 100:2008, 4.3.7).</p>
<p><b>Repeatability, intermediate precision, and reproducibility.</b>
Precision under
repeatability conditions, with the same procedure, operators, measuring
system, operating conditions, and location over a short period, under
intermediate precision conditions, with the same procedure and location
over an extended period in which other conditions may change, and under
reproducibility conditions, with different locations, operators, and
measuring systems (VIM 2.20 to 2.25, and ISO
5725-1:2023, 3.13, 3.18, and 3.23). A statement of precision names the
conditions that changed and those that were kept.  MetaFOCUS uses
none of these words in the general sense of a procedure that others can
repeat, and running a script again on the same data is none of them.</p>
<p><b>Outlier.</b> A value of a set that a statistical test identifies
as inconsistent with the other values (ISO 5725-1:2023, 3.7).  A
heuristic flag of a MetaFOCUS tool, such as a large maximum-to-median
ratio, is not a statistical outlier test, and the values it flags are not
called outliers but, for example, suspect, as the suspect projections of
the Marker Residual Inspector.</p>
<p><b>Sigma nought &sigma;<sub>0</sub>.</b> The reference standard
deviation of the adjustment, also called the standard deviation of unit
weight, computed a posteriori from the weighted residuals (Ghilani,
2010) and spelt nought (Granshaw, 2020). It can also be set a priori.
Its square &sigma;<sub>0</sub><sup>2</sup> is the reference variance
(Ghilani, 2010), also called the variance factor (F&ouml;rstner and
Wrobel, 2016). In the approximate diagnostic of the Gradual Selection
Filter every residual is divided by its a priori standard deviation, so
that &sigma;<sub>0</sub> is a dimensionless number there, the overall
ratio of the residuals to their assumed standard deviations, not the
uncertainty of a point.</p>

<h3>Metashape names and MetaFOCUS terms</h3>
<p>Metashape names some quantities, objects, and steps differently from
the photogrammetric literature.  The table maps each name to what it is
and to the term that MetaFOCUS uses instead.  Some rows only map a name
of Metashape to the photogrammetric term.  Where the tools refer to a
field, command, or criterion of Metashape itself, they keep its name,
for example the criteria of Clean Tie Points in the Gradual Selection
Filter, and where they describe what it is, they use the term of the
table.  Page numbers in the table refer to the
Metashape 2.3 manual (Agisoft LLC, 2026) unless another source is
named.</p>
<table border="1" cellpadding="4" cellspacing="0">
<tr><th width="30%">Metashape name</th><th width="45%">What it is</th><th width="25%">MetaFOCUS term</th></tr>
<tr><td colspan="3"><b>Images and interior orientation</b></td></tr>
<tr><td>Camera (Workspace pane, <code>Metashape.Camera</code>)</td>
<td>One image of the chunk, with its exterior orientation and its
reference data.  A camera is the sensor for photography (Granshaw,
2020, p. 162).</td>
<td>image</td></tr>
<tr><td>Photo (Add Photos, Align Photos)</td>
<td>An image.  Image is a more general term than photograph (Granshaw,
2020, p. 203).</td>
<td>image</td></tr>
<tr><td>Sensor (<code>Metashape.Sensor</code>), calibration group
(manual)</td>
<td>The images that share one camera calibration, except for any
parameters selected as image-variant (p. 95).</td>
<td>interior orientation group</td></tr>
<tr><td>Image group, camera group (Workspace pane,
<code>Metashape.CameraGroup</code>)</td>
<td>A group of images in the Workspace pane (p. 23), which can be marked
as a camera station (p. 29).  The manual also writes camera group for a
calibration group (pp. 98 and 113).</td>
<td>image collection</td></tr>
<tr><td>Pixel size (Camera Calibration dialog,
<code>Sensor.pixel_size</code>)</td>
<td>The distance between the centres of neighbouring photosites, in
millimetres, for an image that keeps the sampling of the sensor, and
otherwise the distance on the sensor that 1 px of the image represents. 
A pixel belongs to the image and has no size (Verhoeven,
2018a).</td>
<td>photosite pitch p, for an image that keeps the sampling of the sensor</td></tr>
<tr><td>f, &ldquo;Focal length measured in pixels&rdquo; (p. 13)</td>
<td>The principal distance divided by the photosite pitch (the vertical
pitch when the two pitches differ), a number, for an image that keeps
the sampling of the sensor (see px).
Many structure-from-motion programs say focal length where the
principal distance is meant (Granshaw, 2020, p. 189).</td>
<td>principal distance in pixels c<sub>pix</sub> = c/p</td></tr>
<tr><td>cx, cy, &ldquo;Principal point coordinates, i.e. coordinates of
lens optical axis interception with sensor plane in pixels&rdquo;
(p. 13)</td>
<td>The offset of the principal point from the centre of the image, in
pixel coordinates, which the manual also calls the &ldquo;principal
point offset (in pixels)&rdquo; (p. 231). The principal point itself
lies at the pixel coordinates (W/2 + cx, H/2 + cy) (Appendix D).  The
gloss of p. 13
describes the point of autocollimation (Granshaw, 2020, p. 155), which
equals the principal point, the point at which the normal from the
perspective centre meets the image plane (Granshaw, 2020, p. 244), only
in a perfect camera.</td>
<td>principal point offset</td></tr>
<tr><td>b1, b2, &ldquo;Affinity and Skew (non-orthogonality) transformation coefficients&rdquo; (p. 13)</td>
<td>The affinity, a difference in scale between the two image axes, and
the non-orthogonality of the axes, in pixel coordinates.</td>
<td>affinity and non-orthogonality coefficients b1 and b2</td></tr>
<tr><td>k1, k2, k3, k4, &ldquo;Radial distortion coefficients&rdquo; (p. 13)</td>
<td>The coefficients of the radial distortion, k1&nbsp;r<sup>2</sup>
+ k2&nbsp;r<sup>4</sup> + k3&nbsp;r<sup>6</sup> + k4&nbsp;r<sup>8</sup>
with r the radial distance in normalised image coordinates, the radial
displacement divided by the radial distance.</td>
<td>radial distortion coefficients k1 to k4</td></tr>
<tr><td>p1, p2 (and p3, p4), &ldquo;Tangential distortion
coefficients&rdquo; (p. 13), Decentering (distortion plot, p. 98)</td>
<td>The coefficients of the decentring distortion, the misalignment of
lens components that causes both tangential and radial distortion
(Granshaw, 2020, p. 174).</td>
<td>decentring distortion coefficients</td></tr>
<tr><td colspan="3"><b>Processing</b></td></tr>
<tr><td>Align Photos, aligning images, alignment</td>
<td>Runs the structure-from-motion (SfM) procedure: it detects and
matches keypoints in the images and estimates the unfixed interior and exterior
orientation parameters of the images together with the 3D tie points in
a bundle
adjustment.  Orientation is the photogrammetric term for what it
estimates (Granshaw, 2020, p. 233).</td>
<td>running the SfM procedure, SfM procedure</td></tr>
<tr><td>Aligned image (aligned camera)</td>
<td>An image with an exterior orientation, which the SfM procedure
usually estimated together with the interior orientation, unless the
interior orientation was fixed or the exterior orientation
imported.</td>
<td>oriented image</td></tr>
<tr><td>Optimize Cameras</td>
<td>The bundle adjustment of the oriented images, with the interior
orientation parameters selected in its dialog and the enabled reference
data, weighted by the a priori standard deviations in their Accuracy
fields.  Align Photos ends with a bundle adjustment, so Optimize Cameras
runs this last step of the SfM procedure again, as a command of its
own, for example after markers were placed or parameters changed.  The manual itself says that Optimize Cameras
&ldquo;performs a full bundle adjustment procedure on the aligned
photogrammetric block&rdquo; (p. 110).</td>
<td>bundle adjustment, the last step of the SfM procedure run again</td></tr>
<tr><td>Accuracy of Align Photos (Highest to Lowest)</td>
<td>The image resampling scale of the keypoint detection: High uses the
original
images, each lower setting halves the numbers of pixel columns and
rows, and Highest doubles them (p. 38).  The setting fixes an image
resampling scale, not an accuracy (VIM 2.13).</td>
<td>image downscale factor, the downscale value of the log and of the
Python API</td></tr>
<tr><td>Quality of the depth maps (Build Point Cloud, Build Model)</td>
<td>The image resampling scale of the depth maps: Ultra High uses the original
images, and each lower setting halves the numbers of pixel columns and
rows (p. 47).  The setting fixes an image resampling scale, not a quality.</td>
<td>depth map quality, with the numbers of pixel columns and rows that
it sets</td></tr>
<tr><td colspan="3"><b>Tie points</b></td></tr>
<tr><td>Tie point (Tie Points in the Workspace pane, a point of
<code>Metashape.TiePoints</code>)</td>
<td>A point whose 3D coordinates the bundle adjustment estimates from
its measured positions in two or more images.</td>
<td>3D tie point, and tie point for the point as a whole, whose 2D tie
points form its track</td></tr>
<tr><td>Tie point projection (p. 90), projection
(<code>TiePoints.projections</code>)</td>
<td>The measured position of a tie point in one image, a keypoint
matched with keypoints of other images.</td>
<td>2D tie point, also image tie point</td></tr>
<tr><td>Point Cloud (Workspace pane, Build Point Cloud)</td>
<td>The point cloud that Build Point Cloud computes, usually from the
depth maps, whose points the bundle adjustment does not estimate.
Dense and sparse point clouds are separate terms (Granshaw, 2020,
pp. 175 and 264).</td>
<td>dense point cloud</td></tr>
<tr><td>Clean Tie Points (Tools &gt; Tie Points), Gradual Selection in
earlier versions</td>
<td>Selects 3D tie points by a criterion and a threshold set with a
slider, and Delete Selection removes them (p. 140).</td>
<td>gradual selection, as in the name of the Gradual Selection
Filter</td></tr>
<tr><td>Key point size, Key point limit</td>
<td>The sigma of the Gaussian blur at the level of the scale pyramid at
which a keypoint was found (p. 90), and the upper limit of the number
of keypoints per image (p. 39).  Keypoint is one word (Granshaw, 2020,
p. 212).</td>
<td>keypoint size, keypoint limit, with the labels of Metashape quoted
as written</td></tr>
<tr><td colspan="3"><b>A priori standard deviations</b></td></tr>
<tr><td>Accuracy fields of the Reference pane and of the Reference
Settings dialog (for example <code>scalebar_accuracy</code> in the
Python API)</td>
<td>Read as the a priori standard deviation &sigma; of an observation,
which fixes its weight, inversely proportional to &sigma;&sup2;.  The
name is a misnomer for two reasons.  The adjustment treats the value as
a measure of precision, not of accuracy, even where it is a standard
uncertainty, which can also include systematic effects.  Moreover,
accuracy is a concept, not a quantity, and is given no numerical
quantity value (VIM 2.13, Note 1).</td>
<td>a priori standard deviation &sigma;</td></tr>
<tr><td>Tie point accuracy (Reference Settings dialog)</td>
<td>Read as the a priori standard deviation of each image coordinate of
a 2D tie point at keypoint size 1.  A 2D tie point of keypoint size s gets s times this
value (p. 90).  The value is an assumed precision of the image
coordinates (F&ouml;rstner and Wrobel, 2016, p. 117), not an
accuracy.</td>
<td>a priori standard deviation of each image coordinate of a 2D tie
point at keypoint size 1</td></tr>
<tr><td>Image coordinates accuracy for markers (Reference Settings
dialog)</td>
<td>Read as the a priori standard deviation of the image coordinates of
a marker in one image, which indicates how precisely the markers were
placed (p. 112).  The value applies to the image coordinates as they
are, as at keypoint size 1, whatever the zoom level at which a marker
was placed.</td>
<td>a priori standard deviation of a marker projection</td></tr>
<tr><td colspan="3"><b>Criteria of Clean Tie Points</b></td></tr>
<tr><td>Projection Accuracy (criterion of Clean Tie Points)</td>
<td>The mean keypoint size of the 2D tie points of a 3D tie point,
&Sigma;&nbsp;s<sub>i</sub>&nbsp;/&nbsp;n, which the manual calls the
&ldquo;Average image scale that was used for measuring coordinates of
the projections of the tie-point&rdquo; (p. 140).  A size, not an
accuracy (VIM 2.13).</td>
<td>mean keypoint size</td></tr>
<tr><td>Reconstruction uncertainty (criterion of Clean Tie Points)</td>
<td>The ratio of the largest to the smallest semi-axis of the error
ellipsoid of a 3D tie point, &radic;(&lambda;<sub>1</sub>&nbsp;/&nbsp;&lambda;<sub>3</sub>)
with &lambda;<sub>1</sub> and &lambda;<sub>3</sub> the largest and the smallest
eigenvalue of its covariance matrix, from the triangulation alone
(pp. 139 and 140).  A measurement uncertainty characterises the
dispersion of values (VIM 2.26), whereas this ratio describes only the
shape of the ellipsoid.  The error ellipse of the manual is an
ellipsoid in 3D (Granshaw, 2020, p. 184).</td>
<td>shape ratio of the error ellipsoid</td></tr>
<tr><td>Reprojection error (criterion of Clean Tie Points)</td>
<td>The largest residual of the 2D tie points of a 3D tie point, each
divided by its keypoint size,
max<sub>i</sub>&nbsp;|x&prime;<sub>i</sub>&nbsp;&minus;&nbsp;x<sub>i</sub>|&nbsp;/&nbsp;s<sub>i</sub>
(p. 139), a number.  It equals the largest image residual v (in px)
only when all these keypoint sizes are 1, and the largest normalised
residual v&nbsp;/&nbsp;&sigma;, with &sigma; the keypoint size times the
Tie point accuracy, only when the Tie point accuracy is 1.  Normalised
residuals serve another purpose: they make the residuals of
observations of different weight comparable (F&ouml;rstner and Wrobel,
2016, p. 144). The test of an observation for a gross error divides its
residual by the standard deviation of the residual itself, the
standardised residual, whose divisor is smaller than &sigma; because it
shrinks with the redundancy number of the observation (F&ouml;rstner and
Wrobel, 2016, pp. 88 and 125), and Metashape reports neither. A
normalised residual well above
3 therefore still points to a possible outlier, but one below 3 does not
exclude it. For independent standard normal components, the length of a
residual vector exceeds 3 about four times as often by chance as one
coordinate does (1.1&nbsp;% against 0.27&nbsp;%). Normalised residuals do not
follow that distribution in general, because their variance shrinks with
the redundancy number of the observation.</td>
<td>largest residual in keypoint size units</td></tr>
<tr><td colspan="3"><b>Residuals and errors</b></td></tr>
<tr><td>RMS reprojection error (chunk information, processing
report)</td>
<td>The RMS of the residuals of the 2D tie points, once in keypoint
size units, which the manual calls the quantity that the bundle
adjustment minimises, and once
in pixels (p. 90). With markers, Metashape scale bars, or reference data
in the adjustment, their weighted residuals are minimised as well.</td>
<td>RMS reprojection error, always with its unit, px or keypoint size
units</td></tr>
<tr><td>Error (m) of markers and cameras, Error (deg) of cameras
(Reference pane)</td>
<td>For control points and for camera positions and angles used in the
adjustment, the residual between the source and the estimated values
(pp. 114 and 115).  For check points, the difference between the
reference coordinates and the position triangulated from the image
measurements alone (p. 115).  A residual is the difference from the
adjusted value (Granshaw, 2020, p. 251), an error the difference from
the true value (Granshaw, 2020, p. 184), or from a reference value (VIM
2.16), here the reference coordinates of a check point (Granshaw, 2020,
p. 164), which have their own uncertainty.</td>
<td>object-space error of a marker (Marker Residual Inspector): a
residual for a control point, a check discrepancy for a check
point</td></tr>
<tr><td>Error (pix) (Reference pane)</td>
<td>The RMS of the residuals of the 2D tie points of valid tie points
in an image, or of the projections of a marker (p. 115).</td>
<td>RMS reprojection error of an image in px (Image Parameter
Exporter), RMS of the residuals of a marker in px (Marker Residual
Inspector)</td></tr>
<tr><td>Markers ticked and unticked in the Reference pane</td>
<td>Markers whose reference coordinates enter the adjustment, and
markers whose reference coordinates only check it.  The manual uses the
same terms (p. 115), and so does the literature (Granshaw, 2020,
pp. 164 and 170).</td>
<td>control point, check point</td></tr>
<tr><td>Scale bar (Reference pane)</td>
<td>Any known distance within the scene, between two markers or two
images, that is, the perspective centres of two images (pp. 113 and
114).  MetaFOCUS keeps scale bar for a physical
bar (see Scale bar above).</td>
<td>Metashape scale bar, which holds a reference distance</td></tr>
<tr><td>pix (unit label)</td>
<td>A value in pixel coordinates, for example a residual or the
principal distance in pixels.</td>
<td>px, a number, not a length</td></tr>
<tr><td colspan="3"><b>Surfaces and region</b></td></tr>
<tr><td>Model, 3D Model (Workspace pane, Build Model)</td>
<td>The surface that Build Model computes as a mesh of
triangles. MetaFOCUS calls it the triangular mesh, because the 3D model
is the mathematical representation, containing the model points and the
modelled camera geometry.</td>
<td>triangular mesh</td></tr>
<tr><td>DEM (Build DEM)</td>
<td>&ldquo;a 2.5D model of a surface represented in a form of a regular
grid, with height values stored per every cell of the grid&rdquo;
(p. 62).  DEM is the generic term (Granshaw, 2020, p. 175), and raster
DEM keeps the grid of Metashape apart from the triangular mesh, which
models the surface too.</td>
<td>raster DEM</td></tr>
<tr><td>Region (Resize Region, Move Region, and Rotate Region)</td>
<td>The bounding box of the reconstruction volume (p. 46).  In computer
vision a region is a connected part of an image (Granshaw, 2020,
p. 250).</td>
<td>region (bounding box)</td></tr>
</table>

<h3>References</h3>
<p>Agisoft LLC (2026). <i>Agisoft Metashape User Manual: Professional
Edition, Version 2.3.</i>
<a href="https://www.agisoft.com/pdf/metashape-pro_2_3_en.pdf">
https://www.agisoft.com/pdf/metashape-pro_2_3_en.pdf</a></p>
<p>European Union (2014). Directive 2014/32/EU of the European
Parliament and of the Council of 26 February 2014 on the harmonisation
of the laws of the Member States relating to the making available on
the market of measuring instruments (recast). <i>Official Journal of
the European Union</i> L 96, 149&ndash;250.</p>
<p>Fiete, R.D. (2004). Elements of photogrammetric optics. In: McGlone,
J.C. (Ed.), <i>Manual of Photogrammetry</i>, 5th edition. American
Society for Photogrammetry and Remote Sensing, Bethesda, MD,
317&ndash;398.</p>
<p>Fiete, R.D. (2010). <i>Modeling the Imaging Chain of Digital
Cameras</i>. Tutorial Texts in Optical Engineering TT92. SPIE,
Bellingham, WA.
<a href="https://doi.org/10.1117/3.868276">doi:10.1117/3.868276</a></p>
<p>F&ouml;rstner, W. and Wrobel, B.P. (2016). <i>Photogrammetric
Computer Vision: Statistics, Geometry, Orientation and
Reconstruction</i>. Geometry and Computing 11. Springer, Cham.
<a href="https://doi.org/10.1007/978-3-319-11550-4">doi:10.1007/978-3-319-11550-4</a></p>
<p>Ghilani, C.D. (2010). <i>Adjustment Computations: Spatial Data
Analysis</i>, 5th edition. John Wiley &amp; Sons, Hoboken.</p>
<p>Granshaw, S.I. (2020). Photogrammetric terminology: fourth edition.
<i>The Photogrammetric Record</i> 35(170), 143&ndash;288.
<a href="https://doi.org/10.1111/phor.12314">doi:10.1111/phor.12314</a></p>
<p>International Organization for Standardization (2019). <i>ISO
19111:2019, Geographic information, Referencing by coordinates.</i> ISO,
Geneva.</p>
<p>International Organization for Standardization (2021). <i>ISO
10360-13:2021, Geometrical product specifications (GPS), Acceptance and
reverification tests for coordinate measuring systems (CMS), Part 13:
Optical 3D CMS.</i> ISO, Geneva.</p>
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
<p>Kraus, K. (2007). <i>Photogrammetry: Geometry from Images and Laser
Scans</i>, 2nd edition. Walter de Gruyter, Berlin.</p>
<p>Lowe, D.G. (2004). Distinctive image features from scale-invariant
keypoints. <i>International Journal of Computer Vision</i> 60(2),
91&ndash;110.
<a href="https://doi.org/10.1023/B:VISI.0000029664.99615.94">
doi:10.1023/B:VISI.0000029664.99615.94</a></p>
<p>Luhmann, T., Robson, S., Kyle, S. and Boehm, J. (2023).
<i>Close-Range Photogrammetry and 3D Imaging</i>, 4th edition. De
Gruyter, Berlin and Boston.</p>
<p>Schowengerdt, R.A. (2007). <i>Remote Sensing: Models and Methods for
Image Processing</i>, 3rd edition. Academic Press, Burlington, MA.</p>
<p>Smith, A.R. (1995). <i>A Pixel Is Not A Little Square, A Pixel Is
Not A Little Square, A Pixel Is Not A Little Square! (And a Voxel is
Not a Little Cube).</i> Technical Memo 6, Microsoft, 17 July 1995.</p>
<p>Smith, A.R. (2021). <i>A Biography of the Pixel.</i> The MIT Press,
Cambridge, MA.</p>
<p>Verhoeven, G.J.J. (2018a). Pixels &ndash; So basic but so confusing.
<i>AARGnews</i> 56, 28&ndash;33.
<a href="https://doi.org/10.5281/zenodo.4024319">doi:10.5281/zenodo.4024319</a></p>
<p>Verhoeven, G.J.J. (2018b). Resolving some spatial resolution issues
&ndash; Part 1: Between line pairs and sampling distances. <i>AARGnews</i> 57,
25&ndash;34.
<a href="https://doi.org/10.5281/zenodo.1465017">doi:10.5281/zenodo.1465017</a></p>
<p>Verhoeven, G.J.J. (2019). Resolving some spatial resolution issues
&ndash; Part 2: When diffraction takes over. <i>AARGnews</i> 59,
12&ndash;23.
<a href="https://doi.org/10.5281/zenodo.3518178">doi:10.5281/zenodo.3518178</a></p>
<p>Verhoeven, G.J. and Ressl, C. (2026). From scale bars to stochastic
distance constraints: MPE-based weighting of reference distances in SfM
bundle adjustment. In preparation.</p>
<p>Vollmerhausen, R.H., Reago, D.A., Jr. and Driggers, R.G. (2010).
<i>Analysis and Evaluation of Sampled Imaging Systems</i>. Tutorial
Texts in Optical Engineering TT87. SPIE, Bellingham, WA.
<a href="https://doi.org/10.1117/3.853462">doi:10.1117/3.853462</a></p>
"""


# The entries of the page that the About sections of Manual and
# Terminology link to: (name of the anchor, heading of the entry as the
# page writes it in bold, the forms of the term that link to it).  A form
# with a capital letter after its first character, an abbreviation such
# as GSD or SfM, is matched in its case, any other form in any case, and
# every form also with the plural ending s or es.
TERMINOLOGY_LINKS = (
    ("term-pixel", "Pixel.", ("pixel",)),
    ("term-pixel-coordinates", "Pixel indices and pixel coordinates.", ("pixel coordinates", "pixel indices")),
    ("term-px", "px.", ("px",)),
    ("term-photosite", "Photosite.", ("photosite",)),
    ("term-photosite-pitch", "Photosite pitch p.", ("photosite pitch",)),
    ("term-photosite-size", "Photosite size.", ("photosite size",)),
    ("term-ifov", "Instantaneous field of view (IFOV).", ("instantaneous field of view", "IFOV")),
    ("term-gsd", "Ground sampling distance (GSD).", ("ground sampling distance", "GSD")),
    ("term-spatial-resolution", "Spatial resolution.", ("spatial resolution",)),
    ("term-gifov", "Ground-projected instantaneous field of view (GIFOV).",
     ("ground-projected instantaneous field of view", "GIFOV")),
    ("term-image-footprint", "Image footprint.", ("image footprint", "footprint")),
    ("term-image-scale-number", "Image scale number m.", ("image scale number",)),
    ("term-perspective-centre", "Perspective centre O.", ("perspective centre",)),
    ("term-principal-distance", "Principal distance c.", ("principal distance",)),
    ("term-camera-coordinates", "Camera coordinates and normalised coordinates.",
     ("camera coordinates", "normalised coordinates")),
    ("term-orientation", "Orientation.", ("interior orientation group", "image collection", "oriented image",
                                         "exterior orientation", "interior orientation")),
    ("term-sfm", "Structure from motion (SfM) and multi-view stereo (MVS).",
     ("structure from motion", "SfM", "multi-view stereo")),
    ("term-keypoint", "Keypoint.", ("keypoint",)),
    ("term-tie-point", "Tie point, 3D tie point, and 2D tie point.", ("3D tie point", "2D tie point", "tie point")),
    ("term-crs", "Coordinate system, coordinate reference system (CRS), and datum.",
     ("coordinate reference system", "CRS", "datum")),
    ("term-scale-bar", "Scale bar.", ("scale bar",)),
    ("term-reference-distance", "Reference distance.", ("reference distance",)),
    ("term-metashape-scale-bar", "Metashape scale bar.", ("Metashape scale bar",)),
    ("term-accuracy", "Accuracy, trueness, and precision.", ("trueness", "precision")),
    ("term-measurement-error", "Measurement error, residual, and check discrepancy.",
     ("measurement error", "check discrepancy", "residual")),
    ("term-a-priori-standard-deviation", "A priori standard deviation and standard uncertainty.",
     ("a priori standard deviation", "standard uncertainty")),
    ("term-standard-error", "Standard error (SE).", ("standard error",)),
    ("term-mpe", "Maximum permissible error (MPE).", ("maximum permissible error", "MPE")),
    ("term-repeatability", "Repeatability, intermediate precision, and reproducibility.",
     ("repeatability", "intermediate precision", "reproducibility")),
    ("term-outlier", "Outlier.", ("outlier",)),
    ("term-sigma-nought", "Sigma nought &sigma;<sub>0</sub>.", ("sigma nought",)),
)

# Elements whose text is never linked: links themselves, headings,
# and code.
UNLINKED_ELEMENTS = ("a", "h1", "h2", "h3", "h4", "h5", "h6", "code", "pre")

# Names that a link never splits: the tools of MetaFOCUS and the names of
# Metashape that hold a term, so that a term is linked where it is used
# as a term (without them, the first "Scale Bar" of a section, in the
# name Scale Bar A Priori Uncertainty, is linked to the physical scale
# bar).
UNSPLIT_NAMES = (
    "SfM Log Parser", "Interior Orientation Viewer", "Tie Point Covariance Exporter", "Image Parameter Exporter",
    "Marker Residual Inspector", "Unpinned Marker Projection Remover", "Scale Bar A Priori Uncertainty",
    "Gradual Selection Filter", "Image Subset Selector", "Image Filter", "Region Tools", "Image Footprint Generator",
    "GSD Value Exporter", "GSD Visualiser", "Manual and Terminology",
    "Metashape Functions for Optimising, Characterising, and Understanding Structure from Motion",
    "Clean Tie Points", "Tie point accuracy", "Tie point limit", "Keypoint limit", "Key point limit",
    "Scale bar accuracy")


def TerminologyHtmlWithAnchors(html=None):
    """The page (the module's own unless *html* is given) with an anchor
    before the heading of every entry of TERMINOLOGY_LINKS, the target of
    the links of the About sections."""
    html = _TERMINOLOGY_HTML if html is None else html
    for anchorName, heading, forms in TERMINOLOGY_LINKS:
        pattern = r"<p>(\s*)<b>" + r"\s+".join(re.escape(word) for word in heading.split()) + r"</b>"
        html, count = re.subn(pattern, lambda match, name=anchorName: '<p><a name="{}"></a>{}{}'.format(
            name, match.group(1), match.group(0)[len("<p>") + len(match.group(1)):]), html, count=1)
    return html


def TermFormPattern(form):
    """The regular expression of one form of a term: whole words, any run
    of white space between them, an optional plural ending, in its case
    for an abbreviation and in any case otherwise."""
    body = r"\s+".join(re.escape(word) for word in form.split()) + r"(?:s|es)?"
    isAbbreviation = any(character.isupper() for character in form[1:])
    return ("(?:" if isAbbreviation else "(?i:") + r"(?<![\w-])" + body + r"(?![\w-]))"


def UnsplitNamesPattern(names=UNSPLIT_NAMES):
    """The regular expression of the names that a link never splits, in
    their case, whole words, any run of white space between the words."""
    return "|".join(r"(?<![\w-])" + r"\s+".join(re.escape(word) for word in name.split()) + r"(?![\w-])"
                    for name in sorted(names, key=len, reverse=True))


def TermFormsPattern(links=TERMINOLOGY_LINKS, names=UNSPLIT_NAMES):
    """One regular expression of every form of *links*, longest first, so
    that Metashape scale bar wins over scale bar, with the form's index
    as the name of its group, and the anchor of each index.  The names
    that a link never splits come first, in the group "unsplit", so that a
    form inside such a name is not matched."""
    forms = sorted(((form, anchorName) for anchorName, _, termForms in links for form in termForms),
                   key=lambda formAndAnchor: -len(formAndAnchor[0]))
    pattern = "|".join("(?P<form{}>{})".format(index, TermFormPattern(form)) for index, (form, _) in enumerate(forms))
    if names:
        pattern = "(?P<unsplit>{})|".format(UnsplitNamesPattern(names)) + pattern
    return re.compile(pattern), {"form{}".format(index): anchorName for index, (_, anchorName) in enumerate(forms)}


def LinkTerminologyTerms(html, links=TERMINOLOGY_LINKS):
    """*html* with the first appearance of every term of *links* linked to
    its entry of the page (#anchor), outside links, headings, and code, so
    that a reader of an About section can jump to the definition and come
    back.  Each term is linked once, the first time one of its forms
    appears.  A form inside one of the UNSPLIT_NAMES is not linked."""
    pattern, anchorsByGroup = TermFormsPattern(links)
    linkedAnchors = set()
    unlinkedDepth = 0
    pieces = []
    for token in re.split(r"(<[^>]*>)", html):
        if token.startswith("<"):
            nameMatch = re.match(r"<\s*(/?)\s*([A-Za-z0-9]+)", token)
            if nameMatch and nameMatch.group(2).lower() in UNLINKED_ELEMENTS and not token.endswith("/>"):
                unlinkedDepth += -1 if nameMatch.group(1) else 1
                unlinkedDepth = max(unlinkedDepth, 0)
            pieces.append(token)
            continue
        if unlinkedDepth or not token:
            pieces.append(token)
            continue
        position = 0
        for match in pattern.finditer(token):
            if match.lastgroup == "unsplit":
                continue
            anchorName = anchorsByGroup[match.lastgroup]
            if anchorName in linkedAnchors:
                continue
            linkedAnchors.add(anchorName)
            pieces.append(token[position:match.start()])
            pieces.append('<a href="#{}">{}</a>'.format(anchorName, match.group(0)))
            position = match.end()
        pieces.append(token[position:])
    return "".join(pieces)
