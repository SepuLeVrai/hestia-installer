# Phase 6B7b5a - libération récupérable des réservations externes

## Base et périmètre

Base qualifiée 6B7b4 : Installer `68f7ed5a0bd1e0368dbd0768f2eed31ad24a0eea`,
arbre `d5566c0651b1619ba7872fdd8b5b37aab670ba6f`. Les verdicts définitifs de cette
base sont dans les suivis des dépôts et sa livraison ; sa documentation conservait
le statut du gel antérieur aux tests.

Le module privé `mobile_reopen_external` compose la seule libération des deux
réservations externes après le plan fichiers terminé. Il ne constitue pas encore
la transition depuis la fenêtre SQL vivante 6B7b4. Il ne rouvre pas les données,
ne consomme aucun bloqueur et ne démarre aucun service. Aucune route ou commande
publique n'est ajoutée. Les anciens lecteurs, StepSpecs et tests restent inchangés.

## Plan, verrous et preuves

`begin` exige le ReopenFilesPlan exact et son check DONE actuel. Il sauvegarde un
plan canonique privé dans `external-release-<lease_id>`, sous la racine de sauvegarde
existante, hors configuration vivante. Le plan lie les deux inodes de verrous de
configuration, leur contenu haché, le bail, l'identité de la racine de sauvegarde,
le plan fichiers et ses treize fichiers parents, ainsi que les bloqueurs mobile
et Gateway. Une nouvelle confirmation doit porter sur l'empreinte de ce plan.

L'appelant ferme normalement son ancien contexte ConfigurationLease avant apply.
L'exécution acquiert les verrous exclusifs non bloquants de `assistant-edit.lock`
et `assistant.json`, vérifie leurs identités et contenus et les garde pendant tout
l'effet. Un autre lecteur partagé, y compris celui du même processus, empêche donc
la libération. Charger le plan avec recover ne contourne pas cette condition.
Aucun ancien objet ConfigurationLease n'est fabriqué ou fermé par le nouveau module.

Avant l'effet et après celui-ci, les parents exacts et les bloqueurs sont relus ;
le journal d'accès aux données et son inode canonique doivent toujours correspondre
à la même fermeture 0700. Les marqueurs des trois protections de fichiers doivent
rester absents. Le plan parent reste immuable : aucune nouvelle StepSpec ne lui est
ajoutée et son intention mobile conserve son empreinte initiale.

## Coupures et reprise

Une intention externe propre au nouveau plan est écrite et fsync avant la première
mutation. Le lecteur bas niveau existant effectue la libération. Ses journaux sont
comparés octet pour octet aux originaux préservés ; toutes les réservations sont
inspectées avant de toucher la première. Une cible étrangère est conservée et refusée.

- Avec RELEASE présent, la reprise native existante termine les retraits.
- Avec seulement le marqueur, la réservation complète doit encore être fermée.
- Sans aucun journal natif, seule l'intention externe exacte permet de réconcilier
  l'effet ; les deux cibles et leurs stages doivent être absents sous les parents liés.

Le reçu final est exclusif et ne remplace aucun contenu existant. Sa présence est
historique : check relit l'état actuel et ne modifie aucun journal. Apply refuse
une intention déjà présente ; resume exige cette intention et peut réconcilier une
réponse perdue après écriture du reçu. Un fichier tronqué, lié, étranger, manquant
ou une identité modifiée ne déclenche ni réparation implicite ni nouvelle fermeture.
Les objets ne se sérialisent pas et ne traversent pas un fork. Après un crash,
l'appelant doit reprendre le même bail avant de charger le plan persistant.

## Qualification et limites explicites

Dix-huit nouveaux tests de fichiers utilisent Ext4, flock, unlink, permissions et
SIGKILL réels dans les jobs Debian 12 et 13 jetables. Les audits de services et
Gateway restent isolés par la fixture du sous-plan fichiers. Les coupures couvrent
l'intention externe, le retrait du flag, les unlink cible/stage/MARKER/RELEASE et
l'écriture du reçu. Les tests couvrent aussi la conservation des parents, le refus
d'un lecteur de configuration encore ouvert, la confirmation, les fichiers étrangers,
la corruption, les hardlinks, la dérive de mode données et le check sans écriture.
Quatre tests de politique pure contrôlent les entrées avant toute lecture native.

Les trois campagnes Installer (Quality, système et paquets) doivent porter le même
commit figé. Aucun test SQL, compte, systemd ou Ext4 réel n'est exécuté dans Work,
LAB ou PROD. Ce lot ne prétend pas rejouer ni étendre la recette native 6B7b4 :
la composition SQL/configuration/réservation reste à qualifier dans le prochain lot.
Cette documentation est gelée avant CI ; les SHA, résultats et preuves définitifs
sont consignés dans les issues et la livraison.

## Prochaine frontière

Raccorder cette primitive à une nouvelle admission native composée : refaire la
preuve SQL courante, fermer l'ancien contexte de configuration, tenir les verrous
adaptés pendant la transition puis réacquérir le contexte sans réservations. Adapter
la lecture de l'enveloppe uniquement aux changements exactement journalisés ; ne pas
élargir les exclusions de maintenance. La réouverture récupérable des données vient
ensuite. Les starts requièrent leurs propres intentions et reçus dédupliqués.
Phase 6, boot, restauration originale, DEV/FCM et recette 6C restent ouverts.
