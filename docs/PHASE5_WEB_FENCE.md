# Protection persistante du Web déployé

Base qualifiée : `2963db6513945a3940341496fd8a797e7046d65a`, campagne
`36338904729`. Les inodes des données et du slot de configuration sont déjà
protégés. Ce lot ajoute le code Web déployé et ses pointeurs d'activation.

## Périmètre et acquisition

Le coordinateur prend `web_fence` après la protection du slot et avant le verrou
SQL, la réservation de l'archive et toute copie. Il exige une barrière HTTP
typée, vivante, du profil provisionné, liée à la même maintenance. Les contrôles
existants des sources, du profil HTTP et des pointeurs scellés restent requis.

Le parcours couvre l'arbre Web entier, fichiers et répertoires, sans exclusion
de noms cachés, `.github`, historiques uploads ou fichiers exécutables du dépôt.
Il inclut obligatoirement `includes/db.php` et `install.lock`. Aucun fichier de
code n'est exécuté. Le journal root:root 0600 `web-inodes.attempt` lie instance,
lease, racine, empreinte du profil HTTP, chemins, inodes, propriétaires, modes,
tailles, empreintes des contenus et flags d'origine. Il est durable avant le
premier flag immutable. Les répertoires sont protégés avant leurs descendants.

Les écritures ordinaires root, remplacements, créations, suppressions et
changements de métadonnées sont bloqués sur les inodes admis, y compris par un
alias bind extérieur ou un descripteur déjà ouvert. Cela empêche aussi de créer
`includes/conf_db_ia.php` dans le Web protégé. Les chemins IA hors du Web gardent
leurs admissions d'absence, sans nouvelle garantie sur les répertoires de l'hôte.

## Profil et bornes

- Ext4 en lecture/écriture, Linux 64 bits x86_64/aarch64, noyau >= 6.1 et capacité
  de gérer immutable, selon le lot données. Aucun repli sur un autre système.
- Répertoires root:root 0755 ; fichiers source root:root 0644/0755 ; les deux
  pointeurs générés root:groupe Web 0640. Les modes exécutables sont conservés.
- Aucun lien symbolique, hardlink, fichier spécial, ACL, xattr ou montage
  imbriqué, même bind sur le même device. La racine peut être un montage Ext4.
- Au plus 10 000 entrées, profondeur 64, chemin 2048 octets, 8 Mio par fichier,
  256 Mio au total, journal 4 Mio, budget de 60 secondes par parcours/transition.
  Un dépassement refuse ; aucun fichier n'est silencieusement exclu.

Les journaux restent hors du Web, dans la maintenance. Le produit ne crée ni
ne convertit de volume et n'adopte pas une protection immutable préexistante.

## Reprise et reçu

Fermeture du handle, succès, échec ou mort du contrôleur ne retirent aucun flag.
`recover` complète uniquement une fermeture dont le journal, le profil et les
fichiers correspondent exactement. Une dérive ne déclenche aucune réparation.

`unseal(confirmed=True)` journalise `web-inodes.release`, lié au journal exact,
puis retire les flags des descendants avant leurs parents. Il vérifie les
contenus et les flags d'origine avant d'effacer ses deux journaux. Une levée
interrompue exige `recover_unseal` et cette intention exacte ; une récupération
de fermeture refuse une intention de levée présente.

La reprise de maintenance et la réouverture des données refusent tant qu'un
journal Web subsiste. Le parcours positif récupère la même barrière, lève
explicitement le Web, le slot et les données, puis rouvre les chemins et la
maintenance. Aucun retour de contexte ne redémarre les services.

Le manifeste V7 lie `web_fence`. Le reçu ajoute `web_code_fenced`,
`web_activation_pointers_fenced`, `ordinary_root_web_writes_fenced` et l'empreinte
de la protection. Les rapports des barrières précédentes gardent leur propre
périmètre ; ils ne certifient pas eux-mêmes la protection du Web.

## Qualification et frontière suivante

Gel attendu : 112 contrôles locaux, 178 Debian 13 dont neuf essais Ext4 Web,
32 scénarios intégrés. Les trois ajouts intégrés exercent écritures root,
pointeurs, alias et descripteur préouvert après copie, mort réelle en cours de
fermeture, et retrait administratif d'un flag empêchant le reçu. Le positif
conserve sauvegarde, restauration et reprise HTTP réelle.

Le banc copie le Web de fixture sur le volume Ext4 jetable et le monte à son
chemin `/srv` d'origine avant finalisation. La source Web épinglée reste inchangée.
Nettoyage et retrait des flags sont exclusivement des opérations de fixture.
Les résultats mesurés, manifestes exacts et l'environnement sont au checkpoint.

Les configurations externes et l'administration de l'hôte restent hors de ce
module. Retirer explicitement immutable, modifier les montages, le périphérique
brut ou le noyau sort de la garantie. Aucun inventaire global des producteurs
n'est déduit de cette barrière. `foreign_cli_controlled`,
`storage_inventory_complete`, `complete_web_backup` et les indicateurs globaux
restent faux. Restent les chemins externes et le bilan 5C2, puis 5C3/5C4/5D et
les Quality globales avant PR ou promotion.
