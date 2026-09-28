# Moteur transactionnel - Phases 2 et 3 / issues #4 et #8

## 28 septembre 2026 — 5D5a, dépendances du serveur vierge

Le [raccordement des paquets officiels au wizard](PHASE5D_PACKAGE_WIZARD.md) ajoute
deux plans distincts, téléchargement puis installation des versions figées.
Les plans Web acquis 5D1–5D4 et les contrôleurs natifs restent inchangés.
MariaDB, le boot et le frontal public/TLS restent à livrer ; la phase 5 est ouverte.
Le nouveau gel exige les gates générales et sa recette ciblée en CI jetable.

Le [lot 5D4](PHASE5D_UPGRADE_WIZARD.md) ajoute une composition upgrade fermée,
avec enregistrement local du profil géré et une activation liée au journal
parent. Le format des anciens plans et les contrôleurs natifs restent acquis.

Le [lot 5D3](PHASE5D_ACTIVATION.md) utilise un second journal pour l'activation
après préparation DONE. Aucun plan approuvé n'est étendu. Sa disponibilité
actuelle est une observation explicite séparée des reçus historiques.

Mise à jour 5D : les adaptateurs applicatifs privés sont décrits dans
[PHASE5D_APPLICATION_JOURNAL.md](PHASE5D_APPLICATION_JOURNAL.md). Le moteur et le
schéma historiques ci-dessous restent inchangés ; leur registre public ne gagne
aucun sélecteur libre. Reconnaissance de reprise et mutation sont distinctes.

## Périmètre réellement implémenté

Cette phase fournit le moteur générique, le format de plan et de journal, les
primitives de reprise, de retry et de rollback, la façade HTTPS et les options
CLI. Elle ne déploie pas encore Web, Gateway, APK, SQL, proxy ou certificats finaux.
La Phase 2 enregistrait uniquement `preflight.run`, module `core`. La Phase 3
ajoute `github.acquire`, décrit en fin de document.
Un résultat `DONE` pour ce plan signifie que les contrôles core ont réussi.

Les boutons du wizard ne sont pas branchés sur ces nouvelles routes dans cette
phase : la connexion fonctionnelle de l'UX appartient à la phase wizard. Aucun
HTML, CSS, JavaScript ou asset n'est modifié par le lot Phase 2.

Les adaptateurs de tests créent et modifient de vrais fichiers en répertoires
isolés. Ils ne sont jamais importés ou enregistrés par l'application de production.

## Organisation du code

| Fichier | Responsabilité |
|---|---|
| `model.py` | États existants conservés ; schémas fermés de plan, ressources, preuves et journal |
| `transaction.py` | Stockage atomique, chemins privés, verrou et révision compare-and-swap |
| `operations.py` | Registre typé, contrat d'adaptateur, preuves de reprise, secrets éphémères |
| `engine.py` | Confirmation, checkpoints, apply, resume, retry, rollback et rapport |
| `service.py` | Façade à actions et paramètres fermés, suivi de l'activité serveur |
| `httpd.py` | Transport HTTPS, authentification, CSRF et erreurs redacted |
| `cli.py` | Modes check, dry-run, resume, report et démarrage du cockpit |

Le `StepRecord.details` historique reste compatible mais n'est pas un format
persistant autorisé : aucun dictionnaire libre de résultat n'entre dans le journal.

## Plan, consentement et frontières

Un adaptateur construit un `StepSpec` non secret à partir de données validées.
Sa méthode `plan()` est sans mutation. Les éventuelles découvertes nécessaires à
ce plan doivent aussi être en lecture seule. `prepare()` est exécuté après la
confirmation afin de vérifier les préconditions et la dérive avant `apply()`.

Séquence d'une étape :

```text
plan inspectable et immuable
  -> approbation explicite du SHA-256 du plan
  -> checkpoint prepare -> prepare (lecture seule)
  -> checkpoint apply   -> apply
  -> checkpoint validate -> validate (lecture seule)
  -> checkpoint commit  -> commit
  -> checkpoint DONE
```

Le journal de planification et son verrou sont des métadonnées privées de
l'installateur, pas un déploiement applicatif. Ils peuvent être créés avant le
consentement. Aucune mutation d'une ressource gérée n'intervient avant approbation.
Un échec d'écriture du checkpoint bloque le callback suivant.

Le plan expose, pour chaque étape : identifiant, opération, module, frontière,
version d'adaptateur, action lisible, dépendances, ressources, possibilité de
rollback, avertissements, interventions manuelles et noms des secrets nécessaires
(sans leurs valeurs). Une ressource déclare son type, sa cible, son existence
préalable, sa politique de retour arrière et, le cas échéant, le chemin du backup.

