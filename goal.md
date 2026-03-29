# Goal

Make building codes computable.

## Problem

Building codes are published as PDFs. They contain provisions (law), equations (math), tables (data), definitions (vocabulary), figures (illustrations), and references (pointers to external standards). None of this is queryable by software. Engineers spend hours navigating them manually. AI agents cannot reason over them.

## What This Tool Does

Converts building code PDFs into structured, machine-readable JSON — a faithful digital twin where:

- Every **provision** is extractable as a rule with conditions, thresholds, and exceptions
- Every **equation** is a computable expression with named parameters and units
- Every **table** is queryable rows and columns with exact values
- Every **definition** is a term-definition pair for vocabulary lookup
- Every **figure** is a linked image with cross-references to the computable elements it illustrates
- Every **reference** is a pointer to an external standard with exact citation text

The authoritative text is preserved exactly. Nothing is paraphrased. Nothing is summarized. The JSON says what the PDF says, in a form machines can use.

## Success Metric

**Can an agent answer a building code question using only the extracted data, and get the same answer a licensed engineer would get reading the PDF?**

Examples:
- "What is the basic wind speed for a Risk Category II building in Miami?" → Agent queries the wind speed table/figure, returns the value.
- "Does a building with mean roof height 45 ft in Exposure B need to use the Directional Procedure?" → Agent evaluates the provision conditions, returns yes/no with the citation.
- "What is the velocity pressure at 100 ft in Exposure C with V = 150 mph?" → Agent computes qz = 0.00256 × Kz × Kzt × Kd × Ke × V² using table values.

## Scope

- **First target:** ASCE 7-22 Chapter 26 (Wind Loads — General Requirements)
- **Designed to generalize** to any chapter of any building code (IBC, ACI 318, AISC 360, etc.)
- **Pipeline runs per chapter** — one PDF chapter in, one JSON file out

## Non-Goals

- Not a code commentary or interpretation tool — it extracts what the code says, not what it means
- Not a compliance engine — it provides the data an engine would consume
- Not a replacement for the PDF — the PDF remains the legal document of record
