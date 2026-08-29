[CmdletBinding()]
param(
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"
$repositoryRoot = Split-Path -Parent $PSScriptRoot
$toolRoot = Join-Path ([Environment]::GetFolderPath("UserProfile")) "Tools\LectureSift"

Add-Type -TypeDefinition @'
using System.Runtime.InteropServices;
using System.Text;

public static class LectureSiftPathNative
{
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    public static extern uint GetShortPathName(string longPath, StringBuilder shortPath, uint bufferLength);
}
'@

function Get-ShortPath {
    param([Parameter(Mandatory)][string]$Path)

    $buffer = New-Object Text.StringBuilder 32768
    $length = [LectureSiftPathNative]::GetShortPathName($Path, $buffer, $buffer.Capacity)
    if ($length -eq 0) {
        return $Path
    }
    return $buffer.ToString()
}

function Get-LatestToolDirectory {
    param([Parameter(Mandatory)][string]$Pattern)

    $directory = Get-ChildItem -LiteralPath $toolRoot -Directory -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -like $Pattern } |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1
    if (-not $directory) {
        throw "Required local tool is missing: $Pattern"
    }
    return $directory.FullName
}

$ffmpegRoot = Get-ShortPath (Get-LatestToolDirectory "ffmpeg-*")
$tesseractRoot = Get-ShortPath (Get-LatestToolDirectory "Tesseract-OCR-*")
$sevenZipRoot = Get-ShortPath (Get-LatestToolDirectory "7-Zip-*")
$tessdata = Join-Path $tesseractRoot "tessdata"
$venvPython = Join-Path $repositoryRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $venvPython)) {
    throw "Local Python environment is missing. Create .venv and install requirements-dev.txt first."
}

$env:LECTURESIFT_TOOL_PATH = "$(Join-Path $ffmpegRoot 'bin');$tesseractRoot;$sevenZipRoot"
$env:PATH = "$env:LECTURESIFT_TOOL_PATH;$env:PATH"
$env:TESSDATA_PREFIX = $tessdata
$env:LECTURESIFT_OCR_COMMAND = Join-Path $tesseractRoot "tesseract.exe"
$publicTestRoot = "C:\Users\Public\LectureSiftTests"
New-Item -ItemType Directory -Path $publicTestRoot -Force | Out-Null
$env:LECTURESIFT_TEST_TEMP_ROOT = $publicTestRoot

Push-Location $repositoryRoot
try {
    $arguments = @("scripts/run_windows_checks.py")
    if ($SkipTests) {
        $arguments += "--skip-tests"
    }
    & $venvPython @arguments
    if ($LASTEXITCODE -ne 0) {
        throw "LectureSift environment or test check failed."
    }
}
finally {
    Pop-Location
}
