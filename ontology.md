# Building Code JSON Ontology

## What This Is

A complete, structured, machine-readable parallel representation of a building code PDF. Everything in the PDF exists in the JSON — nothing is excluded. The JSON is the code in structured form.

## Why It Exists

Building codes are locked in PDFs designed for human reading. Engineers, software tools, and AI agents need structured access to the same information. This JSON format makes every provision, formula, table, definition, and figure addressable, queryable, and computable.

## What It Includes

Every piece of content in the building code PDF gets an element in the JSON. Nothing is skipped. Content falls into categories based on its computational role:

### Computable Content

Content that an agent or program can directly evaluate, query, or compute with.

**Tables** — Lookup data with rows and columns. Given inputs (height, exposure category), return outputs (Kz coefficient). These are the most reliable elements because the data is unambiguous.

**Formulas** — Mathematical expressions with named parameters. `qz = 0.00256 * Kz * Kzt * Kd * Ke * V^2`. Includes parameter definitions, units, and ranges. An agent should be able to plug in values and compute results.

**Provisions** — Rules, requirements, and conditional logic. "Buildings with h > 60 ft shall use Method X." Captured as structured conditions with operators, values, and units so they can be evaluated programmatically. Includes exceptions and else-clauses.

### Definitional Content

**Definitions** — Establishes the meaning of terms used throughout the code. "BASIC WIND SPEED, V: Three-second gust speed at 33 ft (10 m) above ground in Exposure C." These define the vocabulary. An agent reading a provision that references "basic wind speed" can look up exactly what that means.

### Reference Content

**References** — Pointers to external standards, databases, or documents. "See ASTM E1886 for testing procedures." The JSON cannot contain the external document — it records what is referenced, where it's cited, and (if available) a URL.

### Illustrative Content

**Figures** — Diagrams, charts, flowcharts, contour maps, geometry illustrations. These visualize information that typically exists elsewhere in the code as text, formulas, or tables. The figure's data in the JSON is a best-effort natural language description — not a precise digitization.

This is intentional. Figures are supplementary. The wind speed contour map illustrates data available from the ASCE Wind Design Geodatabase. The escarpment diagram shows geometry the Kzt formula already defines mathematically. The flowchart summarizes the process that sections 26.1–26.14 spell out in text.

Each figure element includes:
- A `figure_type` classification (flowchart, contour_map, geometry_diagram, xy_chart, table_image)
- A natural language `description` of what the figure communicates
- `cross_references` linking to the sections, tables, and formulas that contain the precise computable version of the same information
- The source PDF page number, so a human or agent can go look at the original image

Figures are not lesser elements. They are complete elements with honest representation. The description quality depends on how well vision extraction captures the content, and that is sufficient because the authoritative data lives in the computable elements.

## Element Types

Six types. No subtypes, no skipped categories.

| Type | Computational Role | Data Precision |
|------|-------------------|----------------|
| `table` | Directly queryable | Exact — row/column values |
| `formula` | Directly computable | Exact — expression + parameters |
| `provision` | Evaluable as logic | Exact — conditions, operators, values |
| `definition` | Vocabulary reference | Exact — term + definition text |
| `reference` | External pointer | Exact — target + citation |
| `figure` | Illustrative context | Best-effort — description + links |

## Element Structure

Every element, regardless of type, has:

- **id** — Unique identifier: `{STANDARD}-{SECTION}-{SUFFIX}` (e.g., `ASCE7-22-26.10-T1`)
- **type** — One of the six types above
- **source** — Where in the code this comes from (standard, chapter, section, page)
- **title** — Human-readable name
- **description** — Optional plain-language summary
- **data** — Type-specific structured content (the actual payload)
- **cross_references** — Links to other elements by ID
- **metadata** — Extraction and QC tracking

## Cross-References

Cross-references are the linking layer. They connect elements to each other, creating a traversable graph of the code. A provision references the table it uses for lookup. A formula references the section that defines its parameters. A figure references the provisions and formulas it illustrates.

These links are element IDs, not free text. They enable:
- Navigation ("what table does this provision use?")
- Impact analysis ("if this formula changes, what provisions are affected?")
- Completeness checking ("does every referenced element exist?")

References to other chapters or external standards use the same format but may point to elements not yet extracted.

## What It Does NOT Include

- **Page layout or formatting** — column structure, fonts, margins
- **Commentary or user notes** — only the normative code text
- **Pixel-perfect figure reproduction** — figures get descriptions, not image data
- **Redundant representations** — if a table and a formula express the same relationship, both are included as separate elements (because the code includes both), but they are cross-referenced to each other

## Extraction Method

Content is extracted by rendering PDF pages as images and reading them with vision-capable AI (Claude). This single-pass approach reads the page as a human would — handling two-column layouts, complex tables, subscripts, and figures naturally.

There is no intermediate text extraction step. The vision model is the single source of truth for what the PDF contains. Quality depends entirely on how well the vision model reads each page.

## Quality Model

- **Tables, formulas, provisions, definitions** — expected to be exact. Errors are bugs.
- **Figures** — expected to be good-faith descriptions. Imprecision is acceptable because the information is redundant with computable elements.
- **Cross-references** — expected to be complete within a chapter. Inter-chapter references may be unresolved until those chapters are extracted.

Schema validation enforces structural correctness (right fields, right types). Calibration against human-verified gold elements measures content accuracy.
