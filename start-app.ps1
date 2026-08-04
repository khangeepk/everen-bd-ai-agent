# Run this from PowerShell: .\start-app.ps1
# Starts the backend and frontend together, each in its own window, and
# opens the app in your browser once both are up. If a window closes
# instantly, that server crashed -- the window stays open on error so you
# can read what went wrong.

$root = $PSScriptRoot

Write-Host "Starting backend (port 8000)..."
Start-Process powershell -ArgumentList @("-NoExit", "-File", (Join-Path $root "backend-start.ps1"))

Write-Host "Starting frontend (port 3000)..."
Start-Process powershell -ArgumentList @("-NoExit", "-File", (Join-Path $root "frontend-start.ps1"))

Write-Host ""
Write-Host "Waiting 15 seconds for both servers to finish starting..."
Start-Sleep -Seconds 15

Write-Host "Opening http://localhost:3000 in your browser..."
Start-Process "http://localhost:3000"

Write-Host ""
Write-Host "Two new PowerShell windows should now be open and running -- do not close them while you use the app."
Write-Host "If the browser tab still shows a connection error, wait a few more seconds and press Reload."
