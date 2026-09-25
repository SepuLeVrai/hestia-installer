# Réparation explicite des DEFINER historiques

## Candidat privé en qualification

`DefinerRepair.repair` répare uniquement le défaut connu d'une installation
managed 5B2.3 scellée, sur le pin Web exact. Il exige une confirmation distincte,
le consentement au verrou global SQL et une lease de maintenance vivante liée
au nonce de `seal.json`, au groupe Web et à son répertoire privé `maintenance`.
Le contrat [maintenance](PHASE5_MAINTENANCE.md) doit être effectivement installé
sur tous les producteurs par l'orchestrateur. Le raccordement système 5D reste
à livrer ; cette API n'est exposée ni au navigateur ni à un serveur arbitraire.

## Frontières exécutées

1. Relire les sources, reçus, identité d'instance et configuration protégée.
2. Créer une [sauvegarde de secours](PHASE5C2_RESCUE.md) avec restauration réelle,
   pour l'identité orpheline explicitement désignée. Aucun backup opérationnel
   préalable impossible n'est demandé.
3. Réserver et fsync `definer-repair-<cible>.attempt` en 0600 avant dispatch SQL.
   Une réservation partielle interdit aussi le rejeu.
4. Dans le worker PHP privé non privilégié, auditer l'autorité et le compte DML,
   refuser toute autre session SQL applicative encore active, prendre le verrou
   de provisioning et relire la totalité du snapshot. La comparaison avec la
   sauvegarde porte sur les DDL, lignes et cinq définitions de triggers.
5. Refuser toute identité durable préexistante, créer l'identité verrouillée et
   ses droits minimaux, puis réattribuer les cinq corps canoniques sans autre
   changement. Comparer à nouveau données et DDL en normalisant seulement cette
   identité attendue. L'ancien compte temporaire reste absent.
6. Faire une nouvelle sauvegarde normale et une restauration isolée qui exécute
   les cinq effets de triggers. Vérifier que ses données correspondent au résultat
   SQL et que tous les fichiers, secrets, modes et reçus capturés sont inchangés.
7. Écrire le reçu `.done` seulement après toutes ces preuves. La maintenance
   reste active ; la réparation ne rouvre jamais implicitement le Web.

Le smoke métier est effectué dans le clone. Une transaction annulée sur la
source pourrait avancer AUTO_INCREMENT ; la réparation évite donc tout INSERT
de test sur la source. La recette jetable exerce ensuite réellement les cinq
effets avec son compte DML, en distinguant cette preuve du comportement produit.

## Incidents et limites

Une interruption, réponse perdue ou incohérence après réservation rend
REPAIR_MANUAL_ACTION avec maintenance requise et retry automatique interdit.
Les marqueurs, archives et éventuels changements partiels restent disponibles
pour inspection. Le compte de migration n'est jamais recréé. Une réparation
ne modifie ni le mot de passe utilisateur, ni Admin, RBAC, sessions, clé Assistant,
CA, pointeur, lock ou sceau d'activation.

Ce sous-lot ne fournit pas encore une reprise de DDL partiel ou un rollback de
l'upgrade. Ces opérations restent à qualifier dans 5C4. L'inventaire des fichiers
métier modifiables/sessions externes et les services restent ouverts. Le succès
DEFINER_REPAIR_VERIFIED n'est pas une installation Web système terminée.

## Neuf recettes nouvelles

Réparation et données/secrets/session HTTP conservés, cinq effets réellement
exécutés, maintenance/consentements obligatoires, sauvegarde de secours invalide,
dérive entre sauvegarde et DDL, collision de compte, DDL partiel réel, réponse
perdue après DDL, restauration finale échouée et session SQL non drainée.
Les 30 backup, 7 maintenance et 54 SQL/TLS/HTTP historiques restent requis, ainsi
que la Quality permanente complète. Résultats du candidat encore à confirmer.
