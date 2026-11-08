$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing
$projectRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$directory = Join-Path $projectRoot 'docs/store-assets/screenshots'
foreach ($file in Get-ChildItem -LiteralPath $directory -Filter '*.png') {
    $source = [System.Drawing.Image]::FromFile($file.FullName)
    if ($source.Width -ne 1280 -or $source.Height -ne 800) { throw "Unexpected size: $($file.Name)" }
    # Format conversion only: preserve every visible pixel and remove the alpha channel.
    $rgb = [System.Drawing.Bitmap]::new(1280, 800, [System.Drawing.Imaging.PixelFormat]::Format24bppRgb)
    $graphics = [System.Drawing.Graphics]::FromImage($rgb)
    $graphics.DrawImageUnscaled($source, 0, 0)
    $source.Dispose()
    $rgb.Save($file.FullName, [System.Drawing.Imaging.ImageFormat]::Png)
    $graphics.Dispose(); $rgb.Dispose()
    $bytes = [System.IO.File]::ReadAllBytes($file.FullName)
    if ($bytes[24] -ne 8 -or $bytes[25] -ne 2) { throw "Expected 8-bit RGB PNG without alpha: $($file.Name)" }
    Write-Output "$($file.Name): 1280x800, 24-bit RGB PNG, no alpha"
}
