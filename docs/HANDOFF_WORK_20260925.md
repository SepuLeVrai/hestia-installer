# Handoff WORK - 5C2a, prochaine frontière 5C2b

## Nouveau checkpoint — acquisition et installation des paquets

Base qualifiée `870560b61ebc271d8979741b1f6b074a76c93251`. Lire le
[contrat paquets](PHASE5_SYSTEM_PACKAGES.md). Deux confirmations privées :
acquisition officielle puis installation du digest figé hors réseau. Refus des
paquets/services existants, upgrades, suppressions, hooks et policy arbitraires.
Services par défaut durablement masqués ; reçu absent ou dérive = inspection
manuelle, sans réparation automatique. Aucun flag final Web levé.

Exiger 600 core, 47 recettes système historiques et 13 recettes paquets par
Debian, 16 DOM, 21 HTTPS, 118 SQL/HTTP sans skip. Dix manifestes système identiques
à Quality, réseau réellement déconnecté, échec de reçu après dpkg réellement
exercé. Préserver la limite Debian 12/PHP 8.2 et l'intermittence DOM historique.
Suite : activation TLS/proxy, producteurs/stockages complets, 5C2/5C3/5C4/5D.
Aucune PR/promotion avant tous les contrôles et documentation terminée.
Les sections suivantes sont historiques.

## Nouveau checkpoint — création du compte système

Base qualifiée `37de8a99e66aeff24b3cb3a1958968f1f4df2ecc`. Lire
[PHASE5_SERVICE_IDENTITY.md](PHASE5_SERVICE_IDENTITY.md). Identité neuve créée
par useradd, verrouillée et sans home, sous journal exclusif. Aucune adoption,
suppression ou relance d’empreinte partielle. Les services consomment ce compte
dans la recette jetable, sans démarrage produit livré. Exiger 576 core et
47 recettes système par Debian, 16 DOM, 21 HTTPS et 118 SQL/HTTP sur l’arbre gelé.
Huit manifestes système identiques à Quality. Préserver l’intermittence DOM
historique non résolue. Suite : paquets officiels/activation, producteurs et
stockages complets, fin de 5C2/5C3/5C4/5D. Sections suivantes historiques.

## Nouveau checkpoint — collecteur privé de sessions

Base `6477faf85d5f31f1ca645cd3de89b780ed119a07`. Le lot
[PHASE5_SESSION_CLEANER.md](PHASE5_SESSION_CLEANER.md) prépare le collecteur UID
dédié et son timer sous maintenance ; ni démarrage ni enable automatique.
Conserver la durée technique 43200, la politique fonctionnelle 1h/4h/8h et le
nettoyage Debian natif. 558 core et 35 recettes système par Debian, 16 DOM,
21 HTTPS et 118 SQL/HTTP requis sans skip sur l'arbre exact. Les preuves après
gel documentaire sont livrées dans le checkpoint compagnon. Garder visible
l'intermittence DOM historique non résolue. La suite reste activation contrôlée,
producteurs/stockages exhaustifs puis fin de 5C2/5C3/5C4 et 5D. Pas de promotion
ni serveur existant touché. Les sections suivantes sont historiques.

## Nouveau checkpoint — runtime HTTP préparé sous maintenance

Repartir de la base qualifiée `e86ecd7cfabebcfcb141ee2cc66ca9a27eec814d`.
Le lot [PHASE5_HTTP_RUNTIME.md](PHASE5_HTTP_RUNTIME.md) fournit un adaptateur
privé de staging Apache/FPM et cinq répertoires PHP dédiés. Il ne crée pas les
comptes, n'installe pas les paquets et ne démarre aucun service. Récupération
par observation exacte, empreinte partielle conservée, pas de suppression/rejeu.
Avant toute promotion : 542 core par Debian, 11 + 12 recettes systemd par Debian,
16 DOM, 21 HTTPS et 118 SQL/HTTP, toutes sans skip sur le commit gelé et avec
sources stables. Preuves finales et ZIP exact dans le checkpoint compagnon.
Le nettoyage natif, activation Web/TLS, données exhaustives, 5C2/5C3/5C4 et 5D
restent à compléter ; PHP 8.2 Debian 12 ne qualifie pas le Web complet.
Les sections suivantes décrivent les checkpoints antérieurs.

## Reprise active - arrêt contrôlé des services

