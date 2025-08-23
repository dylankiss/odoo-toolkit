from collections.abc import Generator
from os import environ
from typing import Any
from urllib.parse import urljoin

from requests import HTTPError, JSONDecodeError, Response, Session

WEBLATE_URL = environ.get("WEBLATE_URL", "https://translate.odoo.com")
WEBLATE_API_TOKEN = environ.get("WEBLATE_API_TOKEN")

WEBLATE_PROJECT_COMPONENTS_ENDPOINT = "/api/projects/{project}/components/"
WEBLATE_AUTOTRANSLATE_ENDPOINT = "/api/translations/{project}/{component}/{language}/autotranslate/"

WEBLATE_ERR_1 = "Please configure WEBLATE_API_TOKEN in your current environment."

WeblateJson = dict[str, "str | int | float | bool | tuple[str] | WeblateJson"]

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

        message = f"HTTP {self.status_code} ({self.error_type}): The request failed with {len(self.errors)} error(s)."
        super().__init__(message)

    def __str__(self) -> str:
        """Provide a clean string representation, listing all errors."""
        error_list = [
            f"Request URL: {self.response.request.url}",
            f"Request Body: {self.response.request.body}",
            f"Status Code: {self.status_code} ({self.error_type})",
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

    def _request(self, method: str, endpoint: str, *, json: WeblateJson | None = None) -> WeblateJson:
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
            return response.json() if response.content else {}
        except (HTTPError, JSONDecodeError) as e:
            raise WeblateApiError(response) from e

    def get_generator(self, endpoint: str) -> Generator[WeblateJson]:
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

    def get(self, endpoint: str, *, json: WeblateJson | None = None) -> WeblateJson:
        """Perform a GET request against a Weblate API endpoint.

        :param endpoint: The API endpoint to access.
        :param json: The JSON payload to send, defaults to None.
        :raises WeblateApiError: If the request returns an error.
        """
        return self._request("GET", endpoint, json=json)

    def post(self, endpoint: str, *, json: WeblateJson | None = None) -> WeblateJson:
        """Perform a POST request against a Weblate API endpoint.

        :param endpoint: The API endpoint to access.
        :param json: The JSON payload to send, defaults to None.
        :raises WeblateApiError: If the request returns an error.
        """
        return self._request("POST", endpoint, json=json)

    def put(self, endpoint: str, *, json: WeblateJson | None = None) -> WeblateJson:
        """Perform a PUT request against a Weblate API endpoint.

        :param endpoint: The API endpoint to access.
        :param json: The JSON payload to send, defaults to None.
        :raises WeblateApiError: If the request returns an error.
        """
        return self._request("PUT", endpoint, json=json)
