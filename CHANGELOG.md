# Changelog

All notable changes to the academic-kit launcher are documented here.
Each tool under `tools/` keeps its own changelog. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project
uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.1] - 2026-11-22

Released with [doi2ref 0.1.1](tools/doi2ref/CHANGELOG.md) and
[figconvert 0.1.1](tools/figconvert/CHANGELOG.md).

### Changed

- The window is rebuilt with PyQt6 instead of Tkinter. It looks and
  works as before; Qt scales it for high-DPI screens itself.
- The tool buttons are large squares of one fixed size, with room for
  a 64 x 64 icon, side by side in a row centred in the window. Resizing
  the window keeps them centred without changing their size.
- The launcher opens at a larger size (560 x 380 at 100 % scaling).
  The status text has an area of fixed height below the buttons, so
  the buttons no longer move when the text changes.

### Added

- A log file, `academic-kit.log`, recording each start, the tools
  found, each tool started with its process id, and errors with their
  tracebacks.
- **Settings → Logs…**: a dialog that sets the log folder and log file
  name of each program (the launcher, doi2ref, figconvert). By default
  each writes `<program>.log` in the folder the launcher was started
  from. The settings are remembered, and each tool opened from the
  launcher gets its own file through the `ACADEMIC_KIT_LOG_FILE`
  environment variable. A second running copy of a tool gets a numbered
  file (`figconvert-2.log`), since two copies cannot share one log.

## [0.1.0] - 2026-11-13

First release.

### Added

- Launcher window with one button per installed tool. Each button opens
  its tool in a separate process; hovering shows the tool's
  description.
- The launcher window keeps its size when the status text changes. It
  can be resized, with the status text re-wrapping to the new width,
  but not made smaller than the size that fits every status message.
- Tool discovery through the `academic_kit.tools` entry point group, so
  a new tool needs no launcher change.
- uv workspace holding the launcher and two independent tools under
  `tools/`, with one lock file and shared Ruff, mypy, Pylint and pytest
  settings:
  - [doi2ref 0.1.0](tools/doi2ref/CHANGELOG.md): DOIs to BibTeX,
    formatted citations, RIS or CSL JSON.
  - [figconvert 0.1.0](tools/figconvert/CHANGELOG.md): vector and
    raster figures to raster images at a chosen DPI.

[Unreleased]: https://github.com/betterweizh/academic-kit/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/betterweizh/academic-kit/releases/tag/v0.1.1
[0.1.0]: https://github.com/betterweizh/academic-kit/releases/tag/v0.1.0
