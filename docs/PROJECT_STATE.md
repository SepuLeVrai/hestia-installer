## Candidat 6B6 — 29 septembre 2026

Le [contrat de sauvegarde SQLite Gateway](PHASE6B6_GATEWAY_BACKUP.md) ajoute une barrière durable, une restauration isolée et un reçu composé Web/Gateway. Qualification en cours. La réouverture et le boot restent fermés.

## 29 septembre 2026 - candidat 6B5

[Service Gateway MAIN natif](PHASE6B5_GATEWAY_SERVICE.md) sur la base 6B4 qualifiée :
plan séparé, compte dédié, credential systemd privé, listener 9083, sonde signée
via Foundation et arrêt coordonné avant sauvegarde Web. Les acquis et références
Web/Gateway sont conservés. Qualification native et Quality du candidat à obtenir ;
la phase 6 reste ouverte et aucune promotion n'est demandée.

## 29 septembre 2026 - candidat 6B3

## 2026-09-29 — candidat 6B4 Foundation MAIN

Raccordement privé MAIN sur 9082, journal séparé et pool PHP Web existant. Qualification native en attente ; le gel qualifié 6B3 reste la référence précédente. Voir [le contrat 6B4](PHASE6B4_FOUNDATION_MAIN.md). Gateway, accès Mobile public et boot restent à raccorder.

Import du ZIP binaire Gateway qualifié depuis le wizard, confirmation et journal
avant lecture du corps, reprise sans rotation des clés. Les profils et StepSpecs
6B2 restent inchangés. Qualification complète en attente sur le candidat.
Contrat et prérequis natifs vérifiés :
[PHASE6B3_GATEWAY_IMPORT.md](PHASE6B3_GATEWAY_IMPORT.md).
Aucune promotion main/dev-Bastien, évolution Web/APK ou installation native.

## 29 septembre 2026 - candidat 6B2

Acquisition binaire Gateway épinglée et préparation des identités accessibles
depuis le wizard, avec journal séparé et reprise. Voir
[PHASE6B2_GATEWAY_ACQUISITION.md](PHASE6B2_GATEWAY_ACQUISITION.md).
La qualification complète de ce candidat reste à obtenir ; aucun déploiement
Gateway/Foundation ni phase 6 terminé n’est revendiqué. Les références et acquis
6B1 ci-dessous restent le socle.

> Suite 6B1 : [préparation privée des identités P-256](PHASE6B_PRIVATE_IDENTITIES.md).
> Gateway devient configurable (origine HTTPS, MAIN seul). Composition native et
> recette intégrée 6B/6C encore à terminer ; acquis phase 5/6A conservés.

# État du projet

## 28 septembre 2026 - Phase 6A Gateway / Foundation

La [phase 6](PHASE6_GATEWAY_FOUNDATION.md) reprend le gel phase 5 qualifié.
6A fixe la base Gateway bootstrap/SQLite 6 et la matrice : Web 9080,
Foundation DEV 9081 / MAIN 9082, Gateway Installer 9083. Le défaut autonome
Gateway reste 9080. Le composant apporte les sondes adaptées et le refus
des ports étrangers ; son run et son paquet exact sont suivis dans Gateway #4.

Le code Installer reste inchangé dans ce lot documentaire. 6B doit encore
composer les moteurs, clés, Foundation, FCM et wizard ; 6C doit qualifier
l'ensemble avec NGINX Mobile. Phase 6 et issues globales restent ouvertes.

## 28 septembre 2026 — 5D7b, dernier plan du parcours Web

Le [frontal HTTPS et son renouvellement](PHASE5D_PUBLIC_TLS.md) complètent
les plans acquis : paquets 5D5a, MariaDB/fresh 5D5b, boot 5D6 et dépendances
ACME 5D7a. Les anciens journaux, unités, contrôleurs et bundle boot sont
conservés. L'overlay Apache est explicite et reconnu par la sauvegarde native.
Le nouveau frontal emploie une identité distincte, un certificat HTTP-01,
un test de renouvellement et un timer dédié. La maintenance reste fermée au boot.

La clôture de phase 5 porte sur les profils qualifiés : fresh serveur Debian 13,
PHP 8.4, IPv4, et upgrade managed scellé acquis. Elle exige les gates Quality et
la recette finale du même gel ; leurs résultats et SHA sont consignés dans #13.
La recette ACME utilise une autorité privée jetable, sans émission Let's Encrypt
de production. #1/#3 et Web #135 couvrent aussi les phases suivantes et restent
ouverts. Aucune promotion des branches actives n'est implicite.

