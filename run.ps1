# Start PC Price Tracker locally
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path .\.venv\Scripts\python.exe)) {
    Write-Host "Creating venv..."
    python -m venv .venv
    .\.venv\Scripts\python.exe -m pip install --upgrade pip
    .\.venv\Scripts\pip.exe install -r requirements.txt
}

# Prefer explicit PORT, else try a few free local ports
$preferred = @(8877, 8788, 8787, 8899, 5055)
if ($env:PORT) {
    $preferred = @([int]$env:PORT) + $preferred
}

function Test-PortFree([int]$Port) {
    $listener = $null
    try {
        $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $Port)
        $listener.Start()
        return $true
    } catch {
        return $false
    } finally {
        if ($listener) { $listener.Stop() }
    }
}

function Stop-ListenersOnPort([int]$Port) {
    $conns = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    foreach ($c in $conns) {
        $procId = $c.OwningProcess
        if ($procId -and $procId -ne 0) {
            $proc = Get-Process -Id $procId -ErrorAction SilentlyContinue
            if ($proc -and $proc.ProcessName -match 'python|uvicorn') {
                Write-Host "Stopping previous tracker on port $Port (PID $procId)..."
                Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
                Start-Sleep -Milliseconds 400
            }
        }
    }
}

$port = $null
foreach ($p in $preferred) {
    Stop-ListenersOnPort $p
    if (Test-PortFree $p) {
        $port = $p
        break
    }
}

if (-not $port) {
    Write-Error "No free port found among: $($preferred -join ', '). Set `$env:PORT to another port."
    exit 1
}

Write-Host ""
Write-Host "PC Price Tracker"
Write-Host "Open:  http://127.0.0.1:$port"
Write-Host "Stop:  Ctrl+C"
Write-Host ""

& .\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port $port --reload
