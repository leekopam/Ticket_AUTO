[CmdletBinding()]
param(
    [string]$SpecPath = "Ticket_AUTO_flat.spec",
    [switch]$SkipTests,
    [switch]$SkipDependencyInstall
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "[build] $Message" -ForegroundColor Cyan
}

function Invoke-NativeCommand {
    param(
        [string]$FilePath,
        [string[]]$Arguments
    )

    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed ($LASTEXITCODE): $FilePath $($Arguments -join ' ')"
    }
}

function Assert-FileExists {
    param(
        [string]$Path,
        [string]$Message
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "$Message Path: $Path"
    }
}

function Assert-DirectoryExists {
    param(
        [string]$Path,
        [string]$Message
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Container)) {
        throw "$Message Path: $Path"
    }
}

$scriptRoot = Split-Path -Parent $PSCommandPath
$repoRoot = (Resolve-Path -LiteralPath (Join-Path $scriptRoot "..")).Path
Set-Location $repoRoot

$venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
    Write-Step "Create virtual environment"
    $pyLauncher = Get-Command py -ErrorAction SilentlyContinue
    if ($null -ne $pyLauncher) {
        Invoke-NativeCommand $pyLauncher.Source @("-3.13", "-m", "venv", ".venv")
    }
    else {
        Invoke-NativeCommand "python" @("-m", "venv", ".venv")
    }
}
Assert-FileExists $venvPython "Python virtual environment was not created."

if (-not $SkipDependencyInstall) {
    Write-Step "Install Python dependencies"
    Invoke-NativeCommand $venvPython @("-m", "pip", "install", "--upgrade", "pip")
    Invoke-NativeCommand $venvPython @("-m", "pip", "install", "-r", "requirements.txt")

    $devRequirements = Join-Path $repoRoot "requirements-dev.txt"
    if (Test-Path -LiteralPath $devRequirements -PathType Leaf) {
        Invoke-NativeCommand $venvPython @("-m", "pip", "install", "-r", "requirements-dev.txt")
    }
}

Write-Step "Install bundled Playwright Chromium"
$env:PLAYWRIGHT_BROWSERS_PATH = "0"
Invoke-NativeCommand $venvPython @("-m", "playwright", "install", "chromium")

if (-not $SkipTests) {
    Write-Step "Run test suite"
    Invoke-NativeCommand $venvPython @("-m", "pytest")
}

$resolvedSpecPath = Join-Path $repoRoot $SpecPath
Assert-FileExists $resolvedSpecPath "PyInstaller spec file was not found."

Write-Step "Build executable with PyInstaller"
Invoke-NativeCommand $venvPython @("-m", "PyInstaller", "--clean", "--noconfirm", $SpecPath)

$distName = [System.IO.Path]::GetFileNameWithoutExtension($SpecPath)
$distRoot = Join-Path $repoRoot "dist\$distName"
$exePath = Join-Path $distRoot "$distName.exe"
Assert-FileExists $exePath "Build output executable was not created."
Assert-FileExists (Join-Path $distRoot "Resources\templates\receipt_layout.json") "Receipt template was not bundled."
Assert-FileExists (Join-Path $distRoot "Resources\data\data.xlsx") "Data workbook was not bundled."
Assert-FileExists (Join-Path $distRoot "receipt_form.json") "Receipt form file was not bundled."

$browserRoot = Join-Path $distRoot "playwright\driver\package\.local-browsers"
Assert-DirectoryExists $browserRoot "Bundled Playwright browser directory was not created."
$chromiumExe = Get-ChildItem -LiteralPath $browserRoot -Recurse -Filter "chrome.exe" |
    Where-Object { $_.FullName -match "chromium-[^\\]+\\chrome-win64\\chrome\.exe$" } |
    Select-Object -First 1
if ($null -eq $chromiumExe) {
    throw "Bundled Playwright Chromium executable was not found below: $browserRoot"
}

$files = Get-ChildItem -LiteralPath $distRoot -Recurse -File
$sizeMb = [Math]::Round((($files | Measure-Object -Property Length -Sum).Sum / 1MB), 2)

Write-Host ""
Write-Host "[build] Output: $exePath"
Write-Host "[build] Bundled Chromium: $($chromiumExe.FullName)"
Write-Host "[build] Output size: $sizeMb MB"
