# Arrêt des deux services HTTP provisionnés

## Extension explicite au collecteur dédié

Ce document décrit le profil HTTP seul, toujours disponible. Le
[contrat complémentaire](PHASE5_HTTP_CLEANER_DRAIN.md) ajoute le paramètre
optionnel typé `cleaner`, lié au même runtime, avec trois services et arrêt du
timer exact. Il ne généralise pas le périmètre aux autres producteurs.

## Base et frontière du chantier

Base Installer qualifiée `3a5e2a44c411895653bd6a0b173c7b575988183d`, arbre
`ef1392f7ceb3bd37b5557a5ee8c29ee519634783`, 181 fichiers. Ses campagnes sont
terminées : Quality 36198237853, système 36198237915, paquets 36198237815,
SQL/proxy/Web/métier 36198304986. Le Web reste inchangé sur le candidat
`2a27c7a1f9fe0a00289eb53278f75d5f230900b7`, Quality 36195113348 verte.

`installer.http_drain.HttpDrain` raccorde l'arrêt à exactement deux services
créés par `HttpRuntime`. Il n'ajoute pas d'unité CLI factice pour satisfaire le
contrat distinct `SystemDrain` à quatre rôles, qui reste inchangé. Il ne crée,
ne démarre, ne redémarre et ne supprime aucun service. Aucun endpoint public,
écran, changement Web, transition d'upgrade ou activation n'est livré ici.

## Contrat privé

Le seul paramètre de construction est un `HttpRuntime` typé fourni par
l'orchestrateur de confiance. Les noms, profils et empreintes d'unités sont
dérivés du provisionnement vérifié, jamais d'une liste fournie par HTTP.
`HttpRuntime._inspect_configuration` factorise les contrôles déjà présents :
identité dédiée, code, fichiers générés, répertoires, dépendances et journal.
Ce contrôle privé n'est pas une observation d'état. `HttpRuntime.observe`
continue d'exiger son marqueur initial et deux services inactifs. Il reste
invalide après activation ou après une nouvelle session de maintenance.

Séquence de `acquire(confirmed=True)` :

1. Contrôler root, consentement, provisionnement, unités chargées sans dérive,
   propriétés systemd, et absence observée de processus étrangers de l'identité.
2. Publier le gate durable puis acquérir le verrou de maintenance existant.
3. Écrire et synchroniser `http-drain-<lease>.attempt`, lié au plan, aux deux
   unités, à l'instance, au gate et à l'UID/GID dédiés.
4. Arrêter Apache et vérifier immédiatement son état et son cgroup ; arrêter
   ensuite PHP et vérifier le même contrat. Un timeout reste un refus.
5. Recontrôler les fichiers, unités, cgroups et l'absence de threads vivants
   portant l'identité dédiée avant de rendre une lease vivante.

`report()` et `maintenance_lease` répètent les vérifications. La fermeture ne
retire pas le gate. `recover(lease_id, confirmed=True)` reprend uniquement la
même maintenance et le même profil ; les arrêts restent idempotents. Une
erreur, annulation ou mort du contrôleur laisse les traces et le gate en place.
Aucun PID trouvé par observation n'est signalé. Seuls les deux noms d'unités
dérivés sont adressés à `systemctl stop`.

## Réception et descendants

Le guard PHP arrive après réception multipart. Le drainage exige donc l'arrêt
réel de l'admission Apache et du pool PHP avec leurs descendants. Le profil
Apache conserve ses limites de réception 5 à 20 secondes et les unités leur
TimeoutStopSec de 35 secondes. La recette positive ne remplace pas ces valeurs
par des délais plus favorables et ne ferme pas le client pour aider l'arrêt.

Le cgroup v2 est contrôlé récursivement, avec le contrat existant
KillMode=control-group, SendSIGKILL=yes, Delegate=no. Un enfant détaché avec
setsid peut survivre au worker PHP ; l'arrêt du cgroup doit le retirer. La
recette utilise un vrai processus externe Python lancé par proc_open pour
qualifier cette propriété. Elle ne prétend pas qualifier les formats de
conversion LibreOffice ou une opération métier du Web complet.

