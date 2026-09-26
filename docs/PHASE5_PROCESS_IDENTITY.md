# Identités de leaders sélectionnés, liées aux invocations

## Base et portée du lot

Base `e5ffaf35f787d5e4eca3312e96a7952eb03d8f47`, arbre
`bcb93abb8a603303f38d9b7207db723f4a15fa9c`, 224 fichiers. Son run
36247347642 qualifie 200 contrôles et 16 scénarios Debian13, pas le nouvel arbre.
Le prototype nommé c2acc806 reste rejeté ; aucun chemin d'unité nommé n'est relu.

`SystemdProcessIdentity` étend le lecteur de relations, avec la même entrée privée
collect(hints), les PIDFD créés et fermés par le collecteur, les deux passages et
les 24+26N appels D-Bus. Les PID proposés ne sont toujours pas un recensement.
Le nouveau fait porte exclusivement sur le leader vivant effectivement observé
après ouverture de son PIDFD. Il ne valide aucune identité historique avant cette
ouverture, tous les threads, descendants, membres du cgroup ou autres unités.

L'UID/GID effectif et les groupes supplémentaires alimentent le modèle de
pertinence existant. Un signal positif élargit la revue RELATED_UNMANAGED et sa
propagation par les relations observées. Ni KNOWN_PROVISIONED, ni exclusion,
writer prouvé, inventaire exhaustif, adoption ou droit de drainage. Les identités
configurées et les chemins d'exécution restent inconnus. Le modèle pur conserve
ses marqueurs déclaratifs conservateurs ; seuls le lecteur et sa preuve privée
rendent ici compte de l'origine observée du fait.

## Procfs et identité observée

Le profil exige l'observateur root dans les mêmes namespaces PID, mount, user,
cgroup et time que PID1. Le procfs racine doit être monté en entier sur /proc,
sans hidepid restrictif ni subset. Tout sous-montage sur les répertoires du PID
sélectionné, de l'observateur, de PID1, self ou thread-self est refusé. Les autres
sous-montages habituels de /proc ne sont pas présumés masquer ces données.
La table complète est bornée et son empreinte comparée ; une modification sans
rapport peut donc entraîner un refus conservateur. Root ou un administrateur
capable de remplacer le procfs n'est pas un adversaire contenu par cette preuve.

Les chemins ouverts sont fixes ou construits avec des entiers validés. Répertoires
avec O_DIRECTORY/O_NOFOLLOW/O_CLOEXEC ; fichiers réguliers, O_NOFOLLOW et lecture
jusqu'à EOF avec plafond. Les symlinks de namespaces sont lus comme identifiants,
sans résolution de chemin métier. Le fdinfo de notre propre PIDFD doit donner
exactement le PID proposé, dans le même namespace, avant lecture du processus.
La vivacité du PIDFD est contrôlée avant et après l'observation. Le lecteur
procfs ne ferme pas ce PIDFD ; le collecteur propriétaire le ferme dans finally.

Deux snapshots égaux sont exigés à chaque observation :

- Pid, Tgid, NSpid et NStgid désignent le seul leader sélectionné ; les identités
  PID/user de namespace doivent être celles de l'observateur.
- stat fournit start_ticks positif, en ticks depuis le démarrage, dans le contexte
  de temps vérifié de l'observateur. Le comm est ignoré et non conservé ; sa
  parenthèse fermante est analysée depuis la fin. Zombies et morts sont refusés.
  Le start_ticks est relu après chaque snapshot ; R/S ne constitue pas une dérive.
- status fournit les quatre UID et GID, dans l'ordre réel/effectif/sauvé/fichier,
  les groupes supplémentaires uniques triés et un nombre de threads borné.
  Ce nombre ne prouve pas l'identité des threads. Les UID/GID autres qu'effectifs
  sont conservés dans la preuve sans être présentés comme effectifs au sélecteur.
- Les cinq namespaces du processus sont conservés. Un namespace mount privé est
  accepté, sans résolution de chemin à travers ce namespace. Les namespaces
  cgroup/time du processus peuvent différer ; cgroup et start_ticks restent lus
  dans le contexte de l'observateur. Aucun nom d'unité n'est déduit de ce chemin.
- cgroup exige une seule ligne v2 0::/chemin, canonique et bornée. Aucun parcours,
  migration, signal ni modification du processus observé n'est effectué.

La sémantique des credentials et de start_ticks a été relue dans Linux v6.12,
notamment task_state/get_task_cred et do_task_stat/timens_add_boottime_ns.
Le [manifeste primaire](PHASE5_PROCESS_IDENTITY_SOURCES.json) donne les empreintes
et ancres ; les fichiers intégraux accompagnent le checkpoint. Cette lecture de
source ne certifie pas tous les noyaux. La recette enregistre le noyau réellement
utilisé, Debian13 et systemd/busctl257. Debian12 reste refusé par ce transport.

