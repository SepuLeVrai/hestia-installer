# Names et relations des unités déjà chargées

Base `7a331ce38f032e2a6a96a8896062f5bf4541862b`, arbre
`295e19e58298428b1207438384a5219f4e58f8be`, 210 fichiers. Le transport des
listes est qualifié par le run `36235476328` (155 contrôles, dix cas système).
Ce lot distinct ajoute uniquement les détails privés ci-dessous. Le Web reste
`2a27c7a1f9fe0a00289eb53278f75d5f230900b7`, sans nouvelle qualification Web.

## Contrat fermé

`SystemdUnitRelations(target, storage).collect(selected)` reçoit un tuple
explicite de 1 à 128 chemins d'objets D-Bus. Ils doivent tous appartenir à la
première ListUnits validée. Aucun nom libre, GetUnit, LoadUnit, GetAll, lecture
récursive ou choix implicite par préfixe. Un objet absent provoque un refus,
sans appel de propriété. La sélection reste partielle même si elle contient
par hasard tous les objets de cette observation.

Les appels utilisent exclusivement Properties.Get via busctl call sur le
propriétaire unique vérifié du manager. Bus, options sans activation/interactivité,
client protégé, broker déjà actif et provenance sont ceux du transport précédent.
La liste fermée est :

| Interface | Propriétés | Types |
| --- | --- | --- |
| Unit | Id, Names | s, as |
| Unit | Triggers, TriggeredBy, Requires, Wants, BindsTo, Upholds, OnSuccess, OnFailure | as |
| Timer, Path | Unit | s |

Le Socket ne possède pas de propriété Unit dans l'API consultée : ses relations
sont lues par Triggers/TriggeredBy. Aucune instance de template n'est inventée.
Les API officielles v252 et v257 décrivent ces propriétés ; seule Debian13/systemd257
fait l'objet de la recette réelle de ce lot. Références primaires :
https://github.com/systemd/systemd/blob/v257/man/org.freedesktop.systemd1.xml
https://github.com/systemd/systemd/blob/v252/man/org.freedesktop.systemd1.xml
Les empreintes et ancres consultées sont conservées dans le checkpoint.

Ordre : premier tour complet des listes/provenance, deux lectures complètes des
détails sélectionnés, second tour complet des listes/provenance. Le budget est
commun. Listes, provenance et détails doivent être identiques après normalisation.
Id doit égaler le nom primaire listé ; Names doit le contenir, sans doublon,
conflit de propriétaire, suffixe étranger ou template non instancié. Les tableaux
sont normalisés par ordre ; une réponse invalide/inaccessible n'est jamais vide.
Toute erreur annule la collecte entière sans relance ni résultat partiel de succès.

Les bornes partagées restent 2 Mio/réponse, 8 Mio cumulés pendant lecture,
5 secondes/appel et 60 secondes pour la collecte (nettoyage enfant <=1 seconde).
La réservation vaut exactement 24 + deux fois le nombre de propriétés sélectionnées,
soit au maximum 2840 appels pour 128 timers/paths. Elle n'augmente pas les 24 appels
du collecteur de listes seul. Une grande sélection peut épuiser la durée avant
son plafond d'appels : refus fermé, sans subdivision automatique ni retry.
4096 noms au maximum dans l'index, 8192 relations au total, enveloppe privée
complète de 4 Mio incluant index, propriétés et faits. Le transport ne peut pas
interrompre un noyau bloqué pendant un accès de métadonnées local.

## Résultat et limites

Le résultat contient scan/index enrichis uniquement pour les Names sélectionnés,
propriétés privées conservées y compris tableaux vides, et RelevanceFacts liés au
digest de cet index. Chaque relation porte le digest de sa propriété normalisée.
Résolution par égalité de nom primaire ou alias effectivement observé dans ce même
index. Un nom absent, un alias non observé ou un template reste target_object=None ;
aucune visite supplémentaire. Les faits d'identité, chemins et liaisons externes
restent None. Les unités non sélectionnées gardent Names=None et contexte inconnu.

Les modèles purs sont inchangés et revalident scan/faits ; la collecte n'ajoute
aucun signal positif de producteur. Un cycle de relations seul reste UNRESOLVED.
Le rapport public ne contient ni noms ni chemins privés : compteurs et flags
system_manager_lists_observed/selected_unit_relations_observed. Les anciens
blocages conservateurs du modèle sont préservés avec sélection partielle,
relations incomplètes, contexte absent et pont vers provisionnement non livré.
Les digests/reçus sont privés, sans sceau d'autorité ni lease contre un changement
intermédiaire, la disparition/recréation du même objet ou un administrateur root.

Ni définition/ExecStart, identité configurée/effective, NSS, wrapper, destination,
ordonnancement Before/After ou Following n'est interprété comme activation.
Aucune admission KNOWN_PROVISIONED, exclusion automatique, projection, adoption,
activation, drainage, maintenance ou attestation du Web/stockage déclaré.
Tous les indicateurs de clôture phase5 restent faux, dont inventaire exhaustif,
sauvegarde exhaustive, installation, activation et raccordement système.

## Qualification et arrêt

25 nouveaux tests core ; 812 détectables, 807 requis (écart historique cinq).
Sélection locale 180 : relations25, transport21, relevance42, discovery35,
launcher_inventory23, storage_inventory18, systemd_observations16.
Code et documentation gelés ensemble, gardes statiques sur 215 fichiers.

Un seul nouveau job Debian13 est prévu : ces 180 contrôles et huit scénarios
réels dans `tests/integration/systemd_unit_relations_systemd.py` : appels/provenance
et PID conservé ; Timer/Path/Socket et dépendances réelles sans activation de la
cible ; alias lié à un seul objet ; cycle sans preuve de producteur ; inconnus et
fichiers/templates non chargés ; objet absent sans lecture ; alias changé entre
lectures ; objet transitoire supprimé par le banc et lecture refusée sans recharge.
Image minimale du lot précédent, réseau none, cgroup privé sans montage hôte,
fixtures uniquement, manifeste exact avant/après et suppression du conteneur.

Les retours D-Bus ne sont pas simulés dans cette recette. La fixture déclare
sa cible et ses stockages ; ce n'est pas une preuve métier/SQL/Web. Le job ciblé
évite de rejouer les dix scénarios du lot précédent ; sa sélection core couvre
le budget partagé modifié. La nouvelle recette rejoint aussi les futures Quality
système complètes, sans les lancer ici. Résultats exacts dans le checkpoint après
qualification. Quality globales, Debian12, SQL/Web et promotions restent différées.

Checkpoint complet puis arrêt. Prochain lot distinct : borner le contexte
d'exécution utile (identités configurées et chemins), en séparant configuration,
résolution et identités effectives ; ne pas confondre ces métadonnées avec une
preuve exhaustive de producteurs. Pas d'élargissement automatique du drain.
