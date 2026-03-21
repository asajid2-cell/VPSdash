# Open Doplet Platform PRD

## Product Definition

VPSdash will evolve from a setup-and-operations dashboard into a secure local cloud platform that can create and manage many isolated doplets on one physical machine.

In practical terms, the product becomes:

- a host installer and hardening tool
- a doplet creation and lifecycle manager
- a resource planner and capacity dashboard
- a backup, restore, and cloning control plane
- a security-first management surface for isolated workloads
- a secure remote-admin platform with both desktop and web access

The target experience is "open source DigitalOcean for a single machine" with strong local-first control and clear resource accounting.

## Product Vision

Let a user turn a spare computer or dedicated server into a hardened doplet host, then create and operate many isolated doplets from desktop and web control surfaces without hand-building virtualization, networking, storage, backup, and remote-management stacks.

## Core Principles

- Isolation first: doplets are virtual machines by default, not just containers.
- Host safety first: a compromised doplet should not have an easy path to the host.
- Capacity honesty: the UI should show real host resources, real allocations, and real remaining headroom.
- Managed lifecycle: once created, a doplet becomes a first-class managed object.
- Safe defaults: users must opt into weaker isolation features.
- Clarity over magic: the product should make tradeoffs explicit.

## Personas

### 1. Solo Operator

Runs personal services, side projects, and experimental servers on one local machine.

Needs:

- easy doplet creation
- honest resource planning
- backups
- minimal shell work

### 2. Small Team Admin

Runs multiple isolated environments for dev, staging, internal tools, or client demos.

Needs:

- multiple doplets
- network separation
- snapshots and restore
- auditability

### 3. Security-Conscious Self-Hoster

Assumes workloads may be compromised and wants strong separation from the host and between doplets.

Needs:

- VM isolation
- no shared host folders by default
- per-doplet firewalling
- quarantine and backup

## Problem Statement

Today VPSdash can save setup state and automate one stack deployment, but it does not yet model secure isolated instances as actual virtualized compute units. There is no true multi-doplet lifecycle, no host scheduler, no doplet-level resource accounting, and no strong security boundary between hosted workloads and the host machine.

## Product Goals

### Goal 1: Secure Doplet Creation

Users can create isolated doplets from templates or base images with explicit CPU, RAM, disk, network, and SSH settings.

### Goal 2: Resource-Aware Control Plane

Users can see total host resources, allocated resources, remaining resources, and per-doplet resource commitments while creating and managing doplets.

### Goal 3: Strong Post-Create Management

Users can start, stop, reboot, back up, restore, clone, resize, quarantine, and delete doplets from the same product.

### Goal 4: Hardened Host Experience

The host can be installed and configured as a secure doplet node with virtualization, networking, storage, and management components configured by default.

### Goal 5: Secure Anywhere Management

Users can manage hosts and doplets from a native desktop app or a credentialed web app without falling back to undocumented shell workflows.

## Not in Current Product Boundary

- Full multi-host cloud clustering
- Live migration between hosts
- Fine-grained pay-per-use billing
- Managed Kubernetes
- commercial billing / quota resale

## Product Scope

## In Scope

- single-host doplet platform
- Linux host node installation and hardening
- VM-based doplets
- host inventory and capacity planner
- image-based doplet creation
- SSH key injection and first-boot config
- doplet lifecycle management
- backup / restore / clone / snapshot
- multi-provider object-storage backup targets
- per-doplet network posture and firewall model
- audit log and task history
- remote-admin web app and authenticated API

## Outside Current Boundary

- Windows as the hypervisor host
- containers as equal-security doplets
- multi-node federation
- remote commercial marketplace
- commercial billing / quota resale

## User Journeys

### Journey A: Turn a Machine Into a Host Node

1. User installs VPSdash on a control machine or directly on the future host.
2. User chooses "Prepare this machine as a doplet host".
3. Product validates virtualization support, IOMMU support, storage targets, and OS suitability.
4. Product installs host dependencies and hardening baseline.
5. Product creates storage pool, virtual networking baseline, management services, and image cache.
6. User sees host capacity dashboard and can begin creating doplets.

### Journey B: Create a Doplet

