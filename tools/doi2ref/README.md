# doi2ref

Turn DOIs into references. Paste DOIs on the left, pick a reference type
and options in the sidebar, and get BibTeX, formatted citations, RIS or
CSL JSON on the right.

```
┌──────────────────────┬──────────────────────┬──────────────────────┐
│ DOIs                 │ References           │ Reference type       │
│                      │                      │  ◉ BibTeX ...        │
│ 10.1038/nature14539  │ @article{LeCun_2015, │ Citation style  ▾    │
│ doi:10.1126/scien... │   title={Deep lea... │ Source               │
│ https://doi.org/...  │   ...                │  ☑ doi.org ☑ Crossref│
│                      │ }                    │ Requests             │
│                      │                      │  Wait (s)   [1.0]    │
│                      │                      │  Retries    [2]      │
│                      │                      │ [ Convert / Stop ]   │
│                      │                      │ [ Copy references ]  │
│                      │                      │ [ Remove finished ]  │
│                      │                      │ [ Clear ]            │
│                      │                      │                      │
│                      │                      │ ▓▓▓▓▓░░░░░░░░░░░░    │
│                      │                      │ 3 of 5 looked up,    │
│                      │                      │ 0 failed.            │
└──────────────────────┴──────────────────────┴──────────────────────┘
```

## Contents

