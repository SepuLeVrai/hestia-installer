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
