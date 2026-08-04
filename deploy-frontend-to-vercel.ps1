# Run this from PowerShell: .\deploy-frontend-to-vercel.ps1
# Deploys frontend/ to Vercel's free Hobby tier (no card required).

$ErrorActionPreference = "Stop"

Write-Host "Checking for Vercel CLI..."
$vercelInstalled = Get-Command vercel -ErrorAction SilentlyContinue
if (-not $vercelInstalled) {
    Write-Host "Vercel CLI not found -- installing globally via npm..."
    npm install -g vercel
} else {
    Write-Host "Vercel CLI already installed: $(vercel --version)"
}

Write-Host ""
Write-Host "Logging in to Vercel (this opens your browser)..."
vercel login

Write-Host ""
Write-Host "Deploying frontend/ to production..."
Push-Location "$PSScriptRoot\frontend"
vercel --prod --yes
Pop-Location

Write-Host ""
Write-Host "Done. The public URL is printed above (also visible at vercel.com/dashboard)."
