# Open Digital Foundation

A domain-neutral foundation for data integration, governance, workflow
orchestration, AI model lifecycle management, deployment and observability.

This distribution contains no customer identity, production data, private
models, credentials, deployment addresses, backups, or commercial use cases.
Domain solutions must be delivered as separately maintained adapters/plugins.

## Core capabilities

- Identity, organizations, RBAC, auditing and tenant isolation
- Data source integration, catalog, quality, cleaning, lineage and labeling
- Feature engineering, experiments, training, evaluation and model registry
- Typed pipelines, background execution, serving and monitoring
- Object storage, time-series, event and API integration interfaces

## Security model

Runtime secrets are supplied through environment variables. The release must
pass `scripts/open_source_gate.py`; generated data and artifacts are ignored.
