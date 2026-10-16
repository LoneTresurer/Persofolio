import os
"""
agents/collector_agent.py
==========================
Agent de collecte des données de marché.

CORRECTIONS v6 :
  - Bêta réel par régression OLS sur indice de marché (plus proxy vol/0.18)
  - Matrice de covariance via Ledoit-Wolf réel (sklearn) sur returns alignés
  - Indice de marché par pays pour bêta CAPM correct
  - ESG avec timestamp de fraîcheur et label de source

Règle de sélection :
  - Pays choisi    → UNIQUEMENT les actions de ce pays, classées par ESG décroissant
  - Pas de préférence → Top 60 ESG mondial (tous pays confondus)
  - Jamais de mélange de pays quand un pays est explicitement sélectionné

Flux :
  collecter() → univers pays strict → filtre ESG ≥ seuil → tri ESG desc → top 60
              → téléchargement PARALLÈLE (8 workers) → rendements + covariance LW
"""

import asyncio
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError
from typing import Optional, List, Dict, Tuple

import numpy as np
import pandas as pd
import yfinance as yf
from sklearn.covariance import LedoitWolf

logger = logging.getLogger("collector")

# ── Configuration ─────────────────────────────────────────────────────────────
MAX_TICKERS_DOWNLOAD = 60
MAX_TICKERS_ESG      = 30
N_WORKERS            = 8
TIMEOUT_PER_TICKER   = 12

# ── Indices de marché par pays pour le calcul du bêta CAPM réel ──────────────
MARKET_INDEX_PAR_PAYS: Dict[str, str] = {
    "FR": "^FCHI",     # CAC 40
    "US": "^GSPC",     # S&P 500
    "DE": "^GDAXI",    # DAX
    "GB": "^FTSE",     # FTSE 100
    "JP": "^N225",     # Nikkei 225
    "CA": "^GSPTSE",   # TSX Composite
    "CH": "^SSMI",     # SMI
    "SE": "^OMX",      # OMX Stockholm 30
    "AU": "^AXJO",     # ASX 200
    "TN": "^FCHI",     # TUNINDEX peu fiable sur Yahoo Finance → repli CAC 40
                       # (la Tunisie a de fortes relations commerciales avec la France)
    "WORLD": "^GSPC",  # Fallback S&P 500 pour univers mondial
}

