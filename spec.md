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

For text-bearing elements, accuracy means preserving the building code's wording, equations, symbols, numbers, and citations as exactly as possible in the authoritative fields. Some helper fields are intentionally derived structure rather than verbatim source text. Figures are the exception: they are represented descriptively rather than as exact wording or precise digitization.

Official source identifiers must also be preserved wherever they exist. Exact section numbers such as `26.2.1` should be captured in `source.section`, printed item labels such as `Eq. (26.10-1)` or `Table 26.10-1` should be captured in `source.citation`, and canonical element `id` values should reuse those identifiers when available.

## Scope

- **First target:** ASCE 7-22 Chapter 26 (Wind Loads — General Requirements)
- **Designed to generalize** to any chapter of any code (IBC, ACI 318, ASCE 7, AISC 360, etc.)

## Universal Data Model

Every content region is classified into one of three tiers (see ontology.md for full rationale):

- **Structured** — computable content extracted into full JSON with exact authoritative data
- **Linked** — non-computable content exported as a cropped PNG with metadata and reference pointers
- **Skipped** — page furniture dropped entirely

All structured information types — provisions, tables, formulas, definitions, references — are normalized into a single schema with exact authoritative payloads. Figures are linked: exported as images with a one-line description and cross-references to the structured elements that contain the computable version.

### Element Types

| Type | Tier | What it is | How it's stored |
|------|------|-----------|----------------|
| `provision` | structured | A rule with conditions and thresholds | Exact `rule` plus derived structured logic |
| `table` | structured | Tabular lookup data | Exact row/column text and values |
| `formula` | structured | Mathematical relationship | Exact equation/expression text plus parameters; optional derived samples |
| `definition` | structured | Vocabulary definition | Exact `term` and `definition`; derived helpers allowed |
| `reference` | structured | Pointer to external data (maps, APIs) | Exact cited target plus normalized helper metadata |
| `figure` | linked | Diagram, chart, or map | Exported PNG + description + cross-references |

### JSON Schema (per element)

