from enum import Enum


class OdooRemote(str, Enum):
    """Odoo remote names used locally."""

    DEV = "dev"
    ORIGIN = "origin"


class OdooRepo(str, Enum):
    """Odoo repositories available for cloning."""

    DESIGN_THEMES = "design-themes"
    DOCUMENTATION = "documentation"
    ENTERPRISE = "enterprise"
    IAP_APPS = "iap-apps"
    INDUSTRY = "industry"
    INTERNAL = "internal"
    MOBILE = "mobile"
    ODOO = "odoo"
    ODOOFIN = "odoofin"
    O_SPREADSHEET = "o-spreadsheet"
    UPGRADE = "upgrade"
    UPGRADE_UTIL = "upgrade-util"


MULTI_BRANCH_REPOS = [
    OdooRepo.ODOO,
    OdooRepo.ENTERPRISE,
    OdooRepo.DESIGN_THEMES,
    OdooRepo.DOCUMENTATION,
    OdooRepo.INDUSTRY,
    OdooRepo.O_SPREADSHEET,
]
SINGLE_BRANCH_REPOS = [
    OdooRepo.ODOOFIN,
    OdooRepo.UPGRADE,
    OdooRepo.UPGRADE_UTIL,
    OdooRepo.INTERNAL,
    OdooRepo.IAP_APPS,
    OdooRepo.MOBILE,
]
ODOO_DEV_REPOS = [
    OdooRepo.ODOO,
    OdooRepo.ENTERPRISE,
    OdooRepo.DESIGN_THEMES,
    OdooRepo.INDUSTRY,
    OdooRepo.UPGRADE_UTIL
]