Les sections suivantes sont historiques.

## 28 septembre 2026 — 5D5a, dépendances du serveur vierge

Le [raccordement des paquets officiels au wizard](PHASE5D_PACKAGE_WIZARD.md) ajoute
deux plans distincts, téléchargement puis installation des versions figées.
Les plans Web acquis 5D1–5D4 et les contrôleurs natifs restent inchangés.
MariaDB, le boot et le frontal public/TLS restent à livrer ; la phase 5 est ouverte.
Le nouveau gel exige les gates générales et sa recette ciblée en CI jetable.

## Avancement 5D4 - 28 septembre 2026

Le [wizard upgrade](PHASE5D_UPGRADE_WIZARD.md) compose les contrôleurs acquis
pour une instance legacy gérée et scellée enregistrée localement. Quatre étapes
migrent les stockages sous maintenance ; un second plan autorise la réouverture
et démarre les services dédiés. Le rollback natif reste possible avant cette
autorisation, sans restauration SQL. Six nouvelles recettes réelles ciblent ce
raccordement. Les résultats du gel figurent au checkpoint et #13. Les campagnes
5C4/5D1/5D2/5D3 restent acquises. Serveur vierge, boot et frontal public/TLS
restent à livrer ; phase 5 ouverte. Les sections suivantes sont historiques.

## Avancement 5D3 - 28 septembre 2026

L'[activation explicite](PHASE5D_ACTIVATION.md) prolonge le fresh 5D2 par un
second plan lié au premier, sans modifier ses étapes acquises. Le produit ouvre
l'admission, démarre Apache/PHP-FPM et le timer dédiés, puis vérifie la page locale.
Le wizard distingue l'historique DONE d'une disponibilité actuelle horodatée.
Huit nouvelles recettes réelles sont définies ; les résultats du gel sont au
checkpoint et #13. Frontal public/TLS, persistance au boot, wizard upgrade et
serveur vierge restent à livrer. Phase 5 reste ouverte. Les sections suivantes
décrivent l'historique des gels précédents.

## Avancement 5C4 - 28 septembre 2026

La [reprise et le rollback](PHASE5C4_RECOVERY.md) sont implémentés pour la paire
de stockages qualifiée. Les reprises reconnaissent les déplacements effectués,
refusent les dérives SQL/configuration et excluent le rollback dès l'intention
de réouverture. La recette dédiée comporte vingt scénarios dont quatorze nouveaux,
avec arrêts SIGKILL et écritures métier après réponse perdue. Les résultats réels
et Quality du gel sont consignés au checkpoint et #13. Le raccordement wizard,
journal opérateur, activation et recette globale 5D restent à livrer.
Les sections suivantes décrivent les gels précédents.

## Avancement 5C3b - 28 septembre 2026

La [migration des stockages](PHASE5C3_STORAGE_UPGRADE.md) possède maintenant son
contrôleur : sauvegarde restaurée, déplacement réel des uploads, bascule du Web
et des configurations, puis autorisation distincte de reprise. Le profil reste
le Web historique scellé géré par l'Installer, sur Debian 13/PHP 8.4/Ext4.
La recette dédiée utilise les vrais services et six scénarios ciblés ; ses
résultats sont consignés dans le checkpoint et #13. La reprise/rollback 5C4 et
le raccordement wizard/services 5D restent à livrer. La phase 5 reste ouverte.
Les sections suivantes décrivent les gels précédents.

## Avancement 5C3a - 28 septembre 2026

Le [catalogue fermé et son audit des sources](PHASE5C3_CATALOG.md) sont livrés
dans le présent lot. Les deux builds connus ont le même schéma et les mêmes
113 migrations ; la paire candidate demande une migration des stockages.
Aucune transition applicable ni bascule n’est encore qualifiée. Le profil 5C2
reste clos ; 5C3b doit qualifier le profil source puis le déplacement et la bascule.
Les campagnes finales de ce lot sont référencées dans son checkpoint et #13.

## Point actuel - profil 5C2 clos, 5C3 suivant (28 septembre 2026)

