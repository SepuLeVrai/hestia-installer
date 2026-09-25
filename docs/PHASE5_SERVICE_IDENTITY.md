# Phase 5 — création de l’identité système dédiée

## Base et portée

Base `37de8a99e66aeff24b3cb3a1958968f1f4df2ecc`, arbre
`27848a9fa47c14efe16f8e5bfbc6138f0c8ad377`. Quality `36152239576`, système
`36152239622`, SQL/HTTP `36152489096` verts au premier passage : 558 core par
Debian, 35 recettes système par Debian, 16 DOM, 21 HTTPS et 118 SQL/HTTP.

`installer.service_identity.ServiceIdentity` crée exclusivement le compte et
son groupe local destinés aux services privés. L’opération typée
`web.service-identity.create` reste enregistrable explicitement ; aucun registre
public ou wizard ne change. Le runtime HTTP et le nettoyeur peuvent consommer
le compte après observation exacte du reçu. Aucun démarrage produit, paquet,
SQL, copie de code ou bascule n’est livré ici.

## Profil fermé

Debian 12/13, root, bases locales protégées et outils officiels préinstallés
useradd et nologin. Nom déterministe `hst-` suivi des 24 premiers caractères de
l’instance ; journal et commentaire lient l’identifiant complet de 32 caractères.
Toute collision, y compris de préfixe, est refusée sans adoption.

L’argv fixe impose compte système, groupe primaire neuf, UID/GID 100–999,
mot de passe littéral verrouillé `!`, shell nologin, home `/nonexistent` absent
et non créé, aucun groupe supplémentaire, boîte mail ou initialisation
lastlog/faillog. Aucun secret dans les arguments. PATH/LANG seuls, stdin et
sorties fermés, délai 30 secondes. Seul useradd modifie les bases locales avec
ses verrous natifs ; aucun patch manuel de passwd/shadow.

Les lectures passwd/group/shadow/gshadow contrôlent propriétaires, modes,
absence de liens/ACL, stabilité, limite 2 Mio et 20 000 entrées. Les mots de
passe des autres comptes restent en mémoire, jamais dans les sorties, rapports
ou journaux. Le reçu ne garde que les UID/GID et l’empreinte des seules entrées
du nouveau compte, verrouillé avec le marqueur fixe `!`.

NSS accepté : `files`, éventuellement suivi de `systemd`. LDAP, NIS/compat,
sources distantes et règles inconnues sont refusés. Collisions contrôlées dans
les bases locales et NSS. Les hooks useradd pré/post de `/etc/shadow-maint`
doivent être absents ou vides : aucun script de site exécuté. Les plages
subuid/subgid du compte, par nom ou UID numérique, sont refusées et aucune
n’est créée. Autres comptes, groupes, sous-identités et réglages conservés.

Sources primaires :
- https://manpages.debian.org/bookworm/passwd/useradd.8.en.html
- https://manpages.debian.org/trixie/passwd/useradd.8.en.html

## Journal et reprise

1. Préparation sans écriture : profil, outils, nom libre et journal absent.
2. Réservation exclusive `/var/lib/hestia-identity-<instance>`, root:root 0700.
3. `identity.attempt`, root:root 0600, durable avant toute commande.
4. Relecture du profil et des collisions, useradd une seule fois.
5. Contrôle des quatre entrées, NSS, unicité UID/groupe et sous-identités.
6. `created.json` 0600 durable puis observation complète avant réponse.

Le plan lie OS, outils et configurations NSS/login.defs/default-useradd ; toute
dérive invalide la preuve sans réécriture. L’observation contrôle UID/GID,
shell, home, commentaire, verrouillage, vieillissement, membres/administrateurs
de groupes ; elle compare local/NSS et relit les bases pour refuser une mutation
concurrente observée. Aucune commande ni réparation. Un administrateur root
concurrent reste hors du modèle adversarial, sans exclusion globale annoncée.

Réponse perdue après reçu complet : `APPLIED` par lecture seule. Toute empreinte
incomplète reste `MANUAL`, même si le compte existe. Aucune relance implicite,
suppression, réutilisation d’UID ou rollback automatique. Supprimer une identité
nécessite l’inventaire de ses fichiers ; ce lot ne prend pas cette décision.
Une création concurrente de la même instance n’a qu’un gagnant.

## Quality requises

18 nouveaux tests core obligatoires : **576 par Debian**. Grammaire, consentement,
commande privée, NSS, bases protégées, hooks, collisions, droits, dérives,
subids, interruptions et récupération sont couverts sans modifier les comptes hôte.

Les 35 recettes système historiques sont conservées, plus **12 vrais cas par
Debian**, total **47** : préparation en lecture ; compte verrouillé et autres
comptes inchangés ; collision utilisateur ; collision groupe ; commande échouée ;
reçu interrompu ; processus arrêté après useradd ; réponse perdue ; dérive
shell/groupes ; concurrence ; hook refusé ; utilisation du compte par les vrais
Apache/PHP-FPM/nettoyeur générés. Les UID/GID des sessions sont contrôlés. Les
comptes de test sont supprimés uniquement par le teardown du conteneur jetable.

**16 DOM, 21 HTTPS et 118 SQL/HTTP** restent requis sur l’arbre gelé sans échec,
erreur ou skip. Huit manifestes système doivent correspondre à Quality. Les
résultats finaux sont dans le checkpoint compagnon après gel documentaire.
L’intermittence DOM historique de 6477faf8 reste non résolue ; un nouveau passage
vert ne démontre pas sa correction.

## Limites et suite

Identité neuve uniquement. L’upgrade peut réobserver une identité déjà créée et
prouvée, jamais adopter un compte arbitraire. Le choix numérique libre ne prouve
pas l’absence de fichiers historiques d’un ancien UID ailleurs sur l’hôte :
inventaire et migration restent séparés. Paquets officiels, TLS/proxy, activation
Web, producteurs/stockages exhaustifs, sauvegarde complète 5C2, vraie transition
5C3, reprise/rollback 5C4 et wizard 5D restent ouverts. Debian 12/PHP 8.2 qualifie
l’infrastructure seulement ; le Web exige PHP >= 8.3. Les endpoints du banc
sont synthétiques. Aucune production, Gateway ou APK touché ; pas de PR ni
fast-forward avant toutes les Quality et la documentation du périmètre livré.
