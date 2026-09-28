# MariaDB et fresh sur serveur vierge — 5D5b

Ce profil prolonge les [paquets officiels du wizard](PHASE5D_PACKAGE_WIZARD.md)
sur Debian 13. Il conserve les dix étapes fresh, les cinq étapes d'activation
locale et leurs contrôleurs acquis. Le hero, les styles et le code Web restent
inchangés. Les anciennes campagnes dédiées ne sont pas rejouées pour ce lot.

## Plan et consentement

Après les deux plans de paquets terminés, le panneau MariaDB expose un troisième
plan : compte système verrouillé, initialisation neuve, démarrage local, création
de l'autorité SQL. Une confirmation SHA propre à ce plan autorise ces effets.
Le choix est lié à l'identifiant et au SHA du plan de paquets installé. Les
requêtes n'acceptent aucun chemin, commande, port ou nom de compte arbitraire.

Le journal privé `mariadb/state.json` est distinct du journal Web. Le profil est
immuable et écrit exclusivement. Reprendre reconstruit et compare chaque
StepSpec. Le verrou du journal principal sérialise aussi les mutations SQL ;
un plan principal existant bloque toute nouvelle préparation MariaDB.
GET, rapport CLI, rafraîchissement et reconstruction lisent uniquement les
métadonnées privées. DONE décrit un résultat acquis, pas une sonde actuelle.

## Instance dédiée

L'identité SQL dérive de l'identifiant de paquets ; elle est distincte des
identités Web et PHP. Le contrôleur d'identité système acquis crée ce compte.
`/var/lib/hestia-mariadb-<identifiant>/data` est créé exclusivement : aucune
adoption, suppression ou réparation d'un répertoire existant. Le répertoire
Debian `/var/lib/mysql`, éventuellement créé par le postinst, reste inutilisé.
L'initialiseur officiel s'exécute sans privilèges root avec des limites bornées,
sans base test, sans lecture des configurations par défaut. L'arbre créé est
vérifié et synchronisé avant le reçu durable.

Une configuration fermée et une unité dédiée lancent MariaDB sur
`127.0.0.1:3306`, avec socket privé pour l'administration root locale par
unix_socket. Les journaux SQL général/lent/binaire et LOCAL INFILE sont
désactivés. L'unité ne possède aucune section Install ; aucun enable, restart
ou démasquage du MariaDB Debian n'est effectué. Les reçus de paquets continuent
à vérifier les services Debian masqués. Fichiers, outils, propriétés systemd,
PID, exécutable, arguments et cgroup sont vérifiés avant toute observation SQL.

## Autorité et secrets

L'autorité `hba_<identifiant court>` est un compte DBA de cette nouvelle instance,
limité à l'hôte SQL `127.0.0.1`, avec ALL PRIVILEGES global et GRANT OPTION,
nécessaires au contrat managed acquis. Son mot de passe choisi par l'opérateur
doit faire au moins 20 octets. Il reste dans un coffre distinct en mémoire,
est effacé à la fin ou à la fermeture et doit être ressaisi pour le fresh.
Le protocole d'initialisation transmet uniquement le vérificateur natif MariaDB
par stdin privé ; ni mot de passe ni vérificateur n'apparaissent dans les
arguments, journaux Installer, reçus ou messages d'erreur. Le stockage normal
du vérificateur dans les tables système MariaDB reste attendu. L'autorité est
conservée pour l'administration ultérieure ; aucun secret n'est récupérable
depuis l'Installer après redémarrage.

Le formulaire Web impose le mode managed et les comptes d'autorité/préparation
du profil. Le contrôleur SQL acquis crée lui-même le compte applicatif limité
au CRUD et le compte temporaire `hbm_<identifiant court>`, puis supprime ce
dernier après le fresh. Aucun compte de migration préexistant n'est ajouté.
Avant configuration, plan et exécution fresh, les reçus et l'instance SQL sont
observés à nouveau. Le profil fresh historique sans préparation de paquets
conserve son fonctionnement sur hôte déjà préparé.

## Reprise et limites

Chaque effet porte une intention durable liée au plan avant son lancement.
Une réponse perdue après initialisation, démarrage ou création complète du DBA
est reconnue par observation, sans rejouer l'effet. Une création SQL sans reçu
final, un démarrage incertain ou une dérive reste manuel. Aucun rollback SQL,
effacement de données ou tentative aveugle de réparation n'est proposé.
Une reprise ciblée termine cette étape ; Reprendre poursuit les suivantes.

La recette dédiée part d'un Debian 13 minimal sans PHP/FPM, Apache ou SQL.
Seul le contrôleur de paquets produit installe ces dépendances. Après acquisition,
le réseau est coupé ; Chromium reste dans un conteneur séparé. Le vrai wizard
prépare MariaDB, applique le fresh sur le Web complet épinglé, puis active le
backend local. La vérification native contrôle le compte administrateur unique,
les privilèges CRUD, la disparition du compte temporaire et un login réel via
un frontal TLS privé de fixture. Cinq clones jetables couvrent trois réponses
perdues, un reçu SQL incomplet et une configuration modifiée. Les nouveaux
contrats unitaires utilisent uniquement fichiers et mocks sur l'hôte de travail.

Le boot, un frontal public, un certificat public et la clôture de phase 5
restent hors de cette livraison. Le login via TLS privé de recette n'est pas
une livraison du frontal public. Les branches actives ne sont pas promues.

Références primaires : [mariadb-install-db](https://mariadb.com/docs/server/clients-and-utilities/deployment-tools/mariadb-install-db),
[authentification MariaDB](https://mariadb.com/docs/server/security/user-account-management/authentication-from-mariadb-10-4),
[unix_socket](https://mariadb.com/docs/server/reference/plugins/authentication-plugins/authentication-plugin-unix-socket).
