# figconvert

A small desktop program that converts figures — one file or a whole batch — into
a raster image format at the resolution you choose.

- **Input:** vector (SVG, PDF, XPS, EPS/PS, EMF/WMF) and raster (PNG, JPEG, TIFF,
  BMP, GIF, WEBP, ICO, TGA, JPEG 2000, …).
- **Output:** PNG, JPEG, TIFF (LZW), BMP or WEBP.
- **Resolution:** one DPI value. Vector files are rendered at that DPI, so they
  stay sharp. Raster files keep their pixels and get the new DPI written into
  them.
- **Size (optional):** an output width and height, each `auto` by default (no
  change), in the chosen **Unit**: `px` (whole pixels) or `mm` (decimals
  allowed). Millimetres become pixels at the DPI (`mm ÷ 25.4 × DPI`), so the
  image prints at that size: 80 mm at 300 DPI is 945 px. Set only one side and
  the other follows the figure's aspect ratio; set both and the figure is
  scaled to exactly that size, even if its aspect ratio changes. Vector files
  are rendered straight at the new size; the DPI is still written into the
  file.

## Run

```bash
uv run figconvert
```

or install it as a tool so `figconvert` works from anywhere:

```bash
# From the academic-kit root
uv tool install ./tools/figconvert
```

## Window

| Area | What it does |
|---|---|
| **To convert** (left) | Figures waiting. Add them with *Add files…* / *Add folder…* (file explorer). Double-click a figure to open it in the default image program (double-clicking the *Output name* cell renames instead). Failed figures stay here in red; select one to see why. `Delete` removes the selection. |
| **Finished** (right) | Each output file, its pixel size and its source. Double-click to open it. |
| Right-click menu | Left panel: *Open file*, *Open file location*, *Rename output name…*, *Remove from list*. Right panel: *Open file*, *Open file location*. |
| Sidebar | Input buttons; Output (format, quality, all pages, save folder via *Browse…*, overwrite); Resolution (DPI, width, height, unit `px`/`mm`); *Open output folder*; Background; *Convert* / *Stop* (`Ctrl+Enter`); progress. |

### Output names

- The default save folder is `_converted_figures` inside the first figure's folder.
- Double-click a figure's **Output name** cell (or select it and press `F2`) to
  rename its output. Type the name without extension; `Enter` keeps it, `Esc`
  cancels.
- If the name is already used in the save folder by a file of **any** format
  (or by an earlier figure in the same batch), the prefix `_converted_{n}_` is added,
  with `n` the first free number: `_converted_1_fig.jpg`, then
  `_converted_2_fig.jpg`, `_converted_3_fig.jpg`, ….
- With *Overwrite existing files* ticked, a file with the same name **and**
  format is replaced instead. The input figures, and files written earlier in
  the same batch, are never overwritten.
- If the save folder is the folder of any input figure, a warning asks you to
  confirm before converting.
- Multi-page PDF/XPS files convert only the first page unless *All pages* is
  ticked; pages are saved as `name_p1.jpg`, `name_p2.jpg`, ….

## Log file

Each run is logged to `figconvert.log`, so you can follow what happened and
find the cause of a failure; the failure dialog names the file.

Opened from the academic-kit launcher, figconvert writes the log to the folder
and file name set for it in the launcher's **Settings → Logs** dialog (by
default, `figconvert.log` in the folder the launcher was started from). Started
on its own, it writes `figconvert.log` in the folder it was started from.

It records program start (versions, platform, whether CairoSVG loaded), each
batch with its settings, each figure with the renderer used, its pixel size,
the file written and the time taken, and every error with its full traceback,
including errors no one expected. At 1 MB the file is renamed
`figconvert.log.1` and a new one started; three old files are kept.

## Extra requirements

- **SVG** is rendered with [CairoSVG](https://cairosvg.org/), which needs the
  Cairo library; pip does not install it.
  - Windows: install the GTK3 runtime for Windows (it includes
    `libcairo-2.dll`) and add its `bin` folder to `PATH`.
  - macOS: `brew install cairo`. Linux: install `libcairo2` (or your
    distribution's Cairo package).

  Without Cairo, SVG falls back to PyMuPDF. PyMuPDF ignores clipping paths and
  replaces missing fonts with Times, so plots (Matplotlib ones especially) can
  come out wrong. The status line says so when the window opens, and a warning
  asks before the first batch with SVG files.

  Images or style sheets an SVG links to are loaded only from the SVG's own
  folder and its sub-folders (or from `data:` URLs); other links, including
  web addresses, are drawn as empty. Because of a CairoSVG limitation, linked
  files are also left out when the SVG's folder path contains a space or
  another character that URLs encode. Images embedded in the SVG, as
  Matplotlib does, are not affected.

  CairoSVG is slower than PyMuPDF: a full-page Matplotlib figure at 600 DPI
  takes about a second.
- **EPS/PS** need [Ghostscript](https://ghostscript.com/) installed with
  `gswin64c` (Windows) or `gs` on `PATH`.
- **EMF/WMF** render only on Windows.

## Development

figconvert is one of the tools in the [academic-kit](../../README.md)
workspace. Run `uv sync` at the academic-kit root, then
`uv run pytest tools/figconvert` for its tests; the root README lists
the lint and type-check commands.
