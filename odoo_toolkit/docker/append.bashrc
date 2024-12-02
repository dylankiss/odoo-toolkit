# Save the history after each command in a directory that can be persisted by Docker
export PROMPT_COMMAND="history -a; history -n"
export HISTFILE="$HOME/.bash_history_data/.bash_history"

# Launch Starship
eval "$(starship init bash)"
