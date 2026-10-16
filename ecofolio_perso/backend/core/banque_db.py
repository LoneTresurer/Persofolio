"""
core/banque_db.py
=================
Stockage du module « Budget et comptes », dans la même base que le compte-titres
(data/portefeuille.db) et rattaché au même utilisateur (table utilisateurs).

Prototype : l'état complet du module (comptes, opérations, opérations programmées,
dettes, objectifs, budgets) est stocké en un document JSON versionné par utilisateur.
La version sert au contrôle de concurrence optimiste : une écriture fondée sur une
version périmée (autre onglet, autre appareil) est refusée au lieu d'écraser les données.
Étape suivante prévue : éclater ce document en tables (operations_banque, dettes…)
lorsque le compte investissement sera relié aux espèces du compte-titres.
"""

import json
import sqlite3
from datetime import datetime
from typing import Optional, Tuple

from core.portfolio_db import DB_PATH


class ConflitVersion(Exception):
    def __init__(self, version_actuelle: int):
        super().__init__(f"version actuelle : {version_actuelle}")
        self.version_actuelle = version_actuelle


class BanqueDB:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        with self._connect() as c:
            c.execute("""
            CREATE TABLE IF NOT EXISTS banque_etats (
                username  TEXT PRIMARY KEY REFERENCES utilisateurs(username),
                etat      TEXT NOT NULL,          -- document JSON du module
                version   INTEGER NOT NULL,       -- incrémentée à chaque écriture
                cree_le   TEXT NOT NULL,
                maj_le    TEXT NOT NULL
            )""")

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def lire(self, username: str) -> Tuple[Optional[dict], int]:
        with self._connect() as c:
            row = c.execute("SELECT etat, version FROM banque_etats WHERE username=?", (username,)).fetchone()
        return (json.loads(row["etat"]), row["version"]) if row else (None, 0)

    def ecrire(self, username: str, etat: dict, version_attendue: int) -> int:
        """Écrit l'état si la version en base est celle attendue ; renvoie la nouvelle version."""
        texte = json.dumps(etat, ensure_ascii=False, separators=(",", ":"))
        maintenant = datetime.now().isoformat(timespec="seconds")
        with self._connect() as c:
            c.execute("BEGIN IMMEDIATE")          # verrou d'écriture : lecture + écriture atomiques
            row = c.execute("SELECT version FROM banque_etats WHERE username=?", (username,)).fetchone()
            actuelle = row["version"] if row else 0
            if actuelle != version_attendue:
                raise ConflitVersion(actuelle)
            nouvelle = actuelle + 1
            if row:
                c.execute("UPDATE banque_etats SET etat=?, version=?, maj_le=? WHERE username=?",
                          (texte, nouvelle, maintenant, username))
            else:
                c.execute("INSERT INTO banque_etats VALUES (?,?,?,?,?)",
                          (username, texte, nouvelle, maintenant, maintenant))
        return nouvelle

    def supprimer(self, username: str):
        with self._connect() as c:
            c.execute("DELETE FROM banque_etats WHERE username=?", (username,))
