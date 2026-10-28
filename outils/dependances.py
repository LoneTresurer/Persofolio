#!/usr/bin/env python3
"""
outils/dependances.py
=====================
Compare ce que `requirements.txt` déclare à ce que le code importe vraiment.

Écrit après deux dérives constatées, dans les deux sens.

En trop : après que l'équipe compte-titres a réécrit la lecture des avis PDF,
trois paquets de reconnaissance optique sont restés déclarés sans qu'aucun
module ne les importe — environ 85 Mo d'exécutable Windows pour du code que
personne n'appelle. Rien ne le signalait : un paquet installé et jamais importé
ne fait échouer aucun test.

Manquant : le cas inverse est pire. Le serveur démarre chez celui qui a la
bibliothèque installée de longue date, et tombe en ImportError chez le suivant.

    python outils/dependances.py

Le dossier archive_optimiseur/ est ignoré : il n'est pas chargé par le serveur,
et ses dépendances sont déclarées commentées, volontairement.
"""

from __future__ import annotations

import ast
import os
import re
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
BACKEND = RACINE / "persofolio" / "backend"
IGNORES = {"archive_optimiseur", "__pycache__", "tests", ".venv-build"}

# Modules locaux du projet : ce ne sont pas des dépendances externes.
LOCAUX = {"core", "agents", "main", "routes_portefeuille", "routes_banque", "lanceur", "essai_bureau"}

# Nom sur PyPI quand il diffère du nom importé.
PAQUET = {
    "yaml": "pyyaml",
    "PIL": "pillow",
    "sklearn": "scikit-learn",
    "rapidocr_onnxruntime": "rapidocr-onnxruntime",
    "fitz": "pymupdf",
    "dateutil": "python-dateutil",
    "webview": "pywebview",
}

# Déclarés sans être importés directement, et c'est normal : ce sont des
# dépendances de fonctionnement, pas d'API.
INDIRECTS = {
    "python-multipart": "FastAPI s'en sert pour recevoir les fichiers déposés",
    "uvicorn": "serveur, lancé par lanceur.py ou en ligne de commande",
    "pyinstaller": "outil de construction (requirements-bureau.txt)",
}

#: Les deux fichiers sont lus ensemble : requirements-bureau.txt complète
#: requirements.txt pour l'application de bureau (fenêtre native, construction).
FICHIERS = ("requirements.txt", "requirements-bureau.txt")


def imports_du_code() -> dict[str, set[str]]:
    trouve: dict[str, set[str]] = {}
    stdlib = set(sys.stdlib_module_names)
    for dossier, sous, fichiers in os.walk(BACKEND):
        sous[:] = [d for d in sous if d not in IGNORES]
        for f in fichiers:
            if not f.endswith(".py"):
                continue
            chemin = Path(dossier) / f
            try:
                arbre = ast.parse(chemin.read_text(encoding="utf-8"))
            except SyntaxError:
                continue
            for n in ast.walk(arbre):
                noms = []
                if isinstance(n, ast.Import):
                    noms = [a.name for a in n.names]
                elif isinstance(n, ast.ImportFrom) and n.module and n.level == 0:
                    noms = [n.module]
                for m in noms:
                    racine = m.split(".")[0]
                    if racine in stdlib or racine in LOCAUX:
                        continue
                    nom = PAQUET.get(racine, racine).lower()
                    trouve.setdefault(nom, set()).add(
                        str(chemin.relative_to(BACKEND)))
    return trouve


def declares() -> tuple[set[str], set[str]]:
    """Paquets actifs, et paquets mis en commentaire."""
    actifs, commentes = set(), set()
    lignes = [l for f in FICHIERS if (BACKEND / f).is_file()
              for l in (BACKEND / f).read_text(encoding="utf-8").splitlines()]
    for ligne in lignes:
        nu = ligne.strip()
        if not nu or nu.startswith("-r"):
            continue
        cible = commentes if nu.startswith("#") else actifs
        nu = nu.lstrip("#").strip()
        m = re.match(r"^([A-Za-z0-9._-]+)", nu)
        if not m:
            continue
        nom = m.group(1).lower()
        # Une ligne de commentaire en prose commence souvent par un mot ordinaire.
        if cible is commentes and not re.match(r"^[A-Za-z0-9._-]+\s*(\[|[<>=!]|$)", nu):
            continue
        cible.add(nom)
    return actifs, commentes


def main() -> int:
    importes = imports_du_code()
    actifs, commentes = declares()
    noms_importes = set(importes)

    manquants = sorted(n for n in noms_importes if n not in actifs)
    inutiles = sorted(n for n in actifs if n not in noms_importes and n not in INDIRECTS)

    print(f"{len(noms_importes)} paquets importés par le code actif, "
          f"{len(actifs)} déclarés dans requirements.txt.\n")

    if manquants:
        print("MANQUANTS — importés mais non déclarés :")
        for n in manquants:
            print(f"  ✗ {n}")
            for f in sorted(importes[n]):
                print(f"      {f}")
            if n in commentes:
                print("      (présent en commentaire : le décommenter)")
        print()

    if inutiles:
        print("EN TROP — déclarés mais importés par aucun module :")
        for n in inutiles:
            print(f"  ~ {n}")
        print("     Les retirer allège l'exécutable Windows.\n")

    if not manquants and not inutiles:
        print("✓ requirements.txt correspond exactement au code.")
        for n, pourquoi in INDIRECTS.items():
            if n in actifs:
                print(f"  (· {n} : {pourquoi})")
        return 0
    # Un paquet en trop est un avertissement ; un manquant casse le démarrage.
    return 1 if manquants else 0


if __name__ == "__main__":
    sys.exit(main())
