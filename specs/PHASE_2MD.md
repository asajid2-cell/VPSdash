# Phase 2MD

## Purpose

This document is the master delivery spec for building the full single-node doplet platform now, not a minimal subset.

`Phase 2MD` means:

- the platform is planned as a complete product line for a single physical host
- core systems are designed up front instead of being left as vague future ideas
- implementation can still be sequenced, but the target feature surface is decided now

This document supersedes any earlier "do a small V1 first and defer the rest" framing for the single-node product.

## Product Boundary

The product we are building is:

- a hardened host-node installer and validator
- a multi-doplet VM management platform
- a resource-aware compute, storage, and network control plane
- a backup, restore, clone, and snapshot system
- a security-first operations surface
- a native desktop control client for managing the host and its doplets
- a secure web control plane for credentialed remote admin

The product we are not building in this phase:

- multi-host clustering and live migration
- commercial billing / metering platform
- Kubernetes orchestration
- true shared vGPU slicing across many doplets

Those are not "later maybe" placeholders in this file. They are outside the current product boundary.

## Full-Scope Commitment

For the single-node platform, the following are in scope now:

- host preparation and hardening
- virtualization stack installation and validation
- image catalog and template system
- doplet creation wizard
- full doplet lifecycle management
- managed doplet inventory
- capacity accounting and placement checks
- NAT, bridged, and private doplet networks
- per-doplet firewall posture
- quarantine and incident response tools
- manual and scheduled backups
- snapshots, restore, and clone
- remote backup targets
- multi-provider object-storage backup federation
- host and doplet monitoring
- audit log and task history
- SSH/bootstrap/console recovery flows
- GPU inventory, passthrough, and mediated-device abstraction where the host supports it
- native desktop and web UX for create, manage, inspect, and recover

## Delivery Rule

No major capability in the single-node platform should be represented as:

- placeholder UI with no real backend
- "planned later" labels for core management features
- fake resource allocation
- hidden shell-only flows that bypass the product

If a feature is in scope, it must have:

- data model coverage
- backend orchestration plan
- UI surface definition
- acceptance criteria
- explicit security posture

## Technical Position

The recommended technical base remains:

- Linux hypervisor host
- KVM/QEMU/libvirt
- native desktop control client
- secure web app + API service
- local privileged host agent
- Postgres-backed control-plane state for authenticated remote admin

This is still the best path even under the full-scope approach because it gives us the cleanest base for security, networking, snapshots, console access, and device management.

## Core Product Modules

### 1. Host Provisioning and Hardening

Responsibilities:

- validate OS, virtualization, IOMMU, storage, networking
- install hypervisor and supporting packages
- create ZFS or LVM-thin storage pools and baseline networks
- configure host firewall, service accounts, and management services
- surface host readiness and security posture in UI

Acceptance:

- a fresh supported Linux machine can be turned into a VPSdash host from the product
- readiness blockers are shown before doplet creation is allowed

### 2. Compute and Doplet Lifecycle

Responsibilities:

- define doplets as managed VM instances
- create, start, stop, reboot, force stop, delete
- resize supported resources
- provide console access and recovery paths
- support clone and restore-as-new

Acceptance:

- every doplet action is task-driven, logged, and reflected in inventory
- every created doplet becomes a first-class managed object automatically

### 3. Image, Template, and Flavor System

Responsibilities:

- image catalog for Ubuntu, Debian, Fedora, Alpine, and custom import
- flavor presets for CPU, RAM, disk, and optional GPU posture
- project templates that prefill network, bootstrap, firewall, and backup defaults
- reusable setup defaults for host + project + doplet patterns

Acceptance:

- operators can create doplets from images, flavors, or templates without shell edits
- templates can be added through file-backed definitions and from the desktop UI

### 4. Capacity and Placement Engine

Responsibilities:

- detect total and allocatable CPU, RAM, disk, and GPU inventory
- track host reserve, allocations, and remaining headroom
- prevent invalid allocations before task execution
- show live remaining capacity while building a doplet

Acceptance:

- create flow updates remaining resources live
- impossible allocations are blocked before provisioning starts

### 5. Networking and Isolation

Responsibilities:

- NAT networking
- bridged/LAN-visible networking
- private doplet-to-doplet networks
- port mapping where appropriate
- per-doplet ingress posture
- firewall templates and quarantine mode

Acceptance:

- each doplet is attached to a declared network posture
- doplets are isolated from each other by default unless explicitly grouped

### 6. Storage, Snapshot, Backup, and Restore

Responsibilities:

- root disk and attached volume management
- ZFS and LVM-thin backend management
- snapshots
- manual backups
- scheduled backups
- restore in place
- restore as new doplet
- clone from backup or snapshot
- local and remote backup targets
- multi-provider object-storage targets
- quota-aware provider selection and placement
- retention and pruning policies

Acceptance:

- operators can create and restore backups without leaving the product
- backup artifacts, status, and restore history are visible per doplet

### 7. Security and Incident Response

Responsibilities:

- default-secure doplet posture
- no host folder sharing by default
- no device passthrough by default
- quarantine action
- audit log
- incident timeline
- warnings for weaker configurations such as bridged networking or passthrough

Acceptance:

- a suspected doplet can be quarantined from the UI
- risky features are explicit and carry elevated warnings

