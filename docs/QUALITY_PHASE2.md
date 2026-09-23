# Quality Phase 2 - lot local du 23 septembre 2026

## Référence et statut

Base GitHub relue avant modification :
`38e956817f1404b78c4608ccd64ac9c7dd15926d` (`main`, Phase 1 HTTPS).

Le code a été préparé localement à partir des fichiers récupérés à ce SHA. Chaque
fichier original utilisé a été vérifié contre son hash de blob GitHub, puis
conservé en référence séparée. Il ne s'agit pas d'un clone intégral des dépôts
HESTIA ni d'une distribution autonome : le ZIP différentiel doit compléter le
dépôt existant. Aucun autre dépôt HESTIA n'est modifié.

La publication du commit et la mise à jour/clôture de l'issue #4 restent à faire :
aucune action d'écriture GitHub n'était utilisable dans l'environnement de
préparation. Aucun commit distant ni run GitHub Actions n'est revendiqué par ce
rapport. Le lot doit être publié sans écraser un HEAD plus récent.

## Environnement exercé

Debian 13, Python 3.13.5, OpenSSL disponible, iproute2 disponible, Node.js 22.16.0.
Les tests Debian 12 vérifient le preflight à partir d'un os-release représentatif ;
aucun runtime complet Debian 12 ou Python 3.11 n'a été démarré pour ce lot.

Les tests TLS ouvrent de vraies connexions HTTPS sur loopback avec des certificats
éphémères. Les tests transactionnels utilisent des répertoires temporaires et des
ressources locales réelles. Ils ne déploient pas HESTIA sur LAB-PAWEB30.

## Résultat de la suite avant gel

`./scripts/quality-local.sh` : PASS, **111 tests réussis, aucun test ignoré**.

| Groupe | Nombre | Portée |
|---|---:|---|
| Tests Phase 1 conservés sans modification | 32 | TLS/SAN, cookies, session, code one-shot, réseau, port, nettoyage, validation |
| Moteur transactionnel | 30 | Plan, consentement, preuves, crashes, resume, retry, frontières et rollback |
| Journal privé | 21 | Atomicité, concurrence, fsync, révision, corruption, permissions et liens |
| HTTPS transactionnel et cycle de vie | 12 | Authentification, CSRF, commandes typées, refresh, déconnexion, restart et arrêt |
| Contrats et CLI | 16 | Schémas fermés, entrées atypiques, secrets, verrouillage et modes CLI |
| **Total** | **111** | **0 échec, 0 erreur, 0 skip** |

Le Quality final est réexécuté après toutes les modifications, documentation
comprise. Son journal d'exécution accompagne séparément le ZIP ; aucune modification
des fichiers sources n'est autorisée entre ce contrôle, leur gel et l'archivage.

## Vérifications importantes

- Aucune ressource gérée ne change avant confirmation du plan et écriture du
  checkpoint. Un échec d'écriture bloque l'effet suivant. Une étape DONE ne
  réexécute ni apply ni commit lors d'une répétition/reconnexion/reprise.
- Les interruptions couvrent prepare, apply, validate, commit, le checkpoint
  DONE et rollback. Des sous-processus s'arrêtent réellement avec `os._exit`
  après apply, commit et rollback ; le processus suivant relit le journal et
  conserve une occurrence de chaque effet déjà accompli.
- La reprise d'un effet ambigu non prouvé devient MANUAL_ACTION_REQUIRED. Une
  preuve de commit évite un recommit. Une reprise de commit non réalisé passe
  par une nouvelle validation. La direction de rollback survit au crash.
- Les backups de fichiers préexistants sont restaurés. Une ressource étrangère
  modifiée entre-temps n'est pas supprimée. Les frontières se déroulent en ordre
  inverse, les dépendants extérieurs bloquent, les effets irréversibles sont refusés.
- Le retry ne vise qu'une étape et préserve les succès ainsi que les erreurs des
  autres frontières. Les scénarios fresh et upgrade du moteur sont exercés dans
  les sandboxes ; ils ne constituent pas un test d'installation SQL HESTIA.
- Les lecteurs concurrents ne voient pas de JSON partiel. Le verrou reste sur le
  même inode, les écrivains concurrents sont refusés, une révision obsolète ne
  remplace pas le journal. Le test vérifie l'ordre fsync données / rename / fsync
  répertoire, ainsi que les pannes avant et après rename.
- Les symlinks sur ancêtres, répertoire final, journal et verrou sont refusés.
  Les hardlinks, FIFO, mauvais propriétaires, droits trop larges, chemins de
  traversée et données corrompues/vides/trop grandes sont rejetés sans toucher une
  cible étrangère. Les chemins valides avec espaces, accents et ponctuation passent.
- JSON dupliqué, NaN, types inattendus, booléens à la place d'entiers, nombres
  négatifs/énormes, longues chaînes et caractères spéciaux sont testés. Le schéma
  refuse les champs inconnus, sorties libres et preuves de ressources non prévues.
- Les secrets connus et textes bruts d'exception n'apparaissent pas dans le state,
  les réponses ou les logs testés. Les chemins/query strings rejetés ne recopient
  pas leurs valeurs. Le double unlock concurrent ne réussit qu'une fois.
- Les routes transactionnelles exigent l'authentification et, en POST, le CSRF.
  Les headers sensibles dupliqués sont refusés. Aucun endpoint de commande shell
  générique n'existe. L'analyse AST interdit l'évaluation dynamique et le shell
  activé dans le code de production.
- Un navigateur déconnecté n'annule pas l'opération serveur. Un nouveau bootstrap
  exige une nouvelle authentification et retrouve le journal intact. L'arrêt
  gracieux attend l'opération active puis efface le magasin éphémère.

## Contrôles complémentaires et limites

Le script de Quality compile la syntaxe Python, exécute la suite, vérifie les deux
JavaScript originaux avec Node, recherche les motifs de sécurité interdits et
exécute le preflight CLI réel. Les JavaScript récupérés sont inchangés, vérifiés
par leur hash Git ; le lot n'ajoute aucun asset ni appel distant côté navigateur.

La préparation locale n'a pas reconstitué l'illustration WebP ni l'intégralité des
assets visuels inchangés. Le scan des assets dans cette copie porte sur les JS
présents ; il ne constitue pas une nouvelle inspection de chaque fichier HTML/CSS
du dépôt. Aucun test visuel/responsive nouveau n'est revendiqué, puisque l'UX n'est
pas modifiée. Les intégrations HTTPS utilisent des pages de test locales contrôlées.

Ces tests valident les invariants exercés, pas une couverture de code de 100 % ni
une preuve formelle. Une panne physique du stockage, un root hostile, les garanties
NFS, les effets externes non observables et les véritables contrats applicatifs
Web/Gateway/APK/SQL restent hors de ce lot. Les secrets sont filtrés par schéma,
valeurs connues et revue des adaptateurs ; aucun filtre universel n'est promis.

Aucun changement `schema.sql`, `install.php`, signature Android, Firebase ou
certificat final. Aucun déploiement ou compilation Android lancé.

## Livraison différentielle

Le ZIP est créé uniquement après le Quality final. Il contient exclusivement les
fichiers ajoutés ou modifiés, dans leurs chemins relatifs au dépôt, sans cache,
secret, dépendance vendue ni asset inchangé. La liste des chemins et le SHA-256 de
chaque entrée sont comparés au manifeste gelé des fichiers testés. Le SHA-256 du
ZIP est fourni séparément. Le patch textuel optionnel représente le même lot et
ne constitue pas un commit publié.
