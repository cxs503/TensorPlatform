# Agent Contribution and Single-Human Governance Policy

**Status:** Adopted operating policy  
**Accountable human:** Tensor project maintainer  
**Operating model:** One human maintainer; multiple autonomous and semi-autonomous agents  
**Effective:** 2026-11-10

## 1. Why this policy exists

Tensor is not organized as a conventional team of human developers. Agents may research, plan, implement, test, review, maintain documentation, curate data, and coordinate with other agents. The project currently has one human maintainer.

The process must preserve traceability and human accountability without requiring the maintainer to manually inspect every low-risk line or routine agent action.

## 2. Roles are not identities

An agent may perform one or more roles in a work package: producer, verifier, reviewer, integrator or monitor. Record the actual agent/tool identity and run context where available. Do not pretend that multiple agent names represent independent legal persons.

A role assignment does not grant authority to approve its own work, change licenses, waive policy, or attest to rights it cannot establish.

## 3. Human accountability

The project maintainer is the single accountable human for project-level acceptance and external release decisions. This does not mean the maintainer personally authored every file or must manually approve every low-risk internal operation.

Every agent-produced contribution intended for merge or publication must identify an accountable human maintainer. In this project that is the project maintainer, unless a different authorized human is explicitly designated.

An agent must never fabricate a human identity, DCO sign-off, signature, consent, permission or legal approval; claim that agent review constitutes human approval; change repository licensing or copyright ownership autonomously; publish private/customer/sensitive material or assets with unresolved rights; or merge/release high-risk work solely on agent consensus.

## 4. Required provenance for agent work

For material agent-produced contributions, record as much as is available:

- agent identity / stable agent ID and role;
- tool/provider and version; model identifier when exposed;
- run/session/task/work-package identifier;
- repository, branch, commit and changed-file scope;
- source URLs, upstream repositories, datasets and copied/adapted material;
- tests, static analysis, benchmark and validation evidence;
- independent reviewer agents and their findings;
- accountable human maintainer;
- final decision and unresolved limitations.

Do not store prompts, private reasoning traces, credentials, personal data or confidential customer information merely to satisfy provenance. Store concise task references and evidence digests. If metadata is unavailable, record `unknown`; do not invent values.

## 5. Risk tiers and acceptance gates

| Tier | Examples | Agent autonomy | Human gate |
|---|---|---|---|
| R0 — routine | formatting, typo fixes, generated indexes, non-sensitive docs | agent may implement, verify and merge if repository automation allows | none per change; periodic sampling |
| R1 — bounded | tests, isolated bug fixes, approved dependency patches, internal transforms | producer plus independent agent review and automated checks | exception-based or batch review |
| R2 — material | new features, scientific algorithms, new dependencies, dataset curation, model training, public documentation | independent checks and reproducible evidence | maintainer approval before merge/release |
| R3 — critical | license/ownership changes, legal notices, security-sensitive changes, private/customer/personal data, external publication, commercial datasets/models, destructive migrations | agents may prepare analysis and draft changes only | explicit human decision; no autonomous merge/release |

A change inherits the highest risk tier of any affected asset or action. Uncertain classification defaults to the higher tier.

## 6. Agent-to-agent review

Agent review helps discover defects, but is not independent human approval and is not proof of rights. For R1/R2 work, use separate producer and verifier roles where practical. The verifier should receive acceptance criteria and evidence independently, not merely echo the producer's report.

Review requirements should challenge scope, correctness, tests, security, dependencies, provenance, attribution, license compatibility, scientific validity, data quality and reproducibility. Record disagreements and unresolved findings. Repeated reviews by the same agent/session do not count as independent reviewers.

## 7. DCO and contribution attestations

DCO sign-off is for an accountable human contributor who has read and can truthfully make the DCO representations. An agent must never create, forge or apply a human `Signed-off-by` line.

For agent-produced work, the human maintainer may submit/sign off only after reviewing the contribution and confirming they are entitled to make the attestation. If the maintainer cannot establish the required rights, quarantine, rewrite or exclude the contribution. A DCO sign-off is not a substitute for ownership, permission or legal review.

## 8. One-human workload protection

Route only meaningful decisions to the maintainer. Agents should prepare concise decision packets containing what changed and why, risk tier, affected assets, source/rights evidence, test and independent-review results, unresolved issues, reversibility and exact decision requested.

Batch R0/R1 summaries. Escalate R2/R3 decisions individually or in small coherent batches. Do not page the maintainer for routine green checks.

## 9. Non-negotiable release rules

- No unknown-rights asset is published, redistributed, or used for a purpose requiring unresolved permission.
- No R3 action is approved solely by agents.
- No public release without a reproducible release manifest, tests, license inventory and required notices.
- No agent may approve its own work as the only reviewer.
- No review claim without evidence.
- Missing evidence must be reported as a gap, not invented as a pass.

## 10. Review and evolution

Review this policy after the first operational pilot, after major orchestration changes, and at least annually. The project maintainer may delegate preparation and verification, but legal and release authority remains with the explicitly designated human.