La [décision de qualification globale](PHASE5C2_QUALIFICATION_20260928.md)
clôt le profil provisionné de sauvegarde/restauration sur Installer `713335d`.
1 077 core par Debian 12/13, 37 navigateur, 242 système, 36 paquets et 151
recettes SQL/Web passent, en plus des 36 scénarios provisionnés du même arbre.
Le delta courant est documentaire ; le code et les tests restent inchangés.
La phase 5 entière reste ouverte. Prochain chantier : 5C3, transition réelle
entre versions explicitement supportées, puis incidents/rollback 5C4 et wizard
avec activation 5D. Les flags runtime globaux restent faux ; aucune branche
active n'est promue ici. Les sections suivantes sont l'historique des gels.

## Réservations des chemins externes et bilan 5C2 - 28 septembre 2026

Base Web qualifiée `c54ff0367d7ddf07de9b6c6a76373e642e96176d`, run `36383437417`.
Les [réservations externes](PHASE5_EXTERNAL_FENCE.md) ferment les deux anciens
noms IA par inodes vides immutable, après refus de toute occupation initiale.
Préparation et levée sont journalisées ; aucune reprise implicite. Le
[bilan 5C2](PHASE5C2_COVERAGE.md) rapproche destinations et neuf groupes de
producteurs. Gel attendu : 117 locaux, 196 Debian, 36 scénarios en quatre groupes.
Quality globales, transition réelle 5C3, incidents/rollback 5C4 et wizard 5D
restent à qualifier ; aucun flag global n'est relevé.

## Protection du Web déployé - 28 septembre 2026

Base qualifiée `2963db6513945a3940341496fd8a797e7046d65a`, run `36338904729`.
Le [lot Web](PHASE5_WEB_FENCE.md) protège par immutable Ext4 l'arbre complet,
y compris les deux pointeurs d'activation. Journal durable, levée explicite,
reprises bornées et refus de réouverture tant que la protection est journalisée.
Qualification attendue : 112 locaux, 179 Debian et 32 scénarios, résultats au
checkpoint. Les chemins externes et le bilan 5C2 restent à fermer ; Quality
globales et phases suivantes ne sont pas déclarées terminées.

## Protection du slot de configuration - 27 septembre 2026

Base qualifiée `23b54743c605dd37cf9a2f7e45b21e710265d760`, run `36331417069`.
Le [lot configuration](PHASE5_CONFIGURATION_FENCE.md) protège les fichiers du
slot par immutable Ext4 avant copie. La maintenance reste disponible pour ses
journaux. La levée est explicite, persistante et récupérable. Qualification
attendue : 106 locaux, 163 Debian et 29 scénarios, résultats au checkpoint.
Code Web, pointeurs et configurations externes restent une frontière distincte ;
5C2 et les Quality globales ne sont pas déclarées terminées.

## Protection des inodes de données - 27 septembre 2026

Le [lot Ext4](PHASE5_INODE_FENCE.md) ajoute une protection persistante de chaque
inode, opposable aux écritures ordinaires de root et aux bind alias. Le retrait
est explicitement journalisé ; les interruptions ne rouvrent rien. Le profil
refuse les systèmes de fichiers non qualifiés et les montages imbriqués.
Qualification ciblée prévue : 100 contrôles locaux, 149 Debian et 26 scénarios.
Résultats exacts au checkpoint. 5C2 reste ouverte pour la couverture globale
configuration/producteurs ; 5C3/5C4/5D et Quality globales restent à terminer.

Le premier banc `36329926011` passe 149 contrôles mais refuse les 26 scénarios
au démarrage : chemin de fixture trop long. Le nom est raccourci, sans changer
la limite HTTP produit. Le deuxième banc `36330364031` passe 149 contrôles et
25/26 scénarios ; la restauration de fixture tente un renommage entre volumes.
Son parent privé est déplacé sur Ext4, hors des données scellées, avec assertion
de device identique. Le nouveau gel doit être qualifié ; les deux preuves sont conservées.

## Barrière des chemins de données - 27 septembre 2026

La [barrière persistante des données](PHASE5_DATA_ACCESS_FENCE.md) ferme les
chemins canoniques avant copie et refuse une reprise implicite. Le contrôle
couvre les nouveaux CLI/timers non privilégiés utilisant ces chemins ; les
producteurs privilégiés et chemins alternatifs restent non certifiés.
5C2 et la phase 5 restent ouvertes. Résultats exacts au checkpoint.

## Prérequis D-Bus - 27 septembre 2026

Le [raccordement D-Bus](PHASE5_SYSTEM_BUS.md) ajoute installation officielle,
démarrage explicite si inactif et conservation du bus existant. Qualification
ciblée sur le gel décrite dans ce document ; résultats au checkpoint.
Prochain chantier : maîtrise effective des CLI et planificateurs natifs.
5C2 et la phase 5 restent ouvertes.

