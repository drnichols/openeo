#!/usr/bin/env bash
set -euo pipefail

# ─────────────────────────────────────────────────────────────
# fork-setup.sh — one-time downstream fork initialisation
#
# Usage: bash scripts/fork-setup.sh
#
# Before running on a new fork, set UPSTREAM_URL below.
# This script is idempotent — safe to run more than once.
# ─────────────────────────────────────────────────────────────

DEFAULT_UPSTREAM_URL="https://github.com/minceheid/openeo.git"
UPSTREAM_URL="${UPSTREAM_URL:-$DEFAULT_UPSTREAM_URL}"
PUSH_TO_ORIGIN="${PUSH_TO_ORIGIN:-1}"

# ── Helpers ──────────────────────────────────────────────────

ok()   { echo "✓ $*"; }
info() { echo "  $*"; }
fail() { echo "ERROR: $*" >&2; exit 1; }

sanitize_git_url() {
  printf '%s\n' "$1" | sed -E 's#^(https?://)[^/@]+@#\1#; s#\.git$##'
}

branch_upstream_ref() {
  git for-each-ref --format='%(upstream:short)' "refs/heads/$1"
}

push_branch_if_needed() {
  local branch="$1"
  local remote_ref="refs/remotes/origin/$branch"
  local upstream_ref
  upstream_ref=$(branch_upstream_ref "$branch")

  if [ "$PUSH_TO_ORIGIN" != "1" ]; then
    info "Skipping push of $branch because PUSH_TO_ORIGIN=$PUSH_TO_ORIGIN"
    return 0
  fi

  if git show-ref --verify --quiet "$remote_ref"; then
    local local_sha
    local remote_sha
    local_sha=$(git rev-parse "$branch")
    remote_sha=$(git rev-parse "origin/$branch")
    if [ "$local_sha" = "$remote_sha" ]; then
      ok "$branch already matches origin/$branch"
      return 0
    fi
  fi

  if [ -z "$upstream_ref" ]; then
    if git push --set-upstream origin "$branch"; then
      ok "$branch pushed to origin and upstream configured"
    else
      info "Could not push $branch to origin automatically. Push it manually once credentials are available."
    fi
  else
    if git push origin "$branch"; then
      ok "$branch pushed to origin"
    else
      info "Could not push $branch to origin automatically. Push it manually once credentials are available."
    fi
  fi
}

# ── Pre-flight ───────────────────────────────────────────────

echo ""
echo "=== Fork setup starting ==="
echo ""

# Must be inside a git repo
git rev-parse --git-dir > /dev/null 2>&1 || fail "Not inside a git repository. Run from your cloned fork root."

# Warn if UPSTREAM_URL hasn't been updated
if echo "$UPSTREAM_URL" | grep -q "UPSTREAM_OWNER"; then
  fail "Edit UPSTREAM_URL in this script before running."
fi

ORIGIN_URL=$(git remote get-url origin 2>/dev/null || echo "(unknown)")
ORIGIN_DISPLAY_URL=$(sanitize_git_url "$ORIGIN_URL")

# ── 1. Upstream remote ───────────────────────────────────────

if git remote get-url upstream > /dev/null 2>&1; then
  ok "upstream remote already set: $(git remote get-url upstream)"
else
  git remote add upstream "$UPSTREAM_URL"
  ok "upstream remote added: $UPSTREAM_URL"
fi

# ── 2. Fetch upstream ────────────────────────────────────────

info "Fetching upstream..."
git fetch upstream
ok "upstream fetched"

# ── 3. Sync main with upstream ───────────────────────────────

git checkout main
git pull upstream main
push_branch_if_needed main
ok "main is synced with upstream"

# ── 4. Enable rerere ─────────────────────────────────────────

git config rerere.enabled true
ok "rerere enabled (conflict resolutions will be remembered)"

# ── 5. Create my-patches branch ──────────────────────────────

if git show-ref --verify --quiet refs/heads/my-patches; then
  ok "my-patches branch already exists"
  git checkout my-patches
else
  git checkout -b my-patches
  ok "my-patches branch created"
fi

# ── 6. Add fork-specific files to my-patches ─────────────────

CHANGED=0

