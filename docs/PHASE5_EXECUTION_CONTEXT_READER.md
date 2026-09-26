# Lecteur du contexte configuré des services liés

## Base et périmètre

Base `1b3a0887d3b9ea39c61e79eae329eac8512c7f13`, arbre
`f4a32232b9ed8f911158506456796139fc8d3293`, 250 fichiers. Ce parent documentaire
a vérifié les 16 sources et le [contrat fermé](PHASE5_SYSTEMD_EXECUTION_CONTEXT.md)
sans Actions ni changement runtime. Son runtime était celui de7bb59a67,
qualifié par run36256184902. Ces résultats ne qualifient pas ce nouveau lecteur.

`installer/systemd_execution_context.py` implémente le premier profil proposé :
`SystemdExecutionContext(target, storage).collect()`. Aucun ancien sample, PID,
FD, callback, nom d'unité, propriété ou interface fourni par l'appelant. Le
census garde ses FD jusqu'aux dernières vérifications procfs, de vivacité et de
durée. Les primitives parentes, le modèle de pertinence et le Web sont inchangés.

Les20 propriétés exactement documentées sont codées dans une allowlist fixe,
pas chargées depuis le JSON documentaire. Elles visent uniquement Service sur
le chemin canonique d'invocation déjà acquis. Les autres types liés reçoivent
un bloc avec properties=null, distinct d'un bloc observé vide. Les unités sans
leader lié ne sont pas interrogées. Service éligible avec propriété illisible,
absente, mal typée ou hors borne : refus entier, sans fallback ni retry.

## Données privées et invariants

Le décodage exige les variantes/types exacts et conserve l'ordre et les doublons
des listes. Chaînes UTF-8 sous plafonds, sans NUL/C0/C1/DEL/surrogate ; booleans
stricts, enums fermées, binds à quatre champs et flags uint64 opaques. Les
limites par propriété et les limites cumulées du contrat s'appliquent ensemble.
Les textes numériques User/Group restent des chaînes. Aucun NSS, argument de
commande, environnement, credential, fichier d'unité ou contenu d'image n'est lu.

Les valeurs !/~ de WorkingDirectory, préfixes de chemins, modes Ex et booléens
ambigus restent des données configurées. Pas de projection vers IdentityFact ou
PathFact, UID/GID observé, chemin hôte ou namespace effectif. Le modèle reçoit
les mêmes faits de candidats et relations que le parent ; aucun nouveau motif
positif de writer ou exclusion n'est ajouté par ce bloc de configuration.

Premier passage : tous les mappings, regroupements validés, relations puis
contexte par unité/service distinct. Second passage : tous les remappings,
relations puis contexte relus et comparés exactement. Les deux tours complets
du manager, tâches, topologie et provenance restent exigés. Un changement de
configuration à invocation constante est refusé. Une nouvelle invocation n'est
pas adoptée après restart ; disparition n'entraîne aucun chargement nommé.

Le nouvel index hache ensemble l'empreinte précédente (qui lie déjà census,
tous les leaders et relations) et tous les blocs de contexte. Les faits du
modèle sont rattachés à cet index exact, la sélection recalculée et l'enveloppe
combinée rebornée. Aucun FD ne subsiste au retour, aucun reçu vivant ou durable.

Budgets inchangés par rapport au contrat :24+8N+18M+40S appels, S<=M<=N<=128,
réservation initiale24+66N ensuite resserrée sans reset ;60s partagées,
5s/client,2Mio/réponse,8Mio D-Bus et4Mio d'enveloppe totale. Textes256Kio,
4096 éléments groupes/chemins et1024 binds par passage. Les plafonds procfs
restent512 leaders/1024 tâches et FD,32Mio et32768 lectures/entrées. Un hôte
lent, trop grand ou mouvant est refusé ; pas de hausse implicite de limite.

Le rapport ajoute des comptes et selected_service_configuration_observed ;
ce flag étroit décrit le passage terminé, y compris zéro service (voir compte).
effective_context_verified, configured_identity_projection_delivered,
execution_commands_observed et tous les flags globaux restent faux. Report,
repr et erreurs n'exposent pas de compte, chemin, unité, PID ou réponse brute.

## Contrôles et recette prévus après gel

26 tests nouveaux, aucun retrait de baseline :325 contrôles sélectionnés,
957 core détectables/952 requis. Les contrôles vérifient les formes/UTF-8/bornes,
ordre, listes répétées, compteurs cumulés, regroupement, non-service et absence
de candidat, erreurs sans repli, dérives, FD fermés, preuve liée à tout le census,
confidentialité, absence de projection et plafond final de l'enveloppe.

Chaque job Debian13 ciblé contient325 contrôles puis8 cas réels census/relations
parents et12 nouveaux cas. Pas de matrice globale, SQL, packages, DOM ou Web
rejouée pour ce lot. Les recettes indépendantes continuent après un échec
précédent mais cet échec reste bloquant. Résultats exacts dans le checkpoint,
aucun succès anticipé dans ce document gelé.

La recette `systemd_execution_context_systemd.py` est explicitement réservée au
conteneur root/systemd jetable. Elle démarre des services de fixture et un scope,
avec comptes dédiés, helpers de threads/descendants et unités Type=exec. Les
préconditions attendent un vrai sleep dans le scope et le PID prêt des helpers ;
pas de délai de rattrapage dans le collecteur. Les changements de configuration,
arrêts/restarts et copie de dépendances d'un mini-rootfs appartiennent au banc.