## Admission des planificateurs classiques — 27 septembre 2026

Le [lot suivant](PHASE5_CLASSIC_SCHEDULER_ADMISSION.md) refuse les empreintes
cron/anacron/at et familles apparentées avant le gate, puis les réobserve
pendant la sauvegarde. Les unités installées inactives et chargées/transitoires
sont couvertes par deux listes distinctes. Aucun inventaire global ni verrou
contre les changements administratifs n'est revendiqué. 85 contrôles locaux,
117 Debian 13 et 17 scénarios réels attendus sur le gel ; résultats au checkpoint.
Les CLI et planificateurs natifs non couverts, puis 5C3/5C4/5D restent ouverts.

## Correctif de diagnostic —27septembre2026

Le candidat270f2526/run36302090702 conserve106 contrôles Debian13 verts et
12/13 scénarios réels verts ; zéro erreur ou skip, un échec sur le code public
attendu après apparition d’un stockage IA hors profil. La sauvegarde est bien
incomplète, sans reçu vérifié et avec maintenance conservée. Le chemin positif
complet passe. Ces résultats ne valent pas qualification du correctif ci-dessous.

Cause reproduite localement : le context manager SQL interceptait l’exception
émise par le coordinateur pendant yield et la remplaçait par une erreur de
transport SQL. Le correctif limite cette conversion à l’acquisition/libération ;
l’erreur de l’appelant conserve son type pour la politique fermée du coordinateur.
Le nettoyage du worker, du groupe et des pipes reste inconditionnel. Un contrôle
supplémentaire vérifie l’identité de l’exception et la fin effective du worker.
Aucune assertion du scénario réel n’est retirée ni assouplie.

Nouveau gel :75 contrôles locaux,107 prévus sous Debian13 et les mêmes13 scénarios
réels. Aucun second job lancé à ce point d’arrêt ; validation réelle du correctif
requise avant de le déclarer qualifié. Preuve négative et source270f2526 conservées
au checkpoint. Toutes les frontières globales de phase5 restent inchangées.


## Priorité courante : admission des configurations de sauvegarde

Le parent c8a32e61/run36265588722 est qualifié (66 locaux,98 Debian13,7 réels).
Le [lot d’admission](PHASE5_PROVISIONED_BACKUP.md) étend les verrous des réglages
à toute la sauvegarde, refuse les configurations de stockage non couvertes et
vérifie le serveur SQL dédié sous le verrou continu. Aucun ancien PHP secret
n’est chargé. Huit contrôles ajoutés sans retrait :74 locaux,106 dans Debian13 ;
13 scénarios réels ciblés, dont six nouveaux refus et le parcours positif enrichi.
Un seul job prévu après gel code/documentation ; résultats exacts au checkpoint,
pas de succès global anticipé ni de promotion.

Les producteurs CLI/planificateurs étrangers et l’exhaustivité5C2 restent ouverts,
puis5C3 transition réelle,5C4 reprise/rollback,5D activation/wizard et gates finaux.
La découverte générique Exec*Ex reste différée. Les sections suivantes décrivent
les états historiques ; leurs campagnes déjà acquises ne sont pas à recommencer.


## Priorité courante : sauvegarde intégrée provisionnée

Deux essais sont conservés : run36264655150 (cwd du sous-processus de test),
puis run36264993299 (writer de secrets utilisé à tort pour le grand bridge).
Le source-bundle existant traite désormais ce fichier ; limite16Kio conservée,
onzième test ajouté.66 contrôles locaux/98 Debian13 et7 cas réels pour le gel
correctif ; les9 business acquis sur fe95da21 ne sont pas revendiqués comme
rejoués sur ce correctif. Voir le checkpoint pour les résultats définitifs.

Le parent8e063bc3/run36261287478 est vérifié :325 contrôles et20 cas réels verts,
artefact/source/modes contrôlés. Les attentes de « troisième job » plus bas sont
historiques. La découverte générique Exec*Ex est différée selon le recentrage
convenu ; ses protections ne sont ni retirées ni promues en autorités.

Le [lot intégré](PHASE5_PROVISIONED_BACKUP.md) raccorde le drain HTTP/collecteur
à la sauvegarde coordonnée et maintient un verrou SQL durant toute la copie et
la restauration de preuve. Six racines exactes, fresh managed local, aucune
remise en service automatique.66 contrôles locaux et un seul job ciblé prévu
avec98 contrôles et7 scénarios réels ; résultats exacts dans le checkpoint.
Code et documentation gelés ensemble, sans qualification globale ni promotion.
Prochaine frontière : admission opposable des autres producteurs et exhaustivité5C2,
puis vraie bascule5C3, incidents/rollback5C4, orchestration/wizard5D et gates finaux.

