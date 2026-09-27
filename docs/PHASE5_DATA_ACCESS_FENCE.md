# Fermeture persistante des chemins de données provisionnés

## Changement fonctionnel

Base qualifiée : `9a8cb414eb8d60f3fef2730f186d912d0561c073`. Après le drain
HTTP/collecteur et avant la première copie, la sauvegarde provisionnée ferme
la traversée du parent canonique `<runtime>/data`. Ce parent reste root/groupe
applicatif ; son mode passe de 0750 à 0700. Les propriétaires, modes et octets
des six racines enfants ne sont pas modifiés.

Une commande PHP ou un service/timer lancé ensuite sous l'identité applicative
ne peut plus ouvrir, créer ou supprimer un fichier en passant par ces chemins.
La protection vient des permissions du noyau, sans dépendre de la coopération
du script ou de la détection d'un processus suffisamment long. Le verrou SQL
continu garde sa propre fonction pendant la copie et la vérification.

Il s'agit d'une barrière sur les chemins canoniques, pas d'une certification
de tous les producteurs. Root ou CAP_DAC_OVERRIDE peuvent contourner les
permissions. Les alias de montage, autres espaces de noms, références déjà
transmises à une autre identité et écritures administratives ne sont pas prouvés
absents. `foreign_cli_controlled` et l'inventaire global des ordonnanceurs restent
faux. Aucun ordonnanceur étranger n'est arrêté, adopté, supprimé ou modifié.

## Ordre et courses

1. Les admissions existantes sont vérifiées, la maintenance persistante est
   publiée et les services provisionnés sont drainés.
2. Un journal `data-access.attempt`, protégé root/groupe Web 0640, est écrit et
   synchronisé. Il lie l'instance, la lease de maintenance, le parent exact,
   son device/inode et les modes d'ouverture/fermeture.
3. Le parent épinglé par descripteur est fermé en 0700 et synchronisé.
4. Un second census refuse tout processus restant de l'identité ou du groupe
   applicatif. Un processus ayant acquis un descripteur juste avant la fermeture
   empêche ainsi de poursuivre ; il n'est jamais tué par ce mécanisme.
5. La sauvegarde exige la barrière typée, liée à sa lease et son runtime exacts.
   Elle vérifie régulièrement le journal, l'inode, les permissions et le gate.

Un journal manquant, corrompu, lié à une autre maintenance, un remplacement du
parent, une ACL ou une réouverture inattendue refusent la preuve. Le runtime
reconnaît le mode 0700 uniquement avec ce journal exact et la maintenance encore
active ; un chmod isolé ne devient pas un état accepté.

## Sortie, interruption et reprise

Une sortie normale, une exception, une annulation ou la mort du contrôleur
ferment seulement les descripteurs du contrôleur. Le parent reste en 0700 et
le journal demeure. Le reçu de sauvegarde ne rouvre pas les données.
`MaintenanceLease.resume()` refuse tant que ce journal existe, même incomplet.

Si l'interruption survient entre l'intention durable et le chmod, aucun reçu
n'est émis et le parent peut encore être en 0750. La récupération explicite
de la lease exacte, puis `data_access.recover(runtime, lease, confirmed=True)`,
termine uniquement la fermeture. Elle n'adopte pas une tentative étrangère.
Ce cas ne doit pas être présenté comme une fenêtre déjà protégée.

Après décision explicite de reprise, `DataAccessFence.reopen(confirmed=True)`
contrôle le profil et l'absence de processus, rétablit 0750 puis retire son
propre journal. La maintenance principale et les services restent fermés.
La reprise de l'activité est une opération distincte. Une interruption de
réouverture exige inspection ; aucune réparation automatique n'est ajoutée.

## Reçu et compatibilité

Le manifeste passe à la politique
`PROVISIONED_HTTP_CLEANER_SQL_CONFIGURATION_SCHEDULERS_DATA_PATHS_V4` et lie
`data_access_fence.fence_sha256`. Le reçu expose `canonical_data_paths_fenced`.
Ces champs attestent la fenêtre contrôlée, pas l'état futur après une intervention
administrative. Le chemin coordonné historique sans services provisionnés garde
son contrat ; aucun paramètre public ne permet de fabriquer la nouvelle barrière.

Pas de changement Web, SQL, `schema.sql`, `install.php`, APK, Gateway ou UI.
Les parcours d'installation et de sauvegarde d'une instance existante sont
exercés dans le banc ; la vraie transition applicative 5C3 reste distincte.

## Qualification sur le gel

90 contrôles locaux attendus, puis 129 sur Debian 13 avec les contrôles de fichiers
et sept tests réels de permissions sous un UID distinct. Ces derniers vérifient
24 refus d'accès, un détenteur de descripteur existant, l'intention incomplète,
la dérive d'inode/mode/journal et la réouverture explicite.

22 scénarios intégrés attendus : les 17 conservés et cinq ajouts. Ils lancent
réellement un PHP CLI et un timer natif après la copie, injectent un écrivain
dans la fenêtre de fermeture, tuent le contrôleur après chmod et altèrent les
permissions après copie. Le parcours positif restaure et rouvre explicitement
le Web avec ses documents, photos, imports, sessions et racine GED relative.
Aucun skip n'est admis. Les résultats du commit exact sont au checkpoint.

Les producteurs privilégiés et les chemins alternatifs restent à admettre ou
refuser avant de fermer la couverture des écrivains. L'exhaustivité données et
configuration, 5C2, 5C3, 5C4, 5D et la Quality globale restent ouvertes.

## Sémantique système

- [Résolution des chemins et permission de recherche](https://manpages.debian.org/trixie/manpages/path_resolution.7.en.html)
- [Capacités permettant de contourner les permissions](https://manpages.debian.org/trixie/manpages/capabilities.7.en.html)
