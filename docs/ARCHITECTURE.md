# Architecture HESTIA Installer

## Lecteur configuré sans projection de pertinence

[SystemdExecutionContext](PHASE5_EXECUTION_CONTEXT_READER.md) compose le census/
relations avec un transport Service à20 propriétés fixes. Deux passages, détails
regroupés par service mais mappings de tous les leaders conservés. Non-Service
lié : properties=null. La preuve enrichit le digest précédent par le contexte,
puis relie les mêmes faits à l'index obtenu. Le modèle ne reçoit aucun nouveau
signal d'identité/chemin et les primitives parentes restent inchangées. Pas de
nouveau endpoint, wizard, contrôle de service ou reçu durable.

## Contrat du prochain lecteur de contexte configuré

Le [profil Service proposé](PHASE5_SYSTEMD_EXECUTION_CONTEXT.md) compose le
census/relations existant sans nouveau consommateur de mutation.20 propriétés,
deux lectures par service distinct lié, FD encore ouverts, comparaison exacte,
budget24+8N+18M+40S avec S<=M<=N<=128. La preuve privée doit lier le bloc de
configuration à l'index, au census, aux relations et à tous les leaders.
Aucun code de ce lecteur n'est livré ici. Sa première version conservera les
textes configurés sans les convertir en faits numériques/effectifs ou chemins
résolus ; la sélection existante ne gagne pas de signal inventé. Les commandes,
unités sans processus et autres types restent hors de cette première lecture.

## Relations et signaux du census vivant

[systemd_census_relations](PHASE5_CENSUS_RELATIONS.md) étend la liaison privée
par deux lectures de propriétés par unité distincte. Le helper de détails du
lecteur parent est partagé. Le modèle pur ajoute CensusCandidateFact, distinct
d'IdentityFact et d'ExternalBinding : raisons fermées, leader/contextes et digests
du census/binding. L'index lie tous les leaders, pas seulement le représentant.
Une raison inconnue ne démarre pas l'expansion du graphe ; les raisons positives
élargissent uniquement la revue. Aucun consommateur de mutation ou wizard ajouté.

## Liaison des candidats pendant la collecte

[systemd_census_invocations](PHASE5_CENSUS_INVOCATIONS.md) compose le census
et le transport PIDFD par trois hooks privés de cycle de vie. Les FD restent
possédés pendant les deux lectures D-Bus et jusqu'aux contrôles finaux procfs.
La confirmation d'invocation est partagée avec le lecteur parent. Entrée cible/
stockage validée uniquement ; aucun callback, PID ou reçu historique accepté.
Plusieurs leaders par objet sont conservés ; aucune appartenance des autres
threads n'est inférée. Le modèle pur, les relations et le wizard restent
inchangés ; le résultat sert à la revue, sans consommateur de mutation.

## Recensement privé des tâches visibles

[process_census.ProcessCensus](PHASE5_PROCESS_CENSUS.md) ajoute un collecteur
procfs indépendant, sans client D-Bus ni mutation. Topologie, TaskRecord et
candidats sont encapsulés dans CensusSample ; les PIDFD_THREAD sont locaux et
fermés au retour. Les signaux portent sur chaque tâche et les liens parents
observés. Le raccordement aux invocations doit encore revalider leur fraîcheur.
Les modèles purs et lecteurs runtime précédents restent inchangés.

## Observation procfs liée aux invocations

[SystemdProcessIdentity](PHASE5_PROCESS_IDENTITY.md) spécialise le lecteur de
relations et son hook privé de clôture. process_identity lit seulement un leader
lié à notre PIDFD, avec son propre budget sous le délai commun. ProcessIdentity
est conservée dans la liaison privée, puis UID/GID effectifs et groupes alimentent
RelevanceFacts. Le modèle pur reste conservateur et inchangé ; aucune interface
publique, mutation, identité configurée ou résolution de chemin n'est ajoutée.

## Relations liées aux invocations

`systemd_invocation_relations.SystemdInvocationRelations` étend le lecteur minimal
avec Names et huit tableaux de relations. Les points privés budget/pass/sample
factorisent le cycle de PIDFD sans changer le parcours minimal. Le [contrat](PHASE5_SYSTEMD_INVOCATION_RELATIONS.md)
décrit la scan enrichie, la validation des alias et le RelevanceFacts lié à son
digest. Aucun modèle pur ou consommateur de mutation modifié. Le résultat reste
partiel, sans signal métier acquis. Les sections suivantes conservent leurs états historiques.

