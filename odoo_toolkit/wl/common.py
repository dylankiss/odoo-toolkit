import contextlib
import io
import json
import math
import random
import re
import threading
import time
import zipfile
from collections import defaultdict
from collections.abc import Callable, Collection, Generator, Mapping
from email.utils import parsedate_to_datetime
from enum import StrEnum
from fnmatch import fnmatch
from os import environ
from pathlib import Path
from typing import Any, Literal, TypedDict, TypeVar, cast
from urllib.parse import urljoin

from requests import ConnectionError as RequestsConnectionError
from requests import ConnectTimeout, HTTPError, JSONDecodeError, Response, Session
from requests import Timeout as RequestsTimeout

from odoo_toolkit.common import print_warning

WEBLATE_URL = environ.get("WEBLATE_URL", "https://translate.odoo.com")
WEBLATE_API_TOKEN = environ.get("WEBLATE_API_TOKEN")

WEBLATE_PROJECT_COMPONENTS_ENDPOINT = "/api/projects/{project}/components/"
WEBLATE_COMPONENT_ENDPOINT = "/api/components/{project}/{component}/"
WEBLATE_AUTOTRANSLATE_ENDPOINT = "/api/translations/{project}/{component}/{language}/autotranslate/"
WEBLATE_GROUPS_ENDPOINT = "/api/groups/"
WEBLATE_GROUP_ENDPOINT = "/api/groups/{group}/"
WEBLATE_GROUP_PROJECTS_ENDPOINT = "/api/groups/{group}/projects/"
WEBLATE_GROUP_PROJECT_ENDPOINT = "/api/groups/{group}/projects/{project}/"
WEBLATE_GROUP_ROLES_ENDPOINT = "/api/groups/{group}/roles/"
WEBLATE_GROUP_ROLE_ENDPOINT = "/api/groups/{group}/roles/{role}/"
WEBLATE_GROUP_LANGUAGES_ENDPOINT = "/api/groups/{group}/languages/"
WEBLATE_GROUP_LANGUAGE_ENDPOINT = "/api/groups/{group}/languages/{language}/"
WEBLATE_PROJECT_ENDPOINT = "/api/projects/{project}/"
WEBLATE_PROJECT_LANGUAGES_ENDPOINT = "/api/projects/{project}/languages/"
WEBLATE_PROJECTS_ENDPOINT = "/api/projects/"
WEBLATE_ROLES_ENDPOINT = "/api/roles/"
WEBLATE_TRANSLATIONS_FILE_ENDPOINT = "/api/translations/{project}/{component}/{language}/file/"
WEBLATE_PROJECT_LANGUAGE_FILE_ENDPOINT = "/api/projects/{project}/languages/{language}/file/"

WEBLATE_ERR_1 = "Please configure WEBLATE_API_TOKEN in your current environment."

# The Weblate server accepts 2 requests per second. Exceeding it only buys us HTTP 429s and their back-off penalty.
WEBLATE_REQUESTS_PER_SECOND = 2
# The throttle above serializes the start of every request, so extra workers can never breach the rate limit. They only
# keep requests in flight while others wait on the server, which is what actually saturates the allowed rate.
WEBLATE_MAX_WORKERS = 10

MAX_RETRIES = 3
RETRY_STATUS_CODES = frozenset({500, 502, 503, 504})
IDEMPOTENT_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "PUT", "DELETE"})
TOO_MANY_REQUESTS = 429
NOT_FOUND = 404

T = TypeVar("T")

_rate_limit_lock = threading.Lock()
_rate_limit_until: list[float] = [0.0]


def _set_rate_limit(retry_after: int) -> None:
    deadline = time.time() + retry_after
    with _rate_limit_lock:
        _rate_limit_until[0] = max(_rate_limit_until[0], deadline)


def _wait_if_rate_limited() -> None:
    with _rate_limit_lock:
        until = _rate_limit_until[0]
    remaining = until - time.time()
    if remaining > 0:
        print_warning(f"Weblate rate limit hit, waiting about {remaining:.0f} second(s) before retrying.")
        time.sleep(remaining + random.uniform(0, 5))  # noqa: S311


