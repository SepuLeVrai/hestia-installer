# Acquisition GitHub lightweight - Phase 3 / issue #8

## Périmètre

L'installer acquiert les sources, pas encore les installations applicatives.
Aucun dépôt applicatif, client git, package pip, SDK GitHub ou asset distant n'est
ajouté au bootstrap. Les modules sélectionnables sont exactement :

| Module | Dépôt |
|---|---|
| web | SepuLeVrai/hestia-nexus-avv |
| gateway | SepuLeVrai/hestia-mobile-gateway |
| apk | SepuLeVrai/hestia-apk |

L'accès aux trois dépôts est obligatoire avant de planifier, même pour web seul.
L'acquisition ne télécharge ensuite que les modules sélectionnés. Toutes les
opérations réseau sont des GET ; aucun droit d'écriture GitHub n'est requis.
Le branchement visuel du wizard constitue la Phase 4. Aucun champ ou bouton n'a
été ajouté au HTML/JavaScript dans ce lot.

## Credential et validation

Recommandation : fine-grained PAT restreint aux trois dépôts, Metadata Read et
Contents Read. Pas de username/password, SSH key, argument CLI ou variable
persistante pour transmettre le credential à l'installer.

Le client vérifie pour chaque dépôt la réponse Metadata puis résout sa branche
par défaut via `/commits/<ref>` avec le média `application/vnd.github.sha`.
Le second GET exerce Contents Read. Six requêtes légères, aucune archive pendant
cette validation. Un 401/403/404 donne GITHUB_ACCESS_DENIED ; une limite API donne
GITHUB_RATE_LIMITED. Aucun retry automatique en boucle. Après correction, soumettre
un nouveau credential. Le test de lecture n'inspecte pas tous ses droits accordés.

La validation produit un snapshot en mémoire des refs et SHA. Durée logique :
15 minutes, vérifiée à chaque usage/statut. Un échec de revalidation invalide
l'ancien credential. Le statut ne révèle que ready et les dépôts/refs/SHA.

## Contrats HTTPS

D'abord déverrouiller le bootstrap, récupérer le cookie sécurisé et le CSRF de
`GET /api/session`. Tous les POST suivants exigent `X-Hestia-CSRF`, Content-Type
application/json, une session authentifiée et l'Origin du bootstrap lorsqu'envoyé.
Les exemples de valeurs entre chevrons sont des placeholders, pas des credentials.

```text
POST /api/github/validate
{"credential":"<PAT éphémère saisi par l'administrateur>"}

GET /api/github/status

POST /api/github/plan
{"modules":["web","gateway"],"refs":{},"mode":"fresh"}
```

Les trois champs du plan sont obligatoires. `mode` vaut fresh ou upgrade, sans
modifier une BDD à cette phase. `refs` est vide ou limité aux modules sélectionnés.
Par défaut, les SHA inspectés lors de la validation sont conservés. Une ref
explicitement choisie (branche, tag ou SHA) est résolue au moment de la planification.
Le SHA de téléchargement ne bouge plus après confirmation. Les noms de ref sont
ASCII bornés, sans contrôle, query, URL, traversée ou segment ambigu.

Le plan et son plan_sha256 sont inspectables avant toute écriture des sources.
Sa persistance crée uniquement le journal privé. Répéter le même plan après un
refresh conserve le digest, sans réseau ; une sélection différente est refusée.

```text
POST /api/installation/apply
{"confirm":true,"confirmation":"<plan_sha256 approuvé>"}

GET /api/installation/state
GET /api/installation/report

POST /api/installation/resume
{"confirm":true,"confirmation":"<même plan_sha256>"}

POST /api/installation/retry
{"confirm":true,"confirmation":"<même plan_sha256>","name":"github-web"}

POST /api/installation/rollback
{"confirm":true,"confirmation":"<même plan_sha256>","boundary":"github-web"}

POST /api/github/clear
{}
```

Une réponse HTTP réussie peut porter un journal FAILED : examiner son état et
last_error_redacted, pas seulement le statut HTTP. Après échec terminal, le
credential est retiré ; refaire validate avant un retry qui exige le réseau.
Après DONE, les références au credential sont retirées du magasin. Clear, logout
et arrêt gracieux les retirent aussi. Rien n'est placé dans le navigateur hors
la saisie temporaire du futur formulaire ; aucune persistance n'est autorisée.

## Transport sortant

API GitHub version 2026-03-10, TLS vérifié, TLS 1.2 minimum. Les seules origines
acceptées sont api.github.com et codeload.github.com, HTTPS standard. Aucun proxy
implicite d'environnement, cookie amont, URL fournie par le navigateur ou
redirection automatique. Le header Authorization est non redirigeable par urllib.

