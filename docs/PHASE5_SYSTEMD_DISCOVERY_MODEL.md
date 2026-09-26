# Modèle privé de découverte systemd

## Consommateur de pertinence livré séparément

Le [sélecteur pur](PHASE5_SYSTEMD_RELEVANCE_MODEL.md) revalide une DiscoveryScan
et lie ses faits au digest de cet index complet. Ce module de découverte reste
inchangé et ne collecte toujours rien. Le sélecteur ne livre ni transport,
projection ou permission ; les inconnus et limites de preuve sont conservés.
Le prochain lot de transport est distinct. Le texte ci-dessous décrit le lot
historique du modèle de découverte et ses 35 tests.

## Périmètre livré

Base `847cea069daa1f9077bc266f1fec4dbbe3464a72`, arbre
`603e9aac9f25abfac5e25906e0adc0896a9cc2dd`, 198 fichiers. Ce lot documentaire
avait fixé le [contrat de découverte](PHASE5_SYSTEMD_SCOPE.md), avec zéro Actions.
Le présent lot implémente uniquement son index privé pur dans
`installer/systemd_discovery.py`. Ni transport D-Bus, ni collecte hôte,
classification de pertinence, projection de lanceurs ou mutation ne sont ajoutés.

`SystemdDiscovery(target, storage)` réutilise le contrôle strict de cible et
de `StorageRequirements` du modèle `LauncherInventory`, sans modifier celui-ci.
Instance, pin/arbre Web métier `2a27c7a1`, code, slot/gate, UID/GID, machine-id
et boot_id sont liés au résultat, ainsi que le digest du manifeste de stockage
entier. Les blocages de stockage et des autres groupes restent présents.
Cette validation ne relit ni les fichiers Web, ni leurs propriétaires ou ACL.

Le collecteur `SystemdObserver` existant reste fermé sur ses deux/quatre unités.
Il n'est pas raccordé à cet index. Les objets typés reçus du futur adaptateur
sont des déclarations, pas des attestations authentifiées. Aucun résultat de
ce modèle ne doit autoriser un arrêt, une exécution ou une sauvegarde.

## Types et populations distinctes

Une `DiscoveryScan` contient la cible exacte, début/fin UTC, durée monotone
déclarée en millisecondes et deux `DiscoveryRound`, avant et après.
Chaque tour contient la provenance et trois `Enumeration` obligatoires :

| Type de ligne | Contenu privé conservé |
| --- | --- |
| `LoadedUnit` | Nom primaire, objet, états load/active/sub, Following, éventuelle observation Names et référence de job |
| `InstalledUnitFile` | Chemin de fichier et état d'activation déclaré |
| `ManagerJob` | Numéro, type, état, nom d'unité, objet job et objet unité |

Chaque énumération doit être explicitement `observed`, avec une empreinte
SHA-256 déclarée et un tuple de lignes du type exact. Unknown, unreadable,
partial, famille manquante, dictionnaire libre, type approchant, liste mutable
ou empreinte absente entraînent un refus fermé. Cela n'affirme pas qu'une
énumération a effectivement été observée : seul le futur transport pourra
en fournir la preuve. L'appelant ne peut pas assimiler un refus à zéro ligne.
Le modèle ne conserve pas de descriptions ou d'arguments bruts dans ses types.

L'index conserve séparément les trois populations. Le basename d'un fichier
alimente uniquement le comptage des noms ; `loaded_object` reste nul et
`definition_verified` faux, même s'il ressemble exactement à une unité chargée.
Deux chemins de fichier de même basename restent deux lignes. Aucun contenu,
lien symbolique, mode, montage ou recherche de fichier effectif n'est inventé.

Disabled, masked, static, indirect et valeurs futures syntaxiquement valides
restent présents. Les modèles `@.service` sont acceptés dans les fichiers,
mais refusés comme unités prétendument chargées non instanciées. Une unité
transitoire, générée ou d'un autre type peut être indexée sans qu'on invente
un fragment, une origine ou une identité d'exécution. Leur détail reste futur.

