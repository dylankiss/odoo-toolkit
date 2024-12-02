# 🧰 Odoo Toolkit

This toolkit contains several useful tools for Odoo development.

**Usage**:

```console
$ otk [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--help`: Show this message and exit.

**Commands**:

* [`export-pot`](#otk-export-pot): Export Odoo translation files (.pot) to...
* [`create-po`](#otk-create-po): Create Odoo translation files (.po)...
* [`update-po`](#otk-update-po): Update Odoo translation files (.po)...
* [`dev`](#otk-dev): 💻 Odoo Development Server

## `otk export-pot`

Export Odoo translation files (.pot) to each module's i18n folder.

With the default settings, it will start an Odoo server for Community and Enterprise terms separately and install
the modules to export in the corresponding server. Some Community modules require extra Enterprise modules to be
installed that override some terms. These modules will be exported from the Enterprise server as well with the
extra modules installed.

When exporting "base", it will install all modules in Community and Enterprise to have the terms from their
manifest files exported in there.

If you want to export from your own running server, you can provide the corresponding options to the command.

**Usage**:

```console
$ otk export-pot [OPTIONS] MODULES...
```

**Arguments**:

* `MODULES...`: Export .pot files for these Odoo modules, or either "all", "community", or "enterprise".  [required]

**Options**:

* `--start-server / --no-start-server`: Start an Odoo server automatically.  [default: start-server]
* `--full-install / --no-full-install`: Install every available Odoo module.  [default: no-full-install]
* `-c, --com-path PATH`: Specify the path to your Odoo Community repository.  [default: odoo]
* `-e, --ent-path PATH`: Specify the path to your Odoo Enterprise repository.  [default: enterprise]
* `-u, --username TEXT`: Specify the username to log in to Odoo.  [default: admin]
* `-p, --password TEXT`: Specify the password to log in to Odoo.  [default: admin]
* `--host TEXT`: Specify the hostname of your Odoo server.  [default: localhost]
* `--port INTEGER`: Specify the port of your Odoo server.  [default: 8069]
* `-d, --database TEXT`: Specify the PostgreSQL database name used by Odoo.  [default: \__export_pot_db__]
* `--db-host TEXT`: Specify the PostgreSQL server's hostname.  [default: localhost]
* `--db-port INTEGER`: Specify the PostgreSQL server's port.  [default: 5432]
* `--db-username TEXT`: Specify the PostgreSQL server's username.
* `--db-password TEXT`: Specify the PostgreSQL user's password.
* `--help`: Show this message and exit.

## `otk create-po`

Create Odoo translation files (.po) according to their .pot files.

**Usage**:

```console
$ otk create-po [OPTIONS] MODULES...
```

**Arguments**:

* `MODULES...`: Create .po files for these Odoo modules, or either "all", "community", or "enterprise".  [required]

**Options**:

* `-l, --languages [all|am_ET|ar|ar_SY|az|be|bg|bn_IN|bs|ca_ES|cs_CZ|da_DK|de|de_CH|el_GR|en_AU|en_CA|en_GB|en_IN|en_NZ|es|es_419|es_AR|es_BO|es_CL|es_CO|es_CR|es_DO|es_EC|es_GT|es_MX|es_PA|es_PE|es_PY|es_UY|es_VE|et|eu_ES|fa|fi|fr|fr_BE|fr_CA|fr_CH|gl|gu|he|hi|hr|hu|id|it|ja|ka|kab|km|ko_KP|ko_KR|lb|lo|lt|lv|mk|ml|mn_MN|ms|my|nb_NO|nl|nl_BE|pl|pt|pt_AO|pt_BR|ro|ru|sk|sl|sq|sr@Cyrl|sr@latin|sv|sw|te|th|tl|tr|uk|vi|zh_CH|zh_HK|zh_TW]`: Create .po files for these languages, or "all".  [default: all]
* `-c, --com-path PATH`: Specify the path to your Odoo Community repository.  [default: odoo]
* `-e, --ent-path PATH`: Specify the path to your Odoo Enterprise repository.  [default: enterprise]
* `--help`: Show this message and exit.

## `otk update-po`

Update Odoo translation files (.po) according to a new version of their .pot files.

**Usage**:

```console
$ otk update-po [OPTIONS] MODULES...
```

**Arguments**:

* `MODULES...`: Update .po files for these Odoo modules, or either "all", "community", or "enterprise".  [required]

**Options**:

* `-l, --languages [all|am_ET|ar|ar_SY|az|be|bg|bn_IN|bs|ca_ES|cs_CZ|da_DK|de|de_CH|el_GR|en_AU|en_CA|en_GB|en_IN|en_NZ|es|es_419|es_AR|es_BO|es_CL|es_CO|es_CR|es_DO|es_EC|es_GT|es_MX|es_PA|es_PE|es_PY|es_UY|es_VE|et|eu_ES|fa|fi|fr|fr_BE|fr_CA|fr_CH|gl|gu|he|hi|hr|hu|id|it|ja|ka|kab|km|ko_KP|ko_KR|lb|lo|lt|lv|mk|ml|mn_MN|ms|my|nb_NO|nl|nl_BE|pl|pt|pt_AO|pt_BR|ro|ru|sk|sl|sq|sr@Cyrl|sr@latin|sv|sw|te|th|tl|tr|uk|vi|zh_CH|zh_HK|zh_TW]`: Update .po files for these languages, or "all".  [default: all]
* `-c, --com-path PATH`: Specify the path to your Odoo Community repository.  [default: odoo]
* `-e, --ent-path PATH`: Specify the path to your Odoo Enterprise repository.  [default: enterprise]
* `--help`: Show this message and exit.

## `otk dev`

💻 Odoo Development Server

Run an Odoo Development Server using Docker.

**Usage**:

```console
$ otk dev [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--help`: Show this message and exit.

**Commands**:

* `start`: Start an Odoo Development Server using...
* `start-db`: Start a standalone PostgreSQL container...
* `stop`: Stop all running containers of the Odoo...

### `otk dev start`

Start an Odoo Development Server using Docker and launch a terminal session into it.

This command will start both a PostgreSQL container and an Odoo container containing your source code.
You can choose to launch a container using Ubuntu 24.04  (default) or 22.04  using "-u".
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

Stop all running containers of the Odoo Development Server.

**Usage**:

```console
$ otk dev stop [OPTIONS]
```

**Options**:

* `--help`: Show this message and exit.
