# Quality et non-régression de HESTIA Installer

## Validation ciblée HTTP et collecteur, sans promotion

Le [lot suivant](PHASE5_HTTP_CLEANER_DRAIN.md) ajoute dix tests core et six
scénarios réels de collecteur. Inventaire complet détectable : 650 core.
La sélection affectée compte 74 contrôles ; la recette système compte 47 cas
(18 HTTP, 11 cgroups, 18 collecteur). Un seul job Debian 13, dix minutes maximum,
après vérifications locales. Les tests historiques ne sont pas retirés.

La base `8de010e1` n'avait qu'une validation ciblée (`36221268362`). Les Quality
globales, Debian 12, paquets, SQL/proxy/Web et navigateur restent différés pour
les deux lots. Aucun résultat ancien ne qualifie cet arbre. Les manifestes,
comptages et preuves exactes sont vérifiés avant le checkpoint ; aucune PR de
promotion ni fast-forward avant les campagnes requises sur une source gelée.

## Validation ciblée du drainage HTTP, sans promotion

Le [lot borné](PHASE5_HTTP_DRAIN.md) ajoute 12 tests core, soit 640 détectables
dans la suite complète, sans retirer de tests historiques. À la demande de
Bastien pour réduire le coût Actions, seule la sélection affectée de 64 tests
et 41 scénarios système est prévue dans un job Debian 13. Les gardes statiques
et les 64 tests sont exécutés localement avant tout lancement.

La branche work dédiée ne déclenche ni Quality globale ni paquets. Les six
nouvelles recettes restent dans la suite système permanente pour la prochaine
qualification intégrale. Aucun résultat antérieur ne qualifie ce nouvel arbre.
Debian 12, navigateur, SQL/proxy/Web et campagnes globales sont explicitement
différés. Une validation ciblée verte n'autorise aucune PR de promotion ni
fast-forward. Résultats exacts et limites dans le checkpoint après gel.

## Profil métier externe : qualification du nouvel arbre

[Contrat et neuf scénarios](PHASE5_BUSINESS_STORAGE.md). Les **628 core** par
Debian 12/13 incluent cinq nouveaux cas de frontières. Sont aussi requis :
61 système et 13 paquets par Debian, 16 DOM, 21 HTTPS, les 118 SQL/HTTP,
14 helper proxy et 10 Web historique. Le nouveau Web, qualifié séparément par
`36195113348`, est consommé par neuf scénarios réels supplémentaires : SQL
fresh managed, Apache, PHP-FPM 8.4, TLS, écritures puis restauration de GED,
photo, import et session. Aucun skip ni substitution de source technique.
Le manifeste indépendant lie les deux pins complets et les moteurs SQL inchangés.
Les preuves finales et les incidents éventuels restent dans le checkpoint
compagnon après gel. L'intermittence DOM historique n'est pas déclarée résolue.

## Déploiement protégé — Web réel sous services

[Contrat et recette](PHASE5_WEB_DEPLOYMENT.md) : 623 core par Debian, 61 système
et 13 paquets par Debian, 16 DOM, 21 HTTPS, 118 SQL/HTTP, 14 cas helper proxy
et 10 cas Web réel sous Apache/FPM/TLS. Qualification sur les sources exactes,
sans skip. Login et politique de session ne valent pas activation produit,
stockages GED/photos inscriptibles ni fin de Phase 5. L'intermittence navigateur
historique reste à résoudre. Résultats dans le checkpoint après gel.

## Interface proxy — nouvelle qualification requise

Voir [le contrat et la recette TLS](PHASE5_PROXY_INGRESS.md). 608 core par Debian,
61 système (47 historiques + 14 proxy) et 13 paquets par Debian, 16 DOM, 21 HTTPS,
118 SQL/HTTP et 14 recettes proxy avec le helper Web exact sur Debian 13.
Aucun skip, pin assoupli ou booléen d'installation accepté. Le banc est jetable,
hors réseau après construction ; il ne qualifie pas un certificat public ACME
ni une activation métier. Preuves exactes dans le checkpoint après gel.

## Lot paquets officiels — candidat à qualifier

[Contrat](PHASE5_SYSTEM_PACKAGES.md) : **600 core par Debian**, dont 24 nouveaux
cas obligatoires. Conserver les 47 scénarios système par Debian et ajouter
13 cas paquets réels par Debian (5 acquisition, 7 installation sans réseau,
1 échec de reçu après dpkg). Conserver 16 DOM, 21 HTTPS et 118 SQL/HTTP,
zéro erreur/échec/skip. Tous les manifests et ZIP doivent désigner les mêmes
octets gelés ; les résultats antérieurs ne qualifient pas le candidat.

Trois workflows permanents : quality.yml, system-runtime.yml et system-packages.yml.
Le gate principal couvre core/DOM/HTTPS et le ZIP source. Une PR/promotion exige
aussi les deux autres workflows et les huit recettes SQL/HTTP sur ce même code.
Les recettes paquets utilisent des conteneurs jetables amd64 avec réseau pour
l'acquisition officielle, puis sans interface externe pour dpkg et la reprise.
Aucun serveur métier n'est concerné. Rapports finaux dans le checkpoint compagnon.