Second essai c7e9e52a/run36260975820 :325 contrôles et19/20 cas réels verts.
Dernier échec : comparaison textuelle erronée du lien procfs root. Le banc
compare maintenant dev/ino de la racine et du binaire avec la fixture, distincts
de l'hôte, plus namespace et marqueur privé. Troisième job ciblé requis ; aucun
changement du lecteur, aucune qualification globale ni promotion anticipée.

Le premier job du lecteur (b92c1075/run36260640278) conserve325 contrôles et8
cas parents verts, mais0/12 nouveaux cas exécutés : préparation rootfs refusée
à EXEC. Le banc déplace sa racine exécutable de /run vers /var/lib et vérifie
explicitement les flags noexec ; lecteur inchangé. Second job ciblé requis,
résultat dans le checkpoint. Aucune qualification globale ou promotion.

## Reprise courante : lecteur du contexte Service configuré

Sur1b3a0887, [le lecteur](PHASE5_EXECUTION_CONTEXT_READER.md) implémente les20
propriétés dans le cycle de FD du census. Données privées, aucune projection
numérique ou chemin résolu ; modèle et primitives parentes inchangés.26 tests
nouveaux,325 sélectionnés ;957 core détectables/952 requis. Un seul job Debian13
prévu après gel :8 cas parents et12 nouveaux. Résultats exacts au checkpoint,
pas de Quality globale ni promotion. Prochaine frontière : contrat des commandes
Ex et confidentialité des argv. Checkpoint puis arrêt ; historique ci-dessous.

## Reprise courante : contrat du contexte configuré, sans changement runtime

Base exacte `7bb59a67463841db018cb70c5643eb30ad060e4a`, arbre
`e16822ef6183a28b117cc5d0008b0ea566de798d`, 248 fichiers. Son run36256184902
est vert :299 contrôles Debian13 et24 cas réels ;299 contrôles locaux, artefact
et modes vérifiés. Les deux échecs du lot précédent restent conservés ; aucune
résolution rétroactive de leur cause n'est affirmée.

Le [contrat de contexte configuré](PHASE5_SYSTEMD_EXECUTION_CONTEXT.md) fixe
20 propriétés Service, leur route par invocation, getters, sémantique et bornes.
16 sources officielles v257 sont vérifiées par blob Git/SHA256 ;24 futurs cas
restent non exécutés. Ce lot édite seulement docs/ : code/tests/workflows/Web
inchangés, zéro Actions, aucune qualification runtime nouvelle ou promotion.
Prochain chantier unique : implémenter le lecteur de ces20 propriétés dans le
census vivant, bloc privé sans projection numérique des comptes ni résolution
hôte des chemins. Les commandes Ex restent un chantier ultérieur distinct.
Checkpoint puis arrêt. Les sections qui suivent sont des états historiques.

Lot census/relations : deux jobs refusés (runs36255253018 et36255697233).
Le second conserve299 contrôles et22/24 cas réels verts ; deux conflits de jobs
systemd. Le journal montre une boucle de getty@tty1.service dans le conteneur.
Cette seule console est masquée dans l’image jetable ; troisième job ciblé prévu
après gel, sans modification des contrôles produit. Preuves négatives conservées.

## Candidats vivants, relations et sélection conservatrice

Sur la base b1078e9d, le [raccordement census/relations](PHASE5_CENSUS_RELATIONS.md)
conserve les FD jusqu'à clôture, lit les propriétés une fois par unité et par
passage, puis relie des faits candidats distincts au modèle pur. Signaux de
threads, descendants et inconnus ne sont pas des identités effectives inventées.
24 nouveaux tests, 299 contrôles ; un job Debian13 final de24 scénarios réels
prévu après gel. Résultats exacts au checkpoint, sans Quality globale ni promotion.
Prochain lot : contrat d'identités configurées et de contexte d'exécution.
Checkpoint puis arrêt. Les sections suivantes sont historiques ; l'incident
parent12060235 reste non résolu et sa preuve conservée.

Qualification du lot courant : premier job36253303139 en échec sur un diagnostic
parent inattendu. Une seconde exécution avec diagnostic de fixture est prévue ;
preuves et limites dans PHASE5_CENSUS_INVOCATIONS.md. Aucun succès anticipé.

