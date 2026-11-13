# academic-kit

A small launcher window with one button per academic desktop tool. Each
button opens its tool in a separate window and process, so the tools
never interfere with each other or with the launcher.

```
┌─ academic-kit ─────────────┐
│ [        doi2ref         ] │
│ [       figconvert       ] │
│ Choose a tool to open.     │
└────────────────────────────┘
```

| Tool | What it does |
| --- | --- |
| [doi2ref](tools/doi2ref/README.md) | Turn DOIs into BibTeX, formatted citations, RIS or CSL JSON. |
| [figconvert](tools/figconvert/README.md) | Convert vector and raster figures to raster images at a chosen DPI. |

Hover over a button to see what the tool does.

## Contents

1. [Installation](#1-installation)
2. [Usage](#2-usage)
3. [Project layout](#3-project-layout)
4. [Adding a tool](#4-adding-a-tool)
5. [Development](#5-development)
6. [Releasing a version](#6-releasing-a-version)

## 1. Installation

You need [uv](https://docs.astral.sh/uv/) and Python 3.12 or later with
Tkinter; uv can install Python for you. See the
[doi2ref README](tools/doi2ref/README.md#21-install-the-prerequisites)
for per-platform prerequisites.

```bash
# Get the code
git clone https://github.com/betterweizh/academic-kit.git
cd academic-kit

# Create .venv with every tool
uv sync
```

A single tool can also be installed on its own, straight from GitHub,
for example:

```bash
uv tool install "git+https://github.com/betterweizh/academic-kit#subdirectory=tools/doi2ref"
```

## 2. Usage

```bash
# Open the launcher
uv run academic-kit

# Or open one tool directly
uv run doi2ref
uv run figconvert
```

`doi2ref-cli` (the doi2ref command-line interface) is also available
through `uv run doi2ref-cli --help`.

## 3. Project layout

```
academic-kit/
├── pyproject.toml          launcher package, uv workspace, tool settings
├── uv.lock                 one lock file for the whole workspace
├── src/academic_kit/       launcher window and tool discovery
├── tests/                  launcher tests
└── tools/
    ├── doi2ref/            independent package: pyproject, src, tests
    └── figconvert/         independent package: pyproject, src, tests
```

The repository is a [uv workspace](https://docs.astral.sh/uv/concepts/projects/workspaces/).
Each folder under `tools/` is a complete Python package with its own
dependencies, version, README and tests. Tools never import each other
or the launcher, so any tool can be developed, installed or moved out on
its own.

## 4. Adding a tool

1. Create the package under `tools/`, for example with
   `uv init --package tools/newtool`. Give it a Tkinter `main()` and a
   `__main__.py` so `python -m newtool` opens its window.
2. In the tool's `pyproject.toml`, declare the launcher entry point. Its
   name is the button label and its value is the module to run:

   ```toml
   [project.entry-points.'academic_kit.tools']
   newtool = 'newtool'
   ```

3. In the root `pyproject.toml`, add the tool to `dependencies` and to
   `[tool.uv.sources]` as `newtool = { workspace = true }`, and to the
   isort `known-first-party` list. Add its `src` folder to `mypy_path`
   and `files` under `[tool.mypy]`, and a Pylint hook for it in
   `.pre-commit-config.yaml`.
4. Run `uv sync`. The new button appears the next time the launcher
   opens; the launcher code does not change.

## 5. Development

```bash
# Lint (Ruff), with automatic fixes
uv run ruff check --fix

# Type-check every package
uv run mypy

# Second linter, once per package
uv run pylint src
uv run pylint tools/doi2ref/src
uv run pylint tools/figconvert/src

# Run every package's tests
uv run pytest

# Run the checks above on every commit
uv run pre-commit install
```

The Ruff, mypy, Pylint and pytest settings in the root `pyproject.toml`
apply to the launcher and to every tool. The code is formatted by hand
rather than with an auto-formatter: single quotes, 88-character lines
(72 for docstrings and comments), one item per line in multi-item
literals and wrapped calls, and method chains split one call per line.

## 6. Releasing a version

The repository is released as a whole: one tag, such as `v0.1.0`, covers
the launcher and every tool.

1. Set the new `version` in each changed package's `pyproject.toml`
   and `__version__` in its `__init__.py`.
2. In each changed `CHANGELOG.md`, move the `Unreleased` notes under a
   new version heading with today's date, and add its link at the
   bottom.
3. Run the checks in [Development](#5-development) and `uv lock`.
4. Commit, tag and push:

   ```bash
   git tag -a v0.2.0 -m "academic-kit 0.2.0"
   git push origin main v0.2.0
   ```

5. On GitHub, open **Releases → Draft a new release**, pick the tag and
   paste the new changelog sections as the description.
