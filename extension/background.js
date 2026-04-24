const HOST_NAME = "com.echocore.repo_bridge";

console.log("BACKGROUND LOADED");

let nativePort = null;
let nextId = 1;
const pending = new Map();

function nowIso() {
  return new Date().toISOString();
}

function resetNativePort() {
  nativePort = null;
}

function buildErrorResponse(code, message, details = {}, job = null) {
  return {
    ok: false,
    job_id: job?.job_id ?? null,
    action: job?.action ?? null,
    target: job?.target ?? null,
    status: "failed",
    next_state: "needs_user_input",
    repo_root: job?.repo_root ?? null,
    started_at: details?.started_at ?? null,
    finished_at: nowIso(),
    duration_ms: details?.duration_ms ?? null,
    exit_code: details?.exit_code ?? null,
    summary: message,
    stdout: "",
    stderr: message,
    artifacts: [],
    telegram: { sent: false, reason: null },
    git: {
      eligible: Boolean(job?.record_if_good),
      recorded: false
    },
    error: { code, message, details }
  };
}

function ensureNativePort() {
  if (nativePort) return nativePort;

  console.log("Connecting to native host:", HOST_NAME);
  nativePort = browser.runtime.connectNative(HOST_NAME);

  nativePort.onMessage.addListener((msg) => {
    console.log("NATIVE MESSAGE:", msg);
    const id = msg && msg.id;
    if (!id || !pending.has(id)) {
      console.warn("Ignoring native message with unknown id:", msg);
      return;
    }
    const handlers = pending.get(id);
    pending.delete(id);
    handlers.resolve(msg);
  });

  nativePort.onDisconnect.addListener(() => {
    const errMessage =
      (browser.runtime.lastError && browser.runtime.lastError.message) ||
      "Native host disconnected";

    console.error("NATIVE DISCONNECT:", errMessage);

    for (const [, handlers] of pending.entries()) {
      handlers.reject(new Error(errMessage));
    }

    pending.clear();
    resetNativePort();
  });

  return nativePort;
}

function looksLikeDirectJob(msg) {
  return !!(
    msg &&
    typeof msg === "object" &&
    typeof msg.action === "string" &&
    typeof msg.target === "string"
  );
}

function normalizeIncomingMessage(msg) {
  if (!msg || typeof msg !== "object") return null;

  if (looksLikeDirectJob(msg)) {
    return {
      job_id: msg.job_id || `job-${Date.now()}-${nextId}`,
      intent: msg.intent || "project_scoped_action",
      target: msg.target || "HOST",
      repo_root: msg.repo_root || null,
      action: msg.action || null,
      requires_approval: Boolean(msg.requires_approval),
      record_if_good: Boolean(msg.record_if_good),
      telegram_on: Array.isArray(msg.telegram_on) ? msg.telegram_on : [],
      payload: typeof msg.payload === "object" && msg.payload ? { ...msg.payload } : {}
    };
  }

  if (msg.type === "host_call") {
    if (msg.job && typeof msg.job === "object") return msg.job;

    if (msg.payload && typeof msg.payload === "object") {
      return {
        job_id: `legacy-${Date.now()}-${nextId}`,
        intent: "legacy_host_call",
        target: msg.payload.target || "HOST",
        repo_root: msg.payload.repo_root || null,
        action: msg.payload.action || null,
        requires_approval: false,
        record_if_good: false,
        telegram_on: [],
        payload: { ...msg.payload }
      };
    }
  }

  return null;
}

function mapActionForHost(action, payload) {
  const aliases = {
    ping_host: "ping",
    bridge_state: "get_state",
    state: "get_state",
    list_targets: "list_targets",
    targets: "list_targets",
    audit_tail: "get_audit_tail",
    worker_status: "get_worker_status",
    worker_start: "start_control_worker",
    worker_stop: "stop_control_worker",
    worker_run_once: "worker_run_once",
    prompt_mailbox_send: "submit_prompt",
    send_prompt: "submit_prompt",
    memory_save: "memory_set",
    save_memory: "memory_set",
    memory_view: "memory_get_all",
    view_memory: "memory_get_all",
    read_repo_file: "read_file",
    apply_repo_patch: "apply_patch",
    poll_job: "poll_terminal_session",
    stop_job: "stop_terminal_session"
  };

  if (action === "git_diff" || action === "git_add" || action === "git_commit") {
    return "run_git";
  }

  return aliases[action] || action;
}

