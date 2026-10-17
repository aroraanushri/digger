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

The default backend startup imports the organizer's test dataset at `flytbase-ahc-swe-asset-intelligence-dataset-main/test-data/dataset` (600 wells; analysis date **2027-01-15**). The earlier 200-well sample remains in `sample-data`. Databases live in ignored `data/`.

## Replace the dataset

The current checkout is already configured to use the new test dataset. Its organizer drop folder is `test-data`, while the actual dataset root is the `dataset` subfolder containing `assets/` and `fleet/`.

1. Stop the FastAPI backend. Keep the original `sample-data` folder unchanged.
2. Validate the test dataset and replay the current shared-knowledge ledger against it:

```powershell
$env:DIGGER_DATASET = (Resolve-Path '.\flytbase-ahc-swe-asset-intelligence-dataset-main\test-data\dataset').Path
.\.venv\Scripts\python.exe -m backend.validate_dataset $env:DIGGER_DATASET --state .\data
```

The command exits with an error if files, IDs, references, reports, manuals, users, or saved updates are incompatible. Its success output includes the dataset hash, record counts, and replayed knowledge version. `DIGGER_DATASET` is optional for this checkout because the test dataset is the default; use it to select another compatible dataset.

3. In the same PowerShell window, restart the backend:

```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.app:app --host 127.0.0.1 --port 8000
```

4. Confirm the imported counts and dataset version:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/health | Format-List
```

The existing `data/knowledge.sqlite` retains shared corrections, policies, notes, and private conversation memory. For a completely clean evaluation state, choose a new state directory and validate without `--state`:

```powershell
$env:DIGGER_STATE = Join-Path $PWD 'data-new-dataset'
.\.venv\Scripts\python.exe -m backend.validate_dataset $env:DIGGER_DATASET
.\.venv\Scripts\python.exe -m uvicorn backend.app:app --host 127.0.0.1 --port 8000
```

No frontend rebuild is needed. Set the **Analysis date** in the UI to the date required by the new dataset.

## Working capabilities

- Idempotent ingestion and source-linked status, temperature, downtime, corrected spill, cost, maintenance-recurrence, and predictive maintenance-priority analyses.
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
| `DIGGER_DATASET` | `flytbase-ahc-swe-asset-intelligence-dataset-main/test-data/dataset` |
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
