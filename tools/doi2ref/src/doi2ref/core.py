"""Look up DOIs and return references in a chosen format.

Two sources are supported: doi.org content negotiation, which covers
Crossref, DataCite and mEDRA DOIs, and the Crossref transform endpoint.
Selected sources are tried in order, so the first is the main source and
later ones are used only for DOIs the earlier ones could not resolve.

Requests are sent one at a time with a minimum delay between them, and
failed requests can be retried, so the servers are not flooded. The
lookups use only the standard library; only the window needs PyQt6.
"""

import http.client
import json
import logging
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import Enum
from http import HTTPStatus

# Messages go nowhere until an application sets up logging; the window
# writes them to a log file (see doi2ref.logs).
_LOGGER = logging.getLogger(__name__)

__all__ = [
    'BROWSER_HEADERS',
    'CITATION_STYLES',
    'DEFAULT_DELAY_SECONDS',
    'DEFAULT_RETRIES',
    'DEFAULT_SOURCES',
    'DEFAULT_TIMEOUT_SECONDS',
    'LOCALES',
    'MAX_RETRY_AFTER_SECONDS',
    'FetchResult',
    'OutputFormat',
    'RequestOptions',
    'RequestThrottle',
    'Source',
    'StopRequested',
    'append_reference',
    'clean_response',
    'extract_dois',
    'fetch_reference',
    'fetch_references',
    'format_bibtex',
    'source_url',
]

DEFAULT_TIMEOUT_SECONDS = 20.0
DEFAULT_DELAY_SECONDS = 1.0
DEFAULT_RETRIES = 2
# The longest pause a server may request with Retry-After that is
# honoured, in seconds.
MAX_RETRY_AFTER_SECONDS = 60.0

# A generic desktop Chrome on Windows. Requests carry no name, address
# or machine detail of the user; the Accept header is set per request,
# because it is how the reference format is chosen.
BROWSER_HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/157.0.0.0 Safari/537.36'
    ),
    'Accept-Language': 'en-US,en;q=0.9',
}

# A DOI is "10.", a registrant code of 4-9 digits, "/", then a suffix
# that may contain almost anything except whitespace.
_DOI_IN_TEXT = re.compile(r"10\.\d{4,9}/[^\s\"'<>]+")
# Punctuation that commonly follows a DOI in prose but is not part of
# it.
_TRAILING_PUNCTUATION = '.,;:'
# Closing brackets are stripped only when unbalanced, because DOIs such
# as 10.1016/S0140-6736(20)30183-5 legitimately contain brackets.
_BRACKET_PAIRS = {
    ')': '(',
    ']': '[',
    '}': '{',
}
# A numbered citation such as "[12] Smith, J. ..." at the start of a
# line.
_CITATION_NUMBER = re.compile(r'^\[(\d+)\]', re.MULTILINE)

# Characters that structure a BibTeX entry.
_BIBTEX_OPEN_BRACE = '{'
_BIBTEX_CLOSE_BRACE = '}'
_BIBTEX_QUOTE = '"'
_BIBTEX_FIELD_SEPARATOR = ','
# An entry worth laying out has a citation key and at least one field.
_MIN_BIBTEX_PARTS = 2

# Display name -> CSL style identifier.
CITATION_STYLES = {
    'APA (7th)': 'apa',
    'Chicago (author-date)': 'chicago-author-date',
    'Harvard': 'harvard-cite-them-right',
    'IEEE': 'ieee',
    'MLA (9th)': 'modern-language-association',
    'Vancouver': 'vancouver',
    'Nature': 'nature',
    'American Chemical Society': 'american-chemical-society',
    'AMA': 'american-medical-association',
}

# Display name -> locale passed with formatted citations.
LOCALES = {
    'English (US)': 'en-US',
    'English (UK)': 'en-GB',
    'Deutsch': 'de-DE',
    'Français': 'fr-FR',
    'Español': 'es-ES',
    '中文 (简体)': 'zh-CN',
    '日本語': 'ja-JP',
}


