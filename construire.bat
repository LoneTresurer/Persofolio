@echo off
rem ===========================================================================
rem  construire.bat - fabrique PersoFolio pour Windows, sur cette machine
rem
rem  A lancer sur une machine Windows, par un double-clic.
rem
rem  Sert aux essais locaux. La version a distribuer (installateur Windows et
rem  application Mac) est fabriquee par GitHub Actions : onglet "Actions" du
rem  depot, "Construire PersoFolio", "Run workflow".
rem
rem  Le script installe tout ce dont il a besoin dans un dossier isole
rem  (.venv-build) sans rien changer au Python de la machine.
rem
rem  La premiere construction prend 5 a 15 minutes : environ 300 Mo a
rem  telecharger. Les suivantes sont bien plus rapides.
rem
rem  Ce fichier est volontairement ecrit sans accents et avec des fins de
rem  ligne Windows (CRLF) : cmd.exe refuse de lire correctement un .bat
rem  enregistre autrement, et la fenetre se refermerait aussitot.
rem ===========================================================================

setlocal
cd /d "%~dp0"

echo.
echo   PersoFolio - construction de l'application Windows
echo   =================================================
echo.

call :construire
set FINAL=%ERRORLEVEL%

echo.
if "%FINAL%"=="0" goto :fin
echo   La construction ne s'est pas terminee.
echo   Le message d'erreur se trouve plus haut dans cette fenetre.
echo.
echo   Si vous devez le signaler, copiez tout le texte de cette fenetre :
echo   clic droit sur la barre de titre, Modifier, Selectionner tout, Entree.
:fin
echo.
echo   Appuyez sur une touche pour fermer cette fenetre.
pause >nul
exit /b %FINAL%


rem ===========================================================================
:construire
rem ===========================================================================

rem --- 0. Sommes-nous au bon endroit ? ---------------------------------------
rem Un double-clic depuis l'interieur du .zip, sans decompresser, copie ce seul
rem fichier dans un dossier temporaire : le reste du projet serait absent.
if not exist "persofolio\backend\persofolio.spec" (
    echo   [ECHEC] Le projet est introuvable a cote de ce fichier.
    echo.
    echo   Verifiez deux choses :
    echo     1. Le .zip est bien DECOMPRESSE. Lancer ce fichier directement
    echo        depuis la fenetre du .zip ne fonctionne pas.
    echo     2. construire.bat se trouve a cote du dossier "persofolio".
    echo.
    echo   Dossier actuel : %CD%
    exit /b 1
)

rem --- 1. Python --------------------------------------------------------------
rem Python 3.10 ou plus recent. La limite a 3.12 qui figurait ici venait de
rem rapidocr-onnxruntime, retire le 27 octobre : c'etait le seul paquet a ne pas
rem etre publie au-dela. Tout ce qui reste a des roues pour les versions
rem recentes. Si l'OCR revient un jour, la limite revient avec lui.
rem
rem On essaie quand meme les versions nommees d'abord : sur une machine qui en a
rem plusieurs, elles sont plus surement completes que ce que "python" designe.

set "PY="
set "VERSION="

py -3 --version >nul 2>&1
if not errorlevel 1 set "PY=py -3"
if defined PY goto :python_verifie

rem Aucune version nommee : le Python par defaut fera l'affaire s'il convient.
python --version >nul 2>&1
if not errorlevel 1 set "PY=python"
if defined PY goto :python_verifie
py --version >nul 2>&1
if not errorlevel 1 set "PY=py"

:python_verifie
if not defined PY goto :pas_de_python
rem Compatible ? Python repond mieux que findstr sur un numero de version.
%PY% -c "import sys; sys.exit(0 if sys.version_info[:2] >= (3,10) else 1)" >nul 2>&1
if errorlevel 1 goto :python_incompatible

:python_trouve
for /f "tokens=2" %%V in ('%PY% --version 2^>^&1') do set "VERSION=%%V"
echo   Python %VERSION% retenu pour la construction.
goto :python_ok

:pas_de_python
echo   [ECHEC] Python est introuvable sur cette machine.
echo.
echo   Installez Python depuis https://www.python.org/downloads/
echo   Prenez "Windows installer (64-bit)" et cochez "Add Python to PATH".
echo   Fermez cette fenetre, puis relancez ce fichier.
exit /b 1

