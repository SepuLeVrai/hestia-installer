# Phase 5B2.2a - Comptes locaux existants et configuration protégée

## Point de départ et périmètre livré

Base Installer : `93e7d7c1be273c9e17a1fc97846b062367cdb5e5` (5B2.1).
Base Web : `dcb856bc5ef5f35006d2398289b49f5386dcc5f5`, identique sur main et
 dev-Bastien lors de la relecture du 24 septembre 2026.
Leurs arbres ont été reconstruits à l'identique, modes compris, avant modification.
Aucune ancienne branche technique n'est une base de reprise.

5B2.2 est volontairement subdivisée. Ce premier lot prend en charge uniquement
le mode `existing_local`, TCP `127.0.0.1:3306` (`localhost` est normalisé), avec
base et deux comptes déjà créés. Il audite les connexions et prépare une
configuration hors webroot. Il ne crée ni serveur, base, compte, GRANT ou écran.
Les modes managed/remote, IPv6, ports différents et TLS distant restent refusés.
La connexion Web actuelle, `get_pdo()` dans includes/functions.php, n'intègre
ni DB_PORT ni options TLS. Ne pas contourner ce point par une injection de DSN
dans DB_HOST. Le traitement distant exige une évolution et une Quality Web.

Aucun fichier Web, install.php, schema.sql ou migration n'est modifié. Il n'y a
pas de ZIP compagnon Web. Les fonctions 5B2.1 ne sont pas refaites ni implicitement
raccordées à ce préflight ; leur intégration contrôlée reste à réaliser.

## Audit privé des comptes

`installer/sql_accounts.py` expose `audit_local_accounts(runtime, payload,
credentials)`. Le payload est validé par le contrat 5A, puis explicitement réduit
à la cible SQL et aux deux credentials. Ni administrateur HESTIA, ni clé Assistant,
ni chemin Web n'est envoyé à MariaDB. Le credential de provisioning est distinct
du mot de passe applicatif 5A ; mêmes noms OU mêmes mots de passe sont refusés.

L'auditeur réutilise les limites et le lancement durci de 5B2.1 : PHP sous une
identité non privilégiée dédiée, copie privée root-owned, environnement fermé,
stdin anonyme, sorties bornées, erreurs fixes et délai mural. Les deux ressources
PHP de ce lot n'exécutent aucun fichier du Web et n'émettent aucune mutation SQL.
Leur protocole v1 ne contient que `audit_local_accounts`. Les réponses ambiguës,
les identifiants incohérents et le désaccord code de sortie/JSON sont refusés.

Le profil fermé `local-dml-v1` exige :

- application : exactement SELECT, INSERT, UPDATE, DELETE sur la base ciblée ;
- provisioning/migration : ALL PRIVILEGES sur cette seule base, sans GRANT OPTION ;
- pour chaque compte : seulement USAGE global, authentification native par mot
  de passe, identité effective exacte sur localhost ou 127.0.0.1 ;
- aucun privilège PUBLIC, rôle attribué même inactif, PROXY, droit global,
  GRANT OPTION, privilège sur un autre schéma/table ou clause non reconnue.

Le profil inspecte les grants des connexions elles-mêmes, CURRENT_USER,
CURRENT_ROLE et les grants PUBLIC. Il ne confond pas les privilèges déclarés par
le formulaire avec des droits effectivement observés. SHOW GRANTS peut contenir
un hash d'authentification : ses lignes ne quittent jamais le processus privé.
L'absence de grants PUBLIC (erreur SQL 1141) n'est pas assimilée à une erreur
d'autorisation ; tout autre échec du contrôle PUBLIC ferme l'audit.

Les underscores du nom de base doivent être échappés dans les GRANT. Un GRANT
sur un motif `hestia_prod` au lieu de `hestia\_prod` peut couvrir d'autres bases :
il est refusé, pas interprété comme une restriction certaine à un schéma.
Seules les séries MariaDB 10.11, 11.4 et 11.8 sont acceptées dans ce profil ;
une nouvelle série ou syntaxe exige une requalification, pas une tolérance implicite.

Le résultat `ACCOUNT_PRIVILEGES_ONLY / ACCOUNTS_VERIFIED` est une observation
ponctuelle. Il n'atteste ni schéma HESTIA, ni identité cryptographique du serveur,
ni innocuité de triggers/vues/objets DEFINER déjà présents, ni toutes les fonctions
Web avec le compte DML. Les opérations de maintenance/restore/migration pouvant
nécessiter du DDL restent à séparer et qualifier, sans élargir le compte applicatif.

## Préparation des fichiers, sans activation du Web

`installer/database_config.py` expose `stage_local_database_configuration`.
C'est une API Python privée de l'orchestrateur root, pas une route HTTP, une
commande générique ou un résultat de validation 5A librement réutilisable.
Les chemins et identités système doivent déjà être préparés par l'hôte.

```python
from pathlib import Path
from installer.database_config import stage_local_database_configuration

# runtime, payload et provisioning_credentials sont des objets privés déjà reçus
# par l'orchestrateur de confiance ; aucun secret dans argv ou environnement.
result = stage_local_database_configuration(
    runtime, payload, provisioning_credentials,
    config_root=Path('/etc/hestia/instances'), confirmed=True,
)
```

