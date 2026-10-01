# 6B7b8 - Remplacement récupérable des bloqueurs mobile/Gateway

Base gelée : 6B7b7b e65fa34dfd1675a26654a39b50d44cfe39e9a5a1.
Ses trois campagnes Installer sont PASS. Sa recette native 36870409164 est
encore en cours au gel initial ; ses preuves devront être vérifiées avant de
lancer la recette de ce lot. Aucun succès natif n'est anticipé.

## Comportement

Le plan privé préparé en 6B7b7a lie les originaux, les parents, les propriétaires,
les permissions et l'ordre des services. Le nouveau BlockerState s'y rattache
sans effet, vérifie ses reçus et observations exacts, les parents externes/données
et les journaux de fichiers terminés. Il accepte seulement les états explicites
avant, pendant et après le retrait ordonné de mobile-reopen.attempt puis de
gateway-state.released.

Avant le premier retrait, une intention privée est écrite puis le nouveau
mobile-activation.attempt est créé et synchronisé. Chaque unlink est suivi de
fsync et d'un reçu privé. Une réponse perdue après l'effet se reprend sans
supprimer un fichier réapparu, réparer un journal altéré ni refaire une action
étrangère. Un remplacement absent après retrait est un refus, pas une réparation.
Les originaux restent dans le plan de reprise. Un reçu terminé est historique.

Le nouveau marqueur bloque aussi MaintenanceLease.resume, y compris malformé ou
lié. Il reste présent après la consommation des deux anciens. La maintenance,
son journal et les services arrêtés restent inchangés. Ce lot ne lève donc pas
la maintenance, ne démarre aucun service et ne clôt pas la phase 6.

## Admission courante

Apply, resume et check passent par mobile_blocker_admission.acquire. Chaque
appel impose consentement, confirmation du digest, bail natif, verrous exclusifs
de configuration, ordonnanceurs arrêtés, nouvelle barrière SQL bornée à 180 s
et nouvel export logique. Les comptes SQL applicatif et d'autorité sont distincts.
Les parents, copies, reçus, inodes, profils et état des services sont vérifiés,
ainsi que les sauvegardes et leurs sources exactes : SQL, données, Web,
configuration et Gateway. Les contrôles complets précèdent et suivent les effets,
puis se répètent à la sortie. Les limites natives restent inchangées.

L'enveloppe de configuration admet uniquement les deux retraits journalisés et
le nouveau marqueur exact. Aucun répertoire maintenance entier n'est ignoré.
L'ancien lecteur d'archives conserve son lecteur de marqueurs natifs ; seul son
constructeur pur de lignes est partagé avec la nouvelle composition. Les anciens
lecteurs de réouverture refusent toujours les marqueurs manquants.

Une fenêtre fermée ou d'un autre processus est inutilisable ; les objets ne
sont pas sérialisables. Les rapports n'accordent aucune autorité SQL persistante.
Après interruption, la reprise récupère le même bail puis ouvre une nouvelle
admission. Une dérive SQL avant l'intention ou après unlink empêche tout progrès.

## Validation et limites

Les contrats purs vérifient consentement, confirmation, fermeture de fenêtre,
révocation SQL/archives/configuration, enveloppe exacte et original non substituable.
Les tests de fichiers couvrent les vrais journaux, verrous, Ext4, les réponses
perdues et SIGKILL après chaque unlink, les fichiers altérés/liés, les anciens
lecteurs stricts et le maintien du refus de reprise. Ils isolent SQL et services ;
ils ne valent pas une qualification native SQL. Exécution uniquement en CI jetable.

La recette native prolonge le Gateway MAIN réel : deux SIGKILL, deux dérives SQL,
reprise et check sur exports distincts, originaux exacts, absence de démarrage,
maintien du nouveau bloqueur et refus de la reprise ancienne. Les mesures des
fenêtres terminées doivent rester sous 180 s. Le rapport natif attend 36 tests,
zéro erreur/échec/skip et les octets/modes exacts du nouveau gel.

Aucun ancien test ni identifiant requis n'est retiré. Quality Debian 12/13,
système et packages sont requis sur ce gel. Les verdicts, identités et artefacts
sont consignés dans la livraison et les issues existantes après vérification.
Aucune branche active n'est promue ; publication limitée aux branches techniques.
Aucun changement de schéma SQL ni d'install.php n'est nécessaire.

## Suite fonctionnelle

Après qualification native, implémenter l'activation récupérable sur ce bloqueur :
intention avant le premier start, ordre PHP/Apache/Foundation/Gateway/timer,
validation de chaque résultat et absence de retry automatique ambigu. La
maintenance doit être levée en dernier selon un protocole explicitement testé.
Ne jamais rappeler HttpDrain.recover après le premier démarrage : il arrêterait
les services déjà démarrés. Le nouveau lot reste privé, sans raccordement public
au wizard tant que cette activation et les scénarios fresh/upgrade finaux ne sont
pas qualifiés.

## Correction 6B7b8a - profil proxy canonique

Le run natif 36877093949 conserve 36 tests exécutés, une erreur, zéro échec/skip.
Il refuse l'attachement avant intention à mobile_blocker_state.py:104 : le profil
ProxyIngress porte un tuple client_networks en Python, relu en liste depuis JSON.
La comparaison directe échoue malgré des octets canoniques identiques. La fixture
fichiers utilisait ingress=None et ne couvrait donc pas ce cas. Le parent 6B7b7b
reste qualifié nativement (36870409164, artefact 11169952498 vérifié).

La correction reprend la comparaison canonique déjà utilisée par
DataReleasePlan._held, sans normaliser ni accepter de champs différents. Tous les
contrats fichiers du nouveau bloqueur utilisent maintenant le profil proxy réel.
Deux régressions vérifient le round-trip et le refus d'une adresse/réseau changé.
Aucun ancien identifiant requis n'est supprimé. Aucun effet n'avait eu lieu lors
de l'échec natif. Les preuves et le gel 03e008e restent conservés.

La nouvelle recette utilise le gel corrigé et le parent natif déjà acquis ; elle
ne relance pas le run échoué. Trois campagnes Installer sont requises avant la
publication de cette recette. Les verdicts sont dans la livraison. La phase 6
reste ouverte et aucun start n'est ajouté par cette correction.