## Contrôles autour de l'invocation

Pour chaque passage : observation procfs, liaison PIDFD/Id/InvocationID,
observation procfs identique, neuf propriétés puis nouvelle observation identique.
Les deux passages complets sont comparés. Après le dernier tour de listes, les
identités sont relues ; elles le sont encore après construction de l'enveloppe.
Huit observations par leader sont donc nécessaires en cas de succès.
Le hook privé _check_final du lecteur minimal conserve ses contrôles de vivacité
historiques lorsqu'il n'est pas spécialisé ; ses appels et limites sont inchangés.

Changement d'UID/GID/groupes, date, nombre de threads, namespace, cgroup ou contexte,
permission refusée, disparition, mauvais PIDFD, entrée invalide ou dépassement :
collecte annulée, code d'erreur fixe, aucun reçu partiel ni retry. Aucun appel
cmdline/environ/exe, lecture de secret, énumération automatique de /proc ou
collecte de noms de comptes. Les preuves privées lient les credentials au PIDFD,
à l'invocation et à l'index enrichi par empreinte ; rapports/repr ne les révèlent pas.

Ces lectures ne sont pas atomiques. Un changement suivi du retour à l'état initial
entre lectures (ABA) peut échapper à la comparaison. Une observation peut devenir
caduque dès le retour. Le mapping systemd peut retenir un propriétaire parmi
plusieurs et conserve les limites PIDref du contrat parent. Aucun instantané
historique cohérent de tous les processus ou autorité persistante n'est annoncé.

## Bornes et rapports

128 leaders/PIDFD maximum ; 64 groupes par leader ; 32768 threads déclarés,
sans parcours de ces threads. Entiers ASCII, UID/GID au plus 2^32-1, PID au plus
2^31-1, start_ticks positif au plus 2^63-1. Cgroup 2048 octets, identifiants de
namespace 64 caractères. Fichier procfs 64Kio, mountinfo 1Mio/8192 lignes ;
32Mio de contenu de fichiers procfs et 32768 lectures/readlink par collecte.
Ces plafonds peuvent refuser un hôte valide trop volumineux.

Le budget procfs partage les 60s du transport, vérifiées autour des lectures et
pendant les boucles. Il ne constitue pas un watchdog contre un appel noyau bloqué.
Les bornes D-Bus 2Mio/réponse, 8Mio/collecte, 5s/appel, 3352 appels maximum et
4Mio/enveloppe demeurent inchangées. Aucune relance payante automatique.
Les compteurs procfs du manifeste sont capturés avant le contrôle final, soit
sept observations par leader ; counters_before_final_check=true l'indique.
Le huitième contrôle reste soumis aux mêmes budgets avant tout retour.

selected_effective_leaders_observed=true décrit la portée étroite du résultat.
effective_identities_observed=false et process_census_authenticated=false
conservent la frontière globale. Les neuf groupes de producteurs, les six
familles de lanceurs et tous les indicateurs de fin de phase restent ouverts.

## Qualification après gel et reprise

26 nouveaux tests locaux ; sélection de 226 contrôles avec les lecteurs parents
et les modèles affectés. Les anciens IDs restent requis. Un seul job Debian13
prévu : 226 contrôles puis 8 cas PIDFD, 8 cas relations historiques et 8 nouveaux
cas réels : identité dédiée ; groupe supplémentaire sans UID correspondant ;
PrivateTmp avec namespace mount distinct ; mauvais PIDFD possédé ; changement
d'UID effectif avec PID inchangé ; déplacement vers un sous-cgroup avec même PID ;
fin du processus pendant liaison ; observateur en namespace mount distinct.
Les mutations sont exclusivement celles du banc jetable, réseau coupé.
Zéro skip, échec ou erreur, sources/modes stables et artefact exact sont requis.
Les résultats après gel figurent dans le checkpoint, sans PASS anticipé ici.
La recette est ajoutée à la future campagne système Debian13, pas Debian12.

Checkpoint puis arrêt. Prochain lot : recensement borné des candidats et couverture
des threads/descendants, à concevoir explicitement avant toute prétention de
complétude. Les processus sans PID d'unité, configurations/exécution, correction
du collecteur fermé, autres lanceurs et pont au profil provisionné restent ouverts.
Les blocs maintenance exhaustive, sauvegarde/restauration 5C2, vraie transition
5C3, reprise/rollback 5C4, orchestration/wizard 5D et Quality finale restent requis.
Aucune PR/promotion sur la seule qualification ciblée. Web inchangé.
