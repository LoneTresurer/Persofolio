# -*- mode: python ; coding: utf-8 -*-
"""
persofolio.spec — recette de construction de l'application de bureau.

Une seule recette, deux résultats selon la machine qui construit :

- **Windows** : un dossier `dist/PersoFolio/` (PersoFolio.exe et ses
  bibliothèques), que l'installateur (Inno Setup) copie chez l'utilisateur.
  Un dossier plutôt qu'un fichier unique : l'exécutable en un seul fichier se
  décompresse dans un dossier temporaire **à chaque lancement**, ce qui ralentit
  chaque ouverture.
- **macOS** : `dist/PersoFolio.app`, processeur Apple (arm64) par défaut, Intel
  si `PERSOFOLIO_ARCH=x86_64`. PyInstaller ne construit que pour la machine où il
  tourne : un Mac pour le .app, un Windows pour le .exe. C'est GitHub Actions qui
  s'en charge (.github/workflows/construire.yml).

À ne pas lancer à la main : le workflow GitHub, ou `construire.bat` pour un essai
local sous Windows.

Trois choses méritent une explication, parce que ce sont les trois façons dont
cette construction peut échouer — et deux d'entre elles ne se voient qu'à
l'usage, pas à la compilation.

**Les pages web.** PyInstaller n'embarque que du code Python. `frontend/` est
copié explicitement, `vendor/` compris (Chart.js, carte), sans quoi
l'application démarre et sert une page blanche, ou des graphiques vides.

**La reconnaissance optique a été retirée** le 27 octobre : plus aucun module ne
l'importe. Si elle revient, c'est `collect_all("rapidocr_onnxruntime")` et
`collect_all("onnxruntime")` qu'il faut remettre, avec un contrôle que les
modèles sont bien embarqués.

**Ce qu'on laisse dehors.** matplotlib, les tests et les outils de notebook
entrent par la porte de derrière, via les dépendances de pandas, et pèsent sans
rien servir ici.
"""

import os
import sys

from PyInstaller.utils.hooks import collect_submodules

WINDOWS = sys.platform == "win32"
MACOS = sys.platform == "darwin"

RACINE = os.path.abspath(os.path.dirname(SPEC))          # noqa: F821  (SPEC : fourni par PyInstaller)
PROJET = os.path.dirname(RACINE)                          # persofolio/
FRONTEND = os.path.join(PROJET, "frontend")
ICONES = os.path.join(PROJET, "icones")

with open(os.path.join(PROJET, "VERSION"), encoding="utf-8") as f:
    VERSION = f.read().strip()
EDITEUR = "IAE Caen - Master G2C 2026"
IDENTIFIANT = "fr.iae-caen.persofolio"

binaires = []
donnees = [
    (FRONTEND, "frontend"),                               # servi par main.py via core.emplacements
    (os.path.join(PROJET, "VERSION"), "."),               # lu par lanceur.py
]

# ── Modules que PyInstaller ne devine pas ───────────────────────────────────
caches = [
    "uvicorn.logging", "uvicorn.loops.auto", "uvicorn.loops.asyncio",
    "uvicorn.protocols.http.auto", "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets.auto", "uvicorn.lifespan.on", "uvicorn.lifespan.off",
    "anyio._backends._asyncio",
    "routes_portefeuille", "routes_banque", "main", "essai_bureau",
]
caches += collect_submodules("core")
caches += collect_submodules("agents")
# Le moteur de fenêtre de pywebview est choisi à l'exécution : on nomme celui
# de la plateforme, sans quoi il manquerait au paquet.
if WINDOWS:
    caches += ["webview.platforms.edgechromium", "webview.platforms.winforms", "clr"]
if MACOS:
    caches += ["webview.platforms.cocoa"]

exclus = [
    "matplotlib", "scipy", "sklearn", "openpyxl",         # l'optimiseur est archivé
    "tkinter.test", "test", "unittest", "pytest",
    "IPython", "jupyter", "notebook", "pandas.tests", "numpy.tests",
    # Moteurs de fenêtre des autres systèmes
    "webview.platforms.gtk", "webview.platforms.qt", "webview.platforms.android",
    "gi", "PyQt5", "PyQt6", "PySide2", "PySide6", "qtpy",
]
if MACOS:
    # Tkinter ne sert qu'au repli « navigateur » de Windows.
    exclus += ["tkinter", "_tkinter"]

