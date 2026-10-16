"""
core/portfolio_db.py
====================
Stockage SQLite du suivi de portefeuille (fichier data/portefeuille.db) :
  - utilisateurs : identifiant + mot de passe haché (PBKDF2, jamais stocké en clair)
  - jetons       : jetons de connexion (renvoyés au navigateur après login)
  - operations   : carnet d'ordres / mouvements de chaque utilisateur
  - titres       : fiche descriptive des titres (nom, devise, secteur, pays), en cache
"""

import hashlib
import os
import secrets
import sqlite3
from datetime import datetime
from typing import List, Optional

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "portefeuille.db")


class PortfolioDB:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._connect() as c:
            c.executescript("""
            CREATE TABLE IF NOT EXISTS utilisateurs (
                username   TEXT PRIMARY KEY,
                sel        TEXT NOT NULL,
                hash_mdp   TEXT NOT NULL,
                cree_le    TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS jetons (
                jeton      TEXT PRIMARY KEY,
                username   TEXT NOT NULL REFERENCES utilisateurs(username),
                cree_le    TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS operations (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                username    TEXT NOT NULL REFERENCES utilisateurs(username),
                date        TEXT NOT NULL,          -- YYYY-MM-DD
                type        TEXT NOT NULL,          -- DEPOT RETRAIT ACHAT VENTE DIVIDENDE
                ticker      TEXT,
                quantite    REAL,
                prix        REAL,                   -- en devise du titre
                devise      TEXT,
                taux_eur    REAL,                   -- EUR pour 1 unité de devise
                frais_eur   REAL DEFAULT 0,
                montant_eur REAL,                   -- dépôts, retraits, dividendes
                commentaire TEXT,
                saisi_le    TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_ops_user ON operations(username, date);
            CREATE TABLE IF NOT EXISTS titres (
                ticker      TEXT PRIMARY KEY,
                nom         TEXT,
                devise      TEXT,
                secteur     TEXT,
                pays        TEXT,
                type_actif  TEXT,
                maj_le      TEXT
            );
            """)

    # ── Utilisateurs & connexion ─────────────────────────────────────────────

    @staticmethod
    def _hash(mdp: str, sel: str) -> str:
        return hashlib.pbkdf2_hmac("sha256", mdp.encode(), sel.encode(), 200_000).hex()

    def creer_utilisateur(self, username: str, mdp: str) -> bool:
        sel = secrets.token_hex(16)
        try:
            with self._connect() as c:
                c.execute("INSERT INTO utilisateurs VALUES (?,?,?,?)",
                          (username, sel, self._hash(mdp, sel), datetime.now().isoformat()))
            return True
        except sqlite3.IntegrityError:
            return False

    def verifier_mdp(self, username: str, mdp: str) -> bool:
        with self._connect() as c:
            row = c.execute("SELECT sel, hash_mdp FROM utilisateurs WHERE username=?",
                            (username,)).fetchone()
        return bool(row) and secrets.compare_digest(row["hash_mdp"], self._hash(mdp, row["sel"]))

    def creer_jeton(self, username: str) -> str:
        jeton = secrets.token_urlsafe(32)
        with self._connect() as c:
            c.execute("INSERT INTO jetons VALUES (?,?,?)",
                      (jeton, username, datetime.now().isoformat()))
        return jeton

    def utilisateur_du_jeton(self, jeton: str) -> Optional[str]:
        with self._connect() as c:
            row = c.execute("SELECT username FROM jetons WHERE jeton=?", (jeton,)).fetchone()
        return row["username"] if row else None

    def supprimer_jeton(self, jeton: str):
        with self._connect() as c:
            c.execute("DELETE FROM jetons WHERE jeton=?", (jeton,))

    # ── Opérations ───────────────────────────────────────────────────────────

    def ajouter_operation(self, username: str, op: dict) -> int:
        with self._connect() as c:
            cur = c.execute("""
                INSERT INTO operations (username, date, type, ticker, quantite, prix, devise,
                                        taux_eur, frais_eur, montant_eur, commentaire, saisi_le)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (username, op["date"], op["type"], op.get("ticker"), op.get("quantite"),
                 op.get("prix"), op.get("devise"), op.get("taux_eur"),
                 op.get("frais_eur") or 0.0, op.get("montant_eur"), op.get("commentaire"),
                 datetime.now().isoformat()))
            return cur.lastrowid

    def operations(self, username: str) -> List[dict]:
        with self._connect() as c:
            rows = c.execute("SELECT * FROM operations WHERE username=? ORDER BY date, id",
                             (username,)).fetchall()
        return [dict(r) for r in rows]

    def supprimer_operation(self, username: str, op_id: int) -> bool:
        with self._connect() as c:
            cur = c.execute("DELETE FROM operations WHERE id=? AND username=?", (op_id, username))
        return cur.rowcount > 0

    # ── Fiches titres (cache) ────────────────────────────────────────────────

    def fiche_titre(self, ticker: str) -> Optional[dict]:
        with self._connect() as c:
            row = c.execute("SELECT * FROM titres WHERE ticker=?", (ticker,)).fetchone()
        return dict(row) if row else None

    def enregistrer_fiche(self, fiche: dict):
        with self._connect() as c:
            c.execute("""INSERT OR REPLACE INTO titres VALUES (?,?,?,?,?,?,?)""",
                      (fiche["ticker"], fiche.get("nom"), fiche.get("devise"),
                       fiche.get("secteur"), fiche.get("pays"), fiche.get("type_actif"),
                       datetime.now().isoformat()))
