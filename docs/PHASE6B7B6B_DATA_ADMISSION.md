# Phase 6B7b6b — admission SQL courante pendant la réouverture des données

## Base et limites de qualification

Candidat isolé sur `e1a65ce78c38db3133dbc074b875c63043d18c52`, arbre
`b284179a6d727b9e7f4da4111d1133e8c7221510`. Les trois campagnes Installer de
ce parent sont vertes ; sa recette native corrective `36848220815` reste en
cours au gel documentaire. La dernière base entièrement qualifiée reste 6B7b5a.
La primitive données 6B7b6a a ses trois verdicts Installer, sans composition SQL.
Ce nouveau candidat peut être vérifié séparément ; sa recette native ne doit
être lancée qu'après qualification du parent et contrôle de ses preuves exactes.

`mobile_data_admission` compose le plan données existant avec un nouvel export
SQL sous le verrou natif de lecture, les archives et les observations actuelles
des fichiers, du runtime et du Gateway. Le profil reste MAIN local, upgrade,
assistant préservé, identités SQL distinctes et absence de migration retenue.
Cette fenêtre privée ne démarre aucun service et ne consomme aucun bloqueur.
La maintenance, le bloqueur mobile et le reçu Gateway restent identiques.

## Admission et reprise

Apply exige 0700 avec le marqueur original et aucune intention propre. Resume
exige l'intention exacte du plan données ; check exige aussi son reçu terminé.
La confirmation porte sur l'empreinte de ce plan. Charger un plan reste sans
effet : un état 0750 avec marqueur demeure refusé par les lecteurs historiques.

Lors d'une reprise explicite de cet état partiel, la récupération native remet
d'abord 0700 sous les vrais verrous exclusifs de configuration. Un processus
étranger interdit la suite et laisse cette fermeture en place, sans signal.
Les runtimes natifs peuvent alors être rattachés et contrôlés. Un nouvel export
SQL est obligatoire avant la réouverture : la refermeture préparatoire n'est
jamais une autorisation d'ouvrir. Un état déjà terminé n'est pas refermé.

Les verrous exclusifs de configuration restent tenus jusqu'à la libération du
verrou SQL. La méthode privée `_execute_locked` reprend le corps d'effet du plan
données existant, sans changer sa logique, et exige le vrai garde lié au même
bail, à la même sauvegarde et à l'empreinte exacte du parent. L'API historique
continue d'acquérir ce garde elle-même. Aucun faux bail partagé n'est fabriqué.

Chaque apply/resume/check crée une observation et un export distincts. Avant
l'effet, après l'effet, à chaque lecture du rapport et à la sortie normale,
l'admission revérifie les limites SQL, les ordonnanceurs, les verrous, le runtime,
les parents, les fichiers courants et les archives. La limite SQL native de
180 secondes reste inchangée. Une observation persistée est historique et ne
vaut plus admission après la fermeture de sa fenêtre ou un changement de processus.

## Observation après la transition

L'ancien `DataAccessFence` reste un lecteur de fermeture à 0700. Le nouveau
recensement en lecture seule utilise les lecteurs d'entrée natifs, avec leurs
contrôles Ext4, ACL, propriétaires, liens, montages et flags. Ses limites de
nombre, profondeur, chemin et durée sont bornées. Seul le mode de la racine
exactement liée au plan peut passer de 0700 à 0750 ; l'inventaire des descendants
et leurs identités doivent rester exacts. Aucun ancien lecteur n'est affaibli.

L'enveloppe d'archives retire uniquement les deux marqueurs natifs dont la
disparition est prouvée : réservations externes et accès données. Le marqueur
données original est contrôlé avec son propriétaire root, son groupe applicatif
et son mode **0640** exacts. Aucun journal de maintenance inconnu n'est ignoré.
Le contenu courant des données est vérifié par les archives natives, en plus
du recensement d'inodes. Les autres journaux et fichiers de configuration restent
comparés intégralement sous verrou.

Un échec après l'effet peut laisser l'accès données à 0750. Le coordinateur ne
prétend pas restaurer la fermeture : il refuse l'admission, conserve les preuves
et laisse maintenance/bloqueurs en place. Les reprises doivent refaire les
vérifications SQL et fichiers actuelles ; aucun reçu ne les remplace.

## Contrats et recette suivante

Seize contrats purs couvrent les états, l'enveloppe exacte, les observations
historiques et la révocation. Dix tests de fichiers ciblent l'inventaire après
ouverture, les vrais verrous, liens/montages, dérives et la refermeture après
coupure. Ils réutilisent les fixtures existantes sans dupliquer ni modifier les
anciens tests. Ces 26 identifiants sont ajoutés à la baseline obligatoire.
Les tests de fichiers, SQL, comptes et systemd s'exécutent en CI jetable seulement.

La continuation native prépare une dérive SQL refusée avant intention, un vrai
SIGKILL après chmod, une reprise avec export frais et trois fenêtres distinctes
pour reprise, journal inconnu et dérive des données. Les sorties normales restent
vérifiées ; neuf mesures doivent rester sous 180 secondes. Les anciennes assertions
du parcours Gateway et la recette corrective du parent restent inchangées.
Le verdict natif et son budget global restent à obtenir ; le code du scénario
n'est pas une preuve de qualification. Les verdicts finaux et leurs identités
seront conservés dans les issues de suivi et la livraison, après ce gel.

La suppression des bloqueurs, les starts dédupliqués, boot/restauration originale,
DEV/FCM, l'assistant et la recette 6C restent à traiter séparément. Phase 6 ouverte.