Base exacte : `ed7707c9eb7f6314e5d3d3b899f0efe1cef92d53`, arbre
`7b7b4141137a6341a00e0210c9ac84ff736ea184`. Quality `36139574996` et SQL
`36139636592` verts. Lire [PHASE5_SYSTEM_DRAIN.md](PHASE5_SYSTEM_DRAIN.md).
Le nouveau candidat arrête seulement quatre unités déjà provisionnées,
contrôle les cgroups et garde la maintenance après interruption. Aucun
redémarrage ou déploiement implicite. Les tentatives servent à la reprise
exacte des arrêts, jamais au rejeu SQL. Cible : 524 core par Debian, onze vrais
scénarios systemd par Debian, 16 DOM, 21 HTTPS et 118 SQL/HTTP.
Prochaine frontière : vrais profils services/données, sessionclean natif,
remise en service, raccordement sauvegarde puis vraie transition/rollback.
Conserver les limites explicites, pas de clôture Phase 5 ni de promotion
sur la seule réussite du banc systemd. Preuves finales dans le checkpoint.

## Reprise active - inventaire contrôlé

Base : d2f2d0af85bbe4f6167bb1c96065c224f22fb383, arbre
06d952a1b7c59d5eeb714703f7f4b697543a04a1. Quality36136210321 et SQL36136276965
verts. Le candidat suivant cartographie les stockages et producteurs ; lire
[PHASE5_STORAGE_INVENTORY.md](PHASE5_STORAGE_INVENTORY.md). Il ne livre pas encore
le profil système permettant les données inscriptibles sous webroot.
Attention : réception multipart avant le guard et descendants après mort de PHP
nécessitent des barrières au niveau des services. Ne pas transformer les tests
qui reproduisent ces limites en attestation de sauvegarde complète.
Qualification attendue : 506 core par Debian, 16 DOM, 21 HTTPS, 118 SQL/HTTP.
Commit et runs finaux dans le checkpoint compagnon, sans modification après gel.
Suite : profil système fermé, puis vraie transition/reprise/rollback et wizard5D.


## Reprise active - coordination SQL et données

Partir du candidat fichiers qualifié `a19c40318dfac958330fda8890116c6580e3ae43`
(arbre `6961f5b84103a7d418c1caa68611a7cc6b007bb5`, Quality `36133121054`).
Le nouveau lot ajoute une composition privée sous lease, sans modifier Web.
Lire [son contrat](PHASE5C2_COORDINATED_BACKUP.md) avant toute suite.
Gel de ce candidat avant qualification : 488 core par Debian et 110 SQL/HTTP,
plus 16 DOM et 21 HTTPS. Le checkpoint compagnon fournira commit et runs finaux.

Prochain lot : fermer le profil de stockage réel et ses producteurs, notamment
les arbres métier sous webroot encore refusés par le contrat immuable. Ne pas
annoncer de sauvegarde complète, d’upgrade ou de rollback à partir de ce reçu.
Conserver les checkpoints courts demandés et les pins ; ni PR ni promotion
avant tous les gates et la documentation du périmètre réellement livré.
Les passages suivants relatent les checkpoints précédents.

## Reprise après interruption du stream, lot court données

Sources restaurées et comparées à leurs objets Git : 132 fichiers Installer
`726757eeb139818a7aa93b90586091ff37a08f85`, 1840 fichiers Web
`46c03060625d4d53c675474b11aaa33007d9aad7`. HEAD actifs relus inchangés.
Le checkpoint réparation est vert (Quality36126405630, SQL36126448323).

Lot courant : `installer/backup_files.py`, tests obligatoires et contrat
[PHASE5C2_DATA_FILES.md](PHASE5C2_DATA_FILES.md). Il doit être qualifié avant
la suite. Aucun raccordement SQL/système ni support d'upgrade n'est ajouté ici.
Prochain petit lot : composer cette preuve avec la sauvegarde SQL sous la même
maintenance et dériver toutes les racines de la configuration supportée.
Garder la mission globale 5C puis 5D, avec checkpoints courts documentés.


## Reprise WORK active

Le mandat actuel couvre toute la fin de 5C puis 5D. Voir
[le candidat DEFINER et ses limites](PHASE5C2_DEFINER.md). La reproduction du
défaut est acquise ; la qualification du futur provisioning corrigé est en cours.
La réparation de l'existant, les fichiers/sessions, transitions et services
restent obligatoires. Aucune promotion du candidat n'est encore revendiquée.

