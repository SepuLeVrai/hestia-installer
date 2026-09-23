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

### Credential GitHub

Le credential de lecture des dépôts HESTIA est un secret éphémère.

Règles obligatoires :

- privilégier un fine-grained personal access token limité aux dépôts HESTIA ;
- permissions minimales en lecture seule ;
- transmission uniquement dans le corps d'une requête HTTPS authentifiée du mini-web vers l'orchestrateur ;
- aucune query string et aucune URL contenant le token ;
- aucune persistance dans localStorage, sessionStorage, IndexedDB ou cookie ;
- aucune écriture dans le journal de reprise ;
- aucun passage en argument de commande ou dans une URL de clone ;
- pas de stockage dans `.git/config` ;
- conservation uniquement le temps nécessaire en mémoire, ou dans un fichier runtime privé 0600 si une frontière technique l'impose ;
- effacement du secret dès que les téléchargements nécessaires sont terminés ;
- en cas de resume nécessitant un nouvel accès GitHub, demander à nouveau le credential.

La validation ne repose pas sur le format du token mais sur un accès effectif en lecture aux dépôts requis.

## CI

Aucune GitHub Action lourde n'est installée dans le bootstrap initial du dépôt.