## Lot identité système dédiée — candidat à qualifier

[Contrat](PHASE5_SERVICE_IDENTITY.md) : 18 nouveaux tests core obligatoires,
576 par Debian 12/13. Système : 47 cas par Debian (11 drainage, 12 runtime,
12 collecteur, 12 identité), dont concurrence, interruption réelle après useradd
et consommation du compte par les services. Conserver 16 DOM, 21 HTTPS et
118 SQL/HTTP sans skip, sur sources exactes et stables. Les vrais comptes sont
créés uniquement dans le conteneur jetable explicitement autorisé. Résultats
finaux dans le checkpoint après gel documentaire, sans retouche de l’arbre.

## Lot collecteur de sessions — candidat à qualifier

Le [collecteur dédié](PHASE5_SESSION_CLEANER.md) exige 558 core par Debian 12/13,
35 recettes système par Debian (11 drainage + 12 runtime + 12 collecteur),
16 DOM, 21 HTTPS et 118 SQL/HTTP. Les sources doivent être stables, sans skip.
Le timer accéléré est une fixture explicitement distincte du calendrier produit
5min/30min ; aucune assertion de sécurité n'est supprimée. Les résultats finaux
sont dans le checkpoint compagnon. L'intermittence DOM historique reste non résolue.

## Lot runtime Apache/FPM — candidat à qualifier

Base `e86ecd7c` : Quality `36142987500`, système `36142986888`, SQL `36143203828`
verts. Le [runtime privé généré](PHASE5_HTTP_RUNTIME.md) ajoute 18 tests core :
**542 par Debian 12/13**. Le workflow système doit réussir **11 drainage + 12
runtime** sur chaque Debian, avec manifestes de sources stables distincts.
Les 16 DOM, 21 HTTPS et 118 SQL/HTTP restent obligatoires sur le commit exact.
Aucun skip, ni ancien résultat reporté comme preuve du nouveau candidat.
Les résultats après gel documentaire figurent dans le checkpoint compagnon.

## Lot barrière systemd - candidat à qualifier

Base inventaire `ed7707c` qualifiée : Quality `36139574996`, SQL `36139636592`.
Le [lot système](PHASE5_SYSTEM_DRAIN.md) ajoute 18 core obligatoires :
**524 par Debian 12/13**. Le workflow supplémentaire `system-runtime.yml` est
requis : onze scénarios avec systemd PID 1, Apache, FPM et cgroup v2 par Debian,
sans skip, sources stables. Il ne qualifie pas le Web complet sous PHP 8.2.
Les 16 DOM, 21 HTTPS et 118 SQL/HTTP restent requis sur le commit gelé.
Le gate Quality historique seul ne suffit donc pas à promouvoir ce candidat.
Les résultats finaux sont dans le checkpoint compagnon, après gel des docs.

## Lot inventaire - candidat à qualifier

Base coordonnée d2f2d0a qualifiée par Quality36136210321 et SQL36136276965.
Le [lot inventaire](PHASE5_STORAGE_INVENTORY.md) ajoute 18 tests obligatoires :
506 core par Debian 12/13, plus 16 DOM et 21 HTTPS inchangés. Huit nouvelles
recettes PHP/SQL/HTTP complètent les 110 historiques : 118 attendues sans skip.
Les tests multipart et descendant orphelin prouvent deux limites du seul guard
PHP ; ils ne sont pas une réussite de raccordement système. Ce dernier reste
un gate bloquant. Les résultats finaux correspondent uniquement au commit gelé.


## Lot court coordonné - candidat en qualification

Base données `a19c40318dfac958330fda8890116c6580e3ae43` : Quality
`36133121054` verte, 478 core par Debian 12/13, 16 DOM et 21 HTTPS.
Le lot courant ajoute dix tests core obligatoires : **488** par Debian.
Il exige également les 100 scénarios SQL/HTTP historiques et dix nouveaux
scénarios réels de [coordination](PHASE5C2_COORDINATED_BACKUP.md), soit **110**.
Aucun skip, erreur ou échec autorisé ; les 16 tests du gate sont inclus dans core.
Les résultats de ces fichiers gelés seront livrés dans le checkpoint compagnon.
Les sections suivantes sont des états historiques, pas une qualification du lot.

## Lot court données modifiables - candidat en qualification

Le checkpoint réparation `726757eeb139818a7aa93b90586091ff37a08f85` a terminé
ses deux campagnes : Quality `36126405630` verte (456 core par Debian 12/13,
16 navigateur, 21 HTTPS) et SQL/HTTP `36126448323` vert (100 scénarios sans skip).
Les 16 tests du gate sont inclus dans core, pas ajoutés comme tests uniques.

Le lot courant ajoute 22 tests obligatoires à core : cible attendue 478 par
Debian. Contrat et limites : [données modifiables](PHASE5C2_DATA_FILES.md).
Son code et ses docs doivent être figés avant la nouvelle Quality ; les résultats
seront référencés dans le compte rendu compagnon sans retouche après gel.
Les anciens scénarios SQL/HTTP restent historiques pour ce lot indépendant.