class OutputFormat(Enum):
    """Reference formats that can be requested for a DOI."""

    CITATION = 'Formatted'
    BIBTEX = 'BibTeX'
    RIS = 'RIS'
    CSL_JSON = 'CSL JSON'

    @property
    def media_type(self) -> str:
        """Return the media type that requests this format."""
        media_types = {
            OutputFormat.CITATION: 'text/x-bibliography',
            OutputFormat.BIBTEX: 'application/x-bibtex',
            OutputFormat.RIS: 'application/x-research-info-systems',
            OutputFormat.CSL_JSON: 'application/vnd.citationstyles.csl+json',
        }
        return media_types[self]


class Source(Enum):
    """Services that can resolve a DOI to a reference."""

    DOI_ORG = 'doi.org'
    CROSSREF = 'Crossref'


# doi.org first, as it covers Crossref, DataCite and mEDRA DOIs; the
# Crossref API is the fallback.
DEFAULT_SOURCES = (
    Source.DOI_ORG,
    Source.CROSSREF,
)


# A control-flow signal rather than an error, so no "Error" suffix.
class StopRequested(Exception):  # noqa: N818
    """Raised inside a batch when the caller asks it to stop."""


@dataclass(frozen=True, slots=True)
class RequestOptions:
    """Settings that control how references are requested.

    Parameters
    ----------
    output_format : OutputFormat, default OutputFormat.BIBTEX
        Format to request.
    style : str, default 'apa'
        CSL style identifier; used only for ``OutputFormat.CITATION``.
    locale : str, default 'en-US'
        Locale tag; used only for ``OutputFormat.CITATION``.
    sources : tuple of Source, default DEFAULT_SOURCES
        Sources to try, in priority order. The first is the main source;
        each later one is tried only if the earlier ones failed.
    delay_seconds : float, default DEFAULT_DELAY_SECONDS
        Minimum time between any two requests, retries included.
    retries : int, default DEFAULT_RETRIES
        Extra attempts per source after a temporary failure (network
        error, timeout, HTTP 429 or 5xx). Permanent failures such as
        "DOI not found" are not retried.
    timeout_seconds : float, default DEFAULT_TIMEOUT_SECONDS
        Network timeout for each request.
    """

    output_format: OutputFormat = OutputFormat.BIBTEX
    style: str = 'apa'
    locale: str = 'en-US'
    sources: tuple[Source, ...] = DEFAULT_SOURCES
    delay_seconds: float = DEFAULT_DELAY_SECONDS
    retries: int = DEFAULT_RETRIES
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS


@dataclass(frozen=True, slots=True)
class FetchResult:
    """Outcome of looking up one DOI.

    Lookups set exactly one of ``reference`` and ``error``.

    Parameters
    ----------
    doi : str
        The DOI that was looked up.
    reference : str or None, default None
        Reference text, if the lookup succeeded.
    error : str or None, default None
        Reason for the last failure, if every source failed.
    source : Source or None, default None
        The source that supplied the reference.
    """

    doi: str
    reference: str | None = None
    error: str | None = None
    source: Source | None = None

    @property
    def ok(self) -> bool:
        """Return whether the lookup succeeded."""
        return self.reference is not None


