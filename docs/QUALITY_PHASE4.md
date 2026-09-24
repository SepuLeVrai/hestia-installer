# Quality Phase 4 - wizard connecté

## Référence et périmètre

Base relue sur main : `710760aec85ae96795224adce8e91e37e5cb86e5`.
Suivi : #11, dans le périmètre partiel du wizard complet #3.

Le lot relie les écrans existants au moteur d'acquisition et ajoute un brouillon
privé non secret. Il ne déploie ni Web, ni Gateway, ni SQL, ni APK. Les modes fresh
et upgrade testés sont ceux de l'acquisition. Aucun changement de schema.sql ou
install.php n'est nécessaire, aucun autre dépôt n'est modifié.

## Commandes reproductibles

```bash
./scripts/quality-local.sh
./scripts/quality-wizard.sh
```

La seconde commande inclut la première, puis les tests DOM Chromium. Node,
Playwright et Chromium sont des dépendances de Quality uniquement. L'absence d'un
outil requis fait échouer ce contrôle explicite ; aucun test navigateur n'est
silencieusement ignoré. Le bootstrap ne dépend toujours que de Python standard,
OpenSSL et iproute2.

## Campagne du lot

- 182 tests historiques des phases 1/2/3 conservés, sans modification.
- 23 nouveaux tests de contrat/brouillon/HTTPS : schémas fermés, données vides,
  longues, types et valeurs atypiques, secrets, permissions, liens, concurrence,
  révisions obsolètes, écriture atomique, reprise et retrait contrôlé du plan.
- 16 scénarios Chromium : parcours fresh/upgrade, Web seul/Web+Gateway/FULL,
  sélection vide, références avancées, GitHub refusé partiellement, préflight
  bloquant, consentement, modification de plan non appliqué, erreur/retry/rollback,
  reprise d'interface pendant une opération, rapport, clavier, erreur réseau
  initiale, message hostile et conflit de brouillon.

Le plan est exercé à dix tailles de fenêtre, de 320x568 à 1920x1080 ; le formulaire
GitHub à cinq tailles. Les contrôles portent sur les débordements horizontaux,
le footer desktop contenu dans la fenêtre, l'accès aux boutons et reduced-motion.
Le contenu long défile dans le panneau desktop ; sur mobile, la page défile.
Les redimensionnements sont mesurés après deux frames, et non pendant une mise en
page transitoire. Le décor original n'est pas redessiné.

La syntaxe Python et JavaScript, les motifs de sécurité interdits, les URLs d'assets
runtime et le préflight CLI réel sont contrôlés. Les HTML/CSS originaux récupérés
sont présents pour ce scan, contrairement à la copie partielle de la Phase 3.

Le Quality final est exécuté après la documentation. Son log joint donne le résultat
observé : 205 tests Python et 16 tests Chromium doivent être PASS, sans skip. Il ne
s'agit pas d'une couverture de code de 100 % ni d'une preuve formelle de sécurité.

## Limites explicites

Environnement : Debian 13, Python 3.13.5, Chromium et Node locaux. Debian 12 reste
couvert par les tests de préflight, pas par une nouvelle installation système.

Chromium géré refuse la navigation réseau dans cet environnement. Cette restriction
n'a pas été modifiée. Le HTML est lu par HTTPS puis rendu hors réseau dans Chromium,
avec le JavaScript de production inchangé. Un pont de test fetch relie le DOM aux
véritables endpoints HTTPS, en conservant authentification et CSRF côté serveur.
La navigation, le transport fetch et le téléchargement natifs du navigateur ne
sont donc pas une recette bout en bout native. Le test vérifie le contenu du Blob
de rapport, pas le téléchargement du navigateur. Les protections TLS/HTTP sont
également exercées par la suite d'intégration historique indépendante.

L'illustration WebP binaire inchangée n'est pas disponible dans cette copie partielle.
Le banc de géométrie utilise une image neutre de mêmes dimensions ; elle n'est ni
écrite dans les sources ni incluse dans la livraison. Son hash Git distant et son
chemin sont conservés. Aucune nouvelle validation artistique de l'illustration
originale n'est revendiquée.

Les réponses GitHub sont des fixtures contrôlées, sans PAT réel et sans téléchargement
privé réel. La recette sur le serveur cible doit vérifier la navigation HTTPS native,
le téléchargement du rapport et l'accès GitHub avec un credential saisi localement.
Les lectures des données privées du serveur et les effets applicatifs futurs ne
sont pas validés par les seules fixtures d'acquisition.

## Gel et packaging

Après le dernier Quality réussi, les fichiers sont gelés. Le ZIP léger contient
uniquement les fichiers nouveaux ou modifiés, jamais de .patch, dépendance vendue,
cache ou secret. Les hashes de chaque entrée sont comparés au manifeste gelé et aux
blobs publiés. Le commit et le SHA-256 du ZIP sont fournis dans le compte rendu de
livraison, sans modifier les sources après le contrôle final.