# ── Univers par pays ───────────────────────────────────────────────────────────
UNIVERSE_PAR_PAYS: Dict[str, List[str]] = {

    "FR": [
        "TTE.PA","AIR.PA","SAN.PA","BNP.PA","OR.PA","MC.PA","ENGI.PA","SAF.PA",
        "DG.PA","SU.PA","RMS.PA","ACA.PA","DSY.PA","LR.PA","VIE.PA","SGO.PA",
        "PUB.PA","CAP.PA","GLE.PA","HO.PA","RI.PA","ML.PA","RNO.PA","STM.PA",
        "FP.PA","WLN.PA","ERF.PA","EL.PA","SW.PA","BIM.PA","EDEN.PA","TKTT.PA",
        "FLO.PA","BOL.PA","DEC.PA","FNAC.PA","GFC.PA","ILD.PA","LACR.PA",
        "NK.PA","MERY.PA","TFI.PA","IPH.PA","ABCA.PA","SESG.PA","ORA.PA",
        "FROG.PA","HEX.PA","MLCR.PA","DBV.PA","PHEL.PA","DKUSY","ELIS.PA",
        "GTT.PA","ITRK.PA","BNPP.PA","SGEF.PA",
    ],

    "US": [
        "MSFT","AAPL","GOOGL","GOOG","JNJ","PG","NEE","HON","V","MA",
        "ABT","UNH","LIN","DHR","VRTX","SPGI","ETN","ITW","CRM","ADBE",
        "AMT","BLK","MCO","ADP","PAYC","INTU","CDNS","ANSS","TDY","FDS",
        "MPWR","KEYS","IDXX","BIO","WST","RMD","PODD","ISRG","EW",
        "DXCM","ZBH","BAX","BDX","BSX","SYK","MDT","ZTS","ALGN","HOLX",
        "EPAM","AKAM","CTSH","ACN","IBM","ORCL","NOW","WDAY","VEEV","DOCU",
        "ROK","EMR","XYL","TRMB","A","TMO","MTD","BMRN",
    ],

    "DE": [
        "SAP.DE","SIE.DE","ALV.DE","MRK.DE","DTE.DE","RWE.DE","BAS.DE",
        "IFX.DE","HEN3.DE","ADS.DE","BMW.DE","CON.DE","BEI.DE","FRE.DE","MUV2.DE",
        "DHER.DE","MTX.DE","SHL.DE","BOSS.DE","HAG.DE","WAF.DE",
        "AIXA.DE","EVD.DE","SMHN.DE","GXI.DE","VNA.DE","DWS.DE","ENR.DE",
        "EVK.DE","HFG.DE","LEG.DE","PBB.DE","HHFA.DE","KGXN.DE","TLX.DE",
        "SY1.DE","O2D.DE","PSM.DE","QIA.DE","SNG.DE","TUI1.DE","WDP.DE","ZAL.DE",
        "CBK.DE","DBK.DE","HEI.DE","LXS.DE","MAN.DE","RKET.DE","UTDI.DE",
        "VBK.DE","VOW3.DE","WCH.DE","WLSN.DE","ZEG.DE","1COV.DE",
    ],

    "GB": [
        "AZN.L","SHEL.L","ULVR.L","GSK.L","NG.L","SSE.L","DGE.L","BATS.L",
        "RKT.L","LGEN.L","PRU.L","VOD.L","BP.L","RIO.L","HSBA.L",
        "BARC.L","LLOY.L","NWG.L","STAN.L","ABDN.L","AV.L","MNDI.L",
        "CRH.L","EXPN.L","HLMA.L","IMB.L","ITRK.L","KGF.L","LAND.L",
        "MNG.L","PHNX.L","RR.L","SBRY.L","SGE.L","SMDS.L","SPX.L",
        "SVT.L","TSCO.L","UU.L","WPP.L","WEIR.L","BNZL.L","CPG.L",
        "DCC.L","FERG.L","III.L","IHG.L","IMI.L","JMAT.L","JD.L",
        "MKS.L","OCDO.L","PSN.L","SGRO.L","SN.L","SMIN.L","TW.L",
    ],

    "JP": [
        "7203.T","6758.T","4502.T","7751.T","6501.T","9432.T","8306.T",
        "8411.T","9433.T","4063.T","6367.T","6902.T","7267.T","8001.T",
        "4543.T","6954.T","8035.T","9984.T","6861.T","7741.T","4568.T",
        "8058.T","8031.T","7974.T","9022.T","9020.T","9021.T","2914.T",
        "6971.T","5108.T","7011.T","6752.T","7731.T","4901.T","3382.T",
        "8802.T","8766.T","3407.T","4523.T","5401.T","5411.T","6503.T",
        "6701.T","6702.T","6762.T","6857.T","7201.T","7261.T","7270.T",
        "7733.T","8309.T","8316.T","8604.T","8630.T","8725.T","9101.T",
        "9202.T","9531.T","9532.T","9681.T","9766.T","4911.T",
    ],

    "CA": [
        "ENB","BMO","TD","CNR","SU","BCE","MFC","RY","BNS","CP",
        "CNQ","TRP","PPL","FTS","EMA","H","CU","AQN","NPI.TO",
        "GIB-A.TO","CSU.TO","OTEX.TO","SHOP","ATD.TO","MG.TO","BAM-A.TO",
        "WCN.TO","WSP.TO","SNC.TO","GWO.TO","SLF.TO","IAG.TO",
        "POW.TO","IFC.TO","FFH.TO","CWB.TO","EQB.TO","NA.TO","CM.TO",
        "L.TO","MRU.TO","EMP-A.TO","WN.TO","NTR.TO","CCO.TO",
        "IMO.TO","CVE.TO","MEG.TO","ERF.TO","VET.TO","AEM.TO","KL.TO","ABX",
        "FNV","WPM.TO","G.TO",
    ],

    "CH": [
        "NESN.SW","ROG.SW","NOVN.SW","ZURN.SW","ABBN.SW","UBSG.SW","LONN.SW",
        "CSGN.SW","SREN.SW","GIVN.SW","GEBN.SW","SCMN.SW","SIKA.SW","CFR.SW",
        "BAER.SW","ALC.SW","SRENH.SW","TEMN.SW","VACN.SW","PGHN.SW",
        "KNIN.SW","LOGN.SW","SIGN.SW","DKSH.SW","EFGN.SW","EMMN.SW",
        "HIAG.SW","BCGE.SW","BCVN.SW","BELNN.SW","BUCN.SW","CITN.SW",
        "CMBN.SW","COTN.SW","CZNN.SW","DCON.SW","DESN.SW","DHBN.SW",
        "EMSN.SW","FSKN.SW","GAVN.SW","GKBN.SW","HELN.SW",
        "HOCN.SW","HUBN.SW","INRN.SW","IREN.SW","LISP.SW",
        "MOBN.SW","MOZN.SW","MSGN.SW","NBEN.SW","NIHN.SW","NKTN.SW",
        "ODHN.SW","ORON.SW","PCIN.SW","PEHN.SW","PSPN.SW","RAHN.SW",
    ],

    "SE": [
        "VOLV-B.ST","ERIC-B.ST","SAND.ST","SEB-A.ST","ESSITY-B.ST",
        "INVE-B.ST","ATCO-A.ST","SHB-A.ST","SWED-A.ST","ALFA.ST",
        "ALIV-SDB.ST","AZA.ST","BOL.ST","CAST.ST","EKTA-B.ST",
        "EVO.ST","GETI-B.ST","HEXA-B.ST","HMS.ST","HUFV-A.ST",
        "HUSQ-B.ST","LATO-B.ST","LIFCO-B.ST","LOOMIS.ST","NDA-SE.ST",
        "NIBE-B.ST","NOLA-B.ST","PEAB-B.ST","SAGA-B.ST","SAAB-B.ST",
        "SKA-B.ST","SKF-B.ST","SWMA.ST","TELIA.ST","TREL-B.ST",
        "VITR.ST","WALL-B.ST","ACA.ST","ALIF.ST",
        "BETS-B.ST","BILI-A.ST","CINT.ST","COIC.ST","COOR.ST",
        "DIOS.ST","DUNI.ST","ELUX-B.ST","ENEA.ST","FABG.ST",
        "HOIST.ST","IAR-B.ST","JM.ST","KIND-SDB.ST",
        "KNOW-B.ST","LUMI.ST","MIPS.ST","NETI-B.ST",
        "ORES.ST","PNDX-B.ST","PREC.ST","SAGAX-B.ST",
    ],

    "AU": [
        "BHP.AX","CBA.AX","CSL.AX","WBC.AX","ANZ.AX","MQG.AX",
        "NAB.AX","WES.AX","WOW.AX","TCL.AX","SCG.AX","GMG.AX",
        "RIO.AX","FMG.AX","NCM.AX","IGO.AX","LYC.AX",
        "APA.AX","AGL.AX","ORG.AX","ALD.AX","STO.AX","WPL.AX",
        "AMP.AX","SUN.AX","IAG.AX","QBE.AX","MFG.AX","CPU.AX",
        "REA.AX","CAR.AX","SEK.AX","IEL.AX","ALX.AX",
        "DXS.AX","GPT.AX","MGR.AX","VCX.AX","ABP.AX",
        "BWP.AX","CIP.AX","CLW.AX","NSR.AX",
        "COH.AX","FPH.AX","IDX.AX","PME.AX",
        "PRO.AX","NAN.AX","MVF.AX","EBO.AX","SHL.AX",
        "RMD.AX","FLT.AX","QAN.AX","CTD.AX","WEB.AX",
    ],

    "TN": [
        # ── BVMT (Bourse de Tunis) ─────────────────────────────────────────────
        # NOTE IMPORTANTE : la couverture Yahoo Finance des actions tunisiennes
        # est partielle et instable. Si yfinance ne renvoie pas assez de
        # données pour ces tickers, l'agent retombe automatiquement sur
        # l'univers mondial (avec un drapeau pays_repli dans la réponse).
        #
        # Secteur bancaire (le plus représenté à la BVMT)
        "BIAT.TN","ATB.TN","BT.TN","BNA.TN","BH.TN","STB.TN","UBCI.TN",
        "ATTIJARIBANK.TN","WIB.TN","BTE.TN","AB.TN",
        # Industriels et grande consommation
        "SFBT.TN","DH.TN","POULINA.TN","TLNET.TN","ARTES.TN","SAH.TN",
        "ENNAKL.TN","MGR.TN","SOMOC.TN","STIP.TN",
        # Matériaux, énergie, ciment
        "CC.TN","TPR.TN","ICF.TN","SOTUVER.TN","SOTRAPIL.TN",
    ],
}

