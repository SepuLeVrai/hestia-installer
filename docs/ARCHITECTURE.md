# Architecture HESTIA Installer

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
