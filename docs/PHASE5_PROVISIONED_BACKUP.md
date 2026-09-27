# Sauvegarde intégrée des services provisionnés

## Correctif de diagnostic —27septembre2026

Le candidat270f2526/run36302090702 conserve106 contrôles Debian13 verts et
12/13 scénarios réels verts ; zéro erreur ou skip, un échec sur le code public
attendu après apparition d’un stockage IA hors profil. La sauvegarde est bien
incomplète, sans reçu vérifié et avec maintenance conservée. Le chemin positif
complet passe. Ces résultats ne valent pas qualification du correctif ci-dessous.

Cause reproduite localement : le context manager SQL interceptait l’exception
émise par le coordinateur pendant yield et la remplaçait par une erreur de
transport SQL. Le correctif limite cette conversion à l’acquisition/libération ;
l’erreur de l’appelant conserve son type pour la politique fermée du coordinateur.
Le nettoyage du worker, du groupe et des pipes reste inconditionnel. Un contrôle
supplémentaire vérifie l’identité de l’exception et la fin effective du worker.
Aucune assertion du scénario réel n’est retirée ni assouplie.

Nouveau gel :75 contrôles locaux,107 prévus sous Debian13 et les mêmes13 scénarios
réels. Aucun second job lancé à ce point d’arrêt ; validation réelle du correctif
requise avant de le déclarer qualifié. Preuve négative et source270f2526 conservées
au checkpoint. Toutes les frontières globales de phase5 restent inchangées.


## Admission de configuration — lot du27septembre2026

Le parent c8a32e619067f44976f876a2231d4893afba2f84 est acquis : run36265588722,
66 contrôles locaux,98 contrôles Debian13 et7 scénarios intégrés, sans échec,
erreur ou skip. Les attentes et compteurs de la section historique plus bas
restent ceux de ce parent. Le présent lot ajoute huit contrôles locaux et six
scénarios réels ; gel prévu à74 contrôles locaux,106 Debian13 et13 scénarios.
Un seul job ciblé est prévu ; les résultats exacts appartiennent au checkpoint.

