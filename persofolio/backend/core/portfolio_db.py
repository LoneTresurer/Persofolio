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
            CREATE TABLE IF NOT EXISTS parametres (
                username            TEXT PRIMARY KEY REFERENCES utilisateurs(username),
                prelevement_div_pct REAL DEFAULT 0,        -- 0 = PEA, 30 = CTO (flat tax)
                indice              TEXT DEFAULT 'MSCI_WORLD',
                -- 'euros_dividendes' : indice tel que le vit un épargnant français.
                -- 'devise_origine'   : indice de prix dans sa monnaie, comme dans la presse.
                mode_indice         TEXT DEFAULT 'euros_dividendes',
                -- Taux sans risque annuel, en %, utilisé au dénominateur du Sharpe.
                taux_sans_risque    REAL DEFAULT 2.0
            );
            CREATE TABLE IF NOT EXISTS dividendes_traites (   -- évite de créditer deux fois
                username    TEXT NOT NULL,
                ticker      TEXT NOT NULL,
                date_detach TEXT NOT NULL,
                PRIMARY KEY (username, ticker, date_detach)
            );
            CREATE TABLE IF NOT EXISTS notifications (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                username  TEXT NOT NULL,
                cree_le   TEXT NOT NULL,
                titre     TEXT NOT NULL,
                message   TEXT NOT NULL,
                lue       INTEGER DEFAULT 0
            );
            -- Un utilisateur peut suivre plusieurs portefeuilles distincts : un PEA, un
            -- compte-titres, un portefeuille d'école… Chacun a ses propres opérations et
            -- donc ses propres positions, sa performance et son risque.
            CREATE TABLE IF NOT EXISTS portefeuilles (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                username  TEXT NOT NULL REFERENCES utilisateurs(username),
                nom       TEXT NOT NULL,
                cree_le   TEXT NOT NULL,
                UNIQUE (username, nom)
            );
            -- Libellés d'opérations que l'utilisateur a classés lui-même lors d'un import.
            -- Chaque banque a son vocabulaire : plutôt que de deviner, l'application
            -- demande une fois puis retient. Le libellé est stocké normalisé.
            CREATE TABLE IF NOT EXISTS mappages_import (
                username  TEXT NOT NULL,
                libelle   TEXT NOT NULL,
                nature    TEXT NOT NULL,
                cree_le   TEXT NOT NULL,
                PRIMARY KEY (username, libelle)
            );
            """)
            # Migration des bases créées avec les versions précédentes
            cols = [r["name"] for r in c.execute("PRAGMA table_info(operations)")]
            if "origine" not in cols:
                c.execute("ALTER TABLE operations ADD COLUMN origine TEXT DEFAULT 'manuel'")
            if "portefeuille_id" not in cols:
                c.execute("ALTER TABLE operations ADD COLUMN portefeuille_id INTEGER")
            if "portefeuille_id" not in [r["name"] for r in
                                         c.execute("PRAGMA table_info(dividendes_traites)")]:
                c.execute("ALTER TABLE dividendes_traites ADD COLUMN portefeuille_id INTEGER")
            colonnes_par = [r["name"] for r in c.execute("PRAGMA table_info(parametres)")]
            if "mode_indice" not in colonnes_par:
                c.execute("ALTER TABLE parametres ADD COLUMN mode_indice TEXT "
                          "DEFAULT 'euros_dividendes'")
            if "taux_sans_risque" not in colonnes_par:
                c.execute("ALTER TABLE parametres ADD COLUMN taux_sans_risque REAL DEFAULT 2.0")

            # Les opérations saisies avant l'arrivée des portefeuilles multiples sont
            # rattachées à un portefeuille par défaut, créé pour l'occasion. Personne ne
            # perd de données en passant à cette version.
            orphelins = [r["username"] for r in c.execute(
                "SELECT DISTINCT username FROM operations WHERE portefeuille_id IS NULL")]
            for username in orphelins:
                c.execute("INSERT OR IGNORE INTO portefeuilles (username, nom, cree_le) "
                          "VALUES (?,?,?)",
                          (username, "Portefeuille principal", datetime.now().isoformat(timespec="seconds")))
                pid = c.execute("SELECT id FROM portefeuilles WHERE username=? ORDER BY id",
                                (username,)).fetchone()["id"]
                c.execute("UPDATE operations SET portefeuille_id=? "
                          "WHERE username=? AND portefeuille_id IS NULL", (pid, username))
                c.execute("UPDATE dividendes_traites SET portefeuille_id=? "
                          "WHERE username=? AND portefeuille_id IS NULL", (pid, username))
            c.execute("CREATE INDEX IF NOT EXISTS idx_ops_pf ON operations(portefeuille_id, date)")

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

    def utilisateur_local(self) -> str:
        """Le compte unique de cette installation, créé au besoin.

        PersoFolio tourne sur le poste de son utilisateur, pour lui seul : il n'y a
        personne dont il faudrait se protéger, et un mot de passe n'ajouterait qu'un
        obstacle. On retient donc le compte déjà présent — celui qui porte le plus
        d'opérations s'il y en a plusieurs, héritage d'une version antérieure — et on
        en crée un par défaut sur une installation neuve.
        """
        with self._connect() as c:
            lignes = c.execute(
                "SELECT u.username,"
                "  (SELECT COUNT(*) FROM operations o WHERE o.username = u.username) AS n"
                " FROM utilisateurs u ORDER BY n DESC, u.cree_le ASC").fetchall()
        if lignes:
            return lignes[0]["username"]
        nom = "moi"
        self.creer_utilisateur(nom, secrets.token_urlsafe(32))
        return nom

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

    def ajouter_operation(self, username: str, portefeuille: int, op: dict) -> int:
        with self._connect() as c:
            cur = c.execute("""
                INSERT INTO operations (portefeuille_id, username, date, type, ticker, quantite, prix, devise,
                                        taux_eur, frais_eur, montant_eur, commentaire, saisi_le,
                                        origine)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (portefeuille, username, op["date"], op["type"], op.get("ticker"), op.get("quantite"),
                 op.get("prix"), op.get("devise"), op.get("taux_eur"),
                 op.get("frais_eur") or 0.0, op.get("montant_eur"), op.get("commentaire"),
                 datetime.now().isoformat(), op.get("origine") or "manuel"))
            return cur.lastrowid

    def operations(self, username: str, portefeuille: int) -> List[dict]:
        with self._connect() as c:
            rows = c.execute("SELECT * FROM operations WHERE username=? AND portefeuille_id=? "
                             "ORDER BY date, id", (username, portefeuille)).fetchall()
        return [dict(r) for r in rows]

    def supprimer_operation(self, username: str, portefeuille: int, op_id: int) -> bool:
        with self._connect() as c:
            cur = c.execute("DELETE FROM operations WHERE id=? AND username=? AND portefeuille_id=?",
                            (op_id, username, portefeuille))
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

    # ── Paramètres du compte ─────────────────────────────────────────────────

    def parametres(self, username: str) -> dict:
        with self._connect() as c:
            row = c.execute("SELECT * FROM parametres WHERE username=?", (username,)).fetchone()
        defauts = {"username": username, "prelevement_div_pct": 0.0,
                   "indice": "MSCI_WORLD", "mode_indice": "euros_dividendes",
                   "taux_sans_risque": 2.0}
        return {**defauts, **{k: v for k, v in dict(row).items() if v is not None}} if row else defauts

    def enregistrer_parametres(self, username: str, prelevement_div_pct: float, indice: str,
                               mode_indice: str = "euros_dividendes",
                               taux_sans_risque: float = 2.0):
        with self._connect() as c:
            c.execute("INSERT OR REPLACE INTO parametres "
                      "(username, prelevement_div_pct, indice, mode_indice, taux_sans_risque) "
                      "VALUES (?,?,?,?,?)",
                      (username, prelevement_div_pct, indice, mode_indice, taux_sans_risque))

    # ── Dividendes automatiques ──────────────────────────────────────────────

    def dividende_deja_traite(self, username: str, portefeuille: int, ticker: str,
                              date_detach: str) -> bool:
        with self._connect() as c:
            return c.execute("SELECT 1 FROM dividendes_traites WHERE username=? AND "
                             "portefeuille_id=? AND ticker=? AND date_detach=?",
                             (username, portefeuille, ticker, date_detach)).fetchone() is not None

    def marquer_dividende(self, username: str, portefeuille: int, ticker: str, date_detach: str):
        with self._connect() as c:
            c.execute("INSERT OR IGNORE INTO dividendes_traites "
                      "(username, ticker, date_detach, portefeuille_id) VALUES (?,?,?,?)",
                      (username, ticker, date_detach, portefeuille))

    # ── Notifications ────────────────────────────────────────────────────────

    def notifier(self, username: str, titre: str, message: str):
        with self._connect() as c:
            c.execute("INSERT INTO notifications (username, cree_le, titre, message) VALUES (?,?,?,?)",
                      (username, datetime.now().isoformat(timespec="seconds"), titre, message))

    def notifications(self, username: str, limite: int = 30) -> List[dict]:
        with self._connect() as c:
            rows = c.execute("SELECT * FROM notifications WHERE username=? ORDER BY id DESC LIMIT ?",
                             (username, limite)).fetchall()
        return [dict(r) for r in rows]

    def marquer_notifications_lues(self, username: str):
        with self._connect() as c:
            c.execute("UPDATE notifications SET lue=1 WHERE username=?", (username,))

    def remplacer_operation(self, username: str, portefeuille: int, op_id: int, op: dict):
        with self._connect() as c:
            c.execute("""UPDATE operations SET date=?, type=?, ticker=?, quantite=?, prix=?,
                         devise=?, taux_eur=?, frais_eur=?, montant_eur=?, commentaire=?
                         WHERE id=? AND username=? AND portefeuille_id=?""",
                      (op["date"], op["type"], op.get("ticker"), op.get("quantite"), op.get("prix"),
                       op.get("devise"), op.get("taux_eur"), op.get("frais_eur") or 0.0,
                       op.get("montant_eur"), op.get("commentaire"), op_id, username, portefeuille))

    def effacer_notifications(self, username: str):
        with self._connect() as c:
            c.execute("DELETE FROM notifications WHERE username=?", (username,))

    # ── Mémoire des libellés d'import ────────────────────────────────────────

    def mappages_import(self, username: str) -> dict:
        """Libellés d'opérations déjà classés par l'utilisateur : {libellé: nature}."""
        with self._connect() as c:
            return {r["libelle"]: r["nature"] for r in
                    c.execute("SELECT libelle, nature FROM mappages_import WHERE username=?",
                              (username,))}

    def enregistrer_mappages(self, username: str, mappages: dict):
        """Retient le classement choisi pour des libellés jusque-là inconnus."""
        if not mappages:
            return
        with self._connect() as c:
            c.executemany(
                "INSERT OR REPLACE INTO mappages_import (username, libelle, nature, cree_le) "
                "VALUES (?,?,?,?)",
                [(username, lib, nat, datetime.now().isoformat(timespec="seconds"))
                 for lib, nat in mappages.items() if lib and nat])

    # ── Portefeuilles ────────────────────────────────────────────────────────

    def portefeuilles(self, username: str) -> List[dict]:
        """Portefeuilles de l'utilisateur, avec le nombre d'opérations de chacun."""
        with self._connect() as c:
            rows = c.execute("""
                SELECT p.id, p.nom, p.cree_le, COUNT(o.id) AS n_operations,
                       MIN(o.date) AS premiere_operation
                FROM portefeuilles p
                LEFT JOIN operations o ON o.portefeuille_id = p.id
                WHERE p.username = ?
                GROUP BY p.id ORDER BY p.id""", (username,)).fetchall()
        return [dict(r) for r in rows]

    def creer_portefeuille(self, username: str, nom: str) -> Optional[int]:
        """Crée un portefeuille. None si le nom est déjà pris pour cet utilisateur."""
        with self._connect() as c:
            try:
                cur = c.execute("INSERT INTO portefeuilles (username, nom, cree_le) VALUES (?,?,?)",
                                (username, nom.strip(),
                                 datetime.now().isoformat(timespec="seconds")))
                return cur.lastrowid
            except sqlite3.IntegrityError:
                return None

    def renommer_portefeuille(self, username: str, portefeuille: int, nom: str) -> bool:
        with self._connect() as c:
            try:
                cur = c.execute("UPDATE portefeuilles SET nom=? WHERE id=? AND username=?",
                                (nom.strip(), portefeuille, username))
                return cur.rowcount > 0
            except sqlite3.IntegrityError:
                return False

    def supprimer_portefeuille(self, username: str, portefeuille: int) -> bool:
        """Supprime un portefeuille et tout ce qu'il contient. Irréversible."""
        with self._connect() as c:
            c.execute("DELETE FROM operations WHERE username=? AND portefeuille_id=?",
                      (username, portefeuille))
            c.execute("DELETE FROM dividendes_traites WHERE username=? AND portefeuille_id=?",
                      (username, portefeuille))
            cur = c.execute("DELETE FROM portefeuilles WHERE id=? AND username=?",
                            (portefeuille, username))
            return cur.rowcount > 0

    def portefeuille_par_defaut(self, username: str) -> int:
        """Premier portefeuille de l'utilisateur, créé au besoin."""
        with self._connect() as c:
            row = c.execute("SELECT id FROM portefeuilles WHERE username=? ORDER BY id LIMIT 1",
                            (username,)).fetchone()
            if row:
                return row["id"]
        return self.creer_portefeuille(username, "Portefeuille principal")

    def portefeuille_valide(self, username: str, portefeuille: int) -> bool:
        with self._connect() as c:
            return c.execute("SELECT 1 FROM portefeuilles WHERE id=? AND username=?",
                             (portefeuille, username)).fetchone() is not None