Le parent config_root doit exister, appartenir à root, être traversable par le
service Web, non modifiable par groupe/autres et ne pas être servi par HTTP.
Les emplacements connus /var/www et /srv sont refusés pour cette sortie.
Le webroot et son répertoire includes doivent également exister et être protégés.
Liens, propriétaires inattendus, ancêtres modifiables et ACL POSIX sont refusés.
Le groupe Web doit être réservé à l'identité Web et distinct du worker PHP.
L'Installer ne fait aucun chown récursif des arbres reçus ni chmod global permissif.

La confirmation doit être exactement true. Upgrade est refusé avant toute
connexion/écriture. Un includes/db.php, install.lock ou emplacement de préparation
existant (même lien cassé, fichier atypique ou tentative incomplète) bloque le lot.
Aucun fichier PHP préexistant n'est évalué, notamment pas avec l'identité root.

L'audit des comptes est exécuté à nouveau par cette fonction, pas récupéré d'une
ancienne réponse du navigateur ou d'un succès mis en cache. Ensuite, une réservation
exclusive est créée sous config_root avec un nom SHA-256 dérivé du webroot canonique.
Le répertoire est d'abord root 0700, synchronisé avant écriture de secret. Il reçoit :

- `database.json` : uniquement la configuration SQL applicative ;
- `db.php` : chargeur sans credential, définissant les cinq constantes existantes ;
- `state.json` : état fermé `CONFIGURATION_STAGED`, jamais « installé ».

Chaque fichier est créé exclusivement, sans lien suivi, root:groupe-Web 0640 et
fsync. Après synchronisation de tous les fichiers et du répertoire, celui-ci est
passé en root:groupe-Web 0750. Le service peut lire, mais pas écrire ou remplacer
les fichiers ; le worker de provisioning et les autres identités n'ont pas accès.
Le chargeur relit uniquement le JSON protégé, contrôle métadonnées/types/tailles
et représentation JSON canonique, sans interpréter les valeurs comme du code PHP.
Unicode, espaces et ponctuation du mot de passe sont préservés exactement.

**Aucun db.php n'est copié dans le Web.** Le chargeur préparé contient le chemin
absolu de son JSON privé ; son activation future devra copier ce chargeur inchangé,
conserver son JSON à cette adresse, qualifier les permissions et vérifier la base.
Le dossier de préparation ne doit pas être supprimé comme un simple cache : il
contient un secret durable applicatif. Il ne contient aucun credential privilégié,
aucune clé Assistant et aucun mot de passe du premier administrateur.

Le résultat est `CONFIGURATION_STAGED`, `configuration_activated=false`,
`application_installed=false`. Une base vide peut recevoir cette préparation sans
être déclarée installée. Une base existante n'est ni écrasée ni migrée par ce lot.
Un refus ou une coupure avant réservation ne crée aucun fichier de configuration.
Après réservation, une panne/disque plein/fsync/crash laisse un emplacement bloqué
pour inspection manuelle. Le code ne prétend pas avoir annulé des écritures partielles
et ne propose aucun reset/retry automatique. Après une panne tardive, les fichiers
peuvent déjà être lisibles mais ils ne sont jamais activés dans le Web.

## Tests et Quality

Les nouveaux tests couvrent protocole, secrets, privileges réels et format des
GRANT, ACL, liens/hardlinks, propriétaires, données longues/Unicode, conservation
de l'existant, upgrade refusé, concurrence, disque plein, crash et fsync.
La suite SQL crée son propre serveur MariaDB et datadir temporaire, écoute uniquement
127.0.0.1 et refuse un port 3306 occupé. Elle n'utilise jamais une base serveur
existante. Les comptes et bases de fixtures sont aléatoires. L'opt-in est obligatoire :

```bash
# Uniquement dans un hôte/conteneur root jetable, jamais sur le serveur HESTIA.
HESTIA_ACCOUNT_DB_TEST=1 ./scripts/quality-local.sh
```

La CI permanente conserve les tests précédents et ajoute cette recette dans les
jobs Debian 12 et 13 ; aucun workflow APK ou nouveau job navigateur n'est ajouté.
PHP/MariaDB et leurs outils sont installés seulement dans les conteneurs de tests.
Un succès Debian 12/PHP 8.2 pour ce composant ne remplace pas la précondition PHP 8.3+
du vendor Web épinglé. Le test de connexion par les constantes générées n'est pas
une recette HTTP complète du Web. Les tests navigateur historiques restent requis.
Les résultats exacts figurent dans les artefacts de la nouvelle campagne et le
rapport de livraison. Des tests locaux sans pdo_mysql/MariaDB ne sont pas une
qualification SQL ; une ancienne campagne 5B2.1 ne qualifie pas ce code.

## Reprise WORK et limites restantes

Voir [HANDOFF_WORK_20260925.md](HANDOFF_WORK_20260925.md). 5B2.2 n'est pas terminée :
création des comptes/base, gestion de leurs droits, connexion distante/TLS et ports
restent à développer dans une frontière suivante. Ne pas activer ce staging seul.
Assistant/scellement restent en 5B2.3, upgrade/rollback en 5C, écrans/recette système
en 5D. Le wizard affiche encore « Sources prêtes ». Aucun serveur HESTIA n'est déployé.

Références techniques consultées :
[SHOW GRANTS MariaDB](https://mariadb.com/docs/server/reference/sql-statements/administrative-sql-statements/show/show-grants),
[GRANT MariaDB](https://mariadb.com/docs/server/reference/sql-statements/account-management-sql-statements/grant),
[Connexions PDO](https://www.php.net/manual/en/pdo.connections.php).
