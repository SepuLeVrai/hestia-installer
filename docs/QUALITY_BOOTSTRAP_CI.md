# Verification technique de la base Phase 4

Cette branche technique verifie le commit publie `2d86b36da536f118ae5dbb79ddbba663644cf18f` avant la promotion du nouveau dispositif Quality sur main.

Le workflow temporaire rejoue les scripts historiques dans un conteneur Debian 13 jetable. Les credentials GitHub du checkout ne persistent pas. Aucun secret applicatif, aucun autre depot et aucune compilation Android ne sont utilises. Les reponses GitHub applicatives restent les fixtures du banc historique.

Les artefacts prives de cette verification contiennent le snapshot Git exact de la base, ses empreintes, les versions des outils et les logs du test. Ils permettent la verification locale et la conservation des preuves. Le snapshot ne contient ni le dossier .git, ni un environnement deploye, ni les secrets crees pendant les tests.

Ce premier workflow n'est pas la CI finale : les tests natifs navigateur, la matrice Debian 12/13, le filtrage documentaire et les controles de livraison sont prepares dans la suite du lot. Aucun resultat PASS n'est presume avant lecture du run.
