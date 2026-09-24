# Phase 5A - Validation de la configuration Web

## Livraison isolée et périmètre

Base Installer : `7da00daa0069e8af8dadf89dad91037b3c00514a`.
Base Web relue : `c3973361c68febc1f0295020707e50bf73acaf0c`.
Le contrat d'exécution commun Web reste suivi par hestia-nexus-avv#135.

Ce lot fournit un validateur Python de saisies et sa route HTTPS authentifiée
`POST /api/web/config/validate`. Il n'installe rien. Il ne modifie ni le wizard
visuel, ni les journaux, ni une base SQL, ni les permissions du système cible.
Aucun fichier du dépôt Web, schema.sql ou install.php n'est modifié.

La réponse annonce toujours `scope: INPUT_ONLY` et `deployment_ready: false`.
Elle n'est ni un plan approuvé, ni une autorisation de déployer, ni une preuve de
compatibilité avec le contrat PHP encore à réaliser. Aucun adaptateur de mutation
n'est enregistré dans ce lot. Le parcours historique reste « Sources prêtes ».

## Découpage de la Phase 5

1. 5A : contrat de saisie Web, validation serveur, secrets éphémères et tests.
2. 5B : moteur Web partagé et installation neuve MariaDB/admin/Assistant,
   sans automatiser un navigateur contre install.php ; ZIP compagnon Web si nécessaire.
3. 5C : upgrade réellement exécuté, sauvegarde, migrations existantes, reprise
   et limites du rollback ; aucun rejeu aveugle des migrations historiques.
4. 5D : raccordement des écrans, Apache/PHP-FPM, droits des services, puis
   recette intégrée fresh/upgrade et non-régression complète.

Les lots suivants doivent relire les derniers commits des dépôts concernés.
Ils ne doivent pas supposer que les essais non publiés des tentatives interrompues
sont récupérables ou qualifiés. Les seules preuves de ce lot portent sur ses sources
et les rapports de sa propre campagne Quality.

## Schéma d'entrée v1

Le JSON est un objet fermé. Tous les champs suivants sont requis :

| Objet | Champs |
|---|---|
| Racine | version = 1, mode, web, database, administrator, assistant, secrets |
| web | hostname, webroot, service_user |
| database | mode, host, port, name, user, tls_ca_file |
| administrator | first_name, last_name, email en fresh ; null en upgrade |
| assistant | action : disabled, configure, preserve |
| secrets | database_password, admin_password, openai_api_key |

Le mode d'installation est `fresh` ou `upgrade`. Le hostname Web est un FQDN ASCII
normalisé en minuscules, pas une URL. Le webroot est un sous-dossier de /var/www
ou /srv, jamais ces racines. Le compte de service est un nom Linux non privilégié,
pas root ou nobody. Les chemins ne peuvent contenir traversée, lien logique `..`,
composant vide, antislash, caractère de contrôle ni composant trop long. Les liens
symboliques réels ne sont PAS inspectés par cette validation pure : les adaptateurs
ultérieurs doivent vérifier chaque composant du chemin et la propriété des ressources.
Les espaces, accents et ponctuations usuelles sont conservés dans les noms et chemins.

MariaDB : `managed` pour une instance neuve locale au port 3306 ; `existing_local`
pour une base locale existante ; `remote` pour un hôte distant. L'upgrade ne doit
pas sélectionner managed. Les modes locaux n'acceptent que localhost ou une IP
loopback ; le mode distant refuse ces valeurs et impose un chemin de CA externe au
webroot. Ce chemin n'est pas une preuve TLS : le lot d'exécution doit vérifier le
certificat et le nom serveur. Aucune option permettant d'ignorer TLS n'est acceptée.
Une résolution DNS vers une adresse locale n'est pas détectée ici ; cette vérification
reste impérative lors du contrôle réel de la cible et de la connexion SQL.

Port : entier strict 1 à 65535, jamais booléen/flottant/chaîne. Base : 1 à 64
caractères ASCII alphanumériques ou underscore ; schémas système refusés. Utilisateur
SQL : 1 à 32 caractères de même alphabet, compte root refusé. Il s'agit du compte
applicatif, pas du futur credential de provisioning privilégié. Le mot de passe
applicatif SQL est obligatoire, inchangé, limité à 1024 caractères sans contrôle.
L'authentification socket sans mot de passe n'est pas prise en charge par ce schéma v1.