class RequestThrottle:
    """Keep a minimum gap between consecutive requests.

    Parameters
    ----------
    delay_seconds : float
        Minimum seconds between the start of one request and the next.
    stop_event : threading.Event or None, default None
        When set, a pending wait ends early with :class:`StopRequested`.
    """

    def __init__(
        self,
        delay_seconds: float,
        stop_event: threading.Event | None = None,
    ) -> None:
        self.delay_seconds = delay_seconds
        self._stop_event = stop_event if stop_event is not None else threading.Event()
        self._next_allowed = 0.0

    def hold(self, seconds: float) -> None:
        """Delay the next request by at least ``seconds`` from now.

        Used when a server asks the client to slow down
        (``Retry-After``).

        Parameters
        ----------
        seconds : float
            Pause requested by the server.
        """
        self._next_allowed = max(self._next_allowed, time.monotonic() + seconds)

    def wait(self) -> None:
        """Block until the next request may be sent.

        Raises
        ------
        StopRequested
            If the stop event is set before or during the wait.
        """
        remaining = self._next_allowed - time.monotonic()
        # Event.wait doubles as an interruptible sleep.
        if remaining > 0 and self._stop_event.wait(remaining):
            raise StopRequested
        if self._stop_event.is_set():
            raise StopRequested
        self._next_allowed = time.monotonic() + self.delay_seconds


def extract_dois(text: str) -> list[str]:
    """Find all DOIs in free text, in order, without duplicates.

    Accepts bare DOIs, ``doi:`` prefixes, ``doi.org`` links (percent
    escapes are decoded) and DOIs embedded in sentences or reference
    lists.

    Parameters
    ----------
    text : str
        Text to search.

    Returns
    -------
    list of str
        DOIs in the order they first appear. Duplicates are compared
        case-insensitively, as DOIs are case-insensitive.
    """
    dois: list[str] = []
    seen: set[str] = set()
    for match in _DOI_IN_TEXT.finditer(text):
        doi = _strip_trailing_punctuation(urllib.parse.unquote(match.group()))
        folded_doi = doi.casefold()
        if folded_doi not in seen:
            seen.add(folded_doi)
            dois.append(doi)
    return dois


def source_url(
    source: Source,
    doi: str,
    output_format: OutputFormat,
) -> str:
    """Return the URL that asks a source for a DOI's reference.

    Parameters
    ----------
    source : Source
        Service to ask.
    doi : str
        Bare DOI.
    output_format : OutputFormat
        Requested format; Crossref takes it as part of the path.

    Returns
    -------
    str
        Request URL.
    """
    quoted_doi = urllib.parse.quote(doi, safe='/')
    if source is Source.DOI_ORG:
        return f'https://doi.org/{quoted_doi}'
    return (
        f'https://api.crossref.org/works/{quoted_doi}'
        f'/transform/{output_format.media_type}'
    )


def clean_response(body: str, output_format: OutputFormat) -> str:
    """Check that a response is in the requested format and tidy it.

    Parameters
    ----------
    body : str
        Decoded response body.
    output_format : OutputFormat
        Format that was requested.

    Returns
    -------
    str
        The reference with surrounding whitespace removed. BibTeX is
        laid out one field per line and CSL JSON is re-indented, for
        readability.

    Raises
    ------
    ValueError
        If the body is empty or does not look like the requested format
        (for example, an HTML landing page). Formatted citations are
        only checked for being non-empty text without markup.
    """
    # A byte-order mark is not whitespace, so strip() alone keeps it.
    text = (
        body
        .lstrip('﻿')
        .strip()
    )
    if not text:
        raise ValueError('empty response')
    # Landing pages may start with a doctype, <html>, a comment or
    # <head>; no reference format starts with "<", so any markup is
    # rejected.
    if text.startswith('<'):
        raise ValueError('received a web page instead of a reference')

    if output_format is OutputFormat.BIBTEX:
        if not text.startswith('@'):
            raise ValueError('response is not BibTeX')
        text = format_bibtex(text)
    if output_format is OutputFormat.RIS and not text.startswith('TY  -'):
        raise ValueError('response is not RIS')
    if output_format is OutputFormat.CSL_JSON:
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as err:
            raise ValueError('response is not valid JSON') from err
        text = json.dumps(parsed, indent=2, ensure_ascii=False)

    return text


