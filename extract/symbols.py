"""
Global symbols registry.

Parses the symbols list (Section X.3 in each chapter) into a lookup table,
accumulates across chapters, and resolves formula parameters against it.
"""

import json
import re
from pathlib import Path


def build_symbols_table(elements):
    """Parse symbols from Section X.3 text blocks.

    Looks for patterns like:
    - "A = Effective wind area, ft² (m²)"
    - "Kz = Velocity pressure exposure coefficient"
    - "V = Basic wind speed, mi/h (m/s)"
    """
    symbols = {}

    # Find symbol/notation sections — not just X.3 but any section with symbol definitions
    # Common patterns: X.2 (definitions), X.3 (symbols/notation), and any heading with SYMBOLS/NOTATION
    symbol_elements = []
    for e in elements:
        sec = e.get("source", {}).get("section", "")
        title = e.get("title", "").upper()
        text = e.get("data", {}).get("rule", "") or ""

        # Sections that are symbol lists
        if re.match(r'^\d+\.[23]$', sec):
            symbol_elements.append(e)
        # Any element whose heading mentions SYMBOLS or NOTATION
        elif "SYMBOL" in title or "NOTATION" in title:
            symbol_elements.append(e)
        # Text blocks that look like symbol definitions (X = description pattern)
        elif e["type"] == "text_block" and re.match(r'^[A-Za-z_]{1,5}\s*=\s*.{10,}', text):
            symbol_elements.append(e)

    # Also scan "where" blocks anywhere in the document
    where_elements = [e for e in elements if "where" in (e.get("data", {}).get("rule", "") or "")[:20].lower()]

    for e in symbol_elements + where_elements:
        text = e.get("data", {}).get("rule", "") or e.get("data", {}).get("definition", "")
        if not text:
            continue

        # Pattern: "SYMBOL = description, unit"
        # Handle: single-letter (V), multi-letter (Kz), subscripted (B 1 D), Greek (ξ)
        for m in re.finditer(
            r'(?:^|\s)([A-Za-z_αβεγδζηθλμνρστφωΩΔξ][A-Za-z0-9_ ˆ¯]*?)\s*=\s*([^=\n]+?)(?=\s+[A-Za-z_αβεγδζηθλμνρστφωΩΔξ][A-Za-z0-9_ˆ¯]*\s*=|$)',
            text
        ):
            sym = m.group(1).strip()
            desc = m.group(2).strip().rstrip(",").rstrip(".")

            # Skip if symbol is too long (probably not a variable)
            if len(sym) > 15:
                continue
            # Skip if description is too short
            if len(desc) < 3:
                continue
            # Skip known non-variables
            if sym.lower() in ("and", "or", "the", "for", "are", "from", "where", "with"):
                continue

            # Extract unit if present: "description, unit (SI unit)"
            unit = None
            unit_match = re.search(r',\s*(ft[²2]?|m[²2]?|mi/h|m/s|mph|Hz|dimensionless|lb/ft[²2]?|N/m[²2]?|degrees|%)\s*(?:\([^)]+\))?\s*$', desc)
            if unit_match:
                unit = unit_match.group(1)
                desc = desc[:unit_match.start()].strip().rstrip(",")

            # Find which equations/sections use this symbol
            source_id = e.get("id", "")
            defined_in = e.get("source", {}).get("section", "")

            if sym not in symbols or len(desc) > len(symbols[sym].get("description", "")):
                symbols[sym] = {
                    "description": desc,
                    "unit": unit,
                    "source": source_id,
                    "defined_in": defined_in,
                }
                # Also store space-stripped version for matching (B 1 D → B1D)
                stripped = sym.replace(" ", "")
                if stripped != sym and stripped not in symbols:
                    symbols[stripped] = symbols[sym]

    return symbols


def resolve_parameters(elements, symbols):
    """Fill formula parameters from the global symbols table.

    For each formula, extract variable names from the expression,
    look them up in the symbols table, and populate data.parameters.
    """
    # Common non-variable tokens to skip
    skip = {"and", "or", "the", "in", "for", "ft", "mi", "lb", "SI", "where", "from",
            "mi/h", "m/s", "mph", "Hz", "sin", "cos", "tan", "ln", "log", "exp",
            "min", "max", "if", "of", "to", "at", "by", "is", "be", "as", "on"}

    for e in elements:
        if e["type"] != "formula":
            continue

        expression = e["data"].get("expression", "")
        if not expression:
            continue

        # Extract variable-like tokens from expression — broad matching
        expr_vars = set()
        expr_vars |= set(re.findall(r'\b([A-Za-z][A-Za-z0-9_]*)\b', expression)) - skip
        expr_vars |= set(re.findall(r'([A-Za-z]+(?:_[a-zA-Z0-9]+)?)', expression)) - skip
        expr_vars |= set(re.findall(r'(α|β|γ|δ|ε|ζ|η|θ|λ|μ|ν|ρ|σ|τ|φ|ω|Ω|Δ)', expression))
        expr_vars |= set(re.findall(r'\b([A-Z][a-z][A-Z]?|[A-Z]{2,3}[0-9]?)\b', expression)) - skip

        params = {}
        for var in expr_vars:
            if var in symbols:
                sym = symbols[var]
                params[var] = {
                    "description": sym["description"],
                }
                if sym.get("unit"):
                    params[var]["unit"] = sym["unit"]
                if sym.get("source"):
                    params[var]["source"] = sym["source"]

        # Also check nearby elements for local "where" definitions
        idx = elements.index(e)
        for nearby in elements[max(0, idx - 3):idx + 4]:
            if nearby is e:
                continue
            nearby_text = nearby.get("data", {}).get("rule", "")
            if not nearby_text:
                continue
            for m in re.finditer(r'([A-Za-z][A-Za-z0-9_]*)\s*=\s*([^,;=]+)', nearby_text):
                sym = m.group(1).strip()
                desc = m.group(2).strip()
                if sym in expr_vars and sym not in params and len(desc) > 3:
                    params[sym] = {"description": desc}

        if params:
            e["data"]["parameters"] = params

    return sum(1 for e in elements if e["type"] == "formula" and e["data"].get("parameters"))


def save_symbols(symbols, path="output/symbols.json"):
    """Save symbols table to JSON."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"symbols": symbols}, indent=2))
    return path


def load_symbols(path="output/symbols.json"):
    """Load existing symbols table."""
    path = Path(path)
    if path.exists():
        return json.loads(path.read_text()).get("symbols", {})
    return {}


def merge_symbols(existing, new):
    """Merge new symbols into existing table. New takes precedence if longer description."""
    merged = {**existing}
    for sym, info in new.items():
        if sym not in merged or len(info.get("description", "")) > len(merged[sym].get("description", "")):
            merged[sym] = info
    return merged
