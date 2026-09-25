# État du projet

## Reprise WORK - candidat 5C2b en préparation

La sauvegarde de secours est implémentée et ses 30 recettes backup passent sur
`6cd77cac61c2f5c7256ad320e3bb3f5b954b7910`. Une erreur de nettoyage de fixture
SQL garde la campagne globale rouge, voir [QUALITY.md](QUALITY.md). La barrière
de [maintenance coordonnée](PHASE5_MAINTENANCE.md) est le candidat suivant ; elle
doit être qualifiée puis raccordée au service réel avant toute clôture 5C/5D.

Checkpoint futur managed : `12a7e8a045b4f62c22b314173ea0900f512f52de`, Quality
`36123060501` verte (456 core Debian12/13, 16 DOM, 21 HTTPS). Les recettes SQL
18 préparation, 21 finalisation, 15 précontrôles et24 backup sont vertes et relues
dans les artefacts de `36123155845`. Aucune promotion. Le lot suivant prépare la
[sauvegarde de secours explicite](PHASE5C2_RESCUE.md), qualification distincte.

La mission porte sur la fin de 5C puis 5D. Le candidat courant prépare la
[correction DEFINER](PHASE5C2_DEFINER.md) pour les futurs fresh managed, avec
restauration isolée du profil durable. Qualification et promotion restent à
confirmer sur le gel final. La réparation de l'existant et la clôture de la
Phase 5 ne sont pas encore réalisées. Aucun déploiement de production.

## Point de reprise - 5C2a, prochaine frontière 5C2b

5C1 est publiée au commit c0dcb902663130302599635b36c7fb8deab80a47. Le présent lot
ajoute une sauvegarde SQL privée avec restauration réelle dans une MariaDB neuve
sans TCP, copie vérifiée du déploiement root-owned et de l'enveloppe privée.
Contrat et limites : [PHASE5C2_BACKUP.md](PHASE5C2_BACKUP.md).

**5C2 reste ouverte** : la recette révèle des DEFINER orphelins dans le fresh SQL
managed du pin précédent. Erreur1449 reproduite ; BACKUP_DEFINER_MISSING refuse
une certification trompeuse. Aucune réparation implicite ou mutation de la source.
5C2b doit traiter ce défaut avant5C3, sans élargir le compte applicatif DML.

Un résultat BACKUP_RESTORE_VERIFIED certifie le profil SQL/fichiers décrit, pas
une restauration du service. apply_allowed, rollback_verified, web_activation_verified
et application_installed restent faux. Les fichiers métier modifiables et sessions
PHP externes ne sont pas déclarés sauvegardés. Wizard toujours « Sources prêtes ».

Web inchangé46c03060625d4d53c675474b11aaa33007d9aad7. Aucun changement schema.sql,
install.php, migration, seed, version, Gateway ou APK ; aucun déploiement serveur.
Relire HEAD, campagnes et derniers commentaires #13/#135 pour les preuves finales.
Reprise : [HANDOFF_WORK_20260925.md](HANDOFF_WORK_20260925.md).
Les sections suivantes sont l'historique, pas le statut du nouveau lot.

## 2026-09-21

Dépôt initialisé.

- issue parent R4 : #1 ;
- architecture Python standard library initialisée ;
- preflight local non destructif disponible ;
- validation FQDN / CIDR initialisée ;
- aucune mutation système implémentée ;
- aucune GitHub Action activée.

## 2026-09-23 - UX figée et acquisition GitHub

- UX du mini-web figée ;
- l'écran Bienvenue devient le préambule `0` ;
- l'étape `1` est réservée à l'accès GitHub en lecture seule ;
- l'installer reste lightweight et télécharge les composants HESTIA dans un staging privé au lieu de les embarquer ;
- le credential GitHub est éphémère et exclu du state, des logs, des URLs, des arguments de processus et des rapports ;
- l'acquisition via API GitHub + standard library Python est privilégiée afin de ne pas imposer `git` au bootstrap.

## 2026-09-23 - Phase 1 bootstrap HTTPS

La première frontière exécutable est implémentée et testée :

