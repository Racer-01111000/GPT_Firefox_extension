# Kestrel Repo Bridge v0.3

Adds:
- persistent native connection in Firefox
- durable host-side state at `~/.local/state/kestrel-repo-bridge/state.json`
- target routing: `HOST`, `NODE_TEMP`, `NODE_PERM`
- SSH transport for node targets via configured `ssh_host`
- root-helper plumbing for HOST via `/usr/local/sbin/openclaw-root-helper`
- sidebar memory save/view

Notes:
- `NODE_TEMP` and `NODE_PERM` are disabled until you set their `ssh_host` and enable them in `state.json`.
- root-helper mode defaults to `unknown`; inspect the helper before using elevated commands.
