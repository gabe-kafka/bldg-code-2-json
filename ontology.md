# Building Code JSON Ontology

## What This Is

A complete, structured, machine-readable parallel representation of a building code PDF. Every meaningful region on every page is accounted for — either extracted into full JSON or exported as a linked image with metadata. The JSON is the code in structured form.

## Why It Exists

Building codes are locked in PDFs designed for human reading. Engineers, software tools, and AI agents need structured access to the same information. This JSON format makes every provision, formula, table, definition, and figure addressable, queryable, and computable.

## The Core Problem

Building codes mix two fundamentally different types of content: material an agent can compute with, and material meant to help humans understand. The pipeline must classify every region on every page before attempting extraction.

## Three Tiers

Every content region is classified into one of three tiers. This classification drives what the pipeline does with it.

**Structured** — computable content. Extracted into full JSON with exact authoritative data.

**Linked** — non-computable content the agent should know about but cannot digitize. Exported as a cropped PNG with metadata and reference pointers. No digitized data.

**Skipped** — page furniture with no informational value. Dropped entirely. Not represented in the output.

### Why This Matters

The agent must know what it knows and what it doesn't. A linked figure is not a failure — it is an honest boundary. When a provision says "see Figure 26.8-1," the agent can follow the reference, see that it is illustrative, find the exported PNG, and either send it to a vision model for a specific question or flag it for human review. No garbage data treated as real.

## What It Includes

Every meaningful piece of content in the building code PDF gets an element in the JSON. Content falls into categories based on its computational role and tier:

## Fidelity Standard

For structured elements, the JSON is expected to preserve the building code's wording, symbols, numbers, and citations as exactly as possible.

This is a hard requirement for the authoritative payload in `data`:
- Table headers, row labels, and cell values should match the source exactly.
- Formula and equation expressions, parameter names, and units should match the source exactly.
- Provision text should match the code text exactly, not a paraphrase.
- Definition terms and definition text should match the code text exactly.
- Reference targets and cited standards should match the cited text exactly.

Paraphrases are not acceptable substitutes for code text in these fields. If the PDF says "shall," the JSON should not weaken or restate it as a summary. If the code gives a symbol, threshold, or citation in a specific form, the JSON should preserve that form.

Linked elements have no authoritative data fields. Their value is the exported image, the metadata pointing to it, and the references connecting it to the structured elements that contain the computable content.

### Field-Level Fidelity Policy

The exactness requirement applies to authoritative source fields in structured elements. Some schema fields are intentionally derived helper structure rather than verbatim code text.

| Element type | Classification | Exact authoritative fields | Derived/helper fields | Loose/descriptive fields |
|------|---|-----------------------------|-----------------------|--------------------------|
| `table` | structured | `data.columns`, `data.rows` | none | none |
| `formula` | structured | `data.expression`, parameter names and units in `data.parameters` | `data.samples` when present | none |
| `provision` | structured | `data.rule` | `data.conditions`, `data.then`, `data.else`, `data.exceptions` | none |
| `definition` | structured | `data.term`, `data.definition` | `data.conditions`, `data.exceptions` | none |
| `reference` | structured | `data.target` | `data.url`, `data.parameters` | none |
| `figure` | linked | none | `data.figure_type` | `data.description` |

Rules for interpreting this table:
- Derived/helper fields must be faithful to the exact authoritative text, but they do not have to be verbatim quotations.
- Derived/helper fields must never replace or contradict the exact authoritative field they are derived from.
- `title` should match an official heading or caption when one exists, but it is still secondary to the authoritative payload in `data`.
- `description` is non-authoritative for all element types.
- `source.section` and other location metadata should preserve the code's section numbering exactly where available, because engineers use those citations directly.
- If the source provides an official identifier such as `Section 26.2.1`, `Eq. (26.10-1)`, `Table 26.10-1`, or `Figure 26.1-1`, preserve it explicitly and reuse it in the element `id` instead of inventing a purely local sequence number.

### Structured Content

Content that an agent or program can directly evaluate, query, or compute with.

**Provisions** — The actual code language. Shall/shall not, conditions, limits, exceptions. This is law. "Buildings with h > 60 ft shall use Method X." Captured as structured conditions with operators, values, and units so they can be evaluated programmatically. The authoritative provision text is `data.rule`, which must match the code wording exactly. Structured logic fields (`conditions`, `then`, `else`, `exceptions`) are derived from that exact text.

