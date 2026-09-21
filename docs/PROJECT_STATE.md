# État du projet HESTIA Installer

Mise à jour : 21 septembre 2026.

## Référence courante

- branche canonique : `main`
- HEAD : `dd75451f38f3e5d7a8ffe433c0893cf48a127c48`
- version package : `0.1.0.dev0`
- issue parent : #1
- mutations système : aucune active
- GitHub Actions : aucune, volontairement
- Quality locale : PASS, `python3 -m compileall -q installer tests` + 6 tests unittest

## Socle déjà présent

- CLI `python3 -m installer` ;
- `--check` non destructif ;
- détection OS / root / Python / commandes bootstrap ;
- bornes de port temporaire 57000-57999 ;
- validation stricte FQDN ;
- normalisation IPv4 / IPv6 / CIDR ;
- modèle d'états transactionnels ;
- journal d'état non secret avec écriture atomique et permissions 0600 ;
- maquette mini-web locale responsive ;
- support `prefers-reduced-motion` ;
- aucun CDN ;
- aucun endpoint shell arbitraire ;
- aucun secret embarqué.

La maquette UX ne déclenche aucune mutation système et les boutons restent volontairement inactifs à ce jalon.

## Ordre des chantiers ouverts

L'issue #1 reste la source de coordination globale. Ordre recommandé :

1. #2 - bootstrap HTTPS temporaire sécurisé ;
2. #4 - plan/apply transactionnel, resume et rollback ;
3. #3 - mini-web wizard fonctionnel ;
4. Web #135 - moteur Web réutilisable ;
5. Gateway #4 - contrat Gateway pilotable ;
6. #5 - NGINX, DNS, TLS, allowlists et firewall ;
7. APK #5 - provisioning, signature, Firebase et publication ;
8. #6 - import / restauration ;
9. #7 - packaging, manifest, rapport et Quality end-to-end.

La partie visuelle de #3 peut avancer en parallèle, mais son intégration fonctionnelle dépend de #2 et #4.

## Première frontière d'exécution

Le premier lot réellement privilégié reste #2.

Il doit implémenter et tester :

- détection de l'IPv4 d'administration ;
- port aléatoire 57000-57999 avec bind atomique ;
- certificat TLS éphémère avec SAN adapté ;
- token bootstrap à durée courte ;
- authentification bootstrap puis session sécurisée ;
- mini-web HTTPS temporaire ;
- arrêt complet et nettoyage ;
- refus d'exposition Internet automatique si seule une IP publique est disponible.

Aucune mutation NGINX, Apache, MariaDB ou Gateway ne doit précéder la validation de cette frontière.

## Dépendances applicatives

HESTIA Installer orchestre les moteurs existants et ne les recode pas.

- Web #135 : fresh/upgrade, Admin initial, sessions, Foundation, import ;
- Gateway #4 : package, config, systemd, P-256, FCM, fresh/upgrade/rollback ;
- APK #5 : artefact déjà qualifié, signature durable, Firebase, publication privée.

Le chantier transverse Mobile de premier équipement Web #134 / Gateway #3 / APK #4 reste distinct de l'Installer, même si les deux projets devront rester compatibles.

## Sécurité

Invariants :

- jamais de `/run-command` ;
- aucune donnée utilisateur injectée dans un shell arbitraire ;
- secrets absents de Git, URL, process list, logs et rapport final ;
- mini-web temporaire uniquement ;
- CSRF, CSP, no-store et cookies sécurisés ;
- state de reprise sans secret ;
- opérations privilégiées typées et validées ;
- rollback avant exposition de changements partiels ;
- aucun port interne HESTIA ou Gateway publié par facilité.

## Quality

Le dépôt n'active pas encore de GitHub Action. La Quality officielle actuelle reste `scripts/quality-local.sh`.

Le 21 septembre 2026, l'équivalent exact de cette Quality a été rejoué sur les sources du HEAD `dd75451f38f3e5d7a8ffe433c0893cf48a127c48` :

- compileall : PASS ;
- 6 tests unitaires : PASS.

Une CI ne devra être ajoutée que lorsque le premier lot exécutable justifiera une validation distante utile, sans créer de coût inutile.
