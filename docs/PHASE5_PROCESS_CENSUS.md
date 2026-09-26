# Recensement borné des tâches visibles

Extension ultérieure : [liaison des candidats vivants](PHASE5_CENSUS_INVOCATIONS.md).
Le contrat autonome décrit ci-dessous reste distinct.

## Base et portée

Base Installer `67e6db85970bafebc1653a1e69316ad26b51fc39`, arbre
`4761010e471608b4173ccd5697c870ec13ac609e`, 231 fichiers. Son run36248957724
qualifie 226 contrôles et24 scénarios ciblés, pas ce nouvel arbre.

`installer.process_census.ProcessCensus(uid,gid).collect()` parcourt /proc et
/proc/TGID/task. L'entrée privée est une identité numérique dédiée, sans PID,
chemin, unité ou commande fourni. Elle livre une observation de la population
visible et des candidats de revue. Ce résultat ne certifie pas un recensement
atomique de l'hôte, tous les descendants historiques, un écrivain HESTIA,
une exclusion, une sauvegarde ou un droit de drainage. Aucun consommateur de
mutation, endpoint, wizard, modèle pur ou ancien lecteur runtime n'est modifié.

La correspondance d'identité est recherchée dans chaque tâche, pas déduite du
seul leader. Les quatre UID/GID et groupes supplémentaires restent distincts.
Un thread peut donc signaler son groupe même si les credentials du leader ne
correspondent pas. Le code ne lit jamais cmdline, environ, exe ou un secret.

## Visibilité, parcours et PIDFD

Le contexte procfs du lot parent est requis : root, mêmes namespaces PID/mount/
user/cgroup/time que PID1, procfs entier, sans hidepid restrictif ni subset.
Le contrôle est étendu à tout sous-montage /proc/PID, pas seulement aux chemins
d'un processus sélectionné. Contexte, table de montages, identité hôte et boot
sont comparés. Un administrateur privilégié capable de falsifier ces sources
n'est pas contenu par ce mécanisme. Le périmètre est le namespace de l'observateur,
même s'il s'agit de PID1 d'un conteneur ; ce n'est pas l'hôte extérieur.

scandir parcourt en flux, sous plafond, plutôt que listdir non borné. Les leaders
visibles, dont PID1 et l'observateur, doivent exister ; chaque répertoire task
contient son leader, des TID numériques uniques et aucun lien. Des TID dupliqués
entre groupes, alias numériques, dépassements ou erreurs d'énumération refusent
la collecte. La disparition n'est pas transformée en absence vérifiée.

Un PIDFD_THREAD est ouvert pour chaque tâche, leader compris, et conservé jusqu'à
la clôture. Linux UAPI définit ce drapeau par O_EXCL ; il est utilisé via la valeur
os.O_EXCL de la plateforme, sans constante d'architecture inventée. Un noyau sans
ce support est refusé, sans repli sur un simple PIDFD de groupe. fdinfo doit
rapporter le TID visible attendu. Les ID imbriqués sont validés comme nombres,
mais n'autorisent pas à considérer leur namespace comme pris en charge.
Le FD n'est ni reçu du formulaire ni envoyé à systemd dans ce lot.

Deux passages complets lisent les tâches ; la topologie est comparée avant,
entre et après les passages. Vivacité individuelle, identité FD et contexte
sont recontrôlés avant construction de l'enveloppe, puis la vivacité l'est encore
avant retour. Tous les PIDFD sont fermés, même après une erreur ou un échec de
fermeture isolé. Aucun processus observé n'est tué, déplacé, gelé ou modifié.

## Identités et inconnus

Chaque tâche lisible et vivante conserve start_ticks, PPid, quatre UID/GID,
groupes uniques triés, namespaces et cgroup v2. Pid/Tgid et leurs premiers ID
visibles doivent correspondre à la topologie ; Threads doit égaler le nombre
de tâches du groupe observé. stat est relu après les autres fichiers : date et
parent doivent rester identiques. Les états R/S ne déclenchent pas un refus.
start_ticks=0 est admis ici pour les tâches noyau nées au début du démarrage ;
la primitive de leader sélectionné du parent garde son propre contrat inchangé.

Une tâche dont le PIDFD signale la fin mais qui n'est pas réapée reste
TASK_EXITED_NOT_REAPED. Une permission refusée pendant lecture devient
TASK_UNREADABLE. Les tâches sans namespaces utilisables ou hors du profil
PID/user conservent un code d'inconnu explicite. Aucun credential partiellement
lu n'est présenté comme identité complète. Ces mêmes états doivent être retrouvés
au second passage ; leur FD et leur présence restent contrôlés. Une erreur
pendant l'énumération d'un groupe refuse le recensement entier, car sa liste
n'a alors pas été observée. Les données malformed ne deviennent pas un inconnu
permissif. Les bornes, erreurs de ressource et changements refusent sans retry.

## Candidats et liens parents

Une correspondance dans n'importe lequel des quatre UID/GID, ou dans les groupes
supplémentaires, ajoute un signal positif au TGID. Les descendants du groupe,
suivant les PPid des leaders effectivement observés, sont ajoutés à la revue,
y compris s'ils utilisent une autre identité. Un parent absent de la population,
un cycle ou un parent observé né après son enfant refuse ce graphe ambigu.
Un PPid nul désigne une racine visible et n'est pas remplacé par un parent supposé.

