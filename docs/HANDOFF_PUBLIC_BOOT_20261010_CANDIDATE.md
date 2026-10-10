# Transfert public/boot - qualification en cours, 10 octobre 2026

Le périmètre demandé n'est pas encore qualifié de bout en bout. La branche
canonique `quality/phase6-gateway-lifecycle-20261004` reste à
`1028c05ce48d0f528f8b6f71837be51ee15710a0`. Les candidats sont publiés sur
`validation/phase6-public-fragments-20261010`. Aucun merge ni déploiement.

## Implémentation actuelle

Les huit fragments ont un transfert durable par inode, un arrêt natif préalable
et un rechargement systemd explicite. Les générations successeurs conservent les
anciens bundles et les chemins des certificats. Le pointeur de sélection est
immuable et refuse toute incohérence, sans repli implicite vers la source.

L'admission publique lie exactement le drain historique, le contexte HTTP,
la publication cible et les inodes installés. Les gardes SQL natifs restent
bornés à 180 secondes. La consommation des gardes autorise l'activation locale.

Le cockpit public comporte sept étapes : binaires, bascule, publication,
transfert public/boot, admission, activation locale et ouverture publique.
Les intentions de démarrage HTTP, HTTPS et timer précèdent leurs effets.
Une réponse perdue exige l'observation du processus déjà lancé ; un démarrage
ambigu sur un service arrêté n'est jamais rejoué. Les contrôles d'une nouvelle
époque PID 1 lisent les preuves du boot successeur, sans recycler les anciennes
invocations ni démarrer les services.

## Preuves déjà obtenues et limites

- Admission : candidat `b7974e273f117753ad3b3e925f06a7f27984a5b6`, arbre
  `c6923ddadcb60766f3929915c02f03d0c64720e8`, 540 fichiers. Quality
  `38043452943` et protocole/systemd `38043452912` terminés avec succès.
- Première ouverture : candidat `f614b202af19143aeec0f398c0efadbcefcb4159`,
  arbre `e766c74c0c64382f3c5ea8c0f9b92ec8820037db`, 544 fichiers.
  Protocole/systemd `38044621397` terminé avec succès ; Quality
  `38044621429` : suites Debian 12/13, navigateur et gate final réussis.
- Recette composée `38044621464` : échec avant essais, le jeton Installer ne
  peut pas lire le dépôt Web privé. Aucun PASS natif public issu de ce run.
  La recette réutilisable est désormais appelée depuis une branche technique
  Web privée, avec un SHA Installer exact. Aucun paquet privé n'est copié dans
  le dépôt public Installer et aucun nouveau credential interdépôts n'est créé.
- Localement : 20 contrats d'ouverture, 5 de profil/progression publics et
  23 contrats cockpit existants passent. Les tests nécessitant les vrais UID
  système ne passent pas dans le namespace local limité à root. Les assertions
  de propriétaires et les exigences Ext4 restent intactes.

La recette composée crée des hôtes Debian 13/Ext4/PID 1 indépendants pour
upgrade et rollback MAIN, sans DEV/FCM. Elle réutilise les sources Web figées,
les vrais paquets Gateway et une CA ACME jetable. Elle doit prouver les SIGKILL,
la conservation des sources/clés/UUID/certificats, les accès Web/Mobile, puis
une nouvelle époque PID 1 dans le même noyau. Aucun résultat n'est anticipé.

## Travail restant avant livraison finale

Obtenir et inspecter les verdicts natifs composés, corriger les refus éventuels
sans affaiblir les contrôles, compléter la recette du cockpit public et du
renouvellement successeur, puis exécuter la Quality du gel final. Authentifier
les artefacts et leurs manifestes, actualiser les verdicts et livrer les fichiers
complets du gel testé. Les échecs utiles restent conservés.

Le cycle upgrade/rollback/upgrade sur un même hôte, DEV/FCM, la restauration
sur l'origine et la clôture globale de phase 6 restent distincts. #17 et #18
restent ouverts. Aucun changement SQL, `schema.sql` ou `install.php`.


## Retours du premier banc composé

