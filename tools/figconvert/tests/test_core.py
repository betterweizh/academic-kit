"""Tests for figconvert's conversion logic."""

import builtins
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from figconvert import core
from figconvert.core import (
    CONVERTED_PREFIX,
    MAX_SIDE_PIXELS,
    ConversionError,
    ConvertOptions,
    OutputFormat,
    SizeUnit,
    clean_stem,
    convert_file,
    find_figures,
    is_supported,
    is_vector,
    read_figure,
)

_PNG_SIZE = (
    40,
    20,
)
_SMALL_SIZE = (
    5,
    5,
)
_DOUBLE_SIZE = (
    80,
    40,
)
_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="50">'
    '<rect width="100" height="50" fill="red"/>'
    '</svg>'
)


def _write_png(path, size=_PNG_SIZE, mode='RGBA'):
    """Write a small solid-colour PNG and return its path."""
    Image.new(mode, size, 'blue').save(path)
    return path


@pytest.mark.parametrize(
    ('name', 'expected'),
    [
        ('fig.PNG', True),
        ('fig.svg', True),
        ('fig.eps', True),
        ('notes.txt', False),
        ('no_suffix', False),
    ],
)
def test_is_supported_by_suffix(name, expected):
    """Recognise supported extensions in any case."""
    assert is_supported(Path(name)) is expected


@pytest.mark.parametrize(
    ('name', 'expected'),
    [
        ('fig.pdf', True),
        ('fig.EMF', True),
        ('fig.jpg', False),
    ],
)
def test_is_vector_by_suffix(name, expected):
    """Tell vector files from raster files."""
    assert is_vector(Path(name)) is expected


@pytest.mark.parametrize(
    ('text', 'expected'),
    [
        ('fig.png', 'fig'),
        ('fig.svg', 'fig'),
        ('fig.v2', 'fig.v2'),
        ('a<b>c:d', 'a_b_c_d'),
        ('  .name. ', 'name'),
        ('tab\tname', 'tab_name'),
        ('   ', ''),
    ],
)
def test_clean_stem_cases(text, expected):
    """Drop known extensions and replace forbidden characters."""
    assert clean_stem(text) == expected


def test_find_figures_filters_and_sorts(tmp_path):
    """List only supported files, sorted case-insensitively."""
    _write_png(tmp_path / 'b.png')
    _write_png(tmp_path / 'A.png')
    (tmp_path / 'notes.txt').write_text('x', encoding='utf-8')
    sub_folder = tmp_path / 'sub'
    sub_folder.mkdir()
    _write_png(sub_folder / 'c.png')

    flat = find_figures(tmp_path)
    nested = find_figures(tmp_path, recursive=True)

    flat_names = [
        path.name for path in flat
    ]
    nested_names = [
        path.name for path in nested
    ]
    assert flat_names == [
        'A.png',
        'b.png',
    ]
    assert nested_names == [
        'A.png',
        'b.png',
        'c.png',
    ]


def test_convert_file_png_to_jpeg_flattens_and_sets_dpi(tmp_path):
    """Write an RGB JPEG with the chosen DPI and the source's pixels."""
    source = _write_png(tmp_path / 'fig.png')
    out_dir = tmp_path / 'out'
    options = ConvertOptions(output_format=OutputFormat.JPEG, dpi=300)

    outputs = convert_file(source, out_dir, options)

    assert len(outputs) == 1
    assert outputs[0].path == out_dir / 'fig.jpg'
    assert outputs[0].width == _PNG_SIZE[0]
    assert outputs[0].height == _PNG_SIZE[1]
    with Image.open(outputs[0].path) as written:
        assert written.mode == 'RGB'
        assert round(written.info['dpi'][0]) == 300


def test_convert_file_name_taken_adds_prefix(tmp_path):
    """Number the output when its name is used by any format."""
    source = _write_png(tmp_path / 'fig.png')
    out_dir = tmp_path / 'out'
    out_dir.mkdir()
    _write_png(out_dir / 'fig.tif')
    _write_png(out_dir / f'{CONVERTED_PREFIX}1_fig.png')
    options = ConvertOptions(output_format=OutputFormat.PNG)

    outputs = convert_file(source, out_dir, options)

    assert outputs[0].path.name == f'{CONVERTED_PREFIX}2_fig.png'


