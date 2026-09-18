$ErrorActionPreference = "Stop"

$Root = "D:\MT5_Backtests\Research\Autonomous\edge_atlas\NIGHT-ATLAS-V1-20260917-185001"
$InputCsv = Join-Path $Root "data\signals.csv"
$OutDir = Join-Path $Root "analysis\final_is_audit_v1"

if (-not (Test-Path $InputCsv)) { throw "signals.csv missing: $InputCsv" }

$TmpDir = Join-Path $env:TEMP "guardian_final_is_audit_v1"
if (Test-Path $TmpDir) { Remove-Item $TmpDir -Recurse -Force }
New-Item -ItemType Directory -Path $TmpDir | Out-Null

$P1 = Join-Path $TmpDir "part01.txt"
$P2 = Join-Path $TmpDir "part02.txt"
$Script = Join-Path $TmpDir "night_atlas_final_is_audit_v1.py"

$U1 = "https://raw.githubusercontent.com/yum4nity-code/guar/main/tools/night-atlas-v1/final-is-audit-v1/part01.txt"
$U2 = "https://raw.githubusercontent.com/yum4nity-code/guar/main/tools/night-atlas-v1/final-is-audit-v1/part02.txt"

try {
    Invoke-WebRequest $U1 -OutFile $P1
    Invoke-WebRequest $U2 -OutFile $P2

    $H1 = (Get-FileHash $P1 -Algorithm SHA256).Hash.ToLower()
    $H2 = (Get-FileHash $P2 -Algorithm SHA256).Hash.ToLower()
    if ($H1 -ne "4108f787b16f28f0f030ab8e8138e2441eb98b13be9c8731e4bab86c04cf6f0b") { throw "PART01 HASH MISMATCH" }
    if ($H2 -ne "2da622a135dec80d7fbe1fdc190053c3d678ae493cb9ec66be20dae415d42059") { throw "PART02 HASH MISMATCH" }

    $B1 = [IO.File]::ReadAllBytes($P1)
    $B2 = [IO.File]::ReadAllBytes($P2)
    $All = New-Object byte[] ($B1.Length + $B2.Length)
    [Array]::Copy($B1, 0, $All, 0, $B1.Length)
    [Array]::Copy($B2, 0, $All, $B1.Length, $B2.Length)
    [IO.File]::WriteAllBytes($Script, $All)

    $FinalHash = (Get-FileHash $Script -Algorithm SHA256).Hash.ToLower()
    $Expected = "16cf6f821d29c78b299e0dbad42f14c271d1a70e5536d7f6dcc091e812578557"
    "AUDIT SCRIPT EXPECTED : $Expected"
    "AUDIT SCRIPT ACTUAL   : $FinalHash"
    if ($FinalHash -ne $Expected) { throw "FINAL AUDIT SCRIPT HASH MISMATCH" }

    python -m py_compile $Script
    if ($LASTEXITCODE -ne 0) { throw "PYTHON SYNTAX CHECK FAILED" }

    ""
    "SCRIPT VERIFIED - RUNNING FINAL IS AUDIT"
    ""
    python $Script --input $InputCsv --output-dir $OutDir
    if ($LASTEXITCODE -ne 0) { throw "FINAL IS AUDIT FAILED" }
}
finally {
    if (Test-Path $TmpDir) { Remove-Item $TmpDir -Recurse -Force }
}
