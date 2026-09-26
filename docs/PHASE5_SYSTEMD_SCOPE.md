# Contrat de découverte systemd hors du profil provisionné

## Contexte configuré : frontière courante

Le [contrat de lecture Service](PHASE5_SYSTEMD_EXECUTION_CONTEXT.md) définit
20 propriétés via les seules invocations acquises depuis le census vivant.
Aucun lecteur nouveau dans ce lot documentaire.16 sources primaires épinglées,
24 futures exigences non exécutées ; configuration textuelle, identités effectives
et chemins résolus restent distincts. Unités sans processus, autres types et
commandes restent inconnus dans le premier profil ; aucun enrichissement ne
confère droit de drain, exclusion ou complétude de l'inventaire.

## Source de candidats par tâches, liaison restant à raccorder

Le [recensement procfs](PHASE5_PROCESS_CENSUS.md) ajoute une source bornée de
candidats, inconnus et descendants selon la filiation observée. Ce lot n'ajoute
aucun appel systemd et ne transmet pas de FD persistant. Le prochain raccordement
doit revalider leur identité et préserver les limites d'invocation, sans revenir
aux propriétés d'unités nommées. Les critères globaux ne sont pas clos.

## Noms et huit relations collectés sur invocations liées

Le [lecteur étendu](PHASE5_SYSTEMD_INVOCATION_RELATIONS.md) enrichit les seuls
objets sélectionnés, valide les alias, résout les cibles depuis l'index observé
et compose les RelationFacts avec le modèle pur. Pas de lecture récursive par
nom, d'identité effective ou d'admission au drain. Les unités sans PID restent
inconnues et les 28 critères globaux restent ouverts. Preuves ciblées dans le checkpoint.

## Première liaison d'invocation implémentée

Le [lecteur minimal](PHASE5_SYSTEMD_INVOCATION_BINDING.md) livre deux observations
Id/InvocationID sur PIDFD possédés et adresse d'invocation validée. Huit cas
système ciblés prévus, avec preuves exactes dans le checkpoint. Il ne livre pas
Names/relations, UID/GID, source de PID de confiance, couverture des unités sans
processus ou adoption. Les contrats généraux ci-dessous restent historiques ;
aucune clôture des28 critères globaux, aucun retour aux chemins nommés.

## Alternative bornée à qualifier : liaison par invocation

Le [contrat PIDFD/invocation](PHASE5_SYSTEMD_INVOCATION_CONTRACT.md) définit
l'acquisition minimale possible sans propriété sur chemin nommé, à partir de
PID explicites. Analyse de source uniquement, seize futurs cas non exécutés ;
aucun remplacement de transport livré. Absence de PID/API = inconnu/refus, pas
fallback. L'interdiction et la preuve négative ci-dessous restent applicables.

## Correction impérative : proposition Properties.Get rejetée

La [recette négative 36238806917](PHASE5_SYSTEMD_PROPERTY_AUTOLOAD.md) et la
source systemd prouvent qu'un chemin Unit nommé peut recharger une unité
après sa disparition. La proposition Properties.Get ci-dessous reste historique
et n'est plus admise comme preuve de lecture sans chargement. Objectif intact :
pas de chargement, de mutation ou de garde acquise. Prochain lot de contrat
requis ; aucun lecteur de détails livré. Les 28 cas restent non qualifiés comme
campagne globale, les modèles purs et le transport de listes sont inchangés.

## Premier transport limité désormais livré

Le [transport des trois listes](PHASE5_SYSTEMD_DISCOVERY_TRANSPORT.md) utilise
le bus local avec provenance, appels fermés et budgets pendant lecture. Names,
identités d'exécution, définitions et relations détaillées ne sont pas collectés.
Sa recette ciblée ne clôt pas en bloc les 28 critères historiques ni l'inventaire
hôte. Aucun élargissement du collecteur provisionné ou du drain. Sections suivantes
historiques ; voir le checkpoint pour les preuves exactes du nouveau lot.

## Sélection déclarative partielle désormais implémentée

Le [modèle de pertinence](PHASE5_SYSTEMD_RELEVANCE_MODEL.md) consomme les faits
liés à la découverte, sans transport. Il élargit la revue sur signaux positifs
et conserve les inconnus. KNOWN_PROVISIONED, les détails effectifs et leurs
preuves système restent différés. La matrice JSON de 28 critères demeure le
contrat historique non exécuté comme campagne globale ; les tests locaux du
sélecteur ne remplacent pas ces recettes. Sections suivantes historiques.

