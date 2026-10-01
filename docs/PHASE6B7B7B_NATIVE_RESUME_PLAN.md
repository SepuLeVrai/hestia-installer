# 6B7b7b - raccordement natif du plan de reprise

Base exacte : 6B7b7a `349345a0dd7d50621c4ad2dc884701333aa905c6`, arbre
`f40485352a6f38875c8b1b36b51578510cbe3fbd`. Les trois campagnes Installer
36866015561 / 36866015533 / 36866015421 sont acquises. Le candidat conserve
les 40 contrats du plan et tous les modules de production sans modification.

## Blocage constaté à la reprise

La recette native 6B7b6b 36863158334 a terminé avec 36 tests, une erreur,
zéro échec et zéro skip. L'artefact 11166465791, SHA-256
`14d5c5eadcd618677779d422543a6250be0b6e08f9689657fac3fe675e1a1b47`,
localise SQL_FENCE_TIMEOUT à la sortie normale de l'admission fichiers 6B7b4.
Les scénarios données et plan de reprise n'ont donc pas été atteints.
Ce résultat ne qualifie pas 6B7b6b et ne remet pas en cause les verdicts
Installer distincts. L'échec et son diagnostic sont conservés dans la livraison.

La recette répartit maintenant ses quatre scénarios indépendants (rapport,
dérive données, altération archive, journal inconnu) sur quatre acquisitions.
Chaque acquisition refait l'export SQL ; les identifiants et empreintes sont
exigés distincts. Douze mesures comprennent la libération normale, toutes
strictement sous 180 secondes. Les contrôles de dérive SQL, SIGKILL, blob,
fichiers, parents et rejet de fenêtre fermée sont conservés. Aucun délai natif
n'est relevé, aucun contrôle de production n'est supprimé ou mis en cache.

## Composition ajoutée

La continuation Gateway MAIN appelle le plan 6B7b7a après la véritable
réouverture des données. Un SIGKILL après la première copie privée laisse
le plan partiel et une observation historique complète. Une dérive SQL
ultérieure doit refuser une nouvelle admission sans compléter les copies.

Trois fenêtres indépendantes couvrent ensuite recover, prepare et check.
Recover est en lecture seule ; prepare complète les absences autorisées sous
un nouvel export ; check est en lecture seule et conserve le reçu exact.
Chaque opération utilise les lecteurs SQL, archives, fichiers, Ext4 et services
natifs, sans doublure d'admission. La seule instrumentation mutante provoque
le SIGKILL après l'écriture durable réelle. Les observations interrompues
restent byte-exactes. Les fenêtres fermées refusent prepare et check.

Les quatre originaux restent identiques en octets, propriétaires et modes.
Les copies privées sont root:root 0600. L'ordre PHP, Apache, Foundation,
Gateway puis timer est vérifié. Le plan n'autorise toujours ni retrait de
bloqueur, ni sortie de maintenance, ni démarrage de service.

## Qualification et suite

Les 36 tests natifs sont étendus, aucun n'est retiré. Les parcours fresh et
upgrade hérités sont conservés dans la recette et les Quality. Les campagnes
Installer et le nouveau run natif doivent porter sur le gel exact ; les
verdicts mesurés, SHA et limites accompagnent la livraison. Le présent texte
ne revendique aucun succès anticipé. Les budgets par fenêtre restent 180 s.

Aucun changement SQL, schema.sql, install.php, asset UI, APK ou serveur.
Aucune promotion de main/dev/dev-Bastien. Après qualification, la prochaine
frontière reste la consommation récupérable des bloqueurs puis les starts
liés aux invocations courantes, avant boot/restauration, DEV/FCM et recette 6C.
La phase 6 reste ouverte.
