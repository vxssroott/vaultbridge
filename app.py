import os
import requests
import logging
from flask import Flask, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv
from datetime import datetime
import time

load_dotenv()
app = Flask(__name__)

# ===== CORS CONFIG — Allow your GitHub Pages domain =====
ALLOWED_ORIGINS = [
    'https://your-username.github.io',  # Replace with your actual GitHub Pages URL
    'http://localhost:5500',            # For local testing (VS Code Live Server)
    'http://127.0.0.1:5500',
    'http://localhost:3000',
]

CORS(app, origins=ALLOWED_ORIGINS, supports_credentials=True)

# ===== LOGGING =====
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ===== MONIEPOINT CONFIG =====
MONIEPOINT_CLIENT_ID = os.getenv('MONIEPOINT_CLIENT_ID')
MONIEPOINT_CLIENT_SECRET = os.getenv('MONIEPOINT_CLIENT_SECRET')
MONIEPOINT_BASE_URL = os.getenv('MONIEPOINT_BASE_URL', 'https://sandbox.monnify.com/api/v1')
MONIEPOINT_SOURCE_ACCOUNT = os.getenv('MONIEPOINT_SOURCE_ACCOUNT')

# ===== IN-MEMORY STORAGE (Replace with DB in production) =====
transfer_logs = []
token_cache = {
    'access_token': None,
    'expires_at': 0
}

# ===== BANK CODE MAPPING =====
BANK_CODES = {
    'gtbank': 'GTB',
    'zenith': 'ZENITH',
    'access': 'ACCESS',
    'firstbank': 'FIRST',
    'uba': 'UBA',
    'moniepoint': 'MONIEPOINT'
}

# ===== MONIEPOINT AUTH (with caching) =====
def get_moniepoint_token():
    """Get or refresh Moniepoint access token."""
    current_time = time.time()
    
    # Return cached token if still valid
    if token_cache['access_token'] and token_cache['expires_at'] > current_time:
        return token_cache['access_token']
    
    url = f"{MONIEPOINT_BASE_URL}/auth/login"
    headers = {
        'Content-Type': 'application/json',
    }
    auth_string = f"{MONIEPOINT_CLIENT_ID}:{MONIEPOINT_CLIENT_SECRET}"
    auth_b64 = base64.b64encode(auth_string.encode()).decode()
    headers['Authorization'] = f'Basic {auth_b64}'
    
    try:
        response = requests.post(url, headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        token = data.get('responseBody', {}).get('accessToken')
        if token:
            # Cache token for 50 minutes (Moniepoint tokens typically expire in 1 hour)
            token_cache['access_token'] = token
            token_cache['expires_at'] = current_time + 3000  # 50 minutes
            logger.info("Moniepoint token refreshed successfully")
            return token
        else:
            logger.error("No token in response: %s", data)
            return None
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to get Moniepoint token: {str(e)}")
        return None

# ===== VALIDATE ACCOUNT =====
@app.route('/api/validate', methods=['POST'])
def validate_account():
    """Validate a Nigerian bank account number."""
    data = request.json
    account_number = data.get('account_number')
    bank_code = data.get('bank')
    
    if not account_number or len(account_number) != 10:
        return jsonify({"status": "error", "error": "Invalid account number"}), 400
    
    if not bank_code or bank_code not in BANK_CODES:
        return jsonify({"status": "error", "error": "Unsupported bank"}), 400
    
    token = get_moniepoint_token()
    if not token:
        return jsonify({"status": "error", "error": "Authentication failed"}), 500
    
    url = f"{MONIEPOINT_BASE_URL}/disbursements/account/validate"
    headers = {
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json'
    }
    payload = {
        'accountNumber': account_number,
        'bankCode': BANK_CODES[bank_code]
    }
    
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=15)
        response.raise_for_status()
        data = response.json().get('responseBody', {})
        
        if data.get('accountName'):
            return jsonify({
                "status": "valid",
                "account_name": data.get('accountName'),
                "bank": data.get('bankName', bank_code)
            })
        else:
            return jsonify({"status": "invalid", "error": "Account not found"}), 404
            
    except requests.exceptions.RequestException as e:
        logger.error(f"Validation API error: {str(e)}")
        return jsonify({"status": "error", "error": "Service unavailable"}), 503

