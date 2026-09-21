# Contribution

- travailler à partir d'une issue ;
- conserver les responsabilités par module ;
- ne jamais introduire de shell arbitraire ;
- ne jamais committer de secret ;
- documenter les frontières de rollback ;
- ajouter les tests correspondant à toute mutation système ;
- privilégier la standard library Python pour le bootstrap.

Vérification locale :

```bash
./scripts/quality-local.sh
```

Ne pas ajouter de workflow CI lourd ou redondant sans nécessité.