## Liaison du recensement vivant aux invocations

Sur4a14c8d9, la [liaison census/invocations](PHASE5_CENSUS_INVOCATIONS.md)
conserve les PIDFD de toutes les tâches pendant les lectures du manager et les
contrôles finaux. Plusieurs leaders dans une unité sont regroupés ; threads,
inconnus et non-correspondances restent conservés sans exclusion ni droit de
drain.22 nouveaux tests,275 contrôles ; un second job Debian13 de24 scénarios
réels prévu après le premier échec. Résultats au checkpoint, aucune Quality globale ou
promotion anticipée. Prochain lot : relations et sélection sur cette preuve
fraîche, sans confondre signal de thread et identité effective de leader.
Checkpoint puis arrêt. Les sections suivantes sont historiques.

## Recensement des tâches et candidats de revue

Sur67e6db85, le [recensement procfs](PHASE5_PROCESS_CENSUS.md) énumère les leaders
et threads visibles sous bornes, conserve les PIDFD_THREAD pendant deux lectures
et garde les inconnus. Les identités de chaque tâche et la filiation observée
fournissent des candidats de revue sans exclusion, liaison systemd ou droit de
drain. 27 nouveaux tests,253 sélectionnés ; un job Debian13 prévu après gel avec
16 scénarios réels. Résultats exacts au checkpoint. Prochaine étape : raccordement
frais des candidats aux invocations. Quality globale et promotions différées.
Checkpoint puis arrêt ; les sections ci-dessous sont historiques.

## Identité effective des leaders sélectionnés

