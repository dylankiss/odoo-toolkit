# Dev containers have no route out except the egress proxy

AI agents work in the dev containers and can read the whole multiverse and every database of the shared PostgreSQL, so
the dev container sits on an internal Docker network with no route out. Its only exit is the egress proxy, which lets the
allowed domains through (Anthropic's hosts, what Visual Studio Code needs and Odoo's own hosts, by default). Odoo that needs the internet
runs in an Odoo container (`otk dev run`) and is debugged from the user's machine, not from the dev container.

We rejected limiting only Claude Code's commands with its sandbox: it can't cover code Claude writes that runs outside
the sandbox, depends on user namespaces the container's hardening may block, and leaves the container's network open. A
rule for a single process isn't possible without extra capabilities, since Claude and Odoo share one network.

## Consequences

- The dev container also gets the user's Claude Code skills, agents, commands and plugins, read-only, and a managed
  settings file that enables their plugins. Nothing else of their configuration (settings, hooks, login, conversations)
  goes in.

- Odoo started in the dev container has no internet. Use `otk dev run` for that.
- Code an agent wrote still runs with internet when the user starts it in an Odoo container, and the shared PostgreSQL
  stays readable by agents: review what they wrote first, and keep data you can't share out of the databases.
- PostgreSQL has no route out either, since it runs as a superuser and anything that connects can make it run programs
  (`COPY ... TO PROGRAM`) that reach the internet. It is only on networks without a route out, and a small forwarder
  publishes it on the user's machine and on the open network, as `otk-postgres` there.
- The Anthropic connection carries what Claude Code reads. It is the one accepted channel.
