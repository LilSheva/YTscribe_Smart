# YTscribe_Smart - menu launcher for POT + bot (OmniRoute: check only, desktop app)
param(
    [ValidateSet("", "start", "stop", "restart", "status")]
    [string]$Command = ""
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$Root = Split-Path -Parent $PSScriptRoot
$RunDir = Join-Path $Root ".run"
$PidPot = Join-Path $RunDir "pot.pid"
$PidBot = Join-Path $RunDir "bot.pid"
$ServiceLog = Join-Path $RunDir "service.log"
$BotLog = Join-Path $RunDir "yts_bot.log"

function Write-ServiceLog([string]$Message) {
    if (-not (Test-Path $RunDir)) {
        New-Item -ItemType Directory -Path $RunDir -Force | Out-Null
    }
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content -Path $ServiceLog -Value "[$ts] $Message" -Encoding UTF8
}

$Config = @{
    PotServerDir = "C:\Users\yakov\bgutil-ytdlp-pot-provider\server"
    PotPort      = 4416
    OmniPort     = 20128
    EnableLlm    = $true
    EnableDl     = $true
}

function Load-Env {
    $envFile = Join-Path $Root ".env"
    if (-not (Test-Path $envFile)) { return }

    foreach ($line in Get-Content $envFile -Encoding UTF8) {
        $t = $line.Trim()
        if (-not $t -or $t.StartsWith("#")) { continue }
        $idx = $t.IndexOf("=")
        if ($idx -lt 1) { continue }
        $key = $t.Substring(0, $idx).Trim()
        $val = $t.Substring($idx + 1).Trim()
        if ($val.StartsWith('"') -and $val.EndsWith('"')) {
            $val = $val.Substring(1, $val.Length - 2)
        }
        switch -Regex ($key) {
            "^POT_SERVER_DIR$"   { $Config.PotServerDir = $val }
            "^POT_PORT$"         { $Config.PotPort = [int]$val }
            "^OMNIROUTE_PORT$"   { $Config.OmniPort = [int]$val }
            "^ENABLE_LLM$"       { $Config.EnableLlm = ($val -match "^(?i:true|1|yes)$") }
            "^ENABLE_DOWNLOADER$" { $Config.EnableDl = ($val -match "^(?i:true|1|yes)$") }
        }
    }
}

function Test-PortOpen([int]$Port) {
    try {
        $c = New-Object System.Net.Sockets.TcpClient
        $c.Connect("127.0.0.1", $Port)
        $c.Close()
        return $true
    } catch {
        return $false
    }
}

function Read-PidFile([string]$Path) {
    if (-not (Test-Path $Path)) { return $null }
    $raw = (Get-Content $Path -Raw -ErrorAction SilentlyContinue)
    if (-not $raw) { return $null }
    $id = [int]($raw.Trim())
    if ($id -le 0) { return $null }
    return $id
}

function Test-ProcessAlive([int]$ProcessId) {
    if ($ProcessId -le 0) { return $false }
    return $null -ne (Get-Process -Id $ProcessId -ErrorAction SilentlyContinue)
}

function Get-BotProcesses {
    $mainMarker = Join-Path $Root "main.py"
    $venvPy = Join-Path $Root "venv\Scripts\python.exe"
    $found = @()

    try {
        $candidates = Get-CimInstance Win32_Process -ErrorAction Stop |
            Where-Object { $_.Name -in @("python.exe", "pythonw.exe") -and $_.CommandLine }
    } catch {
        Write-Host "[!] Could not enumerate processes: $_"
        return @()
    }

    foreach ($proc in $candidates) {
        $cmd = $proc.CommandLine
        $matchesProject = ($cmd -like "*$Root*") -or ($cmd -like "*$mainMarker*")
        $matchesBot = ($cmd -like "*main.py*") -or ($cmd -like "*$venvPy*" -and $cmd -like "*main.py*")
        if ($matchesProject -and $matchesBot) {
            $found += $proc
        }
    }

    return @($found | Sort-Object ProcessId -Unique)
}

function Show-BotProcesses {
    $procs = Get-BotProcesses
    if ($procs.Count -eq 0) {
        Write-Host "[--] Bot processes: none found"
        return $procs
    }

    Write-Host "[!] Bot processes found: $($procs.Count)"
    foreach ($proc in $procs) {
        Write-Host "    PID $($proc.ProcessId)"
    }
    return $procs
}

function Stop-AllBotProcesses {
    $procs = Get-BotProcesses
    if ($procs.Count -eq 0) {
        Write-Host "[~] Bot processes not found"
        Remove-Item $PidBot -Force -ErrorAction SilentlyContinue
        return
    }

    foreach ($proc in $procs) {
        $procId = [int]$proc.ProcessId
        if (Test-ProcessAlive $procId) {
            Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
            Write-Host "[OK] Bot stopped (PID $procId)"
        }
    }
    Remove-Item $PidBot -Force -ErrorAction SilentlyContinue
}

function Write-PidFile([string]$Path, [int]$ProcessId) {
    if (-not (Test-Path $RunDir)) {
        New-Item -ItemType Directory -Path $RunDir -Force | Out-Null
    }
    Set-Content -Path $Path -Value $ProcessId -Encoding ASCII -NoNewline
}

function Stop-ByPidFile([string]$Path, [string]$Name) {
    $procId = Read-PidFile $Path
    if (-not $procId) {
        Write-Host "[~] $Name - pid file not found"
        return
    }
    if (Test-ProcessAlive $procId) {
        Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
        Start-Sleep -Milliseconds 300
        if (Test-ProcessAlive $procId) {
            Start-Process -FilePath "taskkill.exe" `
                -ArgumentList @("/PID", "$procId", "/T", "/F") `
                -WindowStyle Hidden `
                -Wait `
                -ErrorAction SilentlyContinue | Out-Null
        }
        Write-Host "[OK] $Name stopped (PID $procId)"
        Write-ServiceLog "$Name stopped PID $procId"
    } else {
        Write-Host "[~] $Name - process not running (PID $procId)"
    }
    Remove-Item $Path -Force -ErrorAction SilentlyContinue
}

function Ensure-Venv {
    $envFile = Join-Path $Root ".env"
    if (-not (Test-Path $envFile)) {
        Write-Host "[X] .env not found - copy from .env.example"
        return $false
    }
    $py = Join-Path $Root "venv\Scripts\python.exe"
    if (-not (Test-Path $py)) {
        Write-Host "[*] Creating venv..."
        & python -m venv (Join-Path $Root "venv")
        if ($LASTEXITCODE -ne 0) {
            Write-Host "[X] Failed to create venv"
            return $false
        }
        & (Join-Path $Root "venv\Scripts\pip.exe") install -r (Join-Path $Root "requirements.txt")
    } else {
        Write-Host "[OK] venv found"
    }
    return $true
}

function Ensure-Pot {
    Write-Host "[POT] port $($Config.PotPort)..."

    $mainTs = Join-Path $Config.PotServerDir "src\main.ts"
    if (-not (Test-Path $mainTs)) {
        Write-Host "[X] POT not found: $($Config.PotServerDir)"
        Write-Host "    Set POT_SERVER_DIR in .env"
        return $false
    }

    if (-not (Get-Command deno -ErrorAction SilentlyContinue)) {
        Write-Host "[X] deno not in PATH - https://deno.land/"
        return $false
    }

    if (Test-PortOpen $Config.PotPort) {
        Write-Host "[OK] POT already listening on port $($Config.PotPort)"
        return $true
    }

    Write-Host "[*] Starting POT in background (no window)..."
    Write-ServiceLog "Starting POT on port $($Config.PotPort)"

    # Redirect через Start-Process ломает deno на Windows — запуск без окна, лог в service.log
    $p = Start-Process -FilePath "deno" `
        -ArgumentList @("run", "--allow-all", "src/main.ts") `
        -WorkingDirectory $Config.PotServerDir `
        -WindowStyle Hidden `
        -PassThru

    if (-not $p) {
        Write-Host "[X] Failed to start POT"
        Write-ServiceLog "POT start failed"
        return $false
    }
    Write-PidFile $PidPot $p.Id

    for ($i = 1; $i -le 20; $i++) {
        if (Test-PortOpen $Config.PotPort) {
            Write-Host "[OK] POT ready on port $($Config.PotPort)"
            Write-ServiceLog "POT ready PID $($p.Id)"
            return $true
        }
        Write-Host "    ... waiting for POT ($i/20)"
        Start-Sleep -Seconds 2
    }

    Write-Host "[X] POT did not start in time"
    Write-ServiceLog "POT timeout after 40s"
    return $false
}

function Test-OmniRoute {
    Write-Host "[OmniRoute] checking desktop app on port $($Config.OmniPort)..."

    if (-not (Test-PortOpen $Config.OmniPort)) {
        Write-Host "[X] OmniRoute not available on port $($Config.OmniPort)"
        Write-Host "    Start the OmniRoute desktop app and try again."
        return $false
    }

    $envFile = Join-Path $Root ".env"
    $raw = Get-Content $envFile -Raw -ErrorAction SilentlyContinue
    if ($raw -match "(?m)^OMNIROUTE_API_KEY=sk-x") {
        Write-Host "[X] OMNIROUTE_API_KEY is a placeholder in .env"
        return $false
    }

    Write-Host "[OK] OmniRoute responds on port $($Config.OmniPort)"
    return $true
}

function Start-Bot {
    $running = Get-BotProcesses
    if ($running.Count -gt 0) {
        Write-Host "[!] Found $($running.Count) bot process(es) - stopping before start..."
        Write-ServiceLog "Stopping $($running.Count) existing bot process(es)"
        Stop-AllBotProcesses
        Start-Sleep -Seconds 1
    }

    $existing = Read-PidFile $PidBot
    if ($existing -and (Test-ProcessAlive $existing)) {
        Write-Host "[!] Bot already running (PID $existing)"
        return $false
    }

    $pyw = Join-Path $Root "venv\Scripts\pythonw.exe"
    if (-not (Test-Path $pyw)) {
        $pyw = Join-Path $Root "venv\Scripts\python.exe"
    }
    Write-Host "[Bot] starting in background (log: .run\yts_bot.log)..."

    $p = Start-Process -FilePath $pyw `
        -ArgumentList @("-u", "main.py") `
        -WorkingDirectory $Root `
        -WindowStyle Hidden `
        -PassThru

    if (-not $p) {
        Write-Host "[X] Failed to start bot"
        Write-ServiceLog "Bot start failed"
        return $false
    }
    Write-PidFile $PidBot $p.Id
    Write-ServiceLog "Bot started PID $($p.Id)"
    Start-Sleep -Seconds 3

    if (-not (Test-ProcessAlive $p.Id)) {
        Write-Host "[X] Bot exited immediately - check $BotLog"
        Write-ServiceLog "Bot exited immediately - see yts_bot.log"
        return $false
    }

    Write-Host "[OK] Bot started, PID $($p.Id)"
    return $true
}

function Show-Status {
    Write-Host ""
    Write-Host "=== YTscribe_Smart status ==="
    Write-Host ""

    Show-ServiceStatus "POT server" $PidPot $Config.PotPort
    if (Test-PortOpen $Config.OmniPort) {
        Write-Host "[OK] OmniRoute app - port $($Config.OmniPort) open"
    } else {
        Write-Host "[--] OmniRoute app - port $($Config.OmniPort) closed (start desktop app)"
    }
    Show-ServiceStatus "Telegram bot" $PidBot $null
    Show-BotProcesses | Out-Null
    Write-Host ""
    Write-Host "Logs:"
    Write-Host "  Bot:     $BotLog"
    Write-Host "  Service: $ServiceLog"
    Write-Host "  (POT: no stdout capture; check service.log and port $($Config.PotPort))"
    Write-Host ""
}

function Show-ServiceStatus([string]$Name, [string]$PidPath, [Nullable[int]]$Port) {
    $procId = Read-PidFile $PidPath
    if ($procId -and (Test-ProcessAlive $procId)) {
        Write-Host "[OK] $Name - running, PID $procId"
        return
    }
    if ($Port -and (Test-PortOpen $Port)) {
        Write-Host "[OK] $Name - port $Port open (started outside script)"
        return
    }
    Write-Host "[--] $Name - stopped"
}

function Start-All {
    Write-Host ""
    Write-Host "============================================"
    Write-Host "  Start: POT + bot"
    Write-Host "============================================"
    Write-Host ""

    if (-not (Ensure-Venv)) { return $false }

    if ($Config.EnableDl) {
        if (-not (Ensure-Pot)) { return $false }
    } else {
        Write-Host "[~] ENABLE_DOWNLOADER=False - POT skipped"
    }

    if ($Config.EnableLlm) {
        if (-not (Test-OmniRoute)) { return $false }
    } else {
        Write-Host "[~] ENABLE_LLM=False - OmniRoute check skipped"
    }

    if (-not (Start-Bot)) { return $false }

    Write-Host ""
    Write-Host "[OK] Done. POT and bot run in background (no windows)."
    Write-Host "     Logs: .run\yts_bot.log, .run\service.log"
    Write-ServiceLog "Start-All completed"
    Show-Status
    return $true
}

function Stop-All {
    Write-Host ""
    Write-Host "============================================"
    Write-Host "  Stop bot and POT"
    Write-Host "============================================"
    Write-Host ""
    Stop-AllBotProcesses
    Stop-ByPidFile $PidPot "POT server"
    Write-Host ""
    Write-Host "[OK] Stopped. OmniRoute desktop app is not touched."
}

function Restart-Bot {
    Write-Host ""
    Write-Host "============================================"
    Write-Host "  Restart bot only (POT unchanged)"
    Write-Host "============================================"
    Write-Host ""

    Stop-AllBotProcesses
    Start-Sleep -Seconds 2

    if (-not (Ensure-Venv)) { return $false }

    if ($Config.EnableLlm) {
        if (-not (Test-OmniRoute)) { return $false }
    } else {
        Write-Host "[~] ENABLE_LLM=False - OmniRoute check skipped"
    }

    if (-not (Start-Bot)) { return $false }

    Write-Host ""
    Write-Host "[OK] Bot restarted."
    Show-Status
    return $true
}

function Invoke-GDriveSync([string]$Command) {
    $py = Join-Path $Root "venv\Scripts\python.exe"
    if (-not (Test-Path $py)) {
        Write-Host "[X] venv not found - run option 1 first"
        return $false
    }
    Write-Host "[GDrive sync] $Command ..."
    & $py (Join-Path $Root "scripts\gdrive_sync_cli.py") $Command
    return ($LASTEXITCODE -eq 0)
}

function Show-Menu {
    while ($true) {
        Clear-Host
        Write-Host ""
        Write-Host "  ============================================"
        Write-Host "    YTscribe_Smart"
        Write-Host "  ============================================"
        Write-Host ""
        Write-Host "    1. Start all (POT + bot, background, no windows)"
        Write-Host "    2. Stop bot and POT"
        Write-Host "    3. Restart bot only (POT unchanged)"
        Write-Host "    4. Status"
        Write-Host ""
        Write-Host "    5. Start POT only"
        Write-Host "    6. Stop POT only"
        Write-Host "    7. Start bot only"
        Write-Host "    8. Stop bot only"
        Write-Host "    9. Find and kill all bot processes"
        Write-Host "   10. GDrive sync (audit / repair)"
        Write-Host ""
        Write-Host "    0. Exit"
        Write-Host ""
        $choice = Read-Host "Select option"

        switch ($choice) {
            "1" { Start-All | Out-Null; Pause-Return }
            "2" { Stop-All; Pause-Return }
            "3" { Restart-Bot | Out-Null; Pause-Return }
            "4" { Show-Status; Pause-Return }
            "5" { Ensure-Pot | Out-Null; Pause-Return }
            "6" { Stop-ByPidFile $PidPot "POT server"; Pause-Return }
            "7" {
                if (Ensure-Venv) {
                    if ($Config.EnableLlm -and -not (Test-OmniRoute)) {
                        Pause-Return
                        continue
                    }
                    Start-Bot | Out-Null
                }
                Pause-Return
            }
            "8" { Stop-AllBotProcesses; Pause-Return }
            "9" {
                Show-BotProcesses | Out-Null
                Stop-AllBotProcesses
                Pause-Return
            }
            "10" {
                $sub = Read-Host "  audit / repair-dry / repair"
                switch ($sub.ToLower()) {
                    "audit"       { Invoke-GDriveSync "audit" | Out-Null }
                    "repair-dry"  { Invoke-GDriveSync "repair-dry" | Out-Null }
                    "repair"      { Invoke-GDriveSync "repair" | Out-Null }
                    default {
                        Write-Host "[!] Enter: audit, repair-dry, or repair"
                    }
                }
                Pause-Return
            }
            "0" { return }
            default {
                Write-Host "[!] Invalid option"
                Start-Sleep 1
            }
        }
    }
}

function Pause-Return {
    Write-Host ""
    Read-Host "Press Enter to continue"
}

if (-not (Test-Path $RunDir)) {
    New-Item -ItemType Directory -Path $RunDir -Force | Out-Null
}
Load-Env

switch ($Command.ToLower()) {
    "start"   { if (-not (Start-All)) { exit 1 } }
    "stop"    { Stop-All }
    "restart" { if (-not (Restart-Bot)) { exit 1 } }
    "status"  { Show-Status }
    default   { Show-Menu }
}
