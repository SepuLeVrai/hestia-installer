# Stockages et producteurs du Web épinglé

## Identités observées de quelques leaders

Le [nouveau lecteur](PHASE5_PROCESS_IDENTITY.md) apporte un signal effectif de
revue pour les leaders sélectionnés, sans observer toutes les tâches ou leurs
écritures. Il ne transforme pas StorageRequirements en DataInventory et ne
certifie aucun des neuf groupes. Stockages, sources Web et sauvegarde inchangés.

## Observation partielle des unités connues

Le [collecteur systemd provisionné](PHASE5_SYSTEMD_OBSERVATIONS.md) apporte
une lecture des unités connues, sans valider les neuf groupes de producteurs.
Aucun chemin de stockage ni identité d'écriture effective n'est déduit d'un
nom de service. Les besoins restent non résolus et aucune sauvegarde exhaustive
n'est autorisée par cette observation. États historiques ci-dessous.

## Consommation privée par le modèle de lanceurs

Le [validateur de déclarations](PHASE5_LAUNCHER_OBSERVATIONS.md) lie l'empreinte
de StorageRequirements à sa cible et exige des références de rôles connues.
Il conserve les blocages et les neuf groupes non vérifiés. StorageInventory
reste inchangé ; cette composition ne produit ni DataInventory ni autorisation
de sauvegarde ou d'arrêt, même avec des déclarations apparemment complètes.

## Contrat détaillé des CLI et lanceurs

Le [catalogue de sources](PHASE5_CLI_SCHEDULERS.md) documente les commandes,
identités attendues par leur code, écritures et frontières. Il distingue CLI,
convertisseurs descendants et watchdog HTTP. Il ne modifie pas StorageInventory
et ne remplace aucune observation effective de chemin, propriétaire, montage,
SQL ou planificateur. Les neuf groupes restent requis ; aucun inventaire hôte
complet ni couverture automatique des CLI n'est déduit du catalogue.

## Extension source externe explicite

Le nouveau pin du [profil métier](PHASE5_BUSINESS_STORAGE.md) exige une
observation explicite de `HESTIA_UPLOAD_STORAGE`, même vide. Sa valeur externe
ajoute `uploads_effective` et déplace la résolution des GED historiques relatives.
Le scope `uploads` du webroot est conservé pour ne pas oublier des données
historiques. Le pin précédent conserve son schéma d'observations. Les neuf
producteurs restent non certifiés par cette cartographie seule.

## Périmètre du lot

Base qualifiée : Installer `d2f2d0af85bbe4f6167bb1c96065c224f22fb383`, arbre
`06d952a1b7c59d5eeb714703f7f4b697543a04a1`. Quality `36136210321` et SQL/HTTP
`36136276965` verts : 488 core par Debian, 16 DOM, 21 HTTPS et 110 scénarios
SQL/HTTP. Ces résultats qualifient le lot coordonné précédent, pas ce candidat.
Web reste `46c03060625d4d53c675474b11aaa33007d9aad7`.

`installer.storage_inventory.StorageInventory.inspect` résout une cartographie
privée à partir d'observations explicites du déploiement et du code Web épinglé.
Il vérifie l'empreinte runtime avant et après calcul. Il ne lit pas les secrets,
n'exécute aucun fichier PHP du serveur, ne lance aucune requête SQL et ne crée
ni ne modifie un répertoire. La configuration effective est un **prérequis
observé par l'adaptateur de confiance**, pas un formulaire de chemins HTTP.

Le résultat `STORAGE_REQUIREMENTS_RESOLVED` est une liste de besoins. Les champs
`filesystem_verified`, `storage_inventory_complete`, `system_wiring_verified`,
`complete_web_backup`, `apply_allowed`, `rollback_verified` et
`application_installed` restent faux. Il n'existe pas de conversion automatique
vers `DataInventory` : les chemins, propriétaires, montages, producteurs et
répertoires réellement présents nécessitent encore la qualification système.

## Sources de configuration et stockages

