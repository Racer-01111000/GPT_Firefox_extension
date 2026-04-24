# Control plane

This directory is the repo-backed control loop for the Firefox broker.

- `inbox/current.json` is the next command intent for the local worker.
- `outbox/<command_id>.json` stores execution results written by the worker.
- `mailbox/user_prompts/<prompt_id>.json` stores prompts submitted from the extension form.
- `claims/` is reserved for future worker locking and leasing.

Expected command shape:

```json
{
  "id": "cmd-20260422-0001",
  "target": "HOST",
  "cwd": ".",
  "command": "git status --short --branch",
  "use_root_helper": false,
  "approval_mode": "auto_if_policy_allows",
  "status": "queued",
  "created_at": "2026-04-22T00:00:00Z"
}
```
