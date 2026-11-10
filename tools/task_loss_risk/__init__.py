"""Task loss risk calibrator — predicts P(agent_lost) at dispatch time.

Zero domain semantics: features are purely operational (repo, goal length,
budget, concurrency, historical repo loss rate, tree depth).  No ship-domain
terms appear anywhere in this module.

Provides:
  - extract_mission_dataset(): SQL → feature DataFrame with sha256 provenance
  - train_calibrators(): logistic regression + gradient boosting with time-split
  - evaluate_calibrators(): Brier skill, AUC, ECE, calibration curves
  - counterfactual_split_benefit(): historical replay of split decisions
  - decision_rule(): P(loss) >= tau -> recommend split
"""

__version__ = "0.1.0"
