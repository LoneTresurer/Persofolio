"""
core/categorisation.py
======================
Attribution d'une catégorie à une opération bancaire, à partir de son libellé.

Trois étages, du plus fiable au moins fiable :

  1. **La grammaire des préfixes.** « PRLV SEPA », « CARTE X4489 12/03 », « VIR
     RECU », « RETRAIT DAB », « F COTIS » : cette syntaxe vient des formats
     interbancaires, pas du commerçant. Elle est quasi universelle en France et
     donne le *mode* de paiement — et parfois la catégorie à elle seule (frais
     bancaires, retrait d'espèces).

  2. **Les noms communs français.** PHARMACIE, BOULANGERIE, SUPERMARCHE,
     GARAGE, COIFFURE : ce sont des mots de métier que les commerces locaux
     mettent dans leur propre raison sociale. « SUPERMARCHE KREM » ou
     « PHARMACIE DU CENTRE » se classent sans connaître l'enseigne. C'est ce qui
     couvre la longue traîne, là où un annuaire de marques échoue.

  3. **Les enseignes.** Utile, mais structurellement incomplet : personne ne
     peut énumérer les commerces de France. Cet étage ne sert qu'à rattraper ce
     que les deux premiers n'ont pas vu.

Ce qu'aucun des trois ne reconnaît part en « non catégorisé » et est montré à
l'utilisateur. Une catégorie fausse est pire qu'une case vide : l'utilisateur
fait confiance au graphique et ne revient jamais vérifier.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import List, Optional, Tuple

# ── Catégories, alignées sur celles de l'application (frontend/banque.html) ──
SORTIES = ["logement", "factures", "courses", "transport", "sorties", "abonnements",
           "sante", "shopping", "loisirs", "credit", "impots", "frais", "especes", "divers"]
ENTREES = ["salaire", "complement", "aides", "rembours", "autre_rev"]
INCONNU = "inconnu"


def normaliser(texte: str) -> str:
    """Majuscules sans accents : les relevés mélangent « PRÉLÈVEMENT » et « PRELEVEMENT »."""
    t = unicodedata.normalize("NFKD", texte or "")
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", t).strip().upper()


# ── Étage 0 : nettoyage du libellé ──────────────────────────────────────────
# Tout ce qui est identifiant technique : utile à la banque, inutile au classement,
# et surtout différent à chaque opération, ce qui casserait la détection des récurrences.
BRUIT = [
    r"\bREF\s*:?\s*[A-Z0-9]{6,}", r"\bRUM\s*:?\s*[A-Z0-9/]{6,}", r"\bICS\s*:?\s*[A-Z0-9]{6,}",
    r"\bMANDAT\s*:?\s*[A-Z0-9]{6,}", r"\bID\s*:?\s*[A-Z0-9]{8,}",
    r"\bCARTE\s*X?\s*\d{4}\b", r"\bCB\s*\*?\d{4}\b", r"\b\d{10,}\b",
    r"\bNUMERO DE CLIENT\b.*", r"\bNOT PROVIDED\b",
    r"\b\d{2}/\d{2}(?:/\d{2,4})?\b",                       # dates internes au libellé
    r"(?<=\b)(?:0[1-9]|[12]\d|3[01])(?:0[1-9]|1[0-2])(?=\b)",   # date collée JJMM (CIC : « PSC 0109 IFS »)
    r"\b(?:USD|GBP|CHF|CAD|MAD|TND|PLN|SEK|NOK|DKK)\b",
    r"\b\d+[.,]\d{2}\s*(USD|GBP|CHF|CAD|MAD|TND)\b.*",      # conversion de devise et sa suite
    r"\b1\s*EUR\s*=\s*[\d.,]+\s*[A-Z]{3}\b",
    r"\bCOMMERCE ELECTRONIQUE\b", r"\bPAIEMENT\s+(?:PSC|CB|CARTE)\b",
    r"\bSEPA\b", r"\bRECU\b", r"\bEMIS\b", r"\bINSTANTANE?\b", r"\bINST\b",
    r"[*£#]+",
]
RE_BRUIT = re.compile("|".join(BRUIT))


def nettoyer(libelle: str) -> str:
    """Libellé réduit à ce qui identifie le tiers : c'est la clé du classement et des récurrences."""
    t = normaliser(libelle)
    t = RE_BRUIT.sub(" ", t)
    t = re.sub(r"[^A-Z0-9 &.'-]", " ", t)
    # Numéros de magasin et de terminal : « H-E-B 540 » et « H-E-B 675 » sont la même
    # enseigne, et les séparer casserait la détection des récurrences.
    t = re.sub(r"\b\d{1,6}\b", " ", t)
    return re.sub(r"\s+", " ", t).strip()


