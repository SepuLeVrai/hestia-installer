# État du projet

## Chantier borné suivant : collecteur et timer

Le [raccordement du collecteur](PHASE5_HTTP_CLEANER_DRAIN.md) part de
`8de010e1`, validé par la campagne ciblée `36221268362`, sans qualification
globale. Il compose les deux services HTTP avec le collecteur exact et son
timer, garde la maintenance durable et recontrôle tout réarmement. Le worker,
la rétention 43200 secondes et le nettoyage Debian natif restent inchangés.

74 contrôles locaux puis un seul job Debian 13 de 47 scénarios système sont
prévus. Code et documentation sont gelés ensemble ; les preuves exactes seront
dans le checkpoint. Aucun changement Web, aucune promotion ; les Quality
globales sont différées. Tous les indicateurs de clôture restent faux.
Après ce lot : checkpoint et arrêt, sans ouvrir automatiquement le suivant.
Les sections suivantes décrivent les étapes historiques.

## Chantier borné du 26 septembre : drainage HTTP

La base `3a5e2a44` et ses cinq campagnes Installer/Web sont qualifiées ; voir
[PHASE5_HTTP_DRAIN.md](PHASE5_HTTP_DRAIN.md) pour leurs références exactes.
Le chantier courant raccorde la barrière aux deux services HTTP réellement
provisionnés et refuse les producteurs de l'identité observés hors périmètre.
Il conserve les contrats précédents et tous les indicateurs de clôture faux.

À la demande de Bastien, un seul job ciblé Debian 13 doit exercer les contrôleurs
affectés et 41 recettes système. Les campagnes globales sont différées pour
maîtriser les coûts ; ce candidat n'est pas promouvable et n'est pas déclaré
globalement qualifié. Aucun changement Web. Après vérification des preuves et
checkpoint, arrêt pour redémarrage, sans enchaîner le chantier suivant.
Les sections ci-dessous restent historiques.

## Reprise active : profil de stockages métier externes

Base qualifiée `357d7164af7161aaa113dc50ca33d44dd46ba873`, arbre
`a3587684c50642d374d1aa73d68cb41445b107eb`, 177 fichiers. Campagnes finales :
Quality `36182854088`, système `36182854030`, paquets `36182854029`,
SQL/proxy/Web `36182916684`, toutes vertes sans skip.

Le [lot stockage](PHASE5_BUSINESS_STORAGE.md) raccorde un nouveau Web explicite
`2a27c7a1f9fe0a00289eb53278f75d5f230900b7` : chemins métier externes, code
immuable et maintenance commune au runtime HTTP, collecteur et slot SQL.
Le pin historique reste supporté. La reconnaissance des deux sources n'autorise
aucune transition d'upgrade. Le Web candidat a sa Quality `36195113348` verte.
L'ensemble Installer reste soumis aux campagnes sur son propre arbre gelé :
628 core par Debian, 61 système et 13 paquets par Debian, 16 DOM, 21 HTTPS,
118 SQL/HTTP, 14 helper proxy, 10 Web historique et 9 nouveaux scénarios de
stockage sous SQL managed, Apache/FPM 8.4 et TLS réels.

Les services de ces recettes sont activés et drainés par le banc jetable.
Inventaire des neuf groupes de producteurs, sauvegarde exhaustive 5C2,
transition 5C3, reprise/rollback 5C4 et orchestration/wizard 5D restent ouverts.
Aucune promotion ni activation produit n'est implicite. Les sections suivantes
sont historiques ; les résultats exacts du gel sont dans le checkpoint compagnon.

## Reprise active — déploiement et recette du Web réel

Base proxy `4d9f396e` qualifiée : Quality `36164840460`, système `36164840403`,
paquets `36164840474`, SQL/proxy `36164923531`.
Le [nouveau lot](PHASE5_WEB_DEPLOYMENT.md) déploie exclusivement l'arbre complet
Web épinglé, sans exécution ni adoption. La recette assemble ce déploiement,
SQL, Apache/FPM et TLS pour le login Admin, Dashboard, logout et les sessions
1 h/4 h/8 h. Cible : 623 core par Debian, 61 système et 13 paquets par Debian,
16 DOM, 21 HTTPS, 118 SQL/HTTP, 14 cas helper proxy et 10 cas Web réel.
Activation produit, stockages métier et fin de Phase 5 ouverts. Sections suivantes
historiques ; preuves finales dans le checkpoint après gel.

