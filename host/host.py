#!/usr/bin/env python3
import errno
import fcntl
import hashlib
import json
import os
import pathlib
import pty
import re
import secrets
import shlex
import signal
import struct
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

HOST_VERSION = "0.4.0"
HOST_NAME = "com.echocore.repo_bridge"
CONFIG_DIR = os.path.expanduser("~/.config/kestrel-repo-bridge")
STATE_DIR = os.path.expanduser("~/.local/state/kestrel-repo-bridge")
DEFAULT_POLICY_PATH = os.environ.get("KESTREL_REPO_BRIDGE_POLICY", os.path.join(CONFIG_DIR, "policy.json"))
DEFAULT_STATE_PATH = os.environ.get("KESTREL_REPO_BRIDGE_STATE", os.path.join(STATE_DIR, "state.json"))

SESSION_RULES: List[dict] = []
PENDING_REQUESTS: Dict[str, dict] = {}
TERMINAL_SESSIONS: Dict[str, dict] = {}


class HostError(Exception):
    def __init__(self, code: str, message: str, details: Optional[dict] = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def expand_path(path: str) -> str:
    return os.path.abspath(os.path.expanduser(path))


def ensure_parent(path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)


def sha256_text(data: str) -> str:
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def is_pid_alive(pid: Optional[int]) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def repo_control_paths(repo_root: str) -> dict:
    control_root = os.path.join(repo_root, "control")
    return {
        "root": control_root,
        "inbox": os.path.join(control_root, "inbox"),
        "current": os.path.join(control_root, "inbox", "current.json"),
        "outbox": os.path.join(control_root, "outbox"),
        "mailbox": os.path.join(control_root, "mailbox"),
        "user_prompts": os.path.join(control_root, "mailbox", "user_prompts"),
        "claims": os.path.join(control_root, "claims"),
    }


def ensure_control_layout(repo_root: str) -> dict:
    paths = repo_control_paths(repo_root)
    for key in ("root", "inbox", "outbox", "mailbox", "user_prompts", "claims"):
        os.makedirs(paths[key], exist_ok=True)
    return paths


def git_run(repo_root: str, args: List[str], check: bool = False) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        ["git", "-C", repo_root, *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if check and proc.returncode != 0:
        raise HostError(
            "git_failed",
            f"git {' '.join(args)} failed",
            {"stdout": proc.stdout, "stderr": proc.stderr, "returncode": proc.returncode},
        )
    return proc


def default_state(repo_root: str) -> dict:
    worker_log_path = os.path.join(STATE_DIR, "control-worker.log")
    worker_pid_path = os.path.join(STATE_DIR, "control-worker.pid")
    return {
        "version": 2,
        "repo_root": repo_root,
        "memory": {
            "kv": {
                "canonical_repo": repo_root,
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
            "worker_pid_path": worker_pid_path,
            "worker_log_path": worker_log_path,
            "worker_started_at": None,
            "last_sync_at": None,
            "last_prompt_id": None,
            "last_result_id": None
        },
        "recent_commands": []
    }


def load_policy() -> dict:
    path = expand_path(DEFAULT_POLICY_PATH)
    if not os.path.exists(path):
        raise HostError("missing_policy", f"Policy file not found: {path}", {"policy_path": path})
    with open(path, "r", encoding="utf-8") as handle:
        policy = json.load(handle)
    policy["_policy_path"] = path
    policy["repo_root"] = expand_path(policy["repo_root"])
    log_path = policy.get("log_path", os.path.join(STATE_DIR, "audit.jsonl"))
    policy["log_path"] = expand_path(log_path)
    policy.setdefault("protected_paths", [])
    policy.setdefault("allowed_git_subcommands", ["status", "diff", "add", "commit", "branch"])
    policy.setdefault("rules", [])
    policy.setdefault("command_rules", [])
    return policy


def ensure_repo(policy: dict) -> str:
    repo_root = policy["repo_root"]
    if not os.path.isdir(repo_root):
        raise HostError("bad_repo_root", f"Repo root does not exist: {repo_root}")
    if not os.path.isdir(os.path.join(repo_root, ".git")):
        raise HostError("not_git_repo", f"Repo root is not a git repository: {repo_root}")
    return repo_root


def load_state(repo_root: str) -> dict:
    path = expand_path(DEFAULT_STATE_PATH)
    defaults = default_state(repo_root)
    if not os.path.exists(path):
        state = defaults
        state["_state_path"] = path
        save_state(state)
        return state
    with open(path, "r", encoding="utf-8") as handle:
        state = json.load(handle)
    state.setdefault("version", defaults["version"])
    state["repo_root"] = repo_root
    state.setdefault("memory", defaults["memory"])
    state["memory"].setdefault("kv", {})
    state["memory"].setdefault("notes", [])
    state["memory"]["kv"].setdefault("canonical_repo", repo_root)
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
    state["_state_path"] = path
    return state


def save_state(state: dict) -> None:
    path = expand_path(state.get("_state_path", DEFAULT_STATE_PATH))
    ensure_parent(path)
    persisted = dict(state)
    persisted.pop("_state_path", None)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(persisted, handle, indent=2)
        handle.write("\n")


def audit(policy: dict, event: dict) -> None:
    path = policy["log_path"]
    ensure_parent(path)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps({"ts": utc_now(), **event}, ensure_ascii=False) + "\n")


def normalize_relpath(repo_root: str, rel_path: str) -> str:
    if rel_path in ("", "."):
        return "."
    candidate = os.path.realpath(os.path.join(repo_root, rel_path))
    repo_real = os.path.realpath(repo_root)
    if os.path.commonpath([repo_real, candidate]) != repo_real:
        raise HostError("path_escape", f"Path escapes repo root: {rel_path}")
    return pathlib.Path(candidate).relative_to(repo_real).as_posix()


SPECIAL_CHARS = ".^$+{}[]|()"
_REGEX_CACHE: Dict[str, re.Pattern] = {}
DIFF_GIT_RE = re.compile(r"^diff --git a/(.+) b/(.+)$")


def glob_to_regex(pattern: str) -> re.Pattern:
    pattern = pattern.replace("\\", "/")
    out = ["^"]
    i = 0
    while i < len(pattern):
        c = pattern[i]
        if c == "*":
            if i + 1 < len(pattern) and pattern[i + 1] == "*":
                i += 1
                if i + 1 < len(pattern) and pattern[i + 1] == "/":
                    i += 1
                out.append(".*")
            else:
                out.append("[^/]*")
        elif c == "?":
            out.append("[^/]")
        else:
            out.append("\\" + c if c in SPECIAL_CHARS else c)
        i += 1
    out.append("$")
    return re.compile("".join(out))


def match_glob(path: str, pattern: str) -> bool:
    regex = _REGEX_CACHE.get(pattern)
    if regex is None:
        regex = glob_to_regex(pattern)
        _REGEX_CACHE[pattern] = regex
    return bool(regex.match(path))


def all_paths_match(paths: List[str], globs: List[str]) -> bool:
    if not globs:
        return True
    return all(any(match_glob(path, glob) for glob in globs) for path in paths)


def parse_patch_summary(patch: str, repo_root: str) -> dict:
    if not patch.strip():
        raise HostError("empty_patch", "Patch is empty.")
    files = []
    current = None
    total_added = 0
    total_deleted = 0
    for raw_line in patch.splitlines():
        line = raw_line.rstrip("\n")
        m = DIFF_GIT_RE.match(line)
        if m:
            old_path = m.group(1)
            new_path = m.group(2)
            current = {
                "old_path": old_path,
                "new_path": new_path,
                "display_path": new_path,
                "created": False,
                "deleted": False,
                "renamed": False,
                "added": 0,
                "deleted_lines": 0,
            }
            files.append(current)
            continue
        if current is None:
            continue
        if line.startswith("new file mode "):
            current["created"] = True
            continue
        if line.startswith("deleted file mode "):
            current["deleted"] = True
            current["display_path"] = current["old_path"]
            continue
        if line.startswith("rename from "):
            current["renamed"] = True
            current["old_path"] = line[len("rename from "):]
            continue
        if line.startswith("rename to "):
            current["renamed"] = True
            current["new_path"] = line[len("rename to "):]
            current["display_path"] = current["new_path"]
            continue
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+"):
            current["added"] += 1
            total_added += 1
            continue
        if line.startswith("-"):
            current["deleted_lines"] += 1
            total_deleted += 1
            continue
    if not files:
        raise HostError("bad_patch", "Patch does not look like a git-style unified diff.")
    normalized_paths = []
    for entry in files:
        candidate = entry["display_path"]
        if candidate == "/dev/null":
            candidate = entry["old_path"]
        normalized_paths.append(normalize_relpath(repo_root, candidate))
    return {
        "action": "apply_patch",
        "files": len(files),
        "paths": normalized_paths,
        "added_lines": total_added,
        "deleted_lines": total_deleted,
        "has_create": any(f["created"] for f in files),
        "has_delete": any(f["deleted"] for f in files),
        "has_rename": any(f["renamed"] for f in files),
        "patch_sha256": sha256_text(patch),
    }


def check_protected_paths(policy: dict, paths: List[str]) -> None:
    for path in paths:
        if any(match_glob(path, pattern) for pattern in policy.get("protected_paths", [])):
            raise HostError("protected_path", f"Path is protected by policy: {path}", {"path": path})


def select_rule(action: str, summary: dict, policy: dict) -> Optional[dict]:
    for rule in SESSION_RULES + policy.get("rules", []):
        if not rule.get("enabled", True):
            continue
        if rule.get("action") != action:
            continue
        paths = summary.get("paths", [])
        globs = rule.get("path_globs", [])
        if paths and not all_paths_match(paths, globs):
            continue
        if action == "apply_patch":
            if summary["files"] > rule.get("max_files", 10**9):
                continue
            if summary["added_lines"] > rule.get("max_added_lines", 10**9):
                continue
            if summary["deleted_lines"] > rule.get("max_deleted_lines", 10**9):
                continue
            if summary["has_create"] and not rule.get("allow_create", False):
                continue
            if summary["has_delete"] and not rule.get("allow_delete", False):
                continue
            if summary["has_rename"] and not rule.get("allow_rename", False):
                continue
        return rule
    return None


def sanitized_policy(policy: dict) -> dict:
    out = dict(policy)
    out.pop("_policy_path", None)
    return out


def sanitized_state(state: dict) -> dict:
    out = dict(state)
    out.pop("_state_path", None)
    return out


def read_file_text(repo_root: str, rel_path: str) -> dict:
    normalized = normalize_relpath(repo_root, rel_path)
    full_path = os.path.join(repo_root, normalized)
    if not os.path.exists(full_path):
        raise HostError("missing_file", f"File not found: {normalized}")
    with open(full_path, "r", encoding="utf-8") as handle:
        text = handle.read()
    return {"path": normalized, "sha256": sha256_text(text), "bytes": len(text.encode("utf-8")), "text": text}


def list_repo(repo_root: str, rel_path: str = ".", max_depth: int = 2, max_entries: int = 200) -> dict:
    base_rel = normalize_relpath(repo_root, rel_path) if rel_path != "." else "."
    base_abs = repo_root if base_rel == "." else os.path.join(repo_root, base_rel)
    if not os.path.isdir(base_abs):
        raise HostError("not_directory", f"Not a directory: {base_rel}")
    items = []
    base_parts = [] if base_rel == "." else base_rel.split("/")
    for root, dirs, files in os.walk(base_abs):
        dirs[:] = [d for d in dirs if d != ".git"]
        rel_root = pathlib.Path(root).relative_to(repo_root).as_posix()
        depth = len([p for p in rel_root.split("/") if p and p != "."]) - len(base_parts)
        if depth > max_depth:
            dirs[:] = []
            continue
        for name in sorted(dirs):
            rel = pathlib.Path(root, name).relative_to(repo_root).as_posix()
            items.append({"type": "dir", "path": rel})
            if len(items) >= max_entries:
                return {"base": base_rel, "items": items, "truncated": True}
        for name in sorted(files):
            rel = pathlib.Path(root, name).relative_to(repo_root).as_posix()
            items.append({"type": "file", "path": rel})
            if len(items) >= max_entries:
                return {"base": base_rel, "items": items, "truncated": True}
    return {"base": base_rel, "items": items, "truncated": False}


def run_git(repo_root: str, args: List[str], policy: dict) -> dict:
    if not args:
        raise HostError("bad_git_args", "Git subcommand is required.")
    subcommand = args[0]
    if subcommand not in policy.get("allowed_git_subcommands", []):
        raise HostError("git_forbidden", f"Git subcommand not allowed: {subcommand}")
    proc = subprocess.run(["git", "-C", repo_root, *args], capture_output=True, text=True, check=False)
    return {"command": ["git", "-C", repo_root, *args], "returncode": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr}


def git_status(repo_root: str) -> dict:
    proc = subprocess.run(["git", "-C", repo_root, "status", "--short", "--branch"], capture_output=True, text=True, check=False)
    return {"returncode": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr}


def apply_patch_now(repo_root: str, patch: str) -> dict:
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".patch", delete=False) as handle:
        handle.write(patch)
        patch_path = handle.name
    try:
        check = subprocess.run(["git", "-C", repo_root, "apply", "--check", patch_path], capture_output=True, text=True, check=False)
        if check.returncode != 0:
            raise HostError("patch_check_failed", "git apply --check failed", {"stdout": check.stdout, "stderr": check.stderr})
        applied = subprocess.run(["git", "-C", repo_root, "apply", "--whitespace=nowarn", "--recount", patch_path], capture_output=True, text=True, check=False)
        if applied.returncode != 0:
            raise HostError("patch_apply_failed", "git apply failed", {"stdout": applied.stdout, "stderr": applied.stderr})
        return {"stdout": applied.stdout, "stderr": applied.stderr, "patch_path": patch_path}
    finally:
        try:
            os.unlink(patch_path)
        except FileNotFoundError:
            pass


def append_rule(policy: dict, rule: dict) -> None:
    persisted = dict(policy)
    persisted.pop("_policy_path", None)
    persisted.setdefault("rules", []).append(rule)
    path = policy["_policy_path"]
    ensure_parent(path)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(persisted, handle, indent=2)
        handle.write("\n")


def append_command_rule(policy: dict, rule: dict) -> None:
    persisted = dict(policy)
    persisted.pop("_policy_path", None)
    persisted.setdefault("command_rules", []).append(rule)
    path = policy["_policy_path"]
    ensure_parent(path)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(persisted, handle, indent=2)
        handle.write("\n")


def make_proposed_rule(summary: dict) -> dict:
    return {
        "id": f"auto-{secrets.token_hex(4)}",
        "enabled": True,
        "action": "apply_patch",
        "path_globs": summary["paths"],
        "max_files": summary["files"],
        "max_added_lines": max(summary["added_lines"], 1),
        "max_deleted_lines": summary["deleted_lines"],
        "allow_create": summary["has_create"],
        "allow_delete": False,
        "allow_rename": False,
        "mode": "auto_allow",
    }


def make_proposed_command_rule(summary: dict) -> dict:
    return {
        "id": f"cmd-auto-{secrets.token_hex(4)}",
        "enabled": True,
        "action": "run_command",
        "target": summary["target"],
        "cwd_globs": [summary["cwd"]],
        "argv0": summary["argv0"],
        "pattern": f"^{re.escape(summary['command'].strip())}$",
        "elevate": bool(summary.get("elevate", False)),
        "mode": "auto_allow",
    }


def get_audit_tail(policy: dict, lines: int) -> dict:
    path = policy["log_path"]
    if not os.path.exists(path):
        return {"path": path, "lines": []}
    with open(path, "r", encoding="utf-8") as handle:
        data = handle.readlines()[-max(lines, 1):]
    parsed = []
    for entry in data:
        try:
            parsed.append(json.loads(entry))
        except json.JSONDecodeError:
            parsed.append({"raw": entry.rstrip("\n")})
    return {"path": path, "lines": parsed}


def check_command_safety(command: str, target: str, elevate: bool) -> None:
    stripped = command.strip()
    if not stripped:
        raise HostError("empty_command", "Command is empty.")
    lowered = stripped.lower()
    forbidden = ["sudo", "su ", "scp ", "rsync ", "systemctl", "service ", "mount ", "umount ", "docker ", "podman "]
    if target == "HOST":
        forbidden.append("ssh ")
    for token in forbidden:
        if token in lowered:
            raise HostError("forbidden_command", f"Command contains forbidden token: {token.strip()}")
    if elevate and target != "HOST":
        raise HostError("unsupported_elevate_target", "Root-helper elevation is currently HOST-only.")


def summarize_command(command: str, target: str, cwd: str, elevate: bool) -> dict:
    check_command_safety(command, target, elevate)
    try:
        argv = shlex.split(command)
    except ValueError as exc:
        raise HostError("bad_command", f"Unable to parse command: {exc}")
    if not argv:
        raise HostError("empty_command", "Command is empty.")
    return {
        "action": "run_command",
        "target": target,
        "command": command,
        "argv0": argv[0],
        "argv": argv,
        "cwd": cwd,
        "elevate": elevate,
        "command_sha256": sha256_text(command),
    }


def select_command_rule(summary: dict, policy: dict) -> Optional[dict]:
    for rule in SESSION_RULES + policy.get("command_rules", []):
        if not rule.get("enabled", True):
            continue
        if rule.get("action") != "run_command":
            continue
        if rule.get("target") and rule.get("target") != summary["target"]:
            continue
        if rule.get("argv0") and rule.get("argv0") != summary["argv0"]:
            continue
        if bool(rule.get("elevate", False)) != bool(summary.get("elevate", False)):
            continue
        cwd_globs = rule.get("cwd_globs", [])
        if cwd_globs and not any(match_glob(summary["cwd"], glob) for glob in cwd_globs):
            continue
        pattern = rule.get("pattern")
        if pattern and not re.match(pattern, summary["command"]):
            continue
        return rule
    return None


def make_nonblocking(fd: int) -> None:
    flags = fcntl.fcntl(fd, fcntl.F_GETFL)
    fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)


