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

## Detection Principle

Every element type is detected from the PDF's own text layer using **bold font labels** as the authoritative markers. The PDF tells us what is a table, what is a figure, what is a section heading — we read what it says, not guess from pixels.

- **Bold "Table 26.6-1"** in the text layer = there is a table. The bold font flag (`.B` suffix in the font name, or bit 16 in PyMuPDF flags) distinguishes actual captions from inline text references like "see Table 26.6-1".
- **Bold "Figure 26.5-1A"** in the text layer = there is a figure. Same bold-font distinction.
- **Bold "26.1.1 Scope"** = section heading. Bold font + section number pattern.
- **Bold "APPROVED:"** = definition. Bold font + ALL-CAPS + colon.
- **"(26.10-1)"** in parentheses = equation number. The expression precedes the number.
- **"shall", "is permitted"** = provision. Regulatory language markers.

The authoritative list of tables and figures in a chapter is the set of distinct bold "Table" and "Figure" labels found in the PDF text layer. If the PDF says **Table 26.6-1** in bold, there must be a corresponding table element. If the PDF says **Figure 26.5-1A** in bold, there must be a corresponding figure element. This is a hard requirement — missing a bold-labeled item is a coverage failure.

This is more rigorous than relying on Docling's structural detection alone, which can miss vector-drawn diagrams and borderless tables.

## Element Types

Six types. Classification is deterministic, based on bold font labels and text patterns.

| Type | How Detected | Data |
|------|-------------|------|
| `provision` | Regulatory language ("shall", "is permitted") | Exact `rule` text |
| `definition` | Bold ALL-CAPS TERM + colon | Exact `term` and `definition` |
| `formula` | Equation number `(X.Y-Z)` in parentheses | Expression text |
| `table` | **Bold "Table X.Y-Z" label** in PDF text | Columns + rows from PDF table markup |
| `figure` | **Bold "Figure X.Y-Z" label** in PDF text | Caption + page + linked PNG |
| `reference` | External standard citation (ASTM, ANSI, etc.) | Exact citation text |

### Provisions

The actual code language. Shall/shall not, conditions, limits, exceptions. Detected by regulatory language markers in the text. `data.rule` preserves the code wording exactly as it appears in the PDF.

### Definitions

Vocabulary the code defines for itself. Detected by bold ALL-CAPS TERM followed by colon + definition text. `data.term` and `data.definition` preserve the source text exactly.

### Formulas

Mathematical equations. Detected by equation number patterns `(X.Y-Z)` in parentheses in the text. `data.expression` contains the equation text preceding the number. Parameters are extracted when identifiable.

### Tables

Tabular lookup data. **Detected by bold "Table X.Y-Z" labels in the PDF text layer.** The bold font flag distinguishes the actual table caption from inline references. Table content (columns and rows) is extracted from PDF table markup via Docling.

The authoritative list of tables in a chapter is the set of distinct bold "Table" labels. If the text says **Table 26.6-1** in bold, there must be a corresponding table element — this is a hard coverage requirement.

### Figures

Diagrams, charts, flowcharts, contour maps, geometry illustrations. **Detected by bold "Figure X.Y-Z" labels in the PDF text layer.** This catches every figure including sub-variants (26.5-1A, 26.5-1B, etc.) that image-based detection may group together.

Figures are **linked, not digitized** — the extraction provides:
- `data.description` — the bold figure caption from the PDF
- `data.source_pdf_page` — page number for reference
- `cross_references` — links to the structured elements that contain the computable version

The image itself can be exported as a cropped PNG for downstream vision queries.

### References

Pointers to external standards and documents. Detected by citation patterns (ASTM, ANSI, CAN/CSA, etc.) in the text. `data.target` preserves the exact citation text.

## Fidelity Standard

All text content is extracted from the PDF text layer, not from OCR or vision models.

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
- **data** — Type-specific payload (rule, term+definition, expression, columns+rows, description)
- **cross_references** — Links to other elements by ID
- **metadata** — extracted_by, qc_status, qc_notes

## Quality Model

Three-axis benchmark measured against ground truth:

- **Coverage** — Does every bold-labeled item in the PDF (section, table, figure, equation) have a corresponding element? Ground truth: scan PDF text layer for bold "Table", "Figure", section number, and equation number labels.
- **Fidelity** — Does the extracted text match the PDF's text layer character-for-character? Ground truth: Docling's text output (correct reading order for two-column layout).
- **Structure** — Are elements in correct order? Are IDs unique? Are required fields populated?

Coverage is a hard requirement: every bold-labeled table and figure must have a corresponding element. Missing one is a bug.
