#!/usr/bin/env python3
"""
Teacher Grading AI - Robertson College
AI-powered grading assistant that marks papers in the teacher's voice.
"""

import os
import json
import sys
import re
from pathlib import Path
from typing import Optional

import anthropic
import click
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table
from rich.prompt import Prompt, Confirm
from rich import print as rprint

console = Console()
client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
MODEL = "claude-opus-4-8"

PROFILE_PATH = Path(__file__).parent / "teacher_profile.json"


def load_profile() -> dict:
    if PROFILE_PATH.exists():
        with open(PROFILE_PATH) as f:
            return json.load(f)
    return {
        "name": "Teacher",
        "institution": "Robertson College",
        "voice_samples": [],
        "grading_style": "encouraging but firm",
        "subject": "",
        "grade_level": "",
    }


def save_profile(profile: dict):
    with open(PROFILE_PATH, "w") as f:
        json.dump(profile, f, indent=2)


def read_submission(file_path: str) -> str:
    path = Path(file_path)
    if not path.exists():
        console.print(f"[red]File not found: {file_path}[/red]")
        sys.exit(1)

    suffix = path.suffix.lower()

    if suffix == ".txt":
        return path.read_text(encoding="utf-8")

    elif suffix == ".pdf":
        try:
            import PyPDF2
            with open(path, "rb") as f:
                reader = PyPDF2.PdfReader(f)
                return "\n".join(page.extract_text() or "" for page in reader.pages)
        except ImportError:
            console.print("[yellow]PyPDF2 not installed. Reading as plain text.[/yellow]")
            return path.read_text(encoding="utf-8", errors="ignore")

    elif suffix in (".docx", ".doc"):
        try:
            from docx import Document
            doc = Document(path)
            return "\n".join(p.text for p in doc.paragraphs)
        except ImportError:
            console.print("[yellow]python-docx not installed. Reading as plain text.[/yellow]")
            return path.read_text(encoding="utf-8", errors="ignore")

    else:
        return path.read_text(encoding="utf-8", errors="ignore")


def build_teacher_system_prompt(profile: dict) -> str:
    voice_section = ""
    if profile.get("voice_samples"):
        samples = "\n---\n".join(profile["voice_samples"][:5])
        voice_section = f"""
## Your Writing Voice - Study These Samples
Here are examples of how you actually write feedback. Match this style exactly:

{samples}

"""

    return f"""You are {profile['name']}, a teacher at {profile.get('institution', 'Robertson College')}.
Your grading style is: {profile.get('grading_style', 'encouraging but firm')}.
Subject: {profile.get('subject', 'General') or 'General'}.
Grade level: {profile.get('grade_level', 'College') or 'College'}.
{voice_section}
When giving feedback:
- Write exactly as you naturally speak and write — use your own vocabulary, sentence structure, and tone
- Be specific about what the student got right or wrong
- Correct grammar issues in your comments when you notice them
- Ask follow-up questions that probe whether the student truly understood, not just guessed
- Be direct but fair in scoring
- If AI detection flags something, address it professionally
"""


def detect_ai_content(text: str, profile: dict) -> dict:
    """
    Analyze text to distinguish full AI generation from AI-assisted writing.
    Returns: {level: 'none'|'assisted'|'full', score: 0-100, reasoning: str, flagged: bool}
    """
    system = """You are an expert at detecting AI-generated academic writing.
Your job is to distinguish between:
1. Fully human writing
2. AI-ASSISTED writing (human wrote it, AI helped with grammar/structure/polish) - this is ACCEPTABLE
3. FULLY AI-GENERATED writing (AI wrote the whole thing) - this is NOT acceptable

Key differences:
- Full AI: overly uniform sentence structure, generic examples, no personal voice, unnaturally perfect transitions, no typos or colloquialisms, "In conclusion...", "It is important to note...", lists of exactly 3 points
- AI-assisted: retains human voice and personal touches, some natural imperfections remain, ideas are original even if prose is polished
- Human: natural variation, personal anecdotes, occasional informal phrasing, unique examples

Respond in JSON only:
{
  "level": "none" | "assisted" | "full",
  "score": 0-100,  // 0=fully human, 50=AI-assisted, 100=fully AI-generated
  "indicators": ["list", "of", "specific", "observations"],
  "reasoning": "1-2 sentence summary"
}"""

    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=600,
            system=system,
            messages=[{"role": "user", "content": f"Analyze this student submission for AI content:\n\n{text[:4000]}"}],
        )
        raw = response.content[0].text.strip()
        # Extract JSON from response
        match = re.search(r'\{.*\}', raw, re.DOTALL)
        if match:
            return json.loads(match.group())
    except Exception as e:
        console.print(f"[yellow]AI detection warning: {e}[/yellow]")

    return {"level": "none", "score": 0, "indicators": [], "reasoning": "Detection unavailable"}


