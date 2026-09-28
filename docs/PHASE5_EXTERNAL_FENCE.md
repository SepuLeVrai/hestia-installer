# Réservations durables des chemins IA externes

Base qualifiée : Installer `c54ff0367d7ddf07de9b6c6a76373e642e96176d`,
arbre `c16b448f0c7a3566c51138cfd6c73fe28b5d061b`, campagne `36383437417`.
Ce lot ferme la possibilité de créer les deux anciens stockages IA pendant
la fenêtre de sauvegarde du profil provisionné.

## Profil exact

Deux noms fixes seulement : `/etc/hestia/conf_db_ia.php` et `/var/lib/hestia-ai`.
Leurs parents doivent exister, être root:root 0755, sans ancêtre inscriptible ni
ACL, sur Ext4 en lecture/écriture. Les plateformes/capacités sont celles du lot
inodes. Le produit ne monte, ne crée et ne convertit aucun volume. L'absence
d'un parent est un refus de profil, sans création implicite de répertoire hôte.

Toute occupation initiale, même fichier vide, répertoire vide, lien ou fichier
PHP, est refusée avant préparation. Aucun contenu ancien n'est lu ou exécuté.
Les seuls objets créés sont des fichiers réguliers vides root:root 0000.
À la place du répertoire historique, un fichier bloque également toute création
de descendants. Ces objets sont des réservations de maintenance, pas une
configuration IA ni des données métier. Leurs parents restent utilisables.

## Préparation, publication et protection

`external-paths.prepare`, root:root 0600 dans la maintenance, lie d'abord la
lease, l'instance, un nonce, les deux parents et deux noms de préparation uniques.
Deux fichiers vides sont créés exclusivement dans leurs parents respectifs,
puis synchronisés. `external-paths.attempt` enregistre leurs inodes avant toute
publication. L'intention de préparation n'est retirée qu'après ce journal durable.

Chaque nom final est publié par un lien dur exclusif vers son inode préparé,
dans le même répertoire et le même montage. Un nom existant n'est jamais
remplacé. Les seuls liens multiples admis sont exactement ces deux noms privés
du même inode, contrôlés par le journal ; aucun lien étranger n'est adopté.
Le flag immutable est posé, synchronisé puis relu. Une valeur déjà conforme
est synchronisée sans réappliquer le flag, selon le correctif du lot Web.

L'intention de préparation autorise uniquement la reprise de ses noms aléatoires
vides, root 0000 et à un seul lien, avant publication. La reprise d'un journal
complet exige les inodes enregistrés, leurs deux noms attendus, les parents,
propriétaires, modes et flags d'origine exacts. Une collision ou dérive ferme
le parcours sans réparation, suppression étrangère ou reçu de sauvegarde.

Les contrôles utilisent des descripteurs de répertoires, sans suivre les liens.
Journaux bornés à 8192 octets, exactement deux réservations, aucun parcours
d'arbre arbitraire. Les identifiants et noms restent dans les preuves privées.
Les erreurs publiques sont des codes fermés, sans détails système ou secrets.

## Raccordement et reprise

Le coordinateur acquiert les réservations après les protections des données,
du slot et du Web, avant le verrou SQL, la réservation du backup et la copie.
L'admission de configuration lie ensuite leur journal durable à la même
maintenance et le revérifie, y compris à la sortie du contexte. Fermer le handle
ne retire aucune protection. Une seconde instance ne peut adopter ces noms.

Le manifeste V8 ajoute `external_path_reservations`. Le reçu ajoute
`legacy_external_paths_reserved`, `ordinary_root_legacy_path_writes_fenced` et
l'empreinte du journal. Les autres barrières conservent leurs propres périmètres.

La levée explicite écrit `external-paths.release` avec le journal complet avant
de retirer les flags et les deux noms de chaque inode. Elle synchronise les
parents, vérifie leur absence, retire le journal principal puis l'intention de
levée. Cette dernière contient assez d'information pour récupérer même une
interruption après suppression du journal principal. Une levée interrompue
exige `recover_unseal` ; `recover` refuse une intention de levée présente.

Les trois journaux bloquent la réouverture des données et la reprise de
maintenance. Le parcours positif lève explicitement les réservations externes,
puis le Web, le slot et les données, avant de reprendre HTTP. Aucun succès,
échec, décès du contrôleur ou retour de contexte ne rouvre les services.

## Qualification et limites

Gel attendu : 117 contrôles locaux, 196 Debian dont douze essais Ext4 externes,
36 scénarios intégrés répartis en quatre groupes disjoints de neuf. Chaque
rapport identifie ses cas ; l'union exacte, sans doublon ni omission, est exigée.
Les sources Installer et Web sont comparées octet pour octet aux manifestes.

Les essais couvrent les écritures root, les descripteurs préouverts, les alias,
les voisins utilisables, les collisions, les montages, les interruptions de
préparation/publication/levée, les dérives et le blocage de reprise. Le banc
intégré conserve le scénario d'apparition IA après copie en injectant désormais
un retrait administratif explicite du flag ; le reçu doit rester refusé.
Quatre ajouts couvrent écritures root réelles, fichier PHP étranger conservé,
mort réelle pendant publication et retrait d'un flag invalidant la certification.

Le banc utilise un volume Ext4 jetable pour `/var/lib` et `/etc/hestia`, en
préservant le contenu initial de `/var/lib` de son image avant démarrage.
Cela n'est pas une opération produit. Tous les nettoyages privilégiés restent
limités aux fixtures identifiées par leurs journaux dans le conteneur isolé.

La garantie porte sur les inodes réservés aux noms contrôlés. Le déplacement
administratif des parents, les substitutions de montages, le retrait des flags,
les périphériques bruts et le noyau sont exclus. La configuration générale de
l'hôte n'est pas scellée. `foreign_cli_controlled`, `storage_inventory_complete`,
`complete_web_backup`, `phase5c2_complete` et les indicateurs globaux restent faux.
Le [bilan 5C2](PHASE5C2_COVERAGE.md) détaille ce qui est acquis et les gates restants.

Références Linux : [flags d'inode](https://man7.org/linux/man-pages/man2/FS_IOC_SETFLAGS.2const.html)
et [publication par lien exclusif](https://man7.org/linux/man-pages/man2/link.2.html).

