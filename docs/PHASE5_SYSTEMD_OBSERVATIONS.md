# Collecte privée des unités systemd provisionnées

## Résultat acquis et contrat de l'élargissement suivant

Le candidat `75e987cedcdac2ad5b1795d0b0c55fdca573a894`, arbre
`37a4afb64155a90fdbc8333a912649576dabe09a`, a passé la campagne ciblée
`36225529757` : 73 contrôles et huit scénarios réels Debian 13, un seul job,
première tentative, zéro erreur/échec/skip. Sources et artefact ont été vérifiés.
Ce résultat reste ciblé ; le pont métier scellé n'a pas sa preuve système.

Le [contrat hors profil](PHASE5_SYSTEMD_SCOPE.md) définit désormais le futur
index élargi, sans changer ce collecteur ni ses permissions. Ses cas ne sont
pas encore exécutés. Le texte ci-dessous conserve le contrat du lot livré.

## Base et périmètre

Base Installer `c8af4c8d1bbb25dd55ae17651019692479dde232`, arbre
`46da7d1c7318d293065ac9d7ee16e1b88628399f`, 192 fichiers. Cette base avait
46 contrôles locaux et aucune Actions ; sa qualification globale reste différée.
Le Web métier `2a27c7a1` reste inchangé.

`SystemdObserver(runtime, cleaner=...)` accepte uniquement un `HttpRuntime`
typé et, éventuellement, son `SessionCleaner` exact. Il relit les contrats
immuables de provisionnement avant et après la collecte, sans appeler leurs
observations de staging, qui restent strictes. Le périmètre comporte deux
services Apache/PHP, ou ces deux services, le collecteur et son timer.
Aucun nom fourni par HTTP, inventaire global ou liste arbitraire d'unités.

Il n'exécute aucune commande métier. La seule commande de collecte est
`/usr/bin/systemctl --system ... show --property=... -- <unité dérivée>`.
Aucun start, stop, enable, disable, reload, daemon-reload, enrôlement, écriture
de journal ou acquisition de maintenance. Les actions de préparation et de
démarrage de la recette appartiennent exclusivement au banc jetable.

## Provenance et visibilité

La collecte exige root et les mêmes namespaces PID et montage que PID 1.
Elle conserve en privé ces identifiants, `/etc/machine-id`, le boot_id et le
gestionnaire système. La configuration provisionnée impose déjà Debian,
systemd, cgroup v2, les dépendances officielles et leurs protections.
Ces identifiants locaux ne sont pas une attestation distante ou cryptographique.
Une identité illisible, un autre namespace, un gestionnaire inaccessible ou
un résultat malformé provoquent un refus fermé ; aucun résultat vide ou ancien
n'est retourné comme preuve d'absence. L'appelant doit conserver la visibilité
comme non résolue après ce refus, sans réutiliser silencieusement un ancien reçu.

Chaque ligne contient les propriétés sélectionnées réellement lues : identité
et chargement, chemins du fragment et des drop-ins, besoin de daemon-reload,
états, job ; cgroup, PID principal/de contrôle et résultat pour un service,
service déclenché pour le timer. Les chemins chargés doivent correspondre aux
fichiers du provisionneur dont les octets et protections sont revérifiés.
Un fragment différent, drop-in supplémentaire, unité introuvable, timer ciblant
un autre service ou rechargement requis sont refusés, jamais adoptés.

Les propriétés ne couvrent pas toute la définition effective systemd : ExecStart,
environnement du manager, identités effectives des descendants, wrappers,
autres triggers et contextes SQL ne sont pas attestés ici. Le code conserve
les états actifs, en attente et échoués ; leur présence n'autorise aucun arrêt.
Un service inactif n'est jamais déclaré désarmé sur cette seule observation.

## Limites, temporalité et confidentialité

Deux ou quatre lectures show au maximum, chacune limitée à cinq secondes et
16 Kio pendant la lecture du pipe. Le processus enfant systemctl de cette
collecte est récollecté après dépassement ; aucune unité ni PID observé n'est
signalé. stdin/stderr fermés, environnement fixé, cwd `/`, aucun shell.
Les clés sont fermées, sans doublon ou omission ; chaque valeur est limitée
à 2048 caractères et sans contrôle. Les identifiants de job/PID sont bornés.

