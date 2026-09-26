# Recensement vivant, relations et sélection de revue

## Base et objectif

Base `b1078e9d02820b19945a25271953df95dd3e9efb`, arbre
`e96bb8853af307079857a72e4a566c3d5a0e3f85`,242 fichiers. Le run36253650219
qualifie cette base avec275 contrôles et24 scénarios réels. Il ne qualifie pas
le nouvel arbre. Le premier échec du parent, run36253303139 sur12060235,
reste conservé et sa cause non établie. L'instrumentation de recette et toutes
ses assertions restent en place ; aucune résolution de cet incident n'est annoncée.

`systemd_census_relations.SystemdCensusRelations(target,storage).collect()`
intègre les noms et huit relations au cycle de vie du census. Les entrées restent
la cible et le stockage validés ; aucun PID, callback, ancien sample ou FD externe.
Le résultat est une sélection conservatrice pour revue, sans admission au profil,
exclusion automatique, drainage, endpoint ou changement du wizard.

## Cycle de collecte et regroupement

Le premier tour complet de listes/provenance précède l'ouverture des PIDFD_THREAD
de toutes les tâches visibles. Le premier passage procfs et sa comparaison de
topologie établissent les candidats. Tous les FD restent possédés jusqu'au second
passage complet procfs, aux contrôles de contexte/fdinfo/vivacité et au contrôle
final précédant leur fermeture, comme dans le parent.

Premier passage D-Bus : quatre appels par leader candidat lié, puis Names et
Triggers,TriggeredBy,Requires,Wants,BindsTo,Upholds,OnSuccess,OnFailure par unité
distincte. Les conflits entre leaders d'une même unité sont refusés avant le
choix d'un représentant stable. Le second passage relie à nouveau chaque leader,
compare ces bindings, puis relit les neuf propriétés et compare leurs ensembles.
Le second tour complet de listes/provenance vient ensuite, avant les dernières
lectures procfs. Un changement d'identité/thread/population durant les propriétés
reste donc un refus, même si le leader et l'InvocationID sont restés identiques.

Les lectures utilisent exclusivement le chemin canonique d'invocation déjà lié.
Le helper privé de lecture de détails est partagé avec le lecteur de relations
parent, dont l'ordre intercalé binding/propriétés par indice explicite reste
inchangé. Pas de GetAll, Environment, ExecStart, propriétés nommées, RefUnit,
GetUnitByPID, rechargement ou action système. Aucune résolution récursive d'un
nom de dépendance ; absence, erreur, divergence ou dépassement refuse l'ensemble.

Les alias enrichissent seulement les objets effectivement liés. Les listes brutes
sont validées et comparées avant enrichissement, puis les conflits d'alias sont
contrôlés par le modèle de découverte. Une cible est rattachée uniquement à un
nom chargé observé ; un fichier installé de même basename ne suffit pas. Les
cibles absentes, templates et unités sans processus gardent leurs inconnus.
Une unité sans processus peut rejoindre la revue par une relation positive ;
cela n'atteste pas ses propres propriétés, son contexte ou son éventuelle écriture.

## Fait de candidat distinct de l'identité effective

Le modèle pur ajoute CensusCandidateFact et le champ optionnel candidates de
UnitFacts, ainsi que census_sha256 dans RelevanceFacts. Les anciens constructeurs
restent utilisables grâce aux valeurs par défaut None. Les nouveaux champs sont
privés ; les empreintes sérialisées du modèle évoluent avec cette extension.
None signifie non observé ; un tuple vide n'est pas une preuve d'inventaire vide.

Un fait conserve le ProcessBinding du leader (PID,start_ticks,namespace PID,
cgroup,digest de son enregistrement), le digest du census complet, le digest
du couple binding/leader et une liste fermée de raisons. Les raisons positives
sont UID_MATCH_IN_TASK,GID_MATCH_IN_TASK,SUPPLEMENTARY_GROUP_MATCH_IN_TASK et
DESCENDANT_AT_OBSERVATION. Chacune produit un motif CENSUS_* propre dans la revue.
UNRESOLVED_TASK ajoute un problème, jamais une graine positive. Une raison positive
et un inconnu peuvent coexister sans s'effacer ; un inconnu peut néanmoins être
relié à une graine positive par les relations réellement observées.

