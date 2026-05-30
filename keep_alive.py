from flask import Flask
from threading import Thread
import os

app = Flask('')

@app.route('/')
def home():
    return "Cricket Bot is alive and running!"

def run():
    # Render assigns a dynamic port. This grabs it safely!
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run)
    t.start()
