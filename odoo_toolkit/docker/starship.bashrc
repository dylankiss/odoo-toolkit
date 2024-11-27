# Save the history after each command in a directory that can be persisted by Docker
export HISTFILE="$HOME/.bash_history_data"
PROMPT_COMMAND="history -a; history -n"

# Launch Starship
eval "$(starship init bash)"
