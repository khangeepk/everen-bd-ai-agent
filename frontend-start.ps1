# Internal helper -- launched by start-app.ps1. Not meant to be run directly,
# though it's safe to do so.
$ErrorActionPreference = "Continue"
Set-Location (Join-Path $PSScriptRoot "frontend")
npm run dev
Write-Host ""
Read-Host "Frontend stopped -- press Enter to close this window"
