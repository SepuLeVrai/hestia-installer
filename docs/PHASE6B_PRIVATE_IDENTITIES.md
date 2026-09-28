# Phase 6B1 - préparation privée P-256

`installer/gateway_identity.py` prépare un répertoire privé distinct pour les
identités Gateway. C'est une brique du futur plan Gateway, pas encore un nouvel
écran wizard ni une installation de service. Aucun StepSpec, reçu Web, bundle
boot, profil HTTPS, code PHP métier ou référence Web acquise n'est modifié.

## Contrat

Le profil immuable fixe instance, origine HTTPS canonique et activation DEV.
MAIN est obligatoire. Le stockage verrouillé écrit d'abord l'intention privée,
puis les clés P-256 indépendantes, puis un reçu de leurs seules identités
publiques. Les clés PKCS8 restent en fichiers 0600 dans un répertoire 0700 ;
OpenSSL reçoit les données privées par stdin, jamais par argument ou journal.
Chaque kid dérive de l'environnement et de l'empreinte RFC 7638 du JWK public.

`prepare()` reprend une création interrompue en vérifiant et conservant les
clés déjà écrites. Une clé engagée manquante, remplacée, partielle, une permission
incorrecte, un lien ou un fichier inattendu provoque un refus. Aucun remplacement
ou adoption silencieuse. Un changement de profil n'est pas une rotation de clé.
Le reçu est exclusif et immuable ; une écriture partielle réclame une réparation
explicite. La rotation et l'import d'identités existantes ne sont pas implémentés.

`report()` lit seulement le profil et le reçu publics. Aucun OpenSSL, réseau ou
sondage privé. Ce reçu reste une preuve historique même si le matériel privé
n'est plus disponible. `verify()` est le contrôle explicite des clés effectives.

`configurations()` produit les configurations non secrètes Gateway et Foundation
à partir des JWK. Gateway vise 9083, MAIN 9082, DEV optionnel 9081 ; les noms de
credentials systemd sont `main-key` et `dev-key`. Ce rendu ne copie pas les clés,
ne crée pas de drop-in, ne donne aucun droit PHP et n'active aucun listener.
Le plan de déploiement suivant doit établir et vérifier ces effets.

## Vérifications

Tests obligatoires : clés distinctes, rerun stable, reprise après la première
clé, absence DEV, choix immuables, refus des fichiers étrangers et altérés,
permissions/liens, point P-256 valide, JWK sans secret, absence de sonde dans le
rapport et origine stricte cohérente avec Gateway.

Les signatures sont vérifiées avec OpenSSL et avec le fichier exact
`includes/mobile_foundation/crypto.php` du Web épinglé
`2a27c7a1f9fe0a00289eb53278f75d5f230900b7`, blob
`3f7e589d3cb1ae37f0ad942b8811712ef69220e6`, copié uniquement en fixture de test.
Le test vérifie ce blob, le thumbprint, un JWS ES256 valide et les refus pour
clé, kid, type et corps incorrects. Cela qualifie l'interopérabilité cryptographique,
pas encore la décision Web/SQL complète ni une assertion via Apache natif.

La Quality habituelle core Debian 12/13 et navigateur est déclenchée sur la
branche technique de ce lot, avec neuf tests ajoutés au minimum obligatoire.
Aucun effet APT/SQL/comptes/services n'est exécuté dans Work. Les unités natives
et paquets système n'ayant pas changé, leurs preuves phase 5 sont conservées.

## Suite de la phase 6

Gateway 6B1 rend public_origin configurable et DEV facultatif. Son contrat et
ses limites sont dans `docs/INSTALLER_PHASE6B.md` du dépôt Gateway. Le Web épinglé
impose encore le domaine historique pour ses QR : toute publication sous une
autre origine exige une évolution Web ciblée et qualifiée. L'APK reste phase 8.

À composer ensuite : acquisition binaire vérifiée, plans et reçus Gateway,
installation des identités, Foundation sur Apache natif, FCM privé, wizard,
NGINX Mobile et recette intégrée 6C. Aucun PASS global phase 6 n'est revendiqué.
