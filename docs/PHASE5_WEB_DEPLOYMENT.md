# Phase 5 — déploiement protégé et recette du Web réel

Base qualifiée : `4d9f396e36c9152647fe1151719fd958abe6ec31`, Quality
`36164840460`, système `36164840403`, paquets `36164840474`, SQL/proxy
`36164923531`. Web inchangé : `46c03060625d4d53c675474b11aaa33007d9aad7`.

## Déploiement privé livré

`WebDeployment(DeploymentSpec(source, target, journal))` copie exclusivement
l'arbre Web complet attendu : Git tree `aaac278270e0fd1169396945916dfe997ae078bf`,
1840 fichiers. Le calcul des objets Git utilise les octets et les modes
exécutables, pas seulement une sélection PHP ni un nom de commit déclaré.
Les pins PHP existants ne changent pas. Aucun fichier source n'est exécuté.

La source doit être une extraction protégée, sans `.git`, liens, fichiers
spéciaux, hardlinks, ACL d'accès ou écriture non root. Les chemins source,
destination et journal sont disjoints, sous des parents protégés. La destination
est sous `/srv/` ou `/var/www/`, le journal sous `/var/lib/`. Les ressources
préexistantes, même vides, sont refusées. Le contrat impose root et un
consentement explicite. L'acquisition GitHub demeure une étape préalable.

Le journal durable précède la réservation de la destination. Les répertoires
sont root:root 0755 ; les fichiers root:root 0644 ou 0755 selon leur bit
exécutable Git. Il n'y a aucune conversion récursive d'ownership d'une ancienne
installation. Une umask restrictive n'altère pas ces modes explicites.
Chaque copie vérifie son empreinte ; la source et la destination complètes sont
relues avant le reçu. Limites : 10000 entrées, profondeur 64, 8 Mio par fichier,
256 Mio au total, durée bornée des scans et de la copie à 45 secondes chacun.

`WEB_SOURCE_DEPLOYED` signifie seulement que le code exact est présent et
protégé. Une réponse perdue est récupérable par observation, sans recopie.
Une copie interrompue, un reçu absent ou une dérive reste manuelle : aucune
suppression, adoption ni réparation automatique. Le registre privé typé
`WebDeploymentOperation` n'est pas ajouté implicitement au wizard.

L'observation porte sur l'état vierge du déploiement. La finalisation SQL ajoute
ensuite des pointeurs scellés et `install.lock` : elle invalide volontairement
le reçu de copie vierge. Son contrat propre, puis celui du runtime HTTP, prennent
le relais pour vérifier cet état. Ne pas rendre le premier reçu permissif aux
fichiers inconnus pour autoriser une réactivation.

## Recette intégrée réelle

`tests/integration/deployed_web_systemd.py` exige les opt-ins SQL/finalisation
et `HESTIA_DEPLOYED_WEB_TEST=1`. Dans un conteneur Debian 13 jetable et sans
réseau externe, il consomme le déploiement produit, le fresh SQL et la
finalisation produits, le runtime Apache/FPM, le collecteur privé et le profil
proxy TLS. Les bases sont nouvelles, locales au banc ; aucune base existante
n'est réutilisée. Les services Debian par défaut sont masqués dans cette image
de test. Le banc seul démarre les services dédiés et le NGINX de recette.

Dix cas sont requis : arbre complet avant SQL, connexion Admin/Dashboard/logout,
CSRF et cookies Secure/HttpOnly/SameSite/HSTS/CSP, fichiers privés et install.php,
expiration réelle des sessions aux choix 1 h/4 h/8 h, conservation d'une session
valide de 8 h par le collecteur 43200, maintenance sans modification de session,
sceau altéré refusé, connexion effective du Web en SQL/TLS distant simulé dans
le conteneur, code non inscriptible et limites métier explicites. Les sessions
proviennent d'un vrai login ; leur horodatage est vieilli par PHP sous l'UID Web
uniquement pour éviter plusieurs heures d'attente. Aucun endpoint métier n'est
remplacé par un mock. Le certificat HTTPS de recette est éphémère et explicitement
approuvé par son client. Les configurations globales et phpsessionclean natif
restent inchangés.

Les profils SQL exercés ici sont comptes locaux préexistants au fresh et SQL/TLS
distant de fixture. Cette nouvelle recette n'est pas une preuve supplémentaire
du fresh managed complet ni de la correction exhaustive des triggers managed.
Les 118 recettes SQL historiques restent obligatoires séparément.

## Gates et limites

Qualification requise sur le même arbre : 622 core par Debian 12/13, 16 DOM,
21 HTTPS, 61 recettes système et 13 paquets par Debian, 118 SQL/HTTP, 14 cas
proxy avec le helper Web épinglé, puis les 10 cas du Web réellement déployé.
Aucun skip, aucune retouche de pin. Résultats finaux et échecs éventuels dans le
checkpoint compagnon après gel documentaire.

Le Web réel est maintenant exercé sous les services, mais leur activation
transactionnelle reste à livrer au produit. `uploads` est encore protégé en
lecture seule : GED, photos et autres données métier inscriptibles ne sont pas
qualifiés. Les neuf groupes de producteurs, leur inventaire complet, la
sauvegarde/restauration exhaustive, la vraie transition d'upgrade, le wizard
et la fermeture du bootstrap restent ouverts. La maintenance de la recette
HTTP ne vaut pas orchestration coordonnée SQL/fichiers de toute l'application.

Debian 12 conserve PHP officiel 8.2, incompatible avec le minimum du Web : ses
tests core/système ne certifient pas l'application complète. Aucun module global
NGINX/ACME, serveur métier, Gateway ni APK touché. Aucun succès d'un simple login
ne met `application_installed`, `system_wiring_verified`,
`writable_business_storage_ready` ou `complete_web_backup` à vrai. Phase 5
reste ouverte ; l'intermittence navigateur historique reste non résolue.
