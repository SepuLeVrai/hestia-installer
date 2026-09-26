# Noms et relations observés par invocation

## Base et périmètre

Base Installer `8a2f4ce538b1c6dbdb788d9384d32fdcdc0ae247`, arbre
`fe862fd882bd2f4fa16a5968218f984b7f0f3a11`, 219 fichiers. Le lot parent a
qualifié la liaison PIDFD/Id/InvocationID sur Debian13, run 36244399006,
176 contrôles et huit scénarios réels. Cette preuve ne qualifie pas le nouvel arbre.

`installer.systemd_invocation_relations.SystemdInvocationRelations` ajoute
Names et huit relations de l'interface Unit : Triggers, TriggeredBy, Requires,
Wants, BindsTo, Upholds, OnSuccess et OnFailure. Il réutilise les mêmes indices
explicites objet listé/PID et les mêmes PIDFD possédés. Pas de découverte de PID,
propriété d'unité nommée, GetAll, Unit spécifique timer/path/socket, Before/After,
contexte d'exécution, RefUnit, action de chargement ou contrôle d'un service.
Les limites et refus du [lecteur minimal](PHASE5_SYSTEMD_INVOCATION_BINDING.md)
restent requis. Son API collect(hints) et ses 24+8N appels sont conservés.

La source officielle [dbus-unit.c v257](https://github.com/systemd/systemd/blob/v257/src/core/dbus-unit.c)
a déjà été épinglée dans le contrat primaire JSON. property_get_names lit l'ID et
les alias de l'unité, property_get_dependencies parcourt sa table de dépendances
et retourne les ID cibles ; les neuf propriétés exposent des tableaux as.
Leur lecture ne demande pas de résoudre un nom cible. La protection contre le
chargement d'un objet disparu reste celle du chemin canonique d'invocation.

## Ordre et garanties locales

Un passage par couple effectue les quatre appels de liaison du parent, puis les
neuf propriétés sur la même invocation. Deux passages complets sont comparés,
entourés des deux tours de listes/provenance. Chaque propriété utilise le bus,
l'owner, l'interface et le chemin fermés ; aucun PIDFD hérité pour ces neuf appels.
Le PIDFD est de nouveau contrôlé après les propriétés, puis à la clôture.

Names exige le nom primaire, des noms d'unités chargées de même suffixe, sans
doublon. Les huit listes de relations admettent les noms non résolus et templates
sans les transformer en instances chargées. Réponse illisible, type/JSON invalide,
doublon, dépassement ou changement annule la collecte entière sans retry ni
reçu partiel. L'ordre des tableaux est normalisé ; une modification de leur
ensemble n'est pas masquée par le tri. Listes explicitement vides et propriétés
non observées ne sont jamais confondues.

L'index brut complet est validé et comparé avant enrichissement. Seules les unités
sélectionnées reçoivent leurs Names observés dans une nouvelle DiscoveryScan.
Le digest d'énumération enrichie lie la preuve de liste et toutes les propriétés
liées aux invocations. La validation de découverte est rejouée, notamment pour
refuser un alias revendiqué par deux objets ou en conflit avec les jobs.

La résolution d'une relation utilise exclusivement les noms primaires listés et
les alias effectivement observés de cet index. Pas de basename de fichier,
lookup récursif ni appel supplémentaire à la cible. Une cible non trouvée garde
son nom et target_object=None. Une unité chargée sans PID peut donc être la cible
d'un lien observé sans que ses propres propriétés, son identité ou sa couverture
soient réputées observées. Notamment, un timer référencé reste sans Names lus.

## Composition avec la pertinence

Chaque RelationFact lie propriété, cibles normalisées et InvocationBinding par
empreinte. Les UnitFacts conservent identities=None, paths=None, bindings=None.
Le RelevanceFacts est lié au digest de l'index enrichi et à son intervalle ; le
modèle pur SystemdRelevance le revalide avec la scan enrichie.

Le reçu privé RelationSample expose scan, index, facts, sélection et manifeste.
Le classement automatique ne dispose ici d'aucun signal initial d'identité,
chemin ou liaison métier : toutes les unités restent UNRESOLVED. Les relations
seules ne créent pas de lien à HESTIA. Une composition privée future pourra
ajouter des faits distincts et prouvés ; la recette de composition utilise une
déclaration de fixture explicite, sans la présenter comme une observation hôte.
Même avec ce signal déclaré, RELATED_UNMANAGED signifie revue, pas writer prouvé.

Aucun KNOWN_PROVISIONED, exclusion automatique, autorisation de drainage,
projection d'inventaire ou validation du stockage. Rapports et repr contiennent
uniquement comptes, empreintes et codes fixes, sans noms, chemins, PID ou ID.
Les modèles purs sont inchangés ; aucun endpoint, wizard ou consommateur de
mutation n'est raccordé.

## Limites inchangées et budget de l'extension

- 128 couples/PIDFD maximum ; 4096 noms observés cumulés par passage, en plus du
  plafond global de noms distincts de l'index comprenant fichiers/jobs/listes.
- 8192 relations cumulées par passage, tous objets et propriétés confondus.
- 24+26N appels, maximum 3352 ; l'absence de données ne diminue pas les appels requis.
- 2Mio par réponse, 8Mio reçus, 4Mio pour l'enveloppe complète, 5s/appel et
  60s/collecte partagés. Un inventaire trop volumineux ou lent est refusé.
- Aucun retry, aucun changement de plafond du lecteur minimal ou des listes seules.

Profil runtime 257, Debian13 et busctl257 ; Debian12 reste refusé par ce lecteur.
Pas de couverture des unités non sélectionnées, des processus absents, autres
managers, planificateurs ou clients SQL. Les noms et dépendances ne sont pas
exhaustifs de tous les liens possibles : les huit propriétés restent une liste
fermée de revue. Leurs cibles ne confèrent aucune identité d'exécution.
Les courses ABA, réutilisation de PID avant ouverture, cgroup et configuration
changeante, mapping à un propriétaire parmi plusieurs, fallback interne PIDref
et absence de preuve atomique restent les limites du parent.

## Qualification requise après gel

24 nouveaux tests de contrôleur, sélection de 200 avec les 176 tests affectés du
parent. Types fermés, normalisation, conflits d'alias, limites cumulées, enveloppe,
changement entre passages, propriétés privées, nettoyage FD et composition sont
couverts localement. Baseline core enrichie seulement de ces 24 IDs.

Un seul job ciblé Debian13 exécute les 200 tests puis les huit scénarios réels
PIDFD historiques et huit nouveaux :

1. Alias réellement chargé et neuf propriétés au chemin de la même invocation.
2. Dépendances réelles, cibles conservées, classement sans signal positif.
3. Timer sans PID conservé inconnu, fichier non chargé jamais interrogé par nom.
4. Disparition après liaison : Names refuse et l'unité fichier reste absente.
5. Redémarrage après Names : Triggers refuse sur l'ancienne invocation.
6. Dépendance changée par le banc entre passages, même PID/invocation/noms : refus.
7. Deux unités liées, fermeture de tous les FD.
8. Composition des liens avec une déclaration distincte, sans admission au drain.

Les mutations de services et reload sont exclusivement réalisées par le banc
jetable, jamais par le lecteur. Réseau coupé, cgroup privé, sources/modes comparés
avant/après, environnement et artefact vérifiés. La future campagne système
inclut cette recette sous Debian 13 seulement. Les résultats exacts sont conservés
dans le checkpoint après gel ; aucun PASS anticipé dans ce document.
Pas de qualification globale ni promotion. Le prototype c2acc806 et son run
négatif restent rejetés ; aucune assertion historique n'est affaiblie.

## Checkpoint et prochain chantier

Arrêt après ce lot. Prochain chantier distinct : source fiable des PID candidats
et liaison à leur identité effective, avec ses propres bornes et refus. Le contexte
configuré complet, les unités sans processus, la correction du collecteur fermé,
les autres familles de lanceurs et le pont au profil provisionné restent ouverts.
Ni la matrice historique 16 cas ni les 28 critères globaux ne sont déclarés clos.
Les neuf groupes de producteurs, sauvegarde exhaustive 5C2, upgrade 5C3,
reprise/rollback 5C4 et orchestration/wizard 5D restent à terminer.
