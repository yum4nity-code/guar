$ErrorActionPreference = "Stop"

$Input = "D:\MT5_Backtests\Research\Autonomous\edge_atlas\ASSEMBLY-I-V1-20260918-154527\signals.csv"
$ExpectedInputSha256 = "18aece1bf876d598cbfa30b83ad3ca19659b787b99eb0e9949d100866a1e12c2"

$OutputRoot = "D:\MT5_Backtests\Research\Autonomous\edge_atlas"
$Base = "https://raw.githubusercontent.com/yum4nity-code/guar/main/tools/as2-conversion-lab-v1"
$AnalyzerName = "as2_conversion_analyze_v1.py"
$AnalyzerBlob = "522b08d366f18149f80186688c771f5ac00cca38"

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

if (-not (Test-Path $Input)) {
    throw "Assembly I signals.csv missing: $Input"
}

$InputSha = (Get-FileHash $Input -Algorithm SHA256).Hash.ToLower()
Write-Host "INPUT EXPECTED SHA256 : $ExpectedInputSha256"
Write-Host "INPUT ACTUAL SHA256   : $InputSha"
if ($InputSha -ne $ExpectedInputSha256) {
    throw "ASSEMBLY I INPUT HASH MISMATCH - ABORT"
}

New-Item -ItemType Directory -Force -Path $OutputRoot | Out-Null
$Tmp = Join-Path $env:TEMP ("as2_conversion_lab_v1_" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $Tmp | Out-Null

try {
    $Analyzer = Join-Path $Tmp $AnalyzerName
    Invoke-WebRequest "$Base/$AnalyzerName" -OutFile $Analyzer

    $ActualBlob = Get-GitBlobSha1 $Analyzer
    Write-Host "$AnalyzerName EXPECTED BLOB : $AnalyzerBlob"
    Write-Host "$AnalyzerName ACTUAL BLOB   : $ActualBlob"

    if ($ActualBlob -ne $AnalyzerBlob) {
        throw "AS2 CONVERSION ANALYZER BLOB MISMATCH - ABORT"
    }

    & python -m py_compile $Analyzer
    if ($LASTEXITCODE -ne 0) {
        throw "AS2 conversion analyzer Python syntax check failed"
    }

    $RunId = "AS2-CONVERSION-LAB-V1-" + (Get-Date -Format "yyyyMMdd-HHmmss")
    $Run = Join-Path $OutputRoot $RunId

    if (Test-Path $Run) {
        throw "Unexpected existing output directory: $Run"
    }

    Write-Host ""
    Write-Host "===== AS2 CONVERSION LAB V1 ====="
    Write-Host "SOURCE: frozen Assembly I signals.csv only."
    Write-Host "MARKET PAYLOAD REREAD: FALSE."
    Write-Host "2023-2025: DEVELOPMENT / ALREADY OPENED."
    Write-Host "2026: CLOSED."
    Write-Host "ENTRY RULE: AS2_SKEW_VOV frozen."
    Write-Host "15 exit candidates: TIME H15/H30/H60 plus symmetric +/-0.5/1/1.5/2R barriers with H15/H30/H60 time-stop."
    Write-Host "AMBIGUOUS same-M1 barrier touches are scored as losses."
    Write-Host "No asymmetric TP/SL, BE, trailing, partial, or runner simulation."

    & python $Analyzer --input $Input --output-dir $Run
    if ($LASTEXITCODE -ne 0) {
        throw "AS2 Conversion Lab failed"
    }

    Write-Host ""
    Write-Host "===== AS2 CONVERSION LAB COMPLETE ====="
    Write-Host "REPORT: $(Join-Path $Run 'as2_conversion_analysis.json')"
    Write-Host "MARKET PAYLOAD REREAD: FALSE"
    Write-Host "2026 ACCESSED: FALSE"
}
finally {
    if (Test-Path $Tmp) {
        Remove-Item $Tmp -Recurse -Force
    }
}