def reap_finished_sessions() -> None:
    doomed = []
    for sid, sess in list(TERMINAL_SESSIONS.items()):
        if sess["proc"].poll() is not None and sess.get("closed"):
            doomed.append(sid)
    for sid in doomed:
        sess = TERMINAL_SESSIONS.pop(sid, None)
        if sess:
            try:
                os.close(sess["master_fd"])
            except OSError:
                pass


def build_host_command(target_cfg: dict, command: str, elevate: bool) -> List[str]:
    if not elevate:
        return ["/bin/bash", "-lc", command]
    helper = target_cfg.get("root_helper", {})
    if not helper.get("enabled", False):
        raise HostError("root_helper_disabled", "Root helper is disabled for HOST.")
    helper_path = helper.get("path") or "/usr/local/sbin/openclaw-root-helper"
    helper_mode = helper.get("mode", "unknown")
    if helper_mode == "unknown":
        raise HostError("root_helper_mode_unknown", "Root helper mode is unknown. Inspect the helper interface before using elevation.", {"path": helper_path})
    if helper_mode == "bash-lc":
        return [helper_path, "/bin/bash", "-lc", command]
    if helper_mode == "argv":
        return [helper_path] + shlex.split(command)
    raise HostError("root_helper_bad_mode", f"Unsupported root helper mode: {helper_mode}")


