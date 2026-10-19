# PersoFolio v7.0 — Optimiseur ESG + Analyse Rétrospective de Performance

## Nouveautés v7 vs v6

| Fonctionnalité | Description |
|----------------|-------------|
| **Persistance sessions** | Chaque optimisation génère un `session_id` stocké en SQLite |
| **`POST /feedback`** | Soumet les résultats réels, déclenche les 4 couches de validation |
| **`POST /feedback/pdf`** | Upload relevé courtier PDF → extraction automatique (pdfplumber) |
| **`GET /session/{id}`** | Charge une session passée pour comparaison |
| **`GET /feedback/{id}`** | Historique des feedbacks d'une session |
| **Bannière session ID** | Affichée dans les résultats, copiable en 1 clic |
| **Page "Mon Suivi"** | Formulaire guidé + upload PDF + chat Claude analytique |

## 4 Couches de protection contre les données fausses

```
Couche 1 — Statistique (backend)
  Bornes IC 99% : rendement ± 3σ annualisés
  Absurdités absolues : rendement > 200%, montant < 0, durée invalide

Couche 2 — Cohérence interne (backend)
  Frais > 5% du capital → warning
  Dividendes implicites > 15%/an → warning
  Incohérence montants vs rendement déclaré → warning

Couche 3 — Benchmarking réel (backend, asynchrone)
  Télécharge les vrais prix Yahoo Finance sur la période
  Reconstitue la performance théorique du portefeuille recommandé
  Compare avec l'indice de marché du pays (^FCHI, ^GSPC, etc.)

Couche 4 — Upload PDF (backend)
  Extraction automatique via pdfplumber
  Support : DEGIRO, Interactive Brokers, Boursorama, eToro, Saxo, Fortuneo
  Pré-remplit le formulaire — l'investisseur ne peut plus saisir n'importe quoi
```

## Architecture complète v7

```
ecofolio_v7/
├── backend/
│   ├── main.py                        # API v7 — 8 endpoints
│   ├── requirements.txt               # + pdfplumber, python-multipart
│   ├── core/
│   │   ├── a_calculator.py            # Arrow-Pratt + recalibration
│   │   ├── esg_horizon_model.py       # Primes ESG par horizon
│   │   └── session_db.py              # SQLite : sessions + feedbacks
│   └── agents/
│       ├── collector_agent.py         # Bêta OLS + Ledoit-Wolf
│       ├── optimizer_agent.py         # Drawdown Monte Carlo + alpha adaptatif
│       ├── esg_agent.py               # ESG + fraîcheur
│       ├── predictor_agent.py         # IC Monte Carlo + probabilité gain
│       ├── news_agent.py              # RSS sentiment
│       └── feedback_validator.py      # 4 couches validation anti-fraude
└── frontend/
    └── index.html
        ├── Page "Mon Suivi" complète
        ├── Formulaire guidé (Couche 2 côté UX)
        ├── Upload PDF (Couche 4 côté UX)
        ├── Affichage flags (Couche 1)
        ├── Benchmark marché (Couche 3)
        ├── Chat Claude (diagnostic + multi-tour)
        └── Bannière session_id dans résultats
```

## Endpoints API v7

```
POST /optimiser          → Pipeline ESG + session_id retourné
POST /feedback           → Analyse rétrospective (4 couches)
POST /feedback/pdf       → Extraction relevé courtier
GET  /session/{id}       → Session passée
GET  /sessions           → Liste sessions récentes
GET  /feedback/{id}      → Feedbacks d'une session
GET  /esg/{ticker}       → Score ESG Sustainalytics
GET  /health             → Santé API
GET  /docs               → Swagger UI
```

## Lancement

```bash
cd backend
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

Ouvrir `frontend/index.html` — détecte automatiquement le backend.
La base de données SQLite est créée automatiquement dans `data/ecofolio_sessions.db`.

## Flux utilisateur complet

```
1. Remplir le questionnaire → Obtenir le portefeuille ESG optimisé
2. Noter le session_id (affiché et copiable dans les résultats)
3. Investir selon les recommandations
4. Revenir sur "Mon Suivi" après quelques mois
5. Saisir le session_id + montants réels (ou uploader PDF courtier)
6. Recevoir diagnostic : écart prédit/réel, alpha vs marché, recommandations
7. Poser des questions au chat Claude analytique
```
