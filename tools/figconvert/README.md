# figconvert

A small desktop program that converts figures — one file or a whole batch — into
a raster image format at the resolution you choose.

- **Input:** vector (SVG, PDF, XPS, EPS/PS, EMF/WMF) and raster (PNG, JPEG, TIFF,
  BMP, GIF, WEBP, ICO, TGA, JPEG 2000, …).
- **Output:** PNG, JPEG, TIFF (LZW), BMP or WEBP.
- **Resolution:** one DPI value. Vector files are rendered at that DPI, so they
  stay sharp. Raster files keep their pixels and get the new DPI written into
  them.

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
| Sidebar | Input buttons; Output (format, quality, all pages, save folder via *Browse…*, overwrite); Resolution (DPI); Background; *Convert* / *Stop* (`Ctrl+Enter`); progress. |

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

## Extra requirements

- **EPS/PS** need [Ghostscript](https://ghostscript.com/) installed with
  `gswin64c` (Windows) or `gs` on `PATH`.
- **EMF/WMF** render only on Windows.

## Development

figconvert is one of the tools in the [academic-kit](../../README.md)
workspace. Run `uv sync` at the academic-kit root, then
`uv run pytest tools/figconvert` for its tests; the root README lists
the lint and type-check commands.
