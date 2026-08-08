---
name: writing-shell-scripts
description: Use when writing, editing, or reviewing a shell or bash script — any *.sh file, shebang script, or sourced shell fragment — before treating the script as done, verified, or ready to commit.
---

# Writing Shell Scripts (the shellcheck gate)

A shell script you create or edit is not done until `shellcheck` on it
exits 0. Run it after writing and after every edit — at latest at the
commit points the VM survival rules already require.

```bash
shellcheck path/to/script.sh   # default severity; style findings count
```

- Scripts you **create** pass clean.
- Scripts you **edit**: your change adds no new findings. Fix
  pre-existing findings when the fix is safe and small; a file riddled
  with them gets reported (with SC numbers), not silently churned.
- Counts as a script: `*.sh` / `*.bash`, anything with an sh/bash
  shebang, sourced shell fragments. Inline `bash -c` strings and
  snippets embedded in other files do not.

## No shellcheck on this VM

Fresh VMs lack it. Installing it is a machine change, so it goes through
`scripts/setup-vm.sh` (see working-in-disposable-vms; copy the template
from that skill's directory if the repo has no script yet):

```bash
step "shellcheck installed" 'command -v shellcheck >/dev/null' 'sudo apt-get install -y shellcheck'
```

Run the step, then lint. "Not installed" never excuses skipping the gate.

## Findings

Fix by default. A finding that genuinely does not apply gets the
narrowest possible directive, reason beside it:

```bash
# shellcheck disable=SC2086  # $FLAGS is a deliberate word-split list
run_tool $FLAGS
```

Never: file-wide disables, command-line excludes (`-e` doesn't travel
with the file), or lowering severity until output is empty.

## Rationalizations — captured from baseline runs

| Excuse | Reality |
|---|---|
| "Exercised end-to-end with observed output" | Runs prove one path on one input set. shellcheck checks the paths you didn't run. |
| "`bash -n` passed" | A parse check. It accepts every quoting, splitting, and glob bug shellcheck exists to catch. |
| "Those findings are pre-existing / out of scope" | Maybe — decided after running shellcheck and said with SC numbers in your report, not guessed. |
| "It's a three-line script" | Three-line scripts have quoting bugs too. The check costs a second. |
| "shellcheck isn't installed" | Installing it is one setup-vm.sh step. Do that, then lint. |
