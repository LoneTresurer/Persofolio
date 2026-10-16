# EcoFolio — Guide de démarrage

EcoFolio réunit deux modules autour d'un seul compte utilisateur :

- **Budget et comptes** (`banque.html`) — compte courant et compte investissement,
  revenus et dépenses, objectifs d'épargne, crédits.
- **Compte-titres** (`portefeuille.html`) — ordres, positions, performance et risque
  du portefeuille réel, aux cours du marché.

L'optimiseur ESG a été retiré de l'application en octobre 2026. Son code est conservé
dans `backend/archive_optimiseur/` ; voir le README de ce dossier pour le réactiver.

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
│   └── banque_db.py          ← État du module Budget et comptes
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
