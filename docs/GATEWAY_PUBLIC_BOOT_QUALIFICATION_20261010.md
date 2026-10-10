# Premier transfert public/boot - qualification du 10 octobre 2026

## Verdict et périmètre

**QUALIFIÉ dans le périmètre ci-dessous.** Upgrade et rollback ont chacun
réussi la recette composée, les cinq interruptions, la reprise après nouveau
PID 1 et les deux renouvellements ACME réels. Les artefacts téléchargés ont été
authentifiés par SHA-256, puis leurs manifestes comparés au code testé.

Le code produit testé est `24a210d3ffceda40c0adec4d5f505f0b2a3784b6`,
arbre `f503bc740e258e37e78e6f541d8878ebb0cd5f54`, 545 fichiers.
Les changements postérieurs de ce lot sont exclusivement documentaires.
Le manifeste de livraison donne le commit final et prouve cette restriction.

Le périmètre est le premier transfert d'un Gateway MAIN déjà exposé avec
SharedPublic v1 et MobileBoot, sans DEV/FCM actif. L'upgrade catalogue
`e2c09f53593bf316906ccc4387f185e73e7f85a8` vers
`33927821bbda57a2c10791d0523eaf3b254c8c9e` et son rollback binaire utilisent
deux hôtes indépendants. Les deux paquets partagent SQLite 6.

La phase 6 reste ouverte. Cette qualification ne couvre pas les générations
successives sur un même hôte, DEV/FCM, une restauration sur l'origine,
un reboot noyau, le reverse proxy Synology personnel ni la réception FCM
Google/téléphone. Aucune intégration main et aucun déploiement.

## Sources et campagnes

| Contrôle | Référence exacte | Résultat |
| --- | --- | --- |
| Quality produit | [Installer 38051585735](https://github.com/SepuLeVrai/hestia-installer/actions/runs/38051585735) | PASS |
| Core Debian 12 | Python 3.11.2, 2104 tests | 0 erreur, échec ou skip |
| Core Debian 13 | Python 3.13.5, 2104 tests | 0 erreur, échec ou skip |
| Navigateurs | 37 bridge, 49 HTTPS natif | PASS |
| Statique et gate Quality | Syntaxe, permissions, documents, sécurité et 16 contrats du gate | PASS |
| Protocole public | [Installer 38051585721](https://github.com/SepuLeVrai/hestia-installer/actions/runs/38051585721), 87 tests | PASS |
| Sous-système systemd réel | Même run, 10 tests PID 1 | PASS |
| Composition rollback | [Web 38051606873](https://github.com/SepuLeVrai/hestia-nexus-avv/actions/runs/38051606873), tentative 1, job 114211703404 | PASS |
| Composition upgrade | Même run, tentative 2, job 114260505226 | PASS |

L'appelant Web est figé à
`ea61faf1288f61697ea905936e64de03ab72cdfe`, branche technique
`verification/phase6-public-boot-20261010`. Son workflow appelle le workflow
réutilisable Installer au SHA produit exact ci-dessus et matérialise ce même
SHA. Les sources Web privées ne sont pas copiées dans le dépôt public.

La baseline conserve tous ses tests antérieurs et en ajoute 104 par rapport
à `1028c05` : 90 core, 2 bridge, 2 HTTPS et 10 systemd ciblés.
La suite core contient les parcours fresh, upgrade et les refus historiques.
Les 87 tests du protocole ne sont pas présentés comme 87 tests supplémentaires
indépendants de core.

## Recette composée

Chaque hôte Debian 13 démarre vierge, utilise Ext4, un vrai PID 1, MariaDB,
PHP, Apache, NGINX, les vrais paquets Gateway et une CA ACME Pebble jetable.
Quatre rapports sont exigés pour chaque direction : cockpit partagé,
vérification de ses effets, transfert public et reprise après nouveau PID 1.
Leurs manifestes lient les 545 fichiers exacts, permissions comprises ;
`source_stable` doit rester vrai.

Cinq SIGKILL réels interrompent successivement le transfert public,
l'admission et les démarrages HTTP, HTTPS et timer après leur effet natif,
avant l'écriture du reçu. Les reprises doivent concilier l'effet observé.
Elles ne rejouent pas un start ambigu.

Les contrôles exigent les huit fragments attendus réellement chargés,
les accès Web/Mobile, la conservation des clés P-256, de l'UUID SQLite,
des certificats et des parents historiques, ainsi que les restrictions réseau.
Un contrôle terminé doit rester en lecture seule.

Après un nouveau PID 1 dans le même noyau, la recette vérifie les workers
successeurs et deux renouvellements ACME réels. Le master HTTPS reste le même
pendant ces renouvellements et aucun start Mobile n'est rejoué.

| Direction | Interruptions reprises | Fenêtres SQL finales | Maximum observé |
| --- | --- | --- | --- |
| Rollback | 5 | 6 | 109,004 s |
| Upgrade | 5 | 6 | 132,932 s |

La borne produit reste strictement de 180 secondes. Aucun délai SQL ni audit
natif n'a été assoupli pour obtenir ces résultats.

La suite navigateur HTTPS vérifie le cockpit public à sept étapes, notamment
à 480 px, avec son contrat d'interface. Le banc composé exerce les vrais
effets du transfert via le service ; il ne prouve pas que l'ensemble du
transfert public a été cliqué de bout en bout dans un navigateur réel.

## Échec conservé et relance

La première tentative d'upgrade, job `114211703450`, a échoué au watchdog
du banc composé, lors de `http-start-before-receipt`, après les interruptions
de transfert et d'admission. Le test s'est arrêté après 2864,370 secondes
avec `Native public composition timeout`. Ce résultat reste un FAIL.
Les cinq fenêtres SQL alors terminées restaient inférieures à 180 secondes.

Seul ce job a été relancé, sans changement du SHA produit, des assertions,
du watchdog ou des bornes SQL. Le rollback réussi a été conservé.
La lenteur de l'hôte est une hypothèse de diagnostic, pas une cause démontrée.
Les journaux et mesures utiles de l'échec sont joints à la livraison.

## Livraison et suite

La branche technique Installer est
`validation/phase6-public-fragments-20261010`. La branche canonique
`quality/phase6-gateway-lifecycle-20261004` reste à
`1028c05ce48d0f528f8b6f71837be51ee15710a0`, et `main` reste à
`c0dcb902663130302599635b36c7fb8deab80a47`.

Le ZIP léger contient les fichiers complets modifiés depuis `1028c05`,
les documents mis à jour, les empreintes de base et de livraison, les verdicts
natifs sélectionnés et un vérificateur d'intégrité sans effet système.
Le gate final vérifie le gel documentaire ; les fichiers de code, de tests
et de workflow sont identiques au commit natif qualifié. Aucun changement
de schéma, de `schema.sql` ou d'`install.php`.

Lire [HANDOFF_WORK_20261010.md](HANDOFF_WORK_20261010.md) et
[PHASE6_REMAINING_LOTS.md](PHASE6_REMAINING_LOTS.md) pour continuer.
Les tickets #17 et #18 restent ouverts jusqu'à qualification de leur périmètre
restant ; ce lot ne justifie pas leur clôture globale.
