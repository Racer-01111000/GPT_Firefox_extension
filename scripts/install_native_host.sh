#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 /absolute/path/to/repo-root"
  exit 1
fi

REPO_ROOT="$1"
if [[ ! -d "$REPO_ROOT" ]]; then
  echo "Repo root does not exist: $REPO_ROOT"
  exit 1
fi
if [[ ! -d "$REPO_ROOT/.git" ]]; then
  echo "Repo root is not a git repo: $REPO_ROOT"
  exit 1
fi

HOST_DIR="$HOME/.local/share/kestrel-repo-bridge"
CONFIG_DIR="$HOME/.config/kestrel-repo-bridge"
STATE_DIR="$HOME/.local/state/kestrel-repo-bridge"
NATIVE_DIR="$HOME/.mozilla/native-messaging-hosts"

mkdir -p "$HOST_DIR" "$CONFIG_DIR" "$STATE_DIR" "$NATIVE_DIR"

install -m 0755 "$(dirname "$0")/../host/host.py" "$HOST_DIR/host.py"
install -m 0755 "$(dirname "$0")/../host/control_worker.py" "$HOST_DIR/control_worker.py"
cp "$(dirname "$0")/../host/policy.example.json" "$CONFIG_DIR/policy.json"

python3 - <<PY
import json
from pathlib import Path

repo_root = Path(r"$REPO_ROOT").resolve()
policy_path = Path(r"$CONFIG_DIR/policy.json")
policy = json.loads(policy_path.read_text(encoding="utf-8"))
policy["repo_root"] = repo_root.as_posix()
policy.setdefault("log_path", "~/.local/state/kestrel-repo-bridge/audit.jsonl")
policy.setdefault("rules", [])
policy.setdefault("command_rules", [])
policy_path.write_text(json.dumps(policy, indent=2) + "\n", encoding="utf-8")

state_path = Path(r"$STATE_DIR/state.json")
defaults = {
    "version": 2,
    "repo_root": repo_root.as_posix(),
    "memory": {
        "kv": {
            "canonical_repo": repo_root.as_posix(),
            "bridge_goal": "Replace OpenClaw/CC with a durable operator bridge."
        },
        "notes": []
    },
    "targets": {
        "HOST": {
            "kind": "local",
            "enabled": True,
            "default_cwd": ".",
            "root_helper": {
                "enabled": True,
                "path": "/usr/local/sbin/openclaw-root-helper",
                "mode": "unknown"
            }
        },
        "NODE_TEMP": {
            "kind": "ssh",
            "enabled": False,
            "ssh_host": "",
            "ssh_options": ["-o", "BatchMode=yes"],
            "default_cwd": "~",
            "root_helper": {
                "enabled": False,
                "path": "",
                "mode": "unknown"
            }
        },
        "NODE_PERM": {
            "kind": "ssh",
            "enabled": False,
            "ssh_host": "",
            "ssh_options": ["-o", "BatchMode=yes"],
            "default_cwd": "~",
            "root_helper": {
                "enabled": False,
                "path": "",
                "mode": "unknown"
            }
        }
    },
    "control": {
        "enabled": True,
        "remote": "origin",
        "branch": "main",
        "poll_interval_seconds": 10,
        "worker_pid": None,
        "worker_pid_path": str(Path(r"$STATE_DIR") / "control-worker.pid"),
        "worker_log_path": str(Path(r"$STATE_DIR") / "control-worker.log"),
        "worker_started_at": None,
        "last_sync_at": None,
        "last_prompt_id": None,
        "last_result_id": None
    },
    "recent_commands": []
}

if state_path.exists():
    state = json.loads(state_path.read_text(encoding="utf-8"))
else:
    state = {}
state["version"] = defaults["version"]
state["repo_root"] = repo_root.as_posix()
state.setdefault("memory", defaults["memory"])
state["memory"].setdefault("kv", {})
state["memory"].setdefault("notes", [])
state["memory"]["kv"]["canonical_repo"] = repo_root.as_posix()
state["memory"]["kv"].setdefault("bridge_goal", defaults["memory"]["kv"]["bridge_goal"])
state.setdefault("targets", defaults["targets"])
for name, entry in defaults["targets"].items():
    state["targets"].setdefault(name, entry)
    if "root_helper" in entry:
        state["targets"][name].setdefault("root_helper", entry["root_helper"])
state.setdefault("control", defaults["control"])
for key, value in defaults["control"].items():
    state["control"].setdefault(key, value)
state.setdefault("recent_commands", [])
state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
PY

cat > "$NATIVE_DIR/com.echocore.repo_bridge.json" <<EOJSON
{
  "name": "com.echocore.repo_bridge",
  "description": "Restricted local repo bridge",
  "path": "$HOST_DIR/host.py",
  "type": "stdio",
  "allowed_extensions": ["repo-bridge@echocorelabs.com"]
}
EOJSON

echo "Installed native host to: $HOST_DIR/host.py"
echo "Installed control worker to: $HOST_DIR/control_worker.py"
echo "Installed native host manifest to: $NATIVE_DIR/com.echocore.repo_bridge.json"
echo "Policy path: $CONFIG_DIR/policy.json"
echo "State path: $STATE_DIR/state.json"
echo "Next: reload the extension from about:debugging in Firefox."
