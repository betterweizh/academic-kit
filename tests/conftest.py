"""Shared pytest configuration for the academic-kit launcher tests."""

import os

# Qt windows are drawn off screen, so the GUI tests run without a
# display (and on CI). Set before pytest-qt creates the QApplication.
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
