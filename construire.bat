@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
title Construction de PersoFolio

REM ============================================================================
REM  construire.bat — fabrique PersoFolio.exe
REM
REM  A lancer sur une machine Windows, par un double-clic. Le script installe
REM  tout ce dont il a besoin dans un environnement isole (dossier .venv-build),
REM  sans rien changer au Python de la machine.
REM
REM  La premiere construction prend 5 a 15 minutes : elle telecharge environ
REM  300 Mo de bibliotheques. Les suivantes sont bien plus rapides, les
REM  telechargements etant conserves.
REM ============================================================================

cd /d "%~dp0"
echo.
echo   PersoFolio — construction de l'executable Windows
echo   =================================================
echo.

REM ── 1. Python ───────────────────────────────────────────────────────────────
set PY=
for %%C in (py python) do (
    if not defined PY (
        %%C --version >nul 2>&1 && set PY=%%C
    )
)
if not defined PY (
    echo   [ECHEC] Python est introuvable sur cette machine.
    echo.
    echo   Installez-le depuis https://www.python.org/downloads/
    echo   En cochant bien la case "Add Python to PATH" pendant l'installation,
    echo   puis relancez ce fichier.
    echo.
    pause
    exit /b 1
)
for /f "tokens=2" %%V in ('%PY% --version 2^>^&1') do set VERSION=%%V
echo   Python %VERSION% trouve.

REM Python 3.13 : onnxruntime n'y est pas toujours disponible. On previent.
echo %VERSION% | findstr /b "3.13 3.14" >nul && (
    echo.
    echo   [ATTENTION] Python %VERSION% est tres recent. Si l'installation des
    echo   bibliotheques echoue plus bas, installez Python 3.11 ou 3.12 a cote :
    echo   ce sont les versions pour lesquelles toutes les dependances existent.
    echo.
)

REM ── 2. Environnement isole ──────────────────────────────────────────────────
if not exist ".venv-build\Scripts\python.exe" (
    echo   Creation de l'environnement de construction...
    %PY% -m venv .venv-build || (
        echo   [ECHEC] Impossible de creer l'environnement.
        pause & exit /b 1
    )
)
set PYV=.venv-build\Scripts\python.exe

REM ── 3. Dependances ──────────────────────────────────────────────────────────
echo   Installation des bibliotheques (long la premiere fois)...
%PYV% -m pip install --upgrade pip --quiet
%PYV% -m pip install -r persofolio\backend\requirements.txt --quiet || (
    echo.
    echo   [ECHEC] Une bibliotheque n'a pas pu etre installee.
    echo   Relancez le script : un simple probleme de reseau suffit a le faire
    echo   echouer, et la reprise est rapide.
    pause & exit /b 1
)
%PYV% -m pip install pyinstaller --quiet || (
    echo   [ECHEC] PyInstaller n'a pas pu etre installe.
    pause & exit /b 1
)

REM onnxruntime n'est pas dans requirements.txt : rapidocr l'embarque,
REM mais on le declare pour que PyInstaller le trouve a coup sur.
%PYV% -m pip install onnxruntime --quiet

REM ── 4. Controle avant construction ──────────────────────────────────────────
echo   Verification de la reconnaissance optique...
%PYV% -c "import rapidocr_onnxruntime, onnxruntime; print('   OCR disponible, onnxruntime', onnxruntime.__version__)" || (
    echo.
    echo   [ECHEC] La reconnaissance optique n'est pas installee ; l'executable
    echo   ne saurait pas lire un releve scanne. Construction interrompue.
    pause & exit /b 1
)

REM ── 5. Construction ─────────────────────────────────────────────────────────
echo.
echo   Construction en cours. Comptez plusieurs minutes.
echo.
pushd persofolio\backend
..\..\%PYV% -m PyInstaller --noconfirm --clean --distpath ..\..\dist --workpath ..\..\build persofolio.spec
set CODE=%ERRORLEVEL%
popd

if not "%CODE%"=="0" (
    echo.
    echo   [ECHEC] La construction s'est interrompue ^(code %CODE%^).
    echo   Le message d'erreur se trouve juste au-dessus.
    pause & exit /b %CODE%
)

if not exist "dist\PersoFolio.exe" (
    echo.
    echo   [ECHEC] PyInstaller s'est termine sans erreur mais dist\PersoFolio.exe
    echo   n'existe pas. Signalez-le : c'est anormal.
    pause & exit /b 1
)

REM ── 6. Resultat ─────────────────────────────────────────────────────────────
for %%F in ("dist\PersoFolio.exe") do set /a TAILLE=%%~zF/1048576
copy /y "LISEZ-MOI.txt" "dist\LISEZ-MOI.txt" >nul 2>&1

echo.
echo   ========================================================
echo     Termine. dist\PersoFolio.exe  (%TAILLE% Mo)
echo   ========================================================
echo.
echo   Avant de le distribuer, lancez-le une fois sur cette machine,
echo   et importez un releve scanne pour verifier la lecture optique.
echo.
echo   A envoyer avec LISEZ-MOI.txt : Windows affichera un avertissement
echo   chez vos destinataires, et ce fichier leur explique quoi faire.
echo.
pause
