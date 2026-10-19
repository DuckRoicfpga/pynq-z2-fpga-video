param([string]$VivadoBin = 'C:\Xilinx\Vivado\2022.2\bin', [string]$Python='python')
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$testManifest = Join-Path $projectRoot 'output\rtl_validation\rtl_pass.json'
if (-not (Test-Path -LiteralPath $testManifest)) { throw 'Run RTL validation before building the overlay.' }
$verification = Get-Content -LiteralPath $testManifest -Raw | ConvertFrom-Json
if ($verification.status -ne 'PASS') { throw 'RTL verification did not pass.' }
if (-not $verification.source_sha256) { throw 'RTL verification source fingerprints are missing.' }
foreach ($entry in $verification.source_sha256.PSObject.Properties) {
    $sourcePath = Join-Path $projectRoot $entry.Name
    if ((Get-FileHash -LiteralPath $sourcePath -Algorithm SHA256).Hash -ne $entry.Value.ToUpperInvariant()) {
        throw "RTL changed since validation: $sourcePath"
    }
}
$buildDirectory = Join-Path $projectRoot 'hardware\build'
New-Item -ItemType Directory -Path $buildDirectory -Force | Out-Null
$pythonPath = $Python
$receiptHelper = Join-Path $PSScriptRoot 'build_receipt.py'
& $pythonPath $receiptHelper begin --stem video_dma --log hardware/build/video_dma_build.log
if ($LASTEXITCODE -ne 0) { throw 'Build receipt source snapshot failed.' }
Push-Location $buildDirectory
try {
    & (Join-Path $VivadoBin 'vivado.bat') -mode batch -notrace -source (Join-Path $PSScriptRoot 'build_dma.tcl') -log video_dma_build.log -journal video_dma_build.jou
    if ($LASTEXITCODE -ne 0) { throw 'Vivado video DMA build failed.' }
} finally { Pop-Location }
& $pythonPath $receiptHelper complete --stem video_dma
if ($LASTEXITCODE -ne 0) { throw 'Completed artifacts do not match the observed source snapshot.' }
