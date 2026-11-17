"""
import_releves.py
=================
Lecture d'un envoi de relevés — PDF et tableurs mêlés — et proposition d'import.

Le corps de la route POST /banque/releves, sorti de la route pour pouvoir être
appelé sans serveur web : par les tests, et par le serveur d'essai
(outils/essai/serveur_essai.py), qui ne dispose pas de FastAPI. La route ne fait
plus que recevoir les fichiers et passer leur contenu ici.

Rien n'est enregistré : la réponse décrit ce qui a été compris, fichier par
fichier, puis propose opérations classées, tiers regroupés, récurrences et budgets.
"""

import logging
import os
import tempfile
from typing import Dict, List, Optional, Tuple

from core import tableur
from core.categorisation import categoriser
from core.habitudes import analyser
from core.releve import extraire

logger = logging.getLogger("persofolio.import")

TAILLE_MAX = 10 * 1024 * 1024      # 10 Mo : un relevé en fait quelques centaines de Ko


def _date(d) -> Optional[str]:
    return d.isoformat() if d else None


def lire_fichiers(fichiers: List[Tuple[str, bytes]], apprises: Optional[Dict[str, str]] = None) -> dict:
    """`fichiers` : [(nom, contenu)]. `apprises` : les corrections de catégorie de l'utilisateur."""
    apprises = apprises or {}
    lus, operations, periodes = [], [], []
    for nom, contenu in fichiers:
        if len(contenu) > TAILLE_MAX:
            lus.append({"nom": nom, "erreur": "fichier trop volumineux (10 Mo maximum)"})
            continue
        fmt = tableur.format_de(contenu, nom or "")
        if fmt:
            try:
                lt = tableur.lire(contenu, nom or "")
            except (tableur.ColonnesManquantes, tableur.TableurIllisible) as e:
                lus.append({"nom": nom, "erreur": str(e)})
                continue
            except Exception as e:                       # un fichier illisible ne doit pas tuer l'envoi
                logger.warning("tableur illisible (%s) : %s", nom, e)
                lus.append({"nom": nom, "erreur": "tableur illisible"})
                continue
            c, (debut, fin) = lt.compte, lt.periode
            lus.append({"nom": nom, "banque": f"Tableur ({fmt})", "anomalies": lt.anomalies(),
                        "comptes": [{
                            "numero": c.numero, "libelle": c.libelle, "tableur": True,
                            "debut": _date(debut), "fin": _date(fin),
                            "solde_ouverture": None, "solde_cloture": None,
                            "mouvements": c.somme_mouvements, "nb_operations": len(c.operations),
                            "ecart": None, "equilibre": False,
                            "colonnes": lt.colonnes, "exclues": len(lt.exclues),
                            "operations": [{"date": _date(o.date), "libelle": o.libelle,
                                            "montant": round(o.montant, 2)} for o in c.operations],
                        }]})
            operations.extend(c.operations)
            periodes.append((debut, fin))
            continue
        if not contenu[:5].startswith(b"%PDF"):
            lus.append({"nom": nom, "erreur": "format non reconnu : PDF, .xls, .xlsx ou .csv attendu"})
            continue
        chemin = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                tmp.write(contenu)
                chemin = tmp.name
            rel = extraire(chemin)
        except Exception as e:                       # un PDF illisible ne doit pas tuer l'envoi
            logger.warning("relevé illisible (%s) : %s", nom, e)
            lus.append({"nom": nom, "erreur": "relevé illisible"})
            continue
        finally:
            if chemin and os.path.exists(chemin):
                os.unlink(chemin)                    # le PDF n'est jamais conservé

        comptes = []
        for c in rel.comptes:
            comptes.append({
                "numero": c.numero, "libelle": c.libelle,
                "debut": _date(c.date_ouverture), "fin": _date(c.date_cloture),
                "solde_ouverture": c.solde_ouverture, "solde_cloture": c.solde_cloture,
                "mouvements": c.somme_mouvements, "nb_operations": len(c.operations),
                "ecart": c.ecart, "equilibre": c.equilibre,
                "operations": [
                    {"date": _date(o.date), "libelle": o.libelle, "montant": round(o.montant, 2)}
                    for o in c.operations
                ],
            })
            if c.equilibre and c.operations:
                operations.extend(c.operations)
                periodes.append((c.date_ouverture, c.date_cloture))
        lus.append({"nom": nom, "banque": rel.banque,
                    "comptes": comptes, "anomalies": rel.anomalies})

    a = analyser(operations, periodes, apprises) if operations else None
    proposition = None
    if a:
        proposition = {
            "mois_complets": a["mois_complets"],
            "mois_complets_ignores": a["mois_complets_ignores"],
            "budgets": a["budgets"],
            "mesure": a["mesure"],
            "recurrences": [r.proposition() for r in a["recurrences"]],
            "tiers": [{
                "cle": t.cle, "libelle": t.libelle, "categorie": t.categorie,
                "confiance": t.confiance, "sens": t.sens, "total": t.total,
                "occurrences": t.occurrences, "mois": t.mois,
                # De quoi proposer une opération programmée même pour un tiers que la
                # détection automatique n'a pas retenu : l'utilisateur peut le vouloir.
                "montant_type": abs(t.montant_type), "jour_type": t.jour_type,
                "stabilite": t.stabilite_montant, "ecart_jour": t.ecart_jour,
            } for t in a["tiers"]],
            "operations": [{
                "date": _date(o.date), "libelle": o.libelle, "montant": round(o.montant, 2),
                "tiers": categoriser(o.libelle, o.montant, apprises).tiers,
            } for o in sorted(operations, key=lambda o: o.date)],
        }
    return {"fichiers": lus, "proposition": proposition}
