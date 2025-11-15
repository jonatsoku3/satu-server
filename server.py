# File: server.py (Firebase Version)
from flask import Flask, request, jsonify
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
import hashlib
import pyrebase
import json
import os

# --- CONFIGURATION ---
app = Flask(__name__)
SECRET_KEY = "Your-Super-Secret-Key-Goes-Here-12345"
ADMIN_API_KEY = "Your-Simple-Admin-Key-123"

# --- FIREBASE SETUP ---
# โหลดการตั้งค่าจากไฟล์ firebase_key.json และ databaseURL
try:
    with open("firebase_key.json") as f:
        firebase_config_sdk = json.load(f)

    # เพิ่ม databaseURL เข้าไปใน config
    firebase_config = {
        "apiKey": "THIS-IS-NOT-USED-BUT-REQUIRED", # ใส่ค่าอะไรก็ได้
        "authDomain": f"{firebase_config_sdk['project_id']}.firebaseapp.com",
        "databaseURL": "https://satu-license-server-default-rtdb.asia-southeast1.firebasedatabase.app/", # <<< ใส่ Database URL ของคุณ
        "storageBucket": f"{firebase_config_sdk['project_id']}.appspot.com",
        "serviceAccount": "firebase_key.json"
    }
    
    firebase = pyrebase.initialize_app(firebase_config)
    db = firebase.database()
    print("Successfully connected to Firebase.")
except Exception as e:
    print(f"FATAL: Could not initialize Firebase. Error: {e}")
    db = None

# --- Helper Functions ---
def get_timedelta(data):
    return timedelta(days=int(data.get('days', 0)), hours=int(data.get('hours', 0)), minutes=int(data.get('minutes', 0))) \
         + relativedelta(months=int(data.get('months', 0)), years=int(data.get('years', 0)))

# --- Client-facing APIs ---
@app.route('/api/activate', methods=['POST'])
def activate_license():
    if not db: return jsonify({'status': 'error', 'message': 'Database not configured'}), 503
    data = request.json
    key, machine_id = data.get('license_key'), data.get('machine_id')
    
    lic = db.child("licenses").child(key).get().val()

    if not lic: return jsonify({'status': 'invalid', 'message': 'License key does not exist.'})
    if lic.get('machine_id') and lic['machine_id'] != machine_id: return jsonify({'status': 'in_use', 'message': 'License key already used on another machine.'})
    if datetime.utcnow() > datetime.fromisoformat(lic['expiry_date']): return jsonify({'status': 'expired', 'message': 'This license has expired.'})
    if not lic['is_active']: return jsonify({'status': 'banned', 'message': 'This license has been suspended.'})

    if not lic.get('machine_id'):
        updates = {'machine_id': machine_id, 'activated_at': datetime.utcnow().isoformat()}
        db.child("licenses").child(key).update(updates)
    
    return jsonify({'status': 'valid', 'message': 'Activation successful.', 'expiry_date': lic['expiry_date']})

@app.route('/api/validate', methods=['POST'])
def validate_license():
    if not db: return jsonify({'status': 'error', 'message': 'Database not configured'}), 503
    data = request.json
    key, machine_id = data.get('license_key'), data.get('machine_id')
    
    lic = db.child("licenses").child(key).get().val()
    
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
    if not db: return jsonify({'status': 'error', 'message': 'Database not configured'}), 503
    data = request.json
    customer_name = data['customer_name'].upper()
    expiry_date = datetime.utcnow() + get_timedelta(data)
    
    while True:
        unique_part = hashlib.sha256(f"{customer_name}{datetime.utcnow().isoformat()}{SECRET_KEY}".encode()).hexdigest()
        short_hash = unique_part[:16].upper()
        license_key = f"{customer_name[:5]}-{short_hash}"
        if not db.child("licenses").child(license_key).get().val(): break
    
    new_license_data = {
        'license_key': license_key, 'customer_name': customer_name,
        'expiry_date': expiry_date.isoformat(), 'is_active': True,
        'machine_id': None, 'notes': data.get('notes', ''),
        'activated_at': None, 'created_at': datetime.utcnow().isoformat()
    }
    db.child("licenses").child(license_key).set(new_license_data)
    return jsonify(new_license_data), 201

@app.route('/api/admin/licenses', methods=['GET'])
def get_all_licenses():
    if not db: return jsonify({'status': 'error', 'message': 'Database not configured'}), 503
    licenses = db.child("licenses").get().val()
    if not licenses: return jsonify([])
    return jsonify(list(licenses.values()))

@app.route('/api/admin/update', methods=['POST'])
def admin_update():
    if not db: return jsonify({'status': 'error', 'message': 'Database not configured'}), 503
    data = request.json
    key = data.get('license_key')
    lic = db.child("licenses").child(key).get().val()
    if not lic: return jsonify({'error': 'License not found'}), 404
    
    updates = {}
    if 'add_time_value' in data and 'add_time_unit' in data:
        current_expiry = datetime.fromisoformat(lic['expiry_date'])
        new_expiry = current_expiry + get_timedelta({'days': data.get('days',0), **data})
        updates['expiry_date'] = new_expiry.isoformat()
    if 'set_active' in data: updates['is_active'] = data['set_active']
    if 'notes' in data: updates['notes'] = data['notes']
    
    db.child("licenses").child(key).update(updates)
    updated_lic = {**lic, **updates}
    return jsonify({'status': 'success', 'license': updated_lic})

@app.route('/api/admin/delete', methods=['POST'])
def admin_delete():
    if not db: return jsonify({'status': 'error', 'message': 'Database not configured'}), 503
    data = request.json; key = data.get('license_key')
    if db.child("licenses").child(key).get().val():
        db.child("licenses").child(key).remove()
        return jsonify({'status': 'success', 'message': f'License {key} deleted.'})
    return jsonify({'error': 'License not found'}), 404

@app.route('/api/admin/stats', methods=['GET'])
def get_stats():
    if not db: return jsonify({'status': 'error', 'message': 'Database not configured'}), 503
    licenses = db.child("licenses").get().val()
    if not licenses: return jsonify({'total_licenses': 0, 'active_now': 0, 'expiring_soon_7d': 0})
    
    now = datetime.utcnow()
    expiring_soon_date = now + timedelta(days=7)
    stats = {'total_licenses': len(licenses), 'active_now': 0, 'expiring_soon_7d': 0}
    
    for lic in licenses.values():
        expiry_dt = datetime.fromisoformat(lic['expiry_date'])
        if lic['is_active'] and expiry_dt > now and lic['machine_id']:
            stats['active_now'] += 1
        if lic['is_active'] and now < expiry_dt < expiring_soon_date:
            stats['expiring_soon_7d'] += 1
    return jsonify(stats)

if __name__ == '__main__':
    app.run(debug=True)
