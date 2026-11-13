@echo off
rem ==========================================================================
rem  QuadPV - abre a interface com um Python ja instalado no computador.
rem
rem  Procura, nesta ordem:
rem    1. a variavel de ambiente QUADPV_PYTHON (caminho completo do python.exe)
rem    2. um ambiente virtual .venv nesta pasta
rem    3. Miniconda / Anaconda / Miniforge na pasta do usuario, em %LOCALAPPDATA%,
rem       em %ProgramData% e em C:\
rem    4. o Python oficial (lancador "py" e %LOCALAPPDATA%\Programs\Python)
rem    5. o "python" do PATH
rem  Exige Python 3.10 ou mais novo. Se faltar alguma biblioteca, oferece instalar
rem  com "pip install -r requirements.txt".
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
    echo Nenhum Python 3.10 ou mais novo foi encontrado.
    echo Instale o Miniconda ^(https://docs.conda.io/projects/miniconda^) ou o Python ^(https://www.python.org^)
    echo e rode este arquivo de novo. Se o Python estiver em outro lugar, defina a variavel
    echo QUADPV_PYTHON com o caminho completo do python.exe.
    echo.
    pause
    exit /b 1
)

rem Bibliotecas do Miniconda/Anaconda (DLLs) precisam estar no PATH quando o base nao foi ativado.
for %%F in ("%PY%") do set "PYDIR=%%~dpF"
set "PATH=%PYDIR%;%PYDIR%Library\bin;%PYDIR%Scripts;%PATH%"
echo Python: %PY%

"%PY%" -c "import numpy, serial, pyqtgraph; from pyqtgraph.Qt import QtCore" >nul 2>&1
if errorlevel 1 (
    echo.
    echo Faltam bibliotecas para a interface: numpy, pyserial, pyqtgraph e PySide6.
    choice /c SN /m "Instalar agora com pip"
    if errorlevel 2 exit /b 1
    "%PY%" -m pip install -r "%~dp0requirements.txt"
    if errorlevel 1 (
        echo.
        echo A instalacao falhou. Veja a mensagem acima.
        pause
        exit /b 1
    )
)

"%PY%" "%~dp0interface\quadpv.py" %*
if errorlevel 1 (
    echo.
    echo A interface terminou com erro. Veja a mensagem acima.
    pause
)
exit /b

rem --------------------------------------------------------------------------
rem :try caminho  -  aceita o python se ele existir e for 3.10 ou mais novo
:try
if defined PY goto :eof
if not exist "%~1" goto :eof
"%~1" -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if not errorlevel 1 set "PY=%~1"
goto :eof
