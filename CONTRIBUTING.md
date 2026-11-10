# Contributing to TensorPlatform

TensorPlatform follows the project-wide [License Governance](docs/governance/LICENSE_GOVERNANCE.md) and [Agent Contribution Policy](docs/governance/AGENT_CONTRIBUTION_POLICY.md).

## Operating model

Tensor currently operates with one accountable human maintainer and multiple agents. Agents may produce, test, review and integrate work, but they are not legal persons and must not fabricate human identities, signatures, permissions or approvals.

## Before submitting

1. Read the target repository's current license. Do not assume every Tensor repository uses Apache-2.0.
2. Identify the origin and license of copied, adapted, generated, vendored or otherwise imported material.
3. Preserve upstream copyright, attribution and NOTICE statements.
4. Do not submit proprietary, confidential, leaked, or rights-unclear code, datasets, model weights, figures or benchmark assets.
5. For data/model assets, include an asset-level rights/provenance manifest or keep the asset quarantined.
6. Include tests and explain how the contribution was verified.
7. Material agent work must disclose agent/tool, model and run metadata where available, reviewer evidence, risk tier and accountable human maintainer.

## DCO sign-off — humans only

Where DCO is required, the accountable human contributor signs off their own commit with `git commit -s` after reading the DCO and reviewing the contribution. Agents must never create, forge or apply a human `Signed-off-by` line.

A human maintainer must not sign off unless they can truthfully make the DCO representations and have a reasonable basis to establish the rights to submit the work. If rights cannot be established, quarantine, rewrite or exclude the contribution. DCO is not a substitute for permission or ownership.

## Risk-based review

- R0 routine changes may use automated checks and periodic sampling.
- R1 bounded changes require independent agent verification and automated checks.
- R2 material changes require independent review and maintainer approval before merge/release.
- R3 critical legal, license, security, sensitive-data and external-release changes require explicit human approval; no agent-only approval.

See the [Agent Contribution Policy](docs/governance/AGENT_CONTRIBUTION_POLICY.md) for full requirements.