Un groupe contenant une tâche inconnue reçoit UNRESOLVED_TASK. Cet inconnu n'est
pas une correspondance positive fictive et ne propage pas une filiation inventée.
Tous les enregistrements, y compris les groupes sans signal positif, restent
présents dans le manifeste. Une absence de correspondance n'autorise aucune
exclusion : root, configuration, chemins, capacités et autres clients ne sont
pas certifiés par un comparateur UID/GID. La filiation antérieure d'un processus
réaffecté à un autre parent n'est pas reconstruite. Les descendants transitoires
absents entre les lectures et les courses ABA restent possibles.

Les tâches d'autres namespaces demeurent inconnues ; les tâches inaccessibles
ne disparaissent pas du reçu. Un groupe parent lui-même inconnu peut interrompre
le parcours de filiation. Le reçu ne livre aucune liaison systemd nouvelle,
aucun PID directement admissible au drain et aucun FD persistant. Il est déjà
susceptible d'être périmé au retour. Une future liaison devra revalider la date,
les credentials, les tâches et la population ; rouvrir un PID numérique seul
ne suffit pas à préserver l'identité observée ici.

## Bornes et preuves

512 leaders,1024 tâches/PIDFD,32768 entrées de répertoires visitées cumulées,
64 groupes supplémentaires par tâche. Les plafonds procfs hérités sont partagés :
64Kio/fichier,1Mio et8192 lignes par mountinfo,32Mio de contenu et32768 lectures/
readlink. Entiers ASCII et chemins/identifiants suivent les mêmes bornes que le
parent. L'enveloppe complète est limitée à4Mio ; collecte60s, monotone et horloge
murale cohérente. Les contrôles de délai ne sont pas un watchdog contre un appel
noyau bloqué. Un hôte valide trop grand, trop mouvant ou sans FD disponibles est
refusé. Pas de remontée automatique de plafond ni relance silencieuse.

Les [sources primaires épinglées](PHASE5_PROCESS_CENSUS_SOURCES.json) comprennent
les itérateurs procfs TGID/task, le drapeau PIDFD_THREAD, fdinfo/poll individuels,
les credentials Linux et setfsuid utilisé uniquement par la fixture. Ces sources
ne remplacent pas une preuve sur le noyau réellement exécuté. Le profil qualifié
visé est Debian13/Linux avec PIDFD_THREAD ; Debian12 n'est pas revendiqué par
ce lot. Aucun accès D-Bus, systemctl ou client externe n'existe dans le collecteur.

Le rapport public contient comptes, empreinte et indicateurs, sans TID/TGID,
credentials ou chemins. visible_task_enumeration_observed est étroit ;
process_census_authenticated, all_descendants_identified, systemd_bindings_observed,
live_receipt, automatic_exclusion_allowed, drain_allowed et phase5_complete restent faux.

## Qualification après gel et incident local

27 nouveaux tests, ajout sans retrait dans la baseline ; sélection locale de253.
L'ancien test de plafond d'enveloppe des relations a échoué une fois localement :
la première enveloppe pouvait avoir un elapsed_ms de largeur différente de la
seconde. Sa fixture fige maintenant monotonic pour les deux collectes. Le plafond
len(enveloppe)-1 et l'assertion de refus sont conservés ; aucun code de transport
ni délai produit n'a été modifié. Le journal négatif est conservé. Cela ne résout
pas l'intermittence DOM historique test_raw_error_text_is_never_injected_or_displayed.

L'environnement Work local ne fournit pas le profil procfs/UID nécessaire : son
uid_map ne mappe que0 et les TID numériques n'y correspondent pas aux chemins
procfs attendus. Les sondes locales ne constituent donc pas une preuve de
credentials multi-utilisateurs ; aucune assertion système n'a été remplacée.

Un seul job Debian13 prévu après gel :253 contrôles, les8 scénarios du parent
identité, puis8 nouveaux scénarios réels : thread seul avec fsuid dédié ; enfants
et petits-enfants d'une autre identité ; FD fermés et rapports privés ; changement
du fsuid d'un thread ; naissance d'un thread ; mort d'un thread avec leader vivant ;
zombie explicitement inconnu ; masquage réel d'un fichier procfs refusé. Les
mutations sont exclusivement celles de la fixture jetable, réseau coupé.
Zéro erreur/échec/skip, sources et modes stables, artefact exact requis. Résultats
après gel dans le checkpoint, sans PASS anticipé ici. La recette rejoint la future
campagne système sous Debian13. Pas de Quality globale ou promotion sur ce seul lot.

## Prochaine reprise

Checkpoint puis arrêt. Raccorder ensuite les candidats observés à la liaison
systemd par PIDFD/invocation, avec contrôle explicite de fraîcheur, groupes ayant
plusieurs processus et maintien des inconnus. La relecture historique des unités
nommées reste interdite après la preuve négative c2acc806. Les identités configurées,
chaînes d'exécution, unités sans processus, collecteur provisionné à corriger,
autres lanceurs, maintenance et sauvegarde exhaustives, upgrade réel, reprise/
rollback et orchestration du wizard restent ouverts. Les neuf producteurs et
les indicateurs globaux ne sont pas réputés couverts par ce recensement.
