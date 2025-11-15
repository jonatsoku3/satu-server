# File: server.py (Final - Simple Firebase Secret Auth Version)
from flask import Flask, request, jsonify
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
import hashlib
import os
import requests
import json

# --- CONFIGURATION ---
app = Flask(__name__)
SECRET_KEY = os.environ.get("SECRET_KEY", "Your-Super-Secret-Key-Goes-Here-12345")
ADMIN_API_KEY = os.environ.get("ADMIN_API_KEY", "Your-Simple-Admin-Key-123")

# --- FIREBASE SETUP ---
DATABASE_URL = os.environ.get("FIREBASE_DATABASE_URL")
DATABASE_SECRET = os.environ.get("FIREBASE_DATABASE_SECRET")

if DATABASE_URL and DATABASE_SECRET:
    # ตรวจสอบว่า URL ลงท้ายด้วย / หรือไม่
    if not DATABASE_URL.endswith('/'):
        DATABASE_URL += '/'
    print("Firebase configuration loaded successfully from environment variables.")
else:
    print("FATAL: Missing Firebase environment variables (DATABASE_URL, DATABASE_SECRET).")
    DATABASE_URL = None # Set to None to indicate failure

# --- Helper Functions ---
def get_timedelta(data):
    return timedelta(
        days=int(data.get('days', 0)),
        hours=int(data.get('hours', 0)),
        minutes=int(data.get('minutes', 0))
    ) + relativedelta(
        months=int(data.get('months', 0)),
        years=int(data.get('years', 0))
    )

def make_firebase_request(method, path, data=None):
    """A helper function to make authenticated requests to Firebase."""
    if not DATABASE_URL:
        raise ConnectionError("Database is not configured.")
        
    url = f"{DATABASE_URL}{path}.json?auth={DATABASE_SECRET}"
    if method.upper() == 'GET':
        response = requests.get(url, timeout=10)
    elif method.upper() == 'PUT':
        response = requests.put(url, data=json.dumps(data), timeout=10)
    elif method.upper() == 'PATCH':
        response = requests.patch(url, data=json.dumps(data), timeout=10)
    elif method.upper() == 'DELETE':
        response = requests.delete(url, timeout=10)
    else:
        raise ValueError("Unsupported request method.")
        
    response.raise_for_status() # Will raise an exception for 4xx/5xx errors
    return response.json()

# --- Client-facing APIs ---
@app.route('/api/activate', methods=['POST'])
def activate_license():
    if not DATABASE_URL: return jsonify({'status': 'error', 'message': 'Database not configured'}), 503
    data = request.json
    key, machine_id = data.get('license_key'), data.get('machine_id')
    try:
        lic = make_firebase_request('GET', f"licenses/{key}")
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 404 or e.response.text == 'null':
             return jsonify({'status': 'invalid', 'message': 'License key does not exist.'})
        raise
    
    if not lic: return jsonify({'status': 'invalid', 'message': 'License key does not exist.'})
    if lic.get('machine_id') and lic['machine_id'] != machine_id: return jsonify({'status': 'in_use', 'message': 'License key already used on another machine.'})
    if datetime.utcnow() > datetime.fromisoformat(lic['expiry_date']): return jsonify({'status': 'expired', 'message': 'This license has expired.'})
    if not lic['is_active']: return jsonify({'status': 'banned', 'message': 'This license has been suspended.'})
    if not lic.get('machine_id'):
        updates = {'machine_id': machine_id, 'activated_at': datetime.utcnow().isoformat()}
        make_firebase_request('PATCH', f"licenses/{key}", updates)
    return jsonify({'status': 'valid', 'message': 'Activation successful.', 'expiry_date': lic['expiry_date']})

@app.route('/api/validate', methods=['POST'])
def validate_license():
    if not DATABASE_URL: return jsonify({'status': 'error', 'message': 'Database not configured'}), 503
    data = request.json
    key, machine_id = data.get('license_key'), data.get('machine_id')
    try:
        lic = make_firebase_request('GET', f"licenses/{key}")
    except (requests.exceptions.HTTPError, ValueError):
        return jsonify({'status': 'invalid', 'message': 'License not found for this machine.'})
    if not lic or lic.get('machine_id') != machine_id: return jsonify({'status': 'invalid', 'message': 'License not found for this machine.'})
    if datetime.utcnow() > datetime.fromisoformat(lic['expiry_date']): return jsonify({'status': 'expired', 'message': 'Your license has expired.'})
    if not lic['is_active']: return jsonify({'status': 'banned', 'message': 'Your license has been suspended.'})
    return jsonify({'status': 'valid', 'expiry_date': lic['expiry_date']})

