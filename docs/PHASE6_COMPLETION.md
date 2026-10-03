# Phase 6 - chantier de clôture du 3 octobre 2026

Base qualifiée : 6B11 `dc0dca3ae0d33baafa136f002b2fdeff7f6095a8`.
Le chantier regroupe la composition Web/Gateway/ACME, le cockpit public,
le boot Mobile, la restauration originale, DEV/FCM et la recette finale 6C.
La phase reste ouverte jusqu'aux preuves de qualification de chaque périmètre.

## Cockpit du frontal commun

La préparation `SharedPublicPlan` et l'exécution `SharedPublicLifecycle` sont
exposées par des routes fixes distinctes, avec session HTTPS, Origin, CSRF,
verrou de mutation et verrou du journal parent. Les réseaux IPv4 Mobile sont
choisis indépendamment du Web. La sélection publique exige le choix explicite
de `0.0.0.0/0`. Le pré-plan seul n'autorise aucun effet système.

Le plan d'exécution conserve sa confirmation au SHA exact. Le dialogue décrit
les deux brèves bascules et la suspension du timer jusqu'à la fin du cycle.
Une fermeture, une annulation ou un rafraîchissement ne lance aucune opération.
La reprise ciblée utilise le moteur existant ; un effet incomplet reste manuel.
Les anciennes commandes du frontal Web ne sont plus proposées après approbation
du transfert. Les parents et leurs journaux restent conservés.

Les lectures sont historiques. Le contrôle explicite revalide les étapes et
retourne un résultat horodaté en mémoire, perdu au redémarrage du cockpit.
Il ne constitue pas un test de connexion administrateur, de push téléphone
ou une preuve de disponibilité publique depuis Internet.

## Qualification à obtenir

- [ ] Contrats HTTPS et parcours navigateur sur le nouveau gel.
- [ ] Recette composée avec Web/Gateway réels et ACME privé.
- [ ] Interruptions natives et nouveau PID 1 avec les gardes composés.
- [ ] Boot Mobile et restauration originale.
- [ ] DEV distinct, origine/sonde Web configurables et FCM.
- [ ] Recette globale 6C et preuves terrain distinctes.

Les tests SQL/comptes/services/APT restent exclusivement en CI jetable.
Les campagnes historiques restent conservées, sans relance automatique.
