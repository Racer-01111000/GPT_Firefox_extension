(function () {
  const $ = (id) => document.getElementById(id);
  const REPO_ROOT = "/home/rick/GPT_Firefox_extension";

  const els = {
    pingBtn: $("pingBtn"), targetsBtn: $("targetsBtn"), stateBtn: $("stateBtn"), auditBtn: $("auditBtn"), copyStatusBtn: $("copyStatusBtn"), statusOut: $("statusOut"),
    workerStatusBtn: $("workerStatusBtn"), workerStartBtn: $("workerStartBtn"), workerStopBtn: $("workerStopBtn"), workerRunOnceBtn: $("workerRunOnceBtn"), workerResetBtn: $("workerResetBtn"), workerCopyBtn: $("workerCopyBtn"), workerOut: $("workerOut"),
    promptTopic: $("promptTopic"), promptText: $("promptText"), promptSendBtn: $("promptSendBtn"), promptClearBtn: $("promptClearBtn"), promptCopyBtn: $("promptCopyBtn"), promptOut: $("promptOut"),
    memoryKey: $("memoryKey"), memoryValue: $("memoryValue"), memorySaveBtn: $("memorySaveBtn"), memoryViewBtn: $("memoryViewBtn"), memoryCopyBtn: $("memoryCopyBtn"), memoryOut: $("memoryOut"),
    readPath: $("readPath"), readBtn: $("readBtn"), readCopyBtn: $("readCopyBtn"), readOut: $("readOut"),
    patchText: $("patchText"), patchBtn: $("patchBtn"), patchCopyBtn: $("patchCopyBtn"), patchOut: $("patchOut"),
    targetSelect: $("targetSelect"), termCwd: $("termCwd"), termCmd: $("termCmd"), elevateCheck: $("elevateCheck"), termRunBtn: $("termRunBtn"), termPollBtn: $("termPollBtn"), termStopBtn: $("termStopBtn"), termCopyBtn: $("termCopyBtn"), termClearBtn: $("termClearBtn"), termOut: $("termOut"),
    gitStatusBtn: $("gitStatusBtn"), gitDiffBtn: $("gitDiffBtn"), gitAddBtn: $("gitAddBtn"), gitCommitBtn: $("gitCommitBtn"), gitOut: $("gitOut"),
    recordIfGoodCheck: $("recordIfGoodCheck"), currentActionName: $("currentActionName"), currentActionTarget: $("currentActionTarget"), currentRepoRoot: $("currentRepoRoot"), currentActionCwd: $("currentActionCwd"), currentActionCommand: $("currentActionCommand"), currentJobStatus: $("currentJobStatus"), currentJobNext: $("currentJobNext"), currentJobId: $("currentJobId"), currentJobBadge: $("currentJobBadge"),
    resultStatus: $("resultStatus"), resultStarted: $("resultStarted"), resultDuration: $("resultDuration"), resultExitCode: $("resultExitCode"), resultStateBadge: $("resultStateBadge"),
    repairStatus: $("repairStatus"), repairAttempts: $("repairAttempts"), repairEscalation: $("repairEscalation"), repairStateBadge: $("repairStateBadge"),
    gitRepoRoot: $("gitRepoRoot"), gitBranchName: $("gitBranchName"), gitTreeStatus: $("gitTreeStatus"), gitRecordBadge: $("gitRecordBadge"),
  };

  const currentJob = {
    job_id: null,
    action: "run_target_command",
    target: "HOST",
    repo_root: REPO_ROOT,
    status: "idle",
    next_state: "Enter a command below",
    last_result: null,
    session_id: null,
    repair_count: 0,
    git: { eligible: false, recorded: false },
    telegram: { sent: false, awaiting_reply: false },
  };

  function safeString(v) {
    if (v == null) return "";
    if (typeof v === "string") return v;
    try { return JSON.stringify(v, null, 2); } catch (_) { return String(v); }
  }

  function stripAnsi(text) {
    return String(text || "")
      .replace(/\x1B\[[0-?]*[ -/]*[@-~]/g, "")
      .replace(/\u001b\[[0-?]*[ -/]*[@-~]/g, "")
      .replace(/\\r\\n/g, "\n")
      .replace(/\\n/g, "\n")
      .replace(/\r\n/g, "\n");
  }

  function nowStamp() {
    try { return new Date().toLocaleTimeString(); } catch (_) { return "—"; }
  }

  function makeJobId() {
    return "job-" + Date.now();
  }

  function outputState(status) {
    return status === "running" || status === "queued" || status === "repairing"
      ? "is-running"
      : status === "succeeded" || status === "recorded"
      ? "is-success"
      : status === "failed" || status === "needs_user_input" || status === "awaiting_reply"
      ? "is-error"
      : "is-warn";
  }

  function badgeKind(status) {
    return status === "running" || status === "queued" || status === "repairing"
      ? "badge-info"
      : status === "succeeded" || status === "recorded"
      ? "badge-ok"
      : status === "failed" || status === "needs_user_input" || status === "awaiting_reply"
      ? "badge-error"
      : "badge-warn";
  }

  function setOut(el, value, state) {
    if (!el) return;
    el.textContent = safeString(value);
    el.classList.remove("is-running", "is-success", "is-error", "is-warn");
    if (state) el.classList.add(state);
  }

  function setBadge(el, text, kind) {
    if (!el) return;
    el.textContent = text;
    el.classList.remove("badge-ok", "badge-warn", "badge-error", "badge-info");
    if (kind) el.classList.add(kind);
  }

  function copyText(value) {
    return navigator.clipboard.writeText(safeString(value || ""));
  }

  function requireOk(res) {
    if (!res || res.ok === false) {
      throw new Error((res && (res.error || res.stderr || res.summary)) || "Broker call failed");
    }
    return res;
  }

  function normalizeShellCommandInput(raw) {
    let cwd = (els.termCwd?.value ?? ".").trim() || ".";
    let command = raw.trim();

    const cdPrefix = command.match(/^cd\s+([^&]+?)\s*&&\s*(.+)$/s);
    if (cdPrefix) {
      const cdPath = cdPrefix[1].trim().replace(/^['"]|['"]$/g, "");
      command = cdPrefix[2].trim();

      if (cdPath === REPO_ROOT) {
        cwd = ".";
      } else if (cdPath.startsWith(REPO_ROOT + "/")) {
        cwd = cdPath.slice(REPO_ROOT.length + 1) || ".";
      } else {
        cwd = cdPath;
      }

      if (els.termCwd) els.termCwd.value = cwd;
      if (els.termCmd) els.termCmd.value = command;
    }

    const safeNoRoot = /^(pwd|ls(\s|$)|git\s+(status|diff|log|branch)(\s|$))/.test(command);
    if (safeNoRoot && els.elevateCheck?.checked) {
      els.elevateCheck.checked = false;
    }

    return {
      cwd,
      command,
      target: els.targetSelect?.value || "HOST",
      use_root_helper: !!els.elevateCheck?.checked
    };
  }

  function unpackCommandEnvelope() {
    const raw = (els.termCmd?.value ?? "").trim();
    if (!raw.startsWith("{")) {
      return normalizeShellCommandInput(raw);
    }

    try {
      const parsed = JSON.parse(raw);
      const payload = parsed.payload || {};
      const command = typeof payload.command === "string" ? payload.command.trim() : "";
      const cwd = typeof payload.cwd === "string" ? payload.cwd.trim() || "." : ((els.termCwd?.value ?? ".").trim() || ".");
      const target = typeof parsed.target === "string" ? parsed.target : (els.targetSelect?.value || "HOST");
      if (command) {
        if (els.termCwd) els.termCwd.value = cwd;
        if (els.targetSelect) els.targetSelect.value = target;
        if (els.termCmd) els.termCmd.value = command;
        return { cwd, command, target, use_root_helper: !!els.elevateCheck?.checked };
      }
    } catch (_) {}

    return {
      cwd: (els.termCwd?.value ?? ".").trim() || ".",
      command: raw,
      target: els.targetSelect?.value || "HOST",
      use_root_helper: !!els.elevateCheck?.checked
    };
  }

  async function hostCall(action, payload = {}, options = {}) {
    const envelope = {
      job_id: options.job_id || currentJob.job_id || makeJobId(),
      intent: options.intent || "project_scoped_action",
      target: options.target || currentJob.target || "HOST",
      repo_root: options.repo_root || currentJob.repo_root || REPO_ROOT,
      action,
      requires_approval: options.requires_approval ?? false,
      record_if_good: options.record_if_good ?? !!els.recordIfGoodCheck?.checked,
      payload,
    };

    if (!(typeof browser !== "undefined" && browser.runtime && typeof browser.runtime.sendMessage === "function")) {
      throw new Error("browser.runtime.sendMessage is unavailable. Background/bridge is not wired.");
    }

    return browser.runtime.sendMessage(envelope);
  }

  function summarizeData(data) {
    if (data == null) return "";
    if (typeof data === "string") return stripAnsi(data);
    if (typeof data.output === "string" && data.output) return stripAnsi(data.output);
    if (typeof data.stdout === "string" && data.stdout) return stripAnsi(data.stdout);
    if (typeof data.stderr === "string" && data.stderr) return stripAnsi(data.stderr);
    return JSON.stringify(data, null, 2);
  }

  function renderCurrentJob() {
    els.currentActionName && (els.currentActionName.textContent = currentJob.action || "run_target_command");
    els.currentActionTarget && (els.currentActionTarget.textContent = currentJob.target || "HOST");
    els.currentRepoRoot && (els.currentRepoRoot.textContent = currentJob.repo_root || REPO_ROOT);
    els.currentActionCwd && (els.currentActionCwd.textContent = (els.termCwd?.value ?? ".").trim() || ".");
    els.currentActionCommand && (els.currentActionCommand.textContent = (els.termCmd?.value ?? "").trim() || "—");
    els.currentJobStatus && (els.currentJobStatus.textContent = currentJob.status || "idle");
    els.currentJobNext && (els.currentJobNext.textContent = currentJob.next_state || "Enter a command below");
    els.currentJobId && (els.currentJobId.textContent = currentJob.job_id || "—");
    setBadge(els.currentJobBadge, currentJob.status || "idle", badgeKind(currentJob.status));
  }

  function renderResult(res) {
    const status = res?.status || "idle";
    els.resultStatus && (els.resultStatus.textContent = status);
    els.resultStarted && (els.resultStarted.textContent = res?.started_at || "—");
    els.resultDuration && (els.resultDuration.textContent = res?.duration_ms != null ? `${res.duration_ms} ms` : "—");
    els.resultExitCode && (els.resultExitCode.textContent = res?.exit_code != null ? String(res.exit_code) : "—");
    setBadge(els.resultStateBadge, status, badgeKind(status));

    const body =
      res?.stdout ||
      summarizeData(res?.raw?.data) ||
      res?.summary ||
      res?.stderr ||
      safeString(res || "");

    setOut(els.termOut, body, outputState(status));
  }

  function renderRepair(status = "idle", note = "") {
    els.repairStatus && (els.repairStatus.textContent = status);
    els.repairAttempts && (els.repairAttempts.textContent = String(currentJob.repair_count || 0));
    els.repairEscalation && (els.repairEscalation.textContent = currentJob.telegram.awaiting_reply ? "awaiting_reply" : "none");
    setBadge(els.repairStateBadge, status, badgeKind(status));
    if (note) setOut(els.patchOut, note, outputState(status));
  }

  function renderGit(res) {
    if (!res) return;

    els.gitRepoRoot && (els.gitRepoRoot.textContent = res.repo_root || currentJob.repo_root || REPO_ROOT);
    if (els.gitTreeStatus) {
      els.gitTreeStatus.textContent =
        res.summary ||
        res.raw?.data?.branch ||
        "ok";
    }

    const body =
      res.stdout ||
      summarizeData(res.raw?.data) ||
      res.summary ||
      res.stderr ||
      safeString(res);

    setOut(els.gitOut, body, outputState(res.status || "idle"));

    if (res.status === "recorded") setBadge(els.gitRecordBadge, "Recorded", "badge-ok");
    else if (currentJob.git.eligible) setBadge(els.gitRecordBadge, "Eligible", "badge-info");
    else setBadge(els.gitRecordBadge, "Not Recorded", "badge-warn");
  }

  async function runSimple(action, outEl) {
    setOut(outEl, "Running...", "is-running");
    const res = requireOk(await hostCall(action, {}));
    setOut(outEl, summarizeData(res.raw?.data) || res.stdout || res.summary || safeString(res), "is-success");
  }


  async function doResetTask() {
    setOut(els.workerOut, "Resetting task...", "is-running");

    const res = requireOk(await hostCall("reset_control_loop", {}));

    currentJob.job_id = null;
    currentJob.session_id = null;
    currentJob.action = "run_target_command";
    currentJob.status = "idle";
    currentJob.next_state = "Enter a command below";
    currentJob.last_result = null;
    currentJob.repair_count = 0;
    currentJob.git = { eligible: false, recorded: false };
    currentJob.telegram = { sent: false, awaiting_reply: false };

    if (els.targetSelect) els.targetSelect.value = "HOST";
    if (els.termCwd) els.termCwd.value = ".";
    if (els.termCmd) els.termCmd.value = "";
    if (els.elevateCheck) els.elevateCheck.checked = false;
    if (els.recordIfGoodCheck) els.recordIfGoodCheck.checked = false;

    renderCurrentJob();
    renderResult({ status: "idle", summary: "No result yet." });
    renderRepair("idle", "");
    renderGit({ status: "idle", summary: "No Git action yet.", repo_root: REPO_ROOT });

    setOut(els.termOut, "", "");
    setOut(els.patchOut, "", "");
    setOut(els.gitOut, "", "");
    setOut(els.readOut, "", "");
    setOut(els.promptOut, "", "");
    setOut(els.memoryOut, "", "");
    setOut(els.workerOut, summarizeData(res.raw?.data) || res.stdout || res.summary || safeString(res), "is-success");
  }

  async function doReadFile() {
    const path = (els.readPath?.value ?? "").trim();
    if (!path) return setOut(els.readOut, "Error: Read path is empty.", "is-error");
    setOut(els.readOut, "Running...", "is-running");
    const res = requireOk(await hostCall("read_repo_file", { path }));
    setOut(els.readOut, summarizeData(res.raw?.data) || res.stdout || res.summary || safeString(res), "is-success");
  }

  async function doApplyPatch() {
    const patch = (els.patchText?.value ?? "").trim();
    if (!patch) return setOut(els.patchOut, "Patch text is empty.", "is-error");
    currentJob.repair_count += 1;
    renderRepair("repairing", "Running...");
    const res = requireOk(await hostCall("apply_repo_patch", { patch }));
    setOut(els.patchOut, summarizeData(res.raw?.data) || res.stdout || res.summary || safeString(res), "is-success");
    renderRepair("succeeded", res.summary || "Repair applied");
  }

  async function doRunTerminal() {
    const unpacked = unpackCommandEnvelope();
    if (!unpacked.command) return setOut(els.termOut, "Command is empty.", "is-error");

    const job_id = makeJobId();

    Object.assign(currentJob, {
      job_id,
      action: "run_target_command",
      target: unpacked.target,
      status: "running",
      next_state: "Await result",
      session_id: null,
      git: { eligible: !!els.recordIfGoodCheck?.checked, recorded: false }
    });

    renderCurrentJob();
    renderResult({
      status: "running",
      started_at: nowStamp(),
      summary: "Running...",
      stdout: "Running..."
    });

    try {
      const res = requireOk(await hostCall(
        "run_target_command",
        {
          cwd: unpacked.cwd,
          command: unpacked.command,
          use_root_helper: unpacked.use_root_helper
        },
        {
          job_id,
          target: unpacked.target,
          record_if_good: !!els.recordIfGoodCheck?.checked
        }
      ));

      const sessionId =
        res.raw?.data?.session_id ||
        res.raw?.data?.id ||
        res.session_id ||
        res.id ||
        null;

      Object.assign(currentJob, {
        status: res.status || "succeeded",
        next_state: sessionId ? "Auto-polling output" : (res.next_state || "Idle"),
        last_result: res,
        session_id: sessionId
      });

      currentJob.git.eligible = !!res.git?.eligible || !!els.recordIfGoodCheck?.checked;
      renderCurrentJob();

      if (!sessionId) {
        renderResult(res);
        renderGit(res);
        return;
      }

      await new Promise((resolve) => setTimeout(resolve, 350));

      const polled = requireOk(await hostCall(
        "poll_job",
        { session_id: sessionId },
        { job_id: currentJob.job_id }
      ));

      Object.assign(currentJob, {
        status: polled.status || "succeeded",
        next_state: polled.raw?.data?.done ? "Idle" : "Poll for more output",
        last_result: polled
      });

      renderCurrentJob();
      renderResult(polled);
      renderGit(polled);
    } catch (err) {
      Object.assign(currentJob, {
        status: "failed",
        next_state: "Repair or ask user",
        last_result: { error: err.message }
      });

      renderCurrentJob();
      renderResult({
        status: "failed",
        started_at: nowStamp(),
        exit_code: 1,
        summary: err.message,
        stderr: err.message
      });
      renderRepair("failed", err.message);
    }
  }

  async function doPollTerminal() {
    if (!currentJob.job_id) return setOut(els.termOut, "No active job.", "is-warn");
    setOut(els.termOut, "Running...", "is-running");
    const sessionId = currentJob.session_id || currentJob.job_id;
    const res = requireOk(await hostCall("poll_job", { session_id: sessionId }, { job_id: currentJob.job_id }));
    Object.assign(currentJob, {
      status: res.status || currentJob.status,
      next_state: res.next_state || currentJob.next_state,
      last_result: res
    });
    renderCurrentJob();
    renderResult(res);
  }

  async function doStopTerminal() {
    if (!currentJob.job_id) return setOut(els.termOut, "No active job to stop.", "is-warn");
    const sessionId = currentJob.session_id || currentJob.job_id;
    const res = requireOk(await hostCall("stop_job", { session_id: sessionId }, { job_id: currentJob.job_id }));
    Object.assign(currentJob, {
      status: res.status || "failed",
      next_state: "Stopped",
      last_result: res
    });
    renderCurrentJob();
    renderResult(res);
  }

  async function doGitAction(action) {
    setOut(els.gitOut, "Running...", "is-running");

    let payload;
    if (action === "git_status") {
      payload = {};
    } else if (action === "git_diff") {
      payload = { args: ["diff"] };
    } else if (action === "git_add") {
      payload = { args: ["add", "."] };
    } else if (action === "git_commit") {
      payload = { args: ["commit", "-m", "bridge: record approved change"] };
    } else {
      payload = {};
    }

    const res = requireOk(await hostCall(action, payload, {
      target: els.targetSelect?.value || "HOST",
      record_if_good: !!els.recordIfGoodCheck?.checked
    }));

    if (action === "git_commit") {
      currentJob.git.recorded = true;
      currentJob.status = "recorded";
      currentJob.next_state = "Recorded in Git";
      renderCurrentJob();
      res.status = res.status || "recorded";
    }

    renderGit(res);
  }

  function bind(btn, fn, outEl) {
    if (!btn) return;
    btn.addEventListener("click", async () => {
      try { await fn(); }
      catch (err) {
        console.error(err);
        setOut(outEl || els.statusOut || els.termOut, err?.message || String(err), "is-error");
      }
    });
  }

  bind(els.pingBtn, () => runSimple("ping_host", els.statusOut), els.statusOut);
  bind(els.stateBtn, () => runSimple("state", els.statusOut), els.statusOut);
  bind(els.targetsBtn, () => runSimple("targets", els.statusOut), els.statusOut);
  bind(els.auditBtn, () => runSimple("audit_tail", els.statusOut), els.statusOut);

  bind(els.workerStatusBtn, () => runSimple("worker_status", els.workerOut), els.workerOut);
  bind(els.workerStartBtn, () => runSimple("worker_start", els.workerOut), els.workerOut);
  bind(els.workerStopBtn, () => runSimple("worker_stop", els.workerOut), els.workerOut);
  bind(els.workerRunOnceBtn, () => runSimple("worker_run_once", els.workerOut), els.workerOut);
  bind(els.workerResetBtn, doResetTask, els.workerOut);

  bind(els.readBtn, doReadFile, els.readOut);
  bind(els.patchBtn, doApplyPatch, els.patchOut);
  bind(els.termRunBtn, doRunTerminal, els.termOut);
  bind(els.termPollBtn, doPollTerminal, els.termOut);
  bind(els.termStopBtn, doStopTerminal, els.termOut);

  bind(els.gitStatusBtn, () => doGitAction("git_status"), els.gitOut);
  bind(els.gitDiffBtn, () => doGitAction("git_diff"), els.gitOut);
  bind(els.gitAddBtn, () => doGitAction("git_add"), els.gitOut);
  bind(els.gitCommitBtn, () => doGitAction("git_commit"), els.gitOut);

  els.promptSendBtn && els.promptSendBtn.addEventListener("click", async () => {
    try {
      const prompt = (els.promptText?.value ?? "").trim();
      const topic = (els.promptTopic?.value ?? "").trim();
      if (!prompt) return setOut(els.promptOut, "Prompt is empty.", "is-error");
      setOut(els.promptOut, "Running...", "is-running");
      const res = requireOk(await hostCall("prompt_mailbox_send", {
        prompt,
        meta: topic ? { topic } : {}
      }));
      setOut(els.promptOut, summarizeData(res.raw?.data) || res.stdout || res.summary || safeString(res), "is-success");
    } catch (err) {
      setOut(els.promptOut, err.message, "is-error");
    }
  });

  els.promptClearBtn && els.promptClearBtn.addEventListener("click", () => {
    els.promptTopic && (els.promptTopic.value = "");
    els.promptText && (els.promptText.value = "");
    setOut(els.promptOut, "Prompt cleared.", "is-success");
  });

  els.promptCopyBtn && els.promptCopyBtn.addEventListener("click", () => copyText(els.promptOut?.textContent || ""));

  els.memorySaveBtn && els.memorySaveBtn.addEventListener("click", async () => {
    try {
      const key = (els.memoryKey?.value ?? "").trim();
      const value = (els.memoryValue?.value ?? "").trim();
      if (!key) return setOut(els.memoryOut, "Memory key is empty.", "is-error");
      setOut(els.memoryOut, "Running...", "is-running");
      const res = requireOk(await hostCall("memory_save", { key, value }));
      setOut(els.memoryOut, summarizeData(res.raw?.data) || res.stdout || res.summary || safeString(res), "is-success");
    } catch (err) {
      setOut(els.memoryOut, err.message, "is-error");
    }
  });

  els.memoryViewBtn && els.memoryViewBtn.addEventListener("click", async () => {
    try {
      setOut(els.memoryOut, "Running...", "is-running");
      const res = requireOk(await hostCall("memory_view", {}));
      setOut(els.memoryOut, summarizeData(res.raw?.data) || res.stdout || res.summary || safeString(res), "is-success");
    } catch (err) {
      setOut(els.memoryOut, err.message, "is-error");
    }
  });

  els.memoryCopyBtn && els.memoryCopyBtn.addEventListener("click", () => copyText(els.memoryOut?.textContent || ""));

  els.copyStatusBtn && els.copyStatusBtn.addEventListener("click", () => copyText(els.statusOut?.textContent || ""));
  els.workerCopyBtn && els.workerCopyBtn.addEventListener("click", () => copyText(els.workerOut?.textContent || ""));
  els.readCopyBtn && els.readCopyBtn.addEventListener("click", () => copyText(els.readOut?.textContent || ""));
  els.patchCopyBtn && els.patchCopyBtn.addEventListener("click", () => copyText(els.patchOut?.textContent || ""));
  els.termCopyBtn && els.termCopyBtn.addEventListener("click", () => copyText(els.termOut?.textContent || ""));
  els.termClearBtn && els.termClearBtn.addEventListener("click", () => setOut(els.termOut, "", ""));

  ["targetSelect", "termCwd", "termCmd"].forEach((id) => {
    const el = $(id);
    if (!el) return;
    el.addEventListener("input", renderCurrentJob);
    el.addEventListener("change", renderCurrentJob);
  });

  renderCurrentJob();
  renderResult({ status: "idle", summary: "No result yet." });
  renderRepair("idle", "");
  renderGit({ status: "idle", summary: "No Git action yet.", repo_root: REPO_ROOT });
  setOut(els.statusOut, ["Bridge Ready", `Repo ${REPO_ROOT}`, "Scope Repo Root", "Default HOST"].join("\n"), "is-success");
})();
