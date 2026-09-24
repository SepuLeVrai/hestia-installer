# Quality et non-régression de HESTIA Installer

## Frontière couverte

Le socle actuel s'arrête à l'acquisition de sources. Les contrôles fresh/upgrade,
reprise, retry et rollback concernent le bootstrap, le journal et l'acquisition.
Ils ne constituent pas une validation d'installation SQL, Web, Gateway ou APK.
Les adaptateurs des phases suivantes doivent ajouter leurs vrais tests système.

## Commandes reproductibles

Sur un environnement Debian de test jetable avec les outils requis :

```bash
./scripts/quality-local.sh
./scripts/quality-wizard.sh
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
de dépassement n'est ajoutée et aucun style de production n'est changé.

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
