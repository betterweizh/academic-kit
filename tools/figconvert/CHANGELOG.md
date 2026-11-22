# Changelog

All notable changes to figconvert are documented here. The format
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the
project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.1] - 2026-11-22

Released as part of academic-kit 0.1.1.

### Added

- Width and height fields in the Resolution box, both `auto` by
  default. One fixed side keeps the figure's aspect ratio; both set the
  exact output size.
- A Unit field below them: `px` or `mm`. Millimetres become pixels at
  the DPI, so the image prints at the size given.
- A log file, `figconvert.log`, recording each run, batch, figure and
  error with its traceback; the failure dialog names the file. Its
  folder and name are set in the launcher's Settings → Logs dialog;
  started on its own, figconvert writes `figconvert.log` in the folder
  it was started from.
- SVG files are rendered with CairoSVG, which applies clipping paths
  and uses installed fonts. If the Cairo library is missing, SVG falls
  back to PyMuPDF, and the window warns about it. Pixels are read
  straight from Cairo, and text drawn with `<use>` (as Matplotlib does)
  is not re-parsed per glyph, so a full-page figure takes about a
  second.

### Fixed

- A very small fixed width or height on a large EMF/WMF figure no
  longer fails with a cryptic error (the render DPI rounded to 0).
- SVG plots (for example from Matplotlib) no longer draw bars and lines
  outside their axes or swap their fonts for Times, when Cairo is
  installed. PyMuPDF ignores clipping paths and has no font fallback.

### Changed

- The window is rebuilt with PyQt6 instead of Tkinter. It looks and
  works as before; Qt scales it for high-DPI screens itself.

- `ConvertOptions` takes optional `width` and `height` in pixels
  instead of `size_mode` and `size_pixels`, and `SizeMode` is removed.

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

[Unreleased]: https://github.com/betterweizh/academic-kit/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/betterweizh/academic-kit/releases/tag/v0.1.1
[0.1.0]: https://github.com/betterweizh/academic-kit/releases/tag/v0.1.0
