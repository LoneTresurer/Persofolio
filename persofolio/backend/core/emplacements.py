"""
core/emplacements.py
====================
Où vivent les fichiers, selon la façon dont l'application a été lancée.

Il y a deux situations, et elles ne demandent pas les mêmes chemins.

**Depuis les sources** (`python -m uvicorn main:app`, ce que fait l'équipe) :
rien ne change par rapport à avant. La base reste dans `backend/data/`, les
pages dans `frontend/`, tout est à sa place dans le dépôt.

**Depuis l'application de bureau** : le dossier du programme n'est pas fait pour
recevoir des données — il est remplacé à chaque mise à jour, et le système peut
en refuser l'écriture. Les données vont donc dans le dossier que chaque système
réserve aux applications de l'utilisateur, qui survit aux mises à jour et se
sauvegarde avec son profil :

- Windows : `%LOCALAPPDATA%\\PersoFolio`
- macOS : `~/Library/Application Support/PersoFolio`
- Linux : `$XDG_DATA_HOME/PersoFolio`, à défaut `~/.local/share/PersoFolio`

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
    elif GELE and sys.platform == "darwin":
        chemin = os.path.join(os.path.expanduser("~"), "Library", "Application Support", APPLICATION)
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