1. préflight Debian 12/13, root, Python 3.11+, OpenSSL et iproute2 ;
2. détection des IPv4 et politique fail-closed pour les IP publiques ;
3. port aléatoire `57000-57999` réservé par socket réel ;
4. certificat TLS éphémère avec SAN IP ;
5. code bootstrap one-shot, TTL et limite de tentatives ;
6. mini-web HTTPS avec écran de déverrouillage cohérent avec l'UX figée ;
7. session `Secure`, `HttpOnly`, `SameSite=Strict` et CSRF ;
8. CSP, `no-store`, Host/Origin checks, limites de requête et sécurité des chemins statiques ;
9. nettoyage du staging et contrôle de fermeture du port ;
10. tests collision, IP publique, multi-interface, restart, TLS réel et authentification HTTPS.

Aucune mutation HESTIA, SQL, Apache, NGINX, Gateway ou APK n'est effectuée. Aucun changement de `schema.sql` ou `install.php` n'est nécessaire pour cette phase.

## 2026-09-23 - Phase 2, implémentation locale

Base examinée : `38e956817f1404b78c4608ccd64ac9c7dd15926d`.

Le moteur et sa façade HTTPS sont implémentés : plan immuable inspectable,
confirmation explicite, registre typé, checkpoints persistants, idempotence,
resume conservateur, retry ciblé, rollback par frontière et rapport non secret.
Le journal privé est atomique, verrouillé et protégé contre les symlinks et les
écritures obsolètes. Les états et les erreurs sont des valeurs fermées.

Les tests exercent des mutations réelles dans des sandboxes, des backups réels,
des interruptions de processus après apply, commit et rollback, la concurrence,
la reconnexion HTTPS et la non-régression du bootstrap. Le détail des vérifications
et leurs limites se trouve dans [QUALITY_PHASE2.md](QUALITY_PHASE2.md).

Le seul adaptateur exposé en production est le contrôle `core/preflight.run`.
L'acquisition GitHub, les déploiements Web/Gateway/APK, les mutations SQL et le
branchement des boutons du wizard restent dans les phases suivantes. Aucune
évolution de `schema.sql` ou `install.php` n'est introduite. Aucun asset UI modifié.

Statut de livraison : travaux locaux préparés pour l'issue #4 ; publication sur
`main` et mise à jour de l'issue non réalisées dans l'environnement de préparation,
qui ne dispose pas d'action d'écriture GitHub utilisable. Ne pas interpréter ce
document comme une preuve de commit distant ou de clôture de l'issue.

## 2026-09-23 - Phase 2 publiée et acceptée

Le lot Phase 2 est présent sur main au commit
`3c7453d4ede6bb364f34263ed5929358d7ec1929` et accepté par Bastien.
Le statut local ci-dessus décrit sa préparation historique, pas le HEAD actuel.

## 2026-09-23 - Phase 3 / issue #8

Base de cette évolution : `3c7453d4ede6bb364f34263ed5929358d7ec1929`.

Accès GitHub éphémère aux trois dépôts, sélection des modules, refs figées en SHA,
transport HTTPS GET strict, extraction bornée et acquisition transactionnelle
sont implémentés. Reprise sans réseau des sources déjà prouvées ; nouvelle
validation du credential lorsqu'un téléchargement doit être recommencé.

La documentation et les tests couvrent le journal Phase 2 existant, le bootstrap
HTTPS, les sept combinaisons de modules en modes fresh et upgrade du moteur,
les interruptions de processus, le retry et le rollback ciblé. Résultats et
limites explicites dans [QUALITY_PHASE3.md](QUALITY_PHASE3.md).

Aucun autre dépôt modifié, aucun SQL/install.php à changer, aucune compilation
APK, aucun asset UI modifié. Le credential réel de l'utilisateur n'est pas fourni
à l'environnement de Quality ; les essais sortants utilisent un serveur HTTPS
local contrôlé, pas un téléchargement privé réel depuis GitHub.

## 2026-09-23 - Phase 4 / issue #11

Base : `710760aec85ae96795224adce8e91e37e5cb86e5`.

Les formulaires et boutons des six écrans sont reliés aux opérations typées.
Le brouillon serveur non secret survit à la reconnexion, les prérequis bloquent
la suite, le plan impose une confirmation et le suivi propose retry/rollback
ciblés ainsi qu'un rapport. La composition UX reste celle de référence.

