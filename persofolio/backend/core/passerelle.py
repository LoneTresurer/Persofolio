"""
core/passerelle.py
==================
Le point de jonction entre le module Budget et comptes et le compte-titres.

Jusqu'ici les deux moitiés de l'application s'ignoraient : le « compte
investissement » du budget était un compte sans destination, et le compte-titres
recevait de l'argent venu de nulle part. Ce module les relie, et il n'a qu'une
chose difficile à faire.

**Un virement est un seul fait, donc une seule écriture.** Virer 300 € vers
l'investissement, c'est à la fois une sortie du compte courant (dans l'état du
budget) et un versement sur le compte-titres (dans sa table d'opérations). Si
l'une des deux écritures passait sans l'autre, 300 € apparaîtraient ou
disparaîtraient — et personne ne le verrait avant longtemps.

Les deux tables vivent dans le même fichier SQLite : `banque_etats` est écrite
par `core/banque_db.py`, `operations` par `core/portfolio_db.py`, et toutes deux
pointent sur `data/portefeuille.db`. Une seule transaction peut donc les écrire
ensemble. C'est ce que fait `virer()` : les deux lignes sont enregistrées, ou
aucune. Il n'y a pas de fenêtre pendant laquelle les deux moitiés divergent, pas
de file d'attente à rejouer, et rien à réconcilier après coup.

Deux précisions, parce qu'elles reviennent souvent :

*Aucune connexion internet n'est nécessaire.* Le compte-titres est servi par le
même serveur local que le budget. Seuls les **cours** demandent le réseau, et
aucun chiffre de ce module n'en dépend : les espèces et le prix de revient se
déduisent des seules opérations enregistrées.

*Le même virement envoyé deux fois ne compte qu'une fois.* Chaque virement porte
un identifiant, conservé dans le commentaire de l'opération. Un double-clic, un
rechargement au mauvais moment ou une reprise après erreur retrouvent le
versement déjà écrit et n'en créent pas un second.

Ce module lit et écrit la table `operations`, qui appartient au compte-titres.
C'est le seul endroit où nous le faisons, et `_verifier_schema()` échoue
bruyamment si sa structure change — mieux vaut une erreur au démarrage qu'un
virement silencieusement perdu.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import List, Optional

from core.portfolio_db import DB_PATH

#: Marque portée par les opérations créées depuis le budget. Elle les distingue
#: de celles saisies dans le compte-titres, et sert au rapprochement.
ORIGINE = "budget"

#: Colonnes de `operations` dont nous dépendons.
COLONNES_ATTENDUES = {"portefeuille_id", "username", "date", "type",
                      "montant_eur", "commentaire", "saisi_le", "origine"}


class PasserelleIndisponible(RuntimeError):
    """La table du compte-titres n'a pas la forme attendue : on refuse d'écrire."""


class ConflitVersion(RuntimeError):
    def __init__(self, version_actuelle: int):
        super().__init__(f"version {version_actuelle}")
        self.version_actuelle = version_actuelle


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _verifier_schema(c: sqlite3.Connection) -> None:
    if not list(c.execute("PRAGMA table_info(banque_etats)")):
        # BanqueDB crée cette table à sa construction ; routes_banque l'instancie
        # au chargement. Si elle manque, le module Budget n'est pas en place.
        raise PasserelleIndisponible(
            "Le module Budget et comptes n'est pas initialisé "
            "(table banque_etats absente).")
    cols = {r["name"] for r in c.execute("PRAGMA table_info(operations)")}
    if not cols:
        raise PasserelleIndisponible(
            "La table des opérations du compte-titres n'existe pas encore. "
            "Ouvrez le compte-titres une première fois.")
    manquantes = COLONNES_ATTENDUES - cols
    if manquantes:
        raise PasserelleIndisponible(
            "La table des opérations du compte-titres a changé de structure : "
            f"colonnes manquantes {sorted(manquantes)}. "
            "Mettre à jour core/passerelle.py avant d'écrire quoi que ce soit.")


def portefeuille_unique(c: sqlite3.Connection, username: str) -> Optional[int]:
    """Le portefeuille qui reçoit les virements.

    L'application en gère plusieurs, mais n'en propose qu'un seul par défaut. Tant
    qu'il n'y en a qu'un, le choix va de soi et on ne demande rien. S'il y en a
    plusieurs, on ne devine pas : l'appelant devra trancher.
    """
    rows = c.execute("SELECT id FROM portefeuilles WHERE username=? ORDER BY id",
                     (username,)).fetchall()
    if len(rows) == 1:
        return rows[0]["id"]
    return None


def _depot_existant(c: sqlite3.Connection, username: str, reference: str) -> Optional[int]:
    row = c.execute(
        "SELECT id FROM operations WHERE username=? AND type='DEPOT' AND commentaire=?",
        (username, reference)).fetchone()
    return row["id"] if row else None


def reference(virement_id: str) -> str:
    """Texte inscrit dans le commentaire de l'opération, qui rend le virement unique."""
    return f"virement budget {virement_id}"


