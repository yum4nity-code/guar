$ErrorActionPreference = "Stop"

$Campaign = "D:\MT5_Backtests\guardian-autonomous-main\research\campaigns\EDGE_ATLAS_2026_09_17"
$Manifest = Join-Path $Campaign "ADMITTED_DATA_MANIFEST.json"
$Standards = "D:\MT5_Backtests\guardian-autonomous-main\research\standards"
$OutputRoot = "D:\MT5_Backtests\Research\Autonomous\edge_atlas"
$Base = "https://raw.githubusercontent.com/yum4nity-code/guar/main/tools/atlas-ii-v1"

$Files = @(
    @{ Name = "atlas2_engine_v1.py"; Blob = "4d7eca3d4fff2552ba9290f725fa11841554f8c9" },
    @{ Name = "atlas2_analyze_v1.py"; Blob = "21348d66b87cbe8505f6f63bfcd242575b578bbb" }
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

$Tmp = Join-Path $env:TEMP ("atlas2_v1_" + [guid]::NewGuid().ToString("N"))
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

    $Engine = Join-Path $Campaign "atlas2_engine_v1.py"
    $Analyzer = Join-Path $Campaign "atlas2_analyze_v1.py"
    $RunId = "ATLAS-II-V1-" + (Get-Date -Format "yyyyMMdd-HHmmss")
    $Run = Join-Path $OutputRoot $RunId
    $Analysis = Join-Path $Run "analysis"

    if (Test-Path $Run) { throw "Unexpected existing run directory: $Run" }

    Write-Host ""
    Write-Host "===== ATLAS II PREFLIGHT ====="
    Write-Host "One market pass planned. Discovery 2017-2022 only."
    Write-Host "2023+ CLOSED. 2026 CLOSED."
    & python $Engine --manifest $Manifest --standards $Standards --output-dir $Run
    if ($LASTEXITCODE -ne 0) { throw "Atlas II preflight failed" }

    Write-Host ""
    Write-Host "===== ATLAS II MARKET PASS ====="
    & python $Engine --manifest $Manifest --standards $Standards --output-dir $Run --execute
    if ($LASTEXITCODE -ne 0) {
        throw "Atlas II engine failed. Do not delete the run directory; inspect it."
    }

    $Signals = Join-Path $Run "signals.csv"
    if (-not (Test-Path $Signals)) { throw "signals.csv missing after engine run" }

    Write-Host ""
    Write-Host "===== ATLAS II ANALYSIS ====="
    Write-Host "No market payload is reread here; analysis uses signals.csv only."
    & python $Analyzer --input $Signals --output-dir $Analysis
    if ($LASTEXITCODE -ne 0) { throw "Atlas II analyzer failed" }

    Write-Host ""
    Write-Host "===== ATLAS II COMPLETE ====="
    Write-Host "RUN: $Run"
    Write-Host "REPORT: $(Join-Path $Analysis 'atlas2_analysis.json')"
    Write-Host "2023+ ACCESSED: FALSE"
    Write-Host "2026 ACCESSED: FALSE"
}
finally {
    if (Test-Path $Tmp) {
        Remove-Item $Tmp -Recurse -Force
    }
}
