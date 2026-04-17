# One-shot setup for OttuKnowledgeMCP (Windows PowerShell)
$ErrorActionPreference = "Stop"

$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Here

Write-Host "▶ Creating venv..."
if (-not (Test-Path "venv")) {
    python -m venv venv
}
& "$Here\venv\Scripts\Activate.ps1"

Write-Host "▶ Installing Python dependencies..."
python -m pip install --upgrade pip | Out-Null
pip install -r requirements.txt

Write-Host "▶ Checking git-lfs..."
if (Get-Command git-lfs -ErrorAction SilentlyContinue) {
    git lfs install | Out-Null
    Write-Host "  git-lfs OK"
} else {
    Write-Host "  ⚠ git-lfs not found. Install with: winget install GitHub.GitLFS"
}

Write-Host "▶ Running doctor..."
try { python cli.py doctor } catch { }

Write-Host ""
Write-Host "✅ Setup complete."
Write-Host "   Next:"
Write-Host "     .\venv\Scripts\Activate.ps1"
Write-Host "     python cli.py index"
Write-Host "   Then wire Claude Code to server.py (see README)."
