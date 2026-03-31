"""
Overnight harness — runs batch extraction, generates digital twin viewers
for every chapter, produces quality report, iterates if needed.

Usage: python -c "from extract.overnight import run; run()"
"""

import json
import re
import html as html_mod
import time
from pathlib import Path
from collections import defaultdict
from datetime import datetime


def esc(s):
    return html_mod.escape(str(s)) if s else ""


def run():
    start = time.time()
    print("=" * 60)
    print("OVERNIGHT HARNESS")
    print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    # Step 1: Run full batch
    print("\n[1/4] Running batch extraction...")
    from extract.batch import run_batch
    pdf = _find_pdf()
    run_batch(pdf)

    # Step 2: Generate digital twin viewers for ALL chapters
    print("\n[2/4] Generating digital twin viewers...")
    chapters = sorted(Path("output/runs").glob("ch*.json"),
                      key=lambda p: int(re.search(r'ch(\d+)', p.name).group(1)))

    for ch_file in chapters:
        ch_num = int(re.search(r'ch(\d+)', ch_file.name).group(1))
        elements = json.loads(ch_file.read_text())
        _generate_viewer(elements, ch_num)
        print(f"  Ch {ch_num}: {len(elements)} elements → digital-twin-ch{ch_num}.html")

    # Step 3: Generate index page linking all chapter viewers
    print("\n[3/4] Generating index page...")
    _generate_index(chapters)

    # Step 4: Quality report
    print("\n[4/4] Quality report...")
    _quality_report(chapters)

    elapsed = time.time() - start
    print(f"\n{'=' * 60}")
    print(f"COMPLETE: {elapsed/60:.1f} minutes")
    print(f"Viewers: output/runs/digital-twin-ch*.html")
    print(f"Index:   output/runs/digital-twin-index.html")
    print(f"{'=' * 60}")


def _find_pdf():
    pdfs = sorted(Path("input").glob("ASCE*.pdf"), key=lambda p: p.stat().st_size, reverse=True)
    return str(pdfs[0]) if pdfs else None


