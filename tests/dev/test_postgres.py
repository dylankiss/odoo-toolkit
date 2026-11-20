import subprocess

import pytest
from typer import Exit

from odoo_toolkit.dev import postgres
from odoo_toolkit.dev.postgres import ADMIN_ROLE, MIGRATION_ROLE, ODOO_ROLE, PostgresError


class FakePostgres:
    """Stand in for `psql` in the PostgreSQL container, keeping track of the roles and who owns the databases."""

    def __init__(self, roles: dict[str, bool], databases: dict[str, str], *, fail_on: str | None = None) -> None:
        self.roles = roles
        # The databases besides `postgres` and `template1`, and their owners.
        self.databases = databases
        self.fail_on = fail_on
        self.calls: list[tuple[str, str, str]] = []

    def psql(self, sql: str, *, user: str, database: str = "postgres") -> str:  # noqa: C901
        if user not in self.roles:
            msg = f'FATAL:  role "{user}" does not exist'
            raise PostgresError(msg)
        if self.fail_on and self.fail_on in sql:
            msg = "ERROR:  boom"
            raise PostgresError(msg)
        self.calls.append((user, database, sql))
        if sql.startswith("SELECT rolname"):
            return "\n".join(f"{name}|{'t' if is_super else 'f'}" for name, is_super in self.roles.items())
        if sql.startswith(f"CREATE ROLE {MIGRATION_ROLE}"):
            self.roles[MIGRATION_ROLE] = True
        elif sql.startswith(f"CREATE ROLE {ODOO_ROLE}"):
            self.roles[ODOO_ROLE] = False
        elif sql.startswith("BEGIN;"):
            assert user != ODOO_ROLE
            self.roles[ADMIN_ROLE] = self.roles.pop(ODOO_ROLE)
            self.roles[ODOO_ROLE] = False
            self.databases = {
                name: ADMIN_ROLE if owner == ODOO_ROLE else owner for name, owner in self.databases.items()
            }
        elif sql.startswith(f"ALTER ROLE {ODOO_ROLE} NOSUPERUSER"):
            self.roles[ODOO_ROLE] = False
        elif sql.startswith("SELECT datname"):
            return "\n".join(name for name, owner in self.databases.items() if owner == ADMIN_ROLE)
        elif "ALTER DATABASE" in sql:
            self.databases[database] = ODOO_ROLE
        elif sql.startswith(f"DROP ROLE {MIGRATION_ROLE}"):
            del self.roles[MIGRATION_ROLE]
        return ""

    def statements(self) -> list[str]:
        return [
            sql.split()[0] + " " + sql.split()[1] for _, _, sql in self.calls if not sql.startswith("SELECT rolname")
        ]


@pytest.fixture
def no_output(monkeypatch: pytest.MonkeyPatch) -> None:
    def print_nothing(*_: object, **__: object) -> None:
        pass

    monkeypatch.setattr(postgres.console, "print", print_nothing)


def install(monkeypatch: pytest.MonkeyPatch, fake: FakePostgres) -> FakePostgres:
    monkeypatch.setattr(postgres, "psql", fake.psql)
    return fake


