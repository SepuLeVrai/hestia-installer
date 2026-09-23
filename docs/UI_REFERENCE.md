# Référence UX - mini-web HESTIA Installer

## Statut

La base Web du mini-installer matérialise désormais la direction visuelle validée dans `installer/web`.

La Phase 1 ajoute un écran de déverrouillage HTTPS avant l'UX principale. Cet écran reprend strictement les mêmes composants visuels, palette, illustration et responsive afin de ne pas créer une seconde identité graphique.

## Direction retenue

- fenêtre centrale premium et sobre, cadre aminci et ombre adoucie ;
- illustration HESTIA locale à gauche ;
- contenu fonctionnel à droite ;
- palette ivoire, crème, brun, bordeaux et cuivre ;
- barre supérieure translucide ;
- arrière-plan reprenant l'illustration HESTIA locale, fortement floutée pour créer une profondeur visuelle sans ressource distante ;
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

## Fichiers UI

```text
installer/web/bootstrap.html
installer/web/index.html
installer/web/assets/bootstrap.js
installer/web/assets/installer.css
installer/web/assets/installer.js
installer/web/assets/hestia-hero.webp
```

L'illustration est embarquée localement. L'interface n'effectue aucun chargement réseau externe pour son rendu.

## Écran bootstrap HTTPS

Avant le wizard, `/bootstrap` demande le code temporaire affiché dans le terminal.

Règles UX :

- même fenêtre, même illustration et même identité HESTIA ;
- champ de type `password` ;
- `autocomplete="off"` ;
- le champ est vidé immédiatement après la tentative ;
- erreurs génériques sans reprise du secret ;
- aucune persistance navigateur ;
- après succès, redirection vers `/` et affichage de l'UX principale.

## Wizard

La page contient un préambule puis cinq étapes visuelles de démonstration afin de valider le comportement du futur wizard :

0. Bienvenue
1. Accès GitHub
2. Préflight
3. Modules
4. Plan
5. Installation

L'étape 1 matérialise le prérequis d'accès aux dépôts privés HESTIA. Dans l'implémentation fonctionnelle, elle collectera un credential GitHub de lecture seule et validera l'accès aux trois dépôts applicatifs WEB, GATEWAY et APK. Le téléchargement effectif sera différé jusqu'à la sélection des modules afin de ne copier localement que les sources nécessaires. Le secret restera éphémère et ne sera pas persisté.

## Contraintes d'intégration

- conserver la structure visuelle actuelle ;
- aucune primitive shell arbitraire ;
- CSP, CSRF, `no-store`, cookies sécurisés et isolation des secrets ;
- tous les assets nécessaires restent locaux ;
- aucune donnée secrète dans le DOM après soumission ;
- `textContent` pour les textes dynamiques non fiables ;
- navigation et erreurs restent utilisables au clavier.

## Référence de conception

L'issue parent reste la source fonctionnelle du chantier :

- https://github.com/SepuLeVrai/hestia-installer/issues/1

La présente implémentation Web est la référence concrète pour la composition, la hiérarchie visuelle et le responsive du wizard.