# --- Admin-facing APIs ---
@app.before_request
def check_admin_key():
    if request.path.startswith('/api/admin') and request.headers.get('X-Admin-API-Key') != ADMIN_API_KEY:
        return jsonify({'error': 'Unauthorized'}), 401

@app.route('/api/admin/generate', methods=['POST'])
def generate_license():
    if not DATABASE_URL: return jsonify({'status': 'error', 'message': 'Database not configured'}), 503
    data = request.json
    customer_name = data['customer_name'].upper()
    expiry_date = datetime.utcnow() + get_timedelta(data)
    
    while True:
        unique_part = hashlib.sha256(f"{customer_name}{datetime.utcnow().isoformat()}{SECRET_KEY}".encode()).hexdigest()
        short_hash = unique_part[:16].upper()
        license_key = f"{customer_name[:5]}-{short_hash}"
        check = make_firebase_request('GET', f"licenses/{license_key}")
        if not check: break
    
    new_license_data = {
        'license_key': license_key, 'customer_name': customer_name, 'expiry_date': expiry_date.isoformat(),
        'is_active': True, 'machine_id': None, 'notes': data.get('notes', ''),
        'activated_at': None, 'created_at': datetime.utcnow().isoformat()
    }
    make_firebase_request('PUT', f"licenses/{license_key}", new_license_data)
    return jsonify(new_license_data), 201

@app.route('/api/admin/licenses', methods=['GET'])
def get_all_licenses():
    if not DATABASE_URL: return jsonify([]), 503
    licenses = make_firebase_request('GET', "licenses")
    if not licenses: return jsonify([])
    return jsonify(list(licenses.values()))

@app.route('/api/admin/update', methods=['POST'])
def admin_update():
    if not DATABASE_URL: return jsonify({'status': 'error', 'message': 'Database not configured'}), 503
    data = request.json
    key = data.get('license_key')
    lic = make_firebase_request('GET', f"licenses/{key}")
    if not lic: return jsonify({'error': 'License not found'}), 404
    
    updates = {}
    if 'add_time_value' in data and 'add_time_unit' in data:
        current_expiry = datetime.fromisoformat(lic['expiry_date'])
        new_expiry = current_expiry + get_timedelta({'days': data.get('days',0), **data})
        updates['expiry_date'] = new_expiry.isoformat()
    if 'set_active' in data: updates['is_active'] = data['set_active']
    if 'notes' in data: updates['notes'] = data['notes']
    
    make_firebase_request('PATCH', f"licenses/{key}", updates)
    updated_lic = {**lic, **updates}
    return jsonify({'status': 'success', 'license': updated_lic})

@app.route('/api/admin/delete', methods=['POST'])
def admin_delete():
    if not DATABASE_URL: return jsonify({'status': 'error', 'message': 'Database not configured'}), 503
    data = request.json
    key = data.get('license_key')
    make_firebase_request('DELETE', f"licenses/{key}")
    return jsonify({'status': 'success', 'message': f'License {key} deleted.'})

@app.route('/api/admin/stats', methods=['GET'])
def get_stats():
    if not DATABASE_URL: return jsonify({'total_licenses': 0, 'active_now': 0, 'expiring_soon_7d': 0}), 503
    licenses = make_firebase_request('GET', "licenses")
    if not licenses: return jsonify({'total_licenses': 0, 'active_now': 0, 'expiring_soon_7d': 0})
    
    now = datetime.utcnow()
    expiring_soon_date = now + timedelta(days=7)
    stats = {'total_licenses': len(licenses), 'active_now': 0, 'expiring_soon_7d': 0}
    
    for lic in licenses.values():
        expiry_dt = datetime.fromisoformat(lic['expiry_date'])
        if lic.get('is_active') and expiry_dt > now and lic.get('machine_id'):
            stats['active_now'] += 1
        if lic.get('is_active') and now < expiry_dt < expiring_soon_date:
            stats['expiring_soon_7d'] += 1
    return jsonify(stats)

# Error handler for better JSON error responses
@app.errorhandler(Exception)
def handle_exception(e):
    # Pass through HTTP errors
    if isinstance(e, requests.exceptions.HTTPError):
        return jsonify(error=str(e), details=e.response.text), e.response.status_code
    # Handle other exceptions
    app.logger.error(f"Unhandled exception: {e}", exc_info=True)
    return jsonify(error="An internal server error occurred.", details=str(e)), 500

if __name__ == '__main__':
    app.run(debug=True)
