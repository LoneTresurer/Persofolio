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
from datetime import date as _date, datetime, timedelta
from typing import List, Optional

from core.portfolio_db import DB_PATH

#: Marque portée par les versements venus du compte courant suivi dans le budget.
#: Eux seuls comptent dans la part de l'épargne investie.
ORIGINE = "budget"

#: Versements que l'utilisateur a déclarés comme venant d'ailleurs — héritage,
#: autre banque, salaire versé sur un compte non suivi. Ils entrent bien dans le
#: portefeuille, mais n'ont jamais fait partie de l'épargne que le budget observe :
#: les compter gonflerait le taux d'épargne investie au-delà de 100 %.
ORIGINE_EXTERNE = "externe"

#: Colonnes de `operations` dont nous dépendons.
COLONNES_ATTENDUES = {"portefeuille_id", "username", "date", "type",
                      "montant_eur", "commentaire", "saisi_le", "origine"}


class PasserelleIndisponible(RuntimeError):
    """La table du compte-titres n'a pas la forme attendue : on refuse d'écrire."""


class ExplicationPerimee(RuntimeError):
    """Ce que le navigateur propose d'expliquer ne correspond plus à la base.

    Un deuxième clic, un autre onglet, une opération saisie entre-temps : l'écran
    montrait un montant qui n'est plus le bon. On n'écrit rien, on renvoie ce qui
    reste réellement à expliquer, et le navigateur se met à jour.
    """
    def __init__(self, restant: list):
        super().__init__("explication périmée")
        self.restant = restant


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
        "SELECT id FROM operations WHERE username=? AND type IN ('DEPOT','RETRAIT')"
        " AND commentaire=?",
        (username, reference)).fetchone()
    return row["id"] if row else None


PREFIXE = "virement budget "


def reference(virement_id: str) -> str:
    """Texte inscrit dans le commentaire de l'opération, qui rend le virement unique."""
    return f"{PREFIXE}{virement_id}"


def _ecrire_etat(c: sqlite3.Connection, username: str, etat: dict, existe: bool,
                 actuelle: int, maintenant: str) -> int:
    """Écrit l'état du budget dans la transaction en cours ; renvoie la nouvelle version."""
    texte = json.dumps(etat, ensure_ascii=False, separators=(",", ":"))
    nouvelle = actuelle + 1
    if existe:
        c.execute("UPDATE banque_etats SET etat=?, version=?, maj_le=? WHERE username=?",
                  (texte, nouvelle, maintenant, username))
    else:
        # Colonnes nommées : banque_db insère par position, et une colonne
        # ajoutée là-bas ferait ici une erreur de contrainte plutôt qu'un
        # décalage silencieux.
        c.execute("INSERT INTO banque_etats (username, etat, version, maj_le, cree_le) "
                  "VALUES (?,?,?,?,?)",
                  (username, texte, nouvelle, maintenant, maintenant))
    return nouvelle


def _ouvrir_transaction(username: str, version_attendue: int):
    """Connexion, verrou d'écriture, contrôle de version. Renvoie (conn, existe, actuelle)."""
    conn = _connect()
    try:
        _verifier_schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT version FROM banque_etats WHERE username=?",
                           (username,)).fetchone()
        actuelle = row["version"] if row else 0
        if actuelle != version_attendue:
            conn.rollback()
            raise ConflitVersion(actuelle)
        return conn, row is not None, actuelle
    except Exception:
        conn.close()
        raise


def _retirer_mouvement(c: sqlite3.Connection, username: str, ref: str) -> int:
    """Supprime le mouvement d'espèces portant cette référence. Jamais un ordre :
    seuls les versements et retraits écrits par cette passerelle sont visés."""
    cur = c.execute("DELETE FROM operations WHERE username=? AND type IN ('DEPOT','RETRAIT')"
                    " AND commentaire=?", (username, ref))
    return cur.rowcount


def _operations(c: sqlite3.Connection, username: str, pf: Optional[int]) -> List[dict]:
    if pf is None:
        rows = c.execute("SELECT * FROM operations WHERE username=? ORDER BY date, id",
                         (username,)).fetchall()
    else:
        rows = c.execute("SELECT * FROM operations WHERE username=? AND portefeuille_id=?"
                         " ORDER BY date, id", (username, pf)).fetchall()
    return [dict(r) for r in rows]


