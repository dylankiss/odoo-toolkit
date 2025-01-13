# 🧰 Odoo Toolkit

This toolkit contains a few useful tools for Odoo development. The tools are primarily aimed at Odoo employees.

The quickest way to get started is to install the tools using:

```console
$ pipx install odoo-toolkit
```

> [!TIP]
> You can run any command with the `--help` option to find out all options you can use.

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

### [Odoo Translations (`otk po`)](#odoo-translations-otk-po-1)

| Command                           | Purpose                                                                             |
| --------------------------------- | ----------------------------------------------------------------------------------- |
| [`otk po export`](#otk-po-export) | Export Odoo translation files (.pot) to each module's i18n folder.                  |
| [`otk po create`](#otk-po-create) | Create Odoo translation files (.po) according to their .pot files.                  |
| [`otk po update`](#otk-po-update) | Update Odoo translation files (.po) according to a new version of their .pot files. |
| [`otk po merge`](#otk-po-merge)   | Merge multiple translation files (.po) into one.                                    |
|                                   | [Available Languages](#available-languages)                                         |

### [Odoo Development Server (`otk dev`)](#odoo-development-server-otk-dev-1)

| Command                                 | Purpose                                                                              |
| --------------------------------------- | ------------------------------------------------------------------------------------ |
| [`otk dev start`](#otk-dev-start)       | Start an Odoo Development Server using Docker and launch a terminal session into it. |
| [`otk dev start-db`](#otk-dev-start-db) | Start a standalone PostgreSQL container for your Odoo databases.                     |
| [`otk dev stop`](#otk-dev-stop)         | Stop and delete all running containers of the Odoo Development Server.               |

### [Odoo Transifex (`otk tx`)](#odoo-transifex-otk-tx-1)

| Command                     | Purpose                                   |
| --------------------------- | ----------------------------------------- |
| [`otk tx add`](#otk-tx-add) | Add modules to the Transifex config file. |

### [Other Commands](#other-commands-1)

| Command                             | Purpose                                                                                        |
| ----------------------------------- | ---------------------------------------------------------------------------------------------- |
| [`otk multiverse`](#otk-multiverse) | Set up an Odoo Multiverse environment, having different branches checked out at the same time. |


## Odoo Translations (`otk po`)

**Work with Odoo Translations (.po and .pot files).**

The following commands allow you to export `.pot` files for Odoo modules, create or update `.po` files according to their (updated) `.pot` files, or merge multiple `.po` files into one.


## `otk po export`

**Export Odoo translation files (.pot) to each module's i18n folder.**

This command can autonomously start separate Odoo servers to export translatable terms for one or more modules. A separate server will be started for Community, Community (Localizations), Enterprise, and Enterprise (Localizations) modules with only the modules installed to be exported in that version.

When exporting the translations for `base`, we install all possible modules to ensure all terms added in by other modules get exported in the `base.pot` files as well.

You can also export terms from your own running server using the `--own-server` option and optionally passing the correct arguments to reach your Odoo server.

> [!NOTE]
> Without any options specified, the command is supposed to run from within the parent directory where your `odoo` and `enterprise` repositories are checked out with these names. Your database is supposed to run on `localhost` using port `5432`, accessible without a password using your current user.
>
> Of course, all of this can be tweaked with the available options. 😉

### Usage

```console
$ otk po export [OPTIONS] MODULES...
```
e.g.

```console
$ otk po export --db-username odoo --db-password odoo "account_*" mrp sale
```

### Arguments

* `MODULES...`: Export .pot files for these Odoo modules (supports glob patterns), or either `all`, `community`, or `enterprise`.  **[required]**

### Options

**Odoo Server Options**:

* `--start-server / --own-server`: Start an Odoo server automatically or connect to your own server.  [default: `start-server`]
* `--full-install`: Install every available Odoo module.
* `-c, --com-path PATH`: Specify the path to your Odoo Community repository.  [default: `odoo`]
* `-e, --ent-path PATH`: Specify the path to your Odoo Enterprise repository.  [default: `enterprise`]
* `-u, --username TEXT`: Specify the username to log in to Odoo.  [default: `admin`]
* `-p, --password TEXT`: Specify the password to log in to Odoo.  [default: `admin`]
* `--host TEXT`: Specify the hostname of your Odoo server.  [default: `localhost`]
* `--port INTEGER`: Specify the port of your Odoo server.  [default: `8069`]

**Database Options**:

* `-d, --database TEXT`: Specify the PostgreSQL database name used by Odoo.  [default: `__export_pot_db__`]
* `--db-host TEXT`: Specify the PostgreSQL server's hostname.  [default: `localhost`]
* `--db-port INTEGER`: Specify the PostgreSQL server's port.  [default: `5432`]
* `--db-username TEXT`: Specify the PostgreSQL server's username.
* `--db-password TEXT`: Specify the PostgreSQL user's password.

**Options:**

* `--help`: Show this message and exit.

## `otk po create`

**Create Odoo translation files (.po) according to their .pot files.**

This command will provide you with a clean .po file per language you specified for the given modules. It basically copies all entries from the .pot file in the module and completes the metadata with the right language information. All generated .po files will be saved in the respective modules' `i18n` directories.

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

* `MODULES...`: Create .po files for these Odoo modules, or either `all`, `community`, or `enterprise`.  **[required]**

### Options

* [`-l, --languages LANG`](#available-languages): Create .po files for these languages, or `all`.  [default: None] **[required]**
* `-c, --com-path PATH`: Specify the path to your Odoo Community repository.  [default: `odoo`]
* `-e, --ent-path PATH`: Specify the path to your Odoo Enterprise repository.  [default: `enterprise`]
* `--help`: Show this message and exit.


## `otk po update`

**Update Odoo translation files (.po) according to a new version of their .pot files.**

This command will update the .po files for the provided modules according to a new .pot file you might have exported in their `i18n` directory.

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

* `MODULES...`: Update .po files for these Odoo modules, or either `all`, `community`, or `enterprise`.  **[required]**

### Options

* [`-l, --languages LANG`](#available-languages): Update .po files for these languages, or `all`.  [default: `all`]
* `-c, --com-path PATH`: Specify the path to your Odoo Community repository.  [default: `odoo`]
* `-e, --ent-path PATH`: Specify the path to your Odoo Enterprise repository.  [default: `enterprise`]
* `--help`: Show this message and exit.


## `otk po merge`

**Merge multiple translation files (.po) into one.**

The order of the files determines which translation takes priority. Empty translations in earlier files will be completed with translations from later files, taking the first one in the order they occur.

If the option `--overwrite` is active, existing translations in earlier files will always be overwritten by translations in later files. In that case the last file takes precedence.

The .po metadata is taken from the first file by default, or the last if `--overwrite` is active.

### Usage

```console
$ otk po merge [OPTIONS] PO_FILES...
```
e.g.
```console
$ otk po merge -o nl_merged.po nl.po nl_BE.po
```

### Arguments

* `PO_FILES...`: Merge these .po files together.  [required]

### Options

* `-o, --output-file PATH`: Specify the output .po file.  [default: `merged.po`]
* `--overwrite`: Overwrite existing translations.
* `--help`: Show this message and exit.


## Available Languages

When `LANG` is given as a type, any of the following language codes can be used. They are all available in Odoo.

> [!TIP]
> When creating new .po files, always try to use the most generic language possible. That way, all more specific languages can fall back on that one.

| Code     | Language                     | Code       | Language              |
| -------- | ---------------------------- | ---------- | --------------------- |
| `all`    | *All languages*              | `he`       | Hebrew                |
| `am`     | Amharic                      | `hi`       | Hindi                 |
| `ar`     | Arabic                       | `hr`       | Croatian              |
| `ar_SY`  | Arabic (Syria)               | `hu`       | Hungarian             |
| `az`     | Azerbaijani                  | `id`       | Indonesian            |
| `be`     | Belarusian                   | `it`       | Italian               |
| `bg`     | Bulgarian                    | `ja`       | Japanese              |
| `bn`     | Bengali                      | `ka`       | Georgian              |
| `bs`     | Bosnian                      | `kab`      | Kabyle                |
| `ca`     | Catalan                      | `km`       | Khmer                 |
| `cs`     | Czech                        | `ko`       | Korean                |
| `da`     | Danish                       | `ko_KP`    | Korean (North Korea)  |
| `de`     | German                       | `lb`       | Luxembourgish         |
| `de_CH`  | German (Switzerland)         | `lo`       | Lao                   |
| `el`     | Greek                        | `lt`       | Lithuanian            |
| `en_AU`  | English (Australia)          | `lv`       | Latvian               |
| `en_CA`  | English (Canada)             | `mk`       | Macedonian            |
| `en_GB`  | English (United Kingdom)     | `ml`       | Malayalam             |
| `en_IN`  | English (India)              | `mn`       | Mongolian             |
| `en_NZ`  | English (New Zealand)        | `ms`       | Malay                 |
| `es`     | Spanish                      | `my`       | Burmese               |
| `es_419` | Spanish (Latin America)      | `nb`       | Norwegian Bokmål      |
| `es_AR`  | Spanish (Argentina)          | `nl`       | Dutch                 |
| `es_BO`  | Spanish (Bolivia)            | `nl_BE`    | Dutch (Belgium)       |
| `es_CL`  | Spanish (Chile)              | `pl`       | Polish                |
| `es_CO`  | Spanish (Colombia)           | `pt`       | Portuguese            |
| `es_CR`  | Spanish (Costa Rica)         | `pt_AO`    | Portuguese (Angola)   |
| `es_DO`  | Spanish (Dominican Republic) | `pt_BR`    | Portuguese (Brazil)   |
| `es_EC`  | Spanish (Ecuador)            | `ro`       | Romanian              |
| `es_GT`  | Spanish (Guatemala)          | `ru`       | Russian               |
| `es_MX`  | Spanish (Mexico)             | `sk`       | Slovak                |
| `es_PA`  | Spanish (Panama)             | `sl`       | Slovenian             |
| `es_PE`  | Spanish (Peru)               | `sq`       | Albanian              |
| `es_PY`  | Spanish (Paraguay)           | `sr`       | Serbian               |
| `es_UY`  | Spanish (Uruguay)            | `sr@latin` | Serbian (Latin)       |
| `es_VE`  | Spanish (Venezuela)          | `sv`       | Swedish               |
| `et`     | Estonian                     | `sw`       | Swahili               |
| `eu`     | Basque                       | `te`       | Telugu                |
| `fa`     | Persian                      | `th`       | Thai                  |
| `fi`     | Finnish                      | `tl`       | Tagalog / Filipino    |
| `fr`     | French                       | `tr`       | Turkish               |
| `fr_BE`  | French (Belgium)             | `uk`       | Ukrainian             |
| `fr_CA`  | French (Canada)              | `vi`       | Vietnamese            |
| `fr_CH`  | French (Switzerland)         | `zh_CH`    | Chinese (Simplified)  |
| `gl`     | Galician                     | `zh_HK`    | Chinese (Hong Kong)   |
| `gu`     | Gujarati                     | `zh_TW`    | Chinese (Traditional) |


## Odoo Development Server (`otk dev`)

**Run an Odoo Development Server using Docker.**

The following commands allow you to automatically start and stop a fully configured Docker container to run your Odoo server(s) during development.

> [!IMPORTANT]
> These tools require [Docker Desktop](https://www.docker.com/products/docker-desktop/) to be installed on your system.

The Docker container is configured to resemble Odoo's CI or production servers and thus tries to eliminate discrepancies between your local system and the CI or production server.


## `otk dev start`

**Start an Odoo Development Server using Docker and launch a terminal session into it.**

This command will start both a PostgreSQL container and an Odoo container containing your source code, located on your machine at the location specified by `-w`. Your specified workspace will be sourced in the container at the location `/code` and allows live code updates during local development.

You can choose to launch a container using Ubuntu 24.04 [`-u noble`] (default, recommended starting from version 18.0) or 22.04 [`-u jammy`] (for earlier versions).

When you're done with the container, you can exit the session by running the `exit` command. At this point, the container will still be running and you can start a new session using the same `otk dev start` command.

### Port Mapping

The `jammy` container exposes ports `8070`, `8071`, `8072`, `8073` and `8074`. The default port inside the container when using the `o-bin-*` commands, is `8070`. When not using the `o-bin-*` commands, or when starting another server, you need to use the `--http-port` option to run on one of the available ports.

The `noble` container exposes ports `8075`, `8076`, `8077`, `8078` and `8079`. The default port inside the container when using the `o-bin-*` commands, is `8075`. When not using the `o-bin-*` commands, or when starting another server, you need to use the `--http-port` option to run on one of the available ports.

This allows you to run up to 5 different servers per Docker container, all accessible on your local machine. If you also have a local server running on default port `8069`, it will not clash with the Docker ports.

### PostgreSQL Container

The command starts a separate [PostgreSQL](https://www.postgresql.org/) container that you can access from your host machine at `localhost:5432` by default, using `odoo` as username and password. Inside your other Docker container, the hostname of this server is `db`. It is exposed to the Odoo containers via a socket as well in the `/var/run/postgresql` directory.

### Mailpit Container

The command also starts a [Mailpit](https://mailpit.axllent.org/) container that is used for intercepting outgoing email messages from any of your Odoo instances. The application can be accessed from your local machine by going to http://localhost:8025. It allows you to view any sent email with its attachments and all possible related information. You can download them in `.eml` format, `.html` format, and even `.png` format as a screenshot.

![Mailpit Interface](images/mailpit.png)

The container uses a persistent storage (on the `odoo-mailpit-storage` volume) that saves up to 5000 messages. The oldest messages will be deleted after that. When the volume is deleted, all messages will be gone as well.

### Aliases

The container contains some helpful aliases that you can use to run and debug Odoo from your workspace (*either `/code` or `/code/<branch>` if you're using the multiverse setup*). They contain the right configuration to connect to the PostgreSQL database and set very high time limits by default (useful for debugging). You can check them in [`.bash_aliases`](odoo_toolkit/docker/.bash_aliases).

**Running Odoo** (from within the workspace folder)
- `o-bin` can be used instead of `odoo/odoo-bin` with the same arguments.

**Running an Odoo shell** (from within the workspace folder)
- `o-bin-sh` can be used instead of `odoo/odoo-bin shell` with the same arguments.

**Upgrading Odoo** (from within the workspace folder)
- `o-bin-up` can be used instead of `odoo/odoo-bin --upgrade-path=...` with the same arguments.

**Debugging Odoo** (from within the workspace folder)
- `o-bin-deb` can be used instead of `odoo/odoo-bin` with the same arguments, starts a debug session using [`debugpy`](https://github.com/microsoft/debugpy) and waits for your local debugger to connect to it before starting.

**Debugging Odoo Upgrade** (from within the workspace folder)
- `o-bin-deb-up` can be used instead of `odoo/odoo-bin --upgrade-path=...` with the same arguments, starts a debug session using [`debugpy`](https://github.com/microsoft/debugpy) and waits for your local debugger to connect to it before starting.

> [!TIP]
> Every alias can be appended by `-c` or `-e` to have the addons paths for respectively Community or Enterprise (e.g. `o-bin-deb-up-c`).

> [!TIP]
> Compatible [Visual Studio Code](https://code.visualstudio.com/) debug configurations are available in [`launch.json`](odoo_toolkit/multiverse_config/.vscode/launch.json).

The most common PostgreSQL commands have also been aliased to use the right database and credentials, so you could just run e.g. `dropdb <database>`.

### Docker Configuration

The configuration for the Docker containers is located in the `odoo_toolkit/docker` folder in this repository. The [`compose.yaml`](odoo_toolkit/docker/compose.yaml) file defines an `odoo-noble` and `odoo-jammy` service that run the development containers with the right configuration and file mounts, and a `db` service that runs the PostgreSQL server.

The development container configuration is laid out in [`noble.Dockerfile`](odoo_toolkit/docker/noble.Dockerfile) and [`jammy.Dockerfile`](odoo_toolkit/docker/jammy.Dockerfile).

### Usage

```console
$ otk dev start [OPTIONS]
```
e.g.
```console
$ otk dev start -u jammy
```

### Options

* `-w, --workspace PATH`: Specify the path to your development workspace that will be mounted in the container's `/code` directory.  [default: `~/code/odoo`]
* `-u, --ubuntu-version [noble|jammy]`: Specify the Ubuntu version to run in this container.  [default: `noble`]
* `-p, --db-port INTEGER`: Specify the port on your local machine the PostgreSQL database should listen on.  [default: `5432`]
* `--rebuild`: Rebuild the Docker image to get the latest dependencies.
* `--help`: Show this message and exit.


## `otk dev start-db`

**Start a standalone PostgreSQL container for your Odoo databases.**

You can use this standalone container if you want to connect to it from your local machine which is running Odoo. By default it will listen on port 5432, but you can modify this if you already have another PostgreSQL server running locally.

### Usage

```console
$ otk dev start-db [OPTIONS]
```

### Options

* `-p, --port INTEGER`: Specify the port on your local machine the PostgreSQL database should listen on.  [default: `5432`]
* `--help`: Show this message and exit.


## `otk dev stop`

**Stop and delete all running containers of the Odoo Development Server.**

This is useful if you want to build a new version of the container, or you want the container to have the latest version of `odoo-toolkit`.

Running this is also necessary if you updated the `odoo-toolkit` package on your local machine. If not, your container won't be able to mount the configuration files.

### Usage

```console
$ otk dev stop [OPTIONS]
```

### Options

* `--help`: Show this message and exit.


## Odoo Transifex (`otk tx`)

**Work with Transifex.**

The following commands allow you to modify the Transifex config files.


## `otk tx add`

**Add modules to the Transifex config file.**

This command will add module entries to `.tx/config` files. The `.tx/config` files need to be located at the provided
addons paths' roots. If the entries already exists, they will potentially be updated.

For `odoo` and `enterprise`, the project name follows the format `odoo-18` for major versions and `odoo-s18-1` for SaaS
versions. Other repos have their own project names.

### Usage

```console
$ otk tx add [OPTIONS] MODULES...
```
e.g.

```console
$ otk tx add -p odoo-18 -a design-themes theme_*
```

### Arguments

* `MODULES...`: Add these Odoo modules to `.tx/config`, or either `all`, `community`, or `enterprise`.  **[required]**

### Options

* `-p, --tx-project TEXT`: Specify the Transifex project name.  **[required]**
* `-o, --tx-org TEXT`: Specify the Transifex organization name.  [default: `odoo`]
* `-c, --com-path PATH`: Specify the path to your Odoo Community repository.  [default: `odoo`]
* `-e, --ent-path PATH`: Specify the path to your Odoo Enterprise repository.  [default: `enterprise`]
* `-a, --addons-path PATH`: Specify extra addons paths if your modules are not in Community or Enterprise.
* `--help`: Show this message and exit.


## Other Commands


## `otk multiverse`

**Set up an Odoo Multiverse environment, having different branches checked out at the same time.**

This way you can easily work on tasks in different versions without having to switch branches, or easily compare behaviors in different versions.

The setup makes use of the [`git worktree`](https://git-scm.com/docs/git-worktree) feature to prevent having multiple full clones of the repositories for each version. The `git` history is only checked out once, and the only extra data you have per branch are the actual source files.

The easiest way to set this up is by creating a directory for your multiverse setup and then run this command from that directory.

> [!IMPORTANT]
> Make sure you have set up your GitHub SSH key beforehand in order to clone the repositories.

You can run the command as many times as you want. It will skip already existing branches and repositories and only renew their configuration (when passed the `--reset-config` option).

> [!TIP]
> If you're using [Visual Studio Code](https://code.visualstudio.com/), you can use the `--vscode` option to have the script copy some [default configuration](odoo_toolkit/multiverse_config/.vscode) to each branch folder. It contains recommended plugins, plugin configurations and debug configurations (that also work with the Docker container started via [`otk dev start`](#otk-dev-start)).

### Inner Workings

The command will basically do the following:

1. Clone the repositories that have multiple active branches (among `odoo`, `enterprise`, `design-themes`, `documentation`, `industry` and `o-spreadsheet`) as [bare repositories](https://git-scm.com/docs/git-clone#Documentation/git-clone.txt-code--barecode) to a `.multiverse-source/<repository>` folder (we don't need the source files themselves here). We set them up in a way that we can correctly create worktrees for each branch. We add the remote `origin` (`git@github.com:odoo/<repository>.git`) for all repositories and the remote `dev` (`git@github.com:odoo-dev/<repository>.git`) for all except the `documentation` and `o-spreadsheet` repositories.

2. Clone the single-branch repositories (among `upgrade`, `upgrade-util`, `odoofin` and `internal`) to the root of your multiverse directory.

3. Create a directory per branch in your multiverse directory, and a directory per multi-branch repository inside each branch directory. We use [`git worktree add`](https://git-scm.com/docs/git-worktree#Documentation/git-worktree.txt-addltpathgtltcommit-ishgt) to add a worktree for the correct branch for each repository.

4. Create a symlink to each single-branch repository in each branch directory, since they all use the same `master` branch of these repositories.

5. Copy all [configuration files](odoo_toolkit/multiverse_config) into each branch directory and set up the Javascript tooling for `odoo` and `enterprise` (located in the `tooling` directory inside the `web` module).

6. Set up a Python virtual environment ([`venv`](https://docs.python.org/3/library/venv.html)) in the `.venv` directory inside each branch directory and install all requirements of `odoo` and `documentation`, as well as the recommended ones in [`requirements.txt`](odoo_toolkit/multiverse_config/requirements.txt).
   *You can use this environment from the branch directory using `source .venv/bin/activate`.*

After all that is done, your directory structure should look like this with the default repositories selected:

```
<multiverse-folder>
├── .worktree-source
│   ├── odoo (bare)
│   ├── enterprise (bare)
│   └── design-themes (bare)
├── 16.0
│   └── ...
├── 17.0
│   └── ...
├── saas-17.2
│   └── ...
├── saas-17.4
│   └── ...
├── 18.0
│   ├── .venv (Python virtual environment)
│   ├── odoo (18.0)
│   ├── enterprise (18.0)
│   ├── design-themes (18.0)
│   ├── upgrade (symlink to ../upgrade)
│   ├── upgrade-util (symlink to ../upgrade-util)
│   ├── pyproject.toml
│   └── requirements.txt
├── master
│   └── ...
├── upgrade (master)
└── upgrade-util (master)
```

### Usage

```console
$ otk multiverse [OPTIONS]
```
e.g.
```console
$ otk multiverse -b 16.0 -b 17.0 -b 18.0 -r odoo -r enterprise -r upgrade -r upgrade-util --vscode
```

### Options

* `-b, --branches TEXT`: Specify the Odoo branches you want to add.  [default: `16.0`, `17.0`, `saas-17.2`, `saas-17.4`, `18.0`, `master`]
* `-r, --repositories REPO`: Specify the Odoo repositories you want to sync.  [default: `odoo`, `enterprise`, `design-themes`, `upgrade`, `upgrade-util`]
* `-d, --multiverse-dir PATH`: Specify the directory in which you want to install the multiverse setup.  [default: `<current working directory>`]
* `--reset-config`: Reset every specified worktree's Ruff config, Python virtual environment and dependencies, and optional Visual Studio Code config.
* `--vscode`: Copy settings and debug configurations for Visual Studio Code.
* `--help`: Show this message and exit.

### Repositories

`REPO` can be any of these repositories:
- [`odoo`](https://github.com/odoo/odoo)
- [`enterprise`](https://github.com/odoo/enterprise)
- [`design-themes`](https://github.com/odoo/design-themes)
- [`documentation`](https://github.com/odoo/documentation)
- [`upgrade`](https://github.com/odoo/upgrade)
- [`upgrade-util`](https://github.com/odoo/upgrade-util)
- [`odoofin`](https://github.com/odoo/odoofin)
- [`industry`](https://github.com/odoo/industry)
- [`o-spreadsheet`](https://github.com/odoo/o-spreadsheet)
- [`internal`](https://github.com/odoo/internal)
