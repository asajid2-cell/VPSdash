# Roadmap

This roadmap is sequencing, not deferral.

The single-node doplet platform is being designed as a full product now. The phases below describe implementation order and dependency management, not "maybe later" feature buckets for core platform capabilities.

## Phase 0: Spec Lock

Deliverables:

- product requirements approved
- architecture approved
- security stance approved
- decision register resolved enough to begin implementation
- full-scope `Phase 2MD` accepted as the current build target

Exit criteria:

- host OS, hypervisor, storage, and networking decisions approved

## Phase 1: Host Foundation

Scope:

- host inventory
- virtualization checks
- ZFS and LVM-thin storage setup
- image catalog
- host hardening baseline
- host dashboard
- web/API foundation

Deliverables:

- detect CPU, RAM, disk, GPU inventory
- detect virtualization and IOMMU status
- install/configure libvirt stack
- create first production storage backend
- create NAT network baseline
- prepare bridge/private network prerequisites
- stand up authenticated API and web shell

## Phase 2: Core Doplet Platform

Scope:

- doplet flavors
- image-based create
- SSH key injection
- inventory and status
- lifecycle controls
- private networking
- scheduled backups
- restore and clone
- audit/task history
- quarantine
- web lifecycle parity

Deliverables:

- create doplets from supported images and templates
- show them in managed inventory
- start/stop/reboot/delete/clone/restore them
- console recovery path
- backup and schedule policies
- private network assignment
- audit coverage for major actions
- core web control-plane management flows

## Phase 3: Advanced Resource and Device Management

Scope:

- remaining capacity calculations
- allocation validator
- host reserve logic
- live create-form feedback
- device inventory
- GPU passthrough support
- mediated-device / vGPU abstraction

Deliverables:

- no-overcommit validation
- real-time remaining resource display
- device conflict detection
- passthrough assignment rules
- mediated-device profile exposure rules

## Phase 4: Operations Hardening

Scope:

- hardened warning tiers
- security posture page
- richer monitoring
- failure recovery improvements
- multi-provider object-storage backup fabric
- remote-admin hardening

Deliverables:

- security posture page
- host and doplet alerting
- backup detail and restore inventory
- remote backup target support
- hardened remote-admin posture

## Phase 5: UX Completion and Packaging

Scope:

- installer polish
- native packaging
- secure web packaging / deployment
- default/template editor
- management workflows refined for the full platform

Deliverables:

- polished host setup flow
- polished doplet create/manage/recover flows
- production packaging for control client
- production packaging for secure web control plane

## Suggested First Build Slice

If implementation starts immediately, the best first slice is:

1. Linux host inventory and validation
2. libvirt integration
3. image catalog
4. create one doplet from Ubuntu cloud image
5. show doplet inventory and lifecycle actions

That remains the dependency-correct first slice, but it is no longer the definition of the product. The current product target is the full single-node doplet platform described in `PHASE_2MD.md`.


