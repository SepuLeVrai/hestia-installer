# Modèle de sécurité

## Principe principal

Le mini-web ne doit jamais être un shell root présenté dans un navigateur.

INTERDIT :

```text
POST /run-command
command=<entrée utilisateur>
```

Les opérations privilégiées sont des fonctions connues, validées et testées.

## Bootstrap cible

Le bootstrap final combine :

- HTTPS dès le premier écran ;
- certificat auto-signé éphémère avec SAN IP ;
- port aléatoire 57000-57999 réservé par bind ;
- token bootstrap à usage court ;
- session HttpOnly + Secure + SameSite=Strict ;
- CSRF ;
- CSP ;
- no-store ;
- arrêt complet en fin d'installation.

## Secrets

Interdits dans Git, arguments de processus, logs, URLs, state JSON non secret, rapports et artefacts.

## CI

Aucune GitHub Action lourde n'est installée dans le bootstrap initial du dépôt.