## Reprise active — interface TLS/proxy du backend

Base paquets `15057f62` qualifiée : Quality `36160960174`, système
`36160960191`, paquets `36160960155`, SQL/HTTP `36161088874`.
Le [lot proxy](PHASE5_PROXY_INGRESS.md) sépare pair déclaré et allowlist clients,
normalise IP/HTTPS vers PHP et fournit un bloc NGINX sans installer le module
global NGINX/ACME. Sources à qualifier : 608 core par Debian, 61 recettes système
et 13 paquets par Debian, 16 DOM, 21 HTTPS, 118 SQL/HTTP et 14 cas avec le helper
Web réel. Activation complète, stockages exhaustifs, wizard et fin de Phase 5
restent ouverts. Sections suivantes historiques ; résultats dans le checkpoint.

## Reprise active — paquets Debian officiels

Base identité `870560b61ebc271d8979741b1f6b074a76c93251` qualifiée : Quality
`36155640408`, système `36155640569`, SQL/HTTP `36155918278`. Le
[nouveau lot](PHASE5_SYSTEM_PACKAGES.md) fige les archives signées puis installe
hors réseau les seuls ajouts prévus, sans démarrer les services par défaut.
600 core, 47 recettes système historiques et 13 recettes paquets par Debian,
16 DOM, 21 HTTPS et 118 SQL/HTTP requis sur l'arbre exact. Debian 12 reste
incompatible avec le PHP minimal du Web épinglé. Activation complète, fin de
5C2/5C3/5C4/5D et intermittence DOM historique restent ouvertes. Sections suivantes
historiques ; résultats finaux dans le checkpoint après gel documentaire.

## Reprise active — identité système dédiée

Base collecteur `37de8a99` qualifiée au premier passage : Quality `36152239576`,
système `36152239622`, SQL/HTTP `36152489096`. Le
[nouveau lot](PHASE5_SERVICE_IDENTITY.md) crée exclusivement un compte système
verrouillé et son groupe, sans adoption ni home. Journal durable, réponse perdue
récupérable en lecture, création partielle manuelle. Qualification requise :
576 core et 47 recettes système par Debian, 16 DOM, 21 HTTPS, 118 SQL/HTTP.
Activation réelle et fin de Phase 5 ouvertes ; intermittence DOM historique
non résolue. Les sections suivantes sont historiques ; preuves dans le checkpoint.

## Reprise active — collecteur dédié des sessions

Base runtime `6477faf8` qualifiée : Quality `36147944637` tentative 2,
système `36147944651`, SQL/HTTP `36148072196`. Le [nouveau lot](PHASE5_SESSION_CLEANER.md)
prépare un collecteur sous l'UID du pool et un timer initialement inactif.
Il coordonne le nettoyage de sessions de plus de 43200 secondes avec PHP et la
maintenance, sans lire leur contenu ni désactiver phpsessionclean natif.
Candidat : 558 core par Debian, 35 recettes système par Debian, 16 DOM,
21 HTTPS et 118 SQL/HTTP. Intermittence DOM du checkpoint précédent toujours
non résolue. Activation, producteurs/stockages exhaustifs et Phase 5 restent ouverts.
Les sections suivantes sont historiques.

## Reprise active — préparation Apache/FPM dédiée

Base barrière `e86ecd7cfabebcfcb141ee2cc66ca9a27eec814d` qualifiée : Quality
`36142987500`, système `36142986888`, SQL/HTTP `36143203828`, zéro skip.
Le [nouveau lot](PHASE5_HTTP_RUNTIME.md) prépare deux services isolés et cinq
répertoires PHP privés sous maintenance durable. Pas de démarrage produit,
de création de compte ni d'installation de paquets. L'observation refuse toute
dérive et ne rejoue pas une préparation partielle. Candidat à qualifier :
542 core par Debian 12/13, 11 + 12 recettes système par Debian, 16 DOM,
21 HTTPS, 118 SQL/HTTP. Debian 12/PHP 8.2 reste une qualification d'infrastructure,
pas du Web qui exige PHP >= 8.3. Nettoyeur, activation, stockages exhaustifs,
5C2 complète, 5C3/5C4 et 5D restent ouverts. Les sections suivantes sont historiques.

## Reprise active - barrière systemd des services enrôlés

