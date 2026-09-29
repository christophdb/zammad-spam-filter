import os
import hmac
import logging

from fastapi import FastAPI, Request, HTTPException
import httpx

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("spam_filter")

app = FastAPI()

ZAMMAD_URL = os.environ["ZAMMAD_URL"]
ZAMMAD_TOKEN = os.environ["ZAMMAD_TOKEN"]

# Wohin geblockte Mails einsortiert werden (statt sie per x-zammad-ignore zu verwerfen).
# IDs statt Namen, weil der API-Token keine Leserechte auf Gruppen/Status braucht.
SPAM_GROUP_ID = os.environ["SPAM_GROUP_ID"]
SPAM_STATE_ID = os.environ["SPAM_STATE_ID"]
# Bewusst ein anderer Tag als "Spam", damit der Trigger "Spamlist" nicht erneut feuert
SPAM_TAG = os.environ.get("SPAM_TAG", "Spam-Auto")

# Optional: Bearer-Token aus der Zammad-Webhook-Konfiguration pruefen
WEBHOOK_TOKEN = os.environ.get("WEBHOOK_TOKEN")

MAILCOW_URL = os.environ.get("MAILCOW_URL")
MAILCOW_API_KEY = os.environ.get("MAILCOW_API_KEY")

PER_PAGE = 500


async def fetch_all_filters(client: httpx.AsyncClient) -> list:
    # Zammad liefert pro Seite max. 500 Eintraege, daher alle Seiten abrufen
    filters = []
    page = 1
    while True:
        resp = await client.get(
            f"{ZAMMAD_URL}/api/v1/postmaster_filters",
            headers={"Authorization": f"Token {ZAMMAD_TOKEN}"},
            params={"page": page, "per_page": PER_PAGE},
        )
        resp.raise_for_status()
        batch = resp.json()
        filters.extend(batch)
        if len(batch) < PER_PAGE:
            return filters
        page += 1


@app.post("/zammad-spam")
async def handle_spam(request: Request):
    if WEBHOOK_TOKEN:
        auth = request.headers.get("authorization", "")
        if not hmac.compare_digest(auth, f"Bearer {WEBHOOK_TOKEN}"):
            logger.warning("Ungueltiges Token von %s", request.client.host)
            raise HTTPException(status_code=401)

    data = await request.json()

    # Absender-E-Mail aus dem Webhook-Payload extrahieren
    customer = data.get("ticket", {}).get("customer", {})
    sender = customer.get("email")

    if not sender:
        logger.info("Kein Absender im Webhook-Payload gefunden")
        return {"status": "no sender found"}

    async with httpx.AsyncClient() as client:
        # Duplikat-Check: pruefen ob Filter schon existiert
        existing = await fetch_all_filters(client)
        already_blocked = any(
            f["name"] == f"Spam-Block: {sender}" for f in existing
        )
        if already_blocked:
            logger.info("Bereits geblockt: %s", sender)
            return {"status": "already blocked", "sender": sender}

        # Postmaster-Filter per Zammad-API anlegen
        resp = await client.post(
            f"{ZAMMAD_URL}/api/v1/postmaster_filters",
            headers={
                "Authorization": f"Token {ZAMMAD_TOKEN}",
                "Content-Type": "application/json",
            },
            json={
                "name": f"Spam-Block: {sender}",
                "match": {
                    "from": {
                        "operator": "contains",
                        "value": sender,
                    }
                },
                "perform": {
                    "x-zammad-ticket-group_id": {"value": SPAM_GROUP_ID},
                    "x-zammad-ticket-state_id": {"value": SPAM_STATE_ID},
                    "x-zammad-ticket-tags": {
                        "operator": "add",
                        "value": SPAM_TAG,
                    },
                },
                "active": True,
                "channel": "email",
            },
        )

        # Optional: Mailcow-Blacklisting
        #if MAILCOW_URL and MAILCOW_API_KEY:
        #    await client.post(
        #        f"{MAILCOW_URL}/api/v1/edit/blacklist-policy",
        #        headers={"X-API-Key": MAILCOW_API_KEY},
        #        json={
        #            "items": [sender],
        #            "attr": {"blacklist_from": sender},
        #        },
        #    )

    logger.info("Neu geblockt: %s (Zammad: %s)", sender, resp.status_code)
    return {
        "status": "blocked",
        "sender": sender,
        "zammad_response": resp.status_code,
    }
