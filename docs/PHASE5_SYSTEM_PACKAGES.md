# Phase 5 — paquets officiels acquis puis installés hors réseau

## Base et périmètre privé

Base qualifiée `870560b61ebc271d8979741b1f6b074a76c93251`, arbre
`0ea74d0cad202137e72f8c757282828b67d96d6c`. Quality `36155640408`, système
`36155640569`, SQL/HTTP `36155918278` : 576 core et 47 recettes système par
Debian, 16 DOM, 21 HTTPS et 118 SQL/HTTP, sans skip. L'erreur initiale useradd
CREATE_MAIL_SPOOL a été corrigée et entièrement requalifiée sur cette base.

`installer.system_packages.SystemPackages` prépare les dépendances d'une
installation neuve. Deux adaptateurs typés privés, `system.packages.acquire`
et `system.packages.install`, restent à enregistrer explicitement par un futur
orchestrateur. Aucun bouton, registre public ni activation Web n'est ajouté.
L'installation demande le SHA-256 exact du plan téléchargé et une confirmation
explicite distincte. Un résultat système ne lève jamais application_installed
ni system_wiring_verified. La Phase 5 reste ouverte.

## Profil de l'hôte

Root, Debian 12 ou 13, architecture native amd64 ou arm64 sans architecture
étrangère, base dpkg entièrement installée et propre. La recette réelle de ce
lot couvre **amd64 uniquement** ; arm64 n'est pas qualifié par ces campagnes.
L'hôte doit être administré exclusivement pendant l'opération : les contrôles
ne protègent pas d'un autre administrateur root modifiant simultanément le système.
Les verrous APT/dpkg normaux restent actifs, sans attente ni réparation forcée.

Refus de tout Apache, nginx, serveur MariaDB/MySQL ou FPM déjà installé,
d'un datadir `/var/lib/mysql`, du journal existant ou des unités par défaut
préexistantes à l'installation. Aucun compte, paquet, service ni base existant
n'est adopté. Les paquets déjà présents, y compris leurs versions, sont conservés.
Un état dpkg partiel, retenu (hold), supprimé avec configuration résiduelle ou
ambigu exige une préparation manuelle de l'hôte.

Dépendances explicites : Apache, MariaDB serveur (également nécessaire au
vérificateur de restauration isolé), PHP CLI/FPM et extensions mysql, mbstring,
curl, xml, zip, gd, passwd, util-linux, ca-certificates. nginx est optionnel.
Les dépendances transitives sont résolues puis figées dans le même plan.
Bookworm reçoit PHP 8.2 officiel, Trixie PHP 8.4 officiel. **Debian 12 ne satisfait
pas PHP >= 8.3 du Web épinglé** ; web_php_compatible reste faux. Aucun dépôt tiers,
backport ou changement du pin Web n'est introduit pour masquer cette limite.

## Acquisition authentifiée et figée

APT_CONFIG est défini dès le démarrage du processus : fichiers de configuration,
sources, préférences, authentification, listes, cache et logs sont propres au
journal `/var/lib/hestia-packages-<instance>`. Les configurations/hook APT et
credentials du site ne sont pas chargés ; aucun token GitHub, proxy ou variable
de session n'est hérité. Les chemins, commandes et options sont construits par
le code, sans shell ni saisie libre. Le fichier status dpkg demeure celui de l'hôte.

Trois suites `main` seulement, en HTTPS sur deb.debian.org : la version Debian,
updates et security. Le trousseau local protégé debian-archive-keyring est exigé
et son empreinte figée. TLS, signatures APT, date et Valid-Until sont vérifiés ;
aucun redirect, dépôt faible/non authentifié ni clé téléchargée implicitement.
Ce contrat suppose un système Debian et un trousseau initialement dignes de
confiance. La simulation refuse toute suppression, mise à niveau ou rétrogradation,
y compris une dépendance déjà présente. Puis le téléchargement demande les
versions exactes de tous les ajouts. Un échec d'index interdit la suite.

Le plan durable comprend état antérieur, outils/configuration dpkg, index signés,
versions, architectures, tailles et SHA-256 des archives. Contrôles de propriétaire,
modes, ACL, liens et lectures bornées. Maximum 300 ajouts, 1 Gio d'archives,
128 Mio par fichier, 256 Mio d'index et journaux de 4 Mio. L'installation doit
commencer dans les 24 heures de l'acquisition ; un recul d'horloge ou un plan
expiré est refusé, sans rafraîchissement automatique. L'observation d'une
installation terminée n'expire pas après ces 24 heures.

## Installation sans démarrage des services par défaut

Avant dpkg : nouvelle observation intégrale du plan, état système inchangé,
simulation identique et systemd PID 1 requis. Les configurations dpkg locales
sont limitées aux options Debian connues : journal standard, no-debsig (signature
assurée par APT), exclusions documentaires et force-unsafe-io neutralisé par
--refuse-unsafe-io. Les hooks, redirections et autres options sont refusés.

