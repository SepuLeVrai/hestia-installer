# Modèle de sécurité

## Barrière HTTP limitée à deux unités

[HttpDrain](PHASE5_HTTP_DRAIN.md) dérive les deux unités du provisionnement
vérifié, conserve la maintenance après échec et refuse un thread visible portant
l'UID/GID dédié hors de ces cgroups. Aucune action par PID observé, aucun arrêt
de service étranger et aucune adoption ne sont permis. Le recensement borné
est ponctuel ; root, les planificateurs et les autres écrivains SQL ne sont pas
neutralisés par cette observation. Le reçu ne certifie ni inventaire exhaustif,
ni activation, ni sauvegarde complète. Les contrôles de staging restent stricts.

## Frontière du stockage métier externe

Le [profil externe](PHASE5_BUSINESS_STORAGE.md) refuse source inconnue, slot
non finalisé, instance étrangère, chevauchement code/données/gate et PHP 8.2.
`data/uploads` est créé exclusivement, UID/GID dédiés, 0700. Aucun lien ou
chmod d'un `uploads` existant. Apache ignore les `.htaccess` métier et n'exécute
aucun PHP dans les alias ; seules quatre familles d'images sont publiées.
La restauration testée utilise une destination neuve sous maintenance. Un
profil inscriptible ne certifie ni tous les clients SQL ni les planificateurs hôte.

## Déploiement de code exclusif

Le [déploiement protégé](PHASE5_WEB_DEPLOYMENT.md) vérifie l'arbre Git complet
avant et après copie, réserve un journal durable, refuse les destinations
existantes, liens et fichiers spéciaux et conserve le code root:root non
inscriptible par le Web. Il n'exécute aucun contenu source. Les copies partielles
ne sont ni supprimées ni rejouées automatiquement. Le reçu de copie vierge
cesse volontairement d'être observable après ajout des pointeurs de finalisation.

## Profil proxy explicite

Le [contrat TLS/proxy](PHASE5_PROXY_INGRESS.md) distingue l'adresse du pair de
connexion de l'allowlist clients. Apache normalise REMOTE_ADDR/HTTPS avant PHP
et retire le forwarding ; le frontal remplace les headers client. L'adresse
loopback n'est pas une authentification de processus : l'hôte local est dans
la frontière de confiance. Aucun isolement multi-tenant, certificat public ou
renouvellement ACME n'est implicite. Le backend demeure inaccessible au réseau.

## Provisionnement de l’identité locale

[ServiceIdentity](PHASE5_SERVICE_IDENTITY.md) réserve un journal root:root 0700
avant useradd ; aucun compte/groupe existant adopté. Mot de passe verrouillé,
nologin, pas de home, groupe exclusif, aucune sous-identité. NSS local contrôlé,
hooks useradd absents/vides, outils/réglages liés au plan. Aucune donnée shadow
d’un autre compte persistée ou exposée ; sorties de commande fermées. Création
partielle : MANUAL, sans suppression ni réattribution implicite d’UID. La preuve
du compte reste distincte du déploiement, de la migration et de l’activation Web.

## Collecteur de sessions et exclusion des écritures

Le [collecteur dédié](PHASE5_SESSION_CLEANER.md) n'exécute que sous l'UID du pool,
avec système en lecture seule et sessions seules inscriptibles. Le verrou
exclusif non bloquant reporte un passage si PHP est actif ; la maintenance
attend la libération du collecteur. Marqueur présent, inode modifié, ACL, lien,
propriétaire/droits incorrects ou entrée étrangère ferment le passage. Aucun
contenu de session n'est lu, aucun nom n'est journalisé et aucune date retenue
n'est prolongée. phpsessionclean natif reste intact ; l'inventaire d'un hôte
arbitraire et l'activation complète ne sont pas attestés par ce staging.

## Préparation exclusive du runtime HTTP

