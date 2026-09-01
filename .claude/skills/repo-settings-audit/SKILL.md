---
name: repo-settings-audit
description: Reconcile the live GitHub repository settings against what CONTRIBUTING.md claims — branch protection, the pypi environment's tag policy, the trusted publisher, and the deliberately-disabled code-owner review. Use when a release is rejected or before relying on documented settings.
---

# Auditing repository settings

Some of what makes refaudit's release work lives in GitHub's settings rather
than in the repo, so `CONTRIBUTING.md` records it. Recorded settings drift.
This checks the record against reality.

Everything here is read-only unless you decide to change something. Ask before
writing — these settings govern who can merge and who can publish.

`.github/scripts/configure-repo.sh` exists to apply the intended state; read it
first, since it is the authoritative description of what is supposed to be true.

## 1. Branch protection on `main`

Intended: pull request with one approving review, all four CI checks green,
linear history, no force-push, no deletion. Admins can bypass, deliberately.

```bash
gh api repos/uw-share-lab/refaudit/branches/main/protection
```

Check `required_pull_request_reviews.required_approving_review_count` is 1, that
`required_status_checks.contexts` lists all four (pytest × 3.10–3.13 as one
check set, ruff, mypy — match against `ci.yml`), and that
`allow_force_pushes`/`allow_deletions` are false.

A CI job renamed without updating the required-checks list silently stops
gating merges. Compare the two lists explicitly rather than eyeballing them.

## 2. Code-owner review stays OFF

`require_code_owner_reviews` should be **false**, and that is intentional.
`CODEOWNERS` names a single maintainer, and GitHub does not let anyone approve
their own pull request — so with the rule on, every PR that maintainer opened
was unmergeable except by admin override. A rule bypassed every time is worse
than no rule, because it looks like review is happening when it is not.

Turn it back on only when `CODEOWNERS` lists more than one person:
```bash
CODE_OWNER_REVIEWS=true ./.github/scripts/configure-repo.sh
```

## 3. The `pypi` environment allows the `v*` tag

This is the one that actually breaks releases. A release workflow is triggered
by a **tag**, not a branch, so an environment restricted to "protected branches
only" rejects every release with:

> `Tag "vX.Y.Z" is not allowed to deploy to pypi due to environment protection rules`

```bash
gh api repos/uw-share-lab/refaudit/environments/pypi
gh api repos/uw-share-lab/refaudit/environments/pypi/deployment-branch-policies
```

Expected: `protected_branches: false`, `custom_branch_policies: true`, and one
policy of type `tag` named `v*`. The `gh api` calls that set this are in
CONTRIBUTING.

## 4. The trusted publisher matches exactly

Registered on PyPI against project `refaudit`, owner `uw-share-lab`, repository
`refaudit`, workflow `release.yml`, environment `pypi`. **All five must match or
PyPI refuses the upload** — and the error does not tell you which one is wrong.

This is checked on PyPI's own publishing settings page, not through `gh`. If the
repo was renamed, moved between orgs, or the workflow file renamed, this is
where it broke.

## 5. No secrets where none should be

```bash
gh secret list --repo uw-share-lab/refaudit
grep -n 'password\|api-token\|secrets\.' .github/workflows/release.yml
```

Publishing is OIDC Trusted Publishing; there is **no** PyPI token in this
repository, which is the point — there is no long-lived credential to leak. A
token appearing here is a regression worth raising loudly, not quietly removing.

## Report

For each item: intended (per CONTRIBUTING), actual, and whether they match. If
CONTRIBUTING is the side that is stale, fix the file — the record only helps if
it is true.
