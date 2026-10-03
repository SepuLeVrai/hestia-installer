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

Ce moteur n'est pas encore raccordé aux routes du cockpit ni au profil du
service. L'activation, la rotation et les erreurs d'envoi doivent conserver les
frontières de maintenance et de reprise. Le candidat Gateway FCM sur la branche
`work/phase6-fcm-credentials-20261003` ajoute la lecture `LoadCredential` et le
contrôle du projet ; sa Quality et la recette intégrée restent nécessaires.
Aucun import conservé ne vaut preuve d'autorisation Google ou de push téléphone.