## Adaptateur minimal d'invocation

`systemd_invocation.SystemdInvocationTransport` hérite des tours de listes et
ajoute deux liaisons privées PIDFD/Id/InvocationID sur chemin canonique d'invocation.
Le [contrat livré](PHASE5_SYSTEMD_INVOCATION_BINDING.md) conserve un budget commun
24+8N, N<=128, et le FD local entre les passages. Les modèles purs, endpoints,
projections et opérations de mutation sont inchangés. Aucun recensement de PID
ni contexte effectif déduit. Sections de conception suivantes historiques.

## Contrat futur de liaison d'invocation

Le [protocole PIDFD](PHASE5_SYSTEMD_INVOCATION_CONTRACT.md) acquiert l'ID par
Manager.GetUnitByPIDFD et choisit le chemin par GetUnitByInvocationID. La première
implémentation proposée ne lirait que Id/InvocationID, deux fois et avec des
PIDFD possédés bornés. Aucun nouveau composant exécutable dans ce lot. La source
des PID et la couverture des unités sans processus restent distinctes, sans
admission au profil, identité effective ou résultat d'inventaire exhaustif.

## Acquisition des détails suspendue après preuve négative

Le [lecteur expérimental de propriétés](PHASE5_SYSTEMD_PROPERTY_AUTOLOAD.md)
est rejeté : la résolution interne d'un chemin Unit nommé peut charger une
unité disparue. Le code de ce candidat est celui de 7a331ce3, sans nouvel
adaptateur ni budget élargi. Les modèles purs conservent leurs faits déclarés ;
les trois listes restent observables par leur transport qualifié. La méthode
d'acquisition de Names/relations doit être révisée avant toute implémentation.

## Adaptateur privé des listes du manager

`systemd_discovery_transport.SystemdDiscoveryTransport` effectue 24 appels
busctl call fermés : identité du bus/broker/manager, Version/UnitPath et deux
tours des trois listes. JSON strict, budgets pendant lecture, précontrôle du
broker actif et provenance recontrôlée. Il retourne scan/index et reçu privé.
Le [contrat](PHASE5_SYSTEMD_DISCOVERY_TRANSPORT.md) distingue observation des
listes, cible/stockage déclarés et absence de preuve des détails d'exécution.
Aucun raccordement aux mutations ni changement des modèles purs.

## Sélection déclarative privée de pertinence

`systemd_relevance.SystemdRelevance` revalide la DiscoveryScan puis lie les
faits typés à son index entier par digest. Aucun IO, horloge implicite ou
modification du modèle de découverte. Les signaux d'identité, chemin, liaison
externe et relation produisent RELATED_UNMANAGED ou UNRESOLVED. La propagation
élargit seulement la revue. Fichiers, jobs et inconnus restent dans le résultat.
Le [contrat détaillé](PHASE5_SYSTEMD_RELEVANCE_MODEL.md) conserve tous les blocages.
Pas de pont provisionneur, projection, endpoint ou consommateur de mutation.

## Index privé des trois populations systemd

`systemd_discovery.SystemdDiscovery` réutilise le contrat cible/stockage de
LauncherInventory, puis valide deux tours déclarés d'unités, fichiers et jobs.
Son enveloppe privée accepte jusqu'à 4096 noms/4Mio ; elle ne projette pas les
objets en lanceurs et n'en déduit aucune pertinence. Source, provenance et
horloges restent déclarées. Voir [types et limites](PHASE5_SYSTEMD_DISCOVERY_MODEL.md).

## Découverte élargie : contrat sans transport

Le [contrat hors profil](PHASE5_SYSTEMD_SCOPE.md) sépare index des unités
chargées, fichiers installés et jobs, et conserve les inconnus hors de la
projection limitée à 128 lignes. Trois décisions privées, aucun enrôlement.
Le futur protocole évite LoadUnit et les lectures globales de contexte sensible.
Son premier développement sera un modèle pur ; le collecteur actuel reste fermé.

