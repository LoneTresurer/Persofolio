# -*- mode: python ; coding: utf-8 -*-
"""
persofolio.spec — recette de construction de l'exécutable Windows.

À ne pas lancer à la main : `construire.bat`, à la racine du projet, s'en charge.

Trois choses méritent une explication, parce que ce sont les trois façons dont
cette construction peut échouer — et deux d'entre elles ne se voient qu'à
l'usage, pas à la compilation.

**Les pages web.** PyInstaller n'embarque que du code Python. `frontend/` est
copié explicitement, sans quoi l'exécutable démarre et sert une page blanche.

**La reconnaissance optique a été retirée** le 27 octobre : plus aucun module ne
l'importe. Elle demandait un soin particulier ici — PyInstaller ne suit que les
`import`, et laissait dehors les modèles `.onnx` et les DLL d'onnxruntime, ce qui
donnait un exécutable qui se construisait et ne tombait qu'au premier relevé
scanné. Si elle revient, c'est `collect_all("rapidocr_onnxruntime")` et
`collect_all("onnxruntime")` qu'il faut remettre, avec un contrôle que les
modèles sont bien embarqués.

**Ce qu'on laisse dehors.** matplotlib, les tests et les outils de notebook
entrent dans l'exécutable par la porte de derrière, via les dépendances de
pandas, et pèsent sans rien servir ici.
"""

import os
import sys

from PyInstaller.utils.hooks import collect_submodules

RACINE = os.path.abspath(os.path.dirname(SPEC))          # noqa: F821  (SPEC : fourni par PyInstaller)
PROJET = os.path.dirname(RACINE)                          # persofolio/
FRONTEND = os.path.join(PROJET, "frontend")

binaires = []
donnees = [(FRONTEND, "frontend")]                        # servi par main.py via core.emplacements
caches = []

# ── Modules que PyInstaller ne devine pas ───────────────────────────────────
caches += [
    "uvicorn.logging", "uvicorn.loops.auto", "uvicorn.loops.asyncio",
    "uvicorn.protocols.http.auto", "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets.auto", "uvicorn.lifespan.on", "uvicorn.lifespan.off",
    "anyio._backends._asyncio",
    "routes_portefeuille", "routes_banque", "main",
]
caches += collect_submodules("core")
caches += collect_submodules("agents")

a = Analysis(                                             # noqa: F821
    ["lanceur.py"],
    pathex=[RACINE],
    binaries=binaires,
    datas=donnees,
    hiddenimports=caches,
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        "matplotlib", "scipy", "sklearn", "openpyxl",     # l'optimiseur est archivé
        "tkinter.test", "test", "unittest", "pytest",
        "IPython", "jupyter", "notebook", "pandas.tests", "numpy.tests",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)                                         # noqa: F821

exe = EXE(                                                # noqa: F821
    pyz, a.scripts, a.binaries, a.datas, [],
    name="PersoFolio",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,          # UPX réduit la taille mais ralentit le démarrage et
                        # déclenche des alertes antivirus : le jeu n'en vaut pas la chandelle
    runtime_tmpdir=None,
    console=False,      # pas de fenêtre noire ; le lanceur ouvre sa propre fenêtre
    icon=os.path.join(PROJET, "frontend", "persofolio.ico")
         if os.path.exists(os.path.join(PROJET, "frontend", "persofolio.ico")) else None,
    version=None,
)

# ── Contrôle : les pages sont-elles bien embarquées ? ───────────────────────
# Sans elles l'exécutable démarre et sert une page blanche — l'erreur la plus
# facile à ne pas voir avant de l'avoir distribuée.
pages = [d for d in a.datas if d[0].replace("\\", "/").startswith("frontend/")]
print(f"[persofolio.spec] fichiers d'interface embarqués : {len(pages)}")
if not any(d[0].endswith("index.html") for d in pages):
    print("[persofolio.spec] ÉCHEC — frontend/index.html absent du paquet.")
    sys.exit(1)
