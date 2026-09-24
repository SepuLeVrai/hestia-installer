# Phase 5B - Composition applicative Web, SQL, Assistant et scellement

## Reprise et frontière

Base Installer relue : `07d0da54c317420463a3699ee96dd6000afa6c31`.
Base Web relue : `dcb856bc5ef5f35006d2398289b49f5386dcc5f5` sur main et dev-Bastien.
Le présent lot termine la frontière applicative privée 5B : provisioning logique
SQL, connexions locales/distantes, configuration, moteur fresh, Assistant et
scellement. Il ne transforme pas 5B en installateur système complet.

Les prérequis hôte sont ceux de 5D : serveur MariaDB déjà disponible, PHP 8.3+
avec mysqlnd/PDO, identités système dédiées déjà créées, répertoires privés et
sources Web acquises/déployées avec propriétaires sûrs. Le mode `managed` crée
la base et ses comptes sur ce serveur ; il n'installe pas le service MariaDB.
L'installation des paquets officiels, Apache/PHP-FPM/NGINX, les permissions des
données applicatives et les écrans restent en 5D et phases réseau suivantes.
Le wizard n'est pas raccordé à une nouvelle mutation : il reste « Sources prêtes ».

La réussite maximale est `WEB_CONFIGURED`, `runtime_verified: true`,
`application_installed: false`, `http_verified: false`, `api_access_tested: false`.
Cela signifie que la base, l'Admin, la configuration et le chargeur réel du Web
ont été vérifiés sous l'identité Web, puis scellés. Cela ne prouve pas un login
HTTP, un Dashboard servi par Apache ou un accès OpenAI. Ces preuves ne sont pas
inventées à partir d'un marqueur de version ou d'un fichier seul.

## API Python privée

`installer/phase5b.py` fournit :

- `complete_fresh(runtime, payload, migration, authority, *, config_root, confirmed, cancel=None)` ;
- `update_assistant(runtime, payload_upgrade, *, config_root, confirmed, cancel=None)`.

Le payload réutilise exactement la validation 5A. Ce n'est pas une nouvelle route
HTTP ni une opération libre du journal public. La confirmation doit être le
booléen true. Les chemins, groupes, runtime et credentials supplémentaires sont
fournis par l'orchestrateur de confiance, pas acceptés comme commandes navigateur.
Les dépendances standard-library et les interfaces antérieures restent intactes.

```python
from pathlib import Path
from installer.phase5b import SqlAuthority, complete_fresh
from installer.php_transport import ProvisioningCredentials

# runtime et payload viennent des canaux privés de l'orchestrateur hôte.
# Les variables de secrets ci-dessous ne sont ni argv, ni environnement, ni logs.
result = complete_fresh(
    runtime, payload,
    ProvisioningCredentials(migration_user, migration_secret),
    SqlAuthority(authority_user, authority_secret),
    config_root=Path('/etc/hestia/instances'), confirmed=True,
)
```

Les trois noms de compte ET leurs trois mots de passe doivent être distincts.
Les secrets n'apparaissent ni dans le résultat, ni dans les arguments, ni dans
le journal. L'autorité n'est pas sérialisable. Le parent Python reste root pour
les opérations de fichiers ; PHP SQL tourne sous le worker non privilégié et la
vérification finale sous l'identité du Web. Aucun PHP local fourni par un service
Web n'est évalué en root. Les copies PHP temporaires sont privées et contrôlées.

## Provenance, bornes et canaux

Le code `WEB_RUNTIME_SHA256` contient l'empreinte SHA-256 du catalogue canonique
chemin/empreinte/contenu/mode de tous les fichiers runtime Web. L'orchestrateur
reconstruit ce catalogue, contrôle tous les ancêtres, refuse les liens, propriétaires
non root et droits d'écriture groupe/autres, puis compare l'empreinte compilée.
Aucun catalogue reçu du navigateur n'est une autorisation. Ajout/suppression,
changement d'octets ou de mode produit un refus. Les racines docs/tests/scripts/SCM
ne sont pas exécutées et ne sont pas copiées dans le worker. Un nouveau runtime
Web doit être requalifié et son empreinte mise à jour dans l'Installer.

Limites : 10 000 entrées, profondeur 32, 64 Mio au total, 8 Mio par fichier,
30 secondes de contrôle des sources. Chaque copie SQL est vérifiée à nouveau.
Le code et les dépendances réellement exécutés sont copiés avant lancement ;
aucun `includes/db.php` ou secret PHP historique n'est importé par cette copie.
Le contrôle d'une instance scellée n'exclut que les deux fichiers générés connus,
`includes/db.php` et `install.lock`, qui sont ensuite vérifiés séparément.