Le run Web `38045068546` a franchi les sources privées, les paquets, Ext4,
la préparation SQL/Web et la CA jetable, puis a échoué dans une assertion du
setup historique : `boot_runtime.py` a évolué depuis le bundle 5D6 pour lire
le profil Mobile Web v2. Le banc public utilise toujours le profil fresh v1.
Le nouveau setup vérifie cette version et n'admet que le remplacement exact
du constructeur `FreshProfile(instance)` par `FreshProfile.from_draft`.
Le bundle historique reste inchangé ; toute autre différence est refusée.

La Quality `38045041384` a réussi ses deux suites core mais échoué dans la
construction du nouveau fixture navigateur : changer l'identité en remplaçant
des chaînes laissait les empreintes dérivées incohérentes. Le fixture est
reconstruit avec les factories réelles. Aucun contrôle produit n'est relâché.

La relecture de l'activation ajoute une permission distincte pour les gardes
SQL/Web requis par PHP avant l'ouverture publique. Elle exige le propriétaire
consommé, l'intention PHP durable et l'époque PID 1 de l'activation. Mobile et
renouvellement restent fermés. Une nouvelle époque ne peut pas réutiliser cette
permission locale. Cette évolution doit encore passer la recette composée.

Le candidat `24e747eb7ccdee3edee75791cc68051651a27dd4`, arbre
`6eed255770813bccff1d9c76dfd92ef4a043bd58`, 545 fichiers, a passé Quality
`38045497569` et protocole/systemd `38045497660`. Les six archives sont
authentifiées par leur SHA-256 GitHub ; cinq manifestes correspondent exactement
à cet arbre. La recette composée Web `38045509282` poursuit ses essais natifs.

Le candidat suivant ajoute un reçu d'ouverture complète lié aux trois reçus
HTTP/HTTPS/timer. Un nouveau PID 1 ne peut pas utiliser une ouverture incomplète.
Le banc vérifie aussi les politiques d'accès après transfert et après boot,
puis deux renouvellements ACME réels avec le worker successeur. Ces ajouts ne
sont pas encore qualifiés par les runs ci-dessus.

Ce candidat `72375aebf69eb1e236502416de62e099a6e3330c`, arbre
`9d539d30fd6f7494dfce4aa26b39dcbb61dcac92`, a passé Quality `38046408523`
et protocole/systemd `38046408531` : 2 098 tests core par Debian, 37 bridge,
49 HTTPS natif, 84 contrats ciblés et 10 tests systemd. Ses six archives et
cinq manifestes exacts sont vérifiés ; les captures publiques à 480 px sont
lisibles. La recette composée Web `38047122006` a été lancée sur ce SHA.

La recette précédente `38045509282` a franchi les SIGKILL après transfert et
admission, puis l'upgrade a refusé l'activation dans `DataReleasePlan._held` :
`CONFIGURATION_TARGET_OCCUPIED`, dû au refus privé historique de `boot/public`.
Le même refus subsistait dans `BlockerState.static` et son admission.
Le correctif exige le contexte public vivant, la même identité HTTP, le bail
exact et le profil de drain natif conservé ; hors de ce contexte, l'absence
de `boot/public` reste obligatoire. Aucun contrôle de données ou SQL n'est retiré.
Trois contrats ciblés vérifient ces autorisations et refus. Les deux campagnes
précédentes ne peuvent pas qualifier ce correctif.

Le correctif `123db0e4a6c2da1362309b4cac8b3bede5a0ed6d`, arbre
`1fe076160f7668964f0146f6a5a8a5fc126e133e`, a passé Quality `38047548649`
et protocole/systemd `38047548692` : 2 101 tests core par Debian, 37 bridge,
49 HTTPS natif, 87 contrats ciblés et 10 tests systemd. Le premier job Debian 12
a refusé un chemin temporaire aléatoire du fixture historique avec
`SECRET_REJECTED`, avant le test. Son artefact est conservé ; la relance de ce
seul job au même SHA, puis le gate, réussissent. Les six archives retenues et
leurs cinq manifestes correspondent à cet arbre. Recette native : `38047575225`.

