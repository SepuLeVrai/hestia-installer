# Handoff WORK - HESTIA Installer - reprise du 25 septembre 2026

## Lecture de départ

Ce document prépare la reprise après le sous-lot 5B2.2a du 24 septembre.
Ne pas reprendre le handoff initial demandant 5B2.1 : ce lot est déjà publié.
Lire d'abord les HEAD réels, puis les documents du commit retenu. La preuve de
publication de ce document est le commit qui le contient et ses campagnes Quality,
pas un hash que le document pourrait citer avant sa propre publication.

Base Installer de cette évolution : `93e7d7c1be273c9e17a1fc97846b062367cdb5e5`.
Web relu : main et dev-Bastien = `dcb856bc5ef5f35006d2398289b49f5386dcc5f5`.
La présente évolution n'a besoin d'aucune modification des fichiers Web.
Lire aussi le dernier commentaire de livraison dans Installer #13 : il donne
commit publié, runs finaux, ZIP léger et empreintes. Vérifier leur statut réel.
Ne pas réappliquer les ZIP 5A/5B1/5B2.1 sur un HEAD déjà à jour.

## État fonctionnel à conserver

| Frontière | État et sens exact |
| --- | --- |
| Phases 1-4 | Bootstrap HTTPS, orchestration transactionnelle et acquisition existent. |
| 5A | Validation fermée INPUT_ONLY, publiée et qualifiée. |
| 5B1 Web | Moteur SQL partagé, DATABASE_READY mais application_installed=false. |
| 5B2.1 | Transport privé Python/PHP, provenance/identité/secrets/bornes et interlock anti-rejeu. |
| 5B2.2a | Audit de comptes SQL locaux existants et configuration protégée non activée. |
| Reste 5B2.2 | Création base/comptes, droits, ports et distant/TLS, intégration contrôlée. |
| 5B2.3 | Assistant optionnel, activation cohérente et scellement, non livrés. |
| 5C | Upgrade, sauvegarde, reprise/rollback pilotés, non livrés. |
| 5D | Écrans applicatifs, identités système et recette Web intégrée, non livrés. |

Le wizard termine toujours par « Sources prêtes ». Ne jamais le faire afficher
« HESTIA installé » après un audit, une acquisition, un DDL ou un fichier écrit seul.

## Ce que fournit 5B2.2a

Lire [PHASE5B22A_LOCAL_SQL_CONFIGURATION.md](PHASE5B22A_LOCAL_SQL_CONFIGURATION.md).
Principaux fichiers : installer/sql_accounts.py, installer/database_config.py et
les trois ressources PHP privées associées. Aucun raccordement public/HTTP n'existe.

Seul existing_local / TCP 127.0.0.1:3306 est pris en charge. Base, identités système
et comptes SQL sont préexistants. Application = SELECT/INSERT/UPDATE/DELETE sur
le seul schéma ; provisioning = ALL sur le seul schéma sans GRANT OPTION. Rôles,
PUBLIC privilégié, compte/hôte générique et grants ambigus sont refusés. Les GRANT
sur un nom avec underscore doivent l'échapper. Aucun compte n'est créé ni modifié.

Le chargeur généré et son JSON applicatif restent hors webroot, root:groupe-Web
0750/0640, sans credential privilégié. Aucun includes/db.php, install.lock ou réglage
Assistant n'est posé dans le Web. Ce staging est un secret durable à conserver ;
son activation fera l'objet d'une autre étape. Aucun fichier préexistant n'est exécuté.
Upgrade est refusé pour l'écriture ; l'audit seul peut observer les comptes en upgrade.
Une tentative interrompue reste bloquante et ne peut pas être écrasée automatiquement.

## Prochain travail recommandé, à borner avant codage

