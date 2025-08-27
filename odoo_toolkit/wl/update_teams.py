from collections.abc import Iterator
from contextlib import contextmanager
from fnmatch import fnmatch
from typing import Annotated, Literal, TypedDict, cast

from typer import Argument, Exit, Option, Typer

from odoo_toolkit.common import (
    EMPTY_LIST,
    normalize_list_option,
    print_command_title,
    print_error,
    print_success,
    print_warning,
)

from .common import (
    WEBLATE_GROUP_ENDPOINT,
    WEBLATE_GROUP_LANGUAGE_ENDPOINT,
    WEBLATE_GROUP_LANGUAGES_ENDPOINT,
    WEBLATE_GROUP_PROJECT_ENDPOINT,
    WEBLATE_GROUP_PROJECTS_ENDPOINT,
    WEBLATE_GROUP_ROLE_ENDPOINT,
    WEBLATE_GROUP_ROLES_ENDPOINT,
    WEBLATE_GROUPS_ENDPOINT,
    WEBLATE_PROJECTS_ENDPOINT,
    WEBLATE_ROLES_ENDPOINT,
    WeblateApi,
    WeblateApiError,
    WeblateGroupResponse,
    WeblateProjectResponse,
    WeblateRoleResponse,
    get_weblate_lang,
)

app = Typer()
project_options = {
    "all": 1,
    "public": 3,
    "protected": 4,
}


class TeamUpdatesDict(TypedDict):
    """All info to update a Weblate team."""

    group_request: dict[str, int]
    languages_to_add: set[str]
    languages_to_remove: set[str]
    project_ids_to_add: set[int]
    project_ids_to_remove: set[int]
    role_ids_to_add: set[int]
    role_ids_to_remove: set[int]


@app.command()
def update_teams(
    teams: Annotated[
        list[str],
        Argument(help="Names of the teams to update. Use glob patterns to match multiple teams with one string."),
    ],
    languages: Annotated[
        list[str],
        Option(
            "--language",
            "-l",
            help="The language codes to add or remove from the teams, or `all`. "
            "To add a language, prefix the code with `+`. To remove a language, prefix the code with `-`.",
        ),
    ] = EMPTY_LIST,
    projects: Annotated[
        list[str],
        Option(
            "--project",
            "-p",
            help="The project slugs to add or remove from the teams, or `all`, `public`, or `protected`. "
            "Only existing Weblate projects can be used. "
            "To add a project, prefix it with `+`. To remove a project, prefix it with `-`.",
        ),
    ] = EMPTY_LIST,
    roles: Annotated[
        list[str],
        Option(
            "--role",
            "-r",
            help="The roles to add or remove from the teams. The names need to match exactly with Weblate. "
            "To add a role, prefix it with `+`. To remove a role, prefix it with `-`.",
        ),
    ] = EMPTY_LIST,
) -> None:
    """Update Weblate teams permissions.

    This command will update the languages, projects, and/or roles associated with one or more teams in Weblate.
    """
    print_command_title("Odoo Weblate Teams: Update")

    # Support comma-separated values as well.
    languages = normalize_list_option(languages)
    projects = normalize_list_option(projects)
    roles = normalize_list_option(roles)

    try:
        weblate_api = WeblateApi()
    except NameError as e:
        print_error(str(e))
        raise Exit from e

    # Prepare main group update and filter out special options.
    update_group_request, languages, projects = _get_group_update_request(languages, projects)

    # Parse actions (add/remove) for each category.
    languages_to_add, languages_to_remove = _parse_add_remove(languages)
    projects_to_add, projects_to_remove = _parse_add_remove(projects)
    roles_to_add, roles_to_remove = _parse_add_remove(roles)

    try:
        # Resolve Weblate IDs.
        project_ids_to_add, project_ids_to_remove = _resolve_weblate_ids(
            weblate_api, "project", projects_to_add, projects_to_remove,
        )
        role_ids_to_add, role_ids_to_remove = _resolve_weblate_ids(
            weblate_api, "role", roles_to_add, roles_to_remove,
        )

        # Find all teams matching the provided patterns.
        team_objects = _get_matching_teams(weblate_api, teams)
    except WeblateApiError as e:
        print_error("Weblate API Error", str(e))
        raise Exit from e

    # Prepare a dictionary with all required updates.
    updates: TeamUpdatesDict = {
        "group_request": update_group_request,
        "languages_to_add": {get_weblate_lang(lang) for lang in languages_to_add},
        "languages_to_remove": {get_weblate_lang(lang) for lang in languages_to_remove},
        "project_ids_to_add": project_ids_to_add,
        "project_ids_to_remove": project_ids_to_remove,
        "role_ids_to_add": role_ids_to_add,
        "role_ids_to_remove": role_ids_to_remove,
    }

    if not team_objects:
        print_warning("No teams found matching the given patterns.")
        return

    # Iterate over each found team and apply the updates.
    for team in team_objects:
        _update_single_team(weblate_api, team, updates)


def _parse_add_remove(items: list[str]) -> tuple[set[str], set[str]]:
    """Parse items prefixed with '+' or '-' into sets for adding and removing."""
    to_add = {item[1:] for item in items if item.startswith("+")}
    to_remove = {item[1:] for item in items if item.startswith("-")}
    return to_add, to_remove