def grade_submission(submission_text: str, rubric: Optional[str], profile: dict) -> str:
    """Stream the grading response from Claude in the teacher's voice."""
    ai_result = detect_ai_content(submission_text, profile)

    ai_context = ""
    if ai_result["level"] == "full":
        ai_context = f"""
IMPORTANT - AI DETECTION ALERT: This submission appears to be FULLY AI-GENERATED (confidence: {ai_result['score']}%).
Indicators: {', '.join(ai_result.get('indicators', []))}
You must address this directly in your feedback. The policy is: AI assistance with grammar/structure is acceptable, but submitting entirely AI-written work is not.
"""
    elif ai_result["level"] == "assisted":
        ai_context = f"""
NOTE - AI ASSISTANCE DETECTED: This submission shows signs of AI-assisted writing (grammar/structure help), score: {ai_result['score']}%.
This is acceptable per policy. You may briefly note it positively or neutrally — no penalty needed.
"""

    rubric_section = f"\n## Grading Rubric\n{rubric}" if rubric else ""

    prompt = f"""Please grade this student submission.{rubric_section}
{ai_context}

## Student Submission
{submission_text}

---

Please provide:
1. **Overall Grade** (letter grade + percentage)
2. **Question-by-Question Feedback** — for each question/section:
   - Score for that section
   - Specific comment in YOUR voice
   - If any grammar issues exist in the student's writing, note them
   - A follow-up question to confirm the student truly understood the concept (not just guessed)
3. **Overall Comments** — summary feedback in your natural voice
4. **AI Content Note** — only if flagged, address it directly
"""

    system = build_teacher_system_prompt(profile)

    console.print("\n[bold cyan]Generating graded feedback...[/bold cyan]\n")
    full_response = ""

    with client.messages.stream(
        model=MODEL,
        max_tokens=4000,
        system=system,
        messages=[{"role": "user", "content": prompt}],
        thinking={"type": "enabled", "budget_tokens": 5000} if False else None,
    ) as stream:
        for text in stream.text_stream:
            print(text, end="", flush=True)
            full_response += text

    print("\n")
    return full_response, ai_result


