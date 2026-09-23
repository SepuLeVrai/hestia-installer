# Référence UX - mini-web HESTIA Installer

## Statut

La base Web du mini-installer matérialise désormais la direction visuelle validée dans `installer/web`.

Cette étape reste une maquette fonctionnelle non destructive : elle ne déclenche aucune mutation système et n'expose aucun endpoint privilégié.

## Direction retenue

- fenêtre centrale premium et sobre, cadre aminci et ombre adoucie ;
- illustration HESTIA locale à gauche ;
- contenu fonctionnel à droite ;
- palette ivoire, crème, brun, bordeaux et cuivre ;
- barre supérieure translucide ;
- arrière-plan reprenant l’illustration HESTIA locale, fortement floutée pour créer une profondeur visuelle sans ressource distante ;
- identité HESTIA et emblème du foyer ;
- progression visible ;
- boutons Précédent, Suivant et Annuler ;
- micro-interactions courtes ;
- responsive laptop, tablette et mobile ;
- footer et commandes toujours contenus dans la fenêtre à 100 % de zoom sur les hauteurs desktop/laptop usuelles ;
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

## Ajustements UX 2026-09-23

À la suite de la validation visuelle :

- le footer est désormais ancré dans la grille du panneau droit et ne peut plus déborder du cadre ;
- le mode compact desktop s'active jusqu'à 920 px de hauteur afin de conserver le rendu complet à 100 % de zoom ;
- les dimensions verticales du logo, du titre, des cartes et des actions ont été resserrées sans modifier la hiérarchie ;
- le cadre utilise une bordure unique et une ombre plus douce ;
- le fond reprend l’illustration HESTIA locale déjà embarquée et lui applique un flou gaussien fort, ce qui reste plus proche de la simulation validée ;
- aucune ressource réseau supplémentaire n'est requise ;
- les titres dynamiques du wizard sont injectés par `textContent`, sans `innerHTML`.

## Interaction de prévisualisation

La page contient un préambule puis cinq étapes visuelles de démonstration afin de valider le comportement du futur wizard :

0. Bienvenue
1. Accès GitHub
2. Préflight
3. Modules
4. Plan
5. Installation

L'étape 1 matérialise le prérequis d'accès aux dépôts privés HESTIA. Dans l'implémentation fonctionnelle, elle collectera un credential GitHub de lecture seule et validera l'accès aux trois dépôts applicatifs WEB, GATEWAY et APK. Le téléchargement effectif sera différé jusqu'à la sélection des modules afin de ne copier localement que les sources nécessaires. Le secret restera éphémère et ne sera pas persisté.

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
