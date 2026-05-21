#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
SCRIPT_PATH="$ROOT_DIR/scripts/fork-setup.sh"

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

assert_eq() {
  local expected="$1"
  local actual="$2"
  local message="$3"

  if [[ "$expected" != "$actual" ]]; then
    fail "$message: expected [$expected], got [$actual]"
  fi
}

assert_contains() {
  local haystack="$1"
  local needle="$2"
  local message="$3"

  if [[ "$haystack" != *"$needle"* ]]; then
    fail "$message: missing [$needle]"
  fi
}

tmpdir=$(mktemp -d)
trap 'rm -rf "$tmpdir"' EXIT

origin_repo="$tmpdir/origin.git"
upstream_repo="$tmpdir/upstream.git"
seed_repo="$tmpdir/seed"
work_repo="$tmpdir/work"

git init --bare "$origin_repo" >/dev/null
git init --bare "$upstream_repo" >/dev/null
git init "$seed_repo" >/dev/null

git -C "$origin_repo" symbolic-ref HEAD refs/heads/main
git -C "$upstream_repo" symbolic-ref HEAD refs/heads/main

git -C "$seed_repo" config user.name "Test User"
git -C "$seed_repo" config user.email "test@example.com"

cp "$SCRIPT_PATH" "$seed_repo/fork-setup.sh"
mkdir -p "$seed_repo/.codex"
cat > "$seed_repo/.codex/AGENTS.md" <<'EOF'
# Repo instructions
EOF
cat > "$seed_repo/CLAUDE.md" <<'EOF'
# Fork workflow
EOF
cat > "$seed_repo/PATCHES.md" <<'EOF'
# PATCHES.md — Active Fork Patches

Upstream: https://github.com/UPSTREAM_OWNER/UPSTREAM_REPO
Fork:     https://github.com/YOUR_USERNAME/YOUR_REPO
EOF

git -C "$seed_repo" add fork-setup.sh .codex/AGENTS.md CLAUDE.md PATCHES.md
git -C "$seed_repo" commit -m "seed" >/dev/null
git -C "$seed_repo" branch -M main
git -C "$seed_repo" remote add origin "$origin_repo"
git -C "$seed_repo" remote add upstream "$upstream_repo"
git -C "$seed_repo" push origin main >/dev/null
git -C "$seed_repo" push upstream main >/dev/null

git clone "$origin_repo" "$work_repo" >/dev/null
git -C "$work_repo" config user.name "Test User"
git -C "$work_repo" config user.email "test@example.com"

mkdir -p "$work_repo/scripts"
mv "$work_repo/fork-setup.sh" "$work_repo/scripts/fork-setup.sh"

git -C "$work_repo" add scripts/fork-setup.sh
git -C "$work_repo" commit -m "move script into scripts" >/dev/null
git -C "$work_repo" push origin main >/dev/null

(
  cd "$work_repo"
  UPSTREAM_URL="$upstream_repo" bash scripts/fork-setup.sh >/dev/null
)

current_branch=$(git -C "$work_repo" branch --show-current)
assert_eq "my-patches" "$current_branch" "setup should leave repo on my-patches"

tracking_branch=$(git -C "$work_repo" rev-parse --abbrev-ref --symbolic-full-name '@{upstream}')
assert_eq "origin/my-patches" "$tracking_branch" "setup should configure my-patches to track origin/my-patches"

git -C "$work_repo" remote set-url origin "https://token123@github.com/example/fork.git"

(
  cd "$work_repo"
  PUSH_TO_ORIGIN=0 UPSTREAM_URL="$upstream_repo" bash scripts/fork-setup.sh >/dev/null
)

upstream_remote=$(git -C "$work_repo" remote get-url upstream)
assert_eq "$upstream_repo" "$upstream_remote" "upstream remote should match provided URL"

patches_file=$(cat "$work_repo/PATCHES.md")
assert_contains "$patches_file" "Upstream: ${upstream_repo%.git}" "PATCHES.md should record upstream URL"
assert_contains "$patches_file" "Fork:     https://github.com/example/fork" "PATCHES.md should record sanitized fork URL"

tree_files=$(git -C "$work_repo" ls-tree --name-only -r HEAD)
assert_contains "$tree_files" ".codex/AGENTS.md" "my-patches commit should include .codex/AGENTS.md"
assert_contains "$tree_files" "CLAUDE.md" "my-patches commit should include CLAUDE.md"
assert_contains "$tree_files" "PATCHES.md" "my-patches commit should include PATCHES.md"
assert_contains "$tree_files" "scripts/fork-setup.sh" "my-patches commit should include setup script"

rerere_enabled=$(git -C "$work_repo" config --get rerere.enabled)
assert_eq "true" "$rerere_enabled" "setup should enable rerere"

echo "PASS: fork setup workflow"
