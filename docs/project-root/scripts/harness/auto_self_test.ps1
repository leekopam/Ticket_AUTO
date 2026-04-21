param(
  [string]$ProjectRoot = ".",
  [switch]$Unity,
  [switch]$General
)

$ErrorActionPreference = "Stop"

$root = Resolve-Path $ProjectRoot
$artifactDir = Join-Path $root "artifacts/harness"
New-Item -ItemType Directory -Force -Path $artifactDir | Out-Null

$result = [ordered]@{
  timestamp = (Get-Date).ToString("o")
  projectRoot = $root.Path
  checks = @()
  passed = $true
}

function Add-Check($name, $status, $message) {
  $script:result.checks += [ordered]@{
    name = $name
    status = $status
    message = $message
  }
  if ($status -eq "fail") { $script:result.passed = $false }
}

function Test-PathCheck($relative, $required = $true) {
  $path = Join-Path $root $relative
  if (Test-Path $path) {
    Add-Check "exists:$relative" "pass" "존재함"
  } elseif ($required) {
    Add-Check "exists:$relative" "fail" "필수 파일이 없음"
  } else {
    Add-Check "exists:$relative" "warn" "선택 파일이 없음"
  }
}

Test-PathCheck "AGENTS.md" $true
Test-PathCheck "CLAUDE.md" $false
Test-PathCheck "docs/ai-worklog/attempt-log.md" $true
Test-PathCheck "docs/ai-worklog/user-feedback.md" $true
Test-PathCheck "docs/ai-worklog/context-summary.md" $true
Test-PathCheck "docs/qa/QR_CHECKLIST.md" $true

# 기본 금지 패턴 검사: 프로젝트에 맞게 조정 가능
$forbidden = @("TODO: ship", "HACK: ignore", "console.log('debug')")
$scanRoots = @("Assets", "src", "lib", "app") | ForEach-Object { Join-Path $root $_ } | Where-Object { Test-Path $_ }
foreach ($scanRoot in $scanRoots) {
  $files = Get-ChildItem -Path $scanRoot -Recurse -File -Include *.cs,*.ts,*.tsx,*.js,*.jsx,*.dart,*.py,*.md -ErrorAction SilentlyContinue
  foreach ($file in $files) {
    $content = Get-Content $file.FullName -Raw -ErrorAction SilentlyContinue
    foreach ($pattern in $forbidden) {
      if ($content -like "*$pattern*") {
        Add-Check "forbidden:$pattern" "fail" "$($file.FullName)"
      }
    }
  }
}

# 프로젝트별 하네스 설정이 있으면 명령 실행
$configPath = Join-Path $root "scripts/harness/harness.config.json"
if (Test-Path $configPath) {
  try {
    $config = Get-Content $configPath -Raw | ConvertFrom-Json
    if ($General -and $config.general.commands) {
      foreach ($cmd in $config.general.commands) {
        Write-Host "[general] $cmd"
        cmd /c $cmd
        if ($LASTEXITCODE -eq 0) { Add-Check "cmd:$cmd" "pass" "성공" }
        else { Add-Check "cmd:$cmd" "fail" "exit=$LASTEXITCODE" }
      }
    }
    if ($Unity -and $config.unity.commands) {
      foreach ($cmd in $config.unity.commands) {
        Write-Host "[unity] $cmd"
        cmd /c $cmd
        if ($LASTEXITCODE -eq 0) { Add-Check "cmd:$cmd" "pass" "성공" }
        else { Add-Check "cmd:$cmd" "fail" "exit=$LASTEXITCODE" }
      }
    }
  } catch {
    Add-Check "harness.config.json" "fail" "설정 파싱 또는 명령 실행 실패: $($_.Exception.Message)"
  }
} else {
  Add-Check "harness.config.json" "warn" "프로젝트별 테스트 명령 설정 없음"
}

$out = Join-Path $artifactDir "auto-self-test-result.json"
$result | ConvertTo-Json -Depth 10 | Out-File $out -Encoding UTF8

if ($result.passed) {
  Write-Host "자동 자체 테스트 통과: $out"
  exit 0
} else {
  Write-Error "자동 자체 테스트 실패: $out"
  exit 1
}
