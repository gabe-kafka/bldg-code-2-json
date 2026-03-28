"""
LLM-guided optimizer — Karpathy-style auto-refinement loop.

Loop:
  1. Run extraction with current config
  2. Score it (objective function)
  3. Feed score + failure analysis to Claude
  4. Claude proposes config changes (prompt edits, parameters)
  5. Apply changes, repeat
  6. Stop when score plateaus or max iterations reached
"""

import json
import copy
import time
from pathlib import Path
from dataclasses import dataclass, field, asdict

import anthropic

from extract.pdf_parser import parse_pdf
from extract.llm_structurer import extract_chapter
from refine.objective import score_run


@dataclass
class RunConfig:
    """Mutable parameters the optimizer can change between runs."""
    # Prompt overrides — if set, replace the default prompt in llm_structurer
    structurer_prompt: str | None = None
    # Model to use for structuring
    structurer_model: str = "claude-sonnet-4-20250514"
    # PDF rendering resolution
    render_dpi: int = 300
    # How many pages to process per LLM call (chunking)
    pages_per_chunk: int = 1
    # Spot check sample size
    spot_check_size: int = 10
    # Any extra instructions appended to the structurer prompt
    extra_instructions: str = ""


@dataclass
class RunResult:
    """One iteration's results."""
    run_id: int
    config: RunConfig
    score: dict
    timestamp: float = field(default_factory=time.time)


PROPOSE_PROMPT = """You are optimizing a building code PDF extraction pipeline.

The pipeline extracts provisions, tables, formulas, and figures from building code PDFs into structured JSON. Your job is to improve it.

## Current Configuration
{config_json}

## Run History (most recent first)
{history_json}

## Latest Score Breakdown
Composite: {composite_score}
- Schema validity: {schema_score} (weight 0.2)
- Completeness: {completeness_score} (weight 0.3)
- Accuracy: {accuracy_score} (weight 0.4)
- Cross-ref resolution: {xref_score} (weight 0.1)

## Failure Analysis
{failure_json}

## Your Task
Analyze WHY the score isn't higher. Look at the failure categories and specific errors.

Then propose EXACTLY ONE change to the config to improve the score. Focus on the highest-weighted component that's underperforming.

Strategies you can use:
- Edit "extra_instructions" to add specific guidance that addresses common errors
- Change "structurer_model" to a more capable model if accuracy is low
- Change "pages_per_chunk" to process more/fewer pages at once (affects context)
- Change "render_dpi" if figure extraction is poor
- Write a full "structurer_prompt" replacement if the base prompt is fundamentally wrong

Return a JSON object:
{{
  "analysis": "<2-3 sentences on what's wrong and why>",
  "change": {{
    "field": "<config field to change>",
    "new_value": "<new value>",
    "rationale": "<why this change should improve the score>"
  }},
  "expected_improvement": "<which score component should improve and by roughly how much>"
}}

Return ONLY the JSON object.
"""


def optimize(
    pdf_path: str,
    standard: str,
    chapter: int,
    start_page: int = 1,
    end_page: int | None = None,
    max_iterations: int = 5,
    target_score: float = 0.90,
    output_dir: str = "output/refine",
) -> list[RunResult]:
    """Run the auto-refinement loop.

    Args:
        pdf_path: Path to source PDF.
        standard: Standard name.
        chapter: Chapter number.
        start_page: First page (1-indexed).
        end_page: Last page (inclusive).
        max_iterations: Stop after this many runs.
        target_score: Stop if composite score reaches this.
        output_dir: Where to save run logs.

    Returns:
        List of RunResults, one per iteration.
    """
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    config = RunConfig()
    history: list[RunResult] = []

    # Parse PDF once at current DPI for scoring
    pages = parse_pdf(pdf_path, start_page=start_page, end_page=end_page)

    for i in range(max_iterations):
        run_id = i + 1
        print(f"\n{'='*60}")
        print(f"RUN {run_id}/{max_iterations}")
        print(f"{'='*60}")

        # --- Execute extraction ---
        print("Extracting...")
        elements, run_pages = _run_extraction(pdf_path, standard, chapter, start_page, end_page, config)
        # Use extraction pages for scoring (they match the DPI used)
        pages = run_pages

        # --- Score (vary seed per run to avoid overfitting spot checks) ---
        print("Scoring...")
        score = score_run(elements, pages, spot_check_size=config.spot_check_size, seed=42 + run_id)
        composite = score["composite_score"]

        result = RunResult(run_id=run_id, config=copy.deepcopy(config), score=score)
        history.append(result)

        # Save run artifacts
        _save_run(out_dir, result, elements)

        print(f"Score: {composite:.4f}")
        print(f"  Schema:      {score['components']['schema_validity']:.4f}")
        print(f"  Completeness:{score['components']['completeness']:.4f}")
        print(f"  Accuracy:    {score['components']['accuracy']:.4f}")
        print(f"  Xref:        {score['components']['xref_resolve']:.4f}")

        # --- Check stopping conditions ---
        if composite >= target_score:
            print(f"\nTarget score {target_score} reached. Stopping.")
            break

        if len(history) >= 2:
            prev_score = history[-2].score["composite_score"]
            improvement = composite - prev_score
            print(f"  Improvement: {improvement:+.4f}")
            if abs(improvement) < 0.005 and i >= 2:
                print("\nScore plateaued. Stopping.")
                break

        if run_id >= max_iterations:
            print("\nMax iterations reached. Stopping.")
            break

        # --- Propose improvement ---
        print("Analyzing failures and proposing improvement...")
        proposal = _propose_change(config, history, score)
        print(f"  Analysis: {proposal.get('analysis', 'N/A')}")
        print(f"  Change: {proposal.get('change', {}).get('field', 'N/A')} → {proposal.get('change', {}).get('rationale', 'N/A')}")

        # Apply the change
        change = proposal.get("change", {})
        field = change.get("field")
        new_value = change.get("new_value")
        if field and hasattr(config, field):
            setattr(config, field, new_value)
            print(f"  Applied: {field} = {repr(new_value)[:100]}")

    # Save final summary
    summary = {
        "total_runs": len(history),
        "best_run": max(range(len(history)), key=lambda i: history[i].score["composite_score"]) + 1,
        "best_score": max(r.score["composite_score"] for r in history),
        "score_progression": [r.score["composite_score"] for r in history],
        "runs": [
            {
                "run_id": r.run_id,
                "composite_score": r.score["composite_score"],
                "components": r.score["components"],
                "config_changes": _config_diff(RunConfig(), r.config),
            }
            for r in history
        ],
    }
    with open(out_dir / "summary.json", "w") as f:
        json.dump(summary, f, indent=2)

    print(f"\n{'='*60}")
    print(f"DONE — Best score: {summary['best_score']:.4f} (run {summary['best_run']})")
    print(f"Summary → {out_dir / 'summary.json'}")

    return history


