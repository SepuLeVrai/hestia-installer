# Préalables A et B avant la Phase 5

## Base et constat initial

Le commit `2d86b36da536f118ae5dbb79ddbba663644cf18f` publie la Phase 4 sur main,
avec le parent Phase 3 `710760aec85ae96795224adce8e91e37e5cb86e5`.
L'arbre complet est `0451fb4d41d6fea8315f7ff55563fe8bdfe6b134`.
La copie complète utilisée a été reconstruite depuis une archive Git du commit ;
son arbre Git, y compris modes exécutables et illustration, est identique.

La vérification initiale isolée est le run GitHub Actions `35963996055`, sur la
branche technique `test/installer-quality-20260924`, commit
`d98ae30b46361574e63878ac3aec9b24322b83f5`. Elle révèle un défaut de livraison :
`scripts/quality-wizard.sh` est en 0644 dans la Phase 4 publiée. L'appel documenté
`./scripts/quality-wizard.sh` échoue avec Permission denied, code 126, avant les
tests. Ce run rouge est une preuve conservée, pas une validation de Phase 4.

Le contenu original a ensuite été appelé localement via bash pour isoler le défaut
de mode : les 205 tests Python historiques sont réussis. La campagne complète du
nouveau lot est celle du workflow permanent et de ses preuves, pas ce diagnostic.

## Corrections et livrables

Le mode du script Quality est rétabli à 0755, préservé dans Git et les ZIP, et un
test de non-régression empêche sa perte silencieuse. Les tests historiques et
l'illustration sont conservés. Aucun schéma SQL, install.php, composant applicatif
ou secret HESTIA n'est modifié par ces préalables.

Le préalable B fournit les contrôles permanents décrits dans [QUALITY.md](QUALITY.md),
avec matrice Debian 12/13, suite navigateur native, inventaire de non-régression,
preuves JSON/JUnit, sélection documentaire fermée et packaging exact.

L'issue #11 doit être régularisée sur la publication effective de Phase 4 et sur
les preuves du lot de consolidation. L'issue #3 reste ouverte pour les écrans des
contrats applicatifs. Les anciens commentaires de blocage sont un historique,
pas une description du HEAD publié aujourd'hui.

Les références des commits de livraison, résultats observés, liens de runs et
empreintes du ZIP sont consignées dans l'issue de suivi et le compte rendu de
livraison, sans modifier les fichiers après le dernier contrôle du lot.

## Frontière suivante pour WORK

Phase 5 : contrat Web réutilisable, appelé sans automatisation de son install.php.
Relire les derniers commits des composants avant toute adaptation. Ne pas déduire
une installation applicative réussie des fixtures d'acquisition de ces préalables.

Les exigences supplémentaires validées pour les phases suivantes sont :

- acquisition en lecture des éléments nécessaires depuis hestia-nexus-avv,
  hestia-mobile-gateway et hestia-apk, avec commits/artefacts figés et compatibilité ;
  Actions Read pourra être nécessaire si les APK sont des artefacts Actions ;
- dépendances système, dont NGINX choisi, installées depuis des dépôts officiels
  autorisés ; jamais de propagation du credential GitHub aux dépôts système ;
- propriétaires et permissions appliqués et testés sous les identités des services,
  séparation du code et des données, aucun chmod 777 généralisé ;
- Assistant HESTIA optionnel : sans clé configurée, désactivation explicite côté
  serveur et interface. Réutiliser le stockage de secret Web existant hors webroot.
  En upgrade, conserver/remplacer/désactiver explicitement une configuration déjà
  présente ; un champ laissé vide n'efface pas un secret existant ;
- APK signée préconstruite et vérifiée, publication automatique dans la section
  Application mobile. Aucun SDK Android ou build systématique sur le serveur ;
- nouveaux adaptateurs réellement testés fresh ET upgrade, reprise, rollback,
  états vides, valeurs atypiques, textes longs et tailles d'écran raisonnables.

La configuration Assistant, la gestion système des permissions et le déploiement
applicatif sont des exigences du prochain lot, pas des fonctionnalités prétendument
implémentées ici. Toute adaptation nécessaire d'un autre dépôt exige son périmètre
d'écriture autorisé et sa Quality propre. Les workflows APK ne sont pas déclenchés.
