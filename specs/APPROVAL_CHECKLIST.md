# Approval Checklist

This is the short-form review sheet for starting the full single-node doplet-platform build. The detailed rationale lives in the PRD, architecture, threat model, decision register, and `PHASE_2MD.md`.

## Locked Decisions

- Linux hypervisor host only
- Windows allowed as a client only, with Linux-side execution routed through WSL where needed
- VM-only doplets
- KVM/QEMU/libvirt
- ZFS and LVM-thin storage support in the current build
- NAT, bridged, and private doplet networks
- GPU inventory, passthrough, and mediated-device abstraction in scope
- manual backups, scheduled backups, snapshots, clone, and restore
- local, mounted/network, and object-storage backup targets
- multi-provider backup strategy allowed
- multi-user remote admin in scope
- secure web app in scope
- mixed-use hosts allowed with strong warnings
- Ubuntu, Debian, Fedora, Alpine, and custom image import in scope
- full single-node platform in current scope, not an MVP subset

## Newly Locked Decisions

- overall product name: `VPSdash`
- user-facing compute unit name: `Doplet`
- per-user accounts with MFA and RBAC
- Parsec-style email MFA / new-device verification and alerting
- LAN/VPN-only default web exposure
- public internet exposure available as opt-in
- Postgres for production control-plane state
- S3-compatible adapter first for object storage
- Ubuntu Server LTS as the default host distro

## Spec Status

The platform-shape decisions needed for spec lock are resolved. The spec set is ready to convert into an implementation breakdown.

## If You Want My Default Call

If you want me to choose the boundary without more back-and-forth, the cleanest build target is:

- Linux hypervisor host only
- Windows control client allowed
- WSL-backed local execution on Windows clients
- VM-only doplets
- KVM/QEMU/libvirt
- ZFS and LVM-thin storage support
- no silent overcommit in the current build
- NAT default, bridge optional, private networks included
- GPU inventory + passthrough + mediated-device abstraction
- manual backup + scheduled backup + snapshot + clone + restore-as-new
- local + mounted/network + object-storage backup targets
- multi-user remote admin with web + desktop clients
- Ubuntu + Debian + Fedora + Alpine + custom image import
- dedicated host recommended
- mixed-use host allowed with strong warnings
- per-user accounts with MFA and RBAC
- email MFA / new-device verification and alerting
- LAN/VPN-only by default, public exposure opt-in
- Postgres for production state