def _run_extraction(pdf_path, standard, chapter, start_page, end_page, config: RunConfig) -> tuple[list[dict], list]:
    """Run extraction with the current config. Returns (elements, pages)."""
    import extract.llm_structurer as structurer

    # Inject config into structurer prompt
    original_prompt = structurer.STRUCTURE_PROMPT
    original_model = structurer.STRUCTURER_MODEL
    if config.structurer_prompt:
        structurer.STRUCTURE_PROMPT = config.structurer_prompt
    elif config.extra_instructions:
        structurer.STRUCTURE_PROMPT = original_prompt + f"\n\nADDITIONAL INSTRUCTIONS:\n{config.extra_instructions}"

    # Inject model override
    structurer.STRUCTURER_MODEL = config.structurer_model

    try:
        # Parse PDF once, use for both extraction and scoring
        pages = parse_pdf(pdf_path, start_page=start_page, end_page=end_page, render_dpi=config.render_dpi)
        elements = structurer.extract_chapter_from_pages(
            pages=pages,
            standard=standard,
            chapter=chapter,
            pages_per_chunk=config.pages_per_chunk,
        )
    finally:
        structurer.STRUCTURE_PROMPT = original_prompt
        structurer.STRUCTURER_MODEL = original_model

    return elements, pages


def _propose_change(config: RunConfig, history: list[RunResult], latest_score: dict) -> dict:
    """Ask Claude to analyze failures and propose one config change."""
    client = anthropic.Anthropic()

    # Build concise history (last 3 runs)
    recent = history[-3:]
    history_summary = [
        {
            "run_id": r.run_id,
            "score": r.score["composite_score"],
            "components": r.score["components"],
            "config_diff": _config_diff(RunConfig(), r.config),
        }
        for r in recent
    ]

    prompt = PROPOSE_PROMPT.format(
        config_json=json.dumps(asdict(config), indent=2, default=str),
        history_json=json.dumps(history_summary, indent=2),
        composite_score=latest_score["composite_score"],
        schema_score=latest_score["components"]["schema_validity"],
        completeness_score=latest_score["components"]["completeness"],
        accuracy_score=latest_score["components"]["accuracy"],
        xref_score=latest_score["components"]["xref_resolve"],
        failure_json=json.dumps(latest_score["failure_analysis"], indent=2),
    )

    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=2048,
        messages=[{"role": "user", "content": prompt}],
    )

    text = message.content[0].text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        end = len(lines) - 1
        while end > 0 and not lines[end].strip().startswith("```"):
            end -= 1
        text = "\n".join(lines[1:end])

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"analysis": "Failed to parse proposal", "change": {}, "expected_improvement": "unknown"}


def _config_diff(baseline: RunConfig, current: RunConfig) -> dict:
    """Return fields that differ from baseline."""
    diff = {}
    for field_name in vars(baseline):
        base_val = getattr(baseline, field_name)
        curr_val = getattr(current, field_name)
        if base_val != curr_val:
            diff[field_name] = {"from": base_val, "to": curr_val}
    return diff


def _save_run(out_dir: Path, result: RunResult, elements: list[dict]):
    """Save a run's artifacts."""
    run_dir = out_dir / f"run-{result.run_id}"
    run_dir.mkdir(parents=True, exist_ok=True)

    with open(run_dir / "elements.json", "w") as f:
        json.dump(elements, f, indent=2)

    with open(run_dir / "score.json", "w") as f:
        json.dump(result.score, f, indent=2)

    with open(run_dir / "config.json", "w") as f:
        json.dump(asdict(result.config), f, indent=2, default=str)
