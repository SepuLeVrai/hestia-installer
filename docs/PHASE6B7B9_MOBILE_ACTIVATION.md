# Phase 6B7b9 : activation MAIN explicite après les barrières mobiles

## Portée du candidat

Cette suite part de 6B7b8a (`ed789c74f0e84d6864dee67c262c83007659c7d8`), correction du profil proxy JSON. Les anciens contrats restent obligatoires. Aucun démarrage local ni modification des branches actives. Le lot ajoute le coordinateur interne `mobile_activation_admission.execute`, sans annoncer la phase 6 terminée.

Le parent 6B7b8 avait échoué en recette native sur la comparaison tuple/liste du profil proxy, avant le premier effet du transfert de barrières. Ses trois CI vertes ne qualifiaient donc pas sa recette native. La correction 6B7b8a dispose de trois CI vertes (3 495 exécutions). Sa nouvelle recette native est distincte : `36903341716`, commit Web `81d9223590fe8c9f71c508f51f42c07e741a7ae6`. Ne pas assimiler un lancement à un résultat validé.

## Transition et limites d'autorité

Le coordinateur exige la confirmation SHA-256 du plan de reprise, le consentement explicite et l'autorisation du verrou SQL global. Avant la levée de maintenance, il vérifie les archives et fichiers vivants, les profils natifs, les journaux parents, les verrous de configuration et les ordonnanceurs. Un export SQL neuf est comparé sous la barrière native existante, plafonnée à 180 secondes.

Il crée un journal privé distinct, lié aux identités de répertoire, au plan original, au profil runtime et au système courant (hôte, démarrage, espaces de noms). L'intention d'activation est durable avant la suppression de `mobile-activation.attempt`. Le reçu natif `resumed-<lease>.json` précède la suppression de `maintenance.attempt`, dernière barrière persistante.

Le véritable verrou exclusif `activity.lock` reste détenu au-delà de cette suppression. Les requêtes PHP et les producteurs coopératifs restent donc refusés pendant le démarrage. Le verrou SQL est libéré normalement avant la première commande de service. L'ordre lié au plan est PHP, Apache, Foundation, Gateway, puis timer de nettoyage. Les unités et leurs conditions de maintenance ne sont pas réécrites.

Chaque rôle a une intention privée préalable et un reçu lié à son `InvocationID` systemd et à son instant d'activation monotone. Un rôle avec intention existante ne reçoit jamais une seconde commande de démarrage. Une invocation active, possédée et postérieure à l'intention peut être reconnue après perte de réponse. Une tentative arrêtée, ambiguë, une invocation remplacée ou un changement de démarrage système sont refusés. Une reprise explicite peut poursuivre les rôles dont aucune intention n'a été créée.

Une coupure avant la suppression de maintenance peut rétablir uniquement le marqueur d'activation exact sous son intention propriétaire et son bail natif original, puis exige une nouvelle admission SQL. Un reçu natif partiel n'est accepté que comme préfixe exact de ce même reçu. Après la levée de maintenance, la reprise utilise le reçu natif et le vrai verrou d'activité ; elle ne rejoue ni drain, ni export SQL historique, ni fermeture de maintenance. Le reçu de reprise est conservé et ne rend jamais un retour arrière SQL admissible.

Le contrôle d'une activation terminée est en lecture seule et vérifie les cinq invocations. La sonde HTTP locale s'exécute seulement après la libération du verrou d'activité. Un échec de sonde ne provoque ni arrêt ni redémarrage automatique.

## Vérification

Les tests purs couvrent l'ordre, l'intention préalable, les pertes de réponse aux premier et dernier démarrages, l'absence d'effet sans répétition, les invocations remplacées ou trop anciennes, les journaux étrangers, la perte de verrou et le contrôle sans écriture. Le lecteur de drain original conserve son refus de Foundation/Gateway ; seul le coordinateur d'activation utilise le scanner procfs commun après liaison du profil natif complet.

Les tests fichiers sont réservés à la CI Ext4 jetable. Ils vérifient les permissions, les originaux, le verrou réellement conservé, l'ordre des suppressions, les interruptions avant/après maintenance, le reçu natif partiel, les états étrangers et l'exclusion d'une seconde activation. Ils isolent les lecteurs SQL/systemd et ne constituent pas une qualification native.

La recette native complémentaire ajoute un écart SQL refusé avant le plan, un SIGKILL après le véritable démarrage PHP, la reprise explicite sans seconde commande PHP, les quatre démarrages suivants, l'identité native de Foundation/Gateway, le contrôle sans écriture et la page de connexion locale. Elle exporte `mobile-activation-native.json`, les temps SQL et les commandes réellement observées. Son PASS reste requis séparément des CI Installer.

## Suite

La persistance au redémarrage, la restauration vers l'original après activité, DEV/FCM, 6C et les phases suivantes restent ouverts. Le résultat conserve `boot_persistence=false`, `public_tls_verified=false` et `phase6_complete=false`. Aucun APK n'est livré. Les issues Installer#1, Gateway#4 et Web#135 conservent le suivi et les identités exactes des preuves.
