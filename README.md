# Folder Content Search

A small desktop application that searches the contents of every file in a chosen folder and lists the matching files. It works with ordinary text files and binary files containing an ASCII/UTF-8 or UTF-16 representation of the search text.

## Run it

This application needs Python 3 with Tkinter (included in the standard Windows Python installer).

1. Double-click `run_folder_search.bat`, or run:

   ```powershell
   py folder_content_search.py
   ```

2. Choose a folder.
3. For normal text search, enter the full or partial string, for example `7075BC442EED707A20F9C30126`, then click **Search**.
4. For LicenseServer comparison, turn on **LicenseServer compare mode**, choose the `LicenseServer.win-x64.exe` or `LicenseServer.winDbg-x64.exe` executable, enter the HTTP URL for the `check_match` endpoint, choose the source `.pt` or `.ptl` file, then select whether to compare `.pt`, `.ptl`, or both.
5. Yes, both compare checkboxes can be selected at the same time. That makes the app search both `.pt` and `.ptl` files in one run.
6. The app keeps going through every candidate and lists every file where the server returns `"match": true`.
7. Double-click a result to open it, or select it and use **Open containing folder**.

The search reads files in 1 MB chunks, so it can scan large files without loading each whole file into memory. Unreadable files are skipped and counted in the status line.

## Build a standalone Windows EXE

Run the following from PowerShell:

```powershell
.\build_exe.ps1
```

The script installs PyInstaller if needed and creates:

```text
dist\FolderContentSearch.exe
```

The built EXE can run on another Windows computer without installing Python.
