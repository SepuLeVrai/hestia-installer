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
  `38044621429` : suites Debian 12/13 et navigateur réussies ; gate final
  à consulter avant toute annonce de PASS global.
- Recette composée `38044621464` : échec avant essais, le jeton Installer ne
  peut pas lire le dépôt Web privé. Aucun PASS natif public issu de ce run.
  La recette réutilisable est désormais appelée depuis une branche technique
  Web privée, avec un SHA Installer exact. Aucun paquet privé n'est copié dans
  le dépôt public Installer et aucun nouveau credential interdépôts n'est créé.
- Localement : 13 contrats d'ouverture, 5 de profil/progression publics et
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