Types de ressources : `file`, `directory`, `service`, `port`, `fqdn`, `external`.
Les ports sont des entiers 1-65535, les FQDN sont normalisés, les chemins sont
absolus. Une ressource et son backup ne peuvent pas appartenir à deux étapes.
Les dépendances doivent précéder l'étape dans le plan ; les cycles sont refusés.

Un changement d'empreinte de ressources ou de version d'adaptateur nécessite une
migration/replanification explicite. L'exécution ne remplace pas silencieusement
le plan persistant par un plan reconstruit à partir d'une machine déjà modifiée.

## Journal privé et atomique

Chemin par défaut : `/var/lib/hestia-installer/state.json`.

Le schéma version 1 contient l'identifiant d'installation, la version de
l'installateur, les dates UTC, le mode, les modules, le plan, son hash, le hash
approuvé, la révision, les étapes, leur phase/checkpoint et leur nombre de tentatives,
les ressources créées/préexistantes, les backups, les SHA de sources, les hashes
non secrets, l'état global, le code de dernière erreur et l'intention de rollback.

Les preuves n'acceptent que des noms de ressources déjà déclarées et des hashes
au format attendu. `backups` référence des chemins ; aucun contenu de fichier ou
secret de configuration n'est placé dans le journal. Les preuves historiques
restent visibles après rollback, avec l'état `ROLLED_BACK` de l'étape ; elles ne
prétendent donc pas que les ressources supprimées existent encore.

Écriture : verrou exclusif non bloquant sur `.transaction.lock`, validation du
schéma et de la révision, fichier temporaire privé 0600, flush et `fsync` des
données, vérification de l'ancien snapshot, `os.replace` relatif au répertoire
privé déjà ouvert, puis `fsync` du répertoire. Le verrou garde le même inode : il
n'est jamais supprimé en fin de transaction. Le noyau le libère à la mort du
processus. Un second écrivain reçoit `BUSY` sans rejouer d'opération.

Lecture : aucune création de répertoire ou de verrou. Un lecteur concurrent voit
un snapshot ancien ou nouveau complet. Un fichier corrompu, trop gros, symbolique,
avec hardlink, mauvais propriétaire ou mauvaises permissions est refusé. Aucun
journal endommagé n'est remplacé par un nouvel état vide.

Les garanties de durabilité supposent un stockage local avec sémantique correcte
de `flock`, `fsync` et renommage atomique. Elles ne sont pas une preuve de résistance
à toute défaillance physique du stockage.

## États et reprise

| État d'étape | Signification / prochaine action |
|---|---|
| `PLANNED` | Plan accepté dans le journal ; pas encore appliqué |
| `RUNNING` | Dernier checkpoint d'une opération en cours ou interrompue |
| `DONE` | Frontière validée et commitée ; ne pas rejouer |
| `FAILED` | Erreur connue ; retry ciblé ou rollback explicite |
| `MANUAL_ACTION_REQUIRED` | État ambigu, effet non prouvé ou rollback incomplet |
| `ROLLED_BACK` | Retour arrière terminé ; un redémarrage de cette étape demande un retry explicite |

L'état global synthétise les étapes. Il peut rester `PLANNED` lorsqu'il existe un
mélange d'étapes terminées et d'étapes à traiter. Il faut lire les états individuels.
Une erreur sur une frontière indépendante ne disparaît pas parce qu'un autre retry
vient de réussir.

`resume` ignore toutes les étapes `DONE`. Les callbacks en lecture seule peuvent
être répétés. Après interruption d'`apply`, `commit` ou `rollback`, l'adaptateur
reçoit le dernier contexte non secret et doit prouver une décision :

- `RETRY_SAFE` : le callback concerné peut être repris sans répéter un effet ambigu ;
- `APPLIED` : l'effet d'apply est retrouvé ; passer à validate, pas à apply ;
- `COMMITTED` : le commit est retrouvé ; revalider et marquer DONE sans recommit ;
- `ROLLED_BACK` : le retour arrière est retrouvé ; ne pas supprimer/restaurer deux fois ;
- `MANUAL` : aucune preuve suffisante ; arrêter, sans hypothèse de succès ou d'absence d'effet.

Une reprise `APPLIED` reconstruit la preuve complète des ressources. Même lorsque
le dernier checkpoint était `commit`, le résultat est revalidé après l'interruption.
Le défaut de l'interface `Operation.recover()` est `MANUAL`, jamais « rejouer ».