a = Analysis(                                             # noqa: F821
    ["lanceur.py"],
    pathex=[RACINE],
    binaries=binaires,
    datas=donnees,
    hiddenimports=caches,
    hookspath=[],
    runtime_hooks=[],
    excludes=exclus,
    noarchive=False,
)

pyz = PYZ(a.pure)                                         # noqa: F821


def icone(nom):
    chemin = os.path.join(ICONES, nom)
    return chemin if os.path.exists(chemin) else None


# ── Informations de version (Windows : Propriétés → Détails) ────────────────
fichier_version = None
if WINDOWS:
    n = tuple(int(x) for x in (VERSION.split(".") + ["0", "0", "0"])[:4])
    fichier_version = os.path.join(workpath, "version_windows.txt")   # noqa: F821  (workpath : fourni)
    os.makedirs(workpath, exist_ok=True)                                # noqa: F821
    with open(fichier_version, "w", encoding="utf-8") as f:
        f.write(f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={n}, prodvers={n}, mask=0x3f, flags=0x0, OS=0x40004,
                    fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040C04B0', [
      StringStruct('CompanyName', '{EDITEUR}'),
      StringStruct('FileDescription', 'PersoFolio'),
      StringStruct('FileVersion', '{VERSION}'),
      StringStruct('InternalName', 'PersoFolio'),
      StringStruct('LegalCopyright', '{EDITEUR}'),
      StringStruct('OriginalFilename', 'PersoFolio.exe'),
      StringStruct('ProductName', 'PersoFolio'),
      StringStruct('ProductVersion', '{VERSION}')])]),
    VarFileInfo([VarStruct('Translation', [0x040C, 1200])])
  ]
)
""")

exe = EXE(                                                # noqa: F821
    pyz, a.scripts, [],
    exclude_binaries=True,                                # mode dossier : les bibliothèques à côté
    name="PersoFolio",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,          # UPX réduit la taille mais ralentit le démarrage et
                        # déclenche des alertes antivirus : le jeu n'en vaut pas la chandelle
    console=False,      # pas de fenêtre noire : l'application a sa propre fenêtre
    icon=icone("persofolio.ico") if WINDOWS else None,
    version=fichier_version,
    target_arch=(os.environ.get("PERSOFOLIO_ARCH") or "arm64") if MACOS else None,
    codesign_identity=None,                               # signature « ad hoc » : pas de compte Apple
)

coll = COLLECT(                                           # noqa: F821
    exe, a.binaries, a.datas,
    strip=False, upx=False,
    name="PersoFolio",
)

if MACOS:
    app = BUNDLE(                                         # noqa: F821
        coll,
        name="PersoFolio.app",
        icon=icone("persofolio.icns"),
        bundle_identifier=IDENTIFIANT,
        version=VERSION,
        info_plist={
            "CFBundleName": "PersoFolio",
            "CFBundleDisplayName": "PersoFolio",
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            "NSHumanReadableCopyright": f"© 2026 {EDITEUR}",
            "LSApplicationCategoryType": "public.app-category.finance",
            "LSMinimumSystemVersion": "11.0",
            "NSHighResolutionCapable": True,
            # La fenêtre charge http://localhost : autorisé explicitement.
            "NSAppTransportSecurity": {"NSAllowsLocalNetworking": True},
        },
    )

# ── Contrôle : les pages sont-elles bien embarquées ? ───────────────────────
# Sans elles l'application démarre et sert une page blanche — l'erreur la plus
# facile à ne pas voir avant de l'avoir distribuée.
pages = [d[0].replace("\\", "/") for d in a.datas if d[0].replace("\\", "/").startswith("frontend/")]
print(f"[persofolio.spec] PersoFolio {VERSION} — fichiers d'interface embarqués : {len(pages)}")
for attendu in ("frontend/index.html", "frontend/banque.html", "frontend/portefeuille.html",
                "frontend/vendor/chart.umd.min.js"):
    if attendu not in pages:
        print(f"[persofolio.spec] ÉCHEC — {attendu} absent du paquet.")
        sys.exit(1)
