# Changelog

All notable changes to this project are documented here. The format
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the
project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0] - 2026-11-13

Released as part of academic-kit 0.1.0.

### Changed

- doi2ref now lives in the `tools/doi2ref` folder of the academic-kit
  repository and can be opened from the academic-kit launcher, which
  finds it through an `academic_kit.tools` entry point. Install it from
  GitHub with `#subdirectory=tools/doi2ref` (see the README).
- Code style: Ruff linting with single quotes and hand formatting
  instead of `ruff format`, numpydoc docstrings wrapped at 72
  characters, and Pylint as a second linter. Behaviour is unchanged.

### Fixed

- The sidebar keeps its width when the status text changes, so the
  text panels beside it no longer grow and shrink with each message.

## [0.0.1] - 2026-11-11

First release.

### Added

- Tkinter GUI with two equal-width text panels (DOIs on the left,
  references on the right) and a sidebar of options and actions.
- Reference types: BibTeX (default), Formatted, RIS and CSL JSON.
  BibTeX is laid out one field per line.
- Formatted citations in APA, Chicago (author-date), Harvard, IEEE, MLA,
  Vancouver, Nature, ACS or AMA style, in seven languages, with optional
  `[1]`, `[2]` … numbering.
- Source choice: doi.org and/or Crossref. The first ticked source is the
  main one; the second is used only for DOIs the first could not resolve.
- Requests are sent one at a time with a configurable wait between them
  and a configurable number of retries for temporary failures (network
  errors, timeouts, dropped connections, HTTP 429 and 5xx). A server's
  `Retry-After` pause is honoured, up to 60 seconds.
- Requests carry a generic desktop-Chrome User-Agent and no personal
  data.
- Convert appends each reference to the right panel as it arrives and
  becomes Stop while a batch runs (Ctrl+Enter toggles).
- Copy references, Remove finished (drops converted DOIs from the left
  panel) and Clear actions.
- Progress bar and status text at the bottom of the sidebar.
- The window cannot shrink below the size that shows every control, and
  the layout scales with Windows display scaling.
- DOI detection in free text: bare DOIs, `doi:` prefixes, `doi.org`
  links and pasted reference lists, without duplicates.
- `doi2ref-cli` command-line interface with the same formats, sources,
  waiting time and retries. It shows progress in the terminal, writes
  UTF-8 output, and on Ctrl+C still prints what was converted so far.
- Development tooling: Ruff for formatting and linting, mypy type
  checks, pytest tests and pre-commit hooks for Ruff and mypy.

[Unreleased]: https://github.com/betterweizh/academic-kit/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/betterweizh/academic-kit/releases/tag/v0.1.0
[0.0.1]: https://github.com/betterweizh/doi2ref/releases/tag/v0.0.1