def format_bibtex(entry: str) -> str:
    """Lay out a BibTeX entry with one field per line.

    Parameters
    ----------
    entry : str
        A single BibTeX entry such as
        ``@article{key, title={...}, ...}``.

    Returns
    -------
    str
        The entry with each field on its own indented line. The entry
        is returned unchanged if it cannot be split safely: no opening
        brace, no closing brace at the end, unbalanced braces or
        quotes, or no field after the citation key.
    """
    open_index = entry.find(_BIBTEX_OPEN_BRACE)
    if open_index == -1 or not entry.endswith(_BIBTEX_CLOSE_BRACE):
        return entry

    head = entry[: open_index + 1]
    parts = _split_top_level(entry[open_index + 1 : -1])
    if parts is None or len(parts) < _MIN_BIBTEX_PARTS:
        return entry

    citation_key, *fields = parts
    lines = [f'{head}{citation_key},']
    lines += [
        f'  {field},' for field in fields[:-1]
    ]
    lines.append(f'  {fields[-1]}')
    lines.append(_BIBTEX_CLOSE_BRACE)
    return '\n'.join(lines)


def fetch_reference(
    doi: str,
    options: RequestOptions,
    throttle: RequestThrottle,
) -> FetchResult:
    """Look up one DOI, trying each source in order with retries.

    Network, HTTP and format errors are captured in the result rather
    than raised, so a batch can carry on past a bad DOI.

    Parameters
    ----------
    doi : str
        Bare DOI, e.g. ``"10.1038/nature14539"``.
    options : RequestOptions
        Request settings.
    throttle : RequestThrottle
        Shared throttle that spaces out requests.

    Returns
    -------
    FetchResult
        The reference and its source, or the last error seen.

    Raises
    ------
    StopRequested
        If the throttle's stop event is set while waiting.
    """
    headers = {
        **BROWSER_HEADERS,
        'Accept': _accept_header(options),
    }
    last_error = 'no source selected'

    for source in options.sources:
        url = source_url(source, doi, options.output_format)
        attempts = options.retries + 1
        for attempt in range(1, attempts + 1):
            throttle.wait()
            _LOGGER.debug('GET %s (attempt %d / %d)', url, attempt, attempts)
            try:
                body = _download(url, headers, options.timeout_seconds)
                reference = clean_response(body, options.output_format)
            except (OSError, http.client.HTTPException, ValueError) as err:
                last_error = f'{source.value}: {_describe_error(err)}'
                retry_after = _retry_after_seconds(err)
                if retry_after is not None:
                    throttle.hold(retry_after)
                if isinstance(err, urllib.error.HTTPError):
                    # Release the connection held by the error response.
                    err.close()
                temporary = _is_temporary(err)
                _LOGGER.warning(
                    '%s attempt %d / %d failed for %s: %s (%s)',
                    source.value,
                    attempt,
                    attempts,
                    doi,
                    _describe_error(err),
                    'temporary' if temporary else 'permanent for this source',
                )
                if temporary:
                    continue
                # Permanent for this source; move on to the next one.
                break
            _LOGGER.info('Found %s via %s', doi, source.value)
            return FetchResult(doi=doi, reference=reference, source=source)

    _LOGGER.warning('No reference for %s: %s', doi, last_error)
    return FetchResult(doi=doi, error=last_error)


def fetch_references(
    dois: Sequence[str],
    options: RequestOptions,
    on_result: Callable[[int, FetchResult], None] | None = None,
    stop_event: threading.Event | None = None,
) -> list[FetchResult]:
    """Look up DOIs one after another, spaced by the configured delay.

    Parameters
    ----------
    dois : sequence of str
        Bare DOIs.
    options : RequestOptions
        Request settings shared by every lookup.
    on_result : callable or None, default None
        Called as ``on_result(index, result)`` after each DOI, in order.
    stop_event : threading.Event or None, default None
        Set it from another thread to stop after the current request.
        A DOI whose lookup is cut short this way gets no result.

    Returns
    -------
    list of FetchResult
        Results in input order. Shorter than ``dois`` if stopped early.
    """
    throttle = RequestThrottle(options.delay_seconds, stop_event)
    results: list[FetchResult] = []

    for index, doi in enumerate(dois):
        _LOGGER.info('DOI %d / %d: %s', index + 1, len(dois), doi)
        try:
            result = fetch_reference(doi, options, throttle)
        except StopRequested:
            _LOGGER.info('Stopped before %s finished', doi)
            break
        results.append(result)
        if on_result is not None:
            on_result(index, result)

    return results


