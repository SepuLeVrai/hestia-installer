# Activation mobile dans le cockpit

Ce lot 6B7b10 raccorde le moteur 6B7b9 au cockpit HTTPS. Il ne réalise pas la sauvegarde mobile ni sa préparation amont. Le profil pris en charge est MAIN, créé par cet installer, avec les journaux Web, activation, préparation Gateway, Foundation et service Gateway terminés. Les autres profils restent refusés.

## Parcours

1. Après la préparation de reprise, ouvrir le cockpit avec `--resume`. Cette commande n'applique aucune étape.
2. Dans le suivi du chantier, choisir « Préparer l'activation mobile ». Le serveur recherche exactement une préparation dans son répertoire fixe `gateway-backup`. Une absence ou une ambiguïté bloque le plan. Les anciens bloqueurs doivent déjà avoir été remplacés par le bloqueur d'activation.
3. Relire le plan. Pour l'admission SQL finale, saisir le mot de passe SQL applicatif, le compte d'autorité distinct et son mot de passe. Autoriser explicitement le verrou global de lecture, borné à 180 secondes, puis confirmer la boîte de dialogue.
4. Suivre PHP, Apache, Foundation MAIN, Gateway MAIN et le timer de nettoyage. Les indications proviennent des journaux. Le bouton de vérification contrôle séparément la disponibilité locale à cet instant.
5. Après interruption, actualiser puis choisir « Reprendre l'activation mobile ». Si la maintenance reste fermée, les identifiants SQL doivent être ressaisis. Après admission, laisser ces champs vides : la reprise ne fait aucun nouvel export SQL. Une intention de démarrage incertaine n'autorise jamais un second démarrage du même service.

Annuler la boîte de dialogue ne crée aucune approbation. Une fermeture du navigateur n'annule pas une action serveur déjà commencée. Le serveur conserve son verrou de mutation et l'opération bornée continue. Un redémarrage du cockpit efface son observation de disponibilité, sans altérer les journaux.

## Contrat API

| Route POST | Corps autorisé | Effet |
| --- | --- | --- |
| `/api/mobile/activation/plan` | `parents` : SHA des cinq plans parents | Lier une préparation existante, sans admission ni démarrage |
| `/api/mobile/activation/apply` | `confirmation`, `confirm: true`, `credentials`, `allow_global_read_lock: true` | Première admission et activation explicites |
| `/api/mobile/activation/resume` | `confirmation`, `confirm: true`, `credentials`, `allow_global_read_lock` | Reconstituer les contrôles natifs et continuer selon le journal |
| `/api/mobile/activation/check` | `confirmation`, `confirm: true` | Contrôler une activation terminée sans écrire son journal ni recevoir de secret SQL |

Les routes héritent de l'authentification HTTPS, de l'Origin exact, du CSRF et du verrou global de mutation. Aucun `retry`, nom d'unité, commande, chemin, objet de lease ou ciblage partiel n'est accepté. `credentials` est soit vide pour la reprise après admission, soit exactement l'ensemble `database_password`, `authority_user`, `authority_password`. Le compte d'autorité ne peut être `root`.

`GET /api/wizard/state`, `GET /api/installation/report` et `--report` lisent uniquement les fichiers privés. Leur projection est explicitement historique. Aucun identifiant SQL, diagnostic brut, InvocationID ou chemin de sauvegarde n'est exposé dans cette projection. Les secrets restent dans la requête active ; aucun coffre persistant, journal ou stockage navigateur n'est ajouté.

## Garanties et limites

Le plan du cockpit lie l'instance, la configuration, les cinq parents et le SHA de reprise. Une approbation privée précède les effets. Les journaux parents restent immuables. Le moteur natif vérifie de nouveau les sources, les archives, le SQL et les unités ; un fichier de suivi ne remplace jamais cette admission. Le worker conserve la borne qualifiée de 120 secondes et le verrou SQL sa borne de 180 secondes.

Après admission, l'entrée dédiée exige l'état SERVING, le journal natif original et le vrai verrou exclusif d'activité. Elle ne détient aucune autorité SQL et ne recrée pas de maintenance. Les contrôles complets d'appartenance et d'invocation restent ceux de 6B7b9.

Les tests API couvrent consentement, parents modifiés, secrets, reprise, altération des fichiers et concurrence. Les suites Chromium bridge et HTTPS natif couvrent confirmation, annulation, rechargement, reprise sans identifiants et conservation du champ actif pendant le polling. La recette Debian 13 jetable ajoute ce cockpit à l'admission SQL réelle, au SIGKILL après le vrai démarrage PHP et à l'adoption de la même invocation.

Une CI générale verte ne remplace pas cette recette native. La livraison précise son statut réel. Ce lot ne clôt pas la phase 6 et ne qualifie ni accès Mobile public, ni TLS public, ni boot, ni APK.