# ── Étage 1 : grammaire des préfixes ────────────────────────────────────────
# (motif, type d'opération). Le type sert au classement et, plus tard, à repérer
# les charges fixes : un prélèvement mensuel est un abonnement, pas une course.
TYPES = [
    (r"^\s*(?:F\b|FRAIS\b|COTIS\b|F COTIS|COTISATION|COMMISSION|AGIOS|INT/FRAIS|INTERETS DEBITEURS)"
     r"|\bFRAIS (?:BANCAIRES|PLAFONNES|DE TENUE|D INTERVENTION|PAIEMENT)\b|\bRETRO FRAIS\b|\bCOTISATION\b", "FRAIS"),
    (r"\bRETRAIT\b|\bDAB\b|\bDISTRIB AUTOMATIQUE\b", "RETRAIT"),
    (r"^\s*(?:ANNUL|ANNULATION|EXTOURNE|REMBT|REMBOURSEMENT)\b", "ANNULATION"),
    (r"\bPRLV\b|\bPRELEVEMENT\b|\bPRELVT\b", "PRELEVEMENT"),
    (r"\bVIR\b|\bVIREMENT\b|\bVIRT\b", "VIREMENT"),
    (r"\bCHEQUE\b|\bCHQ\b|\bREMISE CHQ\b", "CHEQUE"),
    (r"\bCARTE\b|\bPAIEMENT\b|\bCB\b|\bACHAT\b", "CARTE"),
]
RE_TYPES = [(re.compile(m), t) for m, t in TYPES]


def type_operation(libelle: str) -> str:
    n = normaliser(libelle)
    for rx, t in RE_TYPES:
        if rx.search(n):
            return t
    return "AUTRE"


