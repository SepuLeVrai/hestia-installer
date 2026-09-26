# Sélection privée de pertinence systemd

## Transport des listes disponible, faits détaillés encore déclaratifs

Le [transport de découverte](PHASE5_SYSTEMD_DISCOVERY_TRANSPORT.md) livre les
trois listes réelles ; il ne collecte aucun des faits détaillés de ce sélecteur.
Son existence ne rend pas ces déclarations authentifiées et ne livre aucune
admission KNOWN_PROVISIONED. Ce modèle reste inchangé ; son prochain raccordement
nécessite un lot distinct de propriétés fermées. Sections suivantes historiques.

## Périmètre livré

Base `2677d0efd1462373784ac77d734b2599d604a9a8`, arbre
`016c10c618bb2cde58fe1211c5da78be1f067038`, 201 fichiers. Le modèle pur de
découverte de cette base avait passé 92 tests locaux, dont 35 nouveaux, sans
Actions. Le présent lot ajoute `installer/systemd_relevance.py`, ses tests et
sa documentation, sans modifier le collecteur fermé ni le modèle de découverte.

Il classe des **déclarations privées** pour élargir les candidats à examiner.
Il ne collecte pas l'hôte, n'exécute rien et ne certifie aucun producteur.
Pas de branchement aux services, à la sauvegarde, au wizard ou à HTTP.
Le Web métier reste épinglé sur `2a27c7a1`, sans modification.

Deux décisions sont produites : `RELATED_UNMANAGED` sur signal positif,
`UNRESOLVED` sinon. Aucune exclusion automatique. `KNOWN_PROVISIONED` reste
indisponible : aucun nom d'unité, même exactement celui du profil HESTIA,
ne remplace une preuve fraîche du provisionneur. Le pont entre cette preuve
et les objets de découverte n'est pas livré. Un proxy partageant le compte
Web peut donc devenir candidat, sans être ajouté au périmètre de HttpDrain.

## Liaison au scan et aux besoins de stockage

`SystemdRelevance(target, storage).inspect(scan, facts, now=...)` revalide
la `DiscoveryScan` typée avec le modèle existant. Le digest de l'index entier
doit égaler `RelevanceFacts.discovery_sha256`. Ce contrôle inclut les trois
populations, les alias, états, provenance, cible, stockage et intervalle.
Le modèle n'accepte pas à la place des octets d'un DiscoveryIndex fabriqué.
La dataclass de scan reste elle-même une déclaration privilégiée, non signée.

Le temps explicite des faits doit appartenir à l'intervalle début/fin du scan.
La fraîcheur de 60 secondes et les deux tours sont contrôlés par la découverte.
Le modèle ne lit pas l'horloge. Il ne prouve ni l'authenticité des dates, ni la
stabilité des détails entre deux lectures, ni l'absence de changement intermédiaire.
Les faits détaillés n'ont qu'une observation déclarée ; aucune nouvelle garantie
d'atomicité n'est ajoutée. Une modification du scan exige de recalculer le digest
et la sélection. Pas de reprise d'une ancienne décision comme lease ou permission.

Chaque `UnitFacts` désigne exactement un objet chargé et son nom primaire.
Un alias ne devient pas une seconde ligne détaillée ; objet étranger, nom primaire
incorrect ou détail dupliqué est refusé. Les unités sans détail restent dans
l'index et dans les décisions, avec `UNIT_DETAILS_UNOBSERVED`.

## Faits typés et motifs distincts

| Fait | Validation et effet conservateur |
| --- | --- |
| `IdentityFact` configuré | UID/GID/groupes explicites ; correspondance cible = `CONFIGURED_IDENTITY_MATCH` |
| `IdentityFact` effectif | Identité et liaison déclarée d'un processus à l'unité = `EFFECTIVE_IDENTITY_MATCH` |
| `PathFact` textuel | Chemin littéral canonique recouvrant une racine = `TEXTUAL_PATH_HINT` |
| `PathFact` résolu déclaré | Même namespace de montage et root `/` = `DECLARED_HOST_PATH_MATCH` ; sinon `CONTEXTUAL_PATH_HINT` |
| `RelationFact` admise | Étend l'examen aux objets liés à un candidat ; aucune preuve d'écriture |
| `ExternalBinding` | Déclaration liée à l'instance, origine fermée et digest = `DECLARED_EXTERNAL_BINDING` |

