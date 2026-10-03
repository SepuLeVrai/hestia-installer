# Phase 6 - lots de finition

Reprise du 4 octobre 2026 après le lot FCM qualifié `b9a3997`.
Chaque lot se termine avec son code, ses preuves et son ZIP exact.
La phase reste ouverte jusqu'à la qualification de tous les périmètres.

| Lot | Résultat attendu | État de reprise |
| --- | --- | --- |
| FCM | Import privé, cockpit, service et boot qualifiés sur Web/Gateway réels | Terminé, #15 fermé et ZIP livré |
| DEV distinct | Identités MAIN/DEV séparées, profils, refus croisés et cockpit | Implémenté, qualification du candidat en cours, #16 |
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

Historique clos : final `b9a3997`, CI `37154464899`, `37154464814`,
`37154464801` PASS ; recette `37153956770` PASS. Les paragraphes ci-dessous
conservent les incidents résolus. Voir le [contrat DEV courant](DEV_CONTEXTS.md).

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

Le premier correctif `6af8b12` a passé les 32 tests bridge. Le test natif FCM
enchaînait trop tôt après la fermeture du dialogue : l'état attendu restait
inchangé pendant le refus asynchrone, et le formulaire pouvait être recréé après
la sélection du fichier suivant. Le scénario attend désormais la réponse 409
et son message avant de poursuivre. Les fichiers vides et supérieurs à 16 Kio
doivent être refusés sans dialogue ni requête d'import supplémentaire.

Une tentative système Debian 13 a refusé une population systemd modifiée pendant
la lecture (`DISCOVERY_CHANGED_DURING_READ`). Sa preuve est conservée ; seul ce
job est relancé sur le même commit, sans changer le garde ni ses assertions.

Voir [le contrat FCM](FCM_PRIVATE_IMPORT.md) et
[les acquis de composition](PHASE6_COMPLETION.md).
