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
  -> accès GitHub éphémère
  -> acquisition locale des sources
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

## Acquisition des sources GitHub

HESTIA Installer reste volontairement léger : il n'embarque pas les dépôts applicatifs complets.

Après l'écran de bienvenue, l'étape 1 demande un credential GitHub capable de lire les dépôts privés nécessaires. Le mode recommandé est un fine-grained personal access token limité aux dépôts HESTIA et aux permissions de lecture strictement nécessaires.

Le credential est utilisé uniquement pendant la session pour :

- vérifier l'accès aux dépôts requis ;
- résoudre la branche ou le commit demandé ;
- télécharger localement les sources nécessaires dans le staging privé ;
- enregistrer dans le plan uniquement les SHA de commits et hashes non secrets.

Le téléchargement doit privilégier l'API GitHub et la standard library Python afin d'éviter d'imposer `git` comme dépendance du bootstrap. Les sources téléchargées sont extraites dans un staging privé avec contrôle des chemins avant extraction.

Le token n'est jamais écrit dans Git, le state JSON, les logs, les URLs, les arguments de processus, les rapports ou les artefacts. Un resume doit demander à nouveau le credential si un nouvel accès GitHub est nécessaire.

## Port de staging

Le contrat cible réserve un port HTTPS aléatoire dans `57000-57999`.

La sélection finale utilisera un tirage cryptographique puis un bind réel avant affichage.

## Source de vérité

Le chantier de référence est l'issue [#1](https://github.com/SepuLeVrai/hestia-installer/issues/1).