Les adaptateurs futurs devront reconnaître les ressources par une identité durable,
par exemple `(installation_id, step_name)`, et vérifier leur propriété réelle.
Cette exigence empêche de recréer un admin, une clé P-256 ou une signature APK par
simple supposition. Les migrations SQL devront posséder leur propre preuve de
version appliquée. Ces adaptateurs applicatifs ne sont pas encore livrés ici.

## Retry et rollback

`retry(name, confirmation)` ne cible qu'une étape FAILED, MANUAL_ACTION_REQUIRED
ou ROLLED_BACK. Une étape DONE ne peut pas être sélectionnée. Les dépendances
doivent être DONE et aucun dépendant actif extérieur ne doit être invalidé.
Les étapes suivantes ne sont pas lancées automatiquement par ce retry : une
reprise explicite continue ensuite le plan. Un retry après rollback remet la
preuve de cette nouvelle tentative à zéro sans créer un nouvel identifiant.

`rollback(boundary, confirmation)` traite seulement la frontière nommée, en ordre
inverse des dépendances. Les dépendants actifs dans une autre frontière bloquent
l'opération. Une frontière déclarée non réversible est refusée avant la première
annulation. Un échec de prepare, lecture seule, peut être abandonné sans undo.

L'intention de rollback est persistée avant les callbacks. Après crash, `resume`
continue ce retour arrière : il ne repasse pas spontanément en installation.
Les ressources préexistantes sont restaurées par leur backup déclaré ; une
ressource étrangère ne doit jamais être supprimée. Ce contrôle appartient à
l'adaptateur et est exercé par les tests. Les effets externes irréversibles doivent
être déclarés comme tels et accompagnés d'actions manuelles.

## Secrets et exceptions

`SecretVault` n'existe qu'en mémoire. Les adaptateurs déclarent les noms requis,
les lisent via `OperationContext.require_secret()` et ne retournent jamais leur
valeur dans une preuve. Un processus repris ne récupère aucun credential depuis
le journal ; il signale `SECRET_REQUIRED` si nécessaire. Les étapes DONE n'ont
pas besoin de leurs anciens credentials. La future collecte GitHub sera effectuée
via un endpoint dédié, pas via les paramètres libres de ces routes.

Les erreurs publiques sont des codes `ErrorCode`. Les textes bruts d'exception,
stdout, stderr, corps HTTP, cookies et sessions ne sont ni journalisés ni renvoyés
par les opérations transactionnelles. Un hash de secret reste un secret interdit.
La détection de motifs et de secrets connus ne remplace pas la revue d'un adaptateur.

## Façade HTTPS

Toutes ces routes sont sous le bootstrap HTTPS authentifié existant :

| Méthode / route | Corps strict |
|---|---|
| GET `/api/installation/state` | Aucun |
| GET `/api/installation/report` | Aucun |
| POST `/api/installation/plan` | `{"modules":["core"]}` |
| POST `/api/installation/apply` | `{"confirm":true,"confirmation":"<SHA-256 du plan>"}` |
| POST `/api/installation/resume` | Même corps |
| POST `/api/installation/retry` | Même corps + `"name":"<étape>"` |
| POST `/api/installation/rollback` | Même corps + `"boundary":"<frontière>"` |

Les POST exigent le cookie de session et `X-Hestia-CSRF`, obtenu depuis
`GET /api/session`. Lorsque Origin est envoyé, il doit correspondre exactement
au bootstrap. Les acquisitions utilisent les routes GitHub dédiées ci-dessous ;
aucun paramètre ou adaptateur libre ne peut être fourni.

Les réponses réussies sont `{"installation": <journal>}` ; avant planification,
les GET renvoient `{"installation": null}`. Un échec métier d'une opération peut
être retourné en HTTP 200 avec l'état `FAILED` du journal : lire l'état, pas
uniquement le statut HTTP. Les conflits/exécution occupée donnent 409 ; les
paramètres invalides donnent 400 ; absence de session 401 et CSRF invalide 403.

Un refresh ou la fermeture du navigateur ne redémarre pas une étape. Les GET
restent consultables pendant l'application. La fin d'une requête perdue ne provoque
pas de rollback ; la reconnexion retrouve le journal. L'arrêt gracieux du bootstrap
refuse les nouveaux travaux et attend les adaptateurs actifs avant d'effacer les
secrets. Les futurs appels système doivent être bornés et utilisent des tableaux
d'arguments, jamais un shell alimenté par le navigateur.

## CLI

