# Phase 5C - Upgrade, sauvegarde, reprise et retour arrière

## Découpage retenu après la clôture de 5B

Le lot précédent est publié : Installer `fe912b7a7ce2622734d0b508683de3b4e55dc2dd`
et Web main/dev-Bastien `46c03060625d4d53c675474b11aaa33007d9aad7`.
Le contrat privé fresh de 5B reste acquis. Les services, écrans et la recette
système de bout en bout restent 5D. Aucune adoption implicite d'une instance legacy.

| Sous-lot | Frontière | Statut du présent gel |
| --- | --- | --- |
| 5C1 | Précontrôle réel, inventaire borné, rapport immuable et non exécutable | Implémenté ici ; publication/Quality à vérifier sur le commit |
| 5C2 | Sauvegardes privées et preuve de restauration sur cible isolée | À réaliser |
| 5C3 | Catalogue de transitions explicites, migration et bascule contrôlée | À réaliser |
| 5C4 | Reprise, réponse perdue, retour arrière et qualification globale 5C | À réaliser |

Chaque sous-lot doit conserver tous les tests antérieurs et documenter son propre
périmètre. Ni la présence d'un schéma fresh récent ni un marqueur APP_VERSION ne
suffit à déclarer une ancienne version migratable. L'inventaire des migrations
publiées ne constitue pas leur ordre d'exécution pour une installation donnée.

## 5C1 - API privée d'inspection

```python
from installer.upgrade_preflight import UpgradePreflight

reader = UpgradePreflight(runtime, web_source,
    repository="SepuLeVrai/hestia-nexus-avv",
    commit="46c03060625d4d53c675474b11aaa33007d9aad7")
assessment = reader.inspect(upgrade_payload, config_root=private_configuration_root)
report = assessment.report()
```

`runtime`, les chemins protégés et la source sont des paramètres de l'orchestrateur
de confiance, jamais une route HTTP directement exposée. Le payload est le contrat
5A en mode `upgrade`, administrateur absent, mot de passe Admin vide, Assistant
`preserve`, clé vide. Seul le mot de passe du compte SQL applicatif est nécessaire.
Les credentials de migration/autorité, la confirmation d'un apply, le fresh et les
réglages Assistant sont refusés. Aucun endpoint, bouton ou adaptateur du journal
public n'est enregistré.

Le profil reconnu est **SEALED_5B23**, sur le commit exact Web ci-dessus et le
marqueur `3.0.0.0-stable-20260914`. La source proposée est actuellement le même
commit que l'instance contrôlée : `target_relation=IDENTICAL_RELEASE`. Ce n'est
pas une transition migratoire implémentée, et même ce cas n'autorise aucun apply.
Un autre commit, une version différente, une installation sans reçu final valide
ou une configuration ancienne ne bénéficie d'aucune adoption automatique.

## Vérifications réellement exécutées

Le parent réutilise les contrôles existants de 5B : identités séparées, ancêtres
root-owned, répertoires non inscriptibles par le Web, absence de liens/ACL, code
runtime source et cible épinglé, configuration préparée, pointeur, lock, sceau et
reçu final cohérents. Il ne répare et ne réécrit aucun de ces fichiers.

La clé Assistant est lue comme des données, sous verrou partagé non bloquant.
Une clé invalide, un fichier lié ou trop permissif, un éditeur actif ou une trace
de réglage incomplète bloque le précontrôle. Le verrou de réglage existant est
pris en lecture lorsqu'il existe ; aucun nouveau fichier de verrou n'est créé.
La configuration, les reçus, le code et le journal sont relus avant de rendre le
résultat ; une dérive détectée bloque la réponse positive.

Deux sondes sous l'identité Web vérifient l'activation et l'état Assistant autour
de l'inventaire. Celui-ci utilise une copie privée du moteur exact, un worker PHP
non privilégié, des pipes bornés et l'environnement fermé de 5B. Les droits DML
sur le seul schéma sont réaudités. Le chemin TLS partagé vérifie la CA, le nom,
le certificat et le chiffrement établi, sans repli en clair.

L'inventaire exécute uniquement des requêtes fixes, sous une transaction MariaDB
`READ ONLY, WITH CONSISTENT SNAPSHOT`, en isolation `REPEATABLE READ`, fermée par
`ROLLBACK`. Les limites de requêtes sont des réglages de session, jamais globaux.
Le délai mural PHP est conservé et les attentes/requêtes SQL sont bornées à cinq
secondes chacune. Un dépassement est un refus, pas un résultat partiel accepté.

Les six tables de contrôle sont UserInfo, Roles, Role_Rbac_Action,
User_Rbac_Override, Sec_User_Session et App_Config. Elles doivent être des tables
InnoDB réelles. Les effectifs sont rendus en chaînes décimales exactes, y compris
zéro, sans conversion flottante. Un Admin général actif doit exister, sans lecture
ni réinitialisation de son mot de passe. Aucun login utilisateur n'est déclenché.