Le fsuid d'un thread root ne devient pas l'UID effectif du leader. Aucun
IdentityFact ou ExternalBinding opérateur fictif n'est créé. Les quatre UID/GID,
les groupes et les threads restent dans le census ; seule l'association du leader
retournée par le manager est liée. L'appartenance des autres threads à cette unité
reste non inférée. Les leaders inconnus et PID1 restent explicitement non liés,
sans faux fait. Les non-correspondances restent présentes et non exclues.

Le modèle exige une raison non vide/unique/fermée, des digests valides et identiques
au census déclaré, un leader PID>=2 dans le namespace observé et un contexte valide.
start_ticks=0 reste accepté pour ce fait de census comme dans la primitive parent ;
le contrat IdentityFact effectif conserve son minimum propre. Un PID ne peut être
déclaré pour deux objets ; un candidat et une identité effective du même PID ne
peuvent contredire sa date/namespace/cgroup. Les doublons restent refusés.

Le modèle pur n'authentifie pas des déclarations fournies isolément. Le transport
fabrique les faits depuis sa propre collecte encore ouverte, jamais depuis un
JSON reçu du caller. L'index enrichi inclut dans son digest les relations, le
census complet et TOUS les bindings, pas seulement le représentant par unité.
Les faits sont liés à cet index exact. Les contrôles empêchent le mélange accidentel
d'observations ; ils ne contiennent pas un administrateur privilégié malveillant.

## Revue, limites et confidentialité

Les raisons positives élargissent la revue via le graphe non orienté existant,
sans simuler l'ordre d'exécution systemd. RELATED_UNMANAGED et UNRESOLVED restent
les seules décisions ; aucun KNOWN_PROVISIONED, enrolled=false partout. Des noms
de service HESTIA, des chemins ou une absence de signal ne suffisent jamais à
certifier la pertinence métier. Le manager peut retourner une association de
surveillance plutôt qu'un propriétaire exclusif, conformément au contrat parent.

N leaders liés au plus128, M unités détaillées au plusN. Budget24+8N+18M appels,
resserré après le premier mapping sans réinitialiser compteur, délai ou octets.
Les neuf propriétés sont lues une fois par unité et par passage, même si128
leaders partagent cette unité. Les faits candidats sont limités à128 par unité
ET cumulés. Plafonds hérités :4096 noms,8192 relations par passage ;2Mio/réponse,
8Mio D-Bus cumulés ;512 leaders visibles,1024 tâches/FD,32768 entrées,32Mio et
32768 lectures/readlink procfs. Collecte60s partagés,5s/client, enveloppe finale4Mio.
Pas de retry, repli, hausse implicite de plafond ou watchdog d'appel noyau bloqué.

Observations successives, ABA et tâches transitoires non exclus, filiation passée
après reparentage non reconstruite. Périmètre du namespace observateur seulement.
Les FD sont fermés au retour : aucun reçu vivant ni autorité durable. Les manifests
privés gardent les preuves ; report/repr n'exposent ni PID, unité, chemin, credentials
ou invocation. Le rapport reprend les comptes et blocages du modèle, plus les
indicateurs étroits de census, listes, liaison et relations observées.

Restent faux : process_census_authenticated, effective_identities_observed,
all_task_unit_memberships_observed, all_descendants_identified, live_receipt,
host_relevance_verified, automatic_exclusion_allowed, drain_allowed,
host_scheduler_inventory_complete, storage_inventory_complete et phase5_complete.
Les autres indicateurs globaux de phase5 restent faux et inchangés.

## Qualification après gel

24 nouveaux tests sans retrait de baseline :299 contrôles sélectionnés,
931 core détectables et926 requis. Les tests couvrent les faits typés, signaux
positifs/inconnus, digests, conflits PID, limites cumulées, regroupements multi-
processus, alias, non-résolution des fichiers, dérives, fermeture FD et confidentialité.
Les anciens tests du modèle et des transports restent requis, sans assertion affaiblie.

Un seul job Debian13 prévu après gel code/docs :299 contrôleurs,8 scénarios
relations parents,8 liaison census parents et8 nouveaux réels. Nouveaux cas :
trois leaders et un seul jeu de propriétés par passage ; signal fsuid/thread et
descendants élargissant la revue aux dépendances/timer ; zombie non lié ; changement
de relation à invocation constante ; fsuid modifié durant les propriétés ; disparition
sans rechargement nommé ; redémarrage avec ancien chemin inutilisable ; fermeture
FD, empreintes et tous les indicateurs d'autorité faux. Les mutations appartiennent
exclusivement au banc jetable, réseau coupé. Les recettes indépendantes restent
exécutées après un échec précédent, qui demeure bloquant pour le job.

