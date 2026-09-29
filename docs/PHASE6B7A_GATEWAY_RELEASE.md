# Phase 6B7a — Levée Gateway durable, activité toujours fermée

Ce sous-lot prépare la réouverture composée après la sauvegarde 6B6. Il ajoute
une primitive privée explicite ; il ne livre ni la réouverture Web/Mobile, ni
un redémarrage, ni une restauration sur la cible originale. Aucune route HTTP
ou action automatique n'est ajoutée. Les références Gateway/Web et les ports
9080 / 9082 / 9083 restent inchangés.

`gateway_state_release.release(snapshot, confirmed=True)` exige la barrière
Gateway et le reçu composé Web/Gateway terminés. Avant toute levée, il revérifie
les archives SQLite/cache, leurs empreintes, le reçu Web, le profil de services,
la maintenance exacte et chaque octet des sources Gateway encore arrêtées.
Il ne relance aucun worker SQLite et ne touche pas au SQL Web. Cette vérification
n'est donc pas une nouvelle preuve de cohérence SQL au moment de la réouverture.

L'intention privée `gateway-state.release` lie le journal de gel complet et les
empreintes de sauvegarde. Les flags sont rendus à leur valeur initiale dans
l'ordre inverse, répertoire racine en dernier. Les inodes, noms, permissions,
montages, métadonnées et empreintes restent contrôlés. Le verrou applicatif est
conservé pendant la levée puis libéré avec les descripteurs.

Le reçu `gateway-state.released` est synchronisé **avant** de supprimer les
anciens marqueurs. Il maintient à lui seul le refus de `MaintenanceLease.resume`.
Une interruption pendant les flags, après le reçu ou après chaque suppression
se reprend uniquement par `recover(runtime, barrier, backup_root, confirmed=True)`.
L'absence seule d'un journal ne vaut jamais succès ; une sauvegarde, un inode,
un flag, un verrou ou un bail différent provoque un refus sans démarrage.
Un gel 6B6 ne peut pas être rejoué au milieu ou après cette levée.

Les tests de fichiers isolent les ioctls. La recette native existante est étendue
avec deux SIGKILL réels, la reprise sans worker, la stabilité UUID/cache et le
refus effectif des démarrages sous maintenance. Les APT, SQL, comptes, Ext4 et
services réels restent exclusivement dans la CI Debian jetable.

## Contrat du lot suivant

La composition devra lier les barrières Web/configuration/données/chemins externes,
revérifier les archives et sources ainsi que l'état SQL courant sous verrou de
lecture, puis consommer explicitement le reçu bloquant Gateway. Elle aura son
propre plan/journal ; les journaux d'installation et d'activation initiaux ne
seront pas rejoués. Les démarrages devront reconnaître les invocations déjà
créées après perte de réponse et refuser les cas ambigus. La composition avec
le boot ou un frontal public restera une qualification séparée.

Le marqueur `gateway-state.released` ne doit jamais être supprimé manuellement
pour contourner cette future admission. Aucun endpoint de ce sous-lot ne le retire.