Le [staging Apache/FPM](PHASE5_HTTP_RUNTIME.md) exige une identité dédiée
préexistante, un code immuable protégé et des chemins fixes. Il réserve les
ressources sans adoption ni écrasement, pose la maintenance avant les unités,
puis teste les configurations. Aucun start n'est produit. Configurations,
dépendances, source, propriétaires et gate sont liés au journal privé et
recontrôlés en lecture. Un échec conserve les ressources partielles ; une reprise
incertaine est manuelle. INI et pool isolés, directives PHP administratives,
Host fermé, chemins privés refusés et absence de `.htaccess` héritée bornent le
profil. Nettoyeur, activation et qualification du Web complet restent requis.

## Drainage systemd fermé et descendants

La [barrière système](PHASE5_SYSTEM_DRAIN.md) exige les noms d’unités dérivés
de l’instance, leurs fragments root protégés et un unique drop-in de condition
lié au marqueur de maintenance. Elle refuse délégation, restart, config non
rechargée, jobs en cours et politique de kill insuffisante. Un service arrêté
avec PID zéro ne suffit pas : le cgroup récursif doit être vide et Result=success.
Les sorties de systemctl ne sont pas réinjectées dans les erreurs. L’arrêt
partiel conserve la maintenance ; la reprise explicite exige le même profil.
Le provisionneur reste responsable de l’inventaire réel des producteurs et
de leurs commandes. Ce lot n’installe aucune unité et ne redémarre aucun service.

## Stockages effectifs et producteurs hors guard PHP

L’[inventaire contrôlé](PHASE5_STORAGE_INVENTORY.md) exige des observations
explicites, ne lit aucun secret et ne confond pas cartographie et qualification
système. Le rapport masque les chemins ; le manifeste est strictement privé.
Un fichier multipart peut exister avant auto_prepend_file. Un enfant externe
peut survivre au processus PHP. Le frontal, FPM et les groupes de processus
nécessitent donc un drainage réel avant toute sauvegarde complète. Le nettoyage
Debian des sessions et les scripts CLI doivent participer au même verrou.
Aucune liste vide supposée de cron ou d’options ne vaut preuve d’absence.


## Reçu commun SQL et données enregistrées

La [composition privée](PHASE5C2_COORDINATED_BACKUP.md) lie instance scellée,
lease vivante, cible SQL et empreintes des archives. Elle exige les consentements
explicites et deux exports SQL sous verrou global de lecture borné. Elle refuse
la dérive SQL à nombre de lignes identique, celle des données et de l’enveloppe,
les archives endommagées et la perte de maintenance. Seul le reçu commun final
vaut succès de composition ; les reçus des composants ne le remplacent pas.
L’inventaire complet et le raccordement de tous les producteurs restent des
gates distincts. Aucun ancien backup n’autorise une restauration vers la source.

## Données modifiables sous maintenance

La primitive de [snapshot de fichiers](PHASE5C2_DATA_FILES.md) n'accepte que
l'inventaire construit par l'orchestrateur de confiance et une lease vivante.
Archives et clones restent privés, sans exécution de PHP ni réactivation Web.
Liens, ACL, attributs étendus, propriétaires étrangers et écritures publiques
sont refusés. Une nouvelle lease après reprise d'activité ne peut pas restaurer
une ancienne archive. Le raccordement à tous les producteurs et au SQL reste
un gate distinct avant sauvegarde complète ou rollback vers la source.


## Principe principal

Le mini-web ne doit jamais être un shell root présenté dans un navigateur.

INTERDIT :

```text
POST /run-command
command=<entrée utilisateur>
```

Les opérations privilégiées sont des fonctions connues, validées et testées.

## Bootstrap HTTPS

La Phase 1 applique :

- HTTPS dès le premier écran ;
- certificat auto-signé éphémère avec SAN IP ;
- port aléatoire `57000-57999` réservé par bind réel ;
- aucun relâchement du socket entre réservation et serveur HTTPS ;
- code bootstrap à usage court et unique ;
- 5 tentatives maximum ;
- session en mémoire ;
- cookie `HttpOnly + Secure + SameSite=Strict` ;
- CSRF indépendant ;
- CSP ;
- `no-store` ;
- contrôle exact du header `Host` ;
- validation `Origin` sur les POST sensibles ;
- rejet des query strings ;
- taille des requêtes bornée ;
- rejet de `Transfer-Encoding` sur les endpoints applicatifs ;
- méthodes HTTP limitées ;
- aucune liste de répertoire ;
- contrôle des traversées de chemin et symlinks pour les assets ;
- TLS 1.2 minimum ;
- arrêt et nettoyage sur Ctrl+C et SIGTERM.

