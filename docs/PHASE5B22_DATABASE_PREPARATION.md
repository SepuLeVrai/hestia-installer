# Phase 5B2.2 - Préparation SQL et configuration assemblées

## Périmètre de l'étape 1

Base Installer relue : `07d0da54c317420463a3699ee96dd6000afa6c31`.
Base Web relue : `dcb856bc5ef5f35006d2398289b49f5386dcc5f5`.
Ce lot termine le résiduel 5B2.2, en conservant 5B2.1 et 5B2.2a comme contrats
compatibles. Il n'intègre ni Assistant/finalisation 5B2.3, ni upgrade 5C, ni
écrans/déploiement système 5D. Ne pas fusionner les tentatives techniques antérieures.
Le wizard affiche toujours « Sources prêtes ». Aucune nouvelle route HTTP n'existe.

`installer/database_step.py` est l'API privée de l'orchestrateur de confiance.
Les sources Web exécutées sont épinglées par commit et empreinte de leur fermeture
exécutable, vendor compris. Les constantes WEB_COMMIT et ENGINE_SHA256 du module
indiquent la compatibilité exacte ; une nouvelle source exige une requalification.
La bibliothèque PHP `includes/installation/connection.php` appartient au Web.
`get_pdo()` utilise son contrat uniquement avec DB_CONNECTION_VERSION=2 ; le
parcours historique reste inchangé sans cette constante. Le moteur fresh, les
seeds/admin/RBAC, install.php, schema.sql et migrations ne sont pas réécrits.

## Modes et préconditions

| Mode | Traitement |
| --- | --- |
| managed | Crée base et comptes sur un serveur MariaDB déjà opérationnel, 127.0.0.1:3306. |
| existing_local | Audite deux comptes et une base existants, loopback IPv4/IPv6 et port explicite. |
| remote | Base/comptes existants, port explicite, TLS vérifié et comptes REQUIRE SSL. |

L'installation des paquets et services MariaDB/PHP, des identités système et des
répertoires hôte reste en 5D. « managed » qualifie ici la création des ressources
SQL, pas la création du serveur ou une installation complète du système.
Les séries MariaDB 10.11, 11.4 et 11.8 sont reconnues. Lire les versions effectivement
exécutées dans le rapport, sans confondre acceptation syntaxique et recette réelle.
Le Web épinglé exige PHP 8.3+ ; le composant peut être testé isolément sous PHP 8.2.

Le parent est root, PHP est une identité dédiée non privilégiée déjà existante,
jamais le compte Web. Les contrôles d'identité, de groupes, de chemins/ancêtres,
d'ACL et de copie privée proviennent de 5B2.1/5B2.2a. Le processus utilise une
commande fixe sans shell, un environnement fermé, aucun descripteur inutile,
stdin privé, entrée 16 Kio et chaque sortie 4 Kio. Le délai mural par défaut est
60 secondes et reste imposé par le parent ; aucune sortie brute n'est renvoyée.

## Trois identités SQL, aucun credential privilégié durable

Le credential du payload 5A est celui de l'application. Le paramètre migration
est un objet ProvisioningCredentials privé distinct. En managed seulement,
SqlAuthorityCredentials représente l'autorité capable de créer bases et comptes.
Noms et mots de passe doivent être distincts. SQL root est permis pour cette
seule autorité, pas pour la migration ou le Web. Son usage ne signifie jamais
qu'un fichier PHP fourni par le Web est lancé sous root.

Le profil managed exige explicitement une autorité locale avec ALL global et
GRANT OPTION, sans rôle actif. Ce compte administratif de l'opérateur n'est pas
créé, stocké ou révoqué par le lot. Il est utilisé uniquement par le canal privé.
Le profil ne prétend pas calculer le minimum de privilèges de tous les serveurs
et plugins possibles ; une autorité moins dotée est refusée avant DDL.

Avant création, un verrou coopératif MariaDB est pris. La visibilité mysql.global_priv
permet de refuser les comptes existants sous n'importe quel Host, puis toute base
occupée, même vide. Pas de CREATE IF NOT EXISTS, ALTER d'un compte existant ou DROP
sur une ressource préexistante. Les identifiants SQL sont fermés et les underscores
du nom de schéma sont échappés dans les GRANT.

Le compte applicatif reçoit exactement SELECT/INSERT/UPDATE/DELETE sur ce seul
schéma. La migration reçoit ALL sur ce seul schéma, sans GRANT OPTION. Après
succès fresh et vérification via l'application, le compte temporaire de migration
créé par cet appel managed est supprimé et son absence vérifiée. Ceux fournis en
existing_local/remote sont conservés : l'Installer n'en est pas propriétaire.
Un échec peut laisser les ressources nouvelles partielles et le compte temporaire ;
une action opérateur est alors nécessaire, sans nettoyage destructeur automatique.

## Audit effectif et TLS

Les deux connexions réelles sont auditées : identités effectives exactes, grants,
rôles attribués même inactifs, PUBLIC, absence de droits globaux ou d'autres schémas,
jokers et clauses ambiguës. Le code réutilise une seule politique de privilèges.
Les lignes SHOW GRANTS et leurs hashes d'authentification restent dans PHP privé.
Le fingerprint version/server_id/hostname détecte une incohérence entre connexions ;
ce n'est pas une identité cryptographique ni une protection contre un DBA hostile.
L'audit des comptes existants porte sur l'identité effectivement authentifiée,
pas sur un inventaire global inaccessible de tous leurs autres Host. Le contrôle
global des Host avant création est propre au mode managed et à son autorité.

