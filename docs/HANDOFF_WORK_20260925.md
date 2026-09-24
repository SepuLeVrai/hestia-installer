# Handoff WORK - HESTIA Installer - après la frontière 5B

## Lire avant de modifier

La demande de Bastien est de terminer 5B, sans absorber 5C/5D. Le présent lot
réalise sa composition applicative privée. Vérifier les HEAD actuels, la présence
de ce contrat sur main et les Quality du commit, puis le dernier commentaire
Installer #13 / Web #135. Ce commentaire contient les SHA/runs/ZIP finaux qui ne
peuvent pas être cités avant la création de ce commit lui-même.

Bases relues : Installer 07d0da54c317420463a3699ee96dd6000afa6c31 ;
Web main/dev-Bastien dcb856bc5ef5f35006d2398289b49f5386dcc5f5.
Ne pas réappliquer les anciens ZIP ni reprendre leurs branches techniques.
La conversation autorise les écritures dans les dépôts concernés, mais les
fast-forwards exigent toutes les Quality requises réussies, sans force push,
sans effacement d'un HEAD concurrent et sans déploiement de production implicite.

## Ordre de lecture

1. [PROJECT_STATE.md](PROJECT_STATE.md), [PHASE5B_COMPLETE.md](PHASE5B_COMPLETE.md)
   et [QUALITY.md](QUALITY.md), puis le dernier compte rendu Installer #13.
2. installer/phase5b.py et les trois ressources private/phase5b_*.php ;
   tests/test_phase5b.py et tests/integration/phase5b_e2e.py.
3. Web docs/INSTALLER_PHASE5B_CONFIGURATION.md, les bibliothèques
   includes/installation/connection.php, managed_config.php et finalize.php,
   puis get_pdo() et les méthodes Assistant modifiées.
4. Relire les moteurs existants avant l'upgrade : installation/core.php,
   fresh.php et les migrations publiées. Ne pas dupliquer leurs factories.

## État après qualification/publication de ce lot

| Frontière | État et sens exact |
| --- | --- |
| 1-4 | Bootstrap/orchestrateur/acquisition présents, wizard Sources prêtes. |
| 5A | Validation INPUT_ONLY, jamais un consentement de mutation. |
| 5B1 | Moteur SQL partagé unique, fresh refuse l'existant. |
| 5B2.1 / 5B2.2a | Anciennes API conservées, leurs limites historiques subsistent. |
| 5B complète | Nouvelle API privée compose comptes, TLS, fresh, config, Assistant et sceau. |
| 5C | Moteur d'upgrade/sauvegarde/restauration/reprise à réaliser. |
| 5D | Installation système/identités, PHP-FPM/Apache, données et écrans/recette HTTP à réaliser. |

WEB_CONFIGURED signifie runtime SQL/config vérifié sous l'identité Web,
application_installed=false et http_verified=false. Ne pas annoncer une
installation one-shot achevée, un accès OpenAI testé ou un login HTTP à partir
seulement de la réussite de 5B. Le mode managed suppose le serveur SQL prêt ;
il crée base et comptes et supprime son compte temporaire après vérification.
Le compte d'autorité/migration n'est jamais un secret durable du Web.

## Prochaine frontière : 5C, pas une nouvelle 5B2.2a

Définir les versions acceptées, un plan non mutant, une sauvegarde réelle et
sa restauration testée avant toute migration. Exiger une autorité privée explicite,
préserver Admin/password/données/RBAC/paramètres et configuration Assistant.
La fresh install ne doit jamais servir de fallback pour une base existante.
Les DDL ne sont pas une transaction annulable : le journal doit décrire les effets
incertains et les frontières de récupération, sans supprimer automatiquement
base/comptes ou fichiers qu'il ne possède pas.

L'API update_assistant actuelle vise une instance gérée, scellée et épinglée :
conserver observe, remplacement vide conserve, disabled est explicite. Elle ne
convertit pas un ancien fichier PHP IA et n'évalue jamais ce fichier en root.
La conversion des configurations legacy et les migrations relèvent de 5C.
Ne pas vider une clé existante parce qu'un champ de formulaire est vide.

## Invariants pour 5D

Ne pas assouplir les contrôles root/dirfds/ACL/liens ni les identités worker/Web
pour contourner un problème du banc. Préparer les répertoires et le PHP avant
l'appel privé ; raccorder seulement une opération typée avec secrets hors journal.
La politique de sources est fermée. L'exécution d'un Web actif avec ses répertoires
uploads/logs/caches modifiables exige une séparation code/données explicite et des
tests, pas l'exclusion globale des contrôles de provenance. La recette DML actuelle
n'est pas une validation de tous les écrans DDL/maintenance/backup du Web.

La connexion gérée prend maintenant le port et TLS séparément ; aucun fragment
DSN dans DB_HOST. Contrôler la CA persistante et le runtime sous PHP-FPM/Apache,
les fichiers sensibles inaccessibles, sessions 43200 s sans désactiver
phpsessionclean, la politique fonctionnelle 1 h / 4 h / 8 h et les proxies fiables.
Conserver l'UX figée. Pas de modification Gateway/APK avant leurs phases propres.

## Quality, publication et preuves

Les tests existants sont conservés, les nouveaux inventoriés. Exécuter la Quality
Installer complète, la Quality Web complète et la campagne transverse E2E sur
les octets finaux. Les essais de préparation, campagnes interrompues et anciennes
preuves ne sont pas le PASS du lot. Le compte rendu indique les matrices exactes,
les incidents et les vérifications de ZIP/modes/application/réapplication.
Les ZIP de chaque dépôt contiennent des fichiers complets ; install.php et
schema.sql complets accompagnent le Web sans modification SQL artificielle.
Les branches verification/quality d'assemblage ou de tests croisés ne sont pas
une base de reprise et leurs sondes/paquets ne doivent jamais être fusionnés.

Les issues transverses Installer #13 et Web #135 restent ouvertes pour 5C/5D
et leurs engagements ultérieurs. La clôture de 5B ne ferme pas ces périmètres.
