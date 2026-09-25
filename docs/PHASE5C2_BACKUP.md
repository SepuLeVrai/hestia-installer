# Phase 5C2a - Sauvegarde privée et restauration SQL isolée

## Statut et découpage

Base Installer : `c0dcb902663130302599635b36c7fb8deab80a47`, arbre
`9d9a13fbd94f954894e6a65c9434632a5b98b3a6`. Web inchangé :
`46c03060625d4d53c675474b11aaa33007d9aad7`, arbre
`aaac278270e0fd1169396945916dfe997ae078bf`.

Ce lot implémente **5C2a**, pas la clôture de 5C2. Il sauvegarde un profil
SEALED_5B23 reconnu et prouve réellement une restauration SQL distincte ainsi
que la copie des fichiers privés. Il ne migre, ne répare et ne réactive pas le Web.
Publication et campagnes finales : lire le commit et les derniers commentaires
Installer #13 / Web #135, pas considérer ce document pré-Quality comme leur preuve.

**5C2b reste obligatoire avant 5C3** : traiter explicitement les DEFINER du mode
SQL géré, qualifier le parcours correspondant et valider la clôture de 5C2.
Les services, la maintenance système, les fichiers métier modifiables et les
sessions PHP externes ne sont pas déclarés sauvegardés par ce profil limité.
Les écrans restent 5D ; aucune installation complète n'est annoncée.

## Défaut hérité détecté sur une fixture réelle

Au pin actuel, le fresh managed crée cinq triggers avec le compte temporaire de
migration comme DEFINER, puis supprime ce compte. Les checks précédents couvraient
l'Admin, la version et le retrait du compte, pas l'exécution des cinq triggers.
Le nouveau test reproduit le parcours et constate l'erreur **MariaDB 1449** lors
d'un INSERT qui appelle un trigger canonique. Aucun serveur de Bastien n'a été
examiné ou modifié : cette observation porte sur une base jetable de recette.

L'export refuse alors `BACKUP_DEFINER_MISSING`. Il ne certifie pas une sauvegarde
opérationnelle et ne recrée jamais le compte supprimé en cachette. Les parcours
existing_local et remote TLS avec DEFINER canonique existant et droits limités
au seul schéma restent vérifiables. Les cinq triggers ne sont jamais omis.

5C2b devra distinguer correction du provisioning futur et réparation consentie
d'une instance déjà affectée, conserver le compte applicatif DML et préserver
les interlocks, sources et enveloppes. Ne pas élargir les GRANT applicatifs ni
réutiliser un compte root comme DEFINER pour contourner le problème.

## API privée

```python
from installer.upgrade_backup import UpgradeBackup
from installer.database_step import SqlAuthorityCredentials

step = UpgradeBackup(runtime, web_source,
    repository="SepuLeVrai/hestia-nexus-avv",
    commit="46c03060625d4d53c675474b11aaa33007d9aad7")
verification = step.create_and_verify(
    upgrade_payload,
    SqlAuthorityCredentials(authority_user, authority_password),
    config_root=private_configuration_root,
    backup_root=private_backup_root,
    confirmed=True,
    allow_global_read_lock=True,
)
report = verification.report()
```

Appel réservé à l'orchestrateur de confiance, aucune nouvelle route, aucun bouton
ou adaptateur d'apply public. Le payload 5C1 reste en mode upgrade, sans Admin,
clé Assistant ni réglage à modifier. Les secrets ne figurent pas dans le rapport.
Le rapport de 5C1 n'est pas consommé comme permission : les contrôles réels sont
réexécutés, puis l'enveloppe est relue pendant la sauvegarde.

L'autorité SQL est fournie explicitement et n'est jamais conservée. Profil fermé
actuel : ALL global avec GRANT OPTION, native-password, Host explicite, sans rôle
ou PUBLIC privilégié, REQUIRE SSL à distance. Ce n'est pas un calcul automatique
de privilèges minimaux. Son identité et son mot de passe doivent être distincts
du compte applicatif, lequel garde ses quatre droits DML. Aucun compte source
n'est créé, effacé ou modifié par la sauvegarde.

## Consentement et cohérence SQL

`confirmed` et `allow_global_read_lock` doivent être exactement le booléen true.
L'export utilise **FLUSH TABLES WITH READ LOCK**, temporairement et à l'échelle
du serveur : les écritures des autres bases de ce serveur peuvent être bloquées.
Ne pas appeler cette API sur un serveur partagé sans fenêtre et consentement
adaptés. Les attentes SQL et la durée totale du worker sont bornées.

