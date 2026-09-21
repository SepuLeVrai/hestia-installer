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
