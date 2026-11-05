# Mezzi Corsa

Web app per telefono: scegli dove corri, il giorno e l'ora in cui devi essere lì. Ti mostra come arrivare da via Menotti (Sestri Ponente) e come tornare a casa nella fascia da 1 a 4 ore dopo, divisi tra solo treno, solo bus e treno + bus.

## Attivazione

1. Settings → Pages → Build and deployment → Source: **GitHub Actions**.
2. Actions → *Aggiorna orari e sito* → **Run workflow**.
3. Apri `https://<utente>.github.io/<repo>/` e aggiungila alla schermata Home.

Il sito si aggiorna da solo due volte a settimana. Se un aggiornamento fallisce, resta online la versione precedente e GitHub ti avvisa per email.

## Modifiche comuni

Luoghi, punti esatti e raggi a piedi sono in `config.json`. Per fissare un punto preciso aggiungi `"pos": [lat, lon]` al luogo.

`report.txt` mostra le fermate usate e quante soluzioni sono state trovate.

## Fonti

- AMT Genova: GTFS ufficiale
- Trenitalia Liguria: GTFS di Regione Liguria (CC BY 4.0)

Sono orari programmati. Per scioperi e ritardi usa il link di controllo nella pagina. Il calcolo considera al massimo due cambi.
