"""Read a vector or raster figure and write it as a raster image.

Vector files are rendered directly at the requested size, so they stay
sharp at any resolution:

* SVG is rendered with CairoSVG, which needs the Cairo library
  installed separately. Without Cairo, SVG falls back to PyMuPDF, which
  ignores clipping paths and replaces fonts it cannot find with Times,
  so some figures come out wrong; :func:`cairosvg_problem` says why
  Cairo is not used.
* PDF and XPS are rendered with PyMuPDF.
* EPS and PS are rendered with Ghostscript (through Pillow), which must
  be installed separately.
* EMF and WMF are rendered by Windows (through Pillow), Windows only.

Raster files are read with Pillow. Their pixels are kept unless the
options ask for a fixed width or height, or for resampling to the DPI.

A fixed width or height alone keeps the figure's aspect ratio; both
together set the exact output size.
"""

import enum
import functools
import logging
import math
import sys
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

import pymupdf
from PIL import EpsImagePlugin, Image, ImageColor, features

__all__ = [
    'CONVERTED_PREFIX',
    'DEFAULT_SOURCE_DPI',
    'GHOSTSCRIPT_SUFFIXES',
    'MAX_SIDE_PIXELS',
    'MM_PER_INCH',
    'POINTS_PER_INCH',
    'PYMUPDF_SUFFIXES',
    'RASTER_SUFFIXES',
    'SUPPORTED_SUFFIXES',
    'SVG_SUFFIX',
    'VECTOR_SUFFIXES',
    'WINDOWS_METAFILE_SUFFIXES',
    'ConversionError',
    'ConvertOptions',
    'OutputFile',
    'OutputFormat',
    'SizeUnit',
    'cairosvg_problem',
    'clean_stem',
    'convert_file',
    'find_figures',
    'is_supported',
    'is_vector',
    'read_figure',
]

# Messages go nowhere until an application sets up logging; the GUI
# writes them to a log file (see figconvert.logs).
_LOGGER = logging.getLogger(__name__)

# Vector sizes are measured in points: 1 pt = 1/72 inch.
POINTS_PER_INCH = 72.0
MM_PER_INCH = 25.4
# Raster files without DPI metadata are assumed to be at this DPI.
DEFAULT_SOURCE_DPI = 72.0
# Refuse to create images larger than this many pixels per side.
MAX_SIDE_PIXELS = 30_000
# Added in front of an output name that is already used in the save
# folder.
CONVERTED_PREFIX = '_converted_'

SVG_SUFFIX = '.svg'
# Files PyMuPDF renders; SVG only when CairoSVG cannot be used.
PYMUPDF_SUFFIXES = frozenset(
    {
        '.svg',
        '.pdf',
        '.xps',
        '.oxps',
    }
)
GHOSTSCRIPT_SUFFIXES = frozenset(
    {
        '.eps',
        '.ps',
        '.epsf',
        '.epsi',
    }
)
WINDOWS_METAFILE_SUFFIXES = frozenset(
    {
        '.emf',
        '.wmf',
    }
)
VECTOR_SUFFIXES = PYMUPDF_SUFFIXES | GHOSTSCRIPT_SUFFIXES | WINDOWS_METAFILE_SUFFIXES
RASTER_SUFFIXES = frozenset(
    {
        '.png',
        '.jpg',
        '.jpeg',
        '.jpe',
        '.jfif',
        '.tif',
        '.tiff',
        '.bmp',
        '.gif',
        '.webp',
        '.ico',
        '.ppm',
        '.pgm',
        '.pbm',
        '.tga',
        '.dib',
        '.jp2',
        '.pcx',
    }
)
SUPPORTED_SUFFIXES = VECTOR_SUFFIXES | RASTER_SUFFIXES

# Characters Windows does not allow in file names.
_INVALID_NAME_CHARS = r'<>:"/\|?*'
# What CairoSVG gets for a linked file it may not read: draws nothing.
_EMPTY_SVG = b'<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1"/>'
_DATA_URL_PREFIX = 'data:'
# Cairo pixel layout as Pillow raw mode: B, G, R, premultiplied A.
_CAIRO_RAW_MODE = 'BGRa'
_LITTLE_ENDIAN = 'little'
_FILE_SCHEME = 'file'
# Code points below this are control characters, invalid in file names.
_FIRST_PRINTABLE_CODE = 32
# Some files store a DPI of 0 or 1 when the real DPI is unknown.
_MAX_UNKNOWN_DPI = 1.0
# 16-bit samples are divided by this to fit in 8 bits.
_SIXTEEN_TO_EIGHT_BITS = 256
_WEBP_METHOD = 4
_WEBP_FEATURE = 'webp'

_ALPHA_CHANNEL = 'A'
# Pillow's image.info key for palette or single-colour transparency.
_TRANSPARENCY_KEY = 'transparency'
_MODE_GREY = 'L'
_MODE_GREY_ALPHA = 'LA'
_MODE_RGB = 'RGB'
_MODE_RGBA = 'RGBA'
_MODE_SIXTEEN_BIT_PREFIX = 'I;16'
_PLAIN_MODES = frozenset(
    {
        _MODE_RGB,
        _MODE_RGBA,
        _MODE_GREY,
        _MODE_GREY_ALPHA,
    }
)
_PREMULTIPLIED_ALPHA_MODES = frozenset(
    {
        'PA',
        'RGBa',
        'La',
    }
)
_WIDE_GREY_MODES = frozenset(
    {
        'I',
        'F',
    }
)
_ALPHA_MODES = frozenset(
    {
        _MODE_RGBA,
        _MODE_GREY_ALPHA,
    }
)


