# Phase 5D1 - Contrôleurs applicatifs dans le journal

Base acquise : `042e844061d26dca875ba18382b54dcf688c39de`, arbre
`67f361d6d8794f383f5e5b8359536d4efa5ffc1c`. La qualification 5C4 reste acquise.
Ce lot raccorde les contrôleurs SQL/finalisation et la transition managed au
moteur transactionnel existant. Il ne raccorde pas encore les formulaires du
wizard public et ne livre pas l'activation des services. Phase 5 reste ouverte.

## Frontières exécutables

| Adaptateur | Effet sous checkpoint | Reprise reconnue | Retour arrière |
| --- | --- | --- | --- |
| `DatabasePreparationOperation` | SQL fresh et configuration privée | Reçu complet, fichiers protégés et vérification réelle SQL/Admin | Aucun DROP ni restauration automatique |
| `FinalizationOperation` | Assistant, pointeur, verrou, sceau et reçu final | Observation active du contrôleur et cohérence avec le choix Assistant | Activation incomplète à examiner manuellement |
| `StorageUpgradeOperation` | Sauvegarde vérifiée, déplacement et bascule sous maintenance | Reçu natif ou reprise explicite de la lease enregistrée | Compensation avant intention de réouverture, sans restauration SQL |
| `StorageResumeOperation` | Autorisation de réouverture après contrôles | Reçu durable ou continuation idempotente de l'autorisation interrompue | Aucune annulation après cette frontière |

Les adaptateurs précédents de copie du Web, identité, paquets, runtime et
collecteur restent inchangés. Le registre est construit par du code Python de
confiance. Aucun chemin, contrôleur, fonction, compte système ou commande libre
n'est ajouté à l'API publique. Les anciens plans/journaux gardent leur schéma et
leurs digests. `DONE` signifie ici que les opérations présentes dans ce plan
sont terminées, pas que HESTIA est accessible ni que la phase 5 est terminée.

## Configuration et secrets

`WebInputs.capture(payload, vault)` valide le contrat 5A, garde uniquement sa
configuration normalisée immuable et transfère les credentials au `SecretVault`.
Les valeurs SQL d'autorité/migration sont également fournies uniquement dans
ce magasin, sous les noms déclarés par chaque étape. Les mots de passe et clés,
ainsi que leurs empreintes, sont absents des plans, reçus et journaux.

La configuration non secrète est liée au plan par son SHA-256. La future
composition wizard doit conserver et présenter ses champs validés, reconstruire
le même registre depuis ces champs et demander les credentials nécessaires après
redémarrage. Ce lot n'ajoute pas ce stockage au brouillon historique. Une
configuration différente ne peut pas remplacer silencieusement celle approuvée.

Une liaison privée `operator-<empreinte>.json`, mode 0600 dans le state-root
protégé du runtime, lie installation, étape, digest du contrat et configuration.
Elle précède toute mutation native. Fichiers liés, tronqués, changés ou aux droits
incorrects bloquent l'opération. Les marqueurs et reçus natifs restent requis.
Une liaison seule ne constitue jamais une preuve de succès.

Les étapes `DONE` ne redemandent pas leurs credentials. Une réponse perdue du
SQL fresh exige les secrets applicatif et Admin nécessaires à sa vérification,
sans recréer l'Admin. La reconnaissance de finalisation achevée ne nécessite que
le mot de passe applicatif. Les observations durables d'upgrade/réouverture
n'ont pas besoin d'autorité SQL. Une continuation mutante d'upgrade ou de rollback
nécessite de nouveau les credentials applicatif et d'autorité.

## Reprise, lecture seule et compensation

`Operation.recover()` ne peut appeler ni `StorageUpgrade.recover()` ni
`authorize_resume()`. Il ne fait que lire/reconnaître les preuves. Un résultat
`RETRY_SAFE` autorise le moteur à écrire son nouveau checkpoint apply/rollback ;
seul le callback mutateur qui suit appelle la reprise native. `APPLIED` reconstruit
le reçu puis passe à la validation, sans réappliquer l'effet reconnu.

