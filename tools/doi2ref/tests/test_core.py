"""Tests for doi2ref.core, with the network replaced by fakes."""

import email.message
import http.client
import io
import json
import threading
import time
import urllib.error
import urllib.request

import pytest

from doi2ref import core
from doi2ref.core import (
    BROWSER_HEADERS,
    FetchResult,
    OutputFormat,
    RequestOptions,
    RequestThrottle,
    Source,
    StopRequested,
    append_reference,
    clean_response,
    extract_dois,
    fetch_reference,
    fetch_references,
    format_bibtex,
)

BIBTEX = '@article{LeCun_2015, title={Deep learning}, year={2015}}'
BIBTEX_FORMATTED = '@article{LeCun_2015,\n  title={Deep learning},\n  year={2015}\n}'
DOI_URL = 'https://doi.org/10.1038/nature14539'
CROSSREF_URL = (
    'https://api.crossref.org/works/10.1038/nature14539/transform/application/x-bibtex'
)
NO_DELAY = RequestOptions(delay_seconds=0)


def _http_error(code, retry_after=None):
    """Build an HTTP error with an optional Retry-After header."""
    headers = email.message.Message()
    if retry_after is not None:
        headers['Retry-After'] = retry_after
    return urllib.error.HTTPError(
        'https://example.org',
        code,
        'error',
        headers,
        io.BytesIO(),
    )


def _throttle():
    """Return a throttle that never waits."""
    return RequestThrottle(0)


def _requested_urls(calls):
    """Return the URLs of the recorded download calls, in order."""
    return [
        call['url'] for call in calls
    ]


class _FakeResponse(io.BytesIO):
    """In-memory stand-in for the response of urllib.request.urlopen."""

    def __enter__(self):
        """Return the response itself, as urlopen's response does."""
        return self

    def __exit__(self, *args):
        """Close the response."""
        self.close()


def _fake_urlopen(request, timeout):
    """Answer every request with UTF-8 text holding non-ASCII names."""
    return _FakeResponse('Müller, Zoë'.encode())


@pytest.fixture
def fake_download(monkeypatch):
    """Replace core._download with scripted replies; record calls."""

    # Nested so each test can script its own replies through the
    # fixture's monkeypatch.
    def install(*responses):
        calls = []
        remaining = list(responses)

        # Nested so the fake shares ``calls`` and ``remaining``.
        def download(
            url,
            headers,
            timeout,
        ):
            calls.append(
                {
                    'url': url,
                    'headers': headers,
                }
            )
            response = remaining.pop(0)
            if isinstance(response, Exception):
                raise response
            return response

        monkeypatch.setattr(core, '_download', download)
        return calls

    return install


def test_extract_dois_handles_prefixes_links_and_prose():
    """Links, prefixes and prose yield DOIs without punctuation."""
    text = (
        'See https://doi.org/10.1038/nature14539, and doi:10.1126/science.aaa8415.\n'
        '(10.1103/PhysRevLett.116.061102)\n'
        'https://dx.doi.org/10.1000/a%2Fb'
    )
    assert extract_dois(text) == [
        '10.1038/nature14539',
        '10.1126/science.aaa8415',
        '10.1103/PhysRevLett.116.061102',
        '10.1000/a/b',
    ]


def test_extract_dois_keeps_brackets_that_belong_to_the_doi():
    """Balanced brackets stay; a closing bracket from prose goes."""
    text = '(see 10.1016/S0140-6736(20)30183-5) and [10.1000/x(1)].'
    assert extract_dois(text) == [
        '10.1016/S0140-6736(20)30183-5',
        '10.1000/x(1)',
    ]


def test_extract_dois_removes_case_insensitive_duplicates():
    """A DOI repeated in other case is kept once, as first spelled."""
    assert extract_dois('10.1000/ABC 10.1000/abc') == ['10.1000/ABC']


def test_extract_dois_ignores_text_without_dois():
    """Text without a DOI yields an empty list."""
    assert extract_dois('no identifiers here, just 10.5 apples') == []


@pytest.mark.parametrize(
    'page',
    [
        '<!DOCTYPE html><html></html>',
        '<!-- banner -->\n<html>',
        '<head></head>',
    ],
)
def test_clean_response_rejects_html(page):
    """A landing page is not mistaken for a formatted citation."""
    with pytest.raises(ValueError, match='web page'):
        clean_response(page, OutputFormat.CITATION)


@pytest.mark.parametrize(
    (
        'body',
        'output_format',
    ),
    [
        ('not bibtex', OutputFormat.BIBTEX),
        ('not ris', OutputFormat.RIS),
        ('{broken', OutputFormat.CSL_JSON),
        ('   ', OutputFormat.CITATION),
    ],
)
def test_clean_response_rejects_wrong_format(body, output_format):
    """Replies that do not match the format raise ValueError."""
    with pytest.raises(ValueError):
        clean_response(body, output_format)


