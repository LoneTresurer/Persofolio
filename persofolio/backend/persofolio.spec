# -*- mode: python ; coding: utf-8 -*-
"""
persofolio.spec — recette de construction de l'exécutable Windows.

À ne pas lancer à la main : `construire.bat`, à la racine du projet, s'en charge.

Trois choses méritent une explication, parce que ce sont les trois façons dont
cette construction peut échouer — et deux d'entre elles ne se voient qu'à
l'usage, pas à la compilation.

**Les pages web.** PyInstaller n'embarque que du code Python. `frontend/` est
copié explicitement, sans quoi l'exécutable démarre et sert une page blanche.

**La reconnaissance optique.** C'est le point fragile. `rapidocr_onnxruntime`
range ses modèles (`.onnx`) et son `config.yaml` à côté de son code, et
`onnxruntime` arrive avec ses propres DLL. PyInstaller ne suit que les `import` :
il ne voit ni les modèles, ni les DLL, et produit un exécutable qui se construit
sans erreur, démarre sans erreur, et ne tombe que le jour où quelqu'un dépose un
relevé scanné. `collect_all` les ramasse ; le contrôle en fin de fichier vérifie
qu'ils sont bien là plutôt que de le découvrir chez l'utilisateur.

**Ce qu'on laisse dehors.** matplotlib, les tests et les outils de notebook
entrent dans l'exécutable par la porte de derrière, via les dépendances de
pandas, et pèsent sans rien servir ici.
"""

import os
import sys

from PyInstaller.utils.hooks import collect_all, collect_submodules

RACINE = os.path.abspath(os.path.dirname(SPEC))          # noqa: F821  (SPEC : fourni par PyInstaller)
PROJET = os.path.dirname(RACINE)                          # persofolio/
FRONTEND = os.path.join(PROJET, "frontend")

binaires = []
donnees = [(FRONTEND, "frontend")]                        # servi par main.py via core.emplacements
caches = []

# ── Reconnaissance optique : modèles et DLL ─────────────────────────────────
ocr_complet = True
for paquet in ("rapidocr_onnxruntime", "onnxruntime"):
    try:
        d, b, c = collect_all(paquet)
        donnees += d
        binaires += b
        caches += c
    except Exception as e:                                # noqa: BLE001
        ocr_complet = False
        print(f"[persofolio.spec] ATTENTION — {paquet} introuvable : {e}")

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

# ── Contrôle : l'OCR est-il réellement embarqué ? ───────────────────────────
# Le seul moyen de ne pas livrer un exécutable qui tombera chez l'utilisateur.
modeles = [d for d in a.datas if d[0].endswith(".onnx")]
dll_ort = [b for b in a.binaries if "onnxruntime" in b[0].lower()]
print(f"[persofolio.spec] modèles OCR embarqués : {len(modeles)}")
print(f"[persofolio.spec] bibliothèques onnxruntime embarquées : {len(dll_ort)}")
if not ocr_complet or not modeles:
    print("[persofolio.spec] ÉCHEC — la reconnaissance optique ne serait pas fonctionnelle.")
    print("[persofolio.spec] Vérifiez : pip install rapidocr-onnxruntime onnxruntime")
    sys.exit(1)