def build_remote_command(target_cfg: dict, command: str, cwd: str) -> List[str]:
    ssh_host = target_cfg.get("ssh_host", "").strip()
    if not ssh_host:
        raise HostError("missing_ssh_host", "Target ssh_host is not configured.")
    ssh_options = target_cfg.get("ssh_options", [])
    remote_cwd = cwd if cwd and cwd != "." else target_cfg.get("default_cwd", "~")
    remote = f"cd {shlex.quote(remote_cwd)} && {command}"
    return ["ssh", *ssh_options, ssh_host, "--", "/bin/bash", "-lc", remote]


def record_recent_command(state: dict, target: str, cwd: str, command: str, elevate: bool) -> None:
    entry = {"ts": utc_now(), "target": target, "cwd": cwd, "command": command, "elevate": elevate}
    recent = state.setdefault("recent_commands", [])
    recent.insert(0, entry)
    del recent[50:]
    save_state(state)


def start_target_command(repo_root: str, policy: dict, state: dict, target_name: str, command: str, cwd: str = ".", elevate: bool = False) -> dict:
    targets = state.get("targets", {})
    if target_name not in targets:
        raise HostError("bad_target", f"Unknown target: {target_name}")
    target_cfg = targets[target_name]
    if not target_cfg.get("enabled", False):
        raise HostError("target_disabled", f"Target is disabled: {target_name}")
    if target_cfg.get("kind") == "local":
        display_cwd = normalize_relpath(repo_root, cwd or ".")
        abs_cwd = repo_root if display_cwd == "." else os.path.join(repo_root, display_cwd)
        popen_cmd = build_host_command(target_cfg, command, elevate)
    elif target_cfg.get("kind") == "ssh":
        display_cwd = (cwd or target_cfg.get("default_cwd", "~")).strip() or target_cfg.get("default_cwd", "~")
        abs_cwd = None
        popen_cmd = build_remote_command(target_cfg, command, display_cwd)
    else:
        raise HostError("bad_target_kind", f"Unsupported target kind: {target_cfg.get('kind')}")
    summary = summarize_command(command, target_name, display_cwd, elevate)
    rule = select_command_rule(summary, policy)
    if rule and rule.get("mode") == "deny":
        raise HostError("denied", "Policy denied command.", {"rule": rule, "summary": summary})
    if not rule or rule.get("mode") == "ask":
        token = secrets.token_urlsafe(16)
        proposed_rule = make_proposed_command_rule(summary)
        PENDING_REQUESTS[token] = {
            "kind": "command",
            "target": target_name,
            "command": command,
            "cwd": display_cwd,
            "elevate": elevate,
            "summary": summary,
            "proposed_rule": proposed_rule,
        }
        return {
            "ok": False,
            "error": {
                "code": "approval_required",
                "message": f"Approval required for {target_name} command: {command}",
                "decision_token": token,
                "summary": summary,
                "proposed_rule": proposed_rule,
            },
        }
    master_fd, slave_fd = pty.openpty()
    env = os.environ.copy()
    env["TERM"] = env.get("TERM", "xterm-256color")
    env["PS1"] = f"({target_name.lower()}) \\w $ "
    proc = subprocess.Popen(
        popen_cmd,
        cwd=abs_cwd,
        stdin=slave_fd,
        stdout=slave_fd,
        stderr=slave_fd,
        start_new_session=True,
        env=env,
        text=False,
        close_fds=True,
    )
    os.close(slave_fd)
    make_nonblocking(master_fd)
    session_id = secrets.token_urlsafe(12)
    TERMINAL_SESSIONS[session_id] = {
        "id": session_id,
        "proc": proc,
        "master_fd": master_fd,
        "target": target_name,
        "command": command,
        "cwd": display_cwd,
        "started_at": utc_now(),
        "closed": False,
    }
    audit(policy, {"action": "run_target_command", "target": target_name, "command": command, "cwd": display_cwd, "elevate": elevate, "result": "started", "session_id": session_id, "mode": rule.get("mode", "auto_allow")})
    record_recent_command(state, target_name, display_cwd, command, elevate)
    initial = drain_terminal_output(session_id)
    return {"ok": True, "data": {"session_id": session_id, "target": target_name, "command": command, "cwd": display_cwd, "initial_output": initial.get("output", ""), "done": initial.get("done", False), "returncode": initial.get("returncode")}}


