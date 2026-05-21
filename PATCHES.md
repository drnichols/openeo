# PATCHES.md — Active Fork Patches

Upstream: https://github.com/minceheid/openeo
Fork:     https://github.com/drnichols/openeo

---

## Active patches

Patches are merged into `my-patches` in the order listed here. Maintain this order.

| # | Branch | Description | Status | Notes |
|---|---|---|---|---|
| — | _(none yet)_ | | | |

**Status values:**
- `local-only` — will never be proposed upstream
- `candidate` — may be proposed or submitted upstream as a PR
- `submitted` — PR open upstream (link in Notes)
- `absorbed` — merged upstream; remove from this table and drop the branch

---

## Rebuild command

To rebuild `my-patches` from the current active patch list:

```bash
git checkout my-patches
git reset --hard main
# merge each patch branch in the order listed above, e.g.:
# git merge patch/example-one
# git merge patch/example-two
git push origin my-patches --force-with-lease
```

---

## Sync upstream command

```bash
git checkout main
git pull upstream main
git push origin main
# then rebase each active patch branch:
# git checkout patch/<name> && git rebase main && git push origin patch/<name> --force-with-lease
```

---

## Workflow quick reference

| Action | Command |
|---|---|
| New patch | `git checkout main && git checkout -b patch/<name>` |
| Push patch | `git push origin patch/<name>` |
| Sync upstream | `git pull upstream main` into `main`, then rebase each patch branch |
| Rebuild integration | Reset `my-patches` to `main`, merge patches in table order |
| Drop absorbed patch | Omit from rebuild, delete branch locally and on origin, remove table row |