| Cas réel | Preuve demandée |
| --- | --- |
|01 | Noms, IDs numériques, groupes supplémentaires et User vide conservés |
|02 | Scope lié sans appel Service, service/timer sans processus non chargés |
|03 | DynamicUser alloué et DynamicUser avec compte statique, sans projection |
|04 | Binds réellement montés, namespace différent, listes et mode disconnected |
|05 | WorkingDirectory vide, home et missing_ok avec marqueur ! |
|06 | PrivateUsersEx, RootImage et PAMName relus après reload, processus inchangé |
|07 | Dérive réelle de configuration à invocation constante, refus |
|08 | Mutation fsuid de thread pendant les propriétés, refus |
|09 | Disparition après User, Group refusé, aucune réapparition de l'unité |
|10 | Restart après User, ancienne invocation inutilisable et nouvelle non adoptée |
|11 | Trois leaders regroupés, FD fermés, budgets/digests et rapport privé |
|12 | Mini-rootfs effectivement utilisé par sleep, RootDirectory reste déclaré |

Le cas06 observe de la configuration rechargée, pas l'activation de PAM, d'une
image ou d'un namespace utilisateur : le PID et son namespace sont comparés.
Le cas12 utilise une vraie racine jetable contenant le sleep officiel et ses
librairies déjà installées ; le banc vérifie /proc/PID/root. Cela ne transforme
pas le lecteur en résolveur de rootfs ni ne qualifie un moteur d'images.

Premier candidat b92c1075, arbre bda44455d45e0e1065a78cc0385acb61d090cfc2 :
run36260640278 refusé.325 contrôles Debian13 et8 cas parents passent ; les12
nouveaux cas ne démarrent pas (setUpClass,0 tests,1 erreur,0 skip). Le journal
montre context-root.service refusé à EXEC, Permission denied. Sa racine contenant
les exécutables était sous le tmpfs /run du conteneur. La correction du banc
place seulement ce mini-rootfs sous /var/lib/hestia-context-root-fixture ; /run
conserve les données et le helper interprété. Elle exige et enregistre statvfs :
/run noexec, mini-rootfs exécutable. Ce contrôle rend l'hypothèse de montage
vérifiable au prochain run ; le premier journal seul ne contenait pas ses flags.
Le lecteur et ses26 tests sont inchangés. Second job ciblé requis après nouveau
gel, sans modification de seuil ni suppression de scénario. Conserver l'artefact
négatif10911767821, SHA256 f591d865f7444fc068f70adfa77e8b3151ceee72b0b4e08e30510b3a3013becb.

Second candidat c7e9e52a, run36260975820 :325 contrôles,8 parents et11/12 nouveaux
cas passent (1 échec,0 erreur,0 skip). statvfs confirme /run noexec et rootfs
exécutable ; le service root démarre. Le dernier cas exigeait à tort que le texte
du lien /proc/PID/root égale le chemin hôte : la valeur observée est /. La source
namespace-v257.c épinglée (setup_namespace, lignes2845–2887) décrit le bind du
RootDirectory puis mount_switch_root ; un nom de lien n'identifie pas l'inode.
Le banc vérifie désormais les paires dev/ino de /proc/PID/root et /proc/PID/exe
contre la racine et le sleep copiés, et leur différence avec ceux du conteneur.
Il exige aussi un namespace de montage différent et un marqueur lisible dans
la racine privée, absent à la racine hôte. Ces identités sont conservées dans
l'artefact ; aucune identité système effective n'est pour autant certifiée par
le produit. Troisième job ciblé après gel, lecteur et contrôles produit inchangés.
Artefact négatif10912586738 : SHA256 0cb5631011ffbe42652e1071687f0358e7c49196bf54c3708fe52d5e1c70bb4d.

Les24 lignes executed=false du JSON parent restent une matrice de conception
historique, pas des résultats de tests. Les contrôles locaux couvrent notamment
strict_decode, bounded_budget, all_bindings_digest et unsupported_property ;
les12 cas ci-dessus couvrent les observations/mutations réelles correspondantes.
Les tests hérités vérifient les invariants des transports et du modèle ; leur
succès ne prouve ni tous les modes d'isolation ni l'inventaire complet d'un hôte.

Le job utilise Dockerfile.discovery, réseau none et cgroup privé, vérifie la
console de fixture masked/inactive/MainPID=0/NRestarts=0 et conserve le journal.
Le workflow système global reçoit aussi cette recette Debian13 pour sa future
campagne obligatoire. Son image plus large n'est pas requalifiée par ce job ;
son bruit de services/timers et sa console devront être contrôlés avant lancement
global. Ne pas supposer que ce résultat ciblé couvre Debian12 ou PHP/Web complet.

## Arrêt et prochaine frontière

Checkpoint complet avec sources/modes, preuves négatives historiques et analyse
primaire du contrat ; arrêt après vérification de la recette. Si elle échoue,
conserver son état exact, ne pas annoncer ce lot qualifié ni relancer à l'aveugle.
Les causes historiques64f97bf5/2907fe68/12060235 et l'intermittence DOM restent
non résolues ; c2acc806 reste rejeté. Aucune PR, promotion ou déploiement.

Prochain petit lot : contrat de collecte des sept familles de commandes Ex,
avec flags de privilège, phases, statuts runtime et confidentialité des arguments.
Ni interprétation shell ni lecture Environment. La projection conservatrice
des textes configurés, unités sans processus et collecteur provisionné restent
à traiter ensuite, puis autres lanceurs, maintenance/backup exhaustifs5C2,
upgrade réel5C3, reprise/rollback5C4, orchestration/wizard5D et Quality finales.
