@echo off
REM scripts/launch_storm.bat — activates the storm conda environment and launches STORM.
REM This file is called by the desktop shortcut created by create_app_windows.bat.
REM Any options given to this file are passed on to main.py (e.g. --perf-log).

REM Resolve project root (one level above scripts/)
FOR %%I IN ("%~dp0..") DO SET "PROJECT_DIR=%%~fI"

REM ── Single-instance guard ─────────────────────────────────────────────────────
powershell -NoProfile -WindowStyle Hidden -Command "(Get-WmiObject Win32_Process | Where-Object {$_.Name -eq 'pythonw.exe' -and $_.CommandLine -like '*main.py*'} | Measure-Object).Count" > "%TEMP%\_storm_running.tmp" 2>nul
SET /P STORM_COUNT=<"%TEMP%\_storm_running.tmp"
DEL /F /Q "%TEMP%\_storm_running.tmp" 2>nul
IF %STORM_COUNT% GTR 0 (
    powershell -NoProfile -WindowStyle Hidden -Command "Add-Type -AssemblyName System.Windows.Forms; [System.Windows.Forms.MessageBox]::Show('STORM is already running.', 'STORM', 'OK', 'Information')"
    EXIT /B 0
)

REM ── Find conda ────────────────────────────────────────────────────────────────
SET "CONDA_BAT="
FOR %%D IN (
    "%USERPROFILE%\miniforge3"
    "%USERPROFILE%\miniconda3"
    "%USERPROFILE%\anaconda3"
    "%LOCALAPPDATA%\miniforge3"
    "%LOCALAPPDATA%\miniconda3"
    "%LOCALAPPDATA%\anaconda3"
    "%PROGRAMDATA%\miniforge3"
    "%PROGRAMDATA%\miniconda3"
    "%PROGRAMDATA%\anaconda3"
    "C:\ProgramData\miniforge3"
    "C:\ProgramData\miniconda3"
    "C:\ProgramData\anaconda3"
    "C:\miniforge3"
    "C:\miniconda3"
    "C:\anaconda3"
) DO (
    IF EXIST "%%~D\condabin\conda.bat" (
        SET "CONDA_BAT=%%~D\condabin\conda.bat"
        GOTO :found_conda
    )
)

powershell -NoProfile -WindowStyle Hidden -Command "Add-Type -AssemblyName System.Windows.Forms; [System.Windows.Forms.MessageBox]::Show('Could not find a conda installation.`n`nRun setup: python setup.py', 'STORM — Launch Error', 'OK', 'Error')"
EXIT /B 1

:found_conda
CALL "%CONDA_BAT%" activate storm 2>nul
IF ERRORLEVEL 1 (
    CALL "%CONDA_BAT%" activate storm311 2>nul
)

IF "%CONDA_DEFAULT_ENV%" == "" (
    powershell -NoProfile -WindowStyle Hidden -Command "Add-Type -AssemblyName System.Windows.Forms; [System.Windows.Forms.MessageBox]::Show('The ""storm"" conda environment was not found.`n`nRun setup: python setup.py', 'STORM — Launch Error', 'OK', 'Error')"
    EXIT /B 1
)

cd /d "%PROJECT_DIR%"


REM Use certifi's CA bundle — Windows conda envs may not inherit system SSL certs
FOR /F "delims=" %%F IN ('dir /b /s "%CONDA_PREFIX%\Lib\site-packages\certifi\cacert.pem" 2^>nul') DO (
    SET "SSL_CERT_FILE=%%F"
    SET "REQUESTS_CA_BUNDLE=%%F"
    GOTO :ssl_done
)
:ssl_done

REM Notify the user that STORM is starting (balloon tip in system tray)
START "" /B powershell -NoProfile -WindowStyle Hidden -Command "Add-Type -AssemblyName System.Windows.Forms; $n=New-Object System.Windows.Forms.NotifyIcon; $n.Icon=[System.Drawing.SystemIcons]::Application; $n.Visible=$true; $n.ShowBalloonTip(5000,'STORM','Starting up, please wait...','Info'); Start-Sleep 6; $n.Dispose()"

REM pythonw suppresses the console window for GUI apps
IF NOT EXIST "%CONDA_PREFIX%\pythonw.exe" (
    powershell -NoProfile -WindowStyle Hidden -Command "Add-Type -AssemblyName System.Windows.Forms; [System.Windows.Forms.MessageBox]::Show('pythonw.exe not found in the storm environment.%CONDA_PREFIX%', 'STORM — Launch Error', 'OK', 'Error')"
    EXIT /B 1
)
REM Use START with /B to prevent a new console window from appearing
START "" /B "%CONDA_PREFIX%\pythonw.exe" "%PROJECT_DIR%\main.py" %*
