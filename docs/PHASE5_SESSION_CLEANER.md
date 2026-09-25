# Phase 5 — nettoyage dédié des sessions

## Base et portée

Base `6477faf85d5f31f1ca645cd3de89b780ed119a07`, arbre
`a0828f1a092cd0a494ecdc0c6d6367fc1a41a6d3` : Quality `36147944637`
(tentative 2), système `36147944651`, SQL/HTTP `36148072196` verts.
542 core par Debian, 23 recettes système par Debian, 16 DOM, 21 HTTPS et
118 SQL/HTTP. L'intermittence DOM historique « fresh au lieu d'upgrade après
refresh » reste **non résolue**, malgré le diagnostic ciblé sans reproduction
et la relance verte ; elle doit rester visible avant clôture finale de Phase 5.

Ce lot ajoute `installer.session_cleaner.SessionCleaner`, une opération privée
de staging du collecteur de fichiers et de son timer. Il exige le runtime HTTP
déjà préparé, sa maintenance exacte et ses services inactifs. Il ne démarre ni
n'active automatiquement le timer, Apache ou FPM. Le registre public, le wizard,
le SQL et les pins Web ne changent pas. La Phase 5 reste ouverte.

## Coexistence avec Debian

Le script Debian consulte les configurations sous `/etc/php` ; le pool privé du
runtime utilise une configuration ailleurs. Ses sessions ont donc besoin d'un
collecteur dédié. **`phpsessionclean` et son timer/cron ne sont ni désactivés,
ni remplacés, ni réécrits**. Les dépendances originales et le php.ini global
sont liés au journal de staging ; le banc exécute également le script natif
original et contrôle les fichiers globaux avant/après.

Sources primaires consultées :
- https://sources.debian.org/src/php-defaults/96/sessionclean/
- https://www.php.net/manual/en/session.configuration.php

Cette coexistence sur les images officielles qualifiées ne prouve pas l'absence
de scripts cron/timers ajoutés par un administrateur sur un serveur arbitraire.
L'inventaire exhaustif des autres producteurs reste une condition séparée.

## Collecteur borné

Le worker Python autonome est installé root:groupe dédié, 0640, dans un dossier
root:groupe dédié 0750. Il s'exécute exclusivement sous l'UID/GID du pool, avec
l'interpréteur Debian explicite (3.11 ou 3.13), `-I -B`, sans import applicatif,
sans argument secret et sans commande fournie par le navigateur. Il contrôle
ses ancêtres, les empreintes du profil et du guard, et le verrou de maintenance.

Le collecteur prend **sans attendre** un verrou exclusif sur `activity.lock`.
Si une requête PHP ou un autre producteur coopératif est actif, il reporte le
passage avec `SESSION_CLEANER_BUSY`, sans suppression. Pendant son passage, le
guard HTTP refuse les nouvelles requêtes dynamiques ; le scan est limité à
2 secondes et 10 000 entrées. Ce choix peut reporter le nettoyage sur un serveur
continuellement occupé ; aucune garantie de délai d'expiration sous charge
continue n'est annoncée. Le timer demandera un nouveau passage.

La présence d'une maintenance, même incomplète, interdit la suppression. Le
marqueur est contrôlé avant/après acquisition du verrou et entre les candidats.
Une acquisition de maintenance attend la fin du collecteur avant de donner sa
lease au backup : pas de copie concurrente au nettoyage dans ce périmètre.

Le worker ne parcourt qu'un niveau du répertoire `data/sessions` privé, UID/GID
dédié 0700. Il ne lit jamais le contenu des sessions. Première passe : noms PHP
fermés, fichiers réguliers 0600, UID/GID exacts, absence de liens/hardlinks/ACL,
limites de temps et de quantité. Une entrée étrangère invalide le passage avant
la première suppression. Deuxième passe : seules les sessions dont le mtime est
strictement antérieur à « maintenant moins 43200 secondes » sont candidates.
Chaque inode est verrouillé sans attendre et revérifié avant unlink ; un fichier
déjà verrouillé est conservé. Les sessions conservées ne sont pas touchées,
leurs dates ne sont pas prolongées. Les suppressions sont suivies d'un fsync du
répertoire, y compris si un passage a commencé puis échoue.