## Références à relire

Base Installer : c0dcb902663130302599635b36c7fb8deab80a47, arbre
9d9a13fbd94f954894e6a65c9434632a5b98b3a6. Web main/dev-Bastien inchangées :
46c03060625d4d53c675474b11aaa33007d9aad7, arbre aaac278270e0fd1169396945916dfe997ae078bf.
Le nouveau HEAD et les campagnes réussies se lisent dans le commit publié et les
derniers commentaires Installer #13 / Web #135. Ce fichier gelé avant Quality
ne prouve pas une promotion à lui seul. Ne pas reprendre une branche technique.

Lire [PHASE5C2_BACKUP.md](PHASE5C2_BACKUP.md), [PHASE5C_UPGRADE.md](PHASE5C_UPGRADE.md),
[QUALITY.md](QUALITY.md), [PROJECT_STATE.md](PROJECT_STATE.md), puis les contrats
5B [PHASE5B23_FINALIZATION.md](PHASE5B23_FINALIZATION.md) et
[PHASE5B22_DATABASE_PREPARATION.md](PHASE5B22_DATABASE_PREPARATION.md).

## État réel et découpage

5C1 reste l'inspection non mutante. 5C2a ajoute UpgradeBackup.create_and_verify,
backup_runtime.py et le worker backup_bridge.php, sans route ou bouton public.
Périmètre : instance SEALED_5B23 exacte, données SQL InnoDB, cinq triggers
canoniques avec DEFINER valide, déploiement root-owned non mutable et enveloppe
privée. Source non modifiée ; restauration dans une nouvelle MariaDB sans TCP.

Résultat limité BACKUP_RESTORE_VERIFIED, backup_verified=true, restauration SQL
et fichiers privés prouvée, trigger_smoke_verified=5. Mais apply_allowed,
restore_to_original_allowed, rollback_verified, web_activation_verified et
application_installed restent faux. Fichiers métier modifiables refusés,
sessions PHP externes non copiées ; aucun reset Admin, migration ou activation.
Les comptes d'authentification SQL source ne sont pas exportés.

**5C2 n'est pas clôturée. Prochaine exécution : 5C2b**, pas 5C3.
5C3 reste migrations/bascule,5C4 reprise/rollback/qualification,5D services/écrans.
Les anciennes garanties sont conservées mais un cas non couvert précédemment est
maintenant signalé comme défaut bloquant de managed.

## Défaut réel à traiter avant clôture de 5C2

La recette test_backup_managed_orphaned_definers_are_not_certified_or_repaired
reproduit fresh managed5B2.2, finalize5B2.3 puis une invocation réelle du trigger.
Les cinq triggers gardent le compte de migration temporaire comme DEFINER alors
que ce compte a été supprimé. INSERT Ged_Legacy_Stat échoue avec MariaDB1449.
BACKUP_DEFINER_MISSING bloque la sauvegarde certifiée, sans créer de compte.
C'est une fixture jetable, pas une inspection ou modification de LAB-PAWEB30.

5C2b doit concevoir puis qualifier des DEFINER durables, séparés du compte runtime
DML et de l'autorité/migration éphémère. Aucun élargissement caché des quatre
GRANT applicatifs. Éviter un compte root/global ou un compte de migration durable
comme raccourci. Prévoir identité non connectable, droits stricts, responsabilité
et nettoyage explicites. Vérifier aussi les cas refusés et l'exécution métier.

Distinguer le provisioning futur de la réparation d'une instance déjà affectée.
La réparation éventuelle doit être volontaire, contrôlée et documentée, jamais
un effet secondaire de backup ou de preflight. Préserver le refus de fresh sur
existant, les .attempt, sources épinglées et l'enveloppe d'activation.
L'audit actuel de DEFINER ne reconnaît que le profil provisioning existant sur
seul schéma ; adapter son contrat au nouveau profil seulement avec preuves,
pas avec une règle générique pour faire passer les tests.

## Sauvegarde et sécurité à préserver

Consentements exacts confirmed=true et allow_global_read_lock=true. Verrou
FLUSH TABLES WITH READ LOCK temporaire sur tout le serveur, donc risque de
blocage des écritures des autres bases : pas d'exécution implicite.
Autorité fournie ALL global/GRANT OPTION, distincte du DML, uniquement stdin.
Ne pas présenter ce profil administratif comme un minimum universel.