Les métadonnées portent sur tables, colonnes et index visibles. Limites : 512
objets, 16384 lignes de colonnes/index, 8 Mio par ensemble de métadonnées. Les
commentaires, valeurs par défaut, corps de vues/routines et données métier ne
sortent pas du serveur dans cet inventaire. Seuls des compteurs, des booléens et
une empreinte structurelle partielle sont rendus. Deux lectures de métadonnées
incohérentes sont refusées. Les tables supplémentaires et vues ne sont ni lues
comme données, ni supprimées.

## Résultat et limites obligatoires

```text
state = UPGRADE_PREFLIGHT_READY
plan.apply_allowed = false
plan.target_relation = IDENTICAL_RELEASE
plan.migration_catalog = NOT_DELIVERED
plan.backup_verified = false
plan.rollback_verified = false
plan.preservation_verified = false
plan.application_installed = false
plan.system_qualification_required = true
```

`UpgradeAssessment` conserve des octets JSON canoniques immuables. `report()` rend
une copie indépendante et `plan_sha256` permet d'identifier son contenu. Cette
empreinte n'est ni une signature, ni une capacité d'exécution, ni une garantie
que l'instance n'a pas changé depuis l'inspection. Aucun plan reçu de l'extérieur
n'est interprété pour exécuter une action. Il n'existe pas de méthode `apply` ici.

L'empreinte de métadonnées **n'est pas une certification du schéma complet** :
contraintes, triggers, événements, routines et objets DEFINER nécessiteront une
inspection privilégiée en 5C2/5C3. La visibilité reste celle du compte applicatif.
Les tables supplémentaires non transactionnelles et les vues sont signalées par
des compteurs ; elles ne sont pas déclarées sauvegardables ou migratables.

L'inspection est ponctuelle et ne met pas le Web en maintenance. Les données
peuvent changer après le snapshot ; une modification aller-retour entre deux
contrôles n'est pas une exclusion globale de concurrence. Une sauvegarde et une
bascule devront revalider et maîtriser leur propre frontière. Les sessions SQL
sont fermées et les copies temporaires sont nettoyées sur sortie normale ; un
SIGKILL pendant une copie peut laisser du code temporaire dans run_root. Aucun
nettoyage universel ou effacement cryptographique n'est promis.

Aucune écriture dans la base applicative, l'enveloppe d'activation ou les secrets
n'est réalisée. La lecture peut mettre à jour l'atime et les métriques/journaux du
serveur. Le processus produit uniquement une copie temporaire de code/CA dans son
répertoire privé ; aucun mot de passe n'y est enregistré. La sonde ne contacte
pas l'API Assistant : `api_access=NOT_TESTED` reste explicite.

## Tests et frontière de qualification

`tests/test_upgrade_preflight.py` ajoute 25 tests de protocole, chemins, secrets,
concurrence, annulation, immutabilité et non-mutation. La baseline core conserve
les 390 précédents et ajoute ces 25, sans retirer ni assouplir une assertion.

`tests/integration/upgrade_preflight_mariadb.py` ajoute 15 scénarios réels séparés :
inspection locale/TLS, installation managed avec compte temporaire déjà retiré,
comparaison de dumps logiques avant/après inspection, clé/paramètres/RBAC/sessions,
Unicode et données longues/BIGINT maximal, version inconnue, table requise absente,
absence d'Admin actif, dérive de droits/code/CA, vues et tables supplémentaires,
refus effectif d'un UPDATE dans le snapshot READ ONLY, arrêt du contrôleur et
login/Dashboard/logout. L'injection de l'UPDATE concerne uniquement une copie de
sonde du banc, pas le code ou les pins livrés. Les autres sondes restent inchangées.

```bash
HESTIA_ACCOUNT_DB_TEST=1 HESTIA_DATABASE_STEP_TEST=1 \
HESTIA_FINALIZATION_TEST=1 HESTIA_UPGRADE_PREFLIGHT_TEST=1 \
python3 tests/integration/upgrade_preflight_mariadb.py \
  --web /chemin/source-web-exacte --report /chemin/rapport.json
```

Ces tests créent leurs propres serveurs/datadirs et refusent un port occupé. Ne
jamais les lancer sur l'hôte de production. Les suites historiques 18 SQL/TLS et
21 finalisation SQL/TLS/HTTP restent indépendantes, ainsi que la Quality permanente
Debian 12/13, 16 DOM et 21 HTTPS natifs. Le réseau d'entreprise et l'upgrade 5C3 ne
sont pas qualifiés par ces tests. Les campagnes finales et versions effectivement
utilisées sont consignées dans le rapport compagnon et Installer #13 / Web #135.

## Livraison

Seul le dépôt Installer évolue pour 5C1. Le Web reste exactement au commit épinglé.
Aucune évolution SQL : schema.sql, install.php, migrations, seeds et version
restent inchangés. Aucun changement Gateway/APK, service ou écran. Les documents,
la baseline et les sources doivent être gelés avant la Quality finale ; ZIP léger
de fichiers complets identique au gel. Ne fusionner aucune branche technique.

Références MariaDB :
[START TRANSACTION](https://mariadb.com/docs/server/reference/sql-statements/transactions/start-transaction),
[Information Schema COLUMNS](https://mariadb.com/docs/server/reference/system-tables/information-schema/information-schema-tables/information-schema-columns-table).
