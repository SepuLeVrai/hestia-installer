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
