# Reprendre PersoFolio dans une nouvelle conversation

Ce fichier existe pour qu'une session qui ne sait rien du projet puisse s'y
mettre sans qu'on ait à lui raconter trois semaines de travail. Il dit où en est
l'application, ce qui a déjà été décidé, et ce qui fait tomber ceux qui
l'ignorent.

**À lire en entier avant de toucher au code.**

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
| **Chart.js vient d'un CDN** | Hors ligne, `donut()` affiche « Aucune donnée » — le même message que pour des données réellement absentes. Trompeur dans un exécutable de bureau. Non corrigé : c'est leur code. |
| **Le serveur d'essai n'est pas FastAPI** | Aucune session n'a jamais pu lancer `uvicorn` : pas de réseau pour `pip install`. Tout le frontend est testé contre un faux serveur Node. Ce qui est vérifié ici ne prouve rien sur le vrai serveur. |
| **PyInstaller ne suit que les `import`** | Les fichiers de données — les pages web — doivent être déclarés dans le `.spec`, sinon l'exécutable démarre et sert une page blanche. |
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
python outils/verifier_fusion.py     # 46 contrôles statiques
python outils/dependances.py         # requirements.txt contre les imports réels
python outils/verifier_bat.py construire.bat
cd persofolio/backend && python -m py_compile main.py routes_*.py core/*.py agents/*.py
```

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

- **Le vrai serveur n'a jamais tourné.** Au premier `uvicorn`, vérifier que
  `/docs` liste à la fois `/compte/*`, `/banque/etat`,
  `/banque/investissement` et `/banque/virement`.
- **L'exécutable Windows n'a jamais été construit.** `construire.bat` est écrit
  et contrôlé statiquement, jamais exécuté.
- **Les versements et retraits d'espèces** ont été retirés de la saisie du
  compte-titres par l'équipe (« un suivi d'investissements, pas un compte
  bancaire »). Le moteur les accepte toujours, ce qui laisse passer les
  virements du budget. À retrancher avec eux s'ils vont plus loin.
- **`core/passerelle.py` écrit dans `operations`**, table qui appartient au
  compte-titres. C'est le seul endroit. `_verifier_schema()` échoue bruyamment
  si leur structure change.
