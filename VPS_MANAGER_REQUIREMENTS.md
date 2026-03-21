# VPS Manager Requirements

## Purpose

Define requirements for a desktop application that can:

1. Replicate the current Harmonizer VPS deployment exactly.
2. Generalize that flow into a reusable "one-stop shop" for self-hosting and VPS management.
3. Support both remote VPS hosting and local-machine hosting.

This document is based on:

- The repo deployment files and docs.
- Direct inspection of the live VPS at `harmonizer@164.92.64.157`.

## Product Vision

The product should let a user go from a fresh machine to a working hosted stack with minimal manual shell usage. It should:

- Walk the user through SSH, repo access, environment variables, domains, TLS, Docker, Nginx, storage, backups, and monitoring.
- Support both "host on a remote VPS" and "host on this machine".
- Continue acting as an operations console after setup.

## Primary Modes

### 1. Remote VPS Mode

The app provisions and manages a Linux VPS over SSH.

### 2. Local Host Mode

The app turns the user's own machine into the host.

- Linux: native host mode.
- Windows: host mode via WSL2-backed Linux environment.

### 3. Project Template Mode

The app should support reusable project definitions so Harmonizer is not the only supported stack.

## Exact Harmonizer Replication Requirements

These are the requirements needed to reproduce the current live deployment.

### Live Topology

The app must be able to reproduce this stack:

- `harmonizer` container serving Flask + Gunicorn on port `5000`
- `codesniff` container serving FastAPI on port `8000`
- `watchtower` container
- Nginx on host machine
- TLS via Let's Encrypt
- Persistent bind mounts for:
  - `uploads`
  - `data`
  - `backend/ourspace_data`
- Persistent Docker volume for `codesniff_storage`

### Current Live Host Facts

The current live deployment uses:

- Linux host
- Ubuntu family OS
- Docker Compose
- Nginx reverse proxy
- `static` branch deployment
- Host-side CodeSniff frontend build before container rebuild

### Current Live Deploy Flow

The app must be able to reproduce the actual deploy sequence:

1. Connect over SSH.
2. Go to `/home/harmonizer/apps/harmonizer`.
3. Reset repo to `origin/static`.
4. Build `codesniff/frontend`.
5. Ensure `.env` exists.
6. Rebuild containers with Docker Compose.
7. Preserve persistent data directories.

### Harmonizer-Specific Environment Requirements

The app must support collecting, storing securely, validating, and writing:

- `SECRET_KEY`
- `GEMINI_API_KEY`
- `GROQ_API_KEY`
- `PRIMARY_DOMAIN`
- `SECONDARY_DOMAIN`
- Any future project-specific variables

For Harmonizer, the current live domain behavior includes:

- `PRIMARY_DOMAIN=harmonizer.cc`
- `SECONDARY_DOMAIN=ourspace.icu`
- Nginx public routing for `harmonizerlabs.cc`
- Nginx public routing for `ourspace.icu`

The app must allow these to be configured explicitly instead of assuming one canonical domain.

### Reverse Proxy Requirements

The app must be able to generate and install Nginx config for:

- HTTP to HTTPS redirects
- TLS cert paths
- Reverse proxy to internal app port
- Upload size limits
- Long timeout settings for heavy requests
- Optional extra path rules such as `/cluster`

It must also detect and warn about configuration drift such as:

- duplicate or stale files still present in `sites-enabled`
- domains configured in Nginx that do not match app environment configuration

### TLS Requirements

The app must support:

- obtaining Let's Encrypt certificates
- renewing certificates
- validating DNS before cert issuance
- binding one or more domains to one app

### Persistent Data Requirements

The app must preserve and manage:

- user uploads
- app cache/data
- OurSpace database and media
- CodeSniff index storage

It must support data migration between hosts.

### Backup Requirements

The app must support:

- full backup creation
- scheduled backups
- restore from backup
- backup verification
- optional backup download/export

For Harmonizer specifically, backup coverage should include:

- `uploads`
- `data`
- `backend/ourspace_data`
- `.env`
- optionally CodeSniff storage volume

### Resource Requirements

The current deployment shows memory pressure. The app must support:

- swap file creation on Linux hosts
- memory recommendations before install
- container resource limits
- low-resource warnings
- disk usage monitoring

The app should flag that the live deployment currently uses swap heavily and has seen Gunicorn worker OOM failures.

### Network Exposure Requirements

The app must distinguish between:

- public ports intended to be exposed
- internal-only service ports

For a safer generalized setup, the app should default to:

- public: `22`, `80`, `443`
- internal-only: application service ports such as `5000` and `8000`

It should warn if Docker publishes internal service ports to the public interface.

## General Product Requirements

### Setup Wizard

The app must provide a guided setup flow that covers:

1. Choose host mode: remote VPS or local machine.
2. Choose project template.
3. Configure SSH.
4. Configure domains and DNS.
5. Configure environment variables.
6. Configure reverse proxy and TLS.
7. Configure storage and backup policy.
8. Install dependencies and deploy.
9. Run health checks.

