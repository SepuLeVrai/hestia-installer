# Contrat de lecture du contexte configuré systemd

## Décision et état exact

Base `7bb59a67463841db018cb70c5643eb30ad060e4a`, arbre
`e16822ef6183a28b117cc5d0008b0ea566de798d`, 248 fichiers. Le parent a passé
le run `36256184902` : 299 contrôles en Debian 13 et 24 cas réels, sans
erreur, échec ou skip. Les 299 contrôles locaux et l'artefact exact sont dans
son checkpoint. Trois jobs ont été nécessaires ; les deux échecs restent conservés.
Le masquage de la console inutilisée du banc ne résout pas rétroactivement les
causes non établies des refus précédents. L'incident parent12060235, le prototype
nommé c2acc806 rejeté et l'intermittence DOM restent distincts et conservés.

Ce lot **définit le prochain lecteur**, sans l'implémenter. Il ne change que
la documentation : code, tests, baseline, workflows et Web inchangés. Zéro
Actions ; aucun appel D-Bus, NSS, montage, commande de service ou syscall de
collecte nouveau n'est exécuté. Les signatures et getters sont relus dans
16 fichiers officiels systemd v257, commit
`70bae7648f2c18010187c9cf20093155eaa26029`, octets vérifiés contre leurs blobs Git.
Le [contrat JSON](PHASE5_SYSTEMD_EXECUTION_CONTEXT.json) épingle les sources,
les 20 propriétés et 24 futurs cas d'acceptation, tous `executed=false`.

Premier profil : **configuration des seuls services déjà liés au census vivant**.
Ce profil complète la preuve privée sans transformer une configuration en
identité effective, chemin résolu, couverture exhaustive ou autorité de drainage.
Les commandes et leur contexte propre formeront un lot suivant. Ne pas convertir
ce contrat de lecture en moteur qui interprète ou exécute une unité systemd.

## Route et getters relus

Le futur lecteur compose `SystemdCensusRelations` dans son cycle de FD ouverts.
Entrées : cible et stockage validés seulement. Ni PID, chemin, ancien reçu,
invocation, FD, interface ou nom de propriété fourni par l'appelant.

Les mappings des leaders, Id et InvocationID sont acquis et recontrôlés selon
les contrats existants. Une unité dont le nom primaire validé se termine par
`.service` est éligible ; les autres types gardent un contexte explicitement
non couvert et ne reçoivent aucun appel Service. Un service éligible dont une
propriété est absente/illisible fait refuser la collecte, sans repli de version.
Les services inactifs, sans leader admissible, et les cibles connues seulement
par une relation ne sont pas interrogés par nom pour compléter les trous.

Chaque propriété utilise `Properties.Get`, signature d'entrée `ss`, interface
exacte `org.freedesktop.systemd1.Service`, destination propriétaire unique du
manager déjà vérifié et **chemin canonique de l'invocation liée**. Pas de GetAll,
introspection, `self`, objet nommé, RefUnit, LoadUnit, Set, abonnement ou retry.

`dbus.c` inscrit `bus_exec_vtable` sous l'interface Service. Son resolver
`bus_exec_context_find` passe par `find_unit`, vérifie le type de l'interface
et obtient le contexte à l'offset de l'unité. `manager_load_unit_from_dbus_path`
traite l'ID d'invocation par sa table et retourne une erreur si l'ID est absent,
avant la branche de chargement par nom. Le nom de cette fonction ne permet pas
à lui seul de conclure à un chargement. La route par nom reste interdite.

Les getters retenus sérialisent des champs mémoire ou des tables d'enum ;
WorkingDirectory ajoute ses marqueurs et BindPaths parcourt la liste mémoire.
Ils n'appellent pas de résolution NSS, de lecture de fichier utilisateur ou
de méthode de chargement dans les corps et helpers examinés. C'est une analyse
de la source épinglée, pas une preuve sur toute version ni une recette exécutée.
Le tag et une chaîne Version ne suffisent pas à qualifier les appels réels.

## Liste fermée du premier profil