def test_clean_response_lays_out_bibtex():
    """A one-line BibTeX reply gets one field per line."""
    assert clean_response(f' {BIBTEX}\n', OutputFormat.BIBTEX) == BIBTEX_FORMATTED


def test_clean_response_accepts_ris_with_byte_order_mark():
    """A UTF-8 byte-order mark before 'TY  -' is ignored."""
    ris = '﻿TY  - JOUR\nER  - '
    assert clean_response(ris, OutputFormat.RIS) == 'TY  - JOUR\nER  -'


def test_format_bibtex_keeps_commas_inside_braces_and_quotes():
    """Commas inside values do not split fields."""
    entry = '@article{k, author={Doe, J. and Roe, R.}, note="a, b", month=may}'
    assert format_bibtex(entry) == (
        '@article{k,\n  author={Doe, J. and Roe, R.},\n  note="a, b",\n  month=may\n}'
    )


def test_format_bibtex_leaves_unbalanced_entries_alone():
    """Entries that cannot be split safely are returned unchanged."""
    broken = '@article{k, title={Unclosed, year=2015}'
    assert format_bibtex(broken) == broken


def test_requests_look_like_chrome_and_carry_no_personal_data(fake_download):
    """Headers are generic Chrome plus the format; nothing personal."""
    calls = fake_download('LeCun, Y. (2015). Deep learning.')
    options = RequestOptions(
        output_format=OutputFormat.CITATION,
        style='ieee',
        locale='de-DE',
    )

    fetch_reference('10.1038/nature14539', options, _throttle())

    headers = calls[0]['headers']
    assert headers['User-Agent'] == BROWSER_HEADERS['User-Agent']
    assert 'Chrome/' in headers['User-Agent']
    assert headers['Accept'] == 'text/x-bibliography; style=ieee; locale=de-DE'
    assert set(headers) == {
        'User-Agent',
        'Accept-Language',
        'Accept',
    }
    assert 'mailto' not in json.dumps(headers)


def test_main_source_is_used_first(fake_download):
    """With both sources ticked, a doi.org success skips Crossref."""
    calls = fake_download(BIBTEX)

    result = fetch_reference('10.1038/nature14539', NO_DELAY, _throttle())

    assert result == FetchResult(
        doi='10.1038/nature14539',
        reference=BIBTEX_FORMATTED,
        source=Source.DOI_ORG,
    )
    assert _requested_urls(calls) == [DOI_URL]


def test_second_source_handles_failures_of_the_first(fake_download):
    """A permanent doi.org failure falls through to Crossref."""
    calls = fake_download(_http_error(404), BIBTEX)

    result = fetch_reference('10.1038/nature14539', NO_DELAY, _throttle())

    assert result.source is Source.CROSSREF
    assert _requested_urls(calls) == [
        DOI_URL,
        CROSSREF_URL,
    ]


def test_single_source_has_no_fallback(fake_download):
    """With only Crossref ticked, doi.org is never contacted."""
    options = RequestOptions(sources=(Source.CROSSREF,), delay_seconds=0)
    calls = fake_download(_http_error(404))

    result = fetch_reference('10.1038/nature14539', options, _throttle())

    assert result.error == 'Crossref: DOI not found'
    assert _requested_urls(calls) == [CROSSREF_URL]


def test_temporary_failures_are_retried(fake_download):
    """Network errors and 5xx/429 are retried on the same source."""
    options = RequestOptions(retries=2, delay_seconds=0)
    calls = fake_download(urllib.error.URLError('offline'), _http_error(503), BIBTEX)

    result = fetch_reference('10.1038/nature14539', options, _throttle())

    assert result.source is Source.DOI_ORG
    assert _requested_urls(calls) == [DOI_URL] * 3


def test_dropped_connections_are_retried(fake_download):
    """A reset or cut-off connection is temporary, not a crash."""
    calls = fake_download(
        ConnectionResetError('reset'),
        http.client.IncompleteRead(b''),
        BIBTEX,
    )

    result = fetch_reference('10.1038/nature14539', NO_DELAY, _throttle())

    assert result.ok
    assert len(calls) == 3


def test_retries_run_out_before_moving_to_next_source(fake_download):
    """After 1 + retries attempts the next source is tried."""
    options = RequestOptions(retries=1, delay_seconds=0)
    calls = fake_download(_http_error(429), _http_error(429), BIBTEX)

    result = fetch_reference('10.1038/nature14539', options, _throttle())

    assert result.source is Source.CROSSREF
    assert _requested_urls(calls) == [
        DOI_URL,
        DOI_URL,
        CROSSREF_URL,
    ]


