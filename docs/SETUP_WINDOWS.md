# Windows setup — RTX 5070 laptop

Target: Windows 11, 32 GB RAM, 8 GB VRAM. Plug in the charger and reserve about 30 GB free storage for tools, models and dependencies.

## 1. GPU

Install Windows updates and the current compatible NVIDIA laptop driver from https://www.nvidia.com/en-us/drivers/. Restart, then open PowerShell:

```powershell
nvidia-smi
```

It should identify your GPU and memory. Native Windows Ollama supplies its inference runtime. A separate CUDA Toolkit, PyTorch, Docker or WSL is not required.

## 2. Development tools

```powershell
winget --version
winget install --id Git.Git -e --source winget
winget install --id astral-sh.uv -e --source winget
```

If winget is missing, install/update Microsoft's App Installer. Install **Node 24 LTS, Windows x64** from https://nodejs.org/en/download. Optionally install VS Code:

```powershell
winget install --id Microsoft.VisualStudioCode -e --source winget
```

Reopen PowerShell:

```powershell
git --version
node --version
npm.cmd --version
uv --version
uv python install 3.12
```

Use `npm.cmd` if PowerShell blocks `npm.ps1`; no global execution-policy change is needed.

## 3. Local Gemma

Install https://ollama.com/download/windows. Open Ollama, then reopen PowerShell:

```powershell
ollama --version
[Environment]::SetEnvironmentVariable("OLLAMA_CONTEXT_LENGTH", "8192", "User")
[Environment]::SetEnvironmentVariable("OLLAMA_NUM_PARALLEL", "1", "User")
[Environment]::SetEnvironmentVariable("OLLAMA_MAX_LOADED_MODELS", "1", "User")
```

Quit Ollama from the tray and relaunch it before downloading:

```powershell
ollama pull gemma4:e2b-it-q4_K_M
ollama run gemma4:e2b-it-q4_K_M
```

Ask a short question, exit with `/bye`, then check:

```powershell
ollama ps
nvidia-smi
Invoke-RestMethod -Uri http://localhost:11434/api/tags
```

Check GPU allocation. Model download size is not total runtime memory. Begin with E2B, one concurrent request and 8K context. Avoid 26B/31B or 64K context on this laptop. Try E4B only after measuring E2B's quality and memory use.

## 4. Clone and install

```powershell
New-Item -ItemType Directory -Path "$env:USERPROFILE\Projects" -Force
Set-Location "$env:USERPROFILE\Projects"
git clone https://github.com/aroraanushri/digger.git
Set-Location digger
uv venv --python 3.12
uv pip install -r requirements.txt
```

If the checkout already exists, use it instead of cloning inside it. Confirm the organizer test dataset:

```powershell
(Get-ChildItem .\flytbase-ahc-swe-asset-intelligence-dataset-main\test-data\dataset\assets -Directory).Count
```

Expected: 600 well folders. The earlier 200-well sample remains available under `sample-data`.

## 5. Start Digger

Terminal A, repository root:

```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.app:app --host 127.0.0.1 --port 8000
```

Terminal B:

```powershell
cd frontend
npm.cmd ci
npm.cmd run dev
```

Open http://localhost:3000. Next.js hosts the frontend and CopilotKit runtime, proxying requests to Python. FastAPI docs: http://localhost:8000/docs. Ollama: http://localhost:11434.

Analytical starter cards work without Ollama. Free-form questions require the model. Missing inference is reported explicitly.

## 6. Verify and demonstrate

```powershell
# Repository root
.\.venv\Scripts\python.exe -m pytest -q
# frontend directory
npm.cmd run typecheck
npm.cmd run build
```

Try the stopped-wells shortcut, open evidence, and submit a reviewed correction. Analysis defaults to 2027-01-15 for the test dataset. Original files remain unchanged; updates persist in `data/`.

## Troubleshooting

| Problem | Action |
|---|---|
| Command not recognized | Reopen PowerShell after installation |
| `npm ci` reports a missing lockfile | In `frontend`, run `npm.cmd install` to generate `package-lock.json`, then keep the lockfile in version control |
| `'next' is not recognized` | In `frontend`, complete dependency installation successfully before running `npm.cmd run dev` |
| GPU unavailable | Install compatible driver and restart |
| Ollama connection refused | Open Ollama from Start |
| Port 11434 already used | Background Ollama may already be running; avoid a second server |
| Slow inference | Check GPU use, charger, power mode, other GPU apps and context size |
| Out of memory | Use E2B, close GPU-heavy apps, lower context and backend `num_ctx` to 4096 |
| Backend disconnected | Start uvicorn from the root and inspect its terminal |
| Stale update rejected | Refresh, inspect the current state, then review and resubmit |

Ollama logs: `%LOCALAPPDATA%\Ollama\server.log`. Application state: `data/`. Keep these databases to retain field updates and memory.

This version is for trusted local use. The user picker is not authentication. Phone/LAN access and production authorization require additional setup; do not expose the demo API or Ollama publicly.