def _parse_retry_after(retry_after: str) -> int | None:
    """Parse a Retry-After header, given either as a number of seconds or as an HTTP date."""
    with contextlib.suppress(ValueError):
        return max(0, int(retry_after))
    with contextlib.suppress(TypeError, ValueError):
        return max(0, math.ceil(parsedate_to_datetime(retry_after).timestamp() - time.time()))
    return None


def _with_retry(make_request: Callable[[], Response], *, idempotent: bool = True) -> Response:
    """Execute make_request(), retrying on HTTP 429, on transient server errors, and on connection failures.

    Non-idempotent requests are only retried when the server certainly didn't process them (HTTP 429 or a failure to
    connect), so a retry can never perform the same action twice.
    """
    retryable_errors = (RequestsConnectionError, RequestsTimeout) if idempotent else (ConnectTimeout,)
    attempt = 0
    while True:
        _wait_if_rate_limited()
        try:
            response = make_request()
        except retryable_errors:
            attempt += 1
            if attempt > MAX_RETRIES:
                raise
            time.sleep(2**attempt)
            continue
        if response.status_code == TOO_MANY_REQUESTS:
            seconds_until_reset = _parse_retry_after(response.headers.get("Retry-After", ""))
            if seconds_until_reset is None:
                raise WeblateApiError(response)
            _set_rate_limit(seconds_until_reset)
            continue
        if idempotent and response.status_code in RETRY_STATUS_CODES:
            attempt += 1
            if attempt > MAX_RETRIES:
                return response
            time.sleep(2**attempt)
            continue
        return response


class WeblatePagedResponse(TypedDict):
    """Minimal response structure of Weblate paged results."""

    count: int
    next: str | None


class WeblateFileFormatParamsData(TypedDict, total=False):
    """Partial structure of Weblate file format parameters."""

    po_fuzzy_matching: bool
    po_keep_previous: bool
    po_line_wrap: int
    po_no_location: bool

class WeblateProjectData(TypedDict):
    """Partial structure of a Weblate project."""

    id: int
    slug: str


class WeblateProjectDetailData(TypedDict, total=False):
    """Partial structure of a Weblate project's details."""

    slug: str
    name: str
    contribute_shared_tm: bool
    use_shared_tm: bool


class WeblateAutotranslateResponse(TypedDict, total=False):
    """Minimal response structure of a Weblate autotranslate request."""

    details: str


class WeblateProjectLanguageData(TypedDict, total=False):
    """Partial structure of a Weblate project language statistic."""

    code: str
    language: str


class WeblateComponentData(TypedDict, total=False):
    """Partial structure of a Weblate component."""

    id: int
    allow_translation_propagation: bool
    auto_lock_error: bool
    branch: str
    commit_pending_age: int
    enable_suggestions: bool
    file_format: str
    file_format_params: WeblateFileFormatParamsData
    filemask: str
    language_code_style: str
    language_regex: str
    license: str
    linked_component: str | None
    merge_style: str
    name: str
    new_base: str
    new_lang: str
    project: WeblateProjectData
    push: str
    push_branch: str
    push_on_commit: bool
    repo: str
    slug: str
    suggestion_voting: bool
    suggestion_autoaccept: int
    url: str
    vcs: str


WeblateConfigType = dict[str, dict[str, list[WeblateComponentData]]]