def test_permanent_failures_are_not_retried(fake_download):
    """A wrong-format response is not worth asking for again."""
    options = RequestOptions(sources=(Source.DOI_ORG,), retries=5, delay_seconds=0)
    calls = fake_download('<html>landing page</html>')

    result = fetch_reference('10.1038/nature14539', options, _throttle())

    assert result.error == 'doi.org: received a web page instead of a reference'
    assert len(calls) == 1


def test_retry_after_pauses_before_the_retry(fake_download):
    """A 429 with Retry-After delays the next attempt by that long."""
    fake_download(_http_error(429, retry_after='0.2'), BIBTEX)

    start = time.monotonic()
    result = fetch_reference('10.1038/nature14539', NO_DELAY, _throttle())

    assert result.ok
    assert time.monotonic() - start >= 0.2


def test_retry_after_is_capped():
    """A very long Retry-After is limited to MAX_RETRY_AFTER_SECONDS."""
    err = _http_error(503, retry_after='86400')
    assert core._retry_after_seconds(err) == core.MAX_RETRY_AFTER_SECONDS
    assert core._retry_after_seconds(_http_error(503, 'soon')) is None


def test_throttle_spaces_requests():
    """Consecutive waits are at least delay_seconds apart."""
    throttle = RequestThrottle(0.05)
    start = time.monotonic()
    for _ in range(3):
        throttle.wait()
    assert time.monotonic() - start >= 0.1


def test_throttle_stops_when_asked():
    """A set stop event ends a long wait at once."""
    stop_event = threading.Event()
    throttle = RequestThrottle(30, stop_event=stop_event)
    throttle.wait()
    stop_event.set()
    start = time.monotonic()
    with pytest.raises(StopRequested):
        throttle.wait()
    assert time.monotonic() - start < 1


def test_fetch_references_keeps_order_and_can_stop(fake_download):
    """Results come in input order; a stop ends the batch early."""
    fake_download(BIBTEX, BIBTEX, BIBTEX)
    stop_event = threading.Event()
    seen = []

    # Nested so the callback can record indexes in ``seen`` and set
    # this test's ``stop_event``.
    def on_result(index, _result):
        seen.append(index)
        if index == 1:
            stop_event.set()

    dois = [
        '10.1000/a',
        '10.1000/b',
        '10.1000/c',
    ]
    results = fetch_references(
        dois,
        NO_DELAY,
        on_result=on_result,
        stop_event=stop_event,
    )

    fetched_dois = [
        result.doi for result in results
    ]
    assert fetched_dois == [
        '10.1000/a',
        '10.1000/b',
    ]
    assert seen == [
        0,
        1,
    ]


def test_append_adds_blank_line_between_references():
    """New references follow existing text after a blank line."""
    text = append_reference('', 'First.', OutputFormat.BIBTEX)
    text = append_reference(text + '\n', 'Second.', OutputFormat.BIBTEX)
    assert text == 'First.\n\nSecond.'


def test_append_continues_numbering():
    """Numbering carries on from the highest number already present."""
    existing = '[1] First.\n\n[2] Second.'
    text = append_reference(existing, 'Third.', OutputFormat.CITATION, numbered=True)
    assert text == '[1] First.\n\n[2] Second.\n\n[3] Third.'


def test_append_never_numbers_bibtex():
    """Numbering is ignored where it would break the format's syntax."""
    assert append_reference('', BIBTEX, OutputFormat.BIBTEX, numbered=True) == BIBTEX


def test_append_extends_csl_json_array():
    """CSL JSON items accumulate in one importable array."""
    text = append_reference('', '{"id": 1}', OutputFormat.CSL_JSON)
    text = append_reference(text, '{"id": 2}', OutputFormat.CSL_JSON)
    assert json.loads(text) == [
        {'id': 1},
        {'id': 2},
    ]


def test_append_csl_json_after_other_text_falls_back_to_plain_append():
    """If the panel holds other text, the item is appended as text."""
    text = append_reference('notes', '{"id": 1}', OutputFormat.CSL_JSON)
    assert text == 'notes\n\n{"id": 1}'


def test_download_decodes_utf8(monkeypatch):
    """Non-ASCII author names survive decoding."""
    monkeypatch.setattr(urllib.request, 'urlopen', _fake_urlopen)
    assert core._download('https://doi.org/x', {}, 5) == 'Müller, Zoë'


def test_fetch_reference_logs_attempts_and_result(fake_download, caplog):
    """Log each failed attempt and where the reference came from."""
    fake_download(_http_error(404), BIBTEX)

    with caplog.at_level('DEBUG', logger='doi2ref'):
        fetch_reference('10.1038/nature14539', NO_DELAY, _throttle())

    assert f'GET {DOI_URL} (attempt 1 / ' in caplog.text
    assert 'doi.org attempt 1 /' in caplog.text
    assert 'permanent for this source' in caplog.text
    assert 'Found 10.1038/nature14539 via Crossref' in caplog.text