Source contrôlée via préflight, compte, SQL/TLS, fichiers, verrou Assistant et
relectures. Worker OS non privilégié, sorties/délais/volumes bornés, erreurs fixes.
Archive NDJSON privée : DDL, cellules hex/NULL, flottants préservés en DOUBLE
natif +17 chiffres. Tri des longs textes sans dépendre de max_sort_length.
Les mutations SQL ne touchent que le serveur vérificateur nouvellement créé.

Vérificateur : version serveur exactement égale, nouveau datadir privé, socket
sans TCP, nouveaux comptes/scéma uniquement, import sans droits globaux du
restaurateur, triggers canoniques sous identités ACCOUNT LOCK. Comparaison des
lignes/DDL, FK, puis cinq effets de triggers avec compte DML. Aucun credential
source dans le vérificateur. Nettoyage contrôlé des objets et du processus.

Fichiers : slots0700, blobs/manifeste/reçu0600. Copier octets/modes/uid/gid,
config SQL/TLS, CA, Assistant, pointeur, lock, sceau et reçus ; aucune clé effacée
sur champ vide. Les pointeurs restaurés sont des DONNÉES, jamais exécutés dans
le clone. Le périmètre mutable/GED/sessions externes n'est pas certifié par ce lot.
Les futures étapes devront réévaluer ces préconditions, pas déduire une sauvegarde
globale d'un résultat scoped. Aucun hash seul ne vaut preuve de restauration.

Échec : BACKUP_INCOMPLETE, reçu de succès non publié, source intacte, artefacts
partiels privés conservés. SIGKILL peut laisser du staging ou un reçu après une
vraie vérification ; aucune récupération automatique/permission de retry implicite.
Pas de rollback DDL atomique, pas de réactivation Web et pas d'effacement crypto.

## Quality et livraison

456 core attendus :415 conservés+41nouveaux,16gate inclus dans core,16DOM et21HTTPS
natifs historiques. Nouvelle recette20scénarios SQL/TLS/HTTP, séparée des54anciens
(18SQL/TLS+21finalisation+15précontrôle). Le refus attendu des DEFINER absents ne
signifie pas que managed est devenu fonctionnel. Consulter les preuves finales.

PHP8.4.24/MariaDB11.8.6 local ne vaut pas matrice SQL exhaustive. Web inchangé,
aucune ancienne campagne Web recyclée en preuve d'un nouveau changement. Aucun
schema.sql/install.php/migration/seed/version modifié dans5C2a.5C2b devra les
mettre à jour intégralement si sa correction le nécessite.

Docs/README/baseline et fichiers complets gelés avant Quality. ZIP léger exact,
application et réapplication sur sa base, sources/modes identiques. Toute retouche
après campagne impose nouvelle qualification. Les métadonnées finales et SHA/runs
vont dans le rapport compagnon et les issues, pas dans un commit post-Quality.
Écritures autorisées, promotion fast-forward force=false après Quality complète,
HEAD relus et comparaison sans divergence. Aucun serveur/Gateway/APK touché.
#13/#135 restent ouvertes. Ne pas annoncer5C2/5C/Phase5 terminées.
# Checkpoints WORK après reprise

La mission reste la fin de toute la Phase 5, sans déploiement de production.
Main n'est pas promue. Le futur managed corrigé, la sauvegarde de secours et la
barrière de maintenance sont qualifiés dans le checkpoint Installer
`3d7440157a3251b54be820d1af5acaa8794a02f6` : Quality `36125259959`, recette technique
`36125303870`, 91 scénarios SQL/HTTP distincts. Les branches Web verification
contiennent seulement le banc et ne doivent jamais être fusionnées.

Le candidat suivant livre [la réparation explicite](PHASE5C2_REPAIR.md), encore
en qualification. Lire aussi [la maintenance](PHASE5_MAINTENANCE.md) et
[le secours](PHASE5C2_RESCUE.md). Restent ensuite le périmètre mutable/sessions,
la vraie transition de release, la reprise/rollback et le raccordement complet
Apache/FPM/wizard Debian12/13. Les interruptions DDL de réparation restent
interloquées pour inspection, sans reprise automatique. Ne pas déclarer terminé.
