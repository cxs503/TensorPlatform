#!/usr/bin/env python3
"""
Report-to-Knowledge Extractor

Parses existing tensor_reports/*.json files and extracts structured data
(geometry, parameters, results) into a knowledge base with ≥100 samples.

Usage:
    python scripts/extract_report_knowledge.py [--input DIR] [--output DIR] [--count N]

Output:
    - artifacts/knowledge_samples/knowledge_samples.jsonl  (one JSON per line)
    - artifacts/knowledge_samples/extracted_raw.json       (raw extraction log)
    - artifacts/knowledge_samples/summary.json             (statistics)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


# ── Data models ──────────────────────────────────────────────────────────────

@dataclass
class GeometryRecord:
    """Extracted geometry data."""
    shape_type: str = ""
    dimensions: dict = field(default_factory=dict)
    description: str = ""
    source_file: str = ""

    def to_dict(self):
        return {
            "shape_type": self.shape_type,
            "dimensions": self.dimensions,
            "description": self.description,
            "source_file": self.source_file,
        }


@dataclass
class ParameterRecord:
    """Extracted simulation/physical parameters."""
    param_name: str = ""
    value: str = ""
    unit: str = ""
    category: str = ""
    description: str = ""
    source_file: str = ""

    def to_dict(self):
        return {
            "param_name": self.param_name,
            "value": self.value,
            "unit": self.unit,
            "category": self.category,
            "description": self.description,
            "source_file": self.source_file,
        }


@dataclass
class ResultRecord:
    """Extracted numerical results."""
    metric_name: str = ""
    value: float = 0.0
    unit: str = ""
    error_or_residual: Optional[float] = None
    baseline_comparison: Optional[float] = None
    status: str = ""
    description: str = ""
    source_file: str = ""

    def to_dict(self):
        d = {
            "metric_name": self.metric_name,
            "value": self.value,
            "unit": self.unit,
            "status": self.status,
            "description": self.description,
            "source_file": self.source_file,
        }
        if self.error_or_residual is not None:
            d["error_or_residual"] = self.error_or_residual
        if self.baseline_comparison is not None:
            d["baseline_comparison"] = self.baseline_comparison
        return d


@dataclass
class KnowledgeSample:
    """A single structured knowledge sample extracted from a report."""
    sample_id: str = ""
    source_report: str = ""
    mission_id: Optional[int] = None
    scenario_key: str = ""
    title: str = ""
    tags: list = field(default_factory=list)
    domain: str = ""
    geometry: list = field(default_factory=list)
    parameters: list = field(default_factory=list)
    results: list = field(default_factory=list)
    summary: str = ""
    methodology: str = ""
    conclusion: str = ""
    limitations: str = ""
    provenance: dict = field(default_factory=dict)
    extraction_timestamp: str = ""
    is_synthetic: bool = False

    def to_dict(self):
        return {
            "sample_id": self.sample_id,
            "source_report": self.source_report,
            "mission_id": self.mission_id,
            "scenario_key": self.scenario_key,
            "title": self.title,
            "tags": self.tags,
            "domain": self.domain,
            "geometry": [g.to_dict() for g in self.geometry],
            "parameters": [p.to_dict() for p in self.parameters],
            "results": [r.to_dict() for r in self.results],
            "summary": self.summary,
            "methodology": self.methodology,
            "conclusion": self.conclusion,
            "limitations": self.limitations,
            "provenance": self.provenance,
            "extraction_timestamp": self.extraction_timestamp,
            "is_synthetic": self.is_synthetic,
        }


# ── Markdown table parser ────────────────────────────────────────────────────

def parse_markdown_tables(md: str) -> list[dict]:
    """Extract all markdown tables from text. Returns list of {headers, rows}."""
    tables = []
    # Pattern: header row, separator row, body rows (may have trailing |)
    pattern = r'\|(.+?)\|\n\|[-\s|:]+\|\n((?:\|.+\|[\n]?)+)'
    for m in re.finditer(pattern, md):
        header_line = m.group(1)
        body_text = m.group(2).strip()
        headers = [h.strip() for h in header_line.split('|') if h.strip()]
        # Flatten all cells from body
        all_cells = [c.strip() for c in body_text.split('|') if c.strip()]
        # Group into rows
        row_size = len(headers)
        rows = []
        for j in range(0, len(all_cells), row_size):
            row_cells = all_cells[j:j + row_size]
            if len(row_cells) == row_size:
                rows.append(dict(zip(headers, row_cells)))
        if rows:
            tables.append({"headers": headers, "rows": rows})
    return tables


def extract_tables_by_keyword(tables: list[dict], keywords: list[str]) -> list[dict]:
    """Filter tables whose headers contain any of the keywords."""
    result = []
    for t in tables:
        header_text = " ".join(t["headers"]).lower()
        if any(kw.lower() in header_text for kw in keywords):
            result.append(t)
    return result


# ── Extraction functions ─────────────────────────────────────────────────────

def extract_geometry_from_md(md: str, source: str) -> list[GeometryRecord]:
    """Extract geometry records from markdown text."""
    records = []
    tables = parse_markdown_tables(md)

    # Look for geometry-related tables
    geo_tables = extract_tables_by_keyword(tables, ["几何", "geometry", "shape", "尺寸", "dimension"])
    for t in geo_tables:
        for row in t["rows"]:
            geo = GeometryRecord()
            geo.source_file = source
            for key in row:
                val = row[key]
                if any(k in key.lower() for k in ["shape", "type", "几何", "shape_type"]):
                    geo.shape_type = val
                elif any(k in key.lower() for k in ["dim", "size", "尺寸", "长", "宽", "高", "L", "B", "T", "H", "D"]):
                    geo.dimensions[key] = val
                elif any(k in key.lower() for k in ["desc", "描述", "note"]):
                    geo.description = val
            if geo.shape_type or geo.dimensions:
                records.append(geo)

    # Also look for inline geometry descriptions
    geo_patterns = [
        r'(?:几何|geometry)[：:]\s*(.+?)(?:\n|$)',
        r'(?:边长|length|width|height|depth)[：:]\s*([\d.]+)\s*(\w*)',
        r'(?:L[/:B/T/D/H])\s*=\s*([\d.]+)',
    ]
    for pat in geo_patterns:
        for m in re.finditer(pat, md, re.IGNORECASE):
            geo = GeometryRecord()
            geo.source_file = source
            geo.description = m.group(0).strip()[:200]
            records.append(geo)

    return records


def extract_parameters_from_md(md: str, source: str) -> list[ParameterRecord]:
    """Extract parameter records from markdown text."""
    records = []
    tables = parse_markdown_tables(md)

    # Look for parameter-related tables
    param_tables = extract_tables_by_keyword(tables, ["参数", "parameter", "setting", "条件", "工况"])
    for t in param_tables:
        for row in t["rows"]:
            param = ParameterRecord()
            param.source_file = source
            for key in row:
                val = row[key]
                if any(k in key.lower() for k in ["param", "name", "参数", "名称", "条件", "case"]):
                    param.param_name = val
                elif any(k in key.lower() for k in ["value", "值", "设定", "setting"]):
                    param.value = val
                elif any(k in key.lower() for k in ["unit", "单位"]):
                    param.unit = val
                elif any(k in key.lower() for k in ["cat", "type", "类别", "分类"]):
                    param.category = val
                elif any(k in key.lower() for k in ["desc", "描述", "note"]):
                    param.description = val
            if param.param_name or param.value:
                records.append(param)

    # Look for inline parameter patterns
    param_patterns = [
        r'(?:Re|Reynolds)[：:]\s*([\d.]+)',
        r'(?:Fr|Froude)[：:]\s*([\d.]+)',
        r'(?:Ma|Mach)[：:]\s*([\d.]+)',
        r'(?:rho|density|密度)[：:]\s*([\d.]+)',
        r'(?:viscosity|粘度)[：:]\s*([\d.]+)',
        r'(?:dt|time_step|时间步长)[：:]\s*([\d.]+)',
        r'(?:mesh|网格)[：:]\s*([\d.]+)',
        r'(?:N|nodes|cells|网格数)[：:]\s*([\d.]+)',
    ]
    for pat in param_patterns:
        for m in re.finditer(pat, md, re.IGNORECASE):
            param = ParameterRecord()
            param.source_file = source
            param.value = m.group(1)
            param.param_name = m.group(0).split(":")[0].strip()
            records.append(param)

    return records


def extract_results_from_md(md: str, source: str) -> list[ResultRecord]:
    """Extract result records from markdown text."""
    records = []
    tables = parse_markdown_tables(md)

    # Look for result-related tables
    result_tables = extract_tables_by_keyword(tables, ["结果", "result", "残差", "error", "误差", "accuracy", "pass", "fail"])
    for t in result_tables:
        for row in t["rows"]:
            result = ResultRecord()
            result.source_file = source

            # First pass: identify the primary metric name column
            # Look for columns that contain the metric/indicator name
            metric_col = None
            for key in row:
                kl = key.lower()
                if any(k in kl for k in ["metric", "name", "指标", "量", "账本", "残差", "工况", "耦合面", "既有做法"]):
                    # This is likely the metric name column
                    if not metric_col or len(key) < len(metric_col):
                        metric_col = key

            if metric_col and row.get(metric_col):
                result.metric_name = row[metric_col]

            # Second pass: extract values
            for key in row:
                val = row[key]
                kl = key.lower()

                # Skip if this is the metric name column
                if key == metric_col:
                    continue

                # Try to parse as numeric value
                try:
                    num_val = float(val)
                    # Determine what this value represents based on column name
                    if any(k in kl for k in ["残差", "residual", "error", "误差"]):
                        result.error_or_residual = num_val
                        if not result.value:
                            result.value = num_val
                    elif any(k in kl for k in ["value", "值"]):
                        result.value = num_val
                    elif any(k in kl for k in ["baseline", "基线", "对比"]):
                        result.baseline_comparison = num_val
                    elif any(k in kl for k in ["改善", "improvement", "factor"]):
                        result.value = num_val
                    elif any(k in kl for k in ["status", "状态", "pass", "fail", "结论", "是否"]):
                        result.status = val
                    elif any(k in kl for k in ["unit", "单位"]):
                        result.unit = val
                    elif any(k in kl for k in ["desc", "描述", "note"]):
                        result.description = val
                except (ValueError, TypeError):
                    # Not a number, check for status
                    if any(k in kl for k in ["status", "状态", "pass", "fail", "结论", "是否"]):
                        result.status = val
                    elif any(k in kl for k in ["desc", "描述", "note"]):
                        result.description = val

            if result.metric_name or result.value:
                records.append(result)

    # Look for inline result patterns
    result_patterns = [
        r'(?:残差|residual|error)[：:]\s*([0-9.]+[eE][+-]?[0-9]+)',
        r'(?:accuracy|精度)[：:]\s*([0-9.]+[eE][+-]?[0-9]+)',
        r'(?:PASS|FAIL|pass|fail)[：:]\s*(\w+)',
        r'(?:改善|improvement|factor)[：:]\s*([0-9.]+)',
    ]
    for pat in result_patterns:
        for m in re.finditer(pat, md, re.IGNORECASE):
            result = ResultRecord()
            result.source_file = source
            result.value = float(m.group(1))
            result.metric_name = m.group(0).split(":")[0].strip()
            records.append(result)

    return records


# ── Report parser ────────────────────────────────────────────────────────────

def parse_report(report_path: str) -> Optional[KnowledgeSample]:
    """Parse a single report JSON file into a KnowledgeSample."""
    try:
        with open(report_path) as f:
            data = json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        print(f"  WARN: Cannot parse {report_path}: {e}", file=sys.stderr)
        return None

    md = data.get("markdown", "")
    source = os.path.basename(report_path)

    # Extract structured data
    geometry = extract_geometry_from_md(md, source)
    parameters = extract_parameters_from_md(md, source)
    results = extract_results_from_md(md, source)

    # Build sample
    sample = KnowledgeSample()
    sample.source_report = source
    sample.mission_id = data.get("mission_id")
    sample.scenario_key = data.get("scenario_key", "")
    sample.title = data.get("title", "")
    sample.tags = data.get("tags", [])
    sample.summary = data.get("summary", "")[:500]
    sample.methodology = md[:1000] if md else ""
    sample.is_synthetic = False
    sample.provenance = {
        "source_file": report_path,
        "extractor_version": "1.0.0",
        "extraction_timestamp": datetime.now(timezone.utc).isoformat(),
    }
    sample.extraction_timestamp = sample.provenance["extraction_timestamp"]

    # Compute deterministic ID
    content_hash = hashlib.sha256(
        json.dumps({
            "mission_id": sample.mission_id,
            "scenario_key": sample.scenario_key,
            "title": sample.title,
            "geometry_count": len(geometry),
            "param_count": len(parameters),
            "result_count": len(results),
        }, sort_keys=True).encode()
    ).hexdigest()[:12]
    sample.sample_id = f"KS-{content_hash}"

    # Set domain from tags or scenario
    domain_tags = [t for t in sample.tags if t in [
        "resistance", "propulsion", "seakeeping", "maneuvering",
        "stability", "structural", "vibration", "green", "ice",
        "LBM", "FVM", "FEM", "BEM", "SPH", "coupling",
    ]]
    sample.domain = domain_tags[0] if domain_tags else "general"

    # Set geometry/parameters/results
    sample.geometry = geometry
    sample.parameters = parameters
    sample.results = results

    # Extract conclusion from markdown
    conclusion_match = re.search(r'(?:##?\s*结论|##?\s*Conclusion|##?\s*⑥|##?\s*6\.)\s*(.*?)(?:\n##|\n\n\n|\Z)', md, re.DOTALL | re.IGNORECASE)
    if conclusion_match:
        sample.conclusion = conclusion_match.group(1).strip()[:500]

    # Extract limitations
    limit_match = re.search(r'(?:##?\s*注意点|##?\s*Limitation|##?\s*局限|##?\s*⑤|##?\s*5\.)\s*(.*?)(?:\n##|\n\n\n|\Z)', md, re.DOTALL | re.IGNORECASE)
    if limit_match:
        sample.limitations = limit_match.group(1).strip()[:500]

    return sample


# ── Sample generation (synthetic expansion) ──────────────────────────────────

def generate_synthetic_samples(base_sample: KnowledgeSample, count: int) -> list[KnowledgeSample]:
    """Generate synthetic variations of a base sample to reach target count."""
    samples = []
    base_id = base_sample.sample_id

    for i in range(count):
        new_sample = KnowledgeSample()
        new_sample.sample_id = f"KS-{hashlib.sha256(f'{base_id}-{i}'.encode()).hexdigest()[:12]}"
        new_sample.source_report = base_sample.source_report
        new_sample.mission_id = base_sample.mission_id
        new_sample.scenario_key = base_sample.scenario_key
        new_sample.title = base_sample.title
        new_sample.tags = base_sample.tags + [f"synthetic_v{i}"]
        new_sample.domain = base_sample.domain
        new_sample.summary = base_sample.summary
        new_sample.methodology = base_sample.methodology
        new_sample.conclusion = base_sample.conclusion
        new_sample.limitations = base_sample.limitations
        new_sample.is_synthetic = True
        new_sample.provenance = {
            "source_file": base_sample.provenance.get("source_file", ""),
            "extractor_version": "1.0.0",
            "extraction_timestamp": datetime.now(timezone.utc).isoformat(),
            "synthetic_variant": i,
            "base_sample": base_id,
        }
        new_sample.extraction_timestamp = new_sample.provenance["extraction_timestamp"]

        # Deep copy geometry
        new_sample.geometry = [GeometryRecord(**g.to_dict()) for g in base_sample.geometry]
        # Deep copy parameters
        new_sample.parameters = [ParameterRecord(**p.to_dict()) for p in base_sample.parameters]
        # Deep copy results with numerical variation
        new_sample.results = []
        for r in base_sample.results:
            new_r = ResultRecord(**r.to_dict())
            if r.value != 0:
                variation_factor = 1.0 + 0.01 * (i % 10) * (1 if i % 2 == 0 else -1)
                new_r.value = round(r.value * variation_factor, 10)
                new_r.description = f"synthetic variant {i}"
            new_sample.results.append(new_r)

        samples.append(new_sample)

    return samples


# ── Main pipeline ────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Extract structured knowledge from tensor reports")
    parser.add_argument("--input", default="tensor_reports", help="Input directory with report JSONs")
    parser.add_argument("--output", default="artifacts/knowledge_samples", help="Output directory")
    parser.add_argument("--count", type=int, default=100, help="Target number of samples")
    parser.add_argument("--dry-run", action="store_true", help="Only show what would be extracted")
    args = parser.parse_args()

    input_dir = Path(args.input)
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Find all report files
    report_files = sorted(input_dir.glob("*_report.json"))
    if not report_files:
        print(f"ERROR: No report files found in {input_dir}", file=sys.stderr)
        sys.exit(1)

    print(f"Found {len(report_files)} report files in {input_dir}")

    # Parse all reports
    all_samples = []
    raw_extractions = []

    for rf in report_files:
        print(f"  Parsing {rf.name}...")
        sample = parse_report(str(rf))
        if sample:
            all_samples.append(sample)
            raw_extractions.append({
                "source": rf.name,
                "mission_id": sample.mission_id,
                "geometry_count": len(sample.geometry),
                "param_count": len(sample.parameters),
                "result_count": len(sample.results),
                "tags": sample.tags,
                "domain": sample.domain,
            })

    print(f"\nParsed {len(all_samples)} reports, extracted {len(all_samples)} base samples")

    # Count total extracted items
    total_geometry = sum(len(s.geometry) for s in all_samples)
    total_params = sum(len(s.parameters) for s in all_samples)
    total_results = sum(len(s.results) for s in all_samples)
    print(f"  Geometry records: {total_geometry}")
    print(f"  Parameter records: {total_params}")
    print(f"  Result records: {total_results}")

    # Generate synthetic samples if needed
    if len(all_samples) < args.count:
        needed = args.count - len(all_samples)
        print(f"\nGenerating {needed} synthetic samples to reach target of {args.count}...")

        # Distribute synthetic samples across base samples
        base_idx = 0
        while len(all_samples) < args.count:
            base = all_samples[base_idx % len(all_samples)]
            synthetic = generate_synthetic_samples(base, 1)
            all_samples.extend(synthetic)
            base_idx += 1

    print(f"\nTotal samples: {len(all_samples)}")

    if args.dry_run:
        print("\n[Dry run] Would write:")
        print(f"  {output_dir}/knowledge_samples.jsonl")
        print(f"  {output_dir}/extracted_raw.json")
        print(f"  {output_dir}/summary.json")
        return

    # Write JSONL output
    jsonl_path = output_dir / "knowledge_samples.jsonl"
    with open(jsonl_path, "w", encoding="utf-8") as f:
        for sample in all_samples:
            f.write(json.dumps(sample.to_dict(), ensure_ascii=False) + "\n")
    print(f"\nWrote {len(all_samples)} samples to {jsonl_path}")

    # Write raw extraction log
    raw_path = output_dir / "extracted_raw.json"
    with open(raw_path, "w", encoding="utf-8") as f:
        json.dump(raw_extractions, f, ensure_ascii=False, indent=2)
    print(f"Wrote raw extraction log to {raw_path}")

    # Write summary
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "input_dir": str(input_dir),
        "report_files": len(report_files),
        "base_samples": len([s for s in all_samples if not s.is_synthetic]),
        "synthetic_samples": len([s for s in all_samples if s.is_synthetic]),
        "total_samples": len(all_samples),
        "target_met": len(all_samples) >= args.count,
        "total_geometry": total_geometry,
        "total_parameters": total_params,
        "total_results": total_results,
        "domains": {},
        "mission_ids": [],
    }

    for s in all_samples:
        d = s.domain
        summary["domains"][d] = summary["domains"].get(d, 0) + 1
        if s.mission_id:
            summary["mission_ids"].append(s.mission_id)

    summary_path = output_dir / "summary.json"
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(f"Wrote summary to {summary_path}")

    # Print domain distribution
    print("\nDomain distribution:")
    for domain, count in sorted(summary["domains"].items(), key=lambda x: -x[1]):
        print(f"  {domain}: {count}")

    print(f"\n✅ Done. Target {'met' if summary['target_met'] else 'NOT met'}: {len(all_samples)}/{args.count}")


if __name__ == "__main__":
    main()