:python_incompatible
for /f "tokens=2" %%V in ('%PY% --version 2^>^&1') do set "VERSION=%%V"
echo   [ECHEC] Aucune version de Python utilisable n'a ete trouvee.
echo.
echo   La seule disponible ici est Python %VERSION%, trop ancienne : il faut
echo   au moins Python 3.10.
echo.
echo   Installez une version recente A COTE de celle-ci, sans la desinstaller :
echo     https://www.python.org/downloads/
echo   Prenez "Windows installer (64-bit)". Les versions cohabitent sans
echo   probleme, et ce script choisira tout seul.
echo.
echo   Versions actuellement installees :
py -0p 2>nul
echo.
exit /b 1

:python_ok

rem --- 2. Environnement isole -------------------------------------------------
set "PYV=%~dp0.venv-build\Scripts\python.exe"

rem Un environnement laisse par une tentative precedente peut reposer sur une
rem version de Python incompatible. On le verifie plutot que de le reutiliser.
if not exist "%PYV%" goto :creer_env
"%PYV%" -c "import sys; sys.exit(0 if sys.version_info[:2] >= (3,10) else 1)" >nul 2>&1
if not errorlevel 1 goto :env_pret
echo   L'environnement de construction existant repose sur une version de
echo   Python incompatible. Il est supprime et refait.
rmdir /s /q ".venv-build"

:creer_env
echo   Creation de l'environnement de construction...
%PY% -m venv ".venv-build"
if errorlevel 1 (
    echo   [ECHEC] L'environnement n'a pas pu etre cree.
    exit /b 1
)

:env_pret
if not exist "%PYV%" (
    echo   [ECHEC] L'environnement a ete cree mais python.exe est introuvable.
    echo   Attendu ici : %PYV%
    exit /b 1
)

rem --- 3. Dependances ---------------------------------------------------------
echo   Installation des bibliotheques. C'est la partie longue.
echo.
"%PYV%" -m pip install --upgrade pip --quiet --disable-pip-version-check

"%PYV%" -m pip install -r "persofolio\backend\requirements-bureau.txt" --disable-pip-version-check
if errorlevel 1 (
    echo.
    echo   [ECHEC] Une bibliotheque n'a pas pu etre installee.
    echo.
    echo   Si le message ci-dessus dit "Could not find a version" ou
    echo   "No matching distribution", c'est une incompatibilite de version,
    echo   pas un probleme de reseau : signalez-le, en copiant ces lignes.
    echo.
    echo   Sinon, une coupure reseau suffit a le provoquer : relancez ce
    echo   fichier, la reprise est rapide, rien n'est retelecharge en vain.
    exit /b 1
)

rem --- 5. Construction --------------------------------------------------------
echo.
echo   Construction en cours. Comptez plusieurs minutes.
echo.
pushd "persofolio\backend"
"%PYV%" -m PyInstaller --noconfirm --clean --distpath "%~dp0dist" --workpath "%~dp0build" persofolio.spec
set "CODE=%ERRORLEVEL%"
popd

if not "%CODE%"=="0" (
    echo.
    echo   [ECHEC] PyInstaller s'est interrompu, code %CODE%.
    exit /b %CODE%
)

if not exist "dist\PersoFolio\PersoFolio.exe" (
    echo.
    echo   [ECHEC] PyInstaller s'est termine sans erreur, mais
    echo   dist\PersoFolio\PersoFolio.exe n'existe pas. C'est anormal : signalez-le.
    exit /b 1
)

rem --- 6. Resultat ------------------------------------------------------------
echo.
echo   =========================================================
echo     Termine.   dist\PersoFolio\PersoFolio.exe
echo   =========================================================
echo.
echo   C'est un dossier, pas un fichier unique : PersoFolio.exe a besoin
echo   de tout ce qui se trouve a cote de lui dans dist\PersoFolio.
echo.
echo   Essayez-le sur cette machine :
echo     1. Double-cliquez dist\PersoFolio\PersoFolio.exe
echo     2. Importez un releve bancaire PDF
echo     3. Fermez la fenetre, rouvrez : vous devez etre encore connecte
echo.
echo   Pour distribuer l'application, utilisez plutot l'installateur
echo   fabrique par GitHub Actions.
exit /b 0
