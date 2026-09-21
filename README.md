# HESTIA Installer

Orchestrateur d'installation one-shot pour l'écosystème HESTIA.

Ce dépôt est le quatrième composant transverse du projet HESTIA. Il coordonne sans les dupliquer :

- WEB : `SepuLeVrai/hestia-nexus-avv`
- GATEWAY : `SepuLeVrai/hestia-mobile-gateway`
- MOBILE : `SepuLeVrai/hestia-apk`
- INSTALLER : `SepuLeVrai/hestia-installer`

## Cible

À terme, l'opérateur doit pouvoir lancer :

```bash
sudo ./install-hestia.sh
```

puis poursuivre toute l'installation dans un mini-web HTTPS temporaire :

```text
Debian supporté
  -> bootstrap local
  -> orchestrateur Python privilégié temporaire
  -> mini-web HTTPS sur port aléatoire 57000-57999
  -> wizard graphique
  -> plan
  -> apply transactionnel
  -> validation
  -> import/restauration facultatif
  -> rapport
  -> arrêt et nettoyage du staging
```

## Référence fonctionnelle

Le chantier global est suivi dans :

- Issue parent : [#1 - HESTIA One-Shot Installer R4](https://github.com/SepuLeVrai/hestia-installer/issues/1)

La simulation visuelle validée est jointe à cette issue.

## Principes

- le navigateur est le cockpit ;
- l'orchestrateur est le moteur système ;
- aucune API de shell arbitraire ;
- aucune duplication des moteurs WEB, GATEWAY ou MOBILE sans nécessité ;
- secrets absents de Git, logs, rapports et URL ;
- plan avant mutation ;
- transaction, rollback et resume ;
- mini-web supprimé en fin de chantier ;
- aucun port interne HESTIA/Gateway exposé par facilité.

## État du dépôt au 21 septembre 2026

Le dépôt contient le socle exécutable non destructif du futur installateur :

- CLI Python et preflight en lecture seule ;
- validation FQDN / CIDR ;
- modèle d'état transactionnel `PLANNED / RUNNING / DONE / ROLLED_BACK / FAILED / MANUAL_ACTION_REQUIRED` ;
- journal d'état non secret écrit atomiquement ;
- maquette locale du mini-web conforme à la direction UX validée ;
- aucun endpoint privilégié ni mutation système active ;
- aucune GitHub Action lourde à ce stade.

La Quality locale canonique `compileall + unittest` passe sur le HEAD courant avec 6 tests.

Le chantier est piloté par l'issue #1 et ses sous-issues #2 à #7. Les contrats applicatifs liés sont Web #135, Gateway #4 et APK #5.

## Développement local

Préflight local non destructif :

```bash
python3 -m installer --check
```

Tests locaux :

```bash
./scripts/quality-local.sh
```

Aucune GitHub Action lourde n'est activée dans ce dépôt au bootstrap initial. La Quality CI sera ajoutée lorsque le premier lot exécutable le justifiera.