**Equations** — Formulas you plug numbers into. Wind speed, load combinations, seismic coefficients. These produce answers. `qz = 0.00256 * Kz * Kzt * Kd * Ke * V^2`. The expression, parameter names, and units must match the source exactly. Sample points, if present, are derived aids.

**Tables** — Lookup values that feed equations. Exposure categories, importance factors, velocity pressure coefficients. Given inputs (height, exposure category), return outputs (Kz coefficient). Table text and values must match the source exactly.

**Definitions** — Vocabulary the code defines for itself. "BASIC WIND SPEED, V: Three-second gust speed at 33 ft (10 m) above ground in Exposure C." An agent reading a provision that references "basic wind speed" can look up exactly what that means. `data.term` and `data.definition` must match the source exactly.

**References** — Pointers to external standards, databases, or documents. "See ASTM E1886 for testing procedures." The JSON cannot contain the external document — it records what is referenced, where it's cited, and (if available) a URL. `data.target` must match the source wording exactly. URLs and helper parameters may be normalized.

### Linked Content

Non-computable content the agent should know about but cannot digitize. Exported as a cropped PNG with metadata and reference pointers.

**Figures** — Diagrams, flowcharts, contour maps, XY charts, geometry illustrations, building cross-sections, 3D views, commentary figures. These visualize information that typically exists elsewhere in the code as text, formulas, or tables.

All figures are linked. Even XY charts with labeled axes are difficult to digitize reliably — the precision required for engineering use is higher than vision models can consistently deliver. The honest representation is the image itself plus a one-line description and pointers to the structured elements that contain the computable version of the same information.

Each linked figure element includes:
- A `figure_type` classification (flowchart, contour_map, geometry_diagram, xy_chart, table_image, detail_drawing, photograph, other)
- A natural language `description` of what the figure communicates
- An `image` path to the exported PNG cropped from the source PDF
- `cross_references` linking to the sections, tables, and formulas that contain the computable version
- The source PDF page number

Linked figures are not lesser elements. They are complete elements with honest representation. When a provision says "see Figure 26.8-1," the agent follows the reference, sees it is illustrative, and can send the PNG to a vision model for a specific question or flag it for human review.

### Skipped Content

Page furniture with no informational value: decorative headers, footers, page numbers, watermarks, blank margins. Dropped entirely — not represented in the output. The QC report records a count of skipped regions per page for debugging.

## Human-in-the-Loop Classification

Tier classification is not fully automatic. The pipeline proposes a classification for each detected region, but the human makes the final call. This happens in two phases:

### Phase 1: Classification Review

