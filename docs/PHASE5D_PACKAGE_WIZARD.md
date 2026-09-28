# 5D5a — dépendances du serveur vierge depuis le wizard

Ce lot compose le contrôleur de [paquets officiels](PHASE5_SYSTEM_PACKAGES.md)
déjà qualifié. Il conserve les contrôleurs natifs et les plans 5D1–5D4. Le hero,
le CSS et la navigation du wizard restent conservés.

## Parcours et consentements

Le panneau « Préparer les dépendances d'un serveur vierge » est disponible aux
étapes Accès GitHub et Préflight, sans imposer un jeton GitHub pour les paquets
Debian. Ce parcours fermé demande Debian 13, root, puis systemd PID 1 pour
l'installation. La qualification réelle porte sur amd64. Debian 12 reste
qualifié pour le contrôleur natif, mais ne satisfait pas PHP >= 8.3 du Web épinglé
et n'est pas proposé par cette composition.

L'opérateur choisit seulement l'inclusion éventuelle de nginx. Un identifiant
aléatoire et un profil privé immuable fixent le cache et ce choix ; aucun chemin,
paquet, dépôt, version ou commande arbitraire n'est accepté par l'HTTP.

1. Le premier plan n'effectue que les contrôles de lecture. Après confirmation
   de son digest, APT acquiert les index signés et les archives officielles.
2. Une action distincte prépare le plan d'installation, lié au plan d'acquisition
   DONE et au SHA-256 natif des archives. Le wizard présente les noms, versions,
   architectures et volume des archives, conservés dans une sélection privée.
3. L'installation exige la confirmation du second digest. Elle consomme les
   versions exactes hors réseau. Les contrôles natifs de fraîcheur, signatures,
   empreintes, état dpkg, policy et masques sont conservés. Le bus système est
   préparé ; Apache, FPM, MariaDB et nginx par défaut restent masqués.

Le reçu « Installation des paquets validée » est historique. Il ne revendique
ni MariaDB configuré, ni HESTIA installé, ni TLS, ni disponibilité actuelle.
Les flags `application_installed` et `mariadb_ready` restent faux. Les postinst
Debian peuvent créer un datadir SQL neuf ; aucun SQL applicatif n'est exécuté.

## Journaux et reprise

Deux journaux privés `packages/acquire/state.json` et `packages/install/state.json`
restent distincts du journal Web. `profile.json` et `selection.json` sont des
documents canoniques bornés, exclusifs, privés et non secrets. La sélection
complète est liée par son empreinte au second StepSpec ; les registres sont
comparés intégralement avant exécution. Un profil incomplet ou altéré n'est pas
écrasé. Le verrou du journal principal sérialise les mutations des paquets,
y compris entre processus ; un plan principal existant bloque ces mutations.
Les actions paquets doivent donc être terminées avant de figer un plan principal.

Le rafraîchissement, le redémarrage de la façade et le rapport lisent uniquement
ces fichiers. Aucun APT, dpkg, systemctl, SQL ou réseau n'est lancé par un GET.
Le rapport HTTPS et `--report` incluent les deux journaux lorsqu'ils existent.
Une réponse perdue après succès est récupérable par observation native sans
rejouer APT. Une installation partielle ou un reçu natif absent reste manuel.
Il n'existe ni réparation forcée, ni désinstallation, ni démasquage implicite.

## Qualification du lot

Douze contrôles core supplémentaires couvrent les données fermées, les deux
consentements, la reconstruction, la dérive, les fichiers privés, les verrous
et l'absence d'effets des lectures. Ils n'exécutent aucun effet système réel.

La recette dédiée utilise une Debian 13 minimale sans dépendance applicative,
un navigateur Chromium dans un conteneur séparé et quatre scénarios : acquisition
réelle depuis le wizard, redémarrage du bootstrap puis installation depuis le
wizard hors réseau, réponse perdue après installation réelle, et échec de reçu
privé après installation réelle. Le navigateur partage uniquement l'espace
réseau du conteneur cible ; aucun port n'est publié, aucun montage hôte ni
credential utilisateur n'est fourni. Sources et modes sont vérifiés avant/après.
Les gates générales Quality/système/paquets restent requises sur le gel final.
Les anciennes campagnes dédiées 5C4 et 5D1–5D4 ne sont pas rejouées.

## Suite

5D5b doit préparer MariaDB et les autorités SQL locales, puis enchaîner le fresh
qualifié. La persistance au boot et le frontal public/TLS suivent séparément.
Ce lot ne clôture pas la phase 5 et ne promeut aucune branche active.
