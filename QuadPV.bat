@echo off
rem ==========================================================================
rem  QuadPV - opens the interface with a Python already installed on the computer.
rem
rem  Looks, in this order, for:
rem    1. the QUADPV_PYTHON environment variable (full path to python.exe)
rem    2. a .venv virtual environment in this folder
rem    3. Miniconda / Anaconda / Miniforge in the user folder, in %LOCALAPPDATA%,
rem       in %ProgramData% and in C:\
rem    4. the official Python ("py" launcher and %LOCALAPPDATA%\Programs\Python)
rem    5. "python" on the PATH
rem  Requires Python 3.10 or newer. If a library is missing, offers to install
rem  it with "pip install -r requirements.txt".
rem ==========================================================================
setlocal EnableExtensions
cd /d "%~dp0"
set "PY="

if defined QUADPV_PYTHON call :try "%QUADPV_PYTHON%"
if not defined PY call :try "%~dp0.venv\Scripts\python.exe"

for %%D in ("%USERPROFILE%\miniconda3" "%USERPROFILE%\anaconda3" "%USERPROFILE%\miniforge3" "%USERPROFILE%\mambaforge" "%LOCALAPPDATA%\miniconda3" "%LOCALAPPDATA%\anaconda3" "%ProgramData%\miniconda3" "%ProgramData%\anaconda3" "C:\miniconda3" "C:\anaconda3") do (
    if not defined PY call :try "%%~D\python.exe"
)

if not defined PY (
    for /f "delims=" %%P in ('py -3 -c "import sys; print(sys.executable)" 2^>nul') do call :try "%%P"
)
if not defined PY (
    for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do call :try "%%~D\python.exe"
)
if not defined PY (
    for /f "delims=" %%P in ('python -c "import sys; print(sys.executable)" 2^>nul') do call :try "%%P"
)

if not defined PY (
    echo.
    echo No Python 3.10 or newer was found.
    echo Install Miniconda ^(https://docs.conda.io/projects/miniconda^) or Python ^(https://www.python.org^)
    echo and run this file again. If Python is somewhere else, set the QUADPV_PYTHON
    echo variable to the full path of python.exe.
    echo.
    pause
    exit /b 1
)

rem Miniconda/Anaconda libraries (DLLs) must be on the PATH when the base environment was not activated.
for %%F in ("%PY%") do set "PYDIR=%%~dpF"
set "PATH=%PYDIR%;%PYDIR%Library\bin;%PYDIR%Scripts;%PATH%"
echo Python: %PY%

"%PY%" -c "import numpy, serial, pyqtgraph; from pyqtgraph.Qt import QtCore" >nul 2>&1
if errorlevel 1 (
    echo.
    echo Libraries missing for the interface: numpy, pyserial, pyqtgraph and PySide6.
    choice /c YN /m "Install them now with pip"
    if errorlevel 2 exit /b 1
    "%PY%" -m pip install -r "%~dp0requirements.txt"
    if errorlevel 1 (
        echo.
        echo The installation failed. See the message above.
        pause
        exit /b 1
    )
)

"%PY%" "%~dp0interface\quadpv.py" %*
if errorlevel 1 (
    echo.
    echo The interface exited with an error. See the message above.
    pause
)
exit /b

rem --------------------------------------------------------------------------
rem :try path  -  accepts the python if it exists and is 3.10 or newer
:try
if defined PY goto :eof
if not exist "%~1" goto :eof
"%~1" -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if not errorlevel 1 set "PY=%~1"
goto :eof
