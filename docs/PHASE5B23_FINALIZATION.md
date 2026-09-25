# Phase 5B2.3 - Finalisation privée du Web fresh

## Frontière et sources

Base Installer : `036cbfd4b581b2245fbe7b6974625e6b11820818`.
Compagnon Web exact : `46c03060625d4d53c675474b11aaa33007d9aad7`, arbre
`aaac278270e0fd1169396945916dfe997ae078bf`.
La présence de ces références ne prouve pas une promotion. Les résultats, les
runs et les HEAD effectivement publiés sont consignés dans le rapport compagnon
et les derniers commentaires Installer #13 / Web #135, après qualification.

Ce lot termine 5B avec l'Assistant optionnel, l'activation des fichiers préparés
et le scellement. Il ne refait pas le provisioning ou le moteur fresh de 5B2.2.
Les API et les empreintes historiques sont conservées, ainsi que tous leurs tests.
Il ne livre ni migrations d'upgrade 5C, ni services, identités système, droits des
données et raccordement au wizard 5D. Aucun nouvel endpoint public n'est ajouté.
Le wizard continue donc d'annoncer « Sources prêtes ».

## API privée

`installer.finalization.FinalizationStep(runtime, source, repository=..., commit=...)`
exige le dépôt Web et le commit du contrat 5B2.3 ci-dessus. Trois opérations :

- `finalize(payload, config_root=Path(...), confirmed=True)` : fresh déjà préparé ;
- `observe(payload, config_root=Path(...))` : observation seule d'une instance scellée ;
- `configure_assistant(payload, config_root=Path(...), confirmed=True)` : réglage
  seul de l'Assistant d'une instance déjà finalisée par ce contrat.

`finalize` exige un payload fresh valide, le mot de passe applicatif et celui de
l'Admin créé en 5B2.2. Il ne prend ni compte d'autorité ni credential de migration.
La configuration privée et le marqueur durable DATABASE_CONFIGURATION_READY de
5B2.2 doivent être présents. Un simple staging d'audit 5B2.2a ne suffit pas.
La cible Web doit déjà contenir le code attendu sous propriétaires protégés : le
copieur de déploiement, les paquets et les services restent en 5D.

L'empreinte runtime inclut les fichiers exécutables/configurations de contrôle du
Web, tout le vendor inclus et l'ensemble de leurs chemins. Les ajouts de scripts,
liens, écritures groupe/autres et ACL non admises sont refusés. Documentation,
tests et pointeurs générés sont exclus de cette empreinte d'exécution. Les mêmes
contrôles sont appliqués à la source et à la copie cible avant toute activation,
puis de nouveau avant publication. La copie SQL privée utilise aussi une empreinte
fermée de son sous-ensemble exécuté. Les pins SQL historiques restent distincts :
leur code fresh/connexion/vendor est inchangé dans le compagnon Web 5B2.3 ; cette
égalité de sous-ensemble ne constitue pas une autorisation d'accepter un autre
commit ou une autre empreinte dans FinalizationStep.

## Séquence et sécurité

1. Confirmation, payload fermé, chemins root-owned, groupe Web dédié, empreintes
   de source/cible, absence de pointeur/verrou/fichiers d'activation préexistants.
   Aucune réparation implicite, réutilisation d'un fichier occupé ou adoption legacy.
2. Vérification stricte des fichiers préparés, du mot de passe applicatif, du CA,
   du chargeur et du reçu SQL. Booléens, nombres, clés JSON et versions sont typés.
3. Sonde SQL en PHP non privilégié sur une copie privée vérifiée, credentials par
   stdin borné : DML exact sur seul schéma réaudité, droits globaux/rôles/PUBLIC
   refusés, TLS observé lorsque requis. Vérification du premier Admin actif, rôle,
   password_verify, unicité, version, tables et Assistant initialement désactivé.
4. Réservation exclusive `finalization.attempt` root:root0600 et fsync avant mutation.
   Création de assistant.json en données seulement, puis écriture transactionnelle
   du seul réglage assistant.enabled. Aucun DDL, compte SQL ou utilisateur modifié.
5. Sonde réelle sous l'identité Web : chargeur préparé, get_pdo, audit DML, version
   et helpers Assistant. Environnement vide, groupes/capacités supprimés, pipes,
   mémoire, fichiers, temps et erreurs bornés. Le confinement open_basedir de la
   copie SQL est conservé. La sonde Web réelle doit lire les chemins actifs et
   leurs ancêtres : elle repose sur les permissions POSIX et les pins de code,
   pas sur un open_basedir limité au seul staging. Aucun PHP n'est lancé root.
6. Publication O_EXCL sans écrasement : includes/db.php sans credential, install.lock,
   puis seal.json privé en dernier. Le pointeur exige leurs empreintes et celles
   de database.json, du CA et du chargeur. Sans sceau cohérent : HTTP503/no-store
   générique, exception CLI fixe. Le formulaire install.php historique reste fermé
   dès le pointeur, sans modification de son code.
7. Sonde Web active sur ces fichiers exacts, puis reçu durable finalized.json lié
   au commit Web, à l'empreinte runtime et aux fichiers d'activation. Succès annoncé
   uniquement après création et relecture de ce reçu.