### SSH Requirements

The app must support:

- using an existing SSH key from the user's machine
- generating a new Ed25519 key
- copying the public key to a VPS
- testing SSH connectivity
- storing host fingerprints
- selecting which key to use per host

For Windows, the app should detect and offer:

- OpenSSH keys in the user profile
- WSL-side SSH keys

### Local Host Mode Requirements

#### Linux

The app must install and manage the stack directly on Linux.

#### Windows

The app must support WSL2-based hosting.

It should be able to:

- install or validate WSL2
- install a supported Linux distro
- install Docker tooling inside the Linux environment, or integrate with Docker Desktop if explicitly chosen
- store project data in a stable location
- bridge ports from WSL to Windows
- manage startup behavior across Windows reboots

### Power and Availability Requirements

The app must support host power policies for local hosting.

It should provide:

- performance mode
- balanced mode
- low power mode

Low power mode should:

- reduce optional services
- reduce polling frequency
- lower resource ceilings where safe

The app must also explain a hard constraint:

- if the laptop sleeps, local hosting is not truly available

Therefore it should support:

- preventing sleep while services are marked critical
- start on boot
- restart after crash
- battery threshold warnings
- explicit "hosting may stop if this device sleeps" messaging

### Monitoring and Operations Requirements

The app must continue to be useful after deployment.

It should provide:

- service status
- CPU, memory, swap, disk, and network usage
- container logs
- domain/TLS status
- backup status
- storage growth
- restart controls
- redeploy controls
- drift detection

### Health Checks

The app must support post-deploy verification:

- DNS resolves correctly
- Nginx config valid
- TLS active
- containers healthy
- required routes respond
- persistent mounts present
- free disk/memory above threshold

For Harmonizer, checks should include:

- main app reachable
- OurSpace domain reachable
- CodeSniff backend reachable
- CodeSniff frontend assets present

### Secrets Handling

The app must:

- never log raw secret values
- store secrets encrypted at rest if stored locally
- redact secrets in UI and logs
- support secret rotation
- support importing/exporting environment definitions without raw secret leakage unless explicitly requested

### Idempotency and Recovery

The app must be safe to rerun.

It should:

- detect already-installed components
- avoid duplicating config
- support resume after interruption
- support rollback of failed deploys
- snapshot existing config before mutating host files

### Project Template System

The app should support a declarative template format describing:

- repo URL
- branch
- required env vars
- build steps
- services
- reverse proxy routes
- ports
- persistent paths
- backup paths
- health checks

Harmonizer should be implemented as the first full template.

## Non-Functional Requirements

### Target Experience

- One guided setup flow
- Minimal manual terminal use
- Clear progress and failure reporting
- Copyable remediation steps when automation cannot continue

### Performance

- Fresh install target: under 10-15 minutes when dependencies and network conditions are favorable
- Redeploy target: as fast as project rebuild permits

### Security

- least-privilege where practical
- explicit elevation only when needed
- SSH and secret hygiene by default

### Auditability

The app should keep an operation log of:

- what changed
- when it changed
- which commands were run
- what failed
- how to revert

## Recommended MVP Scope

### MVP 1: Harmonizer Remote VPS Replication

Support:

- SSH key setup
- server dependency checks
- repo clone/reset to `static`
- env collection
- host-side CodeSniff frontend build
- Docker Compose deploy
- Nginx config generation
- TLS issuance
- backup setup
- health checks

### MVP 2: Harmonizer Local Host

Support:

- Linux native hosting
- Windows WSL2 hosting
- power management guidance
- startup/restart behavior

### MVP 3: Generalized Project Templates

Support:

- reusable host/deploy templates
- multiple projects
- per-project monitoring

## Explicit Risks and Constraints

- Windows hosting should use Linux-compatible execution through WSL2 for stacks like this.
- A sleeping laptop cannot behave like a real always-on VPS.
- Root-required host mutations will still need elevation.
- Public Docker port publishing is convenient but should not be the secure default.
- The live Harmonizer deployment already contains config drift and operational debt; the app should detect and normalize that instead of copying it blindly.

## Recommended Architecture

Suggested implementation direction:

- Desktop UI shell
- privileged local worker for host operations
- SSH execution engine for remote hosts
- template-driven deployment engine
- state database for hosts, projects, env definitions, and health history

For cross-platform delivery, a practical direction is:

- Tauri or Electron for UI
- a typed backend worker
- PowerShell integration for Windows
- Bash/SSH integration for Linux and remote hosts

## Immediate Next Deliverables

1. Define the Harmonizer template schema.
2. Define the exact setup wizard flow for remote VPS mode.
3. Define the exact setup wizard flow for Windows WSL2 local-host mode.
4. Define the monitoring dashboard requirements.
5. Define backup/restore flows and migration flows.
