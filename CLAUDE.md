# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

Everything runs through uv from the repository root (one `uv.lock` covers the whole workspace).

```bash
uv sync                          # create .venv with the launcher and every tool
uv run academic-kit              # open the launcher
uv run doi2ref | figconvert      # open one tool directly

uv run ruff check --fix          # lint
uv run mypy                      # strict type-check of every package
uv run pylint src                # Pylint runs once per package, never across them
uv run pylint tools/doi2ref/src
uv run pylint tools/figconvert/src
uv run pytest                    # all tests (tests/ and tools/*/tests)
uv run pytest tools/doi2ref/tests/test_core.py::test_download_decodes_utf8   # single test
```

Pre-commit (`uv run pre-commit install`) runs the same commands via `uv run`, so hook and local tool versions always match. There is deliberately no formatter hook.

## Architecture

- **uv workspace**: the root package `academic-kit` (`src/academic_kit/`) is a PyQt6 launcher; each folder under `tools/` is an independent workspace member with its own `pyproject.toml`, version, README, CHANGELOG and tests.
- **Independence rule**: tools never import each other or the launcher. Code shared between tools is copied on purpose — this is why Pylint runs per package (to avoid duplicate-code reports). Don't introduce cross-package imports or a shared utility package.
- **Plugin discovery**: a tool joins the launcher by declaring an entry point in the `academic_kit.tools` group (`name = 'module'`, a module, never `module:attr`). `academic_kit/tools.py` discovers these via `importlib.metadata`, takes the button tooltip from the package `Summary`, and launches each tool as `sys.executable -m <module>` in a separate process. Every tool therefore needs a `__main__.py` that opens its PyQt6 window.
- **Tool structure**: each tool splits into `core.py` (logic, no GUI) and `gui.py` (PyQt6). There is no command-line interface: every function is reached through the GUI, so don't add CLI entry points. doi2ref's core uses only the standard library (urllib; doi.org content negotiation and Crossref, sequential requests with delay/retry). figconvert uses CairoSVG for SVG (needs the external Cairo library; falls back to PyMuPDF without it), PyMuPDF (PDF/XPS), Pillow + external Ghostscript (EPS/PS), and Windows-only rendering for EMF/WMF.
- **Logging**: modules log through `logging.getLogger(__name__)` and never configure logging themselves. Each GUI `main()` calls its own package's `logs.setup_logging()` (every package has its own copy of `logs.py`, per the independence rule), which writes a rotating `<package>.log` and logs unhandled errors, including those in threads and Tk callbacks. Each program's log folder and file name are set in the launcher's Settings → Logs dialog (remembered in `%APPDATA%\academic-kit\settings.json`; default `<program>.log` in the launcher's working directory) and the full path is passed to started tools in the `ACADEMIC_KIT_LOG_FILE` environment variable; a tool started on its own logs to `<tool>.log` in its working directory.
- **Shared config**: all Ruff/mypy/Pylint/pytest settings live in the root `pyproject.toml`; tool `pyproject.toml` files have no `[tool.*]` sections. pytest uses `--import-mode=importlib` because tools have same-named test modules.

### Adding a tool

After creating `tools/newtool` (with the entry point above), update the root `pyproject.toml`: `dependencies`, `[tool.uv.sources]` (`{ workspace = true }`), isort `known-first-party`, and mypy `mypy_path` + `files`; add a `pylint-newtool` hook to `.pre-commit-config.yaml`; then `uv sync`. The launcher code does not change.

## Code style (hand-formatted, no auto-formatter)

- Single quotes; 88-char lines, 72 for docstrings/comments; numpydoc docstrings (Ruff `D` with numpy convention).
- One item per line in multi-item literals and wrapped calls; method chains split one call per line (even short ones, e.g. `tool.name\n.casefold()` inside parentheses).
- mypy is `strict`. Compare `sys.platform` against literals (`'win32'`, `'darwin'`) so mypy narrows platform-specific APIs.
- Modules define `__all__`; private helpers are `_prefixed`. Dataclasses use `frozen=True, slots=True, kw_only=True`.
- GUIs are PyQt6 (GPL v3). Qt handles high-DPI scaling, so sizes are plain pixels. Worker threads hand results to the window through a `queue.Queue` polled by a `QTimer`; each `main()` replaces `sys.excepthook` so errors in slots are logged and shown instead of aborting the program.
- Overridden Qt methods keep Qt's camelCase names: mark them `# noqa: N802  # pylint: disable=invalid-name`. PyQt6's stubs want `pyqtSignal(..., arguments=[...])` to satisfy mypy.
- GUI tests use pytest-qt's `qtbot` on the `offscreen` platform (set in each `conftest.py`), so they never need a display; network and subprocess calls are monkeypatched in tests. For screenshots offscreen, set `QT_QPA_FONTDIR=C:/Windows/Fonts` or text renders as boxes.

## Releasing

The repo is released as a whole with one tag (e.g. `v0.2.0`). For each changed package: bump `version` in `pyproject.toml` **and** `__version__` in `__init__.py`, and move its CHANGELOG `Unreleased` notes (Keep a Changelog format) under a dated version heading with a link at the bottom. Then run all checks and `uv lock` before tagging.
