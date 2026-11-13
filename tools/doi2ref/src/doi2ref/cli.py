"""Command-line interface: print or save references for DOIs.

Typical calls::

    doi2ref-cli 10.1038/nature14539
    doi2ref-cli 10.1038/nature14539 --format formatted --style ieee
    doi2ref-cli --input dois.txt --format ris -o refs.ris
    doi2ref-cli 10.1038/nature14539 --source crossref -d 2 -r 3
"""

import argparse
import io
import sys
from collections.abc import Sequence
from pathlib import Path

from doi2ref.core import (
    CITATION_STYLES,
    DEFAULT_DELAY_SECONDS,
    DEFAULT_RETRIES,
    DEFAULT_SOURCES,
    FetchResult,
    OutputFormat,
    RequestOptions,
    Source,
    append_reference,
    extract_dois,
    fetch_references,
)

# Short command-line names for each output format and source. "citation"
# is kept as an alias of "formatted", the name used in the GUI.
_FORMAT_NAMES = {
    'formatted': OutputFormat.CITATION,
    'citation': OutputFormat.CITATION,
    'bibtex': OutputFormat.BIBTEX,
    'ris': OutputFormat.RIS,
    'csl-json': OutputFormat.CSL_JSON,
}
_SOURCE_NAMES = {
    'doi': Source.DOI_ORG,
    'crossref': Source.CROSSREF,
}