function mapPayloadForHost(action, payload) {
  const out = { ...(payload || {}) };

  if (action === "git_diff") {
    return { args: ["diff"] };
  }

  if (action === "git_add") {
    return { args: ["add", "."] };
  }

  if (action === "git_commit") {
    return {
      args: ["commit", "-m", "bridge: record approved change"]
    };
  }

  if (action === "memory_save") {
    return {
      key: out.key ?? "",
      value: out.value ?? ""
    };
  }

  return out;
}

function flattenJobForHost(job) {
  const originalPayload = { ...(job.payload || {}) };
  const mappedAction = mapActionForHost(job.action, originalPayload);
  const payload = mapPayloadForHost(job.action, originalPayload);

  return {
    ...payload,
    action: mappedAction,
    target: job.target ?? payload.target ?? "HOST",
    repo_root: job.repo_root ?? payload.repo_root ?? null,
    intent: job.intent ?? null,
    requires_approval: Boolean(job.requires_approval),
    record_if_good: Boolean(job.record_if_good),
    telegram_on: Array.isArray(job.telegram_on) ? job.telegram_on : []
  };
}

function inferStatusFromHostResponse(response) {
  if (response?.ok === false) return "failed";
  return "succeeded";
}

function inferSummary(response, action) {
  if (response?.summary) return response.summary;
  if (response?.error?.message) return response.error.message;
  if (response?.ok === false) return `${action || "Action"} failed`;
  return `${action || "Action"} completed`;
}

function extractStdout(response) {
  if (typeof response?.stdout === "string") return response.stdout;
  if (typeof response?.data?.stdout === "string") return response.data.stdout;
  return "";
}

function extractStderr(response) {
  if (typeof response?.stderr === "string") return response.stderr;
  if (typeof response?.data?.stderr === "string") return response.data.stderr;
  if (typeof response?.error?.message === "string") return response.error.message;
  return "";
}

function extractExitCode(response) {
  if (typeof response?.exit_code === "number") return response.exit_code;
  if (typeof response?.data?.exit_code === "number") return response.data.exit_code;
  return null;
}

function extractArtifacts(response) {
  if (Array.isArray(response?.artifacts)) return response.artifacts;
  if (Array.isArray(response?.data?.artifacts)) return response.data.artifacts;
  return [];
}

function extractNextState(job, response, status) {
  if (status === "failed") return "repair";
  if (job.record_if_good && status === "succeeded") return "record";
  return "idle";
}

function normalizeHostResponse(job, hostResponse, startedAtMs) {
  const finishedAt = nowIso();
  const durationMs = Date.now() - startedAtMs;
  const status = inferStatusFromHostResponse(hostResponse);

  return {
    ok: hostResponse?.ok !== false,
    job_id: job.job_id ?? null,
    action: job.action ?? null,
    target: job.target ?? null,
    status,
    next_state: extractNextState(job, hostResponse, status),
    repo_root: job.repo_root ?? null,
    started_at: new Date(startedAtMs).toISOString(),
    finished_at: finishedAt,
    duration_ms: durationMs,
    exit_code: extractExitCode(hostResponse),
    summary: inferSummary(hostResponse, job.action),
    stdout: extractStdout(hostResponse),
    stderr: extractStderr(hostResponse),
    artifacts: extractArtifacts(hostResponse),
    telegram: { sent: false, reason: null },
    git: {
      eligible: Boolean(job.record_if_good),
      recorded: false
    },
    raw: hostResponse
  };
}

function callHost(job) {
  return new Promise((resolve, reject) => {
    const port = ensureNativePort();
    const id = `msg-${Date.now()}-${nextId++}`;
    const hostPayload = flattenJobForHost(job);

    pending.set(id, { resolve, reject });

    try {
      console.log("POSTING TO HOST:", { id, ...hostPayload });
      port.postMessage({ id, ...hostPayload });
    } catch (err) {
      pending.delete(id);
      reject(err);
    }
  });
}

browser.runtime.onMessage.addListener((msg) => {
  const job = normalizeIncomingMessage(msg);

  if (!job) {
    return Promise.resolve(
      buildErrorResponse(
        "invalid_message",
        "Unrecognized message format",
        { received: msg },
        null
      )
    );
  }

  const startedAtMs = Date.now();

  return callHost(job)
    .then((hostResponse) => normalizeHostResponse(job, hostResponse, startedAtMs))
    .catch((err) =>
      buildErrorResponse(
        "host_call_failed",
        err?.message || "Broker call failed",
        { started_at: new Date(startedAtMs).toISOString() },
        job
      )
    );
});
