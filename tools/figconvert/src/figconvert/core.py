"""Read a vector or raster figure and write it as a raster image.

Vector files are rendered directly at the requested size, so they stay
sharp at any resolution:

* SVG, PDF and XPS are rendered with PyMuPDF.
* EPS and PS are rendered with Ghostscript (through Pillow), which must
  be installed separately.
* EMF and WMF are rendered by Windows (through Pillow), Windows only.

Raster files are read with Pillow. Their pixels are kept unless the
options ask for a fixed width or height, or for resampling to the DPI.
"""

import enum
import math
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pymupdf
from PIL import EpsImagePlugin, Image, ImageColor, features

__all__ = [
    'CONVERTED_PREFIX',
    'DEFAULT_SOURCE_DPI',
    'GHOSTSCRIPT_SUFFIXES',
    'MAX_SIDE_PIXELS',
    'POINTS_PER_INCH',
    'PYMUPDF_SUFFIXES',
    'RASTER_SUFFIXES',
    'SUPPORTED_SUFFIXES',
    'VECTOR_SUFFIXES',
    'WINDOWS_METAFILE_SUFFIXES',
    'ConversionError',
    'ConvertOptions',
    'OutputFile',
    'OutputFormat',
    'SizeMode',
    'clean_stem',
    'convert_file',
    'find_figures',
    'is_supported',
    'is_vector',
    'read_figure',
]

# Vector sizes are measured in points: 1 pt = 1/72 inch.
POINTS_PER_INCH = 72.0
# Raster files without DPI metadata are assumed to be at this DPI.
DEFAULT_SOURCE_DPI = 72.0
# Refuse to create images larger than this many pixels per side.
MAX_SIDE_PIXELS = 30_000
# Added in front of an output name that is already used in the save
# folder.
CONVERTED_PREFIX = '_converted_'

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


class SizeMode(enum.Enum):
    """How the output pixel size is chosen."""

    DPI = 'By DPI'
    WIDTH = 'Fixed width (px)'
    HEIGHT = 'Fixed height (px)'


@dataclass(frozen=True, slots=True)
class ConvertOptions:
    """Settings for one batch.

    Attributes
    ----------
    output_format : OutputFormat, default OutputFormat.PNG
        Format to write.
    dpi : float, default 300.0
        Resolution written into the file, and used to render vector
        files when ``size_mode`` is ``SizeMode.DPI``.
    size_mode : SizeMode, default SizeMode.DPI
        Whether the size comes from the DPI or a fixed width or height.
    size_pixels : int, default 2000
        Target width or height for the fixed-size modes.
    resample_raster : bool, default False
        In DPI mode, resize raster files from their own DPI to ``dpi``
        so their physical size stays the same. Otherwise only the DPI
        metadata changes.
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
    """

    output_format: OutputFormat = OutputFormat.PNG
    dpi: float = 300.0
    size_mode: SizeMode = SizeMode.DPI
    size_pixels: int = 2000
    resample_raster: bool = False
    transparent: bool = True
    background: str = 'white'
    quality: int = 95
    all_pages: bool = False
    overwrite: bool = False


@dataclass(frozen=True, slots=True)
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
        if suffix in PYMUPDF_SUFFIXES:
            return list(_render_pymupdf(path, options))
        if suffix in GHOSTSCRIPT_SUFFIXES:
            return [_render_ghostscript(path, options)]
        if suffix in WINDOWS_METAFILE_SUFFIXES:
            return [_render_metafile(path, options)]
        if suffix in RASTER_SUFFIXES:
            return [_read_raster(path, options)]
    except ConversionError:
        raise
    # Any other reader failure is reported as a conversion failure, so
    # one bad figure never stops a batch.
    except Exception as err:  # pylint: disable=broad-exception-caught
        raise ConversionError(str(err) or type(err).__name__) from err
    raise ConversionError(f"Unsupported file type: {suffix or '(none)'}")


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


def _vector_zoom(width_pt: float, height_pt: float, options: ConvertOptions) -> float:
    """Return the scale from points to output pixels for a vector page.

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
    float
        Output pixels per point.

    Raises
    ------
    ConversionError
        If the output would exceed ``MAX_SIDE_PIXELS`` on a side.
    """
    if options.size_mode is SizeMode.WIDTH:
        zoom = options.size_pixels / width_pt
    elif options.size_mode is SizeMode.HEIGHT:
        zoom = options.size_pixels / height_pt
    else:
        zoom = options.dpi / POINTS_PER_INCH
    _check_size(round(width_pt * zoom), round(height_pt * zoom))
    return zoom


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
    if options.size_mode is SizeMode.WIDTH:
        size = (
            options.size_pixels,
            round(height * options.size_pixels / width),
        )
    elif options.size_mode is SizeMode.HEIGHT:
        size = (
            round(width * options.size_pixels / height),
            options.size_pixels,
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
            zoom = _vector_zoom(page.rect.width, page.rect.height, options)
            # PyMuPDF's constructors carry no type annotations.
            matrix = pymupdf.Matrix(zoom, zoom)  # type: ignore[no-untyped-call]
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
        zoom = _vector_zoom(width_pt, height_pt, options)
        # Ghostscript renders at a whole-number scale; render at or
        # above the target, then resize down to the exact size.
        scale = max(1, math.ceil(zoom))
        transparent = options.transparent and options.output_format.supports_alpha
        # Image.open returns an EpsImageFile here, whose load() takes
        # these arguments; the stubs only know the base ImageFile.
        image.load(scale=scale, transparency=transparent)  # type: ignore[call-arg]
        rendered = image.copy()
    return _resize_to(rendered, width_pt * zoom, height_pt * zoom)


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
        zoom = _vector_zoom(width_pt, height_pt, options)
        # Image.open returns a WmfStubImageFile here, whose load() takes
        # a DPI; the stubs only know the base ImageFile. When mypy
        # checks for another platform this line is unreachable, so the
        # ignore is unused there.
        image.load(  # type: ignore[call-arg, unused-ignore]
            dpi=round(zoom * POINTS_PER_INCH),
        )
        rendered = image.copy()
    return _resize_to(rendered, width_pt * zoom, height_pt * zoom)


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
