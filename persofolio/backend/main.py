"""
PersoFolio API v11.0
==================
Deux modules, un seul compte utilisateur :

  • Budget et comptes  → routes_banque.py       (compte courant, compte investissement, dettes)
  • Compte-titres      → routes_portefeuille.py (connexion, ordres, positions, performance, risque)

Routes servies ici :
  GET  /app/...   → interface web (index.html, banque.html, portefeuille.html)
  GET  /          → redirection vers /app/
  GET  /health    → contrôle de vie
  GET  /docs      → documentation interactive des routes

L'optimiseur ESG (questionnaire, allocation, projections, suivi) a été retiré de
l'application : son code est conservé tel quel dans backend/archive_optimiseur/,
avec l'ancien main.py (main_optimiseur.py) qui contenait ses routes.
"""

import logging
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s")
logger = logging.getLogger("persofolio")

app = FastAPI(title="PersoFolio API", version="11.0.0", docs_url="/docs")
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])

# Compte-titres (connexion, ordres, positions, PnL, risque, actualités)
from routes_portefeuille import router as router_portefeuille
app.include_router(router_portefeuille, tags=["Compte-titres"])

# Budget et comptes (compte courant, compte investissement, dettes)
from routes_banque import router as router_banque
app.include_router(router_banque, tags=["Budget et comptes"])


@app.get("/health")
def health():
    return {"status": "healthy", "version": app.version}


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/app/")


# Interface web servie par l'API : une seule origine, donc un seul jeton de
# connexion partagé par toutes les pages.
# Monté en dernier : le préfixe /app ne doit capter aucune route de l'API.
# Le chemin passe par core.emplacements : dans l'exécutable Windows, les pages
# sont déballées ailleurs que dans le dépôt.
from core.emplacements import dossier_pages

FRONTEND_DIR = dossier_pages()


@app.middleware("http")
async def revalider_les_pages(requete, suite):
    """
    Oblige le navigateur à revérifier les pages avant de les réutiliser.

    Sans cela, Chrome garde `portefeuille.html` en cache et continue d'afficher
    l'ancienne version apres une mise a jour — y compris quand le fichier sur le
    disque a bien change. Le symptome est deroutant : le serveur sert le bon
    fichier, et l'ecran montre l'ancien.

    « no-cache » ne veut pas dire « ne garde rien » : le navigateur garde sa copie
    mais demande au serveur si elle est toujours valable. StaticFiles repond par un
    ETag, donc un 304 de quelques octets quand rien n'a change. Le cout est nul et
    l'ecran ne peut plus retarder sur le disque.
    """
    reponse = await suite(requete)
    if requete.url.path.startswith("/app"):
        reponse.headers["Cache-Control"] = "no-cache, must-revalidate"
    return reponse


app.mount("/app", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