Toutes ces propriétés sont lues sur l'interface Service, dans cet ordre.
`CONST` dans les vtables n'autorise ni cache entre collectes ni abandon de la
seconde lecture : daemon-reload et changements de configuration restent possibles.

| Propriété | Type D-Bus | Valeur conservée et réserve |
| --- | --- | --- |
| User | s | Texte configuré, nom ou ID ; vide reste vide |
| Group | s | Texte configuré ; ne pas déduire le groupe primaire d'un nom |
| SupplementaryGroups | as | Liste configurée, pas tous les groupes effectifs |
| DynamicUser | b | Option configurée, pas une allocation UID/GID attestée |
| PAMName | s | Nom de service PAM seulement ; ses modules restent inconnus |
| WorkingDirectory | s | Représentation D-Bus, dont `~`, `!` et vide |
| RootDirectory | s | Chemin déclaré ; vide ne prouve pas la racine effective |
| RootImage | s | Référence déclarée ; image jamais ouverte ni montée |
| RootDirectoryStartOnly | b | Option Service distincte du contexte Exec partagé |
| PrivateUsersEx | s | `no`, `self`, `identity` dans la source épinglée |
| PrivateTmpEx | s | `no`, `connected`, `disconnected` dans cette source |
| PrivateMounts | b | Projection avec perte du tristate interne |
| PrivateDevices | b | Option configurée ; ne prouve pas le namespace effectif |
| ProtectHome | s | `no`, `yes`, `read-only`, `tmpfs` |
| ProtectSystem | s | `no`, `yes`, `full`, `strict` |
| ReadWritePaths | as | Textes et préfixes conservés, pas une preuve de droits |
| ReadOnlyPaths | as | Textes et préfixes conservés, pas une preuve d'absence d'écriture |
| InaccessiblePaths | as | Textes déclarés, pas une vérification d'inaccessibilité |
| BindPaths | a(ssbt) | Source, destination, ignore_enoent, flags uint64 |
| BindReadOnlyPaths | a(ssbt) | Même structure, liste filtrée read_only par le getter |

User/Group/PAMName et les éléments de SupplementaryGroups sont des textes opaques
de 256 octets UTF-8 maximum. Les chemins/textes de chemin sont bornés à 2048
octets ; aucun chemin n'est résolu, simplifié ou rendu relatif à l'hôte. Refuser
NUL, contrôles C0/C1/DEL et surrogates ; texte atypique hors profil = refus,
pas d'effacement de valeur. Les chaînes scalaires peuvent être vides ; les
éléments de listes et les deux chemins d'un bind doivent être non vides.
Ne pas imposer une syntaxe ASCII de compte plus restrictive en la présentant
comme la syntaxe exhaustive acceptée par systemd.

SupplementaryGroups : 128 éléments maximum ; chaque liste de chemins : 256 ;
chaque liste de binds : 128. Les listes gardent leur ordre et leurs doublons,
qui peuvent être significatifs ; aucune normalisation en ensemble. Enum inconnue,
mauvais type, booléen représenté par un entier, arité étrangère, uint64 négatif/
hors plage, clé JSON dupliquée, NaN ou réponse tronquée : refus. Les flags de
bind restent un entier opaque 0..2^64-1, jamais un argument mount. La source
actuelle émet 0 ou MS_REC ; aucune capacité d'exécution n'est déduite de ce fait.

## Sémantique à ne pas perdre

La structure ExecContext contient de la configuration parsée par le manager,
pas les credentials runtime ni une copie fidèle des lignes du fichier d'unité.
Un User vide reste un défaut non projeté ; ne pas fabriquer UID=0. Un nom de
compte n'est pas résolu par getpwnam/getgrnam, getent, nss-systemd ou une autre
source implicite. Les chaînes numériques restent des chaînes dans ce profil.
Les propriétés Service UID/GID (ref_uid/ref_gid) sont exclues : elles ne donnent
pas les quatre UID/GID de toutes les tâches ni le mapping des namespaces.

