"""CLI entrypoint for the Saudi e-commerce ad agent.

Usage:
    python main.py                       # runs the canned sample brief
    python main.py --brief "..."         # custom brief
    python main.py --brief-file path.txt # read brief from file
    python main.py --json                # machine-readable output

Outputs land in ./outputs/runs/<timestamp>/ as run.json + storyboard.md.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import uuid
from pathlib import Path

from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from src.graph import build_graph

ROOT = Path(__file__).resolve().parent
RUNS_DIR = ROOT / "outputs" / "runs"

SAMPLE_BRIEF = (
    "Promote our premium Ajwa dates collection for the upcoming Ramadan campaign. "
    "Target audience: Saudi families, ages 25-45, gifting for iftar gatherings. "
    "Single 9:16 short-form video, ≤15 seconds, bilingual (Arabic VO + English overlay). "
    "Objective: drive product page visits."
)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Saudi e-commerce ad agent")
    p.add_argument("--brief", default=None, help="Inline brief text")
    p.add_argument("--brief-file", default=None, help="Path to a brief text file")
    p.add_argument("--locale", default="ar-SA")
    p.add_argument("--audience", default="Saudi adults 25-45, parents, urban")
    p.add_argument("--brand-doc", default=None, help="Path to brand manual (md or pdf)")
    p.add_argument("--json", dest="as_json", action="store_true", help="Emit JSON only")
    return p.parse_args()


def _resolve_brief(args: argparse.Namespace) -> str:
    if args.brief_file:
        return Path(args.brief_file).read_text(encoding="utf-8")
    if args.brief:
        return args.brief
    return SAMPLE_BRIEF


def _save(run_id: str, final: dict) -> Path:
    out_dir = RUNS_DIR / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "run.json").write_text(
        json.dumps(final, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    sb = final.get("storyboard", {}) or {}
    md = (
        f"# Run {run_id}\n\n"
        f"**CTR estimate:** {final.get('ctr_estimate', 0):.2%}\n\n"
        f"**Eval status:** {final.get('eval_status')}\n\n"
        f"**Guardrail status:** {final.get('guardrail_status')} "
        f"(revisions: {final.get('guardrail_revision_count', 0)})\n\n"
        f"## Storyboard\n"
        f"- **Hook:** {sb.get('hook')}\n"
        f"- **Body:** {sb.get('body')}\n"
        f"- **CTA:** {sb.get('cta')}\n"
        f"- **Visual prompt:** {sb.get('visual_prompt')}\n"
        f"- **Motion prompt:** {sb.get('motion_prompt')}\n"
        f"- **Voiceover (AR):** {sb.get('voiceover')}\n"
        f"- **Voice ID:** {sb.get('voice')}\n\n"
        f"## Generated assets\n"
        f"- Image: {final.get('image_url')}\n"
        f"- Video: {final.get('video_url')}\n"
        f"- Audio: {final.get('audio_url')}\n\n"
        f"## Eval notes\n"
        + "\n".join(f"- {n}" for n in final.get("eval_notes", []))
    )
    (out_dir / "storyboard.md").write_text(md, encoding="utf-8")
    return out_dir


def _render(console: Console, final: dict) -> None:
    sb = final.get("storyboard", {}) or {}
    hdr = (
        f"[bold]Saudi Ad Agent[/bold] · run [cyan]{final.get('run_id')}[/cyan]\n"
        f"Mode: {'LIVE' if os.getenv('ANTHROPIC_API_KEY') else 'OFFLINE-MOCK'}"
    )
    console.print(Panel(hdr, expand=False))

    sb_table = Table(show_header=False, box=None)
    sb_table.add_column(style="bold")
    sb_table.add_column()
    for k in ("hook", "body", "cta", "visual_prompt", "motion_prompt", "voiceover", "voice"):
        sb_table.add_row(k, str(sb.get(k, "")))
    console.print(Panel(sb_table, title="Storyboard"))

    asset_table = Table(show_header=True, header_style="bold")
    asset_table.add_column("Asset")
    asset_table.add_column("URL")
    asset_table.add_row("Image (Seedream)", final.get("image_url") or "-")
    asset_table.add_row("Video (Seedance)", final.get("video_url") or "-")
    asset_table.add_row("Audio (Seed Speech)", final.get("audio_url") or "-")
    console.print(Panel(asset_table, title="Generated assets"))

    eval_panel = (
        f"CTR estimate: [bold cyan]{final.get('ctr_estimate', 0):.2%}[/bold cyan]\n"
        f"Status: [bold]{final.get('eval_status')}[/bold]\n"
        f"Guardrail: {final.get('guardrail_status')} "
        f"(revisions: {final.get('guardrail_revision_count', 0)})\n\n"
        + "\n".join(f"• {n}" for n in final.get("eval_notes", []))
    )
    console.print(Panel(eval_panel, title="Eval"))


def main() -> int:
    load_dotenv()
    args = _parse_args()
    console = Console()

    initial: dict = {
        "brief": _resolve_brief(args),
        "locale": args.locale,
        "target_audience": args.audience,
        "brand_doc_path": args.brand_doc,
        "run_id": dt.datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6],
        "errors": [],
        "log": [],
        "guardrail_revision_count": 0,
    }

    graph = build_graph()
    final = graph.invoke(initial)

    out_dir = _save(initial["run_id"], final)

    if args.as_json:
        print(json.dumps(final, ensure_ascii=False, indent=2))
    else:
        _render(console, final)
        console.print(f"\n[dim]Run artefacts saved to {out_dir}[/dim]")

    return 0 if final.get("eval_status") == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