def append_reference(
    existing: str,
    reference: str,
    output_format: OutputFormat,
    numbered: bool = False,
) -> str:
    """Append one reference to text that may already hold references.

    Parameters
    ----------
    existing : str
        Current text, possibly empty or edited by hand.
    reference : str
        New reference in ``output_format``.
    output_format : OutputFormat
        Format of the new reference.
    numbered : bool, default False
        Prefix formatted citations with the next ``[n]``, continuing
        from the highest number already present. Ignored for the other
        formats, where numbering would break the syntax.

    Returns
    -------
    str
        The combined text, with surrounding whitespace removed and
        references separated by a blank line. For CSL JSON, if
        ``existing`` is empty or a JSON array and ``reference`` is
        valid JSON, the item is added to that array so the result stays
        importable.
    """
    current = existing.strip()

    if output_format is OutputFormat.CSL_JSON:
        merged = _append_csl_item(current, reference)
        if merged is not None:
            return merged

    if output_format is OutputFormat.CITATION and numbered:
        numbers = [
            int(number) for number in _CITATION_NUMBER.findall(current)
        ]
        next_number = max(numbers, default=0) + 1
        reference = f'[{next_number}] {reference}'

    if not current:
        return reference
    return f'{current}\n\n{reference}'


def _strip_trailing_punctuation(doi: str) -> str:
    """Remove sentence punctuation and unbalanced brackets from the end.

    Parameters
    ----------
    doi : str
        DOI as matched in free text.

    Returns
    -------
    str
        The DOI without trailing ``.,;:`` or closing brackets that have
        no opening partner inside the DOI.
    """
    while doi:
        last_character = doi[-1]
        if last_character in _TRAILING_PUNCTUATION:
            doi = doi[:-1]
            continue
        opening_bracket = _BRACKET_PAIRS.get(last_character)
        if (
            opening_bracket is None
            or doi.count(last_character) <= doi.count(opening_bracket)
        ):
            break
        doi = doi[:-1]
    return doi


def _accept_header(options: RequestOptions) -> str:
    """Build the HTTP Accept header for the requested format.

    Parameters
    ----------
    options : RequestOptions
        Request settings.

    Returns
    -------
    str
        Value for the ``Accept`` header.
    """
    media_type = options.output_format.media_type
    if options.output_format is OutputFormat.CITATION:
        return f'{media_type}; style={options.style}; locale={options.locale}'
    return media_type


def _split_top_level(body: str) -> list[str] | None:
    """Split BibTeX entry contents at commas outside braces and quotes.

    Parameters
    ----------
    body : str
        Text between the entry's outer braces.

    Returns
    -------
    list of str or None
        Stripped, non-empty parts (citation key first), or None if the
        braces or quotes are unbalanced.
    """
    parts: list[str] = []
    current_characters: list[str] = []
    brace_depth = 0
    in_quotes = False

    for character in body:
        if character == _BIBTEX_QUOTE and brace_depth == 0:
            in_quotes = not in_quotes
        elif character == _BIBTEX_OPEN_BRACE:
            brace_depth += 1
        elif character == _BIBTEX_CLOSE_BRACE:
            brace_depth -= 1
            if brace_depth < 0:
                return None
        elif (
            character == _BIBTEX_FIELD_SEPARATOR
            and brace_depth == 0
            and not in_quotes
        ):
            part = ''.join(current_characters)
            parts.append(part.strip())
            current_characters = []
            continue
        current_characters.append(character)

    if brace_depth != 0 or in_quotes:
        return None
    last_part = ''.join(current_characters)
    parts.append(last_part.strip())
    return [
        part for part in parts
        if part
    ]


