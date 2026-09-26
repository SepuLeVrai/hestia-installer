# Contrat d'acquisition par PIDFD et identifiant d'invocation

## Suite du contrat : implémentation minimale

Le [lot suivant](PHASE5_SYSTEMD_INVOCATION_BINDING.md) implémente la liaison
Id/InvocationID ; sa qualification ciblée est rapportée dans son checkpoint.
Le présent document et son JSON conservent la conception et ses seize exigences
comme état historique, sans transformer cette matrice en campagne exécutée.

## Décision et périmètre de ce lot

Base `d86e406a11868b45c59cf945d71b5076cd7c052e`, arbre
`6b7ca7301aa5980de69124af8f4edda13254e18e`, 212 fichiers. Ce lot définit
une alternative étayée par la source au [lecteur nommé rejeté](PHASE5_SYSTEMD_PROPERTY_AUTOLOAD.md).
Il ne livre aucun transport nouveau et ne réhabilite pas `c2acc806` ni son run
négatif `36238806917`. Aucun appel système/D-Bus de cette proposition n'est exécuté.
Code, tests, baseline, workflows et Web restent inchangés. Zéro GitHub Actions.

Décision : partir de PID explicitement proposés, ouvrir localement leurs PIDFD,
obtenir l'invocation par Manager.GetUnitByPIDFD, puis ne lire que le chemin
renvoyé par GetUnitByInvocationID. Le prochain lot sera limité à la liaison
Id/InvocationID ; Names, relations et identités d'exécution viendront après sa
preuve réelle. Les [références, bornes et futurs cas](PHASE5_SYSTEMD_INVOCATION_CONTRACT.json)
sont vérifiables, tous les cas runtime y restent executed=false.

## Chaîne vérifiée dans la source, à qualifier sur système réel

| Étape | Source officielle v257 | Conséquence pour le contrat |
| --- | --- | --- |
| Manager.GetUnitByPIDFD(h) -> osay | dbus-manager.c : pidref_set_pidfd, manager_get_unit_by_pidref, retour chemin/nom/invocation, pidref_verify | Acquérir l'invocation sans lire une propriété d'objet nommé |
| Recherche du processus | cgroup.c : tables des cgroups et processus surveillés | Pas d'appel de chargement dans cette recherche ; un seul propriétaire est renvoyé |
| Manager.GetUnitByInvocationID(ay) -> o | dbus-manager.c : table units_by_invocation_id pour un ID non nul | Référence à l'invocation, pas au nom réutilisable |
| Résolution du chemin d'invocation | manager.c : table puis erreur si ID absent | Cette branche ne tombe pas sur manager_load_unit |
| Fin/changement d'invocation | unit.c : retrait de l'ancienne entrée et pose de la nouvelle | L'ancien identifiant ne devient pas un alias du nouveau |
| Encodage du chemin | unit-def.c puis bus-label.c | Échappement canonique, y compris un chiffre en première position |
| Passage du PIDFD | busctl.c : argument h parmi les descripteurs hérités | Garder le client officiel ; pas de bibliothèque D-Bus supplémentaire envisagée |

L'inférence de conception est la suivante : si l'ID non nul est acquis ainsi et
si toute propriété vise exclusivement son chemin canonique validé, la disparition
n'emprunte plus la branche de chargement par nom observée au précédent lot.
Il reste à le prouver par la recette réelle. Une validation de source n'est ni
une preuve de support de l'environnement cible ni une qualification runtime.

GetUnitByPIDFD est ajouté en v253 et absent de dbus-manager.c v252. Premier
profil candidat : Debian13/systemd257, busctl257, dbus-daemon natif, cgroupv2,
root, mêmes namespaces PID/montage que PID1 et socket locale protégée. Pas de
support de repli Debian12/v252. Version seule insuffisante : les appels et le
passage de FD devront effectivement fonctionner ; indisponibilité = refus fermé.

## Entrées proposées, aucune recherche implicite de PID

