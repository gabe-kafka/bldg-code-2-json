---
title: "bldg-code-2-json — Spec"
date: 2026-03-28
status: draft
---

# bldg-code-2-json

Convert building code PDFs into machine-readable JSON — a single source of truth for agent-driven code compliance tools.

## Problem

Building codes are published as PDFs containing prose provisions, tables, formulas, and empirical charts. None of this is queryable by software. Engineers and agents both waste time navigating them manually.

## Goal

A pipeline that takes a building code PDF chapter as input and produces a structured JSON file as output, with QC tooling to verify completeness and accuracy.

## Scope

- **First target:** ASCE 7-22 Chapter 26 (Wind Loads — General Requirements)
- **Designed to generalize** to any chapter of any code (IBC, ACI 318, ASCE 7, AISC 360, etc.)

## Universal Data Model

Everything is points. All information types — provisions, tables, formulas, charts — are normalized into a single schema.

### Element Types

| Type | What it is | How it's stored |
|------|-----------|----------------|
| `provision` | A rule with conditions and thresholds | Condition tree with discrete threshold values |
| `table` | Tabular lookup data | Array of row objects |
| `formula` | Mathematical relationship | Sampled point arrays across valid input range |
| `figure` | Chart or empirical curve | Digitized (x, y) coordinate pairs |
| `reference` | Pointer to external data (maps, APIs) | URL or API spec |

### JSON Schema (per element)

```json
{
  "id": "ASCE7-22-26.5-1",
  "type": "table | provision | formula | figure | reference",
  "source": {
    "standard": "ASCE 7-22",
    "chapter": 26,
    "section": "26.5",
    "page": null
  },
  "title": "Directional Factor Kd",
  "description": "Optional plain-language summary",
  "data": { },
  "cross_references": ["ASCE7-22-26.2-1", "ASCE7-22-26.6"],
  "metadata": {
    "extracted_by": "auto | manual",
    "qc_status": "pending | passed | failed",
    "qc_notes": null
  }
}
```

### `data` field by type

**table:**
```json
{
  "columns": [
    { "name": "structure_type", "unit": null },
    { "name": "Kd", "unit": "dimensionless" }
  ],
  "rows": [
    { "structure_type": "Main Wind Force Resisting System", "Kd": 0.85 },
    { "structure_type": "Components and Cladding", "Kd": 0.85 }
  ]
}
```

**figure (empirical curve):**
```json
{
  "x_axis": { "name": "effective_wind_area", "unit": "ft2", "scale": "log" },
  "y_axis": { "name": "GCpf", "unit": "dimensionless", "scale": "linear" },
  "curves": [
    {
      "label": "Zone 1 positive",
      "points": [[10, 0.4], [50, 0.35], [100, 0.3], [500, 0.25]],
      "interpolation": "linear"
    }
  ]
}
```

**formula (sampled):**
```json
{
  "expression": "Kz = 2.01 * (z / zg) ^ (2 / alpha)",
  "parameters": {
    "z": { "unit": "ft", "range": [0, 1500] },
    "zg": { "unit": "ft", "source": "table_26.11-1" },
    "alpha": { "unit": "dimensionless", "source": "table_26.11-1" }
  },
  "samples": {
    "exposure_B": [[0, 0.57], [15, 0.57], [30, 0.70], [60, 0.81], [100, 0.90], [200, 1.04], [500, 1.27]],
    "exposure_C": [[0, 0.85], [15, 0.85], [30, 0.98], [60, 1.09], [100, 1.18], [200, 1.31], [500, 1.51]],
    "exposure_D": [[0, 1.03], [15, 1.03], [30, 1.12], [60, 1.22], [100, 1.30], [200, 1.42], [500, 1.59]]
  }
}
```

**provision:**
```json
{
  "rule": "Buildings with mean roof height h > 60 ft shall use exposure defined in Section 26.7.3",
  "conditions": [
    { "parameter": "mean_roof_height", "operator": ">", "value": 60, "unit": "ft" }
  ],
  "then": "use Section 26.7.3",
  "else": "use Section 26.7.4",
  "exceptions": []
}
```

**reference:**
```json
{
  "target": "USGS Seismic Design Web Service",
  "url": "https://earthquake.usgs.gov/ws/designmaps/",
  "parameters": ["latitude", "longitude", "risk_category", "site_class"]
}
```

## Pipeline

```
PDF ──► Extraction ──► Raw JSON ──► QC ──► Validated JSON
```

### Step 1: Extraction (`extract/`)
- Input: PDF file + chapter range
- Process: Parse text, tables, figures into raw element JSON
- LLM-assisted: use Claude to classify and structure each element
- Output: `output/raw/{standard}-{chapter}.json`

### Step 2: QC (`qc/`)
- **Schema validation:** every element conforms to the JSON schema
- **Completeness check:** every section/table/figure in the PDF has a corresponding element
- **Spot check:** sample N elements, compare against PDF, report accuracy
- **Cross-reference check:** all `cross_references` point to existing element IDs
- Output: `output/qc/{standard}-{chapter}-qc-report.json`

### Step 3: Validated output
- Elements that pass QC move to `output/validated/{standard}-{chapter}.json`
- Failed elements get flagged for manual review

## Repo Structure

```
bldg-code-2-json/
├── spec.md                  # this file
├── schema/
│   └── element.schema.json  # JSON Schema for validation
├── extract/
│   ├── pdf_parser.py        # PDF text/table extraction
│   ├── figure_digitizer.py  # chart → point arrays
│   └── llm_structurer.py    # LLM-assisted classification + structuring
├── qc/
│   ├── schema_validator.py  # validate against JSON Schema
│   ├── completeness.py      # check coverage vs PDF TOC
│   └── spot_check.py        # sample + compare
├── input/                   # source PDFs (gitignored)
├── output/
│   ├── raw/
│   ├── qc/
│   └── validated/
├── requirements.txt
└── README.md
```

## Tech Stack

- Python 3.11+
- `pdfplumber` or `pymupdf` — PDF text and table extraction
- `anthropic` — Claude API for LLM-assisted structuring
- `jsonschema` — validation
- `click` — CLI interface

## CLI Interface

```bash
# Extract a chapter
bldg-code-2-json extract --pdf input/asce7-22.pdf --chapter 26 --standard "ASCE 7-22"

# Run QC on extracted output
bldg-code-2-json qc --file output/raw/asce7-22-ch26.json

# Full pipeline
bldg-code-2-json run --pdf input/asce7-22.pdf --chapter 26 --standard "ASCE 7-22"
```

## Acceptance Criteria

- [ ] Extract all provisions, tables, formulas, and figures from ASCE 7-22 Ch. 26
- [ ] Every element passes JSON Schema validation
- [ ] Completeness ≥ 95% (measured against section headings + table/figure numbers in PDF)
- [ ] Spot-check accuracy ≥ 90% on sampled elements
- [ ] Cross-references resolve to valid element IDs
- [ ] Pipeline runs end-to-end with a single CLI command
