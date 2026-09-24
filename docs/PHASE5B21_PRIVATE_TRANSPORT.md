# Phase 5B2.1 - Transport privé Python/PHP

## Frontière et références de reprise

Base Installer relue : `13df634237ba818c199121d23f4bceea8bf2a5b1`.
Base Web relue : `dcb856bc5ef5f35006d2398289b49f5386dcc5f5`, identique sur
main et dev-Bastien lors du démarrage du lot, le 24 septembre 2026.
Les phases 5A et 5B1 sont publiées et qualifiées. Les mentions locales plus
anciennes de certains documents décrivent leur préparation, pas ce point de reprise.

Ce lot fournit uniquement un appel privé au moteur PHP partagé déjà publié.
Aucune nouvelle route HTTP, commande shell libre, opération du registre public,
automatisation de install.php ou mutation du wizard n'est ajoutée.
Le wizard termine toujours par « Sources prêtes ».
Le code applicatif Web, install.php, schema.sql et les migrations sont inchangés.
Il n'y a donc pas de ZIP compagnon Web à réappliquer.

Hors livraison : création du serveur/base/comptes SQL, validation complète des
GRANT, configuration applicative, fichiers de secrets durables, Assistant,
scellement/install.lock, upgrade, sauvegardes, rollback, écrans, installation de
PHP/NGINX, provisionnement des identités et déploiement serveur.

## Interface interne et préconditions

`installer/php_transport.py` expose `PhpTransport`, `PhpRuntime` et
`ProvisioningCredentials`. Ce sont des objets de l'orchestrateur de confiance,
pas des champs librement acceptés depuis un navigateur.

```python
from pathlib import Path
from installer.php_transport import (
    PhpRuntime, PhpTransport, ProvisioningCredentials, WEB_REPOSITORY, WEB_COMMIT,
)

# Valeurs déterminées par l'orchestrateur hôte, jamais reprises d'un payload HTTP.
runtime = PhpRuntime(
    php=Path('/usr/bin/php8.4'),
    extension_dir=Path('/usr/lib/php/20240924'),
    worker_uid=worker_uid, worker_gid=worker_gid,
    run_root=Path('/var/lib/hestia-installer/php-runs'),
    state_root=Path('/var/lib/hestia-installer/php-attempts'),
)
transport = PhpTransport(runtime, acquired_web_source,
                         repository=WEB_REPOSITORY, commit=WEB_COMMIT)
# Les deux secrets ci-dessous proviennent de canaux privés en mémoire.
credentials = ProvisioningCredentials(provisioning_user, provisioning_password)
result = transport.fresh(validated_5a_payload, credentials, confirmed=True)
# Après un incident : observation indépendante, jamais une autorisation de rejeu.
observation = transport.inspect(validated_5a_payload, credentials)
```

Les chemins PHP/ABI de l'exemple sont à déterminer sur la cible, sans supposer
qu'ils existent partout. PHP 8.3+ est nécessaire aux dépendances Web épinglées.
Le parent Python est root. Le compte enfant doit déjà exister : UID/GID non nuls,
compte non interactif, home inexistant ou /var/empty, groupe dédié sans autre
membre ni autre compte primaire. Il doit être distinct de l'identité Web indiquée
par le contrat 5A. Ne pas recycler www-data ou nobody. Le lot ne crée pas ce compte.
`run_root` doit exister, appartenir à root, être en 0711, hors d'un webroot.
`state_root` doit rester stable et privé (0700) entre les exécutions ; il est créé
avec le mécanisme dirfd sécurisé existant. Ne pas remplacer cet état par un
répertoire temporaire à chaque appel. Les fichiers Installer et sources Web sont
root-owned, non modifiables par le service Web ni par le compte enfant.

La cible doit être une base MariaDB locale déjà provisionnée et vide pour fresh.
Seuls managed/existing_local avec 127.0.0.1 ou localhost sont admis ; localhost
est explicitement converti en TCP 127.0.0.1, jamais en socket implicite.
Le port est celui du contrat 5A. Remote, IPv6 et TLS distant sont refusés dans
ce lot : ils nécessitent la frontière de connexion prévue pour 5B2.2.
Un compte de provisioning dédié, distinct du compte applicatif 5A, doit disposer
des droits nécessaires sur la base. Le nom root est refusé. Cela ne constitue
pas un audit des privilèges réellement accordés au compte : ce contrôle reste
à réaliser en 5B2.2. La visibilité des objets inspectés dépend de ces droits.

## Provenance et fichiers exécutés

L'adaptateur vérifie à la fois le dépôt, le commit Web exact ci-dessus et
l'empreinte SHA-256 fermée du contenu réellement copié :

