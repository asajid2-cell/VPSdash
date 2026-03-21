const appState = {
  templates: [],
  hosts: [],
  projects: [],
  currentTemplate: null,
  currentHost: null,
  currentProject: null,
  generatedPlan: null,
};

const elements = {};

document.addEventListener("DOMContentLoaded", async () => {
  bindElements();
  bindEvents();
  await refreshApp();
});

function bindElements() {
  for (const id of [
    "metricTemplates", "metricHosts", "metricProjects",
    "savedHostSelect", "savedProjectSelect", "templateSelect",
    "hostName", "hostMode", "hostSshUser", "hostSshHost", "hostSshPort", "hostSshKey", "hostWslDistribution",
    "projectName", "projectRepoUrl", "projectBranch", "projectDeployPath",
    "projectPrimaryDomain", "projectLetsEncryptEmail", "projectDomains",
    "envList", "planSummary", "planWarnings", "planStages",
    "diagnosticsOutput", "monitorOutput", "executionOutput", "keyCandidates"
  ]) {
    elements[id] = document.getElementById(id);
  }
}

function bindEvents() {
  document.getElementById("refreshAppButton").addEventListener("click", refreshApp);
  document.getElementById("loadGenericTemplateButton").addEventListener("click", () => applyTemplate("generic-docker-webapp"));
  document.getElementById("templateSelect").addEventListener("change", (event) => applyTemplate(event.target.value));
  document.getElementById("savedHostSelect").addEventListener("change", () => loadSelectedHost());
  document.getElementById("savedProjectSelect").addEventListener("change", () => loadSelectedProject());
  document.getElementById("addEnvButton").addEventListener("click", addEnvRow);
  document.getElementById("saveHostButton").addEventListener("click", saveHost);
  document.getElementById("saveProjectButton").addEventListener("click", saveProject);
  document.getElementById("generatePlanButton").addEventListener("click", generatePlan);
  document.getElementById("runDiagnosticsButton").addEventListener("click", runDiagnostics);
  document.getElementById("runMonitorButton").addEventListener("click", runMonitor);
  document.getElementById("dryRunButton").addEventListener("click", () => executePlan(true));
  document.getElementById("executePlanButton").addEventListener("click", () => executePlan(false));
}

async function refreshApp() {
  const response = await fetchJson("/api/bootstrap");
  appState.templates = response.templates || [];
  appState.hosts = response.state.hosts || [];
  appState.projects = response.state.projects || [];

  renderMetrics();
  renderKeyCandidates(response.key_candidates || []);
  renderTemplateSelect();
  renderHostSelect();
  renderProjectSelect();

  if (!appState.currentTemplate && appState.templates.length) {
    applyTemplate(appState.templates[0].id);
  }
}

function renderMetrics() {
  elements.metricTemplates.textContent = String(appState.templates.length);
  elements.metricHosts.textContent = String(appState.hosts.length);
  elements.metricProjects.textContent = String(appState.projects.length);
}

function renderKeyCandidates(paths) {
  elements.keyCandidates.innerHTML = "";
  for (const path of paths) {
    const option = document.createElement("option");
    option.value = path;
    elements.keyCandidates.appendChild(option);
  }
}

function renderTemplateSelect() {
  const select = elements.templateSelect;
  select.innerHTML = "";
  for (const template of appState.templates) {
    const option = document.createElement("option");
    option.value = template.id;
    option.textContent = `${template.name} - ${template.description}`;
    select.appendChild(option);
  }
  if (appState.currentTemplate) {
    select.value = appState.currentTemplate.id;
  }
}

function renderHostSelect() {
  const select = elements.savedHostSelect;
  select.innerHTML = "";
  const blank = new Option("Saved hosts", "");
  select.add(blank);
  for (const host of appState.hosts) {
    select.add(new Option(`${host.name} (${host.mode})`, host.id));
  }
}

function renderProjectSelect() {
  const select = elements.savedProjectSelect;
  select.innerHTML = "";
  const blank = new Option("Saved projects", "");
  select.add(blank);
  for (const project of appState.projects) {
    select.add(new Option(`${project.name} (${project.template_id})`, project.id));
  }
}

function applyTemplate(templateId) {
  const template = appState.templates.find((item) => item.id === templateId);
  if (!template) return;
  appState.currentTemplate = structuredClone(template);
  appState.currentProject = structuredClone(template);
  appState.currentProject.template_id = template.id;
  syncProjectForm(appState.currentProject);
}

