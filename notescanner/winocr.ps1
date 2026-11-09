# Windows' innebygde OCR via WinRT. Kjøres av NoteScanner med Windows PowerShell 5.1 (powershell.exe).
# Bruk: powershell -NoProfile -ExecutionPolicy Bypass -File winocr.ps1 <bilde.png> <språk,liste>
param([string]$Path, [string]$Lang = "nb-NO,nb,en-US")
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
try {
    Add-Type -AssemblyName System.Runtime.WindowsRuntime
    $null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
    $null = [Windows.Storage.Streams.IRandomAccessStream, Windows.Storage.Streams, ContentType = WindowsRuntime]
    $null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
    $null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics, ContentType = WindowsRuntime]
    $null = [Windows.Globalization.Language, Windows.Globalization, ContentType = WindowsRuntime]

    $asTask = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
        $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
        $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' } | Select-Object -First 1
    function Await($op, [Type]$type) {
        $task = $asTask.MakeGenericMethod($type).Invoke($null, @($op))
        $null = $task.Wait(-1)
        $task.Result
    }

    $file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($Path)) ([Windows.Storage.StorageFile])
    $stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
    $decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
    $bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])

    $engine = $null
    foreach ($tag in $Lang.Split(',')) {
        try {
            $l = New-Object Windows.Globalization.Language $tag
            if ([Windows.Media.Ocr.OcrEngine]::IsLanguageSupported($l)) {
                $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($l)
                if ($engine) { break }
            }
        } catch {}
    }
    if (-not $engine) { $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages() }
    if (-not $engine) { Write-Output (ConvertTo-Json @{ error = "Ingen OCR-språk installert i Windows" } -Compress); exit 2 }

    $result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
    $lines = @()
    foreach ($ln in $result.Lines) {
        $words = @()
        foreach ($w in $ln.Words) {
            $r = $w.BoundingRect
            $words += @{ t = $w.Text; x = $r.X; y = $r.Y; w = $r.Width; h = $r.Height }
        }
        $lines += @{ text = $ln.Text; words = $words }
    }
    $out = @{ lang = $engine.RecognizerLanguage.LanguageTag; lines = $lines }
    Write-Output (ConvertTo-Json $out -Depth 6 -Compress)
    $stream.Dispose()
} catch {
    Write-Output (ConvertTo-Json @{ error = [string]$_.Exception.Message } -Compress)
    exit 1
}
