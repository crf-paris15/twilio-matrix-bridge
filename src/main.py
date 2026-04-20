import asyncio
import os
import sqlite3
import csv

from contextlib import asynccontextmanager

from fastapi import FastAPI, Form, Request, HTTPException, Response
from twilio.rest import Client as TwilioClient
from twilio.request_validator import RequestValidator
from nio import AsyncClient as MatrixClient, MatrixRoom, RoomCreateError, RoomMessageText, RoomResolveAliasError, RoomSendError, RoomVisibility

# Twilio configuration
TWILIO_SID = os.getenv("TWILIO_SID")
TWILIO_TOKEN = os.getenv("TWILIO_TOKEN")
TWILIO_NUMBER = os.getenv("TWILIO_NUMBER")
twilio_client = TwilioClient(TWILIO_SID, TWILIO_TOKEN)

# Matrix configuration
MATRIX_URI = os.getenv("MATRIX_URI")
MATRIX_USER = os.getenv("MATRIX_USER")
MATRIX_PASSWORD = os.getenv("MATRIX_PASSWORD")
MATRIX_TIMEOUT = 30000
MATRIX_USERS_TO_INVITE = ["@pul:sms.crf.tools", "@dlus:sms.crf.tools", "@rlu:sms.crf.tools"]

MATRIX_FIRST_SEND = "!zyCkywgybkaPAAhKbx:sms.crf.tools"

# CSV configuration
CSV_PATH = "/code/contacts.csv"

# SQLite configuration
SQLITE_PATH = "/code/database.db"
db_connection = sqlite3.connect(SQLITE_PATH)
db_cursor = db_connection.cursor()

try:
    db_cursor.execute("SELECT * FROM rooms LIMIT 1")
    db_cursor.fetchall()
except sqlite3.OperationalError:
    print(f"Configuring DB")
    db_cursor.execute("CREATE TABLE rooms(id TEXT PRIMARY KEY, phone TEXT NOT NULL)")
    print(f"DB configured")

# Global variable to hold the Matrix sync task
matrix_sync_task = None

# Callback for handling incoming Matrix messages
async def message_callback(room: MatrixRoom, event: RoomMessageText) -> None:
    if room.room_id == MATRIX_FIRST_SEND:
        # Retrieve the Matrix client from the application state
        matrix_client = app.state.matrix_client

        # Create room
        create_response = await matrix_client.room_create(
            name = event.body,
            is_direct = True,
            visibility = RoomVisibility.public,
            invite = MATRIX_USERS_TO_INVITE,
            power_level_override = {
                "events": {
                    "m.room.name": 0,
                    "m.room.topic": 0
                }
            }
        )

        if type(create_response) == RoomCreateError:
            print(f"Failed to create room: {create_response}")
        else:
            db_cursor.execute("INSERT INTO rooms (id, phone) VALUES (?, ?)", [create_response.room_id, event.body])
            db_connection.commit()

            print(f"Room created")

    if not (event.sender == "SMS - Urgence" or event.sender == "@sms-urgence:sms.crf.tools"):
        db_cursor.execute("SELECT phone FROM rooms WHERE id = ? LIMIT 1", [room.room_id])
        phone = db_cursor.fetchone()[0]

        twilio_client.messages.create(
            body = event.body,
            from_ = TWILIO_NUMBER,
            to = phone
        )

        print(f"Sent message to {phone} with body: {event.body}")

# FastAPI lifespan event to manage Matrix client lifecycle
@asynccontextmanager
async def lifespan(app: FastAPI):
    global matrix_sync_task

    matrix_client = MatrixClient(MATRIX_URI, MATRIX_USER)
    print(await matrix_client.login(MATRIX_PASSWORD))

    await matrix_client.sync(timeout=30000, full_state=False)

    matrix_client.add_event_callback(message_callback, RoomMessageText)

    app.state.matrix_client = matrix_client
    matrix_sync_task = asyncio.create_task(matrix_client.sync_forever(timeout = MATRIX_TIMEOUT))

    yield

    if matrix_sync_task:
        matrix_sync_task.cancel()
    await matrix_client.close()

# Initialize FastAPI app with lifespan management
app = FastAPI(lifespan = lifespan)

@app.post("/")
async def incoming_sms(request: Request, From: str = Form(...), Body: str = Form(...)):
    """Webhook called by Twilio when an SMS is received"""

    # Validate that the request is actually from Twilio
    form_ = await request.form()
    if not RequestValidator(TWILIO_TOKEN).validate(
        uri = str(request.url).replace("http://", "https://"),  # Twilio sends the URL as https, but FastAPI sees it as http
        params = form_,
        signature = request.headers.get("X-Twilio-Signature", "")   
    ):
        raise HTTPException(status_code = 400, detail = "Error in Twilio Signature")

    print(f"Received message from {From} with body: {Body}")

    # Retrieve the Matrix client from the application state
    matrix_client = request.app.state.matrix_client

    # Retrive the Matrix room id if it exists
    db_cursor.execute("SELECT id FROM rooms WHERE phone = ? LIMIT 1", [From])

    rooms_exists = True
    room_id = None

    try:
        room_id = db_cursor.fetchone()[0]
    except:
        rooms_exists = False

    # If the room does not exist, we need to create it
    if not rooms_exists:
        name = From

        # If we have the phone number in Gaia, check who is the sender and use its name for the room
        with open(CSV_PATH, mode = 'r') as csv_file:
            contacts = csv.reader(csv_file, delimiter = ';')
            for contact in contacts:

                if contact[2] == From[3:]:
                    name = contact[1] + " " + contact[0]

        # Create room
        create_response = await matrix_client.room_create(
            name = name,
            is_direct = True,
            visibility = RoomVisibility.public,
            invite = MATRIX_USERS_TO_INVITE,
            power_level_override = {
                "events": {
                    "m.room.name": 0,
                    "m.room.topic": 0
                }
            }
        )

        if type(create_response) == RoomCreateError:
            print(f"Failed to create room: {create_response}")
        else:
            db_cursor.execute("INSERT INTO rooms (id, phone) VALUES (?, ?)", [create_response.room_id, From])
            db_connection.commit()

            room_id = create_response.room_id

    # We can now post the message in the room
    post_message_response = await matrix_client.room_send(
        room_id = room_id,
        message_type = "m.room.message",
        content = {
            "msgtype": "m.text",
            "body": Body
        }
    )

    if type(post_message_response) == RoomSendError:
        print(f"Failed to send message: {post_message_response}")

    return Response("<?xml version=\"1.0\" encoding=\"UTF-8\" ?><Response></Response>", media_type="application/xml")