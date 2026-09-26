# HESTIA Installer

Phase5 en cours : [relations et candidats du census vivant](docs/PHASE5_CENSUS_RELATIONS.md).
Sélection privée de revue ; aucune clôture globale ni promotion.

Phase5 en cours : [census et invocations](docs/PHASE5_CENSUS_INVOCATIONS.md),
observations privées pour revue, sans promotion ni clôture globale.

## Reprise active — paquets système officiels

Le [lot paquets](docs/PHASE5_SYSTEM_PACKAGES.md) acquiert un plan Debian authentifié
puis installe ses versions exactes hors réseau, avec services par défaut masqués.
Adaptateurs privés uniquement ; activation Web et Phase 5 restent ouvertes.
Lire le [handoff](docs/HANDOFF_WORK_20260925.md) et les preuves du commit exact.
Les sections suivantes conservent l'historique.

## Reprise actuelle : 5C2a, puis 5C2b

Sauvegarde privée du profil reconnu et restauration SQL/fichiers réellement
vérifiée sur cible isolée : [contrat](docs/PHASE5C2_BACKUP.md).
**5C2 reste ouverte** : DEFINER orphelins du fresh managed détectés et refusés,
correction explicite à traiter en5C2b avant5C3. Aucun upgrade, serveur ou écran
nouveau. Lire [le handoff](docs/HANDOFF_WORK_20260925.md) et les preuves du commit.
Les sections plus anciennes ci-dessous décrivent les frontières précédentes.


Orchestrateur d'installation one-shot pour l'écosystème HESTIA.

Ce dépôt est le quatrième composant transverse du projet HESTIA. Il coordonne sans les dupliquer :

- WEB : `SepuLeVrai/hestia-nexus-avv`
- GATEWAY : `SepuLeVrai/hestia-mobile-gateway`
- MOBILE : `SepuLeVrai/hestia-apk`
- INSTALLER : `SepuLeVrai/hestia-installer`

## État de livraison

Le wizard public s'arrête à « Sources prêtes ». La frontière privée 5B est livrée.
5C1 ajoute un précontrôle SQL réel et un rapport non exécutable ; les sauvegardes,
l'upgrade et le rollback restent 5C2 à 5C4. Les services et écrans restent 5D.
Consulter [l'état du projet](docs/PROJECT_STATE.md), le
[découpage 5C](docs/PHASE5C_UPGRADE.md) et le [handoff WORK](docs/HANDOFF_WORK_20260925.md).

## Cible

L'opérateur lance :

```bash
sudo ./install-hestia.sh
```

Le bootstrap vérifie l'hôte, réserve un port HTTPS aléatoire dans `57000-57999`, génère un certificat éphémère et un code d'accès temporaire, puis ouvre le mini-web HESTIA.

```text
Debian supporté
  -> bootstrap local minimal
  -> IPv4 d'administration
  -> port HTTPS 57xxx réservé par bind réel
  -> certificat TLS éphémère avec SAN IP
  -> code bootstrap à usage unique
  -> session navigateur sécurisée
  -> wizard graphique
  -> accès GitHub éphémère
  -> acquisition sélective des sources
  -> plan
  -> apply transactionnel
  -> validation
  -> import/restauration facultatif
  -> rapport
  -> arrêt et nettoyage du staging
```

## Bootstrap HTTPS - Phase 1

La première frontière exécutable est implémentée.

Fonctions disponibles :

- Debian 12 et 13 ;
- Python 3.11+ ;
- vérification `openssl` et `iproute2` ;
- staging privé sous `/run/hestia-installer` ;
- détection IPv4 d'administration ;
- choix interactif si plusieurs IPv4 privées sont disponibles ;
- refus par défaut d'exposer le bootstrap sur une IPv4 publique ;
- fallback loopback + tunnel SSH ;
- port aléatoire `57000-57999` réservé avant annonce ;
- certificat TLS auto-signé éphémère avec SAN IP ;
- code `HST-XXXX-XXXX`, 10 minutes, usage unique, 5 essais maximum ;
- cookie de session `Secure`, `HttpOnly`, `SameSite=Strict` ;
- CSP, CSRF, `no-store`, contrôle `Host` et `Origin` ;
- limites de taille des requêtes ;
- chemins statiques protégés contre les traversées et symlinks ;
- nettoyage sur arrêt normal, Ctrl+C et SIGTERM ;
- vérification de fermeture du port après arrêt.

Un certificat auto-signé est volontairement utilisé pour ce bootstrap temporaire. Le navigateur affichera donc un avertissement de confiance lors de la première connexion.

## Utilisation

Préflight non destructif :

```bash
./install-hestia.sh --check
```

Démarrage normal :

```bash
sudo ./install-hestia.sh
```

Forcer une IPv4 locale détectée :

```bash
sudo ./install-hestia.sh --bind-address 192.168.10.42
```

L'exposition sur une IPv4 publique reste bloquée par défaut. Elle demande à la fois une adresse explicitement choisie et l'option avancée :

```bash
sudo ./install-hestia.sh --bind-address <ipv4-publique> --allow-public-bootstrap
```

Ce mode n'est pas recommandé. Si le serveur ne dispose que d'une IPv4 publique, le comportement normal est un bind sur `127.0.0.1` et l'utilisation d'un tunnel SSH.

## Référence fonctionnelle

Le chantier global est suivi dans :

- Issue parent : [#1 - HESTIA One-Shot Installer R4](https://github.com/SepuLeVrai/hestia-installer/issues/1)
- Bootstrap HTTPS : [#2](https://github.com/SepuLeVrai/hestia-installer/issues/2)

## Principes

- le navigateur est le cockpit ;
- l'orchestrateur est le moteur système ;
- aucune API de shell arbitraire ;
- aucune duplication des moteurs WEB, GATEWAY ou MOBILE sans nécessité ;
- secrets absents de Git, logs, rapports, URL et arguments de processus ;
- plan avant mutation ;
- transaction, rollback et resume ;
- mini-web supprimé en fin de chantier ;
- aucun port interne HESTIA/Gateway exposé par facilité.

## Développement local

```bash
./scripts/quality-local.sh
```

La Phase 1 ne modifie ni `schema.sql` ni `install.php` et n'effectue aucune mutation HESTIA, MariaDB, Apache, NGINX, Gateway ou APK.

## Quality permanente

Les commandes, la matrice Debian 12/13, les tests navigateur natifs et les limites
sont décrits dans [docs/QUALITY.md](docs/QUALITY.md). Le workflow Installer Quality
publie des preuves et un ZIP du code exact testé uniquement après réussite des
contrôles requis. Pour reprendre le chantier avant la Phase 5, consulter
[docs/PREREQUISITES_20260924.md](docs/PREREQUISITES_20260924.md).
