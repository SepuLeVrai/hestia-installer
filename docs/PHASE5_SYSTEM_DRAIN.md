# Barrière d'arrêt des services de l'instance

## Composition privée du collecteur

Le [raccordement HTTP/collecteur](PHASE5_HTTP_CLEANER_DRAIN.md) utilise un audit
explicite acceptant le job numérique d'un oneshot collecteur réellement en
activation. Le contrat à quatre rôles n'active pas cette option. Les audits
d'arrêt continuent d'exiger Job vide, Result=success et cgroup vide. Le timer
est contrôlé et arrêté séparément par son provisionneur typé.

## Raccordement HTTP distinct

Le contrat à quatre rôles reste inchangé. Le [chantier HTTP](PHASE5_HTTP_DRAIN.md)
ajoute une barrière séparée pour les deux unités issues de HttpRuntime, avec
observation ponctuelle des threads de l'identité dédiée. Il n'invente pas une
unité CLI et ne transforme pas ce sous-ensemble en inventaire exhaustif.

## Base et périmètre

Base qualifiée : `ed7707c9eb7f6314e5d3d3b899f0efe1cef92d53`, arbre
`7b7b4141137a6341a00e0210c9ac84ff736ea184`. Quality `36139574996` et SQL/HTTP
`36139636592` : 506 core par Debian, 16 DOM, 21 HTTPS et 118 scénarios réels,
sans échec ni skip. Web reste `46c03060625d4d53c675474b11aaa33007d9aad7`.

Ce lot ajoute `installer.system_drain.SystemDrain`, adaptateur privé **d'arrêt
seulement**. Il exige quatre services déjà provisionnés par l'adaptateur système
de confiance : Apache, PHP, CLI et nettoyage des sessions. Il ne les installe
pas, ne découvre pas les autres producteurs de l'hôte et ne permet pas de
choisir une commande shell. Aucun écran ou endpoint public n'est ajouté.

Il répond à deux limites démontrées au lot précédent : réception multipart en
amont du guard PHP et processus enfant survivant à son parent PHP. Un groupe
de processus Unix n'est pas utilisé comme preuve suffisante : un descendant
qui appelle `setsid()` reste suivi par le cgroup v2 de son service.

## Contrat d'enrôlement fermé

Les quatre `UnitBinding` portent uniquement les rôles, dans l'ordre fixe
`apache`, `php`, `cli`, `session-cleaner`, et les SHA-256 attendus des fragments
provisionnés. Leurs noms sont dérivés de l'identifiant d'instance de 32 caractères
hexadécimaux : `hestia-<instance>-<rôle>.service`. Le provisionneur fournit ces
empreintes depuis son plan de confiance, jamais depuis le navigateur.
Cela n'atteste pas que les commandes des unités couvrent tout l'hôte.

Les fragments doivent résider dans `/etc/systemd/system`, être des fichiers
réguliers root:root 0644, sans lien, hardlink ni ACL, dans des parents protégés.
Chaque unité doit avoir exactement un drop-in, `50-hestia-maintenance.conf`,
dont les octets sont produits par `condition_dropin(scope)` :

```ini
[Unit]
ConditionPathExists=!/chemin/prive/maintenance/maintenance.attempt
```

Le chemin est une donnée du provisionneur, soumis à une grammaire fermée sans
espaces, directives, traversées ni spécificateurs systemd. Le contenu chargé
doit correspondre au fragment et à l'unique drop-in contrôlés, avec
`NeedDaemonReload=no`. Pas de lien ou d'alias vers un service existant partagé.

| Propriété requise | Garantie bornée |
| --- | --- |
| `KillMode=control-group`, `SendSIGKILL=yes` | Descendants suivis pendant l'arrêt |
| `Delegate=no`, `Slice=system.slice` | Chemin de cgroup fermé, sans délégation aux producteurs |
| `Restart=no`, `RemainAfterExit=no` | Pas de redémarrage automatique ni d'état actif sans processus |
| Condition de maintenance chargée | Refus d'un nouveau démarrage tant que le marqueur reste présent |
| Aucun job en cours | Pas de transition systemd concurrente acceptée |
| `inactive/dead`, PID principal et contrôle à zéro, `Result=success` | Arrêt réussi, pas seulement un PID principal disparu |
| `cgroup.events: populated 0` et `cgroup.procs` vide, ou feuille disparue | Absence de processus, y compris descendants détachés |

Un timeout avec élimination forcée peut vider le cgroup ; il est tout de même
refusé comme résultat réussi. Un service défectueux nécessite une résolution
explicite, pas un `reset-failed` automatique destiné à fabriquer un PASS.

## Séquence et interruption

1. Contrôler consentement, identité root et profils des quatre services.
2. Acquérir la maintenance existante : marqueur durable avant attente des
   requêtes PHP déjà entrées, verrou exclusif et lease exacte.
3. Écrire et synchroniser `system-drain-<lease>.attempt`, liant instance,
   chemin de maintenance, politique et empreintes des quatre unités.
4. Arrêter Apache, puis PHP, CLI et le service enrôlé de nettoyage. Avant chaque
   arrêt, revérifier la lease et l'unité ; après, vérifier l'état et le cgroup.
