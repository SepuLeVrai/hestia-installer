# Protection persistante des inodes de données

## Résultat et frontière

Base qualifiée : `3a14ddecff75e8ae97c5ecf93f0f0a6a8eee3346`. La sauvegarde
provisionnée exige désormais une protection Ext4 de chaque inode des six racines
de données, après la fermeture des chemins canoniques et avant la copie.
Les lectures restent possibles au contrôleur. Les écritures ordinaires de root,
les créations, suppressions et changements de métadonnées sont bloqués par le
noyau. Les accès par un bind mount vers ces mêmes inodes portent la protection.

Cette propriété complète les permissions 0700 du parent. Elle ne constitue pas
un inventaire de tous les alias ou processus de l'hôte. Un administrateur qui
retire explicitement le flag immutable, remplace un montage, écrit sur le
périphérique brut ou modifie le noyau sort de cette garantie. Le code ne prétend
pas contenir ces pouvoirs administratifs. La dérive observable révoque le reçu.

Les réglages et autres destinations externes conservent leurs admissions et
verrous existants. `foreign_cli_controlled`, `storage_inventory_complete`,
`complete_web_backup` et les indicateurs globaux de phase restent faux. Ce lot
ferme les écritures ordinaires sur les inodes de données admis, pas toute 5C2.

## Profil admis

- Linux 64 bits x86_64/aarch64, noyau 6.1 ou ultérieur, système de fichiers Ext4
  monté en lecture/écriture. Les autres systèmes de fichiers sont refusés.
- Permission `CAP_LINUX_IMMUTABLE` pour ajouter/retirer le flag ; son absence
  provoque un refus, jamais une protection plus faible réputée équivalente.
- Objets privés typés, même lease de maintenance et même barrière de chemins.
- Parent canonique exact, six racines du profil, répertoires/fichiers ordinaires,
  identités et modes existants, pas d'ACL/xattr, lien symbolique ou hardlink.
- Aucun montage imbriqué dans les données, y compris bind sur le même device.
  Les identifiants de montage des descripteurs sont contrôlés via procfs.
- Flags d'origine limités aux caractéristiques Ext4 extents/index de répertoire.
  Un fichier déjà immutable ou append-only n'est pas adopté par acquisition.
- Au plus 8192 entrées, profondeur 64, chemins 2048 octets, journal 4 Mio et
  budget de 60 secondes par parcours/transition. Ce sont des limites d'admission,
  pas une garantie de débit. Les limites de capture parent restent applicables.

Aucune conversion de système de fichiers ni création de volume n'est effectuée
par le produit. Les volumes loop de la recette sont exclusivement des fixtures.

## Fermeture et preuve

1. Le drain provisionné et la barrière 0700 sont acquis selon le contrat existant.
2. Le parcours privé vérifie le système de fichiers, les chemins, devices/inodes,
   propriétaires, modes, montages et flags. Le journal root:root 0600
   `inode-fence.attempt` lie cet ensemble, l'instance et la lease exacte.
3. Le journal est écrit exclusivement et synchronisé avant le premier ioctl.
   Les répertoires sont protégés avant leurs enfants ; chaque inode est
   synchronisé après l'ajout du flag immutable.
4. L'ensemble exact est reparcouru et contrôlé avant de rendre la barrière.
   Une entrée ajoutée, retirée ou remplacée pendant la transition refuse la preuve.
5. Le coordinateur exige la barrière liée à l'objet de fermeture des chemins,
   la revérifie pendant la sauvegarde et lie son empreinte dans le manifeste V5.

Le code Ext4 attend les I/O directes en cours et synchronise les pages sales
lors de la transition d'un fichier vers immutable. La recette teste un
descripteur root déjà ouvert et un mmap partagé déjà modifiable : les écritures
suivantes sont refusées, le mmap provoquant SIGBUS dans le processus jetable.
Le blocage est attaché à l'inode, il ne dépend pas de la durée d'un CLI.

Le reçu ajoute `data_inode_writes_fenced`, `ordinary_root_data_writes_fenced`,
`same_inode_alias_writes_fenced` et `inode_fence_sha256`. Ces champs ne donnent
aucune autorisation d'upgrade, rollback, reprise ou certification de tout l'hôte.

