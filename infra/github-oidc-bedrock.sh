#!/usr/bin/env bash
# One-time setup: let GitHub Actions call Bedrock without a stored secret.
#
# Creates the GitHub OIDC identity provider, a role only this repo's main branch
# can assume, and a policy that allows exactly one action on exactly one model.
# Run with a credential that can manage IAM (the CI user deliberately cannot),
# then set the repo variable that switches the full-bedrock job on:
#
#   aws_profile=admin ./infra/github-oidc-bedrock.sh
#   gh variable set AWS_CI_ROLE_ARN --body "arn:aws:iam::927152787027:role/ControlPlane-eval-ci"
#
# Everything here is deliberately narrow:
#   - the trust policy pins audience AND repo+branch; a sub condition that names
#     only the repo could be assumed by any branch, and one that omits the repo
#     could be assumed by any repository on GitHub
#   - the permission policy grants bedrock:InvokeModel on the one inference
#     profile the registry names, plus the foundation models it routes to
#     (a cross-region profile invokes the underlying model in whichever US
#     region it picks, so the routed-to ARNs must be allowed as well)
set -euo pipefail

AWS="aws ${aws_profile:+--profile $aws_profile}"
ACCOUNT_ID=927152787027
ROLE_NAME=ControlPlane-eval-ci
REPO=Lanier-Developments/ControlPlane
PROVIDER_ARN="arn:aws:iam::${ACCOUNT_ID}:oidc-provider/token.actions.githubusercontent.com"

# 1. The identity provider. AWS manages GitHub's thumbprints now; the value
#    passed here is the classic one and is ignored in favor of AWS's trust.
if ! $AWS iam get-open-id-connect-provider --open-id-connect-provider-arn "$PROVIDER_ARN" >/dev/null 2>&1; then
  $AWS iam create-open-id-connect-provider \
    --url https://token.actions.githubusercontent.com \
    --client-id-list sts.amazonaws.com \
    --thumbprint-list 6938fd4d98bab03faadb97b34396831e3780aea1
  echo "Created OIDC provider."
else
  echo "OIDC provider already exists."
fi

# 2. The role. Trust is scoped to this repo's main branch, which covers the
#    nightly schedule and workflow_dispatch runs — both execute on refs/heads/main.
TRUST=$(cat <<JSON
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {"Federated": "${PROVIDER_ARN}"},
    "Action": "sts:AssumeRoleWithWebIdentity",
    "Condition": {
      "StringEquals": {"token.actions.githubusercontent.com:aud": "sts.amazonaws.com"},
      "StringLike": {"token.actions.githubusercontent.com:sub": "repo:${REPO}:ref:refs/heads/main"}
    }
  }]
}
JSON
)
if ! $AWS iam get-role --role-name "$ROLE_NAME" >/dev/null 2>&1; then
  $AWS iam create-role --role-name "$ROLE_NAME" \
    --assume-role-policy-document "$TRUST" \
    --description "GitHub Actions (${REPO} main) -> Bedrock InvokeModel on claude-sonnet-4-6 only" \
    --max-session-duration 3600 >/dev/null
  echo "Created role ${ROLE_NAME}."
else
  $AWS iam update-assume-role-policy --role-name "$ROLE_NAME" --policy-document "$TRUST"
  echo "Role ${ROLE_NAME} already exists; trust policy refreshed."
fi

# 3. The permission. One action, one model. ARNs verified against the account:
#    aws bedrock get-inference-profile --inference-profile-identifier us.anthropic.claude-sonnet-4-6
PERMS=$(cat <<JSON
{
  "Version": "2012-10-17",
  "Statement": [{
    "Sid": "InvokeRegistrySonnetOnly",
    "Effect": "Allow",
    "Action": "bedrock:InvokeModel",
    "Resource": [
      "arn:aws:bedrock:us-east-1:${ACCOUNT_ID}:inference-profile/us.anthropic.claude-sonnet-4-6",
      "arn:aws:bedrock:us-east-1::foundation-model/anthropic.claude-sonnet-4-6",
      "arn:aws:bedrock:us-east-2::foundation-model/anthropic.claude-sonnet-4-6",
      "arn:aws:bedrock:us-west-2::foundation-model/anthropic.claude-sonnet-4-6"
    ]
  }]
}
JSON
)
$AWS iam put-role-policy --role-name "$ROLE_NAME" \
  --policy-name bedrock-invoke-sonnet-4-6 --policy-document "$PERMS"
echo "Attached bedrock-invoke-sonnet-4-6 policy."

echo
echo "Done. Switch the CI job on with:"
echo "  gh variable set AWS_CI_ROLE_ARN --body \"arn:aws:iam::${ACCOUNT_ID}:role/${ROLE_NAME}\""