5. Recontrôler l'ensemble avant de rendre la lease système vivante.

La commande est uniquement `/usr/bin/systemctl show|stop`, avec arguments
séparés, environnement réduit, délais bornés et diagnostic public fermé.
Aucun appel `start`, `restart`, `daemon-reload`, `mask`, suppression d'unité ou
signal adressé à un PID observé n'existe dans cet adaptateur.

Une exception, annulation, réponse perdue ou mort du contrôleur conserve le
marqueur et la tentative. `recover(lease_id, confirmed=True)` réacquiert seulement
la même maintenance, exige le même profil et répète les arrêts idempotents des
seuls services enrôlés. Ce n'est ni un rejeu SQL ni une reprise d'upgrade.
La disparition d'une unité ou un changement de profil est un refus.

La lease ne se sérialise pas. `report()` et `maintenance_lease` recontrôlent
en ligne le journal, les fichiers, systemd et les cgroups. Sa fermeture libère
le verrou sans retirer la maintenance ni redémarrer les services. La reprise
de l'activité et sa récupération après échec appartiennent au lot suivant.
L'API de maintenance sous-jacente reste privée ; appeler son `resume` invalide
la preuve et n'est pas une procédure de remise en service livrée ici.

## Qualification et limites

18 tests core supplémentaires, soit **524 par Debian 12/13**, couvrent les
refus, dérives, propriétés requises, ordre, annulation, interruption, reprise,
confidentialité et revalidation. Les tests core simulent systemd explicitement.

Le workflow distinct `Installer system runtime Quality` utilise les paquets
officiels Debian 12 et 13, un vrai systemd PID 1 et un cgroup v2 privé dans un
conteneur jetable. Aucun montage du cgroup hôte, port exposé ou credential de
dépôt n'est transmis au conteneur. Apache et FPM fonctionnent réellement.
Les onze recettes par Debian exercent sessions, multipart partiel, enfant
`setsid()` après SIGKILL d'un worker FPM, requête déjà protégée, SIGKILL du
contrôleur, redémarrage refusé, dérive, service étranger préservé, reprise exacte
et timeout d'arrêt refusé. Les octets et modes des sources sont comparés avant
et après ; aucun skip n'est admis. Les 118 recettes SQL/HTTP historiques, 16 DOM
et 21 HTTPS restent requis sur le commit exact.

Les endpoints PHP, commandes CLI et processus de nettoyage de cette recette
sont des **fixtures**. Le Web complet, le nettoyage Debian `phpsessionclean`,
les timers/cron existants et les autres écrivains SQL ne sont pas qualifiés par
ces onze tests. PHP 8.2 de Debian 12 peut qualifier cette barrière sans satisfaire
la contrainte PHP >= 8.3 du Web épinglé. Ne pas confondre ces deux matrices.

Le rapport `ENROLLED_SYSTEM_SERVICES_DRAINED` reste étroit :
`storage_inventory_complete`, `system_wiring_verified`, `complete_web_backup`,
`apply_allowed`, `rollback_verified` et `application_installed` restent faux.
La barrière n'est pas encore raccordée automatiquement à la sauvegarde
coordonnée ou au wizard. Aucun changement Web, Gateway, APK, serveur existant,
PR ou promotion ne découle de ce lot.

Suite : provisionnement typé des vrais services et répertoires, intégration du
nettoyeur natif sous le verrou commun, inventaire des producteurs externes,
remise en service contrôlée et profil de données métier inscriptibles. Puis
transition de version réelle, reprise/rollback et wizard. La Phase 5 reste ouverte.

Références techniques : [systemd.kill](https://www.freedesktop.org/software/systemd/man/systemd.kill.html),
[systemd.unit](https://www.freedesktop.org/software/systemd/man/systemd.unit.html),
[PHP-FPM](https://www.php.net/manual/en/install.fpm.configuration.php),
[Apache mod_proxy_fcgi](https://httpd.apache.org/docs/2.4/mod/mod_proxy_fcgi.html).

## Première campagne système et borne de réception HTTP

Le candidat `349ce62e66e845efba6917ef6e9caddecd458e88` a exécuté neuf cas
système réussis sur dix par Debian. La campagne `36142515676` a correctement
refusé l’arrêt Apache après quatre secondes : le client multipart du banc
laissait son corps ouvert sans aucune borne de réception HTTP. Le cgroup
avait été éliminé par systemd, mais `Result=timeout` ne doit pas devenir un PASS.

La recette positive charge désormais le module officiel `mod_reqtimeout` avec
une limite de réception courte de une à deux secondes propre au banc, inférieure
aux quatre secondes du délai d’arrêt de la fixture. Le client ne ferme pas
artificiellement son upload pour aider le test. Un onzième cas désactive cette
limite et conserve exactement le refus du client bloqué. Le code de drainage,
le délai d’arrêt et le refus des arrêts forcés restent inchangés. Le marqueur
du vrai endpoint upload prouve également que son code n’est pas entré.
Le futur profil Apache devra définir ses propres bornes réalistes de réception
et de drainage ; ces valeurs de test ne sont pas une configuration produit.
Les fichiers corrigés nécessitent une nouvelle qualification complète.
