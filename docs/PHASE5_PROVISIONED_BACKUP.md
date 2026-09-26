# Sauvegarde intégrée des services provisionnés

## Périmètre du lot du 26 septembre 2026

Base Installer `8e063bc3d58e94f7694373ff5360165af38222b4`, arbre
`2ae5e65ab651659bace6a0433dea86276bf5cd92`. Ce parent est qualifié par le
run36261287478 :325 contrôles et20 cas réels, dont12 du lecteur configuré.
Les deux preuves négatives antérieures sont conservées au checkpoint parent.

La priorité convenue est désormais un parcours fonctionnel pour les instances
provisionnées par HESTIA. La découverte générique des commandes systemd reste
différée ; ses lecteurs qualifiés sont conservés. Aucun service étranger n'est
adopté et aucun résultat inconnu ne devient une autorisation.

`ProvisionedBackup` réunit des primitives existantes dans une opération privée :
drain HTTP/collecteur, verrou SQL continu, sauvegarde coordonnée et restauration
réelle dans des destinations isolées. Il ne s'agit pas encore d'une transition
de version, d'un rollback ou d'une activation depuis le wizard.

## Contrat effectivement appliqué

- Objets typés `HttpRuntime` et `SessionCleaner` liés à la même instance,
  PHP8.4, uploads externes, Web complet épinglé au commit
  `2a27c7a1f9fe0a00289eb53278f75d5f230900b7`.
- Instance finalisée depuis un fresh managed (`migration_retained=false`), SQL
  local127.0.0.1, sceau et configuration exacts. Une base existante n'est pas
  transformée en preuve fresh ; un fresh n'est pas une preuve d'upgrade.
- Consentements stricts pour maintenance/sauvegarde et verrou SQL global.
  Payload upgrade/preserve utilisé pour lire une instance existante, sans
  migration ni modification de version.
- Répertoire de maintenance commun au slot scellé et au runtime HTTP ; fragments
  et profils exacts, arrêt du timer puis Apache/PHP/collecteur, cgroups vides et
  absence de processus de l'identité applicative ailleurs.
- Inventaire construit par le produit : `sessions`, `tmp`, `upload-tmp`,
  `imports`, `log`, `uploads`, tous sous `<http_root>/data`. Ensemble exact,
  propriétaires et modes contrôlés par les contrats existants. Une racine
  supplémentaire sous ce parent provoque un refus ; aucune liste partielle
  fournie par le client n'est acceptée dans ce profil.
- Code et contrôles du Web restent immuables. Le profil ne change ni les chemins
  GED/photos/imports déjà livrés, ni les sessions1h/4h/8h et le GC43200s.

Le parent `data` reste protégé root/groupe Web ; les six racines sont privées à
l'identité dédiée. Les règles anti-liens, ACL, volumes étrangers, hardlinks et
fichiers exécutables de `DataInventory` continuent de s'appliquer.

## Continuité SQL et preuve commune

Un worker PHP dédié et sans privilèges système conserve la même connexion PDO
entre `FLUSH TABLES WITH READ LOCK` et `UNLOCK TABLES`. Le verrou précède la
réservation du backup et la première copie ; il couvre l'export SQL, sa
restauration dans MariaDB isolée sans TCP, les cinq déclencheurs, la restauration
des blobs, la seconde empreinte SQL et les contrôles finaux d'enveloppe.
Les exports historiques gardent leurs propres protections.

Le canal privé n'accepte que acquire/check/release. Identifiant aléatoire,
séquences exactes, JSON borné, aucune commande SQL client, aucun secret dans argv,
stdout public ou erreurs. Chaque contrôle exige une réponse de la connexion
originale, sans reconnexion. Le worker est dans son propre groupe, sans core,
avec limites CPU/mémoire/fichiers ; fermeture des pipes ou décès du parent libère
la connexion. Le parent tue et récolte le groupe en sortie exceptionnelle.

