# Reprendre PersoFolio dans une nouvelle conversation

Ce fichier existe pour qu'une session qui ne sait rien du projet puisse s'y
mettre sans qu'on ait à lui raconter trois semaines de travail. Il dit où en est
l'application, ce qui a déjà été décidé, et ce qui fait tomber ceux qui
l'ignorent.

**À lire en entier avant de toucher au code.**

Dépôt public : <https://github.com/LoneTresurer/persofolio>. Branche par défaut
`perso-finance` ; `portefeuille` et `base` servent à la méthode de fusion (§5).
Les commits portent l'adresse « noreply » de GitHub, pas une adresse personnelle.
Une session Claude peut pousser des branches, mais **pas d'étiquettes (tags)** :
le proxy les refuse. Elles se créent depuis l'interface GitHub.

---

## 1. Ce qu'est l'application

Deux modules, un seul compte, un seul serveur.

- **Budget et comptes** (`frontend/banque.html`, `backend/routes_banque.py`) —
  compte courant, objectifs d'épargne, crédits, import de relevés bancaires PDF.
  Écrit par Pierre.
- **Compte-titres** (`frontend/portefeuille.html`,
  `backend/routes_portefeuille.py`, `core/portfolio_*.py`, `agents/`) — ordres,
  positions, performance, risque. Écrit par le reste de l'équipe, livré par
  paquets successifs.
- **Accueil et connexion** (`frontend/index.html`) — un compte unique ouvre les
  deux.

C'est une application de **visualisation**. Elle observe des finances, elle ne
les commande pas. Elle n'interdit rien, elle explique et laisse décider. Cette
phrase a tranché plusieurs arbitrages ; elle en tranchera d'autres.

---

## 2. Ce qu'il faut savoir avant d'écrire une ligne

**Le code de l'équipe compte-titres ne se modifie pas** sans raison de fusion.
Leurs livraisons arrivent régulièrement et sont rejouées par-dessus (§5). Toute
retouche dans leurs fichiers est une dette à repayer à chaque livraison. Quand
c'est inévitable, un bloc unique et clairement commenté se refusionne en une
fois ; dix lignes éparpillées, non.

**L'optimiseur ESG est archivé**, dans `backend/archive_optimiseur/`. Leurs
livraisons le ramènent à chaque fois ; il retourne à l'archive à chaque fois.
Décision de Pierre, prise en septembre, toujours valable.

**Rien ne s'invente sur les chiffres.** Toute valeur affichée vient d'une
mesure, jamais d'un plan. La capacité d'épargne *prévue* sert à guider ; elle
n'entre dans aucune statistique affichée.

**Le ton.** Conseil, jamais leçon. Quand l'utilisateur fait un choix discutable,
l'application le signale une fois, discrètement, et n'y revient pas. Elle ne
félicite pas non plus à contretemps : quand le coussin de précaution n'est pas
constitué, elle se tait plutôt que de dire « objectif atteint ».

---

## 3. Les pièges qui ont déjà coûté du temps

Chacun a été découvert en production ou en test, pas par raisonnement.

| Piège | Ce qui se passe |
|---|---|
| **`.bat` en fins de ligne Unix** | cmd.exe échoue sur les blocs `( )` et les boucles `for`. La fenêtre se ferme avant d'exécuter une ligne, sans rien afficher. `outils/verifier_bat.py` l'attrape. |
| **Accents dans un `.bat`** | Même effet. Le fichier doit rester en ASCII pur. |
| **Chart.js venait d'un CDN** | Hors ligne, `donut()` affichait « Aucune donnée » — le même message que pour un portefeuille vide. **Corrigé le 28 octobre** : Chart.js, le module de carte et le fond de carte sont dans `frontend/vendor/`. La retouche dans `portefeuille.html` est documentée en MERGE.md §4 ; `verifier_fusion.py` échoue si une livraison remet un lien CDN. Seules les polices Google viennent encore d'internet (cosmétique). |
| **Pas de FastAPI dans une session Claude** | Pas d'accès à PyPI ni aux CDN depuis la session. Localement, `outils/essai/serveur_essai.py` fait tourner la vraie passerelle sans FastAPI. **Le vrai serveur tourne dans GitHub Actions** (§9) : c'est là qu'on le vérifie. |
| **Les journaux GitHub Actions ne sont pas lisibles depuis la session** | Ni les journaux ni les fichiers déposés (stockage Azure bloqué). Les **annotations** le sont : `lancer_essai_bureau.py` y recopie temps, contrôles en échec et fin du journal. `gh api repos/LoneTresurer/persofolio/check-runs/<job>/annotations`. |
| **cryptography compilé sur place (Mac Intel)** | Compilé contre l'OpenSSL du système, il heurtait celui que PyInstaller embarque depuis Python : `Symbol not found: _SSL_get0_group_name`, serveur mort. `PIP_ONLY_BINARY=cryptography` dans le workflow. |
| **Le port fait partie de l'origine** | Session et préférences vivent dans le localStorage de la fenêtre, rangé par origine, port compris. Passer de 8000 à 8001 d'un lancement à l'autre = déconnexion. Le lanceur reprend 8000 même en fin de fermeture (SO_REUSEADDR hors Windows). |
| **PyInstaller ne suit que les `import`** | Les fichiers de données — les pages web — doivent être déclarés dans le `.spec`, sinon l'exécutable démarre et sert une page blanche. |
| **Un versement daté d'aujourd'hui ne couvre pas un achat passé** | Le rejeu du compte-titres trie par date puis par ordre de saisie. Un versement enregistré aujourd'hui pour « expliquer » un achat de 2025 passe *après* lui : l'apport implicite reste, et le versement s'ajoute aux espèces. C'était le bouton « d'où venait cet argent ? » : +4 085 € de patrimoine à chaque clic, et la question restait posée. Ces versements sont maintenant datés de la veille de l'achat, côté serveur. |
| **Supprimer un virement côté budget seulement** | La ligne du compte courant disparaissait, le versement restait dans le compte-titres : le patrimoine montait d'autant. Même chose en *modifiant* un virement. Tout passe maintenant par `/banque/virement/annuler` ou `remplace`. |
| **Deux écritures concurrentes** | Tout ce qui envoie l'état du budget porte un numéro de version. Deux envois simultanés et le second est refusé (409). Les virements s'enchaînent pour cette raison. |

