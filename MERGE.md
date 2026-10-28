# Fusionner les deux moitiés du projet

> **Fusion faite le 19 octobre 2026.** La livraison compte-titres du 17 octobre
> est intégrée dans `perso-finance`. Les sections 1 à 4 restent la méthode (elle
> resservira à la prochaine livraison) ; le **§4 bis** raconte ce que la fusion a
> réellement demandé, et le **§7** liste les points à vérifier au premier
> démarrage réel.

Le projet est développé en deux moitiés parties de la même version :

- **Budget et comptes** — cette branche, `perso-finance`.
- **Compte-titres** — l'autre équipe, livraison attendue.

Ce dépôt contient la version commune de départ (commit **`base`**). C'est ce qui
rend la fusion simple : git compare les deux travaux à ce point commun et ne
signale que les endroits réellement touchés des deux côtés.

---

## 1. À envoyer à l'équipe compte-titres maintenant

Trois demandes, qui coûtent peu de leur côté et évitent l'essentiel des conflits :

1. **Tout le travail compte-titres dans `routes_portefeuille.py`, `core/` et
   `agents/`.** Ne rien ajouter dans `main.py` : il ne fait plus que monter les
   modules et servir les pages. Une route ajoutée là devra être déplacée.
2. **Ne pas toucher à `index.html`.** La page d'accueil et la connexion sont
   refaites de ce côté-ci ; leur version sera remplacée en bloc, donc tout
   travail fait dessus serait perdu.
3. **Livrer le dossier complet** (`persofolio/` avec `backend/` et
   `frontend/`), en `.zip`, sans `__pycache__` ni `.db`.

S'ils acceptent de passer à git, le mieux est qu'ils travaillent sur une branche
partie du commit `base` de ce dépôt : la fusion devient une opération normale et
cette page n'est plus qu'un pense-bête.

---

## 2. Intégrer la livraison

Depuis la racine du dépôt, travail en cours validé (`git status` propre) :

```bash
python outils/integrer_portefeuille.py ../livraison_portefeuille.zip
```

Le script replace leur livraison sur le commit `base`, dans une branche
`portefeuille`, et affiche ce qu'ils ont changé. Rien n'est encore fusionné :
c'est le moment de regarder leur diff.

Puis :

```bash
git merge portefeuille
```

ou, en une fois : `python outils/integrer_portefeuille.py ../livraison.zip --fusionner`.

---

## 3. Qui gagne, fichier par fichier

Ce tableau est la règle de résolution. Il vient d'un relevé exact des fichiers
touchés de ce côté depuis `base` (`git diff --stat base perso-finance`).

| Fichier | À qui il appartient | En cas de conflit |
|---|---|---|
| `frontend/banque.html` | nous seuls | impossible : ils ne l'ont pas |
| `backend/routes_banque.py`, `core/banque_db.py` | nous seuls | impossible |
| `frontend/index.html` | **nous** | garder notre version entière : `git checkout --ours persofolio/frontend/index.html` |
| `backend/main.py` | **nous** | garder notre version, puis y remonter à la main les routes qu'ils auraient ajoutées |
| `backend/routes_portefeuille.py` | **eux** | garder leur version, puis y remettre nos 7 lignes (`GET /auth/moi`, voir §4) |
| `frontend/portefeuille.html` | **eux** | garder leur version, puis y remettre nos 4 retouches (§4) |
| `core/portfolio_calc.py`, `core/portfolio_db.py`, `agents/*` | **eux** | garder leur version : nous n'y avons rien touché |
| `backend/archive_optimiseur/*` | nous | garder le dossier archivé, ignorer leur version active de ces fichiers |
| `requirements.txt`, `GUIDE_DEMARRAGE.md` | nous | garder notre version |

Le point important : `core/portfolio_db.py`, `core/portfolio_calc.py`,
`agents/market_data.py` et `agents/news_agent.py` sont **identiques à `base`**
de notre côté. Tout ce qu'ils y ont fait passe sans discussion.

---

## 4. Nos retouches dans leurs fichiers

Ce sont les seuls endroits où nous avons écrit dans des fichiers qui leur
appartiennent. Si git n'arrive pas à les replacer, les voici.

### `backend/routes_portefeuille.py` — une route

Juste avant `@router.post("/auth/deconnexion")` :

```python
@router.get("/auth/moi")
def moi(user: str = Depends(utilisateur_courant)):
    """Vérifie un jeton conservé par le navigateur (reprise de session au rechargement)."""
    return {"username": user}
```

Sans elle, `index.html` ne peut pas vérifier au chargement qu'une session encore
en mémoire est toujours valable.

