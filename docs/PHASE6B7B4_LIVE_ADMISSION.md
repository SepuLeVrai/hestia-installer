# Phase 6B7b4 - fenêtre privée d'admission SQL et fichiers

## Base et frontière

Base qualifiée 6B7b3 : Installer `e0f90297323de102368380a4f12c64ac32e75324`,
arbre `be823daace3cd7844fcdab58dfda5e6318d7622b`. Le nouveau module privé
`mobile_reopen_admission` s'exécute après le sous-plan de libération des fichiers.
Il n'ajoute ni endpoint, ni démarrage, ni consommation de bloqueur. Les anciens
StepSpecs, lecteurs de barrières et contrats de sauvegarde sont inchangés.

Ce lot construit une **fenêtre vivante** : sa preuve dépend d'un vrai verrou SQL
encore détenu, du bail de maintenance exact et des observations natives actuelles.
Un fichier `observed.json` est une trace historique. Il ne permet ni de retrouver
la fenêtre après un crash, ni d'admettre de nouveaux démarrages.

## Vérifications avant et pendant la fenêtre

L'entrée impose le ReopenFilesPlan exact, terminé et explicitement confirmé. Son
check natif revalide les trois étapes DONE et les bloqueurs. La configuration,
les sources Web épinglées et le reçu fresh sont rattachés à la même instance.
Seul le profil MAIN local managed, sans boot/public, est admis ici. Les credentials
d'autorité SQL sont renouvelés par l'appelant natif et restent distincts du compte
applicatif. Ils ne sont ni persistés ni renvoyés dans les rapports.

Le contrôle réutilise les lecteurs stricts acquis et vérifie :

- Le reçu Gateway, son état vivant et ses archives, via le sous-plan qualifié.
- Le manifeste coordonné, son reçu, le manifeste SQL et le reçu de restauration.
- Les octets SQL archivés, leur fin d'export, tous les blobs Web/configuration,
  les blobs de données, leur inventaire exact et le journal fresh sauvegardé/vivant.
- Les contenus, métadonnées et inventaires vivants des racines de données inscrites.
- L'enveloppe Web/configuration complète et les changements précis de ses journaux.
- La configuration courante, l'arrêt des services et l'observation des schedulers.

La maintenance n'est pas un sous-arbre ignoré. Quatre marqueurs archivés retirés
doivent correspondre exactement aux originaux conservés et au reçu Gateway. Les
nouveaux journaux autorisés sont reconstruits depuis le plan terminé, ses intentions,
ses reçus et ses originaux. Tous les autres fichiers archivés doivent rester égaux.
Un journal supplémentaire, un parent manquant ou une permission modifiée est refusé.
Les exclusions Git/CI/cache du lecteur historique restent son contrat existant.

La fenêtre reprend le `SqlReadFence` qualifié, limité à 180 secondes et à son
protocole de vérification existant. `_recheck` effectue un nouvel export canonique
du SQL courant et compare tables, lignes et empreinte logique avec la restauration
qualifiée. Le reçu précédent et de simples comptes de lignes ne suffisent pas.
Les fichiers, archives et gardes sont revérifiés après cet export et à chaque
`assert_held`, ainsi qu'à la sortie normale. Une expiration, perte du verrou,
dérive ou annulation révoque la fenêtre et maintient l'activité fermée.

## Journaux et interruption

Chaque appel crée un répertoire privé distinct `admission-<identifiant aléatoire>`
dans la racine de sauvegarde existante. Ce répertoire est hors de la configuration
vivante. Il conserve une intention, le nouvel export privé et, après validation,
une observation. Les modes, liens, propriétaires, tailles et écritures exclusives
suivent les lecteurs privés existants. Les diagnostics sont fixes et non secrets.

Une interruption laisse l'intention et les éventuels octets partiels en place.
Aucun export existant n'est écrasé, repris comme succès ou supprimé implicitement.
Une nouvelle demande refait l'observation et l'export dans un autre répertoire.
Une fenêtre ne se sérialise pas, appartient au processus créateur et refuse tout
appel après sa fermeture. Aucune fonction de chargement d'un ancien succès n'existe.

Le read fence libère son verrou au terme du contexte, y compris en erreur. Cela
ne retire ni maintenance, ni mode données 0700, ni réservations externes, ni bloqueurs.
La fenêtre ne certifie pas des écritures administratives futures, tous les producteurs
de l'hôte ou une synchronisation après sa fermeture.

## Qualification attendue

Seize nouveaux contrats de politique couvrent les transitions exactes, les fichiers
de maintenance étrangers, les parents altérés, les chemins d'archive, la fermeture,
le changement de processus, la perte du verrou et la confidentialité des erreurs.
Ils complètent le socle obligatoire sur Debian 12 et 13 ; ils ne remplacent pas
la recette native composée.

La recette Gateway/MAIN est prolongée après 6B7b3 et avant la lecture d'identité
SQLite terminale. Elle garde ses 36 tests (35 contrats et une recette étendue).
Elle coupe réellement après le nouvel export SQL et avant `observed.json`, reprend
le bail et réalise un nouvel export. Elle vérifie la conservation de l'essai coupé,
le refus d'une dérive de données courantes, d'un blob archivé, d'un journal étranger
et du SQL courant après fermeture. Le rapport natif explicite est
`mobile-reopen-admission-native.json`.

Les systèmes, SQL et comptes réels sont exclusivement exécutés en CI jetable.
Les quatre campagnes finales doivent porter le même Installer figé. Cette
documentation est figée avant ces campagnes ; leurs verdicts et SHA exacts sont
consignés dans les suivis des dépôts et dans la livraison, sans retouche après tests.

## Suite bornée

La fenêtre garde encore les réservations et le DataAccessFence fermés. Elle ne
peut donc pas être utilisée telle quelle après leur retrait : ce sera une nouvelle
frontière explicitement journalisée, avec fermeture du contexte de configuration
lié aux réservations puis réacquisition adaptée. Les bloqueurs doivent rester
durables jusqu'à l'admission et aux reçus nécessaires à cette frontière.

Les starts exigent ensuite leurs propres intentions boot_id/InvocationID/temps
monotone et une réconciliation sans répétition aveugle. Ne jamais rappeler le drain
après un premier start effectif. Phase 6, boot composé, DEV, FCM, restauration
originale et recette globale 6C restent ouverts.
