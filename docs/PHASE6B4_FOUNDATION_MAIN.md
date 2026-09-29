# Phase 6B4 — raccordement privé Foundation MAIN

Candidat sur la base qualifiée 6B3 `27c654de0d6c8ba3b0e1c4412cb043e264327b90`. La qualification native reste nécessaire avant toute déclaration de disponibilité.

## Contrat

Le Web reste épinglé à `2a27c7a1f9fe0a00289eb53278f75d5f230900b7` et écoute sur 9080. Le paquet Gateway qualifié reste `e2c09f53593bf316906ccc4387f185e73e7f85a8` ; aucun nouveau choix de branche flottante.

Le plan `/api/gateway/foundation/{plan,apply,resume,retry,check}` lie les SHA-256 des trois plans Web, activation et préparation Gateway terminés, le draft Web et la JWK MAIN existante. Il possède son journal séparé et prend le verrou du parent. GET lit uniquement les métadonnées. Toute mutation demande le consentement au plan exact, puis revalide le paquet et les clés. Les adaptateurs historiques restent inchangés.

Trois étapes : création exclusive des fichiers et de l’unité, démarrage explicite, vérification signée. Le listener est fixé à `127.0.0.1:9082`, jamais 9080. Les fichiers appartiennent à root et seule la JWK publique est lisible par le groupe Web. La clé privée reste dans le coffre initial. La configuration Apache dérive du blob Web `cbc20981ebf4030faaac1ed62c5fb58161492919`, ajoute le refus des méthodes autres que POST et des queries, et ne crée aucune route supplémentaire.

Le service partage le socket FPM existant : son `auto_prepend_file` applique le verrou de maintenance pendant toute la requête. Aucun pool, compte SQL, utilisateur système, unité Web ou bundle boot existant n’est modifié. Avant une sauvegarde, le contrôleur ferme le verrou de maintenance, vérifie Foundation, écrit son intention puis arrête exclusivement son unité possédée. Le recensement UID/GID Web conserve ses règles exactes : aucune exception Foundation. La barrière vérifie Foundation arrêtée pendant toute la sauvegarde. Le conditionnement systemd interdit un démarrage tant que la maintenance est présente. La sauvegarde laisse les services fermés ; aucune réouverture implicite. La reprise composée Web/Gateway/Foundation relève de la suite du raccordement.

Les intentions durables précèdent les effets. Une reprise ne démarre pas à nouveau une unité ambiguë. La propriété positive exige le fragment exact sans drop-in, le PID systemd, son exécutable et ses arguments, son cgroup, son identité de processus et l’inode du listener loopback. Une écoute IPv4/IPv6 étrangère, un répertoire préexistant ou un fragment étranger est refusé sans remplacement ni arrêt.

Le contrôle signé est un `access/check` pour des UUID aléatoires sans utilisateur ni appareil existant : ES256, audience MAIN, durée 30 secondes, chemin/méthode/body hash et request ID liés. Il vérifie la réponse fermée, le rejet du même nonce par SQL puis le refus d’un appel non signé. Seuls les nonces et audits techniques sont écrits. Le reçu DONE est historique ; `check` est explicite et ne redémarre rien.

## Qualification

`tests/test_foundation.py` : contrôleur, consentements, parents et clés conservés, GET sans effet, dégâts bloquants, reprise après réponse perdue, état manuel si intention sans processus, signatures OpenSSL réelles et DER fermé, template privé, IPv4/IPv6 et protections session/CSRF/origine de toutes les routes.

`tests/integration/foundation_systemd.py` : une installation Debian 13 jetable avec systemd, Apache, FPM, SQL et navigateur réels. Collision 9082, interruptions SIGKILL après préparation/démarrage, refus négatifs, SQL bloqué avec verrou partagé, maintenance 503 avant SQL, login Web réel et fichiers/PID/clés conservés, puis sauvegarde native Web avec restauration SQL et données isolées. Seul le paquet Gateway utilise la fixture inerte du catalogue : aucun binaire Gateway n’est exécuté ni revendiqué. L’import du véritable paquet est déjà qualifié en 6B3.

## Suite encore nécessaire

Le service Gateway sur 9083, FCM, le frontal HTTPS Mobile, la santé/QR/origine publique Web, la composition au boot et la qualification globale 6C restent à réaliser. Les clés DEV préparées sont préservées ; aucun listener DEV n’est créé sans contexte distinct vérifié. Aucune activation automatique au boot dans ce lot ; aucun déploiement sur LAB ou production, aucune compilation APK, aucune promotion main/dev-Bastien.
