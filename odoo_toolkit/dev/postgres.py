"""Keep the `odoo` role of the shared PostgreSQL a regular one, with `postgres` as its superuser.

Since 2026-10, Odoo refuses to connect as a superuser: one can make PostgreSQL run programs (`COPY ... TO PROGRAM`) and
read or write its files. New volumes get `postgres` as the superuser from the image, and `odoo` from here. The volumes
of earlier versions of this toolkit have `odoo` as their superuser, which PostgreSQL can't make a regular role, since
it created the cluster. So it becomes `postgres`, a new `odoo` takes its place, and that one gets what the old one owned.
"""

import subprocess

from typer import Exit

from odoo_toolkit.common import TransientProgress, console, print_error

from .runtime import DB_HOST

ADMIN_ROLE = "postgres"
ODOO_ROLE = "odoo"
# Only exists while the volume is migrated: PostgreSQL can't rename the role you're connected as.
MIGRATION_ROLE = "otk_migration"
ROLES = (ADMIN_ROLE, MIGRATION_ROLE, ODOO_ROLE)

# It creates the extensions Odoo needs itself, since PostgreSQL trusts them (see `compose.yaml` for `vector`).
CREATE_ODOO_ROLE = f"CREATE ROLE {ODOO_ROLE} LOGIN CREATEDB PASSWORD '{ODOO_ROLE}';"

# Give `odoo` what `postgres` owns in a database, except what belongs to an extension. It commits as it goes, since one
# transaction can't hold a lock on every table of a big database.
REASSIGN_OBJECTS = r"""
DO $$
DECLARE
    old_owner oid := 'postgres'::regrole;
    obj record;
    done int := 0;
BEGIN
    FOR obj IN
        SELECT n.oid, n.nspname FROM pg_namespace n
        WHERE n.nspowner = old_owner AND n.nspname <> 'information_schema' AND n.nspname NOT LIKE 'pg\_%'
    LOOP
        EXECUTE format('ALTER SCHEMA %I OWNER TO odoo', obj.nspname);
    END LOOP;
    -- Their indexes, TOAST tables and the sequences of their columns follow.
    FOR obj IN
        SELECT c.oid::regclass AS name, c.relkind FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE c.relowner = old_owner AND c.relkind IN ('r', 'p', 'v', 'm', 'f', 'c')
            AND n.nspname <> 'information_schema' AND n.nspname NOT LIKE 'pg\_%'
            AND NOT EXISTS (
                SELECT FROM pg_depend d
                WHERE d.classid = 'pg_class'::regclass AND d.objid = c.oid AND d.deptype = 'e'
            )
    LOOP
        EXECUTE format(
            'ALTER %s %s OWNER TO odoo',
            CASE obj.relkind
                WHEN 'v' THEN 'VIEW' WHEN 'm' THEN 'MATERIALIZED VIEW' WHEN 'f' THEN 'FOREIGN TABLE' WHEN 'c' THEN 'TYPE'
                ELSE 'TABLE'
            END,
            obj.name
        );
        done := done + 1;
        IF done % 100 = 0 THEN
            COMMIT;
        END IF;
    END LOOP;
    COMMIT;
    -- The sequences that aren't a column's.
    FOR obj IN
        SELECT c.oid::regclass AS name FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE c.relowner = old_owner AND c.relkind = 'S'
            AND n.nspname <> 'information_schema' AND n.nspname NOT LIKE 'pg\_%'
            AND NOT EXISTS (
                SELECT FROM pg_depend d
                WHERE d.classid = 'pg_class'::regclass AND d.objid = c.oid AND d.deptype IN ('a', 'i', 'e')
            )
    LOOP
        EXECUTE format('ALTER SEQUENCE %s OWNER TO odoo', obj.name);
    END LOOP;
    FOR obj IN
        SELECT p.oid::regprocedure AS name FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE p.proowner = old_owner AND n.nspname <> 'information_schema' AND n.nspname NOT LIKE 'pg\_%'
            AND NOT EXISTS (
                SELECT FROM pg_depend d
                WHERE d.classid = 'pg_proc'::regclass AND d.objid = p.oid AND d.deptype = 'e'
            )
    LOOP
        EXECUTE format('ALTER ROUTINE %s OWNER TO odoo', obj.name);
    END LOOP;
    -- Composite types are done with the relations, and multiranges follow their range.
    FOR obj IN
        SELECT t.oid::regtype AS name, t.typtype FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace
        WHERE t.typowner = old_owner AND t.typtype IN ('d', 'e', 'r')
            AND n.nspname <> 'information_schema' AND n.nspname NOT LIKE 'pg\_%'
            AND NOT EXISTS (
                SELECT FROM pg_depend d
                WHERE d.classid = 'pg_type'::regclass AND d.objid = t.oid AND d.deptype = 'e'
            )
    LOOP
        EXECUTE format(
            'ALTER %s %s OWNER TO odoo', CASE obj.typtype WHEN 'd' THEN 'DOMAIN' ELSE 'TYPE' END, obj.name
        );
    END LOOP;
END
$$;
"""