| Famille | Résolution du Web et contenu à conserver | Producteurs / code audité |
| --- | --- | --- |
| Fichiers métier | Tout `uploads`, dont GED, héritage, photos, éditeurs, distributeurs, références, modèles et qualifications mobiles | `includes/ged/helpers.php`, `includes/reference_templates.php`, `ajax/referentiel_admin.php`, `ajax/profile_photo_upload.php`, `ajax/affaire_reference_save.php`, `api/mobile/v1/_cockpit.php`, `api/mobile/v1/profile_photo.php`, `includes/mobile_foundation/business_photos.php` |
| Imports PA/DAR | Constante `HESTIA_IMPORT_STORAGE`, sinon variable homonyme, sinon `var/imports` ; fragments, manifestes, verrous, registres et quarantaines | `includes/import_engine.php`, `includes/import_watchdog.php`, `ajax/import_*.php` |
| Imports Références | Stockage d'import prioritaire ; repli `sys_get_temp_dir()/hestia-reference-import-<hash16(APP_ROOT)>` si indisponible | `includes/reference_import_engine.php`, `ajax/reference_import_*.php` |
| GED externe | `HESTIA_GED_LEGACY_ROOTS`, liste de chemins absolus séparés par virgule | `includes/ged/helpers.php` ; éventuels producteurs externes à auditer |
| GED héritée configurée | `security.ged_legacy_roots` en SQL, uniquement des chemins relatifs sous `uploads/ged_legacy` | `includes/ged/helpers.php` |
| Sessions | Valeurs PHP effectives `session.save_handler`, `session.save_path`, `session.gc_maxlifetime` | `includes/bootstrap.php`, `includes/session_policy.php`, GC PHP et nettoyage Debian |
| Temporaires et uploads PHP | `sys_get_temp_dir()` effectif, `upload_tmp_dir` distinct et repli système ; appairage `hestia-pair-<hash20(APP_ROOT)>`, portabilité `hestia-portability`, exports et aperçus | `includes/mobile_pairing.php`, `includes/app_portability*.php`, `includes/reference_export*.php`, `includes/reference_template_layout.php`, PHP multipart |
| Assistant géré | Enveloppe scellée, notamment `assistant.json`, sans lire la valeur de clé dans l'inventaire | `includes/assistant/managed_secret.php`, `includes/installation/activation.php`, Installer finalization |
| Ancienne configuration IA | Candidat explicite `HESTIA_AI_CONFIG_FILE`, `/etc/hestia/conf_db_ia.php`, ancien `includes/conf_db_ia.php` ; sélection réellement observée | `includes/ai/config.php`, `includes/assistant/config.php`, scripts de configuration IA |
| Consommation IA | `usage_dir` issu de la configuration PHP, défaut applicatif `/var/lib/hestia-ai` ; fichiers mensuels budgétaires | `includes/ai/CostControl.php` et scripts IA |
| Distribution mobile existante | `App_Config.HESTIA_MOBILE_RELEASE_DIR` non vide prioritaire sur l'environnement ; versions, manifestes, suspension et verrou de publication | `includes/mobile_updates.php`, `includes/mobile_release_admin.php`, `scripts/publish_mobile_update.php` |
| Configuration Mobile Foundation | Fichier `HESTIA_MOBILE_FOUNDATION_CONFIG`, distinct des packages et de la base | `includes/mobile_foundation/service.php`, `scripts/mobile_foundation.php` |
| Journaux | `error_log` effectif ou routage syslog/service à vérifier ; ne pas inventer un stockage depuis le seul répertoire `logs` livré | Appels PHP `error_log`, configuration PHP et services |

Une constante d'import **définie mais vide** désigne le défaut Web : elle ne
retombe pas sur la variable d'environnement. Les racines par défaut et les
valeurs configurées masquées restent listées, car elles peuvent encore contenir
des données anciennes. Une relation `covered_by` conserve tous les rôles
sémantiques ; elle ne supprime aucun chemin et n'autorise aucune capture.

Le résolveur exige toutes les clés attendues, y compris les valeurs vides
explicitement observées. Les clés inconnues, chemins relatifs ou non canoniques,
traversées, caractères de contrôle, listes excessives et entrées GED invalides
sont refusés. Le Web ignore certaines entrées GED invalides ; l'Installer les
refuse pour empêcher une omission silencieuse dans l'inventaire. Les handlers
PHP autres que `files` et les layouts de sessions répartis en sous-répertoires
restent non pris en charge. Le format plat `0;[mode;]/chemin` est reconnu.

