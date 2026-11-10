@echo off
rem ===================================================================
rem  PersoFolio - double-cliquez ce fichier, c'est tout.
rem
rem  Il se debrouille seul :
rem   - cree l'environnement Python s'il n'existe pas
rem   - le REFAIT s'il est casse, ce qui arrive des qu'on deplace ou
rem     renomme le dossier du projet (un environnement retient le
rem     chemin absolu ou il a ete cree)
rem   - installe les bibliotheques seulement si elles manquent
rem   - demarre le serveur et ouvre le navigateur
rem
rem  Il n'y a donc jamais rien a reinstaller a la main.
rem ===================================================================
setlocal
cd /d "%~dp0"
set "VENV=.venv"
set "PY=%VENV%\Scripts\python.exe"
set "REQ=persofolio\backend\requirements.txt"

rem --- L'environnement est-il la, et en etat de marche ? -------------
if not exist "%PY%" goto creer
"%PY%" -c "import sys" >nul 2>&1
if errorlevel 1 (
  echo L'environnement Python est abime, probablement parce que le dossier
  echo a ete deplace ou renomme. Je le refais.
  goto creer
)
goto dependances

:creer
echo.
echo Preparation de l'environnement Python, une seule fois...
if exist "%VENV%" rmdir /s /q "%VENV%"
py -3.12 -m venv "%VENV%" >nul 2>&1
if not exist "%PY%" py -3 -m venv "%VENV%" >nul 2>&1
if not exist "%PY%" python -m venv "%VENV%" >nul 2>&1
if not exist "%PY%" (
  echo.
  echo   Python est introuvable sur cette machine.
  echo.
  echo   Installez-le depuis https://www.python.org/downloads/
  echo   en cochant la case "Add Python to PATH" pendant l'installation,
  echo   puis double-cliquez de nouveau sur ce fichier.
  echo.
  pause
  exit /b 1
)

:dependances
rem --- Les bibliotheques sont-elles installees ? ---------------------
"%PY%" -c "import uvicorn, fastapi, pandas, yfinance, pdfplumber" >nul 2>&1
if errorlevel 1 (
  echo.
  echo Installation des bibliotheques. Comptez deux a trois minutes,
  echo c'est la seule attente, et elle n'aura pas lieu la prochaine fois.
  echo.
  "%PY%" -m pip install --quiet --disable-pip-version-check --upgrade pip
  "%PY%" -m pip install --disable-pip-version-check -r "%REQ%"
  if errorlevel 1 (
    echo.
    echo   L'installation a echoue. Copiez le message ci-dessus
    echo   et envoyez-le pour qu'on regarde.
    echo.
    pause
    exit /b 1
  )
)

rem --- Demarrage -----------------------------------------------------
echo.
echo   PersoFolio demarre.
echo   Votre navigateur va s'ouvrir sur http://localhost:8000
echo.
echo   LAISSEZ CETTE FENETRE OUVERTE pendant que vous utilisez
echo   l'application. Pour quitter : fermez-la, ou Ctrl + C.
echo.
rem Le navigateur est ouvert avec six secondes de retard, dans une fenetre
rem separee : lance tout de suite, il afficherait une erreur de connexion,
rem le serveur n'etant pas encore pret.
start "" /min cmd /c "timeout /t 6 /nobreak >nul & start http://localhost:8000"
cd persofolio\backend
"%~dp0%VENV%\Scripts\python.exe" -m uvicorn main:app --port 8000
echo.
echo Le serveur s'est arrete.
pause
exit /b 0