# ── Étages 2 et 3 : noms communs, puis enseignes ────────────────────────────
# (motif, catégorie, niveau). « nom » = nom commun de métier, le plus solide ;
# « enseigne » = marque, utile mais toujours incomplet.
REGLES: List[Tuple[str, str, str]] = [
    # — Logement —
    (r"\bLOYER\b|\bLOCATION\b|\bBAIL\b|\bQUITTANCE\b|\bSYNDIC\b|\bCOPROPRIETE\b|\bCHARGES LOC", "logement", "nom"),
    (r"\bFONCIA\b|\bNEXITY\b|\bSERGIC\b|\bCITYA\b|\bORPI\b|\bSQUARE HABITAT\b|\bCROUS\b|\bNEXT ?IMMO", "logement", "enseigne"),
    (r"\bASSURANCE HABITATION\b|\bMULTIRISQUE\b|\bHABITATION\b", "logement", "nom"),
    (r"\bMAIF\b|\bMACIF\b|\bMATMUT\b|\bGMF\b|\bAXA\b|\bALLIANZ\b|\bGENERALI\b|\bGROUPAMA\b|\bMMA\b|\bLUKO\b", "logement", "enseigne"),
    # — Énergie, eau, télécoms —
    (r"\bELECTRICITE\b|\bENERGIE\b|\bGAZ\b|\bEAU\b|\bASSAINISSEMENT\b|\bCHAUFFAGE\b", "factures", "nom"),
    (r"\bEDF\b|\bENGIE\b|\bTOTALENERGIES\b|\bEKWATEUR\b|\bMINT ENERGIE\b|\bOHM ENERGIE\b|\bVEOLIA\b|\bSUEZ\b|\bSAUR\b", "factures", "enseigne"),
    (r"\bFREE\b|\bORANGE\b|\bSFR\b|\bBOUYGUES\b|\bSOSH\b|\bRED BY\b|\bPRIXTEL\b|\bLEBARA\b|\bLYCAMOBILE\b|\bNRJ MOBILE\b", "factures", "enseigne"),
    (r"\bTELECOM\b|\bMOBILE\b|\bINTERNET\b|\bFIBRE\b|\bABONNEMENT TEL", "factures", "nom"),
    # — Courses —
    (r"\bSUPERMARCHE\b|\bHYPERMARCHE\b|\bSUPERETTE\b|\bEPICERIE\b|\bBOULANGERIE\b|\bBOUCHERIE\b|"
     r"\bPOISSONNERIE\b|\bFROMAGERIE\b|\bPRIMEUR\b|\bMARCHE\b|\bALIMENTATION\b|\bCAVISTE\b", "courses", "nom"),
    (r"\bCARREFOUR\b|\bLECLERC\b|\bE\.?LECLERC\b|\bINTERMARCHE\b|\bAUCHAN\b|\bLIDL\b|\bALDI\b|\bCASINO\b|"
     r"\bMONOPRIX\b|\bFRANPRIX\b|\bSUPER ?U\b|\bHYPER ?U\b|\bU EXPRESS\b|\bCORA\b|\bGRAND FRAIS\b|"
     r"\bPICARD\b|\bBIOCOOP\b|\bNATURALIA\b|\bLA VIE CLAIRE\b|\bCARREF\b|\bMARKET\b|\bNORMAL\b", "courses", "enseigne"),
    # — Transport —
    (r"\bSTATION\b|\bCARBURANT\b|\bESSENCE\b|\bPEAGE\b|\bPARKING\b|\bGARAGE\b|\bAUTO ?ECOLE\b|"
     r"\bTAXI\b|\bGARE\b|\bAEROPORT\b|\bPNEU", "transport", "nom"),
    (r"\bSNCF\b|\bRATP\b|\bNAVIGO\b|\bTWISTO\b|\bTCL\b|\bTISSEO\b|\bOUIGO\b|\bTRAINLINE\b|\bBLABLACAR\b|"
     r"\bFLIXBUS\b|\bUBER\b|\bBOLT\b|\bFREE ?NOW\b|\bLIME\b|\bVELIB\b|\bTOTAL ?(?:ACCESS|ENERGIES)?\b|"
     r"\bESSO\b|\bBP\b|\bSHELL\b|\bAVIA\b|\bVINCI AUTOROUTES\b|\bSANEF\b|\bAPRR\b|\bINDIGO\b|\bEFFIA\b", "transport", "enseigne"),
    # — Restaurants et sorties —
    (r"\bRESTAURANT\b|\bBRASSERIE\b|\bBISTRO\b|\bPIZZERIA\b|\bCREPERIE\b|\bTRAITEUR\b|\bCAFE\b|\bCOFFEE\b|"
     r"\bBAR\b|\bTABAC\b|\bCINEMA\b|\bTHEATRE\b|\bDISCOTHEQUE\b|\bGLACIER\b|\bSALON DE THE\b", "sorties", "nom"),
    (r"\bMCDONALD\b|\bMCDO\b|\bBURGER KING\b|\bKFC\b|\bSUBWAY\b|\bQUICK\b|\bSTARBUCKS\b|\bPAUL\b|\bBRIOCHE DOREE\b|"
     r"\bUBER ?EATS\b|\bDELIVEROO\b|\bJUST ?EAT\b|\bFRICHTI\b|\bSUSHI ?SHOP\b|\bO TACOS\b|\bUGC\b|\bPATHE\b|"
     r"\bGAUMONT\b|\bCGR\b|\bMK2\b", "sorties", "enseigne"),
    # — Abonnements —
    (r"\bABONNEMENT\b|\bSOUSCRIPTION\b", "abonnements", "nom"),
    (r"\bNETFLIX\b|\bSPOTIFY\b|\bDEEZER\b|\bCANAL\b|\bDISNEY\b|\bPRIME VIDEO\b|\bAMAZON PRIME\b|\bAPPLE\.?COM\b|"
     r"\bITUNES\b|\bGOOGLE\b|\bMICROSOFT\b|\bADOBE\b|\bOPENAI\b|\bCHATGPT\b|\bDROPBOX\b|\bYOUTUBE\b|"
     r"\bMOLOTOV\b|\bAUDIBLE\b|\bLE MONDE\b|\bMEDIAPART\b|\bTELERAMA\b", "abonnements", "enseigne"),
    # — Santé —
    (r"\bPHARMACIE\b|\bMEDECIN\b|\bDOCTEUR\b|\bCABINET\b|\bLABORATOIRE\b|\bLABO\b|\bDENTAIRE\b|\bDENTISTE\b|"
     r"\bOPTIQUE\b|\bOPTICIEN\b|\bRADIOLOGIE\b|\bKINE\b|\bINFIRMIER\b|\bCLINIQUE\b|\bHOPITAL\b|\bMUTUELLE\b|"
     r"\bPREVOYANCE\b|\bOSTEOPATH\b|\bPSYCHOLOG\b|\bVETERINAIRE\b|\bAUDITION\b|\bFRAIS DE SANTE\b", "sante", "nom"),
    (r"\bDOCTOLIB\b|\bCPAM\b|\bAMELI\b|\bASSURANCE MALADIE\b|\bHARMONIE MUT\b|\bMGEN\b|\bALAN\b|\bIPECA\b|"
     r"\bKRYS\b|\bAFFLELOU\b|\bGRAND OPTICAL\b|^DR\b", "sante", "enseigne"),
    # — Shopping —
    (r"\bVETEMENT\b|\bCHAUSSURE\b|\bBIJOUTERIE\b|\bPARFUMERIE\b|\bLIBRAIRIE\b|\bPAPETERIE\b|\bMEUBLE\b|"
     r"\bBRICOLAGE\b|\bJARDINERIE\b|\bCOIFFURE\b|\bCOIFFEUR\b|\bESTHETIQUE\b|\bPRESSING\b|\bCORDONNERIE\b", "shopping", "nom"),
    (r"\bFNAC\b|\bDARTY\b|\bBOULANGER\b|\bDECATHLON\b|\bZARA\b|\bH&M\b|\bUNIQLO\b|\bKIABI\b|\bPRIMARK\b|"
     r"\bZALANDO\b|\bVINTED\b|\bSHEIN\b|\bAMAZON\b|\bCDISCOUNT\b|\bACTION\b|\bIKEA\b|\bCONFORAMA\b|\bMAISONS DU MONDE\b|"
     r"\bLEROY MERLIN\b|\bCASTORAMA\b|\bBRICORAMA\b|\bSEPHORA\b|\bNOCIBE\b|\bYVES ROCHER\b|\bMAXICOFFEE\b", "shopping", "enseigne"),
    # — Loisirs et voyages —
    (r"\bSALLE DE SPORT\b|\bFITNESS\b|\bPISCINE\b|\bMUSEE\b|\bCONCERT\b|\bFESTIVAL\b|\bHOTEL\b|\bCAMPING\b|"
     r"\bAUBERGE\b|\bVOYAGE\b|\bAGENCE DE VOYAGE\b|\bCLUB\b|\bASSOCIATION\b|\bLICENCE SPORT", "loisirs", "nom"),
    (r"\bBASIC ?FIT\b|\bFITNESS ?PARK\b|\bKEEP ?COOL\b|\bNEONESS\b|\bBOOKING\b|\bAIRBNB\b|\bEXPEDIA\b|"
     r"\bRYANAIR\b|\bEASYJET\b|\bAIR FRANCE\b|\bTRANSAVIA\b|\bCENTER PARCS\b|\bFITNESSPARK\b|\bSTEAM\b|"
     r"\bPLAYSTATION\b|\bNINTENDO\b|\bXBOX\b|\bIBIS\b|\bNOVOTEL\b|\bMERCURE\b|\bCAMPANILE\b|\bB&B HOTEL\b|"
     r"\bACCOR\b|\bPREMIERE CLASSE\b", "loisirs", "enseigne"),
    # — Crédits —
    (r"\bECH(?:EANCE)?\b.*\bPRET\b|\bPRET\b.*\bECH(?:EANCE)?\b|\bREMBOURSEMENT PRET\b|\bCREDIT CONSO\b|"
     r"\bMENSUALITE\b|\bAMORTISSEMENT\b", "credit", "nom"),
    (r"\bCETELEM\b|\bSOFINCO\b|\bCOFIDIS\b|\bYOUNITED\b|\bFRANFINANCE\b|\bONEY\b|\bCREALFI\b|\bFLOA\b", "credit", "enseigne"),
    # — Impôts —
    (r"\bIMPOT\b|\bTAXE\b|\bTRESOR PUBLIC\b|\bDGFIP\b|\bURSSAF\b|\bFONCIERE\b|\bREDEVANCE\b|\bAMENDE\b|\bANTAI\b", "impots", "nom"),
    # — Entrées —
    (r"\bSALAIRE\b|\bSALARY\b|\bPAIE\b|\bPAYE\b|\bREMUNERATION\b|\bWAGE\b|\bTRAITEMENT\b|\bSOLDE DE TOUT COMPTE\b", "salaire", "nom"),
    (r"\bCAF\b|\bALLOCATION\b|\bAPL\b|\bPOLE EMPLOI\b|\bFRANCE TRAVAIL\b|\bBOURSE\b|\bCROUS\b|\bPRIME D ACTIVITE\b|"
     r"\bRSA\b|\bAIDE\b", "aides", "nom"),
    (r"\bREMBOURSEMENT\b|\bREMBT\b|\bINDEMNITE\b|\bSECURITE SOCIALE\b", "rembours", "nom"),
    (r"\bHONORAIRES\b|\bFACTURE\b|\bPRESTATION\b|\bMISSION\b|\bFREELANCE\b|\bDIVIDENDE\b|\bINTERETS CREDITEURS\b",
     "complement", "nom"),
]
RE_REGLES = [(re.compile(m), c, n) for m, c, n in REGLES]