## En-têtes HTTP

Le bootstrap envoie notamment :

```text
Cache-Control: no-store, max-age=0
Pragma: no-cache
Content-Security-Policy: default-src 'self'; ...
Referrer-Policy: no-referrer
X-Content-Type-Options: nosniff
X-Frame-Options: DENY
Cross-Origin-Opener-Policy: same-origin
Cross-Origin-Resource-Policy: same-origin
Permissions-Policy: camera=(), microphone=(), geolocation=(), usb=(), payment=()
```

HSTS n'est volontairement pas activé sur le bootstrap éphémère auto-signé afin de ne pas créer une politique persistante sur une IP temporaire.

## Code bootstrap

Le code en clair est affiché uniquement dans le terminal pour permettre l'amorçage de confiance.

Dans le processus :

- seul un dérivé PBKDF2-HMAC-SHA256 salé est conservé pour la validation ;
- aucune écriture sur disque ;
- invalidation après succès ;
- expiration après 10 minutes ;
- blocage après 5 échecs.

Les journaux HTTP n'incluent ni headers, ni corps, ni query string. Seuls une méthode autorisée, une route connue et le statut sont journalisés. Tout chemin inconnu devient `<unmatched>` ; un secret placé dans le chemin rejeté ne doit pas être recopié.

## Staging

Le runtime utilise un répertoire privé mode `0700`. La clé TLS temporaire est mode `0600`.

Le nettoyage refuse tout chemin hors du runtime attendu et tout staging symbolique. Le répertoire runtime est supprimé lui-même lorsqu'il redevient vide.

## Secrets

Interdits dans Git, arguments de processus, logs, URLs, state JSON non secret, rapports et artefacts.

### Credential GitHub

Le credential de lecture des dépôts HESTIA est un secret éphémère.

Règles obligatoires :

- privilégier un fine-grained personal access token limité aux dépôts HESTIA ;
- permissions minimales en lecture seule ;
- transmission uniquement dans le corps d'une requête HTTPS authentifiée ;
- aucune query string et aucune URL contenant le token ;
- aucune persistance dans localStorage, sessionStorage, IndexedDB ou cookie ;
- aucune écriture dans le journal de reprise ;
- aucun passage en argument de commande ou dans une URL de clone ;
- pas de stockage dans `.git/config` ;
- effacement dès que les téléchargements nécessaires sont terminés ;
- en cas de resume nécessitant un nouvel accès GitHub, demander à nouveau le credential.

La Phase 1 fournit le canal HTTPS, la session et la protection CSRF. La Phase 3 ajoute l'acquisition décrite ci-dessous et dans GITHUB_ACQUISITION.md.

## Limites assumées

Le certificat du bootstrap est auto-signé. La première ouverture dans un navigateur demande donc une validation manuelle de confiance. Ce certificat n'est pas une identité durable et n'est jamais réutilisé pour HESTIA Web ou Mobile API.

Aucune protection ne peut garantir le nettoyage après `SIGKILL` ou coupure électrique. Les secrets de bootstrap ne sont toutefois jamais persistés hors de la clé TLS éphémère dans le staging 0700, et un redémarrage crée une nouvelle session indépendante.

## CI

Aucune GitHub Action lourde n'est nécessaire à cette phase. Le Quality Check local exécute les tests unitaires et d'intégration HTTPS, la syntaxe JavaScript et un scan statique des motifs de sécurité interdits.

## Transactions : invariants supplémentaires

Le navigateur ne fournit ni fonction Python, ni nom de binaire, ni commande, ni
chemin de travail exécutable. Les cinq mutations HTTP sont des actions fermées :
plan, apply, resume, retry et rollback. Elles nécessitent la session, le contrôle
Origin lorsqu'il est présent, le jeton CSRF et un corps JSON strict. Les opérations
sur un plan nécessitent `confirm: true` et son SHA-256 exact. Le hash identifie le
plan approuvé ; ce n'est ni un secret, ni une signature contre un administrateur
root malveillant.