@pytest.mark.usefixtures("no_output")
def test_a_new_volume_gets_a_regular_odoo_role(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = install(monkeypatch, FakePostgres({ADMIN_ROLE: True}, {}))

    postgres.ensure_roles()

    assert fake.roles == {ADMIN_ROLE: True, ODOO_ROLE: False}
    assert fake.statements() == ["CREATE ROLE"]


@pytest.mark.usefixtures("no_output")
def test_a_migrated_volume_is_left_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = install(monkeypatch, FakePostgres({ADMIN_ROLE: True, ODOO_ROLE: False}, {"db": ODOO_ROLE}))

    postgres.ensure_roles()

    assert fake.statements() == []


@pytest.mark.usefixtures("no_output")
def test_an_old_volume_makes_odoo_a_regular_role_that_owns_its_databases(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = install(monkeypatch, FakePostgres({ODOO_ROLE: True}, {"master-1": ODOO_ROLE, "19-crm": ODOO_ROLE}))

    postgres.ensure_roles()

    assert fake.roles == {ADMIN_ROLE: True, ODOO_ROLE: False}
    assert fake.databases == {"master-1": ODOO_ROLE, "19-crm": ODOO_ROLE}
    # The superuser that created the cluster can't be renamed while connected as itself.
    assert next(user for user, _, sql in fake.calls if sql.startswith("BEGIN;")) == MIGRATION_ROLE
    assert all(postgres.REASSIGN_OBJECTS in sql for _, database, sql in fake.calls if database in fake.databases)


def test_an_interrupted_migration_continues_where_it_stopped(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = install(
        monkeypatch,
        FakePostgres({ODOO_ROLE: True}, {"a": ODOO_ROLE, "b": ODOO_ROLE}, fail_on="ALTER DATABASE"),
    )

    with pytest.raises(Exit):
        postgres.ensure_roles()

    assert "continues where it stopped" in capsys.readouterr().err
    assert fake.roles == {MIGRATION_ROLE: True, ADMIN_ROLE: True, ODOO_ROLE: False}
    fake.fail_on = None
    fake.calls.clear()

    postgres.ensure_roles()

    assert fake.roles == {ADMIN_ROLE: True, ODOO_ROLE: False}
    assert fake.databases == {"a": ODOO_ROLE, "b": ODOO_ROLE}
    assert not any(sql.startswith(("BEGIN;", f"CREATE ROLE {MIGRATION_ROLE}")) for _, _, sql in fake.calls)


@pytest.mark.usefixtures("no_output")
def test_a_superuser_odoo_next_to_postgres_only_loses_superuser(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = install(monkeypatch, FakePostgres({ADMIN_ROLE: True, ODOO_ROLE: True}, {"db": ODOO_ROLE}))

    postgres.ensure_roles()

    assert fake.roles == {ADMIN_ROLE: True, ODOO_ROLE: False}
    # Its databases are its own already.
    assert fake.databases == {"db": ODOO_ROLE}


def test_no_superuser_stops_with_why(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    install(monkeypatch, FakePostgres({}, {}))

    with pytest.raises(Exit):
        postgres.ensure_roles()

    assert "does not exist" in capsys.readouterr().err


def test_psql_runs_the_sql_in_the_container(monkeypatch: pytest.MonkeyPatch) -> None:
    runs: list[tuple[list[str], str]] = []

    def run(cmd: list[str], *, input: str, **_: object) -> subprocess.CompletedProcess[str]:  # noqa: A002
        runs.append((cmd, input))
        return subprocess.CompletedProcess(cmd, 0, "odoo|f\n", "")

    monkeypatch.setattr(postgres.subprocess, "run", run)

    assert postgres.psql("SELECT 1;", user=ADMIN_ROLE, database="db") == "odoo|f"
    cmd, sql = runs[0]
    assert cmd[:4] == ["docker", "exec", "--interactive", "otk-postgres"]
    assert cmd[-4:] == ["--username", ADMIN_ROLE, "--dbname", "db"]
    assert "ON_ERROR_STOP=1" in cmd
    assert sql == "SELECT 1;"


@pytest.mark.parametrize(
    ("stderr", "error"),
    [
        ('psql: FATAL:  role "postgres" does not exist\n', 'psql: FATAL:  role "postgres" does not exist'),
        ("", "psql failed as postgres in postgres."),
    ],
)
def test_psql_raises_what_failed(monkeypatch: pytest.MonkeyPatch, stderr: str, error: str) -> None:
    def run(cmd: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 2, "", stderr)

    monkeypatch.setattr(postgres.subprocess, "run", run)

    with pytest.raises(PostgresError) as e:
        postgres.psql("SELECT 1;", user=ADMIN_ROLE)
    assert str(e.value) == error
