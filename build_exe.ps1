$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

if (-not (Get-Command pyinstaller -ErrorAction SilentlyContinue)) {
    Write-Host "PyInstaller is not installed. Installing it for the current user..."
    py -m pip install --user pyinstaller
}

py -m PyInstaller --noconfirm --clean --onefile --windowed `
    --name "FolderContentSearch" `
    "folder_content_search.py"

Write-Host ""
Write-Host "Created: $PSScriptRoot\dist\FolderContentSearch.exe"