Le parseur refuse les doublons JSON, NaN, Infinity, les entrées hors schéma et les
en-têtes sensibles dupliqués. Les comparaisons CSRF sont à temps constant. Le code
bootstrap et le magasin de sessions sont verrouillés entre threads : un double
POST concurrent ne consomme pas deux fois le code one-shot.

Le journal est limité à 1 Mio, les identifiants à 64 caractères, le plan à 128
étapes et chaque étape à 128 ressources. Les nombres attendus sont des entiers
bornés, pas des booléens ou des flottants. Les chemins sont absolus et sans
traversée ; chaque composant du chemin persistant est ouvert avec `O_NOFOLLOW`.
Le répertoire final doit appartenir à l'utilisateur effectif et être exactement
0700. Les fichiers doivent être réguliers, appartenir au même utilisateur, être
0600 et ne pas avoir de hardlink. Les permissions trop larges existantes sont
refusées, pas corrigées silencieusement. Un lecteur déjà ouvert peut conserver
l'ancien inode devenu sans lien après un remplacement atomique légitime.

Les erreurs persistées et publiées sont des codes fixes. Ni message d'exception,
ni sortie de commande, ni contenu libre de formulaire n'est inséré dans l'état.
Les preuves contiennent uniquement des noms de ressources approuvées, des SHA de
sources et des hashes d'artefacts explicitement non secrets. Il est interdit d'y
mettre le hash d'un mot de passe ou d'un autre secret.

`SecretVault` est un magasin en mémoire, non sérialisable et verrouillé. Le moteur
refuse la présence des valeurs connues dans les documents et les preuves, même
avec caractères échappés. Le schéma fermé et le rejet de motifs sensibles
complètent ce contrôle. Un filtre ne peut pas reconnaître universellement un
secret arbitraire dissimulé dans une chaîne présentée comme une description :
les adaptateurs Python restent du code de confiance, soumis à revue. Ils ne
reçoivent aucun champ libre à recopier dans le journal.

L'effacement du magasin supprime les références, sans promettre un effacement
physique des copies de chaînes Python. Après redémarrage, un secret nécessaire
est redemandé (`SECRET_REQUIRED`) ; les étapes `DONE` n'en demandent pas de nouveau.
La collecte effective des credentials GitHub appartient à la phase suivante.

Un callback interrompu dans apply/commit/rollback n'est jamais rejoué sans preuve
fournie par l'adaptateur. Le comportement par défaut est `MANUAL_ACTION_REQUIRED`.
Un adaptateur futur doit rendre ses effets identifiables et ses appels externes
bornés ; aucune garantie universelle « exactement une fois » n'est revendiquée
pour des effets externes non observables. Un rollback impossible est refusé.

La durabilité suppose un système de fichiers local offrant les garanties de
`flock`, `fsync` et renommage atomique. NFS, partage réseau, corruption du stockage
et altération du code ou du journal par root ne font pas partie des garanties.
Après coupure brutale, un ancien temporaire privé non secret peut subsister ; il
n'est jamais adopté comme état valide. Le staging TLS conserve les limites de
nettoyage de la Phase 1, distinctes de la persistance du journal.

## Acquisition GitHub : contrôle des sorties et des archives

Les seules destinations réseau de cet adaptateur sont `api.github.com` et
`codeload.github.com`, port HTTPS standard, pour les trois dépôts HESTIA figés
côté serveur. TLS et le nom d'hôte sont vérifiés ; les proxies d'environnement,
cookies amont et redirections automatiques ne sont pas utilisés. Aucun binaire,
URL libre ou commande n'est reçu du navigateur.

La redirection API d'archive doit désigner exactement le dépôt et le SHA approuvés
sur codeload. Sa query signée éventuelle est abandonnée. L'URL est reconstruite
sans query et un header Authorization est créé explicitement pour cette seule
seconde destination GitHub autorisée. Il ne s'agit pas d'une propagation automatique
vers une autre origine. Toute autre redirection est refusée, sans lire son corps.
Aucun credential ne figure dans les URL demandées ni dans les preuves.

