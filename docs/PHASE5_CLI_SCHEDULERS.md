# Contrat des producteurs CLI et des lanceurs planifiés

## Extension : modèle typé des déclarations

Le [modèle privé](PHASE5_LAUNCHER_OBSERVATIONS.md) met en œuvre la validation
des données et des inconnus décrits ici. Il ne collecte pas l'hôte et ne convertit
pas le catalogue en liste d'exécution. Le reste de ce document conserve le
contrat de source du lot précédent et ses limites.

## État et sources

Ce lot livre un contrat documentaire et un [catalogue vérifiable](PHASE5_CLI_SCHEDULERS.json).
Il ne livre pas encore de lanceur CLI, de collecteur d'inventaire hôte ou de
nouvelle barrière système. Aucun script métier n'est exécuté pour cet audit.

Base Installer `cb561320253ba2d73702acfcca9a041a341ab7fe`, arbre
`de1a1327fa0b1496dc4ba0e9adb184f7ef2248ec`, 187 fichiers. La campagne ciblée
`36222491251` a passé 74 contrôles et 47 scénarios système sous Debian 13,
dans un seul job, sans relance. Elle ne qualifie pas globalement la phase 5.
La dernière base globalement qualifiée reste `3a5e2a44` ; références dans
[le lot HTTP](PHASE5_HTTP_DRAIN.md). Les changements de ce lot sont exclusivement
sous `docs/`. Ils ne transforment pas une validation ciblée en Quality globale.

Web lu sans modification : `2a27c7a1f9fe0a00289eb53278f75d5f230900b7`, arbre
`783be5abdcd5e13addefe96d743eee3a97b7a6de`, 1843 fichiers. Le catalogue couvre
les onze fichiers PHP/shell/Python sous `scripts/` et l'outil historique
`docs/maintenance/hestia_root_archive.py`, soit douze points d'entrée. Il ajoute
trois contextes connexes : conversion PDF, aperçus PNG/SVG et workers HTTP
d'import. Les empreintes et ancres relient chaque constat aux octets exacts.
Ce périmètre n'est ni la liste de tous les programmes exécutables du Web, ni
l'inventaire d'un hôte. Les outils de tests et les dépendances ne deviennent
pas des tâches de production par leur seule présence dans l'arbre.

## Opérations observées et décision de raccordement

Les noms ci-dessous identifient des sources à examiner, pas des commandes à
exécuter sur un serveur. Aucun UID réel d'un déploiement n'est déduit du code.

| Source Web | Effets observés | Identité et frontière |
| --- | --- | --- |
| `scripts/mobile_foundation.php` | `backfill`, `export-subject`, alignements et `cleanup` écrivent le registre SQL. Même `export-subject` peut créer un sujet. Cleanup traite des lots bornés, avec événements et purges. | Identité de l'appelant ; configuration JSON externe et `includes/db.php` généré. Aucun lanceur maintenu par l'Installer. Audit du Web seulement ; aucun chantier appareil/Gateway. |
| `scripts/publish_mobile_update.php` | Lecture de configuration SQL si disponible, copies, versions, manifestes, verrou `publish.lock` et renommages. | Racine externe résolue par App_Config avant environnement ; identité de l'appelant. La distribution reste hors de ce chantier. |
| `scripts/hestia_ai_setup.php` | Appel API, comptes/grants SQL, configuration et consommation. `--disable` ne supprime pas le test API initial. | Root demandé si l'extension POSIX est présente ; chemins historiques `root:www-data`, différents de l'enveloppe gérée. Non compatible implicitement. |
| `scripts/hestia_ai_preflight.php` | SELECT, essai de refus d'UPDATE sans ligne, appel API et écriture de consommation. | Pas une sonde en lecture seule. Configuration historique et `usage_dir` à résoudre ; aucune exécution par l'inventaire. |
| `scripts/hestia_ai_toggle.php` | Status charge un fichier PHP externe ; enable/disable remplace atomiquement la configuration. | Mutation root si POSIX disponible, fichier `root:www-data 0640`. Ne pas déclarer le chargement d'une configuration PHP arbitraire sans effets. |
| `scripts/hestia_ai_runtime_repair.php` | Permissions, client MariaDB par socket, GRANT, éventuelle réécriture de configuration et création de consommation. | Opération privilégiée historique ; ce n'est pas la réparation DEFINER déjà qualifiée de l'Installer. |
| `scripts/setup-hestia-ai-secret-store.sh` | Migration/création du fichier secret, propriétaires et modes. | Root ; fichier affecté au compte Web configuré, `www-data` par défaut. Différent du sceau géré root. |
| `scripts/install_reference_export_runtime.sh` | `apt-get update/install libreoffice-impress`, sonde PHP et lancement de version du convertisseur. | Root, modification de l'hôte. Ne vaut pas acquisition/installation bornée par le profil de paquets de l'Installer. |
| `scripts/mobile_internal_apache.py` | Check global Apache ; apply/install/rollback écrivent vhost, lien, sauvegarde et rechargent `apache2.service`. | Mutations root, verrou propre sous `/run/lock`. Ni les unités dédiées ni le verrou commun de l'instance. |
| `scripts/release_metadata.php` | Lecture textuelle d'un fichier version et sortie standard, sans `require` du fichier choisi. | Outil de lecture ; `--file` reste un chemin libre d'appelant, pas un paramètre permis au wizard. Aucun lanceur géré livré. |
| `scripts/quality-installer-core.sh` | Lance les recettes PHP/Python SQL et HTTP après exigence d'une base isolée. | Outil de test, jamais une tâche métier. |
| `docs/maintenance/hestia_root_archive.py` | Plan/inspection ; apply déplace des recettes sous `/root/_HestiaBCKP` et journalise. | Outil opérateur historique, hors périmètre d'exécution autorisé. Sa recherche indicative de références n'est pas un inventaire exhaustif. |

