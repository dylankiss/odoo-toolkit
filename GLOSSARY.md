# odoo-toolkit

Tools to develop Odoo: a multiverse with a version directory per Odoo version, and the Docker environments that run it.
This glossary covers the isolation of the dev containers.

## Language

**Dev container**:
The Visual Studio Code container of one Odoo version, where AI agents work. It sees the multiverse and what the user
chooses to mount read-only from their Claude Code configuration (skills, agents, commands, plugins), and has no route out
except through the egress proxy.
_Avoid_: Sandbox, agent container

**Odoo container**:
The container of one `otk dev run`, which runs Odoo on the open network, with full internet access.
_Avoid_: Dev container (that is where agents work, not where Odoo runs with internet)

**Egress proxy**:
The one shared proxy a dev container reaches the internet through, which only lets the allowed domains through.
_Avoid_: Firewall, gateway

**Allowed domains**:
The list in the user's odoo-toolkit configuration that the egress proxy lets through, shared by every dev container.
The Anthropic connection is the only channel out the defaults accept for Claude Code itself.
_Avoid_: Whitelist, allowlist file