def test_convert_file_overwrite_replaces_same_format(tmp_path):
    """Replace an existing file of the same name and format."""
    source = _write_png(tmp_path / 'fig.png')
    out_dir = tmp_path / 'out'
    out_dir.mkdir()
    _write_png(out_dir / 'fig.jpg', size=_SMALL_SIZE, mode='RGB')
    options = ConvertOptions(output_format=OutputFormat.JPEG, overwrite=True)

    outputs = convert_file(source, out_dir, options)

    assert outputs[0].path == out_dir / 'fig.jpg'
    with Image.open(outputs[0].path) as written:
        assert written.size == _PNG_SIZE


def test_convert_file_never_overwrites_source(tmp_path):
    """Keep the input file even with overwrite ticked."""
    source = _write_png(tmp_path / 'fig.png')
    options = ConvertOptions(output_format=OutputFormat.PNG, overwrite=True)

    outputs = convert_file(source, tmp_path, options)

    assert outputs[0].path.name == f'{CONVERTED_PREFIX}1_fig.png'
    assert source.exists()


def test_convert_file_reserved_names_not_reused(tmp_path):
    """Give later figures in a batch a new name, not an earlier one."""
    first = _write_png(tmp_path / 'one.png')
    second = _write_png(tmp_path / 'two.png')
    out_dir = tmp_path / 'out'
    options = ConvertOptions(output_format=OutputFormat.PNG, overwrite=True)
    reserved = set()

    convert_file(first, out_dir, options, 'same', reserved)
    outputs = convert_file(second, out_dir, options, 'same', reserved)

    assert outputs[0].path.name == f'{CONVERTED_PREFIX}1_same.png'


def test_convert_file_svg_scales_with_dpi(tmp_path):
    """Render a vector file at a size proportional to the DPI."""
    source = tmp_path / 'fig.svg'
    source.write_text(_SVG, encoding='utf-8')

    low = convert_file(source, tmp_path / 'low', ConvertOptions(dpi=72))
    high = convert_file(source, tmp_path / 'high', ConvertOptions(dpi=144))

    assert high[0].width == 2 * low[0].width
    assert high[0].height == 2 * low[0].height


@pytest.mark.parametrize(
    ('width', 'height', 'expected'),
    [
        (None, None, _PNG_SIZE),
        (80, None, _DOUBLE_SIZE),
        (None, 40, _DOUBLE_SIZE),
        (30, 70, (30, 70)),
    ],
)
def test_read_figure_raster_fixed_size(tmp_path, width, height, expected):
    """Keep the aspect ratio for one fixed side, not for both."""
    source = _write_png(tmp_path / 'fig.png')
    options = ConvertOptions(width=width, height=height)

    images = read_figure(source, options)

    assert images[0].size == expected


@pytest.mark.parametrize(
    ('width', 'height', 'expected'),
    [
        (200, None, (200, 100)),
        (None, 25, (50, 25)),
        (120, 120, (120, 120)),
    ],
)
def test_read_figure_vector_fixed_size(tmp_path, width, height, expected):
    """Render a vector file at a fixed width, height or both."""
    source = tmp_path / 'fig.svg'
    source.write_text(_SVG, encoding='utf-8')
    options = ConvertOptions(width=width, height=height)

    images = read_figure(source, options)

    assert images[0].size == expected


def test_convert_options_rejects_zero_width():
    """Refuse a fixed side smaller than one pixel."""
    with pytest.raises(ValueError, match='width'):
        ConvertOptions(width=0)


def test_read_figure_too_large_raises(tmp_path):
    """Refuse an output larger than the per-side limit."""
    source = _write_png(tmp_path / 'fig.png')
    options = ConvertOptions(width=40_000)

    with pytest.raises(ConversionError, match='limit'):
        read_figure(source, options)


def test_read_figure_unsupported_raises(tmp_path):
    """Reject a file type the tool cannot read."""
    source = tmp_path / 'notes.txt'
    source.write_text('x', encoding='utf-8')

    with pytest.raises(ConversionError, match='Unsupported'):
        read_figure(source, ConvertOptions())


def test_read_figure_corrupt_raster_raises(tmp_path):
    """Report an unreadable image as a conversion error."""
    source = tmp_path / 'broken.png'
    source.write_bytes(b'not a png')

    with pytest.raises(ConversionError, match='Cannot read image'):
        read_figure(source, ConvertOptions())