# Maps fnmatch glob patterns (matched against module names) to a fixed list of language codes.
# Patterns are evaluated in order; the first match wins.
# Modules not listed here fall back to scanning available .po files.
L10N_LANGUAGE_MAP: dict[str, list[str] | None] = {
    "*l10n_account_withholding_tax*": ["es_419", "id", "th"],
    "*l10n_ae*": ["ar"],
    "*l10n_ar*": ["es_419"],
    "*l10n_at*": ["de"],
    "*l10n_bd*": ["bn"],
    "*l10n_be*": ["de", "fr", "nl"],
    "*l10n_bf*": ["fr"],
    "*l10n_bg*": ["bg"],
    "*l10n_bh*": ["ar"],
    "*l10n_bj*": ["fr"],
    "*l10n_bo*": ["es_419"],
    "*l10n_br*": ["pt_BR"],
    "*l10n_ca*": ["fr"],
    "*l10n_cd*": ["fr"],
    "*l10n_cf*": ["fr"],
    "*l10n_cg*": ["fr"],
    "*l10n_ch*": ["de", "fr", "it"],
    "*l10n_ci*": ["fr"],
    "*l10n_cl*": ["es_419"],
    "*l10n_cm*": ["fr"],
    "*l10n_cn_city*": None,
    "*l10n_cn*": ["zh_CN"],
    "*l10n_co*": ["es_419"],
    "*l10n_cr*": ["es_419"],
    "*l10n_cy*": ["el", "tr"],
    "*l10n_cz*": ["cs"],
    "*l10n_de*": ["de"],
    "*l10n_din5008*": ["de", "fr", "it"],
    "*l10n_dk*": ["da"],
    "*l10n_do*": ["es_419"],
    "*l10n_dz*": ["ar", "fr"],
    "*l10n_ec*": ["es_419"],
    "*l10n_ee*": ["et"],
    "*l10n_eg*": ["ar"],
    "*l10n_employment_hero*": ["ms"],
    "*l10n_es*": ["es"],
    "*l10n_et*": ["am"],
    "*l10n_eu*": ["bg", "cs", "da", "de", "el", "es", "et", "fi", "fr", "hr", "hu", "it", "lt", "lv", "nl", "pl", "pt", "ro", "sk", "sl", "sv"],
    "*l10n_fi*": ["fi"],
    "*l10n_fr*": ["fr"],
    "*l10n_ga*": ["fr"],
    "*l10n_gcc*": ["ar"],
    "*l10n_gn*": ["fr"],
    "*l10n_gp*": ["fr"],
    "*l10n_gq*": ["es", "fr", "pt"],
    "*l10n_gr*": ["el"],
    "*l10n_gt*": ["es_419"],
    "*l10n_gw*": ["pt"],
    "*l10n_hk*": ["zh_TW"],
    "*l10n_hn*": ["es_419"],
    "*l10n_hr*": ["hr"],
    "*l10n_hu*": ["hu"],
    "*l10n_id*": ["id"],
    "*l10n_il*": ["he"],
    "*l10n_in*": ["hi"],
    "*l10n_iq*": ["ar"],
    "*l10n_it*": ["it"],
    "*l10n_jo*": ["ar"],
    "*l10n_jp*": ["ja"],
    "*l10n_ke*": ["sw"],
    "*l10n_kh*": ["km"],
    "*l10n_km*": ["ar", "fr"],
    "*l10n_kr*": ["ko"],
    "*l10n_kw*": ["ar"],
    "*l10n_kz*": ["ru"],
    "*l10n_latam*": ["es_419", "pt_BR"],
    "*l10n_lb*": ["ar", "fr"],
    "*l10n_lt*": ["lt"],
    "*l10n_lu*": ["de", "fr", "lb"],
    "*l10n_lv*": ["lv"],
    "*l10n_ma*": ["ar", "fr"],
    "*l10n_mc*": ["fr"],
    "*l10n_mk*": ["mk"],
    "*l10n_ml*": ["fr"],
    "*l10n_mm*": ["my"],
    "*l10n_mn*": ["mn"],
    "*l10n_mq*": ["fr"],
    "*l10n_mr*": ["fr"],
    "*l10n_mu*": ["fr"],
    "*l10n_mx*": ["es_419"],
    "*l10n_my*": ["ms"],
    "*l10n_mz*": ["pt"],
    "*l10n_ne*": ["fr"],
    "*l10n_nl*": ["nl"],
    "*l10n_no*": ["nb"],
    "*l10n_om*": ["ar"],
    "*l10n_pa*": ["es_419"],
    "*l10n_pe*": ["es_419"],
    "*l10n_ph*": ["tl"],
    "*l10n_pl*": ["pl"],
    "*l10n_pt*": ["pt"],
    "*l10n_qa*": ["ar"],
    "*l10n_re*": ["fr"],
    "*l10n_ro*": ["ro"],
    "*l10n_rs*": ["sr@latin"],
    "*l10n_rw*": ["fr", "sw"],
    "*l10n_sa*": ["ar"],
    "*l10n_se*": ["sv"],
    "*l10n_si*": ["sl"],
    "*l10n_sk*": ["sk"],
    "*l10n_sn*": ["fr"],
    "*l10n_syscohada*": ["fr"],
    "*l10n_td*": ["ar", "fr"],
    "*l10n_tg*": ["fr"],
    "*l10n_th*": ["th"],
    "*l10n_tn*": ["ar", "fr"],
    "*l10n_tr*": ["tr"],
    "*l10n_tw*": ["zh_TW"],
    "*l10n_tz*": ["sw"],
    "*l10n_ua*": ["uk"],
    "*l10n_ug*": ["sw"],
    "*l10n_us*": ["es_419"],
    "*l10n_uy*": ["es_419"],
    "*l10n_uz*": ["ru", "uz"],
    "*l10n_ve*": ["es_419"],
    "*l10n_vn*": ["vi"],
    "*l10n_yt*": ["fr"],
}


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