```bash
./install-hestia.sh --check
./install-hestia.sh --dry-run
sudo ./install-hestia.sh --report
sudo ./install-hestia.sh --resume
```

`--check` conserve son comportement Phase 1, non destructif et possible sans root.
`--dry-run` relit le plan existant avec son digest original, sans réseau ni écriture.
Sans journal, il affiche un contrôle core indicatif avec nouvel UUID/date ; cette
simulation ne remplace pas un plan approuvé persistant. `--report` et les GET HTTPS
restent disponibles pour consulter le journal.

`--report` lit uniquement le journal et ne démarre pas TLS. `--resume` exige un
journal existant compatible, crée un nouveau bootstrap et une nouvelle session,
mais ne rejoue rien avant confirmation via l'API. La commande seule ne contourne
pas la confirmation et ne branche pas automatiquement le wizard, encore hors lot.

`--state-dir /chemin/absolu` sélectionne un répertoire privé alternatif. Ne jamais
le placer dans le staging TLS ni dans un répertoire à permissions larges. Les
quatre modes CLI sont mutuellement exclusifs. Codes de sortie : check 0/1,
préconditions bootstrap 2, erreur bootstrap 3, erreur de journal/reprise 4.

## Validation et extension

Les hooks d'injection de panne existent uniquement comme argument du constructeur
Python utilisé par les tests. Aucun endpoint, paramètre CLI ou variable d'environnement
ne permet de les activer. Les tests utilisent des interruptions contrôlées et des
processus tués par `os._exit` après apply, commit et rollback ; ils vérifient que
les ressources et les compteurs d'effets ne sont pas doublés à la reprise.

La matrice Quality et les limites d'environnement sont dans
[QUALITY_PHASE2.md](QUALITY_PHASE2.md). Ajouter un adaptateur futur exige ses tests
fresh/upgrade réels, sa preuve d'idempotence, ses limites d'effets externes et la
mise à jour des schémas SQL/installateurs applicatifs lorsque nécessaire.

## Adaptateur github.acquire (Phase 3)

Le `StepSpec` peut porter un `source` fermé : repository, ref, commit_sha. Ce champ
est absent des plans historiques, et non ajouté avec une valeur null : les digests
Phase 2 restent identiques. Le schéma journal reste en version 1. L'ancien plan
preflight adapter_version 1 est reconstruit à l'identique ; les nouveaux contrôles
core utilisent la version 2, dont l'avertissement reflète l'acquisition disponible.

Les plans GitHub sont produits côté serveur par `GitHubAcquisition`. Un journal
ne peut pas changer un chemin de destination, un dépôt ou un adaptateur arbitraire.
Chaque module constitue une frontière `github-web`, `github-gateway` ou `github-apk`.
La preuve locale contient propriétaire d'installation, archive SHA-256, empreinte
d'arborescence et marqueur de commit. Le credential ne fait pas partie de la preuve.

Un crash avant preuve complète permet un retry après réauthentification GitHub.
Une preuve valide après apply ou commit permet de finir hors ligne. Les étapes
DONE ne sont pas rejouées ; avant une action forward, leurs sources sont vérifiées.
Une dérive donne SOURCE_DRIFT et n'entraîne pas de téléchargement de remplacement.
Le rapport est le journal historique : le GET ne transforme pas un DONE historique
en vérification live de chaque fichier. L'action forward effectue ce contrôle.

Routes ajoutées : GET `/api/github/status`, POST `/api/github/validate`,
POST `/api/github/plan`, POST `/api/github/clear`. Les protections bootstrap et CSRF
restent obligatoires. La façade sérialise les mutations/credentials et renvoie BUSY
plutôt que de changer le credential pendant une acquisition. Un logout occupé peut
donc être refusé. Les GET du journal restent disponibles pendant l'opération.

Un journal core de Phase 2 n'est jamais remplacé silencieusement par un plan
GitHub : PLAN_EXISTS. Pour un chantier indépendant, utiliser un autre state-dir
privé ; ne pas supprimer un journal actif pour contourner ce contrôle.
Voir [GITHUB_ACQUISITION.md](GITHUB_ACQUISITION.md) pour les contrats de requêtes.
# Composition applicative 5D2

Voir [PHASE5D_WIZARD_COMPOSITION.md](PHASE5D_WIZARD_COMPOSITION.md) pour le parcours
fresh depuis le wizard, l'identité fixée avant approbation, la configuration
privée sans secret et la reconstruction exacte du registre après redémarrage.
Le contrat du journal historique reste inchangé ; les services ne démarrent pas
dans ce parcours de préparation sous maintenance.
