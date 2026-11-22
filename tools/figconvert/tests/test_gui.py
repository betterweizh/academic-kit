"""Tests for the figconvert window."""

import pytest
from PIL import Image
from PyQt6.QtCore import QPoint
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QLineEdit, QMessageBox

from figconvert import gui
from figconvert.core import SizeUnit
from figconvert.gui import _read_size

_STATUS_TEXTS = [
    'x',
    'Cleared.',
    '3 / 40: a_long_figure_name.svg failed — Cannot open: some long error',
]


def _window(qtbot):
    """Show a figconvert window."""
    window = gui.FigConvertApp()
    qtbot.addWidget(window)
    window.show()
    qtbot.waitExposed(window)
    return window


def test_sidebar_width_ignores_status_text(qtbot):
    """Keep the sidebar width, whatever the status text says."""
    window = _window(qtbot)
    widths = set()
    for text in _STATUS_TEXTS:
        window.status_label.setText(text)
        qtbot.wait(10)
        widths.add(window.sidebar.width())

    assert len(widths) == 1


def test_sidebar_fields_line_up(qtbot):
    """Give the Output and Resolution fields one width and column."""
    window = _window(qtbot)
    fields = [
        window.format_combo,
        window.quality_spin,
        window.dpi_combo,
        window.width_combo,
        window.height_combo,
        window.unit_combo,
    ]
    boxes = {
        (field.mapTo(window, QPoint(0, 0)).x(), field.width()) for field in fields
    }
    rows = [
        field.mapTo(window, QPoint(0, 0)).y() for field in fields
    ]

    assert len(boxes) == 1
    assert rows == sorted(rows)


def test_rename_cleans_or_restores_name(qtbot, tmp_path):
    """Clean a typed output name; keep the old one if it is empty."""
    source = tmp_path / 'fig.png'
    Image.new('RGB', (4, 4)).save(source)
    window = _window(qtbot)
    window._add_paths([source])
    item = window._pending_items()[0]

    item.setData(gui._OUTPUT_COLUMN, gui.Qt.ItemDataRole.UserRole, 'fig')
    item.setText(gui._OUTPUT_COLUMN, 'new:name.png')
    cleaned = item.text(gui._OUTPUT_COLUMN)
    item.setData(gui._OUTPUT_COLUMN, gui.Qt.ItemDataRole.UserRole, cleaned)
    item.setText(gui._OUTPUT_COLUMN, '   ')
    restored = item.text(gui._OUTPUT_COLUMN)

    assert cleaned == 'new_name'
    assert restored == 'new_name'


def test_batch_moves_done_figures_and_marks_failures(qtbot, tmp_path, monkeypatch):
    """Move converted figures right; keep failed ones left, in red."""
    warnings = []
    monkeypatch.setattr(
        QMessageBox,
        'warning',
        lambda _parent, _title, text, *args: warnings.append(text),
    )
    good = tmp_path / 'good.png'
    Image.new('RGB', (40, 20), 'blue').save(good)
    broken = tmp_path / 'broken.png'
    broken.write_bytes(b'not an image')
    window = _window(qtbot)
    window._add_paths([good, broken])
    window.out_dir_edit.setText(str(tmp_path / 'out'))
    window.width_combo.setCurrentText('80')

    window.convert_or_stop()
    qtbot.waitUntil(lambda: not window._running)

    finished = window.done_tree.topLevelItem(0)
    left = window._pending_items()
    assert window.done_tree.topLevelItemCount() == 1
    assert finished.text(1) == '80 × 40'
    assert [gui._item_path(item).name for item in left] == ['broken.png']
    assert left[0].foreground(0).color() == QColor(gui._FAILED_COLOUR)
    assert window.status_label.text() == 'Done: 1 converted, 1 failed.'
    assert 'broken.png' in warnings[0]
    assert (tmp_path / 'out' / 'good.jpg').is_file()


@pytest.mark.parametrize(
    ('format_name', 'enabled_quality'),
    [
        ('JPEG', True),
        ('PNG', False),
    ],
)
def test_quality_only_for_lossy_formats(qtbot, format_name, enabled_quality):
    """Enable the quality field only for JPEG and WEBP."""
    window = _window(qtbot)
    window.format_combo.setCurrentText(format_name)

    assert window.quality_spin.isEnabled() is enabled_quality
    assert SizeUnit(window.unit_combo.currentText()) is SizeUnit.PX


@pytest.mark.parametrize(
    ('text', 'expected'),
    [
        ('auto', None),
        (' AUTO ', None),
        ('', None),
        ('1200', 1200),
    ],
)
def test_read_size_accepts_auto_and_pixels(text, expected):
    """Read "auto" or blank as automatic and digits as pixels."""
    assert _read_size(text, 'Width', SizeUnit.PX, 300) == expected


@pytest.mark.parametrize(
    'text',
    [
        '0',
        '12.5',
        'wide',
        '40000',
    ],
)
def test_read_size_rejects_invalid(text):
    """Refuse sizes that are not whole pixels within the limit."""
    with pytest.raises(ValueError, match='Width'):
        _read_size(text, 'Width', SizeUnit.PX, 300)


def test_read_size_accepts_millimetres():
    """Read decimal millimetres, which become pixels at the DPI."""
    assert _read_size('85.5', 'Width', SizeUnit.MM, 300) == 85.5


@pytest.mark.parametrize(
    'text',
    [
        '0',
        'wide',
        '3000',
        'inf',
        '-inf',
        'nan',
    ],
)
def test_read_size_rejects_invalid_millimetres(text):
    """Refuse millimetres that are not numbers or too many pixels."""
    with pytest.raises(ValueError, match='Width'):
        _read_size(text, 'Width', SizeUnit.MM, 600)


def test_batch_keeps_name_still_being_typed(qtbot, tmp_path, monkeypatch):
    """Use an output name typed but not yet confirmed with Enter."""
    source = tmp_path / 'figure1.png'
    Image.new('RGB', (4, 4)).save(source)
    window = _window(qtbot)
    window._add_paths([source])
    window._edit_output_name(window._pending_items()[0])
    editor = window.pending_tree.viewport().findChildren(QLineEdit)[0]
    editor.setText('figure1_final')
    used = []

    def stop_after_reading(_paths):
        used.extend(item.text(gui._OUTPUT_COLUMN) for item in window._pending_items())
        return False

    monkeypatch.setattr(window, '_confirm_svg_fallback', stop_after_reading)
    window.out_dir_edit.setText(str(tmp_path / 'out'))

    window.convert_or_stop()

    assert used == ['figure1_final']