class OutputFormat(enum.Enum):
    """Raster formats that can be written."""

    PNG = 'PNG'
    JPEG = 'JPEG'
    TIFF = 'TIFF'
    BMP = 'BMP'
    WEBP = 'WEBP'

    @property
    def suffix(self) -> str:
        """File extension, with the dot."""
        return {
            OutputFormat.PNG: '.png',
            OutputFormat.JPEG: '.jpg',
            OutputFormat.TIFF: '.tif',
            OutputFormat.BMP: '.bmp',
            OutputFormat.WEBP: '.webp',
        }[self]

    @property
    def supports_alpha(self) -> bool:
        """Whether the format can store transparency."""
        return self in _ALPHA_FORMATS


_ALPHA_FORMATS = frozenset(
    {
        OutputFormat.PNG,
        OutputFormat.TIFF,
        OutputFormat.WEBP,
    }
)
_OUTPUT_SUFFIXES = frozenset(
    output_format.suffix for output_format in OutputFormat
)


class SizeUnit(enum.Enum):
    """Unit of a fixed output width or height."""

    PX = 'px'
    MM = 'mm'


@dataclass(frozen=True, slots=True, kw_only=True)
class ConvertOptions:
    """Settings for one batch.

    Attributes
    ----------
    output_format : OutputFormat, default OutputFormat.PNG
        Format to write.
    dpi : float, default 300.0
        Resolution written into the file, and used to render vector
        files when neither ``width`` nor ``height`` is set. It also
        turns a width or height in millimetres into pixels.
    width : float or None, default None
        Output width in ``size_unit``, or None for automatic. If only
        ``width`` is set, the height follows the figure's aspect ratio.
    height : float or None, default None
        Output height in ``size_unit``, or None for automatic. If only
        ``height`` is set, the width follows the figure's aspect ratio.
        With both set, the figure is scaled to exactly ``width`` x
        ``height``, so its aspect ratio may change. With neither set,
        the size comes from ``dpi``.
    size_unit : SizeUnit, default SizeUnit.PX
        Unit of ``width`` and ``height``. Millimetres become pixels at
        ``dpi``, so the image prints at that size.
    resample_raster : bool, default False
        When neither ``width`` nor ``height`` is set, resize raster
        files from their own DPI to ``dpi`` so their physical size stays
        the same. Otherwise only the DPI metadata changes.
    transparent : bool, default True
        Keep transparency when the format supports it. When False, or
        the format has no alpha channel, the image is laid on
        ``background``.
    background : str, default 'white'
        Background colour, any name or ``#rrggbb`` Pillow understands.
    quality : int, default 95
        JPEG and WEBP quality, 1 to 100.
    all_pages : bool, default False
        Convert every page of a multi-page PDF or XPS, not only the
        first.
    overwrite : bool, default False
        Replace an existing output file of the same name and format
        instead of adding the ``_converted_{n}_`` prefix.

    Raises
    ------
    ValueError
        If ``width`` or ``height`` is set but is not positive.
    """

    output_format: OutputFormat = OutputFormat.PNG
    dpi: float = 300.0
    width: float | None = None
    height: float | None = None
    size_unit: SizeUnit = SizeUnit.PX
    resample_raster: bool = False
    transparent: bool = True
    background: str = 'white'
    quality: int = 95
    all_pages: bool = False
    overwrite: bool = False

    def __post_init__(self) -> None:
        """Reject a fixed width or height that is not positive."""
        sides = (
            ('width', self.width),
            ('height', self.height),
        )
        for name, value in sides:
            if value is not None and value <= 0:
                raise ValueError(f'{name} must be positive, not {value}.')

    def pixel_size(self) -> tuple[int | None, int | None]:
        """Return the fixed width and height in pixels.

        Returns
        -------
        width : int or None
            Width in pixels, at least 1, or None if automatic.
        height : int or None
            Height in pixels, at least 1, or None if automatic.
        """
        return (
            self._to_pixels(self.width),
            self._to_pixels(self.height),
        )

    def _to_pixels(self, value: float | None) -> int | None:
        """Convert one side from ``size_unit`` to whole pixels."""
        if value is None:
            return None
        if self.size_unit is SizeUnit.MM:
            value = value / MM_PER_INCH * self.dpi
        return max(1, round(value))


@dataclass(frozen=True, slots=True, kw_only=True)
class OutputFile:
    """One image written by a conversion.

    Attributes
    ----------
    path : Path
        File written.
    width : int
        Image width in pixels.
    height : int
        Image height in pixels.
    """

    path: Path
    width: int
    height: int


class ConversionError(Exception):
    """A figure could not be read or written."""


def is_supported(path: Path) -> bool:
    """Return whether the file extension is one this tool can read."""
    return _lower_suffix(path) in SUPPORTED_SUFFIXES