Sources primaires déjà épinglées dans PHASE5_SYSTEMD_INVOCATION_CONTRACT.json et
PHASE5_PROCESS_CENSUS_SOURCES.json : notamment les getters Names/dépendances de
dbus-unit.c257 et les PIDFD_THREAD/procfs Linux6.12. Analyse et empreintes sont
conservées ; seule la recette réelle qualifie le profil exécuté. Work local n'a
pas le profil systemd/multi-UID et ne remplace pas cette preuve par des mocks.
Résultats exacts au checkpoint après gel ; aucune Quality globale ou promotion.

## Prochaine reprise

Checkpoint puis arrêt. Prochain petit lot : définir la collecte d'identités
configurées et du contexte d'exécution via les seules adresses admissibles,
en gardant distincts configuration, credentials constatés, namespaces et chemins.
Les unités sans processus, le collecteur provisionné à corriger, les autres
familles de lanceurs, les neuf groupes de producteurs et l'inventaire exhaustif
restent ouverts. Maintenance/sauvegarde exhaustives5C2, upgrade réel5C3, reprise/
rollback5C4, orchestration système/wizard5D et Quality finales restent à fermer.
L'incident parent12060235, le rejet c2acc806 et l'intermittence DOM restent conservés.

## Premier job du lot : qualification refusée

Le commit64f97bf59848ed769a6a7b2d0cfbc4dbe2a02872, arbre
58079c57613f803a7ff622645c55904c89344cc2, a échoué au run36255253018.
299 contrôleurs verts ;20 scénarios sur24 verts, trois erreurs et un échec,
zéro skip. Le lecteur parent de relations a refusé une nouvelle collecte après
redémarrage avec diagnostic générique ; le census parent a détecté une population
changée. Le nouveau test de redémarrage a refusé AVANT sa mutation et son assertion
de mutation atteinte a correctement échoué ; le dernier cas positif a été refusé.
Ces résultats ne qualifient donc pas la livraison. Sources et preuves négatives
sont conservées, sans conclusion prématurée sur les causes communes ou distinctes.

Le diagnostic de fixture est étendu aux trois recettes : au plus8 exceptions,
16 frames chacune, noms de fichiers/fonctions, lignes, coordonnées PID numériques
et tokens fermés du parseur d'états. Pas de message libre, credentials, commande
ou chemin privé. Aucun changement de code produit, délai ou assertion de recette.
Le job conserve aussi le journal systemd et les dernières listes après les tests.
Un second job ciblé avec instrumentation est prévu sur un nouvel arbre gelé ;
aucune relance identique. Un vert ultérieur ne prouvera pas la résolution de
ces premiers refus. La suite reste conditionnée aux preuves exactes du checkpoint.

## Second job : conflit observé et correction du banc sans console

Le commit2907fe6810a9ace1d26512f3217b64202b2a6eed, arbre
31ba06ab04bb68a91498e799f25331eaa8afc6d4, échoue au run36255697233 :
299 contrôles verts,22/24 cas système verts, deux erreurs et zéro échec/skip.
Les huit nouveaux cas passent. Les deux recettes parentes sont refusées par
`DISCOVERY_JOB_BINDING_CONFLICT`, contrôle de cohérence entre ListUnits/ListJobs.
Le journal montre getty@tty1.service redémarrant toutes les5 à6 secondes,
notamment à16:30:55 et16:31:00, aux fenêtres des deux refus. Les identifiants
des jobs conflictuels ne figurent pas au diagnostic ; cette corrélation ne
suffit pas à attribuer chaque refus à cette unité ni à expliquer rétroactivement
les incidents64f97bf5 et12060235.

L'image Dockerfile.discovery masque uniquement getty@tty1.service, console
inutilisée dans ce banc sans terminal. Le job exige son état masked/inactive,
MainPID=0/NRestarts=0 avant les recettes et conserve le journal après celles-ci.
Aucune unité du système hôte ou du produit n'est modifiée. Aucun changement
du collecteur, de ses refus, des assertions de recettes ou des délais. Les
mutations explicites de jobs/processus/relation restent testées ; la collecte
continue de refuser les changements de population et les jobs incohérents.

Un troisième et dernier job ciblé du lot est prévu sur ce nouvel arbre gelé.
Pas de relance identique ni campagne globale. Ses preuves exactes déterminent
le statut du checkpoint ; aucun succès n'est anticipé ici. Les deux archives
négatives complètes restent jointes, ainsi que l'incident parent et c2acc806.
