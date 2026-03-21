# System Architecture

## Summary

Recommended architecture is a single-node Linux hypervisor host managed by a hybrid control plane. Doplets are full virtual machines created and managed through `KVM + QEMU + libvirt`.

The user-facing control surfaces are:

- a native desktop client
- a secure web app
- an authenticated API layer

The actual doplet runtime remains a dedicated host stack rather than ad hoc Docker-only setup.

## Architectural Principles

- Use VM boundaries for doplets.
- Treat the host as infrastructure, not as a general shared app runtime.
- Keep management-plane responsibilities separate from guest workloads.
- Use explicit resource accounting and deny unsafe allocations by default.
- Keep storage, networking, and compute abstractions first-class in the data model.

## Recommended Deployment Model

### Host Node

- Linux only
- Ubuntu Server LTS or Debian stable recommended
- hardware virtualization enabled
- IOMMU enabled where available
- KVM/QEMU/libvirt installed
- ZFS-backed or LVM-thin-backed primary storage
- management agent running locally

### Control Plane

- native desktop UI
- secure web UI
- authenticated API service
- local privileged worker / host agent
- production state database
- task runner for long operations

### Guest Runtime

- libvirt-managed VMs
- cloud-image or template-backed root disks
- cloud-init or equivalent first-boot configuration

## Major Components

## 1. Desktop UI

Responsibilities:

- host onboarding flow
- resource dashboard
- doplet creation wizard
- doplet inventory and lifecycle actions
- backup and restore views
- security posture and warnings
- task and audit log views

## 1b. Web UI

Responsibilities:

- authenticated remote access
- inventory and lifecycle parity for core operations
- backup and alert views
- audit and task visibility
- administrative configuration

## 2. Core App Service Layer

Responsibilities:

- state orchestration
- validation
- plan generation for host and doplet operations
- task submission
- read models for UI

## 2b. Web/API Service

Responsibilities:

- authenticate remote users
- enforce authorization rules
- manage MFA and step-up verification flows
- detect new devices and emit verification / alert events
- expose doplet, host, backup, and task APIs
- terminate web sessions and API tokens
- publish audit events for remote actions

## 3. Host Agent

Responsibilities:

- gather host inventory
- call libvirt APIs or CLI
- manage storage pools
- manage networks
- create and destroy doplets
- trigger backup and restore jobs
- return task progress and results

Host agent should run locally on the host and expose a narrow command surface to the app.

## 3b. Backup Fabric

Responsibilities:

- manage backup manifests
- stage backup payloads
- send payloads to local, mounted/network, and object targets
- coordinate multi-target placement policies
- surface provider health, quota, and failures

## 4. State Store

Recommended production setup:

- Postgres

Local dev / embedded mode:

- SQLite acceptable

Stores:

- host metadata
- images
- doplet instances
- volumes
- snapshots
- backups
- networks
- SSH keys
- flavors
- tasks
- audit events
- users
- roles
- sessions
- API clients
- object-storage providers

## 5. Task Runner

Needed because these are long operations:

- image download
- VM creation
- snapshot
- backup
- restore
- clone
- resize
- delete with secure cleanup

Each task needs:

- id
- type
- target object id
- status
- created_at
- started_at
- finished_at
- progress
- stdout/stderr or structured log

## Runtime Stack Recommendation

## Hypervisor

Recommended:

- `libvirt`
- `qemu-kvm`
- `virt-install` for some provisioning paths if useful

Why:

- mature tooling
- good Linux support
- console access
- snapshots
- bridges and NAT
- storage pools
- passthrough and mediated-device support paths

## Storage

Recommended current build:

- ZFS-backed storage pools
- LVM-thin-backed storage pools
- host chooses one primary backend at setup time

Why:

- provides real snapshot/clone semantics now
- aligns with the full-scope storage commitment
- keeps storage behavior closer to a real doplet platform than a simple qcow2 baseline

## Networking

Recommended current build:

- libvirt NAT network as default
- optional Linux bridge when explicitly enabled
- private isolated virtual networks between doplets
- security groups / firewall policies
- ingress exposure models surfaced in the UI

## Data Model

## HostNode

Fields:

- id
- hostname
- os
- virtualization_supported
- iommu_supported
- total_cpu_threads
- reserved_cpu_threads
- total_ram_mb
- reserved_ram_mb
- total_disk_bytes
- available_disk_bytes
- gpu_inventory
- storage_pools
- networks
- hardened_status
- created_at
- updated_at

## Image

Fields:

- id
- name
- distro
- version
- source_url
- local_path
- checksum
- size_bytes
- status

## Flavor

Fields:

- id
- name
- vcpu
- ram_mb
- disk_gb
- gpu_mode
- gpu_profile_id

## DopletInstance

Fields:

