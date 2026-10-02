# Préparation de reprise mobile — 6B7b12

Base Installer : 6B7b11 `d7d7de9c42768eda53a5d6790f38cdc59ea362ed`, trois CI
PASS et 3 659 exécutions. Les recettes natives 6B7b10a (37016308782) et 6B7b11
(37022581175) étaient encore en cours à l'ouverture. Les verdicts exacts figurent
dans le checkpoint ; aucune qualification native du nouveau lot n'est anticipée.

## Parcours

Le cockpit relie maintenant les trois cartes : sauvegarde MAIN, préparation de
reprise et activation. La préparation n'apparaît qu'après une sauvegarde complète
reconnue par ses reçus. Un plan distinct lie les cinq parents, la configuration,
le profil de sauvegarde, son reçu et l'identifiant exact du verrou de maintenance.
Plan et GET n'exécutent aucune observation système ou SQL.

L'utilisateur fournit les trois identifiants SQL temporaires et confirme le
consentement aux admissions successives. Chaque verrou SQL reste borné à 180 s ;
les fenêtres ne sont jamais prolongées ni réutilisées. L'approbation est durable
avant la première étape. La préparation reste séparée de l'activation : aucun
service n'est démarré et la maintenance demeure fermée après les six étapes.

| Étape | Composition native | Effet autorisé |
| --- | --- | --- |
| Gateway | GatewayState release/recover | Libération des inodes Gateway sauvegardés |
| Fichiers | ReopenFilesPlan | Libération récupérable données/configuration/Web |
| Externe | ExternalAdmissionWindow | Retrait des réservations sous nouvel export SQL |
| Données | DataAdmissionWindow | Réouverture des accès sous nouvel export SQL |
| Plan | ResumePlan sous DataAdmissionWindow fraîche | Copies privées et ordre des futurs démarrages |
| Passage à l'activation | BlockerWindow | Remplacement des anciens bloqueurs sous nouvel export SQL |

Les lecteurs, StepSpecs, primitives et admissions qualifiés restent inchangés.
Le nouveau composite les appelle avec leurs véritables objets natifs. Après une
interruption de chmod, il laisse DataAdmissionWindow refermer explicitement le
cas partiel avant de rattacher les runtimes. Aucun bail ou fence n'est simulé.

## Reprise et historique

Chaque étape a une intention avant effet puis un reçu lié au précédent. Une
reprise saute uniquement les étapes dont le reçu de cockpit est complet ; les
primitives de l'étape suivante revalident leurs parents et leur état courant.
Si la réponse d'une étape est perdue, la primitive native récupère ou vérifie son
état exact avant un nouveau reçu. Un reçu étranger, réordonné, tronqué ou manquant
au milieu de la chaîne ferme le parcours. Il n'existe pas de retry ciblé HTTP.

Ces reçus choisissent l'étape à examiner ; ils ne constituent jamais une
admission actuelle. À la fin, la sélection existante de l'activation doit retrouver
le même verrou, les copies de reprise complètes et le transfert des bloqueurs.
L'activation refera sa propre admission SQL. Une préparation déjà terminée ne
relance aucune étape après perte de réponse ou rechargement.

Les champs sont vidés à l'ouverture du dialogue, à l'annulation et après la
requête. Le polling conserve le champ actif. Les secrets ne sont pas persistés.

## Vérification

Dix-sept contrats de façade et cinq contrats de sélection native sont ajoutés,
ainsi que trois parcours dans chacune des suites navigateur. La baseline ajoute
aussi les tests 6B7b11 précédemment découverts ; aucun ancien identifiant n'est
retiré. Les tests de façade et de navigateur isolent les effets système.

Un 37e test natif utilise une nouvelle instance, le vrai cockpit HTTPS, SQL,
systemd et Ext4 : sauvegarde, annulation, SIGKILL après libération des fichiers
avant reçu cockpit, dérive SQL refusée avant libération externe, reprise,
maintenance conservée, puis activation séparée et page de connexion vérifiée.
Les 36 tests et leurs assertions antérieures restent exécutés. Les preuves
spécifiques sont `mobile-preparation-native.json`, `mobile-preparation-cockpit.png`
et, en cas d'échec, `mobile-preparation-error.json` sans secrets.

Ce raccordement rend le parcours local complet dans le code. Sa qualification
native dépend du succès du parent et de la nouvelle recette. Mobile public,
TLS public, boot, DEV/FCM et APK ne sont pas déclarés livrés ; phase 6 ouverte.
Aucune promotion main/dev/dev-Bastien.
