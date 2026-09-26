# Transport privé de découverte du manager système

## Extension distincte, listes seules inchangées

Le [lot suivant](PHASE5_SYSTEMD_UNIT_RELATIONS.md) compose ce transport avec
Id/Names et des relations sur objets sélectionnés. Le budget privé permet une
réservation finie supplémentaire à ce seul lecteur ; collect() des listes seul
reste limité à ses 24 appels. Les sections suivantes décrivent sa qualification
historique ; elles n'attestent pas le nouveau lecteur de détails.

## Périmètre

Base `ebafa1199a1a68f6410ce17d24508a1a30feb99b`, arbre
`a4c6e70150ecf625a70367f97dc6b2c0b9fa1e07`, 204 fichiers. Cette base a une
qualification locale de 134 tests, sans Actions ; elle n'est pas globalement
qualifiée. Le présent lot livre les lectures réelles des trois populations
du contrat de découverte, indépendamment du collecteur provisionné fermé.

`SystemdDiscoveryTransport(target, storage).collect(previous=...)` utilise
`/usr/bin/busctl` fourni par systemd, sans bibliothèque Python supplémentaire.
Il renvoie un `DiscoverySample` privé, sa `DiscoveryScan` et son `DiscoveryIndex`.
Les modèles purs de découverte et pertinence restent inchangés.
La cible et les besoins de stockage sont validés comme déclarations : ce
transport n'atteste ni le Web déployé, ni SQL, ni les fichiers métier.

Première qualification ciblée prévue sur Debian 13, systemd officiel, cgroup v2
et dbus-daemon actif dans son unité Debian native. Autres brokers, cgroups
personnalisés ou environnements sans cette provenance sont refusés. Debian 12
reste une qualification différée, même si la recette rejoint sa future Quality.
Il n'y a ni acquisition automatique de dépendances, ni démarrage d'un broker.

## Connexion et provenance

Avant tout appel, root et les namespaces PID/montage identiques à PID 1 sont
exigés, avec machine-id et boot_id valides. PID 1 doit être systemd. Le binaire
busctl et ses parents doivent passer le contrôle de chemin système protégé.
Les parents `/run` et `/run/dbus` sont des répertoires root non inscriptibles
par les autres comptes, sans lien. La socket fixe est root, sans lien ni
plusieurs noms physiques. Son mode peut permettre les connexions aux utilisateurs :
ce sont les permissions des parents qui empêchent le remplacement du chemin.

Le collecteur vérifie **avant connexion** le cgroup fixe
`/sys/fs/cgroup/system.slice/dbus.service/cgroup.procs` : exactement un PID,
exécutable `/usr/bin/dbus-daemon`, même namespace PID. Aucun balayage de procfs
ou inventaire des processus métier. Un broker absent ou une socket seule est
refusé avant d'exécuter busctl. Le PID effectivement annoncé par le bus doit
ensuite correspondre à ce PID préalable.

L'adresse est exclusivement `unix:path=/run/dbus/system_bus_socket` ; aucune
adresse, destination, commande ou propriété libre fournie par HTTP ou l'appelant.
L'environnement du client est reconstruit et ne reprend pas les variables D-Bus,
LD_PRELOAD ou de configuration du processus parent. Pas de shell ni pager,
stdin/stderr fermés, descripteurs hérités fermés.

L'identité du bus (GetId) et le propriétaire unique de org.freedesktop.systemd1
sont relus à chaque extrémité d'un tour. Le manager doit annoncer UID 0 et PID 1.
Les appels suivants ciblent ce propriétaire unique, jamais un nom d'unité à
charger. Identités locales, broker, inode/modes de socket et binaire sont relus
avant/après et comparés entre les deux tours. Une dérive refuse le résultat.

Ces contrôles sont séquentiels : ils ne constituent pas une lease du broker et
ne bloquent pas une intervention concurrente root ou un redémarrage externe.
Le précontrôle du broker évite la connexion à une socket seule observée ; il
ne prétend pas supprimer toute course entre précontrôle et connexion. La recette
couvre le broker effectivement arrêté avant collecte, pas une absence d'événement
intermédiaire. Aucune atomicité globale ou protection contre l'administrateur
root qui modifie simultanément l'hôte n'est revendiquée.

## Appels fermés et formats

Chaque tour effectue exactement douze appels, soit 24 pour une collecte :

1. GetId, PID du broker, GetNameOwner, PID et UID du propriétaire du manager.
2. Properties.Get pour **Version** et **UnitPath** du manager seulement.
3. ListUnits, ListUnitFiles et ListJobs, sans filtre.
4. Relecture GetNameOwner et GetId.

Tous passent par `busctl call` avec `--auto-start=no`,
`--allow-interactive-authorization=no`, `--expect-reply=yes`, `--timeout=5s`
et `--json=short`. Properties.Get utilise également **call**, car les flags
d'activation/autorisation sont appliqués explicitement par cette implémentation.
Pas de get-property générique, GetAll, introspect, status, GetUnit ou LoadUnit,
ni contrôle d'unité, acquisition de maintenance, lecture de secret ou appel SQL.

Le JSON est strict : clés type/data exactes, une seule valeur de retour,
signatures `a(ssssssouso)`, `a(ss)`, `a(usssoo)` et tuples de longueur exacte.
Les variantes Version/UnitPath sont typées s/as. Type erroné, doublon JSON,
NaN, liste illisible ou tronquée produit un refus, jamais une population vide.
Les limites et invariants du modèle pur s'appliquent ensuite.

