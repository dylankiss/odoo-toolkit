import io
import re
import threading
import time
import zipfile
from collections.abc import Callable, Collection, Generator, Iterable, Mapping
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from enum import StrEnum
from os import environ
from typing import Annotated, Any, TypedDict, TypeVar, cast
from urllib.parse import urljoin

from requests import ConnectionError as RequestsConnectionError
from requests import HTTPError, JSONDecodeError, Response, Session
from requests import Timeout as RequestsTimeout
from requests.adapters import HTTPAdapter
from rich.progress import TaskID
from typer import Exit, Option
from urllib3 import BaseHTTPResponse
from urllib3.util.retry import Retry

from odoo_toolkit.common import (
    HTTP_HEADERS,
    TransientProgress,
    console,
    print_error,
    print_warning,
)

WEBLATE_URL = environ.get("WEBLATE_URL", "https://translate.odoo.com")
WEBLATE_API_TOKEN = environ.get("WEBLATE_API_TOKEN")

WEBLATE_PROJECT_COMPONENTS_ENDPOINT = "/api/projects/{project}/components/"
WEBLATE_GROUPS_ENDPOINT = "/api/groups/"
WEBLATE_GROUP_ENDPOINT = "/api/groups/{group}/"
WEBLATE_GROUP_PROJECTS_ENDPOINT = "/api/groups/{group}/projects/"
WEBLATE_GROUP_PROJECT_ENDPOINT = "/api/groups/{group}/projects/{project}/"
WEBLATE_GROUP_ROLES_ENDPOINT = "/api/groups/{group}/roles/"
WEBLATE_GROUP_ROLE_ENDPOINT = "/api/groups/{group}/roles/{role}/"
WEBLATE_GROUP_LANGUAGES_ENDPOINT = "/api/groups/{group}/languages/"
WEBLATE_GROUP_LANGUAGE_ENDPOINT = "/api/groups/{group}/languages/{language}/"
WEBLATE_PROJECTS_ENDPOINT = "/api/projects/"
WEBLATE_ROLES_ENDPOINT = "/api/roles/"
WEBLATE_TRANSLATIONS_FILE_ENDPOINT = "/api/translations/{project}/{component}/{language}/file/"
WEBLATE_PROJECT_LANGUAGE_FILE_ENDPOINT = "/api/projects/{project}/languages/{language}/file/"

WEBLATE_ERR_1 = "Please configure WEBLATE_API_TOKEN in your current environment."

# Below this many components, fetching each file individually costs few enough requests that it beats downloading the
# whole project archive, whose size is driven by the project rather than by the selection.
BULK_ZIP_MIN_COMPONENTS = 10

# translate.odoo.com sits behind nginx, which allows bursts of up to 1000 requests, and 2 requests per second after
# that. It refuses more with HTTP 429, without saying how long to wait.
WEBLATE_REQUESTS_PER_SECOND = 2
WEBLATE_REQUEST_BURST = 1000
# Weblate itself allows a number of requests per hour (5000 at the time of writing), in a sliding window, and reports
# what's left of it in the X-RateLimit-* headers, with the seconds until the oldest request leaves the window. All
# requests wait for that once fewer than this many remain, which covers the requests that are still in flight.
WEBLATE_RATE_LIMIT_RESERVE = 20
# The rate limiter controls the start of every request, so extra workers can never breach the rate limits. They only keep
# requests in flight while others wait on the server, which is what actually saturates the allowed rate.
WEBLATE_MAX_WORKERS = 10

# Seconds to wait for a connection, and for the next bytes of a response. Generous, since exporting or importing a
# large project can take minutes.
REQUEST_TIMEOUT = (15, 600)
TOO_MANY_REQUESTS = 429
NOT_FOUND = 404
# Only waits at least this long are reported.
REPORTED_WAIT = 5
# Seconds all requests wait at least after a rate limited one, so nginx's limit can recover.
RATE_LIMITED_PAUSE = 1

T = TypeVar("T")


