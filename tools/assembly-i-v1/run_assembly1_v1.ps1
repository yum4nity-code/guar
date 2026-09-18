$ErrorActionPreference = "Stop"

$Campaign = "D:\MT5_Backtests\guardian-autonomous-main\research\campaigns\EDGE_ATLAS_2026_09_17"
$Manifest = Join-Path $Campaign "ADMITTED_DATA_MANIFEST.json"
$Standards = "D:\MT5_Backtests\guardian-autonomous-main\research\standards"
$OutputRoot = "D:\MT5_Backtests\Research\Autonomous\edge_atlas"
$Base = "https://raw.githubusercontent.com/yum4nity-code/guar/main/tools/assembly-i-v1"

$Files = @(
    @{ Name = "assembly1_engine_v1.py"; Blob = "a3487e8e9147009a85e039f4cdd4f6a5711fa0f0" },
    @{ Name = "assembly1_analyze_v1.py"; Blob = "2fc2aa237a4cc60e0d1373805c70624660f7910c" }
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
$Tmp = Join-Path $env:TEMP ("assembly1_v1_" + [guid]::NewGuid().ToString("N"))
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

    $Engine = Join-Path $Campaign "assembly1_engine_v1.py"
    $Analyzer = Join-Path $Campaign "assembly1_analyze_v1.py"
    $RunId = "ASSEMBLY-I-V1-" + (Get-Date -Format "yyyyMMdd-HHmmss")
    $Run = Join-Path $OutputRoot $RunId
    $Analysis = Join-Path $Run "analysis"

    if (Test-Path $Run) {
        throw "Unexpected existing run directory: $Run"
    }

    Write-Host ""
    Write-Host "===== ASSEMBLY I EXTERNAL OOS PREFLIGHT ====="
    Write-Host "Warmup payload: 2022-11-01 through 2022-12-31."
    Write-Host "EXTERNAL OOS: 2023-01-01 through 2025-12-31."
    Write-Host "Executing the market pass WILL OPEN 2023-2025 for these strategies."
    Write-Host "2026 REMAINS CLOSED."
    Write-Host "AS1 = Skew Reversal + Kurtosis regime."
    Write-Host "AS2 = Skew Reversal + Vol-of-Vol regime."
    Write-Host "AS3 = Tail Exhaustion + Kurtosis regime."
    Write-Host "Primary H60. H30/H120 descriptive only."
    Write-Host "No tuning on 2023-2025 after this run."

    & python $Engine --manifest $Manifest --standards $Standards --output-dir $Run
    if ($LASTEXITCODE -ne 0) {
        throw "Assembly I preflight failed"
    }

    Write-Host ""
    Write-Host "===== ASSEMBLY I EXTERNAL OOS MARKET PASS ====="
    Write-Host "2023-2025 is now being opened for this frozen test."
    Write-Host "If console selection pauses the process, press Esc."

    & python $Engine --manifest $Manifest --standards $Standards --output-dir $Run --execute
    if ($LASTEXITCODE -ne 0) {
        throw "Assembly I engine failed. Keep the run directory for diagnosis."
    }

    $Signals = Join-Path $Run "signals.csv"
    if (-not (Test-Path $Signals)) {
        throw "signals.csv missing after Assembly I engine run"
    }

    Write-Host ""
    Write-Host "===== ASSEMBLY I EXTERNAL OOS ANALYSIS ====="
    Write-Host "No market payload is reread here; analysis uses signals.csv only."

    & python $Analyzer --input $Signals --output-dir $Analysis
    if ($LASTEXITCODE -ne 0) {
        throw "Assembly I analyzer failed"
    }

    Write-Host ""
    Write-Host "===== ASSEMBLY I COMPLETE ====="
    Write-Host "RUN: $Run"
    Write-Host "REPORT: $(Join-Path $Analysis 'assembly1_analysis.json')"
    Write-Host "2023-2025 ACCESSED: TRUE"
    Write-Host "2026 ACCESSED: FALSE"
}
finally {
    if (Test-Path $Tmp) {
        Remove-Item $Tmp -Recurse -Force
    }
}