## Observation privée des unités provisionnées

`SystemdObserver` relit les contrats immuables, puis des propriétés sélectionnées
via systemctl show. Deux services, ou trois services et leur timer, constituent
un périmètre partiel. `SystemdSample.snapshot(target)` relie conservativement
le profil métier scellé au modèle pur ; aucun mutateur n'en consomme le résultat.
Voir [provenance, temporalité et limites](PHASE5_SYSTEMD_OBSERVATIONS.md).

## Déclarations privées des lanceurs

`launcher_inventory.LauncherInventory` consomme une cible et les besoins de
stockage, puis valide des observations typées sans IO. Sa comparaison lie
cible, provenance déclarée, définitions, chaîne et états ; son résultat est
un manifeste de besoins avec blocages, jamais une lease. Voir
[le contrat et ses limites](PHASE5_LAUNCHER_OBSERVATIONS.md). Le collecteur
système et le raccordement aux mutations restent séparés.

## Composition HTTP et collecteur

`HttpDrain(runtime, cleaner=collector)` ajoute le collecteur vérifié du même
runtime et son timer à la barrière. Le contrôle privé de configuration conserve
la preuve du staging original, tandis que l'audit vivant vérifie les trois
services et le timer arrêté. Le profil durable distingue cette composition du
profil HTTP seul. Voir [le contrat](PHASE5_HTTP_CLEANER_DRAIN.md). Aucun
orchestrateur global, redémarrage ou raccordement au wizard n'est ajouté.

## Arrêt HTTP indépendant de l'activation

`http_drain.HttpDrain` compose les contrôles de configuration privés de
`HttpRuntime`, la maintenance durable et l'audit cgroup de `SystemDrain` pour
les seuls rôles Apache et PHP. Un recensement procfs des threads invalide la
preuve si l'identité dédiée est observée hors périmètre. La lease reste vivante,
recontrôlée et non sérialisable. Le staging n'est pas réutilisé comme reçu de
runtime. Voir le [contrat et sa qualification ciblée](PHASE5_HTTP_DRAIN.md).

## Profil métier externe et identités de source

`web_releases.py` contient deux identités fermées (commit, arbre, nombre de
fichiers, digest runtime). Déploiement, finalisation, lecture d'upgrade,
sauvegarde, réparation et inventaire propagent le choix explicite sans accepter
le reçu d'un autre pin. Les moteurs SQL restent épinglés indépendamment à leurs
octets qualifiés. Aucun moteur de transition n'est ajouté par ce catalogue.
[Le profil métier](PHASE5_BUSINESS_STORAGE.md) garde le code immuable, crée les
données externes sous l'UID dédié et raccorde un scope de maintenance unique.

## Déploiement protégé avant finalisation

L'adaptateur privé [WebDeployment](PHASE5_WEB_DEPLOYMENT.md) consomme une source
Web complète épinglée et crée exclusivement sa destination root:root ainsi que
son journal hors racine publique. Il précède la finalisation SQL et le staging
HTTP, sans exécuter ni démarrer l'application. Ses reçus ne remplacent pas ceux
de finalisation/runtime : chaque frontière conserve son observation exacte.
La nouvelle recette Web réel assemble ces contrats dans un environnement jetable ;
l'orchestration de l'activation et la conversion des stockages restent ouvertes.

## Responsabilité

`hestia-installer` orchestre les composants HESTIA. Il ne devient pas une copie des trois dépôts applicatifs.

```text
GitHub Project HESTIA
        |
        +-- hestia-nexus-avv       WEB
        +-- hestia-mobile-gateway  GATEWAY
        +-- hestia-apk             MOBILE
        `-- hestia-installer       INSTALLER
```

## Runtime cible

```text
install-hestia.sh
  -> bootstrap minimal HTTPS

orchestrateur Python temporaire
  -> accès GitHub éphémère
  -> acquisition locale des sources
  -> preflight
  -> plan
  -> transactions
  -> modules typés
  -> validations
  -> rapport

mini-web HTTPS temporaire
  -> UX
  -> collecte des décisions
  -> affichage des statuts
  -> aucune primitive shell arbitraire