def drain_terminal_output(session_id: str, max_bytes: int = 65536) -> dict:
    sess = TERMINAL_SESSIONS.get(session_id)
    if not sess:
        raise HostError("missing_session", "Terminal session not found.")
    chunks = []
    remaining = max_bytes
    while remaining > 0:
        try:
            data = os.read(sess["master_fd"], min(4096, remaining))
            if not data:
                sess["closed"] = True
                break
            chunks.append(data)
            remaining -= len(data)
        except BlockingIOError:
            break
        except OSError as exc:
            if exc.errno == errno.EIO:
                sess["closed"] = True
                break
            raise
    rc = sess["proc"].poll()
    done = rc is not None
    if done:
        sess["closed"] = True
    output = b"".join(chunks).decode("utf-8", errors="replace")
    return {"session_id": session_id, "target": sess.get("target"), "output": output, "done": done, "returncode": rc}


def stop_terminal_session(session_id: str, policy: dict) -> dict:
    sess = TERMINAL_SESSIONS.get(session_id)
    if not sess:
        raise HostError("missing_session", "Terminal session not found.")
    proc = sess["proc"]
    if proc.poll() is None:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
            time.sleep(0.15)
        except ProcessLookupError:
            pass
        if proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    drained = drain_terminal_output(session_id)
    audit(policy, {"action": "stop_terminal_session", "session_id": session_id, "target": sess.get("target"), "result": "stopped", "returncode": drained.get("returncode")})
    return drained


