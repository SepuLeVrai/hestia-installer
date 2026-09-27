# Protection persistante de la configuration provisionnée

Base qualifiée : `23b54743c605dd37cf9a2f7e45b21e710265d760`.
Le lot précédent protège les six racines de données. Ce lot ferme les écritures
ordinaires sur le répertoire de configuration propre à l'instance.

## Problème et protection

Les verrous partagés de `assistant-edit.lock` et `assistant.json` coordonnent
les writers Installer/Web qui les respectent. Une écriture directe les contourne.
Le banc local a reproduit une modification puis restauration des mêmes octets
sur le même inode, acceptée par l'admission historique. Comparer les dates ou
relire ponctuellement les octets ne constitue pas une interdiction d'écrire.

Après cette admission et avant le verrou SQL, la réservation et toute copie,
`configuration_fence` protège le slot et chacun de ses fichiers avec immutable
Ext4. Le parent est protégé en premier, puis les fichiers. Les écritures ordinaires
root, les descripteurs déjà ouverts et les alias bind vers ces mêmes inodes portent
la protection noyau. Le journal est durable avant la première mutation de flags.

La protection persiste après succès, erreur ou mort du contrôleur. L'admission
coopérative reste active pendant l'opération. Le manifeste V6 et le reçu lient
l'empreinte du journal et certifient seulement `configuration_slot_inodes_fenced`
et `ordinary_root_settings_writes_fenced`.

## Profil fermé

- Même instance, lease de maintenance typée et admission vivante ; descripteur
  du slot identique à celui de l'admission et au chemin canonique.
- Ext4 en lecture/écriture, Linux 64 bits x86_64/aarch64, noyau >= 6.1 et capacité
  de gérer immutable, selon le lot données. Aucun repli overlay/tmpfs.
- Slot root:groupe Web 0750 ; fichiers réguliers root, groupe root ou Web,
  sans lien, hardlink, exécutable, ACL, xattr ou montage imbriqué. Seul
  `assistant.json` peut être modifiable par le groupe avant protection.
- Aucun sous-répertoire sauf `maintenance`, dont l'identité correspond à la
  lease. Ce sous-arbre reste hors protection pour permettre ses journaux.
- 128 enfants maximum, 256 Kio par fichier, 4 Mio au total, journal 128 Kio et
  60 secondes par parcours/transition. Un dépassement refuse, sans omission.

Le journal privé `configuration-inodes.attempt`, root:root 0600, lie instance,
lease, chemin, devices/inodes, propriétaires, modes, tailles, empreintes et flags
d'origine. Aucun secret dans le rapport public. Les flags initiaux autres
qu'extents/index sont refusés à l'acquisition, notamment immutable préexistant.

## Reprise explicite

Récupérer la même maintenance et l'admission des réglages. `recover` complète une
fermeture partielle dont le journal et les fichiers correspondent exactement.
Il n'adopte pas un arbre modifié et ne rouvre rien.

`unseal(confirmed=True)` écrit `configuration-inodes.release`, lié au journal
exact, puis retire les flags des fichiers avant ceux du répertoire. Il vérifie
fichiers et flags d'origine avant d'effacer ses deux journaux. Après interruption,
`recover_unseal` exige cette intention exacte. Une fermeture ordinaire refuse
une intention de levée présente. Les sorties de contexte ne retirent aucun flag.

La reprise de maintenance et la réouverture des données refusent tant qu'un de
ces deux journaux subsiste. Ordre normal : lever explicitement la protection de
configuration, celle des inodes de données, puis rouvrir les chemins et lever la
maintenance. Le produit ne redémarre pas les services et ne restaure pas les sources.

## Qualification et limites

Gel attendu : 106 contrôles locaux, 163 Debian 13 dont huit essais Ext4 de
configuration, 29 scénarios intégrés. Les trois ajouts couvrent écritures root,
alias et descripteur après copie, mort réelle pendant fermeture et retrait d'un
flag invalidant le reçu. Le positif conserve restauration et reprise HTTP réelle.
Les résultats mesurés et sources exactes sont ceux du checkpoint livré.

Le banc place également sa configuration sur son volume Ext4 jetable. Le produit
ne crée ni ne convertit de volume. La documentation est figée avant qualification.
Le code Web, ses pointeurs, les configurations externes et la maintenance restent
hors de cette protection. Le retrait administratif des flags, les montages, le
périphérique brut et le noyau restent hors garantie. Les admissions et relectures
existantes restent requises. `foreign_cli_controlled`, `storage_inventory_complete`,
`complete_web_backup` et les indicateurs globaux restent faux. 5C2, 5C3/5C4/5D et
les Quality globales restent à terminer avant promotion.
