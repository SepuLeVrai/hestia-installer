# Phase 6B7b6a — réouverture récupérable de l’accès aux données

## Base et périmètre

Travail isolé sur le candidat 6B7b5b `b7159802a0973d96f8f8a38662b288e932274a3e`,
arbre `51d005dcda179c9afb7600a7bb08329849d570f1`. Au gel documentaire, ses trois
campagnes Installer sont vertes mais sa recette native composée `36820888356`
reste en cours. Il ne constitue pas encore une base entièrement qualifiée.
La dernière base qualifiée est 6B7b5a, `f75be3c2c26d2c8a2d720f9117a16fd26b89e1ab`.
Le candidat précédent reste inchangé pendant ce travail anticipé.

Le module privé `mobile_reopen_data` compose uniquement le changement d’accès au
répertoire canonique des données, après libération des fichiers et réservations
externes. Il ne constitue pas l’admission SQL courante à travers cette transition.
Il ne consomme ni maintenance, ni bloqueur mobile, ni reçu Gateway. Aucun service
n’est démarré et aucune API publique n’est ajoutée. Les anciens tests, StepSpecs,
lecteurs stricts et primitives natives sont conservés octet pour octet.

## Plan et contrôle des identités

`begin` exige le plan externe exact, son check actuel réussi, la fermeture données
exacte sur le même bail et une confirmation explicite. Le plan canonique privé
`data-release-<lease_id>/plan.json` lie l’empreinte du plan externe, ses parents,
le bail, l’identité de la racine de sauvegarde et le profil runtime avec UID/GID.
Une seconde confirmation porte sur l’empreinte du nouveau plan avant son exécution.
L’objet est lié au processus, non sérialisable. Après un crash, l’appelant reprend
le même bail avant `recover`, qui charge sans modifier les permissions ou journaux.

Chaque opération relit le plan externe et ses intention/reçu exacts, les treize
fichiers du plan fichiers, son état DONE et les deux bloqueurs. Le répertoire
données doit conserver périphérique, inode, propriétaire, groupe et absence d’ACL.
Son marqueur est comparé à l’original conservé. Les protections de fichiers et
réservations externes doivent être absentes ; les profils boot/public sont refusés.
Les deux verrous exclusifs de configuration qualifiés sont tenus pendant l’effet ;
un lecteur partagé encore ouvert empêche l’opération.

Le lecteur historique du plan externe exige toujours les données fermées. Après
le changement, le nouveau module lit ses preuves historiques exactes sans appeler
ce lecteur vivant devenu inapplicable, sans le modifier ni fabriquer une capacité.
Ces preuves persistées ne sont pas une autorisation SQL actuelle.

## Coupures et reprise explicite

L’intention propre au nouveau plan est créée exclusivement et fsync avant tout
effet natif. Apply exige 0700 avec le marqueur original et aucune intention propre.
Resume exige cette intention exacte ; check exige aussi le reçu propre terminé.

| État observé sous intention exacte | Traitement de resume |
| --- | --- |
| 0700 + marqueur original | Récupération native de fermeture puis réouverture native |
| 0750 + marqueur original | Refermeture native explicite à 0700, contrôles runtime/processus, puis réouverture native |
| 0750 sans marqueur | Vérifications actuelles puis rapprochement de l’effet, sans refermeture ni répétition |
| 0700 sans marqueur, inode ou contenu étranger | Refus, aucune reconstruction |

Le cas 0750 + marqueur demeure refusé par `data_access.expected_mode` avec
`DATA_ACCESS_INCOMPLETE`. Le nouveau module ne contourne pas cette règle : la
reprise explicite utilise `data_access.recover`, qui remet d’abord la fermeture
en cohérence. Si un processus de l’identité applicative est présent, elle refuse
en laissant les données à 0700. Aucun signal n’est envoyé à ce processus.

Après disparition du marqueur, une dérive ou un processus étranger interdit le
reçu, mais ne déclenche pas une nouvelle fermeture implicite. L’accès peut donc
être déjà ouvert en cas d’échec ; la maintenance et les bloqueurs restent présents.
Ce résultat ne doit jamais être présenté comme un état données fermé.

L’effet terminé est fsync avant le reçu propre, y compris après perte de réponse
entre unlink natif et fsync. Un reçu présent n’autorise aucun nouvel effet natif.
Check reste sans écriture. Les journaux partiels, inconnus, liés, altérés ou absents
sont conservés et refusés ; aucune réparation silencieuse ni effacement n’est prévu.

## Qualification et suite

Vingt nouveaux tests de fichiers ciblent Ext4, flock, permissions, recensement de
processus UID/GID et deux SIGKILL réels, après chmod et après unlink. Les audits
système/Gateway de la fixture restent isolés : ces tests ne prétendent pas être
une recette SQL native composée. Quatre tests purs vérifient le refus des entrées
avant toute lecture native. Tous sont ajoutés à la baseline obligatoire.

Les trois campagnes Installer doivent vérifier un même gel source. Les tests
SQL, comptes, systemd et Ext4 réels s’exécutent exclusivement en CI jetable.
La documentation est figée avant CI ; les verdicts, identités et preuves finales
seront consignés dans les trois issues de suivi et la livraison.

Avant le raccordement natif suivant : terminer la qualification 6B7b5b, puis tenir
une nouvelle admission SQL et les vérifications actuelles des archives/fichiers
pendant la transition données, avec les seuls écarts de journaux exactement liés.
Ne pas réutiliser la fenêtre 6B7b5b après changement : elle exige encore 0700.
La consommation des bloqueurs, les starts dédupliqués, boot/restauration originale,
DEV/FCM, l’assistant et la recette 6C restent des étapes séparées. Phase 6 ouverte.

## Correction de l’instrumentation avant le second gel

Le premier candidat `9e01c9180480ca2c83ab5420296e8d53773edf13` a exécuté
1 479 tests cœur par Debian, avec un unique échec identique : l’observateur global
de fchmod comptait aussi le chmod 0600 du reçu après les deux transitions attendues
du répertoire données. Le test cible désormais l’identité périphérique/inode exacte
de ce répertoire et conserve l’assertion 0700 puis 0750. Aucun comportement de
production, test historique ou contrôle attendu n’est changé. Les sept artefacts
du premier essai sont conservés. Le nouveau gel requiert à nouveau les trois
campagnes Installer sur sa propre identité source.
