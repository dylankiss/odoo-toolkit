import re
from enum import Enum
from fnmatch import fnmatch
from pathlib import Path
from typing import Annotated

from git import InvalidGitRepositoryError, Repo
from rich.table import Table
from typer import Exit, Option, Typer, confirm

from odoo_toolkit.common import (
    EMPTY_LIST,
    TransientProgress,
    normalize_list_option,
    print,
    print_command_title,
    print_error,
)
from odoo_toolkit.wl.common import (
    WEBLATE_COMPONENT_ENDPOINT,
    WEBLATE_PROJECT_COMPONENTS_ENDPOINT,
    WEBLATE_PROJECTS_ENDPOINT,
    WeblateApi,
    WeblateComponentData,
    WeblateConfig,
    WeblateConfigError,
    WeblateProjectData,
)

WEBLATE_COMPONENT_COMMON_CONFIG: WeblateComponentData = {
    # We want to force-push the dev branch to keep it in sync with the main branch.
    "vcs": "git-force-push",
    "file_format": "po",
    "file_format_params": {
        "po_fuzzy_matching": False,
        "po_keep_previous": False,
        "po_line_wrap": 77,
        "po_no_location": False,
    },
    # Only admins should be able to add new languages.
    "new_lang": "contact",
    # Use the locale codes as used in Odoo for new languages.
    "language_code_style": "linux",
    "merge_style": "rebase",
    # We disable translation propagation, since it is contextless.
    "allow_translation_propagation": False,
    "enable_suggestions": True,
    "suggestion_voting": False,
    "suggestion_autoaccept": 0,
    # We want to push manually in this sync.
    "push_on_commit": False,
    # The maximum value, since we only commit when strictly needed.
    "commit_pending_age": 2160,
    "auto_lock_error": True,
    "license": "BSD-2-Clause",
}


class ComponentConfigStatus(Enum):
    """Status of trying to update or create a component."""

    CREATED = 1
    UPDATED = 2
    NONE = 3


app = Typer()