1. [Features](#1-features)
2. [Installation](#2-installation)
3. [Usage](#3-usage)
4. [Update and uninstall](#4-update-and-uninstall)
5. [Troubleshooting](#5-troubleshooting)
6. [Development](#6-development)
7. [License](#7-license)

## 1. Features

- **Reference types:** BibTeX (default, one field per line), Formatted
  (a citation in the chosen style), RIS, CSL JSON.
- **Citation styles:** APA, Chicago (author-date), Harvard, IEEE, MLA,
  Vancouver, Nature, ACS, AMA — in seven languages, optionally numbered.
- **Sources:** doi.org and/or Crossref. The first ticked source is the
  main one; the second is used only for DOIs the first could not resolve.
- **Polite requests:** one request at a time, a configurable wait in
  seconds between requests, and a configurable number of retries for
  temporary failures (network errors, timeouts, HTTP 429/5xx). A
  server's `Retry-After` pause is honoured; "DOI not found" is not
  retried.
- **Append mode:** Convert appends each reference to the right panel as
  it arrives, continuing `[n]` numbering; CSL JSON stays one array.
- **Remove finished:** deletes the DOIs that converted successfully from
  the left panel, leaving the failed ones to retry.
- **Forgiving input:** bare DOIs, `doi:` prefixes, `doi.org` links, or a
  whole pasted reference list; duplicates are dropped.
- **No third-party dependencies:** only the Python standard library.

References come from [doi.org content negotiation](https://citation.doi.org/docs.html),
which covers Crossref, DataCite and mEDRA DOIs, and from the
[Crossref API](https://www.crossref.org/documentation/retrieve-metadata/content-negotiation/).

## 2. Installation

You need **Python 3.12 or later with Tkinter** (the GUI toolkit). The
easiest route is [uv](https://docs.astral.sh/uv/), which can install
Python for you and keeps doi2ref in its own environment.

### 2.1 Install the prerequisites

**Windows**

```powershell
# uv (also installs Python on demand)
winget install --id=astral-sh.uv -e

# Git, needed to install from GitHub
winget install --id=Git.Git -e
```

Prefer a plain Python? Install it from [python.org](https://www.python.org/downloads/),
keep **"tcl/tk and IDLE"** ticked (it is by default) and tick
**"Add python.exe to PATH"**.

**macOS**

```bash
# uv and Git
brew install uv git

# Tkinter, only if you use Homebrew's own Python instead of uv's
brew install python-tk@3.13
```

**Linux**

```bash
# uv
curl -LsSf https://astral.sh/uv/install.sh | sh

# Tkinter (for the system Python) and Git — Debian / Ubuntu
sudo apt install python3-tk git

# Tkinter and Git — Fedora
sudo dnf install python3-tkinter git

# Tkinter and Git — Arch
sudo pacman -S tk git
```

Close and reopen the terminal after installing uv so it is on your PATH.

### 2.2 Install doi2ref from GitHub

**Option A — as an app with uv (recommended).** Puts the `doi2ref` and
`doi2ref-cli` commands on your PATH:

```bash
# Install the latest version
uv tool install "git+https://github.com/betterweizh/academic-kit#subdirectory=tools/doi2ref"

# First time only, if the command is not found
uv tool update-shell

# Open the window
doi2ref
```

To pin a released version instead of the latest code, add its tag:

```bash
# Install version 0.1.0
uv tool install "git+https://github.com/betterweizh/academic-kit@v0.1.0#subdirectory=tools/doi2ref"
```

**Option B — with pipx or pip:**

```bash
# With pipx
pipx install "git+https://github.com/betterweizh/academic-kit#subdirectory=tools/doi2ref"

# Or with pip, inside a virtual environment of your own
python -m pip install "git+https://github.com/betterweizh/academic-kit#subdirectory=tools/doi2ref"
```

On Windows use `py -m pip …` if `python` is not found.

**Option C — clone and run from source** (for trying changes):

```bash
# Get the code
git clone https://github.com/betterweizh/academic-kit.git
cd academic-kit

# Create .venv with Python, every tool and the dev tools
uv sync

# Open the window
uv run doi2ref
```

Without uv, after cloning, inside the `academic-kit` folder:

```bash
# Create a virtual environment
python -m venv .venv

# Activate it — Windows: .venv\Scripts\activate
# Activate it — macOS/Linux: source .venv/bin/activate

# Install doi2ref and open the window
python -m pip install -e tools/doi2ref
doi2ref
```

### 2.3 Optional: a desktop shortcut (Windows)

After Option A, the launcher is `%USERPROFILE%\.local\bin\doi2ref.exe`.
Right-click it in Explorer → **Send to → Desktop (create shortcut)**. It
opens without a console window.

## 3. Usage

### 3.1 Window

1. Paste DOIs into the left panel — one per line, or any text that
   contains them.
2. Pick a reference type. For **Formatted**, also choose a style and
   language, and tick **Number** if you want `[1]`, `[2]` …
3. Choose the sources, the wait between requests and the retries.
4. Press **Convert** (or **Ctrl+Enter**). References are appended on the
   right as they arrive; press **Stop** to end early.
5. **Copy references** copies the right panel; **Remove finished** clears
   the converted DOIs from the left so only failures remain; **Clear**
   empties both panels.

### 3.2 Command line

```bash
# BibTeX
doi2ref-cli 10.1038/nature14539

# IEEE citation
doi2ref-cli 10.1038/nature14539 -f formatted -s ieee

# File in, file out
doi2ref-cli -i dois.txt -f ris -o refs.ris

# Crossref, 2 s wait, 3 retries
doi2ref-cli -i dois.txt --source crossref -d 2 -r 3
```

Progress is shown in the terminal as each DOI finishes. Press Ctrl+C to
stop early; the references converted so far are still printed or saved.
Run `doi2ref-cli --help` for every option.

## 4. Update and uninstall

### 4.1 Update

```bash
# Installed with uv tool install
uv tool upgrade doi2ref

# Installed with pipx install
pipx upgrade doi2ref
```

If `uv tool upgrade` reports "Nothing to upgrade" but you expect new
code, reinstall with
`uv tool install --force "git+https://github.com/betterweizh/academic-kit#subdirectory=tools/doi2ref"`.

### 4.2 Uninstall

Remove doi2ref the same way you installed it:

```bash
# Installed with uv tool install
uv tool uninstall doi2ref

# Optional: also clear uv's cached download
uv cache clean doi2ref

# Installed with pipx install
pipx uninstall doi2ref

# Installed with pip, inside that environment
python -m pip uninstall doi2ref
```

This removes the `doi2ref` and `doi2ref-cli` commands and doi2ref's own
environment.

- **Installed from a clone** (`uv sync` / `pip install -e .`)? Delete the
  cloned `academic-kit` folder; its `.venv` goes with it.
- **On Windows**, also delete any desktop shortcut you made to
  `doi2ref.exe`.

## 5. Troubleshooting

| Problem | Fix |
| --- | --- |
| `No module named 'tkinter'` / `_tkinter` | Install Tkinter for your Python (see [2.1](#21-install-the-prerequisites)), or let uv provide Python: `uv tool install --python 3.13 "git+https://github.com/betterweizh/academic-kit#subdirectory=tools/doi2ref"` |
| `doi2ref` is not recognised | Run `uv tool update-shell` and open a new terminal |
| `git` is not recognised during install | Install Git (see [2.1](#21-install-the-prerequisites)) and open a new terminal |
| Many "too many requests (HTTP 429)" | Raise **Wait (s)**, e.g. to 2–5 seconds |
| "network error" for every DOI | Check the internet connection, proxy or firewall; try the other source |
| "DOI not found" | The DOI is mistyped or not registered; check it at <https://doi.org> |
| Blurry text on Windows | Update to a current Windows 10/11; the app requests sharp scaling at start-up |

## 6. Development

doi2ref is one of the tools in the
[academic-kit](../../README.md) workspace and lives in its
`tools/doi2ref` folder. Development commands run from the academic-kit
root, where the Ruff, mypy, Pylint and pytest settings are kept:

```bash
# Create .venv with every tool and the dev tools
uv sync

# Lint, with automatic fixes
uv run ruff check --fix

# Type-check
uv run mypy

# Second linter
uv run pylint tools/doi2ref/src

# Run doi2ref's tests
uv run pytest tools/doi2ref

# Run the checks above on every commit
uv run pre-commit install
```

The code is formatted by hand rather than with an auto-formatter; the
[academic-kit README](../../README.md#5-development) lists the layout
rules.

### 6.1 Releases

doi2ref is released together with the other tools as an academic-kit
version; see the
[academic-kit README](../../README.md#6-releasing-a-version).

## 7. License

doi2ref is released under the [MIT License](LICENSE).