def is_vector(path: Path) -> bool:
    """Return whether the file is a vector format."""
    return _lower_suffix(path) in VECTOR_SUFFIXES


def find_figures(folder: Path, recursive: bool = False) -> list[Path]:
    """List supported figures in a folder, sorted by name.

    Parameters
    ----------
    folder : Path
        Folder to search.
    recursive : bool, default False
        Also search sub-folders.

    Returns
    -------
    list of Path
        Supported files found, sorted case-insensitively by full path.
    """
    pattern = '**/*' if recursive else '*'
    figures = [
        path for path in folder.glob(pattern)
        if path.is_file() and is_supported(path)
    ]
    return sorted(figures, key=lambda path: str(path).lower())


def read_figure(path: Path, options: ConvertOptions) -> list[Image.Image]:
    """Read a figure as one or more images at the output size.

    Parameters
    ----------
    path : Path
        Figure to read.
    options : ConvertOptions
        Conversion settings.

    Returns
    -------
    list of PIL.Image.Image
        One image per page (one for single-page files).

    Raises
    ------
    ConversionError
        If the file cannot be read or rendered, or its type is not
        supported.
    """
    suffix = _lower_suffix(path)
    try:
        if suffix == SVG_SUFFIX:
            cairosvg, _problem = _cairosvg_status()
            if cairosvg is not None:
                _LOGGER.info('Rendering %s with CairoSVG', path)
                return [_render_cairosvg(cairosvg, path, options)]
        if suffix in PYMUPDF_SUFFIXES:
            _LOGGER.info('Rendering %s with PyMuPDF', path)
            return list(_render_pymupdf(path, options))
        if suffix in GHOSTSCRIPT_SUFFIXES:
            _LOGGER.info('Rendering %s with Ghostscript', path)
            return [_render_ghostscript(path, options)]
        if suffix in WINDOWS_METAFILE_SUFFIXES:
            _LOGGER.info('Rendering %s with Windows (EMF/WMF)', path)
            return [_render_metafile(path, options)]
        if suffix in RASTER_SUFFIXES:
            _LOGGER.info('Reading %s with Pillow', path)
            return [_read_raster(path, options)]
    except ConversionError:
        raise
    # Any other reader failure is reported as a conversion failure, so
    # one bad figure never stops a batch.
    except Exception as err:  # pylint: disable=broad-exception-caught
        raise ConversionError(str(err) or type(err).__name__) from err
    raise ConversionError(f"Unsupported file type: {suffix or '(none)'}")


def cairosvg_problem() -> str | None:
    """Say why SVG files cannot be rendered with CairoSVG.

    Returns
    -------
    str or None
        None if CairoSVG and the Cairo library work, so SVG files are
        rendered with CairoSVG. Otherwise the error from loading them;
        SVG files are then rendered with PyMuPDF instead.
    """
    _module, problem = _cairosvg_status()
    return problem


def clean_stem(text: str) -> str:
    """Turn typed text into a safe file name without extension.

    A trailing figure or output extension (``fig.png``, ``fig.svg``) is
    dropped, characters Windows forbids are replaced by ``_``, and
    surrounding spaces and dots are removed.

    Parameters
    ----------
    text : str
        Name typed by the user.

    Returns
    -------
    str
        The cleaned name; empty if nothing usable is left.
    """
    name = text.strip()
    suffix = _lower_suffix(Path(name))
    if suffix in SUPPORTED_SUFFIXES or suffix in _OUTPUT_SUFFIXES:
        name = name[: -len(suffix)]
    name = ''.join(
        '_' if _is_forbidden_in_name(character) else character
        for character in name
    )
    return name.strip(' .')


def convert_file(
    source: Path,
    out_dir: Path,
    options: ConvertOptions,
    out_stem: str | None = None,
    reserved: set[str] | None = None,
) -> list[OutputFile]:
    """Convert one figure and write the result into a folder.

    Parameters
    ----------
    source : Path
        Figure to convert.
    out_dir : Path
        Folder to write into; created if missing.
    options : ConvertOptions
        Conversion settings.
    out_stem : str or None, default None
        Output file name without extension; the source name if None.
        Multi-page files add ``_p1``, ``_p2`` … to it.
    reserved : set of str or None, default None
        Case-folded names (without extension) already written in this
        batch. Names written here are added to it, so later figures in
        the batch never overwrite them.

    Returns
    -------
    list of OutputFile
        Files written, one per page.

    Raises
    ------
    ConversionError
        If the figure cannot be read, rendered or saved.
    """
    if (
        options.output_format is OutputFormat.WEBP
        and not features.check(_WEBP_FEATURE)
    ):
        raise ConversionError('This Pillow build cannot write WEBP.')
    stem = clean_stem(out_stem) if out_stem else ''
    stem = stem or source.stem
    reserved = set() if reserved is None else reserved
    images = read_figure(source, options)
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[OutputFile] = []
    is_multi_page = len(images) > 1
    for page_number, page_image in enumerate(images, start=1):
        image = _prepare_for_format(page_image, options)
        page_stem = f'{stem}_p{page_number}' if is_multi_page else stem
        target = _output_path(source, out_dir, page_stem, options, reserved)
        try:
            image.save(
                target,
                options.output_format.value,
                **_save_kwargs(options),
            )
        # Pillow raises many error types; report any of them as this
        # figure's failure.
        except Exception as err:  # pylint: disable=broad-exception-caught
            raise ConversionError(f'Cannot save {target.name}: {err}') from err
        reserved.add(target.stem.casefold())
        _LOGGER.info('Wrote %s (%d x %d px)', target, image.width, image.height)
        written.append(
            OutputFile(
                path=target,
                width=image.width,
                height=image.height,
            )
        )
    return written