# PATCHES.md
PATCHES_TEMPLATE=$(cat <<EOF
# PATCHES.md — Active Fork Patches

Upstream: $(sanitize_git_url "$UPSTREAM_URL")
Fork:     ${ORIGIN_DISPLAY_URL}

---

## Active patches

Patches are merged into \`my-patches\` in the order listed here. Maintain this order.

| # | Branch | Description | Status | Notes |
|---|---|---|---|---|
| — | _(none yet)_ | | | |

**Status values:**
- \`local-only\` — will never be proposed upstream
- \`candidate\` — may be proposed or submitted upstream as a PR
- \`submitted\` — PR open upstream (link in Notes)
- \`absorbed\` — merged upstream; remove from this table and drop the branch

---

## Rebuild command

\`\`\`bash
git checkout my-patches
git reset --hard main
# git merge patch/<name-1>
# git merge patch/<name-2>
git push origin my-patches --force-with-lease
\`\`\`

---

## Sync upstream command

\`\`\`bash
git checkout main
git pull upstream main
git push origin main
# git checkout patch/<name> && git rebase main && git push origin patch/<name> --force-with-lease
\`\`\`

---

## Workflow quick reference

| Action | Command |
|---|---|
| New patch | \`git checkout main && git checkout -b patch/<name>\` |
| Push patch | \`git push origin patch/<name>\` |
| Sync upstream | \`git pull upstream main\` into \`main\`, then rebase each patch branch |
| Rebuild integration | Reset \`my-patches\` to \`main\`, merge patches in table order |
| Drop absorbed patch | Omit from rebuild, delete branch locally and on origin, remove table row |
EOF
)

if [ ! -f PATCHES.md ]; then
  printf '%s\n' "$PATCHES_TEMPLATE" > PATCHES.md
  git add PATCHES.md
  CHANGED=1
  ok "PATCHES.md created"
else
  current_upstream=$(sed -n 's/^Upstream: //p' PATCHES.md | head -1)
  current_fork=$(sed -n 's/^Fork:     //p' PATCHES.md | head -1)
  target_upstream=$(sanitize_git_url "$UPSTREAM_URL")
  if [ "$current_upstream" != "$target_upstream" ] || [ "$current_fork" != "$ORIGIN_DISPLAY_URL" ]; then
    python3 - "$target_upstream" "$ORIGIN_DISPLAY_URL" <<'PY'
from pathlib import Path
import sys

patches = Path("PATCHES.md")
lines = patches.read_text().splitlines()
for index, line in enumerate(lines):
    if line.startswith("Upstream: "):
        lines[index] = f"Upstream: {sys.argv[1]}"
    elif line.startswith("Fork:     "):
        lines[index] = f"Fork:     {sys.argv[2]}"
patches.write_text("\n".join(lines) + "\n")
PY
    git add PATCHES.md
    CHANGED=1
    ok "PATCHES.md updated with fork metadata"
  else
    ok "PATCHES.md already exists"
  fi
fi

# CLAUDE.md
if [ ! -f CLAUDE.md ]; then
  info "CLAUDE.md not found — copy it from your fork template before committing"
else
  git add CLAUDE.md
  CHANGED=1
  ok "CLAUDE.md staged"
fi

# AGENTS.md
if [ -f AGENTS.md ]; then
  git add AGENTS.md
  CHANGED=1
  ok "AGENTS.md staged"
elif [ -f .codex/AGENTS.md ]; then
  git add .codex/AGENTS.md
  CHANGED=1
  ok ".codex/AGENTS.md staged"
else
  info "AGENTS.md not found — copy it from your fork template before committing"
fi

# scripts/fork-setup.sh itself
git add scripts/fork-setup.sh 2>/dev/null && CHANGED=1 || true

# Commit if anything changed
if [ "$CHANGED" -eq 1 ]; then
  git commit -m "chore: initialise fork workflow files on my-patches" || info "Nothing new to commit"
fi

# ── 7. Push my-patches ───────────────────────────────────────

push_branch_if_needed my-patches

# ── Summary ──────────────────────────────────────────────────

echo ""
echo "=== Setup complete ==="
echo ""
echo "Remotes:"
git remote -v
echo ""
echo "Branches:"
git branch -a | grep -E "(main|my-patches|patch/)" | head -20
echo ""
echo "Next steps:"
echo "  New patch:      git checkout main && git checkout -b patch/<descriptive-name>"
echo "  Push patch:     git push origin patch/<name>"
echo "  Rebuild:        git checkout my-patches && git reset --hard main && git merge patch/<name>"
echo "  Sync upstream:  git checkout main && git pull upstream main && git push origin main"
echo ""
