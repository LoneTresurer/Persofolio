# EcoFolio — Guide de démarrage

EcoFolio réunit deux modules autour d'un seul compte utilisateur :

- **Budget et comptes** (`banque.html`) — compte courant et compte investissement,
  revenus et dépenses, objectifs d'épargne, crédits.
- **Compte-titres** (`portefeuille.html`) — ordres, positions, performance et risque
  du portefeuille réel, aux cours du marché. Plusieurs portefeuilles par compte,
  notifications de dividendes, import des relevés de courtier (CSV et PDF).

Les deux modules partagent la même bascule clair / sombre : le choix fait sur une
page vaut pour l'autre.

L'optimiseur ESG a été retiré de l'application en octobre 2026. Son code est conservé
dans `backend/archive_optimiseur/` ; voir le README de ce dossier pour le réactiver.
`scipy`, `scikit-learn` et `openpyxl` ne servaient qu'à lui : ils sont commentés dans
`requirements.txt`, à décommenter en même temps que le reste.

---

## Ce dont tu as besoin (à installer une seule fois)

### 1. Python 3.11 ou plus récent
- Télécharger sur : https://www.python.org/downloads/
- Pendant l'installation : cocher la case « Add Python to PATH » ✓

### 2. Les bibliothèques Python
Ouvrir PowerShell dans le dossier `backend` et taper :
```
pip install -r requirements.txt
```

L'installation prend quelques minutes : elle comprend la lecture des PDF et la
reconnaissance optique (`pypdf`, `pypdfium2`, `rapidocr-onnxruntime`), qui permet
d'importer un relevé **scanné**. Un relevé PDF ordinaire n'en a pas besoin.
RapidOCR télécharge ses modèles au premier relevé scanné lu, pas à l'installation.

---

## Lancer le serveur (à faire à chaque fois)

```powershell
cd <dossier du projet>\ecofolio_perso\backend
python -m uvicorn main:app --reload --port 8000
```

Ouvrir ensuite **http://localhost:8000** dans ton navigateur : l'API sert elle-même
les pages.

Ne plus ouvrir les fichiers HTML directement (double-clic ou aperçu VS Code) : la
connexion et les données passent par le serveur, et toutes les pages doivent venir
de la même adresse pour partager la session.

### Un seul compte pour toute l'application
- Création du compte et connexion sur la page d'accueil (`index.html`).
- Le même compte ouvre le compte-titres et le module Budget et comptes.
- Tout est enregistré dans `backend/data/portefeuille.db` (mots de passe hachés,
  jamais en clair) : les données survivent aux rechargements, aux redémarrages du
  serveur et vous suivent d'un navigateur à l'autre.
- Pour repartir de zéro : supprimer `data/portefeuille.db` serveur arrêté
  (efface tous les comptes).

### Vérifier que tout est en place
- `http://localhost:8000/health` → `{"status":"healthy"}`
- `http://localhost:8000/docs` → la liste des routes, dont `/auth/*`, `/compte/*`,
  `/titres/{ticker}` et les trois `/banque/etat`.

---

## Structure du projet

```
frontend/
├── index.html                ← Connexion, création de compte, accueil (accès aux deux modules)
├── banque.html               ← Budget et comptes
└── portefeuille.html         ← Compte-titres

backend/
├── main.py                   ← Serveur : monte les deux modules et sert le frontend sur /app
├── routes_portefeuille.py    ← Connexion + compte-titres (/auth, /compte, /titres)
├── routes_banque.py          ← Budget et comptes (/banque/etat)
├── requirements.txt
├── GUIDE_DEMARRAGE.md        ← Ce fichier
├── agents/
│   ├── market_data.py        ← Cours, devises et fiches titres (Yahoo Finance)
│   └── news_agent.py         ← Actualités des titres détenus
├── core/
│   ├── portfolio_calc.py     ← Positions, PnL, performance, risque
│   ├── portfolio_db.py       ← Utilisateurs, jetons, opérations de bourse
│   ├── banque_db.py          ← État du module Budget et comptes
│   ├── releve.py             ← Lecture des relevés bancaires PDF (toutes banques)
│   ├── categorisation.py     ← Catégorie d'une opération d'après son libellé
│   ├── habitudes.py          ← Récurrences et budgets déduits des relevés
│   ├── import_csv.py         ← Import d'un relevé de courtier au format CSV
│   └── import_pdf.py         ← Import d'un relevé de courtier au format PDF
├── tests/
│   └── test_releve.py        ← Contrôle d'équilibre sur vos propres relevés
├── archive_optimiseur/       ← Optimiseur ESG retiré de l'application (voir son README)
└── data/
    └── portefeuille.db       ← Base SQLite, créée au premier lancement
```

---

## Si quelque chose ne fonctionne pas

| Problème | Solution |
|---|---|
| « python n'est pas reconnu » | Réinstaller Python en cochant « Add to PATH » |
| « Module not found » | Relancer `pip install -r requirements.txt` |
| Port 8000 déjà utilisé | Changer le port : `--port 8001`, puis ouvrir `http://localhost:8001` |
| « Le serveur ne répond pas » dans une page | Le backend n'est pas lancé, ou la page a été ouverte en double-clic au lieu de `http://localhost:8000` |
| « Session expirée » | Se reconnecter depuis la page d'accueil |
| Les graphiques restent vides | Chart.js vient d'Internet : vérifier la connexion, ou recharger la page |
| Les cours des titres ne s'affichent pas | `yfinance` interroge Yahoo Finance : Internet requis |
| Un relevé scanné n'est pas lu | Premier usage : RapidOCR télécharge ses modèles, laisser le temps au premier import |
