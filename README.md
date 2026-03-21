# VPSdash

VPSdash is a local control plane for self-hosting Doplets on:

- remote Linux VPS hosts over SSH
- local Linux machines
- Windows machines using WSL2 as the Linux host layer

It is built around one primary flow: create a host, prepare it, create Doplets, and manage lifecycle/tasks from one control plane. The current Python package and some build artifacts still use the older `vpsdash` name internally as a legacy implementation label.

## What It Does

- manages Linux hypervisor hosts and Doplets from one control plane
- keeps per-user accounts and owner/operator/viewer roles for web admin, with email login verification available when explicitly enabled
- tracks hosts, networks, Doplets, backup providers, tasks, and audit history in a SQL-backed control plane
- captures host inventory and computes allocatable CPU, RAM, disk, and GPU headroom
- captures ZFS, LVM-thin, GPU, and mediated-device inventory in the control plane
- blocks impossible Doplet allocations before provisioning
- queues and runs host-prepare, Doplet lifecycle, snapshot, clone, restore, and backup tasks through a signed, policy-checked host-agent module
- supports task cancel/retry flows, scheduled backup queues, backup pruning, backup verification, network apply/delete runtime tasks, and Doplet resize tasks
- supports local and S3-compatible backup providers
- exposes a separate host-agent daemon entrypoint for stronger control-plane / host-execution separation
- supports embedded desktop access to the secure web admin so the native shell and web control plane stay in one installable surface
- mirrors core task and asset state inside the native desktop operations page instead of relying only on the embedded web admin
- keeps the older planner internals in the codebase, but the main desktop and web UX now center on Host Admin, Doplet Builder, and Activity

## Current Implementation

This version is implemented as a native desktop app plus a secure web control plane, both backed by shared Python services.

Main pieces:

- `run.py`: native desktop launcher
- `vpsdash/desktop.py`: native desktop client
- `vpsdash/app.py`: secure web admin and JSON API
- `vpsdash/platform_service.py`: SQL-backed control-plane service
- `vpsdash/service.py`: shared desktop/web facade
- `vpsdash/planner.py`: template-driven deploy-plan generation
- `vpsdash/diagnostics.py`: diagnostics and monitoring commands
- `specs/`: product, architecture, security, and roadmap docs for the VPSdash doplet-platform direction

## Launch

1. Install Python 3.11+ if needed.
2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Start the native desktop app:

```bash
python run.py
```

The desktop shell remains the primary installable interface.

## Web Control Plane

Start the secure local web admin:

```bash
python run_web.py
```

Then open `http://127.0.0.1:8787`.

Default local credentials on a fresh data directory:

- username: `owner`
- password: `change-me-now`

Email login verification is disabled by default in the current build. If you enable it later, challenge messages are written to `data/outbox/` unless SMTP is configured.

Optional host-agent daemon entrypoint:

```bash
python run_host_agent.py
```

Optional host acceptance report for a configured host id:

```bash
python run_acceptance.py 1 --capture-inventory
```

## Packaging

Windows:

```powershell
./build_windows.ps1
```

Run the packaged app from:

```text
dist/VPSdash/VPSdash.exe
```

Do not launch `build/VPSdash/VPSdash.exe`. That file is a PyInstaller intermediate and will fail because it does not sit beside the `_internal/` runtime bundle.

Linux:

```bash
./build_linux.sh
```

Both packaging scripts use PyInstaller and emit the app into `dist/`.

## Tests

```bash
python -m unittest discover -s tests
```

## Notes

- Windows local mode assumes Linux-hosted project commands run through WSL.
- Remote Linux mode is framed as Computer A reaching Computer B over SSH.
- Password-first SSH bootstrap is supported as a guided manual first-login flow; automated remote execution is intended to resume after you switch the profile to key-based SSH.
- Generated live plan steps can make real host changes. Use dry run first.
- Legacy planner state is stored in `data/state.json`.
- Control-plane state defaults to `data/vpsdash.db` unless `VPSDASH_DATABASE_URL` is set.



