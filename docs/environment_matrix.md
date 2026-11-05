# Portable environment requirements

This repository does not maintain an inventory of deployed hosts or private
hardware. Configure service endpoints, workspace locations, scheduler targets,
credentials and device selection in the deployment environment.

Use CPU execution where supported and optional accelerator backends according
to the capability requirements of each workload. Detect capabilities explicitly;
do not infer support from a machine name. Numerical equivalence and performance
must be verified independently for each supported backend.

Addresses in documentation use reserved example ranges. Neutral paths are
anonymized examples, not a statement of the original deployment topology.
Benchmark device models and measured conditions remain reproducibility metadata.
See HARDWARE_CLEANUP_NOTICE.md for the history migration and evidence limits.
