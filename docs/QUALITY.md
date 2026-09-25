# Quality et non-régression de HESTIA Installer

## Frontière couverte actuelle - 5B2.3

Le cockpit public reste limité aux sources prêtes. Les API privées vont maintenant
jusqu'au SQL fresh préparé, à l'Assistant optionnel, à l'activation et au scellement.
La recette système et les écrans restent5D ; le nouvel upgrade reste5C.
Contrat et limites : [PHASE5B23_FINALIZATION.md](PHASE5B23_FINALIZATION.md).
Les sections datées plus bas décrivent l'historique, pas une qualification nouvelle.

Le gel5B2.3 conserve353 core et ajoute37 unitaires :390 attendus. Les16 tests du
gate sont inclus dans core et ne s'additionnent pas comme tests uniques. Les16 DOM
et21 HTTPS natifs restent inchangés, avec les mêmes assertions et bornes. Les21
nouveaux scénarios cross-dépôts SQL/TLS/HTTP et les18 anciens SQL/TLS sont des
campagnes distinctes réelles, jamais assimilées à des mocks unitaires.

La Quality Web reste complète (PHP8.3/8.4, Composer, PHP/JS, MariaDB11.4
fresh/replay/upgrade, Apache interne). Son nouveau test de finalisation comporte27
contrôles. Aucune évolution de schema.sql/install.php/migrations/version/vendor.
Le smoke HTTP de finalisation est un test loopback sous identité Web, pas une
recette Apache/FPM/HTTPS/proxy ou des fonctions GED/maintenance complètes.

La publication, la réussite des runs et leurs versions réellement observées sont
consignées dans les rapports compagnons et #13/#135 après la campagne finale.
Ne pas reprendre un vert historique comme preuve ni annoncer un succès système.

## Commandes reproductibles

Sur un environnement Debian de test jetable avec les outils requis :

```bash
HESTIA_ACCOUNT_DB_TEST=1 ./scripts/quality-local.sh
HESTIA_ACCOUNT_DB_TEST=1 ./scripts/quality-wizard.sh
```

La première commande impose Python 3, Node, bash, OpenSSL et iproute2, contrôle
la syntaxe, les sources et leurs permissions, puis exécute la suite Python avec
un rapport strict et le préflight CLI sous l'identité root réellement requise.
Ne pas lancer des tests root sur une machine de production.

La deuxième inclut cette première commande, puis les 16 scénarios DOM historiques
et les scénarios Chromium natifs. Elle exige Chromium et les dépendances de
`requirements-quality.txt`, dans un environnement virtuel de développement.
Ces dépendances ne sont jamais installées par le bootstrap HESTIA.

`--browser-only` ne rejoue que les deux suites navigateur : la CI l'utilise après
avoir demandé les suites Python séparément sur les deux versions de Debian.
`--bridge-only` est un diagnostic partiel explicite pour un environnement qui
interdit la navigation native. Il n'annonce jamais une Quality globale réussie.

## Un résultat vert exige tous les contrôles requis

Le runner `scripts/quality.py` refuse une suite vide, un test ignoré, une erreur,
un échec attendu, une disparition d'identifiant de test ou une modification des
sources pendant le contrôle. L'inventaire versionné est
`tests/quality-baseline.json`. Une modification intentionnelle de cet inventaire
nécessite une revue : ne pas supprimer un test pour contourner un échec.

L'inventaire de ce lot comprend les 205 tests Python historiques, les tests du
dispositif Quality lui-même, les 16 scénarios DOM historiques et les tests natifs.
Les comptes exacts, les identifiants et les résultats mesurés sont dans les
rapports JSON/JUnit produits pour le commit réellement testé.

Les contrôles statiques incluent : syntaxe Python/JavaScript/shell, scripts 0755,
fichiers non inscriptibles par le groupe ou les autres, absence de sources liées,
liens Markdown locaux, motifs de secrets, appels Python runtime dangereux,
absence de ressources distantes et de persistance navigateur dans le mini-web.
Ce sont des garde-fous déterministes, pas un audit de sécurité exhaustif ni une
preuve formelle de sécurité ou de couverture de code à 100 %.

## Workflow GitHub Actions

Un seul workflow permanent : `.github/workflows/quality.yml`.

- `Scope and static guards` : systématique, y compris pour la documentation ;
  le mécanisme de Quality est lui-même testé sans ignorer les tests manquants.
- `Core / Debian 12` et `Core / Debian 13` : vrais conteneurs Debian, Python de
  la distribution, suites historiques et nouvelles, préflight CLI réel.
- `Browser / bridge and native HTTPS` : Debian 13, illustration originale,
  fixtures GitHub, DOM historique et vrai navigateur HTTPS.
- `Installer Quality gate` : agrégation fermée ; un job requis échoué, annulé,
  ignoré ou absent interdit la publication du package de sources.