Une connexion garde ce verrou ; une seconde, à identité/serveur identiques,
exporte sous READ ONLY, WITH CONSISTENT SNAPSHOT et REPEATABLE READ. Version,
objets, tables, colonnes et triggers sont contrôlés avant et après. Le verrou
est libéré dans finally ; le transport tue son groupe de processus sur erreur
ou interruption. La recette vérifie réellement qu'un UPDATE concurrent est
bloqué pendant l'export puis redevient possible, y compris après interruption.
Ce mécanisme ne protège pas contre un administrateur hostile et ne fige pas
l'instance après la fin de la sauvegarde.

Pas de shell construit depuis des saisies ; PHP non privilégié sur une copie de
code épinglée, environnement fermé, arguments sans credentials, stdin privé,
flux et délais limités. Les erreurs publiques sont des codes fermés, sans DSN,
SHOW GRANTS brut, authentification SQL ou message d'exception recopié.

## Profil de données accepté

Base utf8mb4/utf8mb4_unicode_ci, release HESTIA épinglée, tables InnoDB ordinaires.
Les cinq triggers doivent correspondre exactement au schéma canonique épinglé,
y compris événements, tables, corps et collations. Chaque DEFINER doit exister
et avoir le profil de provisioning sur le seul schéma, sans GRANT OPTION/global.
Les comptes d'authentification du serveur source ne font pas partie du dump.

Refus explicite des vues, routines, événements, FK inter-schémas, tables non
transactionnelles, colonnes générées/invisibles, options de stockage inconnues
et types non pris en charge. Aucune omission silencieuse d'un objet pour passer.
Les identifiants SQL sont ASCII bornés et les noms de fichiers sont privés.

Archive logique NDJSON, pas un script SQL à lancer sur un serveur arbitraire.
SHOW CREATE TABLE est capturé, puis les cellules sont encodées en hexadécimal,
avec NULL distinct de la chaîne vide. Les FLOAT/DOUBLE passent en DOUBLE dans
une requête native préparée, puis en 17 chiffres significatifs ; un test compare
la représentation FLOAT réellement préservée, pas seulement un affichage arrondi.
Dates/TIMESTAMP sont traités avec un fuseau de session UTC. Tri par cellules
encadrées et hash de taille fixe, puis valeurs flottantes : les longs TEXT/BLOB
ne dépendent pas de la troncature max_sort_length. Les doublons sans PK sont
conservés. Une collision de tri entre lignes différentes est refusée.

Plafonds : 512 tables, 16384 colonnes au total, 1024 par table, un million de
lignes, archive de 128 Mio, ligne NDJSON de 8 Mio. Ils sont des limites de profil,
pas des raisons de tronquer une sauvegarde. Le serveur MariaDB de vérification
doit avoir exactement la même chaîne de version que la source. Les familles
acceptées sont 10.11/11.4/11.8 ; la nouvelle recette SQL locale qualifie seulement
11.8.6/PHP8.4.24, pas une matrice complète de ces familles.

## Fichiers et secrets durables

La racine de sauvegarde préexistante doit être root:root 0700, hors webroot,
config, source, run et state, et hors /srv, /var/www et /tmp. Chaque appel réserve
un nouveau slot 0700, sans écrasement ni réutilisation d'un slot existant.
Fichiers sauvegardés 0600, manifestes et preuves privés. Aucune sauvegarde n'est
servie par HTTP ou incluse dans un ZIP de livraison des sources.

Le profil copie le déploiement Web root-owned non modifiable par le Web, sa
configuration privée complète et le reçu fresh propre à cette instance. La clé
Assistant 0660 est l'exception contrôlée, protégée par les verrous coopératifs
existants et les relectures. La configuration SQL/TLS, CA, pointeur, install.lock,
sceau, reçus et secret applicatif durable restent des données sauvegardées.
Les credentials d'autorité/migration et le mot de passe Admin fourni ne sont
jamais ajoutés ; les hash de mots de passe déjà présents dans la BDD restent
naturellement dans l'archive privée des données.

Liens, hardlinks, ACL, propriétaires/modes inconnus et modifications observées
font refuser la sauvegarde. Les arbres métier modifiables ne sont pas omis :
ils sont refusés. Seuls .git, .github, .quality et __pycache__ sont exclus comme
métadonnées hors déploiement. La session PHP externe n'est pas copiée ; la recette
prouve sa préservation sur la source, pas sa restaurabilité par ce lot.

Limites fichiers : 10000 entrées, 8 Mio par fichier, 128 Mio cumulés et parcours
borné. Préconditions d'espace libre : 512 Mio pour la sauvegarde, 2 Gio pour le
vérificateur. Il ne s'agit pas d'un quota : une erreur disque reste possible et
ne produit pas de résultat positif. Aucune garantie d'effacement cryptographique.

## Vérification par restauration réelle

