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

Les journaux HTTP n'incluent ni headers, ni corps, ni query string. Seuls la méthode, le chemin sans paramètres et le statut sont journalisés.

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
