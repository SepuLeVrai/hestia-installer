# Bootstrap HTTPS temporaire

## But

Fournir un canal temporaire sécurisé entre le terminal root et le navigateur, avant toute mutation HESTIA.

## Démarrage

```bash
sudo ./install-hestia.sh
```

Exemple de sortie :

```text
[PASS] IPv4 d'administration : 192.168.10.42
[PASS] Port HTTPS temporaire réservé : 57381
[PASS] Certificat temporaire généré
[PASS] Orchestrateur prêt

Continuer l'installation :
https://192.168.10.42:57381

Code d'accès temporaire :
HST-XXXX-XXXX
```

## Cas serveur public

Si aucune IPv4 privée n'est disponible, le serveur écoute uniquement sur `127.0.0.1`.

Depuis le poste d'administration :

```bash
ssh -L 57381:127.0.0.1:57381 <user>@<serveur>
```

Puis ouvrir :

```text
https://127.0.0.1:57381
```

Le numéro réel est celui affiché par l'installer.

## Authentification

1. ouvrir l'URL HTTPS ;
2. accepter le certificat auto-signé temporaire ;
3. saisir le code `HST-XXXX-XXXX` affiché dans le terminal ;
4. le code est invalidé ;
5. une session navigateur sécurisée est créée ;
6. le wizard HESTIA devient accessible.

## Arrêt

Ctrl+C ou SIGTERM déclenche :

1. arrêt du serveur ;
2. fermeture du listener ;
3. suppression des sessions mémoire ;
4. suppression du certificat et de la clé ;
5. suppression du staging ;
6. contrôle que le port n'écoute plus.

## Dépannage

### Plusieurs interfaces

L'installer propose les IPv4 privées plausibles avec la route par défaut présélectionnée.

### Port occupé

Le port n'est jamais incrémenté séquentiellement. Un nouveau port est tiré aléatoirement et bindé jusqu'à réussite ou épuisement de la limite de tentatives.

### Code refusé

Le code :

- expire après 10 minutes ;
- ne fonctionne qu'une fois ;
- est verrouillé après 5 erreurs.

Relancer le bootstrap pour générer un nouveau code.

### Navigateur avertit sur le certificat

Comportement attendu pour le bootstrap temporaire. Le certificat est généré localement et auto-signé. Il n'est pas réutilisé par l'installation finale.