def _generate_viewer(elements, ch_num):
    by_id = {e["id"]: e for e in elements}
    children = defaultdict(list)
    for e in elements:
        pid = e.get("parent_id")
        if pid:
            children[pid].append(e)

    headings = [e for e in elements if e["type"] == "heading"]

    def sec_sort_key(e):
        parts = e["source"]["section"].split(".")
        return tuple(int(p) if p.isdigit() else 999 for p in parts)
    headings.sort(key=sec_sort_key)

    # Count types for stats
    types = defaultdict(int)
    for e in elements:
        types[e["type"]] += 1

    stats = (f'{len(elements)} elements · '
             f'{types.get("provision",0)} provisions · '
             f'{types.get("definition",0)} definitions · '
             f'{types.get("formula",0)} formulas · '
             f'{types.get("table",0)} tables')

    lines = [f"""<!DOCTYPE html><html><head><meta charset="UTF-8">
<title>ASCE 7-22 Chapter {ch_num} — Digital Twin</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
:root{{--bg:#FFF;--s:#F5F5F5;--t:#1A1A1A;--m:#6B6B6B;--b:#D4D4D4;--sub:#E8E8E8;
--g:#16A34A;--a:#0057FF;--r:#DC2626;--am:#D97706;--cy:#06B6D4;--purple:#8B5CF6}}
@media(prefers-color-scheme:dark){{:root{{--bg:#0A0A0A;--s:#141414;--t:#E5E5E5;--m:#808080;
--b:#2A2A2A;--sub:#1A1A1A;--a:#3B82F6}}}}
body{{background:var(--bg);color:var(--t);font-family:'JetBrains Mono','SF Mono',monospace;
font-size:11px;max-width:900px;margin:0 auto;padding:16px}}
h1{{font-size:13px;text-transform:uppercase;letter-spacing:.1em;margin-bottom:2px}}
.sub{{font-size:10px;color:var(--m);margin-bottom:12px}}
.nav{{font-size:10px;color:var(--a);margin-bottom:16px}}
.nav a{{color:var(--a);text-decoration:none}}
.nav a:hover{{text-decoration:underline}}
.section{{margin-bottom:12px}}
.sec-num{{font-size:12px;font-weight:700;color:var(--t);margin-bottom:2px}}
.sec-0,.sec-1{{margin-left:0}}.sec-2{{margin-left:16px}}.sec-3{{margin-left:32px}}.sec-4{{margin-left:48px}}
.child{{margin:3px 0;padding:4px 8px;border-left:2px solid var(--sub);font-size:10px;line-height:1.5}}
.child.provision{{border-color:var(--g);background:rgba(22,163,74,0.03)}}
.child.definition{{border-color:var(--purple);background:rgba(139,92,246,0.03)}}
.child.formula{{border-color:var(--a);background:rgba(59,130,246,0.03)}}
.child.table{{border-color:var(--cy);background:rgba(6,182,212,0.03)}}
.child.figure{{border-color:var(--am);background:rgba(217,119,6,0.03)}}
.child.exception{{border-color:var(--r);background:rgba(220,38,38,0.03)}}
.child.user_note{{border-color:var(--m);background:rgba(128,128,128,0.03)}}
.child.text_block{{border-color:var(--sub)}}
.tag{{display:inline-block;padding:0 3px;border:1px solid;font-size:7px;text-transform:uppercase;
letter-spacing:.04em;margin-right:3px;vertical-align:middle}}
.tag-provision{{border-color:var(--g);color:var(--g)}}
.tag-definition{{border-color:var(--purple);color:var(--purple)}}
.tag-formula{{border-color:var(--a);color:var(--a)}}
.tag-table{{border-color:var(--cy);color:var(--cy)}}
.tag-figure{{border-color:var(--am);color:var(--am)}}
.tag-exception{{border-color:var(--r);color:var(--r)}}
.tag-user_note{{border-color:var(--m);color:var(--m)}}
.tag-text_block{{border-color:var(--sub);color:var(--m)}}
.meta{{font-size:8px;color:var(--m)}}
.xref{{font-size:8px;color:var(--a)}}
b{{font-weight:600}}
table.tbl{{width:100%;border-collapse:collapse;font-size:9px;margin:4px 0}}
table.tbl th{{text-align:left;font-size:7px;text-transform:uppercase;color:var(--m);border-bottom:1px solid var(--b);padding:1px 4px}}
table.tbl td{{padding:1px 4px;border-bottom:1px solid var(--sub)}}
</style></head><body>
<h1>ASCE 7-22 Chapter {ch_num} — Digital Twin</h1>
<div class="sub">{stats}</div>
<div class="nav"><a href="digital-twin-index.html">← All Chapters</a></div>
"""]

    seen = set()
    for h in headings:
        sec = h["source"]["section"]
        if sec in seen:
            continue
        seen.add(sec)
        depth = sec.count(".")

        lines.append(f'<div class="section sec-{min(depth, 4)}">')
        lines.append(f'<div class="sec-num">{esc(h["title"])}</div>')

        kids = children.get(h["id"], [])
        for kid in kids[:50]:
            _render_child(lines, kid)

        if len(kids) > 50:
            lines.append(f'<div class="meta">+{len(kids)-50} more elements</div>')
        if not kids:
            lines.append('<div class="meta" style="padding:2px 8px">(no child elements)</div>')
        lines.append('</div>')

    lines.append('</body></html>')
    Path(f'output/runs/digital-twin-ch{ch_num}.html').write_text('\n'.join(lines))


def _render_child(lines, kid):
    t = kid["type"]
    data = kid.get("data", {})
    xrefs = kid.get("cross_references", [])

    lines.append(f'<div class="child {t}"><span class="tag tag-{t}">{t}</span>')

    if t == "definition":
        lines.append(f'<b>{esc(data.get("term", ""))}</b>: {esc(data.get("definition", "")[:300])}')
    elif t == "formula":
        lines.append(f'<i>{esc(data.get("expression", "")[:200])}</i>')
        params = data.get("parameters", {})
        if params:
            lines.append(f'<br><span class="meta">parameters: {", ".join(list(params.keys())[:8])}</span>')
    elif t == "table":
        cols = data.get("columns", [])
        rows = data.get("rows", [])
        lines.append(f'{esc(kid.get("title", "")[:80])}')
        if cols and rows:
            lines.append('<table class="tbl"><tr>')
            for c in cols[:7]:
                lines.append(f'<th>{esc(c["name"][:20])}</th>')
            lines.append('</tr>')
            for row in rows[:4]:
                lines.append('<tr>')
                for c in cols[:7]:
                    lines.append(f'<td>{esc(str(row.get(c["name"], "")))}</td>')
                lines.append('</tr>')
            if len(rows) > 4:
                lines.append(f'<tr><td colspan="{min(len(cols),7)}" class="meta">+{len(rows)-4} rows</td></tr>')
            lines.append('</table>')
    elif t == "figure":
        lines.append(f'{esc(data.get("description", "")[:150])}')
    else:
        text = data.get("rule", "") or data.get("target", "")
        lines.append(esc(text[:400]))

    if xrefs:
        refs_display = ", ".join(xrefs[:4])
        if len(xrefs) > 4:
            refs_display += f" +{len(xrefs)-4} more"
        lines.append(f'<br><span class="xref">→ {refs_display}</span>')

    lines.append('</div>')


