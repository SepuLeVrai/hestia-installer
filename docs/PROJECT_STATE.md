# État du projet

## 2026-09-21

Dépôt initialisé.

- issue parent R4 : #1 ;
- architecture Python standard library initialisée ;
- preflight local non destructif disponible ;
- validation FQDN / CIDR initialisée ;
- aucune mutation système implémentée ;
- aucune GitHub Action activée.

## 2026-09-23 - UX figée et acquisition GitHub

- UX du mini-web figée ;
- l'écran Bienvenue devient le préambule `0` ;
- l'étape `1` est réservée à l'accès GitHub en lecture seule ;
- l'installer reste lightweight et télécharge les composants HESTIA dans un staging privé au lieu de les embarquer ;
- le credential GitHub est éphémère et exclu du state, des logs, des URLs, des arguments de processus et des rapports ;
- l'acquisition via API GitHub + standard library Python est privilégiée afin de ne pas imposer `git` au bootstrap.

## 2026-09-23 - Phase 1 bootstrap HTTPS

La première frontière exécutable est implémentée et testée :

1. préflight Debian 12/13, root, Python 3.11+, OpenSSL et iproute2 ;
2. détection des IPv4 et politique fail-closed pour les IP publiques ;
3. port aléatoire `57000-57999` réservé par socket réel ;
4. certificat TLS éphémère avec SAN IP ;
5. code bootstrap one-shot, TTL et limite de tentatives ;
6. mini-web HTTPS avec écran de déverrouillage cohérent avec l'UX figée ;
7. session `Secure`, `HttpOnly`, `SameSite=Strict` et CSRF ;
8. CSP, `no-store`, Host/Origin checks, limites de requête et sécurité des chemins statiques ;
9. nettoyage du staging et contrôle de fermeture du port ;
10. tests collision, IP publique, multi-interface, restart, TLS réel et authentification HTTPS.

Aucune mutation HESTIA, SQL, Apache, NGINX, Gateway ou APK n'est effectuée. Aucun changement de `schema.sql` ou `install.php` n'est nécessaire pour cette phase.

## Prochaine frontière

Phase 2 - issue #4 : moteur `prepare -> plan -> apply -> validate -> commit -> rollback`, journal atomique non secret, idempotence, resume et rollback.
