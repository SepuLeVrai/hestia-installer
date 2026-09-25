# Point de reprise Phase 5 - 25 septembre 2026

## Statut vérifié

La mission complète est toujours la fin de 5C puis 5D. Ce point de reprise
documente un sous-lot qualifié, sans clôturer 5C2 ou Phase 5.

Candidat Installer exact : `726757eeb139818a7aa93b90586091ff37a08f85`,
tree `c1a013d3a979de13089f7294602d709a1a436cec`,
branche `quality/phase5c2-repair-20260925`.

- [Installer Quality](https://github.com/SepuLeVrai/hestia-installer/actions/runs/36126405630) : SUCCESS, 5 jobs réussis. 456 tests core sur chacun de Debian 12 et Debian 13, 16 tests navigateur et 21 HTTPS, plus 16 gardes statiques.
- [SQL/HTTP réels](https://github.com/SepuLeVrai/hestia-nexus-avv/actions/runs/36126448323) : SUCCESS, 6 jobs, 100 scénarios distincts, aucun échec, erreur ou skip.
- Les journaux finaux des 11 jobs ont été relus via GitHub, y compris les résumés JSON SQL.
- Les artefacts finaux de cette dernière campagne sont présents dans Actions. Leur contenu ZIP n'a pas été téléchargé/relu localement après la déconnexion de l'environnement. Les comptes ci-dessus proviennent des journaux exécutés, pas d'une supposition sur les ZIP.

| Suite SQL/HTTP | Tests réussis |
| --- | ---: |
| Préparation SQL | 18 |
| Finalisation | 21 |
| Précontrôles upgrade | 15 |
| Sauvegarde et restauration isolée | 30 |
| Maintenance coopérative | 7 |
| Réparation explicite DEFINER | 9 |
| Total | 100 |

La preuve SQL utilise le Web inchangé
`46c03060625d4d53c675474b11aaa33007d9aad7`, tree
`aaac278270e0fd1169396945916dfe997ae078bf`, 1840 fichiers vérifiés.
Le commit technique Web
`d8ac49cbabf9e7fcef4877ca5d5338b5797e7e5d` ne doit jamais être fusionné.
Il transporte le workflow jetable et l'archive Installer, pas une évolution applicative.

Le catalogue [JSON des preuves](CHECKPOINT_PHASE5_20260925_EVIDENCE.json)
conserve les références exactes, jobs et empreintes des artefacts. Les artefacts
Actions expirent : récupérer les livrables utiles avant expiration, puis vérifier
leurs empreintes et manifests avant toute livraison finale.

## Fonctionnalités implémentées et limites

Le candidat qualifié cumule :

1. Une identité DEFINER durable, verrouillée et à droits minimaux pour les futurs fresh managed, sans conserver le compte temporaire.
2. Une sauvegarde de secours explicite des installations historiques affectées, restaurée et testée en clone isolé sans imposer une sauvegarde opérationnelle impossible sur la source défectueuse.
3. Une barrière de maintenance coopérative avec verrou interprocessus, refus HTTP 503, drainage, maintien de la fermeture après mort du contrôleur et reprise explicite.
4. Une réparation explicite sous maintenance, sauvegarde de secours avant DDL, comparaison des données et définitions, restauration opérationnelle après réparation et conservation des secrets, utilisateurs et sessions du profil couvert.

Contrats : [DEFINER](PHASE5C2_DEFINER.md),
[secours](PHASE5C2_RESCUE.md), [maintenance](PHASE5_MAINTENANCE.md),
[réparation](PHASE5C2_REPAIR.md).

Les neuf nouveaux scénarios couvrent aussi le DDL réellement interrompu après
un premier trigger, la réponse perdue après mutation, la restauration finale
échouée, la dérive après sauvegarde et un écrivain SQL non drainé. Les marqueurs
`.attempt` interdisent le rejeu automatique. Une anomalie après réservation
maintient la maintenance et demande une action de reprise explicite.

La barrière coopérative doit encore être raccordée et prouvée dans la vraie
configuration système. Son API seule ne garantit pas qu'un serveur arbitraire ou
tous ses producteurs utilisent cette barrière. La reprise de DDL partiel et le
rollback produit ne sont pas livrés. Les fichiers métier modifiables et sessions
externes ne sont pas encore couverts par une sauvegarde complète.

## Travail restant, dans cet ordre

1. Terminer l'inventaire et la sauvegarde/restauration des fichiers modifiables : GED, uploads, imports et traitements, sessions PHP, temporaires utiles, stockage externe et usages Assistant. Couvrir modes, propriétaires, liens et cohérence avec SQL sous maintenance effectivement raccordée.
2. Construire un catalogue fermé de transition entre vrais commits source/cible, avec évolution fonctionnelle réelle. Aucune version ou migration artificielle pour satisfaire une recette. Préserver données, utilisateurs, RBAC, mots de passe, secrets et enveloppes.
3. Finir 5C4 : observation et reprise explicite après interruption, SQL/DDL partiel, fichiers et enveloppes cohérents, rollback vérifié, refus d'une ancienne restauration après reprise d'activité.
4. Finir 5D : dépendances officielles Debian 12/13, Apache/FPM, écoute loopback et proxys de confiance, permissions, sessions techniques 43200 et options fonctionnelles 1h/4h/8h, conservation de phpsessionclean.
5. Raccorder les opérations réelles au wizard figé. Qualifier un fresh et un upgrade système complets sur Debian 12 et Debian 13.
6. Actualiser et consolider la documentation, geler les arbres exacts, exécuter toutes les Quality, vérifier les artefacts et fabriquer les ZIP légers contenant les fichiers complets modifiés et leurs manifests.
7. Seulement après tous les résultats verts et les documentations réalisées : PR/promotion autorisées. Pour Web, candidate vers dev-Bastien puis main. Ne pas toucher dev, ni fusionner les branches techniques.

Points d'étude, sans choix déjà acté : Debian 12 fournit PHP 8.2 alors que le
vendor Web actuel exige PHP 8.3 ou plus ; ne pas affaiblir le contrôle de plateforme.
Le stockage externe persistant a été étudié pour séparer code immuable et données
modifiables, mais aucune modification Web correspondante n'a été écrite.
Les versions, migrations, seeds et pins Web sont inchangés à ce checkpoint.

## État Git conservé

HEAD actifs relus après la fin des campagnes :

- Installer main : `c0dcb902663130302599635b36c7fb8deab80a47`.
- Web main et dev-Bastien : `46c03060625d4d53c675474b11aaa33007d9aad7`.

Aucune PR créée, aucun fast-forward de branche active, aucun déploiement de
production. LAB-PAWEB30, bases réelles, Gateway et APK restent hors des mutations.

Chaîne des checkpoints Installer, tous candidats uniquement :

- Sauvegarde initiale : `84426c3294e2c5d9b4b4f34fb29fc0dc3d33bc13`.
- DEFINER durable : `12a7e8a045b4f62c22b314173ea0900f512f52de`.
- Sauvegarde de secours : `6cd77cac61c2f5c7256ad320e3bb3f5b954b7910`.
- Maintenance : `3d7440157a3251b54be820d1af5acaa8794a02f6`.
- Réparation qualifiée : `726757eeb139818a7aa93b90586091ff37a08f85`.

## Incident de l'environnement de travail

Après le lancement et le succès distant des campagnes, l'exécution locale a
retourné deux fois `409 Conflict, environment_offline: Environment is not connected`.
Le connecteur GitHub est encore accessible. Ce document et son JSON ont été
conservés directement dans une nouvelle branche de checkpoint. Les deux fichiers
de checkpoint ne modifient pas le code qualifié et ne transforment pas le résultat
du parent en qualification d'une nouvelle version produit.

Dernier état local connu avant déconnexion :
`/workspace/scratch/d9d30314e612/repos/installer`, branche `work/phase5`,
HEAD `3d7440157a3251b54be820d1af5acaa8794a02f6`, index et fichiers déjà égaux
au tree `c1a013d3a979de13089f7294602d709a1a436cec` du candidat réparé.
Ne pas faire de reset destructif à la reprise. Relire le statut local et les HEAD
distants ; au besoin reconstruire un checkout propre du commit qualifié.
Le Web local original se trouve dans `/workspace/scratch/d9d30314e612/repos/web`.

Le ZIP de reprise utilisateur a pour SHA-256
`17eba879ee2c1733620f0b16eb0b3f3df5ffe688668a62872d3d634a46dab5fc`.
Relire son mandat et les documents normatifs des dépôts. L'autorisation d'écrire
dans les dépôts nécessaires reste acquise, avec PR et fast-forward uniquement
après toutes les Quality vertes et les documentations réalisées.
