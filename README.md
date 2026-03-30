# bldg-code-2-json

Extract building code PDFs into structured, machine-readable JSON.

**Goal:** An agent answers a building code question using only the extracted data and gets the same answer a licensed engineer would get reading the PDF.

## How It Works

```
PDF → Docling (reads PDF structure directly) → deterministic classification → JSON
```

No vision models. No OCR. No rendered images. The pipeline reads the PDF's internal text layer — every character with its exact position and font metadata. Docling handles two-column layout, table detection, and figure detection. pdfplumber provides character-level font data for classification (bold = heading, ALL-CAPS + colon = definition).

## Quick Start

```bash
pip install -r requirements.txt

# Extract a chapter
python cli.py extract --pdf input/asce7-22.pdf --standard "ASCE 7-22" --chapter 26

# Validate
python cli.py validate --file output/runs/asce722-ch26-hybrid.json
```

## Current Results

ASCE 7-22 Chapter 26: **594 elements, 94.7% benchmark composite.**

| Metric | Score |
|--------|-------|
| Coverage | 88% |
| Fidelity | 100% |
| Structure | 97% |

## Element Types

| Type | Count | What it is |
|------|-------|-----------|
| provision | 525 | Code rules — "shall", conditions, exceptions |
| definition | 33 | Vocabulary — TERM: definition text |
| formula | 16 | Equations with expression text |
| table | 9 | Tabular data with columns and rows |
| figure | 11 | Linked diagrams with captions |

## Key Finding

PDFs are structured files, not images. Reading the text layer directly produces character-perfect extraction. Vision-based approaches (YOLO, Surya, Claude vision) were tested and cannot match the accuracy of direct PDF parsing.

See [goal.md](goal.md), [ontology.md](ontology.md), and [spec.md](spec.md) for full documentation.
