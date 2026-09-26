# Phase 5 — préparation privée du runtime Apache/PHP-FPM

## Contrat distinct d'arrêt

Le [drainage HTTP](PHASE5_HTTP_DRAIN.md) utilise le provisionnement vérifié après
activation. L'observation de staging conserve ses exigences initiales ; la
factorisation privée des contrôles de configuration ne délivre aucun état
runtime et n'assouplit pas les reçus existants.

## Extension opt-in : stockages métier et gate SQL commun

Le [profil externe](PHASE5_BUSINESS_STORAGE.md) ajoute `external_uploads=True`
et `maintenance_directory` conjointement, uniquement pour Debian 13/PHP 8.4
avec le nouveau Web exact. Le slot doit porter son sceau finalisé et le même
identifiant d'instance. Le gate existant ou une préparation partielle est refusé.
Le profil historique reste inchangé. Six répertoires de données sont alors
créés, dont `data/uploads`, et seuls quatre alias d'images publiques sont exposés.
Les `.htaccess` du code restent root et participent toujours au condensat.
Cette extension ne démarre aucun service et ne certifie pas les producteurs hôte.

## Base et portée

Base qualifiée : `e86ecd7cfabebcfcb141ee2cc66ca9a27eec814d`, arbre
`b3740470259a21c3eb6913416a3633bbaa85238b`. Quality `36142987500`, système
`36142986888`, SQL/HTTP `36143203828` verts : 524 core par Debian 12/13,
11 recettes système par Debian, 16 DOM, 21 HTTPS et 118 SQL/HTTP, sans skip.

`installer.http_runtime` ajoute un **staging privé sous maintenance** de deux
vrais services dédiés. Le compte système, les paquets et le code doivent déjà
exister ; ils ne sont ni créés ni adoptés par cet adaptateur. Aucun service n'est
démarré, aucun fichier SQL n'est exécuté, aucun registre public ou écran ne change.
L'activation Web et la fermeture de la Phase 5 ne sont pas livrées par ce lot.

## Profil fermé

- Debian 12 avec PHP-FPM 8.2 officiel ; Debian 13 avec PHP-FPM 8.4 officiel.
  **PHP 8.2 ne satisfait pas l'exigence PHP >= 8.3 du Web** : le profil Debian 12
  qualifie cette infrastructure seulement. Aucun dépôt tiers ni backport supposé.
- Racine nouvelle sous `/var/lib`, code existant sous `/srv` ou `/var/www`, chemins
  disjoints, sans lien ni ancêtre modifiable. Code root, lisible par le service,
  immuable : pas de stockage métier modifiable dans cette arborescence à ce stade.
- Identité non-root préexistante, sans shell de connexion, UID et groupe primaire
  exclusifs, sans groupe supplémentaire. Ni `www-data`, ni `nobody`.
- systemd PID 1, cgroup v2, port IPv4 loopback libre entre 1024 et 65535, FQDN
  explicite. Les unités et leurs drop-ins doivent être absents, y compris les
  unités déjà chargées ou fournies par le système avec les mêmes noms.
- Binaires, extensions PHP et modules Apache dans les emplacements Debian fixes,
  root et protégés. Leur contenu, le code et les configurations entrent dans le
  condensat du plan privé ; une dérive invalide l'observation du staging.

Les chemins, identité et FQDN restent dans le journal privé. Le rapport public
ne contient que l'état, les compteurs, le condensat non secret et les limites.
Entrées invalide, erreur système ou sortie inattendue donnent un code fermé,
sans recopier de stdout/stderr ni d'exception contenant des données privées.

## Ressources produites

| Ressource | Propriétaire / permissions | Usage |
| --- | --- | --- |
| Racine, `run`, `data` | root:groupe dédié, 0750 | Traversée contrôlée |
| `conf`, `log` | root:root, 0700 | Configurations et journaux des maîtres |
| `data/sessions`, `tmp`, `upload-tmp`, `imports`, `log` | identité dédiée, 0700 | Données privées PHP |
| Configurations et journaux de staging | root:root, 0640 | Preuve de préparation |
| Deux fragments systemd et leurs conditions | root:root, 0644 | Apache/FPM propres à l'instance |

La création est exclusive, avec dirfds protégés, refus d'ACL/liens/hardlinks,
fsync et absence de chmod/chown récursif sur des ressources existantes. Aucun
vhost global, pool `www`, `php.ini` système ou script Debian de nettoyage n'est modifié.

PHP-FPM utilise son INI explicite, sans répertoire global de scan ni `.user.ini`,
ses extensions explicites, un socket Unix 0600 et un pool dédié. Le guard de
maintenance est forcé en `php_admin_value`, ainsi que les chemins effectifs des
sessions et temporaires, leur durée de 43200 secondes et le mode strict.
`TMPDIR/TMP/TEMP/HOME` et `HESTIA_IMPORT_STORAGE` pointent sur les répertoires
privés. Le GC probabiliste des sessions est désactivé : le nettoyage natif dédié
reste un chantier requis **avant activation produit**.

Apache écoute exclusivement sur loopback, vérifie le Host, transmet Authorization
au FastCGI, refuse les chemins privés et le PHP sous uploads, et borne la réception
des corps HTTP. `AllowOverride None` évite de reprendre le proxy privé codé dans
le `.htaccess` historique. Les types MIME et en-têtes de sécurité sont explicites.
Ce profil n'est pas une qualification du reverse proxy TLS, des routes mobiles
internes ou de tous les parcours fonctionnels Web.