### 8. GPU and Device Management

Responsibilities:

- host GPU inventory
- passthrough eligibility checks
- mediated-device capability detection
- assign whole GPU devices to supported doplets
- expose vGPU / mediated-device profiles where actually supported
- show unavailable or already-bound devices
- surface security and compatibility warnings

Acceptance:

- the product never promises GPU sharing it cannot safely provide
- GPU passthrough, when supported, is modeled as an exclusive host resource
- mediated-device offerings are capability-driven and vendor-specific under one product abstraction

### 9. Monitoring, Audit, and Task Execution

Responsibilities:

- host resource dashboard
- per-doplet metrics
- task runner for long operations
- detailed task logs
- audit/event history
- alerts for failed tasks, low capacity, and risky states

Acceptance:

- long-running operations show progress and outcome in the product
- operators can inspect why a doplet create, backup, or restore failed

### 10. Desktop UX and Management Surface

Responsibilities:

- create host wizard
- create doplet wizard
- doplet inventory
- doplet detail page
- backup center
- network manager
- image/template manager
- host dashboard
- security posture page
- settings and backup target management
- remote-admin web app parity for core management flows

Acceptance:

- all major lifecycle operations are reachable from the desktop client
- all major lifecycle operations are reachable from the secure web app
- no core management path depends on hidden docs or shell spelunking

## Required User Flows

### Flow A: Prepare Computer B as a Host

1. User opens VPSdash on the future host or from a management machine.
2. User chooses `Prepare this machine as host`.
3. Product validates host suitability.
4. Product installs the hypervisor stack and configures baseline security.
5. Product creates storage and network baselines.
6. Product presents host inventory, reserves, and readiness.

### Flow B: Create a Doplet

1. User chooses image, flavor, or workload template.
2. User edits CPU, RAM, disk, network, SSH, backup, and optional GPU settings.
3. Product shows total, allocated, requested, and remaining resources.
4. Product blocks impossible or unsafe allocations.
5. Product provisions the doplet and adds it to inventory.

### Flow C: Manage a Doplet

1. User opens doplet detail.
2. User sees status, health, IPs, disks, networks, backups, and task history.
3. User can start, stop, reboot, resize, backup, snapshot, restore, clone, quarantine, or delete.

### Flow D: Recover from Failure or Compromise

1. User sees failed health or suspicious activity.
2. User can quarantine.
3. User can snapshot, clone for analysis, inspect console, restore, or destroy.

### Flow E: Remote Admin Session

1. User signs in through the VPSdash web app.
2. User passes credential checks and any second-factor requirements.
3. User views hosts, doplets, backups, alerts, and recent tasks.
4. User performs allowed management actions and those actions are audited.

## Full Current Workstreams

These are not deferred phases. They are the workstreams that make up the current build.

### Workstream 1: Host Node Foundation

- host validation
- virtualization and IOMMU checks
- package install and service configuration
- firewall baseline
- ZFS and LVM-thin storage pool creation
- baseline networks

### Workstream 2: Doplet Runtime and Lifecycle

- libvirt domain modeling
- doplet create/start/stop/delete/reboot
- console access
- resize support
- clone flows

### Workstream 3: Capacity Planner

- host reserve policy
- resource accounting model
- live create-form calculations
- allocation validation

### Workstream 4: Networks and Isolation

- NAT networks
- bridged networks
- private networks
- ingress policy
- quarantine

### Workstream 5: Backup and Recovery

- snapshots
- manual backups
- scheduled backups
- restore in place
- restore as new
- local and remote backup targets
- object-storage provider adapters
- quota-aware backup placement

### Workstream 6: Security and Audit

- hardening posture page
- risk-tier labels
- audit trail
- warning models
- task/event log

### Workstream 7: GPU and Device Features

- device inventory
- passthrough checks
- mediated-device / vGPU abstraction
- assignment constraints
- conflict detection

### Workstream 8: Remote Admin and Identity

- web control plane
- authenticated API
- session security
- role-based access controls
- MFA and step-up verification
- email new-device verification and alerting
- remote audit visibility

### Workstream 9: UX and Operations Surface

- host dashboard
- create doplet wizard
- managed doplet pages
- inventory filters
- backup center
- network manager
- template/default editor
- desktop and web workflow parity

## Locked Platform Decisions

The current build is now locked to:

- Linux hypervisor host only
- Windows control client allowed
- Windows local execution routes through WSL where needed
- ZFS and LVM-thin storage both supported now, with host setup choosing one primary backend
- passthrough plus mediated-device abstraction, but capability-driven
- local, mounted/network, and multi-provider object-storage targets
- mixed-use host allowed with strong warnings, dedicated host still recommended
- per-user web accounts with MFA and RBAC
- email MFA / new-device verification and alerting
- HTTPS-only remote web access with reverse proxy, rate limits, and strong session controls
- LAN/VPN-only default exposure, with public exposure as an opt-in mode
- Postgres for production control-plane state
- Ubuntu Server LTS default, Debian supported

## Exit Condition for Spec Lock

Spec lock is reached when:

- the product boundary is accepted
- the remaining platform-shape decisions above are answered
- the team agrees that the current program includes the full single-node doplet platform, not an MVP subset

Current status:

- product boundary accepted
- platform-shape decisions resolved
- spec lock achieved


