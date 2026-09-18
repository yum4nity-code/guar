$ErrorActionPreference = "Stop"

$Campaign = "D:\MT5_Backtests\guardian-autonomous-main\research\campaigns\EDGE_ATLAS_2026_09_17"
$Script = Join-Path $Campaign "f14_v2_oos_2023_2025_v2.py"
$Manifest = Join-Path $Campaign "ADMITTED_DATA_MANIFEST.json"
$Standards = "D:\MT5_Backtests\guardian-autonomous-main\research\standards"
$Lock = Join-Path $Campaign "F14_V2_OOS_2023_2025_OPENED.json"
$Out = "D:\MT5_Backtests\Research\Autonomous\edge_atlas\F14-V2-OOS-2023-2025"

$ExpectedScript = "bc083ac484f3f26c034fd4696fdc82605b83adb61c121d1d5f9899df47746da1"
$ExpectedDeps = @{
    "data_loader_v1.py" = "9129e1d48ce5b05ef997e9fa12b6e55a674c2be052e69ed94bc3fb4c235b2f6b"
    "preflight_v1.py" = "d0e799d4a999683c4c1a46495b56a6720d40ab61058ffe2d36a1456ab69cd354"
    "night_atlas_v1.py" = "edf95e275cdd49846fed81c06d180b5190e63c25de347137136b94e0f3674860"
}

if (-not (Test-Path $Campaign)) { throw "Campaign missing: $Campaign" }
if (-not (Test-Path $Script)) { throw "Audited F14 V2 script missing: $Script" }
if (-not (Test-Path $Manifest)) { throw "Manifest missing: $Manifest" }
if (-not (Test-Path $Standards)) { throw "Standards missing: $Standards" }

$scriptHash = (Get-FileHash $Script -Algorithm SHA256).Hash.ToLower()
Write-Host "SCRIPT EXPECTED : $ExpectedScript"
Write-Host "SCRIPT ACTUAL   : $scriptHash"
if ($scriptHash -ne $ExpectedScript) {
    throw "AUDITED SCRIPT HASH MISMATCH - ABORT"
}

foreach ($name in $ExpectedDeps.Keys) {
    $path = Join-Path $Campaign $name
    if (-not (Test-Path $path)) { throw "Required dependency missing: $path" }
    $actual = (Get-FileHash $path -Algorithm SHA256).Hash.ToLower()
    $wanted = $ExpectedDeps[$name]
    Write-Host "$name EXPECTED : $wanted"
    Write-Host "$name ACTUAL   : $actual"
    if ($actual -ne $wanted) {
        throw "DEPENDENCY HASH MISMATCH: $name - ABORT"
    }
}

if (Test-Path $Lock) {
    Write-Host ""
    Write-Host "LOCK EXISTS: $Lock"
    Get-Content $Lock -Raw
    throw "OOS ALREADY RESERVED/OPENED/ATTEMPTED - DO NOT RERUN"
}

if (Test-Path $Out) {
    throw "CANONICAL OUTPUT ALREADY EXISTS: $Out - DO NOT RERUN"
}

Write-Host ""
Write-Host "=== F14-V2 FINAL PREFLIGHT ==="
Write-Host "No market payload should be decoded in this phase."
Write-Host ""

& python $Script --manifest $Manifest --standards $Standards
if ($LASTEXITCODE -ne 0) {
    throw "PREFLIGHT FAILED - OOS NOT OPENED"
}

if (Test-Path $Lock) {
    throw "UNEXPECTED LOCK AFTER PREFLIGHT - ABORT"
}
if (Test-Path $Out) {
    throw "UNEXPECTED OUTPUT AFTER PREFLIGHT - ABORT"
}

Write-Host ""
Write-Host "=== OPENING F14-V2 OOS 2023-2025 ONCE ==="
Write-Host "Rule and gate are frozen."
Write-Host "2026 remains closed."
Write-Host ""

& python $Script --manifest $Manifest --standards $Standards --execute
$exitCode = $LASTEXITCODE

Write-Host ""
Write-Host "=== FINAL STATE ==="
if (Test-Path $Lock) {
    Get-Content $Lock -Raw
}
else {
    Write-Host "WARNING: canonical lock not found"
}

if ($exitCode -ne 0) {
    throw "OOS EXECUTION FAILED. The attempt is consumed if the canonical lock exists. DO NOT RERUN."
}

Write-Host ""
Write-Host "F14-V2 OOS EXECUTION COMPLETE"