Les descriptions reçues obligatoirement dans ListUnits sont supprimées en
mémoire avant la normalisation et le calcul des empreintes d'énumération.
Aucun dump brut, description ou stderr n'est journalisé. Les empreintes sont
calculées sur les lignes typées normalisées et triées, pas sur l'ordre variable
du bus ou les descriptions. Le compteur d'octets mesure la réponse brute.
Les noms et chemins restent privés ; ce traitement n'est pas une anonymisation.

## Limites et comparaison

Maximum 2 Mio par réponse et 8 Mio cumulés, mesurés pendant la lecture du pipe.
Chaque appel dispose de cinq secondes et partage le délai monotone de collecte
de 60 secondes. Un octet sentinelle au-delà du plafond suffit à refuser le résultat.
Le client de lecture est tué et récollecté après dépassement ; aucun PID ou service
observé n'est signalé. Le nettoyage du client peut utiliser jusqu'à une seconde
supplémentaire. Pas de relance automatique.

Les checkpoints de délai couvrent appels, parsing et normalisation. Les lectures
locales de provenance ont des tailles fixes, avec NOFOLLOW/NONBLOCK et contrôles
de stabilité pour les fichiers. Ce n'est pas un mécanisme capable d'interrompre
un noyau bloqué pendant un accès de métadonnées à `/usr` ou procfs. Cette limite
est distincte du watchdog réel des processus busctl.

Plafonds de données inchangés : 4096 unités, 4096 fichiers, 1024 jobs, union de
4096 noms, enveloppe 4 Mio. UnitPath garde son ordre. Les deux tours doivent être
identiques après normalisation, avec leurs digests ; un changement est refusé.
Avec previous, l'ancien contexte de transport et l'index complet sont comparés
au nouveau résultat, sans recul d'intervalle ni adoption permissive.

Le transport ne lit pas Names, identités d'exécution, chemins de définitions,
arguments ou relations détaillées. Names reste `None`, Following vient de la
liste, et les fichiers ne sont jamais rattachés à un objet par leur basename.
Jobs et fichiers masqués/désactivés restent présents. Aucun fait de pertinence
n'est inventé. KNOWN_PROVISIONED et les admissions au drain restent indisponibles.

Le rapport ajoute seulement l'origine LOCAL_SYSTEM_BUS, le compteur d'appels/
octets et `system_manager_lists_observed=true`. Les indicateurs des modèles purs
restent faux et leurs blocages amont sont conservés. Le transport ne ferme pas
les autres canaux, les neuf producteurs, les sauvegardes ou la phase 5.

## Qualification et suite

21 nouveaux tests portent le core détectable à 787, dont 782 requis ; écart
historique de cinq conservé. Sélection locale de 155 : transport21, relevance42,
discovery35, launcher_inventory23, storage_inventory18, systemd_observations16.
Les tests locaux emploient des réponses synthétiques pour le bus et de vrais
processus jetables pour la capture, les dépassements et les délais.

Un seul job ciblé Debian 13 est prévu sur code et documentation gelés ensemble :
les mêmes 155 contrôles, puis dix scénarios réels. Image minimale officielle
systemd/dbus/python/passwd, réseau coupé pendant les essais, aucun montage cgroup
hôte ou accès métier. Manifestes octets/modes avant/après, sources root et
suppression du conteneur dans le job. Les résultats exacts sont dans le checkpoint,
pas déduits de la présence de ce document.

Scénarios : listes/provenance/appels fermés, service étranger conservé actif,
fichiers non chargés et modèles masqués sans activation, alias sans adoption,
service transitoire, vrai job notify en attente, changement entre tours,
invalidation du previous, refus non-root/client inscriptible, et broker arrêté
refusé avant connexion malgré sa socket présente. Le banc prépare et démarre
ses fixtures ; le collecteur ne le fait jamais. L'injection entre tours change
réellement le manager sans remplacer les réponses par des mocks.

Le stockage et le pin Web du banc sont des déclarations explicites de fixture,
sans Web déployé : aucune preuve du profil métier scellé, de SQL ou de l'application.
Quality globale, Debian 12, autres recettes et promotions restent différées.
Les 28 critères historiques du contrat global ne sont pas déclarés exécutés en bloc.

Après ce lot, checkpoint et arrêt. Prochain chantier distinct : borner les
détails d'unités nécessaires aux signaux de pertinence, avec propriétés fermées,
provenance et formats réellement vérifiés. Ne pas ajouter simultanément une
lecture arbitraire de définitions, des wrappers ou une autorisation de drain.

## Références primaires consultées

- [busctl v257](https://github.com/systemd/systemd/blob/v257/man/busctl.xml) : JSON typé, flags call, délai.
- [Implémentation busctl v257](https://github.com/systemd/systemd/blob/v257/src/busctl/busctl.c) : json_transform_message/variant et call.
- [Contrat de découverte et références D-Bus v252/v257](PHASE5_SYSTEMD_SCOPE.md) : signatures des trois listes et du manager.

Les références complètes tierces ne sont pas redistribuées ; URL, empreinte et
ancres consultées sont conservées dans les preuves. La version du binaire réellement
exercé est enregistrée par la recette, distinctement de ces sources documentaires.
