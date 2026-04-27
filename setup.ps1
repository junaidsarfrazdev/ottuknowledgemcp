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

# --- Workspace configuration -------------------------------------------------
$EnvFile = Join-Path $Here ".env"
$CurrentWs = ""
if (Test-Path $EnvFile) {
    $match = Select-String -Path $EnvFile -Pattern '^OTTU_WORKSPACE=' | Select-Object -First 1
    if ($match) {
        $CurrentWs = ($match.Line -split '=', 2)[1]
    }
}
if (-not $CurrentWs -and $env:OTTU_WORKSPACE) {
    $CurrentWs = $env:OTTU_WORKSPACE
}

if (-not $CurrentWs) {
    $DefaultWs = Join-Path $HOME "ottu-workspace"
    $InputWs = Read-Host "▶ Where are your Ottu repos cloned? [$DefaultWs]"
    if ([string]::IsNullOrWhiteSpace($InputWs)) {
        $CurrentWs = $DefaultWs
    } else {
        $CurrentWs = $InputWs
    }
}

if (-not (Test-Path $EnvFile)) {
    if (Test-Path (Join-Path $Here ".env.example")) {
        Copy-Item (Join-Path $Here ".env.example") $EnvFile
    } else {
        New-Item -ItemType File -Path $EnvFile | Out-Null
    }
}

$content = Get-Content $EnvFile -Raw
if ($content -match '(?m)^OTTU_WORKSPACE=.*$') {
    $content = $content -replace '(?m)^OTTU_WORKSPACE=.*$', "OTTU_WORKSPACE=$CurrentWs"
    Set-Content -Path $EnvFile -Value $content -NoNewline
} else {
    Add-Content -Path $EnvFile -Value "OTTU_WORKSPACE=$CurrentWs"
}

Write-Host "▶ OTTU_WORKSPACE = $CurrentWs  (saved to .env)"

if (-not (Test-Path $CurrentWs)) {
    Write-Host "  ⚠ That directory doesn't exist yet. Create it and clone your repos:"
    Write-Host "     mkdir `"$CurrentWs`"; cd `"$CurrentWs`""
    Write-Host "     gh repo clone ottuco/checkout_sdk"
}

# --- Doctor ------------------------------------------------------------------
Write-Host "▶ Running doctor..."
try { python cli.py doctor } catch { }

Write-Host ""
Write-Host "✅ Setup complete."
Write-Host "   Next:"
Write-Host "     .\venv\Scripts\Activate.ps1"
Write-Host "     python cli.py index"
Write-Host "   Then wire Claude Code to server.py (see README)."