Seuls database.json et assistant.json contiennent des credentials durables. Ils
restent hors webroot et survivent au nettoyage du staging éphémère. Dossier privé
root:groupe-Web0750 ; fichiers DB/chargeur/preuves0640 ; journaux privés0600 ;
seul le fichier de données assistant.json est0660. Les dossiers et fichiers PHP
ne sont jamais inscriptibles par le groupe Web. Aucun credential dans les arguments,
variables héritées, reçus, résultats publics ou logs de qualification.

## Assistant et conservation

Sans clé : désactivation explicite, aucun appel externe. Avec clé de format valide :
stockage protégé et activation du réglage. `api_access=NOT_TESTED` ne devient jamais
une validation d'accès au service ou à un modèle. La clé n'est pas stockée en SQL.

Sur une instance déjà finalisée, observe/configure_assistant utilisent le payload
sans recréation d'Admin (mode upgrade, administrator=null, admin_password vide ;
une DB managed déjà créée se décrit alors en existing_local). Cela n'est PAS un
upgrade applicatif. `preserve` conserve ; `configure` avec clé non vide remplace ;
`configure` vide conserve ; `disabled` désactive sans effacer la clé. L'effacement
reste un choix explicite distinct dans l'interface Web de gestion existante.

La gestion Web garde Admin général/ASSISTANT_ADMIN. Le mode géré n'évalue aucun
secret PHP et n'a aucun repli vers les clés d'environnement/globales/legacy. JSON
invalide, permissions anormales, fichier lié, absence ou verrou concurrent rendent
l'Assistant inactif. Lectures/écritures prennent un flock non bloquant sur le même
inode ; une écriture interrompue peut laisser un JSON invalide, refusé en lecture.
La gestion Web historique demeure inchangée hors opt-in géré.

Les réglages privés sont sérialisés par assistant-edit.lock. Chaque changement
possède un marqueur .attempt privé fsync avant mutation puis un .done exact. Une
réponse perdue laisse une observation manuelle et interdit un changement aveugle.
La concurrence entre deux contrôleurs de finalisation ne peut produire deux succès.

## Interruption et états observables

Un échec après réservation ne permet pas de rejouer fresh/finalize. Le résultat
est MANUAL_ACTION ; SQL, secrets et preuves sont préservés. Après une erreur détectée
post-activation, seul notre sceau exact est révoqué. Une impossibilité de révoquer
produit FINALIZATION_REVOCATION_UNCERTAIN, pas une garantie de retour arrière.

Un arrêt brutal après seal.json et avant finalized.json peut laisser le Web actif,
mais l'observation Installer reste MANUAL_ACTION. Il n'est pas prétendu que le sceau
et le reçu soient atomiques. Après reçu complet puis réponse perdue, observe peut
attester WEB_FRESH_FINALIZED sans aucune réexécution. Les tests arrêtent de vrais
processus à ces deux frontières. Pas de rollback DDL atomique promis.

L'état positif exact est :

```text
state = WEB_FRESH_FINALIZED
configuration_activated = true
installation_sealed = true
application_installed = false
system_qualification_required = true
api_access = NOT_TESTED
```

L'Assistant retourné est réellement observé en base et dans le fichier, pas déduit
du formulaire. application_installed reste false tant que la recette système 5D
n'est pas réalisée. Un passage par get_pdo ou un HTTP loopback n'est pas un test
Apache/FPM/HTTPS/proxy/GED complet.

## Qualification reproductible

Sur une machine Debian jetable root, avec port3306 libre, PHP/MariaDB/OpenSSL,
les sources exactes root-owned hors /tmp et les prérequis historiques :

```bash
HESTIA_ACCOUNT_DB_TEST=1 ./scripts/quality-local.sh
HESTIA_ACCOUNT_DB_TEST=1 HESTIA_DATABASE_STEP_TEST=1 HESTIA_FINALIZATION_TEST=1 \
  python3 tests/integration/finalization_mariadb.py --web /chemin/prive/web \
  --report /chemin/prive/finalization-results.json
HESTIA_ACCOUNT_DB_TEST=1 HESTIA_DATABASE_STEP_TEST=1 \
  python3 tests/integration/database_step_mariadb.py --web-source /chemin/prive/web
```

La première campagne conserve les353 tests core précédents et ajoute37 tests
unitaires fichiers/protocole. Les21 scénarios croisés 5B2.3 utilisent vraiment PHP,
MariaDB, le service Web non privilégié, des certificats/données jetables, HTTP
loopback, les changements de privilèges, caractères Unicode, textes longs, valeur
BIGINT maximale, concurrence et interruptions. Les18 scénarios historiques SQL/TLS
restent distincts. Les37 unitaires simulent leurs frontières SQL/source/probe ;
ils ne remplacent pas la campagne réelle.

La Quality distante reste complète : core Debian12/13,16 tests de gate également
inclus dans core,16 DOM et21 HTTPS natifs ; Web PHP8.3/8.4, Composer, syntaxe PHP/JS,
MariaDB11.4 fresh/replay/upgrade, Apache interne. Le nouveau test Web comporte27
contrôles ; il est inclus dans la boucle existante, sans suppression historique.
Les versions/résultats effectivement mesurés sont dans le rapport compagnon.

Aucun install.php, schema.sql, seed, migration, version, vendor, CSS ou JS produit
n'est changé. Pas d'appel API payant, PAT utilisateur, déploiement serveur, mutation
Gateway/APK. Les préconditions et limites SQL/TLS de 5B2.2 restent applicables.