Sur e5ffaf35, le [lecteur procfs lié à l'invocation](PHASE5_PROCESS_IDENTITY.md)
observe les quatre UID/GID, groupes, date, namespaces et cgroup des leaders
sélectionnés, avec contrôles de stabilité et PIDFD possédés. Le signal effectif
élargit seulement la revue ; aucun inventaire complet, writer ou droit de drain.
26 nouveaux tests, sélection de226 ; un seul job Debian13 prévu après gel avec
24 scénarios réels, dont huit nouveaux. Résultats exacts dans le checkpoint,
pas de Quality globale ou promotion. Prochain lot : recensement borné des
candidats et couverture threads/descendants. Checkpoint puis arrêt. Historique.

## Noms et relations par invocation, raccordement conservateur

Sur 8a2f4ce5, le [lecteur de relations](PHASE5_SYSTEMD_INVOCATION_RELATIONS.md)
ajoute Names et huit listes fermées aux deux liaisons PIDFD. Index enrichi validé,
alias observés seulement, cibles absentes conservées inconnues. Le modèle de
pertinence reçoit des faits liés à cet index, sans identité ou signal métier
inventé, sans adoption/drain. 24 nouveaux tests, sélection de 200 ; un seul job
Debian13 prévu (8 cas historiques +8 nouveaux) après gel. Résultats exacts dans
le checkpoint ; pas de Quality globale ou promotion. Prochain lot : source
fiable des PID et identité effective. Checkpoint puis arrêt. Sections historiques.

## Liaison PIDFD minimale implémentée, qualification ciblée

Sur ff13729f, le [lecteur d'invocation](PHASE5_SYSTEMD_INVOCATION_BINDING.md)
implémente Id/InvocationID à partir de PID explicites, deux passages et PIDFD
possédés. Aucune propriété d'unité sur chemin nommé, aucun fallback ni mutation.
21 nouveaux tests, sélection de176 ; un seul job Debian13 de huit scénarios
réels est prévu après gel code/docs. Résultats exacts dans le checkpoint,
aucun succès anticipé. Quality globales et promotions restent différées.
Prochain lot : Names/relations via invocation ; PID de confiance, identités et
unités sans processus restent ouverts. Checkpoint puis arrêt. Sections suivantes
historiques ; la preuve négative c2acc806 reste valable.

## Contrat de remplacement par PIDFD et invocation

Sur `d86e406a`, le [nouveau contrat](PHASE5_SYSTEMD_INVOCATION_CONTRACT.md)
retient des PID proposés explicitement, liaison GetUnitByPIDFD puis lectures
Id/InvocationID exclusivement par adresse d'invocation. La chaîne est relue
dans les sources, pas encore implémentée ou qualifiée sur système réel.
Unités sans PID/processus et Debian12/v252 restent non couvertes ; aucun repli
vers un chemin nommé. Le prototype c2acc806 et son échec restent rejetés.

Lot documentaire : 214 fichiers, quatorze références primaires, seize futurs
cas non exécutés. Code/tests/workflows inchangés, zéro Actions et aucune
promotion. Prochain chantier borné : liaison minimale et preuve de disparition
sans rechargement, avant Names/relations. Checkpoint puis arrêt.

## Blocage confirmé : propriétés d'unités et chargement implicite

La [preuve négative](PHASE5_SYSTEMD_PROPERTY_AUTOLOAD.md) invalide la proposition
Properties.Get sur les chemins nommés d'unités disparues : systemd peut les
recharger. Le prototype `c2acc806` est rejeté après le run `36238806917`
(180 contrôles verts, sept cas système verts et un échec). Aucun second run.

Ce checkpoint repart de `7a331ce3` avec seuls les documents corrigés : code,
tests et workflows identiques. Les détails Names/relations ne sont pas livrés.
Gardes statiques sur 212 fichiers et preuves conservées ; pas de qualification
globale ni promotion. Prochain lot : contrat d'acquisition sans chargement,
avant tout contexte d'exécution. Checkpoint puis arrêt. Sections historiques.

## Transport réel des trois listes systemd

Sur la base `ebafa119`, le [transport privé](PHASE5_SYSTEMD_DISCOVERY_TRANSPORT.md)
utilise busctl call sur le bus local avec appels fermés, activation désactivée,
broker déjà actif contrôlé, provenance et deux tours comparés. Budgets de pipe
et de durée appliqués, descriptions supprimées. Aucun détail de pertinence,
profil adopté ou indicateur global fermé ; modèles purs et Web inchangés.

21 nouveaux tests, sélection de 155 et gardes statiques après gel code/docs.
Un seul job ciblé Debian13 est prévu : mêmes contrôles et dix scénarios réels,
avec conteneur minimal jetable sans réseau. Quality globales et promotions
restent différées ; preuves exactes dans le checkpoint. Puis arrêt avant le
chantier distinct des détails d'unités. Sections suivantes historiques.

## Sélection conservatrice de pertinence systemd

Sur la base `2677d0ef`, le [modèle privé](PHASE5_SYSTEMD_RELEVANCE_MODEL.md)
classe des faits typés liés au digest de découverte. Identités, chemins et
relations élargissent la revue ; les inconnus ne deviennent pas des exclusions.
Ni collecte hôte ni admission KNOWN_PROVISIONED, projection ou mutation.
Le collecteur fermé, le Web et tous les indicateurs de clôture restent inchangés.

42 nouveaux tests, sélection locale de 134 et gardes statiques requis après gel.
Zéro Actions prévu ; aucune qualification globale ou promotion revendiquée.
Checkpoint puis arrêt. Prochain lot borné au transport réel des trois listes,
sans lecteurs détaillés supplémentaires. Sections suivantes historiques.

## Modèle pur de découverte systemd

Sur la base `847cea06`, le [modèle privé](PHASE5_SYSTEMD_DISCOVERY_MODEL.md)
conserve séparément unités chargées, fichiers installés et jobs du manager.
Cible/stockage, provenance, alias, inconnus, limites et comparaison sont validés
sans IO ou horloge implicite. Aucun transport, classement de pertinence ou
raccordement aux mutations n'est ajouté. Le collecteur fermé reste inchangé.

35 nouveaux tests, sélection locale de 92 contrôles et gardes statiques requis
après gel. Zéro Actions prévu ; toutes les Quality globales restent différées.
Les indicateurs de clôture restent faux. Checkpoint et arrêt avant le prochain
lot de sélection conservatrice de pertinence, toujours sans transport hôte.
Sections suivantes historiques.

## Contrat de découverte des unités hors profil

Le [contrat de découverte systemd](PHASE5_SYSTEMD_SCOPE.md) part de
`75e987ce` (73 contrôles et huit scénarios système, run `36225529757`).
Il distingue unités chargées, fichiers installés et jobs du manager, puis
fixe pertinence, inconnus, provenance, limites et protocole sans chargement.
Sa matrice décrit 28 futurs cas, tous non exécutés. Ce lot est documentaire :
aucun élargissement du collecteur, code/test/workflow changé ou Actions lancé.

Les indicateurs globaux restent faux. Les Quality différées restent requises
avant promotion. Prochain lot distinct : modèle privé pur des trois listes,
sans transport hôte, validable localement. Checkpoint et arrêt avant ce lot.
Les sections suivantes décrivent les états historiques.

## Collecte systemd limitée en lecture seule

Sur la base `c8af4c8d`, [SystemdObserver](PHASE5_SYSTEMD_OBSERVATIONS.md)
relit deux ou quatre unités provisionnées, leurs fichiers, états et provenance.
Aucune mutation de service ; systemd_system reste partiel et cinq familles
restent inconnues. Le pont privé exige le profil métier scellé ; la fixture
système isolée ne prétend pas qualifier ce pont ni l'application complète.

73 contrôles locaux et huit scénarios réels sont requis pour ce lot, avec un
seul job ciblé Debian 13 après gel. Les preuves exactes sont dans le checkpoint.
Les Quality globales et promotions restent différées. Tous les indicateurs de
clôture restent faux. Checkpoint et arrêt avant un autre périmètre de collecte.
Les sections suivantes conservent les états historiques.

## Modèle privé des observations de lanceurs

Sur la base `d7ace453`, le [nouveau modèle](PHASE5_LAUNCHER_OBSERVATIONS.md)
valide des déclarations typées liées à l'instance, au pin Web et aux besoins
de stockage. Six familles restent obligatoires ; les inconnus ne deviennent
pas des absences. Comparaison des observations, confidentialité et limites sont
couvertes par 23 nouveaux tests, soit 46 contrôles locaux avec les voisins.

Aucun collecteur hôte ou lanceur n'est ajouté. Aucun workflow modifié ou lancé ;
Quality globale et promotions restent différées. Tous les indicateurs de
clôture restent faux. Prochain lot distinct : collecte systemd limitée en lecture
seule. Checkpoint puis arrêt avant de le commencer. Sections suivantes historiques.

## Contrat CLI et planificateurs : audit de source terminé

Le [catalogue et contrat](PHASE5_CLI_SCHEDULERS.md) identifient douze points
d'entrée Web et trois contextes connexes sur le pin `2a27c7a1`. Base Installer
`cb561320`, validée uniquement par la campagne ciblée `36222491251`.
Ce lot change exclusivement la documentation et se vérifie localement, sans
Actions. Aucun script métier ni serveur n'est exécuté ou inspecté.

Les CLI historiques ne sont pas présumés compatibles avec l'identité dédiée
ou le gate commun. Même export-subject et le préflight IA peuvent écrire.
Aucun lanceur géré supplémentaire, inventaire hôte complet ou promotion.
Prochaine étape distincte : modèle privé des observations de lanceurs et de
leur couverture. Checkpoint puis arrêt avant cette implémentation.
Les sections ci-dessous conservent les états historiques.

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

## 2026-09-28 - Reprise 5D depuis le gel 5C4

La base qualifiée est `042e844061d26dca875ba18382b54dcf688c39de`, arbre
`67f361d6d8794f383f5e5b8359536d4efa5ffc1c`, 304 fichiers. 5A, 5B et 5C sont
acquises dans leurs profils documentés, dont la transition managed 5C4 sur
Debian 13/PHP 8.4/Ext4. Les sections précédentes sont historiques.

Le lot [5D1](PHASE5D_APPLICATION_JOURNAL.md) ajoute les adaptateurs SQL fresh,
finalisation, transition de stockages et autorisation de réouverture au contrat
du journal. Il sépare la reconnaissance read-only de la reprise mutante et lie
les reçus privés à l'installation approuvée. Les contrats 5C4 restent inchangés.

Le wizard public termine encore par Sources prêtes. La composition publique,
l'activation des services et la recette opérateur complète restent à livrer.
La phase 5 et les issues #1/#3/#13/Web #135 restent ouvertes ; aucun succès global
d'installation n'est déduit de ce raccordement transactionnel privé. Les preuves
de qualification de ce lot sont celles de son checkpoint, pas les anciens PASS.

## 5D2 - Wizard fresh et composition immuable

Le fresh storage est raccordé au wizard sur hôte Debian 13 préparé, jusqu’au SQL,
au sceau et aux services sous maintenance. Les choix sont persistants et non
secrets ; l’identité du sceau est fixée avant le plan. La réouverture reconstruit
le même registre, et la ressaisie des secrets ne rejoue aucune étape.
Voir `docs/PHASE5D_WIZARD_COMPOSITION.md` pour les limites et la qualification.
Phase 5 reste ouverte : activation produit, wizard upgrade et préparation
automatique des dépendances/MariaDB restent à livrer. Les acquis 5C4/5D1 demeurent.
