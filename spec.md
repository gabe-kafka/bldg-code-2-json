---
title: "bldg-code-2-json — Spec"
date: 2026-03-29
status: active
---

# bldg-code-2-json

Convert building code PDFs into machine-readable JSON — a single source of truth for agent-driven code compliance tools.

## Problem

Building codes are published as PDFs containing prose provisions, tables, formulas, and empirical charts. None of this is queryable by software. Engineers and agents both waste time navigating them manually.

## Goal

An agent should be able to answer any building code question using only the extracted data and get the same answer a licensed engineer would get reading the PDF.

## Scope

- **First target:** ASCE 7-22 Chapter 26 (Wind Loads — General Requirements)
- **Designed to generalize** to any chapter of any code (IBC, ACI 318, ASCE 7, AISC 360, etc.)

## Architecture

```
PDF → Docling (document structure) + pdfplumber (font metadata)
  → deterministic classification (provision/definition/formula/table/figure/reference)
  → JSON elements
  → benchmark (coverage + fidelity + structure)
```

### Why PDF-direct, not vision

PDFs are structured files with an internal text layer. Every character has an exact position, font name, and size. The pipeline reads this structure directly:

- **Docling** (IBM) — reads PDF structure, handles two-column layout, detects tables and figures, produces text in correct reading order
- **pdfplumber** — character-level extraction with font metadata (bold detection for heading/definition classification)
- **No vision models** in the extraction path. No OCR. No rendered images. Characters are exactly what the PDF contains.

Vision-based approaches (YOLO, Surya, Claude vision, OpenCV) were tested and rejected — they cannot reliably produce precise bounding box coordinates or maintain text fidelity.

## Pipeline

```
python cli.py extract --pdf input/asce7-22.pdf --standard "ASCE 7-22" --chapter 26
```

### Step 1: Docling extraction
Docling reads the PDF and produces a structured document model: text items with bounding boxes in correct reading order, tables with cell structure, pictures with positions.

### Step 2: Text cleanup
- Ligature normalization (fi, fl, ff → ASCII)
- Line-break hyphen repair (da- tabase → database)
- HTML entity decoding (&amp; → &)

### Step 3: Classification
Deterministic rules using font metadata and text patterns:
- Bold + section number → heading
- ALL-CAPS TERM + colon → definition
- Contains "shall" / "is permitted" → provision
- Equation number pattern `(26.X-Y)` → formula
- Docling table detection → table
- Docling picture detection → figure
- External standard citation → reference

### Step 4: Enrichment
- Extract equation elements from inline equation numbers in text
- Extract missing section headings from embedded section numbers
- Resolve figure captions from nearby text elements

### Step 5: Benchmark
Three-axis measurement against PDF ground truth:
- **Coverage**: every section/table/figure/equation in the PDF has a corresponding element
- **Fidelity**: extracted text matches PDF text layer character-for-character
- **Structure**: elements in correct order, IDs unique, fields populated

## Element Types

| Type | What it is | How it's stored |
|------|-----------|----------------|
| `provision` | A rule with conditions and thresholds | Exact `rule` text |
| `definition` | Vocabulary definition | Exact `term` and `definition` |
| `formula` | Mathematical equation | Expression text with equation number |
| `table` | Tabular lookup data | Columns and rows from PDF table markup |
| `figure` | Diagram, chart, or map | Caption + page reference (linked, not digitized) |
| `reference` | Pointer to external standard | Exact citation text |

## Repo Structure

```
bldg-code-2-json/
├── goal.md                        # what success looks like
├── ontology.md                    # what the JSON represents
├── spec.md                        # this file
├── schema/
│   ├── element.schema.json        # JSON Schema for validation
│   └── gold/                      # gold standard reference elements
├── extract/
│   ├── hybrid_v2.py               # main pipeline: Docling + pdfplumber
│   ├── plumber_pipeline.py        # pdfplumber-only pipeline (alternative)
│   ├── hybrid_pipeline.py         # Docling + pdfplumber patching (deprecated)
│   ├── benchmark.py               # three-axis accuracy measurement
│   ├── tune.py                    # iterative tuning harness
│   ├── pdf_arena.py               # parser comparison tool
│   ├── segmenter.py               # Claude vision segmenter (deprecated)
│   ├── pdf_renderer.py            # PDF → page images (for review tools)
│   ├── post_processor.py          # deterministic cleanup
│   └── gold_standard.py           # gold element management
├── qc/
│   ├── schema_validator.py        # JSON Schema validation
│   ├── calibration.py             # gold standard comparison
│   └── compare.py                 # cross-model comparison
├── review/
│   ├── classify.html              # region classification tool
│   ├── classify_server.py         # classification server
│   ├── server.py                  # extraction review tool
│   ├── index.html                 # review UI
│   ├── merge.py                   # human decision merger
│   ├── docling_viewer.py          # Docling result viewer
│   └── docling_view.html          # Docling viewer UI
├── cli.py                         # Click CLI
├── input/                         # source PDFs (gitignored)
└── output/
    ├── runs/                      # extraction outputs + viewers
    └── qc/                        # benchmark reports
```

## CLI

```bash
# Extract a chapter (main command)
python cli.py extract --pdf input/asce7-22.pdf --standard "ASCE 7-22" --chapter 26

# Validate extraction
python cli.py validate --file output/runs/final-ch26.json

# Render PDF pages to images (for review tools)
python cli.py render --pdf input/asce7-22.pdf --standard "ASCE 7-22" --chapter 26

# Launch classification review tool
python cli.py classify --pages-dir output/pages/asce722-ch26

# Compare two extraction runs
python cli.py compare --run-a run1.json --run-b run2.json
```

## Current Results

ASCE 7-22 Chapter 26: 594 elements, 94.7% benchmark composite.

| Metric | Score |
|--------|-------|
| Coverage | 88% (69/78 items) |
| Fidelity | 100% (451/451 text match) |
| Structure | 97% |
| **Composite** | **94.7%** |

| Type | Count |
|------|-------|
| provision | 525 |
| definition | 33 |
| formula | 16 |
| table | 9 |
| figure | 11 |

## Key Findings

1. **PDF structure > vision**: Reading the PDF text layer directly produces character-perfect text. Vision models (YOLO, Surya, Claude) cannot reliably produce precise coordinates or maintain text fidelity.

2. **Docling is the best PDF parser tested**: Compared against pdfplumber, PyMuPDF, pymupdf4llm, and font-semantic heuristics. Docling handles two-column layout, table detection, and figure detection better than alternatives.

3. **pdfplumber's extract_text() interleaves columns**: Its raw character data is perfect, but its text-flow algorithm merges columns incorrectly. Docling solves this.

4. **Ligatures are the main text quality issue**: PDFs encode fi/fl/ff as Unicode ligature characters. The pipeline normalizes these to ASCII.

5. **The benchmark caught its own bugs**: The fidelity checker was initially using pdfplumber's interleaved text as ground truth, which penalized correct extractions. Switching to Docling text as ground truth revealed 100% fidelity.
