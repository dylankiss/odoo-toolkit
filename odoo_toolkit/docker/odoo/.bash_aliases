case "$(hostname)" in
    "odoo-17") export ODOO_PORT="8070" ;;
    "odoo-18") export ODOO_PORT="8071" ;;
    "odoo-18-1-18-4") export ODOO_PORT="8072" ;;
    "odoo-19") export ODOO_PORT="8073" ;;
    *)           export ODOO_PORT="8074" ;;
esac

port_opt="--http-port=$ODOO_PORT --http-interface=0.0.0.0"
com_addons_opt="--addons-path=odoo/addons"
ent_addons_opt="--addons-path=enterprise,odoo/addons"
upgrade_opt="--upgrade-path=upgrade-util/src,upgrade/migrations"
limits_opt="--limit-time-cpu=99999999 --limit-time-real=99999999"

debug_cmd="PYDEVD_DISABLE_FILE_VALIDATION=1 python3 -m debugpy --wait-for-client --listen 0.0.0.0:5678"

# Odoo
alias o-bin="odoo/odoo-bin $port_opt $limits_opt"
alias o-bin-c="odoo/odoo-bin $port_opt $com_addons_opt $limits_opt"
alias o-bin-e="odoo/odoo-bin $port_opt $ent_addons_opt $limits_opt"

# Odoo Shell
alias o-bin-sh="odoo/odoo-bin shell $port_opt $limits_opt"
alias o-bin-sh-c="odoo/odoo-bin shell $port_opt $com_addons_opt $limits_opt"
alias o-bin-sh-e="odoo/odoo-bin shell $port_opt $ent_addons_opt $limits_opt"

# Odoo Upgrade
alias o-bin-up="odoo/odoo-bin $port_opt $upgrade_opt $limits_opt"
alias o-bin-up-c="odoo/odoo-bin $port_opt $com_addons_opt $upgrade_opt $limits_opt"
alias o-bin-up-e="odoo/odoo-bin $port_opt $ent_addons_opt $upgrade_opt $limits_opt"

# Odoo Debug
alias o-bin-deb="$debug_cmd odoo/odoo-bin $port_opt $limits_opt"
alias o-bin-deb-c="$debug_cmd odoo/odoo-bin $port_opt $com_addons_opt $limits_opt"
alias o-bin-deb-e="$debug_cmd odoo/odoo-bin $port_opt $ent_addons_opt $limits_opt"

# Odoo Debug Upgrade
alias o-bin-deb-up="$debug_cmd odoo/odoo-bin $port_opt $upgrade_opt $limits_opt"
alias o-bin-deb-up-c="$debug_cmd odoo/odoo-bin $port_opt $com_addons_opt $upgrade_opt $limits_opt"
alias o-bin-deb-up-e="$debug_cmd odoo/odoo-bin $port_opt $ent_addons_opt $upgrade_opt $limits_opt"

# Odoo Documentation
# Use `LANGUAGE=fr o-make` to build the documentation in a specific language.
alias o-make="VIRTUAL_ENV=/venv/odoo-doc make"
