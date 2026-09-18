$ErrorActionPreference = "Stop"

$Campaign = "D:\MT5_Backtests\guardian-autonomous-main\research\campaigns\EDGE_ATLAS_2026_09_17"
$Manifest = Join-Path $Campaign "ADMITTED_DATA_MANIFEST.json"
$Standards = "D:\MT5_Backtests\guardian-autonomous-main\research\standards"
$OutputRoot = "D:\MT5_Backtests\Research\Autonomous\edge_atlas"
$Base = "https://raw.githubusercontent.com/yum4nity-code/guar/main/tools/ensemble-i-historical-v1"

$Files = @(
    @{ Name = "ensemble_i_engine_v1.py"; Blob = "7eea2d775b4e22054c6ff3acb6d9fa414066d896" },
    @{ Name = "ensemble_i_analyze_v1.py"; Blob = "911e1c4f863f55d9027d76f9ba2daab39bf535c2" }
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
        return ([BitConverter]::ToString(
            $sha1.ComputeHash($all)
        )).Replace("-", "").ToLower()
    }
    finally {
        $sha1.Dispose()
    }
}

if (-not (Test-Path $Campaign)) { throw "Campaign missing: $Campaign" }
if (-not (Test-Path $Manifest)) { throw "Manifest missing: $Manifest" }
if (-not (Test-Path $Standards)) { throw "Standards missing: $Standards" }

New-Item -ItemType Directory -Force -Path $OutputRoot | Out-Null

$Tmp = Join-Path $env:TEMP ("ensemble_i_hist_v1_" + [guid]::NewGuid().ToString("N"))
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

    $Engine = Join-Path $Campaign "ensemble_i_engine_v1.py"
    $Analyzer = Join-Path $Campaign "ensemble_i_analyze_v1.py"

    $RunId = "ENSEMBLE-I-HIST-V1-" + (Get-Date -Format "yyyyMMdd-HHmmss")
    $Run = Join-Path $OutputRoot $RunId
    $Analysis = Join-Path $Run "analysis"

    if (Test-Path $Run) {
        throw "Unexpected existing run directory: $Run"
    }

    Write-Host ""
    Write-Host "===== ENSEMBLE I HISTORICAL HOLDOUT PREFLIGHT ====="
    Write-Host "WARMUP ONLY: 2004-01 through 2004-03."
    Write-Host "HISTORICAL HOLDOUT: 2004-04-01 through 2016-12-31."
    Write-Host "2017-2025: NOT REREAD."
    Write-Host "2026: CLOSED."
    Write-Host "Three frozen equal-weight ensemble rules."
    Write-Host "Execution: uncapped TIME_H60 only."
    Write-Host "One market pass."
    Write-Host ""
    Write-Host "IMPORTANT: executing the pass opens the previously unused 2004-2016 historical block for these ensemble families."

    & python $Engine --manifest $Manifest --standards $Standards --output-dir $Run
    if ($LASTEXITCODE -ne 0) {
        throw "Ensemble I historical preflight failed"
    }

    Write-Host ""
    Write-Host "===== ENSEMBLE I HISTORICAL HOLDOUT MARKET PASS ====="
    Write-Host "2004-2016 historical block is now being opened."
    Write-Host "2017-2025 will not be decoded."
    Write-Host "2026 will not be decoded."
    Write-Host "If console selection pauses the process, press Esc."

    & python $Engine --manifest $Manifest --standards $Standards --output-dir $Run --execute
    if ($LASTEXITCODE -ne 0) {
        throw "Ensemble I historical engine failed. Keep run directory for diagnosis."
    }

    $Signals = Join-Path $Run "signals.csv"
    if (-not (Test-Path $Signals)) {
        throw "signals.csv missing after Ensemble I run"
    }

    Write-Host ""
    Write-Host "===== ENSEMBLE I HISTORICAL HOLDOUT ANALYSIS ====="
    Write-Host "No market payload is reread here; analyzer uses signals.csv only."

    & python $Analyzer --input $Signals --output-dir $Analysis
    if ($LASTEXITCODE -ne 0) {
        throw "Ensemble I historical analyzer failed"
    }

    Write-Host ""
    Write-Host "===== ENSEMBLE I COMPLETE ====="
    Write-Host "RUN: $Run"
    Write-Host "REPORT: $(Join-Path $Analysis 'ensemble_i_historical_analysis.json')"
    Write-Host "2004-2016 ACCESSED: TRUE"
    Write-Host "2017-2025 REREAD: FALSE"
    Write-Host "2026 ACCESSED: FALSE"
}
finally {
    if (Test-Path $Tmp) {
        Remove-Item $Tmp -Recurse -Force
    }
}
