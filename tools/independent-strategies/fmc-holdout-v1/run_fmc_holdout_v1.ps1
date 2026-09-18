$ErrorActionPreference = "Stop"

$Campaign = "D:\MT5_Backtests\guardian-autonomous-main\research\campaigns\EDGE_ATLAS_2026_09_17"
$Manifest = Join-Path $Campaign "ADMITTED_DATA_MANIFEST.json"
$Standards = "D:\MT5_Backtests\guardian-autonomous-main\research\standards"
$OutputRoot = "D:\MT5_Backtests\Research\Autonomous\edge_atlas"
$Base = "https://raw.githubusercontent.com/yum4nity-code/guar/main/tools/independent-strategies/fmc-holdout-v1"

$Files = @(
    @{ Name = "fmc_holdout_engine_v1.py"; Blob = "59161dcf1c10b48a6aa60f1a2f2a3d0d8c3ca418" },
    @{ Name = "fmc_holdout_analyze_v1.py"; Blob = "0151d1cdbe0ab41d99956f9c0f7ac00e89dbe470" }
)

function Get-GitBlobSha1([string]$Path) {
    $bytes = [IO.File]::ReadAllBytes($Path)
    $prefix = [Text.Encoding]::ASCII.GetBytes("blob $($bytes.Length)")
    $header = New-Object byte[] ($prefix.Length + 1)
    [Array]::Copy($prefix, 0, $header, 0, $prefix.Length)
    $header[$prefix.Length] = 0

    $all = New-Object byte[] ($header.Length + $bytes.Length)
    [Array]::Copy($header, 0, $all, 0, $header.Length)
    [Array]::Copy($bytes, 0, $all, $header.Length, $bytes.Length)

    $sha1 = [Security.Cryptography.SHA1]::Create()
    try {
        return ([BitConverter]::ToString($sha1.ComputeHash($all))).Replace("-", "").ToLower()
    }
    finally {
        $sha1.Dispose()
    }
}

if (-not (Test-Path $Campaign)) { throw "Campaign missing: $Campaign" }
if (-not (Test-Path $Manifest)) { throw "Manifest missing: $Manifest" }
if (-not (Test-Path $Standards)) { throw "Standards missing: $Standards" }

New-Item -ItemType Directory -Force -Path $OutputRoot | Out-Null
$Tmp = Join-Path $env:TEMP ("fmc_holdout_v1_" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $Tmp | Out-Null

try {
    foreach ($f in $Files) {
        $tmpFile = Join-Path $Tmp $f.Name
        Invoke-WebRequest "$Base/$($f.Name)" -OutFile $tmpFile

        $actualBlob = Get-GitBlobSha1 $tmpFile
        Write-Host "$($f.Name) EXPECTED BLOB : $($f.Blob)"
        Write-Host "$($f.Name) ACTUAL BLOB   : $actualBlob"

        if ($actualBlob -ne $f.Blob) {
            throw "Git blob mismatch: $($f.Name)"
        }

        & python -m py_compile $tmpFile
        if ($LASTEXITCODE -ne 0) {
            throw "Python syntax check failed: $($f.Name)"
        }

        $target = Join-Path $Campaign $f.Name
        if (Test-Path $target) {
            $existingBlob = Get-GitBlobSha1 $target
            if ($existingBlob -ne $f.Blob) {
                throw "Existing campaign file differs: $target"
            }
        }
        else {
            Copy-Item $tmpFile $target
        }

        $sha256 = (Get-FileHash $target -Algorithm SHA256).Hash.ToLower()
        Write-Host "$($f.Name) SHA256        : $sha256"
    }

    $Engine = Join-Path $Campaign "fmc_holdout_engine_v1.py"
    $Analyzer = Join-Path $Campaign "fmc_holdout_analyze_v1.py"
    $RunId = "FMC-HOLDOUT-2021-2022-V1-" + (Get-Date -Format "yyyyMMdd-HHmmss")
    $Run = Join-Path $OutputRoot $RunId
    $Analysis = Join-Path $Run "analysis"

    if (Test-Path $Run) {
        throw "Unexpected existing run directory: $Run"
    }

    Write-Host ""
    Write-Host "===== FMC HOLDOUT PREFLIGHT ====="
    Write-Host "Warmup payload: 2020-11-01 through 2020-12-31."
    Write-Host "Frozen one-shot test: 2021-01-01 through 2022-12-31."
    Write-Host "Primary H=30. H15/H60 descriptive only."
    Write-Host "2023-2025 CLOSED."
    Write-Host "2026 CLOSED."
    Write-Host "No threshold or horizon tuning after this run."

    & python $Engine --manifest $Manifest --standards $Standards --output-dir $Run
    if ($LASTEXITCODE -ne 0) { throw "FMC preflight failed" }

    Write-Host ""
    Write-Host "===== FMC HOLDOUT MARKET PASS ====="

    & python $Engine --manifest $Manifest --standards $Standards --output-dir $Run --execute
    if ($LASTEXITCODE -ne 0) {
        throw "FMC engine failed. Keep run directory for diagnosis."
    }

    $Signals = Join-Path $Run "signals.csv"
    if (-not (Test-Path $Signals)) { throw "signals.csv missing after FMC engine run" }

    Write-Host ""
    Write-Host "===== FMC HOLDOUT ANALYSIS ====="
    Write-Host "Analysis rereads signals.csv only."

    & python $Analyzer --input $Signals --output-dir $Analysis
    if ($LASTEXITCODE -ne 0) { throw "FMC analyzer failed" }

    Write-Host ""
    Write-Host "===== FMC HOLDOUT COMPLETE ====="
    Write-Host "RUN: $Run"
    Write-Host "REPORT: $(Join-Path $Analysis 'fmc_holdout_analysis.json')"
    Write-Host "2023-2025 ACCESSED: FALSE"
    Write-Host "2026 ACCESSED: FALSE"
}
finally {
    if (Test-Path $Tmp) { Remove-Item $Tmp -Recurse -Force }
}