class WeblateRoleResponse(TypedDict):
    """Minimal response structure of a Weblate role."""

    id: int
    name: str


class WeblateApiError(Exception):
    """Custom exception for Weblate API errors."""

    def __init__(self, response: Response) -> None:
        """Parse the error response.

        :param response: The response object from the Weblate API call.
        """
        self.response = response
        self.status_code = response.status_code
        self.error_type = None
        self.errors: list[dict[str, Any]] = []

        try:
            data = response.json()
            self.error_type = data.get("type")
            self.errors = data.get("errors", [{"code": None, "detail": response.text}])
        except JSONDecodeError:
            self.errors = [{"code": None, "detail": response.text}]

        super().__init__(self.__str__())

    def __str__(self) -> str:
        """Provide a clean string representation, listing all errors."""
        headers = {**self.response.request.headers}
        if "Authorization" in headers and headers["Authorization"].startswith("Token "):
            headers["Authorization"] = "Token **********"
        error_list = [
            f"HTTP {self.status_code} ({self.error_type}): The request failed with {len(self.errors)} error(s).",
            f"  Request URL: {self.response.request.url}",
            f"  Request Headers: {headers}",
            f"  Request Body: {self.response.request.body}",
            f"  Status Code: {self.status_code} ({self.error_type})",
        ]
        for i, err in enumerate(self.errors):
            code = err.get("code", "N/A")
            detail = err.get("detail", "No details provided.")
            attr = err.get("attr", "")
            error_list.append(f"  ({i+1}) Field: '{attr}' | Code: '{code}' | Detail: '{detail}'")
        return "\n".join(error_list)