@app.command()
def update_components(
    project: Annotated[str, Option("--project", "-p", help="The Weblate project to update components in.")],
    languages: Annotated[
        list[str],
        Option(
            "--language",
            "-l",
            help="The default languages to apply to components if not specified in the `.weblate.json` configuration.",
        ),
    ],
    components: Annotated[
        list[str],
        Option(
            "--component",
            "-c",
            help="The Weblate components to update. You can use glob patterns. Updates all components if none are specified.",
        ),
    ] = EMPTY_LIST,
    keys: Annotated[
        list[str],
        Option(
            "--key",
            "-k",
            help="The specific keys to update in the component configuration. Updates all keys if none are specified.",
        ),
    ] = EMPTY_LIST,
    git_url: Annotated[
        str | None, Option("--git-url", help="Override the Git repo URL to use on the components."),
    ] = None,
    git_push_url: Annotated[
        str | None, Option("--git-push-url", help="Override the Git repo push URL to use on the components."),
    ] = None,
    git_branch: Annotated[
        str | None, Option("--git-branch", help="Override the Git branch to use on the components."),
    ] = None,
    git_push_branch: Annotated[
        str | None, Option("--git-push-branch", help="Override the Git push branch to use on the components."),
    ] = None,
) -> None:
    """Update Weblate components based on the `.weblate.json` configuration in the current folder."""
    print_command_title(":jigsaw: Odoo Weblate Update Components")

    languages = sorted(set(normalize_list_option(languages)))
    components = normalize_list_option(components)
    keys = sorted(set(normalize_list_option(keys)))

    try:
        weblate_config = WeblateConfig(Path(".weblate.json"))
    except WeblateConfigError as e:
        print_error(str(e))
        raise Exit from e

    component_configs = weblate_config.get_components(project)
    if not component_configs:
        print_error(f"No components found in project '{project}' in the `.weblate.json` configuration.")
        raise Exit
    if components:
        component_configs = [c for c in component_configs if any(fnmatch(c.get("name", ""), p) for p in components)]
        if not component_configs:
            print_error("No components match the given patterns.")
            raise Exit

    if not (git_url and git_push_url and git_branch and git_push_branch):
        try:
            git_repo = Repo(Path())
        except InvalidGitRepositoryError as e:
            print_error("The current folder is not a valid Git repository.")
            raise Exit from e
        else:
            if not git_url:
                git_url = git_repo.remote().url
            if not git_push_url:
                git_push_url = git_repo.remote("dev").url if "dev" in git_repo.remotes else git_url
            if not git_branch:
                git_branch = git_repo.active_branch.name
            if not git_push_branch:
                git_push_branch = f"{git_branch}-i18n-staging-c3podoo"

    if not git_url.startswith("git@github.com:odoo/"):
        print_error(f"The current Git repository '{git_url}' is not an Odoo repository.")
        raise Exit

    if not re.fullmatch(r"(saas-)?\d{1,2}\.\d", git_branch) and git_branch not in ("master", "odoo-com-translate"):
        print_error(f"The current Git branch '{git_branch}' is not a valid Odoo branch for Weblate.")
        raise Exit

    try:
        weblate_api = WeblateApi()
    except NameError as e:
        print_error(str(e))
        raise Exit from e

    weblate_projects = {p["slug"] for p in weblate_api.get_generator(WeblateProjectData, WEBLATE_PROJECTS_ENDPOINT)}
    if project not in weblate_projects:
        print_error(f"The Weblate project '{project}' does not exist on the server.")
        raise Exit

    WEBLATE_COMPONENT_COMMON_CONFIG.update({
        "repo": git_url,
        "push": git_push_url,
        "branch": git_branch,
        "push_branch": git_push_branch,
        "language_regex": f"^({'|'.join(re.escape(lang) for lang in languages)})$",
    })

    print("Using the following common component values:\n")
    config_table = Table(box=None, pad_edge=False, show_header=False)
    config_table.add_column(justify="right")
    config_table.add_column()
    for key, value in WEBLATE_COMPONENT_COMMON_CONFIG.items():
        if keys and key not in keys:
            continue
        config_table.add_row(f"[b]{key}[/b]", str(value))
    print(config_table, "")
    confirm("Do you want to continue?", abort=True)
    print()

    # Create and/or update components in the Weblate project.
    existing_components = {
        c.get("slug", str(c)): c
        for c in weblate_api.get_generator(
            WeblateComponentData,
            WEBLATE_PROJECT_COMPONENTS_ENDPOINT.format(project=project),
        )
    }

    components_created: list[str] = []
    components_updated: list[str] = []
    master_component = _find_master_component(weblate_api, project, git_url, git_branch)
    with TransientProgress() as progress:
        progress_task = progress.add_task("Updating components...", total=len(component_configs))
        for component_config in component_configs:
            progress.update(progress_task, description=f"Processing [b]{component_config.get('name')}[/b]...")
            component = component_config.get("name")
            if not component:
                print_error("Component config is missing a 'name' key.")
                progress.advance(progress_task)
                continue

            full_component_config: WeblateComponentData = {
                **WEBLATE_COMPONENT_COMMON_CONFIG,
                **component_config,
                "slug": component,
            }
            if not master_component:
                # If there's no master component, we let it be the first one we synchronize.
                master_component = {"project": project, "component": component}
            elif master_component["component"] != component:
                # All other components should link to the master component using an internal URL.
                # https://docs.weblate.org/en/latest/vcs.html#internal-urls
                full_component_config.update({
                    "repo": f"weblate://{master_component['project']}/{master_component['component']}",
                    "push": "",
                    "branch": "",
                    "push_branch": "",
                })

            # Specify the language code style depending on the file format.
            if full_component_config.get("file_format") == "aresource":
                full_component_config["language_code_style"] = "android"
            # Create or update the component config on Weblate.
            status = _create_or_update_component(
                weblate_api, project, full_component_config, existing_components, keys or None,
            )
            if status == ComponentConfigStatus.CREATED:
                components_created.append(component)
            elif status == ComponentConfigStatus.UPDATED:
                components_updated.append(component)
            progress.advance(progress_task)

    if components_created:
        print(f"{len(components_created)} component(s) created: {', '.join(components_created)}")
    if components_updated:
        print(f"{len(components_updated)} component(s) updated: {', '.join(components_updated)}")
    if not components_created and not components_updated:
        print("No components created or updated.")


