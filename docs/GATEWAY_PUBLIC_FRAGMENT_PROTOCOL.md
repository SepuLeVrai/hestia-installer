# Transfert public/boot Gateway

L'extension aux générations successives est décrite séparément dans
[GATEWAY_SUCCESSIVE_GENERATIONS_20261010.md](GATEWAY_SUCCESSIVE_GENERATIONS_20261010.md).
Le gel `dd4d89a` a échoué au deuxième cycle ; son correctif attend une nouvelle
qualification native. Le présent document
conserve le contrat acquis du premier transfert.

## Périmètre implémenté

Le cockpit prend en charge le premier transfert d'une Gateway MAIN déjà exposée
par le frontal NGINX géré de l'Installer, avec SharedPublic v1 et MobileBoot
terminés. Le plan lie la sauvegarde composée, son bail de maintenance, la source,
la cible, les profils historiques et le code du successeur.

Deux directions sont prévues entre les paquets exacts du catalogue :
`0.12.2-installer.rc1` (`e2c09f5`) vers `0.12.3-installer.rc1` (`3392782`),
et le retour binaire inverse. Les deux utilisent SQLite 6. Aucun rembobinage
SQLite ni restauration implicite de clé ou de credential n'est effectué.

Les transitions DEV/FCM, les générations successives et le cycle
upgrade/rollback/upgrade sur le même hôte restent hors de ce premier périmètre.
Le reverse proxy Synology personnel n'est pas ce frontal NGINX géré.

## Sept étapes durables

| Étape | Effet et contrôle |
| --- | --- |
| Binaires | Import authentifié et staging privé du paquet cible |
| Bascule | Remplacement du binaire sous maintenance, reprise par inode |
| Publication | Publication explicite du profil Gateway cible |
| Transfert public/boot | Arrêt public, bundle successeur, huit fragments et reload PID 1 |
| Admission | Réconciliation du drain source avec la génération cible contrôlée |
| Activation locale | Admission SQL finale, consommation des gardes et démarrage local |
| Ouverture publique | Intentions HTTP/HTTPS/timer, observation des invocations, sceau final |

Les reçus cockpit sont liés par empreinte. Ils déterminent l'étape à reprendre,
mais ne donnent aucune autorité native à eux seuls. L'import et l'exécution
ont des consentements distincts. Les identifiants SQL sont éphémères.
GET et refresh ne sondent pas le serveur et ne rejouent aucun effet ; le contrôle
courant exige l'action explicite de vérification.

## Fragments, générations et sélection

`gateway_public_fragments.py` remplace huit fragments dans un ordre fermé.
La transaction lie les inodes source/cible, les répertoires parents, le bail,
les profils, la publication et le code. Chaque effet possède une intention
et un reçu durable. Une réponse perdue après renommage se résout en relisant
l'inode cible. Un inode étranger, même de contenu identique, est refusé.
Un temporaire sans propriétaire durable n'est pas adopté ni supprimé.

`gateway_public_generation.py` compile le bundle et les workers successeurs.
Les anciens bundles, profils et journaux restent intacts à leurs chemins.
Les chemins des certificats, les routes, les restrictions réseau et les
commandes de renouvellement sont conservés. Le worker privé relit les
empreintes, propriétaires, permissions et chemins avant d'importer son code.

`gateway_public_systemd.py` arrête le timer puis les listeners, refuse un
renouvellement en cours, vérifie le garde Apache 50 et son overlay public 60,
et recharge explicitement PID 1. Il compare les commandes effectivement
chargées aux fragments attendus. Le bit `NeedDaemonReload` seul ne suffit pas.
Une réponse perdue après stop ou reload est conciliée par observation.
L'époque PID 1 ne peut pas changer au milieu d'un transfert incomplet.

Le pointeur `gateway-successor.json` est publié seulement après transfert
contrôlé. Son lecteur relit le bundle, la publication et les huit inodes.
Une sélection corrompue ou incomplète refuse sans repli vers les anciens
workers. Ce pointeur ne constitue pas une permission de démarrer.

## Admissions et ouverture

`gateway_public_admission.py` lie le même objet HTTP, le même processus,
le bail et le profil de drain historique à l'overlay successeur réellement
observé. Les exceptions des moteurs fichiers/externes/données/reprise exigent
ce contexte vivant. Les parcours privés conservent leurs refus historiques.
Les observations systemd portent toujours sur les fragments réellement chargés.

La fenêtre SQL finale conserve sa borne de 180 secondes et ses audits natifs.
Après consommation durable des gardes, elle produit le propriétaire d'activation.
Les gardes SQL/Web requis pendant l'activation PHP exigent en plus l'intention
PHP exacte et la même époque PID 1. Ils n'autorisent pas l'ouverture publique.

`gateway_public_opening.py` écrit une intention avant chaque start HTTP, HTTPS
et timer. Après perte de réponse, il exige l'invocation déjà démarrée.
Un start ambigu sur un service arrêté n'est pas rejoué. Les contrôles stricts
NGINX vérifient l'exécutable, les arguments exacts et le socket détenu.
Le timer est démarré après libération du verrou public, pour permettre le
renouvellement immédiat d'un timer Persistent.

L'ouverture complète scelle les trois reçus. Une nouvelle époque PID 1 exige
ce sceau et les preuves des workers successeurs ; elle ne réutilise pas les
anciennes invocations. Un contrôle terminé reste en lecture seule.

## Recettes et portée des preuves

La Quality obligatoire couvre Debian 12/13, le navigateur bridge et HTTPS,
les contrats historiques et ajoutés, le statique, les permissions et le
packaging exact. Les tests de fragments utilisent de vrais fichiers et verrous.
Le banc systemd ciblé vérifie notamment les arrêts, le reload, les substitutions
d'inodes, les commandes chargées et les pertes de réponse.

La recette composée part d'un hôte Debian 13 vierge avec volume Ext4,
MariaDB, PHP, Apache, NGINX, vrais binaires Gateway et autorité ACME Pebble
jetable. Elle exerce cinq SIGKILL par direction, contrôle Web/Mobile, clés,
UUID SQLite, certificats, parents et politiques réseau, puis un nouveau PID 1
et deux renouvellements ACME. Nouveau PID 1 dans le même noyau ne signifie
pas reboot noyau. Les deux directions utilisent des hôtes indépendants.

Le verdict courant et les références exactes sont consignés dans
[GATEWAY_PUBLIC_BOOT_QUALIFICATION_20261010.md](GATEWAY_PUBLIC_BOOT_QUALIFICATION_20261010.md).
L'historique des défauts corrigés est conservé dans
[HANDOFF_PUBLIC_BOOT_20261010_CANDIDATE.md](HANDOFF_PUBLIC_BOOT_20261010_CANDIDATE.md).

Aucun changement de schéma n'est requis : `schema.sql` et `install.php`
ne sont pas modifiés par ce bloc.