Les répertoires partagés usuels, une rétention statique différente de 43200 et
une configuration IA non observée restent des blocages explicites. Le rapport
public ne contient ni chemin, ni nom de fichier métier, ni secret ; le manifeste
privé contient les chemins et ne doit pas être exposé par le wizard.

## Neuf groupes de producteurs à raccorder

| Groupe | Barrière / preuve attendue |
| --- | --- |
| PHP public | Guard avant application, drainage de la requête et de tous les callbacks de fin |
| PHP mobile interne | Même protection sur le listener interne et toutes ses routes |
| Réception des uploads PHP | Blocage au frontal et drainage multipart/FPM, en amont du guard PHP |
| Nettoyage natif des sessions | Même verrou de maintenance et configuration PHP statique vérifiée |
| Scripts CLI administratifs et mobiles | Verrou commun, coordination des modifications de configuration ; même une commande `export-subject` peut provisionner SQL |
| Convertisseurs externes | Groupe de processus du service, drainage des descendants et des orphelins |
| Écritures Installer | Coordination avec la maintenance et relecture de l'enveloppe privée |
| Planificateurs hôte | Inventaire réel cron/systemd et drainage de tous les producteurs gérés |
| Autres clients SQL | Inventaire des écrivains et respect de la fenêtre de maintenance |

Deux limites doivent être traitées par les services, pas masquées dans le reçu :

- PHP prépare les fichiers multipart **avant** `auto_prepend_file`. Un refus 503 ne prouve donc pas l'absence de toute écriture temporaire. La recette observe un vrai fichier uploadé avant l'exécution du guard, puis vérifie le 503.
- La mort du processus PHP ne garantit pas celle de ses enfants externes. La recette utilise un convertisseur de fixture qui ferme ses descripteurs hérités, survit à un SIGKILL du parent et écrit sous maintenance. Elle matérialise le besoin de drainage du groupe ; elle ne prétend pas avoir qualifié LibreOffice ni un service FPM réel.

Le guard actuel est aussi testé avec un vrai script PHP CLI : refus 75 sous
maintenance, exécution après reprise explicite. Ce test ne prouve pas que tous
les scripts de l'hôte sont effectivement lancés avec ce guard.

## Qualification du candidat et frontière suivante

18 nouveaux tests core obligatoires : cible **506** par Debian 12 et 13 ;
16 DOM et 21 HTTPS inchangés. Ils couvrent priorités, valeurs vides et absentes,
chemins masqués retenus, GED, formats de sessions, limites, confidentialité,
immutabilité et refus des pins divergents. La vérification du pin est simulée
explicitement dans ces tests de résolution.

La recette opt-in `tests/integration/storage_inventory_mariadb.py` exige
`HESTIA_STORAGE_INVENTORY_TEST=1` et les opt-ins hérités. Huit cas utilisent le
Web exact, PHP réel sous l'identité Web et MariaDB jetable : priorités imports,
repli Références, préférence SQL de distribution mobile, GED, configuration IA,
CLI, multipart et descendant orphelin. Les deux dernières réussites prouvent
une limite identifiée, **pas sa correction système**. Les 110 scénarios
historiques restent requis dans cette campagne : total attendu **118**.

La prochaine étape est un profil système fermé permettant le stockage métier
inscriptible, les répertoires temporaires/sessions par instance et l'arrêt ou le
drainage réel de ces producteurs. Le contrat immuable actuel, les pins, l'UX,
le Web, Gateway et APK ne sont pas modifiés par ce lot. Aucune clôture 5C2/5D,
PR, promotion ou intervention sur un serveur existant n'en découle.
Les résultats finaux sont livrés dans le checkpoint compagnon après gel.

## Première campagne et correction du banc

Le candidat c5462be24adebe6299926c3cba94ffc9bc766ed8 a passé Quality36138926817
(506 core par Debian, 16 DOM, 21 HTTPS). La recette36138993018 a terminé les
assertions des huit nouveaux cas, puis leur nettoyage a échoué avec MariaDB1064.
Le dictionnaire de configuration du banc utilisait `self.app`, déjà réservé au
nom du compte SQL par la fixture héritée. Il devient `self.storage_app_config`.
Le contrôleur, la suppression des comptes et toutes les assertions restent
conservés. Les fichiers corrigés exigent une nouvelle qualification complète.