## Première implémentation partielle du contrat

Le [modèle pur de découverte](PHASE5_SYSTEMD_DISCOVERY_MODEL.md) implémente
désormais les types des trois listes, provenance, alias, inconnus, budgets
de données et comparaison. Il ne livre pas le transport, la classification
de pertinence ou les détails d'exécution. Les 28 cas et le JSON ci-dessous
conservent le statut historique de contrat global non exécuté. Le modèle a
ses propres tests locaux, qui ne valent pas recette de ces exigences système.

## État livré et but du prochain développement

Ce lot est **documentaire**. Il définit le protocole et les décisions du futur
inventaire système élargi, avec une [matrice de 28 cas](PHASE5_SYSTEMD_SCOPE.json).
Il ne modifie aucun contrôleur, test, workflow, Web ou barrière de maintenance.
Les cas sont des exigences à implémenter, pas des tests exécutés ou verts.

Base Installer `75e987cedcdac2ad5b1795d0b0c55fdca573a894`, arbre
`37a4afb64155a90fdbc8333a912649576dabe09a`, 196 fichiers. La campagne ciblée
[36225529757](https://github.com/SepuLeVrai/hestia-installer/actions/runs/36225529757)
a qualifié cette base : 73 contrôles et huit scénarios réels Debian 13, un job,
première tentative, zéro erreur/échec/skip. Elle ne qualifie pas globalement
le nouvel arbre documentaire. Le Web reste `2a27c7a1`, arbre
`783be5abdcd5e13addefe96d743eee3a97b7a6de`, 1843 fichiers.

Le [collecteur déjà livré](PHASE5_SYSTEMD_OBSERVATIONS.md) reste fermé sur ses
deux ou quatre unités provisionnées. Il ne faut pas élargir son expression de
noms pour lui faire accepter des services étrangers. La découverte élargie
produira une enveloppe distincte, sans droit d'exécution ni enrôlement implicite.

## Trois populations à conserver séparément

Le protocole envisagé s'appuie sur le gestionnaire système de l'hôte lié à
la cible. Les signatures ci-dessous ont été relues dans les références
officielles v252 et v257 [R1/R2](#références-primaires). Cela ne certifie pas
la disponibilité ou la sémantique du futur adaptateur sur un hôte Debian.

| Lecture | Population et donnée à conserver | Conclusion interdite |
| --- | --- | --- |
| `ListUnits` | Unités chargées : nom primaire, état, objet, relation Following et job indiqué ; description supprimée de l'enveloppe | Toutes les unités installées ou tous les producteurs |
| `ListUnitFiles` | Référence de fichier et état d'activation ; l'implémentation v257 renvoie un chemin [R5] | Ce fichier est chargé, inactif ou sans capacité future de lancement |
| `ListJobs` | Jobs du gestionnaire : numéro, type, état, unité et objets associés | Toutes les tâches différées de l'hôte ou tous les clients SQL |

Le jeu initial est leur union, sans filtre de nom, d'état actif ou de type.
Chaque ligne garde ses origines. Un job sans détail d'unité reste présent et
non résolu. Un fichier installé mais non chargé reste présent ; aucun chargement
n'est demandé pour combler ce manque. États disabled, static, indirect ou masked
ne signifient pas « absent », ni « aucun processus vivant ».
Les types service/timer/path/socket sont les premiers détails à concevoir ;
les autres types restent dans l'index avec leur visibilité non résolue.

La lecture des jobs systemd ne ferme pas la famille `queued_jobs` du modèle,
qui inclut encore d'autres mécanismes. Dans toute projection de ce premier
élargissement : `systemd_system=partial`, cinq autres familles `unknown`.
Même trois listes vides ne permettent pas de changer ces états.

## Protocole de lecture, sans chargement ni mutation

Le contrat de transport propose uniquement les méthodes Manager `ListUnits`,
`ListUnitFiles`, `ListJobs`, `GetUnit`, et `Properties.Get` pour une liste fermée
de propriétés par interface. La liaison au propriétaire du nom D-Bus doit aussi
être observée, avant et après. L'implémentation du transport et sa dépendance
éventuelle restent à choisir et qualifier ; aucune commande exécutable n'est
livrée dans ce document. Préserver l'objectif d'Installer léger.

La distinction est déterminante : `GetUnit` échoue si le nom n'est pas chargé,
alors que `LoadUnit` peut le charger [R1/R2]. `LoadUnit` est donc interdit.
Ne pas remplacer cette lecture par un `status` destiné à l'humain, susceptible
de charger une unité [R4], ni par une boucle de noms supposés existants.
L'objet retourné par les listes est préférable à une résolution inventée.
Un objet disparu entre deux lectures reste une course observée, pas une absence.

Sont aussi interdits : `Properties.GetAll` sans sélection, environnement entier
du manager, journaux bruts, shell, `systemd-run`, démarrage/arrêt, enable/disable,
mask/unmask, reload/daemon-reload, reset-failed, set-property, chargement PHP,
exécution de wrapper ou générateur, écriture de configuration et garde acquise
pour faciliter la lecture. Aucune connexion SQL ou réseau applicatif.
Une connexion au bus local ne doit pas provoquer l'activation d'un service
absent. L'adaptateur devra faire vérifier ses appels exacts dans la recette.

La collecte est bornée de bout en bout. Elle prend un état initial des trois
listes, lit les détails autorisés, puis reprend les listes et la provenance.
Un changement de cible, boot, namespaces, propriétaire D-Bus ou identité
d'objet fait refuser la comparaison. Le résultat ne certifie pas l'absence de
changements intermédiaires qui auraient disparu avant la relecture. Aucun
historique d'événements, abonnement ou instantané atomique n'est revendiqué.
Pas de boucle de relance automatique pour attendre un hôte « tranquille ».

## Identité des objets, alias et modèles

La cible reprend instance, pin/arbre Web, webroot, slot/gate et UID/GID, avec
l'empreinte des besoins de stockage. Provenance obligatoire : machine-id,
boot_id, gestionnaire système, namespaces PID/montage identiques à PID 1,
version/capacités effectivement observées et propriétaire unique du nom D-Bus.
Une provenance illisible ou différente interdit une observation réutilisable.

Un nom d'unité est une donnée privée, jamais un argument shell. Le futur type
acceptera les noms systemd et leur échappement validé, jusqu'à 255 octets ; il
ne réutilisera pas le motif `hestia-…` du collecteur fermé. L'identité canonique
associe provenance du manager, objet chargé et nom primaire. `Names` et
`Following` sont conservés ; deux alias d'un objet ne deviennent pas deux
producteurs. Une collision ou association contradictoire doit échouer.
Un simple basename de fichier ne suffit pas à certifier son association à un
objet chargé. Aucune comparaison par sous-chaîne de nom.

Les modèles `@.service` restent des définitions non instanciées. Les instances
chargées gardent leur identité propre ; aucune substitution manuelle de `%i`,
de variable, de wildcard ou de spécificateur n'est exécutée pour en inventer.
Les alias et modèles peuvent modifier les fichiers/drop-ins pertinents [R3].
Un lien n'est ni suivi arbitrairement ni déclaré sûr parce qu'il ressemble à
un alias : conserver le lien et sa cible déclarée, puis exiger une résolution
bornée, sans cycle, à travers des parents et montages effectivement contrôlés.

Une unité transitoire peut ne pas fournir de fragment. Une unité générée doit
conserver ses chemins FragmentPath/SourcePath et le caractère généré observé,
sans exécuter son générateur. Aucun de ces cas n'est écarté pour absence de
fichier natif. Les répertoires `/etc/systemd/system`, `/run/systemd` et
`/usr/lib/systemd/system` sont des points de départ, pas une liste exhaustive :
le chemin de recherche effectif `UnitPath` participe à la preuve [R2/R3].

## Pertinence : signaux positifs, jamais une exclusion par défaut

Trois résultats privés sont proposés :

| Décision | Condition minimale | Suite autorisée par cette décision |
| --- | --- | --- |
| `KNOWN_PROVISIONED` | Identité exacte d'une unité déjà couverte et preuve fraîche inchangée du provisionneur fermé | Conserver la preuve ; aucune nouvelle action |
| `RELATED_UNMANAGED` | Au moins un signal positif lié à la cible | Conserver le candidat et les motifs ; examen/coordination à concevoir |
| `UNRESOLVED` | Informations insuffisantes, dynamiques ou sans lien positif établi | Conserver les inconnus ; aucune exclusion automatique |

Les signaux positifs ont quatre sources :

- **Identité** : UID/GID/groupes configurés correspondant à la cible, ou processus
  rattaché avec identité effective observée. Les deux catégories de faits restent
  distinctes. User vide, root, DynamicUser, changement d'UID par un démon ou NSS
  non résolu n'autorisent aucune conclusion négative.
- **Chemins** : exécutable, script, cwd, configuration ou destination résolue
  recouvrant code, slot ou racines enregistrées. Comparer des composants
  canoniques : `/srv/hestia-old` ne recouvre pas `/srv/hestia`. Une référence
  textuelle est un indice ; elle ne prouve pas l'identité de fichiers vus dans
  un autre mount namespace, chroot, bind mount ou conteneur.
- **Relations de lancement** : lien explicite d'activation ou de dépendance
  vers/depuis un candidat. Il élargit les objets à examiner, sans affirmer que
  chaque dépendance écrit dans HESTIA.
- **Liaison externe déclarée** : tâche opérateur ou client identifié explicitement
  comme associé à cette instance, avec origine et preuve à examiner. Cette
  déclaration ne vaut pas preuve effective d'identité, de chemin ou de drainage.

Un nom HESTIA, une mention en commentaire ou une chaîne vide ne constituent
pas à eux seuls une preuve. Un script root opaque reste non résolu, même sans
chemin HESTIA visible. Le proxy NGINX partageant le compte Web est un candidat
à coordonner ; il ne doit pas être ajouté silencieusement aux unités du drain.
Les commandes distantes capables d'écrire SQL restent dans leur groupe d'audit.

Toutes les phases d'exécution pertinentes devront être examinées (préparation,
start, reload, stop et fin), avec leurs wrappers, interpréteurs et destinations.
Le futur lecteur ne doit ni exécuter ces chaînes, ni charger leurs configurations
PHP, ni lire les secrets SQL pour tester une hypothèse. Un argument ou environnement
non résolu reste inconnu, jamais un tableau vide réputé complet. Il n'existe pas
de liste d'exclusion automatique dans cette version du contrat.

## Graphe des déclencheurs et portée des relations

Les liens `Triggers`/`TriggeredBy` et les cibles des timer/path/socket doivent
être conservés avec leur direction et leur origine effective. Les sockets à
instances exigent de conserver le modèle et les instances observées, sans
inventer une liste complète d'instances futures.
Les relations de requirements, maintien et réaction à succès/échec sont des
pistes supplémentaires. Le futur schéma devra nommer explicitement les propriétés
supportées et signaler toute relation pertinente non prise en charge.

`Before`/`After` restent de simples liens d'ordre pour ce classement. Ils ne
prouvent ni déclenchement ni identité d'écrivain. L'exploration des autres liens
est bornée, mémorise les nœuds déjà visités et conserve les cycles. Un lien
manquant, un modèle unresolved ou une frontière atteinte ne sont pas remplacés
par un graphe artificiellement complet. Même un timer arrêté ne prouve pas
l'absence d'une activation par socket, path, dépendance ou opérateur.

## Enveloppe privée, limites proposées et refus

L'index complet borné est conservé séparément du modèle `LauncherInventory`,
qui reste limité à 128 lignes et 256 Kio. Les unités non détaillées restent
explicitement non résolues dans l'index ; aucune disparition silencieuse.
Une future projection liera le digest de cette enveloppe entière et son
périmètre. Les clés de 64 octets du modèle ne sont pas les noms systemd : une
clé de projection peut être le SHA-256 canonique de l'identité privée complète,
avec détection de collision/duplication. Aucun découpage d'un inventaire trop
grand en morceaux prétendument complets.

| Ressource | Plafond proposé |
| --- | --- |
| Lignes chargées / fichiers / jobs | 4096 / 4096 / 1024 |
| Noms distincts / relations | 4096 / 8192 |
| Unités détaillées / définitions par unité | 128 / 16 |
| Nom / chemin | 255 / 2048 octets |
| Réponse de transport / total des réponses | 2 Mio / 8 Mio |
| Définition lue / cumul des définitions | 1 Mio / 16 Mio |
| Enveloppe privée / projection du modèle | 4 Mio / 256 Kio |
| Appel / intervalle de collecte | 5 s / 60 s |

Les plafonds sont des exigences de conception non implémentées. Le budget
restant devra limiter chaque appel et chaque lecture, pas seulement faire
échouer une admission après un processus non borné. Deux tours d'énumération
comptent dans le même budget. Le dépassement rejette la collecte réutilisable,
sans troncature ou relance aveugle. Un détail illisible peut rester non résolu
si l'index obligatoire est intact ; une énumération obligatoire illisible,
tronquée ou incompatible interdit de qualifier cet index.

L'accès aux définitions est une étape distincte : uniquement des fichiers
ordinaires, tailles contrôlées, parents/ACL/montages validés, lecture stable.
Ne pas ouvrir un chemin arbitraire reçu sur le bus, suivre un lien hostile,
déclencher un automount ou lire `/proc/<pid>/environ`/cmdline comme raccourci.
Une provenance de chemin non vérifiable se conserve comme telle et bloque
l'exploitation du fichier. Aucun générateur, shell ou interpréteur n'est évalué.

Les descriptions et arguments bruts ne sont pas persistés dans l'enveloppe
normalisée. Les descriptions transportées obligatoirement par ListUnits sont
éliminées en mémoire avant sérialisation ; logs de transport interdits. Ce n'est
pas un détecteur de secrets. Les propriétés sensibles ne sont pas lues en bloc.
Les arguments utiles à l'analyse future seront traités par un adaptateur privé
distinct puis réduits à des faits typés et empreintes ; une empreinte n'est pas
une anonymisation. Même noms, chemins et relations restent privés.
Le rapport public contiendra uniquement comptages, empreinte, états et codes
fermés, jamais les descriptions, noms d'unités, chemins ou identités hôte.

## Critères de recette et progression minimale

La matrice JSON comporte 28 scénarios à réaliser : unités étrangères et root,
GID/groupes, proxy, limites de chemins, timer/path/socket, ordre et cycles,
alias, fichiers inactifs/modèles, transitoires/générées, jobs, courses,
visibilité, plafonds, champs interdits et absence de mutation.
Chaque cas porte `executed=false`. Sa cohérence documentaire n'est pas une
preuve système, ni 28 tests ajoutés à la baseline.

Le **prochain lot unique** est un modèle privé pur de découverte : types des
trois listes, identités/alias, provenance, limites, inconnus et comparaison.
Aucun transport ou nouvel élargissement du collecteur existant. Sa validation
locale peut se faire sans Actions. Les décisions de pertinence détaillées et
le transport viennent après ce modèle, en chantiers séparés.

Une future recette réelle devra valider exactement les appels de lecture sur
un conteneur Debian jetable, les refus de visibilité et les états complexes,
puis seulement autoriser l'utilisation de l'adaptateur comme source de faits.
Les variantes Debian 12/13 et le pont du Web métier scellé restent à qualifier
lors des campagnes utiles. Aucun nouveau run n'est justifié par ce contrat seul.

Tous les indicateurs de clôture restent faux. La présence de ces listes ne
neutralise ni les neuf groupes de producteurs, ni root, ni les clients SQL.
Ce résultat n'est ni une lease, ni une autorisation de sauvegarde complète,
d'activation, d'upgrade, de rollback ou de promotion.

## Références primaires

Consultées le 26 septembre 2026 dans les sources officielles systemd. Les
empreintes du texte récupéré et les ancres sont dans le JSON ; les versions
documentaires ne remplacent pas la détection des capacités de l'hôte.
Les règles conservatrices et limites chiffrées ci-dessus sont des choix HESTIA.

- R1 : [Interface D-Bus v252](https://github.com/systemd/systemd/blob/v252/man/org.freedesktop.systemd1.xml).
- R2 : [Interface D-Bus v257](https://github.com/systemd/systemd/blob/v257/man/org.freedesktop.systemd1.xml).
- R3 : [Unités v257](https://github.com/systemd/systemd/blob/v257/man/systemd.unit.xml).
- R4 : [systemctl v257](https://github.com/systemd/systemd/blob/v257/man/systemctl.xml).
- R5 : [Implémentation Manager v257](https://github.com/systemd/systemd/blob/v257/src/core/dbus-manager.c), `list_unit_files_by_patterns`.
