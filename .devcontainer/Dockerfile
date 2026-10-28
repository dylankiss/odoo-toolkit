FROM mcr.microsoft.com/devcontainers/python:3.12

# Claude Code keeps its login in a volume mounted here. Docker gives a new volume the owner of this folder, so the user of
# the dev container can write in it.
RUN mkdir /home/vscode/.claude && chown vscode:vscode /home/vscode/.claude
