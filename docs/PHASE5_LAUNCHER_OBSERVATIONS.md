# Modèle privé des déclarations de lanceurs

## Évolution postérieure : premier adaptateur partiel

La [collecte systemd provisionnée](PHASE5_SYSTEMD_OBSERVATIONS.md) fournit
maintenant des observations réelles sur deux ou quatre unités connues et un
pont privé pour le profil métier scellé. Ce modèle pur reste inchangé ; ses
blocages et les inconnus sont conservés. Le texte ci-dessous décrit le lot
historique `c8af4c8d`, pas une absence actuelle de tout adaptateur.

## Base et portée

Base Installer `d7ace453152a3b5d9d8cbe7d2ec21ac4779bbc64`, arbre
`dab236e5e6bd7d51378eff1dab48282b742bc595`, 189 fichiers. Ce lot documentaire
avait vérifié le [catalogue CLI](PHASE5_CLI_SCHEDULERS.md) localement, sans Actions.
La dernière évolution système reste `cb561320`, campagne ciblée `36222491251`
(74 contrôles, 47 scénarios Debian 13). Aucune de ces références ne constitue
une qualification globale du nouveau code.

`installer.launcher_inventory` ajoute un modèle privé et son validateur pur.
Il reçoit des déclarations typées d'un futur adaptateur de confiance. Il ne lit
aucun fichier de l'hôte ou du Web, ne consulte pas l'horloge, n'exécute ni PHP,
ni shell, ni systemctl, et n'ouvre ni réseau ni SQL. Il n'est raccordé à aucun
endpoint, wizard, lanceur, drain ou sauvegarde. Son résultat ne sert jamais
d'autorisation à l'exécution d'une commande.

Seul le profil métier Web `2a27c7a1f9fe0a00289eb53278f75d5f230900b7` et son
arbre `783be5abdcd5e13addefe96d743eee3a97b7a6de` sont acceptés dans cette
première version. Le pin historique est refusé explicitement. Vérifier ces
identifiants déclarés ne vérifie pas les octets réellement déployés.

## Cible et lien avec StorageInventory

`LauncherTarget` contient instance, commit/arbre Web, webroot, slot de
configuration, gate, UID/GID dédiés, identifiant hôte opaque et boot_id.
Le gate doit être exactement `<configuration>/maintenance`, sous `/var/lib`,
et le slot doit être distinct du code, sans chevauchement. Les chemins sont
canoniques syntaxiquement ; propriétaires, liens, ACL et montages ne sont pas
observés par ce modèle. UID/GID cibles sont des entiers positifs bornés.

`LauncherInventory(target, storage)` exige un `StorageRequirements` typé,
du même pin/digest runtime. Il vérifie les rôles `uploads` et
`managed_configuration` contre la cible, refuse les rôles doublés et conserve
les neuf groupes non vérifiés. L'empreinte du manifeste entier est liée au
résultat. Les références de stockage d'un lanceur doivent exister dans ces
besoins ; aucune racine inconnue n'est inventée. Les blocages du stockage
restent visibles sous le code fermé `STORAGE_REQUIREMENTS_UNRESOLVED`.

Les dataclasses typées ne sont pas des attestations signées. Un appelant
privilégié peut construire une déclaration ; seul un futur collecteur qualifié
pourra en établir la provenance. Il n'y a aucune entrée HTTP de dictionnaire
libre ni de certificat de couverture créé à partir de ce modèle seul.

## Six familles obligatoires, états inconnus conservés

Une `LauncherSnapshot` reprend la cible exacte, un instant d'observation en
secondes UTC, six `CoverageObservation` et les `LauncherObservation` déclarées.
Les familles sont `systemd_system`, `systemd_user`, `cron_system`, `cron_users`,
`queued_jobs` et `external_launchers`. Les objets systemd transitoires/générés
ou les déclencheurs service/timer/path/socket pertinents appartiennent à leur
famille de gestion ; la collecte effective reste à implémenter.

Chaque famille est explicitement `unknown`, `unreadable`, `partial` ou
`observed`. Une famille absente, doublée ou inconnue est une entrée invalide.
`observed` exige une empreinte de preuve fournie par l'adaptateur. Elle ne
signifie pas « couverture exhaustive certifiée ». Six familles observed avec
zéro lanceur ne prouvent donc pas l'absence de producteurs sur l'hôte.
Une famille incomplète peut conserver les quelques lanceurs déjà connus.