L'issue #4 a été documentée et clôturée pour régulariser la Phase 2 déjà publiée
et acceptée. #11 suit cette livraison fonctionnelle limitée à l'acquisition.
L'issue #3 reste ouverte pour les écrans applicatifs ultérieurs, sans déclarer
qu'une acquisition réussie est une installation complète. Détails dans WIZARD.md
et résultats/limites dans QUALITY_PHASE4.md.

Aucun autre dépôt modifié, aucune évolution SQL/schema.sql/install.php,
aucune compilation Android. Aucun téléchargement privé avec un PAT utilisateur
n'est revendiqué dans l'environnement de Quality.

## Prochaine frontière

Phase 5 : contrat HESTIA Web. Intégrer le parcours réel fresh/upgrade du Web,
sa configuration, sa base et son premier administrateur avec les protections
et tests correspondants. Ne pas simuler le déploiement dans le wizard.

## 2026-09-24 - Consolidation Phase 4 et Quality permanente

La Phase 4 est publiée au commit `2d86b36da536f118ae5dbb79ddbba663644cf18f`.
Le défaut de mode 0644 de son script quality-wizard.sh est corrigé et couvert.
Le workflow Installer Quality, les tests stricts, la matrice Debian 12/13,
le navigateur HTTPS natif et la preuve de packaging exact sont ajoutés.

Description, limites et commandes : [QUALITY.md](QUALITY.md).
Traçabilité et préparation Phase 5 : [PREREQUISITES_20260924.md](PREREQUISITES_20260924.md).
Les résultats mesurés du commit livré sont ceux de ses artefacts Actions et de
son compte rendu. L'ajout du workflow n'active pas une protection de branche
administrative. Le périmètre applicatif demeure celui des sources prêtes.

## 2026-09-24 - Phase 5A, validation Web isolée

Première frontière bornée après les interruptions de la préparation Phase 5 :
validateur de configuration et route HTTPS authentifiée, sans mutation de cible,
sans persistance de credentials, sans changement du wizard ou du moteur Web.
La réponse est INPUT_ONLY, jamais un plan approuvé. Fresh/upgrade et les choix
Assistant sont distingués explicitement. Détails et limites dans
[PHASE5A_WEB_CONFIGURATION.md](PHASE5A_WEB_CONFIGURATION.md).

Les sous-lots suivants sont 5B (moteur Web et fresh), 5C (upgrade/reprise/rollback),
puis 5D (écrans et recette système intégrée). Aucun PASS de déploiement applicatif
n'est déduit des tests de configuration de 5A. Aucun SQL/install.php à modifier ici.

Statut 5A : lot local non publié, envoi GitHub bloqué par la plateforme. Le test
natif local est bloqué par la politique Chromium ; ne pas déclarer la CI complète
verte. La base de reprise distante reste le dernier commit des préalables.

## 2026-09-24 - Reprise qualifiée 5A/5B1 et sous-lot 5B2.1

La Phase 5A est publiée sur main Installer au commit
`13df634237ba818c199121d23f4bceea8bf2a5b1`. La Phase 5B1 Web est publiée et
qualifiée sur main/dev-Bastien au commit `dcb856bc5ef5f35006d2398289b49f5386dcc5f5`.
Ces références remplacent les statuts historiques locaux ci-dessus pour la reprise.

Le présent sous-lot ajoute seulement le transport privé Python/PHP, l'empreinte
fermée des sources exécutées, la séparation d'identité, les canaux bornés et
l'interlock empêchant un second fresh après perte de réponse. Une observation
SQL non mutante est disponible, sans autorisation de rejeu ou déclaration d'une
installation complète. Aucune façade publique ou écran n'est raccordé.
Le code applicatif Web reste inchangé. Le périmètre suivant reste 5B2.2, pas 5C.
Contrat, préconditions, tests et limites :
[PHASE5B21_PRIVATE_TRANSPORT.md](PHASE5B21_PRIVATE_TRANSPORT.md).

La publication effective de ce lot et la réussite des nouveaux runs doivent être
vérifiées dans le compte rendu de livraison ; ce document ne recycle pas les
résultats 5A/5B1 comme preuve de 5B2.1. #13 et Web #135 restent transverses ouverts.
