# Modèle de sécurité

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

La Phase 1 fournit le canal HTTPS, la session et la protection CSRF nécessaires. L'acquisition GitHub est traitée dans une phase ultérieure.

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
