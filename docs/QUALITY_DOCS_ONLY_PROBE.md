# Vérification technique du filtrage documentaire

Sonde temporaire sur la branche de contrôle, non destinée à main ni au ZIP livré.

Le commit précédent `7da00daa0069e8af8dadf89dad91037b3c00514a` a réussi la campagne
complète du workflow Installer Quality. Ce changement touche uniquement la présente
documentation. Le résultat attendu est : contrôles statiques et tests du gate
réussis, matrice applicative et navigateur non demandés, aucun package publié.

La sonde vérifie le chemin Actions Read vers l'historique réel, en plus des tests
unitaires qui refusent un résultat précédent absent, échoué, annulé ou en cours.
Elle ne constitue pas une nouvelle validation applicative. Ne pas fusionner cette
branche de diagnostic dans main ; le commit de livraison est celui indiqué plus haut.