UNIVERSE_MONDE: List[str] = (
    UNIVERSE_PAR_PAYS["FR"][:15] + UNIVERSE_PAR_PAYS["US"][:15] +
    UNIVERSE_PAR_PAYS["DE"][:10] + UNIVERSE_PAR_PAYS["GB"][:10] +
    UNIVERSE_PAR_PAYS["CH"][:7]  + UNIVERSE_PAR_PAYS["JP"][:7]  +
    UNIVERSE_PAR_PAYS["CA"][:7]  + UNIVERSE_PAR_PAYS["SE"][:5]  +
    UNIVERSE_PAR_PAYS["AU"][:5]
)

SECTEUR_MAP: Dict[str, str] = {
    "MSFT":"Technology","AAPL":"Technology","GOOGL":"Technology","GOOG":"Technology",
    "CRM":"Technology","ADBE":"Technology","SAP.DE":"Technology","DSY.PA":"Technology",
    "IFX.DE":"Technology","INTU":"Technology","CDNS":"Technology","ANSS":"Technology",
    "MPWR":"Technology","KEYS":"Technology","EPAM":"Technology","AKAM":"Technology",
    "CTSH":"Technology","ACN":"Technology","IBM":"Technology","ORCL":"Technology",
    "NOW":"Technology","WDAY":"Technology","VEEV":"Technology","DOCU":"Technology",
    "TEMN.SW":"Technology","ADP":"Technology","PAYC":"Technology","FDS":"Financials",
    "NEE":"Utilities","DTE.DE":"Utilities","RWE.DE":"Utilities","SSE.L":"Utilities",
    "ENGI.PA":"Utilities","NG.L":"Utilities","EMA":"Utilities","FTS":"Utilities",
    "AQN":"Utilities","NIBE-B.ST":"Utilities","AGL.AX":"Utilities","ORG.AX":"Utilities",
    "NESN.SW":"Cons. Staples","OR.PA":"Cons. Staples","PG":"Cons. Staples",
    "ULVR.L":"Cons. Staples","HEN3.DE":"Cons. Staples","HO.PA":"Cons. Staples",
    "ESSITY-B.ST":"Cons. Staples","WOW.AX":"Cons. Staples","WES.AX":"Cons. Staples",
    "TTE.PA":"Energy","BP.L":"Energy","SHEL.L":"Energy","ENB":"Energy","SU":"Energy",
    "CNQ":"Energy","TRP":"Energy","PPL":"Energy","STO.AX":"Energy","WPL.AX":"Energy",
    "JNJ":"Healthcare","SAN.PA":"Healthcare","AZN.L":"Healthcare","GSK.L":"Healthcare",
    "NOVN.SW":"Healthcare","ROG.SW":"Healthcare","VRTX":"Healthcare","DHR":"Healthcare",
    "ABT":"Healthcare","MRK.DE":"Healthcare","LONN.SW":"Healthcare","UNH":"Healthcare",
    "ISRG":"Healthcare","EW":"Healthcare","DXCM":"Healthcare","ZBH":"Healthcare",
    "BAX":"Healthcare","BDX":"Healthcare","BSX":"Healthcare","SYK":"Healthcare",
    "MDT":"Healthcare","ZTS":"Healthcare","CSL.AX":"Healthcare","COH.AX":"Healthcare",
    "AIR.PA":"Industrials","SAF.PA":"Industrials","SIE.DE":"Industrials",
    "HON":"Industrials","ETN":"Industrials","ITW":"Industrials",
    "VOLV-B.ST":"Industrials","FRE.DE":"Industrials","CNR":"Industrials",
    "CP":"Industrials","WSP.TO":"Industrials","WCN.TO":"Industrials",
    "ROK":"Industrials","EMR":"Industrials","XYL":"Industrials","TRMB":"Industrials",
    "BNP.PA":"Financials","ACA.PA":"Financials","GLE.PA":"Financials",
    "ALV.DE":"Financials","MUV2.DE":"Financials","UBSG.SW":"Financials",
    "ZURN.SW":"Financials","V":"Financials","MA":"Financials","SPGI":"Financials",
    "8306.T":"Financials","8411.T":"Financials","BLK":"Financials","MCO":"Financials",
    "BMO":"Financials","TD":"Financials","RY":"Financials","BNS":"Financials",
    "LGEN.L":"Financials","PRU.L":"Financials","HSBA.L":"Financials",
    "CBA.AX":"Financials","WBC.AX":"Financials","ANZ.AX":"Financials",
    "MQG.AX":"Financials","NAB.AX":"Financials","SEB-A.ST":"Financials",
    "MC.PA":"Cons. Cyclical","BMW.DE":"Cons. Cyclical","7203.T":"Cons. Cyclical",
    "LIN":"Materials","BAS.DE":"Materials","RIO.L":"Materials","BHP.AX":"Materials",
    "RIO.AX":"Materials","SAND.ST":"Materials","AMT":"Real Estate",
    "VOD.L":"Telecom","9432.T":"Telecom","9433.T":"Telecom","BCE":"Telecom",
    "TELIA.ST":"Telecom","ORA.PA":"Telecom",
    # ── Tunisie (BVMT) ─────────────────────────────────────────────────────
    "BIAT.TN":"Financials","ATB.TN":"Financials","BT.TN":"Financials",
    "BNA.TN":"Financials","BH.TN":"Financials","STB.TN":"Financials",
    "UBCI.TN":"Financials","ATTIJARIBANK.TN":"Financials","WIB.TN":"Financials",
    "BTE.TN":"Financials","AB.TN":"Financials",
    "SFBT.TN":"Cons. Staples","DH.TN":"Cons. Staples","POULINA.TN":"Cons. Cyclical",
    "TLNET.TN":"Technology","ARTES.TN":"Cons. Cyclical","SAH.TN":"Cons. Staples",
    "ENNAKL.TN":"Cons. Cyclical","MGR.TN":"Cons. Cyclical",
    "SOMOC.TN":"Materials","STIP.TN":"Industrials",
    "CC.TN":"Materials","TPR.TN":"Industrials","ICF.TN":"Materials",
    "SOTUVER.TN":"Materials","SOTRAPIL.TN":"Energy",
}

