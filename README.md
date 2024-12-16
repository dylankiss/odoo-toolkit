# 🧰 Odoo Toolkit

This toolkit contains a few useful tools for Odoo development. The tools are primarily aimed at Odoo employees.

The quickest way to get started is to install the tools using:

```console
$ pipx install odoo-toolkit
```

> [!TIP]
> You can run any command with the `--help` option to find out all options you can use.

**Usage**:

```console
$ otk [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--version`: Show the version and exit.
* `--install-completion`: Install completion for the current shell.
* `--show-completion`: Show completion for the current shell, to copy it or customize the installation.
* `--help`: Show this message and exit.


## Table of Contents

### Translations Related Commands

| Command                             | Purpose                                                                             |
| ----------------------------------- | ----------------------------------------------------------------------------------- |
| [`otk export-pot`](#otk-export-pot) | Export Odoo translation files (.pot) to each module's i18n folder.                  |
| [`otk create-po`](#otk-create-po)   | Create Odoo translation files (.po) according to their .pot files.                  |
| [`otk update-po`](#otk-update-po)   | Update Odoo translation files (.po) according to a new version of their .pot files. |
|                                     | [Available Languages](#available-languages)                                         |

### Development Server Commands

| Command               | Purpose                                      |
| --------------------- | -------------------------------------------- |
| [`otk dev`](#otk-dev) | Run an Odoo Development Server using Docker. |


## `otk export-pot`

**Export Odoo translation files (.pot) to each module's i18n folder.**

This command can autonomously start separate Odoo servers to export translatable terms for one or more modules. A separate server will be started for Community, Community (Localizations), Enterprise, and Enterprise (Localizations) modules with only the modules installed to be exported in that version.

When exporting the translations for `base`, we install all possible modules to ensure all manifest terms get exported in the `base.pot` files as well.

You can also export terms from your own running server using the `--no-start-server` option and optionally passing the correct arguments to reach your Odoo server.

> [!NOTE]
> Without any options specified, the command is supposed to run from within the parent directory where your `odoo` and `enterprise` repositories are checked out with these names. Your database is supposed to run on `localhost` using port `5432`, accessible without a password using your current user.
>
> Of course, all of this can be tweaked with the available options. 😉

**Usage**:

```console
$ otk export-pot [OPTIONS] MODULES...
```

**Arguments**:

* `MODULES...`: Export .pot files for these Odoo modules, or either `all`, `community`, or `enterprise`.  **[required]**

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


## `otk create-po`

**Create Odoo translation files (.po) according to their .pot files.**

This command will provide you with a clean .po file per language you specified for the given modules. It basically copies all entries from the .pot file in the module and completes the metadata with the right language information. All generated .po files will be saved in the respective modules' `i18n` directories.

> [!NOTE]
> Without any options specified, the command is supposed to run from within the parent directory where your `odoo` and `enterprise` repositories are checked out with these names.

**Usage**:

```console
$ otk create-po [OPTIONS] MODULES...
```

**Arguments**:

* `MODULES...`: Create .po files for these Odoo modules, or either `all`, `community`, or `enterprise`.  **[required]**

**Options**:

* [`-l, --languages LANG`](#available-languages): Create .po files for these languages, or `all`.  [default: `all`]
* `-c, --com-path PATH`: Specify the path to your Odoo Community repository.  [default: `odoo`]
* `-e, --ent-path PATH`: Specify the path to your Odoo Enterprise repository.  [default: `enterprise`]


## `otk update-po`

**Update Odoo translation files (.po) according to a new version of their .pot files.**

This command will update the .po files for the provided modules according to a new .pot file you might have exported in their `i18n` directory.

> [!NOTE]
> Without any options specified, the command is supposed to run from within the parent directory where your `odoo` and `enterprise` repositories are checked out with these names.

**Usage**:

```console
$ otk update-po [OPTIONS] MODULES...
```

**Arguments**:

* `MODULES...`: Update .po files for these Odoo modules, or either `all`, `community`, or `enterprise`.  **[required]**

**Options**:

* [`-l, --languages LANG`](#available-languages): Update .po files for these languages, or `all`.  [default: `all`]
* `-c, --com-path PATH`: Specify the path to your Odoo Community repository.  [default: `odoo`]
* `-e, --ent-path PATH`: Specify the path to your Odoo Enterprise repository.  [default: `enterprise`]

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


## 💻 Odoo Development Server

**Run an Odoo Development Server using Docker.**

The following commands allow you to automatically start and stop a fully configured Docker container to run your Odoo server(s) during development.

> [!IMPORTANT]
> These tools require [Docker Desktop](https://www.docker.com/products/docker-desktop/) to be installed on your system.

The Docker container is configured to resemble Odoo's CI or production servers and thus tries to eliminate discrepancies between your local system and the CI or production server.

**Usage**:

```console
$ otk dev [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--help`: Show this message and exit.


## `otk dev start`

**Start an Odoo Development Server using Docker and launch a terminal session into it.**

This command will start both a PostgreSQL container and an Odoo container containing your source code, located on your machine at the location specified by `-w`. Your specified workspace will be sourced in the container at the location `/code` and allows live code updates during local development.

You can choose to launch a container using Ubuntu 24.04 [`-u noble`] (default, recommended starting from version 18.0) or 22.04 [`-u jammy`] (for earlier versions).
The source code can be mapped using the "-w" option as the path to your workspace.

**Usage**:

```console
$ otk dev start [OPTIONS]
```

**Options**:

* `-w, --workspace PATH`: Specify the path to your development workspace that will be mounted in the container's "/code" directory.  [default: ~/code/odoo]
* `-u, --ubuntu-version [noble|jammy]`: Specify the Ubuntu version to run in this container.  [default: noble]
* `-p, --db-port INTEGER`: Specify the port on your local machine the PostgreSQL database should listen on.  [default: 5432]
* `--help`: Show this message and exit.

### `otk dev start-db`

Start a standalone PostgreSQL container for your Odoo databases.

**Usage**:

```console
$ otk dev start-db [OPTIONS]
```

**Options**:

* `-p, --port INTEGER`: Specify the port on your local machine the PostgreSQL database should listen on.  [default: 5432]
* `--help`: Show this message and exit.

### `otk dev stop`

Stop and delete all running containers of the Odoo Development Server.

**Usage**:

```console
$ otk dev stop [OPTIONS]
```

**Options**:

* `--help`: Show this message and exit.