def _lower_suffix(path: Path) -> str:
    """Return the file extension in lower case, with the dot."""
    return (
        path.suffix
        .lower()
    )


def _is_forbidden_in_name(character: str) -> bool:
    """Return whether Windows forbids a character in file names."""
    return (
        character in _INVALID_NAME_CHARS
        or ord(character) < _FIRST_PRINTABLE_CODE
    )


def _fixed_size(
    width: float,
    height: float,
    options: ConvertOptions,
) -> tuple[float, float] | None:
    """Return the output size that a fixed width or height asks for.

    Parameters
    ----------
    width : float
        Figure width, in any unit.
    height : float
        Figure height, in the same unit as ``width``.
    options : ConvertOptions
        Conversion settings whose ``width`` and ``height`` set the size.

    Returns
    -------
    tuple of float or None
        Output width and height in pixels, not yet rounded; a side left
        automatic follows the figure's aspect ratio. None if neither
        ``width`` nor ``height`` is set.
    """
    width_px, height_px = options.pixel_size()
    if width_px is not None and height_px is not None:
        return (
            float(width_px),
            float(height_px),
        )
    if width_px is not None:
        return (
            float(width_px),
            height * width_px / width,
        )
    if height_px is not None:
        return (
            width * height_px / height,
            float(height_px),
        )
    return None


def _vector_zoom(
    width_pt: float,
    height_pt: float,
    options: ConvertOptions,
) -> tuple[float, float]:
    """Return the scales from points to output pixels for a vector page.

    Parameters
    ----------
    width_pt : float
        Page width in points.
    height_pt : float
        Page height in points.
    options : ConvertOptions
        Conversion settings that set the output size.

    Returns
    -------
    zoom_x : float
        Horizontal output pixels per point.
    zoom_y : float
        Vertical output pixels per point. It differs from ``zoom_x``
        only when both a fixed width and a fixed height are set.

    Raises
    ------
    ConversionError
        If the output would exceed ``MAX_SIDE_PIXELS`` on a side.
    """
    fixed = _fixed_size(width_pt, height_pt, options)
    if fixed is None:
        zoom_x = zoom_y = options.dpi / POINTS_PER_INCH
    else:
        zoom_x = fixed[0] / width_pt
        zoom_y = fixed[1] / height_pt
    _LOGGER.debug(
        'Page %.1f x %.1f pt, output %d x %d px',
        width_pt,
        height_pt,
        round(width_pt * zoom_x),
        round(height_pt * zoom_y),
    )
    _check_size(round(width_pt * zoom_x), round(height_pt * zoom_y))
    return zoom_x, zoom_y


def _raster_target_size(image: Image.Image, options: ConvertOptions) -> tuple[int, int]:
    """Return the output pixel size for a raster image.

    Parameters
    ----------
    image : PIL.Image.Image
        Opened raster image.
    options : ConvertOptions
        Conversion settings that set the output size.

    Returns
    -------
    tuple of int
        Output width and height in pixels, each at least 1.

    Raises
    ------
    ConversionError
        If the output would exceed ``MAX_SIDE_PIXELS`` on a side.
    """
    width, height = image.size
    fixed = _fixed_size(width, height, options)
    if fixed is not None:
        size = (
            round(fixed[0]),
            round(fixed[1]),
        )
    elif options.resample_raster:
        factor = options.dpi / _source_dpi(image)
        size = (
            round(width * factor),
            round(height * factor),
        )
    else:
        size = (
            width,
            height,
        )
    size = (
        max(1, size[0]),
        max(1, size[1]),
    )
    _check_size(*size)
    return size


def _source_dpi(image: Image.Image) -> float:
    """Return the horizontal DPI stored in an image, or the default.

    Parameters
    ----------
    image : PIL.Image.Image
        Opened raster image.

    Returns
    -------
    float
        Stored horizontal DPI, or ``DEFAULT_SOURCE_DPI`` if it is
        missing, unreadable or a placeholder of 0 or 1.
    """
    dpi = (
        image.info
        .get('dpi')
    )
    try:
        value = float(dpi[0]) if dpi else 0.0
    except (TypeError, ValueError, IndexError):
        value = 0.0
    return value if value > _MAX_UNKNOWN_DPI else DEFAULT_SOURCE_DPI


def _check_size(width: int, height: int) -> None:
    """Refuse output sizes that would exhaust memory.

    Parameters
    ----------
    width : int
        Output width in pixels.
    height : int
        Output height in pixels.

    Raises
    ------
    ConversionError
        If either side exceeds ``MAX_SIDE_PIXELS``.
    """
    if max(width, height) > MAX_SIDE_PIXELS:
        raise ConversionError(
            f'Output would be {width} x {height} px; the limit is '
            f'{MAX_SIDE_PIXELS} px per side. Lower the DPI or size.'
        )