Le rollback du run antérieur `38045509282` a échoué dans l'admission externe
sur `SQL_FENCE_TIMEOUT`. La limite de 180 secondes reste obligatoire. L'analyse
locale identifie des constructions répétées du même profil TLS immuable dans
le compilateur pur. Le candidat suivant valide une fois ces mêmes octets par
calcul de manifeste, sans cache persistant et sans changer les audits natifs.
Six variantes conservent exactement les sorties ; 56 contrats passent.
Le microbenchmark local de 30 manifestes passe de 1,25 s à 0,08 s ; ce n'est
pas une preuve du temps SQL natif. Les échecs utiles des campagnes précédentes
restent conservés et ne sont pas assimilés à un PASS.


## Gel Quality et retours natifs suivants

Le compilateur pur est publié dans `835a6fba2981a7a221269cecef40c1c1b4af1d64`,
arbre `a78aee49d87332b951219645a5194f16c70df9b4`. Ses 2 102 tests core passent
sur chaque Debian. Une course entre le polling du cockpit et sa capture visuelle
a ensuite été corrigée dans le test uniquement : arrêt du polling après toutes
les assertions fonctionnelles, puis attente des réponses en vol. Le candidat
`2ec53187ecbac81359955f918550070f1c34ddf4`, arbre
`a44c066ccbe5437c922339d0a90096fde3e4b99c`, a passé toute la Quality
`38049898381` et le protocole/systemd `38049898408` : 2 102 tests core par
Debian, 37 bridge, 49 HTTPS natif, 87 contrats ciblés et 10 tests systemd.
Le gate a vérifié et publié le paquet source exact, 545 fichiers. Les échecs
visuels intermédiaires sont conservés dans `38049061207` et `38049590456`.

Le run composé du candidat précédent `123db0e`, `38047575225`, est terminé
avec deux échecs distincts. L'upgrade a observé les cinq SIGKILL, terminé
l'ouverture publique, vérifié les accès et politiques Web/Mobile, puis conservé
les sources, clés, certificats et UUID. Ses six fenêtres SQL mesurées sont
comprises entre 76,90 et 149,64 secondes. Il échoue ensuite dans le banc :
`patch.object` recevait une méthode au lieu de la classe et du nom d'attribut.
Le contrôle final en lecture seule et le boot ne sont donc pas qualifiés par
ce run. Le correctif du banc fournit la classe `SharedPublic` et `control` ;
l'interdiction de démarrer un service pendant le check est maintenue.
Le rollback de ce même candidat a refusé l'admission externe sur
`SQL_FENCE_TIMEOUT`. Il ne contient pas encore l'optimisation du compilateur.

L'environnement local s'est déconnecté pendant cette reprise. Les écritures
suivantes sont publiées directement sur les seules branches techniques GitHub.
Le banc Web inspecte les archives par SHA-256, vérifie les manifestes présents
contre les octets et modes du commit annoncé, puis restitue les diagnostics
bornés. Les deux archives de `38047575225` et leurs trois manifestes chacune
sont ainsi authentifiées. L'absence du quatrième manifeste de boot est explicite.
Aucun PASS complet public/boot n'est encore acquis. Les recettes des candidats
optimisés antérieurs restent utiles pour le diagnostic, mais contiennent encore
la faute du banc corrigée ici et ne peuvent pas qualifier ce dernier gel.


Le correctif de banc `28fdb19288a36625053fd92f944359a80fa36493`, arbre
`a882c953c044a8f0c32a64c2a94478671913be27`, a passé toute la Quality
`38050830320` et le protocole/systemd `38050830430`.
Un hôte du run antérieur `38050581370` a refusé plus tôt le handoff du
frontal partagé, dans l'observation de la ligne de commande NGINX pendant
son démarrage (`SharedPublic.listener`, `SOURCE_DRIFT`). Son archive
`11669138079` et son manifeste sont authentifiés ; les frames sont relevées
par le run de diagnostic séparé `38051328363`.

L'attente de démarrage est donc rendue bornée face à une observation
`SOURCE_DRIFT` transitoire : elle réobserve pendant au plus les dix secondes
déjà prévues, sans répéter la commande start. Le lecteur strict reste inchangé
(exécutable, arguments exacts, socket détenu et observation systemd stable).
Une dérive persistante et les autres erreurs restent bloquantes. Deux contrats
supplémentaires imposent ces comportements. La qualification native du nouveau
code reste à obtenir ; les autres campagnes ne peuvent pas le qualifier.