### `frontend/portefeuille.html` — quatre retouches

1. L'écran de connexion de la page est masqué (la connexion est sur `index.html`) :
   ```html
   <section id="ecran-connexion" class="cache">
   ```
2. Deux liens dans l'en-tête, avant le bouton « Passer un ordre » :
   ```html
   <a class="mini-btn" href="index.html" style="text-decoration:none">Accueil</a>
   <a class="mini-btn" href="banque.html" style="text-decoration:none">Budget et comptes</a>
   ```
3. L'adresse de l'API suit celle de la page :
   ```js
   const API = /:8000$/.test(location.host) ? location.origin : 'http://localhost:8000';
   ```
4. Sans jeton, on renvoie vers la connexion unique — dans `deconnecter()` et à la
   toute fin du script :
   ```js
   location.replace('index.html');       // dans deconnecter(), à la place de l'affichage de #ecran-connexion
   if (jeton) demarrer(); else location.replace('index.html');
   ```

---

## 4 bis. Ce que la fusion du 19 octobre a réellement demandé

`git merge portefeuille` a laissé **deux fichiers en conflit** :
`frontend/portefeuille.html` (quatre blocs) et `requirements.txt`. Tout le reste
s'est fusionné seul — y compris `routes_portefeuille.py`, où notre route
`/auth/moi` et leurs nouvelles routes se sont posées côte à côte.

**Leur demi-part est arrivée intacte.** Le diff final de `portefeuille.html`
contre leur livraison ne contient plus que nos quatre retouches du §4 : tout
leur travail (sélecteur multi-portefeuille, cloche de notifications, bascule de
thème, synchronisation des dividendes, import courtier CSV/PDF, onglet
performance) est là, ligne pour ligne.

### Les trois arbitrages

1. **La connexion.** Ils avaient retiré l'écran de connexion au profit d'une
   session anonyme (`POST /auth/local`). L'énoncé demande un compte avec
   identifiant et mot de passe : la connexion unique d'`index.html` l'emporte.
   Leur route `/auth/local` et leur fonction `ouvrirSession()` **restent dans le
   code**, simplement plus appelées. Pour revenir au fonctionnement
   mono-utilisateur, une seule ligne à changer, tout en bas de
   `portefeuille.html` : remplacer `location.replace('index.html')` par
   `ouvrirSession()`. Le commentaire sur place le rappelle.
2. **Le thème sombre.** Leur bascule ne valait que pour le compte-titres ;
   passer au budget ramenait au clair. Le même jeu de variables CSS et la même
   bascule ont été ajoutés à `banque.html`, les deux pages partageant la clé
   `localStorage['persofolio_theme']`. Aucune couleur n'a changé en mode clair.
   `index.html` reste volontairement sombre : c'est la page d'accueil.
3. **Les dépendances.** `pypdf`, `pypdfium2` et `rapidocr-onnxruntime`
   deviennent des dépendances requises (lecture des relevés scannés).
   `scipy`, `scikit-learn` et `openpyxl` passent en commentaire : plus aucun
   module chargé par le serveur ne les importe, seul l'optimiseur archivé s'en
   servait.

### Les trois recollages

Du code de liaison, pas des fonctionnalités : sans eux, la page du compte-titres
restait bloquée sur son écran d'attente.

- Le panneau de notifications avait perdu les identifiants de ses deux boutons
  (`activer-notifs`, `effacer-notifs`) : leur disparition cassait le script au
  démarrage, donc **toute** la page.
- Le bouton **Déconnexion** était dans l'en-tête sans être relié à
  `deconnecter()`.
- Un jeton périmé (401) rouvrait une session anonyme ; il renvoie maintenant à
  `index.html`, comme le veut la connexion unique.

### À quoi ressemble un conflit réel

