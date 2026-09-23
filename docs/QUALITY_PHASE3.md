# Quality Phase 3 - acquisition GitHub / 2026-09-23

## Base et portée

Base relue : main `3c7453d4ede6bb364f34263ed5929358d7ec1929`, Phase 2 acceptée.
Issue de suivi : #8. Aucun autre dépôt HESTIA modifié. Aucun SQL/install.php,
compilation APK ou workflow Actions ajouté/déclenché par ce lot.

Le contrôle final exécute `./scripts/quality-local.sh` après toutes les modifications,
documentation comprise. Le ZIP différentiel contient uniquement les fichiers
ajoutés/modifiés, gelés après ce contrôle et comparés au manifeste SHA-256.
La publication doit conserver exactement ces octets ; le commit et le SHA-256 du
ZIP sont fournis dans la livraison et le compte rendu de l'issue, hors des sources.

## Matrice exécutée

| Groupe | Tests |
|---|---:|
| Phase 1 et Phase 2, fichiers de tests inchangés | 111 |
| Accès et transport GitHub | 18 |
| Extraction d'archives | 20 |
| Sources, journal, reprise et rollback | 20 |
| API sous bootstrap HTTPS réel | 10 |
| Transport urllib/TLS sortant réel sur serveur local | 3 |
| Total | 182 |

Résultat attendu au gel : tous PASS, aucun test ignoré. Le journal d'exécution
final accompagne le ZIP. Cette matrice est un nombre de tests, pas un taux de
couverture ni une preuve formelle de sécurité.

Les scénarios supplémentaires comprennent :

- validation Metadata/Contents des trois dépôts sans archive initiale ; sélection
  des sept combinaisons de modules, en modes fresh ET upgrade du moteur ; SHA
  figé malgré changement de branche ; aucun téléchargement des modules absents ;
- credential vide/long/atypique, identifiants/ref/SHA invalides, JSON dupliqué,
  erreurs 401/403/404/429/500, expiration, revalidation et absence de secret dans
  journal/statut/rapport/logs ;
- redirections host/port/protocole/chemin/fragment refusées, query signée abandonnée,
  header reconstruit seulement vers codeload autorisé ; timeout, longueur HTTP,
  taille et Content-Encoding non conforme ;
- archives normales, PAX/longnames, Unicode, permissions, archives vides, limites
  de contenu et d'inodes implicites, chemins hostiles, liens/fichiers spéciaux,
  collisions, bombes de métadonnées/padding, gzip tronqué/CRC, données TAR cachées,
  .gitmodules/LFS et credential connu traversant deux blocs de lecture ;
- sources préexistantes étrangères, symlink, dérive des preuves, rollback isolé,
  retry partiel, concurrence, reconnexion et CLI hors ligne ; conservation des
  plans core Phase 2 adapter_version 1 sans changement de digest ;
- arrêts réels de sous-processus après apply, commit et rollback puis reprise
  sans credential ni nouveau réseau ; arrêt au milieu d'un téléchargement, refus
  SECRET_REQUIRED puis validation du credential et retry ciblé ;
- HTTPS bootstrap : session/CSRF/Origin, tailles, double JSON, clear/logout,
  déconnexion et redémarrage avec ancien cookie refusé et journal conservé ;
- véritable pile urllib/TLS avec certificats de test, vérification de confiance,
  302, streaming et absence de cookies ou query transmis ; TLS non fiable refusé
  avant requête HTTP et aucun second appel vers un hôte hostile.

## Environnement et limites explicites

Debian 13, Python 3.13.5, OpenSSL et iproute2 présents, Node.js 22.16.0.
Les 111 tests historiques sont relancés intégralement. Debian 12 est couvert par
le préflight simulé existant ; aucun nouveau runtime Debian 12 n'est revendiqué.
Les modes fresh/upgrade ici valident l'acquisition et son journal, pas une BDD ou
un déploiement HESTIA Web/Gateway/APK, qui restent hors de cette phase.

Les réponses GitHub sont des fixtures contrôlées. Les tests sortants HTTPS réels
utilisent un serveur local avec certificat de test pour les noms api/codeload,
sans relâcher le TLS du code de production. Aucun PAT réel de l'utilisateur n'est
fourni, aucun téléchargement privé réel depuis GitHub n'est exécuté dans cet
environnement. La compatibilité avec les dépôts et credentials de la cible devra
être constatée lors du premier parcours réel, sans jamais transmettre le PAT ici.

Les deux JavaScript originaux sont relus au SHA de base, vérifiés par hash Git,
et contrôlés par Node. Le HTML/CSS/WebP inchangé n'est pas reconstitué dans cette
copie de test ; le scan local des assets porte donc sur les JS disponibles. Aucun
nouveau test visuel/responsive ni scan exhaustif d'assets absents n'est revendiqué.
Le lot ne modifie aucun asset, HTML, CSS ou JavaScript et le tree distant est conservé.

Les contrôles de confidentialité combinent schéma fermé, codes d'erreur fixes et
secrets connus. Ils ne détectent pas universellement un secret déjà commité dans
un dépôt. Les scénarios root hostile, panne physique du stockage et contenu
incomplet d'un sous-module invisible dans l'archive ne sont pas couverts. Une
reprise ambiguë reste fail-closed avec action manuelle plutôt qu'un écrasement.

La Phase 4 branchera le wizard ; cette Phase 3 livre le moteur et les routes typées.