_CLIPPED_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">'
    '<defs><clipPath id="left"><rect width="50" height="100"/></clipPath></defs>'
    '<rect width="100" height="100" fill="white"/>'
    '<rect width="100" height="100" fill="red" clip-path="url(#left)"/>'
    '</svg>'
)


class _FakeCairoSurface:
    """Stand-in for a drawn cairocffi ImageSurface of one colour."""

    def __init__(self, size, rgba):
        self.size = size
        red, green, blue, alpha = rgba
        # Cairo's layout in memory: B, G, R, A, alpha premultiplied.
        premultiplied = [
            round(channel * alpha / 255) for channel in (blue, green, red)
        ]
        self.data = bytes(premultiplied + [alpha]) * (size[0] * size[1])
        self.flushed = False

    def get_width(self):
        """Return the width in pixels."""
        return self.size[0]

    def get_height(self):
        """Return the height in pixels."""
        return self.size[1]

    def get_stride(self):
        """Return the bytes per row."""
        return 4 * self.size[0]

    def get_data(self):
        """Return the pixel memory."""
        return memoryview(self.data)

    def flush(self):
        """Record that pending drawing was finished."""
        self.flushed = True


class _FakeCairoSvg:
    """Stand-in for the cairosvg module that records its calls.

    Like CairoSVG, it renders at the size asked for with
    ``output_width`` or ``output_height`` and keeps its own natural
    size's aspect ratio for the other side.
    """

    def __init__(self, natural_size, rgba=(0, 128, 0, 255)):
        self.natural_size = natural_size
        self.rgba = rgba
        self.tree_calls = []
        self.calls = []
        self.finished = 0
        # cairosvg.parser.Tree and cairosvg.surface.PNGSurface.
        self.parser = SimpleNamespace(Tree=self._tree)
        self.surface = SimpleNamespace(PNGSurface=self._png_surface)

    def _tree(self, **kwargs):
        """Record the parse arguments and return a placeholder tree."""
        self.tree_calls.append(kwargs)
        return object()

    def _png_surface(self, tree, output, dpi, **kwargs):
        """Record the render arguments and return a drawn surface."""
        self.calls.append(
            {
                'tree': tree,
                'output': output,
                'dpi': dpi,
                **kwargs,
            }
        )
        natural_width, natural_height = self.natural_size
        if 'output_width' in kwargs:
            width = kwargs['output_width']
            height = round(natural_height * width / natural_width)
        elif 'output_height' in kwargs:
            height = kwargs['output_height']
            width = round(natural_width * height / natural_height)
        else:
            width = round(natural_width * kwargs['scale'])
            height = round(natural_height * kwargs['scale'])
        cairo_surface = _FakeCairoSurface((width, height), self.rgba)
        return SimpleNamespace(cairo=cairo_surface, finish=self._finish)

    def _finish(self):
        """Count finished surfaces."""
        self.finished += 1


def _svg_source(tmp_path, text=_SVG):
    """Write an SVG into ``tmp_path`` and return its path."""
    source = tmp_path / 'fig.svg'
    source.write_text(text, encoding='utf-8')
    return source


def test_read_figure_svg_uses_cairosvg(tmp_path, monkeypatch):
    """Render SVG with CairoSVG at 72 DPI, stretched to exact size."""
    source = _svg_source(tmp_path)
    fake = _FakeCairoSvg((100, 50))
    monkeypatch.setattr(core, '_cairosvg_status', lambda: (fake, None))

    images = read_figure(source, ConvertOptions(width=200, height=40))

    assert images[0].size == (200, 40)
    assert images[0].mode == 'RGBA'
    tree_call = fake.tree_calls[0]
    assert tree_call['bytestring'] == source.read_bytes()
    assert tree_call['url'] == source.resolve().as_uri()
    call = fake.calls[0]
    assert call['output'] is None
    assert call['dpi'] == 72
    assert call['scale'] == 2
    assert fake.finished == 1


def test_read_figure_svg_reads_cairo_pixels(tmp_path, monkeypatch):
    """Turn Cairo's premultiplied BGRA pixels into plain RGBA."""
    source = _svg_source(tmp_path)
    fake = _FakeCairoSvg((10, 5), rgba=(200, 100, 50, 128))
    monkeypatch.setattr(core, '_cairosvg_status', lambda: (fake, None))

    image = read_figure(source, ConvertOptions(dpi=72))[0]

    red, green, blue, alpha = image.getpixel((3, 2))
    assert alpha == 128
    assert abs(red - 200) <= 2
    assert abs(green - 100) <= 2
    assert abs(blue - 50) <= 2


