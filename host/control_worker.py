
#!/usr/bin/env python3
import argparse
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone

import host as bridge_host


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: str):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: str, payload: dict) -> None:
    bridge_host.ensure_parent(path)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
        handle.write("\n")


def worker_identity() -> str:
    return f"worker-{os.getpid()}"


def load_runtime(repo_root: str):
    policy = bridge_host.load_policy()
    repo_root = bridge_host.ensure_repo(policy)
    state = bridge_host.load_state(repo_root)
    return policy, state, repo_root


def execute_once(repo_root: str, state: dict, policy: dict) -> dict:
    paths = bridge_host.ensure_control_layout(repo_root)
    current_path = paths["current"]
    if not os.path.exists(current_path):
        return {"status": "idle", "reason": "no_current_command"}

    cmd = read_json(current_path)
    cmd_id = cmd.get("id") or "unknown"
    status = cmd.get("status", "queued")
    if status not in ("queued", "retry"):
        return {"status": "idle", "reason": f"current_status={status}", "id": cmd_id}

    target = cmd.get("target", "HOST")
    command = (cmd.get("command") or "").strip()
    cwd = cmd.get("cwd", ".")
    elevate = bool(cmd.get("use_root_helper", False))
    approval_mode = cmd.get("approval_mode", "auto_if_policy_allows")

    claimed = dict(cmd)
    claimed["status"] = "running"
    claimed["claimed_at"] = utc_now()
    claimed["claimed_by"] = worker_identity()
    write_json(current_path, claimed)

    summary = bridge_host.summarize_command(command, target, cwd, elevate)
    rule = bridge_host.select_command_rule(summary, policy)

    result = {
        "id": cmd_id,
        "target": target,
        "command": command,
        "cwd": cwd,
        "use_root_helper": elevate,
        "started_at": utc_now(),
    }

    if rule and rule.get("mode") == "deny":
        result.update({
            "status": "denied",
            "returncode": None,
            "stdout": "",
            "stderr": "",
            "finished_at": utc_now(),
            "details": {"rule": rule, "summary": summary},
        })
    elif (not rule or rule.get("mode") == "ask") and approval_mode != "force":
        result.update({
            "status": "approval_required",
            "returncode": None,
            "stdout": "",
            "stderr": "",
            "finished_at": utc_now(),
            "details": {
                "summary": summary,
                "proposed_rule": bridge_host.make_proposed_command_rule(summary),
            },
        })
    else:
        targets = state.get("targets", {})
        target_cfg = targets.get(target, {})
        if target_cfg.get("kind") == "local":
            abs_cwd = repo_root if cwd in ("", ".") else os.path.join(repo_root, bridge_host.normalize_relpath(repo_root, cwd))
            popen_cmd = bridge_host.build_host_command(target_cfg, command, elevate)
        elif target_cfg.get("kind") == "ssh":
            abs_cwd = None
            popen_cmd = bridge_host.build_remote_command(target_cfg, command, cwd)
        else:
            raise bridge_host.HostError("bad_target_kind", f"Unsupported target kind: {target_cfg.get('kind')}")

        env = os.environ.copy()
        env["TERM"] = env.get("TERM", "xterm-256color")
        proc = subprocess.run(
            popen_cmd,
            cwd=abs_cwd,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        result.update({
            "status": "finished",
            "returncode": proc.returncode,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "finished_at": utc_now(),
        })

    outbox_path = os.path.join(paths["outbox"], f"{cmd_id}.json")
    write_json(outbox_path, result)

    final_current = dict(claimed)
    final_current["status"] = result["status"]
    final_current["result_path"] = os.path.relpath(outbox_path, repo_root)
    final_current["finished_at"] = result["finished_at"]
    write_json(current_path, final_current)

    state.setdefault("control", {})["last_result_id"] = cmd_id
    bridge_host.save_state(state)

    sync = bridge_host.sync_control_commit(
        repo_root,
        state,
        [current_path, outbox_path],
        f"Process control command {cmd_id}",
    )
    result["sync"] = sync
    return result


def run_loop(repo_root: str, state_path: str, policy_path: str, once: bool = False) -> int:
    os.environ["KESTREL_REPO_BRIDGE_STATE"] = state_path
    os.environ["KESTREL_REPO_BRIDGE_POLICY"] = policy_path

    stop = False

    def _term(_signum, _frame):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGTERM, _term)
    signal.signal(signal.SIGINT, _term)

    while not stop:
        try:
            policy, state, resolved_repo_root = load_runtime(repo_root)
            control_cfg = state.get("control", {})
            result = execute_once(resolved_repo_root, state, policy)
            print(json.dumps(result, ensure_ascii=False), flush=True)
        except Exception as exc:
            print(json.dumps({"status": "error", "message": str(exc)}), flush=True)
            if once:
                return 1
            time.sleep(5)

        if once:
            return 0

        poll_seconds = max(int(state.get("control", {}).get("poll_interval_seconds", 10)), 2)
        slept = 0
        while slept < poll_seconds and not stop:
            time.sleep(1)
            slept += 1

    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--state-path", required=True)
    parser.add_argument("--policy-path", required=True)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    return run_loop(args.repo_root, args.state_path, args.policy_path, once=args.once)


if __name__ == "__main__":
    raise SystemExit(main())