def memory_set(state: dict, key: str, value: str) -> dict:
    if not key.strip():
        raise HostError("bad_memory_key", "Memory key is required.")
    state.setdefault("memory", {}).setdefault("kv", {})[key] = value
    save_state(state)
    return {"key": key, "value": value}


def memory_get_all(state: dict) -> dict:
    return state.get("memory", {})


def list_targets(state: dict) -> dict:
    return state.get("targets", {})


def sync_control_commit(repo_root: str, state: dict, paths: List[str], commit_message: str) -> dict:
    control_cfg = state.get("control", {})
    rel_paths = [os.path.relpath(path, repo_root) for path in paths]

    git_run(repo_root, ["add", *rel_paths], check=True)
    diff = git_run(repo_root, ["diff", "--cached", "--name-only"], check=True)
    changed = [line for line in diff.stdout.splitlines() if line.strip()]

    if not changed:
        return {"status": "no_changes", "changed": []}

    git_run(repo_root, ["commit", "-m", commit_message], check=True)

    control_cfg["last_sync_at"] = utc_now()
    save_state(state)

    return {
        "status": "committed_local_only",
        "changed": changed,
        "push": "skipped_by_policy"
    }


def worker_status(state: dict) -> dict:
    control_cfg = state.get("control", {})
    pid = control_cfg.get("worker_pid")
    pid_path = expand_path(control_cfg.get("worker_pid_path", os.path.join(STATE_DIR, "control-worker.pid")))
    log_path = expand_path(control_cfg.get("worker_log_path", os.path.join(STATE_DIR, "control-worker.log")))
    if not pid and os.path.exists(pid_path):
        try:
            pid = int(pathlib.Path(pid_path).read_text(encoding="utf-8").strip())
        except Exception:
            pid = None
    alive = is_pid_alive(pid)
    return {
        "running": alive,
        "pid": pid,
        "pid_path": pid_path,
        "log_path": log_path,
        "started_at": control_cfg.get("worker_started_at"),
        "poll_interval_seconds": control_cfg.get("poll_interval_seconds", 10),
        "last_sync_at": control_cfg.get("last_sync_at"),
    }


