# Phase 5D6 — Persistance du serveur dédié au démarrage

Ce lot suit le gel 5D5b. Il conserve ses préparations MariaDB/fresh, les cinq
étapes d'activation, leurs empreintes, les unités natives et leurs drop-ins.
Aucune campagne dédiée acquise n'est relancée pour récupérer du contexte.
Le raccordement concerne le serveur vierge Debian 13 préparé par ces journaux.
Un serveur SQL externe/préparé par un tiers et l'adoption d'un ancien boot ne
sont pas autorisés implicitement par ce profil.

## Plan distinct dans le wizard

Après les journaux SQL, fresh et activation DONE, le wizard propose deux étapes :
préparer les contrôles puis raccorder la cible dédiée à `multi-user.target`.
Le journal privé `boot/state.json` possède son propre consentement et le verrou
parent sérialise ses mutations. Les POST `/api/system/boot/{plan,apply,resume,retry,check}`
utilisent les protections HTTPS/session/Origin/CSRF existantes. L'utilisateur
ne fournit aucun chemin, nom d'unité ou commande. Annulation et rechargement
ne déclenchent aucun effet. GET et rapports sont des lectures de fichiers.

Le profil lie les trois plans parents, la configuration publique persistée et
les octets du code des lecteurs natifs. Une copie privée indépendante du
répertoire temporaire de l'Installer est installée avec ses templates. Elle
ne contient ni mot de passe opérateur ni clé. Le worker vérifie toutes ses
empreintes avant import ; les fichiers sont root:root 0600, les répertoires
privés 0700, sans lien suivi. Un changement de version demande un nouveau
contrat explicite ; un ancien plan n'est pas adopté avec du code différent.

## Ordre et admission

Des unités nouvelles et des liens `.requires` dédiés imposent : contrôle SQL,
MariaDB, contrôle de disponibilité SQL et d'admission Web, PHP/Apache/timer.
Les dépendances d'ordre s'inversent à l'arrêt. Les liens n'altèrent ni les octets
ni les drop-ins des unités acquises. Le bus système est une dépendance explicite
du contrôle Web. La cible est raccordée par un lien exclusif, sans `enable --now`.
La configuration ne démarre, ne redémarre et n'arrête aucun service existant.

Au boot, les lecteurs natifs vérifient configuration, identités, sources et
sceaux. La sonde SQL utilise exclusivement le socket local root, sans secret
opérateur. Le Web exige l'admission SERVING et son reçu ACTIVITY_RESUMED ; une
maintenance plus récente reste fermée. Le worker ne supprime jamais sa barrière,
ne lance aucun fresh, ne crée aucun compte et ne répare aucune dérive. Pendant
les jobs systemd de démarrage, les lecteurs de configuration hors ligne sont
utilisés : les contrôles natifs de services déjà au repos ne sont pas assouplis.
PHP et le collecteur gardent aussi leur barrière native à chaque activité.

## Coupures et observations

Chaque étape écrit une intention privée avant effet et un reçu final après
`daemon-reload`. Une réponse perdue après effet complet est reconnue en lecture,
sans recréer de lien ni recharger à nouveau le manager. Fichiers partiels,
liens étrangers et absence du reçu final restent manuels. Un lien de boot rendu
visible juste avant une coupure ne suffit pas : les gardes exigent le reçu final.
Le journal DONE décrit l'enrôlement historique ; le bouton de vérification
contrôle explicitement configuration et disponibilité locale actuelles.
Il n'est pas une surveillance ni une preuve de TLS public.

## Qualification

Les tests locaux ne font que des fichiers et des doubles d'effets. La nouvelle
recette jetable prépare la chaîne acquise comme fixture, puis teste le navigateur
et deux réponses perdues, l'absence de redémarrage lors de l'enrôlement, un nouveau
PID 1 avec `/run` vidé et login réel, puis trois boots bloqués : maintenance,
dérive FPM et reçu final manquant. Elle compare parents, identités administrateur,
unités et sources épinglées. Le nouveau démarrage systemd est réel dans un
conteneur ; le noyau du runner reste partagé, ce n'est pas une recette de
redémarrage du noyau ou de coupure électrique d'une machine physique.

Le frontal public/TLS et la recette finale de phase 5 restent à terminer.
`application_installed` et `phase5_complete` restent faux. La recette de TLS de
fixture utilisée pour le login ne vaut pas frontal public. Phase 5 et #13 restent
ouverts jusqu'à cette dernière étape et à la revue finale Quality/documentation.