def achats_a_decouvert(operations: List[dict]) -> List[dict]:
    """Les achats que les espèces connues ne couvraient pas, et de combien.

    C'est le détail de `a_expliquer_eur`. Un montant global ne suffit pas pour
    l'expliquer : un versement daté d'aujourd'hui arrive *après* des achats
    passés, n'en couvre aucun, et s'ajoute simplement aux espèces. C'était le
    défaut d'origine — chaque réponse à la question gonflait le patrimoine du
    même montant, et la question restait posée.
    """
    from core.portfolio_calc import rejouer
    trous = []
    for op in rejouer(operations).operations:
        m = op.get("apport_implicite_eur")
        if op.get("type") == "ACHAT" and m and m > 0.005:
            trous.append({"achat_id": op["id"], "date": op["date"],
                          "ticker": op.get("ticker"), "montant_eur": round(m, 2)})
    return trous


def _veille(jour: str) -> str:
    return (_date.fromisoformat(jour) - timedelta(days=1)).isoformat()


def identifiant_apport(achat_id: int) -> str:
    """Un achat ne s'explique qu'une fois : l'identifiant découle de l'achat."""
    return f"apport-{achat_id}"


# ── Déclarer l'origine des achats à découvert ───────────────────────────────
def declarer_apports(username: str, etat: dict, version_attendue: int,
                     origine: str, attendus: List[dict],
                     portefeuille: Optional[int] = None) -> dict:
    """Enregistre le versement qui manquait devant chaque achat à découvert.

    Répondre « de mon compte courant » ou « d'ailleurs » ne doit **jamais**
    changer le patrimoine : l'argent est déjà compté, dans les titres qu'il a
    achetés. La réponse ne fait que nommer son origine. Pour cela, chaque
    versement est daté **la veille de l'achat** qu'il finance : rejoué dans
    l'ordre, il est consommé par cet achat, l'apport implicite disparaît, et les
    espèces restent ce qu'elles étaient. (La veille et non le jour même : à date
    égale, le rejeu du compte-titres range les opérations par ordre de saisie, et
    un versement saisi après l'achat passerait derrière lui.)

    Le serveur recalcule lui-même ce qui reste à expliquer, sous verrou. Si cela
    ne correspond pas à ce que l'écran montrait — deuxième clic, autre onglet —
    rien n'est écrit (`ExplicationPerimee`). Une fois écrit, les achats sont
    couverts et la question disparaît : un second envoi ne trouve plus rien.

    L'état du budget voyage avec : il porte, pour « de mon compte courant », les
    sorties du compte courant correspondantes. Tout est écrit ensemble ou rien.
    """
    if origine not in (ORIGINE, ORIGINE_EXTERNE):
        raise ValueError(f"origine inattendue : {origine}")
    maintenant = datetime.now().isoformat(timespec="seconds")
    conn, existe, actuelle = _ouvrir_transaction(username, version_attendue)
    try:
        pf = portefeuille if portefeuille is not None else portefeuille_unique(conn, username)
        if pf is None:
            raise PasserelleIndisponible(
                "Plusieurs portefeuilles existent : précisez lequel est concerné.")
        trous = achats_a_decouvert(_operations(conn, username, pf))
        vus = {(int(a["achat_id"]), round(float(a["montant_eur"]), 2)) for a in attendus}
        reels = {(t["achat_id"], t["montant_eur"]) for t in trous}
        if not trous or vus != reels:
            raise ExplicationPerimee(trous)

        crees = []
        for t in trous:
            vid = identifiant_apport(t["achat_id"])
            if _depot_existant(conn, username, reference(vid)) is not None:
                continue
            jour = _veille(t["date"])
            conn.execute(
                "INSERT INTO operations (portefeuille_id, username, date, type, montant_eur,"
                " commentaire, saisi_le, origine) VALUES (?,?,?,?,?,?,?,?)",
                (pf, username, jour, "DEPOT", t["montant_eur"], reference(vid),
                 maintenant, origine))
            crees.append({"id": vid, "date": jour, "montant_eur": t["montant_eur"]})

        # Contrôle avant de valider : plus rien à découvert. Si ce n'était pas le
        # cas, le patrimoine aurait bougé — on préfère ne rien écrire.
        restant = achats_a_decouvert(_operations(conn, username, pf))
        if restant:
            raise ExplicationPerimee(restant)

        nouvelle = _ecrire_etat(conn, username, etat, existe, actuelle, maintenant)
        conn.commit()
        return {"version": nouvelle, "apports": crees, "portefeuille": pf}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ── Annuler un virement ─────────────────────────────────────────────────────
def annuler_virement(username: str, etat: dict, version_attendue: int,
                     virement_id: str) -> dict:
    """Retire un virement des deux côtés, en une transaction.

    L'état du budget arrive déjà débarrassé de sa ligne (s'il en avait une) ; le
    mouvement d'espèces du compte-titres est supprimé ici. Supprimer la ligne du
    budget sans le versement laissait l'argent dans le compte-titres : le
    patrimoine montait d'autant.

    Annuler un versement qui expliquait un achat fait simplement réapparaître la
    question : l'achat redevient à découvert, rien n'est perdu.
    """
    maintenant = datetime.now().isoformat(timespec="seconds")
    conn, existe, actuelle = _ouvrir_transaction(username, version_attendue)
    try:
        n = _retirer_mouvement(conn, username, reference(virement_id))
        nouvelle = _ecrire_etat(conn, username, etat, existe, actuelle, maintenant)
        conn.commit()
        return {"version": nouvelle, "supprimes": n}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ── Écriture ────────────────────────────────────────────────────────────────
