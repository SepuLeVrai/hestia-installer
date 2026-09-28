# Phase 5C4 — Reprise et retour arrière de la transition qualifiée

Le profil reste celui de [5C3b](PHASE5C3_STORAGE_UPGRADE.md) : Web historique
scellé géré par l'Installer, SQL local managed, Debian 13, PHP 8.4 et Ext4.
La paire reste `46c0306` → `2a27c7a`, sans migration SQL. Aucun hôte historique
arbitraire n'est adopté. Les preuves de qualification du gel sont au checkpoint
et dans l'issue #13 ; la présence du contrôleur seule ne certifie pas la recette.

## Contrat privé

`StorageUpgrade.recover(payload, authority, config_root=..., backup_root=...,
lease_id=..., direction='forward'|'rollback', confirmed=True,
allow_global_read_lock=True)` reconstruit une opération depuis ses preuves
privées. Un contrôleur neuf suffit ; aucun objet de lease vivant n'est requis.
`observe(backup_root, lease_id)` relit le résultat durable sans action. Il décrit
l'opération passée, pas la disponibilité courante de l'application.

Avant une mutation, la reprise prend un verrou exclusif non bloquant et le
verrou de maintenance, confirme les trois services arrêtés, leurs conditions de
maintenance, le recensement des processus et l'arrêt du timer. Elle reprend les
réservations externes et verrous de configuration, prend le verrou SQL global,
puis compare l'export logique à la sauvegarde vérifiée. Une nouvelle écriture SQL
refuse les deux directions ; aucune restauration SQL ne l'écrase.

Le journal de préparation lie les inodes source/cible/uploads, leurs empreintes,
les configurations exactes avant/après/rollback et la sauvegarde. La reprise
reconnaît les déplacements déjà effectués et les configurations partiellement
basculées. Un fichier, code ou emplacement inconnu provoque un refus. Les fichiers
temporaires de configuration tronqués ne sont réparés que si leurs métadonnées
sont exactes et leurs octets un préfixe de la valeur attendue.

## Retour arrière et réouverture

Le rollback replace l'ancien Web, rétablit ses configurations et reçus, conserve
le Web cible et les uploads déplacés dans des emplacements privés. Il ne restaure
pas SQL et ne supprime pas les données. Avant préparation complète, seul l'abandon
du source resté intact est permis. Une intention de rollback durable interdit
ensuite la reprise en avant ; une nouvelle demande de rollback converge.

`authorize_resume(..., confirmed=True)` reste distinct de l'apply/recover et ne
démarre aucun service. Son intention durable interdit tout rollback avant la
première levée de barrière. Il peut terminer une levée interrompue ou renvoyer
le même résultat après une réponse perdue. Si la maintenance est déjà levée,
les reçus exacts permettent de terminer la réponse sans réexaminer ni écraser les
données qui ont légitimement changé depuis. Le reçu de maintenance tronqué est
repris uniquement s'il est un préfixe exact du reçu attendu et correctement protégé.

Les sauvegardes et arbres conservés ne sont pas purgés automatiquement. Après un
rollback terminé, un nouvel apply distinct nécessite une gestion explicite des
emplacements conservés ; rejouer recover sur l'opération existante est idempotent.
Un journal absent, illisible ou contradictoire reste bloqué pour examen manuel.
La recette SIGKILL cible les frontières durables et deux écritures partielles ;
elle ne prétend pas simuler toute corruption matérielle ou toute coupure disque.

## Qualification et suite

`tests/integration/storage_upgrade_recovery_systemd.py` combine les six scénarios
5C3b et quatorze scénarios de reprise sur quatre groupes indépendants. Vrais SQL,
systemd, FPM, Apache, TLS et uploads ; nouveau contrôleur après SIGKILL. Sont
contrôlés : préparation, trois déplacements, rollback interrompu, configurations
mixtes, réponse apply perdue, levée des données interrompue, reçus tronqués,
concurrence, dérive SQL, configuration inconnue et échec de sauvegarde initiale.
Le parcours de réponse resume perdue crée ensuite une vraie photo et vérifie que
ses octets et sa référence SQL survivent à la répétition et au refus du rollback.

5D reste nécessaire : journal opérateur et wizard, activation des services et
recette complète de bout en bout. Les flags globaux `phase5_complete` et
`application_installed` restent faux. Aucun branchement HTTP n'expose directement
les chemins privés ni les credentials d'autorité de ces contrôleurs.