En fresh, premier administrateur obligatoire : prénom/nom 1 à 100 caractères,
email ASCII usuel borné et mot de passe de 12 caractères minimum, 72 octets UTF-8
maximum. Cette limite évite une troncature dans un backend utilisant bcrypt.
Le mot de passe n'est jamais tronqué, trimé ou normalisé. En upgrade, administrator
reste null et admin_password reste vide : cette route n'autorise aucune recréation
ou réinitialisation implicite d'un administrateur existant.

Référence technique : [PHP password_hash](https://www.php.net/manual/en/function.password-hash.php).
Les politiques effectives du moteur Web seront revérifiées au lot 5B : une validation
de saisie n'exécute pas FILTER_VALIDATE_EMAIL, le hachage PHP ni une insertion SQL.

## Assistant et secrets

`disabled` exige une clé vide et fixe desired_enabled à false. `configure` sans clé
est normalisé en disabled avec le code ASSISTANT_DISABLED_NO_KEY. Avec une clé, seul
son format est contrôlé : desired_enabled vaut true, sans annoncer une connexion
OpenAI réussie. Le format suit le validateur Web actuellement relu : 20 à 500
caractères ASCII alphanumériques, underscore, point ou tiret. Deux placeholders
connus sont également refusés ; un format correct ne prouve jamais une clé valide.

`preserve` est réservé à l'upgrade, avec clé vide et desired_enabled null : le
véritable état n'est pas inventé. Le moteur ultérieur devra le relire. Pour remplacer
une clé existante, sélectionner configure ; pour désactiver, sélectionner disabled.
L'état runtime n'est modifié par aucune de ces sélections dans le présent lot.

Les trois secrets ne vivent que dans la requête courante. Ils ne sont ni retournés,
ni stockés dans SecretVault, le journal, le brouillon ou un fichier. Le validateur
ne prolonge pas leur durée de vie ; Python ne garantit pas l'effacement physique
des copies mémoire. L'appelant ne doit jamais persister le payload intégral.
La présence d'un secret fourni dans une valeur publique, même après normalisation
de casse, est refusée. La route applique aussi le filtre des secrets déjà connus
par le moteur, notamment le credential GitHub. Les secrets très courts peuvent
provoquer un rejet conservateur s'ils apparaissent dans une valeur publique.

Aucun POST public, aucune query string de credential, aucun stockage navigateur.
Le mini-web conserve ses contrôles de session, CSRF, Host/Origin, no-store, parsing
JSON strict et taille maximale. Les erreurs ne retournent que des codes fixes.
La gestion définitive de la clé réutilisera le mécanisme Web hors webroot, pas un
second magasin concurrent. Référence :
[OpenAI - API key safety](https://help.openai.com/en/articles/5112595-best-practices-for-api-key-safety).

## Qualité et limites

Les nouveaux tests couvrent les deux modes, les transitions Assistant, la séparation
des secrets, les schémas fermés, textes longs, UTF-8, champs vides, nombres atypiques,
contrôles TLS déclaratifs, injections DSN/chemin, session, CSRF, Origin, doublons JSON,
corps trop grand, concurrence et absence de mutation du journal. Le validateur est
également testé avec accès réseau, processus et ouverture de fichier interdits.

Les suites historiques core, DOM et navigateur natif restent requises, avec leurs
identifiants conservés dans tests/quality-baseline.json. Les comptes et résultats
sont ceux des rapports de la campagne finale, pas une promesse de couverture totale.
Le workflow existant exerce Debian 12/13 et vérifie l'identité du package testé.
Aucun nouveau workflow, aucune dépendance et aucune compilation APK ne sont ajoutés.

Ce lot ne prouve pas une installation/upgrade SQL, un contrôle réel des droits,
un accès aux trois dépôts privés avec PAT utilisateur ou un appel OpenAI effectif.
Ces frontières sont explicitement dans pending_checks et dans les lots suivants.
La création d'un nouveau formulaire applicatif reste dans le lot 5D, UX figée.

## État de publication du lot local

L'envoi de installer/web_config.py via create_blob a été bloqué par la plateforme
pendant cette reprise. Aucune publication Phase 5A ni modification de main n'a
été effectuée. Aucun autre canal de publication n'a été utilisé pour contourner
ce refus. Le ZIP local est un lot d'intégration, pas une release qualifiée en CI.

Le test natif local s'arrête sur `net::ERR_BLOCKED_BY_ADMINISTRATOR` à la navigation
HTTPS loopback. La politique du navigateur n'a pas été modifiée. Le banc DOM
historique peut être exercé hors navigation native, avec son pont HTTPS existant.
Les deux tests ne sont pas interchangeables : les validations natives et Debian 12
restent à exécuter via le workflow existant après intégration autorisée du lot.
Aucune réussite du workflow précédent n'est réutilisée comme preuve du nouveau code.