Un journal install.attempt est synchronisé avant mutation. Une policy-rc.d
exacte `#!/bin/sh` puis `exit 101` inhibe les démarrages par les maintainer scripts.
Une policy identique préexistante est préservée ; une autre est refusée. Apache,
son collecteur cache, FPM, MariaDB et ses alias/socket, ainsi que nginx si choisi,
sont masqués dans `/etc/systemd/system` **avant** dpkg et restent masqués après
succès. Les états inactifs et cgroups vides sont contrôlés. Le nettoyage PHP
natif n'est pas masqué ou remplacé. Ce n'est pas une garantie contre des scripts
malveillants exécutés en root : les paquets officiels authentifiés sont de confiance.

APT installe avec --no-download, --no-remove et les versions exactes ; aucun
réseau n'est nécessaire. L'état final doit correspondre exactement aux ajouts
prévus sans modification d'un paquet antérieur. Les postinst officiels peuvent
créer comptes, configurations et **datadir MariaDB neuf** ; l'application HESTIA
n'est pas configurée et aucun service par défaut n'est rendu accessible.
Le reçu final est écrit puis la policy créée par cette opération est retirée.
Une policy qui préexistait est conservée. Les masques de services restent en place :
leur retrait appartient à une future activation explicite, pas à ce lot.

## Interruption et observation

Une acquisition incomplète conserve son journal/cache et ne peut pas être rejouée
sur le même emplacement. Une installation partielle, un reçu absent ou une dérive
retourne une erreur fermée et exige une inspection manuelle. Aucun apt --fix-broken,
dpkg --configure -a, suppression, rollback de paquet ou démasquage automatique.
Si l'échec précède la suppression de la policy créée, elle reste présente et peut
bloquer d'autres démarrages par les outils de paquets : inspection manuelle requise.
Un crash entre le reçu et ce retrait reste manuel tant que la policy n'est pas
conforme à l'état attendu. Les masques persistants protègent les services au reboot.

Une réponse perdue après succès peut être récupérée par observation du reçu et
de toutes les empreintes. Cette observation lance uniquement des commandes de
lecture (dpkg-query, dpkg-deb, audit, systemctl show), jamais APT ni une mutation.
L'opérateur doit examiner les journaux privés avant toute récupération manuelle ;
ce lot n'expose aucune commande générique de nettoyage ou réparation.

## Recette requise sur l'arbre gelé

22 nouveaux tests core obligatoires, soit **598 par Debian**. Conservation des
47 scénarios système par Debian (11 drainage + 12 HTTP + 12 sessions + 12 identité),
16 DOM, 21 HTTPS et 118 SQL/HTTP sur le Web épinglé.

Le workflow system-packages ajoute **13 scénarios par Debian** : cinq d'acquisition,
sept d'installation et un échec de reçu après installation réelle. Images Debian
officielles minimales, systemd PID 1, sans Apache/FPM/MariaDB préinstallés. Acquisition
réseau, puis déconnexion Docker vérifiée (interface loopback seule) avant installation.
Un clone jetable du plan acquis exerce l'échec de reçu hors réseau. Aucun port
publié, credential, montage hôte, serveur métier ou runner auto-hébergé.

La matrice exerce sans nginx/policy préexistante sur Debian 12, avec nginx/policy
créée sur Debian 13. Elle vérifie versions exactes, fichiers corrompus/configuration
altérée, policy et unité occupées, masques persistants, extensions PHP, récupération
sans APT et consommation réelle par identité dédiée + HTTP/FPM + collecteur de
sessions. Ces derniers utilisent une fixture HTTP synthétique, pas le Web complet.
Les commandes APT réelles et les contrôles de sécurité ne sont pas simulés.

Chaque rapport, manifeste source et log doit correspondre au commit exact, sans
skip. Tout changement impose une nouvelle qualification. Résultats finaux et ZIP
compagnon après gel ; aucun résultat précédent ne qualifie ce code. Le workflow
Quality principal conserve son packaging de sources, mais une PR/promotion exige
aussi les workflows système et paquets, ainsi que les huit recettes SQL/HTTP.
L'intermittence DOM historique reste non résolue, même si cette campagne est verte.

## Suite encore ouverte

TLS/proxy et activation orchestrée ; profil complet des producteurs et stockages
pour finir 5C2 ; vraie transition versionnée 5C3 ; reprise/rollback 5C4 ; parcours
wizard et recette finale 5D. Aucun pin affaibli, transition fictive, modification
Gateway/APK, PR ou fast-forward ne fait partie de ce checkpoint.

Références Debian : [apt.conf](https://manpages.debian.org/trixie/apt/apt.conf.5.en.html),
[apt-get](https://manpages.debian.org/trixie/apt/apt-get.8.en.html),
[PHP Bookworm](https://packages.debian.org/bookworm/php-fpm),
[PHP Trixie](https://packages.debian.org/trixie/php-fpm).
