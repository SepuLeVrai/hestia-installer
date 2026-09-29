# Phase 6B2 - acquisition et préparation Gateway

Ce lot raccorde le paquet binaire et `GatewayIdentityStore` au wizard et à un
journal distinct. Il ne déploie pas encore Gateway, Foundation ou le frontal
Mobile. `DONE` désigne uniquement cette préparation. La phase 6 reste ouverte.

## Référence fermée

Le catalogue serveur fixe Gateway `e2c09f53593bf316906ccc4387f185e73e7f85a8`,
version `0.12.2-installer.rc1`, Linux amd64, SQLite 6, Quality `36499757403`,
artefact `11005276084`. SHA-256 du paquet :
`f3138b5bd4fc5c85e4dcf9e4480d8f521f72c02db34219cb9053c38a1eae8a0e`.
Aucun ref, URL, chemin de destination ou commande ne vient du navigateur.
L'API vérifie le dépôt, le run réussi, son SHA et son workflow, l'identité,
la taille, le digest et la non-expiration de l'artefact avant téléchargement.
Le ZIP externe, le ZIP interne, tous les checksums, VERSION et le binaire sont
ensuite vérifiés, sans extraction ni exécution. SQLite 6 est la qualification
liée à ces octets exacts, pas une sonde SQL de la machine.

Il n'existe pas encore de release durable pour ce candidat. L'artefact CI expire
le 12 octobre 2026 à 23:57:18 UTC. Indisponibilité, expiration ou dérive bloquent
explicitement, sans repli vers main, un autre run ou une archive source. Le
paquet qualifié livré avec 6B1 reste un livrable hors réseau ; ce lot ne propose
pas encore son import dans le wizard. La distribution durable reste à résoudre
avant de livrer un installeur utilisable après cette échéance.

## Transport et credential

Le jeton reste éphémère, avec les validations Metadata/Contents historiques sur
les trois dépôts. L'acquisition Gateway exige en plus **Actions Read** sur le
dépôt Gateway ; la validation des sources seule ne prouve pas cette permission.
Le wizard l'indique. Référence :
<https://docs.github.com/en/rest/actions/artifacts?apiVersion=2026-03-10>.

Seuls les GET API reconstruits reçoivent Authorization. La redirection fournie
par cette API doit viser un hôte HTTPS Azure Blob canonique sans port explicite.
Le GET signé n'envoie ni Authorization, ni cookie, ni referer. Sa capacité
éphémère reste en mémoire et aucune seconde redirection n'est suivie. TLS vérifié,
absence de proxy ambiant, délais, encodage et tailles bornés restent imposés.
Les corps d'erreur et URL signées ne sont jamais exposés. Le jeton est effacé
après chaque exécution ; une reprise de téléchargement peut demander sa ressaisie.

## Journal et reprise

La préparation exige le journal Web fresh terminé sur le Web qualifié
`2a27c7a1f9fe0a00289eb53278f75d5f230900b7`. Son hash est lié au profil Gateway.
Le même verrou parent sérialise les mutations, sans réécrire le journal Web.
Le profil immuable fixe origine HTTPS, DEV facultatif, identité d'instance et
catalogue. Deux StepSpecs ont leur propre confirmation : acquisition binaire,
puis identités. Les répertoires sont privés 0700 et fichiers 0600.

Seul un téléchargement partiel possédé et non engagé peut être repris. Un
paquet engagé altéré, un fichier inconnu, lien ou droit inattendu bloque.
Les identités suivent le contrat 6B1 : conserver toute clé déjà écrite, refuser
la régénération d'une clé engagée manquante ou remplacée. Les reçus publics
permettent la reprise après une coupure entre l'effet et le journal moteur.
Aucun rollback automatique des identités n'est proposé.

`GET /api/wizard/state`, le rapport HTTPS et `--report` lisent uniquement les
métadonnées. Ils ne téléchargent rien et ne vérifient ni binaire, clé ou service.
`POST /api/gateway/preparation/check` vérifie explicitement paquet et identités,
sans transformer DONE en affirmation de disponibilité réseau. Les mutations
plan/apply/resume/retry/check sont protégées par session, CSRF et origine comme
les routes historiques. Les reprises valident d'abord les ressources terminées.

## Qualification et suite

Tests dédiés : transport, ZIP hostile/corrompu, confirmation, profils fermés,
reprise des téléchargements et clés, coupure au commit, conservation du parent,
rapports sans sondes, protections HTTPS et navigateur natif avec les vrais assets.
La fixture réseau utilise un binaire inerte ; elle ne qualifie pas un déploiement.
Le vérificateur est également exercé sur le vrai artefact 6B1 téléchargé et vérifié.
Les tests dédiés sont ajoutés au socle obligatoire, sans retirer de test historique.

Suite : rendre le paquet durable, puis composer credentials systemd et Foundation
native. Le gate Apache historique, l'origine QR Web et le frontal public partagé
nécessitent encore les adaptations décrites dans le handoff 6B1. Aucune promotion
main/dev-Bastien, modification Web/APK ou recette téléphone dans ce lot.
