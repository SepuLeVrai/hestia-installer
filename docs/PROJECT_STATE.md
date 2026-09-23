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

## 2026-09-23 - Phase 2, implémentation locale

Base examinée : `38e956817f1404b78c4608ccd64ac9c7dd15926d`.

Le moteur et sa façade HTTPS sont implémentés : plan immuable inspectable,
confirmation explicite, registre typé, checkpoints persistants, idempotence,
resume conservateur, retry ciblé, rollback par frontière et rapport non secret.
Le journal privé est atomique, verrouillé et protégé contre les symlinks et les
écritures obsolètes. Les états et les erreurs sont des valeurs fermées.

Les tests exercent des mutations réelles dans des sandboxes, des backups réels,
des interruptions de processus après apply, commit et rollback, la concurrence,
la reconnexion HTTPS et la non-régression du bootstrap. Le détail des vérifications
et leurs limites se trouve dans [QUALITY_PHASE2.md](QUALITY_PHASE2.md).

Le seul adaptateur exposé en production est le contrôle `core/preflight.run`.
L'acquisition GitHub, les déploiements Web/Gateway/APK, les mutations SQL et le
branchement des boutons du wizard restent dans les phases suivantes. Aucune
évolution de `schema.sql` ou `install.php` n'est introduite. Aucun asset UI modifié.

Statut de livraison : travaux locaux préparés pour l'issue #4 ; publication sur
`main` et mise à jour de l'issue non réalisées dans l'environnement de préparation,
qui ne dispose pas d'action d'écriture GitHub utilisable. Ne pas interpréter ce
document comme une preuve de commit distant ou de clôture de l'issue.

## Prochaine frontière

Publier et vérifier ce lot Phase 2, puis traiter l'acquisition GitHub lightweight.
Les trois dépôts devront être validés avec un credential de lecture éphémère ;
seuls les composants sélectionnés devront ensuite être téléchargés. Ne pas
brancher un exécuteur de commande arbitraire sur la façade transactionnelle.