Le futur lecteur reçoit 1 à 128 couples privés (objet de l'index, PID candidat).
Le PID entier strict est compris entre 2 et 2^31-1 ; ni booléen, PID0/sender,
chemin procfs fourni, FD extérieur ou identifiant d'invocation fourni n'est accepté.
Pas de doublon de PID ou d'objet. Le nom primaire vient de la première ListUnits
validée, jamais d'une résolution libre ou d'un basename de fichier installé.

Ces PID sont des indices déclarés, pas des preuves d'identité ou d'exhaustivité.
La méthode renvoyée par le manager doit confirmer exactement le nom primaire et
le chemin nommé de l'objet sélectionné. Le chemin nommé reçu est seulement comparé
à l'index, **jamais utilisé pour un appel de propriété**. Mauvais PID, objet absent,
liaison différente ou ID nul/invalide annulent la collecte, sans autre candidat essayé.

Le PID numérique peut avoir été réutilisé avant pidfd_open : ce contrat ne le
relie donc pas à une ancienne observation de processus. Il identifie la cible du
FD nouvellement ouvert et sa liaison actuelle vérifiée. Pas de fait UID/GID,
start_ticks, exécutable ou ProcessBinding effectif déduit de ce seul résultat.
Le raccordement à un recensement de PID de confiance constitue un chantier distinct.

Les unités non sélectionnées, sans PID fourni ou sans processus utilisable,
notamment timers, paths et services inactifs, restent explicitement inconnues.
Ne pas démarrer de processus témoin, charger l'unité ou lire son InvocationID par
nom pour compléter cette couverture. Un manque connu n'est pas une liste vide
réputée exhaustive. Aucune conclusion d'absence de producteur n'en découle.

## Descripteurs et courses

Le futur lecteur ouvre lui-même les PIDFD avec pidfd_open(pid, 0). Ils sont
CLOEXEC, conservés entre les deux passages et fermés dans tous les chemins de
sortie. Maximum 128 FD possédés ; pas de hausse de RLIMIT ni de descripteur fourni
par l'appelant. Le seul FD hérité par chaque enfant busctl est celui de l'appel
GetUnitByPIDFD correspondant ; aucun FD supplémentaire pour les autres appels.
stdin/stderr fermés, environnement reconstruit, close_fds et adresse fixe restent requis.

Contrôle non bloquant des notifications du PIDFD avant/après chaque liaison,
après les détails et avant la clôture. Notification de fin, erreur ou FD invalide
= refus ; jamais de signal au processus observé, attente de sa fin ou gel de l'unité.
Le FD référence le processus noyau, sans maintenir l'unité chargée et sans l'empêcher
de sortir. C'est distinct d'un Ref/RefUnit ou d'une garde d'activation.

La source pidref.c peut abandonner sa copie du FD en cas d'épuisement de
ressources et pidref_verify peut alors retourner zéro. Le retour positif de la
méthode ne suffit donc pas à affirmer que le manager a conservé le FD. Garder et
surveiller le PIDFD local, comparer les deux liaisons exactes ; ne pas prétendre
attester le chemin interne du manager ni éliminer toute migration de cgroup.
Le résultat est ponctuel, non atomique : pas de garantie qu'une configuration,
un exécutable ou une appartenance n'a pas changé puis retrouvé sa valeur.

## Appels, signatures et ordre fermés du prochain lot

Deux tours du transport existant encadrent deux passages sur les couples sélectionnés.
Un passage par couple contient exactement :

1. GetUnitByPIDFD, entrée h, retour osay : chemin nommé, nom primaire, tableau de 16 octets.
2. GetUnitByInvocationID, entrée ay de ces 16 octets non tous nuls, retour o.
3. Properties.Get(Unit, Id) sur ce seul chemin d'invocation, retour v de type s.
4. Properties.Get(Unit, InvocationID) sur ce même chemin, retour v de type ay.

Les deux couples de propriétés et les deux réponses de liaison doivent coïncider.
La propriété Id égale le nom primaire listé ; InvocationID égale les 16 octets
obtenus par PIDFD. Chemin renvoyé, identité du bus/manager, provenance et les trois
listes doivent aussi rester compatibles et inchangés selon leurs contrats.
Aucune référence, abonnement, fonction de chargement, signal ou mutation systemd.

JSON busctl attendu d'après la source : retour osay avec data de trois éléments,
retour o avec un élément, variantes {type,data}. Les octets sont des entiers stricts
0..255, jamais booléens, sur exactement 16 positions. JSON dupliqué, clé étrangère,
NaN, mauvaise arité/type, troncature ou erreur : refus sans conserver le texte brut.
Ce format sera vérifié sur le bus réel avant de le déclarer supporté.

L'ID est rendu en 32 caractères hexadécimaux minuscules. Son chemin doit égaler
`/org/freedesktop/systemd1/unit/` suivi de bus_label_escape(ID). Si le premier
caractère est 0..9, il devient _30.._39 ; a..f reste inchangé. Exemples :
`a111...` conserve a ; `1111...` commence par _31 ; `0aaa...` commence par _30.
ID tout nul, casse non canonique, suffixe d'unité, self, échappement surnuméraire,
segment supplémentaire ou chemin nommé sont rejetés avant toute propriété.
Ne pas se contenter d'un motif D-Bus générique acceptant noms et invocations.

## Budgets et confidentialité

| Budget proposé | Limite |
| --- | --- |
| Couples / PIDFD gardés | 128 / 128 |
| Appels réservés | 24 + 8 × N, maximum 1048 |
| Réponse / total reçu | 2 Mio / 8 Mio, plafonds pendant lecture |
| Appel / collecte | 5 s / 60 s, budget partagé sans retry |
| Enveloppe privée complète | 4 Mio, index et liaisons inclus |

Le transport de listes seul reste à 24 appels. Pas d'extension arbitraire de sa
liste de commandes ; nouvelle réservation finie et seules propriétés ci-dessus.
Les limites cliente ne sont pas un watchdog du travail interne du manager ou
d'un noyau bloqué. Nettoyage de l'enfant busctl borné comme auparavant (<=1 s
supplémentaire), fermeture de tous les FD locaux sans agir sur les observés.
Le rapport public n'expose ni PID, nom, chemin ou ID d'invocation. Le reçu privé
reste sans signature d'autorité, admission au drain ou preuve de writer.

## Alternatives examinées et non retenues pour cette première étape

- Properties.Get nommé après ListUnits/GetUnit : course de rechargement déjà démontrée.
- RefUnit : mutation et GENERIC_UNIT_LOAD dans la source ; pas une lecture sans chargement.
- GetUnitProcesses sur le manager : sa route flags=0 ne charge pas activement,
  mais produit les lignes de commande et parcourt récursivement les cgroups.
  Non retenu pour obtenir un simple PID dans ce premier périmètre léger ; pas de
  GetProcesses sur un objet nommé, qui réintroduirait le défaut de résolution.
- GetUnitByPID ou GetUnitByControlGroup : renvoient le chemin nommé, sans résoudre
  seuls l'acquisition sûre de l'ID ; pas de fallback à ces chemins.
- Dump, environnement, journaux ou xattrs de cgroups : aucune acquisition par ces
  canaux n'est proposée ou attestée ici. Pas de nouveau balayage hôte implicite.

## Qualification future et arrêt courant

Les seize cas du JSON sont un plan non exécuté : liaison réelle, format/échappement,
refus de l'ID nul, FD inaccessible/invalide, liaison étrangère, version absente,
sortie/recyclage, remplacement d'invocation, unité retirée et **non rechargée**,
provenance, limites, confidentialité, FD non divulgués/nettoyés, couverture inconnue,
et propriétés modifiées entre lectures. Le contrôle de non-rechargement doit
observer ListUnits avant/après le refus, sans stop/reload dans le callback du
second tour comme dans le prototype rejeté. Conserver la preuve négative originale.

Ce lot documentaire vérifie 214 fichiers, les empreintes/ancres primaires,
cohérence de la matrice et des budgets, catalogue Web et identité de tous les
fichiers hors docs avec d86e406a. Core 787 détectables/782 requis inchangés ;
aucune recette runtime relancée. Toutes les Quality globales restent différées.

Prochain chantier unique : implémenter et qualifier la liaison minimale proposée,
tests locaux d'abord, puis un seul job Debian13 ciblé si les précontrôles sont bons.
Pas encore de Names, relations ou identités effectives. La réparation du collecteur
fermé systemctl show et le raccordement de la source des PID restent distincts ;
ses anciens résultats ne prouvent pas le contrat sans chargement en cas de disparition.
Checkpoint puis arrêt. Phase5, inventaire des neuf groupes, backup exhaustif,
upgrade réel, reprise/rollback et orchestration/wizard restent ouverts.