`646653d2785a1ed1d933bf56ff40ee0cc2f3ae35666f6f553ff23312a0079ca8`

Le périmètre comprend includes/installation/core.php, fresh.php,
includes/version.php, sql/schema.sql et tous les fichiers vendor : 721 fichiers
sur cette version. L'empreinte est calculée sur la liste triée, chaque entrée
étant le JSON ASCII compact `[chemin_relatif,taille,sha256]` suivi d'un LF.
Le bridge PHP appartient au paquet Installer de confiance, pas au répertoire Web.
La copie privée est exécutée, jamais un fichier applicatif mutable en place.
La version épinglée n'est pas une recommandation de rester indéfiniment sur cette
release : toute autre version/empreinte doit être examinée et requalifiée avant
activation. Les sources privées continuent d'être acquises, pas embarquées ici.

Les composants de chemin sont contrôlés ; liens symboliques, liens physiques,
fichiers spéciaux, propriétaires étrangers et écritures groupe/autres sont
refusés. Un ancêtre sticky root tel que /tmp est permis, pas un parent mutable
ordinaire. Le compte enfant lit la copie root:groupe-enfant (dossiers 0750,
fichiers 0640) sans pouvoir l'écrire. includes/db.php, la configuration Assistant,
les fichiers PHP de secrets et install.php ne sont ni lus ni exécutés.
La confiance dans root et dans l'installation système PHP demeure une précondition.
Cette protection n'est pas une sandbox contre un administrateur root hostile.

## Contrat fermé, secrets et bornes

Version de protocole : entier 1. Deux opérations seulement : fresh_database et
inspect_database. Identifiant aléatoire de requête vérifié à la réponse.
Le mapping réutilise la validation 5A puis extrait explicitement les données
nécessaires : cible SQL, credential de provisioning séparé, identité et mot de
passe du premier administrateur pour fresh. Le mot de passe SQL applicatif,
la clé OpenAI, le hostname et le webroot ne sont jamais transmis au processus.
Le mode upgrade est refusé avant lancement.

Les secrets passent dans un pipe stdin anonyme, pas dans argv, l'environnement,
une URL, un fichier de configuration ou le journal. L'environnement enfant est
réduit à PATH/LANG/LC_ALL/TZ. Aucun proxy, token GitHub, variable PHPINI/PHPRC,
préchargement ou secret hérité du parent n'est transmis.
PHP démarre avec -n, extensions PDO/MySQL explicitement chargées, configuration
PHP fermée, erreurs et traces non affichées, arguments d'exception masqués,
fichiers auto-prepend/append désactivés. open_basedir limite les accès PHP à la
copie privée. Le lancement passe par setpriv : UID/GID abaissés, groupes annexes
vidés, capacités supprimées, no-new-privileges, parent-death signal KILL.
Aucun shell ni preexec_fn Python n'est utilisé.

| Limite | Valeur |
| --- | --- |
| Requête JSON ASCII | 16 384 octets |
| stdout / stderr | 4 096 octets chacun ; stderr jamais conservé |
| Délai processus | 60 s par défaut, configuration de confiance de 0,05 à 180 s |
| Connexion PDO | timeout demandé de 5 s, doublé par le délai mural du parent |
| Copie de sources | 30 s, 10 000 entrées, profondeur 32 |
| Fichier / total de copie | 8 Mio / 64 Mio |
| PHP / espace virtuel processus | 128 Mio / 512 Mio |
| CPU / fichiers ouverts / processus par UID | 120 s / 64 / 16 |
| Core dump / écriture fichier enfant | limites système à zéro |

Les pipes sont servis simultanément et de façon non bloquante. Timeout,
interruption et sortie excessive entraînent une demande de SIGKILL au groupe de
processus et une attente bornée. Cela ne prouve pas l'arrêt immédiat d'une requête
SQL distante côté serveur ou d'une tâche noyau non interruptible : l'interlock
reste bloqué dans tous les cas. Les secrets résident temporairement en mémoire
Python/PHP ; aucune garantie d'effacement cryptographique de ces copies n'est
revendiquée. Root, un débogueur autorisé ou un système compromis restent hors menace.

JSON ambigu/dupliqué, champs supplémentaires, types approximatifs, mauvais
identifiant/version/opération, compte d'instructions invalide, texte brut,
stderr non vide et désaccord JSON/code de sortie sont rejetés. Le bridge vérifie
une représentation canonique avant toute connexion. Succès : exit 0 ; erreur
fermée : exit 20. Aucun message PDO, stack trace ou flux brut n'est publié.

## États, interruption et impossibilité de rejeu aveugle

