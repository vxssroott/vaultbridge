import os
import requests
import logging
from flask import Flask, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv
from datetime import datetime

load_dotenv()
app = Flask(__name__)
CORS(app)
logging.basicConfig(level=logging.INFO)

# ===== MONIEPOINT CONFIG =====
MONIEPOINT_CLIENT_ID = os.getenv('MONIEPOINT_CLIENT_ID')
MONIEPOINT_CLIENT_SECRET = os.getenv('MONIEPOINT_CLIENT_SECRET')
MONIEPOINT_BASE_URL = 'https://sandbox.monnify.com/api/v1'  # or live

# ===== IN-MEMORY LOGS (replace with DB in production) =====
transfer_logs = []

# ===== MONIEPOINT AUTH =====
def get_moniepoint_token():
    url = f"{MONIEPOINT_BASE_URL}/auth/login"
    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Basic {MONIEPOINT_CLIENT_ID}:{MONIEPOINT_CLIENT_SECRET}'
    }
    response = requests.post(url, headers=headers)
    if response.status_code == 200:
        return response.json().get('responseBody', {}).get('accessToken')
    return None

# ===== VALIDATE ACCOUNT =====
@app.route('/api/validate', methods=['POST'])
def validate_account():
    data = request.json
    account_number = data.get('account_number')
    bank_code = data.get('bank')  # You'll need a bank code mapping

    token = get_moniepoint_token()
    if not token:
        return jsonify({"status": "error", "error": "Authentication failed"}), 500

    url = f"{MONIEPOINT_BASE_URL}/disbursements/account/validate"
    headers = {'Authorization': f'Bearer {token}'}
    payload = {'accountNumber': account_number, 'bankCode': bank_code}
    response = requests.post(url, json=payload, headers=headers)

    if response.status_code == 200:
        data = response.json().get('responseBody', {})
        return jsonify({"status": "valid", "account_name": data.get('accountName')})
    return jsonify({"status": "invalid", "error": "Account not found"}), 400

# ===== INITIATE TRANSFER =====
@app.route('/api/transfer', methods=['POST'])
def transfer():
    data = request.json
    account_number = data.get('account_number')
    amount = data.get('amount')
    bank_code = data.get('bank')

    token = get_moniepoint_token()
    if not token:
        return jsonify({"status": "error", "error": "Authentication failed"}), 500

    url = f"{MONIEPOINT_BASE_URL}/disbursements/single"
    headers = {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}
    payload = {
        'amount': amount,
        'reference': f'VB-{os.urandom(4).hex()}-{int(datetime.now().timestamp())}',
        'narration': 'VaultBridge Transfer',
        'destinationBankCode': bank_code,
        'destinationAccountNumber': account_number,
        'currency': 'NGN',
        'sourceAccountNumber': os.getenv('MONIEPOINT_SOURCE_ACCOUNT')
    }
    response = requests.post(url, json=payload, headers=headers)

    if response.status_code == 200:
        transfer_logs.append({
            'bank': bank_code,
            'account': account_number,
            'account_name': data.get('account_name', 'Unknown'),
            'amount': amount,
            'status': 'success',
            'timestamp': datetime.now().isoformat()
        })
        return jsonify({"status": "success", "reference": payload['reference']})
    else:
        transfer_logs.append({
            'bank': bank_code,
            'account': account_number,
            'account_name': data.get('account_name', 'Unknown'),
            'amount': amount,
            'status': 'failed',
            'timestamp': datetime.now().isoformat()
        })
        return jsonify({"status": "failed", "error": response.text}), 400

# ===== GET BALANCE =====
@app.route('/api/balance', methods=['GET'])
def balance():
    token = get_moniepoint_token()
    if not token:
        return jsonify({"error": "Authentication failed"}), 500

    url = f"{MONIEPOINT_BASE_URL}/disbursements/wallet-balance"
    headers = {'Authorization': f'Bearer {token}'}
    response = requests.get(url, headers=headers)

    if response.status_code == 200:
        balance = response.json().get('responseBody', {}).get('availableBalance', 0)
        return jsonify({"balance": balance})
    return jsonify({"error": "Failed to fetch balance"}), 400

# ===== GET LOGS =====
@app.route('/api/logs', methods=['GET'])
def get_logs():
    return jsonify(transfer_logs)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