## Provenance et identité

`DiscoveryProvenance` exige host_id/boot_id identiques à la cible, manager
`system`, propriétaire D-Bus unique déclaré, namespaces PID et montage
identiques à ceux de PID 1, version non vide, capacités déclarées contenant
ListUnits/ListUnitFiles/ListJobs et chemin de recherche non vide.
Tous ces éléments sont des entrées : aucun namespace, bus, noyau ou fichier
n'est lu par le modèle. Leur authenticité et leurs capacités effectives ne
sont pas vérifiées ici. Les chemins de recherche gardent leur ordre de priorité.

Les noms suivent un profil syntaxique ASCII borné à 255 octets : caractères
usuels et séquences littérales `\xhh` hexadécimales minuscules, suffixe de type,
au plus un séparateur @. Les templates chargés, caractères de contrôle, slash,
wildcards et échappements libres sont refusés. Ce sous-ensemble conservateur
n'est pas présenté comme un validateur complet de toutes les versions systemd.
Les noms ne sont jamais décodés en chemins ou exécutés. Les chemins restent
canoniques syntaxiquement et ASCII dans cette première version ; une valeur
hors profil entraîne un refus, sans omission de la ligne.

Un objet chargé n'a qu'une ligne canonique. Names, s'il est déclaré, doit
contenir le nom primaire une seule fois ; les alias doivent avoir le même
suffixe de type et ne peuvent appartenir à deux objets. `names=None` garde
le manque d'observation ; un tuple vide n'est pas accepté comme liste Names
complète. L'ordre des alias n'a pas de signification. Un objet ou nom primaire
dupliqué, une collision d'alias et un rattachement contradictoire sont refusés.

Following est une relation indépendante, jamais une preuve d'alias ou un
déclencheur. `None` signifie inconnu, chaîne vide signifie explicitement vide.
Une cible Following absente reste non résolue. Les cycles sont conservés sans
exploration ni fusion de leurs objets. Aucun graphe d'activation n'est livré.

## Jobs et contradictions

`UnitJobReference` représente exactement le tuple normalisé : numéro zéro,
type vide et chemin `/` pour « aucun job déclaré », ou identifiant positif
uint32, type et chemin job associé. Booléens et débordements sont refusés.
Un `ManagerJob` est toujours positif et son chemin doit correspondre au numéro.

Si le détail chargé existe, nom/alias connu, objet, identifiant et type de job
doivent être cohérents. Un nom de job ne peut être associé à deux objets,
un objet à deux jobs ou le même numéro à deux unités. Des contradictions ne
deviennent pas des informations ignorées pour accepter le reste du scan.

Un job dont le détail d'unité manque reste dans sa liste avec blocage.
Inversement, une référence non nulle de LoadedUnit sans ligne ListJobs reste
dans l'unité avec blocage. Un alias de job non vérifiable parce que Names n'a
pas été observé reste explicitement non vérifié. L'index n'invente pas le détail
manquant, et ne fait aucune action sur un numéro de job ou un objet déclaré.

## Deux tours, comparaison et confidentialité

Début, fin, durée et `now` sont fournis explicitement par l'appelant. Entiers
exacts, sans booléen ; début ≤ fin ≤ now, âge depuis le début au plus 60 s,
durée déclarée au plus 60000 ms. Le modèle n'interroge aucune horloge et ne
certifie pas la véracité de ces valeurs.

Les deux tours doivent être identiques après normalisation, y compris les
empreintes déclarées des trois énumérations. Un futur adaptateur devra définir
leur sérialisation canonique ; elles ne sont pas recalculées ici à partir du
transport. Les lignes, capacités et alias sont ordonnés canoniquement ; les
chemins de recherche gardent leur ordre. Une dérive de propriétaire D-Bus,
état, alias, fichier, job ou empreinte refuse le scan. Une identité host/boot
étrangère est refusée avant la comparaison.