---

## 4. La jonction entre les deux modules

C'est la partie la plus récente et la plus délicate. `core/passerelle.py` la
documente en détail ; l'essentiel :

- Le **compte investissement du budget n'existe plus en propre**. Ce qu'il
  affiche, ce sont les espèces et les titres du compte-titres.
- Les espèces et le prix de revient se déduisent des **seules opérations
  enregistrées** : exacts, et connus **sans réseau**. Seule la valeur de marché
  demande des cours, et hors ligne c'est le prix de revient qui s'affiche, dit
  comme tel.
- Un virement écrit **deux lignes dans une seule transaction SQLite** —
  `banque_etats` et `operations` sont deux tables du même fichier. Il ne peut
  pas passer à moitié. Son identifiant le rend rejouable sans doublon.
- L'argent entré dans le portefeuille **sans passer par le budget** est une
  question posée à l'utilisateur, pas une anomalie : « de mon compte courant »
  (compte dans le taux d'épargne investie) ou « d'ailleurs » (n'y compte pas).
- **Nommer l'origine d'un achat passé ne change jamais le patrimoine.**
  L'argent est déjà compté dans les titres. `declarer_apports()` recalcule
  sous verrou les achats à découvert, refuse d'écrire si l'écran n'était plus
  à jour (un second clic ne trouve plus rien), et vérifie avant de valider que
  plus rien n'est à découvert. « De mon compte courant » ne débite le courant
  que pour un achat postérieur au début du suivi (`E.creeLe`) : avant, le
  solde de départ en tient déjà compte. Si le relevé importé porte déjà la
  sortie, c'est elle qui devient le virement (`reclasse` garde sa catégorie
  pour une éventuelle annulation).
- **Tout se répare depuis l'écran Comptes** : la liste « Espèces échangées
  avec le compte-titres » annule n'importe quel versement écrit par le budget,
  des deux côtés, et signale les doublons du même jour. « Corriger le solde »
  inscrit l'écart avec le solde réel comme une ligne visible, catégorie
  `correction` — ni revenu ni dépense (`estFlux` exclut tout ce qui est
  `INTERNE`).
- La **part de l'épargne investie** = virements partis du courant ÷ épargne
  libre, sur les mois civils complets. Jamais la valeur du portefeuille : elle
  monte avec le marché et afficherait un taux supérieur à 100 % sans qu'un euro
  de plus ait été épargné.

---

## 5. Intégrer une nouvelle livraison de l'équipe

La méthode est dans `MERGE.md`, §2 et §6 ter. En deux lignes :

```bash
git checkout portefeuille      # leur branche, partie du commit base
# remplacer backend/ et frontend/ par le contenu de leur livraison
git commit -m "Livraison de l'équipe compte-titres (<date>)"
git checkout perso-finance
git merge portefeuille
```

Poser leur livraison **sur leur propre branche** est ce qui rend la fusion
triviale : git ne compare que ce qu'ils ont changé depuis la fois précédente. La
dernière fusion n'a demandé qu'un seul fichier résolu à la main.

---

## 6. Vérifier avant de livrer

```bash
python outils/verifier_fusion.py     # 52 contrôles statiques
python outils/dependances.py         # requirements.txt contre les imports réels
python outils/verifier_bat.py construire.bat
python outils/essai/essai_passerelle.py .   # 34 contrôles, vrai navigateur hors ligne, vraie passerelle
cd persofolio/backend && python -m py_compile main.py routes_*.py core/*.py agents/*.py
python -m unittest tests.test_passerelle   # depuis persofolio/backend
```

`outils/essai/` remplace le faux serveur Node : `serveur_essai.py` sert
`banque.html` et appelle **les vraies fonctions** de `core/passerelle.py` et
`core/banque_db.py`, sans FastAPI. Seule la couche FastAPI (pydantic,
authentification) reste non testée ici.

Et le contrôle qui vaut tous les autres : **le patrimoine de l'exemple doit
afficher 1 150,86 €**. Il n'a pas bougé d'un centime à travers la fusion, le
renommage et la jonction des deux modules. S'il change sans raison explicite,
quelque chose s'est perdu en route.

---

## 7. Lancer l'application

```bash
cd persofolio/backend
python -m pip install -r requirements.txt
python -m uvicorn main:app --reload --port 8000
```

Puis <http://localhost:8000>. Python 3.10 ou plus récent.

Si `No module named uvicorn` : les dépendances ne sont pas installées dans le
Python qui lance la commande. Avec un environnement virtuel, c'est
`.venv\Scripts\python.exe -m pip install -r requirements.txt` qu'il faut, pas
`pip` tout court.

---

## 8. Ce qui reste ouvert

- **Le vrai serveur tourne**, dans l'application construite (GitHub Actions,
  Windows et Mac Intel) : session locale, profil d'exemple à 1 150,86 €. Les
  routes `/banque/virement`, `/banque/virement/annuler` et `/banque/apports`
  n'y sont pas encore exercées par l'essai de bureau — seulement par
  `essai_passerelle.py`, sans FastAPI.
