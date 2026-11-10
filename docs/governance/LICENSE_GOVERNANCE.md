# Tensor License Governance

**Status:** Adopted policy  
**Policy owner:** Tensor project maintainer  
**Effective date:** 2026-11-10  
**Scope:** All repositories published under the Tensor project family

## 1. Strategic decision

Tensor follows a **fully open-source core** strategy.

- Original Tensor software code is intended to use **Apache License 2.0 (SPDX: Apache-2.0)** as the default license.
- Tensor will monetize services, hosted infrastructure, support, enterprise operations, certified capabilities, data products where rights permit, and ecosystem transactions—not by withholding the basic right to use the core software.
- Open source does not mean that every dataset, model weight, customer artifact, third-party asset, or trademark is unrestricted.

This policy is a governance target. It does not itself relicense any existing repository or third-party contribution.

## 2. License by asset type

| Asset | Default policy | Notes |
|---|---|---|
| Original software source code, SDKs, APIs, agents, tools and infrastructure | Apache-2.0 | Use the complete canonical LICENSE text and SPDX identifier. |
| Software documentation maintained with code | Apache-2.0 by default | A repository may explicitly separate documentation under CC BY 4.0. State the boundary clearly. |
| Standalone tutorials, articles, diagrams and educational prose | CC BY 4.0 | Exclude embedded third-party material and software source code. |
| Public datasets | Dataset-specific license | Prefer CC BY 4.0 when attribution is required, or CC0 only where the rights holder can validly waive rights. |
| Structured databases | Dataset-specific terms; assess ODbL where appropriate | Record database rights, source terms, attribution, share-alike and extraction restrictions. |
| Model weights and checkpoints | Model-specific license | Never assume the software repository license applies to weights. |
| Third-party code, data, models, papers, images and benchmark materials | Original upstream license/terms | Preserve notices; do not relicense without permission. |
| Customer data, private research and commissioned deliverables | Contract and customer authorization | Never publish by default. |
| Tensor names, logos and marks | Trademark policy, not software license | Apache-2.0 does not grant trademark rights. |

Every distributable dataset or model artifact must carry a machine-readable manifest stating its license, source, provenance, attribution, restrictions, version, and permitted uses. Unknown or conflicting rights mean **do not redistribute** until resolved.

## 3. Repository policy

### 3.1 New repositories

Before the first non-trivial code contribution is merged:

1. Select Apache-2.0 for original Tensor software.
2. Add the full canonical Apache-2.0 text in the root `LICENSE`.
3. Add a root `NOTICE` only when attribution notices or distribution context need to be carried; keep it accurate.
4. Declare `SPDX-License-Identifier: Apache-2.0` in package metadata and source headers where practical.
5. Add the contribution rules in `CONTRIBUTING.md`.
6. Record third-party dependencies and their licenses.

An empty repository can be licensed before code is added. A repository containing contributions from multiple authors must not be newly licensed until the rights to license those contributions have been confirmed.

### 3.2 Existing repositories

- Existing Apache-2.0 repositories are aligned; retain existing copyright and attribution notices.
- Existing MIT repositories remain MIT for already released versions. A move to Apache-2.0 for the repository's future combined work requires a rights/provenance review and authorization from all relevant copyright holders, or a clean replacement/rewrite of material that cannot be relicensed.
- Existing GPL-3.0 repositories remain GPL-3.0 unless a documented rights audit confirms that all included code can be relicensed. Do not remove GPL notices or copy GPL-only code into Apache-2.0 repositories.
- Repositories without a root license are **not automatically Apache-2.0**. Public visibility does not grant general reuse rights. Add a license only after the copyright and contribution audit.
- Historical releases retain the license that applied to those releases. Any approved license change must be documented and versioned.

### 3.3 No blanket relicensing

Changing a repository's LICENSE file does not erase prior grants or automatically grant the maintainer rights in code written by others. Preserve individual notices and upstream license files. When rights are uncertain, quarantine the affected files from new releases until resolved.

## 4. Contributor and AI-agent rules

