$ErrorActionPreference = "Stop"

$Campaign = "D:\MT5_Backtests\guardian-autonomous-main\research\campaigns\EDGE_ATLAS_2026_09_17"
$Manifest = Join-Path $Campaign "ADMITTED_DATA_MANIFEST.json"
$Standards = "D:\MT5_Backtests\guardian-autonomous-main\research\standards"
$OutputRoot = "D:\MT5_Backtests\Research\Autonomous\edge_atlas"

$HistoricalSignals = "D:\MT5_Backtests\Research\Autonomous\edge_atlas\ENSEMBLE-I-HIST-V1-20260918-165711\signals.csv"
$HistoricalSha256 = "524f9a07e5e0980465d22abd70f4f672c7615b832ce8d82fefcb922276f73f3b"

$Base = "https://raw.githubusercontent.com/yum4nity-code/guar/main/tools/e2-sparsification-lab-i-v1"

$Files = @(
    @{ Name = "e2_extension_engine_v1.py"; Blob = "75abec8195b852b64998f1503347f53b40c70f8e" },
    @{ Name = "e2_sparsification_analyze_v1.py"; Blob = "0f272abed4a30d2644ad7fe4c53265de1944da40" }
)

$EnsembleDependency = Join-Path $Campaign "ensemble_i_engine_v1.py"
$EnsembleDependencyBlob = "1f780087128b27732368be2aec5515d1bbf7406a"
$EnsembleDependencySha256 = "defe36eb894789697d71951552b0d5df26b90e6bdee7064db4fab6bac28e9ed5"
$EnsembleDependencyUrl = "https://raw.githubusercontent.com/yum4nity-code/guar/main/tools/ensemble-i-historical-v1/ensemble_i_engine_v1.py"

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
if (-not (Test-Path $HistoricalSignals)) { throw "Historical Ensemble I signals.csv missing: $HistoricalSignals" }

$actualHistoricalSha = (Get-FileHash $HistoricalSignals -Algorithm SHA256).Hash.ToLower()
Write-Host "HISTORICAL EXPECTED SHA256 : $HistoricalSha256"
Write-Host "HISTORICAL ACTUAL SHA256   : $actualHistoricalSha"
if ($actualHistoricalSha -ne $HistoricalSha256) {
    throw "Historical Ensemble I signals.csv hash mismatch - ABORT"
}

New-Item -ItemType Directory -Force -Path $OutputRoot | Out-Null

$Tmp = Join-Path $env:TEMP ("e2_sparsification_lab_i_v1_" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $Tmp | Out-Null

try {
    # Pin/import the exact Ensemble I vote engine used to generate the historical block.
    if (-not (Test-Path $EnsembleDependency)) {
        $tmpDep = Join-Path $Tmp "ensemble_i_engine_v1.py"
        Invoke-WebRequest $EnsembleDependencyUrl -OutFile $tmpDep

        $depBlob = Get-GitBlobSha1 $tmpDep
        $depSha = (Get-FileHash $tmpDep -Algorithm SHA256).Hash.ToLower()

        if ($depBlob -ne $EnsembleDependencyBlob) {
            throw "Downloaded Ensemble I dependency Git blob mismatch"
        }
        if ($depSha -ne $EnsembleDependencySha256) {
            throw "Downloaded Ensemble I dependency SHA256 mismatch"
        }

        Copy-Item $tmpDep $EnsembleDependency
    }

    $existingDepBlob = Get-GitBlobSha1 $EnsembleDependency
    $existingDepSha = (Get-FileHash $EnsembleDependency -Algorithm SHA256).Hash.ToLower()

    Write-Host "ENSEMBLE I DEP EXPECTED BLOB : $EnsembleDependencyBlob"
    Write-Host "ENSEMBLE I DEP ACTUAL BLOB   : $existingDepBlob"
    Write-Host "ENSEMBLE I DEP EXPECTED SHA  : $EnsembleDependencySha256"
    Write-Host "ENSEMBLE I DEP ACTUAL SHA    : $existingDepSha"

    if ($existingDepBlob -ne $EnsembleDependencyBlob -or $existingDepSha -ne $EnsembleDependencySha256) {
        throw "Campaign Ensemble I dependency differs from frozen source - ABORT"
    }

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
    }

    $Engine = Join-Path $Campaign "e2_extension_engine_v1.py"
    $Analyzer = Join-Path $Campaign "e2_sparsification_analyze_v1.py"

    $RunId = "E2-SPARSIFICATION-I-V1-" + (Get-Date -Format "yyyyMMdd-HHmmss")
    $Run = Join-Path $OutputRoot $RunId
    $Analysis = Join-Path $Run "analysis"

    if (Test-Path $Run) {
        throw "Unexpected existing run directory: $Run"
    }

    Write-Host ""
    Write-Host "===== E2 SPARSIFICATION LAB I PREFLIGHT ====="
    Write-Host "HISTORICAL 2005-2016: reuse frozen signals.csv only; no market reread."
    Write-Host "EXTENSION MARKET PASS: 2017-2025 only."
    Write-Host "2026: CLOSED."
    Write-Host "Seven preregistered vote-strength/breadth selectors."
    Write-Host "No component weights, no new indicators, no exit changes."

    & python $Engine --manifest $Manifest --standards $Standards --output-dir $Run
    if ($LASTEXITCODE -ne 0) {
        throw "E2 sparsification preflight failed"
    }

    Write-Host ""
    Write-Host "===== E2 SPARSIFICATION 2017-2025 EXTENSION PASS ====="
    Write-Host "2005-2016 market payload will NOT be reread."
    Write-Host "2017-2025 is development and will be decoded once."
    Write-Host "2026 will not be decoded."
    Write-Host "If console selection pauses the process, press Esc."

    & python $Engine --manifest $Manifest --standards $Standards --output-dir $Run --execute
    if ($LASTEXITCODE -ne 0) {
        throw "E2 sparsification extension engine failed. Keep run directory for diagnosis."
    }

    $ExtensionSignals = Join-Path $Run "signals_2017_2025.csv"
    if (-not (Test-Path $ExtensionSignals)) {
        throw "signals_2017_2025.csv missing after extension pass"
    }

    Write-Host ""
    Write-Host "===== E2 SPARSIFICATION ANALYSIS ====="
    Write-Host "Analyzer combines frozen historical signals.csv with the new 2017-2025 extension."
    Write-Host "No market payload is reread in analysis."

    & python $Analyzer --historical $HistoricalSignals --extension $ExtensionSignals --output-dir $Analysis
    if ($LASTEXITCODE -ne 0) {
        throw "E2 sparsification analyzer failed"
    }

    Write-Host ""
    Write-Host "===== E2 SPARSIFICATION LAB I COMPLETE ====="
    Write-Host "RUN: $Run"
    Write-Host "REPORT: $(Join-Path $Analysis 'e2_sparsification_analysis.json')"
    Write-Host "2005-2016 MARKET REREAD: FALSE"
    Write-Host "2017-2025 DEVELOPMENT USED: TRUE"
    Write-Host "2026 ACCESSED: FALSE"
}
finally {
    if (Test-Path $Tmp) {
        Remove-Item $Tmp -Recurse -Force
    }
}
