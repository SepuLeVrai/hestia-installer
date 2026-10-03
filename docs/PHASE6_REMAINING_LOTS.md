# Phase 6 - lots de finition

Reprise du 3 octobre 2026 après le candidat FCM `f52c504`.
Chaque lot se termine avec son code, ses preuves et son ZIP exact.
La phase reste ouverte jusqu'à la qualification de tous les périmètres.

| Lot | Résultat attendu | État de reprise |
| --- | --- | --- |
| FCM | Import privé, cockpit, service et boot qualifiés sur Web/Gateway réels | En cours : scénario navigateur et acquisition du paquet de recette |
| DEV distinct | Identités MAIN/DEV séparées, profils, refus croisés et cockpit | À faire |
| Upgrade et restauration | Restauration originale, upgrade/rollback Gateway et reprise sans perte d'identité | À faire |
| Recette 6C et intégration | Parcours composés, limites, intégration des commits qualifiés et package exact | À faire |

## Acquis à conserver

- Frontal commun, cockpit, boot Mobile et ACME privé : Installer `f8c004c`,
  recette Web `37119330868` PASS.
- Web Mobile v2, maintenance, QR privé, sonde 9083 et nouveau PID 1 :
  Installer `261e053`, trois CI PASS et recette Web `37141785730` PASS.
- Gateway FCM `3392782` : Quality `37142673219` PASS, paquet qualifié
  distinct de son raccordement Installer.

## FCM : correction des deux blocages observés

Le candidat initial passe cœur, système et paquets. Le nouveau test navigateur
attendait le sélecteur avant de rouvrir le formulaire après refresh. Le choix
d'activer le formulaire est volontairement éphémère. Le test contrôle maintenant
ce choix explicite, la conservation du brouillon, le profil v2 et le commit du
plan. Aucune assertion antérieure n'est retirée.

La recette native `37145740081` s'arrête au checkout privé Gateway, avant
installation. Son runner n'a pas accès à cet autre dépôt privé. La correction
utilise le paquet déjà qualifié, conservé dans le dépôt privé de recette, avec
contrôle taille/SHA-256 avant utilisation. Aucun token interdépôts ni credential
Firebase réel n'est ajouté.

Le nouveau verdict doit porter sur le commit corrigé exact et sa recette.
L'autorisation Google et la réception téléphone restent des preuves externes
distinctes. Le compte synthétique ne permet pas de les déclarer PASS.

Une seule campagne utile par gel ; les preuves historiques sont conservées.
Voir [le contrat FCM](FCM_PRIVATE_IMPORT.md) et
[les acquis de composition](PHASE6_COMPLETION.md).
