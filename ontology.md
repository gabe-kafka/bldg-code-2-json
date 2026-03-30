# Building Code JSON Ontology

## What This Is

A structured, machine-readable representation of a building code PDF. Every meaningful piece of content is extracted into JSON — provisions as rules, equations as expressions, tables as queryable data, definitions as vocabulary, figures as linked images with pointers to the computable elements they illustrate.

## Why It Exists

Building codes are locked in PDFs. Engineers navigate them manually. Software cannot query them. AI agents cannot reason over them. This JSON format makes every provision, formula, table, and definition addressable, queryable, and computable.

## Extraction Method

Content is extracted by reading the PDF's internal structure directly — not by rendering pages to images. PDFs contain a text layer with exact character positions, font metadata, table markup, and embedded images. The pipeline reads this structure using Docling (IBM's document parser) for layout and reading order, enriched with PyMuPDF for character-level font metadata (bold detection).

No vision model is involved in text extraction. Vision is only used downstream when an agent needs to interpret a linked figure image.

## Detection Principle

Every element type is detected from the PDF's own text layer using **bold font labels** as the authoritative markers. The PDF tells us what every content block is — we read what it says.

Building code PDFs use a consistent typography system where every content block is led by a bold label. The bold font flag (`.B` or `.BI` suffix in the font name, or bit 16 in PyMuPDF flags) distinguishes actual captions and headings from inline text references.

### Bold Label Taxonomy

The complete set of bold label patterns in a building code PDF:

| Pattern | Type | Example |
|---------|------|---------|
| `26.X.Y Title` | heading | **26.1.1 Scope** |
| `ALL-CAPS TERM:` | definition | **APPROVED:** Acceptable to... |
| `Table 26.X-Y.` | table | **Table 26.6-1.** Wind Directionality Factor |
| `Figure 26.X-Y.` | figure | **Figure 26.5-1A.** Basic wind speeds for... |
| `(X.Y-Z)` after expression | equation | qz = 0.00256... **(26.10-1)** |
| `User Note:` | user_note | **User Note:** A building or other structure... |
| `EXCEPTION:` | exception | **EXCEPTION:** Glazing located more than 60 ft... |
| `ASTM/ANSI/CAN...` | reference | **ASTM E1886,** Standard Test Method for... |
| `ALL-CAPS TOPIC` | heading | **PROCEDURES**, **DEFINITIONS**, **EXPOSURE** |
| `Surface Roughness X.` | sub_definition | **Surface Roughness B.** Urban and suburban... |
| `Exposure X.` | sub_definition | **Exposure B.** For buildings with h <= 30 ft... |
| `CHAPTER X` | heading | **CHAPTER 26** |
| Page numbers, footers | skip | **261**, **STANDARD ASCE/SEI 7-22** |

If the PDF has a bold label, there must be a corresponding element. Missing a bold-labeled item is a coverage failure.

## Element Types

Nine types. Classification is deterministic, based on bold font labels and text patterns.

| Type | How Detected | Data |
|------|-------------|------|
| `heading` | Bold section number or ALL-CAPS topic | Section number + title text |
| `provision` | Regulatory language ("shall", "is permitted") | Exact `rule` text |
| `definition` | Bold ALL-CAPS TERM + colon | Exact `term` and `definition` |
| `formula` | Equation number `(X.Y-Z)` in parentheses | Expression text |
| `table` | Bold "Table X.Y-Z" label | Columns + rows from PDF table markup |
| `figure` | Bold "Figure X.Y-Z" label | Caption + page (linked, not digitized) |
| `reference` | Bold external standard name (ASTM, ANSI, CAN/CSA) | Exact citation text |
| `user_note` | Bold "User Note:" label | Informational text (not enforceable code) |
| `exception` | Bold "EXCEPTION:" label | Exception text modifying preceding provision |

### Headings

Section headings and topic markers. Detected by bold section numbers (`26.1.1 Scope`) or bold ALL-CAPS topic names (`PROCEDURES`, `DEFINITIONS`). These are structural markers — they define what section the following content belongs to.

### Provisions

The actual code language. Shall/shall not, conditions, limits, exceptions. Detected by regulatory language markers in the text. `data.rule` preserves the code wording exactly. This is law.

### Definitions

Vocabulary the code defines for itself. Detected by bold ALL-CAPS TERM followed by colon. `data.term` and `data.definition` preserve the source text exactly. Sub-definitions (Surface Roughness B, Exposure C) are also definitions with the parent term as context.

### Formulas

Mathematical equations. Detected by equation number patterns `(X.Y-Z)` in parentheses. `data.expression` contains the equation text preceding the number.

### Tables

Tabular lookup data. Detected by bold "Table X.Y-Z" labels. Table content extracted from PDF table markup via Docling. `data.columns` and `data.rows` preserve the table content exactly.

### Figures

Diagrams, charts, flowcharts, contour maps. Detected by bold "Figure X.Y-Z" labels including sub-variants (A, B, C, D). Figures are linked, not digitized — caption + page reference.

### References

Pointers to external standards. Detected by bold standard names (ASTM E1886, ANSI/DASMA 115, CAN/CSA A123.21). `data.target` preserves the exact citation text.

### User Notes

Informational guidance that is not enforceable code. Detected by bold "User Note:" label. These help engineers understand intent but are not provisions.

### Exceptions

Modifications to the preceding provision. Detected by bold "EXCEPTION:" label. Each exception modifies the rule that immediately precedes it.

## Fidelity Standard

All text content is extracted from the PDF text layer, not from OCR or vision models.

- **Exact text**: Every character comes from the PDF's internal text stream. Ligatures (fi, fl, ff) are normalized to ASCII equivalents. Line-break hyphens are repaired.
- **Exact structure**: Section numbers, table numbers, equation numbers, and figure numbers are preserved exactly as printed.
- **Exact values**: Table cell values are extracted from PDF table markup.

## Element Structure

Every element has:

- **id** — Unique identifier: `{STANDARD}-{SECTION}-{PREFIX}{N}`
- **type** — One of the nine types above
- **source** — standard, chapter, section, citation, page number
- **title** — First 200 characters of text or official caption
- **description** — Empty string (reserved for future use)
- **data** — Type-specific payload
- **cross_references** — Links to other elements by ID
- **metadata** — extracted_by, qc_status, qc_notes

## Quality Model

Three-axis benchmark measured against ground truth:

- **Coverage** — Does every bold-labeled item in the PDF have a corresponding element? Ground truth: scan PDF text layer for bold labels.
- **Fidelity** — Does the extracted text match the PDF's text layer character-for-character?
- **Structure** — Are elements in correct order? Are IDs unique? Are required fields populated?

Coverage is a hard requirement: every bold-labeled table, figure, section, equation, definition, reference, user note, and exception must have a corresponding element.