def start_control_worker(repo_root: str, state: dict) -> dict:
    status = worker_status(state)
    if status["running"]:
        return status
    control_cfg = state.setdefault("control", {})
    log_path = expand_path(control_cfg.get("worker_log_path", os.path.join(STATE_DIR, "control-worker.log")))
    pid_path = expand_path(control_cfg.get("worker_pid_path", os.path.join(STATE_DIR, "control-worker.pid")))
    ensure_parent(log_path)
    worker_path = os.path.join(os.path.dirname(__file__), "control_worker.py")
    if not os.path.exists(worker_path):
        raise HostError("missing_worker", f"Control worker not found: {worker_path}")
    log_handle = open(log_path, "ab")
    proc = subprocess.Popen(
        [sys.executable, worker_path, "--repo-root", repo_root, "--state-path", expand_path(state.get("_state_path", DEFAULT_STATE_PATH)), "--policy-path", expand_path(DEFAULT_POLICY_PATH)],
        stdin=subprocess.DEVNULL,
        stdout=log_handle,
        stderr=log_handle,
        start_new_session=True,
        close_fds=True,
    )
    pathlib.Path(pid_path).write_text(str(proc.pid) + "\n", encoding="utf-8")
    control_cfg["worker_pid"] = proc.pid
    control_cfg["worker_started_at"] = utc_now()
    save_state(state)
    return worker_status(state)


def stop_control_worker(state: dict) -> dict:
    status = worker_status(state)
    pid = status.get("pid")
    if pid and is_pid_alive(pid):
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        time.sleep(0.2)
        if is_pid_alive(pid):
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    control_cfg = state.setdefault("control", {})
    pid_path = expand_path(control_cfg.get("worker_pid_path", os.path.join(STATE_DIR, "control-worker.pid")))
    if os.path.exists(pid_path):
        try:
            os.unlink(pid_path)
        except OSError:
            pass
    control_cfg["worker_pid"] = None
    save_state(state)
    return worker_status(state)


def worker_run_once(repo_root: str, state: dict) -> dict:
    worker_path = os.path.join(os.path.dirname(__file__), "control_worker.py")
    if not os.path.exists(worker_path):
        raise HostError("missing_worker", f"Control worker not found: {worker_path}")
    proc = subprocess.run(
        [sys.executable, worker_path, "--once", "--repo-root", repo_root, "--state-path", expand_path(state.get("_state_path", DEFAULT_STATE_PATH)), "--policy-path", expand_path(DEFAULT_POLICY_PATH)],
        capture_output=True,
        text=True,
        check=False,
    )
    return {"returncode": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr}