def _render_pymupdf(path: Path, options: ConvertOptions) -> Iterator[Image.Image]:
    """Render SVG, PDF or XPS pages with PyMuPDF.

    Parameters
    ----------
    path : Path
        SVG, PDF or XPS file.
    options : ConvertOptions
        Conversion settings; ``all_pages`` chooses every page or the
        first only.

    Yields
    ------
    PIL.Image.Image
        One RGB or RGBA image per rendered page.

    Raises
    ------
    ConversionError
        If the file cannot be opened, has no pages, or a page would be
        too large.
    """
    try:
        # PyMuPDF's constructors carry no type annotations.
        document = pymupdf.open(path)  # type: ignore[no-untyped-call]
    # PyMuPDF raises several unrelated error types for unreadable files.
    except Exception as err:  # pylint: disable=broad-exception-caught
        raise ConversionError(f'Cannot open: {err}') from err
    with document:
        if document.page_count == 0:
            raise ConversionError('The file has no pages.')
        pages = range(document.page_count) if options.all_pages else range(1)
        alpha = options.transparent and options.output_format.supports_alpha
        for page_number in pages:
            page = document[page_number]
            zoom_x, zoom_y = _vector_zoom(page.rect.width, page.rect.height, options)
            # PyMuPDF's constructors carry no type annotations.
            matrix = pymupdf.Matrix(zoom_x, zoom_y)  # type: ignore[no-untyped-call]
            pixmap = page.get_pixmap(matrix=matrix, alpha=alpha)
            mode = _MODE_RGBA if pixmap.alpha else _MODE_RGB
            yield Image.frombytes(
                mode,
                (
                    pixmap.width,
                    pixmap.height,
                ),
                pixmap.samples,
            )


@functools.cache
def _cairosvg_status() -> tuple[ModuleType | None, str | None]:
    """Load CairoSVG once, or say why it cannot be loaded.

    Returns
    -------
    module : types.ModuleType or None
        The ``cairosvg`` module, or None if it cannot be used.
    problem : str or None
        Why it cannot be used, or None if it can. Importing it fails
        with OSError when the Cairo library itself is missing.
    """
    try:
        # Imported here, not at the top, because the import fails on
        # machines without Cairo and SVG then falls back to PyMuPDF.
        import cairosvg  # pylint: disable=import-outside-toplevel
    # A broken install can fail with any error, not only ImportError or
    # OSError. Catching all of them means the result is always cached
    # and SVG still falls back to PyMuPDF.
    except Exception as err:  # pylint: disable=broad-exception-caught
        _LOGGER.warning(
            'CairoSVG cannot be used, so SVG falls back to PyMuPDF: %s',
            err,
            exc_info=True,
        )
        return None, str(err) or type(err).__name__
    _LOGGER.info(
        'CairoSVG %s loaded from %s',
        getattr(cairosvg, '__version__', '?'),
        cairosvg.__file__,
    )
    return cairosvg, None


def _render_cairosvg(
    cairosvg: ModuleType,
    path: Path,
    options: ConvertOptions,
) -> Image.Image:
    """Render an SVG with CairoSVG.

    CairoSVG renders at 72 DPI, so 1 SVG unit is one point, as with
    PyMuPDF. CairoSVG works out the figure's size and aspect ratio
    itself, so the drawing is never stretched to a size another parser
    measured. PyMuPDF's size is only used to refuse outputs that would
    be too large before rendering.

    The pixels are read straight from Cairo's image in memory, which
    is about twice as fast as having CairoSVG write a PNG and decoding
    it again.

    Images and style sheets the SVG links to are loaded only from the
    SVG's own folder and its sub-folders, or from ``data:`` URLs.

    Parameters
    ----------
    cairosvg : types.ModuleType
        The loaded ``cairosvg`` module.
    path : Path
        SVG file.
    options : ConvertOptions
        Conversion settings that set the output size.

    Returns
    -------
    PIL.Image.Image
        The rendered figure in RGBA mode.

    Raises
    ------
    ConversionError
        If the file cannot be opened or the output would be too large.
    """
    width_pt, height_pt = _pymupdf_page_size(path)
    zoom_x, zoom_y = _vector_zoom(width_pt, height_pt, options)
    width_px, height_px = options.pixel_size()
    target: tuple[int, int] | None = None
    size_arguments: dict[str, float]
    if width_px is not None and height_px is not None:
        # CairoSVG scales both sides alike, so render at one scale and
        # stretch to the exact size afterwards.
        target = (
            width_px,
            height_px,
        )
        size_arguments = {'scale': _render_zoom(width_pt, height_pt, zoom_x, zoom_y)}
    elif width_px is not None:
        size_arguments = {'output_width': width_px}
    elif height_px is not None:
        size_arguments = {'output_height': height_px}
    else:
        size_arguments = {'scale': zoom_x}
    # The URL must be the standard, percent-encoded one. CairoSVG keys
    # its cache of parsed documents on it, and with any other form it
    # re-reads and re-parses the whole file for every
    # ``<use href="#id">``; Matplotlib draws each piece of text that
    # way, which made a figure several times slower.
    file_url = (
        path
        .resolve()
        .as_uri()
    )
    tree = cairosvg.parser.Tree(
        bytestring=path.read_bytes(),
        url=file_url,
        url_fetcher=_local_fetcher(path.parent),
    )
    # Drawing happens while the surface is created; output=None keeps
    # the result in memory instead of writing a PNG.
    surface = cairosvg.surface.PNGSurface(
        tree,
        None,
        POINTS_PER_INCH,
        **size_arguments,
    )
    try:
        rendered = _cairo_surface_to_image(surface.cairo)
    finally:
        surface.finish()
    if target is not None:
        rendered = _resize_to(rendered, *target)
    return rendered


