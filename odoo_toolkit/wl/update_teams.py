from collections.abc import Callable
from fnmatch import fnmatch
from typing import Annotated, Any, cast

from typer import Argument, Exit, Option, Typer

from odoo_toolkit.common import (
    EMPTY_LIST,
    normalize_list_option,
    print_command_title,
    print_error,
    print_success,
    print_warning,
)
from odoo_toolkit.po.common import get_cldr_lang

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
    get_weblate_api,
)

app = Typer()

# The values of a team's `project_selection` and `language_selection`: `AS_DEFINED` means the ones in its lists.
AS_DEFINED = 0
PROJECT_SELECTIONS = {"all": 1, "public": 3, "protected": 4}
LANGUAGE_SELECTIONS = {"all": 1}


# Per list of a team, like `languages`: the items to add, and the ones to remove.
ListChanges = dict[str, tuple[set[str] | set[int], set[str] | set[int]]]


# Per list of a team: its API endpoints to add to and remove from, the field to add with, and the selection that
# must be "as defined" for the list to apply.
LISTS: dict[str, tuple[str, str, str, str | None]] = {
    "languages": (
        WEBLATE_GROUP_LANGUAGES_ENDPOINT,
        WEBLATE_GROUP_LANGUAGE_ENDPOINT,
        "language_code",
        "language_selection",
    ),
    "projects": (WEBLATE_GROUP_PROJECTS_ENDPOINT, WEBLATE_GROUP_PROJECT_ENDPOINT, "project_id", "project_selection"),
    "roles": (WEBLATE_GROUP_ROLES_ENDPOINT, WEBLATE_GROUP_ROLE_ENDPOINT, "role_id", None),
}


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

    This command will update the languages, projects, and/or roles associated with one or more teams in Weblate.\n
    \n
    Adding or removing specific languages or projects only applies to teams that use their own list of them. A team
    with access to all languages or projects is left as is, and reported. Teams defined by a project are skipped.
    """
    print_command_title(":closed_lock_with_key: Odoo Weblate Teams: Update")

    languages = normalize_list_option(languages)
    projects = normalize_list_option(projects)
    roles = normalize_list_option(roles)

    # Values without a sign would otherwise be ignored without a word.
    if invalid := [
        *(f"-l {item}" for item in languages if not _is_change(item) and item not in LANGUAGE_SELECTIONS),
        *(f"-p {item}" for item in projects if not _is_change(item) and item not in PROJECT_SELECTIONS),
        *(f"-r {item}" for item in roles if not _is_change(item)),
    ]:
        print_error(
            f"Prefix the values with [b]+[/b] to add them or [b]-[/b] to remove them: [b]{'[/b], [b]'.join(invalid)}[/b].\n",
        )
        raise Exit(1)

    weblate_api = get_weblate_api()

    selections: dict[str, int] = {}
    if "all" in languages:
        selections["language_selection"] = LANGUAGE_SELECTIONS["all"]
    if project_option := next((option for option in projects if option in PROJECT_SELECTIONS), None):
        selections["project_selection"] = PROJECT_SELECTIONS[project_option]

    try:
        changes: ListChanges = {
            "languages": _parse_changes(languages, get_cldr_lang),
            "projects": _resolve_ids(weblate_api, WEBLATE_PROJECTS_ENDPOINT, "slug", _parse_changes(projects)),
            "roles": _resolve_ids(weblate_api, WEBLATE_ROLES_ENDPOINT, "name", _parse_changes(roles)),
        }
        matching_teams = [
            team
            for team in weblate_api.get_generator(WeblateGroupResponse, WEBLATE_GROUPS_ENDPOINT)
            if any(fnmatch(team.get("name", ""), pattern) for pattern in teams)
        ]
    except WeblateApiError as e:
        print_error(f"Fetching the teams, projects or roles failed: {e.summary}", str(e))
        raise Exit(1) from e

    if not matching_teams:
        print_warning("No teams found matching the given patterns.")
        raise Exit(1)

    failed = False
    for team in matching_teams:
        failed = not _update_team(weblate_api, team, selections, changes) or failed
    if failed:
        raise Exit(1)


def _is_change(item: str) -> bool:
    """Check whether a value adds (`+`) or removes (`-`) something."""
    return item[:1] in {"+", "-"} and len(item) > 1


def _parse_changes(items: list[str], convert: Callable[[str], str] = str) -> tuple[set[str], set[str]]:
    """Parse the items prefixed with `+` or `-` into the ones to add and the ones to remove."""
    to_add = {convert(item[1:]) for item in items if item.startswith("+")}
    to_remove = {convert(item[1:]) for item in items if item.startswith("-")}
    return to_add, to_remove


def _resolve_ids(
    api: WeblateApi,
    endpoint: str,
    key: str,
    changes: tuple[set[str], set[str]],
) -> tuple[set[int], set[int]]:
    """Get the IDs of the projects or roles to add and remove, by their slug or name.

    :raises Exit: If one of them doesn't exist, before anything changes.
    """
    to_add, to_remove = changes
    if not to_add and not to_remove:
        return set(), set()
    ids: dict[str, int] = {
        cast("str", item[key]): cast("int", item["id"]) for item in api.get_generator(dict[str, Any], endpoint)
    }
    if unknown := sorted((to_add | to_remove) - set(ids)):
        print_error(f"These don't exist on Weblate: [b]{'[/b], [b]'.join(unknown)}[/b]. Nothing was changed.")
        raise Exit(1)
    return {ids[name] for name in to_add}, {ids[name] for name in to_remove}


def _update_team(api: WeblateApi, team: WeblateGroupResponse, selections: dict[str, int], changes: ListChanges) -> bool:
    """Apply the changes to a team, and report the result.

    :return: Whether every change succeeded.
    """
    name = team["name"]
    if team.get("defining_project"):
        print_warning(f"Team [b]{name}[/b] is defined by a project, so it's skipped.")
        return True
    team_id = team["id"]
    done: list[str] = []
    errors: list[str] = []
    current = cast("dict[str, Any]", team)
    if selections:
        _apply(api, "selection", "PATCH", WEBLATE_GROUP_ENDPOINT.format(group=team_id), selections, done, errors)
        if not errors:
            # Only what Weblate accepted decides which lists are left as they are.
            current |= selections

    for list_name, (add_endpoint, remove_endpoint, field, selection) in LISTS.items():
        to_add, to_remove = changes[list_name]
        if not to_add and not to_remove:
            continue
        if selection and current.get(selection, AS_DEFINED) != AS_DEFINED:
            print_warning(f"Team [b]{name}[/b] has access to all {list_name}, so they're left as is.")
            continue
        requests = [
            *(
                (f"removing {item}", "DELETE", remove_endpoint.format(group=team_id, **{list_name[:-1]: item}), None)
                for item in to_remove
            ),
            *((f"adding {item}", "POST", add_endpoint.format(group=team_id), {field: item}) for item in to_add),
        ]
        for description, method, endpoint, payload in requests:
            _apply(api, description, method, endpoint, payload, done, errors)

    if errors:
        print_error(f"Team [b]{name}[/b]: " + "; ".join(errors))
    elif done:
        print_success(f"Team [b]{name}[/b] updated.")
    else:
        print_warning(f"Team [b]{name}[/b] not updated.")
    return not errors


def _apply(
    api: WeblateApi,
    description: str,
    method: str,
    endpoint: str,
    payload: dict[str, Any] | None,
    done: list[str],
    errors: list[str],
) -> None:
    """Send one change of a team, adding its description to the done ones or its error to the errors."""
    try:
        api.request(dict[str, Any], method, endpoint, json=payload)
        done.append(description)
    except WeblateApiError as e:
        errors.append(f"{description}: {e.summary}")