def virer(username: str, etat: dict, version_attendue: int,
          virement_id: str, date: str, montant_eur: float,
          portefeuille: Optional[int] = None, sens: str = "DEPOT",
          origine: str = ORIGINE, remplace: Optional[str] = None) -> dict:
    """Enregistre, en une seule transaction, l'état du budget et le mouvement d'espèces.

    `sens` vaut DEPOT (le budget alimente le compte-titres) ou RETRAIT
    (l'utilisateur rapatrie des espèces sur son compte courant).

    `remplace` est l'identifiant d'un virement que celui-ci corrige : son
    mouvement d'espèces est retiré dans la même transaction. Sans cela, modifier
    un virement depuis le budget laissait l'ancien versement en place dans le
    compte-titres, et l'argent y était compté deux fois.

    Renvoie la nouvelle version de l'état du budget, l'identifiant de l'opération
    créée, et `deja_enregistre` si ce virement avait déjà été écrit.
    """
    if sens not in ("DEPOT", "RETRAIT"):
        raise ValueError(f"sens inattendu : {sens}")
    if origine not in (ORIGINE, ORIGINE_EXTERNE):
        raise ValueError(f"origine inattendue : {origine}")
    if not (montant_eur > 0):
        raise ValueError("le montant d'un virement doit être strictement positif")

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

        if remplace and remplace != virement_id:
            _retirer_mouvement(conn, username, reference(remplace))

        # Idempotence : ce virement a-t-il déjà donné lieu à une opération ?
        deja = _depot_existant(conn, username, ref)
        if deja is None:
            cur = conn.execute(
                "INSERT INTO operations (portefeuille_id, username, date, type, montant_eur,"
                " commentaire, saisi_le, origine) VALUES (?,?,?,?,?,?,?,?)",
                (pf, username, date, sens, round(float(montant_eur), 2),
                 ref, maintenant, origine))
            operation_id = cur.lastrowid
        else:
            operation_id = deja

        nouvelle = _ecrire_etat(conn, username, etat, row is not None, actuelle, maintenant)
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
        operations = _operations(conn, username, pf)
    finally:
        conn.close()

    etat = rejouer(operations)
    titres_cout = round(sum(p.cout_eur for p in etat.positions.values()
                            if p.quantite > 1e-9), 2)
    net = lambda marque: round(sum(
        float(o.get("montant_eur") or 0) * (1 if o["type"] == "DEPOT" else -1)
        for o in operations
        if o["type"] in ("DEPOT", "RETRAIT") and (o.get("origine") or "") == marque), 2)
    depuis_budget = net(ORIGINE)
    declare_externe = net(ORIGINE_EXTERNE)
    entre_en_tout = round(etat.depots_nets_eur + etat.apports_implicites_eur, 2)

    return {
        "portefeuille": pf,
        "cash_eur": round(etat.cash_eur, 2),
        "titres_cout_eur": titres_cout,
        "verses_depuis_budget_eur": depuis_budget,
        "verses_exterieurs_eur": declare_externe,
        "entre_en_tout_eur": entre_en_tout,
        # Argent entré dans le portefeuille sans que son origine soit connue :
        # un achat passé chez le courtier sans versement enregistré. Ce n'est pas
        # une anomalie, c'est une question à poser. Déclarer le versement le fait
        # tomber à zéro, puisque l'achat n'est alors plus à découvert.
        "a_expliquer_eur": round(etat.apports_implicites_eur, 2),
        "a_expliquer_detail": achats_a_decouvert(operations),
        # Tous les mouvements d'espèces, pour pouvoir les relire et annuler ceux
        # que la passerelle a écrits. `virement_id` est nul pour un versement saisi
        # dans le compte-titres lui-même : celui-là se corrige là-bas.
        "mouvements": [{
            "operation_id": o["id"], "date": o["date"], "type": o["type"],
            "montant_eur": round(float(o.get("montant_eur") or 0), 2),
            "origine": o.get("origine") or None,
            "virement_id": (o.get("commentaire") or "")[len(PREFIXE):]
                           if (o.get("commentaire") or "").startswith(PREFIXE) else None,
            "saisi_le": o.get("saisi_le"),
        } for o in operations if o["type"] in ("DEPOT", "RETRAIT")],
        "nb_lignes": sum(1 for p in etat.positions.values() if p.quantite > 1e-9),
    }