Le transport durci 5B2.1 est réutilisé sans assouplissement : stdin anonyme,
environnement fermé, PHP sans ini utilisateur, limites mémoire/CPU/processus,
aucun core dump ni écriture de fichier par le worker, sorties 4 Kio chacune,
entrée 16 Kio, délai mural configurable de 0,05 à 180 s (60 s par défaut),
arrêt du groupe de processus et erreurs fermées. Le protocole v2 exige une réponse
JSON unique, sa version, l'identifiant de requête, l'opération et un code de sortie
cohérents. Aucun diagnostic PDO/SHOW GRANTS/hash d'authentification brut ne sort.

## Comptes et cibles SQL

Séries MariaDB reconnues : 10.11, 11.4 et 11.8. La matrice réellement exécutée du
lot est consignée dans les preuves, pas déduite de cette liste de reconnaissance.

`managed` : local, port 3306 suivant 5A, base et comptes absents. Une autorité
explicitement fournie doit avoir ALL PRIVILEGES global avec GRANT OPTION et
une authentification native par mot de passe. Ce profil administratif fermé
est vérifié avant tout DDL. Il n'est jamais utilisé comme compte de migration ou
runtime. Aucun compte système n'est supprimé. La création est sans IF NOT EXISTS,
REPLACE ou reprise opportuniste. Base utf8mb4, compte applicatif DML, compte de
migration limité à ce seul schéma. Le compte de migration créé par ce parcours est
supprimé après vérification runtime, avant scellement. Les secrets temporaires ne
sont jamais conservés pour un prochain upgrade, qui devra fournir son autorité.

`existing_local` : hôte loopback explicite, port fourni, base et deux comptes
préexistants ; ils sont audités, jamais remplacés ou modifiés. Une autorité de
lecture des métadonnées de privilèges est fournie séparément. Le moteur fresh
refuse une base non vide, sans réinitialisation d'Admin ni de données.

`remote` : cible DNS/IP non déclarée loopback, port fourni, CA protégée obligatoire.
Les comptes/base préexistent et sont audités. PDO mysqlnd reçoit la CA et la
vérification de certificat/nom dès sa construction ; aucun basculement en clair.
Le cipher est contrôlé et la version observée doit être TLS 1.2 ou 1.3. Les comptes
applicatif/migration doivent aussi exiger SSL. Une CA incorrecte, un nom erroné,
un certificat expiré ou un serveur annonçant l'absence de TLS font échouer le
parcours. Les octets CA vérifiés sont copiés dans le staging et dans la configuration
persistante ; le fichier original n'est plus une dépendance mutable du runtime.

Le profil applicatif exige exactement SELECT, INSERT, UPDATE, DELETE sur le schéma.
Migration : ALL sur ce schéma sans GRANT OPTION. Refus de privilèges globaux,
supplémentaires/manquants, PUBLIC privilégié, rôles même inactifs et PROXY.
L'autorité lit aussi mysql.global_priv/db/tables_priv/columns_priv/procs_priv,
roles_mapping et proxies_priv : un seul compte Host exact par nom, aucun anonyme
ou grant orphelin/ambigu. Cela couvre le fait que SHOW GRANTS seul peut omettre des
droits correspondant à un autre Host du même nom. Les underscores du schéma sont
échappés dans les GRANT. L'absence d'objets est observée avec la connexion de
migration auditée, dont la visibilité couvre le schéma, pas supposée à partir d'un
auditeur limité à mysql. Les droits sont revérifiés après les effets SQL.

Ces observations ne protègent pas d'un administrateur SQL hostile modifiant les
droits en parallèle. La sérialisation repose sur des verrous MariaDB coopératifs.
Un profil DML réussi n'est pas une recette exhaustive des écrans de maintenance,
dump/restauration ou opérations Web nécessitant DDL : les séparer et qualifier en
5C/5D sans redonner ALL/root au runtime.

## Ordre fresh et états d'échec

1. Validation, consentement, identités, provenance, chemins, CA et préflight SQL.
2. Réservation exclusive et fsync de l'interlock fresh partagé avec 5B2.1.
3. Réservation du slot privé par webroot, écriture data-only de la configuration.
4. Provisioning logique si managed ; appel du moteur SQL partagé 5B1 unique.
5. Vérification version, Admin/password exact, RBAC, utf8mb4 et état Assistant via DML.
6. Publication du chargeur protégé dans le Web et contrôle réel sous son identité.
7. Suppression du compte temporaire créé en managed, puis scellement et reçu.

