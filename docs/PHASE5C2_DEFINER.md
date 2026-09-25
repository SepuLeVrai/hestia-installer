# Phase 5C2b - identité durable des triggers

## Candidat en cours de qualification

Base produit : candidat 5C2a `84426c3294e2c5d9b4b4f34fb29fc0dc3d33bc13`,
non promu. Web inchangé : `46c03060625d4d53c675474b11aaa33007d9aad7`.
Ce sous-lot corrige le futur fresh managed et étend la vérification de sauvegarde.
Il ne répare pas encore une installation existante. La Phase 5 reste ouverte.

La reproduction initiale sur Debian 13/PHP 8.4.24/MariaDB 11.8.6 est acquise dans
le run technique Web `36121554719`, sur les sources exactes 5C2a : cinq DEFINER
orphelins, erreur 1449 et refus BACKUP_DEFINER_MISSING. Le précédent run
`36120827721` était bloqué avant SQL par les modes 0664/0775 de l'extraction TAR
root. Le banc a été corrigé avec umask022/--no-same-permissions ; les contrôles
produit et les pins n'ont pas été assouplis. Les branches techniques sont exclues
des promotions et des ZIPs produit.

Premier essai candidat : run technique `36122266655`, quatre nouvelles recettes
PASS et une régression de fixture. La reconstitution historique oubliait
SET NAMES utf8mb4 COLLATE utf8mb4_unicode_ci ; le produit refusait donc le profil
avant de contrôler l'identité absente. La fixture conserve maintenant toutes
les métadonnées et les compare avant/après reconstitution. L'assertion historique
BACKUP_DEFINER_MISSING est conservée. La nouvelle campagne doit confirmer le tout.

## Provisioning futur

Le compte `hdf_` suivi de 24 caractères dérivés de SHA-256 du nom de base est
distinct de l'application, de la migration et de l'autorité. Il est créé sous
`localhost`, sans mot de passe, avec ACCOUNT LOCK dès sa création. Aucune session
ne peut s'ouvrir sous cette identité. Tous ses variants Host préexistants sont
refusés avant la création de base ; aucun compte existant n'est adopté.

Le catalogue fermé `installer/private/trigger_definer.php` accorde TRIGGER sur
quatre tables seulement et les privilèges SELECT/UPDATE/INSERT sur les seules
colonnes référencées par les cinq corps canoniques. `UserInfo` est limité à
`id_user` : aucun droit sur les hashes de mots de passe. Pas de droit global,
de GRANT OPTION, de wildcard de schéma, de CREATE/DROP/ALTER, rôle ou PROXY.
L'audit relit les droits de tables et colonnes, le verrou, l'authentification,
les variantes Host, les droits globaux/de schéma/routines et les rôles/PUBLIC.

Les cinq corps sont issus du schema.sql épinglé. Leur définition, ordre,
collations, mode SQL et ancienne identité sont vérifiés avant remplacement.
L'autorité effectue le rebind ; le compte de migration n'obtient jamais SET USER
ou un autre droit global. Après remplacement et audit, le compte temporaire est
supprimé. Le compte DML applicatif exécute ensuite les cinq effets métier dans
une transaction annulée, avant toute activation du Web.

Un échec après mutation conserve l'interlock et exige l'inspection manuelle.
Pas de transaction DDL fictive, de nettoyage destructif ou de retry aveugle.

## Sauvegarde et versions antérieures

Le backup reste non mutant. Le nouveau profil durable est accepté seulement
après audit fermé et correspondance du nom avec la base source. Il est recréé
dans le vérificateur sans authentification, avec les mêmes droits minimaux sur
le seul schéma jetable. Les anciens profils de provisioning explicitement
supportés restent inchangés. Un compte manquant, déverrouillé, excessivement
privilégié ou privé d'un droit attendu est refusé, jamais corrigé en silence.

Le test négatif historique reste présent avec les mêmes assertions. Sa fixture
reconstruit explicitement l'état de l'ancienne version, dont la reproduction
indépendante est enregistrée ci-dessus. Les nouveaux cas positifs exécutent le
provisioning corrigé et une restauration réelle distincte.

La [sauvegarde de secours](PHASE5C2_RESCUE.md) est maintenant un candidat séparé
en qualification. La réparation consentie de l'existant, puis les fichiers
métier/sessions, upgrade, reprise et rollback restent à réaliser.
Ce correctif ne suffit pas à clore 5C2 ou toute la Phase 5.

## Qualification requise

- Les 456 tests core, 16 DOM et 21 HTTPS natifs historiques restent requis.
- La recette backup passe de 20 à 24 scénarios : futur managed, collision Host,
  compte déverrouillé, droits manquants/excessifs, plus les cas historiques.
- Les 18 SQL/TLS, 21 finalisation et 15 précontrôles sont toujours requis.
- `tests/integration/source_pins.py` vérifie indépendamment l'arbre Git Web
  complet et les cinq pins de contenu sans les régénérer ni exécuter du PHP.

Lire les résultats des campagnes sur le gel final avant de déclarer qualifié.
Pas de changement Web, schema.sql, install.php, migrations, version ou UI.
La réattribution des triggers relève du provisioning des identités SQL, et
ne modifie pas les corps métier ni une migration déjà publiée.

Références : [ACCOUNT LOCK](https://mariadb.com/docs/server/security/user-account-management/account-locking),
[CREATE TRIGGER](https://mariadb.com/docs/server/server-usage/triggers-events/triggers/create-trigger),
[GRANT](https://mariadb.com/docs/server/reference/sql-statements/account-management-sql-statements/grant).