def reset_control_loop(repo_root: str, state: dict) -> dict:
    paths = ensure_control_layout(repo_root)
    current_path = paths["current"]
    removed = False

    if os.path.exists(current_path):
        os.unlink(current_path)
        removed = True

    state.setdefault("control", {})["last_result_id"] = None
    save_state(state)

    return {
        "status": "reset",
        "removed_current": removed,
        "current_path": os.path.relpath(current_path, repo_root),
        "message": "Worker current command cleared. Ready for next task."
    }


def submit_prompt(repo_root: str, state: dict, prompt_text: str, meta: Optional[dict] = None) -> dict:
    prompt_text = prompt_text.strip()
    if not prompt_text:
        raise HostError("empty_prompt", "Prompt is empty.")
    paths = ensure_control_layout(repo_root)
    prompt_id = f"prompt-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{secrets.token_hex(3)}"
    payload = {
        "id": prompt_id,
        "type": "user_prompt",
        "prompt": prompt_text,
        "meta": meta or {},
        "created_at": utc_now(),
        "status": "queued",
    }
    prompt_path = os.path.join(paths["user_prompts"], f"{prompt_id}.json")
    with open(prompt_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
        handle.write("\n")
    state.setdefault("control", {})["last_prompt_id"] = prompt_id
    save_state(state)
    sync = {"status": "local_only"}
    try:
        sync = sync_control_commit(repo_root, state, [prompt_path], f"Queue user prompt {prompt_id}")
    except HostError as exc:
        sync = {"status": "local_only", "error": {"code": exc.code, "message": exc.message, "details": exc.details}}
    return {"prompt_id": prompt_id, "path": os.path.relpath(prompt_path, repo_root), "sync": sync}


def handle_request(message: dict) -> dict:
    reap_finished_sessions()
    policy = load_policy()
    repo_root = ensure_repo(policy)
    state = load_state(repo_root)
    action = message.get("action")
    if action == "ping":
        return {"ok": True, "data": {"host_name": HOST_NAME, "host_version": HOST_VERSION, "policy_path": policy["_policy_path"], "state_path": state["_state_path"], "repo_root": repo_root}}
    if action == "get_policy":
        return {"ok": True, "data": sanitized_policy(policy)}
    if action == "get_state":
        return {"ok": True, "data": sanitized_state(state)}
    if action == "list_targets":
        return {"ok": True, "data": list_targets(state)}
    if action == "memory_set":
        result = memory_set(state, message.get("key", ""), message.get("value", ""))
        audit(policy, {"action": action, "key": message.get("key", ""), "result": "ok"})
        return {"ok": True, "data": result}
    if action == "memory_get_all":
        return {"ok": True, "data": memory_get_all(state)}
    if action == "get_worker_status":
        return {"ok": True, "data": worker_status(state)}
    if action == "start_control_worker":
        result = start_control_worker(repo_root, state)
        audit(policy, {"action": action, "result": "ok", "worker": result})
        return {"ok": True, "data": result}
    if action == "stop_control_worker":
        result = stop_control_worker(state)
        audit(policy, {"action": action, "result": "ok", "worker": result})
        return {"ok": True, "data": result}
    if action == "worker_run_once":
        result = worker_run_once(repo_root, state)
        audit(policy, {"action": action, "result": "ok", "returncode": result["returncode"]})
        return {"ok": True, "data": result}
    if action == "reset_control_loop":
        result = reset_control_loop(repo_root, state)
        audit(policy, {"action": action, "result": "ok", "removed_current": result["removed_current"]})
        return {"ok": True, "data": result}
    if action == "submit_prompt":
        result = submit_prompt(repo_root, state, message.get("prompt", ""), message.get("meta", {}))
        audit(policy, {"action": action, "result": "ok", "prompt_id": result["prompt_id"]})
        return {"ok": True, "data": result}
    if action == "list_repo":
        rel_path = message.get("path", ".")
        return {"ok": True, "data": list_repo(repo_root, rel_path, int(message.get("max_depth", 2)), int(message.get("max_entries", 200)))}
    if action == "get_audit_tail":
        return {"ok": True, "data": get_audit_tail(policy, int(message.get("lines", 20)))}
    if action == "git_status":
        result = git_status(repo_root)
        audit(policy, {"action": action, "result": "ok"})
        return {"ok": True, "data": result}
    if action == "run_git":
        args = message.get("args", [])
        result = run_git(repo_root, args, policy)
        audit(policy, {"action": action, "args": args, "result": "ok", "returncode": result["returncode"]})
        return {"ok": True, "data": result}
    if action == "read_file":
        rel_path = message.get("path", "")
        summary = {"action": action, "paths": [normalize_relpath(repo_root, rel_path)]}
        check_protected_paths(policy, summary["paths"])
        rule = select_rule(action, summary, policy)
        if not rule:
            raise HostError("no_matching_rule", f"No policy rule matched action {action}", {"summary": summary})
        if rule.get("mode") == "deny":
            raise HostError("denied", f"Policy denied action {action}", {"rule": rule})
        result = read_file_text(repo_root, rel_path)
        audit(policy, {"action": action, "path": result["path"], "result": "ok"})
        return {"ok": True, "data": result}
    if action == "apply_patch":
        patch = message.get("patch", "")
        summary = parse_patch_summary(patch, repo_root)
        check_protected_paths(policy, summary["paths"])
        rule = select_rule(action, summary, policy)
        if rule and rule.get("mode") == "deny":
            raise HostError("denied", "Policy denied patch.", {"rule": rule, "summary": summary})
        if not rule or rule.get("mode") == "ask":
            token = secrets.token_urlsafe(16)
            proposed_rule = make_proposed_rule(summary)
            PENDING_REQUESTS[token] = {"kind": "patch", "patch": patch, "summary": summary, "proposed_rule": proposed_rule}
            return {"ok": False, "error": {"code": "approval_required", "message": f"Approval required for patch touching {summary['files']} file(s), +{summary['added_lines']} / -{summary['deleted_lines']}", "decision_token": token, "summary": summary, "proposed_rule": proposed_rule}}
        applied = apply_patch_now(repo_root, patch)
        audit(policy, {"action": action, "summary": summary, "result": "ok", "mode": rule.get("mode", "auto_allow")})
        return {"ok": True, "data": {"summary": summary, "apply": applied}}
    if action == "run_target_command":
        return start_target_command(repo_root, policy, state, message.get("target", "HOST"), message.get("command", ""), message.get("cwd", "."), bool(message.get("elevate", False)))
    if action == "poll_terminal_session":
        data = drain_terminal_output(message.get("session_id", ""))
        if data.get("done"):
            audit(policy, {"action": action, "session_id": data["session_id"], "target": data.get("target"), "result": "finished", "returncode": data.get("returncode")})
        return {"ok": True, "data": data}
    if action == "stop_terminal_session":
        return {"ok": True, "data": stop_terminal_session(message.get("session_id", ""), policy)}
    if action == "approve_request":
        token = message.get("decision_token", "")
        if token not in PENDING_REQUESTS:
            raise HostError("missing_token", "Approval token not found or expired.")
        decision = message.get("decision")
        item = PENDING_REQUESTS.pop(token)
        proposed_rule = message.get("rule") or item["proposed_rule"]
        if decision == "deny":
            audit(policy, {"action": "approve_request", "decision": decision, "summary": item.get("summary"), "result": "denied", "kind": item.get("kind")})
            return {"ok": True, "data": {"decision": decision, "summary": item.get("summary"), "kind": item.get("kind")}}
        if decision == "session":
            SESSION_RULES.insert(0, proposed_rule)
        elif decision == "always":
            if item.get("kind") == "command":
                append_command_rule(policy, proposed_rule)
                SESSION_RULES.insert(0, proposed_rule)
            else:
                append_rule(policy, proposed_rule)
                SESSION_RULES.insert(0, proposed_rule)
        elif decision != "once":
            raise HostError("bad_decision", f"Unknown decision: {decision}")
        if item.get("kind") == "command":
            return start_target_command(repo_root, policy, state, item.get("target", "HOST"), item["command"], item.get("cwd", "."), bool(item.get("elevate", False)))
        summary = item["summary"]
        patch = item["patch"]
        applied = apply_patch_now(repo_root, patch)
        audit(policy, {"action": "approve_request", "decision": decision, "summary": summary, "result": "ok", "kind": item.get("kind")})
        return {"ok": True, "data": {"decision": decision, "summary": summary, "apply": applied, "rule": proposed_rule}}
    raise HostError("unknown_action", f"Unknown action: {action}")


def read_message() -> Optional[dict]:
    raw_length = sys.stdin.buffer.read(4)
    if len(raw_length) == 0:
        return None
    if len(raw_length) != 4:
        raise HostError("protocol_error", "Failed to read message length.")
    message_length = struct.unpack("=I", raw_length)[0]
    message = sys.stdin.buffer.read(message_length)
    if len(message) != message_length:
        raise HostError("protocol_error", "Failed to read full message body.")
    return json.loads(message.decode("utf-8"))


def write_message(message: dict) -> None:
    encoded = json.dumps(message).encode("utf-8")
    sys.stdout.buffer.write(struct.pack("=I", len(encoded)))
    sys.stdout.buffer.write(encoded)
    sys.stdout.buffer.flush()


def main() -> None:
    incoming = None
    while True:
        try:
            incoming = read_message()
            if incoming is None:
                break
            msg_id = incoming.get("id")
            response = handle_request(incoming)
            write_message({"id": msg_id, **response})
        except HostError as error:
            msg_id = incoming.get("id") if isinstance(incoming, dict) else None
            payload = {"id": msg_id, "ok": False, "error": {"code": error.code, "message": error.message, "details": error.details}}
            try:
                write_message(payload)
            except Exception:
                break
        except Exception as error:
            msg_id = incoming.get("id") if isinstance(incoming, dict) else None
            payload = {"id": msg_id, "ok": False, "error": {"code": "internal_error", "message": str(error), "details": {}}}
            try:
                write_message(payload)
            except Exception:
                break


if __name__ == "__main__":
    main()