# Scores ESG de secours (backup statique) — utilisés uniquement si API indisponible
ESG_BACKUP: Dict[str, float] = {
    "MSFT":74,"NEE":74,"SIE.DE":73,"OR.PA":72,"TTE.PA":72,"AIR.PA":71,
    "NESN.SW":70,"DSY.PA":70,"SAP.DE":69,"SAN.PA":68,"ABBN.SW":66,"ROG.SW":70,
    "NOVN.SW":64,"JNJ":65,"AZN.L":66,"LIN":65,"ZURN.SW":65,"ETN":64,
    "HON":65,"DHR":63,"ADBE":62,"LONN.SW":63,"SPGI":66,"V":64,"MA":65,
    "PG":67,"ULVR.L":66,"GSK.L":66,"NG.L":65,"SSE.L":65,"DTE.DE":62,
    "ENGI.PA":64,"BNP.PA":67,"UBSG.SW":61,"HEN3.DE":64,"ABT":63,"ITW":62,
    "RWE.DE":62,"SAF.PA":71,"SHEL.L":60,"BP.L":60,"AMT":61,"ENB":61,
    "BLK":63,"MCO":66,"ADP":65,"INTU":62,"CDNS":63,"UNH":62,
    "BMO":65,"TD":64,"RY":64,"CNR":67,"VOLV-B.ST":68,"ESSITY-B.ST":69,
    "BHP.AX":61,"CBA.AX":63,"CSL.AX":64,"MQG.AX":62,"SAND.ST":67,
    "ERIC-B.ST":65,"SEB-A.ST":63,"ISRG":63,"EW":62,"ZTS":64,
    # ── Tunisie (BVMT) — scores ESG estimés (couverture Sustainalytics
    # presque inexistante pour la BVMT, valeurs conservatrices) ─────────
    "BIAT.TN":65,"ATB.TN":63,"BT.TN":64,"BNA.TN":62,"BH.TN":61,"STB.TN":61,
    "UBCI.TN":62,"ATTIJARIBANK.TN":64,"WIB.TN":60,"BTE.TN":60,"AB.TN":60,
    "SFBT.TN":68,"DH.TN":67,"POULINA.TN":63,"TLNET.TN":66,"ARTES.TN":62,
    "SAH.TN":65,"ENNAKL.TN":63,"MGR.TN":62,"SOMOC.TN":60,"STIP.TN":60,
    "CC.TN":60,"TPR.TN":61,"ICF.TN":59,"SOTUVER.TN":60,"SOTRAPIL.TN":60,
}

