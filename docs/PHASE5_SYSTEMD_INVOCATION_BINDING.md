# Liaison privée PIDFD / invocation systemd

## Extension ultérieure bornée

Le [lot noms/relations](PHASE5_SYSTEMD_INVOCATION_RELATIONS.md) ajoute un lecteur
séparé avec neuf propriétés par passage. Le présent lecteur minimal conserve
son API, ses propriétés et 24+8N appels ; son parcours reste testé. Les sections
suivantes décrivent la qualification du lot parent, pas celle du nouvel arbre.

## Périmètre livré sur le contrat ff13729f

`installer/systemd_invocation.py` implémente uniquement la liaison minimale
Id/InvocationID du [contrat de source](PHASE5_SYSTEMD_INVOCATION_CONTRACT.md).
`SystemdInvocationTransport(target, storage).collect(hints)` reçoit un tuple de
1 à 128 `InvocationHint(object_path, pid)` privés, sans doublon. Chemin issu de
ListUnits, PID entier strict 2..2^31-1 ; aucune découverte implicite de PID.
Les déclarations de cible/stockage ne prouvent pas le Web ni le stockage réel.

Les deux tours de listes/provenance encadrent deux passages de quatre appels
par couple. PIDFD ouvert localement avec flags=0, CLOEXEC vérifié, gardé entre
passages ; GetUnitByPIDFD confirme le chemin **nommé** et le nom primaire listés.
Ces deux chaînes sont comparées seulement. L'ID non nul reçu donne le chemin
canonique, confirmé par GetUnitByInvocationID, utilisé pour Id et InvocationID.
L'échappement du premier chiffre est obligatoire. Aucun chemin nommé n'est
utilisé pour une propriété d'unité, même comme solution de repli.

L'index complet, provenance locale, propriétaire du manager, liaison et
propriétés doivent rester identiques. Notifications PIDFD vérifiées sans attente
avant/après la liaison, après ses détails et avant clôture. Tous les FD sont
fermés y compris lors d'une erreur ; seul celui de la méthode PIDFD correspondante
est hérité par busctl. Erreur de fermeture = refus après tentative des autres.
Aucune référence d'unité acquise, mutation systemd ou signal aux observés.

## Profil et limites

Premier profil visé : Debian13, systemd/busctl257, cgroupv2 et dbus-daemon déjà
actif. Le lecteur refuse les autres versions majeures du manager. Le succès des
appels réels reste nécessaire : une chaîne Version ne suffit pas à attester le
support. Précontrôles root, même namespace que PID1, socket/binaire protégés et
broker contrôlé sont ceux du transport des listes. Debian12/v252 est refusé,
sans fallback ; aucun nouveau client ou paquet de bibliothèque D-Bus.

Budget partagé : 24+8N appels (1048 maximum), 128 PIDFD, 2Mio par réponse,
8Mio reçus, 4Mio d'enveloppe, 5s par appel/60s par collecte, aucun retry.
Le transport des listes seul conserve 24 appels. `_capture` hérite au maximum
un FD explicitement choisi ; son défaut reste aucun FD. Limites pendant lecture,
environnement fermé, stdin/stderr neutralisés et nettoyage du client conservés.
Ce ne sont pas des watchdogs du noyau ou du travail interne de systemd.

Le reçu `InvocationSample` conserve l'index et les liaisons privées. Son rapport
public contient des comptes/digests, aucun nom/PID/ID/chemin. Il reste sans
signature d'autorité, admission au drain, statut KNOWN_PROVISIONED ou preuve de
producteur. PID réutilisé avant l'ouverture, migration/reconfiguration puis retour
entre observations, propriétaires multiples et chemin interne de secours PIDref
restent les limites décrites dans le contrat. Pas d'identité d'exécution déduite.

## Vérifications et qualification bornée

21 tests nouveaux : types fermés, ID nul/malformed, chemins non canoniques,
liaison/propriété étrangère, changement entre passages, sélection absente,
version non supportée, erreurs sans retry, fermeture complète, limites et
confidentialité. Deux tests utilisent réellement pidfd_open et des processus
locaux jetables pour la fin de processus et l'héritage sélectif de FD.
Ils ne remplacent pas une preuve de systemd réel ni de recyclage forcé de PID.

Sélection affectée : 176 tests (21 nouveaux +155 existants), sans affaiblissement
des assertions historiques. Baseline core enrichie seulement avec ces nouveaux
IDs ; les cinq tests détectables historiques hors baseline restent inchangés.
Gardes statiques et snapshot octets/modes après gel code/docs.

Un seul job ciblé Debian13 est prévu par `systemd-invocation-validation.yml` :
les mêmes 176 tests puis huit scénarios avec vraies méthodes D-Bus dans un
conteneur officiel minimal, cgroup privé, sans réseau pendant les recettes :

1. Format réel osay/ay, liaison et propriétés sans changement du service.
2. Deux unités distinctes, conservation/fermeture de tous les FD.
3. PID vivant lié à une autre unité : refus avant propriétés.
4. Fin du processus après liaison : refus avant lookup d'invocation.
5. Unité fichier disparue après lookup : premier Id refuse, ListUnits confirme
   l'absence avant/après, le fichier reste présent, aucune recharge par nom.
6. Redémarrage après lookup : ancien chemin refuse ; nouvelle invocation
   observable séparément. Aucun callback de stop répété au second tour.
7. Objet absent de l'index : refus sans requête PIDFD.
8. Privilège root requis avant usage du bus.

La recette est aussi raccordée à la future campagne système, pour Debian13
seulement. Le résultat exact, commit/arbre, source manifestée, environnement,
logs et empreinte d'artefact doivent être vérifiés et conservés dans le checkpoint.
Ce document est gelé avant cette exécution : il n'annonce pas un résultat anticipé.
Pas de qualification globale ou promotion tant que toutes les Quality requises
ne sont pas vertes sur le même arbre. Le run rejeté 36238806917 reste une preuve
négative valide ; son prototype n'est pas réintroduit.

Les seize exigences générales du JSON de conception conservent executed=false
comme campagne globale : leurs vérifications sont ici réparties entre contrôleurs
et huit cas système, sans prétendre avoir forcé un recyclage réel de PID,
validé 128 unités réelles ni simulé tous les épuisements internes de systemd.
Les 28 cas d'inventaire global restent également non qualifiés.

## Arrêt et prochaine limite

Checkpoint puis arrêt après ce seul job. Prochain lot borné : lecture Names et
relations sur l'invocation désormais liée, avec classification conservatrice et
couverture inconnue des unités sans processus. Ne pas déduire des relations une
identité effective ni une adoption. Recensement de PID de confiance, réparation
du collecteur fermé systemctl show et pont vers les producteurs restent distincts.
Aucun raccordement wizard ou indicateur phase5 n'est activé. La phase5, inventaire
des neuf groupes, sauvegarde exhaustive, upgrade réel et rollback restent ouverts.