def generate_followup_questions(submission_text: str, graded_feedback: str, profile: dict) -> str:
    """Generate targeted follow-up questions to probe real understanding."""
    system = build_teacher_system_prompt(profile)

    prompt = f"""Based on this student's submission and your grading feedback, generate 3-5 targeted verbal follow-up questions you would ask this student in person or in a follow-up assignment to confirm they genuinely understood the material (not just guessed or copied).

Make the questions:
- Specific to what they wrote
- Designed to reveal whether they truly understand the underlying concepts
- Progressive (easier to harder)
- In your natural voice

## Student Submission (excerpt)
{submission_text[:2000]}

## Your Grading Feedback
{graded_feedback[:1500]}

List just the follow-up questions, numbered, with a brief note on what each question is probing for."""

    response = client.messages.create(
        model=MODEL,
        max_tokens=800,
        system=system,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text


@click.group()
def cli():
    """Teacher Grading AI — Robertson College"""
    pass


@cli.command()
def setup():
    """Set up your teacher profile and voice samples."""
    console.print(Panel.fit(
        "[bold blue]Teacher Profile Setup[/bold blue]\nThis helps the AI grade in your voice.",
        border_style="blue"
    ))

    profile = load_profile()

    profile["name"] = Prompt.ask("Your name", default=profile.get("name", "Teacher"))
    profile["institution"] = Prompt.ask("Institution", default=profile.get("institution", "Robertson College"))
    profile["subject"] = Prompt.ask("Subject you teach", default=profile.get("subject", ""))
    profile["grade_level"] = Prompt.ask("Grade/year level", default=profile.get("grade_level", ""))
    profile["grading_style"] = Prompt.ask(
        "How would you describe your grading style?",
        default=profile.get("grading_style", "encouraging but firm")
    )

    console.print("\n[yellow]Now add some examples of feedback you've written before.[/yellow]")
    console.print("This teaches the AI to sound like you. Add 2-5 samples for best results.\n")

    while True:
        if not Confirm.ask("Add a voice sample (paste previous feedback you wrote)?"):
            break
        console.print("[dim]Paste your feedback sample, then press Enter twice:[/dim]")
        lines = []
        while True:
            line = input()
            if line == "" and lines and lines[-1] == "":
                break
            lines.append(line)
        sample = "\n".join(lines[:-1]).strip()
        if sample:
            profile.setdefault("voice_samples", []).append(sample)
            console.print(f"[green]Sample added ({len(profile['voice_samples'])} total)[/green]")

    save_profile(profile)
    console.print("\n[green]Profile saved![/green]")
    console.print(f"[dim]Stored at: {PROFILE_PATH}[/dim]")


@cli.command()
@click.argument("submission_file")
@click.option("--rubric", "-r", help="Path to rubric file (text)", default=None)
@click.option("--student", "-s", help="Student name", default="Student")
@click.option("--save", help="Save graded feedback to this file", default=None)
def grade(submission_file: str, rubric: Optional[str], student: str, save: Optional[str]):
    """Grade a student submission."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        console.print("[red]Error: ANTHROPIC_API_KEY environment variable not set.[/red]")
        console.print("Set it with: export ANTHROPIC_API_KEY=your-key-here")
        sys.exit(1)

    profile = load_profile()

    console.print(Panel.fit(
        f"[bold]Grading submission for: {student}[/bold]\nFile: {submission_file}",
        border_style="cyan"
    ))

    submission_text = read_submission(submission_file)
    rubric_text = None
    if rubric:
        rubric_text = Path(rubric).read_text(encoding="utf-8")

    if not submission_text.strip():
        console.print("[red]Submission appears to be empty.[/red]")
        sys.exit(1)

    console.print(f"[dim]Submission loaded: {len(submission_text)} characters[/dim]\n")

    # Run AI detection first
    console.print("[bold]Running AI content analysis...[/bold]")
    with Progress(SpinnerColumn(), TextColumn("{task.description}"), transient=True) as progress:
        progress.add_task("Analyzing...", total=None)
        ai_result = detect_ai_content(submission_text, profile)

    # Show AI detection result
    ai_table = Table(title="AI Content Detection", show_header=False, border_style="yellow")
    ai_table.add_column("Label", style="bold")
    ai_table.add_column("Value")
    ai_table.add_row("Level", {
        "none": "[green]No AI detected[/green]",
        "assisted": "[yellow]AI-assisted (grammar/structure) — ACCEPTABLE[/yellow]",
        "full": "[red]FULLY AI-GENERATED — NOT ACCEPTABLE[/red]",
    }.get(ai_result.get("level", "none"), "Unknown"))
    ai_table.add_row("Confidence", f"{ai_result.get('score', 0)}%")
    ai_table.add_row("Summary", ai_result.get("reasoning", ""))
    console.print(ai_table)

    # Grade the submission
    graded_feedback, _ = grade_submission(submission_text, rubric_text, profile)

    # Generate follow-up questions
    console.print("\n[bold cyan]Generating follow-up questions...[/bold cyan]")
    followup = generate_followup_questions(submission_text, graded_feedback, profile)

    console.print(Panel(followup, title="[bold]Follow-Up Questions to Confirm Understanding[/bold]", border_style="green"))

    # Save output
    output_content = f"""GRADED SUBMISSION
Student: {student}
File: {submission_file}
Graded by: {profile['name']} (AI-assisted)

AI DETECTION RESULT
Level: {ai_result.get('level', 'none')}
Score: {ai_result.get('score', 0)}%
Summary: {ai_result.get('reasoning', '')}

---

GRADING FEEDBACK
{graded_feedback}

---

FOLLOW-UP QUESTIONS
{followup}
"""

    if save:
        Path(save).write_text(output_content, encoding="utf-8")
        console.print(f"\n[green]Saved to: {save}[/green]")
    else:
        default_name = f"graded_{Path(submission_file).stem}.txt"
        if Confirm.ask(f"\nSave graded output to '{default_name}'?"):
            Path(default_name).write_text(output_content, encoding="utf-8")
            console.print(f"[green]Saved to: {default_name}[/green]")


@cli.command()
@click.argument("text")
def detect(text: str):
    """Quick AI detection check on pasted text."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        console.print("[red]Error: ANTHROPIC_API_KEY environment variable not set.[/red]")
        sys.exit(1)

    profile = load_profile()
    console.print("[bold]Analyzing text for AI content...[/bold]\n")
    result = detect_ai_content(text, profile)

    level_display = {
        "none": "[green]Human written[/green]",
        "assisted": "[yellow]AI-assisted (acceptable)[/yellow]",
        "full": "[red]Fully AI-generated (not acceptable)[/red]",
    }
    console.print(f"Result: {level_display.get(result.get('level', 'none'))}")
    console.print(f"Confidence: {result.get('score', 0)}%")
    console.print(f"Reasoning: {result.get('reasoning', '')}")
    if result.get("indicators"):
        console.print("\nIndicators:")
        for ind in result["indicators"]:
            console.print(f"  • {ind}")


@cli.command()
def profile():
    """View your current teacher profile."""
    p = load_profile()
    table = Table(title="Teacher Profile", border_style="blue")
    table.add_column("Setting", style="bold cyan")
    table.add_column("Value")
    table.add_row("Name", p.get("name", ""))
    table.add_row("Institution", p.get("institution", ""))
    table.add_row("Subject", p.get("subject", "") or "[dim]Not set[/dim]")
    table.add_row("Grade Level", p.get("grade_level", "") or "[dim]Not set[/dim]")
    table.add_row("Grading Style", p.get("grading_style", ""))
    table.add_row("Voice Samples", str(len(p.get("voice_samples", []))))
    console.print(table)


if __name__ == "__main__":
    cli()