@dataclass
class Classement:
    categorie: str
    confiance: str            # "sure" | "probable" | "aucune"
    origine: str              # ce qui a décidé : type, nom, enseigne, règle utilisateur…
    tiers: str                # libellé nettoyé : clé des récurrences et des règles apprises
    type_operation: str


def categoriser(libelle: str, montant: float, regles_utilisateur: Optional[dict] = None) -> Classement:
    """Classe une opération. `regles_utilisateur` : {tiers nettoyé: catégorie}, appris des corrections."""
    tiers = nettoyer(libelle)
    typ = type_operation(libelle)
    entree = montant > 0

    # Les corrections de l'utilisateur priment sur tout le reste : c'est sa vie, pas la nôtre.
    if regles_utilisateur:
        cat = regles_utilisateur.get(tiers)
        if cat:
            return Classement(cat, "sure", "regle_utilisateur", tiers, typ)

    candidates = ENTREES if entree else SORTIES
    nom = enseigne = None
    for rx, cat, niveau in RE_REGLES:
        if cat not in candidates or not rx.search(tiers):
            continue
        if niveau == "nom" and nom is None:
            nom = cat
            break                                     # le nom de métier est le signal le plus sûr
        if niveau == "enseigne" and enseigne is None:
            enseigne = cat

    # Un nom de métier passe avant le type : « VIRT IPECA FRAIS DE SANTE » est un
    # remboursement de santé, pas des frais bancaires, bien qu'il contienne « FRAIS ».
    if nom:
        return Classement(nom, "sure", "nom", tiers, typ)

    # Le type décide ensuite, sans rien savoir du tiers.
    if typ == "FRAIS" and not entree:
        return Classement("frais", "sure", "type", tiers, typ)
    if typ == "RETRAIT" and not entree:
        return Classement("especes", "sure", "type", tiers, typ)
    if typ in ("ANNULATION", "FRAIS") and entree:
        # Frais restitués, opération extournée : c'est un remboursement, pas un revenu.
        return Classement("rembours", "probable", "type", tiers, typ)

    if enseigne:
        return Classement(enseigne, "probable", "enseigne", tiers, typ)

    # Rien de reconnu. On ne devine pas : la case reste vide et visible.
    return Classement(INCONNU, "aucune", "", tiers, typ)


def couverture(classements: List[Classement], montants: List[float]) -> dict:
    """Mesure de la couverture : en lignes et, ce qui compte davantage, en euros.

    Un loyer de 820 € non reconnu pèse plus qu'une vingtaine de cafés à 3 €.
    """
    total_lignes = len(classements)
    total_euros = sum(abs(m) for m in montants) or 1.0
    connues = [(c, m) for c, m in zip(classements, montants) if c.categorie != INCONNU]
    sures = [(c, m) for c, m in connues if c.confiance == "sure"]
    return {
        "lignes": total_lignes,
        "lignes_classees": len(connues),
        "part_lignes": round(len(connues) / max(total_lignes, 1) * 100, 1),
        "part_euros": round(sum(abs(m) for _, m in connues) / total_euros * 100, 1),
        "part_lignes_sures": round(len(sures) / max(total_lignes, 1) * 100, 1),
        "euros_inconnus": round(total_euros - sum(abs(m) for _, m in connues), 2),
    }