```json
{
  "id": "ASCE7-22-26.5-1",
  "type": "table | provision | formula | figure | reference | definition",
  "classification": "structured | linked",
  "source": {
    "standard": "ASCE 7-22",
    "chapter": 26,
    "section": "26.5",
    "citation": "Section 26.5",
    "page": null
  },
  "title": "Directional Factor Kd",
  "description": "Optional plain-language summary; non-authoritative",
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

**figure (linked):**
```json
{
  "figure_type": "xy_chart",
  "description": "External pressure coefficients GCpf vs effective wind area for Zone 1, showing positive and negative curves on log-linear axes.",
  "image": "figures/ASCE7-22-30.3-F30.3-1.png",
  "referenced_by": ["ASCE7-22-30.3-P1"],
  "source_pdf_page": 312
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
`expression` and parameter notation are exact source content. `samples` are derived aids when present.

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
`rule` is the exact authoritative code text. `conditions`, `then`, `else`, and `exceptions` are structured derivatives of that exact text.

**reference:**
```json
{
  "target": "USGS Seismic Design Web Service",
  "url": "https://earthquake.usgs.gov/ws/designmaps/",
  "parameters": ["latitude", "longitude", "risk_category", "site_class"]
}
```
`target` is exact citation text. `url` and `parameters` are helper metadata and may be normalized.

## Pipeline

```
PDF ──► Render ──► Segment ──► Classify (human) ──► Extract ──► Review (human) ──► QC ──► Validated JSON
```

### Step 1: Render
- Input: PDF file + chapter range
- Process: Render pages as PNG images at configurable DPI
- Output: `output/pages/{standard}-ch{chapter}/page-NNN.png`

### Step 2: Segment
- Input: Page images
- Process: Vision model detects content regions, proposes region type (text_block, table, equation, figure) and tier classification (structured, linked, skipped)
- Output: Bounding boxes with proposed classifications

### Step 3: Classify (human checkpoint)
- The user reviews proposed classifications overlaid on each page image
- Accepts, overrides, splits, merges, or adds regions
- Confirms before extraction proceeds

### Step 4: Extract
- Each confirmed region is cropped and routed to a type-specific extractor
- Text blocks: pdfplumber text extraction + LLM structuring → provisions, definitions, references
- Tables: pdfplumber table detection with vision fallback → table elements
- Equations: vision model → formula elements
- Linked figures: export PNG to `figures/`, generate one-line description
- Output: `output/raw/{standard}-{chapter}.json` + `output/figures/`

Identifiers:
- Preserve exact section numbering in `source.section`
- Preserve printed labels such as equation, table, and figure numbers in `source.citation`
- Reuse official identifiers inside canonical element `id` values whenever available

### Step 5: Review (human checkpoint)
- The user reviews extracted elements against the source PDF
- Structured elements: verify authoritative fields match the source exactly
- Linked elements: verify description and cross-references are correct
- Resolves disagreements, corrects errors

### Step 6: QC (`qc/`)
- **Schema validation:** every element conforms to the JSON schema including `classification` field
- **Fidelity check:** exact authoritative fields should match the source wording exactly; derived helper fields should stay faithful; linked elements should have valid image paths
- **Completeness check:** every section/table/figure in the PDF has a corresponding element; linked figures count as extracted (not missing)
- **Spot check:** sample N structured elements, compare against PDF, report accuracy; linked figures are skipped (nothing to verify against)
- **Cross-reference check:** all `cross_references` point to existing element IDs
- Output: `output/qc/{standard}-{chapter}-qc-report.json`

### Step 7: Validated output
- Elements that pass QC move to `output/validated/{standard}-{chapter}.json`
- Failed elements get flagged for manual review

## Repo Structure

```
bldg-code-2-json/
├── spec.md                       # this file
├── ontology.md                   # what the JSON represents
├── schema/
│   ├── element.schema.json       # JSON Schema for validation
│   └── gold/                     # gold standard reference elements
├── extract/
│   ├── pdf_renderer.py           # PDF → page images (PyMuPDF)
│   ├── segmenter.py              # page image → bounding boxes + classification
│   ├── cropper.py                # crop regions from page images
│   ├── text_extractor.py         # text blocks → provisions, definitions, references
│   ├── table_extractor.py        # table regions → table elements
│   ├── equation_extractor.py     # equation regions → formula elements
│   ├── figure_extractor.py       # figure regions → linked elements + PNG export
│   ├── pipeline.py               # orchestrates the full extraction flow
│   ├── post_processor.py         # deterministic cleanup (no API)
│   ├── gold_standard.py          # gold element management
│   └── llm_client.py             # shared Claude API wrapper
├── qc/
│   ├── schema_validator.py       # validate against JSON Schema
│   ├── calibration.py            # gold standard comparison
│   ├── compare.py                # cross-model comparison
│   ├── completeness.py           # check coverage (linked figures count as extracted)
│   └── spot_check.py             # sample structured elements + compare
├── review/
│   ├── server.py                 # human review tool (classification + extraction)
│   ├── merge.py                  # apply human decisions
│   └── index.html                # review UI
├── input/                        # source PDFs (gitignored)
├── output/
│   ├── pages/                    # rendered PDF page images
│   ├── figures/                  # exported linked figure PNGs
│   ├── crops/                    # cropped region images (intermediate)
│   ├── raw/                      # raw extraction JSON
│   ├── runs/                     # model comparison runs
│   ├── qc/                       # QC reports
│   └── validated/                # final validated output
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
# Render pages
python cli.py render --pdf input/asce7-22.pdf --standard "ASCE 7-22" --chapter 26

# Segment and classify (launches review tool for human classification)
python cli.py segment --pages-dir output/pages/asce722-ch26 --standard "ASCE 7-22" --chapter 26

# Extract (runs after classification is confirmed)
python cli.py extract --pdf input/asce7-22.pdf --standard "ASCE 7-22" --chapter 26

# Review extraction (launches review tool for human verification)
python cli.py review --run-a output/runs/asce722-ch26.json ...

# Validate
python cli.py validate --file output/runs/asce722-ch26.json

# Compare two model runs
python cli.py compare --run-a run1.json --run-b run2.json
```

## Acceptance Criteria

- [ ] Extract all provisions, tables, formulas, definitions, and references from ASCE 7-22 Ch. 26
- [ ] All figures are linked with exported PNGs and cross-references
- [ ] Every element passes JSON Schema validation including `classification` field
- [ ] Completeness ≥ 95% (linked figures count as extracted, not missing)
- [ ] Spot-check accuracy ≥ 90% on sampled structured elements
- [ ] Cross-references resolve to valid element IDs
- [ ] Human classifies regions before extraction runs
- [ ] Human reviews extraction output before QC passes