1. User clicks `Create Doplet`.
2. User chooses image or template.
3. User sets doplet name, vCPU, RAM, disk, network, and SSH options.
4. Product shows total host resources, allocated resources, requested resources, and remaining headroom in real time.
5. Product blocks impossible allocations.
6. Product creates the doplet, injects keys, provisions first-boot config, and adds the doplet to managed inventory.

### Journey C: Operate a Doplet

1. User opens doplet detail page.
2. User sees status, uptime, health, resource usage, network posture, and backup history.
3. User can stop, reboot, snapshot, back up, restore, clone, or delete the doplet.
4. User can load console access when SSH is broken.

### Journey D: Respond to Suspected Compromise

1. User sees alert or suspicious behavior on a doplet.
2. User selects `Quarantine`.
3. Product cuts public ingress, optionally cuts egress, preserves audit trail, and captures a snapshot.
4. User can inspect console, clone for analysis, or destroy and restore.

### Journey E: Manage from the Web

1. User signs into the VPSdash web control plane with credentials.
2. User inspects host status, doplets, backups, tasks, and alerts.
3. User performs lifecycle actions allowed by their permissions.
4. User reviews audit history and security posture remotely.

## Functional Requirements

## Epic 1: Host Onboarding and Inventory

### User Stories

- As an operator, I can see whether the machine supports virtualization.
- As an operator, I can see total CPU, RAM, disk, and GPU resources.
- As an operator, I can see which resources are allocatable to doplets.
- As an operator, I can configure the host storage pool and network baseline.
- As an operator, I can see host hardening status before creating doplets.
- As an operator, I can choose between supported primary storage backends during host setup.

### Requirements

- Detect CPU topology and hardware virtualization support.
- Detect total RAM and safe allocatable RAM.
- Detect storage devices and free capacity.
- Detect ZFS suitability and LVM-thin suitability.
- Detect GPU inventory and passthrough eligibility where possible.
- Detect IOMMU presence and whether secure passthrough or mediated-device flows are possible.
- Persist host inventory snapshots.

### Acceptance Criteria

- Host dashboard must show total and remaining CPU, RAM, and disk before the first doplet is created.
- Unsupported hosts must show blockers before doplet creation is allowed.

## Epic 2: Doplet Templates, Images, and Flavors

### User Stories

- As an operator, I can create doplets from standard Linux images.
- As an operator, I can save common doplet sizes as reusable flavors.
- As an operator, I can save workload-specific templates.

### Requirements

- Support image catalog with Ubuntu, Debian, Fedora, Alpine, and custom import.
- Support flavor presets such as small, medium, large, and custom.
- Support workload templates that prefill network, disk, and bootstrap config.
- Support image refresh/update metadata.
- Support host defaults and doplet defaults editable from the product.

## Epic 3: Doplet Creation

### User Stories

- As an operator, I can choose a doplet name, OS image, CPU, RAM, disk, network mode, and SSH settings.
- As an operator, I can see the host resources remaining while I change the doplet spec.
- As an operator, I am prevented from over-allocating unavailable resources.
- As an operator, I can inject SSH keys and first-boot config.
- As an operator, I can choose GPU passthrough or mediated-device profiles when the host supports them.

### Requirements

- Real-time capacity display during creation.
- Allocation validator for CPU, RAM, disk, GPU, and device claims.
- First-boot user-data generation.
- Per-doplet root disk creation.
- Secure default network mode.
- Management record created immediately after provisioning.

### Acceptance Criteria

- A doplet cannot be created if requested resources exceed allocatable capacity.
- Remaining capacity updates live without requiring a manual refresh.

## Epic 4: Managed Doplet Inventory

### User Stories

- As an operator, each created doplet appears in a central inventory.
- As an operator, I can filter and search doplets.
- As an operator, I can load an existing dopletâ€™s details and actions quickly.

### Requirements

- Central doplet inventory page.
- Status indicators: running, stopped, degraded, provisioning, quarantined, failed.
- Tags and metadata.
- Per-doplet summaries of resource allocation, IPs, and recent backups.

## Epic 5: Doplet Lifecycle Management

### User Stories

- As an operator, I can start, stop, reboot, force stop, or destroy doplets.
- As an operator, I can open console access.
- As an operator, I can clone a doplet.
- As an operator, I can resize a doplet where supported.

### Requirements

- State transitions managed through a task system.
- Confirmation flows for destructive actions.
- Console access path for recovery scenarios.
- Clone and restore flows create new managed doplets.

