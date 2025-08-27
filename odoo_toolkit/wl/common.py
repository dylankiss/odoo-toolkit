import json
from collections import defaultdict
from collections.abc import Generator
from os import environ
from pathlib import Path
from typing import Any, Literal, TypedDict, TypeVar
from urllib.parse import urljoin

from requests import HTTPError, JSONDecodeError, Response, Session

WEBLATE_URL = environ.get("WEBLATE_URL", "https://translate.odoo.com")
WEBLATE_API_TOKEN = environ.get("WEBLATE_API_TOKEN")

WEBLATE_PROJECT_COMPONENTS_ENDPOINT = "/api/projects/{project}/components/"
WEBLATE_AUTOTRANSLATE_ENDPOINT = "/api/translations/{project}/{component}/{language}/autotranslate/"
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

WEBLATE_ERR_1 = "Please configure WEBLATE_API_TOKEN in your current environment."

T = TypeVar("T")
WeblateConfigType = dict[str, dict[str, list[dict[str, str]]]]


class WeblatePagedResponse(TypedDict):
    """Minimal response structure of Weblate paged results."""

    count: int
    next: str | None


class WeblateProjectResponse(TypedDict):
    """Minimal response structure of a Weblate project."""

    id: int
    slug: str


class WeblateComponentResponse(TypedDict):
    """Minimal response structure of a Weblate component."""

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
        error_list = [
            f"HTTP {self.status_code} ({self.error_type}): The request failed with {len(self.errors)} error(s).",
            f"  Request URL: {self.response.request.url}",
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

    def __init__(self) -> None:
        """Initialize a WeblateApi object.

        :raises NameError: If the WEBLATE_API_TOKEN in the environment is falsy.
        """
        if not WEBLATE_API_TOKEN:
            raise NameError(WEBLATE_ERR_1)
        self.base_url = WEBLATE_URL
        self.session = Session()
        self.session.headers.update({
            "Authorization": f"Token {WEBLATE_API_TOKEN}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "Odoo Toolkit",
        })

    def _request(self, return_type: type[T], method: str, endpoint: str, *, json: dict[str, Any] | None = None) -> T:  # noqa: ARG002
        """Do an HTTP request and handle errors.

        :param method: An HTTP method verb.
        :param endpoint: The API endpoint to access.
        :param json: The JSON payload to send, defaults to None.
        :raises WeblateApiError: If the request returns an error.
        """
        url = urljoin(self.base_url, endpoint)
        response = self.session.request(method, url, json=json)
        try:
            response.raise_for_status()
            return response.json() if response.content else {} # pyright: ignore[reportReturnType]
        except (HTTPError, JSONDecodeError) as e:
            raise WeblateApiError(response) from e

    def get_generator(self, return_type: type[T], endpoint: str) -> Generator[T]:  # noqa: ARG002
        """Fetch all results from a paginated Weblate API endpoint.

        :param endpoint: The API endpoint to access.
        :raises WeblateApiError: If the request returns an error.
        :yield: Every element in the `results` section of the response(s).
        """
        current_url = urljoin(self.base_url, endpoint)
        while current_url:
            response = self.session.get(current_url)
            data: dict[str, Any] = {}
            try:
                response.raise_for_status()
                data = response.json() if response.content else {}
            except (HTTPError, JSONDecodeError) as e:
                raise WeblateApiError(response) from e
            yield from data.get("results", [])
            current_url = data.get("next")

    def get(self, return_type: type[T], endpoint: str, *, json: dict[str, Any] | None = None) -> T:
        """Perform a GET request against a Weblate API endpoint.

        :param endpoint: The API endpoint to access.
        :param json: The JSON payload to send, defaults to None.
        :raises WeblateApiError: If the request returns an error.
        """
        return self._request(return_type, "GET", endpoint, json=json)

    def post(self, return_type: type[T], endpoint: str, *, json: dict[str, Any] | None = None) -> T:
        """Perform a POST request against a Weblate API endpoint.

        :param endpoint: The API endpoint to access.
        :param json: The JSON payload to send, defaults to None.
        :raises WeblateApiError: If the request returns an error.
        """
        return self._request(return_type, "POST", endpoint, json=json)

    def patch(self, return_type: type[T], endpoint: str, *, json: dict[str, Any] | None = None) -> T:
        """Perform a PATCH request against a Weblate API endpoint.

        :param endpoint: The API endpoint to access.
        :param json: The JSON payload to send, defaults to None.
        :raises WeblateApiError: If the request returns an error.
        """
        return self._request(return_type, "PATCH", endpoint, json=json)

    def delete(self, return_type: type[T], endpoint: str, *, json: dict[str, Any] | None = None) -> T:
        """Perform a DELETE request against a Weblate API endpoint.

        :param endpoint: The API endpoint to access.
        :param json: The JSON payload to send, defaults to None.
        :raises WeblateApiError: If the request returns an error.
        """
        return self._request(return_type, "DELETE", endpoint, json=json)


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
        self.config = defaultdict[str, dict[str, list[dict[str, str]]]](lambda: defaultdict[str, list[dict[str, str]]](list))

        if self.file_path.is_file():
            try:
                self.config.update(json.loads(self.file_path.read_text()))
            except (OSError, json.JSONDecodeError) as e:
                raise WeblateConfigError(self.file_path, "load") from e

    def add_module(self, module_path: Path, project: str, languages: list[str]) -> None:
        """Add a module configuration to the Weblate config file.

        :param module_path: The path to the module to add.
        :param project: The Weblate project slug.
        :param languages: The specific language codes to translate this module into.
        """
        module_name = module_path.name
        relative_module_path = module_path.relative_to(self.file_path.parent.parent)
        module_config = {
            "name": module_name,
            "filemask": f"{relative_module_path}/i18n/*.po",
            "new_base": f"{relative_module_path}/i18n/{module_name}.pot",
        }
        if languages:
            module_config["language_regex"] = f"^({'|'.join(sorted(languages))})$"
        existing_module_config = next((c for c in self.config["projects"][project] if c["name"] == module_name), None)
        if existing_module_config:
            existing_module_config.update(module_config)
        else:
            self.config["projects"][project].append(module_config)

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
            self.file_path.write_text(json.dumps(sort_config(self.config), indent=4))
        except (OSError, json.JSONDecodeError) as e:
            raise WeblateConfigError(self.file_path, "save") from e


def get_weblate_lang(lang_code: str) -> str:
    """Convert Odoo lang codes to Weblate ones."""
    match lang_code:
        case "ku":
            return "ckb"
        case "nb":
            return "nb_NO"
        case "sr@latin":
            return "sr_Latn"
        case "zh_CN":
            return "zh_Hans"
        case "zh_TW":
            return "zh_Hant"
        case _:
            return lang_code