def _pymupdf_page_size(path: Path) -> tuple[float, float]:
    """Return the first page's size in points, as PyMuPDF reads it.

    Parameters
    ----------
    path : Path
        File PyMuPDF can open.

    Returns
    -------
    width_pt : float
        Page width in points.
    height_pt : float
        Page height in points.

    Raises
    ------
    ConversionError
        If the file cannot be opened or has no pages.
    """
    try:
        # PyMuPDF's constructors carry no type annotations.
        document = pymupdf.open(path)  # type: ignore[no-untyped-call]
    # PyMuPDF raises several unrelated error types for unreadable files.
    except Exception as err:  # pylint: disable=broad-exception-caught
        raise ConversionError(f'Cannot open: {err}') from err
    with document:
        if document.page_count == 0:
            raise ConversionError('The file has no pages.')
        rect = document[0].rect
        return rect.width, rect.height


def _local_fetcher(folder: Path) -> Callable[[str, str], bytes]:
    """Return a CairoSVG URL fetcher limited to one folder.

    Parameters
    ----------
    folder : Path
        Folder of the SVG being rendered.

    Returns
    -------
    callable
        Takes a URL and a resource type and returns the resource's
        bytes. It reads ``data:`` URLs and files inside ``folder`` or
        its sub-folders; anything else, including web addresses, gives
        an empty SVG, which CairoSVG draws as nothing.
    """
    root = folder.resolve()

    def fetch(url: str, _resource_type: str) -> bytes:
        """Return the bytes of a linked resource, or an empty SVG."""
        if url.startswith(_DATA_URL_PREFIX):
            # The standard library decodes data: URLs itself, with no
            # network or file access.
            with urllib.request.urlopen(url) as response:
                data: bytes = response.read()
            return data
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != _FILE_SCHEME or parsed.netloc:
            return _EMPTY_SVG
        target = (
            Path(urllib.request.url2pathname(parsed.path))
            .resolve()
        )
        if target.is_relative_to(root) and target.is_file():
            return target.read_bytes()
        return _EMPTY_SVG

    return fetch


def _cairo_surface_to_image(cairo_surface: Any) -> Image.Image:
    """Copy a Cairo image surface into a Pillow image.

    Cairo stores each pixel as one native-endian 32-bit number with
    premultiplied alpha, which on little-endian machines (every
    Windows, macOS and Linux desktop) lies in memory as B, G, R, A.
    Pillow's ``BGRa`` raw mode reads exactly that and removes the
    premultiplication. Unlike opening an image file, this has no
    decompression-bomb limit, so large outputs are not refused.

    Parameters
    ----------
    cairo_surface : cairocffi.ImageSurface
        Surface in Cairo's ARGB32 format, already drawn.

    Returns
    -------
    PIL.Image.Image
        A copy of the pixels in RGBA mode.

    Raises
    ------
    ConversionError
        If the surface exceeds ``MAX_SIDE_PIXELS`` on a side, or the
        machine is big-endian.
    """
    if sys.byteorder != _LITTLE_ENDIAN:
        raise ConversionError('CairoSVG output is read on little-endian machines only.')
    width = cairo_surface.get_width()
    height = cairo_surface.get_height()
    _check_size(width, height)
    # Finish any drawing Cairo still holds back before reading memory.
    cairo_surface.flush()
    return Image.frombuffer(
        _MODE_RGBA,
        (
            width,
            height,
        ),
        bytes(cairo_surface.get_data()),
        'raw',
        _CAIRO_RAW_MODE,
        cairo_surface.get_stride(),
        1,
    )


def _render_zoom(
    width_pt: float,
    height_pt: float,
    zoom_x: float,
    zoom_y: float,
) -> float:
    """Return one scale for a renderer that scales both sides alike.

    The larger of the two scales keeps both sides sharp before the
    image is resized to the exact size. It is lowered, if needed, so
    the image rendered first also stays within ``MAX_SIDE_PIXELS`` per
    side; the resize then enlarges the other side instead.

    Parameters
    ----------
    width_pt : float
        Page width in points.
    height_pt : float
        Page height in points.
    zoom_x : float
        Horizontal output pixels per point.
    zoom_y : float
        Vertical output pixels per point.

    Returns
    -------
    float
        Pixels per point to render at, on both sides.
    """
    limit = MAX_SIDE_PIXELS / max(width_pt, height_pt)
    return min(max(zoom_x, zoom_y), limit)


