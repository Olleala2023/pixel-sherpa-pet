@echo off
rem Starts the pet without a console window: .venv from the install script first, otherwise pythonw from PATH.
cd /d "%~dp0"
if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" "%~dp0sherpa_pet.py"
) else (
    start "" pythonw "%~dp0sherpa_pet.py"
)
