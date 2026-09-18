$ErrorActionPreference = "Stop"

$Campaign = "D:\MT5_Backtests\guardian-autonomous-main\research\campaigns\EDGE_ATLAS_2026_09_17"
$Manifest = Join-Path $Campaign "ADMITTED_DATA_MANIFEST.json"
$Standards = "D:\MT5_Backtests\guardian-autonomous-main\research\standards"
$OutputRoot = "D:\MT5_Backtests\Research\Autonomous\edge_atlas"
$Base = "https://raw.githubusercontent.com/yum4nity-code/guar/main/tools/independent-strategies/simple-three-dev-v1"

$Files = @(
    @{ Name = "simple_three_dev_engine_v1.py"; Blob = "f1e32a0dde28406564895ffeea8f6c66a2153088" },
    @{ Name = "simple_three_dev_analyze_v1.py"; Blob = "a3678f7a947667f9211c51afc6b8209d8e280906" }
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

$Tmp = Join-Path $env:TEMP ("simple_three_dev_v1_" + [guid]::NewGuid().ToString("N"))
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

    $Engine = Join-Path $Campaign "simple_three_dev_engine_v1.py"
    $Analyzer = Join-Path $Campaign "simple_three_dev_analyze_v1.py"
    $RunId = "SIMPLE-THREE-DEV-V1-" + (Get-Date -Format "yyyyMMdd-HHmmss")
    $Run = Join-Path $OutputRoot $RunId
    $Analysis = Join-Path $Run "analysis"

    if (Test-Path $Run) {
        throw "Unexpected existing run directory: $Run"
    }

    Write-Host ""
    Write-Host "===== SIMPLE THREE DEV PREFLIGHT ====="
    Write-Host "ONE market pass only."
    Write-Host "Warmup: 2016-11-01 to 2016-12-31."
    Write-Host "DEV: 2017-01-01 to 2020-12-31."
    Write-Host "2021-2022 CLOSED."
    Write-Host "2023+ CLOSED."
    Write-Host "2026 CLOSED."
    Write-Host "Costs are expressed directly in R."

    & python $Engine --manifest $Manifest --standards $Standards --output-dir $Run
    if ($LASTEXITCODE -ne 0) {
        throw "Simple Three DEV preflight failed"
    }

    Write-Host ""
    Write-Host "===== SIMPLE THREE DEV MARKET PASS ====="
    Write-Host "Do not select text in this console while it runs. If selection pauses it, press Esc."

    & python $Engine --manifest $Manifest --standards $Standards --output-dir $Run --execute
    if ($LASTEXITCODE -ne 0) {
        throw "Simple Three DEV engine failed. Keep the run directory for diagnosis."
    }

    $Signals = Join-Path $Run "signals.csv"
    if (-not (Test-Path $Signals)) {
        throw "signals.csv missing after engine run"
    }

    Write-Host ""
    Write-Host "===== SIMPLE THREE DEV ANALYSIS ====="
    Write-Host "No market data is reread here; analyzer uses signals.csv only."

    & python $Analyzer --input $Signals --output-dir $Analysis
    if ($LASTEXITCODE -ne 0) {
        throw "Simple Three DEV analyzer failed"
    }

    Write-Host ""
    Write-Host "===== SIMPLE THREE DEV COMPLETE ====="
    Write-Host "RUN: $Run"
    Write-Host "REPORT: $(Join-Path $Analysis 'simple_three_dev_analysis.json')"
    Write-Host "2021-2022 ACCESSED: FALSE"
    Write-Host "2023+ ACCESSED: FALSE"
    Write-Host "2026 ACCESSED: FALSE"
}
finally {
    if (Test-Path $Tmp) {
        Remove-Item $Tmp -Recurse -Force
    }
}
