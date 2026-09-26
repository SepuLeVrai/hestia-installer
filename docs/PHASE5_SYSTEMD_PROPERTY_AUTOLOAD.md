# Blocage du lecteur de propriétés : chargement implicite

## Décision de reprise

Le lecteur expérimental Id/Names/relations n'est pas livré. Le candidat de
reprise repart de `7a331ce38f032e2a6a96a8896062f5bf4541862b`, arbre
`295e19e58298428b1207438384a5219f4e58f8be`. Seuls ces documents changent ;
code, tests, baseline et workflows restent exactement ceux de cette base.
Le Web `2a27c7a1` reste inchangé. Aucun second job, PR ou promotion.

Le prototype rejeté est `c2acc8068d68ade12e7ff0fea9d810b8217b008f`, arbre
`5bc91622f2468d83e733861d77248ab69669a5df`, 215 fichiers, branche
`work/phase5-systemd-unit-relations-20260926`. Il est conservé pour diagnostic,
jamais comme candidat livrable. Ses fichiers complets et preuves sont dans la
quarantaine du checkpoint. Ne pas le fusionner ni reprendre son lecteur en
le présentant comme sûr. La branche de reprise est
`work/phase5-systemd-unit-relations-blocker-20260926`.

## Preuve réelle, non simulée

Run [36238806917](https://github.com/SepuLeVrai/hestia-installer/actions/runs/36238806917),
job `108395379652`, première tentative : **échec**. Un seul run et un seul job
ont été consommés pour ce lot. Les 180 contrôles locaux puis les 180 dans
Debian13 passent ; sept scénarios système passent et le huitième échoue
(une assertion, zéro erreur/skip). Cet ensemble n'est pas une qualification.

Le banc lance son unité transitoire, la sélectionne dans ListUnits puis l'arrête
après le premier tour réel du lecteur. Une liste systemctl --all confirme son
absence. Le lecteur appelle ensuite Properties.Get sur son ancien chemin nommé.
Au lieu d'échouer dès Id, vingt appels de propriétés réussissent (deux séries de
dix). Le banc constate aussi une exception globale, sans en isoler la cause :
son callback d'arrêt intervient à chaque tour. Cette exception ne prouve donc
ni le mécanisme de refus final ni l'absence d'effet pendant les lectures.
L'assertion exigeant l'arrêt après le premier appel a échoué et n'a pas été
affaiblie. Le diagnostic repose sur les vingt lectures et la source officielle.

Le banc utilise Debian13, systemd257, dbus-daemon et un conteneur jetable sans
réseau, sans montage cgroup hôte. Le conteneur est supprimé. Aucun hôte métier,
Web ou SQL réel n'est concerné. Artefact `10904518037`, 18922 octets, SHA-256
`b83d946b85baf2fd7ca8ac0400105525a863b1adee4cb43f7cb96ad3d05cf2da`.
Archive, commit/arbre, manifeste des 215 fichiers et modes ont été vérifiés.

## Cause confirmée dans la source officielle

Les [références et ancres](PHASE5_SYSTEMD_PROPERTY_AUTOLOAD.json) distinguent
l'observation du banc et l'analyse de source. Dans systemd v257 :

- Le traitement des objets Unit utilise une vtable de secours avec bus_unit_find.
- bus_unit_find passe par find_unit, qui appelle manager_load_unit_from_dbus_path
  pour un chemin nommé (hors le chemin spécial self).
- manager_load_unit_from_dbus_path décode ce nom puis appelle manager_load_unit.

Ainsi, connaître le chemin lors d'une précédente ListUnits ne suffit pas à
empêcher un chargement si l'unité disparaît ensuite. La même route find_unit
est présente dans dbus.c v252 ; cela ne constitue pas une nouvelle recette
Debian12. --auto-start=no concerne l'activation D-Bus du destinataire, pas ce
chargement interne au manager systemd. Lire Properties.Get sans LoadUnit
explicite ne suffit donc pas à satisfaire le contrat « sans chargement ».
Il ne faut pas confondre ce chargement avec le démarrage du service : la preuve
ne dit pas que le service métier a été démarré par une lecture.

Une nouvelle ListUnits ou GetUnit immédiatement avant chaque propriété laisse
une fenêtre de course. Comparer deux lectures, contrôler Id, appliquer des
budgets ou refuser le résultat final ne supprime pas l'effet déjà possible.
Ref/RefUnit serait une mutation du suivi de références et n'est pas ajouté
comme contournement au contrat de lecture sans garde acquise.

La source v257 possède aussi une branche par identifiant d'invocation, avec
recherche dans une table et erreur si l'identifiant est absent. C'est une piste
à étudier, pas une solution livrée : l'acquisition initiale sûre des identifiants,
les unités inactives, la couverture et les versions supportées restent ouvertes.
Aucune lecture préalable par chemin nommé n'est rendue acceptable par cette piste.

## Correction du contrat historique

La proposition historique Properties.Get sur les objets nommés n'est plus une
méthode admise pour livrer le futur lecteur de détails sans chargement.
PHASE5_SYSTEMD_SCOPE.md/JSON portent ce blocage ; leurs 28 critères restent
historiques, tous executed=false comme campagne globale. Les modèles purs restent
utiles pour des faits déclarés, sans transformer ceux-ci en observations hôte.

Le transport courant des trois listes n'utilise aucune propriété d'objet Unit :
seules Version/UnitPath du manager sont lues. Son code reste identique au run
36235476328. Le collecteur fermé des unités provisionnées reste également
inchangé ; ce lot ne lui apporte aucune preuve nouvelle contre le rechargement
d'une unité disparue. Ce point devra être pris en compte lors de sa revue de
contrat, sans étendre son périmètre au nom de ses recettes précédentes.

## Qualification de ce checkpoint et prochain arrêt

Arbre documentaire de 212 fichiers : contrôle d'identité de tous les fichiers
hors docs par rapport à 7a331ce3, ancres/empreintes, preuves négatives, catalogue
Web, core 787 détectables/782 requis inchangés et gardes statiques. Les résultats
exacts sont conservés dans le checkpoint ; aucun nouveau résultat système vert
n'est revendiqué. Aucune Quality globale n'est lancée ni promotion autorisée.

Prochain chantier unique : réviser le contrat d'acquisition des détails avec une
méthode prouvée sans chargement, ou garder explicitement ces détails inconnus.
Étudier les identifiants d'invocation et leurs limites sans implémentation massive
ni nouvelle Actions avant une hypothèse étayée par la source. Ne pas passer aux
identités/ExecStart sur le même chemin nommé. Conserver le test négatif, le plafond
et le refus de résultats incomplets. Checkpoint puis arrêt maintenant.

Inventaire des neuf groupes, sauvegarde exhaustive 5C2, vrai upgrade 5C3,
reprise/rollback 5C4, orchestration/wizard 5D et recette globale restent ouverts.
Tous les indicateurs de clôture restent faux. Aucun déploiement ou service
supplémentaire n'est livré par ce diagnostic.