Bornes :180s pour la fenêtre,12s par échange,16Kio pour l'entrée initiale,1Kio
pour les échanges suivants, au plus64 séquences. Les plafonds de fichiers/SQL
parents restent des plafonds d'admission, pas une promesse de débit en180s.
Toute borne dépassée refuse le reçu commun. Aucun paramètre global persistant
du serveur SQL n'est modifié ; le verrou bloque temporairement les écritures
sur **toutes** ses bases, ce qui exige un serveur dédié au profil.

Références de sémantique MariaDB :
[FLUSH](https://mariadb.com/docs/server/reference/sql-statements/administrative-sql-statements/flush-commands/flush),
[UNLOCK TABLES](https://mariadb.com/docs/server/reference/sql-statements/transactions/transactions-unlock-tables).
La recette vérifie réellement le blocage d'une autre connexion puis sa levée.

Le coordinateur revérifie la barrière HTTP/collecteur et le verrou SQL entre
les composants et juste avant le reçu. Manifeste et reçu lient le digest du
profil. L'état positif `PROVISIONED_BACKUP_RESTORE_VERIFIED` prouve le périmètre
enregistré, avec `provisioned_services_drained` et `sql_read_fence_verified` vrais.
Le chemin coordonné historique sans barrière garde son état et ses préconditions.

## Échecs et arrêt

Avant réservation : refus fermé, aucun backup créé. Si le drain a commencé, la
maintenance demeure. Après réservation : tentative conservée, aucun reçu commun
vérifié, inspection manuelle nécessaire. Perte du worker/verrou, dérive du profil,
annulation et erreur de libération finale ne certifient jamais l'ensemble.
Une nouvelle invocation n'adopte ni ne rejoue la tentative précédente.

Même en succès, la maintenance demeure et les services restent arrêtés. La
libération SQL ne rouvre pas HTTP. La reprise explicite de la même maintenance
existe dans la primitive de drain ; l'opération de sauvegarde ne l'effectue pas.
Elle ne remplace pas les données originales et ne démarre aucun service.

## Qualification du lot

65 contrôles ciblés locaux ;97 avec les32 cas de fichiers/coordinateur dans le
conteneur Debian13. Sept nouveaux cas système/SQL/Web réels : chemin complet
et écriture SQL concurrente, mort du worker SQL, altération de barrière après
copie, processus étranger, racine supplémentaire, annulation, autorité SQL fausse.
Le premier restaure GED/photo/import/session puis les relit via le vrai Web,
avec reprise **explicitement effectuée par la fixture**.

Les neuf scénarios historiques business storage sont conservés dans le même job
ciblé. Le proxy de la nouvelle fixture utilise une identité distincte ; réutiliser
l'UID Web serait justement refusé par le drain. Le getty console inutile de
l'image jetable est masqué ; les contrôles produit ne sont pas assouplis.
Les contrôles PHP/SQL/systemd impossibles dans Work sont exécutés dans ce seul
job, après gel code/documentation. Résultats et sources exacts au checkpoint ;
ce document ne préjuge pas de leur succès. Pas de Quality globale ni promotion.

## Frontières encore ouvertes

`storage_inventory_complete`, `complete_web_backup`, `system_wiring_verified`,
`writable_business_storage_ready`, `service_activation_delivered`,
`application_installed`, `phase5_complete`, apply et rollback restent faux.
Le verrou SQL ne remplace pas le contrôle des autres écrivains de fichiers.
Le census d'identité constate une population ; il ne prévient pas le lancement
ultérieur d'une tâche étrangère ni une écriture administrative root.

Avant clôture5C2, il reste à rendre opposable le profil d'admission des neuf
groupes de producteurs : interdire/raccorder les CLI et planificateurs, configurations
de racines externes, écritures Installer et destinations mobile/Assistant, puis
prouver l'exhaustivité données/configuration. Ces situations ne sont pas certifiées
par la seule présence des six racines ou des quatre unités provisionnées.
La fermeture correspondante doit refuser les instances incompatibles, pas étendre
implicitement le périmètre de cette sauvegarde.

Ensuite : vraie paire de versions et bascule5C3 ; incidents/reprise/rollback5C4 ;
activation et journal/écrans5D ; Quality complètes du même arbre et promotions
autorisées. Aucun déploiement métier, TLS public/ACME, APK ou Gateway dans ce lot.