def _render_ghostscript(path: Path, options: ConvertOptions) -> Image.Image:
    """Render EPS or PS with Ghostscript, through Pillow.

    Parameters
    ----------
    path : Path
        EPS or PS file.
    options : ConvertOptions
        Conversion settings that set the output size and transparency.

    Returns
    -------
    PIL.Image.Image
        The rendered figure at the exact output size.

    Raises
    ------
    ConversionError
        If Ghostscript is not installed or the output would be too
        large.
    """
    if not EpsImagePlugin.has_ghostscript():
        raise ConversionError(
            'EPS/PS needs Ghostscript. Install it from ghostscript.com '
            'and make sure gswin64c (Windows) or gs is on PATH.'
        )
    with Image.open(path) as image:
        # Pillow reports the EPS size in points.
        width_pt, height_pt = image.size
        zoom_x, zoom_y = _vector_zoom(width_pt, height_pt, options)
        zoom = _render_zoom(width_pt, height_pt, zoom_x, zoom_y)
        # Ghostscript renders at a whole-number scale; render at or
        # above the target, then resize down to the exact size. Round
        # down instead if rounding up would pass the size limit.
        scale = math.ceil(zoom)
        if scale * max(width_pt, height_pt) > MAX_SIDE_PIXELS:
            scale = math.floor(zoom)
        scale = max(1, scale)
        transparent = options.transparent and options.output_format.supports_alpha
        # Image.open returns an EpsImageFile here, whose load() takes
        # these arguments; the stubs only know the base ImageFile.
        image.load(scale=scale, transparency=transparent)  # type: ignore[call-arg]
        rendered = image.copy()
    return _resize_to(rendered, width_pt * zoom_x, height_pt * zoom_y)


def _render_metafile(path: Path, options: ConvertOptions) -> Image.Image:
    """Render EMF or WMF with Windows, through Pillow.

    Parameters
    ----------
    path : Path
        EMF or WMF file.
    options : ConvertOptions
        Conversion settings that set the output size.

    Returns
    -------
    PIL.Image.Image
        The rendered figure at the exact output size.

    Raises
    ------
    ConversionError
        If not running on Windows or the output would be too large.
    """
    if sys.platform != 'win32':
        raise ConversionError('EMF/WMF can only be rendered on Windows.')
    with Image.open(path) as image:
        # Pillow reports the size at the file's own DPI (default 72).
        stored_dpi = (
            image.info
            .get('dpi', POINTS_PER_INCH)
        )
        file_dpi = float(stored_dpi or POINTS_PER_INCH)
        width_pt = image.size[0] * POINTS_PER_INCH / file_dpi
        height_pt = image.size[1] * POINTS_PER_INCH / file_dpi
        zoom_x, zoom_y = _vector_zoom(width_pt, height_pt, options)
        zoom = _render_zoom(width_pt, height_pt, zoom_x, zoom_y)
        # Windows renders with one DPI for both sides; render at that
        # scale, then resize to the exact size.
        # Image.open returns a WmfStubImageFile here, whose load() takes
        # a DPI; the stubs only know the base ImageFile. When mypy
        # checks for another platform this line is unreachable, so the
        # ignore is unused there.
        image.load(  # type: ignore[call-arg, unused-ignore]
            # At least 1: a tiny output from a huge page rounds to 0.
            dpi=max(1, round(zoom * POINTS_PER_INCH)),
        )
        rendered = image.copy()
    return _resize_to(rendered, width_pt * zoom_x, height_pt * zoom_y)


def _resize_to(image: Image.Image, width: float, height: float) -> Image.Image:
    """Resize an image to a rounded target size if it differs.

    Parameters
    ----------
    image : PIL.Image.Image
        Rendered image.
    width : float
        Target width in pixels, rounded and at least 1.
    height : float
        Target height in pixels, rounded and at least 1.

    Returns
    -------
    PIL.Image.Image
        The image itself if it already has the target size, otherwise
        a resized copy.
    """
    target = (
        max(1, round(width)),
        max(1, round(height)),
    )
    if image.size == target:
        return image
    return image.resize(target, Image.Resampling.LANCZOS)


def _read_raster(path: Path, options: ConvertOptions) -> Image.Image:
    """Open a raster file and resize it if the options ask for it.

    Parameters
    ----------
    path : Path
        Raster image file.
    options : ConvertOptions
        Conversion settings that set the output size.

    Returns
    -------
    PIL.Image.Image
        The image in RGB, RGBA, L or LA mode, detached from the file.

    Raises
    ------
    ConversionError
        If the file cannot be read or the output would be too large.
    """
    try:
        with Image.open(path) as opened:
            opened.load()
            size = _raster_target_size(opened, options)
            image = _normalise_mode(opened)
            if size != image.size:
                image = image.resize(size, Image.Resampling.LANCZOS)
            else:
                image = image.copy()
    except ConversionError:
        raise
    # Pillow raises many error types for unreadable images.
    except Exception as err:  # pylint: disable=broad-exception-caught
        raise ConversionError(f'Cannot read image: {err}') from err
    return image


