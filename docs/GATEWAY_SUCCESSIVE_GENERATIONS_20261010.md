# Générations publiques successives - candidat de qualification

État : candidat implémenté, non qualifié nativement à ce stade. Aucun résultat
de ce lot ne doit être déduit des preuves du premier transfert à `24a210d`.

## Gel et résultats acquis

Candidat : `dd4d89af9ee6d5ff62621ba77aba36f194c53b5d`, arbre
`6739bfa15369c9ed18671da702f108dba70fe336`, 555 fichiers. Branche
`validation/phase6-public-generations-20261010`. Appelant Web :
`c826a48bbe62e9e0121a13ba654496a1416e2599`, épinglé sur le candidat exact.

| Campagne sur ce candidat | Résultat | Preuve |
| --- | --- | --- |
| Core Debian 12 | PASS, 2138 tests | Artefact `11680083676` |
| Core Debian 13 | PASS, 2138 tests | Artefact `11680421936` |
| Navigateur bridge et HTTPS | PASS, 38 et 50 tests | Artefact `11680696567` |
| Quality finale | PASS | Run `38080884154`, source complète `11679889328` |
| Protocole public | PASS, 109 tests | Run `38080884162`, artefact `11680761073` |
| Contrats systemd natifs | PASS, 10 tests | Même run, artefact `11680073329` |
| Upgrade indépendant | En cours, aucun verdict | Run `38080901195`, job `114297421605` |
| Rollback indépendant | Échec de préparation avant transfert | Même run, job `114297421526`, artefact `11680751576` |
| Trois cycles sur le même hôte | Premier upgrade en cours | Même run, job `114297421437` |

Les artefacts téléchargés sont comparés aux empreintes de GitHub. Les rapports
acquis n'ont ni erreur, ni échec, ni test sauté. Les manifestes des 555 fichiers
et les modes Unix de la source livrée par Quality correspondent au commit.
Le tableau ne constitue pas une qualification globale : les transferts natifs
et leurs redémarrages doivent encore tous réussir.

Le rollback indépendant échoue dans `shared.public.handoff.prepare`, sur un
contrôle strict de l'unité session-cleaner. Le journal montre son démarrage et
sa fin réussie à 19:50:12 UTC, au même instant que le refus
`SYSTEM_DRAIN_UNIT_REJECTED`. L'artefact est conservé. GitHub a refusé la
relance ciblée tant que les autres jobs tournent ; aucune relance n'a démarré.

Le périmètre reste MAIN avec SharedPublic v1 et MobileBoot historiques, sans
DEV ni FCM actif. Les deux paquets du catalogue restent les seules cibles.
La chaîne est bornée à 64 générations publiques ; le dépassement est refusé.

## Passage explicite au cycle suivant

Après une exécution publique complète, le cockpit propose un nouveau cycle.
Le POST `/api/gateway/transition/execution/next` exige la confirmation exacte
de l'exécution terminée et `confirm: true`. Sous le verrou du journal parent,
il vérifie le paquet, l'admission consommée, les services et le frontal courant,
puis écrit un lien `next.json` immuable. Il ne déclenche pas la sauvegarde.

Le nouveau cycle possède un journal, un import de paquet et une sauvegarde
distincts. Sa source est la cible publiée du cycle précédent. Avant de prendre
le nouveau bail, la sauvegarde vérifie de nouveau cette génération et son
ouverture consommée. Les anciens baux, journaux, paquets et sauvegardes restent
conservés. Les requêtes GET suivent seulement les liens de métadonnées ; elles
ne vérifient pas les processus et ne démarrent aucun service.

## Publications et sélection publique

L'enrôlement `gateway-service/staged.json` et la première publication restent
immuables. Une publication suivante occupe le répertoire privé
`control/publications/<empreinte-publication-précédente>/`. Elle lie exactement
le manifeste source, le binaire précédent et ses inodes, le nouveau bail et la
preuve de bascule. Les inodes de `gateway.db` et `gateway.lock` sont conservés.
Un enfant incomplet ferme les lecteurs ordinaires. Seul le producteur sous le
bail d'origine peut reprendre cet enfant à partir de son parent épinglé.