# ── Écriture ────────────────────────────────────────────────────────────────
def virer(username: str, etat: dict, version_attendue: int,
          virement_id: str, date: str, montant_eur: float,
          portefeuille: Optional[int] = None, sens: str = "DEPOT") -> dict:
    """Enregistre, en une seule transaction, l'état du budget et le mouvement d'espèces.

    `sens` vaut DEPOT (le budget alimente le compte-titres) ou RETRAIT
    (l'utilisateur rapatrie des espèces sur son compte courant).

    Renvoie la nouvelle version de l'état du budget, l'identifiant de l'opération
    créée, et `deja_enregistre` si ce virement avait déjà été écrit.
    """
    if sens not in ("DEPOT", "RETRAIT"):
        raise ValueError(f"sens inattendu : {sens}")
    if not (montant_eur > 0):
        raise ValueError("le montant d'un virement doit être strictement positif")

    texte = json.dumps(etat, ensure_ascii=False, separators=(",", ":"))
    maintenant = datetime.now().isoformat(timespec="seconds")
    ref = reference(virement_id)

    conn = _connect()
    try:
        _verifier_schema(conn)
        # Verrou d'écriture pris d'emblée : la lecture de la version et les deux
        # écritures doivent être indivisibles.
        conn.execute("BEGIN IMMEDIATE")

        row = conn.execute("SELECT version FROM banque_etats WHERE username=?",
                           (username,)).fetchone()
        actuelle = row["version"] if row else 0
        if actuelle != version_attendue:
            conn.rollback()
            raise ConflitVersion(actuelle)

        pf = portefeuille
        if pf is None:
            pf = portefeuille_unique(conn, username)
        if pf is None:
            conn.rollback()
            raise PasserelleIndisponible(
                "Plusieurs portefeuilles existent : précisez lequel reçoit les virements.")

        # Idempotence : ce virement a-t-il déjà donné lieu à une opération ?
        deja = _depot_existant(conn, username, ref)
        if deja is None:
            cur = conn.execute(
                "INSERT INTO operations (portefeuille_id, username, date, type, montant_eur,"
                " commentaire, saisi_le, origine) VALUES (?,?,?,?,?,?,?,?)",
                (pf, username, date, sens, round(float(montant_eur), 2),
                 ref, maintenant, ORIGINE))
            operation_id = cur.lastrowid
        else:
            operation_id = deja

        nouvelle = actuelle + 1
        if row:
            conn.execute("UPDATE banque_etats SET etat=?, version=?, maj_le=? WHERE username=?",
                         (texte, nouvelle, maintenant, username))
        else:
            # Colonnes nommées : banque_db insère par position, et une colonne
            # ajoutée là-bas ferait ici une erreur de contrainte plutôt qu'un
            # décalage silencieux.
            conn.execute("INSERT INTO banque_etats (username, etat, version, maj_le, cree_le) "
                         "VALUES (?,?,?,?,?)",
                         (username, texte, nouvelle, maintenant, maintenant))
        conn.commit()
        return {"version": nouvelle, "operation_id": operation_id,
                "portefeuille": pf, "deja_enregistre": deja is not None}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ── Lecture ─────────────────────────────────────────────────────────────────
def etat_investissement(username: str, portefeuille: Optional[int] = None) -> dict:
    """Ce que le budget doit savoir du compte-titres, sans toucher au marché.

    Trois chiffres, tous déduits des seules opérations enregistrées, donc exacts
    et disponibles hors ligne :

    - `cash_eur` : les espèces versées et pas encore investies ;
    - `titres_cout_eur` : ce que les titres détenus ont coûté, frais compris ;
    - `verses_depuis_budget_eur` : ce que le budget a effectivement envoyé.

    `apports_hors_budget_eur` est la différence entre tout l'argent entré dans le
    portefeuille et ce que le budget a envoyé. Ce n'est pas une anomalie : un
    versement fait directement chez le courtier est parfaitement légitime. Mais
    c'est de l'argent que le budget n'a jamais vu passer, et le taux d'épargne
    investie ne doit donc pas le compter.
    """
    from core.portfolio_calc import rejouer

    conn = _connect()
    try:
        _verifier_schema(conn)
        pf = portefeuille if portefeuille is not None else portefeuille_unique(conn, username)
        if pf is None:
            rows = conn.execute(
                "SELECT * FROM operations WHERE username=? ORDER BY date, id",
                (username,)).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM operations WHERE username=? AND portefeuille_id=? ORDER BY date, id",
                (username, pf)).fetchall()
        operations: List[dict] = [dict(r) for r in rows]
    finally:
        conn.close()

    etat = rejouer(operations)
    titres_cout = round(sum(p.cout_eur for p in etat.positions.values()
                            if p.quantite > 1e-9), 2)
    depuis_budget = round(sum(
        float(o.get("montant_eur") or 0) * (1 if o["type"] == "DEPOT" else -1)
        for o in operations
        if o["type"] in ("DEPOT", "RETRAIT") and (o.get("origine") or "") == ORIGINE), 2)
    entre_en_tout = round(etat.depots_nets_eur + etat.apports_implicites_eur, 2)

    return {
        "portefeuille": pf,
        "cash_eur": round(etat.cash_eur, 2),
        "titres_cout_eur": titres_cout,
        "verses_depuis_budget_eur": depuis_budget,
        "entre_en_tout_eur": entre_en_tout,
        "apports_hors_budget_eur": round(entre_en_tout - depuis_budget, 2),
        "nb_lignes": sum(1 for p in etat.positions.values() if p.quantite > 1e-9),
    }
