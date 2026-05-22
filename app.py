from time import sleep
from sportident import SIReaderReadout
import sqlite3
from datetime import datetime


# app.py
from flask import Flask, render_template
app = Flask(__name__)


# connect to base station, the station is automatically detected,
# if this does not work, give the path to the port as an argument
# see the pyserial documentation for further information.
###si = SIReaderReadout('COM5')  # check Device Manager for the right port number

# wait for a card to be inserted into the reader
#while not si.poll_sicard():
#    sleep(1)

# some properties are now set
#card_number = si.sicard
#card_type = si.cardtype

# read out card data
#card_data = si.read_sicard()

# beep
#si.ack_sicard()

#print(f"Card number: {card_number}")
#print(f"Card type: {card_type}")
#print(f"Card data: {card_data}")

#sleep(20)


# mock_data.py - fake SI card data to develop against
MOCK_CARD_DATA = {
    'card_number': 8635918,
    'start': datetime(2026, 5, 17, 9, 27, 8),
    'finish': datetime(2026, 5, 17, 10, 49, 53),
    'check': datetime(2026, 5, 17, 9, 22, 36),
    'clear': None,
    'punches': [
        (138, datetime(2026, 5, 17, 9, 38, 27)),
        (130, datetime(2026, 5, 17, 9, 41, 29)),
        # etc
    ]
}


print(f"Card data: {MOCK_CARD_DATA}")

startTime = MOCK_CARD_DATA['start'].strftime("%H:%M:%S")
finishTime = MOCK_CARD_DATA['finish'].strftime("%H:%M:%S")

total_seconds = int((MOCK_CARD_DATA['finish'] - MOCK_CARD_DATA['start']).total_seconds())
totalTime = f"{total_seconds // 3600:02d}:{(total_seconds % 3600) // 60:02d}:{total_seconds % 60:02d}"

@app.route('/')
def index():
    user_data = {"name": MOCK_CARD_DATA['name'],
                 "startTime": startTime,
                 "finishTime": finishTime,
                 "totalTime": totalTime,
                 "splits": splits}
    return render_template('index.html', data=user_data)


# SQLite database setup
connection = sqlite3.connect('competitor_data.db')
cursor = connection.cursor()
competitorsTable = """CREATE TABLE IF NOT EXISTS
competitors (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    si_number INTEGER NOT NULL,
    class TEXT NOT NULL,
    club TEXT
) """

coursesTable = """CREATE TABLE IF NOT EXISTS
courses (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL
) """

controlsTable = """CREATE TABLE IF NOT EXISTS
controls (
    id INTEGER PRIMARY KEY,
    course_id INTEGER,
    code INTEGER NOT NULL,
    sequence INTEGER NOT NULL -- order of controls in the course
) """

punchesTable = """CREATE TABLE IF NOT EXISTS
punches (
    id INTEGER PRIMARY KEY,
    competitor_id INTEGER,
    control_code INTEGER NOT NULL,
    timestamp INTEGER NOT NULL  -- seconds since midnight
) """

cursor.execute(competitorsTable)
cursor.execute(coursesTable)
cursor.execute(controlsTable)
cursor.execute(punchesTable)

# Add to stores

# Insert competitor
cursor.execute("INSERT INTO competitors (name, si_number, class, club) VALUES (?, ?, ?, ?)",
               (MOCK_CARD_DATA['name'], MOCK_CARD_DATA['card_number'], MOCK_CARD_DATA['class'], MOCK_CARD_DATA['club']))

competitor_id = cursor.lastrowid  # grabs the id SQLite just assigned

# Insert course
cursor.execute("INSERT INTO courses (name) VALUES (?)", ('Course 1',))
course_id = cursor.lastrowid

# Insert controls (each punch location in order)
for sequence, (code, timestamp) in enumerate(MOCK_CARD_DATA['punches']):
    cursor.execute("INSERT INTO controls (course_id, code, sequence) VALUES (?, ?, ?)",
                   (course_id, code, sequence))

# Insert punches (what actually came off the card)
for code, timestamp in MOCK_CARD_DATA['punches']:
    cursor.execute("INSERT INTO punches (competitor_id, control_code, timestamp) VALUES (?, ?, ?)",
                   (competitor_id, code, timestamp))

connection.commit()

punches = MOCK_CARD_DATA['punches']
previous_time = MOCK_CARD_DATA['start']

splits = []

previous_time = MOCK_CARD_DATA['start']

for code, timestamp in MOCK_CARD_DATA['punches']:
    split = (timestamp - previous_time).total_seconds()
    print(f"Control {code}: {split:.0f} seconds")
    previous_time = timestamp

total = (MOCK_CARD_DATA['finish'] - MOCK_CARD_DATA['start']).total_seconds()
print(f"Total: {total:.0f} seconds")

if not MOCK_CARD_DATA['punches']:
    [31, 32, 33, 34, 35]
    splits = "Mispunches detected, no splits available"



if __name__ == '__main__':
    app.run(debug=True)