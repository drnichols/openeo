# CLAUDE.md — Fork Workflow

This repo is a downstream fork. All work follows the patch branch strategy below.
At the start of every session, read `PATCHES.md` to understand current patch state.

---

## First-time setup — no design phase required

If the user says "implement my patches workflow", "set up this fork", "initialise the fork",
or any similar phrase:

- This is NOT a feature request. Do not enter a design or brainstorming phase.
- The implementation already exists: `scripts/fork-setup.sh`
- Run it immediately: `bash scripts/fork-setup.sh`
- Report what was created or already present, then stop and wait for further instruction

Do not propose alternative implementations. The shell script is the canonical, complete
implementation of the setup workflow.

---

## Branch model

There are three branch types. Understand them precisely before doing any work.

| Branch | Purpose | Commit directly? |
|---|---|---|
| `main` | Always mirrors upstream exactly | Never |
| `patch/<name>` | One topic branch per logical change, cut from `main` | Yes — all work goes here |
| `my-patches` | Integration branch; rebuilt by merging active `patch/*` branches onto `main` | Never — always rebuilt, never committed to directly |

`my-patches` is a real persistent Git branch. It is not generated from scratch — it is reset
to `main` and rebuilt by merging the active patch branches in the order listed in `PATCHES.md`.

`PATCHES.md` is the source of truth for which patches are active and in what merge order.

---

## Every session — before starting any work

1. Run `git branch --show-current`
   - If on `main` or `my-patches`, stop — ask the user which patch branch to use or create
2. Read `PATCHES.md` — check active patches and their current status
3. Run `git fetch upstream && git log main..upstream/main --oneline`
   - If upstream has moved, tell the user before doing anything else
   - Do not start new work until the user has decided whether to sync first

---

## Patch lifecycle

### New patch
```bash
git checkout main
git checkout -b patch/<descriptive-name>
# make changes, commit
git push origin patch/<descriptive-name>
```
Then add a row to `PATCHES.md`.

### Rebuild my-patches
Run after adding a new patch or after syncing upstream.
```bash
git checkout my-patches
git reset --hard main
# merge each active branch in the order listed in PATCHES.md
git merge patch/<name-1>
git merge patch/<name-2>
# etc.
git push origin my-patches --force-with-lease
```

### Sync upstream
Run before any new patch work if upstream may have moved.
```bash
git checkout main
git pull upstream main
git push origin main
# rebase each active patch branch onto the updated main
git checkout patch/<name>
git rebase main
git push origin patch/<name> --force-with-lease
```
Then rebuild `my-patches`.

### Drop an absorbed patch
When upstream has merged one of your patches:
```bash
git checkout my-patches
git reset --hard main
# rebuild WITHOUT the absorbed patch branch
git merge patch/<remaining-name-1>
git merge patch/<remaining-name-2>
git push origin my-patches --force-with-lease
# clean up the branch
git branch -d patch/<absorbed-name>
git push origin --delete patch/<absorbed-name>
```
Remove its row from `PATCHES.md` and commit the change.

---

## Conflict handling — authority boundary

- If a conflict occurs during rebase or merge, stop immediately
- Do not attempt to resolve conflicts autonomously
- Show the user the conflicting files and wait for explicit instruction
- If a patch branch rebases cleanly with no diff against main, flag this to the user as a
  possible sign that upstream has absorbed it — do not silently drop the branch
- The user decides when a patch is removed, not the AI

---

## Sustainability rules

- One logical change per `patch/*` branch — keep patches small and focused
- Update `PATCHES.md` immediately when creating, rebuilding, or dropping any patch
- Push all patch branches to origin after every commit: `git push origin patch/<name>`
- `rerere` is enabled by `fork-setup.sh` — it remembers conflict resolutions across rebuilds
- `CLAUDE.md`, `AGENTS.md`, `PATCHES.md`, and `scripts/fork-setup.sh` live on `my-patches`
  only — do not push them to `main` or propose upstreaming them