```

## Phase 1 - Bootstrap HTTPS

Le bootstrap est entièrement basé sur la standard library Python, avec `openssl` et `iproute2` comme commandes externes minimales.

Séquence :

```text
install-hestia.sh
  -> Python isolé (-I) + ajout explicite du répertoire local
  -> preflight Debian / root / Python / OpenSSL / ip
  -> détection IPv4
  -> sélection de la politique de bind
  -> création staging 0700
  -> réservation cryptographique d'un port 57000-57999
  -> génération certificat TLS éphémère
  -> génération code bootstrap one-shot
  -> transfert du socket déjà bindé au serveur HTTPS
  -> saisie du code dans /bootstrap
  -> session navigateur sécurisée
  -> accès à l'UX figée
  -> arrêt
  -> fermeture socket + effacement staging
  -> contrôle du port fermé
```

Le socket choisi est bindé et mis en écoute avant que son numéro soit affiché. Il est ensuite transféré directement au serveur HTTPS, sans fenêtre de libération/réouverture du port.

## Réseau

Les IPv4 sont obtenues via les sorties JSON de `iproute2`.

Politique :

- une IPv4 privée plausible : sélection automatique ;
- plusieurs IPv4 privées : choix interactif, route par défaut présélectionnée ;
- mode non interactif : route privée par défaut ;
- aucune IPv4 privée : bind sur `127.0.0.1` et tunnel SSH recommandé ;
- IPv4 publique explicitement demandée : refus sauf option `--allow-public-bootstrap`.

## TLS bootstrap

Le certificat :

- est recréé à chaque lancement ;
- est valable un jour maximum ;
- contient l'IPv4 de bind dans le SAN ;
- utilise une clé RSA 2048 temporaire ;
- reste dans le staging privé ;
- est supprimé à l'arrêt ;
- n'est jamais réutilisé pour les services HESTIA finaux.

TLS 1.2 est le minimum accepté. La compression TLS est désactivée.

## Session navigateur

Le code bootstrap :

- est généré avec `secrets` ;
- contient 40 bits aléatoires utiles ;
- est dérivé en mémoire par PBKDF2-HMAC-SHA256 ;
- expire après 10 minutes ;
- est invalidé après une utilisation réussie ;
- est verrouillé après 5 échecs.

La session :

- est uniquement en mémoire ;
- utilise un identifiant aléatoire 256 bits ;
- expire après 2 heures ;
- est transmise dans un cookie `Secure`, `HttpOnly`, `SameSite=Strict` ;
- possède un jeton CSRF indépendant pour les mutations futures.

## Acquisition des sources GitHub

HESTIA Installer reste volontairement léger : il n'embarque pas les dépôts applicatifs complets.

Après l'écran de bienvenue, l'étape 1 demande un credential GitHub capable de lire les dépôts privés nécessaires. Le mode recommandé est un fine-grained personal access token limité aux dépôts HESTIA et aux permissions de lecture strictement nécessaires.

Le credential est utilisé uniquement pendant la session pour :

- vérifier l'accès aux dépôts requis ;
- résoudre la branche ou le commit demandé ;
- télécharger localement les sources nécessaires dans le staging privé ;
- enregistrer dans le plan uniquement les SHA de commits et hashes non secrets.

Le téléchargement doit privilégier l'API GitHub et la standard library Python afin d'éviter d'imposer `git` comme dépendance du bootstrap.

## Source de vérité

Le chantier de référence est l'issue [#1](https://github.com/SepuLeVrai/hestia-installer/issues/1).

## Phase 2 - Coeur transactionnel

Le journal persistant est indépendant du staging TLS :

```text
CLI / HTTPS authentifié
  -> TransactionService : routes et arguments fermés
  -> TransactionEngine : plan immuable + confirmation de son SHA-256
  -> OperationRegistry : adaptateurs Python explicitement enregistrés
  -> prepare (lecture) -> apply -> validate (lecture) -> commit
  -> reprise prouvée par recover, ou arrêt MANUAL_ACTION_REQUIRED
  -> rollback ciblé par frontière, en ordre inverse des dépendances

/var/lib/hestia-installer/       0700, persistant
  state.json                   0600, atomique, non secret
  .transaction.lock            0600, inode conservé

