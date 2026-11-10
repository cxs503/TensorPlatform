# Agent instructions

Before making changes, read the [Tensor ecosystem vision](https://github.com/cxs503/TensorPlatform/blob/main/docs/architecture/TENSOR_ECOSYSTEM_AI_ERA_VISION.md) and this repository's [project charter](docs/PROJECT_CHARTER.md). These documents define ownership boundaries; the source code and tests define what is actually implemented.

## Required development behavior

- State the goal, why it belongs in this repository, and which public or cross-repository contracts it affects.
- Preserve the repository boundary: TensorPlatform is domain-neutral AI-era infrastructure. Read the canonical ecosystem vision and README before work. Keep physical formulas, scientific workflow semantics, video grammar, game mechanics, humanities methodology, and other specialist rules out of platform core. Implement reusable primitives and versioned contracts; integrate domain capabilities through adapters. Do not duplicate mature external infrastructure without a demonstrated need. Report code/test/integration/production evidence separately; PR completion is not proof of quality.
- Inspect relevant code, tests, recent changes, and existing benchmark records before editing.
- Prefer stable, versioned public interfaces; avoid private cross-repository imports and duplicated sources of truth.
- Include runnable tests/examples and quantitative evidence appropriate to the change. Report exact commands, results, failures, untested paths, assumptions, and limitations.
- Distinguish implementation, unit tests, integration tests, benchmarks, domain validation, and production deployment evidence. Never claim more than the evidence establishes.
- Keep changes scoped, reviewable, and reversible. Do not optimize for PR count, code volume, or workflow completion alone.

## Agent contribution and license governance

Before producing, reviewing, merging or releasing material agent work, follow the [single-human Agent Contribution Policy](docs/governance/AGENT_CONTRIBUTION_POLICY.md) and [License Governance](docs/governance/LICENSE_GOVERNANCE.md).

- Identify the producer agent/tool, model and run/work-package metadata where available; never invent unavailable metadata.
- Identify the accountable human maintainer. Agent identities are operational identities, not legal persons.
- Classify work R0–R3. R2 requires maintainer approval before merge/release; R3 requires explicit human approval and cannot be agent-only approved.
- Agent review is evidence for quality control, not a human signature, legal permission or rights clearance.
- Never fabricate a human DCO `Signed-off-by` line, consent, signature, CLA, copyright ownership or legal approval. DCO sign-off is human-only.
- Record source URLs, upstream licenses, third-party assets, tests and reviewer evidence. Unknown rights remain quarantined.
- Prepare concise decision packets for the maintainer; batch routine R0/R1 summaries and escalate material R2/R3 decisions.