Les blobs de fichiers privés sont relus et restaurés dans un répertoire non
servi, avec vérification des octets, modes, propriétaires et dossiers vides.
Les pointeurs absolus sont conservés comme données, jamais exécutés depuis ce
clone. Le reçu fresh est lui aussi recopié et contrôlé. Le clone temporaire est
ensuite supprimé ; la sauvegarde privée originale est conservée.

Une MariaDB neuve est initialisée sous l'identité worker, dans un répertoire
exclusif, sans réseau TCP, binlog ou événements. Aucun host, schéma existant,
chemin de données source ou commande client n'est accepté comme cible. Un socket
Unix privé et la correspondance de @@datadir/@@skip_networking sont vérifiés.
Les exécutables système root-owned sont des prérequis, pas téléchargés par ce lot.

Le vérificateur reçoit uniquement une COPIE de l'archive et un secret jetable,
pas la connexion ou l'autorité source. La base backup_verify et son utilisateur
sont créés exclusivement dans ce serveur neuf ; toute préexistence est refusée.
DDL exécuté sous un compte restreint au schéma, sans FILE/EXECUTE/global, sans
multi-statements ni local-infile. Les données sont insérées avant les triggers.
Warnings, comptes de lignes, hash logiques et DDL différents font refuser.

Les DEFINER sont recréés comme identités ACCOUNT LOCK avec leurs droits sur le
seul schéma isolé, jamais avec leurs anciens mots de passe. Les cinq triggers
canoniques sont recréés avec leurs métadonnées exactes. Toutes les FK sont
contrôlées après import. Une nouvelle connexion DML seule exécute des insertions
et mises à jour de preuve : activités, dimensions, référence, import et clic GED.
Les effets attendus des cinq triggers sont vérifiés, puis ces données de preuve
sont annulées dans la cible jetable. Ce n'est pas une recette HTTP de réactivation.

L'utilisateur de vérification, les DEFINER temporaires et la base de contrôle
sont supprimés et leur absence contrôlée après succès ; le serveur est arrêté.
La sauvegarde n'est positive qu'après ces preuves et les synchronisations disque.

## Résultats, interruptions et limites

```text
state = BACKUP_RESTORE_VERIFIED
backup_verified = true
database_restoration_verified = true
private_files_restoration_verified = true
canonical_triggers_verified = 5
trigger_smoke_verified = 5
restore_to_original_allowed = false
apply_allowed = false
rollback_verified = false
web_activation_verified = false
application_installed = false
```

Ce statut porte sur le périmètre SQL/root-owned/enveloppe privé décrit ici,
pas sur une restauration complète de service. L'échec après réservation rend
BACKUP_INCOMPLETE, backup_verified=false et manual_inspection_required=true.
Les fichiers partiels restent privés. Une erreur retournée révoque seulement le
reçu de succès créé par cet appel, jamais la sauvegarde ou la source. Un SIGKILL
peut laisser du staging privé ; aucune reprise/activation automatique n'est faite.
Une réponse perdue après reçu complet et la consommation de ce reçu par un futur
upgrade sont des contrats à traiter explicitement, pas une permission implicite.

## Qualification et suite

41 nouveaux tests core de fichiers/protocole/processus, distincts de la recette
réelle `tests/integration/upgrade_backup_mariadb.py` (20 scénarios). Les 415 core
historiques, 16 DOM et 21 HTTPS natifs restent requis. Les 18 SQL/TLS, 21 finalisations
et 15 précontrôles historiques restent des recettes réelles séparées.

Commande sur hôte jetable, jamais un serveur de production :

```bash
HESTIA_UPGRADE_BACKUP_TEST=1 HESTIA_UPGRADE_PREFLIGHT_TEST=1 \
HESTIA_FINALIZATION_TEST=1 HESTIA_DATABASE_STEP_TEST=1 HESTIA_ACCOUNT_DB_TEST=1 \
python3 tests/integration/upgrade_backup_mariadb.py --web /chemin/source-web-epinglee --report rapport.json
```

Les tests créent leurs serveurs et refusent un port occupé. Ne pas exécuter en
parallèle les recettes historiques qui réservent le même port de fixture.
Les résultats finaux, incidents et limites sont dans le rapport de livraison.
Le Web, install.php, schema.sql, migrations, seeds et version sont inchangés.
Sources et docs gelées avant Quality, ZIP léger de fichiers complets exactement
identiques au gel ; aucune branche technique ne doit être fusionnée.

Références techniques : [FLUSH](https://mariadb.com/docs/server/reference/sql-statements/administrative-sql-statements/flush-commands/flush),
[CAST](https://mariadb.com/docs/server/reference/sql-functions/string-functions/cast),
[transactions](https://mariadb.com/docs/server/reference/sql-statements/transactions/start-transaction).
