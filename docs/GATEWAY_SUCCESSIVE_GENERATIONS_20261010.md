# Générations publiques successives - candidat de qualification

État : implémentation en cours de qualification. Aucun résultat natif de ce lot
ne doit être déduit des preuves du premier transfert à `24a210d`.

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
du parent, ses fragments, sa publication et son bail. Les huit fragments cibles
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

Ce lot ne qualifie pas DEV/FCM, une restauration sur l'origine, un redémarrage
du noyau ou la clôture globale de phase 6. #17 et #18 restent ouverts.
