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

- [x] Cockpit `2f7a33bca0325b25880e7f90960774c14b92b667` : trois CI PASS,
  3971 exécutions, neuf jobs, huit artefacts et 475 fichiers exacts vérifiés.
- [ ] Recette composée avec Web/Gateway réels et ACME privé.
- [ ] Interruptions natives et nouveau PID 1 avec les gardes composés.
- [ ] Boot Mobile et restauration originale.
- [ ] DEV distinct, origine/sonde Web configurables et FCM.
- [ ] Recette globale 6C et preuves terrain distinctes.

Les tests SQL/comptes/services/APT restent exclusivement en CI jetable.
Les campagnes historiques restent conservées, sans relance automatique.

## Candidat boot Mobile

Un plan et une confirmation distincts ajoutent un unique service privé de boot,
après le boot Web et avant le frontal HTTPS. L'enrôlement ne démarre aucun
service. Foundation et Gateway conservent leurs fragments statiques d'origine.
Le bundle privé inclut explicitement le modèle Apache Foundation ; le contrat
des bundles Web/public historiques reste inchangé.

Le worker contrôle les reçus complets, les parents, la configuration, le mode
SERVING et les processus possédés. Un journal privé sous `/run` lie chaque
tentative au boot du noyau, au démarrage de PID 1, au profil et au service fixe.
Une réponse perdue peut être réconciliée par observation. Un service arrêté
après une intention antérieure, un processus remplacé ou une dérive ne provoque
aucun nouveau démarrage automatique. La maintenance bloque les deux démarrages
et est contrôlée de nouveau entre Foundation et Gateway.

Les routes `/api/mobile/boot/{plan,apply,resume,retry,check}` suivent les gardes
HTTPS et les verrous existants. GET/rapport restent historiques. Le contrôle
explicite atteste la configuration actuelle, sans prouver un reboot effectif.
La recette composée est étendue au reboot réel et au bootstrap signé après
reboot ; cette qualification reste à obtenir sur ce candidat.
