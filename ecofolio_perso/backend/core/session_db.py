"""
core/session_db.py
==================
Base de données SQLite pour la persistance des sessions et feedbacks.

Tables :
  sessions  — Résumé de chaque optimisation (pour comparaison future)
  feedbacks — Données réelles saisies par l'investisseur + diagnostic
  flags     — Flags de validation générés lors du feedback

Usage :
  db = SessionDB()
  session_id = db.save_session(portfolio, meta, A)
  db.save_feedback(session_id, feedback_data, diagnostic)
  history = db.get_history(session_id)
"""

import sqlite3
import json
import uuid
from datetime import datetime
from typing import Optional, List, Dict
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "ecofolio_sessions.db"


class SessionDB:

    def __init__(self, db_path: str = None):
        self.db_path = str(db_path or DB_PATH)
        # Le dossier data/ n'existe pas sur une installation neuve — il ne contient
        # que des bases, et une archive partagée ne les emporte pas. On le crée
        # plutôt que de laisser sqlite échouer au démarrage sur « unable to open
        # database file », message qui n'indique en rien ce qu'il faut faire.
        import os
        os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        """Crée les tables si elles n'existent pas."""
        with self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id      TEXT PRIMARY KEY,
                    created_at      TEXT NOT NULL,
                    pays            TEXT,
                    horizon         INTEGER,
                    montant         REAL,
                    esg_seuil       REAL,
                    A               REAL,
                    profil_label    TEXT,
                    rendement_predit REAL,
                    volatilite      REAL,
                    sharpe          REAL,
                    drawdown        REAL,
                    esg_moyen       REAL,
                    beta_porte      REAL,
                    div_yield       REAL,
                    n_titres        INTEGER,
                    composition_json TEXT,
                    predictions_json TEXT
                );

                CREATE TABLE IF NOT EXISTS feedbacks (
                    feedback_id     TEXT PRIMARY KEY,
                    session_id      TEXT NOT NULL,
                    submitted_at    TEXT NOT NULL,
                    montant_initial REAL,
                    montant_final   REAL,
                    dividendes_percus REAL,
                    frais_courtage  REAL,
                    duree_mois      INTEGER,
                    rendement_reel_pct REAL,
                    rendement_annualise_pct REAL,
                    source_donnees  TEXT,
                    pdf_fourni      INTEGER DEFAULT 0,
                    validation_ok   INTEGER DEFAULT 1,
                    flags_json      TEXT,
                    diagnostic_json TEXT,
                    benchmark_json  TEXT,
                    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
                );

                CREATE INDEX IF NOT EXISTS idx_fb_session
                    ON feedbacks(session_id);
                CREATE INDEX IF NOT EXISTS idx_sess_created
                    ON sessions(created_at);
            """)

    # ── Sessions ──────────────────────────────────────────────────────────────

    def save_session(
        self,
        portfolio: dict,
        meta: dict,
        A: float,
        profil_label: str,
    ) -> str:
        """Sauvegarde un résumé de l'optimisation. Retourne le session_id."""
        session_id = str(uuid.uuid4())
        compo = portfolio.get("composition", [])

        with self._connect() as conn:
            conn.execute("""
                INSERT INTO sessions VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
            """, (
                session_id,
                datetime.now().isoformat(),
                meta.get("pays"),
                meta.get("horizon"),
                meta.get("montant"),
                meta.get("esg_seuil"),
                round(A, 3),
                profil_label,
                portfolio.get("rendement"),
                portfolio.get("volatilite"),
                portfolio.get("sharpe"),
                portfolio.get("drawdown"),
                portfolio.get("esg_moyen"),
                portfolio.get("beta_portefeuille"),
                portfolio.get("div_yield"),
                portfolio.get("n_titres_actifs"),
                json.dumps([
                    {"ticker": s.get("ticker"), "poids_pct": s.get("poids_pct"),
                     "secteur": s.get("secteur")}
                    for s in compo[:30]
                ], ensure_ascii=False),
                json.dumps({}),  # predictions stockées à part si nécessaire
            ))
        return session_id

    def get_session(self, session_id: str) -> Optional[dict]:
        """Charge une session par son ID."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["composition"] = json.loads(d.pop("composition_json", "[]"))
        d["predictions"]  = json.loads(d.pop("predictions_json", "{}"))
        return d

    def list_sessions(self, limit: int = 50) -> List[dict]:
        """Liste les sessions les plus récentes."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT session_id, created_at, pays, horizon, montant, "
                "rendement_predit, profil_label FROM sessions "
                "ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    # ── Feedbacks ─────────────────────────────────────────────────────────────

    def save_feedback(
        self,
        session_id:         str,
        montant_initial:    float,
        montant_final:      float,
        dividendes:         float,
        frais:              float,
        duree_mois:         int,
        rendement_reel:     float,
        rendement_ann:      float,
        source:             str,
        pdf_fourni:         bool,
        validation_ok:      bool,
        flags:              list,
        diagnostic:         dict,
        benchmark:          dict,
    ) -> str:
        """Sauvegarde un feedback. Retourne le feedback_id."""
        feedback_id = str(uuid.uuid4())
        with self._connect() as conn:
            conn.execute("""
                INSERT INTO feedbacks VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
            """, (
                feedback_id,
                session_id,
                datetime.now().isoformat(),
                montant_initial,
                montant_final,
                dividendes,
                frais,
                duree_mois,
                rendement_reel,
                rendement_ann,
                source,
                int(pdf_fourni),
                int(validation_ok),
                json.dumps(flags, ensure_ascii=False),
                json.dumps(diagnostic, ensure_ascii=False),
                json.dumps(benchmark, ensure_ascii=False),
            ))
        return feedback_id

    def get_feedbacks_for_session(self, session_id: str) -> List[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM feedbacks WHERE session_id = ? "
                "ORDER BY submitted_at DESC", (session_id,)
            ).fetchall()
        results = []
        for row in rows:
            d = dict(row)
            d["flags"]      = json.loads(d.get("flags_json") or "[]")
            d["diagnostic"] = json.loads(d.get("diagnostic_json") or "{}")
            d["benchmark"]  = json.loads(d.get("benchmark_json") or "{}")
            results.append(d)
        return results

    # ── Statistiques agrégées (anonymisées) ──────────────────────────────────

    def calibration_bias(self) -> dict:
        """
        Biais de calibration du modèle : écart moyen ANNUALISÉ entre le rendement
        réel observé (suivis) et le rendement prédit lors de l'optimisation.

        biais_annuel_pct > 0  → le modèle SOUS-estime (réel > prévu) : les futures
                                projections devraient être révisées à la hausse.
        biais_annuel_pct < 0  → le modèle SUR-estime  (réel < prévu) : à la baisse.

        C'est le mécanisme d'auto-correction : chaque suivi enregistré affine ce biais.
        """
        with self._connect() as conn:
            r = conn.execute("""
                SELECT
                    COUNT(*) AS n,
                    AVG(f.rendement_annualise_pct - s.rendement_predit) AS biais_ann,
                    AVG(ABS(f.rendement_annualise_pct - s.rendement_predit)) AS erreur_abs
                FROM feedbacks f
                JOIN sessions s ON f.session_id = s.session_id
                WHERE f.validation_ok = 1
                  AND f.rendement_annualise_pct IS NOT NULL
                  AND s.rendement_predit IS NOT NULL
            """).fetchone()
        n = (r["n"] if r else 0) or 0
        return {
            "n_observations":     int(n),
            "biais_annuel_pct":   round(r["biais_ann"], 3) if (r and r["biais_ann"] is not None) else 0.0,
            "erreur_abs_ann_pct": round(r["erreur_abs"], 3) if (r and r["erreur_abs"] is not None) else 0.0,
        }

    def stats_globales(self) -> dict:
        """
        Statistiques globales anonymisées — utilisées pour la recalibration
        des modèles (distribution des écarts prédit-réel).
        """
        with self._connect() as conn:
            r = conn.execute("""
                SELECT
                    COUNT(*) as n_feedbacks,
                    AVG(rendement_reel_pct - s.rendement_predit) as ecart_moyen,
                    AVG(ABS(rendement_reel_pct - s.rendement_predit)) as ecart_abs_moyen,
                    AVG(duree_mois) as duree_moyenne
                FROM feedbacks f
                JOIN sessions s ON f.session_id = s.session_id
                WHERE f.validation_ok = 1
            """).fetchone()
        return dict(r) if r else {}
