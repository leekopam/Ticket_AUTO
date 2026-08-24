[CmdletBinding(DefaultParameterSetName = "Fast")]
param(
    [Parameter(ParameterSetName = "Fast")]
    [switch]$Fast,

    [Parameter(Mandatory, ParameterSetName = "Release")]
    [switch]$Release,

    [string]$SpecPath = "build_support\specs\Ticket_AUTO_flat.spec",
    [double]$ExeStartupSeconds = 10
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Invoke-LoggedNativeCommand {
    param(
        [string]$FilePath,
        [string[]]$Arguments,
        [string]$LogPath
    )

    $previousErrorPreference = $ErrorActionPreference
    try {
        # Native tools such as PyInstaller write progress messages to stderr.
        # Preserve the output, but use the process exit code as the failure signal.
        $ErrorActionPreference = "Continue"
        & $FilePath @Arguments *>&1 | Tee-Object -FilePath $LogPath
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousErrorPreference
    }

    if ($exitCode -ne 0) {
        throw "Command failed ($exitCode): $FilePath $($Arguments -join ' ')"
    }
}

$scriptRoot = Split-Path -Parent $PSCommandPath
$repoRoot = (Resolve-Path -LiteralPath (Join-Path $scriptRoot "..\..")).Path
Set-Location $repoRoot

$venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
    throw "Python virtual environment not found: $venvPython"
}
if ($ExeStartupSeconds -le 0) {
    throw "ExeStartupSeconds must be greater than zero."
}

$runMode = if ($Release) { "Release" } else { "Fast" }
$timestamp = Get-Date -Format "yyyyMMdd_HHmmss_fff"
$resultsRoot = Join-Path $repoRoot "artifacts\test-results\$timestamp"
New-Item -ItemType Directory -Path $resultsRoot -Force | Out-Null

$summaryPath = Join-Path $resultsRoot "summary.md"
$startedAt = Get-Date
$status = "FAILED"
$failureMessage = ""
$completedSteps = [System.Collections.Generic.List[string]]::new()

try {
    $env:TICKET_AUTO_RUN_PLAYWRIGHT_SMOKE = if ($Release) { "1" } else { "0" }
    $pytestLog = Join-Path $resultsRoot "pytest.log"
    $pytestXml = Join-Path $resultsRoot "pytest.xml"
    Invoke-LoggedNativeCommand $venvPython @(
        "-m", "pytest", "tests", "-q", "--tb=short", "-p", "no:cacheprovider",
        "--junitxml=$pytestXml"
    ) $pytestLog
    $completedSteps.Add("pytest")

    if ($Release) {
        $buildLog = Join-Path $resultsRoot "build.log"
        $buildScript = Join-Path $repoRoot "scripts\build\build_windows.ps1"
        Invoke-LoggedNativeCommand "powershell" @(
            "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $buildScript,
            "-SpecPath", $SpecPath, "-SkipTests"
        ) $buildLog
        $completedSteps.Add("PyInstaller build")

        $distName = [System.IO.Path]::GetFileNameWithoutExtension($SpecPath)
        $exePath = Join-Path $repoRoot "dist\$distName\$distName.exe"
        $smokeLog = Join-Path $resultsRoot "exe-smoke.log"
        $smokeScript = Join-Path $scriptRoot "smoke_packaged_exe.py"
        Invoke-LoggedNativeCommand $venvPython @(
            $smokeScript, "--exe", $exePath,
            "--startup-seconds", $ExeStartupSeconds,
            "--log-path", $smokeLog
        ) (Join-Path $resultsRoot "exe-smoke-runner.log")
        $completedSteps.Add("packaged EXE startup smoke")
    }

    $status = "PASSED"
}
catch {
    $failureMessage = $_.Exception.Message
    throw
}
finally {
    $finishedAt = Get-Date
    $duration = [Math]::Round(($finishedAt - $startedAt).TotalSeconds, 2)
    $summaryLines = @(
        "# Ticket_AUTO verification result",
        "",
        "- Mode: $runMode",
        "- Status: $status",
        "- Started: $($startedAt.ToString('yyyy-MM-dd HH:mm:ss'))",
        "- Finished: $($finishedAt.ToString('yyyy-MM-dd HH:mm:ss'))",
        "- Duration: $duration seconds",
        "- Completed steps: $($completedSteps -join ', ')"
    )
    if ($failureMessage) {
        $summaryLines += "- Failure: $failureMessage"
    }
    $summaryLines | Set-Content -LiteralPath $summaryPath -Encoding UTF8
    Write-Host "[verify] Results: $resultsRoot"
}