class WeblateRateLimiter:
    """Limit the requests to Weblate like nginx does, and make all of them wait when Weblate's rate limit runs out.

    Like nginx, it allows a burst of requests, refilled at a steady rate. We can't know how much of nginx's burst other
    runs used up, so we start with a full burst, and fall back to the steady rate as soon as nginx refuses a request.

    It's shared by all requests and their retries, across threads and `WeblateApi` objects.
    """

    def __init__(
        self,
        requests_per_second: float,
        burst: int,
        reserve: int,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """Initialize the rate limiter.

        :param requests_per_second: The steady rate of requests.
        :param burst: How many requests may start at once.
        :param reserve: Wait for Weblate's rate limit window to reset once fewer requests than this remain in it.
        :param clock: Get the current time in seconds, like `time.monotonic`.
        :param sleep: Wait for a number of seconds, like `time.sleep`.
        """
        self._clock = clock
        self._sleep = sleep
        self.requests_per_second = requests_per_second
        self.burst = burst
        self.reserve = reserve
        self._tokens = float(burst)
        self._updated_at = self._clock()
        self._paused_until = 0.0
        self._reported_until = 0.0
        self._lock = threading.Lock()

    def _refill(self, now: float) -> None:
        if now > self._updated_at:
            self._tokens = min(self.burst, self._tokens + (now - self._updated_at) * self.requests_per_second)
            self._updated_at = now

    def acquire(self) -> None:
        """Wait until the next request may start."""
        while True:
            with self._lock:
                now = self._clock()
                self._refill(now)
                if now < self._paused_until:
                    wait_time = self._paused_until - now
                elif self._tokens >= 1:
                    self._tokens -= 1
                    return
                else:
                    wait_time = (1 - self._tokens) / self.requests_per_second
            # Wait without blocking others, then check again, since all requests may have been paused meanwhile.
            self._sleep(wait_time)

    def drain(self) -> None:
        """Fall back to the steady rate, since nginx's burst is used up."""
        with self._lock:
            self._refill(self._clock())
            self._tokens = 0
            # Don't refill while all requests are paused, so nginx's limit can recover.
            self._updated_at = max(self._updated_at, self._paused_until)

    def pause(self, seconds: float, reason: str) -> None:
        """Make all requests wait.

        :param seconds: How long to wait.
        :param reason: Why, to report long waits.
        """
        with self._lock:
            until = self._clock() + seconds
            self._paused_until = max(self._paused_until, until)
            # Parallel requests often hit the same limit, so only report a wait that ends noticeably later.
            report = seconds >= REPORTED_WAIT and until > self._reported_until + 1
            if report:
                self._reported_until = until
        if report:
            print_warning(f"{reason}, waiting {_format_duration(seconds)} before continuing.")

    def update(self, headers: Mapping[str, str]) -> None:
        """Pause all requests if Weblate's rate limit is (almost) used up, according to a response's headers.

        Requests that are exempt from the rate limit don't get these headers.
        """
        try:
            remaining = int(headers["X-RateLimit-Remaining"])
            reset = float(headers["X-RateLimit-Reset"])
        except (KeyError, ValueError):
            return
        if remaining < self.reserve and reset > 0:
            limit = headers.get("X-RateLimit-Limit", "?")
            self.pause(reset, f"Only {remaining} of Weblate's {limit} requests are left in its rate limit window")


def _format_duration(seconds: float) -> str:
    if seconds < 120:  # noqa: PLR2004
        return f"{seconds:.0f} second(s)"
    return f"{seconds / 60:.0f} minute(s)"


RATE_LIMITER = WeblateRateLimiter(WEBLATE_REQUESTS_PER_SECOND, WEBLATE_REQUEST_BURST, WEBLATE_RATE_LIMIT_RESERVE)


class WeblateRetry(Retry):
    """Retry failed Weblate requests: rate limits, server errors and connection problems.

    Requests that change something (like uploading a file) are only retried when Weblate certainly didn't process them:
    when nginx or Weblate refused them because of a rate limit, or when the connection couldn't be made. Retrying them
    after a server error or a timeout could perform them twice.

    Retries go through the shared rate limiter as well.
    """

    def is_retry(self, method: str, status_code: int, has_retry_after: bool = False) -> bool:
        """Retry rate limited requests of any method, since nginx or Weblate refused them before processing them."""
        if status_code == TOO_MANY_REQUESTS:
            return True
        return super().is_retry(method, status_code, has_retry_after)

    def sleep(self, response: BaseHTTPResponse | None = None) -> None:
        """Wait before retrying, making all requests wait if we hit a rate limit."""
        if response is not None:
            RATE_LIMITER.update(response.headers)
        if response is None or response.status != TOO_MANY_REQUESTS:
            time.sleep(self.get_backoff_time())
        elif (retry_after := self.get_retry_after(response)) is None:
            # nginx doesn't say how long to wait, but a short pause lets its limit recover.
            RATE_LIMITER.pause(max(self.get_backoff_time(), RATE_LIMITED_PAUSE), "nginx's rate limit was hit")
            RATE_LIMITER.drain()
        else:
            RATE_LIMITER.pause(retry_after, "Weblate's rate limit was hit")
        RATE_LIMITER.acquire()


RETRY = WeblateRetry(
    total=None,
    # Connecting failed, so nothing was sent: safe to retry for any request.
    connect=3,
    # Reading the response failed: only retried for requests that don't change anything, and only once, since every
    # attempt can wait for the whole read timeout.
    read=1,
    # Rate limits and server errors (server errors only for requests that don't change anything).
    status=10,
    other=0,
    status_forcelist=[TOO_MANY_REQUESTS, 500, 502, 503, 504],
    backoff_factor=1,
    backoff_jitter=1,
    backoff_max=30,
    # Return the last response instead of raising, so it becomes a WeblateApiError with Weblate's message.
    raise_on_status=False,
)


class WeblateComponentData(TypedDict, total=False):
    """The parts of a Weblate component the toolkit uses, from the API and in `.weblate.json`."""

    filemask: str
    language_regex: str
    name: str
    new_base: str
    slug: str


class WeblateGroupResponse(TypedDict):
    """Minimal response structure of a Weblate group."""

    id: int
    name: str
    defining_project: str | None
    project_selection: int
    language_selection: int
    roles: list[str]
    languages: list[str]
    projects: list[str]


class WeblateTranslationsUploadResponse(TypedDict):
    """Minimal response structure of a Weblate translations upload request."""

    not_found: int
    skipped: int
    accepted: int
    total: int


class WeblateApiError(Exception):
    """Custom exception for Weblate API errors."""

    def __init__(self, response: Response) -> None:
        """Parse the error response.

        :param response: The response object from the Weblate API call.
        """
        self.response = response
        self.status_code: int | None = response.status_code
        self.error_type = None
        self.errors: list[dict[str, Any]] = []

        try:
            data = response.json()
            self.error_type = data.get("type")
            self.errors = data.get("errors", [{"code": None, "detail": response.text}])
        except JSONDecodeError:
            self.errors = [{"code": None, "detail": response.text}]

        super().__init__(self.__str__())

    @property
    def summary(self) -> str:
        """Describe the error in one line, like `HTTP 404: Not found.`."""
        detail = next((str(err["detail"]) for err in self.errors if err.get("detail")), "")
        first_line = detail.strip().splitlines()[0] if detail.strip() else ""
        return f"HTTP {self.status_code}: {first_line[:200]}" if first_line else f"HTTP {self.status_code}"

    def __str__(self) -> str:
        """Provide a clean string representation, listing all errors."""
        request = self.response.request
        headers = {**request.headers}
        # Header values can be bytes as well, so mask the token whatever its type.
        if "Authorization" in headers:
            headers["Authorization"] = "Bearer **********"
        # Uploads send whole files, which would drown the error.
        body = request.body
        if isinstance(body, bytes | str) and (
            len(body) > 500 or str(headers.get("Content-Type", "")).startswith("multipart/")  # noqa: PLR2004
        ):
            body = f"<{len(body)} bytes>"
        error_list = [
            f"HTTP {self.status_code} ({self.error_type}): The request failed with {len(self.errors)} error(s).",
            f"  Request URL: {request.url}",
            f"  Request Headers: {headers}",
            f"  Request Body: {body}",
            f"  Status Code: {self.status_code} ({self.error_type})",
        ]
        for i, err in enumerate(self.errors):
            code = err.get("code", "N/A")
            detail = err.get("detail", "No details provided.")
            attr = err.get("attr", "")
            error_list.append(f"  ({i + 1}) Field: '{attr}' | Code: '{code}' | Detail: '{detail}'")
        return "\n".join(error_list)


class WeblateConnectionError(WeblateApiError):
    """Weblate couldn't be reached, or didn't answer in time."""

    def __init__(self, error: RequestsConnectionError | RequestsTimeout) -> None:
        """Initialize the error.

        :param error: The connection error.
        """
        self.error = error
        self.response = None
        self.status_code = None
        self.error_type = "connection"
        self.errors = [{"code": None, "detail": str(error)}]
        self.url = error.request.url if error.request else None
        Exception.__init__(self, self.__str__())

    @property
    def summary(self) -> str:
        """Describe the error in one line."""
        # Read timeouts that were retried end up as connection errors, but still mention the timeout.
        timed_out = isinstance(self.error, RequestsTimeout) or "timed out" in str(self.error).lower()
        return f"Weblate {'timed out' if timed_out else "couldn't connect"}"

    def __str__(self) -> str:
        """Describe the error and the request that failed."""
        return f"{self.summary}: {self.error}\n  Request URL: {self.url}"


class WeblateTokenMissingError(Exception):
    """The Weblate API token isn't set in the environment."""


def get_weblate_api() -> "WeblateApi":
    """Get a client of the Weblate API, or tell how to configure it.

    :raises Exit: If the API token isn't set.
    :return: The client.
    """
    try:
        return WeblateApi()
    except WeblateTokenMissingError as e:
        print_error(str(e))
        raise Exit(1) from e


class WeblateApi:
    """A wrapper for making calls to the Weblate API."""

    def __init__(self) -> None:
        """Initialize a WeblateApi object.

        :raises WeblateTokenMissingError: If `WEBLATE_API_TOKEN` isn't set in the environment.
        """
        if not WEBLATE_API_TOKEN:
            raise WeblateTokenMissingError(WEBLATE_ERR_1)
        self.base_url = WEBLATE_URL
        self.session = Session()
        self.session.headers.update({**HTTP_HEADERS, "Authorization": f"Bearer {WEBLATE_API_TOKEN}"})
        for prefix in ("https://", "http://"):
            self.session.mount(prefix, HTTPAdapter(max_retries=RETRY))

    def _send(self, method: str, url: str, **kwargs: Any) -> Response:  # noqa: ANN401
        """Send a request at the allowed rate. The session retries it if needed.

        :raises WeblateApiError: If the request failed.
        :raises WeblateConnectionError: If Weblate couldn't be reached, or didn't answer in time.
        """
        RATE_LIMITER.acquire()
        try:
            response = self.session.request(method, url, timeout=REQUEST_TIMEOUT, **kwargs)
        except (RequestsConnectionError, RequestsTimeout) as e:
            raise WeblateConnectionError(e) from e
        RATE_LIMITER.update(response.headers)
        try:
            response.raise_for_status()
        except HTTPError as e:
            raise WeblateApiError(response) from e
        return response

    def request(
        self,
        return_type: type[T],  # noqa: ARG002
        method: str,
        endpoint: str,
        *,
        data: Mapping[str, Any] | None = None,
        files: Mapping[str, Any] | None = None,
        json: Mapping[str, Any] | None = None,
        params: Mapping[str, Any] | None = None,
    ) -> T:
        """Call a Weblate API endpoint.

        :param return_type: The type of the response, for type checking.
        :param method: The HTTP method, like `GET` or `POST`.
        :param endpoint: The API endpoint to access.
        :param data: The form data to send, if any.
        :param files: The files to send with the form data, if any.
        :param json: The JSON payload to send, if any.
        :param params: The query parameters to pass, if any.
        :raises WeblateApiError: If the request returns an error.
        :return: The response, parsed from JSON.
        """
        response = self._send(
            method,
            urljoin(self.base_url, endpoint),
            data=data,
            files=files,
            json=json,
            params=params,
            headers={"Accept": "application/json"},
        )
        try:
            return cast("T", response.json() if response.content else {})
        except JSONDecodeError as e:
            raise WeblateApiError(response) from e

    def get(self, return_type: type[T], endpoint: str, *, params: Mapping[str, Any] | None = None) -> T:
        """Get the response of a Weblate API endpoint, parsed from JSON.

        :raises WeblateApiError: If the request returns an error.
        """
        return self.request(return_type, "GET", endpoint, params=params)

    def get_bytes(self, endpoint: str, *, params: Mapping[str, Any] | None = None) -> bytes:
        """Get the response from a Weblate API endpoint as bytes, like a file.

        :param endpoint: The API endpoint to access.
        :param params: The query parameters to pass, if any.
        :raises WeblateApiError: If the request returns an error.
        :return: The raw response content.
        """
        return self._send("GET", urljoin(self.base_url, endpoint), params=params).content

    def get_generator(
        self,
        return_type: type[T],  # noqa: ARG002
        endpoint: str,
        *,
        params: Mapping[str, Any] | None = None,
    ) -> Generator[T]:
        """Fetch all results from a Weblate API endpoint, following its pages.

        :param endpoint: The API endpoint to access.
        :param params: Query parameters for the first request. The next pages use the `next` URL Weblate provides,
            which already holds them.
        :raises WeblateApiError: If the request returns an error.
        :yield: Every element in the `results` section of the response(s).
        """
        url: str | None = endpoint
        while url:
            data = self.request(dict[str, Any], "GET", url, params=params)
            yield from cast("list[T]", data.get("results", []))
            url = cast("str | None", data.get("next"))
            params = None


class UploadMethod(StrEnum):
    """Upload methods available to the translation upload endpoint."""

    TRANSLATE = "translate"
    APPROVE = "approve"
    SUGGEST = "suggest"


class UploadConflicts(StrEnum):
    """Conflict handling available to the translation upload endpoint."""

    IGNORE = "ignore"
    REPLACE_TRANSLATED = "replace-translated"
    REPLACE_APPROVED = "replace-approved"


class UploadFuzzy(StrEnum):
    """Fuzzy handling available to the translation upload endpoint."""

    IGNORE = "ignore"
    PROCESS = "process"
    APPROVE = "approve"


MethodOption = Annotated[
    UploadMethod,
    Option(
        "--method",
        "-m",
        help="Upload the translations as reviewed strings (`approve`), non-reviewed strings (`translate`), or "
        "suggestions (`suggest`).",
    ),
]
OverwriteOption = Annotated[
    UploadConflicts,
    Option(
        "--overwrite",
        "-o",
        help="Don't overwrite existing translations (`ignore`), overwrite only non-reviewed translations "
        "(`replace-translated`), or overwrite even approved translations (`replace-approved`).",
    ),
]


def upload_translations(
    api: "WeblateApi",
    project: str,
    component: str,
    language: str,
    content: bytes,
    upload_data: Mapping[str, str],
) -> WeblateTranslationsUploadResponse:
    """Upload a PO file to a translation on Weblate.

    :param api: The Weblate API to use.
    :param project: The project slug.
    :param component: The component slug.
    :param language: The Weblate language code, like `zh_Hans`.
    :param content: The PO file.
    :param upload_data: The upload options, like the method and the author.
    :raises WeblateApiError: If the upload failed.
    :return: What Weblate did with the translations.
    """
    return api.request(
        WeblateTranslationsUploadResponse,
        "POST",
        WEBLATE_TRANSLATIONS_FILE_ENDPOINT.format(project=project, component=component, language=language),
        data=dict(upload_data),
        files={"file": (f"{project}-{component}-{language}.po", content)},
    )


def format_upload_counts(accepted: int, skipped: int, not_found: int) -> str:
    """Describe the result of translation uploads, like `updated 10 and skipped 2 translation(s)`."""
    text = f"updated {accepted} and skipped {skipped} translation(s)"
    if not_found:
        text += f", and {not_found} source string(s) weren't found"
    return text


def get_weblate_project_component_slugs(api: WeblateApi, project: str) -> set[str]:
    """Fetch and return a set of component slugs for a given project.

    :param api: The Weblate API to use.
    :param project: The project slug to find the components for.
    :raises WeblateApiError: If the request returns an error.
    :return: A set of component slugs for the given project.
    """
    return {
        c.get("slug", "")
        for c in api.get_generator(
            WeblateComponentData,
            WEBLATE_PROJECT_COMPONENTS_ENDPOINT.format(project=project),
            params={"page_size": 1000},
        )
    }


# Matches the header lines Weblate fills in per translation, which must not be carried over to another translation.
PO_CLEAN_HEADER_PATTERN = re.compile(b'^"(?:Language|Plural-Forms):.*\n', flags=re.MULTILINE)
# Matches a `msgstr` (or `msgstr[n]`) whose value is non-empty, either inline or on a following continuation line.
PO_TRANSLATED_PATTERN = re.compile(
    rb'^msgstr(?:\[\d+\])? "(?:[^"]|\\")+"|^msgstr(?:\[\d+\])? ""\n"(?:[^"]|\\")+"',
    flags=re.MULTILINE,
)
# Matches the start of any entry.
PO_ENTRY_PATTERN = re.compile(rb"^msgid ", flags=re.MULTILINE)
# Matches the leading comment block and `msgid ""` line that open the PO header entry.
PO_HEADER_PATTERN = re.compile(rb'(?:#[^\n]*\n)*msgid ""\n')


def clean_po_header(po_file: bytes) -> bytes:
    """Strip the translation-specific header lines from a PO file, so it can be uploaded to another translation."""
    return PO_CLEAN_HEADER_PATTERN.sub(b"", po_file)


def _po_body(po_file: bytes) -> bytes:
    """Return a PO file's contents without the header entry, or the whole file if it has no recognizable header."""
    body = po_file.replace(b"\r\n", b"\n")
    # The header entry's `msgstr` is always filled in, so it must not count as an entry or a translation.
    if PO_HEADER_PATTERN.match(body):
        _, _, body = body.partition(b"\n\n")
    return body


def po_has_entries(po_file: bytes) -> bool:
    """Return whether a PO file contains at least one entry besides its header, translated or not.

    :param po_file: The raw contents of the PO file.
    :return: True if the file has at least one entry, False if it only contains the header.
    """
    return PO_ENTRY_PATTERN.search(_po_body(po_file)) is not None


def po_has_translations(po_file: bytes) -> bool:
    """Return whether a PO file contains at least one non-empty translation.

    The check is deliberately fail-safe: when the file does not open with a recognizable PO header, the whole file is
    scanned instead, so callers skip a file only when it is certainly not worth uploading.

    :param po_file: The raw contents of the PO file.
    :return: True if the file has at least one translation, False if it is certainly untranslated.
    """
    return PO_TRANSLATED_PATTERN.search(_po_body(po_file)) is not None


def extract_po_files_from_zip(zip_bytes: bytes, components: Collection[str] | None = None) -> dict[str, bytes]:
    """Extract the PO files from a Weblate archive of one language of a project, keyed by component slug.

    Weblate stores all components that share a Git repo under a single VCS directory. Inside the ZIP, every Odoo PO
    file sits at `…/{module}/i18n/{language}.po`, so the component slug (= module name) is always at `parts[-3]` of
    the ZIP entry path. The file names use Odoo's language codes, like `zh_CN.po`, not Weblate's.

    :param zip_bytes: The raw ZIP archive as returned by the project language file endpoint.
    :param components: Only extract these component slugs, or all of them when None.
    :raises zipfile.BadZipFile: If the archive could not be read.
    :return: A mapping of component slug to the raw contents of its PO file.
    """
    po_files: dict[str, bytes] = {}
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zip_file:
        for name in zip_file.namelist():
            parts = name.split("/")
            # All Odoo PO files live at …/{module}/i18n/{language}.po
            if len(parts) < 3 or parts[-2] != "i18n" or not parts[-1].endswith(".po"):  # noqa: PLR2004
                continue
            component_slug = parts[-3]
            if components is not None and component_slug not in components:
                continue
            po_files[component_slug] = zip_file.read(name)
    return po_files


@dataclass
class LanguageFiles:
    """The PO files of one language of a project."""

    # The PO files, by component slug.
    files: dict[str, bytes] = field(default_factory=dict[str, bytes])
    # The components that don't have the language, like localization modules for other countries.
    missing: set[str] = field(default_factory=set[str])
    # Why the other components couldn't be fetched.
    failures: dict[str, str] = field(default_factory=dict[str, str])


def fetch_language_files(
    api: WeblateApi,
    project: str,
    language_code: str,
    components: set[str],
    *,
    query: str | None = None,
    progress: TransientProgress,
    task_id: TaskID,
) -> LanguageFiles:
    """Fetch the PO files of one language of a project.

    Without a query, the files of many components are fetched in a single request for the whole project, instead of one
    request per component. That request can't filter strings, so a query needs one request per component.

    :param api: The Weblate API to use.
    :param project: The project to fetch the files from.
    :param language_code: The CLDR language code, like `zh_Hans`.
    :param components: The component slugs to fetch.
    :param query: The Weblate search string to filter the strings by, if any.
    :param progress: The progress bar to advance.
    :param task_id: The progress bar task to advance.
    :return: The files, the components without the language, and the ones that failed.
    """
    if not query and len(components) >= BULK_ZIP_MIN_COMPONENTS:
        try:
            zip_bytes = api.get_bytes(
                WEBLATE_PROJECT_LANGUAGE_FILE_ENDPOINT.format(project=project, language=language_code),
                params={"format": "zip"},
            )
            po_files = extract_po_files_from_zip(zip_bytes, components)
        except (WeblateApiError, zipfile.BadZipFile) as e:
            reason = e.summary if isinstance(e, WeblateApiError) else f"invalid ZIP file ({e})"
            print_warning(
                f"Downloading all {language_code} files at once failed ({reason}), so downloading them one by one.",
            )
        else:
            progress.advance(task_id, len(components))
            return LanguageFiles(files=po_files, missing=components - set(po_files))

    params = {"format": "po"} | ({"q": query} if query else {})
    results = run_concurrently(
        sorted(components),
        lambda component: api.get_bytes(
            WEBLATE_TRANSLATIONS_FILE_ENDPOINT.format(project=project, component=component, language=language_code),
            params=params,
        ),
        progress=progress,
        task_id=task_id,
    )
    language_files = LanguageFiles()
    for component, result in results.items():
        if isinstance(result, bytes):
            language_files.files[component] = result
        elif result.status_code == NOT_FOUND:
            language_files.missing.add(component)
        else:
            language_files.failures[component] = result.summary
    return language_files


def run_concurrently[K, R](
    items: Iterable[K],
    work: Callable[[K], R],
    *,
    progress: TransientProgress,
    task_id: TaskID,
) -> dict[K, R | WeblateApiError]:
    """Run Weblate requests for many items at once, at the allowed rate.

    :param items: The items to run the work for, like components.
    :param work: The work to run for each item.
    :param progress: The progress bar to advance.
    :param task_id: The progress bar task to advance for every item done.
    :return: The result or the Weblate error per item.
    """
    results: dict[K, R | WeblateApiError] = {}
    with ThreadPoolExecutor(max_workers=WEBLATE_MAX_WORKERS) as executor:
        futures = {executor.submit(work, item): item for item in items}
        for future in as_completed(futures):
            try:
                results[futures[future]] = future.result()
            except WeblateApiError as e:
                results[futures[future]] = e
            progress.advance(task_id)
    return results


def print_failures(failures: Mapping[str, str]) -> None:
    """Print failures, grouped by reason, since many usually fail for the same reason."""
    by_reason: dict[str, list[str]] = {}
    for item, reason in sorted(failures.items()):
        by_reason.setdefault(reason, []).append(item)
    for reason, items in by_reason.items():
        console.print(f"  {reason}: {', '.join(items)}")
