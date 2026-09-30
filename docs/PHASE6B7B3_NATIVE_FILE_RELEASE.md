# Phase 6B7b3 - raccordement natif du plan de libération des fichiers

## Périmètre

La base qualifiée est 6B7b2, Installer
`d407c530a14763a849f6a1381d634191c4e090de`, arbre
`310719782fc7c4aa23f37f5fafe9487c5b3b6445`. Ce lot raccorde son sous-plan privé
à la recette composée réelle Gateway/MAIN. Aucun code de production, endpoint,
StepSpec historique, schéma SQL ou composant Web/Gateway/APK n'est modifié.

La recette préexistante fournit le vrai binaire Gateway qualifié, Foundation MAIN,
Web, PHP-FPM, MariaDB, les comptes privés, systemd et un volume Ext4 jetable.
Elle conserve les acquisitions authentifiées, consentements navigateur, sondes
signées MAIN, backup composé, restauration isolée et coupures Gateway antérieurs.
Elle se poursuit maintenant jusqu'à la libération des trois protections de
fichiers. Elle s'arrête avant l'admission actuelle SQL et tout démarrage effectif.

## Réacquisition et effets vérifiés

La continuation reprend le MaintenanceScope et son lease_id exacts. Elle reprend
DataAccessFence, reconstitue HttpDrainLease à partir de son profil durable et
observe la réservation externe existante. Elle reprend les verrous de configuration
liés à cette réservation. Aucun audit de service, compte, archive ou système de
fichiers n'est remplacé par une simulation dans cette continuation native.
Elle n'appelle pas HttpDrain.recover et n'émet aucun stop/start pour reconstruire
ces objets. Les seules injections terminent le processus après le vrai syscall.

Le nouveau plan est écrit, relu identique, puis explicitement confirmé. Trois
SIGKILL successifs interviennent après le premier retrait de flag données, après
le retrait du journal configuration RELEASE et après le retrait du marqueur Web.
À chaque reprise, le même bail et le même plan sont retrouvés, l'étape RUNNING
est observée et les originaux restent identiques. Le dernier resume termine le
sous-plan. Le check suivant est sans réécriture des preuves.

Une altération du code Web après DONE doit révoquer le check natif. Seule la
fixture restaure ensuite ses propres octets et recontrôle le résultat. Les appels
systemd start explicites des services restent bloqués par la maintenance ; aucun
service ne devient actif. Le timer du cleaner reste arrêté et n'est pas démarré
par cette vérification. Les journaux parents, unités Web, clés MAIN/DEV et UUID SQLite
restent inchangés. Les protections d'accès données et réservations externes sont
toujours présentes. Le reçu mobile n'est jamais consommé.

La lecture SQLite d'identité intervient après le dernier contrôle exact des
fichiers Gateway : une connexion SQLite mode=ro peut créer ou retirer des fichiers
WAL auxiliaires. La vérification d'identité reste native et complète. Les lecteurs
de production et leur détection de dérive ne sont pas assouplis.

Chaque enfant composé est borné à 900 secondes, avec échec explicite si la
coupure n'est pas atteinte. Cela couvre les audits natifs répétés de plusieurs
étapes ; les helpers et budgets historiques restent inchangés. La recette entière
est bornée à 55 minutes, dans un job de 60 minutes incluant collecte et nettoyage.

## Sources et preuves

Les sources Web exécutées restent épinglées à
`2a27c7a1f9fe0a00289eb53278f75d5f230900b7` (arbre
`783be5abdcd5e13addefe96d743eee3a97b7a6de`), avec la fixture legacy
`46c03060625d4d53c675474b11aaa33007d9aad7`. Gateway reste au commit
`e2c09f53593bf316906ccc4387f185e73e7f85a8`, package SHA256
`f3138b5bd4fc5c85e4dcf9e4480d8f521f72c02db34219cb9053c38a1eae8a0e`.
La branche de recette Web est purement technique et conserve les sources
applicatives exactes. Elle épingle le commit et l'arbre Installer de ce lot.

La suite native garde 36 tests : 35 contrats Gateway et une recette composée
étendue. Les trois nouvelles coupures sont des assertions de cette recette,
pas trois tests indépendants supplémentaires. `mobile-reopen-files-native.json`
porte la preuve explicite du raccordement. Le manifeste natif doit correspondre
à celui des campagnes Quality complètes, système et paquets du même Installer.
Les résultats finaux, SHA et identités des recettes accompagnent la livraison.

## Point d'arrêt et suite

Ce lot qualifie la reprise native des protections de fichiers. Il ne transforme
pas leur retrait en admission des données courantes ou du SQL. Le contrat inode
données ne hache pas leur contenu ; les archives et l'export SQL actuel doivent
être recontrôlés sous la limite de verrou de 180 secondes avant toute réouverture.

La prochaine tranche doit réunir cette admission et le retrait récupérable des
réservations externes puis du mode données 0700. L'admission liée aux réservations
doit être fermée avant leur retrait, puis réacquise sans réservation. Un bloqueur
doit rester durable jusqu'au reçu d'admission. Les démarrages auront leur propre
intention boot_id/InvocationID/temps monotone, sans répétition aveugle après réponse
perdue. Boot/public, DEV natif, FCM et restauration originale restent ouverts.
La phase 6 et la réouverture complète ne sont pas clôturées par ce lot.
