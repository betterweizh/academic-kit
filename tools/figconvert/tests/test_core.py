"""Tests for figconvert's conversion logic."""

from pathlib import Path

import pytest
from PIL import Image

from figconvert.core import (
    CONVERTED_PREFIX,
    ConversionError,
    ConvertOptions,
    OutputFormat,
    SizeMode,
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


def test_read_figure_fixed_width_keeps_aspect(tmp_path):
    """Resize a raster to a fixed width, keeping its aspect ratio."""
    source = _write_png(tmp_path / 'fig.png')
    options = ConvertOptions(size_mode=SizeMode.WIDTH, size_pixels=80)

    images = read_figure(source, options)

    assert images[0].size == _DOUBLE_SIZE


def test_read_figure_too_large_raises(tmp_path):
    """Refuse an output larger than the per-side limit."""
    source = _write_png(tmp_path / 'fig.png')
    options = ConvertOptions(size_mode=SizeMode.WIDTH, size_pixels=40_000)

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