class WeblateApi:
    """A wrapper for making calls to the Weblate API."""

    def __init__(self, requests_per_second: int | None = None) -> None:
        """Initialize a WeblateApi object.

        :raises NameError: If the WEBLATE_API_TOKEN in the environment is falsy.
        """
        if not WEBLATE_API_TOKEN:
            raise NameError(WEBLATE_ERR_1)
        self.base_url = WEBLATE_URL
        self._request_interval = 1 / requests_per_second if requests_per_second else 0
        self._next_request_at = 0.0
        self._request_throttle_lock = threading.Lock()
        self.session = Session()
        self.session.headers.update({
            "Authorization": f"Token {WEBLATE_API_TOKEN}",
            "User-Agent": "Odoo Toolkit",
        })
        self.json_session = Session()
        self.json_session.headers.update({
            "Authorization": f"Token {WEBLATE_API_TOKEN}",
            "Accept": "application/json",
            "User-Agent": "Odoo Toolkit",
        })

    def _throttle_request_rate(self) -> None:
        if not self._request_interval:
            return
        with self._request_throttle_lock:
            now = time.monotonic()
            wait_time = self._next_request_at - now
            if wait_time > 0:
                time.sleep(wait_time)
                now = time.monotonic()
            self._next_request_at = now + self._request_interval

    def _request(
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
        """Do an HTTP request and handle errors.

        :param method: An HTTP method verb.
        :param endpoint: The API endpoint to access.
        :param json: The JSON payload to send, defaults to None.
        :raises WeblateApiError: If the request returns an error.
        :return: The response in JSON format.
        """
        url = urljoin(self.base_url, endpoint)
        response = _with_retry(
            lambda: self._rate_limited_request(
                lambda: self.json_session.request(method, url, data=data, files=files, json=json, params=params),
            ),
            idempotent=method.upper() in IDEMPOTENT_METHODS,
        )
        try:
            response.raise_for_status()
            return cast("T", response.json() if response.content else {})
        except (HTTPError, JSONDecodeError) as e:
            raise WeblateApiError(response) from e

    def get_bytes(self, endpoint: str, *, params: Mapping[str, Any] | None = None) -> bytes:
        """Get the response from a Weblate API endpoint as bytes.

        :param endpoint: The API endpoint to access.
        :param params: The query parameters to pass, defaults to None.
        :raises WeblateApiError: If the request returns an error.
        :return: The raw response content as bytes.
        """
        url = urljoin(self.base_url, endpoint)
        response = _with_retry(lambda: self._rate_limited_request(lambda: self.session.get(url, params=params)))
        try:
            response.raise_for_status()
        except (HTTPError, JSONDecodeError) as e:
            raise WeblateApiError(response) from e
        else:
            return response.content

    def get_generator(
        self,
        return_type: type[T],  # noqa: ARG002
        endpoint: str,
        *,
        params: Mapping[str, Any] | None = None,
    ) -> Generator[T]:
        """Fetch all results from a paginated Weblate API endpoint.

        :param endpoint: The API endpoint to access.
        :param params: Query parameters for the first request. Subsequent pages use the
            server-provided ``next`` URL which already embeds pagination params.
        :raises WeblateApiError: If the request returns an error.
        :yield: Every element in the `results` section of the response(s).
        """
        current_url = urljoin(self.base_url, endpoint)
        current_params = params
        while current_url:
            response = _with_retry(
                lambda url=current_url, p=current_params: self._rate_limited_request(
                    lambda: self.json_session.get(url, params=p),
                ),
            )
            data: dict[str, Any] = {}
            try:
                response.raise_for_status()
                data = response.json() if response.content else {}
            except (HTTPError, JSONDecodeError) as e:
                raise WeblateApiError(response) from e
            yield from cast("list[T]", data.get("results", []))
            current_url = cast("str | None", data.get("next"))
            current_params = None  # next URL already includes all query params

    def get(
        self,
        return_type: type[T],
        endpoint: str,
        *,
        data: Mapping[str, Any] | None = None,
        json: Mapping[str, Any] | None = None,
        params: Mapping[str, Any] | None = None,
    ) -> T:
        """Perform a GET request against a Weblate API endpoint.

        :param endpoint: The API endpoint to access.
        :param json: The JSON payload to send, defaults to None.
        :raises WeblateApiError: If the request returns an error.
        :return: The response in JSON format.
        """
        return self._request(return_type, "GET", endpoint, data=data, json=json, params=params)

    def post(
        self,
        return_type: type[T],
        endpoint: str,
        *,
        data: Mapping[str, Any] | None = None,
        files: Mapping[str, Any] | None = None,
        json: Mapping[str, Any] | None = None,
        params: Mapping[str, Any] | None = None,
    ) -> T:
        """Perform a POST request against a Weblate API endpoint.

        :param endpoint: The API endpoint to access.
        :param json: The JSON payload to send, defaults to None.
        :raises WeblateApiError: If the request returns an error.
        :return: The response in JSON format.
        """
        return self._request(return_type, "POST", endpoint, data=data, files=files, json=json, params=params)

    def _rate_limited_request(self, request: Callable[[], Response]) -> Response:
        self._throttle_request_rate()
        return request()

    def patch(
        self,
        return_type: type[T],
        endpoint: str,
        *,
        data: Mapping[str, Any] | None = None,
        json: Mapping[str, Any] | None = None,
        params: Mapping[str, Any] | None = None,
    ) -> T:
        """Perform a PATCH request against a Weblate API endpoint.

        :param endpoint: The API endpoint to access.
        :param json: The JSON payload to send, defaults to None.
        :raises WeblateApiError: If the request returns an error.
        :return: The response in JSON format.
        """
        return self._request(return_type, "PATCH", endpoint, data=data, json=json, params=params)

    def delete(
        self,
        return_type: type[T],
        endpoint: str,
        *,
        data: Mapping[str, Any] | None = None,
        json: Mapping[str, Any] | None = None,
        params: Mapping[str, Any] | None = None,
    ) -> T:
        """Perform a DELETE request against a Weblate API endpoint.

        :param endpoint: The API endpoint to access.
        :param json: The JSON payload to send, defaults to None.
        :raises WeblateApiError: If the request returns an error.
        :return: The response in JSON format.
        """
        return self._request(return_type, "DELETE", endpoint, data=data, json=json, params=params)


class WeblateConfigError(Exception):
    """Custom exception for WeblateConfig errors."""

    def __init__(self, file_path: Path, error_type: Literal["load", "save"]) -> None:
        """Initialize a WeblateConfigError for loading `file_path`."""
        match error_type:
            case "load":
                super().__init__(f"The configuration file '{file_path}' could not be loaded.")
            case "save":
                super().__init__(f"The configuration file '{file_path}' could not be saved.")


class WeblateConfig:
    """A Weblate config file."""

    def __init__(self, file_path: Path) -> None:
        """Initialize a WeblateConfig object.

        :param file_path: The file to load into the object or to save the new object to.
        :raises WeblateConfigError: If the given file could not be loaded or parsed.
        """
        self.file_path = file_path
        self.config: dict[str, dict[str, list[WeblateComponentData]]] = {
            "projects": defaultdict[str, list[WeblateComponentData]](list),
        }

        if self.file_path.is_file():
            try:
                projects = json.loads(self.file_path.read_text()).get("projects")
                if projects:
                    self.config["projects"].update(projects)
            except (OSError, json.JSONDecodeError) as e:
                raise WeblateConfigError(self.file_path, "load") from e

    def update_module(self, module_path: Path, project: str, languages: list[str]) -> bool:
        """Update a module configuration in the Weblate config file.

        If the `.pot` file exists, the module configuration will be added or updated.
        If the `.pot` file doesn't exist, the module configuration will be removed.

        If no languages are provided and the module is a l10n module, only the languages available in that module will
        be added.

        :param module_path: The path to the module to update.
        :param project: The Weblate project slug.
        :param languages: The specific language codes to translate this module into.
        :return: True if the module was added or updated, False if it couldn't be added or updated, or was removed.
        """
        module_name = module_path.name
        existing_module_config = next(
            (c for c in self.config["projects"][project] if c.get("name") == module_name), None,
        )
        if not (module_path / "i18n" / f"{module_name}.pot").is_file():
            if existing_module_config:
                self.config["projects"][project].remove(existing_module_config)
            return False

        relative_module_path = module_path.relative_to(self.file_path.parent)
        module_config: WeblateComponentData = {
            "name": module_name,
            "filemask": f"{relative_module_path}/i18n/*.po",
            "new_base": f"{relative_module_path}/i18n/{module_name}.pot",
        }
        if not languages and "l10n_" in module_name:
            # If we are adding a l10n module, check the fixed language map first.
            fixed_language_match = next(
                ((pattern, langs) for pattern, langs in L10N_LANGUAGE_MAP.items() if fnmatch(module_name, pattern)),
                None,
            )
            if fixed_language_match is None:
                languages = sorted([lang.stem for lang in (module_path / "i18n").glob("*.po") if lang.is_file()])
            else:
                _, fixed_languages = fixed_language_match
                languages = fixed_languages or []
        if languages:
            module_config["language_regex"] = f"^({'|'.join(sorted(languages))})$"
        if existing_module_config:
            existing_module_config.update(module_config)
        else:
            self.config["projects"][project].append(module_config)
        return True

    def save(self) -> None:
        """Save the Weblate config to a file.

        :raises WeblateConfigError: If the given file could not be saved.
        """

        def sort_config(config: WeblateConfigType) -> WeblateConfigType:
            return {
                "projects": {
                    project: sorted(components, key=lambda c: c.get("name", ""))
                    for project, components in sorted(config["projects"].items())
                },
            }

        try:
            self.file_path.write_text(f"{json.dumps(sort_config(self.config), indent=4)}\n")
        except (OSError, json.JSONDecodeError) as e:
            raise WeblateConfigError(self.file_path, "save") from e

    def clear(self, project: str | None = None) -> None:
        """Clear one or all projects' components from the config."""
        if project:
            self.config["projects"].get(project, {}).clear()
        else:
            self.config["projects"].clear()

    def get_projects(self) -> list[str]:
        """Get the list of projects in the config file."""
        return list(self.config["projects"])

    def get_components(self, project: str) -> list[WeblateComponentData]:
        """Get the list of components for a given project."""
        return self.config["projects"].get(project, [])


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


def get_weblate_components(api: WeblateApi, project: str) -> list[WeblateComponentData]:
    """Fetch and return a list of components for a given project.

    :param api: The Weblate API to use.
    :param project: The project slug to find the components for.
    :raises WeblateApiError: If the request returns an error.
    :return: A list of components for the given project.
    """
    return list(
        api.get_generator(
            WeblateComponentData,
            WEBLATE_PROJECT_COMPONENTS_ENDPOINT.format(project=project),
            params={"page_size": 1000},
        ),
    )


def get_weblate_project_languages(api: WeblateApi, project: str) -> list[str]:
    """Fetch and return the language codes present in a given project.

    :param api: The Weblate API to use.
    :param project: The project slug to find the languages for.
    :raises WeblateApiError: If the request returns an error.
    :return: A sorted list of language codes available in the given project.
    """
    return sorted(
        code
        for language in api.get_generator(
            WeblateProjectLanguageData,
            WEBLATE_PROJECT_LANGUAGES_ENDPOINT.format(project=project),
            params={"page_size": 1000},
        )
        if (code := language.get("code"))
    )


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
    body = po_file.replace(b"\r\n", b"\n") if b"\r\n" in po_file else po_file
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


def extract_po_files_from_zip(
    zip_bytes: bytes,
    language_code: str,
    components: Collection[str] | None = None,
) -> dict[str, bytes]:
    """Extract the PO files of one language from a Weblate project ZIP, keyed by component slug.

    Weblate stores all components that share a Git repo under a single VCS directory. Inside the ZIP, every Odoo PO
    file sits at `…/{module}/i18n/{language}.po`, so the component slug (= module name) is always at `parts[-3]` of
    the ZIP entry path.

    :param zip_bytes: The raw ZIP archive as returned by the project language file endpoint.
    :param language_code: The CLDR language code whose PO files should be extracted.
    :param components: Only extract these component slugs, or all of them when None.
    :raises zipfile.BadZipFile: If the archive could not be read.
    :return: A mapping of component slug to the raw contents of its PO file.
    """
    po_files: dict[str, bytes] = {}
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zip_file:
        for name in zip_file.namelist():
            parts = name.split("/")
            # All Odoo PO files live at …/{module}/i18n/{language}.po
            if len(parts) < 3 or parts[-2] != "i18n" or parts[-1] != f"{language_code}.po":  # noqa: PLR2004
                continue
            component_slug = parts[-3]
            if components is not None and component_slug not in components:
                continue
            po_files[component_slug] = zip_file.read(name)
    return po_files