def _get_group_update_request(languages: list[str], projects: list[str]) -> tuple[dict[str, int], list[str], list[str]]:
    """Determine the group update request from special language/project options."""
    update_request: dict[str, int] = {}

    if "all" in languages:
        update_request["language_selection"] = 1
        # "all" overrides specific languages
        languages = []
    elif languages:
        update_request["language_selection"] = 0

    project_option = next((opt for opt in projects if opt in project_options), None)
    if project_option:
        update_request["project_selection"] = project_options[project_option]
        # A project option overrides specific projects
        projects = []
    elif projects:
        update_request["project_selection"] = 0

    return update_request, languages, projects


def _resolve_weblate_ids(
    weblate_api: WeblateApi,
    model: Literal["project", "role"],
    items_to_add: set[str],
    items_to_remove: set[str],
) -> tuple[set[int], set[int]]:
    """Fetch Weblate resources and resolve their names/slugs to IDs."""
    ids_to_add = set[int]()
    ids_to_remove = set[int]()
    unmatched_add = items_to_add.copy()
    unmatched_remove = items_to_remove.copy()

    if model == "project":
        endpoint = WEBLATE_PROJECTS_ENDPOINT
        response_type = WeblateProjectResponse
        compare_key = "slug"
    elif model == "role":
        endpoint = WEBLATE_ROLES_ENDPOINT
        response_type = WeblateRoleResponse
        compare_key = "name"

    for item in weblate_api.get_generator(response_type, endpoint):
        if not unmatched_add and not unmatched_remove:
            break

        name = cast("str", item[compare_key]) # pyright: ignore[reportGeneralTypeIssues]
        if name in unmatched_add:
            ids_to_add.add(item["id"])
            unmatched_add.remove(name)
        elif name in unmatched_remove:
            ids_to_remove.add(item["id"])
            unmatched_remove.remove(name)

    if unmatched_add:
        print_warning(f"Could not find the following {compare_key}s to add: {', '.join(unmatched_add)}")
    if unmatched_remove:
        print_warning(f"Could not find the following {compare_key}s to remove: {', '.join(unmatched_remove)}")

    return ids_to_add, ids_to_remove


def _get_matching_teams(weblate_api: WeblateApi, team_patterns: list[str]) -> list[WeblateGroupResponse]:
    """Fetch all teams from Weblate and filter them based on glob patterns."""
    return [
        team_object
        for team_object in weblate_api.get_generator(WeblateGroupResponse, WEBLATE_GROUPS_ENDPOINT)
        if any(fnmatch(team_object.get("name", ""), pattern) for pattern in team_patterns)
    ]


def _update_team_languages(api: WeblateApi, team_id: int, to_add: set[str], to_remove: set[str]) -> bool:
    """Update languages for a specific team."""
    updated = False
    for lang in to_remove:
        with _weblate_error_handler():
            api.delete(str, WEBLATE_GROUP_LANGUAGE_ENDPOINT.format(group=team_id, language=lang))
            updated = True
    for lang in to_add:
        with _weblate_error_handler():
            api.post(str, WEBLATE_GROUP_LANGUAGES_ENDPOINT.format(group=team_id), json={"language_code": lang})
            updated = True
    return updated


def _update_team_projects(api: WeblateApi, team_id: int, to_add: set[int], to_remove: set[int]) -> bool:
    """Update projects for a specific team."""
    updated = False
    for project_id in to_remove:
        with _weblate_error_handler():
            api.delete(str, WEBLATE_GROUP_PROJECT_ENDPOINT.format(group=team_id, project=project_id))
            updated = True
    for project_id in to_add:
        with _weblate_error_handler():
            api.post(str, WEBLATE_GROUP_PROJECTS_ENDPOINT.format(group=team_id), json={"project_id": project_id})
            updated = True
    return updated


def _update_team_roles(api: WeblateApi, team_id: int, to_add: set[int], to_remove: set[int]) -> bool:
    """Update roles for a specific team."""
    updated = False
    for role_id in to_remove:
        with _weblate_error_handler():
            api.delete(str, WEBLATE_GROUP_ROLE_ENDPOINT.format(group=team_id, role=role_id))
            updated = True
    for role_id in to_add:
        with _weblate_error_handler():
            api.post(str, WEBLATE_GROUP_ROLES_ENDPOINT.format(group=team_id), json={"role_id": role_id})
            updated = True
    return updated


def _update_single_team(weblate_api: WeblateApi, team: WeblateGroupResponse, updates: TeamUpdatesDict) -> None:
    """Apply all updates to a single Weblate team and print the result."""
    team_id = team["id"]
    updated = False

    if updates["group_request"]:
        with _weblate_error_handler():
            weblate_api.patch(str, WEBLATE_GROUP_ENDPOINT.format(group=team_id), json=updates["group_request"])
            updated = True

    if _update_team_languages(
        weblate_api, team_id, updates["languages_to_add"], updates["languages_to_remove"],
    ):
        updated = True

    if _update_team_projects(
        weblate_api, team_id, updates["project_ids_to_add"], updates["project_ids_to_remove"],
    ):
        updated = True

    if _update_team_roles(
        weblate_api, team_id, updates["role_ids_to_add"], updates["role_ids_to_remove"],
    ):
        updated = True

    if updated:
        print_success(f'Team "{team["name"]}" updated.')
    else:
        print_warning(f'Team "{team["name"]}" not updated.')


@contextmanager
def _weblate_error_handler() -> Iterator[None]:
    """Handle WeblateApiError within a 'with' block."""
    try:
        yield
    except WeblateApiError as e:
        print_error("Weblate API Error", str(e))
