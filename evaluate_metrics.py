import json
import re

with open("corpus.json", "r") as f:
    corpus = json.load(f)

# Baseline: Simple keyword match (simulating a naive rule-based system)
keywords = ["底孔", "排水", "不畅", "裂缝", "渗水", "底板", "脱空", "止水", "老化", "启闭机", "卡阻", "振动"]
baseline_pattern = re.compile("|".join(keywords))

results = []
for item in corpus:
    content = item["content"]
    # Remove markdown/code block wrappers for matching
    clean_content = re.sub(r"```.*?```", "", content, flags=re.DOTALL)
    clean_content = re.sub(r"<!--.*?-->", "", clean_content)
    
    pred = 1 if baseline_pattern.search(clean_content) else 0
    results.append({
        "id": item["id"],
        "label": item["label"],
        "pred": pred,
        "type": item["type"],
        "operator": item["operator"]
    })

# Calculate metrics
tp, fp, tn, fn = 0, 0, 0, 0
for r in results:
    if r["label"] == 1 and r["pred"] == 1: tp += 1
    elif r["label"] == 0 and r["pred"] == 1: fp += 1
    elif r["label"] == 0 and r["pred"] == 0: tn += 1
    elif r["label"] == 1 and r["pred"] == 0: fn += 1

recall = tp / (tp + fn) if (tp + fn) > 0 else 0
fp_rate = fp / (fp + tn) if (fp + tn) > 0 else 0
precision = tp / (tp + fp) if (tp + fp) > 0 else 0

# Per-type metrics
type_metrics = {}
for t in set(r["type"] for r in results):
    t_items = [r for r in results if r["type"] == t]
    t_tp = sum(1 for r in t_items if r["label"] == 1 and r["pred"] == 1)
    t_fn = sum(1 for r in t_items if r["label"] == 1 and r["pred"] == 0)
    t_fp = sum(1 for r in t_items if r["label"] == 0 and r["pred"] == 1)
    t_tn = sum(1 for r in t_items if r["label"] == 0 and r["pred"] == 0)
    type_metrics[t] = {
        "recall": t_tp / (t_tp + t_fn) if (t_tp + t_fn) > 0 else 0,
        "fp_rate": t_fp / (t_fp + t_tn) if (t_fp + t_tn) > 0 else 0,
        "n": len(t_items)
    }

# Missed samples (FN)
missed = [r for r in results if r["label"] == 1 and r["pred"] == 0]

metrics = {
    "overall": {
        "recall": recall,
        "fp_rate": fp_rate,
        "precision": precision,
        "tp": tp, "fp": fp, "tn": tn, "fn": fn
    },
    "per_class": type_metrics,
    "missed_samples": missed[:10] # Top 10
}

with open("metrics.json", "w") as f:
    json.dump(metrics, f, ensure_ascii=False, indent=2)

print(json.dumps(metrics, ensure_ascii=False, indent=2))