Le credential expire logiquement après 15 minutes ; l'expiration est contrôlée à
l'usage ou à la consultation du statut. Il est retiré du magasin en mémoire à la
fin de l'acquisition, sur échec terminal, clear, logout ou arrêt. Une opération
en cours garde sa référence locale jusqu'à sa fin bornée. Python ne garantit pas
l'effacement physique de toutes les copies mémoire ; aucun effacement sécurisé
universel n'est revendiqué. Le serveur n'accepte aucun stockage navigateur du
credential. Le futur formulaire devra rester sans persistance et vider sa saisie.

Metadata + résolution SHA vérifient la lecture effective des trois dépôts. Cela
ne prouve pas que le PAT ne possède aucun droit supplémentaire : sa restriction
fine-grained Metadata/Contents Read doit être faite lors de sa création.

L'extracteur n'utilise ni extractall ni extraction permissive : il refuse liens,
fichiers spéciaux, traversées, noms ambigus, collisions et archives hors limites.
Les fichiers sont 0600, ou 0700 si exécutables dans Git, les dossiers 0700 ; aucun
suid, propriétaire ou droit amont n'est conservé. Les sources ne sont pas exécutées.
Le credential connu est aussi recherché dans les noms et contenus extraits ; une
archive qui le recopie est rejetée et les données partielles possédées sont retirées.

Un root hostile ou un stockage défaillant reste hors de la garantie. Un état local
ambigu, une preuve manquante ou une dérive ne justifie jamais un écrasement : arrêt
conservateur. Une suppression interrompue au milieu de son arborescence peut exiger
une action manuelle si la preuve de propriété a disparu.

## Phase 4 - Cockpit fonctionnel

Le brouillon `wizard.json` a un schéma fermé, une limite de 8192 octets, un verrou
partagé avec le journal et un contrôle de révision. Les lectures/écritures sont
relatives au répertoire privé vérifié ; liens, fichiers spéciaux et droits trop
larges sont refusés. L'écriture est atomique avec synchronisation des données et
du répertoire. Aucun credential n'est accepté dans ce fichier.

Le préflight du wizard est revérifié côté serveur avant planification. Les nouvelles
routes conservent session/CSRF/Host/Origin. Le retrait confirmé d'un plan ne concerne
que les plans jamais approuvés ni exécutés ; il n'efface pas un journal d'audit.

Le client crée le DOM par textContent/createElement. Il traduit une liste fermée
de codes d'erreur, ne persiste pas de secret et n'en réinjecte aucun après envoi.
Le refresh lit l'état ; aucune mutation n'est rejouée automatiquement. Logout
efface le credential et la session, pas les sources. Voir [WIZARD.md](WIZARD.md).

## Contrôles permanents et chaîne de livraison

Le dispositif [Quality](QUALITY.md) s'exécute sans secrets applicatifs, avec le
credential Actions en lecture seule et sans persistance dans Git. Les tests root
restent dans des conteneurs Debian jetables, pas sur l'infrastructure HESTIA.
Les tests navigateur utilisent les assets et la CSP de production sans pont pour
la suite native ; l'acceptation du certificat auto-signé est locale au banc de test.
Les fixtures ne prouvent pas un accès effectif aux dépôts privés avec PAT réel.

L'inventaire refuse les tests manquants/ignorés et les preuves de livraison incluent
le contenu et le mode Unix de chaque fichier. Le packaging ne remplace pas la
revue sécurité des futurs adaptateurs. La protection GitHub de main est distincte
du workflow et nécessite une configuration administrative vérifiée.
# Maintenance et réparation privées en Phase 5

Le [contrat de maintenance](PHASE5_MAINTENANCE.md) impose un prepend root-owned
et une barrière coopérative commune à tous les producteurs du déploiement géré.
Il ne prouve pas à lui seul le raccordement d'un service préexistant. La barrière
reste après interruption ; seule une reprise explicite et journalisée la retire.
[La réparation DEFINER](PHASE5C2_REPAIR.md) exige cette lease liée à l'instance,
une sauvegarde de secours restaurée et une nouvelle preuve après mutation.
Pas de retry aveugle, d'élargissement DML, de réinitialisation Admin ou de secret
dans le journal. Ces APIs restent privées et non raccordées au routeur public.
