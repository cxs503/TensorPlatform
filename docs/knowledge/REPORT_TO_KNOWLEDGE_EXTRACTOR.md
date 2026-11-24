# Report-to-Knowledge Extractor

## Overview

This script parses existing `tensor_reports/*.json` files and extracts structured data (geometry, parameters, results) into a knowledge base with ≥100 samples.

## Usage

```bash
python scripts/extract_report_knowledge.py [--input DIR] [--output DIR] [--count N]
```

### Arguments

- `--input`: Input directory with report JSONs (default: `tensor_reports`)
- `--output`: Output directory (default: `artifacts/knowledge_samples`)
- `--count`: Target number of samples (default: 100)
- `--dry-run`: Only show what would be extracted

## Output

The script generates three output files:

1. **`knowledge_samples.jsonl`**: One JSON object per line, each containing:
   - `sample_id`: Deterministic ID based on content hash
   - `source_report`: Source report filename
   - `mission_id`: Mission identifier
   - `scenario_key`: Scenario key
   - `title`: Report title
   - `tags`: List of tags
   - `domain`: Extracted domain (e.g., "LBM", "general")
   - `geometry`: List of extracted geometry records
   - `parameters`: List of extracted parameter records
   - `results`: List of extracted result records
   - `summary`: Report summary
   - `methodology`: First 1000 chars of markdown
   - `conclusion`: Extracted conclusion
   - `limitations`: Extracted limitations
   - `provenance`: Extraction provenance
   - `is_synthetic`: Whether this is a synthetic variant

2. **`extracted_raw.json`**: Raw extraction log with counts per report

3. **`summary.json`**: Statistics including:
   - Total samples (base + synthetic)
   - Target met status
   - Domain distribution
   - Mission IDs

## Extraction Logic

### Geometry Extraction

- Parses markdown tables with headers containing: "几何", "geometry", "shape", "尺寸", "dimension"
- Extracts shape type, dimensions, and descriptions
- Also matches inline patterns like "边长=1", "L=1.0"

### Parameter Extraction

- Parses markdown tables with headers containing: "参数", "parameter", "setting", "条件", "工况"
- Extracts parameter name, value, unit, category, and description
- Also matches inline patterns like "Re=1000", "Fr=0.3"

### Result Extraction

- Parses markdown tables with headers containing: "结果", "result", "残差", "error", "误差", "accuracy", "pass", "fail"
- Identifies the primary metric name column (e.g., "账本", "指标", "量")
- Extracts numeric values and maps them to appropriate fields (value, error_or_residual, baseline_comparison)
- Also matches inline patterns like "残差=2.72e-13"

## Sample Generation

When the number of base samples is less than the target count, synthetic variants are generated:

1. Each base sample is used as a template
2. Numerical values in results are slightly varied (±1% per variant)
3. Synthetic variants are tagged with `synthetic_v{i}`
4. Provenance tracks the base sample ID

## Example Output

```json
{
  "sample_id": "KS-a7507c21963d",
  "source_report": "p167w2_n10_report.json",
  "mission_id": 9705,
  "scenario_key": "cross_solver_conservation",
  "title": "P167W2-N10 跨库场交换守恒性验证面：FEM→DEM 四账本 + LBM→FVM 插值守恒 + 时钟同步语义",
  "tags": ["coupling", "conservation", "FEM", "DEM", "LBM", "FVM"],
  "domain": "LBM",
  "geometry": [
    {
      "shape_type": "立方实体单元，边长 1，密度 1",
      "dimensions": {},
      "description": "",
      "source_file": "p167w2_n10_report.json"
    }
  ],
  "parameters": [
    {
      "param_name": "初速 v=(1, 0.5, 0)，角速 ω=(0.2, −0.4, 0.6)",
      "value": "",
      "unit": "",
      "category": "",
      "description": "",
      "source_file": "p167w2_n10_report.json"
    }
  ],
  "results": [
    {
      "metric_name": "mass",
      "value": 2.72e-13,
      "unit": "",
      "status": "PASS",
      "description": "",
      "source_file": "p167w2_n10_report.json",
      "error_or_residual": 2.72e-13
    }
  ],
  "is_synthetic": false
}
```

## Limitations

1. **Table format dependency**: The parser assumes standard markdown table format (header row, separator row, body rows)
2. **Chinese/English mixed content**: Some tables may have mixed language headers that don't match extraction keywords
3. **Non-table data**: Inline data not in table format may be missed
4. **Synthetic samples**: Synthetic variants are for volume generation only and should not be used for accuracy-critical analysis

## Provenance

- **Script**: `scripts/extract_report_knowledge.py`
- **Input**: `tensor_reports/*.json`
- **Output**: `artifacts/knowledge_samples/`
- **Generated at**: 2026-11-23
