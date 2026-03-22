$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$repoRootLower = $repoRoot.ToLowerInvariant()
$script:killed = @()

function Stop-RepoProcess {
    param(
        [Parameter(Mandatory = $true)]
        $ProcessRecord
    )

    try {
        Stop-Process -Id $ProcessRecord.ProcessId -Force -ErrorAction Stop
        $script:killed += [pscustomobject]@{
            pid  = $ProcessRecord.ProcessId
            name = $ProcessRecord.Name
        }
    } catch {
        Write-Warning "Failed to stop PID $($ProcessRecord.ProcessId) ($($ProcessRecord.Name)): $($_.Exception.Message)"
    }
}

$candidates = Get-CimInstance Win32_Process | Where-Object {
    $_.ExecutablePath -or $_.CommandLine
}

foreach ($process in $candidates) {
    $name = [string]$process.Name
    $commandLine = ([string]$process.CommandLine).ToLowerInvariant()
    $executablePath = ([string]$process.ExecutablePath).ToLowerInvariant()
    $matchesRepo = $commandLine.Contains($repoRootLower) -or $executablePath.Contains($repoRootLower)
    if (-not $matchesRepo) {
        continue
    }

    if ($name -in @("formal-claim-desktop.exe", "cargo.exe", "node.exe", "vite.exe", "python.exe")) {
        Stop-RepoProcess -ProcessRecord $process
    }
}

if (-not $script:killed.Count) {
    Write-Output "No repo-owned dev processes found."
    exit 0
}

$script:killed |
    Sort-Object name, pid |
    Format-Table -AutoSize |
    Out-String |
    Write-Output
