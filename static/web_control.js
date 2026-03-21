(function () {
  const initialElement = document.getElementById("initial-bootstrap");
  if (!initialElement) {
    return;
  }

  const currentRole = document.body.dataset.currentRole || "viewer";
  const roleRank = { viewer: 10, operator: 20, owner: 30 };
  const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || "";
  let state = JSON.parse(initialElement.textContent || "{}");
  const selected = {
    hostId: null,
    dopletId: null,
    networkId: null,
    providerId: null,
    snapshotId: null,
    taskId: null,
  };
  let didAutofillHostDraft = false;

  const byId = (id) => document.getElementById(id);
  const hasRole = (role) => (roleRank[currentRole] || 0) >= (roleRank[role] || 0);
  const localMachine = () => state.local_machine || {};

  const flash = (message, tone = "info") => {
    const node = byId("flash");
    if (!node) {
      return;
    }
    node.textContent = message;
    node.className = `flash ${tone === "info" ? "" : tone}`.trim();
    node.classList.remove("hidden");
    window.clearTimeout(flash._timer);
    flash._timer = window.setTimeout(() => node.classList.add("hidden"), 5000);
  };

  function slugify(value) {
    return String(value || "")
      .trim()
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "") || "host";
  }

  function bestLocalAddress(machine = localMachine()) {
    return (machine.ip_candidates || [])[0] || machine.fqdn || machine.hostname || "";
  }

  function normalizeErrorText(raw, fallback) {
    const stripped = String(raw || "")
      .replace(/<style[\s\S]*?<\/style>/gi, " ")
      .replace(/<script[\s\S]*?<\/script>/gi, " ")
      .replace(/<[^>]+>/g, " ")
      .replace(/\s+/g, " ")
      .trim();
    return stripped || fallback;
  }

  function escapeHtml(value) {
    return String(value || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function buildSshTarget(payload = {}) {
    const machine = localMachine();
    const host = String(payload.ssh_host || bestLocalAddress(machine) || "").trim();
    const user = String(payload.ssh_user || machine.username || "").trim();
    const port = Number(payload.ssh_port || 22) || 22;
    if (!host) {
      return "";
    }
    const authority = user ? `${user}@${host}` : host;
    return port !== 22 ? `${authority}:${port}` : authority;
  }

  function buildSshCommand(payload = {}) {
    const machine = localMachine();
    const host = String(payload.ssh_host || bestLocalAddress(machine) || "").trim();
    const user = String(payload.ssh_user || machine.username || "").trim();
    const port = Number(payload.ssh_port || 22) || 22;
    if (!host) {
      return "";
    }
    const authority = user ? `${user}@${host}` : host;
    return `ssh ${port !== 22 ? `-p ${port} ` : ""}${authority}`.trim();
  }

  async function copyText(value, label) {
    const text = String(value || "").trim();
    if (!text) {
      flash(`No ${label.toLowerCase()} available yet.`, "warn");
      return;
    }
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(text);
      } else {
        throw new Error("Clipboard API unavailable");
      }
    } catch (_error) {
      const input = document.createElement("textarea");
      input.value = text;
      input.setAttribute("readonly", "readonly");
      input.style.position = "fixed";
      input.style.left = "-9999px";
      document.body.appendChild(input);
      input.focus();
      input.select();
      let copied = false;
      try {
        copied = document.execCommand("copy");
      } catch (_copyError) {
        copied = false;
      }
      input.remove();
      if (!copied) {
        window.prompt(`Copy ${label}`, text);
      }
    }
    flash(`${label} copied.`);
  }

  async function api(url, options = {}) {
    const response = await fetch(url, {
      method: options.method || "GET",
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": csrfToken,
        ...(options.headers || {}),
      },
      body: options.body ? JSON.stringify(options.body) : undefined,
    });
    if (!response.ok) {
      const text = await response.text();
      let message = "";
      try {
        const payload = JSON.parse(text);
        message = payload.error || payload.message || "";
      } catch (_error) {
        message = normalizeErrorText(text, "");
      }
      throw new Error(message || `${response.status} ${response.statusText}`);
    }
    if (response.status === 204) {
      return {};
    }
    return response.json();
  }

  async function reloadBootstrap(message) {
    state = await api("/api/bootstrap");
    renderAll();
    if (message) {
      flash(message);
    }
  }

  function optionMarkup(items, valueKey, labelFn, placeholder) {
    const rows = [`<option value="">${placeholder}</option>`];
    for (const item of items) {
      rows.push(`<option value="${item[valueKey]}">${labelFn(item)}</option>`);
    }
    return rows.join("");
  }

  function tableMarkup(headers, rows, emptyText) {
    const thead = `<thead><tr>${headers.map((value) => `<th>${value}</th>`).join("")}</tr></thead>`;
    const tbody = rows.length ? `<tbody>${rows.join("")}</tbody>` : `<tbody><tr><td colspan="${headers.length}" class="muted">${emptyText}</td></tr></tbody>`;
    return thead + tbody;
  }

  function hostById(id) {
    return (state.hosts || []).find((item) => Number(item.id) === Number(id));
  }

  function readyHosts() {
    return (state.hosts || []).filter((item) => item.status === "ready");
  }

  function preferredHostId(explicitValue = "") {
    if (String(explicitValue || "").trim()) {
      return String(explicitValue);
    }
    if (selected.hostId && hostById(selected.hostId)) {
      return String(selected.hostId);
    }
    const ready = readyHosts();
    if (ready.length) {
      return String(ready[0].id);
    }
    if ((state.hosts || []).length) {
      return String(state.hosts[0].id);
    }
    return "";
  }

  function preferredImageId(explicitValue = "") {
    if (String(explicitValue || "").trim()) {
      return String(explicitValue);
    }
    const preferred = (state.images || []).find((item) => item.slug === "ubuntu-24-04-lts") || (state.images || [])[0];
    return preferred ? String(preferred.id) : "";
  }

  function preferredFlavorId(explicitValue = "") {
    if (String(explicitValue || "").trim()) {
      return String(explicitValue);
    }
    const preferred = (state.flavors || []).find((item) => item.slug === "micro") || (state.flavors || [])[0];
    return preferred ? String(preferred.id) : "";
  }

  function preferredDopletId(explicitValue = "") {
    if (String(explicitValue || "").trim()) {
      return Number(explicitValue) || null;
    }
    const records = state.doplets || [];
    const preferred = records.find((item) => ["running", "provisioning", "stopped", "draft", "error"].includes(String(item.status || "").toLowerCase()))
      || records.find((item) => String(item.status || "").toLowerCase() !== "deleted")
      || records[0];
    return preferred ? Number(preferred.id) : null;
  }

  function isDeletedDoplet(item) {
    return String(item?.status || "").toLowerCase() === "deleted";
  }

  function dopletById(id) {
    return (state.doplets || []).find((item) => Number(item.id) === Number(id));
  }

  function networkById(id) {
    return (state.networks || []).find((item) => Number(item.id) === Number(id));
  }

  function snapshotById(id) {
    return (state.snapshots || []).find((item) => Number(item.id) === Number(id));
  }

  function imageById(id) {
    return (state.images || []).find((item) => Number(item.id) === Number(id));
  }

  function taskById(id) {
    return (state.tasks || []).find((item) => Number(item.id) === Number(id));
  }

  function latestTaskForDoplet(dopletId) {
    return (state.tasks || []).find(
      (item) => item.target_type === "doplet" && Number(item.target_id) === Number(dopletId)
    );
  }

  function flavorById(id) {
    return (state.flavors || []).find((item) => Number(item.id) === Number(id));
  }

  function parseJsonField(value, fallback) {
    const trimmed = String(value || "").trim();
    if (!trimmed) {
      return fallback;
    }
    return JSON.parse(trimmed);
  }

  function splitLines(value) {
    return String(value || "")
      .split(/\r?\n/)
      .map((item) => item.trim())
      .filter(Boolean);
  }

  function fillForm(form, payload) {
    for (const element of form.elements) {
      if (!element.name) {
        continue;
      }
      const value = payload[element.name];
      if (element.type === "checkbox") {
        element.checked = Boolean(value);
      } else if (Array.isArray(value)) {
        element.value = value.join("\n");
      } else if (typeof value === "object" && value !== null) {
        element.value = JSON.stringify(value, null, 2);
      } else {
        element.value = value ?? "";
      }
    }
  }

  function fillHostForm(payload) {
    const form = byId("host-form");
    fillForm(form, payload || {});
    const config = payload?.config || {};
    form.elements.config_runtime_root.value = config.runtime_root || "/var/lib/vpsdash";
    form.elements.config_zfs_pool.value = config.zfs_pool || "tank";
    form.elements.config_zfs_dataset_root.value = config.zfs_dataset_root || "doplets";
    form.elements.config_lvm_vg.value = config.lvm_vg || "vg_vpsdash";
    form.elements.config_lvm_thinpool.value = config.lvm_thinpool || "thinpool";
    form.elements.config_libvirt_network.value = config.libvirt_network || "default";
    form.elements.config_reserve_cpu_threads.value = config.reserve_cpu_threads ?? 1;
    form.elements.config_reserve_ram_mb.value = config.reserve_ram_mb ?? 2048;
    form.elements.config_reserve_disk_gb.value = config.reserve_disk_gb ?? 20;
    form.elements.config_zfs_devices.value = (config.zfs_devices || []).join("\n");
    form.elements.config_lvm_devices.value = (config.lvm_devices || []).join("\n");
    renderHostModeHelp();
    renderHostCapabilityReadout(payload);
  }

  function renderLocalMachineInfo() {
    const node = byId("host-local-info");
    if (!node) {
      return;
    }
    const machine = localMachine();
    const wslDistributions = machine.wsl_distributions || [];
    node.innerHTML = [
      `<strong>${machine.hostname || "Unknown machine"}</strong>`,
      machine.platform || "Unknown platform",
      machine.username ? `Local user: ${machine.username}` : "",
      machine.fqdn && machine.fqdn !== machine.hostname ? `FQDN: ${machine.fqdn}` : "",
      (machine.ip_candidates || []).length ? `IPv4: ${(machine.ip_candidates || []).join(", ")}` : "IPv4: none detected",
      wslDistributions.length ? `WSL distros: ${wslDistributions.join(", ")}` : (String(machine.platform || "").toLowerCase().includes("windows") ? "WSL distros: none detected yet" : ""),
    ]
      .filter(Boolean)
      .join("<br>");
  }

  function renderHostSshReadout() {
    const node = byId("host-ssh-readout");
    const form = byId("host-form");
    if (!node || !form) {
      return;
    }
    const machine = localMachine();
    const hostPayload = {
      host_mode: form.elements.host_mode.value,
      ssh_host: form.elements.ssh_host.value,
      ssh_user: form.elements.ssh_user.value,
      ssh_port: Number(form.elements.ssh_port.value || 22),
      wsl_distribution: form.elements.wsl_distribution.value,
    };
    const target = buildSshTarget(hostPayload);
    const command = buildSshCommand(hostPayload);
    const mode = hostPayload.host_mode || "linux-hypervisor";
    node.innerHTML = [
      `Target: <strong>${target || "not set"}</strong>`,
      command ? `Command: <code>${command}</code>` : "Command: fill SSH host or prefill this machine first",
      mode === "windows-local" || mode === "windows-remote"
        ? `WSL distro: ${hostPayload.wsl_distribution || machine.recommended_wsl_distribution || "Ubuntu"}`
        : "",
    ]
      .filter(Boolean)
      .join("<br>");
  }

  function applyLocalMachineToHostForm(modeOverride) {
    const form = byId("host-form");
    const machine = localMachine();
    const platformText = String(machine.platform || "").toLowerCase();
    const resolvedMode = modeOverride || (platformText.includes("windows") ? "windows-local" : "linux-local");
    const suggestedName = machine.hostname || (resolvedMode === "windows-local" ? "Windows Host" : "Linux Host");
    form.elements.name.value = suggestedName;
    if (!String(form.elements.slug.value || "").trim() || form.elements.slug.dataset.autofilled === "true") {
      form.elements.slug.value = slugify(suggestedName);
      form.elements.slug.dataset.autofilled = "true";
    }
    form.elements.host_mode.value = resolvedMode;
    form.elements.distro.value = "ubuntu-server-lts";
    form.elements.primary_storage_backend.value = "files";
    form.elements.wsl_distribution.value = machine.recommended_wsl_distribution || form.elements.wsl_distribution.value || "Ubuntu";
    form.elements.ssh_user.value = machine.username || "";
    form.elements.ssh_host.value = bestLocalAddress(machine);
    form.elements.ssh_port.value = 22;
    renderHostModeHelp();
    renderHostCapabilityReadout(null);
    flash(`Host form filled from ${machine.hostname || "this machine"}.`);
  }

  function fillSshTargetFromLocalMachine() {
    const form = byId("host-form");
    const machine = localMachine();
    form.elements.ssh_user.value = machine.username || form.elements.ssh_user.value || "";
    form.elements.ssh_host.value = bestLocalAddress(machine);
    form.elements.ssh_port.value = 22;
    renderHostSshReadout();
    flash("SSH target filled from this machine.");
  }

  function renderHostModeHelp() {
    const form = byId("host-form");
    if (!form) {
      return;
    }
    const mode = form.elements.host_mode.value || "linux-hypervisor";
    const help = byId("host-mode-help");
    const sshFields = ["ssh_host", "ssh_user", "ssh_port"];
    const isWindows = mode === "windows-local" || mode === "windows-remote";
    const isRemote = mode === "remote-linux" || mode === "windows-remote";
    const isLocalOnly = mode === "linux-local" || mode === "windows-local";

    document.querySelectorAll(".host-remote-field").forEach((node) => {
      node.classList.toggle("is-hidden", !isRemote);
    });
    document.querySelectorAll(".host-windows-field").forEach((node) => {
      node.classList.toggle("is-hidden", !isWindows);
    });

    sshFields.forEach((name) => {
      form.elements[name].disabled = isLocalOnly;
    });
    form.elements.wsl_distribution.disabled = !isWindows;

    let helpText = "Linux hypervisor hosts can run locally or be reached over SSH. Add an SSH host if this control plane is not running on the hypervisor itself.";
    if (mode === "windows-local") {
      helpText = "This Windows machine becomes the host. Linux hypervisor commands run inside the selected local WSL distro, and the machine must stay awake for Doplets to remain available.";
    } else if (mode === "windows-remote") {
      helpText = "VPSdash connects to a remote Windows machine over SSH, verifies or installs the selected WSL distro there, and runs the hypervisor stack inside that distro.";
    } else if (mode === "remote-linux") {
      helpText = "A remote Linux host is managed over SSH. Fill the SSH user, host, and port so the control plane can reach it.";
    } else if (mode === "linux-local") {
      helpText = "The current Linux machine is the host. SSH is optional here because commands run locally on this machine.";
    }
    if (help) {
      help.textContent = helpText;
    }
    renderLocalMachineInfo();
    renderHostSshReadout();
  }

  function fillDopletForm(payload) {
    const form = byId("doplet-form");
    fillForm(form, {
      ...payload,
      ssh_public_keys: payload?.ssh_public_keys || [],
    });
    const backupPolicy = payload?.backup_policy || {};
    form.elements.gpu_assignments_json.value = JSON.stringify(payload?.gpu_assignments || [], null, 2);
    form.elements.clone_host_id.value = payload?.host_id || "";
    form.elements.clone_primary_network_id.value = payload?.primary_network_id || "";
    form.elements.clone_storage_backend.value = payload?.storage_backend || "";
    form.elements.storage_backend.value = payload?.storage_backend || form.elements.storage_backend.value || "files";
    form.elements.backup_policy_enabled.checked = Boolean(backupPolicy.enabled);
    form.elements.backup_schedule_minutes.value = backupPolicy.schedule_minutes ?? 0;
    form.elements.backup_retain_count.value = backupPolicy.retain_count ?? 7;
    form.elements.backup_verify_after_upload.checked = Boolean(backupPolicy.verify_after_upload ?? true);
    form.elements.backup_prune_remote.checked = Boolean(backupPolicy.prune_remote);
    form.elements.backup_provider_ids_json.value = JSON.stringify(backupPolicy.provider_ids || []);
    form.elements.resize_vcpu.value = payload?.vcpu ?? 1;
    form.elements.resize_ram_mb.value = payload?.ram_mb ?? 1024;
    form.elements.resize_disk_gb.value = payload?.disk_gb ?? 20;
    byId("gpu-preflight-readout").textContent = JSON.stringify(payload?.gpu_preflight || {}, null, 2);
    populateGpuOptions();
    renderGpuCapabilityReadout();
    renderDopletTerminalReadout();
  }

  function selectedDopletRecord() {
    const form = byId("doplet-form");
    const dopletId = Number(form?.elements.id.value || selected.dopletId || 0);
    if (!dopletId) {
      return null;
    }
    const record = dopletById(dopletId) || null;
    if (record && isDeletedDoplet(record)) {
      return null;
    }
    return record;
  }

  function selectedDopletAnyRecord() {
    const form = byId("doplet-form");
    const dopletId = Number(form?.elements.id.value || selected.dopletId || 0);
    if (!dopletId) {
      return null;
    }
    return dopletById(dopletId) || null;
  }

  function currentDopletTerminalInfo() {
    const item = selectedDopletRecord();
    if (!item) {
      return { supported: false, reason: "Save or select a Doplet first." };
    }
    const host = hostById(Number(item.host_id || 0));
    if (!host) {
      return { supported: false, reason: "Select a host for this Doplet first." };
    }

    const localModes = new Set(["linux-hypervisor", "linux-local", "windows-local", "windows-wsl-local"]);
    const hostMode = String(host.host_mode || host.mode || "").toLowerCase();
    if (!localModes.has(hostMode)) {
      return {
        supported: false,
        reason: "Direct terminal launch is available only for local Linux hosts or Windows hosts running Doplets through local WSL.",
      };
    }

    const bootstrapUser = String(item.bootstrap_user || "ubuntu").trim() || "ubuntu";
    const bootstrapPassword = String(item.bootstrap_password || item.metadata_json?.bootstrap_password || "").trim();
    const status = String(item.status || "draft").toLowerCase();
    const primaryIp = (item.ip_addresses || []).map((value) => String(value || "").trim()).find(Boolean) || "";
    const distro = String(host.wsl_distribution || "Ubuntu").trim() || "Ubuntu";
    if (["draft", "planned"].includes(status)) {
      return {
        supported: false,
        reason: "Create the Doplet first before opening a terminal.",
        status,
      };
    }
    if (["provisioning", "queued"].includes(status) && !primaryIp) {
      return {
        supported: false,
        reason: "Provisioning is still running. Wait for the Doplet to finish before opening a terminal.",
        status,
      };
    }
    if (status === "deleted") {
      return {
        supported: false,
        reason: "This Doplet has already been deleted.",
        status,
      };
    }
    const transport = primaryIp ? "ssh" : "virsh-console";
    const previewCommand = hostMode.startsWith("windows")
      ? (primaryIp
          ? `wsl.exe -d ${distro} -- bash -lc "ssh -o StrictHostKeyChecking=accept-new ${bootstrapUser}@${primaryIp}"`
          : `wsl.exe -u root -d ${distro} -- bash -lc "virsh console ${item.slug}"`)
      : (primaryIp
          ? `ssh -o StrictHostKeyChecking=accept-new ${bootstrapUser}@${primaryIp}`
          : `virsh console ${item.slug}`);
    return {
      supported: true,
      host,
      item,
      transport,
      target: primaryIp || item.slug,
      preview_command: previewCommand,
      wsl_distribution: distro,
      bootstrap_user: bootstrapUser,
      bootstrap_password: bootstrapPassword,
      status,
    };
  }

  async function fetchDopletTerminalInfo(dopletId) {
    if (!dopletId) {
      return { supported: false, reason: "Save or select a Doplet first." };
    }
    try {
      const payload = await api(`/api/doplets/${dopletId}/terminal`);
      return payload.terminal || currentDopletTerminalInfo();
    } catch (_error) {
      return currentDopletTerminalInfo();
    }
  }

  function renderDopletTerminalReadout() {
    const node = byId("doplet-terminal-readout");
    const openButton = byId("open-doplet-terminal");
    const copyButton = byId("copy-doplet-terminal-command");
    if (!node) {
      return;
    }
    const info = currentDopletTerminalInfo();
    if (openButton) {
      openButton.disabled = !info.supported;
    }
    if (copyButton) {
      copyButton.disabled = !info.supported;
    }
    if (!info.supported) {
      node.innerHTML = `<span class="chip">${escapeHtml(info.reason || "Terminal access is not available yet.")}</span>`;
      return;
    }
    const hostMode = String(info.host.host_mode || info.host.mode || "").toLowerCase();
    node.innerHTML = [
      `<span class="chip ${info.status === "running" ? "success" : info.status === "error" ? "danger" : "warn"}">State ${escapeHtml(info.status || "unknown")}</span>`,
      `<span class="chip">Access ${escapeHtml(info.transport === "ssh" ? "SSH" : "Virsh Console")}</span>`,
      `<span class="chip">Target ${escapeHtml(info.target)}</span>`,
      hostMode.startsWith("windows") ? `<span class="chip">WSL ${escapeHtml(info.wsl_distribution)}</span>` : "",
      `<span class="chip">User ${escapeHtml(info.bootstrap_user)}</span>`,
      info.bootstrap_password ? `<span class="chip warn">Password ${escapeHtml(info.bootstrap_password)}</span>` : "",
      info.transport === "virsh-console" ? `<span class="chip warn">Console only until guest networking is ready</span>` : "",
      `<span class="chip"><code>${escapeHtml(info.preview_command)}</code></span>`,
    ].filter(Boolean).join("");
  }

  function collectHostConfig(form) {
    return {
      runtime_root: form.elements.config_runtime_root.value || "/var/lib/vpsdash",
      zfs_pool: form.elements.config_zfs_pool.value || "tank",
      zfs_dataset_root: form.elements.config_zfs_dataset_root.value || "doplets",
      lvm_vg: form.elements.config_lvm_vg.value || "vg_vpsdash",
      lvm_thinpool: form.elements.config_lvm_thinpool.value || "thinpool",
      libvirt_network: form.elements.config_libvirt_network.value || "default",
      reserve_cpu_threads: Number(form.elements.config_reserve_cpu_threads.value || 1),
      reserve_ram_mb: Number(form.elements.config_reserve_ram_mb.value || 2048),
      reserve_disk_gb: Number(form.elements.config_reserve_disk_gb.value || 20),
      zfs_devices: splitLines(form.elements.config_zfs_devices.value),
      lvm_devices: splitLines(form.elements.config_lvm_devices.value),
    };
  }

  function parseGpuAssignments(form) {
    return parseJsonField(form.elements.gpu_assignments_json.value, []);
  }

  function parseBackupPolicy(form) {
    return {
      enabled: form.elements.backup_policy_enabled.checked,
      schedule_minutes: Number(form.elements.backup_schedule_minutes.value || 0),
      retain_count: Number(form.elements.backup_retain_count.value || 0),
      verify_after_upload: form.elements.backup_verify_after_upload.checked,
      prune_remote: form.elements.backup_prune_remote.checked,
      provider_ids: parseJsonField(form.elements.backup_provider_ids_json.value, []),
    };
  }

  function clearForm(form) {
    form.reset();
    for (const element of form.elements) {
      if (element.name === "id") {
        element.value = "";
      }
    }
  }

  function scrollToWorkspace(targetId) {
    const node = byId(targetId);
    node?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function renderMetrics() {
    const metrics = [
      ["Users", state.counts.users],
      ["Hosts", state.counts.hosts],
      ["Doplets", state.counts.doplets],
      ["Snapshots", state.counts.snapshots],
      ["Networks", state.counts.networks],
      ["Providers", state.counts.providers],
      ["Tasks", state.counts.tasks],
    ];
    byId("metric-grid").innerHTML = metrics
      .map(
        ([label, value]) => `
          <div class="metric-card">
            <div class="metric-label">${label}</div>
            <div class="metric-value">${value}</div>
          </div>
        `
      )
      .join("");
  }

  function statusTone(status) {
    const normalized = String(status || "").toLowerCase();
    if (["ready", "running", "succeeded", "complete"].includes(normalized)) {
      return "success";
    }
    if (["error", "failed", "deleted", "cancelled"].includes(normalized)) {
      return "danger";
    }
    if (["provisioning", "planned", "queued", "running", "stopped", "draft"].includes(normalized)) {
      return "warn";
    }
    return "";
  }

  function statusChip(status, label) {
    return `<span class="chip ${statusTone(status)}">${escapeHtml(label || status || "unknown")}</span>`;
  }

  function hostAccessSummary(host) {
    const hostMode = String(host.host_mode || host.mode || "").toLowerCase();
    if (hostMode === "windows-local") {
      return `Local WSL Â· ${escapeHtml(host.wsl_distribution || "Ubuntu")}`;
    }
    if (hostMode === "linux-local" || hostMode === "linux-hypervisor") {
      return "Local Linux hypervisor";
    }
    const user = escapeHtml(host.ssh_user || "root");
    const endpoint = escapeHtml(host.ssh_host || "unknown");
    const port = Number(host.ssh_port || 22) || 22;
    return `${user}@${endpoint}:${port}`;
  }

  function dopletAccessSummary(item) {
    const primaryIp = (item.ip_addresses || []).map((value) => String(value || "").trim()).find(Boolean) || "";
    if (primaryIp) {
      return `${escapeHtml(item.bootstrap_user || "ubuntu")}@${escapeHtml(primaryIp)}`;
    }
    if (["running", "stopped", "error"].includes(String(item.status || "").toLowerCase())) {
      return `Console ${escapeHtml(item.slug || item.name || "vm")}`;
    }
    return "No terminal yet";
  }

  function formatDateTime(value) {
    if (!value) {
      return "-";
    }
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime()) ? String(value) : parsed.toLocaleString();
  }

  function renderHosts() {
    const rows = (state.hosts || []).map((host) => {
      const capacity = host.capacity?.remaining || {};
      const selectedClass = Number(selected.hostId) === Number(host.id) ? "active" : "";
      return `
        <tr data-host-id="${host.id}" class="${selectedClass}">
          <td><strong>${host.name}</strong><div class="muted">${host.distro} | ${host.primary_storage_backend}</div></td>
          <td>${hostAccessSummary(host)}</td>
          <td>${statusChip(host.status, `Host ${host.status || "draft"}`)}</td>
          <td>${capacity.cpu_threads || 0} CPU | ${capacity.ram_mb || 0} MB | ${capacity.disk_gb || 0} GB</td>
        </tr>
      `;
    });
    byId("hosts-table").innerHTML = tableMarkup(["Host", "Access", "Status", "Remaining"], rows, "No hosts yet.");
    byId("hosts-table").querySelectorAll("tbody tr[data-host-id]").forEach((row) => {
      row.addEventListener("click", () => {
        selected.hostId = Number(row.dataset.hostId);
        fillHostForm(hostById(selected.hostId) || {});
        updateCapacityReadout();
        renderHosts();
      });
    });
  }

  function renderDoplets() {
    const visibleRecords = (state.doplets || []).filter((item) => !isDeletedDoplet(item));
    const rows = visibleRecords.map((item) => {
      const host = hostById(item.host_id);
      const image = imageById(item.image_id);
      const task = latestTaskForDoplet(item.id);
      const selectedClass = Number(selected.dopletId) === Number(item.id) ? "active" : "";
      return `
        <tr data-doplet-id="${item.id}" class="${selectedClass}">
          <td><strong>${item.name}</strong><div class="muted">${host?.name || "Unknown host"}</div></td>
          <td>${dopletAccessSummary(item)}</td>
          <td>${item.vcpu} CPU | ${item.ram_mb} MB | ${item.disk_gb} GB</td>
          <td>${statusChip(item.status, item.status || "draft")}${task ? `<div class="muted">Task: ${escapeHtml(task.task_type)} (${escapeHtml(task.status)})</div>` : ""}</td>
          <td>${image?.name || item.image_id || "-"}<div class="muted">${formatDateTime(item.updated_at)}</div></td>
        </tr>
      `;
    });
    byId("doplets-table").innerHTML = tableMarkup(["Doplet", "Access", "Size", "Status", "Image / Updated"], rows, "No active Doplets yet.");
    byId("doplets-table").querySelectorAll("tbody tr[data-doplet-id]").forEach((row) => {
      row.addEventListener("click", () => {
        selected.dopletId = Number(row.dataset.dopletId);
        const item = dopletById(selected.dopletId);
        if (item) {
          fillDopletForm(item);
        }
        updateCapacityReadout();
        renderDopletTerminalReadout();
        renderDopletManagement();
        renderDoplets();
      });
    });
  }

  function setManageButtonDisabled(disabled) {
    [
      "edit-selected-doplet",
      "open-doplet-terminal",
      "copy-doplet-terminal-command",
      "doplet-start",
      "doplet-shutdown",
      "doplet-reboot",
      "doplet-force-stop",
      "doplet-delete-task",
      "delete-doplet",
      "resize-doplet",
      "backup-doplet",
      "snapshot-doplet",
      "clone-doplet",
    ].forEach((id) => {
      const node = byId(id);
      if (node) {
        node.disabled = disabled || node.disabled;
      }
    });
  }

  function renderDopletManagement() {
    const summary = byId("doplet-management-summary");
    const access = byId("doplet-management-access");
    const stateNode = byId("doplet-management-state");
    const countsNode = byId("doplet-state-counts");
    if (!summary || !access || !stateNode || !countsNode) {
      return;
    }

    const records = state.doplets || [];
    const counters = ["running", "provisioning", "stopped", "draft", "error", "deleted"].map((status) => {
      const count = records.filter((item) => String(item.status || "").toLowerCase() === status).length;
      return count ? statusChip(status, `${status} ${count}`) : "";
    }).filter(Boolean);
    countsNode.innerHTML = counters.length ? counters.join("") : '<span class="chip">No Doplets saved yet</span>';

    const item = selectedDopletRecord();
    const selectedAny = selectedDopletAnyRecord();
    const canOperate = hasRole("operator");
    const manageButtons = [
      "edit-selected-doplet",
      "open-doplet-terminal",
      "copy-doplet-terminal-command",
      "doplet-start",
      "doplet-shutdown",
      "doplet-reboot",
      "doplet-force-stop",
      "doplet-delete-task",
      "delete-doplet",
      "resize-doplet",
      "backup-doplet",
      "snapshot-doplet",
      "clone-doplet",
    ];
    manageButtons.forEach((id) => {
      const node = byId(id);
      if (node) {
        node.disabled = !item || !canOperate;
      }
    });

    if (!item) {
      if (selectedAny && isDeletedDoplet(selectedAny)) {
        summary.innerHTML = `<strong>${escapeHtml(selectedAny.name)}</strong> was deleted. Select a non-deleted Doplet to manage it, or start a new draft.`;
        access.innerHTML = "Deleted Doplets cannot be opened or managed.";
        stateNode.innerHTML = '<span class="chip danger">Deleted</span>';
      } else {
        summary.innerHTML = "Select a Doplet below to review host, image, size, runtime state, and recent task activity.";
        access.innerHTML = "Terminal access details appear here after you select a Doplet.";
        stateNode.innerHTML = '<span class="chip">No Doplet selected</span>';
      }
      return;
    }

    const host = hostById(item.host_id);
    const image = imageById(item.image_id);
    const task = latestTaskForDoplet(item.id);
    const info = currentDopletTerminalInfo();
    summary.innerHTML = [
      `<div><strong>${escapeHtml(item.name)}</strong> <span class="muted">(${escapeHtml(item.slug)})</span></div>`,
      `<div>Host: <strong>${escapeHtml(host?.name || "Unknown host")}</strong></div>`,
      `<div>Image: <strong>${escapeHtml(image?.name || "Not set")}</strong></div>`,
      `<div>Size: <strong>${escapeHtml(`${item.vcpu} CPU / ${item.ram_mb} MB / ${item.disk_gb} GB`)}</strong></div>`,
      `<div>Updated: <strong>${escapeHtml(formatDateTime(item.updated_at))}</strong></div>`,
      task ? `<div>Latest task: <strong>${escapeHtml(task.task_type)}</strong> <span class="muted">(${escapeHtml(task.status)} Â· ${Number(task.progress || 0)}%)</span></div>` : "",
    ].filter(Boolean).join("");

    if (info.supported) {
      access.innerHTML = [
        `<div>Mode: <strong>${escapeHtml(info.transport === "ssh" ? "SSH" : "Console")}</strong></div>`,
        `<div>Target: <strong>${escapeHtml(info.target)}</strong></div>`,
        `<div>User: <strong>${escapeHtml(info.bootstrap_user)}</strong></div>`,
        info.bootstrap_password ? `<div>Bootstrap password: <strong>${escapeHtml(info.bootstrap_password)}</strong></div>` : "",
        `<div class="muted"><code>${escapeHtml(info.preview_command)}</code></div>`,
      ].filter(Boolean).join("");
    } else {
      access.innerHTML = escapeHtml(info.reason || "Terminal access is not available yet.");
    }

    stateNode.innerHTML = [
      statusChip(item.status, item.status || "draft"),
      host ? statusChip(host.status, `host ${host.status || "draft"}`) : "",
      task ? statusChip(task.status, `task ${task.status}`) : '<span class="chip">No active task</span>',
      item.ip_addresses?.length ? `<span class="chip success">${escapeHtml(item.ip_addresses[0])}</span>` : '<span class="chip warn">No guest IP yet</span>',
    ].filter(Boolean).join("");

    if (!info.supported) {
      const openButton = byId("open-doplet-terminal");
      const copyButton = byId("copy-doplet-terminal-command");
      if (openButton) {
        openButton.disabled = true;
      }
      if (copyButton) {
        copyButton.disabled = true;
      }
    }
  }

  function fillRestoreDraftFromSnapshot(snapshot) {
    const restoreForm = byId("restore-form");
    const source = dopletById(snapshot.doplet_id);
    restoreForm.elements.snapshot_id.value = snapshot.id;
    restoreForm.elements.target_doplet_id.value = "";
    restoreForm.elements.name.value = source ? `${source.name} Restore` : `${snapshot.name} Restore`;
    restoreForm.elements.slug.value = source ? `${source.slug}-restore` : `snapshot-${snapshot.id}-restore`;
    restoreForm.elements.host_id.value = source?.host_id || "";
    restoreForm.elements.primary_network_id.value = source?.primary_network_id || "";
    restoreForm.elements.storage_backend.value = source?.storage_backend || "";
    byId("restore-source-info").innerHTML = `
      <strong>${snapshot.name}</strong> from <strong>${source?.name || `Doplet ${snapshot.doplet_id}`}</strong>.
      Choose an existing target to restore in place, or leave it blank to create a recovered Doplet.
    `;
  }

  function clearRestoreDraft() {
    selected.snapshotId = null;
    clearForm(byId("restore-form"));
    byId("restore-source-info").textContent = "Select a snapshot to restore it in place or create a recovered Doplet.";
    renderSnapshots();
  }

  function renderNetworks() {
    const rows = (state.networks || []).map((network) => {
      const host = hostById(network.host_id);
      const selectedClass = Number(selected.networkId) === Number(network.id) ? "active" : "";
      return `
        <tr data-network-id="${network.id}" class="${selectedClass}">
          <td><strong>${network.name}</strong><div class="muted">${host?.name || "Unknown host"}</div></td>
          <td>${network.mode}</td>
          <td>${network.cidr || "-"}</td>
          <td>${network.bridge_name || "-"}<div class="muted">${network.firewall_policy?.runtime_status || "draft"}</div></td>
        </tr>
      `;
    });
    byId("networks-table").innerHTML = tableMarkup(["Network", "Mode", "CIDR", "Bridge"], rows, "No networks yet.");
    byId("networks-table").querySelectorAll("tbody tr[data-network-id]").forEach((row) => {
      row.addEventListener("click", () => {
        selected.networkId = Number(row.dataset.networkId);
        const network = networkById(selected.networkId);
        if (network) {
          fillForm(byId("network-form"), {
            ...network,
            firewall_policy: network.firewall_policy || {},
          });
        }
        renderNetworks();
      });
    });
  }

  function renderProviders() {
    const rows = (state.providers || []).map((provider) => {
      const selectedClass = Number(selected.providerId) === Number(provider.id) ? "active" : "";
      return `
        <tr data-provider-id="${provider.id}" class="${selectedClass}">
          <td><strong>${provider.name}</strong><div class="muted">${provider.provider_type}</div></td>
          <td>${provider.bucket || provider.root_path || "-"}</td>
          <td>${provider.enabled ? "enabled" : "disabled"}</td>
          <td>${provider.has_secret ? "secret set" : "no secret"}</td>
        </tr>
      `;
    });
    byId("providers-table").innerHTML = tableMarkup(["Provider", "Target", "Status", "Credentials"], rows, "No backup providers yet.");
    byId("providers-table").querySelectorAll("tbody tr[data-provider-id]").forEach((row) => {
      row.addEventListener("click", () => {
        selected.providerId = Number(row.dataset.providerId);
        const provider = (state.providers || []).find((entry) => Number(entry.id) === selected.providerId);
        if (provider) {
          fillForm(byId("provider-form"), {
            ...provider,
            quota_model: provider.quota_model || {},
          });
          byId("provider-form").elements.secret_key.value = "";
        }
        renderProviders();
      });
    });
  }

  function renderUsers() {
    const rows = (state.users || []).map((user) => `
      <tr>
        <td><strong>${user.username}</strong><div class="muted">${user.email}</div></td>
        <td>${user.role}</td>
        <td>${user.mfa_enabled ? "MFA" : "no MFA"}</td>
        <td>${user.status}</td>
      </tr>
    `);
    byId("users-table").innerHTML = tableMarkup(["User", "Role", "MFA", "Status"], rows, "No operators yet.");
  }

  function renderTasks() {
    const rows = (state.tasks || []).map((task) => {
      const selectedClass = Number(selected.taskId) === Number(task.id) ? "active" : "";
      const canCancel = ["planned", "queued", "running", "cancel-requested"].includes(task.status);
      const canRetry = ["failed", "cancelled"].includes(task.status);
      const operatorDisabled = hasRole("operator") ? "" : "disabled";
      return `
      <tr data-task-id="${task.id}" class="${selectedClass}">
        <td><strong>${task.task_type}</strong><div class="muted">${task.target_type} ${task.target_id}</div></td>
        <td>${task.status}</td>
        <td>${task.progress}%</td>
        <td>
          <div class="button-row compact">
            <button class="secondary task-run" data-task-id="${task.id}" data-dry-run="true" type="button" ${operatorDisabled}>Dry run</button>
            <button class="secondary task-launch" data-task-id="${task.id}" type="button" ${operatorDisabled}>Launch</button>
            <button class="secondary task-run" data-task-id="${task.id}" data-dry-run="false" type="button" ${operatorDisabled}>Run now</button>
            <button class="secondary task-cancel" data-task-id="${task.id}" type="button" ${canCancel ? operatorDisabled : "disabled"}>Cancel</button>
            <button class="secondary task-retry" data-task-id="${task.id}" type="button" ${canRetry ? operatorDisabled : "disabled"}>Retry</button>
          </div>
        </td>
      </tr>
    `;
    });
    byId("tasks-table").innerHTML = tableMarkup(["Task", "Status", "Progress", "Actions"], rows, "No tasks queued.");
    byId("tasks-table").querySelectorAll("tbody tr[data-task-id]").forEach((row) => {
      row.addEventListener("click", (event) => {
        if (event.target.closest("button")) {
          return;
        }
        selected.taskId = Number(row.dataset.taskId);
        renderTasks();
        renderTaskDetail();
      });
    });
    byId("tasks-table").querySelectorAll(".task-run").forEach((button) => {
      button.addEventListener("click", async () => {
        try {
          await api(`/api/tasks/${button.dataset.taskId}/run`, {
            method: "POST",
            body: { dry_run: button.dataset.dryRun === "true" },
          });
          await reloadBootstrap("Task executed.");
        } catch (error) {
          flash(error.message, "error");
        }
      });
    });
    byId("tasks-table").querySelectorAll(".task-launch").forEach((button) => {
      button.addEventListener("click", async () => {
        try {
          await api(`/api/tasks/${button.dataset.taskId}/launch`, {
            method: "POST",
            body: { dry_run: false },
          });
          await reloadBootstrap("Task launched in background.");
        } catch (error) {
          flash(error.message, "error");
        }
      });
    });
    byId("tasks-table").querySelectorAll(".task-cancel").forEach((button) => {
      button.addEventListener("click", async () => {
        try {
          await api(`/api/tasks/${button.dataset.taskId}/cancel`, { method: "POST", body: {} });
          await reloadBootstrap("Task cancel requested.");
        } catch (error) {
          flash(error.message, "error");
        }
      });
    });
    byId("tasks-table").querySelectorAll(".task-retry").forEach((button) => {
      button.addEventListener("click", async () => {
        try {
          await api(`/api/tasks/${button.dataset.taskId}/retry`, { method: "POST", body: {} });
          await reloadBootstrap("Retry task queued.");
        } catch (error) {
          flash(error.message, "error");
        }
      });
    });
  }

  function renderBackups() {
    const operatorDisabled = hasRole("operator") ? "" : "disabled";
    const rows = (state.backups || []).map((backup) => `
      <tr>
        <td><strong>Backup ${backup.id}</strong><div class="muted">Doplet ${backup.doplet_id}</div></td>
        <td>${backup.status}</td>
        <td>${backup.artifact_reference || "-"}</td>
        <td><button class="secondary verify-backup" data-backup-id="${backup.id}" type="button" ${operatorDisabled}>Verify</button></td>
      </tr>
    `);
    byId("backups-table").innerHTML = tableMarkup(["Backup", "Status", "Artifact", "Verify"], rows, "No backup records yet.");
    byId("backups-table").querySelectorAll(".verify-backup").forEach((button) => {
      button.addEventListener("click", async () => {
        try {
          const result = await api(`/api/backups/${button.dataset.backupId}/verify`, { method: "POST", body: {} });
          flash(result.verification?.ok ? "Backup verified." : "Backup verification found problems.", result.verification?.ok ? "info" : "warn");
          await reloadBootstrap();
        } catch (error) {
          flash(error.message, "error");
        }
      });
    });
  }

  function renderSnapshots() {
    const rows = (state.snapshots || []).map((snapshot) => {
      const source = dopletById(snapshot.doplet_id);
      const selectedClass = Number(selected.snapshotId) === Number(snapshot.id) ? "active" : "";
      return `
        <tr data-snapshot-id="${snapshot.id}" class="${selectedClass}">
          <td><strong>${snapshot.name}</strong><div class="muted">${source?.name || `Doplet ${snapshot.doplet_id}`}</div></td>
          <td>${snapshot.status}</td>
          <td>${snapshot.artifact_reference || "-"}</td>
        </tr>
      `;
    });
    byId("snapshots-table").innerHTML = tableMarkup(["Snapshot", "Status", "Artifact"], rows, "No snapshots yet.");
    byId("snapshots-table").querySelectorAll("tbody tr[data-snapshot-id]").forEach((row) => {
      row.addEventListener("click", () => {
        selected.snapshotId = Number(row.dataset.snapshotId);
        const snapshot = snapshotById(selected.snapshotId);
        if (snapshot) {
          fillRestoreDraftFromSnapshot(snapshot);
        }
        renderSnapshots();
      });
    });
  }

  function renderAudit() {
    const rows = (state.audit || []).map((entry) => `
      <tr>
        <td><strong>${entry.action}</strong><div class="muted">${entry.summary}</div></td>
        <td>${entry.actor}</td>
        <td>${new Date(entry.created_at).toLocaleString()}</td>
      </tr>
    `);
    byId("audit-table").innerHTML = tableMarkup(["Event", "Actor", "Time"], rows, "No audit events yet.");
  }

  function renderTaskDetail() {
    const task = taskById(selected.taskId);
    const detail = byId("task-detail");
    if (!task) {
      detail.textContent = "Select a task to inspect progress, logs, and results.";
      return;
    }
    detail.textContent = [
      `${task.task_type} (${task.status})`,
      `target: ${task.target_type} ${task.target_id}`,
      `progress: ${task.progress}%`,
      "",
      "result payload:",
      JSON.stringify(task.result_payload || {}, null, 2),
      "",
      "log output:",
      task.log_output || "(no log output yet)",
    ].join("\n");
  }

  function renderHostCapabilityReadout(host) {
    const node = byId("host-capability-readout");
    const target = host || hostById(byId("host-form").elements.id.value || selected.hostId);
    const resources = target?.inventory?.resources || {};
    const config = target?.config || {};
    const mediatedProfiles = resources.mediated_profiles || [];
    const zfsPools = resources.zfs_pools || [];
    const lvmVgCount = ((resources.vgs || {}).report?.[0]?.vg || []).length;
    const mode = target?.host_mode || byId("host-form").elements.host_mode.value || "linux-hypervisor";
    const wslDistro = target?.wsl_distribution || byId("host-form").elements.wsl_distribution.value || "Ubuntu";
    const backend = target?.primary_storage_backend || byId("host-form").elements.primary_storage_backend.value || "files";
    const backendChip = backend === "files"
      ? `<span class="chip">Files ${config.runtime_root || "/var/lib/vpsdash"}</span>`
      : backend === "zfs"
        ? `<span class="chip">ZFS ${config.zfs_pool || "tank"} (${zfsPools.length} pool(s))</span>`
        : `<span class="chip">LVM ${config.lvm_vg || "vg_vpsdash"} / ${config.lvm_thinpool || "thinpool"}</span>`;
    node.innerHTML = [
      `<span class="chip">${mode}</span>`,
      backendChip,
      `<span class="chip">GPU devices ${resources.gpu_device_count || 0}</span>`,
      `<span class="chip">vGPU profiles ${mediatedProfiles.length}</span>`,
      `<span class="chip">Volume groups ${lvmVgCount}</span>`,
      (mode === "windows-local" || mode === "windows-remote") ? `<span class="chip">WSL ${wslDistro}</span>` : "",
    ].join("");
  }

  function populateGpuOptions() {
    const form = byId("doplet-form");
    const host = hostById(Number(form.elements.host_id.value || 0));
    const devices = host?.inventory?.resources?.gpu_devices || [];
    const profiles = host?.inventory?.resources?.mediated_profiles || [];
    byId("gpu-parent-select").innerHTML = optionMarkup(
      devices,
      "pci_address",
      (item) => `${item.pci_address || "GPU"} ${item.name || ""}`.trim(),
      "Choose GPU device"
    );
    byId("gpu-profile-select").innerHTML = optionMarkup(
      profiles,
      "profile_id",
      (item) => `${item.name || item.profile_id} (${item.available_instances || 0} free)`,
      "Choose mediated profile"
    );
  }

  function renderGpuCapabilityReadout() {
    const form = byId("doplet-form");
    const host = hostById(Number(form.elements.host_id.value || 0));
    const node = byId("gpu-capability-readout");
    const preflightNode = byId("gpu-preflight-readout");
    const resources = host?.inventory?.resources || {};
    let assignments = [];
    try {
      assignments = parseGpuAssignments(form);
    } catch (_error) {
      node.innerHTML = [
        `<span class="chip">Physical GPUs ${(resources.gpu_device_count || 0)}</span>`,
        `<span class="chip">GPU assignment JSON is invalid</span>`,
      ].join("");
      preflightNode.textContent = "GPU assignment JSON is invalid.";
      return;
    }
    const mediatedProfiles = resources.mediated_profiles || [];
    node.innerHTML = [
      `<span class="chip">Physical GPUs ${(resources.gpu_device_count || 0)}</span>`,
      `<span class="chip">Mediated profiles ${mediatedProfiles.length}</span>`,
      `<span class="chip">Requested GPU assignments ${assignments.length}</span>`,
    ].join("");
    const selectedDoplet = dopletById(Number(form.elements.id.value || selected.dopletId || 0));
    const preflight = selectedDoplet?.gpu_preflight || {};
    preflightNode.textContent = JSON.stringify(preflight, null, 2);
  }

  function populateSelects() {
    const currentValues = {
      dopletHost: byId("doplet-host-select")?.value || "",
      dopletCloneHost: byId("doplet-clone-host-select")?.value || "",
      networkHost: byId("network-host-select")?.value || "",
      restoreHost: byId("restore-host-select")?.value || "",
      dopletImage: byId("doplet-image-select")?.value || "",
      dopletFlavor: byId("doplet-flavor-select")?.value || "",
      dopletNetwork: byId("doplet-network-select")?.value || "",
      dopletCloneNetwork: byId("doplet-clone-network-select")?.value || "",
      restoreNetwork: byId("restore-network-select")?.value || "",
      restoreTarget: byId("restore-target-select")?.value || "",
    };
    const hostOptions = optionMarkup(state.hosts || [], "id", (item) => item.name, "Choose host");
    byId("doplet-host-select").innerHTML = hostOptions;
    byId("doplet-clone-host-select").innerHTML = hostOptions.replace("Choose host", "Source host");
    byId("network-host-select").innerHTML = hostOptions;
    byId("restore-host-select").innerHTML = hostOptions.replace("Choose host", "Source host");
    byId("doplet-image-select").innerHTML = optionMarkup(state.images || [], "id", (item) => item.name, "Choose image");
    byId("doplet-flavor-select").innerHTML = optionMarkup(state.flavors || [], "id", (item) => `${item.name} (${item.vcpu} CPU / ${item.ram_mb} MB / ${item.disk_gb} GB)`, "Choose flavor");
    const networkOptions = optionMarkup(state.networks || [], "id", (item) => `${item.name} (${item.mode})`, "Use default NAT network");
    byId("doplet-network-select").innerHTML = networkOptions;
    byId("doplet-clone-network-select").innerHTML = networkOptions.replace("Choose network", "Source network");
    byId("restore-network-select").innerHTML = networkOptions.replace("Choose network", "Source network");
    byId("restore-target-select").innerHTML = optionMarkup(state.doplets || [], "id", (item) => `${item.name} (${item.status})`, "Restore as new Doplet");

    byId("doplet-host-select").value = preferredHostId(currentValues.dopletHost);
    byId("doplet-clone-host-select").value = currentValues.dopletCloneHost;
    byId("network-host-select").value = currentValues.networkHost;
    byId("restore-host-select").value = currentValues.restoreHost;
    byId("doplet-image-select").value = preferredImageId(currentValues.dopletImage);
    byId("doplet-flavor-select").value = preferredFlavorId(currentValues.dopletFlavor);
    byId("doplet-network-select").value = currentValues.dopletNetwork;
    byId("doplet-clone-network-select").value = currentValues.dopletCloneNetwork;
    byId("restore-network-select").value = currentValues.restoreNetwork;
    byId("restore-target-select").value = currentValues.restoreTarget;
    renderDopletTerminalReadout();
  }

  function updateCapacityReadout() {
    const form = byId("doplet-form");
    const hostId = Number(form.elements.host_id.value || 0);
    const host = hostById(hostId);
    const readout = byId("capacity-readout");
    if (!host) {
      readout.innerHTML = '<span class="chip">Select a host to view remaining capacity</span>';
      renderDopletTerminalReadout();
      return;
    }
    const capacity = host.capacity?.remaining || {};
    const requested = {
      cpu: Number(form.elements.vcpu.value || 0),
      ram: Number(form.elements.ram_mb.value || 0),
      disk: Number(form.elements.disk_gb.value || 0),
    };
    const selectedNetwork = form.elements.primary_network_id.value ? networkById(form.elements.primary_network_id.value) : null;
    readout.innerHTML = [
      `<span class="chip ${host.status === "ready" ? "success" : "warn"}">Host ${escapeHtml(host.status || "draft")}</span>`,
      `<span class="chip">Remaining CPU ${capacity.cpu_threads || 0}</span>`,
      `<span class="chip">Remaining RAM ${capacity.ram_mb || 0} MB</span>`,
      `<span class="chip">Remaining Disk ${capacity.disk_gb || 0} GB</span>`,
      `<span class="chip">Requested ${requested.cpu} CPU / ${requested.ram} MB / ${requested.disk} GB</span>`,
      `<span class="chip">${selectedNetwork ? `Network ${escapeHtml(selectedNetwork.name)}` : "Network default NAT"}</span>`,
    ].join("");
    renderGpuCapabilityReadout();
    renderDopletTerminalReadout();
  }

  function renderAll() {
    if (!selected.dopletId && (state.doplets || []).length) {
      selected.dopletId = preferredDopletId();
    }
    if (selected.dopletId) {
      const selectedRecord = dopletById(selected.dopletId);
      if (!selectedRecord || isDeletedDoplet(selectedRecord)) {
        selected.dopletId = preferredDopletId();
      }
    }
    populateSelects();
    renderMetrics();
    renderHosts();
    renderDoplets();
    renderDopletManagement();
    renderNetworks();
    renderProviders();
    renderUsers();
    renderTasks();
    renderBackups();
    renderSnapshots();
    renderAudit();
    if (selected.hostId && hostById(selected.hostId)) {
      fillHostForm(hostById(selected.hostId));
    }
    if (selected.dopletId) {
      const item = dopletById(selected.dopletId);
      if (item) {
        fillDopletForm(item);
      }
    }
    if (selected.networkId && networkById(selected.networkId)) {
      fillForm(byId("network-form"), networkById(selected.networkId));
    }
    if (selected.providerId) {
      const provider = (state.providers || []).find((entry) => Number(entry.id) === Number(selected.providerId));
      if (provider) {
        fillForm(byId("provider-form"), provider);
      }
    }
    if (selected.snapshotId) {
      const snapshot = snapshotById(selected.snapshotId);
      if (snapshot) {
        fillRestoreDraftFromSnapshot(snapshot);
      } else {
        clearForm(byId("restore-form"));
        byId("restore-source-info").textContent = "Select a snapshot to restore it in place or create a recovered Doplet.";
      }
    }
    if (!selected.hostId) {
      const hostForm = byId("host-form");
      if (
        !didAutofillHostDraft &&
        hostForm &&
        !String(hostForm.elements.name.value || "").trim() &&
        !String(hostForm.elements.slug.value || "").trim()
      ) {
        applyLocalMachineToHostForm();
        didAutofillHostDraft = true;
      } else {
        renderLocalMachineInfo();
        renderHostSshReadout();
      }
    }
    renderTaskDetail();
    updateCapacityReadout();
    renderDopletTerminalReadout();
    renderDopletManagement();
  }

  function setDisabled(ids, disabled) {
    ids.forEach((id) => {
      const node = byId(id);
      if (!node) {
        return;
      }
      if ("disabled" in node) {
        node.disabled = disabled;
      }
      node.querySelectorAll?.("input, select, textarea, button").forEach((child) => {
        child.disabled = disabled;
      });
    });
  }

  function applyRoleGating() {
    if (!hasRole("operator")) {
      setDisabled(
        [
          "host-form",
          "doplet-form",
          "network-form",
          "edit-selected-doplet",
          "new-doplet-draft",
          "capture-host-inventory",
          "host-acceptance",
          "prepare-host",
          "queue-create-doplet",
          "resize-doplet",
          "backup-doplet",
          "snapshot-doplet",
          "clone-doplet",
          "apply-network",
          "delete-network-runtime",
          "delete-doplet",
          "delete-network",
          "restore-form",
          "run-backup-scheduler",
        ],
        true
      );
    }
    if (!hasRole("owner")) {
      setDisabled(
        [
          "save-provider",
          "delete-provider",
          "save-user",
          "prune-backups",
        ],
        true
      );
    }
  }

  function bindActions() {
    byId("refresh-bootstrap").addEventListener("click", () => reloadBootstrap("Control plane refreshed."));
    byId("refresh-tasks").addEventListener("click", () => reloadBootstrap("Tasks refreshed."));
    byId("run-backup-scheduler").addEventListener("click", async () => {
      try {
        const result = await api("/api/maintenance/backups/run", { method: "POST", body: {} });
        await reloadBootstrap(`Queued ${result.count || 0} scheduled backup task(s).`);
      } catch (error) {
        flash(error.message, "error");
      }
    });
    byId("prune-backups").addEventListener("click", async () => {
      try {
        const result = await api("/api/maintenance/backups/prune", { method: "POST", body: {} });
        await reloadBootstrap(`Pruned ${result.count || 0} backup record(s).`);
      } catch (error) {
        flash(error.message, "error");
      }
    });

    const hostForm = byId("host-form");
    const saveHostDraft = async ({ announce = true } = {}) => {
      const payload = {
        id: hostForm.elements.id.value || undefined,
        name: hostForm.elements.name.value,
        slug: hostForm.elements.slug.value,
        host_mode: hostForm.elements.host_mode.value,
        distro: hostForm.elements.distro.value,
        primary_storage_backend: hostForm.elements.primary_storage_backend.value,
        exposure_mode: hostForm.elements.exposure_mode.value,
        ssh_host: hostForm.elements.ssh_host.value,
        ssh_user: hostForm.elements.ssh_user.value,
        ssh_port: Number(hostForm.elements.ssh_port.value || 22),
        wsl_distribution: hostForm.elements.wsl_distribution.value,
        mixed_use_allowed: hostForm.elements.mixed_use_allowed.checked,
        mixed_use_warning_acknowledged: hostForm.elements.mixed_use_warning_acknowledged.checked,
        config: collectHostConfig(hostForm),
        notes: hostForm.elements.notes.value,
      };
      const result = await api("/api/hosts", { method: "POST", body: payload });
      selected.hostId = Number(result.host?.id || selected.hostId || 0) || null;
      await reloadBootstrap(announce ? "Host saved." : undefined);
      return hostById(selected.hostId) || result.host;
    };

    const ensureHostSaved = async ({ announce = false } = {}) => {
      const hostId = hostForm.elements.id.value || selected.hostId;
      if (hostId) {
        return hostById(hostId) || { id: Number(hostId) };
      }
      return saveHostDraft({ announce });
    };

    byId("fill-local-windows-host").addEventListener("click", () => applyLocalMachineToHostForm("windows-local"));
    byId("fill-local-linux-host").addEventListener("click", () => applyLocalMachineToHostForm("linux-local"));
    byId("fill-this-machine-ssh").addEventListener("click", () => fillSshTargetFromLocalMachine());
    byId("copy-ssh-target").addEventListener("click", async () => {
      try {
        await copyText(
          buildSshTarget({
            ssh_host: hostForm.elements.ssh_host.value,
            ssh_user: hostForm.elements.ssh_user.value,
            ssh_port: Number(hostForm.elements.ssh_port.value || 22),
          }),
          "SSH target"
        );
      } catch (error) {
        flash(error.message, "error");
      }
    });
    byId("copy-ssh-command").addEventListener("click", async () => {
      try {
        await copyText(
          buildSshCommand({
            ssh_host: hostForm.elements.ssh_host.value,
            ssh_user: hostForm.elements.ssh_user.value,
            ssh_port: Number(hostForm.elements.ssh_port.value || 22),
          }),
          "SSH command"
        );
      } catch (error) {
        flash(error.message, "error");
      }
    });
    hostForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      try {
        await saveHostDraft({ announce: true });
      } catch (error) {
        flash(error.message, "error");
      }
    });
    byId("clear-host-form").addEventListener("click", () => {
      selected.hostId = null;
      clearForm(hostForm);
      delete hostForm.elements.slug.dataset.autofilled;
      hostForm.elements.primary_storage_backend.value = "files";
      hostForm.elements.config_runtime_root.value = "/var/lib/vpsdash";
      renderHostModeHelp();
      renderHostCapabilityReadout(null);
      renderHosts();
    });
    hostForm.elements.name.addEventListener("input", () => {
      const slugField = hostForm.elements.slug;
      if (!String(slugField.value || "").trim() || slugField.dataset.autofilled === "true") {
        slugField.value = slugify(hostForm.elements.name.value);
        slugField.dataset.autofilled = "true";
      }
      renderHostSshReadout();
    });
    hostForm.elements.slug.addEventListener("input", () => {
      if (String(hostForm.elements.slug.value || "").trim()) {
        delete hostForm.elements.slug.dataset.autofilled;
      }
    });
    hostForm.elements.host_mode.addEventListener("change", () => {
      renderHostModeHelp();
      renderHostCapabilityReadout(null);
    });
    ["ssh_host", "ssh_user", "ssh_port"].forEach((name) => {
      hostForm.elements[name].addEventListener("input", renderHostSshReadout);
      hostForm.elements[name].addEventListener("change", renderHostSshReadout);
    });
    hostForm.elements.wsl_distribution.addEventListener("input", () => {
      renderHostCapabilityReadout(null);
      renderHostSshReadout();
    });
    byId("capture-host-inventory").addEventListener("click", async () => {
      try {
        const host = await ensureHostSaved();
        const hostId = host?.id || hostForm.elements.id.value || selected.hostId;
        await api(`/api/hosts/${hostId}/inventory`, { method: "POST", body: {} });
        await reloadBootstrap("Host inventory captured.");
      } catch (error) {
        flash(error.message, "error");
      }
    });
    byId("host-acceptance").addEventListener("click", async () => {
      try {
        const host = await ensureHostSaved();
        const hostId = host?.id || hostForm.elements.id.value || selected.hostId;
        const report = await api(`/api/hosts/${hostId}/acceptance`);
        flash(report.ok ? "Host acceptance checks passed." : "Host acceptance report includes failures.", report.ok ? "info" : "warn");
      } catch (error) {
        flash(error.message, "error");
      }
    });
    byId("prepare-host").addEventListener("click", async () => {
      try {
        const host = await ensureHostSaved();
        const hostId = host?.id || hostForm.elements.id.value || selected.hostId;
        await api(`/api/hosts/${hostId}/inventory`, { method: "POST", body: {} });
        const result = await api(`/api/hosts/${hostId}/prepare`, { method: "POST", body: { launch: true } });
        selected.taskId = result.task?.id || selected.taskId;
        await reloadBootstrap("Host preparation started.");
      } catch (error) {
        flash(error.message, "error");
      }
    });
    byId("delete-host").addEventListener("click", async () => {
      const hostId = hostForm.elements.id.value || selected.hostId;
      if (!hostId) {
        flash("Select a host first.", "warn");
        return;
      }
      try {
        await api(`/api/hosts/${hostId}`, { method: "DELETE" });
        selected.hostId = null;
        clearForm(hostForm);
        await reloadBootstrap("Host deleted.");
      } catch (error) {
        flash(error.message, "error");
      }
    });

    const dopletForm = byId("doplet-form");
    const saveDopletDraft = async ({ announce = true } = {}) => {
      const resolvedName = String(dopletForm.elements.name.value || "").trim() || "New Doplet";
      const resolvedSlug = String(dopletForm.elements.slug.value || "").trim() || slugify(resolvedName);
      const resolvedHostId = Number(preferredHostId(dopletForm.elements.host_id.value || selected.hostId || "")) || 0;
      const resolvedImageId = Number(preferredImageId(dopletForm.elements.image_id.value || "")) || 0;
      const resolvedFlavorId = Number(preferredFlavorId(dopletForm.elements.flavor_id.value || "")) || 0;
      if (!resolvedHostId) {
        throw new Error("Create or prepare a host first, then choose it in the VPS builder.");
      }
      dopletForm.elements.host_id.value = resolvedHostId ? String(resolvedHostId) : "";
      dopletForm.elements.image_id.value = resolvedImageId ? String(resolvedImageId) : "";
      dopletForm.elements.flavor_id.value = resolvedFlavorId ? String(resolvedFlavorId) : "";
      const payload = {
        id: dopletForm.elements.id.value || undefined,
        name: resolvedName,
        slug: resolvedSlug,
        host_id: resolvedHostId,
        image_id: resolvedImageId || undefined,
        flavor_id: resolvedFlavorId || undefined,
        primary_network_id: Number(dopletForm.elements.primary_network_id.value || 0) || undefined,
        vcpu: Number(dopletForm.elements.vcpu.value || 1),
        ram_mb: Number(dopletForm.elements.ram_mb.value || 1024),
        disk_gb: Number(dopletForm.elements.disk_gb.value || 20),
        bootstrap_user: dopletForm.elements.bootstrap_user.value,
        storage_backend: dopletForm.elements.storage_backend.value,
        security_tier: dopletForm.elements.security_tier.value,
        ssh_public_keys: splitLines(dopletForm.elements.ssh_public_keys.value),
        gpu_assignments: parseGpuAssignments(dopletForm),
        backup_policy: parseBackupPolicy(dopletForm),
      };
      const result = await api("/api/doplets", { method: "POST", body: payload });
      selected.dopletId = Number(result.doplet?.id || selected.dopletId || 0) || null;
      await reloadBootstrap(announce ? "Doplet saved." : undefined);
      return dopletById(selected.dopletId) || result.doplet;
    };

    const ensureDopletSaved = async ({ announce = false } = {}) => {
      const dopletId = dopletForm.elements.id.value || selected.dopletId;
      if (dopletId) {
        return dopletById(dopletId) || { id: Number(dopletId) };
      }
      return saveDopletDraft({ announce });
    };

    function requireManagedDoplet() {
      const item = selectedDopletRecord();
      if (!item) {
        const selectedAny = selectedDopletAnyRecord();
        if (selectedAny && isDeletedDoplet(selectedAny)) {
          throw new Error("This Doplet was deleted. Select a running, stopped, provisioning, draft, or error Doplet instead.");
        }
        throw new Error("Select an existing Doplet from the management table first.");
      }
      return item;
    }

    dopletForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      try {
        await saveDopletDraft({ announce: true });
      } catch (error) {
        flash(error.message, "error");
      }
    });
    byId("clear-doplet-form").addEventListener("click", () => {
      selected.dopletId = null;
      clearForm(dopletForm);
      delete dopletForm.elements.slug.dataset.autofilled;
      dopletForm.elements.host_id.value = preferredHostId();
      dopletForm.elements.image_id.value = preferredImageId();
      dopletForm.elements.flavor_id.value = preferredFlavorId();
      dopletForm.elements.storage_backend.value = "files";
      dopletForm.elements.gpu_assignments_json.value = "[]";
      updateCapacityReadout();
      renderDopletTerminalReadout();
      renderDopletManagement();
      renderDoplets();
    });
    byId("new-doplet-draft").addEventListener("click", () => {
      byId("clear-doplet-form").click();
      scrollToWorkspace("doplets-panel");
      flash("Started a fresh Doplet draft.");
    });
    byId("edit-selected-doplet").addEventListener("click", () => {
      let item;
      try {
        item = requireManagedDoplet();
      } catch (error) {
        flash(error.message, "warn");
        return;
      }
      fillDopletForm(item);
      updateCapacityReadout();
      scrollToWorkspace("doplets-panel");
      flash(`Loaded ${item.name} into the builder.`);
    });
    dopletForm.elements.name.addEventListener("input", () => {
      const slugField = dopletForm.elements.slug;
      if (!String(slugField.value || "").trim() || slugField.dataset.autofilled === "true") {
        slugField.value = slugify(dopletForm.elements.name.value || "New Doplet");
        slugField.dataset.autofilled = "true";
      }
    });
    dopletForm.elements.slug.addEventListener("input", () => {
      if (String(dopletForm.elements.slug.value || "").trim()) {
        delete dopletForm.elements.slug.dataset.autofilled;
      }
    });
    ["host_id", "vcpu", "ram_mb", "disk_gb"].forEach((name) => {
      dopletForm.elements[name].addEventListener("input", updateCapacityReadout);
      dopletForm.elements[name].addEventListener("change", updateCapacityReadout);
    });
    byId("gpu-assignment-mode").addEventListener("change", renderGpuCapabilityReadout);
    byId("add-gpu-assignment").addEventListener("click", () => {
      const assignments = parseGpuAssignments(dopletForm);
      const mode = dopletForm.elements.gpu_assignment_mode.value;
      const parent = dopletForm.elements.gpu_parent_address.value;
      const profileId = dopletForm.elements.gpu_profile_id.value;
      if (!parent) {
        flash("Choose a GPU device first.", "warn");
        return;
      }
      assignments.push(
        mode === "mediated"
          ? { mode: "mediated", parent_address: parent, profile_id: profileId }
          : { mode: "passthrough", pci_address: parent }
      );
      dopletForm.elements.gpu_assignments_json.value = JSON.stringify(assignments, null, 2);
      renderGpuCapabilityReadout();
    });
    dopletForm.elements.gpu_assignments_json.addEventListener("input", renderGpuCapabilityReadout);
    dopletForm.elements.host_id.addEventListener("change", () => {
      populateGpuOptions();
      renderDopletTerminalReadout();
    });
    dopletForm.elements.flavor_id.addEventListener("change", () => {
      const flavor = flavorById(Number(dopletForm.elements.flavor_id.value || 0));
      if (!flavor) {
        return;
      }
      dopletForm.elements.vcpu.value = flavor.vcpu;
      dopletForm.elements.ram_mb.value = flavor.ram_mb;
      dopletForm.elements.disk_gb.value = flavor.disk_gb;
      updateCapacityReadout();
    });

    async function queueDopletAction(action, path, body = {}) {
      try {
        const item = action === "create" ? await ensureDopletSaved() : requireManagedDoplet();
        const host = hostById(Number(item?.host_id || dopletForm.elements.host_id.value || 0));
        if (action === "create" && host && host.status !== "ready") {
          flash("Prepare the selected host first. Save the host, capture inventory, and run Prepare Host before creating a Doplet.", "warn");
          return;
        }
        const dopletId = item?.id || dopletForm.elements.id.value || selected.dopletId;
        const result = await api(path(dopletId), { method: "POST", body: { ...body, launch: true } });
        selected.taskId = result.task?.id || selected.taskId;
        await reloadBootstrap(`Doplet ${action} started.`);
        scrollToWorkspace("manage-doplets-panel");
      } catch (error) {
        flash(error.message, "error");
      }
    }

    byId("queue-create-doplet").addEventListener("click", () => queueDopletAction("create", (id) => `/api/doplets/${id}/create`));
    byId("open-doplet-terminal").addEventListener("click", async () => {
      try {
        const item = requireManagedDoplet();
        const dopletId = item?.id || dopletForm.elements.id.value || selected.dopletId;
        const terminal = await fetchDopletTerminalInfo(dopletId);
        if (!terminal.supported) {
          flash(terminal.reason || "Terminal access is not available yet.", "warn");
          renderDopletTerminalReadout();
          return;
        }
        const result = await api(`/api/doplets/${dopletId}/open-terminal`, {
          method: "POST",
          body: {},
        });
        renderDopletManagement();
        renderDopletTerminalReadout();
        flash(
          result.terminal?.transport === "ssh"
            ? "Doplet SSH terminal opened."
            : "Doplet console opened.",
        );
      } catch (error) {
        flash(error.message, "error");
      }
    });
    byId("copy-doplet-terminal-command").addEventListener("click", async () => {
      try {
        const item = requireManagedDoplet();
        const dopletId = item?.id || dopletForm.elements.id.value || selected.dopletId;
        const info = await fetchDopletTerminalInfo(dopletId);
        if (!info.supported) {
          flash(info.reason, "warn");
          return;
        }
        await copyText(info.preview_command, "Terminal command");
      } catch (error) {
        flash(error.message, "error");
      }
    });
    byId("resize-doplet").addEventListener("click", async () => {
      try {
        const item = requireManagedDoplet();
        const dopletId = item?.id || dopletForm.elements.id.value || selected.dopletId;
        const result = await api(`/api/doplets/${dopletId}/resize`, {
          method: "POST",
          body: {
            launch: true,
            vcpu: Number(dopletForm.elements.resize_vcpu.value || 0) || undefined,
            ram_mb: Number(dopletForm.elements.resize_ram_mb.value || 0) || undefined,
            disk_gb: Number(dopletForm.elements.resize_disk_gb.value || 0) || undefined,
          },
        });
        selected.taskId = result.task?.id || selected.taskId;
        await reloadBootstrap("Resize started.");
      } catch (error) {
        flash(error.message, "error");
      }
    });
    byId("backup-doplet").addEventListener("click", () => queueDopletAction("backup", (id) => `/api/doplets/${id}/backup`));
    byId("snapshot-doplet").addEventListener("click", async () => {
      try {
        const item = requireManagedDoplet();
        const dopletId = item?.id || dopletForm.elements.id.value || selected.dopletId;
        const result = await api(`/api/doplets/${dopletId}/snapshot`, {
          method: "POST",
          body: {
            launch: true,
            snapshot_name: dopletForm.elements.snapshot_name.value || undefined,
          },
        });
        selected.taskId = result.task?.id || selected.taskId;
        await reloadBootstrap("Snapshot started.");
      } catch (error) {
        flash(error.message, "error");
      }
    });
    byId("clone-doplet").addEventListener("click", async () => {
      try {
        const item = requireManagedDoplet();
        const dopletId = item?.id || dopletForm.elements.id.value || selected.dopletId;
        const result = await api(`/api/doplets/${dopletId}/clone`, {
          method: "POST",
          body: {
            launch: true,
            name: dopletForm.elements.clone_name.value || undefined,
            slug: dopletForm.elements.clone_slug.value || undefined,
            host_id: Number(dopletForm.elements.clone_host_id.value || 0) || undefined,
            primary_network_id: Number(dopletForm.elements.clone_primary_network_id.value || 0) || undefined,
            storage_backend: dopletForm.elements.clone_storage_backend.value || undefined,
          },
        });
        selected.taskId = result.task?.id || selected.taskId;
        await reloadBootstrap("Clone started.");
      } catch (error) {
        flash(error.message, "error");
      }
    });
    byId("doplet-start").addEventListener("click", () => queueDopletAction("start", (id) => `/api/doplets/${id}/lifecycle/start`));
    byId("doplet-shutdown").addEventListener("click", () => queueDopletAction("shutdown", (id) => `/api/doplets/${id}/lifecycle/shutdown`));
    byId("doplet-reboot").addEventListener("click", () => queueDopletAction("reboot", (id) => `/api/doplets/${id}/lifecycle/reboot`));
    byId("doplet-force-stop").addEventListener("click", () => queueDopletAction("force stop", (id) => `/api/doplets/${id}/lifecycle/force-stop`));
    byId("doplet-delete-task").addEventListener("click", () => queueDopletAction("delete", (id) => `/api/doplets/${id}/lifecycle/delete`));
    byId("delete-doplet").addEventListener("click", async () => {
      let item;
      try {
        item = requireManagedDoplet();
      } catch (error) {
        flash(error.message, "warn");
        return;
      }
      try {
        const dopletId = item.id;
        await api(`/api/doplets/${dopletId}`, { method: "DELETE" });
        selected.dopletId = null;
        clearForm(dopletForm);
        await reloadBootstrap("Doplet record deleted.");
        scrollToWorkspace("manage-doplets-panel");
      } catch (error) {
        flash(error.message, "error");
      }
    });

    const restoreForm = byId("restore-form");
    byId("clear-restore-form").addEventListener("click", clearRestoreDraft);
    byId("queue-restore-snapshot").addEventListener("click", async () => {
      const snapshotId = restoreForm.elements.snapshot_id.value || selected.snapshotId;
      if (!snapshotId) {
        flash("Select a snapshot first.", "warn");
        return;
      }
      try {
        const result = await api(`/api/snapshots/${snapshotId}/restore`, {
          method: "POST",
          body: {
            launch: true,
            target_doplet_id: Number(restoreForm.elements.target_doplet_id.value || 0) || undefined,
            name: restoreForm.elements.name.value || undefined,
            slug: restoreForm.elements.slug.value || undefined,
            host_id: Number(restoreForm.elements.host_id.value || 0) || undefined,
            primary_network_id: Number(restoreForm.elements.primary_network_id.value || 0) || undefined,
            storage_backend: restoreForm.elements.storage_backend.value || undefined,
          },
        });
        selected.taskId = result.task?.id || selected.taskId;
        await reloadBootstrap("Restore started.");
      } catch (error) {
        flash(error.message, "error");
      }
    });

    const networkForm = byId("network-form");
    networkForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      const payload = {
        id: networkForm.elements.id.value || undefined,
        name: networkForm.elements.name.value,
        slug: networkForm.elements.slug.value,
        host_id: Number(networkForm.elements.host_id.value || 0),
        mode: networkForm.elements.mode.value,
        cidr: networkForm.elements.cidr.value,
        bridge_name: networkForm.elements.bridge_name.value,
        nat_enabled: networkForm.elements.nat_enabled.checked,
        firewall_policy: parseJsonField(networkForm.elements.firewall_policy.value, {}),
      };
      try {
        await api("/api/networks", { method: "POST", body: payload });
        await reloadBootstrap("Network saved.");
      } catch (error) {
        flash(error.message, "error");
      }
    });
    byId("clear-network-form").addEventListener("click", () => {
      selected.networkId = null;
      clearForm(networkForm);
      renderNetworks();
    });
    byId("delete-network").addEventListener("click", async () => {
      const networkId = networkForm.elements.id.value || selected.networkId;
      if (!networkId) {
        flash("Select a network first.", "warn");
        return;
      }
      try {
        await api(`/api/networks/${networkId}`, { method: "DELETE" });
        selected.networkId = null;
        clearForm(networkForm);
        await reloadBootstrap("Network deleted.");
      } catch (error) {
        flash(error.message, "error");
      }
    });
    byId("apply-network").addEventListener("click", async () => {
      const networkId = networkForm.elements.id.value || selected.networkId;
      if (!networkId) {
        flash("Select or save a network first.", "warn");
        return;
      }
      try {
        const result = await api(`/api/networks/${networkId}/apply`, { method: "POST", body: { launch: true } });
        selected.taskId = result.task?.id || selected.taskId;
        await reloadBootstrap("Network apply started.");
      } catch (error) {
        flash(error.message, "error");
      }
    });
    byId("delete-network-runtime").addEventListener("click", async () => {
      const networkId = networkForm.elements.id.value || selected.networkId;
      if (!networkId) {
        flash("Select or save a network first.", "warn");
        return;
      }
      try {
        const result = await api(`/api/networks/${networkId}/runtime-delete`, { method: "POST", body: { launch: true } });
        selected.taskId = result.task?.id || selected.taskId;
        await reloadBootstrap("Network runtime delete started.");
      } catch (error) {
        flash(error.message, "error");
      }
    });

    const providerForm = byId("provider-form");
    providerForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      const payload = {
        id: providerForm.elements.id.value || undefined,
        name: providerForm.elements.name.value,
        slug: providerForm.elements.slug.value,
        provider_type: providerForm.elements.provider_type.value,
        endpoint: providerForm.elements.endpoint.value,
        bucket: providerForm.elements.bucket.value,
        region: providerForm.elements.region.value,
        root_path: providerForm.elements.root_path.value,
        access_key_id: providerForm.elements.access_key_id.value,
        secret_key: providerForm.elements.secret_key.value || undefined,
        quota_model: parseJsonField(providerForm.elements.quota_model.value, {}),
        policy_notes: providerForm.elements.policy_notes.value,
        enabled: providerForm.elements.enabled.checked,
      };
      try {
        await api("/api/providers", { method: "POST", body: payload });
        await reloadBootstrap("Provider saved.");
      } catch (error) {
        flash(error.message, "error");
      }
    });
    byId("clear-provider-form").addEventListener("click", () => {
      selected.providerId = null;
      clearForm(providerForm);
      renderProviders();
    });
    byId("test-provider").addEventListener("click", async () => {
      const providerId = providerForm.elements.id.value || selected.providerId;
      if (!providerId) {
        flash("Select a provider first.", "warn");
        return;
      }
      try {
        const result = await api(`/api/providers/${providerId}/test`, { method: "POST", body: {} });
        flash(result.detail || "Provider is reachable.");
      } catch (error) {
        flash(error.message, "error");
      }
    });
    byId("delete-provider").addEventListener("click", async () => {
      const providerId = providerForm.elements.id.value || selected.providerId;
      if (!providerId) {
        flash("Select a provider first.", "warn");
        return;
      }
      try {
        await api(`/api/providers/${providerId}`, { method: "DELETE" });
        selected.providerId = null;
        clearForm(providerForm);
        await reloadBootstrap("Provider deleted.");
      } catch (error) {
        flash(error.message, "error");
      }
    });

    const userForm = byId("user-form");
    userForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      const payload = {
        username: userForm.elements.username.value,
        email: userForm.elements.email.value,
        password: userForm.elements.password.value,
        role: userForm.elements.role.value,
        mfa_enabled: userForm.elements.mfa_enabled.checked,
      };
      try {
        await api("/api/users", { method: "POST", body: payload });
        clearForm(userForm);
        await reloadBootstrap("User created.");
      } catch (error) {
        flash(error.message, "error");
      }
    });
  }

  bindActions();
  renderAll();
  renderHostModeHelp();
  applyRoleGating();

  window.setInterval(() => {
    const active = (state.tasks || []).some((task) => ["queued", "running", "planned"].includes(task.status));
    if (active) {
      reloadBootstrap().catch(() => {});
    }
  }, 4000);

  api("/api/users")
    .then((payload) => {
      state.users = payload.users || [];
      renderUsers();
    })
    .catch((error) => {
      flash(error.message, "error");
    });
  api("/api/backups")
    .then((payload) => {
      state.backups = payload.backups || [];
      renderBackups();
    })
    .catch((error) => {
      flash(error.message, "error");
    });
})();