Les événements sont les pushes sur main ou `quality/**`, les PR vers main et
le lancement manuel. Un lancement manuel demande toujours la campagne complète.
Les seules modifications classées documentaires sont README.md, CONTRIBUTING.md
et les fichiers Markdown du dossier docs. Un chemin inconnu, un changement mixte,
une suppression de test ou un historique non disponible déclenchent les tests
complets. Un changement documentaire ne peut éviter la campagne complète que si
le commit précédent a une exécution réussie de ce même workflow. Un run précédent
en cours, annulé, échoué, absent ou inaccessible impose une nouvelle campagne
complète. Cela empêche un push de documentation de masquer une modification de
code encore non validée. Aucun filtre global de workflow ne laisse un check requis en attente.
Un contrôle documentaire vert est explicitement distinct d'une nouvelle recette
applicative et ne publie pas de package applicatif.

Les conteneurs sont jetables. Les dépendances Python de test sont versionnées ;
les paquets Debian suivent les dépôts signés de la distribution, donc les versions
exactes doivent être relues dans les preuves de chaque campagne. Cela n'est pas
une image système hermétique figée pour toujours.

Les Actions officielles sont figées par SHA. Le token Actions a Contents Read et,
pour le seul job de sélection, Actions Read afin de vérifier le résultat précédent.
Il n'est pas conservé dans Git et ne sert pas de PAT applicatif. Aucun secret
réel de HESTIA, aucun autre dépôt et aucune compilation Android ne sont nécessaires.
Aucun `pull_request_target`, runner auto-hébergé, accès SSH ou mutation de serveur
externe n'est utilisé. Les runs obsolètes du même événement/ref sont annulés.
Les gros outils navigateur ne sont installés que dans le job qui en a besoin.

## Navigateur natif et limites

`tests/browser_native.py` utilise les routes HTTPS et les assets de production,
le vrai formulaire de bootstrap, les cookies réels, le vrai fetch et le vrai
téléchargement de rapport. Aucun pont fetch, réécriture du HTML, faux Blob ou
assouplissement de la CSP de production n'est utilisé. Certaines réponses d'erreur
et interruptions sont injectées de manière ciblée comme scénarios négatifs.

L'acceptation du certificat auto-signé est limitée au contexte navigateur de test.
Le transport de production n'est pas modifié ; les tests historiques vérifient
séparément les règles TLS. GitHub utilise des fixtures et le préflight du wizard
est contrôlé par des fixtures. Le CLI est exercé réellement sur Debian 12/13.
Aucun téléchargement privé avec PAT utilisateur ni déploiement complet sur VM
n'est revendiqué. Les contrôles d'affichage ne remplacent pas une nouvelle
validation artistique de l'UX déjà figée. Les mesures natives de redimensionnement
attendent que le viewport et les unités CSS dynamiques aient réellement pris leur
taille cible, avant les mêmes assertions strictes de débordement. Aucune tolérance
de dépassement n'est ajoutée et aucun style de production n'est changé. Les
attentes du banc natif utilisent des fonctions JavaScript explicites pour ne pas
dépendre d'un eval de chaîne interdit par la CSP. La CSP n'est jamais affaiblie.

## Preuves, gel et livraison

Chaque suite produit un JSON strict, un JUnit et un manifeste des fichiers sources
avec leur SHA-256 et leurs permissions. Le préflight CLI est inclus dans le
résultat core et exigé par le packaging. Le manifeste est identique avant et après
les tests. Le packaging vérifie les quatre preuves : core Debian 12, core Debian 13,
DOM bridge et navigateur natif. Elles doivent correspondre aux mêmes octets.

Le ZIP de sources est déterministe, conserve les modes Unix, et chaque entrée est
relue puis comparée au manifeste. Aucune création de paquet n'est autorisée avec
des preuves manquantes, un échec ou une source changée après les tests. Les caches,
venv, états temporaires et fichiers Git internes ne sont pas dans le manifeste.
Le ZIP léger fourni avec un lot ne contient que ses fichiers nouveaux ou modifiés,
pas de fichiers .patch. Les suppressions éventuelles doivent être explicites.

Les logs, rapports et captures de fixtures ont une rétention Actions de 7 jours.
Les rapports incluent les résultats et versions, pas de secrets applicatifs.
Le paquet de sources de CI ne remplace pas le futur package applicatif Phase 11.

## Protection de main

Le check à rendre obligatoire dans un ruleset/protection est :
`Installer Quality gate`. L'ajout d'un workflow et son agrégation ne rendent pas
à eux seuls un push Git impossible. La configuration de protection requiert les
droits Administration du dépôt ; le connecteur de ce chantier n'offre pas cette
écriture. Ne pas affirmer qu'une protection serveur est activée sans la vérifier.
La livraison du présent lot ne promeut main qu'après lecture des preuves vertes.

## Extension 5B2.1 : transport privé PHP