function loadSelectedHost() {
  const host = appState.hosts.find((item) => item.id === elements.savedHostSelect.value);
  if (!host) return;
  appState.currentHost = structuredClone(host);
  syncHostForm(host);
}

function loadSelectedProject() {
  const project = appState.projects.find((item) => item.id === elements.savedProjectSelect.value);
  if (!project) return;
  appState.currentProject = structuredClone(project);
  appState.currentTemplate = appState.templates.find((item) => item.id === project.template_id) || appState.currentTemplate;
  syncProjectForm(project);
}

function syncHostForm(host) {
  elements.hostName.value = host.name || "";
  elements.hostMode.value = host.mode || "remote-linux";
  elements.hostSshUser.value = host.ssh_user || "";
  elements.hostSshHost.value = host.ssh_host || "";
  elements.hostSshPort.value = host.ssh_port || 22;
  elements.hostSshKey.value = host.ssh_key_path || "";
  elements.hostWslDistribution.value = host.wsl_distribution || "Ubuntu";
}

function syncProjectForm(project) {
  elements.templateSelect.value = project.template_id || project.id || "";
  elements.projectName.value = project.name || "";
  elements.projectRepoUrl.value = project.repo_url || "";
  elements.projectBranch.value = project.branch || "";
  elements.projectDeployPath.value = project.deploy_path || "";
  elements.projectPrimaryDomain.value = project.primary_domain || "";
  elements.projectLetsEncryptEmail.value = project.letsencrypt_email || "";
  elements.projectDomains.value = (project.domains || []).join("\n");
  renderEnvList(project.env || []);
}

function renderEnvList(envItems) {
  elements.envList.innerHTML = "";
  for (const item of envItems) {
    addEnvRow(item);
  }
}

function addEnvRow(seed = null) {
  const row = document.createElement("div");
  row.className = "env-row";
  row.innerHTML = `
    <div class="env-grid">
      <label>
        <span>Key</span>
        <input class="input env-key" value="${escapeHtml(seed?.key || "")}" placeholder="SECRET_KEY">
      </label>
      <label>
        <span>Value</span>
        <input class="input env-value" value="${escapeHtml(seed?.value || "")}" placeholder="value">
      </label>
      <label class="check">
        <input type="checkbox" class="env-secret" ${seed?.secret ? "checked" : ""}>
        <span>Secret</span>
      </label>
      <button class="button button-ghost env-remove" type="button">Remove</button>
    </div>
  `;
  row.querySelector(".env-remove").addEventListener("click", () => row.remove());
  elements.envList.appendChild(row);
}

function collectHost() {
  return {
    id: appState.currentHost?.id,
    name: elements.hostName.value.trim() || "New host",
    mode: elements.hostMode.value,
    ssh_user: elements.hostSshUser.value.trim(),
    ssh_host: elements.hostSshHost.value.trim(),
    ssh_port: Number(elements.hostSshPort.value || 22),
    ssh_key_path: elements.hostSshKey.value.trim(),
    wsl_distribution: elements.hostWslDistribution.value.trim() || "Ubuntu",
  };
}

function collectProject() {
  return {
    ...(appState.currentProject || {}),
    id: appState.currentProject?.id,
    template_id: elements.templateSelect.value,
    name: elements.projectName.value.trim() || "New project",
    repo_url: elements.projectRepoUrl.value.trim(),
    branch: elements.projectBranch.value.trim() || "main",
    deploy_path: elements.projectDeployPath.value.trim() || "~/apps/my-app",
    primary_domain: elements.projectPrimaryDomain.value.trim(),
    letsencrypt_email: elements.projectLetsEncryptEmail.value.trim() || "admin@example.com",
    domains: elements.projectDomains.value.split("\n").map((item) => item.trim()).filter(Boolean),
    env: [...elements.envList.querySelectorAll(".env-row")].map((row) => ({
      key: row.querySelector(".env-key").value.trim(),
      value: row.querySelector(".env-value").value,
      secret: row.querySelector(".env-secret").checked,
    })).filter((item) => item.key),
  };
}

async function saveHost() {
  const payload = collectHost();
  const response = await fetchJson("/api/hosts/upsert", "POST", payload);
  appState.currentHost = response.host;
  appState.hosts = response.state.hosts || [];
  renderHostSelect();
  elements.savedHostSelect.value = appState.currentHost.id;
  showExecutionResult([{ ok: true, title: "Host saved", stdout: `${appState.currentHost.name} is stored in VPSdash state.` }]);
}

