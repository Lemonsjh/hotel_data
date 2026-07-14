$ErrorActionPreference = "Stop"
$desktop = [Environment]::GetFolderPath("Desktop")
$shell = New-Object -ComObject WScript.Shell
Add-Type -AssemblyName System.Drawing
Add-Type @"
using System;
using System.Runtime.InteropServices;
public static class HotelIconNative {
    [DllImport("user32.dll")]
    public static extern bool DestroyIcon(IntPtr handle);
}
"@

function ConvertFrom-CodePoints([int[]]$Codes) {
    return -join ($Codes | ForEach-Object { [char]$_ })
}

$startName = ConvertFrom-CodePoints @(0x542F, 0x52A8, 0x9152, 0x5E97, 0x6570, 0x636E, 0x91C7, 0x96C6)
$stopName = ConvertFrom-CodePoints @(0x505C, 0x6B62, 0x9152, 0x5E97, 0x6570, 0x636E, 0x91C7, 0x96C6)
$iconDir = Join-Path $PSScriptRoot "icons"
New-Item -ItemType Directory -Path $iconDir -Force | Out-Null

function New-ServiceIcon([string]$Path, [string]$Color, [ValidateSet("Play", "Stop")][string]$Glyph) {
    $bitmap = New-Object System.Drawing.Bitmap 64, 64
    $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
    $graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $graphics.Clear([System.Drawing.Color]::Transparent)
    $graphics.FillEllipse((New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(35, 0, 0, 0))), 5, 7, 54, 54)
    $graphics.FillEllipse((New-Object System.Drawing.SolidBrush ([System.Drawing.ColorTranslator]::FromHtml($Color))), 5, 4, 54, 54)
    $white = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::White)
    if ($Glyph -eq "Play") {
        $points = [System.Drawing.Point[]]@(
            (New-Object System.Drawing.Point 26, 18),
            (New-Object System.Drawing.Point 26, 44),
            (New-Object System.Drawing.Point 45, 31)
        )
        $graphics.FillPolygon($white, $points)
    } else {
        $graphics.FillRectangle($white, 22, 19, 21, 24)
    }
    $handle = $bitmap.GetHicon()
    try {
        $icon = [System.Drawing.Icon]::FromHandle($handle)
        $stream = [System.IO.File]::Create($Path)
        try { $icon.Save($stream) } finally { $stream.Dispose(); $icon.Dispose() }
    } finally {
        [HotelIconNative]::DestroyIcon($handle) | Out-Null
        $white.Dispose()
        $graphics.Dispose()
        $bitmap.Dispose()
    }
}

$startIcon = Join-Path $iconDir "start.ico"
$stopIcon = Join-Path $iconDir "stop.ico"
New-ServiceIcon $startIcon "#0F8A70" "Play"
New-ServiceIcon $stopIcon "#D6453D" "Stop"

$items = @(
    @{ Name = $startName; Target = ($startName + ".bat"); Icon = $startIcon },
    @{ Name = $stopName; Target = ($stopName + ".bat"); Icon = $stopIcon }
)

foreach ($item in $items) {
    $shortcut = $shell.CreateShortcut((Join-Path $desktop ($item.Name + ".lnk")))
    $shortcut.TargetPath = Join-Path $PSScriptRoot $item.Target
    $shortcut.WorkingDirectory = $PSScriptRoot
    $shortcut.IconLocation = $item.Icon
    $shortcut.Save()
}

Write-Host "Desktop shortcuts installed."
