from fastapi import FastAPI, Form
from twilio.rest import Client
from asterisk.ami import AMIClient, SimpleAction
import os

app = FastAPI()

# Configuration Twilio

TWILIO_SID = os.getenv("TWILIO_SID")
TWILIO_TOKEN = os.getenv("TWILIO_TOKEN")
TWILIO_NUMBER = os.getenv("TWILIO_NUMBER")
AMI_HOST = os.getenv("AMI_HOST")
AMI_USER = os.getenv("AMI_USER")
AMI_PASS = os.getenv("AMI_PASS")

twilio_client = Client(TWILIO_SID, TWILIO_TOKEN)

@app.post("/incoming-sms")
async def incoming_sms(From: str = Form(...), Body: str = Form(...)):
    """Webhook appelé par Twilio lors de la réception d'un SMS"""
    client = AMIClient(address=AMI_HOST, port=5038)
    client.login(username=AMI_USER, secret=AMI_PASS)

    # On forge le SIP MESSAGE pour l'extension 101 (exemple)
    # Dans une version prod, on mapperait le numéro Twilio à une extension
    action = SimpleAction(
        'MessageSend',
        To='pjsip:101',
        From=f'SMS:{From}',
        Body=Body
    )
    client.send_action(action)
    client.logout()
    return {"status": "success"}

@app.post("/outgoing-sms")
async def outgoing_sms(to: str = Form(...), body: str = Form(...)):
    """Appelé par Asterisk quand un softphone envoie un message"""
    message = twilio_client.messages.create(
        body=body,
        from_=TWILIO_NUMBER,
        to=to
    )
    return {"sid": message.sid}