Le GET API `/tarball/<sha40>` peut répondre directement ou par 302 vers le chemin
codeload exact `/owner/repo/legacy.tar.gz/<sha40>`. Le client abandonne toute query
signée reçue et reconstruit l'URL sans query. Il recrée explicitement Authorization
sur cette destination GitHub autorisée. Aucun autre hôte/port/chemin, fragment ou
second saut n'est accepté. Les textes d'exception et corps d'erreur ne sortent pas.

Timeout socket : 15 secondes ; budget lecture metadata 30 secondes, archive
300 secondes, contrôlé entre lectures courtes. Aucune stratégie de retry infinie.
Une coupure réseau laisse une frontière reprenable, sans téléchargement silencieux
sur un nouveau HEAD. Une évolution du protocole GitHub refusée par ces contrôles
nécessitera une mise à jour explicite, pas un fallback non vérifié.

## Extraction et limites

| Limite par archive | Valeur |
|---|---:|
| Archive compressée | 128 MiB |
| Un fichier | 64 MiB |
| Contenus cumulés | 512 MiB |
| TAR décompressé, padding/métadonnées compris | 576 MiB |
| Entrées TAR et inodes créés | 50 000 chacun |
| Métadonnée PAX / GNU longname | 16 KiB |
| Chemin / composant | 1 024 / 255 octets UTF-8 |
| Profondeur | 32 composants |

Les archives doivent porter le wrapper GitHub attendu, lié au dépôt et au SHA.
Les tailles sont contrôlées avant allocations importantes. CRC gzip, checksums
TAR, fin d'archive et contenus supplémentaires sont vérifiés. Un seul arbre,
non vide, est accepté. Les chemins absolus, ../, antislash, caractères de contrôle,
collisions casse/Unicode, .git, liens, devices, FIFO et sparse sont refusés.

PAX path/comment/mtime/atime/ctime et GNU longname sont bornés ; les extensions
inconnues sont refusées. Les fichiers .gitmodules et pointeurs LFS reconnus sont
refusés : pas de résolution récursive ni récupération LFS implicite. Un gitlink
absent de l'archive et sans .gitmodules n'est pas découvrable depuis ce seul format ;
aucune complétude de sous-modules cachés n'est promise. Le contenu n'est jamais exécuté.

## État, preuves, reprise et rollback

```text
<state-dir>/state.json
<state-dir>/sources/<module>-<sha40>/
    owner.json
    archive.tar.gz
    tree/
    manifest.json
    committed.json
```

Tous ces chemins sont calculés côté serveur. Dossiers 0700, fichiers 0600 ; seuls
les fichiers sources exécutables dans Git deviennent 0700. Aucun héritage de droits
ou propriétaire amont. Le manifest conserve l'identité d'installation, le dépôt,
la ref/SHA et les empreintes SHA-256 archive/arbre. Le journal conserve seulement
les preuves non secrètes. Les clés GitHub/FCM/DB ne font pas partie du schéma.

Le staging source est persistant, distinct du staging TLS nettoyé à l'arrêt.
Reprendre une preuve apply/commit valide ne demande aucun réseau ni credential.
Une frontière partielle exige une nouvelle validation GitHub puis un retry ciblé.
Une source DONE modifiée ou supprimée bloque l'action forward avec SOURCE_DRIFT :
aucune régénération. Les ressources étrangères restent intactes. Le rollback est
local à sa frontière ; une preuve ambiguë mène à MANUAL_ACTION_REQUIRED. Un crash
au milieu d'une suppression peut nécessiter une vérification manuelle.

`--check` reste identique. `--dry-run` relit le plan persistant hors ligne, ou montre
le contrôle core sans journal. `--report` lit l'état, `--resume` rouvre un nouveau
bootstrap authentifié sans rejeu automatique. Un ancien journal core est conservé,
mais ne peut être transformé en plan sources : utiliser un autre state-dir pour
une installation distincte. La phase finale gérera le nettoyage définitif du cache.

## Références et vérifications

- GitHub Contents / archives : https://docs.github.com/en/rest/repos/contents
- GitHub Commits : https://docs.github.com/en/rest/commits/commits
- urllib et redirections : https://docs.python.org/3.11/library/urllib.request.html
- Risques d'extraction : https://docs.python.org/3/library/tarfile.html#extraction-filters

Voir [QUALITY_PHASE3.md](QUALITY_PHASE3.md) pour les tests réellement exécutés et
la distinction entre HTTPS local réel et accès privé GitHub en environnement cible.