# Exit statuses returned by main.
_EXIT_OK = 0
_EXIT_SOME_FAILED = 1
_EXIT_USAGE_OR_FILE_ERROR = 2
# The conventional status for a process ended by SIGINT (128 + 2).
_EXIT_INTERRUPTED = 130


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command-line interface.

    Parameters
    ----------
    argv : sequence of str or None, default None
        Command-line arguments; ``None`` means ``sys.argv[1:]``.

    Returns
    -------
    int
        0 if every DOI converted, 1 if any failed, 2 if no DOI was given
        or a file could not be read or written, 130 if interrupted with
        Ctrl+C (references converted until then are still output).
    """
    arguments = _parse_arguments(argv)
    _use_utf8_output()

    try:
        dois = _collect_dois(arguments)
    except OSError as err:
        print(f'error: cannot read {arguments.input}: {err}', file=sys.stderr)
        return _EXIT_USAGE_OR_FILE_ERROR
    if not dois:
        print('error: no DOIs given', file=sys.stderr)
        return _EXIT_USAGE_OR_FILE_ERROR

    output_format = _FORMAT_NAMES[arguments.format]
    options = RequestOptions(
        output_format=output_format,
        style=arguments.style,
        locale=arguments.locale,
        sources=_selected_sources(arguments.sources),
        delay_seconds=arguments.delay,
        retries=arguments.retries,
    )
    results: list[FetchResult] = []
    show_progress = sys.stderr.isatty()

    # A closure keeps the results collected so far, so that Ctrl+C still
    # prints what was converted before it was pressed.
    def collect(index: int, result: FetchResult) -> None:
        """Keep one result and report progress on a terminal."""
        results.append(result)
        if show_progress:
            status = 'ok' if result.ok else 'failed'
            print(f'[{index + 1}/{len(dois)}] {result.doi}: {status}', file=sys.stderr)

    interrupted = False
    try:
        fetch_references(dois, options, on_result=collect)
    except KeyboardInterrupt:
        interrupted = True
        print('interrupted; showing what was converted so far', file=sys.stderr)

    combined = ''
    for result in results:
        if result.reference is None:
            print(f'error: {result.doi}: {result.error}', file=sys.stderr)
            continue
        combined = append_reference(
            combined,
            result.reference,
            output_format,
            numbered=arguments.numbered,
        )

    converted_count = sum(
        result.ok for result in results
    )
    output_path: Path | None = arguments.output
    if combined:
        if output_path is not None:
            try:
                output_path.write_text(combined + '\n', encoding='utf-8')
            except OSError as err:
                print(f'error: cannot write {output_path}: {err}', file=sys.stderr)
                return _EXIT_USAGE_OR_FILE_ERROR
            print(f'Wrote {converted_count} reference(s) to {output_path}')
        else:
            print(combined)

    if interrupted:
        return _EXIT_INTERRUPTED
    if converted_count == len(results):
        return _EXIT_OK
    return _EXIT_SOME_FAILED


def _non_negative_float(text: str) -> float:
    """Parse a non-negative number for argparse.

    Parameters
    ----------
    text : str
        Argument text.

    Returns
    -------
    float
        Parsed value.

    Raises
    ------
    argparse.ArgumentTypeError
        If the text is not a number or is below 0.
    """
    try:
        value = float(text)
    except ValueError as err:
        raise argparse.ArgumentTypeError(f'not a number: {text!r}') from err
    if value < 0:
        raise argparse.ArgumentTypeError('must be 0 or more')
    return value


def _non_negative_int(text: str) -> int:
    """Parse a non-negative whole number for argparse.

    Parameters
    ----------
    text : str
        Argument text.

    Returns
    -------
    int
        Parsed value.

    Raises
    ------
    argparse.ArgumentTypeError
        If the text is not a whole number of at least 0.
    """
    try:
        value = int(text)
    except ValueError as err:
        raise argparse.ArgumentTypeError(f'not a whole number: {text!r}') from err
    if value < 0:
        raise argparse.ArgumentTypeError('must be 0 or more')
    return value


def _parse_arguments(argv: Sequence[str] | None) -> argparse.Namespace:
    """Parse command-line arguments.

    Parameters
    ----------
    argv : sequence of str or None
        Arguments to parse; ``None`` means ``sys.argv[1:]``.

    Returns
    -------
    argparse.Namespace
        Parsed arguments.
    """
    parser = argparse.ArgumentParser(
        prog='doi2ref-cli',
        description='Turn DOIs into references.',
    )
    parser.add_argument('dois', nargs='*', help='DOIs, doi: prefixes or doi.org links')
    parser.add_argument(
        '-i',
        '--input',
        type=Path,
        help='read DOIs from this text file as well',
    )
    parser.add_argument(
        '-f',
        '--format',
        choices=list(_FORMAT_NAMES),
        default='bibtex',
        help='output format (default: bibtex)',
    )
    parser.add_argument(
        '-s',
        '--style',
        default='apa',
        help=(
            'CSL style for --format formatted, e.g. '
            + ', '.join(sorted(set(CITATION_STYLES.values())))
            + ' (default: apa)'
        ),
    )
    parser.add_argument('-l', '--locale', default='en-US', help='default: en-US')
    parser.add_argument(
        '-n',
        '--numbered',
        action='store_true',
        help='number formatted citations [1], [2] ...',
    )
    parser.add_argument(
        '--source',
        action='append',
        choices=list(_SOURCE_NAMES),
        dest='sources',
        help=(
            'source to use; repeat for a fallback, first is main '
            '(default: --source doi --source crossref)'
        ),
    )
    parser.add_argument(
        '-d',
        '--delay',
        type=_non_negative_float,
        default=DEFAULT_DELAY_SECONDS,
        help=f'seconds to wait between requests (default: {DEFAULT_DELAY_SECONDS:g})',
    )
    parser.add_argument(
        '-r',
        '--retries',
        type=_non_negative_int,
        default=DEFAULT_RETRIES,
        help=f'retries for a failed request (default: {DEFAULT_RETRIES})',
    )
    parser.add_argument('-o', '--output', type=Path, help='write to this file')
    return parser.parse_args(argv)


def _collect_dois(arguments: argparse.Namespace) -> list[str]:
    """Gather DOIs from positional arguments and the input file.

    Parameters
    ----------
    arguments : argparse.Namespace
        Parsed arguments.

    Returns
    -------
    list of str
        Unique DOIs, positional ones first, in the order given.

    Raises
    ------
    OSError
        If the input file cannot be read.
    """
    text = '\n'.join(arguments.dois)
    input_path: Path | None = arguments.input
    if input_path is not None:
        text += '\n' + input_path.read_text(encoding='utf-8', errors='replace')
    return extract_dois(text)


def _selected_sources(names: list[str] | None) -> tuple[Source, ...]:
    """Turn ``--source`` names into sources, dropping repeats.

    Parameters
    ----------
    names : list of str or None
        Names given on the command line, or None for the default.

    Returns
    -------
    tuple of Source
        Sources in the order first given, or ``DEFAULT_SOURCES`` if no
        name was given.
    """
    if not names:
        return DEFAULT_SOURCES
    unique_sources = dict.fromkeys(
        _SOURCE_NAMES[name] for name in names
    )
    return tuple(unique_sources)


def _use_utf8_output() -> None:
    """Write output as UTF-8 so non-ASCII names never crash printing.

    On Windows, output redirected to a file or pipe otherwise uses the
    legacy code page, which cannot encode characters such as "–" or
    "中".
    """
    for stream in (
        sys.stdout,
        sys.stderr,
    ):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding='utf-8', errors='replace')


if __name__ == '__main__':
    raise SystemExit(main())
