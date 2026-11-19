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
from typing import Dict, List, Optional

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


def lien_budget(operations: List[dict], op_id: int) -> Optional[str]:
    """Message de refus si l'opération est un mouvement d'espèces écrit par le budget.

    Depuis le 10 novembre, le compte-titres affiche de nouveau versements et
    retraits, avec « Modifier » et « Annuler ». Ceux que le budget a écrits —
    virements, origine déclarée d'un achat — ont une ligne en face dans le compte
    courant. Les supprimer d'un seul côté, c'est le défaut du 28 octobre vu de
    l'autre bord : l'argent quitte le compte-titres et reste sorti du compte
    courant, le patrimoine baisse d'autant. Ils se corrigent donc depuis le
    budget, qui retire les deux lignes ensemble (`annuler_virement`).

    Rend None pour tout le reste, y compris un versement saisi dans le
    compte-titres lui-même : celui-là se corrige librement.
    """
    op = next((o for o in operations if o.get("id") == op_id), None)
    if not op or op.get("type") not in ("DEPOT", "RETRAIT"):
        return None
    if not (op.get("commentaire") or "").startswith(PREFIXE):
        # Saisi ici, mais déclaré « de mon compte courant » depuis le budget : le
        # compte courant porte la ligne d'en face, il faut d'abord retirer la réponse.
        try:
            conn = _connect()
            try:
                rep = _reponses(conn, op.get("username") or "").get(op_id)
            finally:
                conn.close()
        except sqlite3.Error:
            rep = None
        if rep != ORIGINE:
            return None
        quoi = "Ce versement" if op["type"] == "DEPOT" else "Ce retrait"
        return (f"{quoi} est rattaché à votre compte courant dans Budget et comptes. "
                "Retirez d'abord ce rattachement : Budget et comptes → Comptes → "
                "« Espèces échangées avec le compte-titres » → « Annuler la réponse ».")
    quoi = "Ce versement" if op["type"] == "DEPOT" else "Ce retrait"
    return (f"{quoi} vient de Budget et comptes. Pour le corriger ou l'annuler, passez "
            "par Budget et comptes → Comptes → « Espèces échangées avec le "
            "compte-titres » : il y sera retiré des deux côtés à la fois.")


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
    from core.portfolio_calc import rejouer, _cle_tri
    trous = []
    ordre = sorted(operations, key=_cle_tri)
    for op in rejouer(operations).operations:
        m = op.get("apport_implicite_eur")
        if op.get("type") == "ACHAT" and m and m > 0.005:
            trous.append({"achat_id": op["id"], "date": op["date"],
                          "ticker": op.get("ticker"), "montant_eur": round(m, 2)})
    # Le montant affiché est arrondi au centime ; celui qu'on versera ne doit pas
    # l'être. Un versement arrondi laissait chaque achat couvert à un demi-centime
    # près : les restes s'additionnaient en « 0,01 € à expliquer » qu'aucune ligne
    # ne portait — question impossible à faire disparaître —, ou en centimes
    # d'espèces apparus de nulle part. Le calcul du compte-titres ne publie que
    # l'arrondi ; l'exact se lit dans son total non arrondi, avant et après l'achat.
    if trous:
        rang = {o["id"]: k for k, o in enumerate(ordre)}
        for t in trous:
            k = rang[t["achat_id"]]
            t["exact_eur"] = (rejouer(ordre[:k + 1]).apports_implicites_eur
                              - rejouer(ordre[:k]).apports_implicites_eur)
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
                (pf, username, jour, "DEPOT", t.get("exact_eur", t["montant_eur"]), reference(vid),
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


# ── D'où vient ce versement ? Où est allé ce retrait ? ──────────────────────
#
# Un versement saisi ou importé dans le compte-titres — relevé du courtier, saisie
# à la main — n'est pas passé par le budget. Comme un achat à découvert, c'est une
# question à poser : venait-il du compte courant (le budget l'enregistre alors
# comme un virement, et il compte dans l'épargne investie), ou d'ailleurs ?
#
# La réponse ne va pas dans la colonne `origine` de `operations` : le compte-titres
# y note comment l'opération a été saisie (manuel, import_ordres…), et l'écraser
# lui ferait perdre cette information. Elle va dans `banque_origines`, table du
# budget, écrite dans la même transaction que l'état du budget.

def _table_origines(c: sqlite3.Connection) -> None:
    c.execute("""CREATE TABLE IF NOT EXISTS banque_origines (
        username      TEXT NOT NULL,
        operation_id  INTEGER NOT NULL,          -- id dans operations (compte-titres)
        origine       TEXT NOT NULL,             -- 'budget' ou 'externe'
        repondu_le    TEXT NOT NULL,
        PRIMARY KEY (username, operation_id))""")


def _reponses(c: sqlite3.Connection, username: str) -> Dict[int, str]:
    _table_origines(c)
    return {r["operation_id"]: r["origine"] for r in c.execute(
        "SELECT operation_id, origine FROM banque_origines WHERE username=?", (username,))}


def versements_sans_origine(operations: List[dict], reponses: Dict[int, str]) -> List[dict]:
    """Versements et retraits du compte-titres que le budget n'a ni écrits ni vu expliquer."""
    return [{"operation_id": o["id"], "date": o["date"], "type": o["type"],
             "montant_eur": round(float(o.get("montant_eur") or 0), 2),
             "saisie": o.get("origine") or "manuel"}
            for o in operations
            if o["type"] in ("DEPOT", "RETRAIT")
            and not (o.get("commentaire") or "").startswith(PREFIXE)
            and (o.get("origine") or "") not in (ORIGINE, ORIGINE_EXTERNE)
            and o["id"] not in reponses
            and float(o.get("montant_eur") or 0) > 0.005]


def declarer_versements(username: str, etat: dict, version_attendue: int,
                        reponses: List[dict]) -> dict:
    """Enregistre l'origine de versements (ou la destination de retraits).

    `reponses` : [{operation_id, montant_eur, origine}], ce que l'écran montrait.
    Chaque ligne doit encore être sans réponse, au même montant ; sinon rien
    n'est écrit (`ExplicationPerimee`) — un second clic ne trouve plus rien.
    Le compte-titres n'est pas modifié : le versement y est déjà. L'état du budget
    voyage avec et porte, pour « compte courant », la ligne correspondante.
    """
    if not reponses:
        raise ValueError("aucune réponse")
    for r in reponses:
        if r["origine"] not in (ORIGINE, ORIGINE_EXTERNE):
            raise ValueError(f"origine inattendue : {r['origine']}")
    maintenant = datetime.now().isoformat(timespec="seconds")
    conn, existe, actuelle = _ouvrir_transaction(username, version_attendue)
    try:
        ouverts = {v["operation_id"]: v for v in
                   versements_sans_origine(_operations(conn, username, None), _reponses(conn, username))}
        demandes = {int(r["operation_id"]): r for r in reponses}
        if any(i not in ouverts or ouverts[i]["montant_eur"] != round(float(r["montant_eur"]), 2)
               for i, r in demandes.items()):
            raise ExplicationPerimee(list(ouverts.values()))
        conn.executemany(
            "INSERT INTO banque_origines (username, operation_id, origine, repondu_le) VALUES (?,?,?,?)",
            [(username, i, r["origine"], maintenant) for i, r in demandes.items()])
        nouvelle = _ecrire_etat(conn, username, etat, existe, actuelle, maintenant)
        conn.commit()
        return {"version": nouvelle, "enregistres": sorted(demandes)}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def annuler_reponse(username: str, etat: dict, version_attendue: int, operation_id: int) -> dict:
    """Retire une réponse : la question est de nouveau posée. L'état du budget,
    sans la ligne de virement correspondante, voyage avec."""
    maintenant = datetime.now().isoformat(timespec="seconds")
    conn, existe, actuelle = _ouvrir_transaction(username, version_attendue)
    try:
        _table_origines(conn)
        n = conn.execute("DELETE FROM banque_origines WHERE username=? AND operation_id=?",
                         (username, int(operation_id))).rowcount
        nouvelle = _ecrire_etat(conn, username, etat, existe, actuelle, maintenant)
        conn.commit()
        return {"version": nouvelle, "supprimes": n}
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

    **Portefeuille par portefeuille.** Chaque portefeuille est rejoué à part,
    exactement comme l'en-tête du compte-titres le fait pour celui qui est
    affiché, puis les chiffres sont additionnés. Rejouer toutes les opérations
    ensemble donnerait d'autres espèces dès qu'il y a plusieurs portefeuilles :
    un achat de l'un serait payé avec les espèces de l'autre. Avec un seul
    portefeuille, `cash_eur` est donc au centime le « Solde espèces » du
    compte-titres.

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
        if portefeuille is not None:
            ids = [portefeuille]
        else:
            ids = [r["id"] for r in conn.execute(
                "SELECT id FROM portefeuilles WHERE username=? ORDER BY id", (username,)).fetchall()]
        par_pf = [(i, _operations(conn, username, i)) for i in ids] or [(None, _operations(conn, username, None))]
        reponses = _reponses(conn, username)
    finally:
        conn.close()

    rejoues = [(i, ops, rejouer(ops)) for i, ops in par_pf]
    operations = [o for _, ops, _ in rejoues for o in ops]
    pf = rejoues[0][0] if len(rejoues) == 1 else None
    somme = lambda f: sum(f(e) for _, _, e in rejoues)
    cash = somme(lambda e: e.cash_eur)
    apports_implicites = somme(lambda e: e.apports_implicites_eur)
    depots_nets = somme(lambda e: e.depots_nets_eur)
    titres_cout = round(somme(lambda e: sum(p.cout_eur for p in e.positions.values()
                                            if p.quantite > 1e-9)), 2)
    # L'origine d'un mouvement : celle que la passerelle a écrite en le créant, ou
    # la réponse donnée depuis le budget pour un versement saisi dans le compte-titres.
    origine_de = lambda o: reponses.get(o["id"]) or (o.get("origine") or "")
    net = lambda marque: round(sum(
        float(o.get("montant_eur") or 0) * (1 if o["type"] == "DEPOT" else -1)
        for o in operations
        if o["type"] in ("DEPOT", "RETRAIT") and origine_de(o) == marque), 2)
    detail = [t for _, ops, _ in rejoues for t in achats_a_decouvert(ops)]
    for t in detail:
        t.pop("exact_eur", None)
    depuis_budget = net(ORIGINE)
    declare_externe = net(ORIGINE_EXTERNE)
    entre_en_tout = round(depots_nets + apports_implicites, 2)

    return {
        "portefeuille": pf,
        "portefeuilles": [i for i, _, _ in rejoues if i is not None],
        "cash_eur": round(cash, 2),
        "titres_cout_eur": titres_cout,
        "verses_depuis_budget_eur": depuis_budget,
        "verses_exterieurs_eur": declare_externe,
        "entre_en_tout_eur": entre_en_tout,
        # Argent entré dans le portefeuille sans que son origine soit connue :
        # un achat passé chez le courtier sans versement enregistré. Ce n'est pas
        # une anomalie, c'est une question à poser. Déclarer le versement le fait
        # tomber à zéro, puisque l'achat n'est alors plus à découvert.
        # Somme des lignes listées, et non total brut : un total qui contiendrait des
        # restes de moins d'un centime afficherait une question sans réponse possible.
        "a_expliquer_eur": round(sum(t["montant_eur"] for t in detail), 2),
        "a_expliquer_detail": detail,
        # Versements et retraits saisis ou importés dans le compte-titres, dont on
        # ne sait pas encore d'où ils viennent (ou où ils sont allés).
        "versements_a_expliquer": versements_sans_origine(operations, reponses),
        # Tous les mouvements d'espèces, pour pouvoir les relire et annuler ceux
        # que la passerelle a écrits. `virement_id` est nul pour un versement saisi
        # dans le compte-titres lui-même : celui-là se corrige là-bas.
        "mouvements": [{
            "operation_id": o["id"], "date": o["date"], "type": o["type"],
            "montant_eur": round(float(o.get("montant_eur") or 0), 2),
            "origine": (reponses.get(o["id"]) or o.get("origine")) if (reponses.get(o["id"]) or o.get("origine")) in (ORIGINE, ORIGINE_EXTERNE) else None,
            "repondu": o["id"] in reponses,
            "virement_id": (o.get("commentaire") or "")[len(PREFIXE):]
                           if (o.get("commentaire") or "").startswith(PREFIXE) else None,
            "saisi_le": o.get("saisi_le"),
        } for o in operations if o["type"] in ("DEPOT", "RETRAIT")],
        "nb_lignes": somme(lambda e: sum(1 for p in e.positions.values() if p.quantite > 1e-9)),
    }
