# Phase 5D2 - Composition du fresh Web depuis le wizard

Ce lot raccorde un parcours fresh réel au wizard, jusqu'à la préparation du Web
et des services sous maintenance. Il ne déclare ni l'installation globale
terminée, ni la disponibilité du site. Le parcours historique d'acquisition des
sources reste disponible, avec ses anciens plans et leurs digests inchangés.

## Profil explicite

Le choix « Préparer une nouvelle instance HESTIA Web » sélectionne uniquement
Web/fresh et le build à stockages externes `2a27c7a1f9fe0a00289eb53278f75d5f230900b7`.
Il requiert un hôte Debian 13 déjà doté d'Apache, PHP 8.4, systemd/cgroups et d'une
MariaDB accessible sur `127.0.0.1:3306`. Les dépendances officielles doivent être
installées avant ce parcours. Le profil refuse Debian 12 et les dépendances
manquantes avant de créer un plan applicatif. Il ne lance pas APT ni MariaDB.

L'opérateur choisit le DNS, une base locale gérée ou une base locale vide avec ses
comptes existants, le premier administrateur et l'Assistant facultatif. Le compte
SQL de préparation est distinct du compte applicatif. Le mode géré requiert un
compte d'autorité SQL dédié. Les privilèges effectifs et l'absence de données sont
contrôlés par les contrôleurs SQL acquis, pas présumés par la validation du formulaire.
Le profil public n'expose ni commande, ni compte système, ni chemin de contrôleur.
Remote SQL, adoption d'un Web existant et upgrade applicatif restent hors de ce
nouveau formulaire. Le mode upgrade historique continue de préparer des sources.

## Plan immuable avant toute création

Une identité aléatoire de 32 caractères hexadécimaux est fixée lors du premier
enregistrement. Le profil serveur en dérive le compte Web, un worker séparé,
les chemins `/srv/hst-<instance>`, `/var/lib/hst-<instance>` et
`/var/lib/hst-config-<instance>`, ainsi que les noms des unités. L'écoute backend
prévue est locale sur le port 9080, avec le contrat proxy fermé déjà qualifié.
La création native du runtime refuse un conflit ; aucune écoute ne démarre ici.

Les dix étapes sont reliées par des dépendances explicites : contrôle du profil,
acquisition du commit, identité Web, identité worker, répertoires, déploiement
intégral, SQL fresh, finalisation, Apache/PHP-FPM sous maintenance et nettoyage
des sessions. Le plan est inspectable avant son approbation. Chaque étape conserve
son contrôle natif et son checkpoint transactionnel avant mutation.

Les UID/GID sont résolus après la création des comptes. Cette résolution doit
produire exactement le StepSpec approuvé. Elle ne peut changer le plan après SQL.
La finalisation reçoit désormais une identité serveur facultative : le contrat
historique sans identité explicite reste inchangé. Un observateur planifié refuse
un sceau cohérent portant une autre identité.

Le contrôleur SQL conserve son pin de protocole historique : les fichiers du
protocole et le schéma de ce build storage ont les empreintes SQL déjà qualifiées.
Le déploiement, lui, contrôle l'arbre Git complet du build storage. Ce choix ne
constitue ni une migration SQL nouvelle ni un assouplissement des empreintes.

## Brouillon et secrets

`application.json`, dans le même répertoire privé que le journal, contient
uniquement version, révision, identité et configuration normalisée. Il est borné,
canonique, écrit atomiquement en 0600 et protégé contre liens et droits inattendus.
Le verrou du journal sérialise les modifications et la création du plan. Une
révision périmée ne remplace pas les choix ; un plan existant les fige. Le retrait
explicite d'un plan jamais approuvé autorise à nouveau leur édition.

Les routes authentifiées `/api/web/setup` et `/api/web/credentials` utilisent les
mêmes protections HTTPS/session/Origin/CSRF que le wizard. Les secrets restent
dans SecretVault, sans hash persistant. Les champs sont vidés après soumission,
changement de page et sortie. La fin du parcours, la déconnexion et l'arrêt du
bootstrap effacent les références détenues par le coffre. Python ne garantit
pas l'effacement physique de toutes les copies mémoire.

La ressaisie exige le digest exact du plan, refuse les noms inconnus ou étrangers
au plan, et n'applique aucune opération. Un GET ne crée ni identité, ni journal,
ni répertoire et ne lance aucun contrôle SQL. La réouverture CLI reconstruit tout
le registre à partir du brouillon et vérifie la liste complète des StepSpecs.
Une configuration changée, manquante ou un sous-ensemble du plan est refusé.

## Reprise et limites conservées

Un compte créé mais dont la réponse est perdue est reconnu par son contrôleur.
Un SQL/finalisation complet est reconnu par les adaptateurs 5D1. L'identité du
sceau et les noms de services restent identiques après redémarrage. Un répertoire
de profil partiel sans reçu reste manuel ; il n'est pas adopté ou supprimé.
Le code ou les sources ayant dérivé restent bloquants. Les étapes DONE ne sont
pas rejouées, et les secrets peuvent être renouvelés pour une reprise ciblée.

Le résultat affiché est « Web préparé sous maintenance ». Les services restent
arrêtés et l'admission fermée. L'autorisation et le démarrage produit, les tests
de disponibilité, le frontal public, la préparation automatique des dépendances
et de MariaDB, ainsi que le wizard upgrade restent à composer. Le profil 5C4
upgrade/reprise acquis et les adaptateurs 5D1 sont conservés. Phase 5 et #13 restent
ouverts. Gateway, APK et import restent des périmètres distincts.

## Qualification

Les tests de composition utilisent les vrais fichiers privés et le moteur, sans
SQL ni comptes locaux sur le poste de travail. Les suites navigateur historiques
et natives couvrent le formulaire, le consentement, le refresh et la ressaisie.
La recette `application_wizard_systemd.py` utilise un conteneur jetable Debian 13,
les sources Web exactes et le vrai navigateur : fresh local existant, fresh géré,
finalisation avec réponse perdue, compte worker avec réponse perdue, répertoires
partiels et reprise SQL après renouvellement des secrets. Un login TLS de recette
démarre explicitement les services en fixture ; il ne transforme pas ce démarrage
en fonctionnalité produit livrée. Les résultats mesurés et les pins du gel sont
consignés dans le checkpoint. Les campagnes 5C4 acquises ne sont pas rejouées.