def _download(
    url: str,
    headers: dict[str, str],
    timeout: float,
) -> str:
    """Download a URL and decode it as UTF-8.

    Parameters
    ----------
    url : str
        URL to fetch.
    headers : dict of str to str
        HTTP request headers.
    timeout : float
        Timeout in seconds.

    Returns
    -------
    str
        Decoded response body; undecodable bytes become U+FFFD.

    Raises
    ------
    OSError
        If the request fails, including ``urllib.error.HTTPError`` for
        HTTP error statuses and ``TimeoutError``.
    http.client.HTTPException
        If the connection breaks off mid-response.
    """
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body: bytes = response.read()
    return body.decode('utf-8', errors='replace')


def _is_temporary(err: Exception) -> bool:
    """Return whether a failed request is worth retrying.

    Parameters
    ----------
    err : Exception
        The error raised while fetching.

    Returns
    -------
    bool
        True for network errors, timeouts, dropped connections, HTTP
        429 and HTTP 5xx; False for other HTTP errors and wrong-format
        replies.
    """
    if isinstance(err, urllib.error.HTTPError):
        return (
            err.code == HTTPStatus.TOO_MANY_REQUESTS
            or err.code >= HTTPStatus.INTERNAL_SERVER_ERROR
        )
    return isinstance(err, OSError | http.client.HTTPException)


def _retry_after_seconds(err: Exception) -> float | None:
    """Read a server's ``Retry-After`` pause from an HTTP error.

    Parameters
    ----------
    err : Exception
        The error raised while fetching.

    Returns
    -------
    float or None
        Seconds to wait, capped at ``MAX_RETRY_AFTER_SECONDS``, or None
        if the error carries no usable ``Retry-After`` value in
        seconds.
    """
    if not isinstance(err, urllib.error.HTTPError) or err.headers is None:
        return None
    response_headers = err.headers
    header_value = response_headers.get('Retry-After', '')
    try:
        seconds = float(header_value)
    except ValueError:
        # The HTTP-date form is rare for these APIs and is ignored.
        return None
    if seconds < 0:
        return None
    return min(seconds, MAX_RETRY_AFTER_SECONDS)


def _describe_error(err: Exception) -> str:
    """Turn a network or format error into a short message.

    Parameters
    ----------
    err : Exception
        The error raised while fetching.

    Returns
    -------
    str
        Human-readable reason.
    """
    if isinstance(err, urllib.error.HTTPError):
        if err.code == HTTPStatus.NOT_FOUND:
            return 'DOI not found'
        if err.code == HTTPStatus.TOO_MANY_REQUESTS:
            return 'too many requests (HTTP 429); increase the waiting time'
        return f'HTTP {err.code} {err.reason}'
    if isinstance(err, urllib.error.URLError):
        return f'network error: {err.reason}'
    if isinstance(err, TimeoutError):
        return 'request timed out'
    if isinstance(err, OSError | http.client.HTTPException):
        return f'connection error: {err!r}'
    return str(err)


def _append_csl_item(current: str, reference: str) -> str | None:
    """Add a CSL JSON item to a JSON array held as text.

    Parameters
    ----------
    current : str
        Existing text, stripped; empty or a JSON array to extend.
    reference : str
        CSL JSON object or array to add; an array is merged item by
        item.

    Returns
    -------
    str or None
        The extended array as indented JSON, or None if either text is
        not JSON of the expected shape.
    """
    try:
        items = json.loads(current) if current else []
        new_item = json.loads(reference)
    except json.JSONDecodeError:
        return None
    if not isinstance(items, list):
        return None

    if isinstance(new_item, list):
        items.extend(new_item)
    else:
        items.append(new_item)
    return json.dumps(items, indent=2, ensure_ascii=False)