Avant toute émission d'une requête fresh, un marqueur O_EXCL est créé et fsync
sur fichier et répertoire. Sa clé dépend de la cible canonique host/port/base,
pas du request_id ni du compte SQL. Le fichier root 0600 ne contient que version,
identifiant et états/codes fermés. Il ne contient ni credentials ni résultat brut.
Deux appels concurrents ne peuvent donc pas tous deux franchir cette frontière.
Le fichier reste bloquant après succès, refus, timeout, réponse perdue ou crash.
Une nouvelle instance Python, un nouvel identifiant et de nouveaux credentials
n'autorisent pas à rejouer. Il n'existe aucune API de reset/retry dans ce lot.

Le résultat de succès fresh reste uniquement DATABASE_READY, avec
application_installed=false et assistant_enabled=false. Une erreur de transport
après dispatch, un échec SQL partiel ou un défaut de libération du verrou mène à
MANUAL_ACTION. Après un crash du parent, DISPATCHING persistant doit également
être traité comme indéterminé et bloquant. La base ne doit pas être supprimée ni
recréée automatiquement. Le moteur MariaDB partagé conserve ses propres refus
sur base non vide et son verrou coopératif.

inspect_database effectue uniquement des SELECT sur la version MariaDB et les
objets de la base. DATABASE_OBSERVATION donne les comptes tables/vues, routines
et événements, avec empty, retry_authorized=false, application_installed=false.
Elle ne vérifie pas une installation complète, ne certifie pas la lisibilité de
tous les objets sans les GRANT appropriés et ne déverrouille pas le marqueur.
Une base vide observée n'autorise donc jamais un second fresh automatique.
La décision de restauration/reprise, les preuves plus complètes et l'éventuelle
levée du blocage appartiennent à un opérateur puis au lot 5C.

La copie privée est nettoyée à la sortie normale. Une terminaison brutale du
parent peut laisser une copie root restreinte sans credential et le marqueur
bloquant. La collecte de ces copies orphelines et le pilotage de reprise ne sont
pas présentés comme livrés. Les DDL ne sont pas annulés atomiquement.

## Tests et qualification

36 nouveaux tests unittest couvrent contrat, Unicode, credentials, erreurs,
version, limites, vrais pipes/processus, timeout, interruption, descendants,
identités réduites réelles, provenance, liens, modes et interlock durable.
Leurs sources moteur synthétiques sont strictement des fixtures unitaires :
elles ne remplacent pas le test du moteur Web réel. Les tests historiques sont
conservés et l'inventaire obligatoire est augmenté, pas assoupli.
La Quality Debian 12/13 installe les outils PHP de test dans des conteneurs jetables.
La syntaxe du bridge est compatible avec le PHP de Debian 12 ; cela ne change
pas l'exigence PHP 8.3+ du vrai vendor Web.

Le banc opt-in `tests/integration/php_transport_mariadb.py` exécute six scénarios
sur le Web exact épinglé et une MariaDB strictement dédiée aux fixtures : fresh
réel et conservation du premier compte, données Unicode/longues déjà présentes,
refus d'upgrade, DDL partiel, réponse perdue après mutation réelle, crash parent
après mutation et inspection non autorisante. Les credentials du contrôleur de
fixtures passent eux aussi par stdin. Les bases/comptes sont aléatoires et seuls
ces objets sont supprimés. Ne jamais lancer ce banc sur un serveur de production.
La fixture GRANT utilise le pair exact de USER(), validé localhost/IP, sans joker.

```bash
# Conteneur Debian root jetable ; outils indiqués dans QUALITY.md.
./scripts/quality-local.sh
# Environnement de test SQL explicitement isolé ; credentials de fixture en env.
HESTIA_TRANSPORT_DB_TEST=1 python3 tests/integration/php_transport_mariadb.py \
    --web-source /opt/hestia-web-qualified
```

Les résultats effectifs, versions, commits et campagnes du lot sont fournis dans
les rapports de livraison et artefacts de ses nouveaux runs. Un PASS historique
5A/5B1 ne qualifie pas ce code. Le banc local sans pdo_mysql ne vaut pas une
recette MariaDB. Les parcours navigateur historiques bridge et HTTPS natif
restent requis par la Quality permanente, même si aucun écran n'a changé.

Références techniques :
[Python subprocess](https://docs.python.org/3/library/subprocess.html),
[options PHP CLI](https://www.php.net/manual/en/features.commandline.options.php),
[PDO et DDL](https://www.php.net/manual/en/pdo.transactions.php),
[setpriv](https://man7.org/linux/man-pages/man1/setpriv.1.html).