`inspect(scan, now=..., previous=...)` accepte une nouvelle plage temporelle
sans chevauchement avec le résultat précédent, puis compare tout le contenu,
la cible, la provenance, le stockage et les blocages. Les deux tours identiques
et cette comparaison ne prouvent pas l'absence de changements intermédiaires.
Pas de reprise permissive, de retry ou de lease vivante.

`DiscoveryIndex` est immuable ; `private_manifest()` retourne une copie.
Le rapport public expose seulement état, origine déclarative, comptages,
empreinte, codes fixes et indicateurs faux. Repr et erreurs ne contiennent
ni nom d'unité, chemin ou identité hôte. Une dataclass fabriquée par un appelant
privilégié n'est pas signée et ne doit pas être admise comme certificat d'hôte.

## Limites effectivement implémentées et limites futures

4096 lignes chargées, 4096 fichiers, 1024 jobs ; union de 4096 noms distincts
incluant alias, Following, fichiers et jobs. Plafonds vérifiés avant itération
des tuples et pendant la construction de l'union. Nom255, chemin/objet2048,
états64, version128, 64 capacités et 64 chemins de recherche. Les valeurs
inconnues syntaxiquement valides sont retenues, sans classification positive.
L'enveloppe JSON canonique est bornée à 4 Mio pendant la sérialisation.

Les 4096 lignes peuvent dépasser les 128 lanceurs du modèle précédent : aucun
recoupage, aucune projection ou troncature n'est effectué. Les limites IO,
fichiers de définition et graphe du contrat global restent à implémenter dans
leurs futurs adaptateurs. La durée déclarée n'est pas un watchdog de transport.
Il n'y a actuellement ni IO, ni collecte réelle à interrompre dans ce modèle.

Le canal systemd_system reste partiel et les cinq autres inconnus. Même toutes
les listes vides laissent les blocages : transport absent, déclarations non
authentifiées, pertinence non classée, couverture non certifiée, observation
non atomique, aucun contrôle des lanceurs et autres producteurs non vérifiés.
Tous les indicateurs de clôture, sauvegarde, activation et phase 5 restent faux.

## Qualification locale et suite

35 nouveaux tests portent l'inventaire core détectable à 724, dont 719 requis
dans la baseline (écart historique de cinq conservé). Sélection locale de
92 tests : systemd_discovery35, launcher_inventory23, storage_inventory18 et
systemd_observations16. La suite complète n'est pas lancée pour ce lot.
Les besoins de stockage viennent du vrai résolveur avec son IO de pin simulé
dans la fixture. Les scans sont des déclarations synthétiques ; aucune preuve
systemd ou SQL/Web réelle n'est revendiquée.

Les tests vérifient types, populations, provenance, collisions, inconnus,
comparaisons, limites réelles (4096 unités et dépassement de 4 Mio), protection
des diagnostics et absence d'IO/horloge implicite. Code et documentation doivent
être gelés ensemble, puis sélection locale et gardes statiques sur cet arbre.
Aucun workflow modifié ou Actions nécessaire. Preuves exactes dans le checkpoint.

Les 28 cas du contrat initial restent historiques et marqués non exécutés
comme campagne globale : ce lot couvre une partie de leur sémantique pure,
pas la pertinence détaillée ni leur recette système. Aucun flag global ne
devient vrai par simple correspondance d'un scénario local.

Prochain chantier unique : sélection conservatrice de pertinence sur faits
typés, liée à cet index, sans transport ni exécution. Identités, chemins et
relations demandent des preuves distinctes ; l'absence de signal reste inconnue.
Le transport et ses recettes réelles restent un lot ultérieur. Après le présent
checkpoint : arrêt. Quality globales différées et promotions toujours interdites
tant que toutes les campagnes requises sur l'arbre exact ne sont pas vertes.
