# Changelog

All notable changes to figconvert are documented here. The format
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the
project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0] - 2026-11-13

First release, as part of academic-kit 0.1.0.

### Added

- Tkinter window with two equal-width lists (figures waiting on the
  left, finished files on the right) and a sidebar of options. Each
  figure moves to the right as soon as it is converted; failed figures
  stay on the left in red, with the reason shown when selected.
- Input: SVG, PDF, XPS, EPS/PS (with Ghostscript) and EMF/WMF (on
  Windows), plus common raster formats. Vector files are rendered at
  the chosen DPI; raster files keep their pixels and get the new DPI.
- Output: PNG, JPEG, TIFF (LZW), BMP or WEBP, with JPEG/WEBP quality,
  transparency or a background colour, and every page of a PDF/XPS on
  request.
- Output names that can be edited per figure. A name already used in
  the save folder gets a `_converted_{n}_` prefix unless overwriting is
  ticked; input figures and earlier outputs of the batch are never
  overwritten, and a warning appears when saving next to the inputs.
- Right-click menus to open a file or its folder, rename an output or
  remove a figure, plus a progress bar and Convert/Stop (Ctrl+Enter).
- The sidebar keeps its width when the status text changes.
- Opens from the academic-kit launcher through an `academic_kit.tools`
  entry point.
- Tests for the conversion logic and the window layout.

[Unreleased]: https://github.com/betterweizh/academic-kit/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/betterweizh/academic-kit/releases/tag/v0.1.0
