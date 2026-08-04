# Internal helper -- launched by start-app.ps1. Not meant to be run directly,
# though it's safe to do so.
$ErrorActionPreference = "Continue"
Set-Location (Join-Path $PSScriptRoot "backend")
uvicorn app.main:app --reload --port 8000
Write-Host ""
Read-Host "Backend stopped -- press Enter to close this window"