Le backup-root est dédié à cette opération et vide avant le premier apply.
Après une interruption, il doit contenir une seule opération native liée à
l'instance, aux deux commits épinglés et aux chemins attendus. Une liaison absente,
plusieurs slots, une preuve manquante ou contradictoire restent bloquants. Un arrêt
après liaison privée mais avant preuve native suffisante peut exiger un examen
manuel ; il ne déclenche pas un nouvel apply deviné.

La validation d'upgrade relit aussi le code/configuration du runtime cible,
la maintenance et les services/timer arrêtés. Le reçu natif historique ne suffit
donc pas à valider un runtime courant altéré. Il ne certifie toujours pas la
disponibilité de l'application ni l'absence de toute activité SQL étrangère.
La reprise native mutante garde ses contrôles SQL et ses barrières complets.

Une intention de rollback interdit de reprendre en avant. Une intention de
réouverture interdit de revenir en arrière, même si un ancien reçu applied existe
encore. Les dépendances du journal bloquent également l'annulation de la transition
après activation de l'étape de réouverture. Le rollback natif conserve code cible,
uploads déplacés et sauvegardes ; aucun nettoyage ni SQL destructif n'est ajouté.

Le refresh et le rapport `TransactionService` lisent le journal sans mutation.
Une réponse perdue ne provoque pas de retry automatique. Après réouverture, le reçu
est un historique d'autorisation, pas un test de disponibilité live. Le démarrage
et la vérification des services sont la frontière suivante.

## Qualification ciblée et périmètre système

`tests/test_application_operations.py` vérifie le moteur réel avec fichiers de
liaison/journal réels et frontières de contrôleurs substituées. Il couvre la
confirmation, les pertes de réponses, les secrets, les digests, les droits/liens,
les plans anciens inchangés et la séparation reconnaissance/mutation.

`tests/integration/application_journal_systemd.py` est réservé au banc jetable.
Douze scénarios utilisent les vrais contrôleurs, PHP/MariaDB et les deux builds
Web épinglés, avec SIGKILL puis reconstruction de l'orchestrateur. Six scénarios
fresh couvrent existing_local/managed, Assistant, Admin préservé, configuration
incomplète et sceau sans reçu. Six scénarios upgrade couvrent la bascule, la
réouverture, les réponses perdues, la reprise après déplacement, le rollback et
la dérive du runtime. Les redémarrages HTTP/TLS de fin de test restent des actions
de fixture et ne sont pas présentés comme du produit livré.

Ce profil applicatif reste Debian 13/PHP 8.4. Les contrôles core Debian 12 ne
qualifient pas implicitement HESTIA sur PHP 8.2. Les campagnes 5C4 acquises ne sont
pas relancées par cette recette ciblée. Les résultats mesurés, commits, arbres,
logs et manifestes des nouvelles campagnes sont conservés dans le checkpoint
de livraison ; ce document ne revendique pas de PASS avant leur exécution.

## Suite 5D

Le lot 5D2 décrit dans [PHASE5D_WIZARD_COMPOSITION.md](PHASE5D_WIZARD_COMPOSITION.md)
compose maintenant le fresh storage depuis le wizard jusqu'aux services préparés
sous maintenance. Le paragraphe suivant reste l'objectif global de la phase ;
l'activation produit et le wizard upgrade ne sont pas clos par ce nouveau lot.

Composer les opérations depuis des choix opérateur fermés, persister le brouillon
non secret, raccorder configuration et credentials au wizard, puis ajouter
l'activation/vérification effective des services et du Web. Qualifier ces nouveaux
parcours depuis l'interface avant toute clôture de #13 ou promotion de phase 5.
Gateway/APK/frontal public/import restent des périmètres distincts. Aucun fichier
Web, `schema.sql`, `install.php`, asset UX ou règle de migration n'est changé ici.