## Interruption et réouverture

Une sortie ou une mort du contrôleur ne retire aucun flag. Une interruption de
fermeture peut laisser une protection partielle : aucun reçu n'est délivré.
Après récupération exacte du drain et de la barrière de chemins,
`inode_fence.recover(data_fence, confirmed=True)` complète seulement la fermeture.
Un arbre changé ou journal étranger n'est jamais réécrit pour permettre la reprise.

`InodeFence.unseal(confirmed=True)` journalise d'abord une intention explicite
de retrait dans `inode-fence.release`, puis retire uniquement les flags ajoutés,
en commençant par les enfants. Le contrôle final précède le retrait des deux
journaux. La maintenance principale et le mode 0700 demeurent.

Une interruption de cette réouverture conserve son intention. La récupération
de fermeture la refuse ; `recover_unseal(data_fence, confirmed=True)` peut finir
uniquement l'intention exacte. Si l'interruption arrive après retrait de cette
intention mais avant retrait du journal principal, une récupération de fermeture
peut de nouveau sceller l'arbre. Aucune sortie ne rouvre automatiquement les données.

La barrière de chemins refuse de se lever tant qu'un de ces journaux existe.
`MaintenanceLease.resume()` les contrôle également. L'ordre normal est donc :
récupérer, retirer explicitement la protection des inodes, rouvrir les chemins,
puis reprendre la maintenance et les services par l'opération dédiée.

## Qualification du gel

Le candidat `a26504393280a3de277934b9e5a43638b0511a45`, campagne
`36329926011`, a passé les 149 contrôles Debian, dont les dix essais Ext4.
Les 26 scénarios intégrés ont tous été refusés avant démarrage HTTP : le chemin
de fixture sous le volume dépassait la limite de 75 caractères du runtime.
Le correctif raccourcit uniquement le nom de la fixture ; la limite produit
et toutes les assertions sont conservées. Cette preuve négative est archivée.
Le candidat `c2c2a97caee9ac5465be0e2b69fd1bdc2bc6c793`, campagne
`36330364031`, passe 149 contrôles et 25 des 26 scénarios intégrés. La restauration
de fixture échoue sur un renommage entre Ext4 et le système de fichiers du
conteneur (`EXDEV`). Le correctif place la restauration et les anciens répertoires
dans un parent privé root:root 0700 sur le même volume Ext4, hors des données
scellées. Une assertion vérifie le device avant le renommage. Aucune opération
produit ni assertion de résultat n'est assouplie ; les deux preuves sont conservées.
La qualification intégrée de ce nouveau gel est requise avant livraison qualifiée.

100 contrôles locaux attendus ; 149 avec fichiers sur Debian 13, dont dix essais
réels Ext4. Ils couvrent 48 refus de mutation root, descripteur préouvert, mmap,
alias bind, montage imbriqué, tmpfs, liens, limites et interruptions/reprises.

26 scénarios intégrés attendus : les 22 précédents et quatre ajouts. Le nouveau
CLI root, le bind alias et le descripteur préouvert sont exercés après la copie ;
un vrai timer systemd root tente aussi d'écrire. La mort du contrôleur intervient
au milieu de la protection. Le retrait administratif d'un flag invalide le reçu.
Le positif conserve la restauration et la réouverture réelle du Web.

Le banc utilise un volume Ext4 jetable ; aucun résultat overlay/tmpfs n'est
présenté comme preuve Ext4. Le workflow Quality prépare désormais ce prérequis
pour les contrôles obligatoires Debian 12/13. Cette modification ne constitue
pas une nouvelle exécution des Quality globales. Le noyau effectif du banc,
les résultats exacts et les manifestes de sources sont consignés au checkpoint.

## Références de sémantique

- [Attributs d'inode Linux](https://www.man7.org/linux/man-pages/man2/ioctl_iflags.2.html)
- [Transition immutable Ext4, noyau 6.12](https://github.com/torvalds/linux/blob/v6.12/fs/ext4/ioctl.c)
