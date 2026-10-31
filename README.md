# 🧰 Odoo Toolkit

This toolkit contains a few useful tools for Odoo development. The tools are primarily aimed at Odoo employees.

The quickest way to get started is to install the tools using either [pipx](https://pipx.pypa.io/stable/), [uv](https://docs.astral.sh/uv/) or any other package manager to install them in an isolated environment.

You can install the package from [PyPI](https://pypi.org/project/odoo-toolkit/):
```console
$ pipx install odoo-toolkit
```
```console
$ uv tool install odoo-toolkit
```
or from the [GitHub repository](https://github.com/dylankiss/odoo-toolkit) directly:
```console
$ pipx install --force git+https://github.com/dylankiss/odoo-toolkit.git
```
```console
$ uv tool install --force git+https://github.com/dylankiss/odoo-toolkit.git
```

> [!TIP]
> You can run any command with the `--help` option to find out all options you can use.

> [!NOTE]
> At most once a day, in the background, the toolkit checks [PyPI](https://pypi.org/project/odoo-toolkit/) for a new version and tells you after the command has run. It doesn't check in scripts, in CI, or when the toolkit isn't installed from PyPI, like a linked development version. Set `OTK_NO_UPDATE_CHECK` to any value to turn it off.

> [!TIP]
> Anywhere you can provide modules as arguments or options, you can also use [glob patterns](https://docs.python.org/3/library/fnmatch.html) to match multiple similar ones.

**Usage**:

```console
$ otk [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--install-completion`: Install completion for the current shell.
* `--show-completion`: Show completion for the current shell, to copy it or customize the installation.
* `--version`: Show the version and exit.
* `--help`: Show this message and exit.


## Table of Contents

### [Odoo Translation Files (`otk po`)](#odoo-translation-files-otk-po-1)

| Command                           | Purpose                                                                             |
| --------------------------------- | ----------------------------------------------------------------------------------- |
| [`otk po export`](#otk-po-export) | Export Odoo translation files (.pot) to each module's i18n folder.                  |
| [`otk po create`](#otk-po-create) | Create Odoo translation files (.po) according to their .pot files.                  |
| [`otk po update`](#otk-po-update) | Update Odoo translation files (.po) according to a new version of their .pot files. |

### [Odoo Development Server (`otk dev`)](#odoo-development-server-otk-dev-1)

| Command                                 | Purpose                                                                              |
| --------------------------------------- | ------------------------------------------------------------------------------------ |
| [`otk dev run`](#otk-dev-run)     | Run Odoo for the current Odoo Multiverse version directory, in Docker.              |
| [`otk dev shell`](#otk-dev-shell) | Open a shell in the Odoo container of the current Odoo Multiverse version directory. |
| [`otk dev stop`](#otk-dev-stop)   | Stop the Odoo containers started with `otk dev run`.                                 |
| [`otk dev db`](#otk-dev-db)       | Start PostgreSQL and Mailpit, the services Odoo uses, without starting Odoo.        |

### [Odoo Weblate (`otk wl`)](#odoo-weblate-otk-wl-1)

| Command                                                 | Purpose                                                                                     |
| ------------------------------------------------------- | ------------------------------------------------------------------------------------------- |
| [`otk wl authors`](#otk-wl-authors)                     | List the translators you can upload translations for.                                       |
| [`otk wl config`](#otk-wl-config)                       | Update modules in the Weblate config file.                                                  |
| [`otk wl config-odoo`](#otk-wl-config-odoo)             | Generate the standard Odoo Weblate config profile for a version.                            |
| [`otk wl copy`](#otk-wl-copy)                           | Copy translations between languages, components, and/or projects.                           |
| [`otk wl download`](#otk-wl-download)                   | Download specific PO files from Weblate.                                                    |
| [`otk wl update-teams`](#otk-wl-update-teams)           | Update Weblate teams permissions.                                                           |
| [`otk wl upload`](#otk-wl-upload)                       | Upload specific PO files to Weblate.                                                        |

### [Odoo Multiverse (`otk mv`)](#odoo-multiverse-otk-mv-1)

| Command                           | Purpose                                                                                        |
| --------------------------------- | ---------------------------------------------------------------------------------------------- |
| [`otk mv setup`](#otk-mv-setup)   | Set up an Odoo Multiverse environment, having different versions checked out at the same time. |
| [`otk mv reset`](#otk-mv-reset)   | Reset the repositories inside an Odoo Multiverse version directory.                            |
| [`otk mv switch`](#otk-mv-switch) | Switch branches inside an Odoo Multiverse version directory.                                   |
| [`otk mv link`](#otk-mv-link)     | Link a shared or pinned repository into the current Odoo Multiverse version directory.            |
| [`otk mv unlink`](#otk-mv-unlink) | Unlink a shared or pinned repository from the current Odoo Multiverse version directory.          |
| [`otk mv cleanup`](#otk-mv-cleanup) | Remove the branches the Odoo Multiverse repositories don't need, and reclaim their disk space. |


## Odoo Translation Files (`otk po`)

**Work with Odoo Translation Files (`.po` and `.pot` files).**

The following commands allow you to export `.pot` files for Odoo modules, and create or update `.po` files according to their (updated) `.pot` files.

Inside an [Odoo Multiverse](#odoo-multiverse-otk-mv) version directory (like `19.0`), they use its `odoo` and `enterprise` repositories, wherever you are in it.


## `otk po export`

**Export Odoo translation files (`.pot`) to each module's `i18n` folder.**

This command can autonomously start separate Odoo servers to export translatable terms for one or more modules. A separate server is started for Community, Enterprise, localizations, and custom modules, with only the modules to export installed, and all (indirect) dependent modules that might contribute terms to them.

Inside an [Odoo Multiverse](#odoo-multiverse-otk-mv) version directory, the servers run in that version's Docker image, like [`otk dev run`](#otk-dev-run), with the PostgreSQL of `otk dev`. Use `--local` to run them with Python on this machine instead.

You can also export terms from your own running server using the `--own-server` option and optionally passing the correct arguments to reach your Odoo server.

> [!NOTE]
> Outside a multiverse, the command is supposed to run from within the parent directory where your `odoo` and `enterprise` repositories are checked out with these names. Your database is supposed to run on `localhost` using port `5432`, accessible without a password using your current user.
>
> Of course, all of this can be tweaked with the available options. 😉

### Usage

```console
$ otk po export [OPTIONS] MODULES...
```
e.g.

```console
$ otk po export "account_*" mrp sale
$ otk po export --local --db-username odoo "account_*" mrp sale
```

### Arguments

* `MODULES...`: Export .pot files for these Odoo modules (supports glob patterns), or either `all`, `community`, `enterprise`, `community-l10n`, or `enterprise-l10n`.  **[required]**

### Options

* `-x, --exclude TEXT`: Exclude these modules from being installed and exported. `default` stands for localizations, themes, hardware and test modules.
* `-f, --path-filter PATH`: Only include modules within these paths.
* `--help`: Show this message and exit.

**Odoo Server Options**:

* `--start-server / --own-server`: Start an Odoo server automatically or connect to your own server.  [default: `start-server`]
* `--keep-db`: Keep the database after exporting the `.pot` files.
* `--full-install`: Install every available Odoo module in Community and Enterprise.
* `--quick-install`: Install only the modules to export.
* `--local`: Run the Odoo servers with Python on this machine, instead of in the Docker image of the Odoo Multiverse version directory you're in.
* `--build`: Build the Docker image on your machine instead of pulling it.
* `-c, --com-path PATH`: Specify the path to your Odoo Community repository.  [default: `odoo`]
* `-e, --ent-path PATH`: Specify the path to your Odoo Enterprise repository.  [default: `enterprise`]
* `-a, --addons-path PATH`: Specify extra addons paths if your modules are not in Community or Enterprise.
* `-u, --username TEXT`: Specify the username to log in to Odoo.  [default: `admin`]
* `--password TEXT`: Specify the password to log in to Odoo.  [default: `admin`]
* `--host TEXT`: Specify the hostname of your Odoo server.  [default: `localhost`]
* `--port INTEGER`: Specify the port of your Odoo server.  [default: `8069`, or the first free one after that when `--start-server`]

**Database Options**:

* `-d, --database TEXT`: Specify the PostgreSQL database name used by Odoo.  [default: `export_pot_db_{port}`]
* `--db-host TEXT`: Specify the PostgreSQL server's hostname. The database options don't apply to servers in Docker, which use the PostgreSQL of `otk dev`.  [default: `localhost`]
* `--db-port INTEGER`: Specify the PostgreSQL server's port.  [default: `5432`]
* `--db-username TEXT`: Specify the PostgreSQL server's username.
* `--db-password TEXT`: Specify the PostgreSQL user's password.

## `otk po create`

**Create Odoo translation files (`.po`) according to their `.pot` files.**

> [!NOTE]
> Uses the gettext `msginit` command if available. Either way, the plural rules are the ones Weblate uses.

This command will provide you with a clean `.po` file per language you specified for the given modules. It basically copies all entries from the `.pot` file in the module and completes the metadata with the right language information. All generated `.po` files will be saved in the respective modules' `i18n` directories.

> [!NOTE]
> Without any options specified, the command is supposed to run from within the parent directory where your `odoo` and `enterprise` repositories are checked out with these names.

### Usage

```console
$ otk po create [OPTIONS] MODULES...
```
e.g.

```console
$ otk po create -l nl -l fr -l de l10n_be l10n_be_reports
```

### Arguments

* `MODULES...`: Create .po files for these Odoo modules, or either `all`, `community`, `enterprise`, `community-l10n`, or `enterprise-l10n`.  **[required]**

### Options

* `-l, --language TEXT`: Create .po files for these language codes, or `all`.  **[required]**
* `-x, --exclude TEXT`: Exclude these modules from being created. `default` stands for localizations, themes, hardware and test modules.
* `-f, --path-filter PATH`: Only include modules within these paths.
* `-c, --com-path PATH`: Specify the path to your Odoo Community repository.  [default: `odoo`]
* `-e, --ent-path PATH`: Specify the path to your Odoo Enterprise repository.  [default: `enterprise`]
* `-a, --addons-path PATH`: Specify extra addons paths if your modules are not in Community or Enterprise.
* `--help`: Show this message and exit.


## `otk po update`

**Update Odoo translation files (`.po`) according to a new version of their `.pot` files.**

> [!NOTE]
> Uses the gettext `msgmerge` and `msgattrib` commands if available.

This command will update the `.po` files for the provided modules according to a new `.pot` file you might have exported in their `i18n` directory.

> [!NOTE]
> Without any options specified, the command is supposed to run from within the parent directory where your `odoo` and `enterprise` repositories are checked out with these names.

### Usage

```console
$ otk po update [OPTIONS] MODULES...
```
e.g.
```console
$ otk po update -l nl -l fr account account_accountant
```

### Arguments

* `MODULES...`: Update .po files for these Odoo modules, or either `all`, `community`, `enterprise`, `community-l10n`, or `enterprise-l10n`.  **[required]**

### Options

* `-l, --language TEXT`: Update .po files for these language codes, or `all`.  [default: `all`]
* `-t, --only-translated`: Only keep translated terms in the updated `.po` files, removing all untranslated terms.
* `-x, --exclude TEXT`: Exclude these modules from being updated. `default` stands for localizations, themes, hardware and test modules.
* `-f, --path-filter PATH`: Only include modules within these paths.
* `-c, --com-path PATH`: Specify the path to your Odoo Community repository.  [default: `odoo`]
* `-e, --ent-path PATH`: Specify the path to your Odoo Enterprise repository.  [default: `enterprise`]
* `-a, --addons-path PATH`: Specify extra addons paths if your modules are not in Community or Enterprise.
* `--help`: Show this message and exit.


## Odoo Development Server (`otk dev`)

**Run Odoo in Docker, in the same environment as Runbot.**

Run these commands inside an [Odoo Multiverse](#odoo-multiverse-otk-mv) version directory (like `19.0`). They require [Docker](https://docs.docker.com/get-started/get-docker/).

Odoo runs in a new container for every `otk dev run`, removed when Odoo stops. The containers use the images in [`dylankiss/odoo-dev`](https://hub.docker.com/r/dylankiss/odoo-dev), one per Odoo version (like `dylankiss/odoo-dev:19.0`), built for `amd64` and `arm64` (where Paper Muncher, which only exists for amd64, is left out) from [Runbot's Dockerfiles](https://runbot.odoo.com/runbot/versions), with Git, SSH and the debugger added. Next to them run shared containers for PostgreSQL 18 (`localhost:5432`, user `odoo`, no password needed) and [Mailpit](https://mailpit.axllent.org/) (`http://localhost:8025`), which catches all emails Odoo sends.

The multiverse directory is mounted at the same path as on your machine, so the links between repositories and the Git worktrees work in the container, the debugger needs no path mappings, and the paths in tracebacks are the ones of your machine. All ports are only reachable from your machine.

What stays between runs:
- your databases, in the `otk-postgres-data` volume,
- the filestores, in the `otk-filestore` volume.

Your SSH agent (the one in `SSH_AUTH_SOCK`, like Bitwarden's or 1Password's) is available in the container, for Odoo modules that run Git commands. Your keys never leave your machine.

With [OrbStack](https://orbstack.dev), which provides its own agent forwarding, an agent like Bitwarden's needs an `IdentityAgent` line in your `~/.ssh/config`. Add it below any `Include` lines, then restart OrbStack:

```
Host *
    IdentityAgent "~/Library/Containers/com.bitwarden.desktop/Data/.bitwarden-ssh-agent.sock"
```

Use the socket in your `SSH_AUTH_SOCK` for the path, which is different for other agents, like 1Password's.

On Linux, the container runs as your user, so the files Odoo writes are yours. The images on Docker Hub run as user ID 1000, the first user on most Linux machines. With another user ID, build the image on your machine once with `--build`.

> [!TIP]
> In Visual Studio Code, `F5` runs Odoo with the debugger and attaches to it, and stopping the debugger stops Odoo. The configuration comes with [`otk mv setup`](#otk-mv-setup).

## `otk dev run`

**Run Odoo for the current Odoo Multiverse version directory, in Docker.**

It starts PostgreSQL and Mailpit if needed, and runs Odoo with:
- the addons path containing every repository of the version directory with modules in it, like `enterprise`, `odoo/addons`, `design-themes`, and linked repositories like `odoofin`,
- the database and mail server set up,
- no time limits, so requests don't stop while you're paused in the debugger.

Use `--community` to only load Odoo Community, or `--exclude` to leave out repositories, like `-x enterprise`. Use `--volume` and `--env` to give the container files and settings of your machine, like the configuration of a linked repository. Pass any other Odoo options after `--`. Stop Odoo with `Ctrl+C`.

### Usage

```console
$ otk dev run [OPTIONS] [-- ODOO OPTIONS]
```
e.g.
```console
$ otk dev run -- -d mydb -i sale
$ otk dev run --port 8070 --debug -- -d mydb
$ otk dev run --community -- -d community-db
$ otk dev run -v ~/etc-odoo:/etc/odoo:ro -e KEY=value -- -d mydb
```

### Options

* `-p, --port INTEGER`: The port to reach Odoo on, on your machine.  [default: `8069`]
* `--debug`: Start Odoo under the debugger, waiting for Visual Studio Code to attach.
* `--debug-port INTEGER`: The port of the debugger, on your machine.  [default: `5678`]
* `--upgrade`: Add the upgrade scripts of `upgrade` and `upgrade-util` to the upgrade path.
* `--community`: Only load the modules of Odoo Community (`odoo/addons`).
* `-x, --exclude TEXT`: Leave these repositories out of the addons path, like `enterprise` or `odoofin,design-themes`.
* `-v, --volume TEXT`: Mount a file or folder of your machine in the container, like `~/etc-odoo:/etc/odoo:ro`. Can be used several times. A path starting with `~` or `.` is made absolute, and the path must exist.
* `-e, --env TEXT`: Set an environment variable in the container, like `REQUESTS_CA_BUNDLE=/etc/ca.pem`. Can be used several times.
* `--build`: Build the image on your machine instead of pulling it.
* `--pull`: Pull the latest image, even if you have one already.
* `--db-port INTEGER`: The port to reach PostgreSQL on, on your machine. Defaults to the one it runs on, or `5432`. Changing it restarts PostgreSQL.
* `--platform [amd64|arm64]`: Run the image of this architecture. On Apple Silicon, `amd64` runs emulated and slower, but has what only exists for amd64, like Paper Muncher. Defaults to the architecture of this machine.
* `--help`: Show this message and exit.

Run several versions at the same time by giving them different ports, like `otk dev run --port 8070` in another version directory.


## `otk dev shell`

**Open a shell in the Odoo container of the current Odoo Multiverse version directory.**

If Odoo is running for this version, the shell opens in its container. Otherwise it opens in a new container with the same environment, removed when you exit the shell.

### Usage

```console
$ otk dev shell [OPTIONS]
```

### Options

* `--build`: Build the image on your machine instead of pulling it.
* `--pull`: Pull the latest image, even if you have one already.
* `--platform [amd64|arm64]`: Use the image of this architecture. Defaults to the architecture of this machine.
* `--help`: Show this message and exit.


## `otk dev stop`

**Stop the Odoo containers started with `otk dev run`.**

Inside a multiverse version directory, this stops the Odoo containers of that version. Elsewhere, it stops all of them. Your shells and `otk po export` servers keep running. Your databases and filestores stay.

### Usage

```console
$ otk dev stop [OPTIONS]
```

### Options

* `-p, --port INTEGER`: Only stop the Odoo running on this port, on your machine.
* `--all`: Stop every container of this toolkit, including shells and `otk po export` servers, and PostgreSQL, Mailpit and pgAdmin as well.
* `--help`: Show this message and exit.


## `otk dev db`

**Start PostgreSQL and Mailpit, the services Odoo uses, without starting Odoo.**

`otk dev run` starts them by itself. Use this command to connect to the databases with your own tools, to run an Odoo directly on your machine (with `--db_host=localhost --db_user=odoo`), or to use pgAdmin. Note that an Odoo on your machine keeps its filestore in its own data directory, not in the containers' `otk-filestore` volume.

> [!TIP]
> To connect without any options, from an Odoo on your machine as well as from tools like `psql`, `pg_dump` and [`otk po export`](#otk-po-export), add this to your shell profile (like `~/.zshrc`):
> ```bash
> export PGHOST=localhost PGUSER=odoo
> ```
> Odoo leaves the connection settings you don't pass to PostgreSQL's client library, which reads these variables. If you also run another PostgreSQL on your machine, the tools then connect to this one by default.

> [!NOTE]
> Images are pulled once and then kept, so a rebuilt image goes unnoticed. At most once a day per image, `otk dev run`, `otk dev shell` and `otk dev db` compare your Odoo image and the images of these services with Docker Hub, and tell you when there is a newer build, with the command to get it (`--pull`). A dev container keeps using the image it was created from, so rebuild it after pulling. Images you built yourself are skipped, and nothing is checked in scripts, in CI, or with `OTK_NO_UPDATE_CHECK` set.

### Usage

```console
$ otk dev db [OPTIONS]
```

### Options

* `-p, --port INTEGER`: The port to reach PostgreSQL on, on your machine. Defaults to the one it runs on, or `5432`. Changing it restarts PostgreSQL.
* `--pgadmin`: Start pgAdmin as well, on `http://localhost:5050`.
* `--pull`: Pull newer builds of the images first. A service whose image changed is recreated, which keeps its data.
* `--help`: Show this message and exit.


## Odoo Weblate (`otk wl`)

**Work with Odoo translations on Weblate.**

The following commands allow you to perform operations related to Weblate.

In order to connect to the Weblate server, you need to have an API key available in the `WEBLATE_API_TOKEN` variable in
your environment. You can do this either by providing the variable in front of the command each time, like:
```console
WEBLATE_API_TOKEN=wlu_XXXXXX... otk wl ...
```
or make the variable available to your execution environment by putting it in your `.bashrc`, `.zshrc` or equivalent
configuration file for your shell.


## `otk wl authors`

**List the translators you can upload translations for, with `--author`.**

Configure them once in your odoo-toolkit configuration file (`~/.config/odoo-toolkit/config.toml`, or in `$XDG_CONFIG_HOME`), by a short name:

```toml
[weblate.authors]
alice = { name = "Alice Martin", email = "alice.martin@example.com" }
bob = { name = "Bob Jones", email = "bob.jones@example.com" }
```

Then `otk wl copy --author alice` or `otk wl upload --author alice` credits Alice Martin. With shell completion installed (`otk --install-completion`), `--author <Tab>` completes the short names.

Weblate finds the author's account by the email, and keeps that account's name. Only for a new email, it creates an account with the name you configured.

### Usage

```console
$ otk wl authors
```


## `otk wl config`

**Update modules in the Weblate config file.**

This command will add, update, or remove module entries in `.weblate.json` files. The `.weblate.json` files need to be located at the provided addons paths' roots. If not, a new file will be created.

A localization module gets the languages of its country, from the list that ships with the toolkit ([`l10n_languages.toml`](odoo_toolkit/wl/l10n_languages.toml)), or the languages of its `.po` files if it isn't listed. Without `-l`, you can add or change them for yourself in your odoo-toolkit configuration file (`~/.config/odoo-toolkit/config.toml`, or in `$XDG_CONFIG_HOME`). Your patterns are matched first, and the first match wins. An empty list makes the module follow the languages of the Weblate project.

```toml
[weblate.l10n_languages]
"*l10n_xx*" = ["fr", "nl"]
```

For `odoo` and `enterprise`, the project slug follows the format `odoo-18` for major versions and `odoo-s18-1` for SaaS versions. Other repos have their own project slugs. Check the Weblate URLs to find the right project slug.

### Usage

```console
$ otk wl config [OPTIONS] MODULES...
```
e.g.

```console
$ otk wl config all -p odoo-18 -x "l10n_*,test_*,*_test"
$ otk wl config l10n_be -p odoo-18-l10n -l nl,fr,de
```

### Arguments

* `MODULES...`: Include these Odoo modules in `.weblate.json`, or either `all`, `community`, or `enterprise`.  **[required]**

### Options

* `-p, --project TEXT`: Specify the Weblate project slug.  **[required]**
* `-x, --exclude TEXT`: Exclude these modules from being added or updated.
* `--path-filter PATH`: Only add or update modules within these paths.
* `-l, --language TEXT`: Define specific language codes for this component. Mostly used for localizations. If none are given, it follows the default languages on Weblate. If you want the specific PO file languages added as a filter, use `filter`.
* `--reset`: Reset the config file for the given project and only add the given modules.
* `--com-path PATH`: Specify the path to your Odoo Community repository.  [default: `odoo`]
* `--ent-path PATH`: Specify the path to your Odoo Enterprise repository.  [default: `enterprise`]
* `--addons-path PATH`: Specify extra addons paths if your modules are not in Community or Enterprise.
* `--help`: Show this message and exit.


## `otk wl config-odoo`

**Generate the standard Odoo Weblate config profile for a version.**

This is a shorthand for generating the main Odoo Weblate project, including the l10n modules, with the default exclude list used internally.

If you do not provide an Odoo version explicitly, the command tries to detect it from the Community path (or one of its parent folders) by looking for a folder named like `18.0`, `saas-18.2`, or `master`.

### Usage

```console
$ otk wl config-odoo [OPTIONS]
```
e.g.

```console
$ otk wl config-odoo
$ otk wl config-odoo -v saas-18.2
```

### Options

* `-v, --odoo-version TEXT`: The Odoo branch/version to configure, like `18.0`, `18`, `saas-18.2`, `18.2`, `s18-2`, or `master`. If omitted, it is detected from the current directory path.
* `--com-path PATH`: Specify the path to your Odoo Community repository.  [default: `odoo`]
* `--ent-path PATH`: Specify the path to your Odoo Enterprise repository.  [default: `enterprise`]
* `--addons-path PATH`: Specify extra addons paths if your modules are not in Community or Enterprise.
* `--help`: Show this message and exit.


## `otk wl copy`

**Copy translations between languages, components, and/or projects.**

This command allows you to copy existing translations of components in Weblate to either another language, another component, and/or another project. Technically it downloads the PO files from the source and uploads them to the destination.

If you don't define a destination project, it will copy inside the same project.
If you don't define a destination language, it will copy to the same language(s).
If you don't define any source components, it will copy all components that are both in the source and destination projects.
If you don't define a target component, it will copy to the same component(s) in the target project.

### Usage

```console
$ otk wl copy [OPTIONS]
```
e.g.

```console
$ otk wl copy -p odoo-18 -l pt_BR -L pt -o translate
$ otk wl copy -p odoo-18 -P odoo-19 -c l10n_be -l nl,fr,de
```

### Options

* `-p, --src-project TEXT`: The Weblate project to copy translations from.  **[required]**
* `-l, --src-language TEXT`: The language codes to copy translations from.  **[required]**
* `-P, --dest-project TEXT`: The Weblate projects to copy translations to.
* `-L, --dest-language TEXT`: The language code to copy translations to.
* `-c, --src-component TEXT`: The Weblate components to copy translations from.
* `-C, --dest-component TEXT`: The Weblate component to copy translations to.
* `-a, --author TEXT`: The author of the uploaded translations: the short name of a translator in your configuration file (see [`otk wl authors`](#otk-wl-authors)), or a full name. If not set, the API key user will be used.
* `-e, --email TEXT`: The author's email, for an author that isn't in your configuration file. Weblate finds the author's account by it.
* `-m, --method [translate|approve|suggest]`: Specify what the upload should do. Either upload the translations as reviewed strings (`approve`), non-reviewed strings (`translate`), or suggestions (`suggest`).  [default: `translate`]
* `-o, --overwrite [ignore|replace-translated|replace-approved]`: Specify what the upload should do. Either don't overwrite existing translations (`ignore`), overwrite only non-reviewed translations (`replace-translated`), or overwrite even approved translations (`replace-approved`).  [default: `ignore`]
* `--help`: Show this message and exit.


## `otk wl download`

**Download specific PO files from Weblate.**

This command can be useful to bulk download specific PO files from Weblate, and filtering the strings using a Weblate query. The files will be saved in the current working directory with the name pattern `<project>-<component>-<language>.po`.

### Usage

```console
$ otk wl download [OPTIONS]
```
e.g.

```console
$ otk wl download -p odoo-19 -l fr -q "state:<translated"
```

### Options

* `-p, --project TEXT`: The Weblate project to download translations from.  **[required]**
* `-l, --language TEXT`: The languages to download translations for. At least one language must be specified.  **[required]**
* `-c, --component TEXT`: The Weblate components to download translations from. You can use glob patterns. Downloads all components if none are specified.
* `-i, --ignore-component TEXT`: The Weblate components to ignore when downloading translations. You can use glob patterns.
* `-q, --query TEXT`: A Weblate query to filter strings. Files without any matching string aren't saved.
* `--help`: Show this message and exit.

Components that don't have the language, like localization modules of other countries, are skipped.


## `otk wl update-teams`

**Update Weblate teams permissions.**

This command will update the languages, projects, and/or roles associated with one or more teams in Weblate.

Adding or removing specific languages or projects only applies to teams that use their own list of them. A team with access to all languages or projects is left as is, and reported. Teams defined by a project are skipped. If a project or role doesn't exist, nothing is changed.

### Usage

```console
$ otk wl update-teams [OPTIONS] TEAMS...
```
e.g.

```console
$ otk wl update-teams "Translators:*" -p -odoo-16,+odoo-19 -r "+Add suggestion"
```

### Arguments

* `TEAMS...`: Names of the teams to update. Use glob patterns to match multiple teams with one string.  **[required]**

### Options

* `-l, --language TEXT`: The language codes to add or remove from the teams, or `all`. To add a language, prefix the code with `+`. To remove a language, prefix the code with `-`.
* `-p, --project TEXT`: The project slugs to add or remove from the teams, or `all`, `public`, or `protected`. Only existing Weblate projects can be used. To add a project, prefix it with `+`. To remove a project, prefix it with `-`.
* `-r, --role TEXT`: The roles to add or remove from the teams. The names need to match exactly with Weblate. To add a role, prefix it with `+`. To remove a role, prefix it with `-`.
* `--help`: Show this message and exit.


## `otk wl upload`

**Upload specific PO files to Weblate.**

This command can be useful to bulk upload specific PO files to Weblate. The files need to be in the current working directory, named `<project>-<component>-<language>.po`, like the ones [`otk wl download`](#otk-wl-download) saves.

### Usage

```console
$ otk wl upload [OPTIONS]
```
e.g.

```console
$ otk wl upload -p odoo-19 -l fr -m suggest
```

### Options

* `-p, --project TEXT`: The Weblate project to upload translations to.  **[required]**
* `-l, --language TEXT`: The languages to upload translations for. At least one language must be specified.  **[required]**
* `-c, --component TEXT`: The Weblate components to upload translations to. You can use glob patterns. Uploads all matching files if none are specified.
* `-a, --author TEXT`: The author of the uploaded translations: the short name of a translator in your configuration file (see [`otk wl authors`](#otk-wl-authors)), or a full name. If not set, the API key user will be used.
* `-e, --email TEXT`: The author's email, for an author that isn't in your configuration file. Weblate finds the author's account by it.
* `-m, --method [translate|approve|suggest]`: Upload the translations as reviewed strings (`approve`), non-reviewed strings (`translate`), or suggestions (`suggest`).  [default: `translate`]
* `-o, --overwrite [ignore|replace-translated|replace-approved]`: Don't overwrite existing translations (`ignore`), overwrite only non-reviewed translations (`replace-translated`), or overwrite even approved translations (`replace-approved`).  [default: `ignore`]
* `-f, --fuzzy [ignore|process|approve]`: Ignore fuzzy translations (`ignore`), mark them as "Needs editing" (`process`), or add them as regular translations (`approve`).  [default: `process`]
* `--help`: Show this message and exit.

The author is only used if your API key's user may set it (the "Define author of uploaded translation" permission).


## Odoo Multiverse (`otk mv`)

**Work with an Odoo Multiverse environment.**

The following commands allow you to set up and Odoo Multiverse environment and perform several useful actions inside the environment.

> [!WARNING]
> **Upgrading from 3.x:** version 4.0 changes how the multiverse is laid out and fetched. The easiest way to upgrade is to set up a new multiverse. If you keep your existing one:
> - `odoofin` is now a pinned repository: it moves into the version it runs on. Use `otk mv link odoofin` to link it elsewhere.
> - The repositories only fetch the branches you use from now on. Run `otk mv cleanup` to remove the others and reclaim disk space.
> - `otk dev start` is now `otk dev run`, which runs Odoo directly instead of opening a shell (use `otk dev shell` for that). It uses PostgreSQL 18 with new, empty databases. Stop the old containers with `docker compose --project-name odoo-dev down`.

## `otk mv setup`

**Set up an Odoo Multiverse environment, having different versions checked out at the same time.**

This way you can easily work on tasks in different versions without having to switch branches, or easily compare behaviors in different versions.

Every version gets its own directory (like `19.0`), which is a complete workspace you can open in your editor. The setup makes use of the [`git worktree`](https://git-scm.com/docs/git-worktree) feature to prevent having multiple full clones of the repositories for each version. The `git` history is only downloaded once, and the only extra data you have per version are the actual source files.

The easiest way to set this up is by creating a directory for your multiverse setup and then run this command from that directory.

> [!IMPORTANT]
> Make sure you have set up your GitHub SSH key beforehand in order to clone the repositories, and installed [`uv`](https://docs.astral.sh/uv/getting-started/installation/) and `npm`.

You can run the command as many times as you want. It only adds what's missing, and never touches your own files. Pass `--update-config` to overwrite the configuration files with the latest ones, or `--reset-config` to recreate the Python environments as well. Without `-b` or `-r`, it updates the versions and repositories that are already set up.

### Repositories

Every repository is one of four kinds:

| Kind | Examples | Where it lives |
|---|---|---|
| **Versioned** | `odoo`, `enterprise`, `design-themes`, `documentation`, `industry`, `o-spreadsheet` | A worktree in every version directory, on the version's branch. |
| **Shared** | `upgrade`, `upgrade-util` | One clone in the multiverse directory, on its default branch, linked into every version directory. |
| **Pinned** | `odoofin`, `iap-apps`, `internal` | One clone in the version directory of the Odoo version it runs on, on its default branch, linked from the multiverse directory. |
| **Standalone** | `mobile` | One clone in the multiverse directory, on its default branch, not linked into any version directory. |

You can set up any repository, also from another GitHub owner (like `-r odoo-ps/psbe-internal`). A repository that's already set up keeps its kind and owner. For new repositories that aren't known Odoo repositories, whatever isn't configured in `multiverse.toml` is detected: they're versioned if they have branches for several Odoo versions (like `18.0` and `19.0`), and shared otherwise, and their `odoo-dev` fork is added as the `dev` remote if you have access to it. Pinned and standalone repositories need their `kind` in `multiverse.toml`.

Shared, pinned and standalone repositories follow their default branch on GitHub: when it changes, the next setup switches to it, unless you're on another branch or have uncommitted changes.

A pinned repository runs on the Odoo version of its default branch (like `18.0`), or, if that's not a version, on the version of its modules' manifests (like `20.0` for `20.0.1.0`). `internal` runs on the version odoo.com runs on, which is looked up on odoo.com every time you run the setup. It lives in that version's directory, and is linked from the multiverse directory, so it's reachable at the same place as the other repositories. Use [`otk mv link`](#otk-mv-link) to also link it into another version while you work on code that interacts with it.

Describe pinned repositories and exceptions in a `multiverse.toml` file in the multiverse directory. Its repositories are set up by default:

```toml
[repos.i18n-apps]        # A pinned repository that isn't a known Odoo repository
kind = "pinned"          # versioned, shared, pinned or standalone
runs_on = "20.0"         # Optional: the Odoo version it runs on if it can't be detected, or "odoo.com"

[repos.odoofin]
also_in = ["19.0"]       # Always link it into these versions as well

[repos.runbot]
versions = ["17.0", "19.0"]   # Only add worktrees of a versioned repository in these versions

[repos.psbe-internal]
owner = "odoo-ps"        # The GitHub owner, if not `odoo`
dev_remote = false       # Whether it has a fork on `odoo-dev`
```

### Inner Workings

The command will basically do the following:

1. Clone the versioned repositories as [bare repositories](https://git-scm.com/docs/git-clone#Documentation/git-clone.txt-code--barecode) to a `.worktree-source/<repository>` folder. The clones are partial (`--filter=blob:none`): file contents are only downloaded when a version needs them. We add the remote `origin` (`git@github.com:odoo/<repository>.git`) for all repositories, and the remote `dev` (`git@github.com:odoo-dev/<repository>.git`) for the ones that have a fork there. Pushing to `origin` is disabled when there is a `dev` remote.

   To keep the repositories small and fast, every remote only fetches the branches your local branches track: the versions you set up, and the branches you switched to with [`otk mv switch`](#otk-mv-switch), as long as they exist on GitHub. On top of that, `origin` fetches its default branch, and `dev` fetches your own branches: the ones ending with `-<branch suffix>`, which defaults to the part of your Git email before the `@` (a suffix given with `--branch-suffix` is remembered). Other branches, like the `staging.*` and `tmp.*` branches or other people's branches, are only fetched on demand. Existing repositories are configured the same way; run [`otk mv cleanup`](#otk-mv-cleanup) to remove the branches they don't need anymore.

2. Clone the other repositories to the root of your multiverse directory, on their default branch. Pinned repositories then move into the version directory they run on, with a link at the root, so editors and language servers (like the Odoo one) see a regular folder. When the version a pinned repository runs on changes, the next setup moves it.

3. Create a directory per version, and add a worktree of every versioned repository in it, tracking the version's branch on `origin`.

4. Link each shared repository into each version directory, and each pinned repository into the versions in its `also_in` setting (using relative links).

5. Set up a Python virtual environment in the `.venv` directory of each version with [`uv`](https://docs.astral.sh/uv/), using the same Python version as [Runbot](https://runbot.odoo.com/runbot/versions). It contains all requirements of `odoo`, `documentation` and the linked pinned repositories, the ones they need but don't pin (like a `setuptools` that still has the `pkg_resources` the documentation's Sphinx imports), and the recommended ones in [`requirements.txt`](odoo_toolkit/multiverse_config/requirements.txt). Requirements that fail to build on your machine are reported, but don't fail the setup, since Odoo runs in Docker.
   *You can use this environment from the version directory using `source .venv/bin/activate`.*

6. Set up the JavaScript tooling for `odoo` and `enterprise` (located in the `tooling` directory inside the `web` module).

   It also installs `typescript@6` (not saved in Odoo's `package.json`), because the Odoo language server needs `tsserver` for JavaScript and only looks for it on the `PATH`. TypeScript 7 has no `tsserver` anymore, so it stays on 6.

   Then write `odools.toml`, the configuration of the Odoo language server: the `.venv` interpreter, the paths of `odoo` and every addons repository (relative to the workspace folder, so it works in a dev container too, and the server doesn't have to search the version directory for them), and the `tsserver` that was just installed.

7. Write the [Visual Studio Code configuration](odoo_toolkit/multiverse_config/.vscode) in each version's `.vscode` directory: recommended extensions, settings using the version's `.venv`, and tasks and debug configurations to run Odoo with [`otk dev run`](#otk-dev-run), where `F5` runs it with the debugger.

After all that is done, your directory structure should look like this with the default repositories selected:

```
<multiverse-folder>
├── .worktree-source
│   ├── odoo (bare)
│   ├── enterprise (bare)
│   └── design-themes (bare)
├── 18.0
│   └── ...
├── 19.0
│   ├── .venv (Python virtual environment)
│   ├── .vscode (Visual Studio Code configuration)
│   ├── ruff.toml (extends the one of odoo, for the other repositories)
│   ├── odoo (19.0)
│   ├── enterprise (19.0)
│   ├── design-themes (19.0)
│   ├── upgrade (link to ../upgrade)
│   ├── upgrade-util (link to ../upgrade-util)
│   └── odools.toml
├── ...
├── master
│   └── ...
├── upgrade (master)
├── upgrade-util (master)
├── requirements.txt
├── ruff-odoo.toml (Odoo's Ruff rules of master)
└── ruff.toml (extends ruff-odoo.toml)
```

Open a version directory (like `code <multiverse-folder>/19.0`) in your editor to work on that version.

### Dev Container

With `--devcontainer`, every version directory gets a [dev container](https://containers.dev) configuration. Open the version in Visual Studio Code and run **Dev Containers: Reopen in Container**. The window, its extensions and any AI agent in it then only see your multiverse directory (mounted at the same path as on your machine): not your home folder, SSH keys or Git credentials.

- Opening the container starts the shared services (`otk dev db`), since it joins their network. Odoo starts from the **Odoo (container): run and debug** launch configuration, because `otk dev run` needs Docker.
- Git commands that need GitHub (fetch, pull, push) don't work in the container. Run them from a terminal on your machine.
- The `.devcontainer`, `.vscode` and `.venv` folders are read-only in the container, because they hold what runs on your machine later (like tasks and the dev container's own configuration). So are the `hooks` folder and `config` file of every bare repository in `.worktree-source`, since a Git hook, alias or `core.fsmonitor` setting there would run the next time you use Git on your machine. Commands that write that configuration, like `git remote add` or a new branch that tracks a remote, fail in the container: run them on your machine. Change all of these from your machine, for example with `otk mv setup --update-config`, which also covers repositories you add later.
- Everything else in the multiverse can be read and changed in the container, and it has network access, including to the databases of `otk dev`. Don't keep secrets or data you can't share in your multiverse or those databases if you use AI agents.
- Claude Code in the container gets instructions on how it works (the source layout, how to start Odoo, the services, and the limits above) from a read-only `CLAUDE.md` that `--update-config` writes in the `.devcontainer` folder. Your own `CLAUDE.md` in the repositories still applies. The network is open, so Claude Code's own permission prompts are what stop it from reaching a site you didn't approve: don't run it in a mode that skips them.
- Odoo's skills (the `skills/` folder of the Odoo repository: guidelines, review, security and git conventions) are linked into `.claude/skills` of every version directory by `otk mv setup`, so Claude Code finds them there, natively or in a dev container. It is a link to the repository, so they are the ones of the checked out branch, and nothing is copied. Only the newest versions have a `skills/` folder, so an older version links to the skills of the newest version that has them, until it has its own.
- Only windows opened in the container are isolated, not agents you start from a terminal on your machine.
- Visual Studio Code shares your Git config and credential helper with every dev container, which a project can't turn off. Add `"dev.containers.gitCredentialHelperConfigLocation": "none"` to your user settings to keep your GitHub login out. Add `"dev.containers.copyGitConfig": false` as well, unless you've checked that your `~/.gitconfig` holds no secrets, like a token in `url.*.insteadOf` or `http.extraHeader`, which the container could use to push.
- Extensions and settings you want in the dev containers of this multiverse, like a spell checker, go in a `[devcontainer]` table in `multiverse.toml`, which `--update-config` applies. Extensions you want in every dev container go in the `dev.containers.defaultExtensions` user setting.

  ```toml
  [devcontainer]
  extensions = ["streetsidesoftware.code-spell-checker"]

  [devcontainer.settings]
  "cSpell.allowCompoundWords" = true
  ```
- To sign commits in a dev container, use a GPG key: Visual Studio Code forwards your `gpg-agent` into it, so the container can sign, and GitHub doesn't accept GPG keys to push, so it still can't push. Set it up once for all your dev containers:
  1. Install GnuPG and a graphical passphrase prompt: on macOS `brew install gnupg pinentry-mac`, on Debian or Ubuntu `sudo apt install gnupg pinentry-gnome3` (or `pinentry-qt`, depending on your desktop).
  2. Create a signing-only key, with an email that is verified on GitHub: `gpg --quick-generate-key "Your Name <you@example.com>" ed25519 sign 2y`. Find its fingerprint with `gpg --list-secret-keys --keyid-format long`.
  3. Ask for the passphrase in a window: add `default-cache-ttl 600` to `~/.gnupg/gpg-agent.conf` (use `0` to be asked for every commit), and on macOS also `pinentry-program` with the output of `brew --prefix` followed by `/bin/pinentry-mac`. Then run `gpgconf --kill gpg-agent`. On Linux, the prompt your desktop provides is used by default.
  4. Add the public key (`gpg --armor --export <fingerprint>`) to GitHub, in *Settings*, *SSH and GPG keys*, *New GPG key*.
  5. Sign with it on your machine too: `git config --global gpg.format openpgp`, `git config --global user.signingkey <fingerprint>` and `git config --global commit.gpgsign true`.
  6. Let the dev containers copy your Git config, by setting `"dev.containers.copyGitConfig": true` in your user settings, and keep `"dev.containers.gitCredentialHelperConfigLocation": "none"`. Check first that `~/.gitconfig` holds no secrets, as the previous point explains.
  7. Rebuild your dev containers, and check there: `echo test | gpg --clearsign` asks for your passphrase, and `ssh -T git@github.com` is refused.
- Claude Code is installed, but your login on your machine stays out. Log in once in the container: it's kept in the `otk-claude` volume, which all your dev containers share, so it survives rebuilds.

### Usage

```console
$ otk mv setup [OPTIONS]
```
e.g.
```console
$ otk mv setup -b 18.0 -b 19.0 -b master -r odoo -r enterprise -r upgrade -r upgrade-util
```

### Options

* `-b, --branches TEXT`: The Odoo versions to set up, like `19.0` or `saas-19.2`. Defaults to the ones already set up, or every supported version for a new multiverse.
* `-r, --repositories TEXT`: The repositories to set up, like `odoo`, or `odoo-ps/psbe-internal` for another GitHub owner. Defaults to the ones already set up, or `odoo`, `enterprise`, `design-themes`, `upgrade` and `upgrade-util` for a new multiverse, plus the ones in `multiverse.toml`. A repository the toolkit doesn't know, like the one above, is only set up again later if it has a `[repos.<name>]` table there.
* `-d, --multiverse-dir PATH`: The directory to set up the multiverse in. Defaults to this directory.
* `--update-config`: Overwrite the configuration files of every version, like the Visual Studio Code and dev container ones, to get the latest from the toolkit. The Python virtual environments are kept.
* `--reset-config`: Recreate the Python virtual environments as well as overwriting the configuration files of every version.
* `--vscode / --no-vscode`: Write the Visual Studio Code configuration in each version directory.  [default: `--vscode`]
* `--devcontainer / --no-devcontainer`: Write a dev container configuration in each version directory, to open it in Visual Studio Code in a container that only sees your multiverse, for example to keep AI agents away from the rest of your machine. Versions that already have one keep it up to date, unless you pass `--no-devcontainer`.
* `--branch-suffix TEXT`: The suffix of your own branches on the `dev` remote, like `abcd` for `master-fix-xyz-abcd`. It's remembered for later commands. Defaults to the remembered one, or else the part of your Git email before the `@`.
* `--full-history`: Download the file contents of the whole history of the versioned repositories, so `git blame` works offline, like in a dev container. It needs several gigabytes more disk space and time than the default partial clones. Repositories that are partial already are completed, which only downloads what they miss with Git 2.49 or newer, and everything again with older versions.
* `--help`: Show this message and exit.

## `otk mv reset`

**Reset the repositories inside an Odoo Multiverse version directory.**

Run this command anywhere inside one of the multiverse version directories (like `19.0`). Every versioned repository is checked out on the version's branch and reset to the latest state on `origin`, so you start with a clean slate.

Uncommitted changes are stashed with a label telling where they came from (like `otk: 19.0/odoo before reset to 19.0`), and the command to restore them is shown afterwards. Commits on the version's branch that only exist on your machine are listed first, and only discarded if you confirm. They're reported with the commit to recover them from. Leftover `__pycache__` folders of deleted or moved modules are removed as well.

### Usage

```console
$ otk mv reset [OPTIONS]
```

### Options

* `--all`: Reset all repositories in the version directory, including the linked ones (like `upgrade` or `odoofin`), which are reset to their default branch. Other versions use them too, so this affects them as well.
* `-y, --yes`: Don't ask before discarding commits that only exist on your machine.
* `--help`: Show this message and exit.


## `otk mv switch`

**Switch branches inside an Odoo Multiverse version directory.**

Run this command anywhere inside one of the multiverse version directories (like `19.0`). Every repository that has the given branch on one of its remotes (`origin` first) or locally is switched to it and fast-forwarded. The other repositories stay on their current branch, which is fast-forwarded to its upstream.

A common use-case is to switch to a specific task's branches. Branches the remotes don't fetch by default, like your colleagues' `dev` branches, are fetched on demand, and keep being fetched from then on.

Uncommitted changes are stashed with a label telling where they came from, so you can restore them later. Local commits are never discarded: if a branch has diverged from its remote, it is left as is and reported.

### Usage

```console
$ otk mv switch [OPTIONS] BRANCH
```
e.g.
```console
$ otk mv switch odoo-dev:master-fix-xyz
```

### Arguments

* `BRANCH`: Switch to this branch for all repositories having the branch on their remote. The branch can be prefixed with its GitHub owner, like `odoo-dev:master-fix-xyz`, to only look for it on the remotes of that owner.  **[required]**

### Options

* `--all`: Switch all repositories in the version directory, including the linked ones (like `upgrade` or `odoofin`). Other versions use them too, so this affects them as well.
* `--help`: Show this message and exit.


## `otk mv link`

**Link a shared or pinned repository into the current Odoo Multiverse version directory.**

Pinned repositories live in the version they run on. Link them into another version when you work on code that interacts with them, and unlink them again with [`otk mv unlink`](#otk-mv-unlink) when you're done. To always link a pinned repository into a version, add the version to its `also_in` setting in `multiverse.toml`.

### Usage

```console
$ otk mv link REPO
```
e.g.
```console
$ cd ~/code/odoo/18.0 && otk mv link odoofin
```


## `otk mv unlink`

**Unlink a shared or pinned repository from the current Odoo Multiverse version directory.**

Only the link is removed: the repository itself stays, and other versions keep using it.

### Usage

```console
$ otk mv unlink REPO
```


## `otk mv cleanup`

**Remove the branches the Odoo Multiverse repositories don't need, and reclaim their disk space.**

This removes:
- local branches that aren't checked out in any version, and whose commits all exist on GitHub,
- the copies of branches the remotes don't fetch anymore, like other people's `dev` branches.

Nothing that only exists on your machine is removed: branches with local commits are kept, and everything removed can be fetched again from GitHub. The command shows what it will remove and asks for confirmation. Afterwards, Git's garbage collection reclaims the disk space, which can take a few minutes for large repositories.

### Usage

```console
$ otk mv cleanup [OPTIONS]
```

### Options

* `-d, --multiverse-dir PATH`: The multiverse directory. Defaults to the one containing the current directory.
* `--branch-suffix TEXT`: The suffix of your own branches on the `dev` remote, like `abcd` for `master-fix-xyz-abcd`. Defaults to the one given to `otk mv setup`, or else the part of your Git email before the `@`.
* `-y, --yes`: Don't ask for confirmation.
* `--help`: Show this message and exit.

## `otk code`

Open a version directory of your multiverse in Visual Studio Code, like `otk code 19.0`.

Run it anywhere inside your multiverse. Without a version, it opens the version directory you are in. If the version has a [dev container](#otk-mv-setup) (from `otk mv setup --devcontainer`), it opens in it. With a [profile](https://code.visualstudio.com/docs/configure/profiles), the window always has your extensions and settings for Odoo, so you don't have to associate every version directory with it in Visual Studio Code. Set it once in `multiverse.toml` to use it every time:

```toml
[code]
profile = "Odoo"
```

It needs the `code` command: run **Shell Command: Install 'code' command in PATH** in Visual Studio Code.

### Usage

```console
$ otk code [OPTIONS] [VERSION]
```

### Arguments

* `[VERSION]`: The version to open, like `19.0`. Defaults to the version directory you are in.

### Options

* `-p, --profile TEXT`: The Visual Studio Code profile to open it with, like `Odoo`. Defaults to `profile` in the `[code]` table of `multiverse.toml`. A profile that doesn't exist yet is created empty.
* `--local`: Open it on your machine, even if it has a dev container.
* `--help`: Show this message and exit.