# ===== INITIATE TRANSFER =====
@app.route('/api/transfer', methods=['POST'])
def transfer():
    """Execute a single disbursement to a Nigerian bank account."""
    data = request.json
    account_number = data.get('account_number')
    amount = data.get('amount')
    bank_code = data.get('bank')
    
    # Validation
    if not account_number or len(account_number) != 10:
        return jsonify({"status": "error", "error": "Invalid account number"}), 400
    
    if not amount or not isinstance(amount, (int, float)) or amount < 100:
        return jsonify({"status": "error", "error": "Amount must be at least ₦100"}), 400
    
    if not bank_code or bank_code not in BANK_CODES:
        return jsonify({"status": "error", "error": "Unsupported bank"}), 400
    
    token = get_moniepoint_token()
    if not token:
        return jsonify({"status": "error", "error": "Authentication failed"}), 500
    
    reference = f"VB-{os.urandom(4).hex()}-{int(datetime.now().timestamp())}"
    
    url = f"{MONIEPOINT_BASE_URL}/disbursements/single"
    headers = {
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json'
    }
    payload = {
        'amount': amount,
        'reference': reference,
        'narration': 'VaultBridge Transfer',
        'destinationBankCode': BANK_CODES[bank_code],
        'destinationAccountNumber': account_number,
        'currency': 'NGN',
        'sourceAccountNumber': MONIEPOINT_SOURCE_ACCOUNT
    }
    
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=30)
        response.raise_for_status()
        result = response.json()
        
        # Log success
        transfer_logs.append({
            'bank': bank_code,
            'account': account_number,
            'account_name': data.get('account_name', 'Unknown'),
            'amount': amount,
            'reference': reference,
            'status': 'success',
            'timestamp': datetime.now().isoformat()
        })
        
        return jsonify({
            "status": "success",
            "reference": reference,
            "message": "Transfer initiated successfully"
        })
        
    except requests.exceptions.RequestException as e:
        logger.error(f"Transfer API error: {str(e)}")
        
        # Log failure
        transfer_logs.append({
            'bank': bank_code,
            'account': account_number,
            'account_name': data.get('account_name', 'Unknown'),
            'amount': amount,
            'reference': reference,
            'status': 'failed',
            'timestamp': datetime.now().isoformat()
        })
        
        return jsonify({
            "status": "failed",
            "error": "Transfer failed. Please try again."
        }), 502

# ===== GET BALANCE =====
@app.route('/api/balance', methods=['GET'])
def get_balance():
    """Retrieve Moniepoint wallet balance."""
    token = get_moniepoint_token()
    if not token:
        return jsonify({"error": "Authentication failed"}), 500
    
    url = f"{MONIEPOINT_BASE_URL}/disbursements/wallet-balance"
    headers = {'Authorization': f'Bearer {token}'}
    
    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json().get('responseBody', {})
        
        balance = data.get('availableBalance', 0)
        currency = data.get('currency', 'NGN')
        
        return jsonify({
            "balance": balance,
            "currency": currency
        })
        
    except requests.exceptions.RequestException as e:
        logger.error(f"Balance API error: {str(e)}")
        return jsonify({"error": "Service unavailable"}), 503

# ===== GET TRANSFER LOGS =====
@app.route('/api/logs', methods=['GET'])
def get_logs():
    """Return transfer history."""
    return jsonify(transfer_logs)

# ===== HEALTH CHECK =====
@app.route('/api/status', methods=['GET'])
def status():
    """Check if the API is live."""
    return jsonify({
        "status": "online",
        "version": "2.0",
        "timestamp": datetime.now().isoformat()
    })

# ===== ERROR HANDLING =====
@app.errorhandler(404)
def not_found(e):
    return jsonify({"error": "Endpoint not found"}), 404

@app.errorhandler(500)
def internal_error(e):
    return jsonify({"error": "Internal server error"}), 500

if __name__ == '__main__':
    # Run with Gunicorn in production
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
