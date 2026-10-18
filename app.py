import os
import time
import json
import hashlib
import sqlite3
from web3 import Web3
from dotenv import load_dotenv
from google import genai
from flask import Flask, request, jsonify, render_template
from werkzeug.utils import secure_filename

load_dotenv()
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
gemini_client = genai.Client(api_key=GOOGLE_API_KEY)


neoxt_url = os.getenv("WEB3_PROVIDER", "https://neoxt4seed1.ngd.network")
web3 = Web3(Web3.HTTPProvider(neoxt_url))

# Check if the connection is successful
if web3.is_connected():
    print("Connected to blockchain")
else:
    print("Failed to connect to blockchain")

# Define your wallet address, private key and chain ID
from_address = os.getenv("FROM_ADDRESS", "")
private_key = os.getenv("PRIVATE_KEY", "")
chain_id = int(os.getenv("CHAIN_ID", "12227332"))

app = Flask(__name__)
UPLOAD_DIR = os.path.join(app.root_path, 'uploads')
os.makedirs(UPLOAD_DIR, exist_ok=True)

dictionary = {}

# SQLite database connection
conn = sqlite3.connect('document_verification.db')
c = conn.cursor()

# Create a table if it doesn't exist
c.execute('''
    CREATE TABLE IF NOT EXISTS documents (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        participant_name TEXT,
        document_hash TEXT,
        txn_hash TEXT,
        timestamp TEXT
    )
''')
conn.commit()
conn.close()

# Function to add document hash to the database
def store_in_db(participant_name, document_hash, txn_hash):
    timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
    conn = sqlite3.connect('document_verification.db')
    c = conn.cursor()
    c.execute('INSERT INTO documents (participant_name, document_hash, txn_hash, timestamp) VALUES (?, ?, ?, ?)',
              (participant_name, document_hash, txn_hash, timestamp))
    conn.commit()
    conn.close()


@app.route('/')
def home():
    return render_template('index.html')

def verify_data(participant_name, json_data):
    # Calculate the hash of the provided JSON data
    json_string = json.dumps(json_data, sort_keys=True)
    calculated_hash = hashlib.sha256(json_string.encode()).hexdigest()
    conn = sqlite3.connect('document_verification.db')
    c = conn.cursor()
    # Retrieve the stored hash from the database
    c.execute('SELECT document_hash FROM documents WHERE participant_name = ?', (participant_name,))
    result = c.fetchone()
    conn.close()

    if result:
        stored_hash = result[0]
        if calculated_hash == stored_hash:
            return " ✅ Verification successful! Data is authentic."
        else:
            return " ❌ Verification failed! Data has been altered."
    else:
        return " ❌ Verification failed! Doesn't Exist."

def extract_document_data(image_path):
    uploaded_file = gemini_client.files.upload(file=image_path)
    result = gemini_client.models.generate_content(
        model="gemini-flash-latest",
        contents=[uploaded_file, "Extract the text content from this image. Organise the text into paragraph format, and extract the name and hackathon name. Respond with JSON containing text, hackathon_name and name."],
    )
    response_text = result.text.strip()
    if response_text.startswith("```"):
        response_text = response_text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    result_data = json.loads(response_text)
    return {
        "hackathon_name": result_data.get("hackathon_name", "Hackathon name not found"),
        "name": result_data.get("name", "Name not found"),
    }

@app.route('/verify')
def verify():
    return render_template('verify.html')

@app.route('/upload_details')
def upload_details():
    return render_template('upload.html')

@app.route('/upload', methods=['POST'])
def upload():
    global dictionary   
    if 'image' not in request.files:
        return "No file part", 400
    image = request.files['image']
    if image.filename == '':
        return "No selected file", 400
    filename = secure_filename(image.filename)
    save = os.path.join(UPLOAD_DIR, filename)
    image.save(save)

    try:
        dictionary = extract_document_data(save)
    except Exception as error:
        return f"Gemini processing failed: {error}", 502
    return render_template('result.html', dictionary=dictionary, verify_result=verify_data( dictionary["name"], dictionary))

@app.route('/upload_data', methods=['POST'])
def upload_data():
    if 'image' not in request.files:
        return "No file part", 400
    image = request.files['image']
    if image.filename == '':
        return "No selected file", 400
    filename = secure_filename(image.filename)
    save = os.path.join(UPLOAD_DIR, filename)
    image.save(save)
    try:
        data = extract_document_data(save)
    except Exception as error:
        return f"Gemini processing failed: {error}", 502
    # Convert JSON to string and then create a hash (SHA-256)
    data_string = json.dumps(data, sort_keys=True)
    data_hash = hashlib.sha256(data_string.encode()).hexdigest()
    
    account = web3.eth.account.from_key(private_key)
    nonce = web3.eth.get_transaction_count(account.address)
    conn = sqlite3.connect('document_verification.db')
    c = conn.cursor()
    # lenth of the data
    c.execute('SELECT COUNT(*) FROM documents')
    result = c.fetchone()
    conn.close()
    if result:
        length = result[0]
    else:
        length = 0
    
    to_address = "0x00000000000000000000000000000000000000" + str(length)
    
    transaction = {
        'to': to_address,  # Use a smart contract address if interacting with one
        'value': web3.to_wei(0, 'ether'),  # We are not sending ETH, just a transaction with data
        'gas': 2000000,
        'gasPrice': web3.to_wei('50', 'gwei'),
        'nonce': nonce,
        'chainId': chain_id,  # Include the chain ID for replay protection
        'data': web3.to_bytes(hexstr=data_hash)
    }
    # Sign and send the transaction
    signed_txn = web3.eth.account.sign_transaction(transaction, private_key)
    txn_hash = web3.eth.send_raw_transaction(signed_txn.raw_transaction)

    # Store the document hash and transaction hash in the database
    store_in_db(data["name"], data_hash, web3.to_hex(txn_hash))
    return web3.to_hex(txn_hash)

@app.route('/result')
def result():
    global dictionary
    return render_template('result.html', dictionary=dictionary, verify_result=verify_data( dictionary["name"], dictionary))

if __name__ == '__main__':
    app.run(debug=True)