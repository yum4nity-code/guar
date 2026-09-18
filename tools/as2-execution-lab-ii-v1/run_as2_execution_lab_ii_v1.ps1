$ErrorActionPreference = "Stop"

$Campaign = "D:\MT5_Backtests\guardian-autonomous-main\research\campaigns\EDGE_ATLAS_2026_09_17"
$Manifest = Join-Path $Campaign "ADMITTED_DATA_MANIFEST.json"
$Standards = "D:\MT5_Backtests\guardian-autonomous-main\research\standards"
$OutputRoot = "D:\MT5_Backtests\Research\Autonomous\edge_atlas"
$Base = "https://raw.githubusercontent.com/yum4nity-code/guar/main/tools/as2-execution-lab-ii-v1"

$Files = @(
    @{ Name = "as2_execution_engine_v1.py"; Blob = "ffae4da2b967c232b4c8323903289dc162045dbb" },
    @{ Name = "as2_execution_analyze_v1.py"; Blob = "58714f8a7e424e78f2230b9a93e5f54498ce11e2" }
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

$Tmp = Join-Path $env:TEMP ("as2_exec_lab_ii_v1_" + [guid]::NewGuid().ToString("N"))
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

    $Engine = Join-Path $Campaign "as2_execution_engine_v1.py"
    $Analyzer = Join-Path $Campaign "as2_execution_analyze_v1.py"

    $RunId = "AS2-EXECUTION-LAB-II-V1-" + (Get-Date -Format "yyyyMMdd-HHmmss")
    $Run = Join-Path $OutputRoot $RunId
    $Analysis = Join-Path $Run "analysis"

    if (Test-Path $Run) {
        throw "Unexpected existing run directory: $Run"
    }

    Write-Host ""
    Write-Host "===== AS2 EXECUTION LAB II PREFLIGHT ====="
    Write-Host "ENTRY: AS2_SKEW_VOV frozen."
    Write-Host "REPLAY: exact Dukascopy M1."
    Write-Host "DEVELOPMENT: 2017-2025, already contaminated."
    Write-Host "2026: CLOSED."
    Write-Host "12 preregistered execution candidates."
    Write-Host "All same-M1 ambiguities use conservative stop/BE-first rules."
    Write-Host "One market pass only."

    & python $Engine --manifest $Manifest --standards $Standards --output-dir $Run
    if ($LASTEXITCODE -ne 0) {
        throw "AS2 Execution Lab II preflight failed"
    }

    Write-Host ""
    Write-Host "===== AS2 EXECUTION LAB II MARKET PASS ====="
    Write-Host "2017-2025 only. 2026 will not be read."
    Write-Host "If console selection pauses the process, press Esc."

    & python $Engine --manifest $Manifest --standards $Standards --output-dir $Run --execute
    if ($LASTEXITCODE -ne 0) {
        throw "AS2 Execution Lab II engine failed. Keep the run directory for diagnosis."
    }

    $Rows = Join-Path $Run "execution_rows.csv"
    if (-not (Test-Path $Rows)) {
        throw "execution_rows.csv missing after engine run"
    }

    Write-Host ""
    Write-Host "===== AS2 EXECUTION LAB II ANALYSIS ====="
    Write-Host "No market payload is reread here; analyzer uses execution_rows.csv only."

    & python $Analyzer --input $Rows --output-dir $Analysis
    if ($LASTEXITCODE -ne 0) {
        throw "AS2 Execution Lab II analyzer failed"
    }

    Write-Host ""
    Write-Host "===== AS2 EXECUTION LAB II COMPLETE ====="
    Write-Host "RUN: $Run"
    Write-Host "REPORT: $(Join-Path $Analysis 'as2_execution_lab_ii_analysis.json')"
    Write-Host "2026 ACCESSED: FALSE"
}
finally {
    if (Test-Path $Tmp) {
        Remove-Item $Tmp -Recurse -Force
    }
}
