"""Tests for the doi2ref window."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QMessageBox

from doi2ref import gui
from doi2ref.core import FetchResult, OutputFormat, Source

_STATUS_TEXTS = [
    'x',
    'Cleared.',
    'Done: 3 of 5 looked up, 2 failed. Failed DOIs stay on the left.',
]


def _window(qtbot):
    """Show a doi2ref window."""
    window = gui.Doi2RefApp()
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


def test_ctrl_enter_in_text_panel_converts(qtbot, monkeypatch):
    """Start Convert with Ctrl+Enter even while typing DOIs."""
    window = _window(qtbot)
    started = []
    monkeypatch.setattr(window, '_start_batch', lambda: started.append(True))
    window.doi_text.setFocus()

    qtbot.keyClick(
        window.doi_text,
        Qt.Key.Key_Return,
        Qt.KeyboardModifier.ControlModifier,
    )

    assert started == [True]


def test_style_fields_only_for_formatted(qtbot):
    """Enable style, language and numbering only for citations."""
    window = _window(qtbot)
    bibtex_enabled = window.style_combo.isEnabled()
    window.format_buttons[OutputFormat.CITATION].setChecked(True)
    citation_enabled = window.style_combo.isEnabled()

    assert not bibtex_enabled
    assert citation_enabled
    assert window.numbered_check.isEnabled()


def test_batch_appends_references_and_reports_failures(qtbot, monkeypatch):
    """Append each reference and list failed DOIs when done."""
    warnings = []
    monkeypatch.setattr(
        QMessageBox,
        'warning',
        lambda _parent, title, text: warnings.append(text),
    )

    def fake_fetch(dois, options, on_result, stop_event):
        found = FetchResult(
            doi=dois[0],
            reference='@article{a}',
            source=Source.CROSSREF,
        )
        on_result(0, found)
        on_result(1, FetchResult(doi=dois[1], error='doi.org: DOI not found'))

    monkeypatch.setattr(gui, 'fetch_references', fake_fetch)
    window = _window(qtbot)

    window.convert_or_stop()
    qtbot.waitUntil(lambda: window.convert_button.text().startswith('Convert'))

    assert '@article{a}' in window.output_text.toPlainText()
    assert window.status_label.text() == 'Done: 2 looked up, 1 failed.'
    assert 'DOI not found' in warnings[0]


def test_batch_crash_is_reported_not_stopped(qtbot, monkeypatch):
    """Show an error, not "Stopped", when the lookups crash."""
    errors = []
    monkeypatch.setattr(
        QMessageBox,
        'critical',
        lambda _parent, title, text: errors.append(text),
    )

    def broken_fetch(dois, options, on_result, stop_event):
        on_result(0, FetchResult(doi=dois[0], reference='@article{a}'))
        raise TypeError('unexpected reply')

    monkeypatch.setattr(gui, 'fetch_references', broken_fetch)
    window = _window(qtbot)

    window.convert_or_stop()
    qtbot.waitUntil(lambda: window.convert_button.text().startswith('Convert'))

    assert window.status_label.text() == 'Stopped by an error: 1 looked up, 0 failed.'
    assert 'TypeError: unexpected reply' in errors[0]