class PostgresError(Exception):
    """A command in the PostgreSQL container failed."""


def psql(sql: str, *, user: str, database: str = "postgres") -> str:
    """Run SQL in the shared PostgreSQL, through its own socket.

    :param sql: The statements, which stop at the first error.
    :param user: The role to connect as.
    :param database: The database to connect to.
    :return: What the statements returned, a row per line, with `|` between columns.
    :raises PostgresError: If it failed.
    """
    result = subprocess.run(
        [
            "docker",
            "exec",
            "--interactive",
            DB_HOST,
            "psql",
            "--no-psqlrc",
            "--quiet",
            "--tuples-only",
            "--no-align",
            "--set",
            "ON_ERROR_STOP=1",
            "--username",
            user,
            "--dbname",
            database,
        ],
        input=sql,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise PostgresError(result.stderr.strip() or f"psql failed as {user} in {database}.")
    return result.stdout.strip()


def get_roles() -> tuple[str, dict[str, bool]]:
    """Find a superuser to connect as, and which of the roles of this toolkit exist.

    :return: The superuser, and whether each role that exists is a superuser.
    :raises PostgresError: If no superuser can connect.
    """
    query = "SELECT rolname, rolsuper FROM pg_roles WHERE rolname IN ('postgres', 'otk_migration', 'odoo');"
    error = PostgresError("No superuser can connect to PostgreSQL.")
    for user in ROLES:
        try:
            output = psql(query, user=user)
        except PostgresError as e:
            error = e
            continue
        roles = {name: is_super == "t" for name, _, is_super in (line.partition("|") for line in output.splitlines())}
        if roles.get(user):
            return user, roles
    raise error


def ensure_roles() -> None:
    """Make sure `odoo` is a regular role that can create databases, and `postgres` the superuser.

    It migrates the volumes of earlier versions of this toolkit, and finishes a migration that was interrupted.

    :raises Exit: If it failed.
    """
    try:
        admin, roles = get_roles()
        if ODOO_ROLE not in roles:
            # A new volume.
            psql(CREATE_ODOO_ROLE, user=admin)
            return
        if MIGRATION_ROLE not in roles and not roles[ODOO_ROLE]:
            return
        with TransientProgress() as progress:
            progress.add_task("Making the odoo role of PostgreSQL a regular one", total=None)
            _migrate(admin, roles)
    except PostgresError as e:
        print_error(
            "Setting up the roles of PostgreSQL failed. Run the command again to retry: it continues where it stopped.",
            str(e),
        )
        raise Exit(1) from e
    console.print(
        f"PostgreSQL's superuser is now [b]{ADMIN_ROLE}[/b], and Odoo connects as [b]{ODOO_ROLE}[/b], a regular role "
        "that owns your databases, since Odoo refuses superusers. Restart the Odoo servers that are running, if any.\n",
    )


def _migrate(admin: str, roles: dict[str, bool]) -> None:
    """Make `odoo` a regular role, and give it the databases `postgres` owns. Each step can run again.

    :param admin: A superuser to connect as.
    :param roles: Whether each role of this toolkit that exists is a superuser.
    """
    if MIGRATION_ROLE not in roles:
        psql(f"CREATE ROLE {MIGRATION_ROLE} LOGIN SUPERUSER;", user=admin)
    if ADMIN_ROLE not in roles:
        # `odoo` created the cluster, so it can't stop being a superuser: it becomes `postgres`. The sessions it had are
        # stopped, since they would stay superusers: Odoo connects again as the new `odoo`.
        psql(
            "BEGIN;\n"
            f"ALTER ROLE {ODOO_ROLE} RENAME TO {ADMIN_ROLE};\n"
            f"ALTER ROLE {ADMIN_ROLE} PASSWORD '{ADMIN_ROLE}';\n"
            f"{CREATE_ODOO_ROLE}\n"
            "COMMIT;\n"
            "SELECT count(pg_terminate_backend(pid)) FROM pg_stat_activity\n"
            "WHERE usesysid = 'postgres'::regrole::oid AND pid <> pg_backend_pid();",
            user=MIGRATION_ROLE,
        )
    elif roles.get(ODOO_ROLE):
        psql(f"ALTER ROLE {ODOO_ROLE} NOSUPERUSER CREATEDB;", user=ADMIN_ROLE)
    databases = psql(
        "SELECT datname FROM pg_database WHERE datallowconn AND datdba = 'postgres'::regrole::oid\n"
        "AND NOT datistemplate AND datname <> 'postgres';",
        user=ADMIN_ROLE,
    ).splitlines()
    for database in databases:
        # The database goes last, so one that is still `postgres`'s isn't done yet.
        psql(
            f"{REASSIGN_OBJECTS}\nSELECT format('ALTER DATABASE %I OWNER TO odoo', current_database()) \\gexec",
            user=ADMIN_ROLE,
            database=database,
        )
    psql(f"DROP ROLE {MIGRATION_ROLE};", user=ADMIN_ROLE)
