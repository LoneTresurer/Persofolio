"""
core/emplacements.py
====================
Où vivent les fichiers, selon la façon dont l'application a été lancée.

Il y a deux situations, et elles ne demandent pas les mêmes chemins.

**Depuis les sources** (`python -m uvicorn main:app`, ce que fait l'équipe) :
rien ne change par rapport à avant. La base reste dans `backend/data/`, les
pages dans `frontend/`, tout est à sa place dans le dépôt.

**Depuis l'exécutable Windows** : PyInstaller déballe l'application dans un
dossier temporaire qu'il efface à la fermeture. Y écrire la base reviendrait à
perdre tous les comptes à chaque sortie — et si l'utilisateur a installé le
programme dans `C:\\Program Files`, Windows refuserait même l'écriture. Les
données vont donc dans `%LOCALAPPDATA%\\PersoFolio`, qui appartient à
l'utilisateur, survit aux mises à jour et se sauvegarde avec son profil.

`PERSOFOLIO_DONNEES` force le dossier de données, quelle que soit la situation :
utile pour les tests, et pour faire tourner deux jeux de comptes côte à côte.
"""

from __future__ import annotations

import os
import sys

APPLICATION = "PersoFolio"

#: Vrai quand le programme tourne depuis l'exécutable produit par PyInstaller.
GELE = getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")

#: Racine des fichiers livrés avec le programme (pages web, données jointes).
#: Dans l'exécutable, c'est le dossier temporaire de déballage ; sinon, `backend/`.
RACINE = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def dossier_donnees() -> str:
    """Dossier où sont écrits la base et les journaux. Créé s'il n'existe pas."""
    force = os.environ.get("PERSOFOLIO_DONNEES")
    if force:
        chemin = os.path.abspath(force)
    elif GELE:
        base = (os.environ.get("LOCALAPPDATA")                       # Windows
                or os.environ.get("XDG_DATA_HOME")                   # Linux
                or os.path.join(os.path.expanduser("~"), ".local", "share"))
        chemin = os.path.join(base, APPLICATION)
    else:
        chemin = os.path.join(RACINE, "data")
    os.makedirs(chemin, exist_ok=True)
    return chemin


def fichier_base() -> str:
    """Chemin complet de la base SQLite : comptes, opérations, budget."""
    return os.path.join(dossier_donnees(), "portefeuille.db")


def dossier_pages() -> str:
    """Dossier des pages web servies par l'API (`index.html` et les deux modules)."""
    if GELE:
        return os.path.join(RACINE, "frontend")
    return os.path.join(os.path.dirname(RACINE), "frontend")
