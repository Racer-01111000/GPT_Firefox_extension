const $ = (id) => document.getElementById(id);

let currentSessionId = null;
let pollTimer = null;

async function hostCall(payload) {
  return browser.runtime.sendMessage({ type: "host_call", payload });
}

function fmt(obj) {
  return typeof obj === "string" ? obj : JSON.stringify(obj, null, 2);
}

function appendTerm(text) {
  const el = $("termOut");
  if (!el) return;
  el.textContent += text;
  el.scrollTop = el.scrollHeight;
}

function setTerm(text) {
  const el = $("termOut");
  if (!el) return;
  el.textContent = text;
  el.scrollTop = el.scrollHeight;
}

async function ping() {
  const res = await hostCall({ action: "ping" });
  $("statusOut").textContent = fmt(res);
}

async function listTargets() {
  const res = await hostCall({ action: "list_targets" });
  $("statusOut").textContent = fmt(res);
}

async function getState() {
  const res = await hostCall({ action: "get_state" });
  $("statusOut").textContent = fmt(res);
}

async function auditTail() {
  const res = await hostCall({ action: "get_audit_tail", lines: 20 });
  $("statusOut").textContent = fmt(res);
}

async function readFile() {
  const path = $("readPath").value.trim();
  const res = await hostCall({ action: "read_file", path });
  $("readOut").textContent = fmt(res);
}

async function rememberKeyValue() {
  const key = $("memoryKey").value.trim();
  const value = $("memoryValue").value;
  const res = await hostCall({ action: "memory_set", key, value });
  $("memoryOut").textContent = fmt(res);
}

async function viewMemory() {
  const res = await hostCall({ action: "memory_get_all" });
  $("memoryOut").textContent = fmt(res);
}

async function approveDecision(errorObj) {
  const typed = window.prompt(
    "Approval required. Type one of: once, session, always, deny",
    "once"
  );
  if (!typed) return null;
  const decision = typed.trim().toLowerCase();
  if (!["once", "session", "always", "deny"].includes(decision)) {
    window.alert("Invalid decision.");
    return null;
  }
  return hostCall({
    action: "approve_request",
    decision_token: errorObj.decision_token,
    decision,
    rule: errorObj.proposed_rule,
  });
}

async function applyPatch() {
  const patch = $("patchText").value;
  const res = await hostCall({ action: "apply_patch", patch });
  if (res && res.ok) {
    $("patchOut").textContent = fmt(res);
    return;
  }
  if (res && res.error && res.error.code === "approval_required") {
    const decided = await approveDecision(res.error);
    $("patchOut").textContent = fmt(decided || res);
    return;
  }
  $("patchOut").textContent = fmt(res);
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

function startPolling(sessionId) {
  currentSessionId = sessionId;
  stopPolling();
  pollTimer = setInterval(pollSession, 800);
}

async function runCommand() {
  const target = $("targetSelect").value;
  const cwd = $("termCwd").value.trim() || ".";
  const command = $("termCmd").value.trim();
  const elevate = $("elevateCheck").checked;
  if (!command) return;
  const res = await hostCall({ action: "run_target_command", target, cwd, command, elevate });
  if (res && res.ok && res.data) {
    setTerm(res.data.initial_output || "");
    startPolling(res.data.session_id);
    return;
  }
  if (res && res.error && res.error.code === "approval_required") {
    const decided = await approveDecision(res.error);
    if (decided && decided.ok && decided.data) {
      setTerm(decided.data.initial_output || "");
      startPolling(decided.data.session_id);
      return;
    }
    appendTerm("\n" + fmt(decided || res) + "\n");
    return;
  }
  appendTerm("\n" + fmt(res) + "\n");
}

async function pollSession() {
  if (!currentSessionId) return;
  const res = await hostCall({ action: "poll_terminal_session", session_id: currentSessionId });
  if (!res || !res.ok) {
    appendTerm("\n" + fmt(res) + "\n");
    stopPolling();
    return;
  }
  const data = res.data || {};
  if (data.output) appendTerm(data.output);
  if (data.done) {
    appendTerm(`\n[session finished: returncode=${data.returncode}]\n`);
    stopPolling();
  }
}

async function stopSession() {
  if (!currentSessionId) return;
  const res = await hostCall({ action: "stop_terminal_session", session_id: currentSessionId });
  appendTerm("\n" + fmt(res) + "\n");
  stopPolling();
}

async function copyTermOutput() {
  const text = $("termOut").textContent || "";
  try {
    await navigator.clipboard.writeText(text);
  } catch (err) {
    window.alert("Clipboard copy failed.");
  }
}

function clearTermOutput() {
  setTerm("");
}

$("pingBtn")?.addEventListener("click", ping);
$("targetsBtn")?.addEventListener("click", listTargets);
$("stateBtn")?.addEventListener("click", getState);
$("auditBtn")?.addEventListener("click", auditTail);
$("readBtn")?.addEventListener("click", readFile);
$("memorySaveBtn")?.addEventListener("click", rememberKeyValue);
$("memoryViewBtn")?.addEventListener("click", viewMemory);
$("patchBtn")?.addEventListener("click", applyPatch);
$("termRunBtn")?.addEventListener("click", runCommand);
$("termPollBtn")?.addEventListener("click", pollSession);
$("termStopBtn")?.addEventListener("click", stopSession);
$("termCopyBtn")?.addEventListener("click", copyTermOutput);
$("termClearBtn")?.addEventListener("click", clearTermOutput);

ping();


(function installCopyHelpers() {
  function makeSelectable(id) {
    const el = document.getElementById(id);
    if (!el) return;
    el.style.userSelect = "text";
    el.style.MozUserSelect = "text";
    el.style.webkitUserSelect = "text";
    el.style.cursor = "text";
  }

  async function copyFrom(id) {
    const el = document.getElementById(id);
    if (!el) return;
    const text = el.textContent || "";
    try {
      await navigator.clipboard.writeText(text);
    } catch (err) {
      window.alert("Clipboard copy failed.");
    }
  }

  function addButtonRowButton(row, id, label, handler) {
    if (!row || document.getElementById(id)) return;
    const btn = document.createElement("button");
    btn.id = id;
    btn.textContent = label;
    btn.addEventListener("click", handler);
    row.appendChild(btn);
  }

  function install() {
    makeSelectable("statusOut");
    makeSelectable("readOut");
    makeSelectable("patchOut");
    makeSelectable("memoryOut");
    makeSelectable("termOut");

    const firstRow = document.querySelector(".card .row");
    addButtonRowButton(firstRow, "copyStatusBtn", "Copy status", () => copyFrom("statusOut"));
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", install);
  } else {
    install();
  }
})();