## Epic 6: Backups, Snapshots, and Restore

### User Stories

- As an operator, I can create a backup on demand.
- As an operator, I can schedule backups.
- As an operator, I can restore a doplet from backup.
- As an operator, I can clone from snapshot or backup.
- As an operator, I can target local, network, and object-storage providers for backups.
- As an operator, I can spread backups across multiple configured providers.

### Requirements

- Backup metadata includes creation time, status, size if available, and source doplet.
- Snapshot and backup are modeled separately.
- Restore can target the same doplet or a new one.
- Retention policies supported in the current build.
- Backup targets support local, mounted/network, and object-storage-style destinations.
- Provider abstraction supports multiple destinations and quota-aware placement.

## Epic 7: Isolation and Security Controls

### User Stories

- As an operator, doplets are isolated from the host by default.
- As an operator, doplets are isolated from each other by default.
- As an operator, I can explicitly choose weaker features like bridged networking or device passthrough.
- As an operator, I can quarantine a doplet.

### Requirements

- VM isolation by default.
- No shared host folders by default.
- No privileged device passthrough by default.
- Firewall model separating host management traffic from doplet workload traffic.
- Quarantine action available in management UI.

## Epic 8: Networking

### User Stories

- As an operator, I can create a doplet with NAT networking.
- As an operator, I can optionally expose a doplet to the LAN.
- As an operator, I can define private networks for groups of doplets.
- As an operator, I can define per-doplet ingress posture.

### Requirements

- NAT default.
- Optional bridged network mode if host prerequisites are met.
- Private network support in the current build.
- Per-doplet firewall posture visible in UI.

## Epic 9: Capacity and Scheduling

### User Stories

- As an operator, I can see total host resources.
- As an operator, I can see current allocations and remaining capacity.
- As an operator, I can see how much a new doplet would consume before I create it.
- As an operator, I can avoid starved hosts by respecting safe reserve thresholds.

### Requirements

- Resource accounting engine tracks:
  - total
  - reserved for host
  - allocated to doplets
  - remaining allocatable
- Safe reserve defaults for RAM and disk.
- No silent overcommit in the current build.

## Epic 10: Monitoring and Audit

### User Stories

- As an operator, I can see host health and doplet health.
- As an operator, I can review actions taken by the control plane.
- As an operator, I can inspect failure output for provisioning or backups.
- As an operator, I can securely manage the platform from a remote web session.

### Requirements

- Host and doplet summary metrics.
- Task history.
- Audit/event log for create, delete, backup, restore, resize, and quarantine.
- Per-task logs retained locally.
- Authenticated remote web access with audit coverage.

## Resource Model

The UI must show:

- host total CPU threads
- host reserved CPU threads
- doplet allocated vCPU total
- host total RAM
- host reserved RAM
- doplet allocated RAM total
- host total disk pool size
- allocated disk
- free disk
- GPU inventory and allocation posture

The create flow must show:

- requested resources for current draft
- remaining resources if the draft is accepted
- warnings when remaining host reserve falls below safe thresholds

## Security Tiers

### Tier 1: Standard Secure Doplet

- VM isolation
- NAT networking
- no device passthrough
- no shared folders

### Tier 2: Network-Exposed Doplet

- same as Tier 1
- bridged or mapped ingress
- stronger warnings

### Tier 3: Privileged Hardware Doplet

- optional GPU passthrough or custom devices
- explicit warnings that host attack surface increases

## Success Metrics

- time to create first doplet on a prepared host
- number of successful doplet creates without shell intervention
- number of successful backups and restores
- resource accounting accuracy under repeated create/delete cycles
- zero unintentional over-allocation in supported flows

## Recommended Current Technical Baseline

The current baseline should be:

- single Linux host node
- VM-only doplets
- KVM/QEMU/libvirt
- desktop and web control planes
- CPU/RAM/disk/GPU resource accounting
- GPU inventory, passthrough, and mediated-device abstraction where host capabilities allow it
- NAT default, optional bridged networking
- private doplet networks
- manual and scheduled backups
- local, network, and object-storage backup targets
- snapshots, clone, restore
- multi-user remote admin with web and desktop access
- ZFS and LVM-thin storage backend support

## Explicitly Outside Current Boundary

- cluster scheduling
- Windows hypervisor host
- live migration


