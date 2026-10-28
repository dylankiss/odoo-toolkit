# Development layer added by odoo-toolkit on top of Runbot's image.
USER root

# `otk dev run` checks these: the layout tells these images apart from the ones of odoo-toolkit 3.x, which used the same
# tags, and on Linux, the user ID must be yours.
LABEL io.github.dylankiss.odoo-toolkit.layout="2" io.github.dylankiss.odoo-toolkit.uid="${USERUID}"

# Show Odoo's logs right away, instead of in chunks.
ENV PYTHONUNBUFFERED=1

# Git and SSH, for Odoo modules that run Git commands, and the `python` command.
RUN set -x ; \
    apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends git openssh-client python-is-python3 \
    && rm -rf /var/lib/apt/lists/*

# Trust GitHub's SSH host keys, so the first `git fetch` doesn't stop at the host key verification.
# Source: https://api.github.com/meta
RUN printf '%s\n' \
    'github.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl' \
    'github.com ecdsa-sha2-nistp256 AAAAE2VjZHNhLXNoYTItbmlzdHAyNTYAAAAIbmlzdHAyNTYAAABBBEmKSENjQEezOmxkZMy7opKgwFB9nkt5YRrYMjNuG5N87uRgg6CLrbo5wAdT/y6v0mKV0U2w0WZ2YB/++Tpockg=' \
    'github.com ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQCj7ndNxQowgcQnjshcLrqPEiiphnt+VTTvDP6mHBL9j1aNUkY4Ue1gvwnGLVlOhGeYrnZaMgRK6+PKCUXaDbC7qtbW8gIkhL7aGCsOr/C56SJMy/BCZfxd1nWzAOxSDPgVsmerOBYfNqltV9/hWCqBywINIR+5dIg6JTJ72pcEpEjcYgXkE2YEFXV1JHnsKgbLWNlhScqb2UmyRkQyytRLtL+38TGxkxCflmO+5Z8CSSNY7GidjMIZ7Q4zMjA2n1nGrlTDkzwDCsw+wqFPGQA179cnfGWOWRVruj16z6XyvxvjJwbz0wQZ75XK5tKSb7FNyeIEs4TT4jk+S4dhPeAUC5y+bDYirYgM4GC7uEnztnZyaVWQ7B381AK4Qdrwt51ZqExKbQpTUNn+EjqoTwvqNj4kqx5QUCI0ThS/YkOxJCXmPUWZbhjpCg56i+2aB6CmK2JGhn57K5mj0MNdBXA4/WnwH6XoPWJzK5Nyu2zB3nAZp+S5hpQs+p1vN1/wsjk=' \
    > /etc/ssh/ssh_known_hosts

# The debugger, used by `otk dev run --debug`, and the file watchers Odoo's `--dev=reload` restarts with.
RUN python3 -m pip install --no-cache-dir debugpy pydevd-odoo inotify watchdog

# The folders `otk dev run` and the dev containers mount volumes on: the filestore, the Python bytecode cache, and
# Claude Code's login. Docker gives new volumes the owner of these folders, so Odoo and Claude Code can write in them.
RUN mkdir -p /home/${USERNAME}/.local/share/Odoo /home/${USERNAME}/.cache/pycache /home/${USERNAME}/.claude \
    && chown -R ${USERNAME}:${USERNAME} /home/${USERNAME}

USER ${USERNAME}
