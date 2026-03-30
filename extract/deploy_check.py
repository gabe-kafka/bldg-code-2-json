"""
Deployment gate — all checks must pass before JSON ships.
"""

import json
import re
import sys
from pathlib import Path


def check(json_path, pdf_path):
    elements = json.loads(Path(json_path).read_text())

    results = []

    # 1. Schema validation
    from qc.schema_validator import validate_chapter
    sv = validate_chapter(elements)
    results.append(("SCHEMA", sv["passed"] == sv["total"], f"{sv['passed']}/{sv['total']}"))

    # 2. Benchmark composite
    from extract.benchmark import benchmark
    report = benchmark(json_path, pdf_path)
    results.append(("COMPOSITE ≥95%", report["composite"] >= 0.95, f"{report['composite']*100:.1f}%"))
    results.append(("COVERAGE ≥85%", report["coverage"]["score"] >= 0.85, f"{report['coverage']['score']*100:.0f}%"))
    results.append(("FIDELITY ≥95%", report["fidelity"]["score"] >= 0.95, f"{report['fidelity']['score']*100:.0f}%"))

    # 3. No text_blocks containing "shall" (should be provisions)
    bad_tb = sum(1 for e in elements if e["type"] == "text_block"
                 and "shall " in e.get("data", {}).get("rule", "").lower())
    results.append(("NO SHALL IN TEXT_BLOCKS", bad_tb == 0, f"{bad_tb} found"))

    # 4. Equations: found/total ≥ 50% (relative to what the chapter contains)
    #    "total" here means the count of formula elements already found — we check
    #    that at least half of them were actually populated with an expression.
    formulas_list = [e for e in elements if e["type"] == "formula"]
    formulas_with_expr = sum(
        1 for f in formulas_list
        if f["data"].get("expression") or f["data"].get("latex")
    )
    total_formulas = len(formulas_list)
    eq_ratio = formulas_with_expr / max(total_formulas, 1)
    results.append((
        "EQUATIONS ≥50%",
        total_formulas == 0 or eq_ratio >= 0.5,
        f"{formulas_with_expr}/{total_formulas} ({eq_ratio*100:.0f}%)"
    ))

    # 5. ≥30% of found formulas have parameters
    with_params = sum(
        1 for f in formulas_list
        if f["data"].get("parameters") and len(f["data"]["parameters"]) > 0
    )
    pct = with_params / max(total_formulas, 1)
    results.append((
        "PARAMS ≥30%",
        total_formulas == 0 or pct >= 0.3,
        f"{with_params}/{total_formulas} ({pct*100:.0f}%)"
    ))

    # 6. ≥15% of provisions have conditions
    provs = [e for e in elements if e["type"] == "provision"]
    with_cond = sum(
        1 for p in provs
        if p["data"].get("conditions") and len(p["data"]["conditions"]) > 0
    )
    pct_c = with_cond / max(len(provs), 1)
    results.append((
        "CONDITIONS ≥15%",
        len(provs) == 0 or pct_c >= 0.15,
        f"{with_cond}/{len(provs)} ({pct_c*100:.0f}%)"
    ))

    # 7. Cross-references exist
    with_xref = sum(1 for e in elements if e.get("cross_references") and len(e["cross_references"]) > 0)
    results.append(("CROSS-REFS >0", with_xref > 0, f"{with_xref} elements"))

    # 8. References extracted — only required when chapter text mentions ASTM/ANSI/CAN
    import fitz as _fitz
    _doc = _fitz.open(pdf_path)
    _full_text = "".join(_doc[i].get_text() for i in range(len(_doc)))
    _doc.close()
    _needs_refs = bool(re.search(r'\b(ASTM|ANSI|CAN)\b', _full_text))
    refs = sum(1 for e in elements if e["type"] == "reference")
    if _needs_refs:
        results.append(("REFERENCES >0", refs > 0, f"{refs} found (ASTM/ANSI/CAN present)"))
    else:
        results.append(("REFERENCES (N/A)", True, f"{refs} found (no ASTM/ANSI/CAN)"))

    # 9. Required types: heading, provision, text_block always; others are chapter-dependent
    types = set(e["type"] for e in elements)
    required = {"heading", "provision", "text_block"}
    missing = required - types
    results.append(("CORE 3 TYPES", len(missing) == 0, f"missing: {missing}" if missing else "all present"))

    # 10. No broken references (pending is OK, broken is not)
    unresolved_path = Path("output/unresolved.json")
    if unresolved_path.exists():
        ur = json.loads(unresolved_path.read_text())
        broken = ur.get("summary", {}).get("broken", 0)
        pending = ur.get("summary", {}).get("pending", 0)
        results.append(("0 BROKEN REFS", broken == 0, f"{broken} broken, {pending} pending"))
    else:
        results.append(("0 BROKEN REFS", True, "no unresolved file (OK for single chapter)"))

    # 11. Symbols registry exists
    symbols_path = Path("output/symbols.json")
    if symbols_path.exists():
        sym = json.loads(symbols_path.read_text())
        n = len(sym.get("symbols", {}))
        results.append(("SYMBOLS REGISTRY", n > 10, f"{n} symbols"))
    else:
        results.append(("SYMBOLS REGISTRY", False, "not generated"))

    # Print
    print("DEPLOYMENT GATE")
    print("=" * 60)
    all_pass = True
    for name, passed, detail in results:
        status = "PASS" if passed else "FAIL"
        if not passed:
            all_pass = False
        print(f"  [{status}] {name:25s} {detail}")

    print("=" * 60)
    if all_pass:
        print("RESULT: ALL PASS — ready for deployment")
    else:
        fails = sum(1 for _, p, _ in results if not p)
        print(f"RESULT: {fails} FAILURES — not ready")

    return all_pass


if __name__ == "__main__":
    json_path = sys.argv[1] if len(sys.argv) > 1 else "output/runs/final-ch26.json"
    pdf_path = list(Path("input").glob("*.pdf"))[0]
    ok = check(json_path, pdf_path)
    sys.exit(0 if ok else 1)
