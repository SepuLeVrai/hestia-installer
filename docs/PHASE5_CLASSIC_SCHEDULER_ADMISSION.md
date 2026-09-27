# Admission des planificateurs classiques dans le profil provisionné

## Base et objectif

Parent qualifié : `7d839437ea512e79a17116ecd20c4ccfe4551cd7`, arbre
`17a6db587ef5878401f5062cc9a763a7956ec7e5`, run `36308302494` : 75 contrôles
locaux, 107 Debian 13 et 13 scénarios intégrés. Les résultats de ce parent
ne qualifient pas ce nouveau code.

Le parcours provisionné refuse désormais les empreintes connues de cron,
cronie, anacron, atd, fcron, bcron et systemd-cron. Ce garde-fou ferme un cas
concret : un lanceur installé ou chargé, même sans processus vivant, pouvait
passer le seul census d'identité. Il ne certifie pas tous les lanceurs de l'hôte.

## Comportement et compatibilité

Le premier contrôle précède l'acquisition du gate et tout arrêt de service.
Un refus laisse donc les services, la maintenance et les sauvegardes intacts.
Le coordinateur exige ensuite l'observation typée et la réévalue à ses points
de cohérence, notamment après la copie, avant certification et sous verrou SQL.
Une apparition observée après réservation conserve un essai incomplet, sans
reçu vérifié ; le verrou SQL est libéré et la maintenance reste requise.

Le profil dédié accepte uniquement l'absence des empreintes classiques listées.
Il refuse aussi un planificateur installé mais désactivé ou masqué : cet état
n'est pas une preuve d'absence de producteur futur. Il ne supprime, ne masque,
ne désactive et n'exécute aucun planificateur. Une installation Debian générale
avec cron déjà présent ne satisfait pas ce profil. Son adoption ou sa mise en
quiescence sera une opération distincte ; aucune suppression automatique n'est
proposée. Le nettoyage natif PHP et le collecteur HESTIA restent inchangés.

## Observations exactes

- Entrées protégées, sans lecture du contenu : exécutables usuels dans
  `/usr/sbin`, générateur `/usr/lib/systemd/system-generators/systemd-crontab-generator`,
  `/etc/crontab`, `/etc/anacrontab`, `/var/spool/cron` et `/var/spool/at`.
  Toute présence est refusée, y compris répertoire vide, FIFO ou lien pendant.
  Un parent lié, modifiable ou illisible ne devient jamais une absence.
- Bus système existant : client borné déjà qualifié, sans auto-démarrage ni
  autorisation interactive, identité locale/PID 1/broker/bus vérifiée avant et
  après. Deux populations distinctes sont nécessaires : `ListUnits` pour les
  unités chargées/transitoires et `ListUnitFiles` pour les fichiers installés.
- Les familles de noms exactes et leurs suffixes `-…` ou instances `@…` sont
  refusées quel que soit leur état. Les noms et chemins sont validés. Aucune
  commande `GetUnit`, `LoadUnit`, `systemctl show`, `start`, `stop` ou
  `daemon-reload` n'est émise par cette admission. Les descriptions sont écartées.
- Les diagnostics publics sont fermés : `PROVISIONED_CLASSIC_SCHEDULER_REJECTED`
  ou `PROVISIONED_SCHEDULER_OBSERVATION_UNAVAILABLE`. Aucun contenu de crontab,
  nom privé, argument ou secret n'est exposé.

## Preuve et limites

La preuve expose `classic_scheduler_absence_observed=true` et conserve
`host_scheduler_inventory_complete=false`, `foreign_cli_controlled=false`.
Le manifeste précise qu'il s'agit d'observations répétées. Ce n'est pas un
verrou du plan de contrôle de l'hôte : une apparition puis disparition entre
deux observations n'est pas couverte. Les programmes déplacés ou renommés,
autres services/timers/path/socket natifs, gestionnaires utilisateurs, jobs
d'autres ordonnanceurs et commandes administratives restent non admis comme
producteurs maîtrisés. Aucun parsing lexical de shell n'est présenté comme
une preuve de destination d'écriture.

Les flags globaux de 5C2/phase 5, sauvegarde exhaustive, apply et rollback restent
faux. Le prochain chantier reste le raccordement ou le refus effectif des CLI
et planificateurs natifs non couverts, puis la couverture données/configuration.
Ne pas reprendre le développement générique Exec*Ex différé.

## Recettes prévues sur le gel

85 contrôles locaux et 117 Debian 13, avec 10 contrôles supplémentaires sans
retrait. Les 17 scénarios intégrés comprennent les 13 acquis que ce parcours
traverse et quatre ajouts : timer installé inactif, unité transitoire active,
spool présent sans lecture ni suppression, et crontab apparue après copie.
Ces comptes sont des attentes jusqu'au checkpoint du commit exact. Un seul
job ciblé après gel code/documentation ; pas de nouvelle Quality globale.

## Sources primaires consultées le 27 septembre 2026

- [cron Debian 13](https://manpages.debian.org/trixie/cron/cron.8.en.html) : crontabs utilisateurs, table système et cron.d.
- [systemd-cron Debian 13](https://manpages.debian.org/trixie/systemd-cron/systemd.cron.7.en.html) : générateur et familles d'unités.
- [Fichiers du paquet at Debian 13](https://packages.debian.org/trixie/amd64/at/filelist) : exécutable atd et unité native.
- Le transport D-Bus et ses bornes sont ceux de [la découverte qualifiée](PHASE5_SYSTEMD_DISCOVERY_TRANSPORT.md).
