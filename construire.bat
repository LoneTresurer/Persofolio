@echo off
rem ===========================================================================
rem  construire.bat - fabrique PersoFolio.exe
rem
rem  A lancer sur une machine Windows, par un double-clic.
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
echo   PersoFolio - construction de l'executable Windows
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
rem "if ... && ..." sur une meme ligne se lit de plusieurs facons selon la
rem version de Windows. On evite, et on teste en deux temps.
set "PY="
py --version >nul 2>&1
if not errorlevel 1 set "PY=py"
if defined PY goto :python_trouve
python --version >nul 2>&1
if not errorlevel 1 set "PY=python"
:python_trouve

if not defined PY (
    echo   [ECHEC] Python est introuvable sur cette machine.
    echo.
    echo   Installez-le depuis https://www.python.org/downloads/
    echo   IMPORTANT : cochez "Add Python to PATH" pendant l'installation.
    echo   Fermez cette fenetre, puis relancez ce fichier.
    exit /b 1
)

for /f "tokens=2" %%V in ('%PY% --version 2^>^&1') do set "VERSION=%%V"
echo   Python %VERSION% trouve.

echo %VERSION% | findstr /b "3.13 3.14 3.15" >nul
if not errorlevel 1 (
    echo.
    echo   [ATTENTION] Python %VERSION% est tres recent. Si l'installation des
    echo   bibliotheques echoue plus bas, installez Python 3.11 ou 3.12 :
    echo   ce sont les versions pour lesquelles toutes les dependances
    echo   existent, onnxruntime en particulier.
    echo.
)

rem --- 2. Environnement isole -------------------------------------------------
set "PYV=%~dp0.venv-build\Scripts\python.exe"

if not exist "%PYV%" (
    echo   Creation de l'environnement de construction...
    %PY% -m venv ".venv-build"
    if errorlevel 1 (
        echo   [ECHEC] L'environnement n'a pas pu etre cree.
        exit /b 1
    )
)
if not exist "%PYV%" (
    echo   [ECHEC] L'environnement a ete cree mais python.exe est introuvable.
    echo   Attendu ici : %PYV%
    exit /b 1
)

rem --- 3. Dependances ---------------------------------------------------------
echo   Installation des bibliotheques. C'est la partie longue.
echo.
"%PYV%" -m pip install --upgrade pip --quiet --disable-pip-version-check

"%PYV%" -m pip install -r "persofolio\backend\requirements.txt" --disable-pip-version-check
if errorlevel 1 (
    echo.
    echo   [ECHEC] Une bibliotheque n'a pas pu etre installee.
    echo   Une coupure reseau suffit a le provoquer : relancez ce fichier,
    echo   la reprise est rapide, rien n'est retelecharge inutilement.
    exit /b 1
)

"%PYV%" -m pip install pyinstaller onnxruntime --disable-pip-version-check
if errorlevel 1 (
    echo.
    echo   [ECHEC] PyInstaller ou onnxruntime n'a pas pu etre installe.
    echo   Si le message ci-dessus parle de version non trouvee, votre Python
    echo   est trop recent : installez Python 3.11 ou 3.12.
    exit /b 1
)

rem --- 4. Controle avant construction -----------------------------------------
echo.
echo   Verification de la reconnaissance optique...
"%PYV%" -c "import rapidocr_onnxruntime, onnxruntime; print('   OCR disponible, onnxruntime ' + onnxruntime.__version__)"
if errorlevel 1 (
    echo.
    echo   [ECHEC] La reconnaissance optique n'est pas disponible ; l'executable
    echo   ne saurait pas lire un releve scanne. Construction interrompue.
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

if not exist "dist\PersoFolio.exe" (
    echo.
    echo   [ECHEC] PyInstaller s'est termine sans erreur, mais
    echo   dist\PersoFolio.exe n'existe pas. C'est anormal : signalez-le.
    exit /b 1
)

rem --- 6. Resultat ------------------------------------------------------------
set "TAILLE=?"
for %%F in ("dist\PersoFolio.exe") do set /a TAILLE=%%~zF/1048576
copy /y "LISEZ-MOI.txt" "dist\LISEZ-MOI.txt" >nul 2>&1

echo.
echo   =========================================================
echo     Termine.   dist\PersoFolio.exe   (%TAILLE% Mo)
echo   =========================================================
echo.
echo   Essayez-le sur cette machine avant de le distribuer :
echo     1. Double-cliquez dist\PersoFolio.exe
echo     2. Importez un releve SCANNE, pour verifier la lecture optique
echo     3. Creez un compte, quittez, rouvrez : le compte doit etre la
echo.
echo   A envoyer avec LISEZ-MOI.txt (copie dans dist) : Windows affichera
echo   un avertissement chez vos destinataires, ce fichier leur explique
echo   quoi faire.
exit /b 0