SupplementaryGroups complète les groupes de la base de comptes selon la
documentation officielle : une liste vide ne prouve pas que le processus n'a
aucun groupe supplémentaire. DynamicUser peut utiliser une identité statique
existante ; ni true ni false n'atteste seul une identité effective durable.
PAM peut migrer processus et descendants vers un scope de session. La réponse
du manager à GetUnitByPIDFD ne devient pas une preuve de propriétaire unique.

WorkingDirectory est particulièrement piégeux : le getter expose `~` pour le
home, et préfixe `!` lorsque missing_ok est actif. Ce n'est pas la syntaxe `-`
de la directive du fichier d'unité. Ne pas passer `!/chemin` au normaliseur de
chemins existant. Conserver la valeur entière et son état non résolu. RootImage,
rootfs, bind mounts, PAM et namespaces empêchent toute assimilation d'un texte
absolu à l'objet de même nom sur l'hôte.

Les booléens historiques PrivateUsers et PrivateTmp réduisent plusieurs modes
à true ; leurs variantes Ex sont donc retenues sans fallback. PrivateMounts
passe un tristate par `value > 0` : false fusionne notamment défaut non spécifié
et faux explicite. Il ne prouve pas l'absence de namespace de montage. D'autres
réglages non lus peuvent en créer un, et la configuration ne prouve pas son
application réelle. Même des valeurs apparemment neutres laissent le contexte
effectif inconnu. PrivateUsersEx n'est pas une lecture de uid_map/gid_map.

## Commandes : frontière suivante, aucune collecte dans ce profil

La source expose sept familles : ExecCondition, ExecStartPre, ExecStart,
ExecStartPost, ExecReload, ExecStop, ExecStopPost. Les variantes historiques
`a(sasbttttuii)` ne sérialisent que le flag ignore-failure, tandis que les
variantes Ex `a(sasasttttuii)` exposent la liste des flags en plus des argv.
Lire seulement ExecStart historique ferait perdre des informations de privilège.

Les préfixes `+` et `!`, et conditionnellement `!!`, modifient les restrictions
appliquées à la commande. `RootDirectoryStartOnly` distingue aussi les phases.
Ainsi un User configuré ne peut pas être attribué sans réserve à toutes les
commandes. Les sept propriétés Ex sont une piste auditée, **pas une allowlist
active dans le premier profil**. Elles transportent les arguments complets,
qui peuvent contenir des secrets ; leur acquisition, réduction, empreinte,
comparaison des états d'exécution et confidentialité exigent leur propre contrat.

Ne lire ni Environment/EnvironmentFiles, credentials, stdin/stdout, FragmentPath
ou DropInPaths pour contourner cette frontière. Pas de parsing shell, substitution
`$`/`%`, expansion de home, résolution PATH, ouverture d'image, recherche récursive
de scripts, exécution de wrapper ou interprétation d'argv. Aucun contenu de
commande, même déclaré, ne doit devenir du code exécuté par l'Installer.

## Composition et bornes futures

Le lecteur possède toujours tous les PIDFD_THREAD de la collecte, sans rouvrir
un PID par son numéro. N leaders liés, M unités distinctes, S services éligibles :
`0 <= S <= M <= N <= 128`. Lire les relations une fois par unité et par passage,
puis les 20 propriétés une fois par service et par passage. Plusieurs leaders
d'une même unité partagent les détails, mais tous leurs mappings restent vérifiés.

Séquence : premier tour des listes/provenance ; premier census/topologie et FD ;
tous les mappings ; relations et contexte ; tous les remappings ; relecture
relations et contexte et comparaison exacte ; second tour des listes ; seconde
lecture des tâches/topologie/contexte, fdinfo, vivacité et construction du sample
tant que les FD sont détenus ; contrôle final et fermeture dans tous les cas.
Toute erreur d'un service éligible refuse l'ensemble, sans omission ni nouveau
budget. Un changement puis retour à la même valeur reste une limite ABA.

