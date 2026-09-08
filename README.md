# CloudServe Support System

An AI-assisted customer support pipeline for CloudServe Solutions, built as the
IIT Roorkee Forward Deployed AI Engineering capstone project.

> Status: scaffold only. Fill in each section as the corresponding component
> is built. Nothing below should describe something that does not actually
> work yet — the README is tested literally, on a clean checkout, as part of
> grading (acceptance criterion A1).

## What this is

CloudServe Solutions receives 500+ support tickets a week across four
channels (email, live chat, documentation comments, community forum) and is
missing its two-hour first-response SLA by a wide margin, with fewer than
half of tickets resolved on first contact. This system is not "a chatbot" —
see `docs/architecture.md` and the discovery workbook in
`../Capstone_Pack/02_Stage_Workbooks/` (or the copy in this repo's report
appendix) for the evidence behind what it actually does instead.

## Setup

```bash
# 1. Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate        # macOS/Linux
# .venv\Scripts\activate         # Windows

# 2. Install dependencies
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

# 3. Configure environment variables
echo ".env" >> .gitignore
cp .env.example .env
# then edit .env and add your own OPENROUTER_API_KEY (free tier is sufficient)

# 4. Confirm model access works
python -c "
import os, requests
from dotenv import load_dotenv
load_dotenv()
r = requests.post('https://openrouter.ai/api/v1/chat/completions',
    headers={'Authorization': f\"Bearer {os.environ['OPENROUTER_API_KEY']}\"},
    json={'model': os.environ['MODEL_NAME'], 'messages': [{'role':'user','content':'Reply with the word ready.'}]},
    timeout=30)
print(r.status_code, r.json())
"
```

## Running the system

```bash
# start the API
python -m src.api

# run the full evaluation set, unattended, against ANY file with the same schema
python -m evaluation.harness --input data/validation_tickets.json --output evaluation/results/

# run the tests
python -m pytest tests/ -v
```

The harness takes `--input` and `--output` as arguments rather than a
hardcoded filename, because after submission it is run against a hidden
120-ticket set with the same schema that this repo has never seen.

## Project structure

```
src/                 pipeline components (ingest, classify, retrieve, route, generate, guardrails), logging, API
prompts/build/        prompts that run inside the system, versioned
prompts/evaluation/    prompts used to judge output, versioned
tests/                 unit and integration tests
evaluation/harness.py  runs the full ticket set end to end, unattended
evaluation/results/    dated output from each run
docs/architecture.md   system design explanation
data/                  ticket/documentation samples used in development
storage/               generated at runtime (Chroma index, SQLite decision log) — never committed
.github/workflows/     CI pipeline
```

## Testing

```bash
python -m pytest tests/ -v
```

## AI tool use declaration

See the report for the full declaration of where AI tools were used in this
project's development, per the capstone's Section 09 requirement.