Le pointeur public initial reste conservé. Chaque successeur ajoute un lien
dans `public/shared/private/gateway-generations/`, nommé par l'empreinte de la
génération précédente. Le profil v2 épingle les reçus d'activation et d'ouverture
du parent, son plan d'admission et son reçu de consommation, ses fragments,
sa publication et son bail. Ces reçus privés sont relus dans leur emplacement
d'origine, sans réactiver une ancienne autorité. Les huit fragments cibles
référencent le nouveau worker ; le calendrier de renouvellement est inchangé.

L'enveloppe de configuration HTTP peut observer les anciens fragments pendant
la bascule Gateway. Cette lecture ne donne aucune autorité d'admission : les
workers, ouvertures et admissions exigent toujours la publication courante,
les fragments installés exacts et les preuves natives correspondantes.

## Recette demandée

Le workflow composé conserve les deux recettes indépendantes du premier
transfert. L'option `include_cycles` ajoute un troisième hôte jetable Debian 13,
avec le parcours 0.12.2 -> 0.12.3 -> 0.12.2 -> 0.12.3 sur le même système.

Chaque cycle comporte une sauvegarde vérifiée, un import explicite, un SIGKILL
après un effet natif mais avant son reçu cockpit, une reprise, les contrôles
Web/Mobile et la conservation des clés, UUID et anciens fichiers. Les coupures
successives visent publication, transfert public et ouverture publique.
Un nouveau PID 1, dans le même noyau, suit chaque cycle ; les deux certificats
sont renouvelés réellement et le worker Mobile est rappelé sans rejeu de start.

Les fenêtres SQL restent strictement limitées à 180 secondes. La limite du job
de recette à trois cycles est de 150 minutes ; les deux jobs à un cycle gardent
leur limite de 100 minutes. Le watchdog d'une coupure reste à 1800 secondes.
Les manifestes et résultats sont produits sur les sources exactes testées.

La recette expose six étapes distinctes dans Actions : transfert puis
redémarrage pour chacun des trois cycles. Le même conteneur et les mêmes
volumes sont conservés entre ces étapes. Un échec conserve les preuves déjà
produites et empêche l'exécution des cycles suivants.

## Diagnostic intermédiaire

Sur `f59494e`, le premier upgrade indépendant est PASS ; le rollback indépendant
échoue au watchdog et le premier cycle à sa reprise explicite. La correction de
fixture navigateur et les sceaux d'admission sont inclus dans `20a5e75`.

Sur `20a5e75`, le premier cycle et son redémarrage sont PASS, avec deux
renouvellements réels. Le deuxième cycle refuse sa sauvegarde préalable.
L'upgrade indépendant passe également ; le rollback indépendant échoue une
nouvelle fois au watchdog de 1800 s avant le reçu de démarrage HTTP. Ses six
fenêtres SQL terminées restent inférieures à 180 s, maximum 167,770744 s.
Les artefacts `11680566349`, `11680401164` et `11680184074` conservent ces trois
résultats sur la source exacte `20a5e75`. Aucun de leurs PASS ne qualifie `dd4d89a`.
Le lecteur de petits reçus y était utilisé pour un profil complet de génération
qui dépasse sa limite de 64 Kio. La correction utilise le lecteur privé borné
du profil de génération, conserve sa grammaire et son empreinte, puis vérifie
l'ouverture courante avant toute acquisition du nouveau bail. Le chemin de
l'archive précédente est déduit de son ascendance figée.

Les tests couvrent le profil réel supérieur à 64 Kio, l'altération du profil,
les permissions privées, l'archive distincte du troisième cycle et le refus
avant toute nouvelle maintenance. Les exceptions natives produisent désormais
une chaîne de codes et de positions, sans arguments ni données locales.
Ces tests ne remplacent pas la recette native complète, qui reste à valider.

Ce lot ne qualifie pas DEV/FCM, une restauration sur l'origine, un redémarrage
du noyau ou la clôture globale de phase 6. #17 et #18 restent ouverts.
