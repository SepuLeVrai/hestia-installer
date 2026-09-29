# Phase 6B3 - import durable du paquet Gateway

Le wizard peut importer le ZIP binaire Gateway qualifié déjà livré avec 6B1.
Son utilisation ne dépend plus de la conservation de l'artefact GitHub Actions
pour les nouveaux plans qui choisissent ce mode. Ce lot prépare le paquet et
les identités ; il ne déploie pas encore de service Foundation ou Gateway.

## Même référence, nouveau mode explicite

La référence reste `e2c09f53593bf316906ccc4387f185e73e7f85a8`, version
`0.12.2-installer.rc1`, Linux amd64, SQLite 6. Le ZIP attendu est
`HESTIA-Gateway-0.12.2-installer.rc1-linux-amd64.zip`, **10 832 222 octets**,
SHA-256 `f3138b5bd4fc5c85e4dcf9e4480d8f521f72c02db34219cb9053c38a1eae8a0e`.
Le nom local n'est pas une preuve et n'est pas envoyé au serveur. Le ZIP externe
d'Actions ou une archive du code source ne remplacent pas ce paquet binaire.

L'utilisateur choisit « Importer le ZIP binaire qualifié » avant de créer le
plan, sélectionne le fichier puis confirme sa transmission et la préparation
des identités. Une sélection annulée est effacée. Aucun fichier n'est conservé
dans le stockage du navigateur, aucune URL ni destination libre n'est acceptée.
Le serveur vérifie tous les octets, checksums, VERSION, modes et chemins selon
le vérificateur 6B2. Aucun contenu du ZIP n'est extrait ni exécuté.

Les plans historiques sans champ `acquisition` conservent le profil et les
StepSpecs 6B2 à l'identique. Le nouveau profil `acquisition: package` utilise
`gateway.binary.import` et reste immuable. Une tentative de conversion d'un
plan existant est refusée. L'import ne change ni le catalogue, ni le journal
Web, ni les identités déjà engagées. Le mode GitHub reste disponible avec ses
contrôles et son expiration historiques. Aucune release GitHub n'est créée.

## Transport et reprise

`POST /api/gateway/preparation/import` accepte un corps `application/zip`, une
taille exacte et `X-Hestia-Plan` égal au hash du plan confirmé. Session, CSRF,
Host et Origin restent requis. Les en-têtes en double, Transfer-Encoding,
compression, type ou hash de plan incorrects sont refusés. La connexion est
fermée à chaque réponse pour ne jamais interpréter les octets non consommés
comme une seconde requête. Le fichier est lu par blocs de 64 Kio maximum ;
l'import est borné à 120 secondes avec le timeout socket historique.

La confirmation puis le checkpoint `apply` sont persistés avant la première
lecture du corps et la création de la ressource gérée. Un upload tronqué ou
altéré laisse un échec explicite et aucune identité nouvelle. Seuls des
partiels privés, possédés par le même profil et non engagés, sont remplacés lors
d'un nouvel import explicite. Un lien, fichier étranger, permission inattendue
ou paquet engagé endommagé bloque. Un reçu durable peut être repris sans
renvoyer le ZIP ; les clés MAIN/DEV restent conservées. GET et rapport restent
des lectures de métadonnées sans vérification de clé ni téléchargement.

Les tests couvrent HTTPS réel, contrôles d'en-têtes, checkpoint avant lecture,
corruption, interruption, reçu durable, préservation des clés, profils anciens
et parcours Chromium avec confirmation, annulation, réimport et actualisation.
Les fixtures sont inertes ; le vrai ZIP qualifié est vérifié séparément sans
exécution. Les tests historiques restent obligatoires.

## Raccordement natif suivant

Les prérequis ont été lus dans les sources exactes Web et Gateway conservées.
Les adaptations suivantes doivent former un plan distinct avec sa propre preuve
Apache/systemd, avant de déclarer un accès Mobile disponible.

| Zone | Constat sur la référence qualifiée | Adaptation nécessaire |
|---|---|---|
| Web `includes/mobile_foundation/web.php` | `hm_web_gateway_online()` sonde le port 9080 | Conserver le défaut autonome, configurer 9083 pour le profil Installer, vérifier via Apache/PHP réel |
| Web QR/bootstrap | Origine historique encore imposée | Origine publique canonique liée au profil, tests des QR et assertions sans élargir les routes |
| Foundation `service.php` | Configuration privée hors Webroot, schéma fermé, JWK publics, JWT ES256 et nonce SQL | Garder ces contrôles, MAIN sur 9082 ; DEV 9081 seulement avec un contexte DEV distinct vérifié |
| Apache interne | Gate Gateway limité aux sites globaux et `apache2ctl` | Prouver les listeners, règles fermées et PHP-FPM du runtime privé sans contourner le gate |
| Gateway `deploy.sh` | Préflight avant installation, port possédé, migration SQLite 6 et restauration N-1 | Fournir le profil 9083 et les credentials dans l'ordre, maintenir toutes les protections et le durcissement systemd |
| Boot et HTTPS Web | Bundle et workers scellés ; overlay TLS reconnu par les contrôles de drainage | Composer explicitement les nouvelles configurations ; ne pas réécrire un ancien profil ni ajouter un drop-in invisible aux vérifications |
| Frontal public | NGINX Web possède déjà 80/443 | Partager le frontal Mobile et renouvellement, avec méthodes et routes fermées |

Web reste épinglé à `2a27c7a1f9fe0a00289eb53278f75d5f230900b7`. Déplacer ce pin
requiert une évolution Web ciblée et qualifiée avec compatibilité des plans
existants. Aucun main/dev-Bastien promu, aucun build APK, aucun compte, APT, SQL
ou service réel manipulé dans Work/LAB. Les recettes natives restent dans la CI
jetable. FCM privé et la recette 6C intégrée restent à réaliser.

Contexte précédent : [acquisition 6B2](PHASE6B2_GATEWAY_ACQUISITION.md) et
[identités 6B1](PHASE6B_PRIVATE_IDENTITIES.md).