DIV_YIELD_SECTEUR: Dict[str, float] = {
    "Utilities":0.038,"Financials":0.030,"Energy":0.042,"Cons. Staples":0.025,
    "Real Estate":0.040,"Healthcare":0.014,"Industrials":0.016,"Technology":0.005,
    "Materials":0.020,"Telecom":0.030,"Cons. Cyclical":0.012,
}


class CollectorAgent:
    def __init__(self, chemin_excel: str = "data/Etude_Entreprises_REEL_Data_final.xlsx"):
        self.chemin_excel = chemin_excel
        self._excel_data: Optional[pd.DataFrame] = None

    async def collecter(
        self,
        pays: Optional[str] = None,
        esg_seuil: float = 59.0,
        horizon: int = 10,
        n_periodes: int = 252,
    ) -> dict:
        """
        Sélectionne et collecte les données pour un univers pays.
        Si l'univers du pays demandé ne produit pas assez de tickers
        exploitables (couverture Yahoo Finance limitée — typiquement la
        Tunisie), retombe automatiquement sur l'univers mondial et
        marque la réponse avec `pays_repli=True` + `motif_repli`.
        """
        try:
            return await self._collecter_inner(pays, esg_seuil, horizon, n_periodes)
        except (ValueError, Exception) as e:
            # On ne tente le repli que si l'utilisateur avait choisi un pays
            # spécifique ET que l'échec porte bien sur les données.
            msg = str(e).lower()
            is_data_issue = any(k in msg for k in (
                "données insuffisantes", "donnees insuffisantes",
                "aucun ticker", "0/", "yahoo finance"
            ))
            if pays and is_data_issue:
                logger.warning(
                    f"[{pays}] Collecte impossible ({e}) — repli sur l'univers mondial."
                )
                result = await self._collecter_inner(None, esg_seuil, horizon, n_periodes)
                result["pays_demande"] = pays
                result["pays_repli"]   = True
                result["motif_repli"]  = (
                    f"La couverture Yahoo Finance des actions de '{pays}' est insuffisante "
                    f"pour optimiser un portefeuille. Un univers ESG international a été "
                    f"utilisé en repli."
                )
                return result
            # Sinon on relance l'erreur d'origine
            raise

    async def collecter_liste(
        self,
        tickers: List[str],
        idx_ticker: str = MARKET_INDEX_PAR_PAYS["WORLD"],
        period: str = "1y",
    ) -> dict:
        """
        Collecte les données pour une liste de tickers CHOISIE PAR L'UTILISATEUR
        (portefeuille personnalisé) : pas de sélection d'univers, pas de filtre ESG.

        Même format de sortie que collecter(), plus la clé "tickers_rejetes"
        (tickers pour lesquels Yahoo Finance n'a pas renvoyé assez de données).
        """
        excel_df = self._load_excel()
        cibles = list(dict.fromkeys(t.strip().upper() for t in tickers if t and t.strip()))
        if not cibles:
            raise ValueError("Aucun ticker fourni.")

        donnees_yf, market_returns = await asyncio.to_thread(
            self._telecharger_avec_marche, cibles, idx_ticker, period
        )
        tickers_ok = [t for t in cibles if t in donnees_yf]   # garde l'ordre saisi
        rejetes    = [t for t in cibles if t not in donnees_yf]
        if not tickers_ok:
            raise ValueError(
                f"Aucun ticker exploitable sur Yahoo Finance : {', '.join(rejetes)}. "
                f"Vérifiez l'orthographe (ex. MC.PA, AAPL, SAP.DE)."
            )

        betas = np.array([
            self._beta_ols(donnees_yf[t].get("returns_series", np.array([])), market_returns)
            for t in tickers_ok
        ])
        rendements = np.array([donnees_yf[t]["mu_ann"]  for t in tickers_ok])
        vols       = np.array([donnees_yf[t]["vol_ann"] for t in tickers_ok])
        esg_scores = np.array([self._get_esg(t, excel_df) for t in tickers_ok])
        div_yields = np.array([
            donnees_yf[t].get("div_yield", self._div_yield(t)) for t in tickers_ok
        ])
        if len(tickers_ok) >= 2:
            cov_matrix = self._build_cov_lw(donnees_yf, tickers_ok, vols, betas)
        else:
            cov_matrix = np.array([[vols[0] ** 2]])

        def _secteur(t: str) -> str:
            return (donnees_yf[t].get("secteur_yf") or SECTEUR_MAP.get(t) or "Autre").strip()

        return {
            "tickers":      tickers_ok,
            "tickers_rejetes": rejetes,
            "rendements":   rendements.tolist(),
            "covariance":   np.asarray(cov_matrix).tolist(),
            "vols":         vols.tolist(),
            "betas":        betas.tolist(),
            "esg_scores":   esg_scores.tolist(),
            "div_yields":   div_yields.tolist(),
            "noms":         [donnees_yf[t].get("nom", t)     for t in tickers_ok],
            "secteurs":     [_secteur(t)                     for t in tickers_ok],
            "prix_actuels": [donnees_yf[t].get("last_price") for t in tickers_ok],
            "perf_1y":      [donnees_yf[t].get("perf_1y", 0) for t in tickers_ok],
            "label_pays":   "Portefeuille personnalisé",
            "pays_demande": None,
            "pays_repli":   False,
            "motif_repli":  "",
            "indice_marche": idx_ticker,
            "n_observations": int(np.median([len(donnees_yf[t].get("returns_series", []))
                                             for t in tickers_ok])),
            "methode_covariance": "Ledoit-Wolf (sklearn)" if len(tickers_ok) >= 2 else "Variance simple",
            "methode_beta": "OLS regression",
        }

    async def _collecter_inner(
        self,
        pays: Optional[str] = None,
        esg_seuil: float = 59.0,
        horizon: int = 10,
        n_periodes: int = 252,
    ) -> dict:
        """
        Implémentation interne — utilise strictement l'univers du pays demandé.
        Bêtas calculés par régression OLS contre l'indice de marché approprié.
        Matrice de covariance estimée par Ledoit-Wolf (sklearn).
        """
        excel_df = self._load_excel()

        # ── Sélection de l'univers ────────────────────────────────────────────
        if pays is None:
            candidats  = UNIVERSE_MONDE
            label_pays = "Monde entier"
            idx_ticker = MARKET_INDEX_PAR_PAYS["WORLD"]
        else:
            univers_pays = UNIVERSE_PAR_PAYS.get(pays)
            if not univers_pays:
                candidats  = UNIVERSE_MONDE
                label_pays = "Monde entier"
                idx_ticker = MARKET_INDEX_PAR_PAYS["WORLD"]
                logger.warning(f"Pays '{pays}' non reconnu → fallback monde entier")
            else:
                candidats  = univers_pays
                label_pays = pays
                idx_ticker = MARKET_INDEX_PAR_PAYS.get(pays, MARKET_INDEX_PAR_PAYS["WORLD"])

        logger.info(f"Univers candidat [{label_pays}]: {len(candidats)} tickers | Indice: {idx_ticker}")

        # ── Filtre ESG ────────────────────────────────────────────────────────
        filtrés_avec_esg: List[Tuple[str, float]] = [
            (ticker, self._get_esg(ticker, excel_df))
            for ticker in candidats
            if self._get_esg(ticker, excel_df) >= esg_seuil
        ]

        if len(filtrés_avec_esg) < 3:
            seuil_abaissé = esg_seuil - 10
            filtrés_avec_esg = [
                (ticker, self._get_esg(ticker, excel_df))
                for ticker in candidats
                if self._get_esg(ticker, excel_df) >= seuil_abaissé
            ]
            logger.warning(f"Seuil ESG abaissé à {seuil_abaissé}: {len(filtrés_avec_esg)} titres")

        filtrés_avec_esg.sort(key=lambda x: x[1], reverse=True)
        top60   = filtrés_avec_esg[:MAX_TICKERS_DOWNLOAD]
        cibles  = [ticker for ticker, _ in top60]

        logger.info(f"Top-{len(top60)} ESG [{label_pays}]: n°1={top60[0][0] if top60 else '?'}")

        # ── Téléchargement PARALLÈLE stocks + indice de marché ───────────────
        period = "2y" if n_periodes > 252 else "1y"
        t0 = time.time()

        donnees_yf, market_returns = await asyncio.to_thread(
            self._telecharger_avec_marche, cibles, idx_ticker, period
        )

        logger.info(
            f"Téléchargement [{label_pays}]: {len(donnees_yf)}/{len(cibles)} tickers "
            f"en {time.time()-t0:.1f}s | Indice {idx_ticker}: {len(market_returns)} observations"
        )

        tickers_ok = list(donnees_yf.keys())
        if len(tickers_ok) < 3:
            raise ValueError(
                f"[{label_pays}] Données insuffisantes: {len(tickers_ok)} tickers valides. "
                f"Essayez 'Pas de préférence' pour un univers mondial."
            )

        # ── Calcul des bêtas OLS réels ────────────────────────────────────────
        betas = np.array([
            self._beta_ols(donnees_yf[t].get("returns_series", np.array([])), market_returns)
            for t in tickers_ok
        ])

        rendements = np.array([donnees_yf[t]["mu_ann"]  for t in tickers_ok])
        vols       = np.array([donnees_yf[t]["vol_ann"] for t in tickers_ok])
        esg_scores = np.array([self._get_esg(t, excel_df) for t in tickers_ok])
        div_yields = np.array([
            donnees_yf[t].get("div_yield", self._div_yield(t)) for t in tickers_ok
        ])

        # ── Matrice de covariance Ledoit-Wolf réelle ──────────────────────────
        cov_matrix = self._build_cov_lw(donnees_yf, tickers_ok, vols, betas)

        logger.info(
            f"Univers final [{label_pays}]: {len(tickers_ok)} titres | "
            f"μ moy={rendements.mean()*100:.1f}% | β moy={betas.mean():.2f} | "
            f"ESG moy={esg_scores.mean():.1f}pts"
        )

        # ── Résolution du secteur : yfinance → SECTEUR_MAP → "Autre" ─────────
        # On évite explicitement "International" qui n'est pas un vrai secteur.
        def _resoudre_secteur(t: str) -> str:
            sec_yf = donnees_yf[t].get("secteur_yf")
            if sec_yf and isinstance(sec_yf, str) and sec_yf.strip():
                return sec_yf.strip()
            sec_map = SECTEUR_MAP.get(t)
            if sec_map and sec_map.strip():
                return sec_map.strip()
            return "Autre"

        return {
            "tickers":     tickers_ok,
            "rendements":  rendements.tolist(),
            "covariance":  cov_matrix.tolist(),
            "vols":        vols.tolist(),
            "betas":       betas.tolist(),
            "esg_scores":  esg_scores.tolist(),
            "div_yields":  div_yields.tolist(),
            "noms":        [donnees_yf[t].get("nom", t)          for t in tickers_ok],
            "secteurs":    [_resoudre_secteur(t)                 for t in tickers_ok],
            "prix_actuels":[donnees_yf[t].get("last_price")       for t in tickers_ok],
            "perf_1y":     [donnees_yf[t].get("perf_1y", 0)      for t in tickers_ok],
            "label_pays":  label_pays,
            "pays_demande": pays,
            "pays_repli":   False,
            "motif_repli":  "",
            "indice_marche": idx_ticker,
            "n_observations": int(np.median([len(donnees_yf[t].get("returns_series", [])) for t in tickers_ok])),
            "methode_covariance": "Ledoit-Wolf (sklearn)",
            "methode_beta": "OLS regression",
        }

    # ── Téléchargement parallèle avec indice de marché ─────────────────────

    def _telecharger_avec_marche(
        self, tickers: List[str], idx_ticker: str, period: str
    ) -> Tuple[Dict[str, dict], np.ndarray]:
        """Télécharge les données stocks + l'indice de marché en parallèle."""
        all_tickers = tickers + [idx_ticker]
        results_raw: Dict[str, dict] = {}

        with ThreadPoolExecutor(max_workers=N_WORKERS) as executor:
            future_to_ticker = {
                executor.submit(self._fetch_one_ticker, tk, period): tk
                for tk in all_tickers
            }
            for future in as_completed(future_to_ticker, timeout=120):
                tk = future_to_ticker[future]
                try:
                    data = future.result(timeout=TIMEOUT_PER_TICKER)
                    if data:
                        results_raw[tk] = data
                except (TimeoutError, Exception) as e:
                    logger.debug(f"Skip {tk}: {e}")

        # Séparer l'indice du reste
        market_data    = results_raw.pop(idx_ticker, None)
        market_returns = market_data.get("returns_series", np.array([])) if market_data else np.array([])

        return results_raw, market_returns

    def _fetch_one_ticker(self, ticker: str, period: str) -> Optional[dict]:
        """Télécharge et calcule les métriques pour un ticker."""
        try:
            tk   = yf.Ticker(ticker)
            hist = tk.history(period=period, auto_adjust=True)
            if hist.empty or len(hist) < 30:
                return None

            closes  = hist["Close"].dropna()
            returns = closes.pct_change().dropna().values  # ndarray
            if len(returns) < 30:
                return None

            mu_ann  = float(returns.mean() * 252)
            vol_ann = float(returns.std(ddof=1) * np.sqrt(252))

            divs       = hist["Dividends"].dropna()
            div_annual = float(divs.sum()) if len(divs) > 0 else 0.0
            last_price = float(closes.iloc[-1])
            div_yield  = div_annual / last_price if last_price > 0 else 0.0

            perf_1y = float(closes.iloc[-1] / closes.iloc[0] - 1) if len(closes) > 1 else 0.0

            try:
                nom = getattr(tk.fast_info, "long_name", None) or ticker
            except Exception:
                nom = ticker

            # Récupération du secteur réel depuis yfinance (info dictionnaire)
            # Si indisponible, on tombera sur SECTEUR_MAP puis sur "Autre"
            secteur_yf = None
            try:
                info = tk.info  # appel réseau, mais mis en cache par yfinance
                if isinstance(info, dict):
                    secteur_yf = info.get("sector") or info.get("sectorDisp")
                    if secteur_yf and isinstance(secteur_yf, str):
                        secteur_yf = secteur_yf.strip() or None
            except Exception:
                secteur_yf = None

            return {
                "mu_ann":         mu_ann,
                "vol_ann":        vol_ann,
                "div_yield":      float(div_yield),
                "perf_1y":        float(perf_1y),
                "last_price":     float(last_price),
                "nom":            nom,
                "secteur_yf":     secteur_yf,
                "n":              len(returns),
                "returns_series": returns,   # gardé pour OLS et LW
            }
        except Exception as e:
            logger.debug(f"fetch_one {ticker}: {e}")
            return None

    # ── Bêta OLS réel ─────────────────────────────────────────────────────────

    @staticmethod
    def _beta_ols(stock_returns: np.ndarray, market_returns: np.ndarray) -> float:
        """
        Bêta CAPM par régression OLS : beta = Cov(r_stock, r_mkt) / Var(r_mkt)
        Fallback vers 1.0 si données insuffisantes.
        """
        if len(stock_returns) < 30 or len(market_returns) < 30:
            return 1.0
        n = min(len(stock_returns), len(market_returns))
        r_s = stock_returns[-n:]
        r_m = market_returns[-n:]
        var_m = float(np.var(r_m, ddof=1))
        if var_m < 1e-12:
            return 1.0
        cov = float(np.cov(r_s, r_m, ddof=1)[0, 1])
        beta = cov / var_m
        # Bornes réalistes : ]0.1 ; 3.0]
        return float(np.clip(beta, 0.1, 3.0))

    # ── Matrice de covariance Ledoit-Wolf ─────────────────────────────────────

    @staticmethod
    def _build_cov_lw(
        donnees_yf: Dict[str, dict],
        tickers_ok: List[str],
        vols: np.ndarray,
        betas: np.ndarray,
    ) -> np.ndarray:
        """
        Matrice de covariance annualisée via Ledoit-Wolf (sklearn).
        Si les séries temporelles ne sont pas assez longues ou alignées,
        fallback vers le modèle à un facteur avec shrinkage fixe 15%.
        """
        try:
            # Collecter les séries de retours
            series = [donnees_yf[t].get("returns_series", np.array([])) for t in tickers_ok]
            min_len = min(len(s) for s in series)

            if min_len < 60:
                raise ValueError(f"Séries trop courtes ({min_len} obs) pour LW")

            # Aligner sur la longueur minimale (retours les plus récents)
            matrix = np.column_stack([s[-min_len:] for s in series])  # (n_obs, n_stocks)

            # Ledoit-Wolf : estimation du shrinkage optimal
            lw = LedoitWolf(assume_centered=False)
            lw.fit(matrix)
            cov_daily   = lw.covariance_
            cov_annual  = cov_daily * 252          # annualisation
            shrinkage   = round(float(lw.shrinkage_), 4)

            logger.info(f"Ledoit-Wolf OK: shape={matrix.shape} | shrinkage={shrinkage:.4f}")
            return cov_annual

        except Exception as e:
            logger.warning(f"LW fallback sur modèle à un facteur: {e}")
            return CollectorAgent._build_cov_factor(vols, betas)

    @staticmethod
    def _build_cov_factor(vols: np.ndarray, betas: np.ndarray) -> np.ndarray:
        """
        Fallback : modèle à un facteur + shrinkage fixe 15% vers la diagonale.
        σ_m = 18% annualisé (volatilité marché long terme)
        ρ_résiduel = 15% (corrélation résiduelle cross-sectorielle)
        """
        n, sig_m, rho_r = len(vols), 0.18, 0.15
        cov = np.zeros((n, n))
        for i in range(n):
            eps_i = np.sqrt(max(0.0, vols[i]**2 - betas[i]**2 * sig_m**2))
            for j in range(n):
                eps_j = np.sqrt(max(0.0, vols[j]**2 - betas[j]**2 * sig_m**2))
                cov[i, j] = (vols[i]**2 if i == j
                             else betas[i]*betas[j]*sig_m**2 + rho_r*eps_i*eps_j)
        # Shrinkage fixe 15%
        return (1 - 0.15) * cov + 0.15 * np.diag(np.diag(cov))

    # ── Helpers ESG ───────────────────────────────────────────────────────────

    def _load_excel(self) -> Optional[pd.DataFrame]:
        """Chargement optionnel — le fallback ESG_BACKUP est utilisé si absent."""
        if self._excel_data is not None:
            return self._excel_data
        if not os.path.isfile(self.chemin_excel):
            return None   # fichier optionnel, pas de warning
        try:
            df = pd.read_excel(self.chemin_excel, index_col=0)
            self._excel_data = df
            logger.info(f"Données historiques chargées: {len(df)} entreprises")
            return df
        except Exception as e:
            logger.warning(f"Erreur lecture Excel: {e}")
            return None

    def _get_esg(self, ticker: str, excel_df: Optional[pd.DataFrame]) -> float:
        """Retourne le score ESG depuis l'Excel si disponible, sinon backup statique."""
        if excel_df is not None:
            tk_excel = (ticker
                        .replace(".PA", ",PA")
                        .replace(".DE", ",DE")
                        .replace(".L", ",L"))
            if tk_excel in excel_df.index:
                esg_cols = [c for c in excel_df.columns if "ESG" in c.upper() or "Score" in c]
                if esg_cols:
                    val = excel_df.loc[tk_excel, esg_cols[0]]
                    if pd.notna(val) and 0 < float(val) <= 100:
                        return float(val)
        return ESG_BACKUP.get(ticker, 62.0)

    def _div_yield(self, ticker: str) -> float:
        return DIV_YIELD_SECTEUR.get(SECTEUR_MAP.get(ticker, ""), 0.015)
