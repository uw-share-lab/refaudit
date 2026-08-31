#!/usr/bin/env bash
#
# Apply the repository settings that releases and review depend on.
#
# These settings live in GitHub rather than in the source tree, which means a
# fork or a recreated repository silently loses them and the failure surfaces
# much later as a rejected release. Keeping them here makes the configuration
# reviewable, diffable and re-appliable.
#
# Idempotent: safe to re-run at any time. Requires the gh CLI, authenticated
# with admin rights on the repository.
#
#   ./.github/scripts/configure-repo.sh                     # uw-share-lab/refaudit
#   REPO=my-org/my-fork ./.github/scripts/configure-repo.sh # somewhere else
#
set -euo pipefail

REPO="${REPO:-uw-share-lab/refaudit}"
ENVIRONMENT="${ENVIRONMENT:-pypi}"
CODE_OWNER_REVIEWS="${CODE_OWNER_REVIEWS:-true}"

command -v gh >/dev/null || { echo "error: gh CLI not found" >&2; exit 1; }
gh auth status >/dev/null 2>&1 || { echo "error: gh is not authenticated" >&2; exit 1; }

echo "configuring ${REPO}"

# --- 1. Release environment -------------------------------------------------
# A release workflow is triggered by a *tag*. An environment restricted to
# "protected branches only" therefore rejects every release with:
#   Tag "vX.Y.Z" is not allowed to deploy to <env> due to environment
#   protection rules
# so the policy has to be custom rules with a tag pattern. This is the single
# most confusing failure in the whole pipeline; it looks like a permissions
# problem and is actually a branch-vs-tag mismatch.
echo "  environment '${ENVIRONMENT}': allow v* tags to deploy"
gh api -X PUT "repos/${REPO}/environments/${ENVIRONMENT}" --input - >/dev/null <<'JSON'
{"deployment_branch_policy": {"protected_branches": false, "custom_branch_policies": true}}
JSON

# Re-adding an existing policy returns 422; that is success for our purposes.
if ! gh api "repos/${REPO}/environments/${ENVIRONMENT}/deployment-branch-policies" \
        --jq '.branch_policies[].name' 2>/dev/null | grep -qx 'v\*'; then
  gh api -X POST "repos/${REPO}/environments/${ENVIRONMENT}/deployment-branch-policies" \
    --input - >/dev/null <<'JSON'
{"name": "v*", "type": "tag"}
JSON
  echo "    added tag policy v*"
else
  echo "    tag policy v* already present"
fi

# --- 2. Branch protection ---------------------------------------------------
# enforce_admins is deliberately false: repository admins can still push
# directly, everyone else goes through review. Tightening this would lock the
# maintainer out of their own hotfixes.
echo "  branch protection on main"
gh api -X PUT "repos/${REPO}/branches/main/protection" --input - >/dev/null <<JSON
{
  "required_status_checks": {
    "strict": true,
    "contexts": ["test (3.10)", "test (3.11)", "test (3.12)", "test (3.13)"]
  },
  "enforce_admins": false,
  "required_pull_request_reviews": {
    "required_approving_review_count": 1,
    "require_code_owner_reviews": ${CODE_OWNER_REVIEWS},
    "dismiss_stale_reviews": true,
    "require_last_push_approval": false
  },
  "restrictions": null,
  "required_linear_history": true,
  "allow_force_pushes": false,
  "allow_deletions": false,
  "required_conversation_resolution": true
}
JSON

# --- 3. Discoverability -----------------------------------------------------
echo "  topics"
gh api -X PUT "repos/${REPO}/topics" --input - >/dev/null <<'JSON'
{"names":["bibtex","citations","crossref","openalex","arxiv","research-integrity","latex"]}
JSON

echo
echo "done. current state:"
gh api "repos/${REPO}/environments/${ENVIRONMENT}/deployment-branch-policies" \
  --jq '.branch_policies[] | "  deploy allowed from: \(.name) [\(.type)]"'
gh api "repos/${REPO}/branches/main/protection" --jq '
  "  reviews required   : \(.required_pull_request_reviews.required_approving_review_count)",
  "  code-owner review  : \(.required_pull_request_reviews.require_code_owner_reviews)",
  "  status checks      : \(.required_status_checks.contexts | join(", "))",
  "  enforced on admins : \(.enforce_admins.enabled)"'

cat <<'NOTE'

  Not settable from here (do it once, by hand, on pypi.org):
    Publishing -> trusted publisher for project 'refaudit'
      owner uw-share-lab | repo refaudit | workflow release.yml | environment pypi
    All five fields must match exactly or PyPI refuses the upload.
NOTE
