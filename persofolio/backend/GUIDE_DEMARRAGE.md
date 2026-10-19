# PersoFolio — Guide de démarrage

> L'application s'appelait **EcoFolio** jusqu'au 19 octobre 2026. Les comptes et leurs
> données ne sont pas concernés : ils vivent dans `data/portefeuille.db`, que le
> changement de nom ne touche pas. Seules les clés gardées par le navigateur changent,
> et la page d'accueil reprend automatiquement une session ouverte avant le changement.

PersoFolio réunit deux modules autour d'un seul compte utilisateur :

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

## Deux façons de lancer PersoFolio

**Pour vous et l'équipe, pendant le développement** : le serveur, lancé à la
main. C'est ce que décrit ce guide, et rien n'a changé.

**Pour tous les autres — un professeur, un ami, un jury** : l'exécutable Windows.
Un double-clic, l'application s'ouvre dans leur navigateur, ni Python ni
terminal. Pour le fabriquer, double-cliquez `construire.bat` à la racine du
projet ; le détail est dans la section « Fabriquer l'exécutable » en bas de page.

Les deux partagent le même code, mais pas le même dossier de données :

| | Base de données |
|---|---|
| Lancé à la main | `backend/data/portefeuille.db` |
| Exécutable Windows | `C:\Users\<vous>\AppData\Local\PersoFolio\portefeuille.db` |

L'exécutable ne peut pas écrire dans son propre dossier : Windows le déballe
dans un dossier temporaire qu'il efface en sortant, et les comptes
disparaîtraient à chaque fermeture.

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
cd <dossier du projet>\persofolio\backend
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

---

## Fabriquer l'exécutable Windows

À faire sur une machine **Windows** — un exécutable Windows ne se fabrique pas
ailleurs. Double-cliquez, à la racine du projet :

```
construire.bat
```

Le script vérifie que Python est là, crée un environnement isolé dans
`.venv-build` (le Python de la machine n'est pas touché), installe les
bibliothèques, contrôle que la reconnaissance optique est bien présente, puis
appelle PyInstaller. Comptez **5 à 15 minutes la première fois** : environ
300 Mo à télécharger. Les constructions suivantes sont bien plus rapides.

Le résultat : `dist\PersoFolio.exe`, autour de **260 Mo**, à envoyer avec
`LISEZ-MOI.txt`.

### Avant de le distribuer, essayez-le

Trois points que seule une machine Windows peut confirmer :

1. **Il démarre.** Double-clic, la petite fenêtre « PersoFolio est ouvert »
   apparaît et le navigateur s'ouvre. Premier lancement : 5 à 15 secondes, le
   temps du déballage.
2. **Un relevé scanné se lit.** C'est le contrôle qui compte : c'est la partie
   la plus fragile de la construction, et elle ne se voit qu'à l'usage. Le
   fichier `.spec` vérifie à la construction que les modèles sont embarqués,
   mais un essai réel vaut mieux.
3. **Les comptes survivent.** Créez un compte, fermez par le bouton
   « Quitter », rouvrez : le compte doit être là.

### Ce que Windows dira à vos destinataires

Un exécutable non signé déclenche « Windows a protégé votre ordinateur » au
premier lancement : *Informations complémentaires* → *Exécuter quand même*.
C'est écrit dans `LISEZ-MOI.txt`, à envoyer avec le fichier. Le faire taire
demande un certificat de signature de code, plusieurs centaines d'euros par an.

Gmail refuse les pièces jointes `.exe`, même dans un `.zip` : passez par un lien
Drive, WeTransfer, ou une clé USB.

### Si quelque chose se passe mal chez un utilisateur

Le journal de démarrage est dans
`C:\Users\<lui>\AppData\Local\PersoFolio\journal.txt`. Il indique le port
retenu, le dossier des données, et l'erreur complète en cas d'échec.