After the segmenter detects regions on each page, the user sees every bounding box overlaid on the page image with the proposed tier (structured, linked, skipped). The user can:
- Accept the proposal
- Reclassify any region (e.g., promote a figure from linked to structured if they believe it can be digitized, or demote a text block to skipped if it's commentary)
- Split or merge regions the segmenter got wrong
- Add regions the segmenter missed

Only after the user confirms do the type-specific extractors run.

### Phase 2: Extraction Review

After extraction, the user reviews the structured output — the same review tool used for cross-model comparison. For structured elements, they verify the authoritative fields against the source PDF. For linked elements, they verify the description and cross-references make sense.

This two-phase approach means the pipeline never silently produces garbage. The segmenter does the tedious work of finding regions; the human makes the judgment calls; the extractors do the tedious work of digitizing; the human verifies the result.

## Element Types

Six types across two tiers. No subtypes.

| Type | Tier | Computational Role | Data Precision |
|------|------|--------------------|----------------|
| `table` | structured | Directly queryable | Exact — all tabular content |
| `formula` | structured | Directly computable | Mixed — exact equation/expression + parameters; derived samples allowed |
| `provision` | structured | Evaluable as logic | Mixed — exact `rule`; derived structured logic |
| `definition` | structured | Vocabulary reference | Mixed — exact `term` + `definition`; derived helpers |
| `reference` | structured | External pointer | Mixed — exact `target`; normalized helper metadata allowed |
| `figure` | linked | Illustrative context | Image export + description + links |

## Element Structure

Every element, regardless of type or tier, has:

- **id** — Unique identifier: `{STANDARD}-{SECTION}-{SUFFIX}`. When the source provides an official identifier, the suffix should reuse that identifier rather than inventing a purely local sequence number (e.g., `ASCE7-22-26.10-E26.10-1`)
- **type** — One of the six types above
- **classification** — `"structured"` or `"linked"`. Determines what the pipeline extracts and what fidelity standard applies
- **source** — Where in the code this comes from (standard, chapter, section, citation, page); location numbering should match the printed code citations exactly where available
- **title** — Human-readable name; should match an official heading or caption when one exists
- **description** — Optional plain-language summary; non-authoritative and never a substitute for exact code wording in `data`
- **data** — Type-specific structured content (the actual payload). For linked elements, contains `figure_type`, `description`, `image` (path to exported PNG), and `referenced_by`
- **cross_references** — Links to other elements by ID
- **metadata** — Extraction and QC tracking

## Cross-References

Cross-references are the linking layer. They connect elements to each other, creating a traversable graph of the code. A provision references the table it uses for lookup. A formula references the section that defines its parameters. A figure references the provisions and formulas it illustrates.

These links are element IDs, not free text. They enable:
- Navigation ("what table does this provision use?")
- Impact analysis ("if this formula changes, what provisions are affected?")
- Completeness checking ("does every referenced element exist?")

References to other chapters or external standards use the same format but may point to elements not yet extracted.

## Source Identifiers

Engineers rely on printed code identifiers such as section numbers, equation numbers, table numbers, and figure numbers. The schema should preserve them wherever they exist.

- `source.section` stores the exact structural section or subsection number, such as `26.2.1`.
- `source.citation` stores the exact printed identifier for the specific item when one exists, such as `Section 26.2.1`, `Eq. (26.10-1)`, `Table 26.10-1`, or `Figure 26.1-1`.
- The element `id` should incorporate the official item identifier when available.
- Sequential local suffixes such as `P1`, `P2`, `D1`, and `D2` should be used only when the source does not provide a more specific official identifier for sibling items.

## What It Does NOT Include

- **Page furniture** — headers, footers, page numbers, watermarks (skipped tier)
- **Commentary or user notes** — only the normative code text
- **Digitized figure data** — figures are linked (exported as PNGs with metadata), not digitized into coordinate arrays. The computable content lives in the structured elements they illustrate.
- **Redundant representations** — if a table and a formula express the same relationship, both are included as separate elements (because the code includes both), but they are cross-referenced to each other

## Extraction Method

Content is extracted through a segmented pipeline:

1. **Render** — PDF pages are rendered as images at high DPI.
2. **Segment** — A vision model detects content regions on each page and proposes a classification (structured, linked, skipped) and region type (text_block, table, equation, figure).
3. **Classify** — The human reviews and confirms or overrides the proposed classifications.
4. **Extract** — Each confirmed region is cropped and routed to a type-specific extractor. Text regions use pdfplumber for text extraction, then an LLM for structuring. Tables use pdfplumber table detection with vision fallback. Equations use pure vision. Linked figures are exported as PNGs with a one-line description.
5. **Review** — The human reviews extracted elements against the source PDF, resolving any errors.

The combination of pdfplumber (for text fidelity) and vision models (for layout understanding and equation reading) gives better results than either approach alone. The human checkpoints at classification and review prevent silent failures.

## Quality Model

- **Exact authoritative fields** (structured elements) — expected to preserve the code's wording, equations, symbols, numbers, and citations as exactly as possible. Errors are bugs.
- **Derived helper fields** (structured elements) — expected to be faithful structured restatements of the exact authoritative fields. They may normalize structure, but they must not paraphrase away substance or introduce contradictions.
- **Linked elements** — expected to have an accurate image export, a faithful one-line description, and correct cross-references to the structured elements that contain the computable version of the same information.
- **Cross-references** — expected to be complete within a chapter. Inter-chapter references may be unresolved until those chapters are extracted.
- **Skipped regions** — expected to be genuinely non-informational. Counts are recorded in the QC report for auditing.

Schema validation enforces structural correctness (right fields, right types, valid classification). Calibration against human-verified gold elements measures content accuracy. Completeness checks count linked elements as extracted — they are intentionally not digitized, not missing.