Chaque lanceur conserve séparément :

- clé privée, famille et fichiers de définition avec empreintes éventuelles ;
- chaîne ordonnée exécutable/interpréteur/scripts/wrappers, fixe/dynamique/inconnue ;
- empreintes des arguments, de l'environnement et des identités SQL ;
- UID/GID/groupes, cwd, rôles de stockage, cgroup et gate déclarés ;
- état inconnu/inactif/actif/en attente et déclencheur inconnu/désarmé/armé.

Les arguments bruts, valeurs d'environnement et identifiants SQL ne sont pas
stockés. Le futur adaptateur doit définir leur sérialisation canonique privée ;
une empreinte absente reste inconnue. Les empreintes fournies ne sont pas
recalculées à partir des secrets par ce modèle. `None` pour rôles ou groupes
diffère d'un tuple explicitement vide. Root, une autre identité ou un autre
gate sont retenus avec des blocages, jamais supprimés du rapport privé pour
faire paraître l'inventaire complet. Aucun lanceur, même apparemment conforme,
n'est enrôlé pour exécution ou arrêt dans ce lot.

## Relecture et confidentialité

`inspect(snapshot, now=..., previous=...)` exige un instant fourni explicitement
par l'adaptateur, entier, non futur et vieux d'au plus 60 secondes relativement
à `now`. Ce contrôle ne garantit pas la véracité de l'horloge fournie.
Lors d'une comparaison, l'instant ne peut pas reculer. Cible, stockage,
couverture, empreintes, chaîne et états doivent rester identiques ; un changement
produit `LAUNCHER_OBSERVATIONS_CHANGED`. Le nouvel instant peut avancer.

Familles, définitions, rôles, groupes et lanceurs sont normalisés ; l'ordre des
éléments de la chaîne d'exécution reste significatif. Une observation active,
en attente, réarmée, dynamique ou incomplète génère des blocages fermés.
Une modification exige une nouvelle évaluation explicite, pas une adoption
silencieuse pendant une comparaison de stabilité.

Le résultat immuable contient un manifeste privé copiable. Son rapport public
ne montre que l'état `LAUNCHER_DECLARATIONS_RECORDED`, les comptages, l'empreinte
du manifeste et des codes de blocage fixes. Ni chemins, noms de tâches, identités
hôte ni secrets n'apparaissent dans repr ou les diagnostics du validateur.
`report()` décrit une déclaration figée ; ce n'est pas une relecture de l'hôte
et cela ne remplace jamais la lease vivante d'HttpDrain.

Limites : 128 lanceurs, huit éléments par chaîne, seize fichiers de définition,
64 groupes/rôles, chemins de 2048 octets, clés de 64 octets et manifeste de
256 Kio. Les types approximatifs (bool pour UID/instant, liste pour tuple),
doublons, chemins non canoniques et valeurs hors contrat sont refusés.

## Vérification du lot

23 nouveaux tests unitaires, avec les 18 tests StorageInventory et cinq du
profil métier : 46 contrôles locaux. Ils exercent identités/pins/stockages,
inconnus et omission de famille, données malformées, limites, confidentialité,
stabilité/rejeu et changements de déclaration. Un contrôle interdit fichiers,
processus, réseau et horloge implicite pendant l'exécution du validateur.
Les besoins de stockage sont issus du vrai résolveur avec son IO de pin simulé
dans les fixtures ; aucun hôte réel ni source déployée n'est ainsi certifié.

Inventaire core détectable : 673, sans retirer les cas historiques. Gardes
statiques et sélection locale requises après gel code/documentation.
Aucun workflow n'est modifié ou lancé pour ce modèle pur. Les campagnes
globales, Debian 12/13, système, SQL/Web et navigateur restent différées.
Les preuves exactes sont conservées dans le checkpoint. Aucune promotion.

Les indicateurs d'inventaire complet, câblage système, sauvegarde exhaustive,
activation et fin de phase restent faux, même si toutes les déclarations sont
cohérentes. Aucun accès au wizard ni transition d'upgrade n'est ajouté.

Prochain chantier borné : première collecte effective en lecture seule d'un
périmètre systemd explicitement limité, avec provenance, limites et échecs de
visibilité déclarés. Les autres familles restent inconnues. Qualifier le vrai
collecteur en conteneur jetable si nécessaire, sans commande métier, enrôlement
ou arrêt de service. Arrêt après le présent checkpoint avant ce chantier.
