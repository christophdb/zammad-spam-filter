# Zammad Spam-Filter

Kleiner Webhook-Dienst für [Zammad](https://zammad.org): Markiert ein Agent ein Ticket als Spam, legt der Dienst automatisch einen **Postmaster-Filter** für den Absender an. Künftige Mails dieses Absenders landen dann direkt **geschlossen in der Gruppe `zz_Spam`** mit dem Tag `Spam-Auto` – sie tauchen nicht mehr in den normalen Übersichten auf, bleiben aber auffindbar.

Mails werden bewusst **nicht** verworfen (`x-zammad-ignore`). Ein Fehlklick auf „Spam“ würde sonst alle künftigen Mails eines legitimen Absenders stillschweigend löschen, ohne dass es jemand bemerkt.

## Ablauf

```
Agent wendet Makro "Close & Tag as Spam" an
  → Ticket: Gruppe zz_Spam, Tag Spam, geschlossen
  → Trigger "Spamlist" feuert (einmal)
  → Webhook POST http://zammad-spam-filter:8484/zammad-spam
  → Dienst legt Postmaster-Filter "Spam-Block: <absender>" an

Neue Mail vom selben Absender
  → Postmaster-Filter greift
  → neues Ticket: Gruppe zz_Spam, geschlossen, Tag Spam-Auto
  → Trigger feuert NICHT (Tag ist Spam-Auto, nicht Spam)
```

Der angelegte Filter sieht so aus:

```json
{
  "name": "Spam-Block: spammer@example.com",
  "match":   { "from": { "operator": "contains", "value": "spammer@example.com" } },
  "perform": {
    "x-zammad-ticket-group_id": { "value": "<SPAM_GROUP_ID>" },
    "x-zammad-ticket-state_id": { "value": "<SPAM_STATE_ID>" },
    "x-zammad-ticket-tags":     { "operator": "add", "value": "Spam-Auto" }
  },
  "channel": "email",
  "active": true
}
```

Existiert bereits ein Filter mit diesem Namen, legt der Dienst keinen zweiten an.

## Einrichtung in Zammad

Die Schritte am besten in dieser Reihenfolge durchgehen – der Webhook (Schritt 5) setzt voraus, dass der Container bereits läuft.

### 1. Gruppe `zz_Spam` anlegen

*Verwalten → Gruppen → Neue Gruppe*

- **Name:** `zz_Spam` (das `zz_` sortiert die Gruppe in Listen ans Ende)
- **Zugriff:** Nur die Agenten, die Spam gelegentlich sichten sollen. Die anderen sehen die Tickets dann gar nicht.

Tipp: Agenten mit Zugriff sollten in ihren Profil-Benachrichtigungen die Gruppe `zz_Spam` abwählen, damit sie für automatisch einsortierten Spam keine Mails bekommen.

### 2. Tags `Spam` und `Spam-Auto` anlegen

*Verwalten → Tags*

Beide Tags anlegen, falls nur Admins neue Tags erstellen dürfen:

| Tag | gesetzt von | Bedeutung |
|---|---|---|
| `Spam` | Makro (manuell durch Agent) | löst den Webhook aus |
| `Spam-Auto` | Postmaster-Filter (automatisch) | löst **nichts** aus |

Die Trennung ist wichtig: Würde der Filter ebenfalls `Spam` setzen, würde jede geblockte Mail erneut den Webhook aufrufen.

### 3. API-Token erstellen

Empfohlen ist ein eigener technischer Benutzer (z. B. `spamfilter@…`) mit einer eigenen Rolle, die nur diese Berechtigungen hat:

- `admin.channel_email` – zum Lesen und Anlegen von Postmaster-Filtern
- `user_preferences.access_token` – damit der Benutzer sich einen Token erzeugen kann

Als dieser Benutzer anmelden, dann *Profil → Token-Zugriff → Neuen Token erstellen*, Berechtigung **`admin.channel_email`** auswählen. Den Token in die `.env` als `ZAMMAD_SPAMFILTER_API_TOKEN` eintragen.

Der Token braucht **keine** Rechte auf Gruppen oder Ticket-Status – deshalb werden deren IDs per Umgebungsvariable übergeben (nächster Schritt).

### 4. IDs von Gruppe und Status ermitteln

Auf dem Zammad-Server:

```bash
docker compose exec zammad-railsserver bundle exec rails r \
  'puts "Gruppe: #{Group.find_by(name: "zz_Spam").id}"; puts "Status: #{Ticket::State.find_by(name: "closed").id}"'
```

Die Werte als `ZAMMAD_SPAMFILTER_GROUP_ID` und `ZAMMAD_SPAMFILTER_STATE_ID` in die `.env` eintragen. Der Status `closed` hat in einer Standard-Installation die ID `4`.

Danach den Container starten (siehe [Installation](#installation)).

### 5. Webhook anlegen

*Verwalten → Webhook → Neuer Webhook*

| Feld | Wert |
|---|---|
| Name | `Spam-Filter Webhook` |
| Endpunkt | `http://zammad-spam-filter:8484/zammad-spam` |
| Request-Methode | `POST` |
| SSL-Verifizierung | nein (interne HTTP-Verbindung im Docker-Netz) |
| Authentifizierung | optional *Bearer Token* – dann denselben Wert als `ZAMMAD_SPAMFILTER_WEBHOOK_TOKEN` in die `.env` |
| Eigene Nutzlast | **aus** |

Der Dienst liest aus der Standard-Nutzlast `ticket.customer.email`. Mit eigener Nutzlast muss dieses Feld erhalten bleiben.

### 6. Makro „Close & Tag as Spam“ anlegen

*Verwalten → Makros → Neues Makro*

| Aktion | Wert |
|---|---|
| Status | geschlossen |
| Tags | hinzufügen: `Spam` |
| Besitzer | aktueller Benutzer |
| Gruppe | `zz_Spam` |

Mit diesem Makro markieren Agenten ein Ticket als Spam.

![Makro „Close & Tag as Spam“](docs/makro-close-tag-spam.png)

### 7. Trigger „Spamlist“ anlegen

*Verwalten → Trigger → Neuer Trigger*

| Feld | Wert |
|---|---|
| Name | `Spamlist` |
| Aktiviert durch | Aktion |
| Aktions-Ausführung | **Selektiv** |
| Bedingung 1 | Gruppe **ist** `zz_Spam` |
| Bedingung 2 | Tags **enthält eins** `Spam` |
| Aktion 1 | Status: geschlossen |
| Aktion 2 | Webhook: `Spam-Filter Webhook` |

![Trigger „Spamlist“](docs/trigger-spamlist.png)

**Warum genau so?**

- **Selektiv statt Immer:** Mit „Immer“ feuert der Trigger bei *jeder* Änderung an einem Spam-Ticket (Notiz, Besitzerwechsel, Merge …) und ruft den Webhook immer wieder auf.
- **Bedingung auf die Gruppe:** Bei „Selektiv“ prüft Zammad, ob sich ein Feld aus der Bedingung geändert hat. **Tags zählen dabei nicht als geändertes Feld** – ein Trigger mit nur einer Tag-Bedingung feuert im Modus „Selektiv“ nie. Die Gruppe dagegen ist ein Ticket-Feld; das Makro verschiebt nach `zz_Spam`, dadurch feuert der Trigger genau einmal.

## Installation

Der Container muss im **selben Docker-Netz wie Zammad** laufen, damit Zammad den Webhook unter `http://zammad-spam-filter:8484` erreicht. Ein Port nach außen ist nicht nötig.

```bash
git clone <repo-url> zammad-spam-filter
cd zammad-spam-filter
cp .env.example .env
# .env ausfüllen (siehe Schritte 3 und 4 oben)

docker compose config            # prüfen: alle Variablen gefüllt?
docker compose up -d --build
docker compose logs -f
```

Soll der Dienst stattdessen in einer bestehenden `docker-compose.yml` neben Zammad laufen, den Service-Block übernehmen und `build:` auf das Verzeichnis dieses Repos zeigen lassen.

### Umgebungsvariablen

| Variable in `.env` | Pflicht | Bedeutung |
|---|---|---|
| `ZAMMAD_URL` | ja | Hostname von Zammad, **ohne** `https://` |
| `ZAMMAD_NETWORK` | ja | Docker-Netz, in dem Zammad läuft |
| `ZAMMAD_SPAMFILTER_API_TOKEN` | ja | API-Token mit `admin.channel_email` |
| `ZAMMAD_SPAMFILTER_GROUP_ID` | ja | ID der Gruppe `zz_Spam` |
| `ZAMMAD_SPAMFILTER_STATE_ID` | ja | ID des Status `closed` |
| `ZAMMAD_SPAMFILTER_WEBHOOK_TOKEN` | nein | Bearer-Token des Webhooks; leer = keine Prüfung |

Im Container heißen die Variablen kürzer (`ZAMMAD_TOKEN`, `SPAM_GROUP_ID`, …), siehe `docker-compose.yml`. Zusätzlich kann dort `SPAM_TAG` gesetzt werden (Standard: `Spam-Auto`).

### Test

1. Von einer eigenen Test-Adresse eine Mail an Zammad schicken.
2. Auf das Ticket das Makro „Close & Tag as Spam“ anwenden.
3. `docker compose logs zammad-spam-filter` zeigt `Neu geblockt: <adresse>`.
4. Eine zweite Mail von derselben Adresse schicken → neues Ticket, geschlossen, Gruppe `zz_Spam`, Tag `Spam-Auto`. Im Log erscheint **kein** neuer Aufruf.
5. Test-Filter wieder löschen (siehe unten).

## Betrieb

### Fehlklick rückgängig machen

Ein legitimer Absender wurde als Spam markiert:

```bash
docker compose exec zammad-railsserver bundle exec rails r \
  'PostmasterFilter.where(name: "Spam-Block: absender@example.com").destroy_all'
```

Anschließend die betroffenen Tickets in `zz_Spam` (Tag `Spam-Auto`) in die richtige Gruppe verschieben, wieder öffnen und die Tags entfernen.

Regelmäßig einen Blick auf Tickets mit dem Tag `Spam-Auto` werfen – legitime Absender dort sind Fehlklicks.

### Filter suchen

**Die Filterliste in der Zammad-Oberfläche zeigt maximal 500 Filter an** – und zwar die ältesten (siehe [Bekannte Zammad-Eigenheiten](#bekannte-zammad-eigenheiten)). Filter deshalb per Rails-Konsole suchen:

```bash
docker compose exec zammad-railsserver bundle exec rails r '
PostmasterFilter.select { |f| f.match.to_s.include?("example.com") }
  .each { |f| puts [f.id, f.name, f.active, f.created_at].join(" | ") }
'
```

Anzahl und Duplikate:

```bash
docker compose exec zammad-railsserver bundle exec rails r '
puts "Gesamt:  #{PostmasterFilter.count}"
puts "Doppelt: #{PostmasterFilter.group(:name).having("count(*) > 1").count.inspect}"
'
```

### Nachvollziehen, was mit einer Mail passiert ist

```bash
docker compose logs --since 24h zammad-scheduler | grep -i -A5 "<message-id oder absender>"
```

Das Log zeigt, welcher Filter gegriffen hat (`matching: key 'from' contains '…'`).

## Bekannte Zammad-Eigenheiten

- **500er-Limit der Filterliste:** Die Admin-Oberfläche lädt Postmaster-Filter über `GET /api/v1/postmaster_filters` ohne `per_page`. Der Server liefert dann nur 500 Einträge nach ID (`paginate_with(default: 500)` in `app/controllers/application_controller/renders_models.rb`). Neuere Filter sind in der UI unsichtbar, **wirken aber trotzdem** – die Mailverarbeitung liest alle Filter direkt aus der Datenbank. Der Dienst selbst ruft alle Seiten ab.
- **Selektive Trigger ignorieren Tag-Änderungen:** siehe Schritt 7.
- **IMAP-Abruf löscht Mails:** Ohne „Nachrichten auf dem Server behalten“ löscht Zammad jede abgeholte Mail vom Mailserver – auch dann, wenn daraus kein Ticket entsteht.
- **`rails` im Docker-Image:** nicht im `PATH`, immer `bundle exec rails …` verwenden.

## Umstieg von `x-zammad-ignore`

Ältere Versionen dieses Dienstes haben Filter mit `x-zammad-ignore` angelegt, die Mails komplett verwerfen. Bestehende Filter lassen sich einmalig umstellen. Erst mit `DRY = true` prüfen, dann mit `DRY = false` ausführen; Gruppen- und Status-ID anpassen:

```bash
docker compose exec zammad-railsserver bundle exec rails r '
DRY = true
NEW_PERFORM = {
  "x-zammad-ticket-group_id" => { "value" => "5" },
  "x-zammad-ticket-state_id" => { "value" => "4" },
  "x-zammad-ticket-tags"     => { "operator" => "add", "value" => "Spam-Auto" },
}
spam  = PostmasterFilter.where("name LIKE ?", "Spam-Block:%").order(:id).to_a
dupes = spam.group_by { |f| [f.name, f.match] }.values.flat_map { |fs| fs.drop(1) }
conv  = (spam - dupes).select { |f| f.perform.key?("x-zammad-ignore") }
puts "Duplikate löschen: #{dupes.size}"
puts "Umstellen:         #{conv.size}"
unless DRY
  PostmasterFilter.transaction do
    dupes.each(&:destroy)
    conv.each { |f| f.update!(perform: NEW_PERFORM) }
  end
  puts "Fertig. Gesamt: #{PostmasterFilter.count}"
end
'
```

## Sicherheit

- Der Dienst ist nur im Docker-Netz erreichbar (kein veröffentlichter Port).
- Mit gesetztem `ZAMMAD_SPAMFILTER_WEBHOOK_TOKEN` werden Aufrufe ohne passenden Bearer-Token mit `401` abgelehnt. Ohne Token kann jeder Container im selben Netz Absender blocken lassen.
- Die HMAC-Signatur des Webhooks wird nicht geprüft.