- **Les bases déjà abîmées par l'ancien bouton** se réparent à la main, dans
  Comptes : annuler *tous* les versements qu'il a écrits (datés du jour du
  clic, signalés « doublon probable » s'il y en a plusieurs), puis répondre de
  nouveau à la question.
- **Mac Apple Silicon** : jamais construit à ce jour, faute de machine libre
  chez GitHub (« capacity constraints »). La version Intel passe tous les
  contrôles et tourne aussi sur Apple Silicon via Rosetta.
- **Installateur Windows, .dmg, publication (Releases), guides d'installation** :
  étapes (b) et (c), pas encore faites. Icône : en attente du logo.
- **Les versements et retraits d'espèces** ont été retirés de la saisie du
  compte-titres par l'équipe (« un suivi d'investissements, pas un compte
  bancaire »). Le moteur les accepte toujours, ce qui laisse passer les
  virements du budget. À retrancher avec eux s'ils vont plus loin.
- **`core/passerelle.py` écrit dans `operations`**, table qui appartient au
  compte-titres. C'est le seul endroit. `_verifier_schema()` échoue bruyamment
  si leur structure change.

---

## 9. L'application de bureau

`persofolio/backend/lanceur.py` ouvre une **fenêtre native** (pywebview :
WebView2 sous Windows, WKWebView sous macOS) sur un écran de chargement, démarre
le serveur, puis affiche l'application. Fermer la fenêtre arrête tout. Repli sur
le navigateur si WebView2 manque.

- **Construction** : GitHub Actions, `.github/workflows/construire.yml`, bouton
  « Run workflow » (une session Claude peut aussi le lancer :
  `gh workflow run construire.yml --ref perso-finance -R LoneTresurer/persofolio`,
  `-f intel=true` pour le Mac Intel). Windows : dossier `PersoFolio/` ;
  macOS : `PersoFolio.app` (arm64 par défaut, `PERSOFOLIO_ARCH=x86_64`).
- **Essai de l'application construite** : `PERSOFOLIO_ESSAI=<rapport.json>` fait
  tourner `essai_bureau.py` *dans* l'application — session locale, profil
  d'exemple, 1 150,86 €, Chart.js, puis au second lancement la session doit
  avoir survécu. Temps mesurés (28 octobre, machines GitHub) : fenêtre ~1 s,
  serveur prêt 2,5 à 6,5 s selon la machine et le lancement.
- **Dépendances** : `requirements-bureau.txt` (pywebview, PyInstaller) s'ajoute
  à `requirements.txt` ; le serveur seul n'en a pas besoin.
- **Version** : un seul fichier, `persofolio/VERSION`.
- **Données** : `%LOCALAPPDATA%\PersoFolio` (Windows),
  `~/Library/Application Support/PersoFolio` (macOS). Le stockage de la
  fenêtre (localStorage) est dans le sous-dossier `fenetre/`.
- **Éditeur** affiché : « IAE Caen - Master G2C 2026 ».
