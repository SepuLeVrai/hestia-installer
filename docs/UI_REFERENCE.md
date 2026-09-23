# Référence UX - mini-web HESTIA Installer

## Statut

La base Web du mini-installer matérialise désormais la direction visuelle validée dans `installer/web`.

Cette étape reste une maquette fonctionnelle non destructive : elle ne déclenche aucune mutation système et n'expose aucun endpoint privilégié.

## Direction retenue

- fenêtre centrale premium et sobre ;
- illustration HESTIA locale à gauche ;
- contenu fonctionnel à droite ;
- palette ivoire, crème, brun, bordeaux et cuivre ;
- barre supérieure translucide ;
- identité HESTIA et emblème du foyer ;
- progression visible ;
- boutons Précédent, Suivant et Annuler ;
- micro-interactions courtes ;
- responsive laptop, tablette et mobile ;
- navigation clavier et focus visibles ;
- prise en charge de `prefers-reduced-motion` ;
- aucun CDN ;
- aucun asset distant requis au runtime.

## Fichiers de la maquette

```text
installer/web/index.html
installer/web/assets/installer.css
installer/web/assets/installer.js
installer/web/assets/hestia-hero.webp
```

L'illustration est embarquée localement. L'interface n'effectue aucun chargement réseau pour son rendu.

## Interaction de prévisualisation

La page contient cinq étapes visuelles de démonstration afin de valider le comportement du futur wizard :

1. Bienvenue
2. Préflight
3. Modules
4. Plan
5. Installation

Les boutons et les points de progression modifient uniquement l'état local du navigateur. Le bouton Annuler ouvre une boîte de dialogue locale. Aucun appel backend n'est effectué.

## Contraintes d'intégration futures

Lors de la connexion au bootstrap sécurisé :

- conserver la structure visuelle actuelle ;
- remplacer l'état de démonstration par les données de session du wizard ;
- ne jamais ajouter de primitive shell arbitraire ;
- conserver CSP, CSRF, `no-store`, cookies sécurisés et isolation des secrets prévus par le modèle de sécurité ;
- conserver tous les assets nécessaires dans le package local de l'installer.

## Référence de conception

L'issue parent reste la source fonctionnelle du chantier :

- https://github.com/SepuLeVrai/hestia-installer/issues/1

La présente implémentation Web est la référence concrète pour la composition, la hiérarchie visuelle et le responsive du wizard.