async function saveProject() {
  const payload = collectProject();
  const response = await fetchJson("/api/projects/upsert", "POST", payload);
  appState.currentProject = response.project;
  appState.projects = response.state.projects || [];
  renderProjectSelect();
  elements.savedProjectSelect.value = appState.currentProject.id;
  showExecutionResult([{ ok: true, title: "Project saved", stdout: `${appState.currentProject.name} is stored in VPSdash state.` }]);
}

async function generatePlan() {
  const payload = { host: collectHost(), project: collectProject() };
  const response = await fetchJson("/api/plans/generate", "POST", payload);
  appState.currentHost = response.host;
  appState.currentProject = response.project;
  appState.generatedPlan = response.plan;
  renderPlan();
}

function renderPlan() {
  const plan = appState.generatedPlan;
  if (!plan) return;
  elements.planSummary.innerHTML = `
    <strong>${escapeHtml(plan.summary.project_name)}</strong><br>
    Host mode: ${escapeHtml(plan.summary.host_mode)}<br>
    Deploy path: ${escapeHtml(plan.summary.deploy_path)}<br>
    Shell: ${escapeHtml(plan.summary.shell)}
  `;

  elements.planWarnings.innerHTML = "";
  for (const warning of plan.warnings || []) {
    const node = document.createElement("div");
    node.className = "warning";
    node.textContent = warning;
    elements.planWarnings.appendChild(node);
  }

  elements.planStages.innerHTML = "";
  for (const stage of plan.stages || []) {
    const card = document.createElement("article");
    card.className = "stage";
    const stepsMarkup = (stage.steps || []).length
      ? (stage.steps || []).map((step) => `
          <div class="step">
            <strong>${escapeHtml(step.title)}</strong>
            <div>${escapeHtml(step.detail || "")}</div>
            <code>${escapeHtml(step.command || "")}</code>
          </div>
        `).join("")
      : `<div class="empty-state">No actions in this stage for the current host mode.</div>`;
    card.innerHTML = `
      <div class="stage-head">
        <div>
          <strong>${escapeHtml(stage.title)}</strong>
          <div>${(stage.steps || []).length} step(s)</div>
        </div>
      </div>
      <div class="steps">${stepsMarkup}</div>
    `;
    elements.planStages.appendChild(card);
  }
}

async function runDiagnostics() {
  const payload = { host: collectHost(), project: collectProject() };
  const response = await fetchJson("/api/diagnostics/run", "POST", payload);
  const cards = response.checks.map(renderResultCard).join("");
  elements.diagnosticsOutput.innerHTML = cards || `<div class="empty-state">No diagnostic results returned.</div>`;
}

async function runMonitor() {
  const payload = { host: collectHost(), project: collectProject() };
  const response = await fetchJson("/api/monitor/snapshot", "POST", payload);
  const cards = Object.entries(response).map(([key, value]) => renderResultCard({ title: key, ...value })).join("");
  elements.monitorOutput.innerHTML = cards || `<div class="empty-state">No monitor output returned.</div>`;
}

async function executePlan(dryRun) {
  if (!appState.generatedPlan) {
    await generatePlan();
  }
  const steps = (appState.generatedPlan?.stages || []).flatMap((stage) => stage.steps || []);
  const response = await fetchJson("/api/actions/execute", "POST", {
    host: collectHost(),
    steps,
    dry_run: dryRun,
  });
  showExecutionResult(response.results || []);
}

function showExecutionResult(results) {
  elements.executionOutput.innerHTML = results.map(renderResultCard).join("");
}

function renderResultCard(result) {
  const statusClass = result.ok ? "result-ok" : "result-fail";
  const extra = [result.stdout, result.stderr, result.command].filter(Boolean).join("\n\n");
  return `
    <article class="result-card ${statusClass}">
      <strong>${escapeHtml(result.title || "Result")}</strong>
      <div>${result.ok ? "Success" : "Failed"}${result.dry_run ? " - dry run" : ""}${result.skipped ? " - skipped" : ""}</div>
      ${extra ? `<pre>${escapeHtml(extra)}</pre>` : ""}
    </article>
  `;
}

async function fetchJson(url, method = "GET", body = null) {
  const options = { method, headers: {} };
  if (body !== null) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  const response = await fetch(url, options);
  if (!response.ok) {
    throw new Error(`Request failed: ${response.status}`);
  }
  return response.json();
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