Le GC technique n'est pas l'expiration fonctionnelle d'une connexion. La politique
Web **1 h / 4 h / 8 h** n'est pas modifiée ni déclarée qualifiée par ce collecteur.
Le banc contrôle la conservation physique de fichiers ayant ces âges, ainsi que
l'expiration au-delà de 12 heures ; la politique fonctionnelle reste à exercer
avec le Web réellement installé. Une interruption après suppression ne restaure
pas les sessions techniquement expirées : le prochain passage inspecte l'état
actuel, sans reçu fictif ni restauration de fichiers expirés.

## Unité, planification et preuves

L'unité `hestia-<instance>-session-cleaner.service` est oneshot, sans restart,
avec arrêt du cgroup, UID/GID dédié, aucune capability, `NoNewPrivileges`, système
en lecture seule et unique chemin inscriptible pour les sessions. Elle possède
le même drop-in de condition de maintenance que les unités HTTP.

Le timer associé utilise `OnBootSec=5min`, `OnUnitInactiveSec=30min`,
`AccuracySec=1min`. Son staging le laisse inactif et non activé au boot.
L'activation contrôlée du timer et du Web reste un chantier explicite.
La condition et le verrou restent efficaces si un timer déjà actif tente de
lancer le collecteur pendant la maintenance.

La création réserve exclusivement le dossier, le fragment, le timer et le
drop-in, sous la lease exacte du runtime. `cleaner.attempt` précède les écritures ;
`staged.json` n'est produit qu'après rechargement des définitions. L'observation
recontrôle les octets, droits, dépendances, services inactifs, timer inactif,
absence de jobs et cgroups vides, sans écriture ni reload. Réponse perdue :
`APPLIED` seulement sur preuve vivante complète ; sinon `MANUAL`. Pas de rejeu,
adoption, suppression automatique de traces ou rollback aveugle.

`system_drain.audit_unit` expose seulement l'audit en lecture d'une unité.
La construction de `SystemDrain` exige toujours les quatre rôles canoniques ;
aucune lease complète ne peut venir du seul audit du nettoyeur. Le banc compose
trois unités réelles (Apache, FPM, collecteur) avec un rôle CLI explicitement
fixture. `system_wiring_verified` et `complete_web_backup` restent faux.

## Quality requises sur le commit gelé

- **558 core** par Debian 12/13 : les 16 nouveaux cas contrôlent expiration,
  verrou réel, fichiers étrangers/liens/FIFO/ACL, dates, bornes, dérive, marqueur,
  refus privés, consentement et récupération sans rejeu.
- **35 recettes système** par Debian : 11 drainage + 12 runtime + 12 collecteur.
  Les nouveaux cas couvrent staging fermé, native inchangé, expiration physique,
  vraie requête PHP active, verrou de session, refus de lien, collision de timer,
  écriture interrompue, récupération/dérive, composition avec drainage, déclenchement
  réel par timer et maintenance attendant un collecteur en cours.
- Le test de déclenchement accélère **uniquement les durées de sa fixture timer** ;
  le worker, l'unité et la condition restent les vrais fichiers produits. La
  planification 5min/30min est auditée séparément sans attendre trente minutes.
- 16 DOM, 21 HTTPS et 118 SQL/HTTP historiques, zéro erreur/échec/skip, sources
  stables. Les rapports finaux après gel documentaire sont dans le checkpoint
  compagnon, avec toute campagne échouée conservée distinctement.

Debian 12/PHP 8.2 qualifie l'infrastructure, pas le Web qui exige PHP >= 8.3.
Création des comptes et paquets, TLS/proxy, activation Web, autres producteurs,
stockages exhaustifs, sauvegarde complète 5C2, vraie transition 5C3,
reprise/rollback 5C4 et wizard 5D restent requis. Pas de PR/fast-forward avant
l'ensemble des Quality requises et la documentation ; aucune production touchée.