| Budget du profil proposé | Plafond |
| --- | --- |
| Appels complets | 24 + 8N + 18M + 40S, au plus 8472 |
| Réservation avant regroupement | 24 + 66N, ensuite resserrée sans reset |
| Réponse / D-Bus cumulé | 2 Mio / 8 Mio, hérités |
| Textes nouveaux cumulés par passage | 256 Kio UTF-8 |
| Éléments de groupes et chemins / binds par passage | 4096 / 1024 |
| Procfs | 512 leaders, 1024 tâches/FD, 32 Mio, 32768 lectures et entrées |
| Durée | 60 s partagées, 5 s/client, pas de retry |
| Enveloppe privée combinée | 4 Mio, doublons des preuves inclus |

Les limites cumulées s'appliquent en plus des limites par propriété. Les octets
des deux champs texte de chaque bind comptent dans les 256 Kio. Le plafond de
8472 est une limite d'admission, pas une promesse de traiter 128 services en
60 secondes. L'hôte trop grand, mouvant ou lent est refusé. Aucun élargissement
de RLIMIT, nouveau thread de collecte ou watchdog noyau n'est proposé.

La preuve privée future doit lier le bloc de contexte, le census entier, tous
les bindings de leaders et les relations à l'index exact. Ne pas jeter cette
liaison lors de l'enrichissement du digest des listes. Le modèle existant
IdentityFact attend des UID/GID numériques : **aucune conversion automatique
du nouveau bloc vers IdentityFact ou PathFact** dans ce premier lecteur.
La sélection conservatrice existante reste fondée sur les candidats/relations
déjà qualifiés ; aucun nouveau signal métier n'est inventé. Le bloc est une
observation privée supplémentaire, sa projection éventuelle est un autre lot.

Report/repr/erreurs restent sans compte, groupe, chemin, unité, PID, invocation,
message libre ou réponse brute. Un rapport étroit pourra compter les services
et propriétés lus, jamais annoncer effective_context_verified. FD fermés au
retour : pas de receipt vivant, exclusion, enrollment, drain, modification de
service, endpoint ou changement de wizard. Tous les indicateurs globaux restent
faux, notamment storage_inventory_complete et phase5_complete.

## Recette à écrire ensuite, pas exécutée ici

Les 24 cas du JSON distinguent validations de décodage, courses et système réel.
Le prochain lot implémentera uniquement ces 20 propriétés sur les services liés.
Vérifier localement les contrôles affectés avant un seul job Debian 13 utile,
sur code et documentation gelés ensemble. Réutiliser le conteneur headless
réseau coupé ; conserver la preuve masked/inactive/MainPID=0/NRestarts=0 du
getty de fixture et le journal. Aucun seuil ni assertion parent ne doit baisser.

Exiger des lectures réelles de User/Group et groupes numériques/noms, DynamicUser,
WorkingDirectory avec ses marqueurs, variantes Ex, binds et contexte distinct.
Déclencher réellement une dérive de contexte à invocation constante, une
disparition après liaison et un restart ; vérifier que le chemin ancien n'est
ni rechargé ni adopté. Contrôler les mutations procfs pendant les détails,
la fermeture des FD, le regroupement, les budgets et la confidentialité.
Une fixture rootfs doit être jetable ; ne pas qualifier un chemin « résolu »
sur la seule lecture de RootDirectory. Le support Debian 12/v252 reste absent.

La vérification du présent lot se limite à la documentation, aux signatures,
aux ancres/hashes/blob IDs des sources, à la cohérence des budgets, à l'absence
de changement runtime et aux gardes statiques. Aucun futur cas du JSON n'est
annoncé vert. Le résultat est DOCUMENTATION_VERIFIED, pas TARGETED_PASS runtime
ni Quality globale. Checkpoint puis arrêt ; reprendre du nouveau commit de
documentation, dont le runtime est exactement celui du parent qualifié.

Après ce lecteur : commandes et projection conservatrice, unités sans processus,
collecteur provisionné, autres familles de lanceurs et neuf groupes de producteurs,
maintenance/backup exhaustifs 5C2, upgrade réel 5C3, reprise/rollback 5C4,
orchestration et wizard 5D, recette finale et promotions selon autorisations.
