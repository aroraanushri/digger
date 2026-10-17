# Digger

**Operational answers. Traceable evidence.**

A local-first asset intelligence copilot. CopilotKit provides chat and interactive results; Gemma proposes validated plans; DuckDB and Python calculate answers. Field updates preserve original evidence.

## Run

Install Python 3.12, uv, and Node 24 LTS (Node 22.15 also supported).

```powershell
uv venv --python 3.12
uv pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn backend.app:app --host 127.0.0.1 --port 8000
```

In another terminal:

```powershell
cd frontend
npm.cmd ci
npm.cmd run dev
```

Open **http://localhost:3000**. For free-form questions, install Ollama and run `ollama pull gemma4:e2b-it-q4_K_M`. Analytical starter cards work without the model. Missing inference is reported explicitly, never replaced by fake answers.

The first backend startup imports the bundled dataset. Databases live in ignored `data/`. The challenge analysis date is **2026-10-16**; advance it to include new field observations.

## Working capabilities

- Idempotent ingestion and source-linked status, temperature, downtime, corrected spill, cost, and maintenance-recurrence analyses.
- Interactive cards, filterable tables, charts, coordinate maps, heatmaps, source records, and manuals.
- Reviewed field inspections, shared notes, and historical corrections with original preservation, attribution, version checks, and duplicate protection.
- Persistent private user memory and shared operational knowledge.
- Self-hosted CopilotKit v2 runtime, Python AG-UI backend, and local Gemma structured planning.

This is a **trusted local hackathon application**. The user picker is not authentication. Do not expose the demo server publicly. The supplied data and operating rules are fictional. Recurrence counts are not failure probabilities.

## Verify

```powershell
.\.venv\Scripts\python.exe -m pytest -q
cd frontend
npm.cmd run typecheck
npm.cmd run build
```

## Configuration

| Variable | Default |
|---|---|
| `DIGGER_DATASET` | Bundled `sample-data` directory |
| `DIGGER_STATE` | Repository `data` directory |
| `OLLAMA_BASE_URL` | `http://127.0.0.1:11434` |
| `OLLAMA_MODEL` | `gemma4:e2b-it-q4_K_M` |
| `DIGGER_API_URL` (frontend) | `http://127.0.0.1:8000` |

## Documentation

- [Windows / RTX 5070 setup](docs/SETUP_WINDOWS.md)
- [Architecture and unique features](docs/ARCHITECTURE.md)
- [Approach and technology rationale](docs/APPROACH.md)
- [Implementation status and limitations](docs/STATUS.md)

Digger code is MIT licensed. The bundled FlytBase dataset retains its original MIT license and attribution.
