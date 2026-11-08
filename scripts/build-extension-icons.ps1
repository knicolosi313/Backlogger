# Export the extension's existing layers mark as PNGs. No additional packages needed.
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing
$projectRoot = Split-Path $PSScriptRoot -Parent
$iconDirectory = Join-Path $projectRoot 'apps/extension/public/icon'
$storeDirectory = Join-Path $projectRoot 'docs/store-assets'
New-Item -ItemType Directory -Force -Path $iconDirectory, $storeDirectory | Out-Null

function Export-BackloggerIcon([int]$Size, [string]$Destination, [float]$Padding = 0) {
    $renderSize = $Size * 4
    $bitmap = [System.Drawing.Bitmap]::new($renderSize, $renderSize)
    $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
    $graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $graphics.Clear([System.Drawing.Color]::Transparent)
    $graphics.ScaleTransform($renderSize / 24.0, $renderSize / 24.0)
    $graphics.TranslateTransform($Padding, $Padding)
    $graphics.ScaleTransform((24 - 2 * $Padding) / 24, (24 - 2 * $Padding) / 24)
    $shape = [System.Drawing.Drawing2D.GraphicsPath]::new()
    $shape.AddArc(0, 0, 9, 9, 180, 90)
    $shape.AddArc(15, 0, 9, 9, 270, 90)
    $shape.AddArc(15, 15, 9, 9, 0, 90)
    $shape.AddArc(0, 15, 9, 9, 90, 90)
    $shape.CloseFigure()
    $gradient = [System.Drawing.Drawing2D.LinearGradientBrush]::new(
        [System.Drawing.Rectangle]::new(0, 0, 24, 24),
        [System.Drawing.ColorTranslator]::FromHtml('#a78bfa'),
        [System.Drawing.ColorTranslator]::FromHtml('#6d28d9'), 60.0)
    $graphics.FillPath($gradient, $shape)
    $pen = [System.Drawing.Pen]::new([System.Drawing.Color]::White, 1.65)
    $pen.StartCap = $pen.EndCap = [System.Drawing.Drawing2D.LineCap]::Round
    $pen.LineJoin = [System.Drawing.Drawing2D.LineJoin]::Round
    # Same three layers as components/Icon.tsx, inset for legible toolbar rendering.
    $graphics.DrawPolygon($pen, [System.Drawing.PointF[]]@(
        [System.Drawing.PointF]::new(12, 5), [System.Drawing.PointF]::new(4.5, 8.75),
        [System.Drawing.PointF]::new(12, 12.5), [System.Drawing.PointF]::new(19.5, 8.75)))
    foreach ($y in @(12.5, 16.25)) {
        $graphics.DrawLines($pen, [System.Drawing.PointF[]]@(
            [System.Drawing.PointF]::new(4.5, $y), [System.Drawing.PointF]::new(12, $y + 3.75),
            [System.Drawing.PointF]::new(19.5, $y)))
    }
    $output = [System.Drawing.Bitmap]::new($Size, $Size)
    $outputGraphics = [System.Drawing.Graphics]::FromImage($output)
    $outputGraphics.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
    $outputGraphics.DrawImage($bitmap, 0, 0, $Size, $Size)
    $output.Save($Destination, [System.Drawing.Imaging.ImageFormat]::Png)
    $outputGraphics.Dispose(); $output.Dispose()
    $pen.Dispose(); $gradient.Dispose(); $shape.Dispose(); $graphics.Dispose(); $bitmap.Dispose()
}

foreach ($size in @(16, 32, 48, 128)) {
    Export-BackloggerIcon $size (Join-Path $iconDirectory "$size.png") 1
}
# Chrome's store icon: 96px artwork inside a transparent 128px canvas.
Export-BackloggerIcon 128 (Join-Path $storeDirectory 'store-icon-128.png') 3
Write-Output 'Exported toolbar icons and Chrome Web Store icon.'
