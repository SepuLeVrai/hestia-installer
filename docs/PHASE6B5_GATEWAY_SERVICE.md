# Phase 6B5 — service Gateway MAIN natif

Candidat additif sur le gel 6B4 qualifié `891b95b7e454d92578d8556be2877c4a27b4e437`.
La qualification native et les gates du SHA final doivent être consignées dans
le handoff et les issues avant de considérer ce lot acquis. Phase 6 reste ouverte.

## Références conservées

- Gateway `e2c09f53593bf316906ccc4387f185e73e7f85a8`, version
  `0.12.2-installer.rc1`, SQLite 6, paquet et binaire du catalogue 6B3 inchangés.
- Web `2a27c7a1f9fe0a00289eb53278f75d5f230900b7`, Foundation MAIN 9082,
  Web 9080 ; ancien Web de recette `46c03060625d4d53c675474b11aaa33007d9aad7`.
- Aucun script historique install/deploy exécuté : ils ciblent les ressources
  globales et activent/redémarrent le service. Le binaire qualifié conserve ses
  propres migrations et son verrou SQLite. Le contrat natif Foundation vérifie
  le listener, le vhost fermé, le FPM protégé et la propriété des processus ; il
  remplace explicitement la recherche historique dans sites-enabled.

## Plan distinct et fermé

`/api/gateway/service/{plan,apply,resume,retry,check}` emploie session, CSRF,
origine et confirmation du SHA exact. Le profil lie les quatre plans DONE Web,
activation, préparation Gateway et Foundation. Les journaux acquis restent
inchangés. GET/report ne lisent que les métadonnées, sans sonde ni lecture PEM.

Le plan crée un compte système verrouillé exclusif, dérivé des instances Web et
Gateway, différent du compte Web. Avant cette création, il refuse tout listener
IPv4/IPv6 sur 9083, répertoire natif occupé, unité ou drop-in étranger. Une identité
partielle n'est jamais adoptée. Le plan déploie exclusivement le binaire du ZIP
authentifié, une configuration MAIN fermée et une unité propre à l'instance.

Les fichiers binaires/configuration sont root:Gateway ; seul `state/` appartient
au service, mode 0700. `LoadCredential=main-key` lit le PEM privé d'origine sans
copie dans le profil, les arguments ou l'environnement. La clé DEV préparée est
conservée mais n'est pas raccordée à un backend inexistant. Les protections de
l'unité qualifiée Gateway sont conservées, avec une unité statique, `Restart=no`
et la condition de maintenance Web. Aucun enable, restart ou réouverture implicite.

## Interruption et disponibilité

Chaque effet dispose d'une intention durable préalable. Le premier démarrage
exige un état vide ; les migrations restent celles du binaire qualifié. Le reçu
de démarrage lie les inodes de la base et du verrou. Une réponse perdue après le
reçu se réconcilie en observant le même service ; une intention sans reçu, un état
remplacé ou un service arrêté demande une intervention, jamais une initialisation
répétée. La reprise des sondes validées est une lecture, pas un nouvel appel.

L'identification vivante vérifie unité/fragment/drop-ins, fichier exécutable,
arguments, UID/GID/groupes, cgroup, pidfd/starttime et socket kernel 127.0.0.1:9083.
La disponibilité explicite vérifie aussi `/health`, puis un aller-retour signé
Gateway → Foundation → Web via bootstrap/check et un jeton aléatoire inexistant.
Les réponses Web admises sont invalid ou unavailable avec expiration zéro ; une
réponse prête ou une erreur backend ne prouve jamais le raccordement. L'origine
incorrecte et les en-têtes de transfert sont refusés. Seuls quotas, nonces et
événements techniques sont ajoutés : aucun enrôlement, APK ou compte utilisateur.
DONE reste historique si le contrôle courant devient indisponible.

## Maintenance et limites

La sauvegarde Web ferme d'abord sa barrière, arrête le Gateway possédé, puis
Foundation, avant le census strict Web existant (inchangé). Le census de l'identité
Gateway arrêtée exige lui aussi zéro processus. Les liaisons de manifeste et
d'état rejoignent la preuve de barrière vérifiée pendant la sauvegarde. Une
réponse perdue à stop ne permet de terminer que l'arrêt du même PID ; un nouveau
PID est refusé. Les deux unités restent bloquées par maintenance.attempt.

La recette ciblée `tests/integration/gateway_service_systemd.py` utilise le vrai
ZIP figé, systemd, Apache, FPM, MariaDB, Ext4 et le navigateur en conteneur jetable
sans réseau. Elle doit vérifier les interruptions stage/start, le credential réel,
les nonces SQL du raccordement, UUID/schema SQLite conservés, les parents/clés/PID
Web inchangés, puis la restauration SQL/données Web et l'arrêt coordonné.

Cette sauvegarde qualifie les données enregistrées du Web. Elle ne constitue pas
encore une sauvegarde/restauration du SQLite Gateway. DEV natif, FCM, NGINX Mobile,
TLS Mobile, politique de redémarrage/réouverture et recette complète 6C restent à
livrer. Aucun build APK, accès LAB, changement main/dev-Bastien ou promotion.
