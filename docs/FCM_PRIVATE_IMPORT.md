# Préparation privée du credential FCM

`FcmCredentials` fournit le stockage privé préalable au raccordement du service.
Le profil confirme un `project_id` et le SHA du plan Gateway. Le JSON importé
doit désigner ce projet, un compte de service et une clé RSA PKCS8 de 2048 à
4096 bits validée par OpenSSL. L'endpoint OAuth reste fixé à Google. Un compte
d'un autre projet peut être utilisé ; l'adresse e-mail ne choisit pas le projet.

Les fichiers sont privés, de taille bornée et écrits exclusivement dans le
répertoire géré. Le reçu public contient uniquement la sélection et des
empreintes. Une intention durable lie les octets canoniques avant écriture.
Après interruption, seul un nouvel import explicite du même credential permet
de compléter un reçu absent. Un fichier partiel, modifié, lié ou manquant après
un reçu complet reste bloqué. Aucun fichier terminé n'est remplacé ou recréé.

La lecture d'état est historique ; elle ne lit pas la clé et ne lance pas de
processus ni de requête Google. La vérification explicite relit les fichiers
privés. Les onze tests locaux utilisent des clés synthétiques OpenSSL et des
répertoires temporaires, sans SQL, compte système, service ou accès réseau.

Le raccordement candidat ajoute les routes `/api/gateway/fcm/{plan,import,check}`,
protégées par session HTTPS, origine et CSRF. Le corps JSON de l'import est limité
à 16 Kio et ne passe jamais dans les rapports. Le plan FCM suit la préparation
Gateway terminée ; la confirmation porte sur son SHA et le projet choisi. Une
fois le profil du service scellé, aucun import ni changement de projet n'est
permis. Le verrou principal sérialise ces mutations avec les autres chantiers.

Le cockpit choisit explicitement le binaire FCM, puis le projet et enfin le
fichier privé. Une confirmation distincte précède son envoi. Annulation et
rafraîchissement ne réimportent rien. Les rapports restent historiques ; le
contrôle explicite vérifie la clé locale sans contacter Google.

Le catalogue conserve le binaire historique et ajoute uniquement Gateway
`33927821bbda57a2c10791d0523eaf3b254c8c9e`, version `0.12.3-installer.rc1`,
SQLite 6. Quality `37142673219` est PASS, y compris le test natif de lecture
`LoadCredential` sans réseau ni création SQLite. Le paquet exact a pour SHA-256
`f31fc543fabf538ca279027c08614d7f7c6ae6e0380a8514104979b01d629a8c`.

Le service lie les empreintes publiques du credential à sa configuration et
au fragment systemd. `LoadCredential` donne accès au fichier privé ; un
`ExecStartPre --check-push-credential` vérifie le projet avant démarrage. Les
lecteurs de maintenance, sauvegarde et boot reconstruisent le même profil.
Les profils historiques sans push conservent leur rendu exact. La sauvegarde
SQLite ne constitue pas une sauvegarde ni une rotation du credential Firebase.

La recette `fcm_mobile_application.py` doit qualifier ce raccordement complet,
avec un compte synthétique, maintenance réelle et nouveau PID 1. La rotation,
la réinstallation de credentials après sinistre, l'autorisation Google et la
réception sur téléphone restent à traiter séparément. Aucun import conservé
ne vaut preuve d'autorisation Google ou de push téléphone.