def _normalise_mode(image: Image.Image) -> Image.Image:
    """Convert unusual modes (palette, CMYK, 16-bit) to RGB or RGBA.

    Parameters
    ----------
    image : PIL.Image.Image
        Opened raster image.

    Returns
    -------
    PIL.Image.Image
        The image itself if it is already RGB, RGBA, L or LA; 16- and
        32-bit greyscale scaled down to L; anything else converted to
        RGBA if it has transparency, RGB otherwise.
    """
    if image.mode in _PLAIN_MODES:
        return image
    if (
        image.mode.startswith(_MODE_SIXTEEN_BIT_PREFIX)
        or image.mode in _WIDE_GREY_MODES
    ):
        return (
            image
            .point(lambda value: value / _SIXTEEN_TO_EIGHT_BITS)
            .convert(_MODE_GREY)
        )
    has_alpha = (
        image.mode in _PREMULTIPLIED_ALPHA_MODES
        or _TRANSPARENCY_KEY in image.info
    )
    return image.convert(_MODE_RGBA if has_alpha else _MODE_RGB)


def _prepare_for_format(image: Image.Image, options: ConvertOptions) -> Image.Image:
    """Flatten transparency or convert modes the output format needs.

    Parameters
    ----------
    image : PIL.Image.Image
        Image in RGB, RGBA, L or LA mode.
    options : ConvertOptions
        Conversion settings with the output format, transparency and
        background colour.

    Returns
    -------
    PIL.Image.Image
        The image ready to save in the output format.

    Raises
    ------
    ConversionError
        If the background colour is unknown and needed.
    """
    keep_alpha = options.transparent and options.output_format.supports_alpha
    if image.mode in _ALPHA_MODES and not keep_alpha:
        try:
            colour = ImageColor.getrgb(options.background)
        except ValueError as err:
            raise ConversionError(
                f'Unknown background colour: {options.background}'
            ) from err
        base = Image.new(_MODE_RGB, image.size, colour[:3])
        base.paste(image, mask=image.getchannel(_ALPHA_CHANNEL))
        image = base if image.mode == _MODE_RGBA else base.convert(_MODE_GREY)
    if (
        options.output_format is OutputFormat.BMP
        and image.mode == _MODE_GREY_ALPHA
    ):
        image = image.convert(_MODE_GREY)
    return image


def _save_kwargs(options: ConvertOptions) -> dict[str, object]:
    """Return Pillow ``save`` keyword arguments for the output format.

    Parameters
    ----------
    options : ConvertOptions
        Conversion settings with the output format, DPI and quality.

    Returns
    -------
    dict of str to object
        Keyword arguments for ``PIL.Image.Image.save``. WEBP carries no
        DPI because Pillow cannot store it there.
    """
    dpi = (
        options.dpi,
        options.dpi,
    )
    output_format = options.output_format
    if output_format is OutputFormat.PNG:
        return {
            'dpi': dpi,
            'optimize': True,
        }
    if output_format is OutputFormat.JPEG:
        return {
            'dpi': dpi,
            'quality': options.quality,
            'subsampling': 0,
        }
    if output_format is OutputFormat.TIFF:
        return {
            'dpi': dpi,
            'compression': 'tiff_lzw',
        }
    if output_format is OutputFormat.BMP:
        return {'dpi': dpi}
    return {
        'quality': options.quality,
        'method': _WEBP_METHOD,
    }


def _stem_taken(out_dir: Path, stem: str, reserved: set[str]) -> bool:
    """Return whether a file name, of any format, is already used.

    Parameters
    ----------
    out_dir : Path
        Save folder.
    stem : str
        File name without extension.
    reserved : set of str
        Case-folded names already written in this batch.

    Returns
    -------
    bool
        True if the name is reserved or a file in the folder has it,
        compared case-insensitively.
    """
    key = stem.casefold()
    if key in reserved:
        return True
    if not out_dir.is_dir():
        return False
    return any(
        path.stem.casefold() == key for path in out_dir.iterdir()
        if path.is_file()
    )


def _output_path(
    source: Path,
    out_dir: Path,
    stem: str,
    options: ConvertOptions,
    reserved: set[str],
) -> Path:
    """Choose the output file name.

    Without ``options.overwrite``, a name already used in the output
    folder by a file of any format (or earlier in this batch) gets the
    prefix ``_converted_{n}_``, where ``n`` counts up from 1 to the
    first free name: ``fig`` → ``_converted_1_fig``, then
    ``_converted_2_fig`` …. With ``options.overwrite``, an existing file
    of the same name and format is replaced, except the input file
    itself or a file written earlier in this batch.

    Parameters
    ----------
    source : Path
        Figure being converted.
    out_dir : Path
        Save folder.
    stem : str
        Wanted file name without extension.
    options : ConvertOptions
        Conversion settings with the output format and overwrite flag.
    reserved : set of str
        Case-folded names already written in this batch.

    Returns
    -------
    Path
        File to write.
    """
    suffix = options.output_format.suffix
    candidate = out_dir / f'{stem}{suffix}'
    is_source = candidate.resolve() == source.resolve()
    if options.overwrite and not is_source and stem.casefold() not in reserved:
        return candidate
    if not is_source and not _stem_taken(out_dir, stem, reserved):
        return candidate
    number = 1
    while True:
        new_stem = f'{CONVERTED_PREFIX}{number}_{stem}'
        if not _stem_taken(out_dir, new_stem, reserved):
            return out_dir / f'{new_stem}{suffix}'
        number += 1
