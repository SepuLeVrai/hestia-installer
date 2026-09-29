# Phase 6B6 — Sauvegarde SQLite Gateway sous maintenance

Ce candidat prolonge le gel Installer 6B5 `5cc9e36e8e493e909cb83d8423b24a278f275239`.
Il ajoute la sauvegarde et une restauration isolée de l'état Gateway à la sauvegarde Web.
La qualification native reste à obtenir. Ce lot ne rouvre pas l'activité et n'active pas le boot.

## Contrat

La maintenance ferme d'abord les entrées Web, puis arrête Gateway, Foundation et les producteurs Web acquis.
La nouvelle barrière exige le runtime exact lié au profil de drainage, le compte Gateway exclusif,
les inodes d'état attendus, un répertoire privé sans ACL et un Ext4 pris en charge.
Elle refuse tout fichier inattendu, lien symbolique, hardlink, montage imbriqué, verrou détenu,
dérive de métadonnées ou drapeau immutable sans journal possédé.

Une intention durable `gateway-state.attempt` précède le gel du répertoire et des fichiers
`gateway.db`, `gateway.lock`, et des éventuels `gateway.db-wal` / `gateway.db-shm`.
Le verrou applicatif est détenu pendant la capture et la sauvegarde Web.
Les drapeaux immutable protègent aussi les écritures ordinaires root, anciens descripteurs
et alias de montage. L'administration qui retire ces drapeaux, modifie les montages ou
écrit sur le périphérique brut reste hors de cette barrière.
Les drapeaux et la maintenance survivent à la fermeture du contrôleur ou à sa mort.

Le journal et les inodes exacts permettent une reprise explicite du gel partiel après SIGKILL.
Un identifiant de maintenance différent ou un inode remplacé est refusé. Le nouveau paramètre
privé `recover_lease_id` reprend uniquement une sauvegarde sous cette même maintenance.
Il ne lève aucune barrière, ne reprend pas une migration et ne démarre aucun service.
Un snapshot complet se réconcilie sans nouvelle copie ni nouvelle exécution SQLite ;
un snapshot incomplet reste à examiner, sans écrasement automatique.

## Copie et restauration

Aucune connexion SQLite n'ouvre l'état original. Le snapshot physique privé conserve le WAL,
qui peut contenir des transactions validées absentes du fichier principal. Le SHM est conservé
comme preuve physique mais reconstruit sur la copie isolée, sans devenir une source de vérité.

Le worker de vérification utilise le compte privé non privilégié déjà provisionné, distinct
du Web et de Gateway. Il s'exécute sans réseau (`unshare --net`), sans groupes supplémentaires,
sans capabilities, avec no-new-privileges et des limites de temps, mémoire, fichiers et processus.
Son protocole et ses noms de fichiers sont fermés. Aucun secret n'est transmis dans les arguments.
Les erreurs SQLite et le contenu des lignes ne sont jamais renvoyés dans les rapports.

La copie doit passer quick_check, foreign_key_check, les six checksums de migration du
Gateway qualifié et l'identité UUID v4. Un digest logique typé couvre le schéma et toutes les lignes.
L'API backup SQLite produit une image autonome privée. Une seconde restauration isolée relit
l'image sauvegardée et vérifie le même contenu logique et le même UUID.
Le code Installer n'exécute aucune migration Gateway et n'invente aucun UUID.

Le snapshot est lié au bail, au profil Gateway, au gel des inodes et aux empreintes des fichiers.
Le reçu composé exige également le reçu Web exact et son manifeste vérifié sur disque.
Un échec Web ne devient jamais un succès Mobile. La capture SQLite précède le verrou SQL Web :
sa limite de 180 secondes demeure inchangée.

## Limites et acquis

Les fichiers source sont limités à 512 MiB au total. Le worker accepte au plus 2 millions de lignes,
128 entrées de schéma et 64 colonnes par table ; les dépassements sont des refus explicites.
Le profil reste MAIN sur 9083, Foundation MAIN sur 9082 et Web sur 9080.
Gateway reste `e2c09f53593bf316906ccc4387f185e73e7f85a8`, SQLite 6, et le Web exécuté reste
`2a27c7a1f9fe0a00289eb53278f75d5f230900b7`.

La restauration sur l'instance originale, la réouverture composée, le boot, DEV natif,
FCM et NGINX Mobile/TLS nécessitent leurs prochains plans et preuves. Le marqueur Gateway
est ajouté au refus de `MaintenanceLease.resume` ; les anciens journaux et bundles restent intacts.
La recette native doit vérifier un SIGKILL pendant le gel, sa reprise explicite, l'UUID,
la restauration réelle, les refus d'écriture et le maintien des services arrêtés.

Références techniques : [WAL SQLite](https://sqlite.org/wal.html),
[API backup SQLite](https://sqlite.org/backup.html).