/run/hestia-installer/          staging HTTPS éphémère, nettoyé séparément
```

`model.py` conserve `InstallState` et `StepRecord`. Le format persistant est un
schéma fermé distinct ; le dictionnaire libre `StepRecord.details` n'est jamais
sérialisé par le moteur. Le plan contient les ressources, services, ports, chemins,
FQDN, dépendances, backups prévus, avertissements et limites de rollback.

L'écriture utilise un fichier temporaire, `fsync`, `os.replace` relatif à un
descripteur de répertoire privé, puis `fsync` du répertoire. Le verrou couvre toute
l'opération, pas uniquement l'écriture JSON. Un numéro de révision empêche les
écritures obsolètes. Les lecteurs consultent un snapshot ancien ou nouveau complet.

L'orchestrateur ne dépend pas de la connexion du navigateur. Une reconnexion lit
le journal ; un arrêt gracieux attend les opérations actives avant d'effacer les
secrets en mémoire. Un crash brutal laisse le dernier checkpoint durable pour
`recover`. Aucun résultat d'exécution ambigu n'est automatiquement assimilé à un
échec sans effet.

L'UX reste inchangée. Les endpoints sont prêts ; leur branchement aux contrôles
visuels relève de la phase wizard. Le seul adaptateur de production de cette phase
est `preflight.run` (`core`, mode `check`). Son état `DONE` signifie « contrôles core
terminés », pas « HESTIA déployé ». Les adaptateurs qui modifient réellement des
fichiers dans les tests restent exclusivement sous `tests/`.

Le contrat détaillé, les commandes CLI et les exemples HTTP figurent dans
[TRANSACTION_ENGINE.md](TRANSACTION_ENGINE.md).

## Phase 3 - Acquisition GitHub lightweight / issue #8

`github_client.py` fournit le transport GET HTTPS à destinations fermées et
`GitHubAccess` conserve uniquement en mémoire le credential et le résultat de
validation des trois dépôts. Aucun client git ni dépôt applicatif embarqué.

```text
POST github/validate -> Metadata + Contents des 3 dépôts -> snapshot des SHA
POST github/plan -> sélection 1..3 modules -> refs résolues -> plan immuable
POST installation/apply -> confirmation du plan -> github.acquire par module
  -> archive bornée -> extraction contrôlée -> preuve SHA-256 -> commit local
GET installation/state/report -> état non secret, indépendant du navigateur
```

`github_sources.py` reconstruit les adaptateurs exclusivement à partir des modules,
dépôts et chemins autorisés côté serveur. Une reprise conserve le SHA approuvé,
même si une branche distante a avancé. `source_archive.py` matérialise les fichiers
sans les exécuter, avec des limites de contenu, de métadonnées, de chemins et d'inodes.

Les sources restent sous `/var/lib/hestia-installer/sources/<module>-<sha>/`, en
staging privé persistant distinct de `/run`. Le journal référence ce répertoire,
le dépôt, la ref et le SHA ; les preuves locales contiennent les SHA-256 de
l'archive et de l'arborescence. Le rollback ne retire que la frontière créée et
prouvée. Le nettoyage final de ce cache relève de la phase de finition.

Les adaptateurs de production sont désormais `preflight.run` et `github.acquire`.
`DONE` signifie ici « sources acquises et vérifiées », jamais « HESTIA déployé ».
Les boutons du wizard restent inchangés jusqu'à la Phase 4.
Voir [GITHUB_ACQUISITION.md](GITHUB_ACQUISITION.md).

## Phase 4 - Wizard connecté

Les six écrans de l'UX existante pilotent maintenant les routes typées du moteur :
validation GitHub, préflight, sélection, plan, acquisition et suivi/reprise.
`installer/wizard.py` porte le brouillon non secret privé et les contrôles normalisés.
`TransactionService` conserve la sérialisation des mutations et fournit un snapshot
lisible pendant leur exécution. Aucun nouveau serveur ni dépendance runtime.

`wizard.json` conserve uniquement les choix pré-plan ; `state.json` fait autorité
dès la planification. Les sessions, CSRF et credentials restent éphémères. Le contrat
et les confirmations sont décrits dans [WIZARD.md](WIZARD.md). Le résultat est une
acquisition de sources, pas un déploiement Web/Gateway/APK.
