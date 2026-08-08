# Teacher Grading AI — Robertson College

An AI-powered grading assistant that marks student papers **in the teacher's own voice**.

## Features

- **Grades papers** with per-question scores and comments
- **Sounds like you** — learns from your past feedback samples
- **AI detection** — distinguishes fully AI-written papers (not OK) from AI-assisted writing (OK)
- **Follow-up questions** — generates targeted questions to confirm students actually understood the material
- **Grammar notes** — flags grammar issues in student writing within comments
- **Supports** `.txt`, `.pdf`, and `.docx` submissions

---

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Set your API key

```bash
export ANTHROPIC_API_KEY=your-key-here
```

Get a key at: https://console.anthropic.com

### 3. Set up your teacher profile

```bash
python grader.py setup
```

This walks you through:
- Your name and institution
- Your subject and grade level
- Your grading style
- Paste in 2–5 examples of feedback you've written before (so the AI sounds like you)

---

## Usage

### Grade a submission

```bash
python grader.py grade student_paper.txt --student "Jane Smith"
```

With a rubric:
```bash
python grader.py grade student_paper.pdf --student "John Doe" --rubric rubric.txt --save graded_john.txt
```

### Quick AI detection check

```bash
python grader.py detect "paste student text here"
```

### View your profile

```bash
python grader.py profile
```

---

## AI Detection Policy

| Detection Level | What it means | Action |
|---|---|---|
| **None** | Human-written | Grade normally |
| **AI-assisted** | AI helped with grammar/structure | Note it, no penalty — this is acceptable |
| **Fully AI-generated** | AI wrote the whole paper | Flag in feedback, address directly |

Your aunt's policy: students can use AI to help clean up their writing, but they cannot submit entirely AI-written work. The tool automatically applies this distinction.

---

## Rubric Format (optional)

Create a plain text rubric file:

```
Question 1 (20 points): Explain the concept of X
Question 2 (30 points): Analyze the case study
Question 3 (25 points): Compare and contrast A vs B
Question 4 (25 points): Apply the theory to a real-world example
Total: 100 points
```

---

## Example Output

```
GRADED SUBMISSION
Student: Jane Smith
...

AI DETECTION RESULT
Level: assisted
Score: 45%
Summary: Writing shows AI-assisted polish but retains student voice and personal examples.

---

GRADING FEEDBACK

Question 1 — 17/20
Good work here Jane! You've captured the main idea, but you missed the nuance around X. 
Grammar note: "their" should be "there" in your second sentence.

Question 2 — 22/30
...

FOLLOW-UP QUESTIONS
1. You mentioned X in your answer — can you explain what would happen if Y changed? (Probing: do they understand causality)
2. ...
```