Tous les digests sont déclarés, non recalculés depuis un transport ou un fichier.
Les objets n'acceptent ni descriptions, arguments bruts, environnement libre
ou commentaire. Les chemins et noms restent néanmoins privés ; ce schéma
n'est pas un détecteur de secrets et un digest n'est pas une anonymisation.

### Identités

Une identité est `observed`, `unknown` ou `unreadable`. Un état non observé
ne peut transporter des valeurs prétendument effectives ou une preuve.
Une identité observée exige un digest ; UID, GID et groupes peuvent rester
partiellement inconnus. Les booléens ne sont pas des UID, ni des PID.
Une liste de groupes vide décrit uniquement ce fait, pas tous les producteurs.

Configured et effective restent distincts, y compris s'ils diffèrent. Pour un
fait effectif, `ProcessBinding` exige PID positif, start_ticks positif, namespace
PID égal à celui du scan, chemin cgroup et digest de liaison. Le modèle ne lit
ni procfs ni les cgroups : la liaison reste déclarative. Un PID répété dans
une unité, ou attribué à deux objets dans le même lot, est refusé, même avec
des start_ticks différents. Une collecte ambiguë doit être reprise explicitement.

Root, autre UID, DynamicUser, NSS non résolu ou absence d'identité ne sont
jamais des motifs d'exclusion. DynamicUser inconnu/actif reste signalé. Le
modèle ne résout pas NSS et n'atteste pas les mappings de user namespaces,
changements d'UID ultérieurs ou tous les descendants. Il conserve toujours
`EXECUTION_CONTEXT_INCOMPLETE`.

### Chemins

Les racines comparées viennent du webroot, du slot et de toutes les portées
enregistrées dans le manifeste de stockage strictement lié à la cible. La
comparaison porte sur des composants, dans les deux sens de recouvrement :
égalité, descendant ou ancêtre. `/srv/private-web-old` ne correspond pas
à `/srv/private-web`. Un ancêtre large, notamment `/`, peut donc sélectionner
beaucoup d'unités pour examen ; ce comportement prudent ne prouve pas un accès.

Un chemin peut être un exécutable, script, cwd, configuration, destination ou
source générée, aux phases prepare/start/reload/stop/post/runtime. Ces valeurs
ne constituent pas une analyse exhaustive des chaînes d'exécution. Aucun
wrapper, générateur, variable, spécificateur ou code PHP n'est évalué.

Les états unknown/unreadable conservent l'absence de valeur. Textual n'accepte
pas de contexte prétendument résolu. Resolved exige un namespace et une racine
déclarés ; un namespace étranger ou un chroot préserve l'indice textuel mais
ajoute `PATH_CONTEXT_UNVERIFIED`. Même dans le contexte déclaré local, liens,
montages, propriétaires et octets ne sont pas attestés. `PATH_IDENTITY_NOT_ATTESTED`
reste présent. Un chemin différent dans un chroot n'est pas transformé en chemin
hôte supposé et ne devient jamais une exclusion.

### Relations

Chaque relation conserve la propriété source, le nom cible, son éventuel objet
et son digest. Un objet cible renseigné doit exister dans l'index et le nom doit
être son primaire ou un alias déclaré. Un nom ressemblant au basename d'un fichier
ne suffit pas. Une cible sans objet reste non résolue, même si son nom pourrait
être retrouvé ; le modèle ne remplace pas une liaison manquante par une déduction.

Propriétés qui élargissent l'examen : Triggers, TriggeredBy, Requires, Wants,
BindsTo, Upholds, OnSuccess, OnFailure et Unit. Unit n'est accepté qu'au départ
d'une unité timer/path/socket. Cette liste est une politique de revue HESTIA,
pas une simulation de l'exécution de systemd ; le futur adaptateur devra vérifier
les interfaces et propriétés réelles. Before/After restent de l'ordre seulement.
Following et toute propriété non prise en charge sont conservées sans propagation,
avec une limite explicite de couverture.

