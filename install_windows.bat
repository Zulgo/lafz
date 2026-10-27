@echo off
rem Lafz installer for Windows 10/11. Double-click this file.
cd /d "%~dp0"
echo ==^> Installing uv (Python manager)...
where uv >nul 2>nul || powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
set "PATH=%USERPROFILE%\.local\bin;%PATH%"
echo ==^> Installing Ollama (optional, for LLM cleanup)...
where ollama >nul 2>nul || winget install -e --id Ollama.Ollama --accept-source-agreements --accept-package-agreements
rem winget's PATH change only applies to new windows, so add Ollama's install folder for this one.
set "PATH=%LOCALAPPDATA%\Programs\Ollama;%PATH%"
ollama list >nul 2>nul || (start "" /b ollama serve & timeout /t 5 /nobreak >nul)
ollama pull qwen2.5:1.5b || echo (Skipped cleanup model; Lafz will use rule-based cleanup.)
echo ==^> Installing Lafz libraries...
uv sync || goto :fail
echo ==^> Creating Desktop shortcut...
powershell -c "Remove-Item -ErrorAction SilentlyContinue ([Environment]::GetFolderPath('Desktop')+'\Murmur.lnk')"
powershell -c "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop')+'\Lafz.lnk'); $s.TargetPath='%CD%\.venv\Scripts\pythonw.exe'; $s.Arguments='app.py'; $s.WorkingDirectory='%CD%'; $s.IconLocation='%CD%\assets\icon.ico'; $s.Save()"
echo Done. Open "Lafz" from your Desktop. Hold Right-Ctrl to dictate.
pause
exit /b 0
:fail
echo Install failed. See messages above.
pause
