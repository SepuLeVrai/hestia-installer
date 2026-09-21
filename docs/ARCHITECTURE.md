# Architecture HESTIA Installer

## Responsabilité

`hestia-installer` orchestre les composants HESTIA. Il ne devient pas une copie des trois dépôts applicatifs.

```text
GitHub Project HESTIA
        |
        +-- hestia-nexus-avv       WEB
        +-- hestia-mobile-gateway  GATEWAY
        +-- hestia-apk             MOBILE
        `-- hestia-installer       INSTALLER
```

## Runtime cible

```text
install-hestia.sh
  -> bootstrap minimal

orchestrateur Python temporaire
  -> preflight
  -> plan
  -> transactions
  -> modules typés
  -> validations
  -> rapport

mini-web HTTPS temporaire
  -> UX
  -> collecte des décisions
  -> affichage des statuts
  -> aucune primitive shell arbitraire
```

Le HEAD courant possède déjà le modèle d'état transactionnel, le journal non secret, la validation des entrées et une maquette locale du mini-web. Ces éléments sont un socle, pas encore un installateur privilégié opérationnel.

## Port de staging

Le contrat cible réserve un port HTTPS aléatoire dans `57000-57999`.

La sélection finale utilisera un tirage cryptographique puis un bind réel avant affichage. Le port est temporaire et doit être fermé après arrêt du staging.

## Découpage de responsabilité

### Installer

- bootstrap ;
- orchestration ;
- plan/apply ;
- reprise et rollback ;
- UX de staging ;
- frontal NGINX/TLS si choisi ;
- validation globale ;
- rapport final.

### Web

Contrat : `SepuLeVrai/hestia-nexus-avv#135`.

Web reste propriétaire de son schéma, de ses migrations, de la création Admin, de sa Foundation et de ses moteurs d'import.

### Gateway

Contrat : `SepuLeVrai/hestia-mobile-gateway#4`.

Gateway reste propriétaire de son état SQLite, de son service systemd, de sa configuration et de ses mécanismes fresh/upgrade/rollback.

### Android

Contrat : `SepuLeVrai/hestia-apk#5`.

Le mode normal consomme un artefact déjà qualifié. La signature durable et Firebase ne doivent jamais être régénérés silencieusement.

## Dépendances de chantier

Ordre recommandé depuis l'issue parent #1 :

1. #2 bootstrap sécurisé ;
2. #4 moteur transactionnel ;
3. #3 wizard ;
4. Web #135 ;
5. Gateway #4 ;
6. #5 frontal HTTPS ;
7. APK #5 ;
8. #6 import/restauration ;
9. #7 packaging et Quality globale.

## Source de vérité

Le chantier de référence est l'issue [#1](https://github.com/SepuLeVrai/hestia-installer/issues/1). Les sous-issues définissent les frontières d'implémentation. Les états réellement présents dans les quatre dépôts priment sur une ancienne note de handoff.