def test_read_figure_svg_keeps_cairosvg_aspect(tmp_path, monkeypatch):
    """Leave the other side to CairoSVG, not to PyMuPDF's size."""
    source = _svg_source(tmp_path)
    # CairoSVG sees a 100 x 80 figure where PyMuPDF sees 100 x 50.
    fake = _FakeCairoSvg((100, 80))
    monkeypatch.setattr(core, '_cairosvg_status', lambda: (fake, None))

    by_width = read_figure(source, ConvertOptions(width=200))[0]
    by_dpi = read_figure(source, ConvertOptions(dpi=144))[0]

    assert by_width.size == (200, 160)
    assert by_dpi.size == (200, 160)
    assert fake.calls[0]['output_width'] == 200


def test_read_figure_svg_caps_render_scale(tmp_path, monkeypatch):
    """Render a stretched SVG within the per-side limit first."""
    source = _svg_source(tmp_path)
    # A tiny natural size keeps the fake's render small; the scale it
    # is asked for still comes from the 100 x 50 pt figure.
    fake = _FakeCairoSvg((1, 1))
    monkeypatch.setattr(core, '_cairosvg_status', lambda: (fake, None))

    read_figure(source, ConvertOptions(width=100, height=30_000))

    assert fake.calls[0]['scale'] * 100 <= MAX_SIDE_PIXELS


def test_read_figure_svg_ignores_pillow_pixel_limit(tmp_path, monkeypatch):
    """Keep a render that Pillow's bomb limit would refuse as a file."""
    source = _svg_source(tmp_path)
    fake = _FakeCairoSvg((100, 50))
    monkeypatch.setattr(core, '_cairosvg_status', lambda: (fake, None))
    monkeypatch.setattr(Image, 'MAX_IMAGE_PIXELS', 100)

    images = read_figure(source, ConvertOptions(dpi=72))

    assert images[0].size == (100, 50)


def test_read_figure_png_ignores_broken_cairosvg(tmp_path, monkeypatch):
    """Read non-SVG figures without loading CairoSVG at all."""
    source = _write_png(tmp_path / 'fig.png')

    def fail():
        raise AssertionError('CairoSVG loaded for a PNG')

    monkeypatch.setattr(core, '_cairosvg_status', fail)

    images = read_figure(source, ConvertOptions())

    assert images[0].size == _PNG_SIZE


def test_cairosvg_status_survives_any_import_error(monkeypatch):
    """Fall back, not crash, when importing CairoSVG fails oddly."""
    real_import = builtins.__import__

    def broken_import(name, *args, **kwargs):
        if name == 'cairosvg':
            raise RuntimeError('mismatched cffi')
        return real_import(name, *args, **kwargs)

    core._cairosvg_status.cache_clear()
    monkeypatch.setattr(builtins, '__import__', broken_import)
    try:
        problem = core.cairosvg_problem()
    finally:
        core._cairosvg_status.cache_clear()

    assert problem == 'mismatched cffi'


def test_local_fetcher_limits_files_to_folder(tmp_path):
    """Read linked files in the SVG's folder tree, refuse others."""
    folder = tmp_path / 'figures'
    (folder / 'sub').mkdir(parents=True)
    (folder / 'sub' / 'photo.png').write_bytes(b'inside')
    (tmp_path / 'secret.txt').write_bytes(b'outside')
    fetch = core._local_fetcher(folder)

    inside = fetch((folder / 'sub' / 'photo.png').as_uri(), 'image/*')
    outside = fetch((tmp_path / 'secret.txt').as_uri(), 'image/*')
    web = fetch('https://example.com/photo.png', 'image/*')
    data = fetch('data:text/plain;base64,aGk=', 'image/*')

    assert inside == b'inside'
    assert outside == core._EMPTY_SVG
    assert web == core._EMPTY_SVG
    assert data == b'hi'


def test_read_figure_svg_falls_back_to_pymupdf(tmp_path, monkeypatch):
    """Render SVG with PyMuPDF when CairoSVG cannot be loaded."""
    source = tmp_path / 'fig.svg'
    source.write_text(_SVG, encoding='utf-8')
    monkeypatch.setattr(core, '_cairosvg_status', lambda: (None, 'no Cairo'))

    images = read_figure(source, ConvertOptions(dpi=144))

    assert images[0].size == (200, 100)
    assert core.cairosvg_problem() == 'no Cairo'