Les scripts PHP historiques ne changent pas eux-mêmes d'UID pour adopter
l'identité dédiée. Les permissions demandées dans leur code ne prouvent pas
les propriétaires effectifs, les ACL, les groupes ou les montages d'un serveur.
Le catalogue est une classification de sources, jamais une allowlist d'exécution.

## Processus descendants et workers portant un nom trompeur

`includes/reference_export.php` lance LibreOffice pour le PDF ;
`includes/reference_template_layout.php` le lance pour PNG/SVG. Les arguments
sont des tableaux `proc_open`, le cwd et HOME désignent le temporaire de
conversion. Le binaire peut venir de `HESTIA_LIBREOFFICE_BIN` ou de candidats
locaux. Les sorties, profils temporaires et leurs droits doivent être observés
avec le vrai moteur. Le délai applicatif de 45 secondes et `proc_terminate`
du processus direct ne prouvent pas le drainage de tous les descendants.
La preuve cgroup antérieure utilise un convertisseur de fixture ; elle ne
qualifie ni LibreOffice, ni ses formats, ni ses chemins effectifs.

Le watchdog d'import est appelé par `ajax/import_prepare.php`,
`ajax/import_progress.php` et `ajax/import_worker.php`. Il écrit son registre
`.worker-watchdog` sous le stockage d'import et agit sur SQL. Les callbacks
de fin du worker restent des producteurs HTTP à drainer. Ce nom n'autorise
pas à créer un cron ou une unité CLI supplémentaire.

Aucun fichier de service/timer de production ou crontab n'a été identifié
dans ce périmètre Web. La documentation mentionne la possibilité de cron
pour le cleanup mobile sans fournir une observation de lanceur installé.
L'absence de définition dans Git ne prouve jamais l'absence d'un job sur l'hôte.

## Trois classes de lancement à conserver séparées

1. **Activité applicative contrôlée.** Une future opération explicitement
   supportée utilise l'UID/GID dédié, un exécutable et des arguments fermés,
   le pin exact, le gate commun et ses racines externes. L'admission prend
   le verrou partagé avant chargement du code ; le cgroup couvre le processus
   et ses descendants jusqu'à leur disparition. La condition systemd seule
   ne suffit pas, puisqu'un lancement peut être déjà en cours.
2. **Mutation administrative sous maintenance.** Une commande qui change une
   configuration, des comptes SQL ou des permissions nécessite une opération
   privée typée et sa lease exclusive. Elle ne peut pas prendre en plus le
   verrou partagé du prepend alors que l'Installer détient cette lease : cela
   serait un refus ou un blocage. Aucun flag public de contournement du guard
   ne doit être ajouté. La conception de ces opérations reste à faire.
