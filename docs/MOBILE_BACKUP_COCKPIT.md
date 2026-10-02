# Sauvegarde mobile depuis le cockpit — 6B7b11

Base exacte : Installer 6B7b10a `546a53ced5e443fd02c7bd039f98ef9dbe153c05`.
Ses trois CI sont PASS ; sa recette native 37016308782 est encore en cours
au début de ce lot. Le parent natif qualifié reste 6B7b9, run 36910866139.
Les verdicts exacts du nouveau candidat sont consignés dans le checkpoint.

## Parcours livré

Après les cinq parents Web, activation, Gateway, Foundation et Gateway MAIN,
le cockpit propose un plan séparé de sauvegarde. Le POST plan ne touche aucun
service. Apply nécessite confirmation, trois identifiants SQL valides et accord
sur le verrou global de lecture SQL limité à 180 secondes. Le dialogue indique
l'arrêt des services et le maintien de la maintenance après sauvegarde.

Le profil, les chemins, la source Web et les services viennent exclusivement du
serveur. Les secrets ne sont ni persistés ni placés dans le rapport ; annuler,
terminer la requête ou quitter la page vide les valeurs du navigateur.

La composition native ProvisionedBackup reste inchangée : SQL et enveloppe Web,
données enregistrées, SQLite Gateway et cache sont sauvegardés et leur restauration
est vérifiée sur des copies isolées. Aucun redémarrage n'est déclenché.

## Reprise bornée

L'approbation précède la création du vrai verrou de maintenance. Son identifiant
est enregistré sous verrou du journal parent avant l'appel natif. Le descripteur
est fermé sans rouvrir l'activité ; ProvisionedBackup récupère ensuite ce même
verrou par sa méthode native existante. Aucun objet de bail n'est sérialisé.

Une interruption avant l'enregistrement de l'identifiant, avec maintenance déjà
fermée, impose une inspection manuelle : le cockpit n'adopte jamais un verrou
par déduction. Un verrou différent ou une activité déjà rouverte refuse la reprise.
Un répertoire de sauvegarde préexistant non vide refuse un nouveau départ.
Les refus natifs de copie partielle restent inchangés : cette API ne promet pas
la récupération de toute interruption possible et n'offre pas de retry ciblé.

Les reçus originaux complets, liés au même verrou et aux manifestes, constituent
un historique. Ils permettent de reconnaître une sauvegarde terminée malgré
une réponse HTTPS perdue, sans refaire un export. GET et rapport ne lancent
aucune observation SQL/systemd et ne délivrent pas d'autorisation courante.

## Vérification et suite

Quinze contrats de façade couvrent les consentements, le lien au verrou réel,
les interruptions avant/après ce lien, la perte de réponse, la dérive des parents,
les reçus altérés, les secrets et l'exclusion mutuelle. Deux parcours navigateur,
aussi hérités par la campagne navigateur native, complètent ces contrats.
Le scénario natif existant conserve son SIGKILL après le premier verrouillage
Gateway et toutes les assertions de sauvegarde ; il utilise maintenant le
cockpit HTTPS pour préparer/annuler puis reprendre. Ses deux sorties de navigateur
recréent le TransactionService, conformément au correctif 6B7b10a.

L'activation 6B7b10 conserve son propre consentement. Le raccordement au cockpit
des étapes intermédiaires de préparation de reprise reste à réaliser avant un
parcours de maintenance autonome de bout en bout. Accès Mobile public, TLS,
persistance au démarrage et phase 6 globale restent ouverts. Aucune promotion
des branches actives n'est effectuée.
