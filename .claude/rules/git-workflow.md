# Git workflow — regola obbligatoria

## Regola

Ogni modifica al codice DEVE avvenire su un branch dedicato, mai direttamente su `main`.

## Flusso obbligatorio

1. **Crea un branch** con nome descrittivo prima di iniziare qualsiasi modifica:
   ```bash
   git checkout -b feature/nome-feature
   # oppure
   git checkout -b fix/nome-fix
   ```

2. **Implementa e testa** fino in fondo sulla branch (test Chrome DevTools, verifica API, log backend, zero errori console).

3. **Commit sulla branch** — mai su `main`.

4. **Chiedi approvazione** all'utente prima del merge:
   > "Feature X è implementata e testata. Posso fare il merge su main?"

5. **Merge su `main` solo con esplicita approvazione** dell'utente.
   ```bash
   git checkout main
   git merge --no-ff feature/nome-feature
   git push
   ```

## Non fare mai

- Committare o pushare direttamente su `main`
- Fare merge senza approvazione esplicita dell'utente
- Creare PR o fare push prima che i test siano completati