3. **Outil externe, historique ou non résolu.** Il n'est ni adopté ni exécuté
   automatiquement. Sa présence pertinente bloque la preuve de quiescence
   complète tant qu'un raccordement explicite ou son exclusion justifiée n'est
   pas observé. Ne pas arrêter un service étranger ou changer ses permissions.

Le pool FPM fixe le prepend, les variables et les chemins PHP de son runtime.
Un `php script.php` manuel n'hérite pas de la configuration de ce pool.
Les options INI, extensions, variables TMP/HOME/HESTIA, sessions et logs d'un
futur CLI doivent donc être propres, fermés et vérifiés. La sonde CLI antérieure
du guard démontre son comportement, pas le raccordement de toutes les commandes.

## Données exigées pour l'observation hôte future

| Objet observé | Preuve privée à lier à l'instance |
| --- | --- |
| Déclencheur | Système ou utilisateur, cron/anacron/jobs différés, timer/service/path/socket pertinent, unité transitoire ou générée ; définition effective et propriétaire. |
| Chaîne d'exécution | Interpréteur, binaire, script, wrapper et arguments structurés ; cwd, variables pertinentes et versions/empreintes. Aucune évaluation shell ou chargement de PHP pour lire une configuration. |
| Identité et destinations | UID/GID/groupes réels, cgroup, fichiers de configuration, racines de données/temp/log, montages/ACL et identités SQL. Les secrets ne figurent pas dans le rapport public. |
| État actif | Jobs en attente, processus et descendants, déclencheurs encore armés. Un fichier d'unité ou une liste de quatre noms n'est pas cette preuve. |
| Couverture et fraîcheur | Origine, visibilité, instant et limites de lecture ; définitions relues avant mutation et lors du reçu. Inaccessible, tronqué, dynamique ou inconnu signifie non résolu, pas absent. |

Il faudra examiner les fichiers hôte et surtout les configurations effectivement
chargées, y compris les lanceurs utilisateur et les wrappers externes. Les
chemins usuels `/etc/crontab`, `/etc/cron.*`, spools utilisateurs et répertoires
systemd sont des points d'entrée, jamais une liste réputée complète. Les scripts
Update opérateur, sauvegardes externes et clients distants ne se déduisent pas
des sources Web. Les autres identités et les événements SQL restent dans leur
groupe d'audit ; le census du seul compte Web ne les exclut pas.

Le futur protocole devra fermer l'admission de chaque lanceur géré, publier
le gate commun, drainer les activités et jobs déjà lancés, vérifier les cgroups,
puis relire les observations. Son ordre exact devra être qualifié contre les
courses de lancement, une mort du contrôleur et un réarmement. Aucun fichier
de cron étranger n'est supprimé ou désactivé pour fabriquer une preuve verte.
Le contrat ne prétend pas empêcher une intervention concurrente de root.

## Qualification de ce lot et suite bornée

Vérifications locales seulement : identité complète des deux arbres, catalogue
des douze entrées et trois contextes, toutes les empreintes/ancres, absence de
modification de code/tests/workflows et gardes statiques de l'Installer.
Aucun PHP métier, appel API, SQL ou inventaire système local. Aucun Actions
nécessaire pour ce lot documentaire ; aucune nouvelle qualification runtime.
Le checkpoint contient les preuves et le vérificateur reproductible.

Tous les indicateurs globaux restent faux : `storage_inventory_complete`,
`system_wiring_verified`, `complete_web_backup`, `service_activation_delivered`
et `phase5_complete`. Aucun lancement CLI géré n'est déclaré livré.

Prochain lot limité : implémenter le modèle privé d'observations de lanceurs
et la validation de leur couverture, en lecture seule, avec états explicites
inconnu/non résolu. Commencer par le contrat de données et ses tests locaux ;
ne pas lancer de commande métier ni élargir HttpDrain à un CLI fictif. Une
collecte système réelle, son drainage et leur qualification seront des lots
distincts. Après checkpoint, arrêt avant de commencer ce prochain lot.