## Recensement de l'identité : portée ponctuelle

Le contrôle procfs examine les threads visibles, leurs UID réel/effectif/sauvé/
filesystem, leurs GID et groupes supplémentaires. Un thread de cette identité
doit appartenir à l'un des deux cgroups avant arrêt ; aucun ne doit subsister
après arrêt. Les zombies ne sont pas des producteurs vivants. L'heure de
création est relue pour refuser une identité de thread incohérente. Aucun
cmdline, environnement, nom de commande, chemin ou PID n'est rendu au public.

L'observation exige le même namespace PID que systemd PID 1, un procfs sans
restriction hidepid/subset, au plus 32768 threads, 64 Kio par lecture et cinq
secondes. Une erreur de visibilité ou de lecture refuse la preuve. Une sortie
de thread pendant la lecture peut être ignorée ; aucune action ne cible ce PID.

Ce recensement n'empêche pas un administrateur ou un planificateur privilégié
de lancer ensuite un autre producteur. Un nouveau processus étranger invalide
la prochaine observation, sans être tué. Un collecteur, CLI ou frontal NGINX
partageant l'identité dédiée hors des deux unités est également refusé. Leur
coordination explicite appartient au prochain chantier. Ce n'est pas un
inventaire exhaustif de l'hôte, une isolation contre root ou une barrière SQL.

## Qualification limitée pour maîtriser le coût Actions

Demande de Bastien du 26 septembre 2026 : limiter les Actions au strict
nécessaire et arrêter après chaque chantier. Le candidat utilise une branche
`work/phase5-http-drain-20260926`, sans déclencher les trois campagnes globales.
Le workflow ciblé, limité à cette branche, lance un seul job Debian 13, un seul
conteneur officiel jetable, réseau coupé pendant les essais et timeout 10 min.

Contrôles prévus sur l'arbre gelé :

- contrôleurs HTTP/drainage/collecteur ciblés, localement puis dans le conteneur ;
- 18 recettes du runtime HTTP, dont les 12 historiques et six nouvelles :
  multipart partiel, descendant détaché, refus étranger avant maintenance,
  invalidation du reçu par un nouveau producteur, SIGKILL/reprise et dérive ;
- 11 recettes historiques de barrière cgroup ;
- 12 recettes du collecteur et conservation des règles de session.

Soit 41 scénarios système sous Debian 13. Les résultats et références exactes
seront conservés dans le checkpoint compagnon après gel, sans commit
documentaire postérieur modifiant l'arbre testé. Les contrôles locaux ne
remplacent pas ces essais : l'accès local au namespace PID 1 est refusé.

Quality globale, Debian 12, paquets, SQL, proxy, Web métier complet et navigateur
ne sont pas relancés pour ce chantier. Leurs résultats précédents qualifient
uniquement la base, pas le nouvel arbre. **Pas de promotion ni qualification
globale déclarée** tant que les campagnes requises n'ont pas été exécutées sur
la future source de promotion exacte. Aucune assertion historique n'est retirée.

## Reçu et suite

Le reçu `PROVISIONED_HTTP_SERVICES_DRAINED` porte deux services, des cgroups
vides et une observation ponctuelle de l'identité. Il conserve explicitement
`other_producers_controlled=false`, `storage_inventory_complete=false`,
`system_wiring_verified=false`, `complete_web_backup=false`,
`application_installed=false`, `service_activation_delivered=false` et
`phase5_complete=false`. Les neuf groupes de producteurs restent visibles dans
la cartographie. Le présent lot ne ferme ni 5C2 ni 5D.

Après checkpoint : arrêt pour redémarrage. Prochain chantier distinct proposé :
coordination du collecteur et des producteurs planifiés avec cette barrière,
sans inventer de service CLI ni prétendre couvrir les autres clients SQL.

Références : [contrat cgroup v2 du noyau](https://cdn.kernel.org/doc/html/latest/admin-guide/cgroup-v2.html),
[systemd.kill](https://www.freedesktop.org/software/systemd/man/systemd.kill.html).
