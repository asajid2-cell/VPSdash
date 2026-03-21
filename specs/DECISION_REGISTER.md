# Decision Register

Status values:

- `approved`: explicitly decided
- `proposed`: recommended but not yet approved
- `needs-user-approval`: I need your call
- `rejected`: explicitly not part of the current product boundary

## D-001 Host Operating System

- Status: `approved`
- Decision: Linux hypervisor host only
- Preferred choice: Ubuntu Server LTS is the default host distro
- Supported alternative: Debian stable can remain supported
- Why: strongest path for KVM/libvirt, security hardening, storage, networking, and device control without fighting platform limitations
- Alternative: Windows host
- Problem with alternative: materially worse path for a secure local cloud product

## D-002 Doplet Runtime Type

- Status: `approved`
- Decision: VM-only doplets
- Why: aligns with your isolation requirement
- Alternative: support both VMs and containers in the current build
- Problem with alternative: adds confusion and weakens the security story

## D-003 Hypervisor Stack

- Status: `approved`
- Decision: KVM/QEMU/libvirt
- Why: mature Linux stack with console, networking, storage pools, snapshots, and device passthrough support

## D-004 Control Plane Shape

- Status: `approved`
- Decision: hybrid control plane
  - native desktop client
  - secure web app
  - authenticated API service
  - local privileged host agent on the Linux hypervisor host
- Why: desktop remains useful for local management, while remote admin is now part of the product boundary

## D-005 Supported Host Modes

- Status: `approved`
- Decision:
  - Linux dedicated host: yes
  - Windows management client: yes, routed through WSL for Linux-side operations
  - Windows hypervisor host: no
  - remote web admin client: yes
- Why: keeps the virtualization host secure and Linux-native while still allowing Windows access paths

## D-006 Storage Backend

- Status: `approved`
- Decision:
  - support ZFS-backed storage in the current build
  - support LVM-thin-backed storage in the current build
  - let each host choose one primary storage backend at setup time
- Why: you explicitly want real storage backends now instead of a simplified qcow2-first path
- Rejected default: qcow2-only baseline as the main architecture

## D-007 Resource Allocation Policy

- Status: `approved`
- Decision: no silent overcommit in the current build
- Why: easier to reason about and safer for a first secure-host release

## D-008 Networking Model

- Status: `approved`
- Decision:
  - NAT default: yes
  - optional bridged networking: yes
  - private multi-doplet networks: yes
- Why: private network isolation is part of the current single-node platform, not a later add-on

## D-009 GPU Scope

- Status: `approved`
- Decision:
  - GPU inventory and capability detection: yes
  - whole-device passthrough: yes
  - vendor-agnostic vGPU / mediated-device abstraction layer: yes
  - actual mediated-device exposure only when the host, driver, and hardware support it: yes
- Why: the product should model real GPU resources now, but it still must remain capability-driven and technically honest

## D-010 Backup Scope

- Status: `approved`
- Decision:
  - manual backups: yes
  - snapshots: yes
  - restore as new doplet: yes
  - scheduled backups: yes
  - local backup targets: yes
  - mounted/network backup targets: yes
  - object-storage-style targets: yes
  - multiple providers in tandem: yes
  - free-tier-aware provider strategy: yes, within provider limits and policies
- Why: backup automation and target management are part of the core management promise, and you want multi-provider no-cost paths designed in now

## D-011 Access Model

- Status: `approved`
- Decision:
  - remote admin in scope
  - secure web app in scope
  - multi-user credentialed access in scope
  - desktop client remains supported
- Why: remote access is now part of the product boundary, not a future extension

## D-012 Image Catalog Scope

- Status: `approved`
- Decision:
  - Ubuntu LTS cloud image: yes
  - Debian stable cloud image: yes
  - custom image import: yes
  - Fedora and Alpine: yes

## D-013 Host Use Model

- Status: `approved`
- Decision: dedicated host strongly recommended, mixed-use host allowed only with strong warnings
- Why: security posture is better on a dedicated node, but user choice remains allowed

## D-014 Product and Compute Unit Naming

- Status: `approved`
- Decision:
  - overall product name: `VPSdash`
  - user-facing compute unit name: `Doplet`
  - engineering data models may still use `instance` where it improves clarity, but product-facing UX should say `Doplet`
- Why: ties the platform identity back to the original Harmonizer project while giving the managed compute unit a distinct product name

## D-015 Current Program Shape

- Status: `approved`
- Decision: build the full single-node platform under one current program, sequencing foundations first but not deferring core management capabilities out of scope
- Why: matches the product direction you asked for and avoids false "later" placeholders

## D-016 Web Admin Security Model

- Status: `approved`
- Decision:
  - per-user accounts: yes
  - MFA: yes
  - Parsec-style verification posture: yes
  - email MFA / new-device verification and alerting: yes
  - step-up verification for sensitive actions: yes
  - RBAC roles: owner/admin/operator/viewer
  - no shared admin password as the primary security model
- Why: once the web app is in scope, identity and authorization are no longer optional details

## D-017 Web Exposure Model

- Status: `approved`
- Decision:
  - HTTPS only
  - reverse proxy in front of the web API
  - hardened session cookies and CSRF protection
  - rate limiting and lockout rules
  - configurable exposure mode:
    - LAN/VPN only by default
    - public internet available as an opt-in mode with stronger warnings
- Why: "remote admin from the web" changes the threat surface materially, so default exposure must stay conservative

## D-018 State and Control-Plane Database

- Status: `approved`
- Decision: Postgres for the authenticated web/API control plane, with SQLite acceptable only for local dev or embedded test mode
- Why: multi-user web access, task coordination, audit history, and remote sessions push the product beyond a SQLite-first architecture

## D-019 Object Storage Provider Strategy

- Status: `approved`
- Decision:
  - build an object-target abstraction with provider adapters
  - support S3-compatible targets first
  - add quota-aware placement and multi-target striping / replication rules
  - prioritize no-cost / free-tier-capable providers within their published limits and terms
  - keep provider selection configurable because free tiers and policies can change
- Why: your backup target strategy depends on flexibility, not on a single provider

## Spec Lock Status

Blocking platform-shape decisions are approved.
Naming decisions are also approved.


