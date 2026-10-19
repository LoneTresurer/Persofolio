# Optimiseur ESG — code archivé

Ce dossier contient le module d'optimisation de portefeuille ESG, retiré de
l'application en octobre 2026. Rien ici n'est importé par l'application : les
fichiers sont conservés intacts pour pouvoir être réactivés.

## Contenu

| Fichier | Rôle |
|---|---|
| `main_optimiseur.py` | Ancien `main.py`, avec les routes `/optimiser`, `/portefeuille/perso`, `/feedback`, `/suivi`, `/sessions`, `/esg/{ticker}`, `/prices/{ticker}`, `/portfolio/performance` |
| `agents/collector_agent.py` | Collecte des données de marché pour l'univers d'investissement |
| `agents/optimizer_agent.py` | Optimisation moyenne-variance sous contrainte ESG |
| `agents/esg_agent.py` | Scores ESG |
| `agents/predictor_agent.py` | Prévision de rendements |
| `agents/feedback_validator.py` | Validation du feedback et lecture des relevés PDF |
| `core/a_calculator.py` | Coefficient d'aversion au risque de Markowitz |
| `core/esg_horizon_model.py` | Profil ESG selon l'horizon d'investissement |
| `core/session_db.py` | Base des sessions d'optimisation (`data/ecofolio_sessions.db`) |
| `scripts/update_esg_scores.py` | Mise à jour du fichier de scores ESG |

Le front de l'optimiseur (questionnaire, résultats, projections, « Mon Suivi »)
se trouve dans les versions précédentes de `frontend/index.html`, conservées
dans votre historique de livraisons.

## Réactivation

1. Réinstaller les dépendances commentées dans `backend/requirements.txt`
   (`scipy`, `scikit-learn`, `openpyxl`, `pdfplumber`, `python-multipart`).
2. Remonter les fichiers : `agents/*` vers `backend/agents/`, `core/*` vers
   `backend/core/`, `scripts/*` vers `backend/scripts/`.
3. Reprendre les routes voulues de `main_optimiseur.py` dans `backend/main.py`,
   en gardant les deux `include_router` et le montage de `/app` du main actuel.

Les deux modules conservés (`routes_portefeuille.py`, `routes_banque.py`)
n'ont jamais dépendu de ce code : seuls `agents/market_data.py`,
`agents/news_agent.py` et `core/portfolio_calc.py` leur sont nécessaires, et
ces trois fichiers sont restés à leur place.
