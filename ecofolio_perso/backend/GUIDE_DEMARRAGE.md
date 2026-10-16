# EcoFolio — Guide de démarrage complet

## Ce dont tu as besoin (à installer une seule fois)

### 1. Python 3.11 ou plus récent
- Télécharger sur : https://www.python.org/downloads/
- Pendant l'installation : cocher la case "Add Python to PATH" ✓

### 2. Les bibliothèques Python
Ouvrir PowerShell dans le dossier `backend` et taper :
```
pip install -r requirements.txt
```

---

## Lancer le serveur (à faire à chaque fois)

```powershell
cd C:\Users\21655\Desktop\StartUp\ecofolio_v7_final\ecofolio_v7\backend
python -m uvicorn main:app --reload --port 8000
```

Ouvrir ensuite `frontend/index.html` dans ton navigateur.

---

## Mettre à jour les scores ESG (une fois par mois)

```powershell
cd C:\Users\21655\Desktop\StartUp\ecofolio_v7_final\ecofolio_v7\backend
python scripts/update_esg_scores.py
```

Attendre 2-3 minutes. Relancer le serveur ensuite.

---

## Structure du projet

```
backend/
├── main.py                          ← Serveur principal (ne pas modifier)
├── requirements.txt                 ← Liste des bibliothèques nécessaires
├── GUIDE_DEMARRAGE.md               ← Ce fichier
├── agents/
│   ├── esg_agent.py                 ← Gestion des scores ESG ← MODIFIÉ
│   ├── collector_agent.py           ← Collecte des données boursières ← MODIFIÉ
│   ├── optimizer_agent.py           ← Optimisation du portefeuille
│   ├── predictor_agent.py           ← Prédictions
│   ├── news_agent.py                ← Actualités financières
│   └── feedback_validator.py        ← Validation
├── core/
│   ├── a_calculator.py
│   ├── esg_horizon_model.py
│   └── session_db.py
├── scripts/
│   └── update_esg_scores.py         ← Script mise à jour ESG mensuelle ← NOUVEAU
└── data/
    ├── README.txt
    └── esg_scores.json              ← Généré par update_esg_scores.py (absent au départ)
```

---

## Explication des scores ESG

**Tant que tu n'as pas lancé update_esg_scores.py :**
→ Les scores viennent du dictionnaire statique dans esg_agent.py
→ Message affiché : "Scores ESG estimés à partir de sources publiques disponibles."

**Après avoir lancé update_esg_scores.py :**
→ Les scores viennent directement de Sustainalytics
→ Message affiché : "Sustainalytics (site public)"

---

## Si quelque chose ne fonctionne pas

| Problème | Solution |
|---|---|
| "python n'est pas reconnu" | Réinstaller Python en cochant "Add to PATH" |
| "Module not found" | Relancer `pip install -r requirements.txt` |
| Port 8000 déjà utilisé | Changer le port : `--port 8001` |
| Scores ESG tous identiques | Normal en mode fallback, lancer update_esg_scores.py |
