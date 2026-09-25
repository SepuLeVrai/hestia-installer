# Sauvegarde des répertoires de données modifiables

## Lot court et frontière réelle

`installer.backup_files` ajoute une primitive privée de copie et restauration de
répertoires explicitement enregistrés, avec lease de maintenance vivante.
Ce lot n'est pas encore une sauvegarde complète SQL + Web : son raccordement à
`UpgradeBackup`, à l'inventaire applicatif et aux services 5D reste à réaliser.
Aucune route HTTP ni modification du wizard n'est introduite.

`DataInventory` est construit par l'orchestrateur de confiance, jamais depuis
une liste de chemins fournie par le navigateur. Il identifie les racines, UID et
GID Web. La primitive vérifie leur sécurité et leur non-chevauchement ; elle ne
peut pas déduire qu'un stockage externe a été oublié. Le rapport conserve donc
`storage_inventory_complete=false`, `database_verified=false`,
`restore_to_original_allowed=false` et `application_installed=false`.

## Contrat de capture et de restauration

- Confirmation explicite et lease active liée à l'instance, au groupe et à la session de maintenance.
- Racines préexistantes, parents root non modifiables, refus des chevauchements avec les archives et la maintenance.
- Parcours par descripteurs de répertoires, sans suivre les liens. Refus des liens symboliques, hardlinks, fichiers spéciaux, montages imbriqués, propriétaires/groupes étrangers, droits spéciaux, écriture publique et fichiers exécutables.
- Refus de tous les attributs étendus, notamment ACL et capabilities, tant qu'une restauration fidèle de ceux-ci n'est pas prise en charge.
- Copie par blocs, empreintes SHA-256, limites et délais bornés, annulation, vérification des inodes et changements de métadonnées pendant le parcours. Aucune exclusion de noms tels que `.git` dans les données métier.
- Archives root:root 0700, blobs et manifestes 0600, réservation durable avant copie. Les noms métier, identifiants de session et contenus restent dans le manifeste privé.
- Restauration réelle vers une cible nouvelle sous parent root:root 0700. Une cible existante, même vide, reste interdite. Les données restaurées ne sont ni exposées ni exécutées.
- Relecture indépendante des fichiers restaurés et vérification des UID/GID, modes, dates d'accès et de modification. Les répertoires vides sont conservés ; les dates de leurs parents sont rétablies après les créations.
- Relecture complète de la source avant et après restauration. Le reçu `verified.json` n'est écrit qu'après ces contrôles.

Les lectures utilisent `O_NOATIME` sans repli, pour ne pas prolonger l'ancienneté
des sessions PHP. L'expiration fonctionnelle d'une session et sa réactivation
HTTP après un rollback complet nécessitent encore la recette d'intégration.
Le rétablissement de `ctime` n'est pas possible par une copie ordinaire et n'est
pas annoncé ; `ctime` sert seulement à détecter une modification pendant lecture.

Limites explicites du format : 32 racines, 100 000 entrées, profondeur 64,
1 Gio par fichier, 16 Gio au total, manifeste 64 Mio, 300 secondes par parcours,
64 Mio de réserve disque. Un dépassement refuse le résultat ; aucune donnée
n'est omise. La restauration utilise un nombre borné de descripteurs, même pour
un arbre large. Un échec peut laisser une archive ou un clone incomplet privé,
sans reçu de succès et sans effacement automatique des preuves.

Une ancienne archive est liée à la lease qui l'a créée : après reprise d'activité,
une nouvelle maintenance n'autorise pas sa restauration. La récupération de la
même lease interrompue permet une inspection et une restauration isolée. Le
rollback vers la source reste une opération distincte à implémenter et qualifier.

## Qualification et prochaine étape

22 tests obligatoires sont ajoutés à la Quality core Debian 12 et 13 : vraie
copie/restauration, fichier de 9 Mio, Unicode, vide, métadonnées et dates de
sessions, consentement, lease fermée ou renouvelée, arrêt réel SIGKILL, collisions,
liens, ACL réelle, attributs étendus, permissions, manque d'espace, écritures
courtes, erreur ENOSPC, dérive, remplacement d'inode, corruption et troncature,
annulation, limites, arbre large et refus de lecture par l'identité Web.

Le petit lot est en qualification. Ne pas convertir les anciens résultats verts
en preuve de ce nouveau code. Les suites SQL/HTTP antérieures n'ont pas été
réexécutées pour cette primitive indépendante, sans changement des chemins SQL.
La recette suivante doit raccorder les mêmes leases aux sauvegardes SQL et aux
racines déduites de la configuration réelle, avant toute revendication de
sauvegarde complète ou clôture 5C2.
