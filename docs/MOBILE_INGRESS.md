# Frontal Mobile à routes fermées — 6B8

Le parcours local 6B7b12 reste figé au commit
`0289ba17ad97e134712e7f96072c115b07e6299d`. Ce lot prépare son exposition
Mobile sans changer les moteurs de maintenance, sauvegarde, reprise ou activation.
Les campagnes natives 6B7b11/12 déjà en cours restent distinctes.

## Contrat rendu

`installer.mobile_ingress.MobileIngress` produit un bloc serveur NGINX TLS,
sans opération système. Le nom DNS, les réseaux clients IPv4 et les chemins de
certificat/clé sont validés. Le listener est fixe sur 443, l'unique destination
est la Gateway Installer `127.0.0.1:9083`, avec connexion sortante depuis
`127.0.0.3`. Web 9080, Foundation DEV 9081 et MAIN 9082 restent inaccessibles
depuis ce frontal. La politique Mobile ne reprend pas l'allowlist Web.

Le contrat est dérivé de la Gateway qualifiée
`e2c09f53593bf316906ccc4387f185e73e7f85a8`. Les cinq fichiers source utiles
et leurs identités Git sont figés dans la fixture de contrat. Les tests comparent
l'inventaire complet, sans utiliser une correspondance de préfixe permissive.

| Surface | Routes | Méthodes | Taille maximale du corps |
| --- | ---: | --- | ---: |
| Santé | 1 | GET | Aucun corps |
| Enrôlement, auth, contextes, push et mise à jour | 14 | POST | 16 384 octets |
| Opérations métier | 42 | POST | 1 048 576 octets, sauf referentials-list à 16 384 |
| Page et ressources bootstrap | 4 | GET, HEAD | Aucun corps |
| Vérification et téléchargement bootstrap | 2 | POST | 1 024 octets et Origin exact |

Toutes les **63 routes** utilisent une correspondance exacte et vérifient
l'URI originale. Chemins inconnus, sous-chemins, casse différente, encodages,
normalisations, requêtes absolues, query strings y compris `?` vide et méthodes
non prévues sont refusés. Host et SNI doivent correspondre au domaine Mobile.

Les en-têtes entrants ne sont jamais transmis en bloc. Seuls Host canonique,
Content-Type, Content-Length, DPoP et Origin sont fournis au backend avec
`X-Hestia-Client-IP` reconstruit à partir du pair TCP direct. Tous les Forwarded,
X-Forwarded-* même inconnus, X-Real-IP et l'identité client fournie par l'appelant
sont écartés. Cookie, Authorization, Upgrade, transfert chunked et corps compressé
sont refusés. Le contenu et les preuves DPoP restent interprétés par Gateway ;
le frontal n'effectue pas l'authentification métier.

Ce profil est une **terminaison TLS directe**, pas une configuration à placer
derrière un proxy supplémentaire en réutilisant ses en-têtes. Loopback est une
frontière de confiance de l'hôte, sans authentification du processus émetteur.

Cache, répétition sur erreur amont et fichiers temporaires de réponse sont
désactivés. Les corps autorisés tiennent dans le tampon mémoire de 1 Mio.
Les réponses imposent no-store, no-referrer et nosniff ; Set-Cookie est retiré.
Les CSP spécifiques au bootstrap restent celles de Gateway. Aucun chemin,
query string, corps ou en-tête entrant n'est journalisé par ce bloc serveur.
Le délai de lecture de téléchargement bootstrap est de 90 s, les autres de 35 s ;
ces délais NGINX sont des délais d'inactivité, les budgets applicatifs Gateway
restent responsables de la durée totale des opérations.

## Validation et limites

Douze tests de contrat purs et dix tests avec **NGINX réel et TLS réel** sont
ajoutés. La recette indépendante s'exécute dans les deux conteneurs Debian 12/13
déjà utilisés par la CI système, avec réseau extérieur coupé. Elle vérifie
toutes les routes, les corps reçus, les refus avant tout appel au backend,
l'anti-spoof, les seuils de taille, l'allowlist et l'absence de rejeu après une
réponse amont perdue. Les anciens tests restent présents.

Le backend de cette recette enregistre les requêtes ; il ne simule pas une
qualification fonctionnelle Gateway/Web. La recette ne démarre aucun service
sur l'hôte Work, LAB ou PROD. Verdicts et identités des artefacts dans le checkpoint.

Le premier gel `cf737125e0a6ddd1686cddcd074261b701689e3c` a détecté un
défaut du test de méthode : son corps de deux octets dépassait le budget des
routes sans corps et produisait 413 avant le 405 attendu. La correction envoie
une requête sans corps pour isoler la méthode ; l'assertion exacte 405 et tous
les refus avant backend sont conservés. Le rendu de production est inchangé.

Le rendu seul **n'ouvre aucun port en production**. Restent à composer :
enrôlement durable dans le frontal acquis, certificat Mobile/HTTP-01 et
renouvellement, refus des collisions, activation explicite depuis le cockpit,
boot Mobile et recette avec la vraie Gateway. Ne pas remplacer le frontal Web
ou son bundle figé par ce rendu. Phase 6 ouverte, aucune promotion de branche.

Références de syntaxe : documentation officielle NGINX,
https://nginx.org/en/docs/http/ngx_http_core_module.html et
https://nginx.org/en/docs/http/ngx_http_proxy_module.html.