Le coordinateur provisionné prend désormais deux verrous partagés :
`assistant-edit.lock` root0600 (créé exclusivement s'il manque), puis
`assistant.json` root/groupe Web0660. Ils restent ouverts autour de toute la
copie/restauration et des contrôles finaux, y compris en dehors du composant SQL.
Les lecteurs imbriqués restent compatibles ; l'API Installer et le writer Web
utilisent les verrous exclusifs correspondants. Un writer déjà actif refuse
l'admission avant réservation. Les inodes, propriétaires, modes, ACL, octets et
journaux de réglages sont revérifiés ; remplacement, tentative incomplète ou
dérive refuse le reçu. Aucun secret n'est affiché ni modifié par l'admission.

Le worker SQL vérifie avant, puis sous son verrou global, l'absence d'autre
schéma utilisateur que la base cible. Les schémas système MariaDB sont admis.
La vérification revient à chaque échange, sans reconnexion. Deux clés fixes
App_Config sont lues avec un résultat borné à3 lignes et4096octets par valeur.
`HESTIA_MOBILE_RELEASE_DIR` doit être absent ou vide. `security.ged_legacy_roots`
doit être absent, vide ou contenir au plus16 chemins relatifs valides, sans
traversée, segment vide, contrôle ou profondeur supérieure à64 ; ils désignent
les descendants déjà sauvegardés de `uploads/ged_legacy`. Les liens restent
refusés par la capture des données. Aucune destination mobile n'est provisionnée.
Les refus SQL exposent seulement les codes fermés SERVER_PROFILE_REJECTED ou
STORAGE_PROFILE_REJECTED préfixés SQL_FENCE ; jamais les noms ou valeurs privées.

Le profil PHP-FPM exact fixe les racines uploads/imports/session/temp/log et
clear_env ; il n'injecte ni chemin Assistant historique, ni racines GED externes,
ni configuration mobile fondation. Pour le chemin IA ancien encore présent dans
le Web épinglé, l'admission exige l'absence de `/etc/hestia/conf_db_ia.php`,
`<webroot>/includes/conf_db_ia.php` et `/var/lib/hestia-ai`. Tout élément présent,
y compris lien ou répertoire vide, refuse ; les parents doivent être protégés.
Ces fichiers PHP ne sont jamais exécutés ni lus. L'absence est revérifiée pendant
la fenêtre, sans prétendre empêcher un administrateur root de modifier l'hôte.

Le reçu ajoute `installer_settings_fenced` et `configuration_storage_admitted`
et le manifeste utilise PROVISIONED_HTTP_CLEANER_SQL_AND_CONFIGURATION_V2.
Ces assertions concernent cette fenêtre et le profil borné, pas tous les
producteurs de l'hôte. Les CLI, planificateurs étrangers et écritures root
restent une admission distincte à fermer. `storage_inventory_complete`,
`complete_web_backup`, `system_wiring_verified` et `phase5_complete` restent faux.
Le chemin coordonné historique sans barrière de services ne change pas.

La recette positive restaure aussi une racine GED relative et bloque réellement
l'API de réglages pendant la copie, sans journal ni changement de secret ; les
verrous redeviennent disponibles après la sortie. Six scénarios négatifs ajoutés :
schéma SQL étranger, destination mobile externe, traversée GED, stockage IA ancien,
writer de réglages concurrent, apparition du stockage IA après copie. Les sept
scénarios parents sont repris parce que la nouvelle admission les traverse.
Le moteur systemd/SQL/Web réel reste nécessaire ; les contrôles de pipes et
flock locaux ne le remplacent pas.

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

66 contrôles ciblés locaux ;98 avec les32 cas de fichiers/coordinateur dans le
conteneur Debian13. Sept nouveaux cas système/SQL/Web réels : chemin complet
et écriture SQL concurrente, mort du worker SQL, altération de barrière après
copie, processus étranger, racine supplémentaire, annulation, autorité SQL fausse.
Le premier restaure GED/photo/import/session puis les relit via le vrai Web,
avec reprise **explicitement effectuée par la fixture**.

Les neuf scénarios historiques business storage sont verts sur le candidat initial
fe95da21/run36264655150 ; ils ne sont pas présentés comme une nouvelle exécution
sur le correctif de staging. Le job final cible les98 contrôles et7 nouveaux cas. Le proxy de la nouvelle fixture utilise une identité distincte ; réutiliser
l'UID Web serait justement refusé par le drain. Le getty console inutile de
l'image jetable est masqué ; les contrôles produit ne sont pas assouplis.
Les contrôles PHP/SQL/systemd impossibles dans Work sont exécutés dans ce seul
job, après gel code/documentation. Résultats et sources exacts au checkpoint ;
ce document ne préjuge pas de leur succès. Pas de Quality globale ni promotion.

## Incidents conservés et correction

Le premier run36264655150 passe96/97 contrôles et les9 business historiques.
Le sous-processus d'un contrôle partait du mauvais cwd : le workflow est corrigé,
aucune assertion retirée. Le run36264993299 passe ensuite97/97 contrôles mais
4 des7 nouveaux cas refusent avant réservation : le writer de secrets16Kio
recevait backup_bridge.php (33062 octets). Le refus CONFIGURATION_SIZE_REJECTED
est reproduit localement. Les deux artefacts négatifs restent au checkpoint.

Le correctif fait copier ce fichier par le source-bundle existant, renomme son
bridge dans le stage privé protégé et écrit uniquement le petit canal et sa
politique avec le writer de secrets. Sa limite reste inchangée. Un onzième test
vérifie les octets/modes du grand fichier et le refus du writer16Kio.66 contrôles
locaux/98 Debian13 désormais ; nouveau gel code/docs avant job ciblé final.
Les compteurs prévus plus haut décrivent ce correctif, pas un vert anticipé.

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
