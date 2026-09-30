# Phase 6B7b2 - plan privé de libération des protections de fichiers

## Périmètre et point d'arrêt

Le nouveau `ReopenFilesPlan` enchaîne trois étapes TransactionEngine : inodes des
données, configuration puis code Web. Il s'appuie sur la base 6B7b1 qualifiée
`b7a9845441138996d116bb06a773996a554b5c20`. Les plans et lecteurs historiques ne
sont pas modifiés. Aucun endpoint ni bouton de wizard n'est ajouté à ce stade.

Le résultat `FILE_PROTECTIONS_RELEASED_ACTIVITY_CLOSED` signifie que les trois
protections immutable ont été retirées sous le même bail de maintenance. Les
accès canoniques aux données restent en mode 0700, les réservations externes
restent immuables, Gateway et le bloqueur mobile gardent leurs reçus, et les
services restent arrêtés. `DONE` désigne exclusivement ces trois étapes.
Ni admission du SQL courant, ni restauration sur les chemins originaux, ni
démarrage, ni réouverture HTTP ne sont livrés par ce lot. La phase 6 reste ouverte.

## Contrat natif et conservation

L'appelant privé fournit les types natifs exacts : HttpDrainLease, DataAccessFence,
ConfigurationLease liée à ExternalFence, GatewayServiceRuntime et répertoire de
sauvegarde privé. Tous doivent appartenir à la même instance et au même bail.
Le profil exige Foundation MAIN, Gateway et le cleaner arrêtés. Tout profil
public ou répertoire natif boot/public présent est refusé. Ces lecteurs ne
réadoptent pas une installation boot avec une ancienne copie de code.

Avant le premier effet, les cinq journaux complets d'origine sont conservés sous
`maintenance/mobile-reopen-files/`, root:root 0700, fichiers 0600 exclusifs avec
fsync. Le profil lie leurs empreintes au reçu Web composé, au reçu Gateway exact,
au répertoire de sauvegarde, au profil de services, à l'instance et au bail.
Un fichier partiel, modifié, lié ou avec des permissions inattendues est refusé.
Le sous-journal TransactionEngine est distinct de tous les journaux initiaux.
Une coupure entre profil complet et création du journal nécessite une inspection
manuelle ; ce lot ne reconstruit pas un journal absent à partir d'un profil seul.

La confirmation doit être le digest exact du nouveau plan. Le bloqueur 6B7b1
est alors créé, ou repris s'il correspond exactement. Un ancien bloqueur créé
avec un digest arbitraire n'est pas migré. Aucun code ne supprime les bloqueurs
mobile/Gateway, le bail de maintenance, l'accès données ou les réservations.

La récupération Gateway 6B7a recontrôle la propriété native, les services arrêtés,
ses fichiers courants, ses images sauvegardées et le reçu Web composé. Le manifeste
Web exact est recontrôlé. Les marcheurs historiques vérifient les inodes, modes,
identités et flags ; configuration et Web incluent leurs empreintes de contenu.
Le marcheur inode des données ne recertifie pas leur contenu. Ce lot ne refait
ni l'export SQL courant, ni une restauration exhaustive des archives Web/données.
Ces preuves supplémentaires restent obligatoires avant toute admission.

## Coupures et revalidation

Chaque étape publie une intention privée liée à l'identifiant d'installation,
au profil et au StepSpec avant tout retrait de flag. Elle publie ensuite un reçu
après les contrôles et retraits de marqueurs. Les cinq originaux restent intacts.

La reprise traite le retrait partiel des flags, la réponse perdue après retrait
de RELEASE, celle après retrait de MARKER et celle après écriture du reçu. Dans
le cas RELEASE absent/MARKER présent, elle exige l'intention externe exacte,
l'intégralité du journal d'origine et un arbre entièrement revenu à ses flags
initiaux. L'absence seule ne prouve jamais un succès. Aucun lecteur bas niveau
historique n'est assoupli et aucune étape ne regèle les fichiers.

Les étapes DONE sont revalidées avant de poursuivre et à chaque frontière native.
Les étapes encore PLANNED doivent rester entièrement scellées. Une dérive ferme
la progression ; les reçus ne remplacent pas une observation actuelle. La reprise
du TransactionEngine suit ses opérations explicites resume/retry, sans rollback.
Un check final vérifie le sous-plan sans réécrire ses preuves.

## Qualification prévue sur les octets figés

La baseline Quality conserve tous les scénarios antérieurs et ajoute 16 tests.
Ils utilisent les vrais fichiers privés, verrous, ioctl Ext4 et permissions dans
le volume jetable de la CI Debian 12/13. Ils couvrent les trois étapes, les deux
frontières unlink pour chacune, les réponses perdues, les preuves altérées,
l'absence de consentement, les profils exclus et un SIGKILL réel suivi d'une
réacquisition du bail exact. Les audits systemd/Gateway sont isolés dans cette
suite de fichiers ; elle ne revendique pas une recette mobile composée native.

Les workflows Quality complets, système et paquets restent obligatoires. Leurs
résultats finaux et l'identité exacte des sources sont joints à la livraison.
L'intermittence historique du refresh natif APK reste suivie sans correction
revendiquée ni assertion assouplie.

La première Quality 36710658968 a détecté un attribut du nouvel adaptateur qui
masquait Operation.plan(), puis un ordre de nettoyage incorrect de la fixture.
L'attribut a été renommé et les contextes d'admission sont fermés avant le
nettoyage des réservations de test. Le diagnostic 36711256818, sur branche séparée,
exécute ensuite les 16 scénarios avec succès sur les deux Debian, sans affaiblir
les assertions. Le workflow diagnostique n'appartient pas aux sources livrées.

## Suite après ce point d'arrêt

Raccorder le sous-plan dans une recette native composée MAIN local, puis fournir
une admission actuelle SQL/Web/données sous verrou borné à 180 secondes, avec
relecture des archives et des parents. Libérer ensuite les réservations externes
hors du contexte d'admission qui les recontrôle en sortie, reprendre un contexte
d'admission sans réservation, puis rouvrir l'accès données de façon récupérable.
Conserver un bloqueur jusqu'au reçu durable d'admission. Les démarrages doivent
posséder leur intention boot_id/InvocationID/temps monotone, leur reçu exact et
la réconciliation de réponse perdue. Ne jamais rappeler HttpDrain.recover après
un start. Les sondes Web/MAIN précèdent la clôture de la réouverture composée.
