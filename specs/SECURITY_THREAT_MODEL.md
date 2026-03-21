# Security and Threat Model

## Security Goal

Assume a doplet may be compromised. The platform should minimize the chance that a compromised doplet can:

- escape into the host
- access other doplets
- access secrets held by the control plane
- tamper with management records or backups

This is a risk-reduction goal, not an absolute guarantee.

## Security Position

To meet the stated product intent, doplets must be VMs by default. Containers alone are not a sufficient answer for "treat doplet compromise as hostile and keep the host safe."

## Trust Boundaries

### Boundary 1: User Interface / API vs Host Agent

The desktop UI and web/API layer should not run privileged host mutations directly. Privileged operations should be handled by a narrow host agent or privileged worker.

### Boundary 2: Host vs Doplet

The host is trusted infrastructure. Doplets are untrusted workloads.

### Boundary 3: Doplet vs Doplet

Each doplet is mutually untrusted unless explicitly grouped on a private network with allowed communication.

### Boundary 4: Management Plane vs Backup Artifacts

Backup artifacts must not be writable by doplets.

### Boundary 5: Remote Web Sessions vs Control Plane

Remote users must authenticate through a hardened web/API layer. Web credentials and sessions are not equivalent to direct host trust.

## Threat Actors

- remote attacker compromising an exposed doplet
- malware inside a guest VM
- user error during resource, network, or passthrough configuration
- malicious or broken guest workload
- attacker with LAN access to bridged doplets
- attacker targeting the remote web admin plane
- attacker abusing backup-provider credentials or object targets

## Primary Threats

### T1: Guest-to-Host Escape

Risk:

- hypervisor escape
- abuse of shared folders
- abuse of passed-through devices
- abuse of host-exposed agent sockets

Mitigations:

- VM-first architecture
- no shared folders by default
- no host socket mounts
- no privileged passthrough by default
- minimal host services
- patched host and virtualization stack

### T2: Lateral Movement Between Doplets

Risk:

- flat shared network
- permissive bridge setup
- shared management credentials

Mitigations:

- NAT default
- isolate doplets by default
- explicit private network creation only
- per-doplet firewall posture
- no shared credentials by default

### T3: Host Secret Exposure

Risk:

- control-plane secrets readable by doplets
- SSH keys exposed to guests
- backup credentials exposed to workloads

Mitigations:

- secrets stored only on host control plane
- no guest access to host secret store
- SSH public key injection only unless explicitly needed otherwise
- redact secrets in logs and UI

### T4: Dangerous Hardware Features

Risk:

- GPU passthrough
- USB passthrough
- PCI device passthrough

Mitigations:

- disabled by default
- explicit warning tiers
- require IOMMU validation
- display which doplets use privileged features

### T5: Backup Tampering or Ransomware Blast Radius

Risk:

- compromised doplet tampering with on-host backup files
- uncontrolled backup mount exposure

Mitigations:

- backups stored outside guest filesystem
- doplets never mount backup destinations directly
- immutable or append-only backup strategy where provider capabilities allow it
- separate backup metadata and retention logic

### T6: Remote Admin Account or Session Compromise

Risk:

- stolen credentials
- weak shared admin passwords
- exposed sessions
- insufficient authorization boundaries

Mitigations:

- per-user accounts
- MFA for privileged actions
- email verification / alerting for new devices
- trusted-device tracking with suspicious-login review
- secure session cookies and CSRF protection
- rate limiting and lockout rules
- RBAC with least privilege
- full audit trail for login and management actions

### T7: Object Storage Provider Abuse or Credential Leakage

Risk:

- backup credentials exposed from the control plane
- provider policy drift or quota exhaustion
- attacker deletes or corrupts off-host backups

Mitigations:

- encrypt provider credentials at rest
- keep provider credentials only on the host control plane
- support multi-target backup policy instead of a single fragile target
- surface provider health and quota state in the UI
- prefer immutable / versioned targets where available

## Host Hardening Baseline

Current host baseline should include:

- minimal Linux install
- only required management services enabled
- automatic security updates or clear patch posture
- firewall enabled
- SSH hardened
- AppArmor or SELinux enabled if practical
- no general shared user workspace on the host node
- audit logging enabled for management actions
- hardened reverse proxy / TLS posture if web admin is enabled

## Doplet Security Tiers

### Standard

- NAT networking
- no shared folders
- no passthrough devices
- no host shell exposure

### Network Exposed

- bridged networking or public ingress
- additional warning
- stronger firewall validation

### Privileged Hardware

- GPU or special device passthrough
- highest warning level
- not recommended for hostile multi-tenant workloads

## Management Plane Security Requirements

- all management actions are authenticated through the local or remote identity layer
- remote web actions are authenticated through a hardened identity layer
- dangerous actions require confirmation
- task logs and audit logs are immutable enough for debugging
- control-plane commands are not exposed directly to doplets
- least-privilege service account where possible

## Network Security Requirements

- separate host management network conceptually from guest workload traffic
- NAT as default
- bridged networking opt-in only
- clear ingress exposure UI
- quarantine mode to rapidly restrict a doplet

## Storage Security Requirements

- per-doplet disk images
- no writable host bind mounts by default
- secure deletion flow for doplet records and disks
- backups and snapshots outside guest control
- object-storage targets accessed only through the control plane

## Incident Response Requirements

The platform should support:

- quarantine doplet
- capture snapshot
- clone for analysis
- console access
- controlled delete and rebuild

## Explicit Limits

- A VM boundary reduces risk but does not eliminate hypervisor escape risk.
- Full GPU passthrough weakens the security model compared with pure virtual devices.
- Mediated-device / vGPU paths also depend heavily on vendor and driver trust boundaries.
- Bridged networking increases exposure.
- A host used for unrelated desktop activity is weaker than a dedicated host node.

## Recommended Security Stance for the Current Build

- Linux-only dedicated host
- VM-only doplets
- no shared folders
- passthrough and mediated-device features disabled by default and heavily warned
- NAT default
- mixed-use hosts allowed only with strong warnings
- per-user remote admin with MFA, RBAC, and new-device verification recommended