def test_cairosvg_renders_clip_paths(tmp_path):
    """Apply clipping paths, which PyMuPDF ignores."""
    if core.cairosvg_problem() is not None:
        pytest.skip('Cairo library not installed')
    source = tmp_path / 'clipped.svg'
    source.write_text(_CLIPPED_SVG, encoding='utf-8')

    image = read_figure(source, ConvertOptions(dpi=72))[0]

    assert image.getpixel((25, 50))[:3] == (255, 0, 0)
    assert image.getpixel((75, 50))[:3] == (255, 255, 255)


def _require_cairo():
    """Skip the calling test when the Cairo library is missing."""
    if core.cairosvg_problem() is not None:
        pytest.skip('Cairo library not installed')


def test_cairosvg_use_does_not_reread_file(tmp_path, monkeypatch):
    """Resolve ``<use href="#id">`` without fetching the SVG again."""
    _require_cairo()
    folder = tmp_path / 'with space'
    folder.mkdir()
    source = _svg_source(
        folder,
        '<svg xmlns="http://www.w3.org/2000/svg" '
        'xmlns:xlink="http://www.w3.org/1999/xlink" width="20" height="10">'
        '<defs><rect id="box" width="10" height="10" fill="blue"/></defs>'
        '<use xlink:href="#box"/><use xlink:href="#box" x="10"/>'
        '</svg>',
    )
    fetched = []
    real_fetcher = core._local_fetcher

    def recording_fetcher(folder_path):
        fetch = real_fetcher(folder_path)

        def record(url, resource_type):
            fetched.append(url)
            return fetch(url, resource_type)

        return record

    monkeypatch.setattr(core, '_local_fetcher', recording_fetcher)

    image = read_figure(source, ConvertOptions(dpi=72))[0]

    assert fetched == []
    assert image.getpixel((15, 5))[:3] == (0, 0, 255)


def test_cairosvg_draws_linked_image(tmp_path):
    """Draw an image linked by relative path from the SVG's folder."""
    _require_cairo()
    Image.new('RGB', (10, 10), (0, 255, 0)).save(tmp_path / 'photo.png')
    source = _svg_source(
        tmp_path,
        '<svg xmlns="http://www.w3.org/2000/svg" '
        'xmlns:xlink="http://www.w3.org/1999/xlink" width="10" height="10">'
        '<image xlink:href="photo.png" width="10" height="10"/>'
        '</svg>',
    )

    image = read_figure(source, ConvertOptions(dpi=72))[0]

    assert image.getpixel((5, 5)) == (0, 255, 0, 255)


@pytest.mark.parametrize(
    ('options', 'expected'),
    [
        (ConvertOptions(width=1200), (1200, None)),
        (ConvertOptions(height=10.4), (None, 10)),
        (ConvertOptions(width=25.4, size_unit=SizeUnit.MM, dpi=300), (300, None)),
        (
            ConvertOptions(width=50.8, height=25.4, size_unit=SizeUnit.MM, dpi=600),
            (1200, 600),
        ),
        (ConvertOptions(width=0.01, size_unit=SizeUnit.MM, dpi=72), (1, None)),
    ],
)
def test_convert_options_pixel_size(options, expected):
    """Turn pixels and millimetres (at the DPI) into whole pixels."""
    assert options.pixel_size() == expected


def test_read_figure_raster_width_in_millimetres(tmp_path):
    """Resize a raster to a width given in millimetres."""
    source = _write_png(tmp_path / 'fig.png')
    # 25.4 mm at 160 DPI is 160 px; the 40 x 20 PNG keeps its aspect.
    options = ConvertOptions(width=25.4, size_unit=SizeUnit.MM, dpi=160)

    images = read_figure(source, options)

    assert images[0].size == (160, 80)


def test_convert_file_logs_renderer_and_output(tmp_path, caplog):
    """Log which renderer read the figure and which file was written."""
    source = _write_png(tmp_path / 'fig.png')

    with caplog.at_level('INFO', logger='figconvert'):
        outputs = convert_file(source, tmp_path / 'out', ConvertOptions())

    assert f'Reading {source} with Pillow' in caplog.text
    assert f'Wrote {outputs[0].path}' in caplog.text
