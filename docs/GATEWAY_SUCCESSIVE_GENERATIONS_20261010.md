# Générations publiques successives - candidat de qualification

État : candidat implémenté, non qualifié nativement à ce stade. Aucun résultat
de ce lot ne doit être déduit des preuves du premier transfert à `24a210d`.

## Dernier gel natif et résultats acquis

Dernier gel natif : `dd4d89af9ee6d5ff62621ba77aba36f194c53b5d`, arbre
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
| Upgrade indépendant | Watchdog HTTP 1800 s | Run `38080901195`, job `114297421605`, artefact `11681289874` |
| Rollback indépendant | Échec de préparation avant transfert | Même run, job `114297421526`, artefact `11680751576` |
| Trois cycles sur le même hôte | Cycle 1 et boot PASS ; reprise du cycle 2 refusée | Même run, job `114297421437`, artefact `11681931422` |

Les artefacts téléchargés sont comparés aux empreintes de GitHub. Les rapports
acquis n'ont ni erreur, ni échec, ni test sauté. Les manifestes des 555 fichiers
et les modes Unix de la source livrée par Quality correspondent au commit.
Le tableau ne constitue pas une qualification globale : les transferts natifs
et leurs redémarrages doivent encore tous réussir.

Le rollback indépendant échoue dans `shared.public.handoff.prepare`, sur un
contrôle strict de l'unité session-cleaner. Le journal montre son démarrage et
sa fin réussie à 19:50:12 UTC, au même instant que le refus
`SYSTEM_DRAIN_UNIT_REJECTED`. L'artefact est conservé. GitHub a refusé la
relance ciblée tant que les autres jobs tournaient ; aucune relance n'a démarré.
Le run est désormais terminé. Les nouveaux défauts ci-dessous sont corrigés
avant une nouvelle recette complète ; ce gel dépassé n'est pas relancé.

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

## Correctif suivant : journaux par bail et audits HTTP composés

Le run `38080901195` est terminé en échec. Le cycle 1 de `dd4d89a` et son
redémarrage passent : six fenêtres SQL, maximum 134,266560 s, deux renouvellements
ACME réels et nouveau PID 1 dans le même noyau. Au cycle 2, la sauvegarde passe
avec le lecteur de profil corrigé ; l'interruption `public-transfer` est observée.
La reprise refuse ensuite `mobile_reopen_files.execute` : l'emplacement commun
`maintenance/mobile-reopen-files` retrouve le journal déjà terminé du cycle 1,
alors que son ancien garde a été consommé. Le cycle 3 n'a pas été exécuté.
L'artefact `11681931422` est authentifié, avec les 555 sources exactes.

Le correctif crée `maintenance/mobile-reopen-files-<lease_id>` pour chaque
nouveau bail. Tous les lecteurs de réouverture externe, données et bloqueurs,
ainsi que l'enveloppe d'archives, utilisent ce même emplacement. Les anciens
journaux restent intacts. Un journal historique à l'ancien chemin est lu
uniquement pour son bail exact ; un historique partiel, ambigu, altéré ou
d'une autre instance est refusé. Le profil est lié au bail et à la sauvegarde.

L'upgrade indépendant de `dd4d89a` échoue au watchdog de 1800 s avant le reçu
HTTP. Sa trace situe le processus dans une relecture d'intégrité HTTP via
SessionCleaner et Foundation ; cinq fenêtres SQL terminées, maximum
171,212366 s. L'artefact `11681289874` est authentifié, sources exactes.
Une duplication démontrée est supprimée : l'audit composé lit la configuration
HTTP une seule fois pour le HTTP et son nettoyeur, avant les observations natives.
Il n'y a aucun cache entre appels, aucune observation native conservée et aucune
limite modifiée. Une comparaison sur fixture de 2 Mio produit les mêmes résultats,
avec 10 lectures complètes au lieu de 20 pour 10 audits. Elle ne qualifie pas
les durées sur le véritable hôte ni la résolution du watchdog.

Quatorze nouveaux tests vérifient notamment les trois journaux distincts,
la compatibilité historique, le refus de mélange des baux/sauvegardes, les
modifications de source et du nettoyeur, et le maintien des refus natifs.
Le registre obligatoire inclut aussi les cinq tests du précédent correctif
qui n'y figuraient pas encore : 2152 tests core attendus. Les campagnes Quality,
Ext4/systemd et la recette complète du nouveau gel restent à recueillir.
Aucun ancien PASS ne qualifie ce correctif.