Commencer par le reste de 5B2.2, sans absorber Assistant, upgrade et écrans.
Relire includes/functions.php::get_pdo() et la chaîne includes/bootstrap.php.
Le runtime Web ne gère actuellement ni un DB_PORT distinct ni des options PDO TLS.
Ne pas glisser de fragment DSN dans DB_HOST pour contourner ce contrat.

La création contrôlée des comptes et de la base doit distinguer l'autorité SQL
capable de provisionner des comptes, le compte de migration restreint au schéma
et le runtime DML. Définir préflight, consentement, cible non occupée, privilèges,
révocation des droits temporaires et traitement d'un résultat incertain AVANT DDL.
Ne jamais publier le mot de passe d'autorité/migration dans la configuration Web.

Le parcours distant nécessite une CA protégée, la vérification du certificat et
du nom du serveur, un chiffrement effectivement observé, des délais et des tests
négatifs (CA incorrecte, certificat expiré/mauvais nom, refus du clair, interruption).
Ne pas transformer le simple champ tls_ca_file validé en 5A en preuve de TLS réel.
Toute évolution du Web exige sa propre Quality complète et un ZIP compagnon
avec fichiers complets nécessaires ; ni schema.sql ni install.php ne doivent
recevoir une modification artificielle pour donner l'impression d'une migration.

Assembler ensuite explicitement les préconditions, l'appel 5B2.1 et le staging.
L'audit des grants est ponctuel, ne certifie pas le schéma et ne remplace pas une
vérification au moment d'emploi. La politique DML n'est pas encore une recette
exhaustive de toutes les fonctions Web, notamment maintenance/restore/DDL.
Qualifier cela sans redonner des privilèges root/ALL au runtime.

## Règles et autorisation de cette conversation

Bastien a autorisé l'écriture dans les dépôts concernés par ce chantier, avec
fast-forward uniquement après Quality 100 % réussie. Ne pas en déduire un déploiement
sur LAB-PAWEB30 ni une permission de modifier une base de production. Ne pas toucher
Gateway/APK tant que leur frontière n'est pas abordée. Pas de compilation APK inutile.

Partir de main Installer et des branches Web demandées, jamais d'une branche
technique d'assemblage, de qualification ou d'une vieille préparation. Comparer
les HEAD à nouveau avant promotion et refuser une divergence inattendue. Aucun
force push. Les ZIP sont légers, composés des fichiers complets exacts testés.
Aucun changement de dernière minute après les preuves ; sinon tout requalifier.

## Quality et limites de preuve

[QUALITY.md](QUALITY.md) décrit les contrôles permanents et leur inventaire strict.
La suite SQL 5B2.2a est incluse dans core Debian 12/13 et impose un opt-in explicite :

```bash
# Conteneur root jetable, aucun MariaDB existant sur le port 3306.
HESTIA_ACCOUNT_DB_TEST=1 ./scripts/quality-local.sh
```

Les 290 tests core antérieurs sont conservés ; les nouveaux tests doivent tous
être présents dans tests/quality-baseline.json. Le serveur SQL de test utilise
son propre datadir aléatoire, n'écoute que loopback et refuse un port déjà occupé.
Ne pas confondre ce parcours de fixture avec l'installation système de MariaDB.
Ne pas confondre la connexion PDO via constantes générées avec une recette Web HTTP.

Les 16 scénarios DOM et 21 HTTPS natifs restent obligatoires même sans nouvel écran.
PHP 8.2 Debian 12 qualifie la compatibilité du nouveau composant, pas le vendor Web
exigeant PHP 8.3+. Sans MariaDB ou pdo_mysql locaux, ne pas annoncer les tests SQL
comme exécutés localement. Lire les artefacts du nouveau run pour les preuves CI.
Les contrôles antérieurs 5A/5B1/5B2.1 restent historiques, pas une preuve du nouveau code.

Issues de reprise : Installer #13 et Web #135 (ouvertes, transverses), Installer #1
(global), #3 (wizard complet), puis contrats Gateway #4 et APK #5 quand concernés.
