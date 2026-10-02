# 6B9 — composition des frontaux Web et Mobile

`SharedMobileTLS` compile les deux domaines dans les mêmes processus NGINX :
un listener HTTP 80 et un listener HTTPS 443. Il lie le profil Web et l'origine
exacte de l'identité Gateway, refuse leur collision et conserve les 63 routes
Mobile du lot 6B8a. Les listes d'accès, upstreams, adresses proxy, certificats
et répertoires HTTP-01 restent distincts.

## Deux états explicites

Avant certificat Mobile, HTTP sert uniquement ses jetons HTTP-01 canoniques et
répond 503 ailleurs ; le HTTPS Web est identique octet pour octet. Après
certificat, le serveur Mobile rejoint le même fichier HTTPS et HTTP redirige
vers son origine canonique. Chaque serveur HTTPS contrôle Host et SNI. Le seul
ajout dans le bloc Web est le contrôle SNI réciproque, nécessaire pour empêcher
SNI=Mobile/Host=Web de sélectionner le backend Web.

Les défis Mobile sont publics même si son API est sur liste d'accès. Seuls GET
et HEAD, un chemin de jeton base64url de 22 à 128 caractères, sans query ni
encodage alternatif, accèdent au répertoire Mobile. Aucun proxy sur HTTP.

Le candidat produit des vecteurs Certbot fixes : webroot, certificat séparé
`hestia-mobile`, configuration/travail/logs dédiés, production pour émission,
staging pour dry-run, sans plugins Apache/NGINX ni hooks de répertoire. Il ne
lance pas ces commandes. Son manifeste lie les deux profils, le pin Gateway,
les réseaux et les octets de chaque configuration avant/après certificat.
Ce manifeste est un candidat déterministe, jamais un reçu de déploiement.

## Qualification bornée

Dix contrats purs obligatoires et dix tests NGINX/TLS réels par Debian 12/13
s'ajoutent aux CI existantes, sans retrait de tests. Les tests utilisent les
configurations produites intégralement, deux certificats auto-signés et deux
upstreams enregistreurs. Ils vérifient domaines/certificats, les 63 routes,
Host/SNI croisés, listes d'accès, HTTP-01 séparé, les deux états, rotation de
certificat et reload, refus de configtest sans interruption, indisponibilité
Gateway sans interruption Web et redirections canoniques.

Les configurations globales Apache/NGINX sont comparées avant/après. Aucun
compte, listener ou certificat n'est créé hors des conteneurs CI jetables.
Les certificats/clefs de fixture ne sont pas exportés en preuves.

Correction 6B9a : le NGINX officiel activé au boot du conteneur possédait déjà
le port 80. Le premier scénario lisait ce listener au lieu d'attendre son propre
master : trois tests HTTP échouaient, sept tests HTTPS passaient. La fixture
arrête uniquement ce service connu et le rétablit en sortie ; elle exige le PID
du master lancé avant toute requête. Le compilateur produit reste inchangé.
Les preuves initiales sont conservées ; le nouveau gel est qualifié à nouveau.

## Reprise des services encore à raccorder

Ce lot ne modifie ni bundle figé, ni unité, ni worker, ni contrôleur Phase 5.
Leurs gardes refusent justement des fichiers ou drop-ins ajoutés arbitrairement.
L'intégration suivante doit lire le parent figé, enrôler une nouvelle identité
de plan/reçus, transférer explicitement la responsabilité des unités HTTP,
HTTPS et du renouvellement Web/Mobile, puis exposer une action cockpit avec
reprise après interruption. Écraser les fichiers Phase 5 avec le candidat est
interdit. La vraie émission ACME, l'authentification Gateway/Web et cette reprise
systemd ne sont pas qualifiées par les backends enregistreurs.

Le parent 6B7b11 est qualifié nativement (37022581175, 36 tests). La recette
6B7b12 37028464992 est déjà en cours : elle n'est pas relancée. Les verdicts
actualisés, commits exacts et preuves figurent dans le checkpoint livré.
Phase 6 ouverte, aucune promotion main/dev/dev-Bastien et aucun APK.

## Correction 6B9b du signal de disponibilité

NGINX configtest crée un pidfile vide avant le démarrage effectif. La fixture
6B9a le convertissait trop tôt en entier. Elle attend désormais sa publication
non vide, dans la même attente bornée, puis exige toujours le PID exact du
master lancé. Aucun changement produit, aucun retrait de test ; l'échec 6B9a
est conservé avec celui du premier gel.

## Correction 6B9c de l'attente du reload TLS

Le reload HTTP peut se terminer avant le reload HTTPS. 6B9b obtenait encore
le certificat Web sur une connexion Mobile pendant cette transition normale.
La fixture attend désormais le résultat HTTPS effectif pour les deux états ;
une erreur de confiance transitoire reste en attente bornée, puis la réussite
exige toujours la validation stricte du certificat Mobile et un statut 200.
La rotation et les huit autres scénarios restent inchangés.