- id
- name
- status
- image_id
- flavor_id or explicit resource spec
- vcpu
- ram_mb
- disk_gb
- host_node_id
- network_ids
- ip_addresses
- root_disk_path
- storage_backend
- attached_volume_ids
- ssh_authorized_keys
- security_tier
- gpu_assignments
- created_at
- updated_at

## Volume

Fields:

- id
- name
- size_bytes
- backend
- attached_instance_id
- mount_hint

## Snapshot

Fields:

- id
- doplet_id
- created_at
- size_bytes
- status
- backend_reference

## Backup

Fields:

- id
- doplet_id
- created_at
- size_bytes
- destination
- artifact_reference
- status
- provider_id
- manifest_path
- encryption_profile

## ObjectStorageProvider

Fields:

- id
- name
- provider_type
- endpoint
- bucket
- region
- credential_ref
- quota_model
- enabled
- policy_notes

## Network

Fields:

- id
- name
- mode
- cidr
- bridge_name
- nat_enabled
- doplet_ids

## AuditEvent

Fields:

- id
- actor
- action
- target_type
- target_id
- created_at
- summary
- details

## UserAccount

Fields:

- id
- email_or_username
- role
- mfa_enabled
- mfa_method
- status
- trusted_device_state
- last_new_device_alert_at
- created_at
- updated_at

## Resource Accounting Model

The scheduler must track:

- host total CPU threads
- host reserved CPU threads
- doplet allocated vCPUs
- host total RAM
- host reserved RAM
- doplet allocated RAM
- storage pool total bytes
- storage pool allocated bytes
- storage pool free bytes
- GPU devices and allocation states
- mediated-device profile capacity where supported

Current policy:

- no silent overcommit
- configurable but safe host reserve
- allocation denied if remaining safe headroom becomes negative

## Allocation Flow

1. Load current host inventory.
2. Load all active doplet allocations.
3. Compute reserved host baseline.
4. Compute remaining allocatable capacity.
5. Apply draft doplet request.
6. If any dimension exceeds capacity, block submission.
7. Persist draft as a task request only after validation passes.

## Doplet Creation Flow

1. User chooses image and flavor or custom spec.
2. Product validates capacity.
3. Product creates root disk from image/template.
4. Product generates cloud-init or first-boot config.
5. Product creates VM definition in libvirt.
6. Product attaches disks and NICs.
7. Product boots VM.
8. Product records doplet in inventory.
9. Product collects first known IP and status.

## Doplet Delete Flow

1. User confirms delete.
2. Product stops guest if needed.
3. Product undefines VM.
4. Product detaches and optionally destroys volumes.
5. Product updates allocation ledger.
6. Product retains or deletes snapshots/backups depending on user choice.
7. Product records audit event.

## Backup Flow

Recommended current build:

- host-driven backup of doplet disks, snapshots, or backend-native artifacts depending on backend
- provider abstraction for local, mounted/network, and object-storage targets
- manifest-based backup records so a restore can reconstruct source, destination, backend, and integrity metadata

Baseline flow:

1. Freeze or stop guest depending on implementation level.
2. Create snapshot or copy disk artifact.
3. Package and encrypt backup payload as required by policy.
4. Store to one or more configured targets based on placement policy.
5. Store backup metadata and manifest.
6. Mark task result and artifact path(s).

## Restore Flow

Two supported restore modes:

- restore in place
- restore as new doplet

Restore as new doplet is safer and should be the default recommendation.

## Console and Access

Current build needs:

- SSH after boot and initialization
- console access through libvirt tooling or integrated viewer

Console is mandatory for recovery if network or cloud-init fails.

## GPU Handling

Recommended current build:

- detect GPUs
- show inventory and eligibility
- support full-device passthrough for supported hardware
- support a vendor-agnostic mediated-device / vGPU abstraction layer
- expose only host-supported profiles and capacities

Do not promise universal fractional GPU allocation. The abstraction must be capability-driven and only surface profiles the host can actually provide.

## Cross-Platform Position

### Linux

Recommended host platform.

### Windows

Recommended role:

- desktop control client only, not hypervisor host
- local Linux-side execution routed through WSL where applicable

Reason:

- strong Linux virtualization and security posture are materially better for this product.

### macOS

Out of scope for host operation.

## Observability

Host metrics:

- CPU
- RAM
- disk
- network
- temperatures if available
- GPU inventory and status

Doplet metrics:

- state
- allocated resources
- basic usage if agentless measurement is possible from host

Task metrics:

- provisioning duration
- backup duration
- restore duration
- failure counts

## Recovery and Resilience

- all long operations are task-based
- idempotent retries where safe
- pre-destructive confirmations
- state transition audit log
- partial-failure handling for create and delete

## Recommended Technology Choices

- Host OS: Ubuntu Server LTS
- Virtualization: KVM/QEMU/libvirt
- State DB: Postgres for production, SQLite for local dev/test
- Web/API layer: secure authenticated service in front of host agent
- App/agent language can remain Python initially for leverage
- Later control-plane rewrite is optional, not required before proving the system model


