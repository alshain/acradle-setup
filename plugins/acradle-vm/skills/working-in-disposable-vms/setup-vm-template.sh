#!/usr/bin/env bash
# setup-vm.sh — idempotent VM provisioning and the machine's change log.
#
# Contract (authoritative — the working-in-disposable-vms skill points here):
#   * Running this twice in a row produces the same machine and no errors
#     the second time.
#   * step "name" '<test-cmd>' '<apply-cmd>': args are shell strings run
#     via eval. test-cmd exits 0 iff the step is already satisfied;
#     apply-cmd makes it so. State-test before act, always. Multi-line or
#     quoting-heavy logic goes in a function defined above the steps and
#     passed by name (a function name is a command).
#   * --check evaluates EVERY step (no fail-fast), prints ok:/MISSING: per
#     step, mutates nothing, and exits 0 iff all steps are satisfied.
#   * Steps stay ordered so a bare VM runs the file top-to-bottom.
#   * Every command that changes machine state outliving a session belongs
#     here — installs, config edits, daemons — sudo or not.
set -euo pipefail

CHECK=false
[[ "${1:-}" == "--check" ]] && CHECK=true
MISSING=false

step() {
  local name="$1" test_cmd="$2" apply_cmd="$3"
  if eval "$test_cmd" >/dev/null 2>&1; then
    echo "ok:      $name"
  elif $CHECK; then
    echo "MISSING: $name"
    MISSING=true
  else
    echo "apply:   $name"
    eval "$apply_cmd"
  fi
}

# ── steps — ordered; a bare VM runs top-to-bottom ──────────────────────
# Example (a real step from the chute VM — uncomment to use):
# step "jq installed" 'command -v jq' 'sudo apt-get install -y jq'

if $CHECK && $MISSING; then
  exit 1
fi
