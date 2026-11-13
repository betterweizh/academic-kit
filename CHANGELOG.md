# Changelog

All notable changes to the academic-kit launcher are documented here.
Each tool under `tools/` keeps its own changelog. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project
uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

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

[Unreleased]: https://github.com/betterweizh/academic-kit/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/betterweizh/academic-kit/releases/tag/v0.1.0
