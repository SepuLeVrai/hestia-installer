# Sauvegarde coordonnée SQL, enveloppe et données enregistrées

## Lot court, candidat à qualifier

Base qualifiée : Installer `a19c40318dfac958330fda8890116c6580e3ae43`, arbre
`6961f5b84103a7d418c1caa68611a7cc6b007bb5`, Quality `36133121054` verte.
Web inchangé : `46c03060625d4d53c675474b11aaa33007d9aad7`.

`installer.coordinated_backup.CoordinatedBackup` compose les primitives SQL et
fichiers sous la même lease de maintenance. L'API privée accepte une instance
scellée 5B2.3, un inventaire de confiance de racines **externes** et les deux
consentements explicites, dont celui du verrou global de lecture SQL.
Aucune route, activation, migration, restauration vers la source ou opération
sur un serveur existant n'est ajoutée.

## Ordre et conditions du reçu commun

1. Vérifier les pins fermés, l'enveloppe scellée, l'identité Web et la liaison exacte entre instance, répertoire privé et lease vivante. Refuser les chevauchements et toute racine de données sous le code Web ou les répertoires de contrôle.
2. Réserver durablement un répertoire privé exclusif et écrire `attempt.json`. Copier les données enregistrées, les restaurer réellement dans un clone neuf puis relire les sources.
3. Exécuter la sauvegarde SQL et de l'enveloppe existante. Restaurer réellement SQL dans une MariaDB neuve sans TCP, comparer données/DDL, FK et cinq triggers canoniques avec leurs essais DML isolés. Aucun DML de validation sur la source.
4. Relire les données sources et restaurer une deuxième fois les blobs sauvegardés, après la preuve SQL. Un blob endommagé ne peut pas être couvert par un ancien succès du composant.
5. Relire SQL avec le même exporteur canonique, les mêmes limites, le même TLS et un deuxième verrou global de lecture borné. Comparer l'empreinte logique des données et objets, pas uniquement le nombre de lignes.
6. Vérifier de nouveau la lease, les fichiers sources, l'enveloppe complète et le journal fresh. Relire les archives, leurs empreintes et les reçus des composants. Restaurer de nouveau les fichiers de l'enveloppe.
7. Écrire `coordinated.json`, puis le seul reçu global `verified.json` après les contrôles finaux et fsync. Le manifeste privé lie instance, lease, cible SQL, source exacte et empreintes des composants.

Un échec après réservation conserve les preuves privées incomplètes sans reçu
commun. Une preuve isolée SQL ou fichiers ne suffit jamais. Un arrêt brutal ne
rouvre pas le Web : le marqueur durable de maintenance demeure. La récupération
de sa lease autorise l'inspection ; aucun rejeu automatique ou effacement des
archives n'est fourni par ce lot. Les erreurs publiques sont fermées et ne
contiennent ni chemins, noms métier, identifiants de sessions ni credentials.

Le résultat `COORDINATED_BACKUP_RESTORE_VERIFIED` porte uniquement sur le SQL,
l'enveloppe immuable et les racines externes enregistrées. La cohérence repose
sur la participation de **tous** les producteurs à la maintenance ; les relectures
détectent une dérive, elles ne prouvent pas le raccordement d'un producteur oublié.
`storage_inventory_complete`, `system_wiring_verified`, `complete_web_backup`,
`apply_allowed`, `restore_to_original_allowed`, `rollback_verified` et
`application_installed` restent **false**. La maintenance exige une reprise
explicite ; elle n'est jamais levée automatiquement par ce résultat.

## Inventaire applicatif encore à fermer

L'inventaire injecté n'est pas la découverte exhaustive des stockages Web.
Le contrat immuable actuel refuse les arbres métier inscriptibles sous le
webroot. Le lot ne retire aucun contrôle root, mode ou empreinte pour contourner
ce refus. Le prochain profil de stockage/services doit traiter au minimum :

| Producteur connu du Web épinglé | Stockage à inventorier et raccorder |
| --- | --- |
| GED et héritage | `uploads/ged_documents`, `uploads/ged_legacy`, racines externes configurées |
| Photos, références, qualifications mobiles | Sous-arbres de `uploads`, y compris modèles et exports |
| Moteurs d'import et leur surveillance | `var/imports` ou `HESTIA_IMPORT_STORAGE`, verrous et registres |
| Sessions PHP | `session.save_path` propre à l'instance, expiration et nettoyage |
| Fichiers temporaires | Répertoire propre à l'instance, producteurs HTTP et CLI |
| Assistant et IA | Enveloppe privée, configuration IA externe et répertoire de consommation |
| Publications mobiles existantes | Détecter les répertoires configurés et leurs producteurs, sans chantier APK |

Cette liste de travail n'est pas une attestation d'exhaustivité. Configuration
réelle, scripts planifiés, workers et nettoyage des sessions doivent être audités
avant une sauvegarde complète. Aucune transition de version artificielle ne
remplace la future recette réelle d'upgrade et de rollback.

## Qualification requise sur les fichiers gelés

Dix nouveaux tests core obligatoires s'ajoutent aux 478 : cible **488** par
Debian 12/13, avec 16 DOM et 21 HTTPS inchangés. Ils utilisent de vrais fichiers
et un transport SQL explicitement simulé ; ils ne prouvent pas MariaDB.

La recette `tests/integration/coordinated_backup_mariadb.py`, opt-in
`HESTIA_COORDINATED_BACKUP_TEST=1` plus ceux des fixtures héritées, ajoute dix
scénarios SQL/HTTP réels. Elle teste documents Unicode, secrets conservés,
restauration des sessions et connexion avec le cookie existant, TLS distant,
consentements/instance, dérives SQL à compte de lignes identique, fichiers et
clé privée, archives SQL/données corrompues, perte de lease et SIGKILL réel
entre manifeste commun et reçu final. Le remplacement du répertoire de sessions
est limité à la fixture jetable, pour vérifier les octets effectivement restaurés.
Ce n'est pas une activation complète du Web sur la base SQL restaurée.

Les six suites historiques restent requises : 18 préparation, 21 finalisation,
15 précontrôles, 30 backup, 7 maintenance et 9 réparation, soit 110 scénarios
avec les dix nouveaux. Les résultats du commit gelé seront joints au checkpoint
compagnon ; les anciens runs verts ne qualifient pas ce nouveau code.
La Phase 5 et 5C2 restent ouvertes, sans PR ni promotion des branches actives.
