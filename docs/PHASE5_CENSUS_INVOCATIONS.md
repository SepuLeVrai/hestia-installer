# Candidats du recensement et invocations systemd

## Base, entrée et portée

Base `4a14c8d98b077401ef9dfc82f1753240972aecd2`, arbre
`8b543bd091d132c1d5e3dd4f3f91e78bf2e7e900`,237 fichiers. Le run36252160235
qualifie cette base (253 contrôles,16 cas système), pas ce nouvel arbre.

`systemd_census_invocations.SystemdCensusInvocations(target,storage).collect()`
réutilise la cible validée et son UID/GID dédié. Aucun PID, FD, nom d'unité,
invocation, ancien CensusSample ou callback n'est accepté en entrée. Ce lecteur
privé lie les leaders candidats à la réponse du manager système, pour revue.
Aucun endpoint, wizard, enrôlement, opération de drainage ou modèle pur n'est
modifié. Le lecteur de relations existant reste distinct ; ce lot ne prétend
pas avoir achevé la classification des unités.

## Continuité des observations

Le budget procfs et son transport sont créés une seule fois. Un premier tour
D-Bus complet et validé précède l'énumération procfs. Le profil exige toujours
Debian13/systemd257, bus local actif, client officiel protégé et observateur
privilégié dans les namespaces pris en charge. Le recensement ouvre et conserve
un PIDFD_THREAD par tâche, leader compris. Il effectue son premier passage et
compare sa topologie avant les lectures des invocations.

Les candidats proviennent exclusivement de ces enregistrements : quatre UID/GID,
groupes supplémentaires, inconnus et descendants observés. Un leader lisible
et vivant de PID supérieur à1 est lié par son FD déjà détenu. Il n'est jamais
fermé puis rouvert à partir de son numéro. GetUnitByPIDFD retourne objet, nom
primaire et InvocationID non nul ; l'objet et le nom doivent appartenir à la
première liste complète. GetUnitByInvocationID puis Id et InvocationID utilisent
exclusivement le chemin canonique de cette invocation. Les contrôles communs
du lecteur parent sont réutilisés, sans chemin nommé ou nouvelle méthode D-Bus.

Deux passages de ces quatre appels par leader doivent être identiques. Ils sont
suivis du second tour complet D-Bus, avant le deuxième passage procfs. Toutes
les tâches, y compris non candidates, doivent conserver identité, date, parent,
credentials, namespaces et cgroup ; topologie, provenance, fdinfo et vivacité
sont recontrôlés. Les contextes procfs et D-Bus doivent partager hôte, boot et
namespaces PID/mount. Le manifeste combiné est construit pendant que les FD
restent ouverts ; le contrôle final de vivacité précède leur fermeture.

Ce sont des observations successives, pas un instantané atomique. Un changement
puis retour ABA et des tâches transitoires entre lectures restent possibles.
Le FD protège contre la substitution du processus, pas contre ses changements
de configuration ou de credentials. Les clients busctl temporaires terminent
avant les énumérations ; aucune exclusion permanente de leur PID n'est ajoutée.
Le reçu est historique après fermeture des FD et ne peut autoriser une mutation
ultérieure. L'inventaire du namespace observateur ne couvre pas l'hôte extérieur.

## Plusieurs processus, threads et inconnus

Plusieurs leaders peuvent désigner un même objet ; tous leurs bindings sont
conservés. Une unité n'apparaît qu'une fois dans la liste de revue, avec ses
leaders et l'union de leurs raisons. Le nom, l'InvocationID et son chemin doivent
être identiques pour ce même objet. Un InvocationID attribué à deux objets
refuse également l'observation. Chaque leader consomme son propre quota, même
si tous appartiennent à une seule unité.

Un signal fsuid dans un thread peut proposer son leader root à la revue. Les
credentials du thread ne deviennent jamais ceux du leader. Seul le leader est
lié : le cgroup de chaque tâche reste conservé, mais aucune appartenance d'un
autre thread à l'unité n'en est déduite. Les descendants d'autres identités sont
liés individuellement s'ils sont des leaders connus. Une filiation ancienne
après reparentage n'est pas reconstruite.

Un leader inconnu reste explicitement non lié, avec ses raisons et le code
LEADER_IDENTITY_UNAVAILABLE. PID1 reste INIT_LEADER_UNSUPPORTED. Un thread
inconnu ne supprime pas son leader lisible : les deux états sont conservés.
Les non-correspondances restent intégralement présentes dans le census et ne
deviennent pas des exclusions. Une erreur D-Bus, absence d'unité, ID nul ou
réponse incohérente refuse toute la collecte ; elle ne fabrique pas un succès
partiel. Aucun retry, rechargement, référence persistante ou fallback numérique.

La source systemd257 manager_get_unit_by_pidref préfère l'unité du cgroup puis
peut utiliser ses tables de processus surveillés ; elle ne promet pas un
propriétaire unique. Le binding signifie donc « association retournée par le
manager », pas appartenance exhaustive, exclusivité de propriété, ni preuve
que cette unité écrit les données HESTIA. Les FD conservés et revalidés côté
observateur restent requis même si systemd rencontre sa propre pression de FD.

