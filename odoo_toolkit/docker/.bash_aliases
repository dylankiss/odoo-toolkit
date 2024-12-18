com_addons_opt="--addons-path=odoo/addons"
ent_addons_opt="--addons-path=enterprise,odoo/addons"
upgrade_opt="--upgrade-path=upgrade-util/src,upgrade/migrations"
db_opt="--db_host=db --db_user=odoo --db_password=odoo"
limits_opt="--limit-time-cpu=99999999 --limit-time-real=99999999"

debug_cmd="PYDEVD_DISABLE_FILE_VALIDATION=1 python3 -m debugpy --wait-for-client --listen 0.0.0.0:5678"

# Odoo
alias o-bin="odoo/odoo-bin $db_opt $limits_opt"
alias o-bin-c="odoo/odoo-bin $db_opt $com_addons_opt $limits_opt"
alias o-bin-e="odoo/odoo-bin $db_opt $ent_addons_opt $limits_opt"

# Odoo Shell
alias o-bin-sh="odoo/odoo-bin shell $db_opt $limits_opt"
alias o-bin-sh-c="odoo/odoo-bin shell $db_opt $com_addons_opt $limits_opt"
alias o-bin-sh-e="odoo/odoo-bin shell $db_opt $ent_addons_opt $limits_opt"

# Odoo Upgrade
alias o-bin-up="odoo/odoo-bin $db_opt $upgrade_opt $limits_opt"
alias o-bin-up-c="odoo/odoo-bin $db_opt $com_addons_opt $upgrade_opt $limits_opt"
alias o-bin-up-e="odoo/odoo-bin $db_opt $ent_addons_opt $upgrade_opt $limits_opt"

# Odoo Debug
alias o-bin-deb="$debug_cmd odoo/odoo-bin $db_opt $limits_opt"
alias o-bin-deb-c="$debug_cmd odoo/odoo-bin $db_opt $com_addons_opt $limits_opt"
alias o-bin-deb-e="$debug_cmd odoo/odoo-bin $db_opt $ent_addons_opt $limits_opt"

# Odoo Debug Upgrade
alias o-bin-deb-up="$debug_cmd odoo/odoo-bin $db_opt $upgrade_opt $limits_opt"
alias o-bin-deb-up-c="$debug_cmd odoo/odoo-bin $db_opt $com_addons_opt $upgrade_opt $limits_opt"
alias o-bin-deb-up-e="$debug_cmd odoo/odoo-bin $db_opt $ent_addons_opt $upgrade_opt $limits_opt"

# Override PostgreSQL commands to use the external database
alias createdb="PGPASSWORD=odoo createdb --host=db --username=odoo"
alias dropdb="PGPASSWORD=odoo dropdb --host=db --username=odoo"
alias pgbench="PGPASSWORD=odoo pgbench --host=db --username=odoo"
alias pg_dump="PGPASSWORD=odoo pg_dump --host=db --username=odoo"
alias psql="PGPASSWORD=odoo psql --host=db --username=odoo"
