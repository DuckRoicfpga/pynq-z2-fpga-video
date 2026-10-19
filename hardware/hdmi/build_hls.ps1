param(
    [string]$VitisHls = 'C:\Xilinx\Vitis_HLS\2022.2\bin\vitis_hls.bat',
    [ValidatePattern('^[A-Z]$')][string]$AliasDrive = 'R'
)
$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$vendorRoot = Join-Path $PSScriptRoot 'vendor\PYNQ'
if (!(Test-Path -LiteralPath $VitisHls)) { throw "Vitis HLS missing: $VitisHls" }
if (!(Test-Path -LiteralPath (Join-Path $vendorRoot 'boards\ip\hls'))) { throw 'Pinned PYNQ source missing.' }
$driveRoot = "${AliasDrive}:\"
if (Test-Path -LiteralPath $driveRoot) { throw "$driveRoot already exists; use a free drive letter." }
$origin = Get-Location
try {
    # HLS 2022.2 rejects spaces in project paths. subst is only a path alias:
    # all generated files stay under the already-authorized projectRoot.
    & subst "${AliasDrive}:" $projectRoot
    if ($LASTEXITCODE) { throw 'subst failed.' }
    $relativeHls = 'hardware\hdmi\vendor\PYNQ\boards\ip\hls'
    Set-Location (Join-Path $driveRoot $relativeHls)
    foreach ($name in @('color_convert', 'pixel_pack', 'pixel_unpack')) {
        & $VitisHls -f "$name\script.tcl"
        # HLS can return exit code 0 after Tcl errors; verify artifact too.
        $component = "$name\solution1\impl\ip\component.xml"
        if ($LASTEXITCODE -or !(Test-Path -LiteralPath $component)) { throw "HLS failed: $name" }
    }
}
finally {
    Set-Location $origin
    & subst "${AliasDrive}:" /D
}
