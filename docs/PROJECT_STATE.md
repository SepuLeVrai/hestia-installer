# État du projet

## 2026-09-21

Dépôt initialisé.

- issue parent R4 : #1 ;
- architecture Python standard library initialisée ;
- preflight local non destructif disponible ;
- validation FQDN / CIDR initialisée ;
- aucune mutation système implémentée ;
- aucune GitHub Action activée.

## Prochaine frontière

Le premier lot exécutable doit traiter le bootstrap sécurisé :

1. détection IPv4 d'administration ;
2. port aléatoire 57000-57999 ;
3. bind atomique ;
4. certificat TLS éphémère ;
5. token bootstrap ;
6. mini-web HTTPS ;
7. session sécurisée ;
8. arrêt et nettoyage.

Ne pas démarrer les mutations NGINX, MariaDB ou Apache avant validation de cette frontière.

## 2026-09-23 - UX figée et acquisition GitHub

- UX du mini-web figée ;
- l'écran Bienvenue devient le préambule `0` ;
- l'étape `1` est réservée à l'accès GitHub en lecture seule ;
- l'installer reste lightweight et télécharge les composants HESTIA dans un staging privé au lieu de les embarquer ;
- le credential GitHub est éphémère et exclu du state, des logs, des URLs, des arguments de processus et des rapports ;
- l'acquisition via API GitHub + standard library Python est privilégiée afin de ne pas imposer `git` au bootstrap.

La prochaine frontière exécutable reste le bootstrap HTTPS sécurisé, qui devra fournir le canal sûr nécessaire à la saisie de ce credential.