Le remote exige une CA protégée, lue avec descripteur vérifié, bornée, analysée et
copiée dans le contexte privé. Le connecteur partagé PDO/mysqlnd vérifie certificat
et hostname, puis une session TLSv1.2/1.3 avec cipher non vide, avant la moindre
mutation. Aucune reconnexion en clair. Le nom DNS n'est pas remplacé par son IP,
pour ne pas perdre la vérification d'identité. Les comptes distants doivent aussi
porter REQUIRE SSL. Ce profil ne configure pas le serveur distant ni ses certificats.
Les connexions locales en clair sont limitées à 127.0.0.1/::1 ; localhost est
normalisé en 127.0.0.1, jamais utilisé comme socket Unix contournant le port.

## Ordre fresh et états

1. Validation fermée 5A et mapping explicite : aucun secret Assistant n'est envoyé.
2. Contrôles hôte, CA, sources exécutées, absence de configuration/lock Web existants.
3. Réservation exclusive du slot hors webroot, fsync, marqueur de base durable.
4. Provisioning managed éventuel, connexions et audit effectif, moteur fresh partagé.
5. Contrôles Admin général/mot de passe/version/Assistant désactivé via le compte DML.
6. Suppression vérifiée de la migration temporaire managed, libération du verrou.
7. Préparation de database.json, chargeur db.php, CA éventuelle et état non secret.

Résultat maximal : DATABASE_CONFIGURATION_READY, configuration_activated=false,
application_installed=false, assistant_enabled=false. Le JSON durable ne contient
que le credential applicatif. Aucune autorité SQL, migration ou password admin.
Le chargeur et les données restent hors webroot : root:groupe-Web, 0750/0640,
non modifiables par le service. L'emplacement reste 0700 jusqu'au commit fichiers.
Aucun includes/db.php ou install.lock n'est posé dans le Web. L'activation appartient
à l'étape 2, et le test d'une connexion PDO ne vaut pas un login HTTP ou une recette 5D.

Le même marqueur fresh que 5B2.1 est utilisé pour une cible identique normalisée.
Succès, refus après réservation, interruption, réponse perdue ou crash bloquent le
rejeu aveugle. Aucun fichier existant n'est évalué ou écrasé. Une erreur après SQL
peut laisser une base prête sans configuration ; elle retourne MANUAL_ACTION.
L'audit séparé reste non mutant, ne supprime pas le marqueur et n'autorise pas de
reprise. Pas de rollback DDL atomique, de garantie d'effacement mémoire ou de
nettoyage automatique après SIGKILL. L'administrateur root/SQL et le noyau sont
hors frontière de protection. Les alias DNS et un autre orchestrateur doivent
respecter le verrou SQL ; un alias ne rend jamais une base non vide réinstallable.

L'upgrade refuse prepare avant mutation. audit peut observer les comptes d'une
instance existante, sans toucher données/paramètres/secrets. Ce n'est pas 5C.

## Utilisation privée et tests

```python
from installer.database_step import DatabaseStep, SqlAuthorityCredentials, WEB_COMMIT
from installer.php_transport import WEB_REPOSITORY

client = DatabaseStep(runtime, acquired_web_source,
                      repository=WEB_REPOSITORY, commit=WEB_COMMIT)
# migration et authority sont reçus par des canaux privés de l'orchestrateur.
result = client.prepare(payload_5a, migration, config_root=config_root,
                        confirmed=True, authority=authority_for_managed_or_none)
```

Les 331 tests core précédents sont conservés et 22 tests de contrats/fichiers sont
ajoutés. Ces derniers simulent explicitement SQL, mais exercent réellement les
identités/permissions/chargeurs. La campagne indépendante de 18 scénarios exécute
MariaDB et les sources Web exactes, avec provisioning, audit, fresh, conservation
de l'existant, DDL partiel, crash/réponse perdue, échec disque, TLS positif/négatif,
IPv6/ports et get_pdo sous l'identité Web. La fixture de DDL invalide est une copie
repinnée uniquement dans le test ; jamais un assouplissement du pin de production.

```bash
# Uniquement hôte/runner root jetable, aucun service SQL existant sur 3306.
HESTIA_ACCOUNT_DB_TEST=1 ./scripts/quality-local.sh
HESTIA_ACCOUNT_DB_TEST=1 HESTIA_DATABASE_STEP_TEST=1 \
  python3 tests/integration/database_step_mariadb.py --web-source /chemin/source-web-exacte
```

La campagne TLS emploie des bases, datadirs, certificats, identités et un hostname
jetables, en local. Elle ne contacte pas un serveur d'entreprise. Aucune installation
sur LAB-PAWEB30 ou base de production. La Quality Web complète qualifie séparément
ses parcours legacy fresh/upgrade ; les résultats exacts, commits, campagnes et
limites de cette livraison sont consignés dans le rapport et Installer #13/Web #135.

L'Assistant, la conservation/remplacement/désactivation de sa clé, l'activation des
fichiers Web et le scellement sont les seuls sujets de la prochaine étape 5B2.3.
Ne pas les anticiper dans le wizard et ne pas annoncer la Phase 5 complète terminée.
