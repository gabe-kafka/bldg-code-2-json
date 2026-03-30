# Building Code JSON Ontology

## What This Is

A structured, machine-readable representation of a building code PDF. Every meaningful piece of content is extracted into JSON — provisions as rules, equations as expressions, tables as queryable data, definitions as vocabulary, figures as linked images with pointers to the computable elements they illustrate.

## Why It Exists

Building codes are locked in PDFs. Engineers navigate them manually. Software cannot query them. AI agents cannot reason over them. This JSON format makes every provision, formula, table, and definition addressable, queryable, and computable.

## Extraction Method

Content is extracted by reading the PDF's internal structure directly — not by rendering pages to images. PDFs contain a text layer with exact character positions, font metadata, table markup, and embedded images. The pipeline reads this structure using Docling (IBM's document parser) for layout and reading order, enriched with pdfplumber for character-level font metadata.

This approach is fundamentally more accurate than vision-based extraction because:
- Characters come from the PDF text layer, not OCR
- Two-column reading order is resolved from coordinates, not guessed
- Tables come from PDF table markup, not cell detection
- Font metadata (bold, size) enables deterministic classification

No vision model is involved in text extraction. Vision is only used downstream when an agent needs to interpret a linked figure image.

## Element Types

Six types. Classification into types is deterministic, based on font metadata and text patterns.

| Type | How Detected | Data Precision |
|------|-------------|----------------|
| `provision` | Contains "shall", "is permitted", "must be" | Exact `rule` text from PDF |
| `definition` | ALL-CAPS TERM followed by colon + definition text | Exact `term` and `definition` |
| `formula` | Contains equation number pattern `(26.X-Y)` | Expression text from PDF |
| `table` | Docling table structure detection | Exact columns and rows |
| `figure` | Docling picture detection | Caption + page + linked PNG |
| `reference` | Cites external standards (ASTM, ANSI, etc.) | Exact citation text |

### Provisions

The actual code language. Shall/shall not, conditions, limits, exceptions. Detected by regulatory language markers in the text. `data.rule` preserves the code wording exactly as it appears in the PDF.

### Definitions

Vocabulary the code defines for itself. Detected by the pattern: `BOLD ALL-CAPS TERM: definition text`. `data.term` and `data.definition` preserve the source text exactly.

### Formulas

Mathematical equations. Detected by equation number patterns `(26.X-Y)` in the text. `data.expression` contains the equation text. Parameters are extracted when identifiable.

### Tables

Tabular lookup data. Detected by Docling's table structure parser, which reads PDF table markup and ruled-line grids. `data.columns` and `data.rows` preserve the table content exactly.

### Figures

Diagrams, charts, flowcharts, contour maps, geometry illustrations. Detected by Docling's picture detection. Figures are **linked, not digitized** — the extraction provides:
- `data.figure_type` — classification of what kind of figure
- `data.description` — the figure caption from the PDF
- `data.source_pdf_page` — page number for reference
- `cross_references` — links to the structured elements that contain the computable version of what the figure illustrates

The image itself can be exported as a cropped PNG for downstream vision queries.

### References

Pointers to external standards and documents. Detected by citation patterns (ASTM, ANSI, CAN/CSA, etc.). `data.target` preserves the exact citation text.

## Fidelity Standard

All text content is extracted from the PDF text layer, not from OCR or vision models. The fidelity standard is:

- **Exact text**: Every character comes from the PDF's internal text stream. Ligatures (fi, fl, ff) are normalized to ASCII equivalents. Line-break hyphens are repaired. No paraphrasing, no summarization.
- **Exact structure**: Section numbers, table numbers, equation numbers, and figure numbers are preserved exactly as printed in the source.
- **Exact values**: Table cell values are extracted from PDF table markup. No rounding, no approximation.

## Element Structure

Every element has:

- **id** — Unique identifier: `{STANDARD}-{SECTION}-{PREFIX}{N}`
- **type** — One of: provision, definition, formula, table, figure, reference
- **source** — standard, chapter, section, citation, page number
- **title** — First 200 characters of text or official caption
- **description** — Empty string (non-authoritative, reserved for future use)
- **data** — Type-specific payload (rule, term+definition, expression, columns+rows, figure_type+description)
- **cross_references** — Links to other elements by ID
- **metadata** — extracted_by, qc_status, qc_notes

## Quality Model

Three-axis benchmark measured against ground truth:

- **Coverage** — Does every identifiable item in the PDF (section, table, figure, equation) have a corresponding element? Ground truth: regex scan of the PDF text for section numbers, table numbers, figure numbers, equation numbers.
- **Fidelity** — Does the extracted text match the PDF's text layer character-for-character? Ground truth: Docling's text output (correct reading order for two-column layout).
- **Structure** — Are elements in correct order? Are IDs unique? Are required fields populated?

Current scores for ASCE 7-22 Chapter 26:
- Coverage: 88% (69/78 items)
- Fidelity: 100% (451/451 text match)
- Structure: 97%
- **Composite: 94.7%**
