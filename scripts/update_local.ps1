# Weekly Google Scholar refresh for the homepage, run from this PC.
#
# Google Scholar refuses requests from GitHub's servers (HTTP 403), so the
# GitHub Actions workflow cannot refresh the data. A home or office connection
# works, so a Windows scheduled task runs this script once a week instead.
#
#   scripts\update_local.ps1             refresh now (what the task runs)
#   scripts\update_local.ps1 -Install    create or replace the weekly task
#   scripts\update_local.ps1 -Uninstall  remove the weekly task
#
# Log: %LOCALAPPDATA%\scholar-site-update\update.log
#
# Keep this file ASCII-only: Windows PowerShell 5.1 reads it as ANSI.

param(
    [switch]$Install,
    [switch]$Uninstall
)

$ErrorActionPreference = 'Continue'

$Repo      = Split-Path -Parent $PSScriptRoot
$TaskName  = 'Update homepage publications (Google Scholar)'
$LogDir    = Join-Path $env:LOCALAPPDATA 'scholar-site-update'
$Log       = Join-Path $LogDir 'update.log'
$DataFiles = @('data/publications.json', 'data/scholar_meta.json', 'index.html')

if ($Uninstall) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Removed scheduled task: $TaskName"
    exit 0
}

if ($Install) {
    $powershell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $arguments  = '-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "{0}"' -f $PSCommandPath
    $action   = New-ScheduledTaskAction -Execute $powershell -Argument $arguments -WorkingDirectory $Repo
    $trigger  = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday -At '10:00'
    # StartWhenAvailable: if the PC was off or asleep on Monday at 10:00, run at
    # the next opportunity instead of skipping the week.
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RunOnlyIfNetworkAvailable `
        -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 20)
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
        -Description 'Refreshes citation counts and the paper list on sungeunkim0127.github.io from Google Scholar, then pushes to GitHub.' `
        -Force | Out-Null
    Write-Host "Installed scheduled task: $TaskName (Mondays 10:00, runs late if the PC was off)"
    exit 0
}

# ---------------------------------------------------------------- refresh ----

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Log([string]$Message) {
    $line = '{0}  {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message
    Add-Content -Path $Log -Value $line -Encoding UTF8
}

# Runs a program, copies its output into the log, returns its exit code.
function Invoke-Logged {
    param([string]$Exe, [string[]]$ArgList)
    & $Exe @ArgList 2>&1 | ForEach-Object { Log "    $_" }
    return $LASTEXITCODE
}

# Puts the three generated files back as they were. Only called after the
# working tree was confirmed clean, so it can only discard this run's output.
function Undo-Changes {
    Invoke-Logged git (@('checkout', '--') + $DataFiles) | Out-Null
}

function Stop-Run([string]$Message, [int]$Code) {
    Log $Message
    Log '---- finished ----'
    exit $Code
}

# Keep the log from growing without bound.
if ((Test-Path $Log) -and (@(Get-Content $Log).Count -gt 2000)) {
    $tail = Get-Content $Log -Tail 1000
    Set-Content -Path $Log -Value $tail -Encoding UTF8
}

Log '---- weekly Scholar refresh started ----'
Set-Location $Repo

$env:PYTHONUTF8          = '1'      # titles contain non-ASCII characters
$env:PYTHONIOENCODING    = 'utf-8'
$env:GIT_TERMINAL_PROMPT = '0'      # fail instead of waiting for a password
$env:GCM_INTERACTIVE     = 'never'  # no sign-in window from a background task

if (-not (Get-Command python -ErrorAction SilentlyContinue)) { Stop-Run 'FAILED: python is not on PATH.' 1 }
if (-not (Get-Command git -ErrorAction SilentlyContinue))    { Stop-Run 'FAILED: git is not on PATH.' 1 }

# Never touch work in progress: only run on a clean checkout of main.
$branch = (& git rev-parse --abbrev-ref HEAD 2>$null)
if ($branch -ne 'main') { Stop-Run "SKIPPED: repository is on branch '$branch', not main." 2 }

$dirty = (& git status --porcelain --untracked-files=no 2>$null)
if ($dirty) { Stop-Run 'SKIPPED: the repository has uncommitted changes. Commit or discard them; the next run will pick up.' 2 }

if ((Invoke-Logged git @('pull', '--rebase', 'origin', 'main')) -ne 0) {
    Stop-Run 'FAILED: could not pull the latest site from GitHub.' 1
}

if ((Invoke-Logged python @('scripts/scrape_scholar.py')) -ne 0) {
    Undo-Changes
    Stop-Run 'FAILED: Google Scholar could not be read this time. Nothing was changed.' 1
}

if ((Invoke-Logged python @('scripts/generate_html.py')) -ne 0) {
    Undo-Changes
    Stop-Run 'FAILED: could not rebuild the publication list. Nothing was changed.' 1
}

Invoke-Logged git (@('add', '--') + $DataFiles) | Out-Null
& git diff --cached --quiet
if ($LASTEXITCODE -ne 0) {
    if ((Invoke-Logged git @('commit', '-m', 'chore: update publications from Google Scholar')) -ne 0) {
        Invoke-Logged git (@('reset', '-q', 'HEAD', '--') + $DataFiles) | Out-Null
        Undo-Changes
        Stop-Run 'FAILED: could not commit the refreshed data.' 1
    }
} else {
    Log 'No changes since the last refresh.'
}

# Always push: this also delivers a commit left behind by an earlier failed push.
if ((Invoke-Logged git @('push', 'origin', 'main')) -ne 0) {
    Stop-Run 'FAILED: could not push to GitHub. The refresh is committed locally and will be pushed on the next run.' 1
}

Stop-Run 'OK: site data is up to date on GitHub.' 0