Les unités ont `Restart=no`, `KillMode=control-group`, `SendSIGKILL=yes`,
`Delegate=no`, UMask 0077 et un unique drop-in
`ConditionPathExists=!<maintenance>/maintenance.attempt`. Leurs noms dérivent
uniquement de l'identifiant d'instance. Les commandes produites sont des argv
fixes : tests de syntaxe FPM/Apache puis `systemctl daemon-reload`, jamais start.

## Interruption et observation

1. Contrôles sans écriture, puis réservation exclusive de la racine.
2. `provision.attempt` durable lie le profil et les condensats attendus.
3. Répertoires dédiés, scope et **maintenance durable avant toute unité**.
4. Création exclusive des configurations, fragments et conditions ; tests réels
   de syntaxe ; rechargement de la définition des unités.
5. `staged.json` lie le condensat et la lease de maintenance. L'observation
   contrôle à nouveau fichiers, dépendances, permissions, gate, propriétés
   chargées, absence de jobs/PID et cgroups vides.

Un échec après réservation conserve l'empreinte et, dès sa création, la
maintenance. La présence d'un répertoire, d'un fichier ou d'un journal partiel
ne déclenche ni écrasement, ni nettoyage, ni rejeu automatique. L'opération typée
`web.http-runtime.stage` récupère une réponse perdue uniquement si toute la preuve
de staging est encore observable ; sinon décision `MANUAL`. Aucun rollback
automatique ne supprime des données ou unités. L'observation n'exécute ni test
de syntaxe ni daemon-reload : elle reste en lecture et lie les tests initiaux
aux octets inchangés de leurs entrées.

Après reprise d'activité, la preuve `HTTP_RUNTIME_STAGED` n'est plus valable.
`http_bindings()` fournit uniquement les deux empreintes Apache/PHP observées.
La barrière `SystemDrain` exige toujours ses quatre rôles ; cet adaptateur ne
fabrique pas de service CLI ou de nettoyeur vide pour prétendre la compléter.

## Qualification requise

18 tests core supplémentaires portent le total à **542 par Debian**. Ils couvrent
grammaire, identités, absence de consentement, commandes, source immuable,
permissions/liens/FIFO, erreurs privées, validation et récupération sans rejeu.

Le workflow permanent `system-runtime.yml` conserve les **11 recettes de drainage**
et ajoute **12 recettes du runtime généré par Debian 12/13** :

1. Staging fermé, permissions exactes, start refusé par la condition, globals intacts.
2. Chemins PHP effectifs, session 43200, extensions, verrouillage des directives.
3. Host, routes privées, code uploads, MIME et `.htaccess` ignoré.
4. Multipart réel dans le temporaire privé, puis nettoyage par PHP.
5. Collision d'unité, sans réservation ni écrasement.
6. Port occupé, sans création de ressources.
7. Racine existante vide non adoptée.
8. Écriture interrompue, journal et maintenance conservés, reprise manuelle.
9. Réponse apply perdue, récupération en lecture sans reload.
10. Dérives code/configurations/permissions/dépendances refusées.
11. Guard PHP réel ferme l'admission sans prétendre les services drainés.
12. Deux unités générées combinées avec les deux autres rôles explicitement
    **fixtures**, drainage cgroup réel et sessions préservées.

Seul le banc reprend explicitement l'activité et démarre les endpoints PHP
synthétiques pour les recettes. Ce démarrage n'est pas une fonction produit.
Le conteneur jetable systemd conserve son cgroup privé et aucun accès réseau.
Les dépendances viennent des paquets officiels. Tous les rapports exigent zéro
erreur/échec/skip et les mêmes sources avant/après. Les **16 DOM, 21 HTTPS et
118 SQL/HTTP historiques** restent requis sur le commit exact. Les résultats
finaux sont consignés dans le checkpoint compagnon après gel documentaire.

## Frontières restantes

Création de l'identité et installation des paquets contrôlées, configuration
TLS/proxy, nettoyeur dédié et autres producteurs, activation typée et preuve du
Web réellement servi, cartographie exhaustive des données (y compris celles
sous le Webroot), sauvegarde complète 5C2, vraie transition 5C3, rollback/recovery
5C4 et wizard 5D. Les flags `system_wiring_verified`, `application_installed` et
`complete_web_backup` restent faux. Pas de PR/fast-forward avant l'ensemble des
Quality requises et la documentation achevée ; aucun accès production/LAB-PAWEB30.

## Diagnostic de la première campagne Debian 13

Le candidat `f2432979` passe Quality `36147225337` et les 23 recettes système
Debian 12. Le système `36147225231` échoue côté Debian 13 avant réservation.
Le diagnostic ciblé `36147579492` confirme les préconditions hôte, puis un refus
dans le lecteur de dépendances : le budget 8 Mio des sources PHP avait été
réutilisé à tort pour les extensions système, dont une dépasse cette taille.
Le lecteur système possède désormais sa propre borne de 32 Mio, avec les mêmes
refus de liens, droits non protégés et lecture instable, plus refus des ACL et
empreinte des permissions/groupe. Le budget des sources et les pins historiques
ne changent pas. Deux régressions supplémentaires couvrent un binaire de 9 Mio,
le dépassement de 32 Mio et l'intégrité. La taille des extensions officielles
est enregistrée par le banc ; toutes les Quality doivent être rejouées sur le
nouveau commit exact. La branche diagnostic est technique et ne doit pas être fusionnée.