## Candidat WORK 5C2b

Checkpoint maintenance `3d7440157a3251b54be820d1af5acaa8794a02f6` : Quality
`36125259959` PASS et les cinq jobs de `36125303870` PASS. Artefacts relus :
18 préparation, 21 finalisation, 15 précontrôles, 30 backup et7 maintenance,
zéro erreur, échec ou skip. Le nettoyage SQL borné et vérifié résout le problème
de banc détaillé ci-dessous. Neuf recettes [réparation](PHASE5C2_REPAIR.md) sont
ajoutées dans le candidat suivant, qui nécessite sa propre qualification.

Checkpoint secours `6cd77cac61c2f5c7256ad320e3bb3f5b954b7910` : Quality permanente
`36124073968` verte. Recette technique `36124119321` : 30 backup, 21 finalisation,
15 précontrôles PASS ; préparation SQL rouge sur le seul teardown du cas DDL
partiel, après ses assertions réussies. Le contrôleur de nettoyage jetable
dépasse cinq secondes pour DROP DATABASE. Son budget de destruction est séparé
à 30 secondes avec vérification d'absence, sans changer les délais produit ni
ceux des autres sondes. Le prochain gel doit repasser toutes les suites.
La [maintenance](PHASE5_MAINTENANCE.md) ajoute sept scénarios réels en préparation.

Checkpoint `12a7e8a045b4f62c22b314173ea0900f512f52de` : Quality `36123060501`
PASS, sources stables, 456 core par Debian12/13, 16 DOM et21 HTTPS, zéro échec,
erreur ou skip. Les 18 SQL/TLS, 21 finalisation et15 précontrôles sont relus PASS
dans les artefacts du run technique `36123155845`, ainsi que les 24 backup PASS.
L'extension [secours](PHASE5C2_RESCUE.md) ajoute ensuite six recettes SQL : elle
n'est pas couverte par ces résultats antérieurs et attend sa propre campagne.

Le [correctif DEFINER](PHASE5C2_DEFINER.md) conserve les suites historiques et
ajoute quatre recettes SQL réelles (24 backup au total). La qualification du
candidat est en cours ; les preuves 5C2a ci-dessous ne qualifient pas le correctif.
Le vérificateur indépendant `tests/integration/source_pins.py` exige l'arbre Web
exact et les cinq empreintes de contenu, sans adaptation automatique des pins.

## Frontière couverte actuelle - 5C2a

Contrat : [PHASE5C2_BACKUP.md](PHASE5C2_BACKUP.md). Sauvegarde privée bornée,
restauration SQL sur serveur jetable sans TCP et relecture réelle des fichiers.
Ni upgrade, ni retour arrière vers la source, ni réactivation HTTP ou écrans.
5C2b reste requis : la recette reproduit le DEFINER orphelin du fresh managed,
erreur1449, et vérifie le refus de certification sans réparation implicite.
Les tests des étapes précédentes restent conservés, avec cette réserve nouvelle.

Baseline : **456 core attendus, 415 conservés +41 nouveaux**. Les 16 tests du gate
sont inclus dans core, pas à additionner. 16 DOM et21 HTTPS natifs inchangés.
20 nouveaux scénarios SQL/TLS/HTTP indépendants, plus18 SQL/TLS,21 finalisation,
15 précontrôles historiques. Le core teste les frontières simulées explicitement ;
les restaurations, triggers, FK, flottants et verrous SQL sont testés réellement
dans la recette opt-in séparée. Un refus attendu de managed n'est pas un PASS
fonctionnel de ce parcours ; il matérialise une limite à résoudre dans5C2b.

La recette positive compare les données et DDL relus, restaure les cinq triggers,
exécute leurs effets avec quatre droits DML et contrôle les FK. Elle couvre aussi
Unicode, long texte, BIGINT maximal, FLOAT/DOUBLE, binaires/NUL, doublons, champs
vides, TLS, altération, disque, décès de processus et libération du verrou global.
Les sessions HTTP existantes sont préservées sur la source ; leurs fichiers
externes et une réactivation HTTP du clone ne sont pas déclarés restaurés.

Web inchangé46c03060625d4d53c675474b11aaa33007d9aad7 : pas de nouveau run Web
revendiqué. Aucune évolution schema.sql/install.php/migrations/seeds/version/UI.
Les versions exactes et les résultats des campagnes finales sont dans les preuves,
pas déduits du seul document. PHP8.4.24/MariaDB11.8.6 local ne vaut pas matrice
SQL/TLS exhaustive ; les Quality core Debian12/13 et navigateur restent requises.
Sources, docs et modes gelés avant la campagne finale. Tout changement ultérieur
impose une nouvelle qualification. Un dump ou un hash seul ne suffit jamais.

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

Le workflow principal est `.github/workflows/quality.yml` ; les workflows système
et paquets ci-dessus sont également requis pour ce lot.

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