def _generate_index(chapters):
    lines = ["""<!DOCTYPE html><html><head><meta charset="UTF-8">
<title>ASCE 7-22 — All Chapters</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
:root{--bg:#FFF;--s:#F5F5F5;--t:#1A1A1A;--m:#6B6B6B;--b:#D4D4D4;--sub:#E8E8E8;
--g:#16A34A;--a:#0057FF;--cy:#06B6D4;--purple:#8B5CF6}
@media(prefers-color-scheme:dark){:root{--bg:#0A0A0A;--s:#141414;--t:#E5E5E5;--m:#808080;
--b:#2A2A2A;--sub:#1A1A1A;--a:#3B82F6}}
body{background:var(--bg);color:var(--t);font-family:'JetBrains Mono','SF Mono',monospace;
font-size:11px;max-width:900px;margin:0 auto;padding:16px}
h1{font-size:14px;text-transform:uppercase;letter-spacing:.1em;margin-bottom:4px}
.sub{font-size:10px;color:var(--m);margin-bottom:16px}
table{width:100%;border-collapse:collapse}
th{text-align:left;font-size:8px;text-transform:uppercase;letter-spacing:.08em;color:var(--m);
border-bottom:1px solid var(--b);padding:4px 8px}
td{padding:4px 8px;border-bottom:1px solid var(--sub);font-size:11px}
td a{color:var(--a);text-decoration:none}
td a:hover{text-decoration:underline}
.num{text-align:right;font-variant-numeric:tabular-nums}
</style></head><body>
<h1>ASCE 7-22 — Digital Twin Index</h1>
<div class="sub">Every chapter extracted from the PDF text layer. Click to view.</div>
<table>
<tr><th>CH</th><th>TITLE</th><th class="num">ELEMENTS</th><th class="num">PROVISIONS</th><th class="num">DEFINITIONS</th><th class="num">FORMULAS</th><th class="num">TABLES</th></tr>
"""]

    total_elements = 0
    for ch_file in chapters:
        ch_num = int(re.search(r'ch(\d+)', ch_file.name).group(1))
        elements = json.loads(ch_file.read_text())
        total_elements += len(elements)

        types = defaultdict(int)
        for e in elements:
            types[e["type"]] += 1

        # Get chapter title from first heading
        title = ""
        for e in elements:
            if e["type"] == "heading":
                title = e.get("title", "")
                # Strip section number prefix
                title = re.sub(r'^\d+\.\d+\s*', '', title)
                if len(title) > 3:
                    break

        lines.append(f'<tr>')
        lines.append(f'<td><a href="digital-twin-ch{ch_num}.html"><b>{ch_num}</b></a></td>')
        lines.append(f'<td><a href="digital-twin-ch{ch_num}.html">{esc(title[:50])}</a></td>')
        lines.append(f'<td class="num">{len(elements)}</td>')
        lines.append(f'<td class="num">{types.get("provision", 0)}</td>')
        lines.append(f'<td class="num">{types.get("definition", 0)}</td>')
        lines.append(f'<td class="num">{types.get("formula", 0)}</td>')
        lines.append(f'<td class="num">{types.get("table", 0)}</td>')
        lines.append(f'</tr>')

    lines.append(f'<tr style="font-weight:600"><td></td><td>TOTAL</td><td class="num">{total_elements}</td><td></td><td></td><td></td><td></td></tr>')
    lines.append('</table></body></html>')
    Path('output/runs/digital-twin-index.html').write_text('\n'.join(lines))


def _quality_report(chapters):
    total = 0
    total_tb = 0
    total_clean_headings = 0
    total_headings = 0

    for ch_file in chapters:
        elements = json.loads(ch_file.read_text())
        total += len(elements)
        total_tb += sum(1 for e in elements if e["type"] == "text_block")
        headings = [e for e in elements if e["type"] == "heading"]
        total_headings += len(headings)
        total_clean_headings += sum(1 for h in headings if len(h.get("title", "")) < 80)

    print(f"  Total elements:     {total}")
    print(f"  Text blocks:        {total_tb} ({total_tb/max(total,1)*100:.0f}%)")
    print(f"  Clean headings:     {total_clean_headings}/{total_headings} ({total_clean_headings/max(total_headings,1)*100:.0f}%)")


if __name__ == "__main__":
    run()