Essai fait **avant** la livraison, avec une livraison fictive (nouvelle route,
nouveau bouton dans l'en-tête, nouvelle constante) : **un seul fichier en conflit**,
`portefeuille.html`, sur deux blocs de trois lignes, tous deux du type « garder
les deux côtés » :

```
<<<<<<< HEAD
      <a class="mini-btn" href="index.html">Accueil</a>
      <a class="mini-btn" href="banque.html">Budget et comptes</a>
=======
      <button class="mini-btn" id="btn-export">Exporter</button>
>>>>>>> portefeuille
```

On garde les trois lignes et on supprime les marqueurs. `main.py` et
`routes_portefeuille.py` s'étaient fusionnés seuls.

---

## 5. Après la fusion

```bash
python outils/verifier_fusion.py
```

43 contrôles statiques, sans dépendance ni serveur : les deux modules sont bien
montés, la connexion unique n'a pas été réécrasée par la connexion locale, les
pages suivent l'adresse du serveur, aucun fichier n'importe un module parti dans
`archive_optimiseur/`, aucun marqueur de conflit n'est resté. Le script affiche
le remède à côté de chaque échec.

Puis le test de bout en bout, serveur lancé (`python -m uvicorn main:app --reload`),
sur <http://localhost:8000> :

1. Créer un compte → on arrive sur l'accueil.
2. Ouvrir **Budget et comptes**, charger l'exemple, ajouter une dépense.
3. Revenir à l'accueil, ouvrir le **Compte-titres** : on y entre sans se
   reconnecter.
4. Recharger la page : toujours connecté, les données sont là.
5. Se déconnecter, puis ouvrir `banque.html` directement : renvoi vers la
   connexion.

Si les cinq passent, la fusion est bonne.

```bash
git commit                       # valider la fusion
git tag v1-fusion                # repère utile avant la suite
```

---

## 6. Ce qui a été vérifié, et comment

Les 43 contrôles statiques passent. Le parcours des cinq points ci-dessus a été
joué automatiquement sur la version fusionnée, plus :

- le thème suit d'un module à l'autre, dans les deux sens ;
- les cinq onglets du budget s'ouvrent sans erreur en clair comme en sombre ;
- l'import de relevés, la relecture des tiers, les moyennes qui se recalculent
  au fur et à mesure du classement, le ré-import sans doublon ;
- le rapprochement d'une échéance de crédit déjà repérée à l'import avec la
  dette qu'on crée ensuite ;
- la déconnexion, puis l'accès direct aux deux pages protégées : renvoi vers
  `index.html` ; après reconnexion, les données sont retrouvées.

**Ce que ces essais ne prouvent pas.** Ils tournent contre un serveur d'essai qui
rejoue les relevés déjà analysés, parce que l'environnement de développement n'a
pas accès au réseau pour installer FastAPI. Le vrai serveur n'a donc jamais été
lancé ici. D'où le §7.

---

## 6 bis. Après la fusion : renommage et page d'accueil

Trois changements faits le 19 octobre, une fois la fusion validée.

**EcoFolio devient PersoFolio.** Le nom est remplacé partout : pages, titres, API,
dossier du projet (`ecofolio_perso/` → `persofolio/`), journaux, documentation, et les
clés gardées par le navigateur (`ecofolio_jeton` → `persofolio_jeton`, de même pour
l'utilisateur et le thème). Deux exceptions délibérées, toutes deux commentées sur
place : `ecofolio_banque_v1`, qui désigne des données déjà écrites sous l'ancien nom et
deviendrait illisible si on la renommait ; et `data/ecofolio_sessions.db` dans
l'optimiseur archivé, qui est un nom de fichier, pas une marque. `index.html` reprend
au chargement une session ouverte avant le changement, pour ne déconnecter personne.

**Nouvelle page d'accueil.** `index.html` est refaite : bandeau, argumentaire à gauche,
carte de connexion à droite, trois sections de présentation et un pied de page. Elle
reprend les teintes des deux modules et porte la même bascule clair / sombre, si bien
que les trois pages partagent enfin une seule identité visuelle. L'écran d'accueil
d'après connexion suit le même habillage. Le contrat de la page n'a pas bougé :
mêmes identifiants (`page-login`, `form-login`, `tab-signup`…), mêmes fonctions
(`doLogin`, `doSignup`, `doLogout`, `switchAuthTab`), même enchaînement.

**Crédits repérés à l'import.** Quand la lecture d'un relevé classe un prélèvement en
échéance de crédit, une modale le signale à la fin de l'import : l'utilisateur ouvre la
création du crédit (préremplie, et la dépense programmée en double est remplacée), ou
reclasse la ligne — ce qui vaut règle pour les relevés suivants. Sans réponse, rien ne
change. Le repérage porte sur les tiers, pas seulement sur les récurrences : un prêt
débloqué le mois dernier n'a encore qu'une échéance au relevé.

---

## 6 ter. La deuxième livraison, du 23 octobre

Elle a confirmé que la méthode tient : **un seul fichier en conflit**,
`requirements.txt`. Tout le reste s'est fusionné seul, y compris le
renommage du dossier (`ecofolio_perso/` → `persofolio/`), que git a reconnu
comme un déplacement. Nos quatre retouches de §4 sont passées sans y toucher.

La recette, à réutiliser telle quelle pour la prochaine :

```bash
git checkout portefeuille           # leur branche, partie du commit base
# remplacer backend/ et frontend/ par le contenu de leur livraison
git commit -m "Livraison de l'équipe compte-titres (<date>)"
git checkout perso-finance
git merge portefeuille
```

Poser leur livraison **sur leur propre branche** plutôt que de repartir de
`base` est ce qui rend la fusion triviale : git ne compare alors que ce qu'ils
ont changé depuis leur livraison précédente — ici sept fichiers — au lieu de
rejouer l'écart depuis le début.

### Ce qu'elle apporte

- **Régularité des investissements** : une cible mensuelle en euros, et un
  score sur 100 qui croise la fréquence (combien de mois sur douze avec au
  moins un achat) et le montant (plafonné mois par mois, de sorte qu'un gros
  versement unique ne rattrape pas les mois vides). Plusieurs ordres le même
  jour comptent pour un seul acte d'investissement.
- **Classes d'actif** Action / OPC / Obligation, au lieu d'Action / ETF.
- **Sharpe** : rendement annualisé géométrique, le même que l'onglet
  Performance. L'ancienne annualisation arithmétique donnait un chiffre
  supérieur et faisait coexister deux définitions dans la même application.
- **Lecture des avis PDF réécrite** : couche de texte seule, et chaque bloc
  confronté à ses propres égalités (quantité × cours = brut, brut ± courtage =
  montant). Une ligne qui ne retombe pas juste est signalée, pas importée.

### Deux points à savoir

**Les versements et retraits d'espèces ont disparu de la saisie.** Leur
raison : l'application suit des investissements, pas un compte bancaire. Le
moteur de calcul les traite toujours — une base existante peut en contenir —
mais plus rien ne permet d'en saisir à la main. Cela regarde directement le
point resté ouvert au §8 : relier le compte investissement du budget aux
espèces du compte-titres. Le lien reste possible, puisque le calcul accepte
toujours un DEPOT, mais il devra être alimenté par le programme et non par
l'utilisateur. À trancher avec eux.

**La reconnaissance optique n'est plus utilisée nulle part.** Leur nouvelle
lecture d'avis PDF s'en passe, et `core/releve.py` ne s'en est jamais servi :
il s'appuie sur la position des montants dans la page, que la reconnaissance
optique ne restitue pas, et sur un rapprochement au centime qu'un chiffre mal
lu ferait échouer. Les trois paquets concernés sont donc commentés dans
`requirements.txt` — environ 85 Mo de moins dans l'exécutable Windows.
`outils/dependances.py` compare désormais ce que le code importe à ce qui est
déclaré, pour que l'écart se voie tout seul la prochaine fois.

---

## 7. À vérifier au premier démarrage réel

```bash
cd persofolio/backend
python -m pip install -r requirements.txt
python -m uvicorn main:app --reload
```

1. **Ouvrir <http://localhost:8000/docs>** : la liste doit contenir à la fois les
   routes `/compte/*` du compte-titres et `/banque/etat` du budget. Si l'une des
   deux familles manque, c'est qu'un routeur n'est pas monté dans `main.py`.
2. **Les graphiques.** Chart.js et les polices viennent d'un CDN, bloqué dans
   l'environnement d'essai : aucun graphique n'a pu être affiché ici. Au premier
   lancement avec Internet, vérifier la courbe des soldes, les deux camemberts à
   côté du Sankey, et qu'ils se redessinent bien aux couleurs du thème quand on
   bascule en sombre.
3. **Les relevés scannés** sont refusés, des deux côtés, avec un message qui
   l'explique. C'est voulu depuis le 27 octobre : voir le §6 ter.
4. **Les données de marché** (`yfinance`) demandent Internet.

---

## 8. Ce qu'il restera à faire ensuite

- **Sortir l'authentification de `routes_portefeuille.py`** vers un
  `routes_auth.py` : `routes_banque.py` y importe aujourd'hui
  `utilisateur_courant`, ce qui lie les deux modules par un fichier qui
  appartient à l'autre équipe. **À faire après la fusion, pas avant** : déplacer
  ce code maintenant créerait exactement le conflit que l'on cherche à éviter.
- **Relier les deux modules** : le compte investissement du budget doit alimenter
  les espèces du compte-titres. Décision à prendre d'abord : quel côté détient ce
  solde. Le garder côté compte-titres évite de stocker le même montant deux fois.
- **Découper l'état du module budget en tables** (`routes_banque.py` stocke
  aujourd'hui un document JSON par utilisateur). C'est ce qui permettra à deux
  onglets ouverts de fusionner leurs modifications au lieu de se refuser l'un
  l'autre.