L'intervalle complet doit rester inférieur ou égal à 60 secondes sur les deux
horloges ; un recul de l'horloge murale est refusé. Ce contrôle d'admission en
fin de collecte ne constitue pas un watchdog interruptible des lectures de
fichiers des provisionneurs. Le résultat n'est pas un instantané atomique :
les unités sont lues séquentiellement et peuvent changer ensuite. Les plans,
définitions et provenance sont relus ; une dérive interdit le résultat.
`collect(previous=...)` refait la collecte et compare contenu, états et
provenance en refusant un recul temporel. Un changement impose une nouvelle
évaluation explicite, sans reprise permissive.

`SystemdSample` contient un manifeste immuable privé, borné à 256 Kio.
`report()` ne retourne que comptage, état, empreinte, couverture partielle et
indicateurs faux. Les chemins, noms d'unités et identités hôte restent privés ;
exceptions et repr utilisent des messages fixes. Ce rapport ne relit pas
l'hôte et n'est jamais une lease, signature, autorisation ou preuve de drainage.

## Pont conservateur vers LauncherInventory

`sample.snapshot(target)` exige le profil métier `external_uploads`, dont
le provisionneur vérifie déjà le Web épinglé et le slot scellé. La cible doit
être identique : instance, commit/arbre Web, webroot, slot/gate, UID/GID,
machine-id et boot_id. Le profil isolé historique ne peut pas produire cette
snapshot, même si ses unités réelles ont été observées avec succès.

Le canal systemd_system reste `partial`, les cinq autres restent `unknown`.
Son empreinte de contenu exclut les instants pour permettre une comparaison
stable, tandis que la fraîcheur conserve le début réel de collecte. Fragments
et états sont transmis ; chaîne, arguments, environnement, UID/GID effectifs,
groupes, cwd, stockages et identités SQL restent inconnus. Le gate effectif
n'est pas certifié par la simple lecture des chemins du drop-in.
Un timer actif est armé ; tout autre déclencheur reste inconnu.

Le modèle pur existant n'est pas modifié et ne fait toujours aucun IO. Ses
blocages conservateurs, dont `HOST_COLLECTION_NOT_IMPLEMENTED`, ne sont pas
retirés par cet adaptateur partiel : ils continuent d'interdire toute conclusion
globale. Une dataclass privée construite par un appelant privilégié n'est pas
authentifiée. Aucun endpoint, wizard, HttpDrain ou sauvegarde ne consomme ce
résultat comme autorisation dans ce lot.

## Qualification bornée et suite

16 nouveaux tests portent l'inventaire core détectable à 689, sans retirer
les identifiants historiques. Sélection locale : `test_systemd_observations`,
`test_launcher_inventory`, `test_http_runtime`, `test_session_cleaner`, soit
73 tests. Le pont métier est contrôlé localement avec les IO de provisionnement
simulés ; cela ne prouve pas une recette SQL/Web complète de ce pont.

Une seule campagne ciblée, un job Debian 13, est prévue après gel code/docs.
Elle rejoue les 73 contrôles et huit scénarios de lecture systemd réelle :
unités inactives et provenance, HTTP actif/timer armé sans arrêt, unité étrangère
hors périmètre, dérive des octets et permissions, drop-in supplémentaire chargé,
changement d'état depuis une observation précédente, refus non-root réel et
refus du pont métier depuis la fixture historique. Réseau coupé pendant la
recette, sources root dans un conteneur jetable, manifeste octets/modes avant
et après. Aucun scénario de job oneshot en attente n'est revendiqué comme
preuve réelle de ce lot ; sa représentation est couverte localement.

La recette est aussi inscrite dans la future campagne système habituelle
Debian 12/13, qui n'est pas lancée pour ce lot. Les preuves finales et les
références exactes restent dans le checkpoint, sans commit documentaire après
la qualification. Aucun résultat d'un arbre antérieur ne qualifie celui-ci.
Quality globale, SQL/Web métier, paquets, navigateur et promotions différés.
Les indicateurs d'inventaire complet, sauvegarde exhaustive, activation,
application installée et fin de phase restent faux.

Après checkpoint et arrêt : chantier distinct sur l'identification des autres
lanceurs pertinents (unités système hors profil, gestionnaires utilisateur,
cron et tâches en attente). Définir d'abord les critères de pertinence et les
limites de visibilité ; ne pas transformer quatre unités connues en inventaire
complet. Le pont du profil métier doit encore recevoir sa preuve réelle lors
de la prochaine recette SQL/Web utile, sans campagne dédiée redondante.