Tensor accepts contributions under the license stated by the target repository, subject to the repository's contribution process.

- Require a Developer Certificate of Origin (DCO) sign-off for new contributions, using `Signed-off-by: Full Name <email>`, after the contributor has reviewed the DCO terms.
- DCO is a provenance/attestation mechanism; it is not a substitute for obtaining rights where the submitter does not own or control them.
- Require a separate written permission or CLA for exceptional large contributions, corporate contributions where authority is unclear, code intended for dual licensing, or any contribution whose rights cannot be established through the normal process.
- Agent-generated contributions must be submitted by an accountable human or organization. The submitter must identify the agent/tool where practical, review the output, confirm authority to submit it, and check for copied or incompatible third-party material.
- Do not accept submissions copied from proprietary repositories, papers, websites, or datasets unless the license permits the intended use.
- Preserve author attribution and do not replace all authors with a single project-wide copyright holder.

## 5. Dependency and release compliance

Every repository must maintain:

- A dependency inventory with package name, version, source, license/SPDX identifier, and any exceptions.
- A record of vendored, copied, generated, or bundled code.
- A release-time license scan (for example, a suitable SBOM and license scanner).
- A documented review for copyleft, source-available, non-commercial, field-of-use, attribution, and model/data restrictions.
- Required third-party notices in source and binary distributions.

A dependency being installable through a package manager does not establish that its license is compatible with Tensor's distribution model. Escalate uncertain or conflicting dependencies before release.

## 6. Data and knowledge governance

TensorData and TensorKnowledge must distinguish the license of the software from the rights governing each content item.

Each dataset/knowledge asset should record at minimum:

- asset identifier, title, version and owner/rightsholder if known;
- source URL or acquisition record and collection date;
- license and exact license version, or `NOASSERTION` / `UNRESOLVED` when unknown;
- attribution and notice obligations;
- consent, privacy, confidentiality and jurisdictional restrictions;
- allowed purposes, redistribution and derivative-work rules;
- transformations, lineage and quality/validation evidence.

Do not infer that facts, web access, public availability, or an open-source tool make the underlying dataset freely redistributable. Obtain legal review for personal data, confidential data, database rights, text/data mining, and cross-border transfers where relevant.

## 7. Branding and commercial services

- Tensor trademarks, logos, product names, official badges and domains are governed separately from code licenses.
- Third parties may truthfully describe compatibility but must not imply official endorsement or impersonate Tensor-operated services.
- Official hosted services, enterprise support, managed operations, certification, curated catalogs and paid data services may be commercial.
- Provide exportable formats and documented APIs; do not create artificial lock-in as the primary retention mechanism.

## 8. Required repository files

For an Apache-2.0 software repository, the target baseline is:

- `LICENSE` — complete canonical Apache-2.0 text;
- `NOTICE` — only when required for accurate attributions or useful distribution notices;
- `CONTRIBUTING.md` — contribution, DCO, AI-assistance and provenance rules;
- `THIRD_PARTY_NOTICES.md` or an equivalent generated inventory — bundled third-party materials;
- package metadata with `Apache-2.0` SPDX expression;
- `DATA_LICENSES.md` or per-asset manifests where data/model artifacts are present.

Do not add blanket Apache source headers to files whose ownership or license is different. Preserve upstream headers.

## 9. Change control and enforcement

- The repository maintainer is responsible for the license inventory and release gate.
- Any license change requires a pull request, a provenance/rights review, a compatibility review, and a recorded approval.
- A license change must not be merged solely by an autonomous agent.
- CI should fail on missing license declarations for new first-party packages, unapproved license exceptions, missing data manifests for distributable datasets, or unresolved third-party notices.
- This policy should be reviewed at least annually and whenever Tensor's legal entity, contribution model, or distribution model materially changes.

## 10. Legal boundary

This policy is an engineering and project-governance baseline, not legal advice. Before commercial release, fundraising diligence, relicensing, or distribution of data/model assets at scale, obtain review from qualified counsel in the relevant jurisdictions.
