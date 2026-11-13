"""Tests for the doi2ref command-line interface."""

import pytest

from doi2ref import cli
from doi2ref.core import FetchResult, OutputFormat, Source


@pytest.fixture
def captured_options(monkeypatch):
    """Fake the lookup: 'bad' DOIs fail, 'interrupt' raises Ctrl+C."""
    seen = []

    # Nested so the fake can record each call's options in ``seen``.
    def fake_fetch(
        dois,
        options,
        on_result=None,
    ):
        seen.append(options)
        results = []
        for index, doi in enumerate(dois):
            if 'interrupt' in doi:
                raise KeyboardInterrupt
            if 'bad' in doi:
                result = FetchResult(doi=doi, error='DOI not found')
            else:
                result = FetchResult(doi=doi, reference=f'@misc{{{doi}}}')
            results.append(result)
            if on_result is not None:
                on_result(index, result)
        return results

    monkeypatch.setattr(cli, 'fetch_references', fake_fetch)
    return seen


def test_cli_prints_references(captured_options, capsys):
    """References go to stdout and the exit code is 0."""
    assert cli.main(['doi:10.1000/a']) == 0
    printed = (
        capsys
        .readouterr()
        .out
        .strip()
    )
    assert printed == '@misc{10.1000/a}'


def test_cli_passes_style_options(captured_options):
    """Format, style and locale flags reach the request options."""
    arguments = [
        '10.1000/a',
        '-f',
        'formatted',
        '-s',
        'ieee',
        '-l',
        'fr-FR',
    ]
    cli.main(arguments)
    assert captured_options[0].output_format is OutputFormat.CITATION
    assert captured_options[0].style == 'ieee'
    assert captured_options[0].locale == 'fr-FR'


def test_cli_passes_sources_delay_and_retries(captured_options):
    """Source order, waiting time and retries reach the options."""
    arguments = [
        '10.1000/a',
        '--source',
        'crossref',
        '--source',
        'doi',
        '-d',
        '2.5',
        '-r',
        '4',
    ]
    cli.main(arguments)
    options = captured_options[0]
    assert options.sources == (
        Source.CROSSREF,
        Source.DOI_ORG,
    )
    assert options.delay_seconds == 2.5
    assert options.retries == 4


def test_cli_defaults_to_both_sources(captured_options):
    """Without --source, doi.org is main and Crossref the fallback."""
    cli.main(['10.1000/a'])
    assert captured_options[0].sources == (
        Source.DOI_ORG,
        Source.CROSSREF,
    )


def test_cli_rejects_negative_delay(captured_options):
    """A negative waiting time is a usage error."""
    arguments = [
        '10.1000/a',
        '-d',
        '-1',
    ]
    with pytest.raises(SystemExit):
        cli.main(arguments)


def test_cli_reads_input_file_and_writes_output(captured_options, tmp_path):
    """DOIs come from --input and references go to --output."""
    source = tmp_path / 'dois.txt'
    source.write_text('https://doi.org/10.1000/a\n10.1000/b\n', encoding='utf-8')
    target = tmp_path / 'refs.bib'
    arguments = [
        '-i',
        str(source),
        '-o',
        str(target),
    ]

    assert cli.main(arguments) == 0
    assert target.read_text(encoding='utf-8') == (
        '@misc{10.1000/a}\n\n@misc{10.1000/b}\n'
    )


def test_cli_reports_unwritable_output(
    captured_options,
    tmp_path,
    capsys,
):
    """A write error is reported instead of crashing."""
    target = tmp_path / 'missing-folder' / 'refs.bib'
    arguments = [
        '10.1000/a',
        '-o',
        str(target),
    ]
    assert cli.main(arguments) == 2
    output = capsys.readouterr()
    assert 'cannot write' in output.err


def test_cli_reports_failures(captured_options, capsys):
    """A failed DOI is reported on stderr and the exit code is 1."""
    arguments = [
        '10.1000/good',
        '10.1000/bad',
    ]
    assert cli.main(arguments) == 1
    output = capsys.readouterr()
    assert '10.1000/bad: DOI not found' in output.err
    assert '@misc{10.1000/good}' in output.out


def test_cli_keeps_results_when_interrupted(captured_options, capsys):
    """Ctrl+C prints what was converted so far and exits with 130."""
    arguments = [
        '10.1000/first',
        '10.1000/interrupt',
        '10.1000/never',
    ]
    assert cli.main(arguments) == 130
    output = capsys.readouterr()
    printed = (
        output
        .out
        .strip()
    )
    assert printed == '@misc{10.1000/first}'
    assert 'interrupted' in output.err


def test_cli_without_dois_exits_2(captured_options, capsys):
    """Running with nothing to look up is a usage error."""
    assert cli.main([]) == 2
    output = capsys.readouterr()
    assert 'no DOIs' in output.err