## Bornes, confidentialité et indicateurs

Les bornes du recensement restent512 leaders,1024 tâches/FD,32768 entrées de
répertoires,32Mio et32768 lectures/readlink procfs. Au plus128 leaders liés ;
24+8N appels,2Mio par réponse D-Bus,8Mio cumulés. Le quota est fixé après
sélection, sans réinitialiser compteurs, délai ou origine monotone. Délai partagé
de60s,5s par client, enveloppe combinée4Mio. Un hôte trop grand ou trop mouvant
est refusé. Ces contrôles ne constituent pas un watchdog d'appel noyau bloqué.

Le rapport public ne contient que comptes, empreinte et indicateurs. Les tâches,
bindings, unités, chemins et identités sont privés ; repr les masque. Les listes
retournées par private_manifest sont des copies. La décision est REVIEW_REQUIRED
et enrolled=false, known_provisioned_units=0. Les indicateurs étroits de listes,
recensement visible et bindings de leaders observés peuvent être vrais.
process_census_authenticated, all_descendants_identified,
all_task_unit_memberships_observed, live_receipt, automatic_exclusion_allowed,
drain_allowed, host_scheduler_inventory_complete, storage_inventory_complete
et phase5_complete restent faux. Les autres indicateurs globaux de phase5
restent inchangés et faux.

## Qualification et sources

22 tests ajoutés à la baseline sans retrait :275 contrôles sélectionnés,
907 core détectables et902 requis. Les tests de cycle de vie vérifient les
descripteurs réutilisés, regroupements, inconnus, absence d'entrée historique,
changements des populations/identités/provenances, limites, confidentialité et
refus. Les anciens tests du census et de la liaison restent obligatoires.

Un job Debian13 par arbre gelé :275 contrôles,8 scénarios PIDFD
parents,8 census parents et8 nouveaux scénarios réels. Ces derniers couvrent
le fsuid non leader avec trois leaders dans une invocation, zombie non lié et
non-correspondance conservée, descripteurs possédés/fermés, changement fsuid
pendant D-Bus, changement cgroup dans la même unité, naissance puis disparition
de thread, redémarrage sans adoption du remplaçant. Les mutations appartiennent
exclusivement à la fixture jetable, réseau coupé. Aucun mock remplace ces preuves.
La recette rejoint la future matrice système Debian13. Work ne fournit pas ce
profil procfs/multi-UID ; il exécute les contrôleurs et les gardes statiques.

Sources primaires déjà épinglées et conservées au checkpoint :
[contrat invocation](PHASE5_SYSTEMD_INVOCATION_CONTRACT.json), notamment
dbus-manager-v257.c, pidref-v257.c, cgroup-v257.c et busctl-v257.c ;
[sources census](PHASE5_PROCESS_CENSUS_SOURCES.json), notamment Linux6.12
PIDFD_THREAD, fdinfo/poll et parcours TGID/task. L'analyse de ces sources ne
remplace pas la recette sur le noyau exécuté. Les preuves négatives c2acc806
restent applicables ; aucun retour au lecteur de propriétés nommées.

Résultats exacts après gel dans le checkpoint, sans PASS anticipé. Qualités
globales, Debian12, Web/SQL et promotions différés ; intermittence DOM toujours
ouverte. Prochain lot : raccorder cette preuve fraîche aux relations et à la
sélection conservatrice, sans convertir les signaux fsuid/threads en identités
effectives de leader. Puis identités configurées, chaînes d'exécution, unités
sans processus et autres familles de lanceurs. Les neuf producteurs, maintenance/
sauvegarde exhaustives, upgrade réel, reprise/rollback et wizard restent ouverts.
Checkpoint puis arrêt ; aucune opération ne continue après la livraison.

## Premier job et diagnostic borné

Le candidat120602353818bda011fe9ad92dca5f6733097445, arbre
f066f143cd67531d3fc4667861b477e1f0a069b2, a échoué au run36253303139 :
275 contrôleurs verts,7 cas PIDFD verts et1 échec. Le cas historique de sélection
absente a reçu INVOCATION_TRANSPORT_UNAVAILABLE au lieu de NOT_LISTED. Le produit
a refusé ; la cause interne n'est pas disponible dans ce premier journal.
Les recettes census et nouvelle liaison n'ont pas été exécutées. Cette première
qualification n'est donc pas acquise et les preuves sont conservées.

La recette parent imprime désormais au plus8 types/codes fermés de la chaîne
d'exceptions, y compris les contextes masqués par from-None. Aucun texte libre,
changement runtime, délai supplémentaire ou assertion affaiblie. Les recettes
indépendantes suivantes sont exécutées même après échec d'une recette parent,
qui reste bloquant pour le job. Les nouveaux refus après mutation exigent aussi
que cette mutation réelle ait été atteinte. Une seconde exécution ciblée est
prévue sur ce nouvel arbre gelé ; aucune relance identique et aucune campagne
globale. Un éventuel succès ne suffira pas à déclarer l'incident initial corrigé.
