# Prérequis D-Bus du provisionnement

## Raccordement fonctionnel du 27 septembre 2026

Base acquise : `fb1717da471f1e27bb55a7bd787dcb4c67bf013d`. Ce lot ferme
le prérequis auparavant préparé par le banc : le plan de paquets officiel
demande explicitement `dbus`, puis l'installation appelle `system_bus.ensure`
avant d'écrire son reçu. Le reçu installé exige également une observation
réussie du bus. Apache, PHP-FPM, MariaDB et le proxy restent masqués ; seul
le bus système natif peut être démarré par cette étape.

Le paquet et ses dépendances utilisent le même téléchargement authentifié,
les mêmes versions figées et la même installation hors réseau. Aucun dépôt
tiers, mise à niveau, suppression ou changement de configuration APT n'est ajouté.
Un ancien plan acquis sans cette dépendance ne correspond plus au code : il
ne doit pas être rejoué ou modifié. Les empreintes incomplètes restent à examiner.

## Cycle de vie et installations existantes

`ensure(confirmed=True)` est une opération explicite du provisionnement.
Si le service est inactif, elle démarre uniquement `dbus.service`, avec
une commande fixe sans shell, sans autorisation interactive et sans réessai.
Si le service est actif, elle conserve son identité complète, PID, socket,
identité du bus et propriétaire systemd compris. Une dérive refuse le résultat.

`observe()` reste en lecture seule pour l'existant et la récupération d'un
reçu. Un bus arrêté ne provoque aucun démarrage implicite. La récupération
du moteur reste manuelle tant que ce prérequis n'est plus disponible ; un
`ensure` explicite peut rétablir un service natif simplement inactif.
Il ne s'agit pas d'une migration Web ni d'une preuve de bascule de version.

Aucun arrêt, redémarrage, démasquage, activation persistante, daemon-reload,
remplacement de fichier ou réparation de bus n'est émis par cet adaptateur.
Les liens de démarrage au boot sont ceux du paquet Debian. Les appels de
découverte et de sauvegarde existants restent strictement en lecture seule ;
ils ne deviennent pas des points de démarrage de services.

## Sécurité et refus

Le profil est limité à Debian 12/13, root, systemd PID 1 et aux espaces de noms
déjà contrôlés par le transport. Les fichiers du service, du socket et du
binaire dbus-daemon doivent être protégés et correspondre à leurs empreintes
enregistrées par dpkg. La compatibilité `/lib` de Bookworm est explicite.
Ces empreintes détectent une dérive ; elles ne remplacent pas l'authentification
APT et ne constituent pas une protection contre un administrateur root hostile.

Unités masquées, substitutions, drop-ins, rechargement requis, jobs en cours,
états en échec ou transitoires sont refusés. Le transport confirme ensuite le
vrai dbus-daemon dans son cgroup, le socket local et le gestionnaire UID 0/PID 1.
Un bus actif incompatible n'est jamais remplacé. Les diagnostics sont fermés,
sans sorties de commande, noms privés, secrets ou lignes de configuration.

Un démarrage qui échoue ou ne peut être confirmé laisse l'installation
incomplète, sans reçu installé. Aucun rollback de paquets ou arrêt d'un bus
partagé n'est tenté. Un échec d'écriture du reçu après démarrage ne certifie
pas les paquets et conserve la procédure d'inspection manuelle existante.

## Qualification ciblée

Le gel comporte 35 contrôles locaux : 24 contrôles paquets conservés et
11 nouveaux contrôles de cycle de vie, dérive, confidentialité et refus.
Le banc prévu utilise une seule campagne, deux conteneurs Debian indépendants :
Debian 13 sans D-Bus préinstallé et Debian 12 avec un bus déjà actif.
L'installation est réalisée réseau déconnecté dans les deux cas.

18 scénarios par Debian sont attendus : 5 d'acquisition, 12 d'installation
et d'état existant, 1 d'interruption du reçu. Les cinq ajouts vérifient
la conservation du bus actif, le refus d'un masque, d'un drop-in et d'un
fichier vendor altéré, ainsi que le démarrage explicite d'un bus arrêté.
Les 13 cas paquets acquis restent exigés, y compris le runtime HTTP/collecteur
sur les dépendances réellement installées. Aucun skip n'est admis.
Les résultats du commit exact sont consignés au checkpoint après exécution.

Pas de changement SQL, `schema.sql`, `install.php`, UI, Web produit, APK ou
Gateway. La maîtrise des CLI et planificateurs natifs reste le prochain
chantier. 5C2, 5C3, 5C4, 5D et la Quality globale restent ouverts ; aucun
indicateur global ou autorisation de promotion n'est déduit de ce lot.

## Références primaires

- [D-Bus Bookworm](https://packages.debian.org/bookworm/amd64/dbus/filelist)
- [D-Bus Trixie](https://packages.debian.org/trixie/amd64/dbus/filelist)
- [Socket Trixie](https://packages.debian.org/trixie/all/dbus-system-bus-common/filelist)
- [Limites de la vérification dpkg](https://manpages.debian.org/trixie/dpkg/dpkg.1.en.html)