La suite core conserve les scénarios historiques et ajoute 36 tests de transport.
Outils de test requis en plus : php-cli, php-mysql, useradd/userdel (paquet passwd),
setpriv/prlimit (util-linux). Ils sont installés seulement dans les conteneurs
Quality ; le lot ne provisionne pas ces dépendances sur une cible applicative.
Les tests créent puis suppriment une identité système non interactive de fixture.
Ils nécessitent donc un environnement root jetable, pas un serveur de production.

Le banc MariaDB opt-in est séparé des tests unitaires sans SQL. Son exécution
sur les fichiers Installer exacts et le Web épinglé est une preuve additionnelle,
pas un remplacement des contrôles Debian, bridge, HTTPS natif et packaging.
Aucun ancien run Web/Installer ne qualifie le nouvel adaptateur. Les campagnes
isolées de recette ne sont pas des branches de reprise ni des sources à fusionner.
Voir [PHASE5B21_PRIVATE_TRANSPORT.md](PHASE5B21_PRIVATE_TRANSPORT.md).

## 5B2.2a - Contrôles SQL locaux et configuration privée

Les contrôles historiques restent requis. La matrice core Debian 12/13 installe
MariaDB uniquement dans ses conteneurs jetables et exécute désormais aussi les
tests SQL de comptes existants. Ceux-ci créent un datadir/serveur isolé et refusent
un port 3306 occupé. Le démarrage du service SQL par défaut des paquets est inhibé
dans ces conteneurs. L'opt-in `HESTIA_ACCOUNT_DB_TEST=1` est obligatoire pour la
commande `./scripts/quality-local.sh` ; sans lui, aucun PASS global n'est produit.

Les ressources PHP privées sont toutes lintées. Le nouvel inventaire couvre
les politiques de GRANT, rôles/PUBLIC, connexion réelle, préparation non active,
permissions sous identités réelles, ACL, liens, secrets, données atypiques,
concurrence, crash, fsync et préservation d'un upgrade refusé. Aucun nouveau
workflow APK ou scénario UI n'est ajouté. Le packaging exact couvre les nouveaux
fichiers comme les anciens ; une mutation après le snapshot invalide le paquet.

Les versions effectivement testées et les nombres finaux sont ceux des rapports
core du commit livré. Le test de connexion via les constantes générées n'est pas
un test du Web complet avec compte DML. Contrat et limites :
[PHASE5B22A_LOCAL_SQL_CONFIGURATION.md](PHASE5B22A_LOCAL_SQL_CONFIGURATION.md).

## Étape 1 résiduelle 5B2.2

331 tests core historiques conservés, 22 nouveaux tests fermés/fichiers : 353 core.
Les 16 tests du gate sont aussi inclus dans core ; ne pas compter deux fois.
16 scénarios DOM et 21 HTTPS natifs restent requis. Pas d'écran modifié.
Le script cross-dépôts tests/integration/database_step_mariadb.py ajoute une
campagne SQL/TLS de 18 scénarios sur le Web épinglé, distincte des mocks core.
Commande, invariants, fixtures et limites :
[PHASE5B22_DATABASE_PREPARATION.md](PHASE5B22_DATABASE_PREPARATION.md).
La promotion exige les preuves de cette campagne en plus des Quality complètes
Installer et Web. Ne pas remplacer cette recette par une ancienne campagne 5B2.1.
Le contrôle de CA respecte l'open_basedir du worker, sans accès supplémentaire
aux ancêtres déjà vérifiés par le parent. Toute erreur PDO/JSON/timeout/crash
reste fermée. DDL partiel et erreur disque après SQL ne sont jamais des succès.

La factory de payload synthétique est extraite sans changement de corps vers
`tests/web_configuration_fixture.py`. Les fixtures SQL ne chargent plus les
fixtures HTTP par transitivité ; aucune assertion historique n’est supprimée.


### 5B2.2 - Synchronisation du viewport dans le banc DOM

Le candidat e5ae0dfa a révélé la récidive du test responsive à 1440x900
(footerBottom=919, rootBottom=920). La sonde locale sans modification CSS/JS a
observé innerHeight=1080 ou900 alors que body.minHeight restait à768px après
les deux requestAnimationFrame. Les unités de viewport CSS n'étaient donc pas
nécessairement actualisées au moment de la mesure.

Le banc DOM attend désormais, comme le banc natif existant, la concordance
innerWidth/innerHeight et body.minHeight avec la taille demandée. L'attente ne
porte jamais sur les valeurs de footer, d'overflow ou de bouton faisant l'objet
des assertions. Les bornes pixels, assertions et timeout existants sont conservés.
Les dix tailles sont parcourues trois fois, et le viewport est aussi synchronisé
avant capture. Aucun asset, CSS, JS ou écran applicatif n'est modifié. Il ne s'agit
pas d'une relance aveugle du run : le banc corrigé et tous les contrôles requis
font l'objet d'une nouvelle qualification sur les nouveaux fichiers gelés.