Le checkpoint inventaire `ed7707c9eb7f6314e5d3d3b899f0efe1cef92d53` est qualifié
(Quality `36139574996`, SQL/HTTP `36139636592`, 506 core par Debian et 118 recettes).
Le lot suivant ajoute une [barrière d’arrêt système](PHASE5_SYSTEM_DRAIN.md) :
quatre unités explicitement enrôlées, condition de maintenance, arrêt Apache
avant FPM, vérification des cgroups et reprise exacte après interruption.
Candidat à qualifier : 524 core par Debian, onze recettes systemd par Debian,
16 DOM, 21 HTTPS et 118 recettes SQL/HTTP. Les endpoints PHP et producteurs
CLI/nettoyage de la recette système sont des fixtures, pas le Web installé.
Le provisionnement, le nettoyage Debian natif, la reprise des services et le
raccordement aux stockages/sauvegardes restent ouverts. Aucun gate final de
Phase 5 n’est levé par cette seule barrière. Les sections suivantes sont historiques.

## Reprise active - inventaire des stockages et producteurs

Le checkpoint coordonné `d2f2d0af85bbe4f6167bb1c96065c224f22fb383` est qualifié
(Quality36136210321, SQL36136276965). Le nouveau lot ajoute une
[cartographie contrôlée](PHASE5_STORAGE_INVENTORY.md), sans mutation ni évaluation
de PHP déployé. Il exige les observations explicites de configuration, retient
les racines de repli et masquées, et identifie neuf groupes de producteurs.
Le seul guard PHP ne couvre pas la préparation multipart ni les convertisseurs
orphelins ; des recettes réelles matérialisent ces limites encore bloquantes.
Candidat en qualification : 506 core par Debian et 118 SQL/HTTP attendus.
La sauvegarde complète et la Phase 5 restent ouvertes, aucun Web ou serveur
modifié. Prochaine étape : profil de stockage/services fermé et drainage réel.
Les sections suivantes restent historiques ; preuves finales dans le checkpoint.


## Reprise WORK - sauvegarde coordonnée, Phase 5 ouverte

Le lot fichiers `a19c40318dfac958330fda8890116c6580e3ae43` est qualifié :
Quality `36133121054`, 478 core par Debian 12/13, 16 DOM, 21 HTTPS, sans skip.
Le lot courant [coordonne SQL et données enregistrées](PHASE5C2_COORDINATED_BACKUP.md)
sous la même maintenance, avec contrôle des dérives et reçu global final.
Il est en qualification sur ses fichiers gelés : 488 core attendus et 110
scénarios SQL/HTTP requis. Aucun résultat antérieur ne vaut preuve du lot.

L’inventaire exhaustif, les stockages métier sous webroot, tous les producteurs
système, la transition réelle, la reprise/rollback et les services/wizard 5D
restent à livrer. Le résultat limité garde complete_web_backup=false et
application_installed=false. Aucun changement Web, PR, fast-forward ou serveur.
Résultats finaux et ZIP exact : checkpoint compagnon, sans retouche après gel.

## Reprise WORK - lots courts, Phase 5 toujours ouverte

Le candidat `726757eeb139818a7aa93b90586091ff37a08f85` est qualifié :
Quality `36126405630` et recette SQL/HTTP `36126448323` réussies. Les journaux
finaux confirment 100 scénarios (18+21+15+30+7+9), sans échec, erreur ou skip.
Il couvre le DEFINER durable, le secours, la maintenance coopérative et la
réparation explicite. Les branches actives n'ont pas été promues.

À la demande de Bastien, la suite est découpée en petits lots sauvegardés.
Le lot courant ajoute la [copie des données modifiables](PHASE5C2_DATA_FILES.md)
et leur restauration isolée sous maintenance, avec 22 tests supplémentaires.
Ce nouveau code est en qualification. Son raccordement au snapshot SQL et à
l'inventaire complet des stockages n'est pas encore livré.

Restent ensuite : transition réelle et catalogue de versions, reprise/rollback,
services et permissions Debian12/13, sessions et wizard, recette système complète,
gel documentaire et livraison exacte. Ni 5C2 ni Phase 5 ne sont closes.
Aucune PR, promotion ou mutation de production pour ces sous-lots.

Les sections suivantes décrivent l'historique et ne remplacent pas ce statut.

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