def _find_master_component(
    weblate_api: WeblateApi, project: str, git_url: str, git_branch: str,
) -> dict[str, str] | None:
    """Find the component linked to our current repository and branch.

    In Weblate, there is ideally one component that clones the Git repo for a specific branch on the Weblate server.
    All other components would then link to this one component to also find their files in that local repo.
    If not, the Git repo would be cloned repeatedly for every component.
    More info: https://docs.weblate.org/en/latest/vcs.html#internal-urls

    This method looks if there is already a component that is referring to the right Git repo and branch in the
    current project.
    """
    for component in weblate_api.get_generator(
        WeblateComponentData,
        WEBLATE_PROJECT_COMPONENTS_ENDPOINT.format(project=project),
    ):
        if component.get("repo") == git_url and component.get("branch") == git_branch:
            # Weblate returns the linked component's repo and branch, so we need to check for it.
            if linked_component_url := component.get("linked_component"):
                master_component = weblate_api.get(WeblateComponentData, linked_component_url)
                return {
                    "project": master_component.get("project", {}).get("slug", ""),
                    "component": master_component.get("slug", ""),
                }

            return {"project": project, "component": component.get("slug", "")}

    return None


def _create_or_update_component(
    weblate_api: WeblateApi,
    project: str,
    component_config: WeblateComponentData,
    existing_components: dict[str, WeblateComponentData],
    keys: list[str] | None = None,
) -> ComponentConfigStatus:
    """Create a new component or update an existing one in Weblate."""
    component = component_config.get("slug", "")

    if component in existing_components:
        # We need to handle linked components in a special way.
        # When creating them, we need to provide the repo as `weblate://project/component`.
        # When we request the config from Weblate, it gives us the repo config of the component it is linked to.
        # That linked component's URL is given via the `linked_component` key.
        linked_component = False
        match = re.fullmatch(r"weblate://(?P<project>[\w-]+)/(?P<component>[\w-]+)", component_config.get("repo", ""))
        if match and (existing_components[component].get("linked_component") or "").endswith(
            WEBLATE_COMPONENT_ENDPOINT.format(project=match["project"], component=match["component"]),
        ):
            linked_component = True

        update_data: WeblateComponentData = {}
        for c_key, c_value in component_config.items():
            if keys and c_key not in keys:
                # Only update the specified keys.
                continue
            if linked_component and c_key in ("repo", "push", "branch", "push_branch"):
                # We checked the linked component config before.
                continue
            if existing_components[component].get(c_key) != c_value:
                # Prepare the data to update only the changed keys.
                update_data[c_key] = c_value
        if update_data:
            # Update the component config when it doesn't match what we have locally.
            weblate_api.patch(
                WeblateComponentData,
                WEBLATE_COMPONENT_ENDPOINT.format(project=project, component=component),
                json=update_data,
            )
            return ComponentConfigStatus.UPDATED
        return ComponentConfigStatus.NONE

    if not keys:
        weblate_api.post(
            WeblateComponentData,
            WEBLATE_PROJECT_COMPONENTS_ENDPOINT.format(project=project),
            json=component_config,
        )
        return ComponentConfigStatus.CREATED
    return ComponentConfigStatus.NONE