La propagation parcourt les liens admis dans les deux sens pour élargir la revue.
La direction originale reste dans les faits. Une dépendance n'est pas déclarée
écrivain pour autant. Chaque objet est mis en file au plus une fois et chaque
lien admis est examiné au plus deux fois. Les cycles sans signal initial restent
UNRESOLVED ; un cycle relié à un signal peut devenir candidat. Aucun appel récursif
ou exploration de nom arbitraire. Les cibles manquantes et modèles `@.service`
restent inconnus ; aucune instance future n'est inventée.

### Fichiers, jobs et déclarations externes

Les fichiers installés conservent tous `UNRESOLVED`, même si un objet chargé
porte leur basename. Masqué/désactivé ne veut pas dire absent ou sans processus.
L'analyse de leur contenu et leur liaison aux objets ne sont pas livrées.
Un job hérite du statut candidat seulement si son objet et son nom/alias sont
liés par les déclarations à un objet chargé sélectionné. Détail manquant ou
alias non vérifié laisse le job UNRESOLVED. Aucun job n'est supprimé ni exécuté.

Les liaisons externes sont limitées à operator_task/operator_catalog/sql_client,
à l'instance exacte et à un digest. Elles ne remplacent pas l'identité, le chemin
ou la coordination SQL. Une liaison étrangère ou une origine libre est refusée.

## Limites, confidentialité et résultat

128 unités détaillées au plus, indépendamment des 4096 lignes de découverte.
Par unité : 64 identités, 64 chemins, 16 liaisons externes ; globalement
1024 identités, 1024 chemins et 8192 relations. Les tuples sont bornés avant
itération, puis les cumuls contrôlés par unité. Les doublons ambigus sont
refusés, sans suppression silencieuse. Les limites de noms et chemins reprennent
le sous-ensemble conservateur de la découverte. L'enveloppe totale, incluant
l'index complet, les faits et les décisions, est limitée à 4 Mio pendant
sérialisation. Un index admissible peut donc dépasser le plafond une fois
enrichi : refus explicite, aucune troncature ou découpe réputée exhaustive.

`RelevanceSelection` est immuable et son manifeste privé est une copie.
Le rapport public expose seulement état déclaratif, empreinte, comptages et
codes fermés. Aucune identité d'unité, de processus, d'hôte ou de chemin.
Les exceptions et repr restent fermés. Les blocages de l'index amont sont
conservés, dont `RELEVANCE_NOT_CLASSIFIED` : ils concernent la preuve hôte,
que la sélection déclarative ne livre pas. Les indicateurs publics de preuve,
action, drainage, inventaire complet, activation et phase 5 restent tous faux.

## Qualification locale et prochain lot

42 nouveaux tests, soit 766 core détectables et 761 requis (écart historique
de cinq conservé). Sélection locale de 134 : relevance42, discovery35,
launcher_inventory23, storage_inventory18 et systemd_observations16. Gardes
statiques sur les 204 fichiers ; code et documentation gelés ensemble avant
la vérification finale. Les preuves exactes appartiennent au checkpoint.

Les tests incluent réellement 4096 unités, 8192 liens cycliques, dépassement
de 4 Mio, limites cumulées, contradictions de PID, liens d'alias, chemins,
fraîcheur et interdiction d'IO/horloge implicite pendant la validation.
Il s'agit de données synthétiques et de la fixture du résolveur de stockage,
pas d'une recette système ni d'un pont SQL/Web réel. Aucun workflow modifié
ou Actions demandé ; Quality globales et promotions restent différées.
Les 28 critères du contrat global restent `executed=false`.

Le prochain chantier doit borner le transport réel de découverte des **trois
listes seulement** : choix d'un client système officiel disponible, formats
et dépendances explicites, appels autorisés sans activation/LoadUnit, budgets
appliqués pendant les IO et provenance avant/après. Lire les contrats et les
capacités locales avant de choisir l'implémentation. Ne pas étendre en même
temps les lecteurs de définitions, processus ou relations détaillées. Sa
qualification nécessitera une recette système ciblée au moment utile ; ne pas
présenter des mocks comme preuve réelle. Après ce présent lot : checkpoint
et arrêt, sans démarrer ce transport automatiquement.