Un slot préparé par 5B2.2a n'est pas adopté silencieusement : toute cible existante,
partielle ou étrangère est refusée. Avant réservation, les erreurs sont des refus
sans mutation applicative. Après réservation, toute incertitude devient
`MANUAL_ACTION_REQUIRED`. Aucun second fresh aveugle, effacement ou rollback DDL
fictif. La perte de réponse et le crash après mutation sont des tests réels.
Un échec de suppression du compte temporaire empêche le scellement.
Une erreur disque tardive peut laisser un scellement partiel : interlock bloquant,
inspection manuelle requise, jamais retour de succès inventé.

## Configuration et Assistant

Le slot /etc/hestia/instances/<empreinte-webroot> est root:groupe-Web 0750 ;
instance.json et l'éventuelle CA sont root:groupe-Web 0640, nlink=1, sans ACL.
Seul le mot de passe SQL applicatif et l'éventuelle clé Assistant y sont durables.
Le chargeur PHP ne contient aucun secret ; il charge le lecteur Web partagé,
qui valide le JSON canonique v2, les propriétaires, modes et ancêtres. Les
fichiers existants ne sont ni exécutés par root ni écrasés. Le compte Web lit,
ne modifie pas ; worker et autre utilisateur n'ont pas accès au slot.

Une configuration gérée définit une clé Assistant propre à l'instance, éventuellement
vide. Elle prévaut explicitement sur les variables d'environnement et les fichiers
historiques globaux/locaux : pas d'activation ou de clé héritée silencieuse.
Sans clé, le runtime renvoie Assistant désactivé. Avec clé au format accepté,
la configuration est enregistrée puis l'état SQL activé et vérifié. Aucun appel
OpenAI n'est effectué et aucun droit d'accès API n'est certifié par cette étape.
Les méthodes historiques Assistant restent utilisées et inchangées pour les
instances non gérées. Sur une instance gérée, l'administration Web annonce le
stockage système et ne remplace pas ce fichier protégé via l'ancienne écriture PHP.

`update_assistant` opère uniquement sur une instance gérée/scellée et épinglée :
- preserve lit l'état runtime effectif ; configure avec champ vide le conserve ;
- configure non vide remplace atomiquement le JSON, puis active et vérifie ;
- disabled désactive d'abord en base, puis retire explicitement la clé du JSON.

La mutation est verrouillée par un journal privé durable. Crash/interruption après
dispatch bloque une autre mutation jusqu'à inspection. Un champ vide ne supprime
jamais une clé. Aucun fichier PHP de secret legacy n'est évalué par ce chemin.
La conversion des anciennes instances et l'upgrade SQL relèvent de 5C. Le contrôle
des sources reste strict ; l'évolution des chemins de données modifiables et la
recette en serveur actif relèvent de 5D, pas d'une exception générale aux empreintes.

## Qualification et livraison

Les anciens tests ne sont pas retirés. Les nouveaux tests de contrat/fichiers sont
inventoriés dans tests/quality-baseline.json. La campagne transverse obligatoire est :

```bash
# Hôte root jetable uniquement, aucun serveur occupant le port 3306.
HESTIA_PHASE5B_DB_TEST=1 python3 tests/integration/phase5b_e2e.py --web-source /chemin/source-web-qualifiee
```

Elle démarre ses propres datadirs/serveurs, certificats valides/expirés et serveur
sans TLS ; elle ne se connecte pas à une base utilisateur. Les modifications de
hosts et identités de fixture sont confinées à ce banc jetable. Les fixtures de
pannes sont explicites, jamais des options de production permettant de changer
une empreinte. La Quality Installer complète ET la Quality Web complète sont
requises en plus de cette campagne, avant les fast-forwards.

Le rapport de livraison et Installer #13 donnent les HEAD, runs, nombres réellement
exécutés et empreintes. Les ZIP légers contiennent les fichiers complets gelés.
install.php et sql/schema.sql sont inchangés mais fournis complets dans le compagnon
Web. Aucune migration, version runtime ou dépendance applicative n'est modifiée.
Ne pas fusionner les branches techniques de préparation/qualification transverse.

Références primaires :
[PDO MySQL](https://www.php.net/manual/en/ref.pdo-mysql.php),
[GRANT MariaDB](https://mariadb.com/docs/server/reference/sql-statements/account-management-sql-statements/grant).
