# `otk`

🧰 Odoo Toolkit

This toolkit contains several useful tools for Odoo development.

**Usage**:

```console
$ otk [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--help`: Show this message and exit.

**Commands**:

* `export-pot`: Export Odoo translation files (.pot) to...
* `update-po`: Update Odoo translation files (.po)...

## `otk export-pot`

Export Odoo translation files (.pot) to each module's i18n folder.

With the default settings, it will start a new Odoo Enterprise server and install all modules in order to export
all possible terms that can come from other modules in the module(s) you want to export.

If you don't want this behavior, start an Odoo server manually and provide the corresponding options to the
command.

**Usage**:

```console
$ otk export-pot [OPTIONS] MODULES...
```

**Arguments**:

* `MODULES...`: The Odoo modules to export or either "all", "community", or "enterprise"  [required]

**Options**:

* `--start-server / --no-start-server`: Start an Odoo server automatically  [default: start-server]
* `-c, --com-path PATH`: The path to the Odoo Community repo  [default: odoo]
* `-e, --ent-path PATH`: The path to the Odoo Enterprise repo  [default: enterprise]
* `-u, --username TEXT`: The Odoo username  [default: admin]
* `-p, --password TEXT`: The Odoo password  [default: admin]
* `--host TEXT`: The Odoo hostname  [default: localhost]
* `--port INTEGER`: The Odoo port  [default: 8069]
* `-d, --database TEXT`: The PostgreSQL database  [default: \_\_export\_pot\_db\_\_]
* `--db-host TEXT`: The PostgreSQL hostname  [default: localhost]
* `--db-port INTEGER`: The PostgreSQL port  [default: 5432]
* `--db-username TEXT`: The PostgreSQL username
* `--db-password TEXT`: The PostgreSQL password
* `--help`: Show this message and exit.

## `otk update-po`

Update Odoo translation files (.po) according to a new version of its .pot file.

**Usage**:

```console
$ otk update-po [OPTIONS] MODULES...
```

**Arguments**:

* `MODULES...`: The Odoo modules to update or either "all", "community", or "enterprise"  [required]

**Options**:

* `-c, --com-path PATH`: The path to the Odoo Community repo  [default: odoo]
* `-e, --ent-path PATH`: The path to the Odoo Enterprise repo  [default: enterprise]
* `--help`: Show this message and exit.
