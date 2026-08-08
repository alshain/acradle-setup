# acradle-setup

Deliverables installed into [acradle](https://github.com/alshain/acradle)'s
disposable VMs — Claude Code skills, hooks, and scaffolding, packaged as the
**`acradle-vm` plugin** in a same-repo marketplace (**`acradle-setup`**).

## What's here

| Path | What |
| --- | --- |
| `.claude-plugin/marketplace.json` | Marketplace manifest; references the plugin by relative path |
| `plugins/acradle-vm/` | The plugin: skills + hooks installed into every VM |
| `plugins/acradle-vm/skills/pushing-artifacts-to-phone/` | Push build artifacts to the user's phone via [chute](https://github.com/alshains-prison/alshain-chute); `chute-push` script handles pairing + upload |
| `plugins/acradle-vm/skills/working-in-disposable-vms/` | Survival conventions for deletable machines; injected into every session (and subagent) by the plugin's hooks |
| `plugins/acradle-vm/hooks/` | SessionStart + SubagentStart injection of the survival rules |
| `docs/superpowers/specs/` | Design specs (source of truth for behavior) |
| `docs/superpowers/plans/` | Implementation plans |

## Installing into a VM

```bash
claude plugin marketplace add <path-or-url-of-this-repo>
claude plugin install acradle-vm@acradle-setup
```

Automation of this (cloud-init / setup-vm.sh wiring) is a planned
deliverable; see the specs' out-of-scope sections.

## Status

Implementation exists; the deferred test phases in each spec's §7 (script
harness against a local chuted, skill RED/GREEN pressure scenarios, in-VM
hook verification) are a **hard gate before the plugin lands in VM
images**.
