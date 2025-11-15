# File: server.py (Upgraded for Advanced Admin Panel)
# Description: The central API server for the online licensing system.

from flask import Flask, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timedelta
import hashlib

# --- CONFIGURATION ---
app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///licenses.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
SECRET_KEY = "Your-Super-Secret-Key-Goes-Here-12345"
ADMIN_API_KEY = "Your-Simple-Admin-Key-123"
db = SQLAlchemy(app)

# --- DATABASE MODEL ---
class License(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    license_key = db.Column(db.String(80), unique=True, nullable=False, index=True)
    customer_name = db.Column(db.String(80), nullable=False)
    expiry_date = db.Column(db.DateTime, nullable=False)
    machine_id = db.Column(db.String(120), unique=True, nullable=True)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    activated_at = db.Column(db.DateTime, nullable=True)

    def to_dict(self):
        return {
            'license_key': self.license_key, 'customer_name': self.customer_name,
            'expiry_date': self.expiry_date.isoformat() + 'Z', 'is_active': self.is_active,
            'machine_id': self.machine_id, 'notes': self.notes,
            'activated_at': self.activated_at.isoformat() + 'Z' if self.activated_at else None,
            'created_at': self.created_at.isoformat() + 'Z'
        }

# --- Client-facing APIs ---
@app.route('/api/activate', methods=['POST'])
def activate_license():
    data = request.json
    lic = License.query.filter_by(license_key=data.get('license_key')).first()
    if not lic: return jsonify({'status': 'invalid', 'message': 'License key does not exist.'})
    if lic.machine_id and lic.machine_id != data.get('machine_id'): return jsonify({'status': 'in_use', 'message': 'License key already used on another machine.'})
    if datetime.utcnow() > lic.expiry_date: return jsonify({'status': 'expired', 'message': 'This license has expired.'})
    if not lic.is_active: return jsonify({'status': 'banned', 'message': 'This license has been suspended.'})
    if not lic.machine_id:
        lic.machine_id = data.get('machine_id')
        lic.activated_at = datetime.utcnow()
    db.session.commit()
    return jsonify({'status': 'valid', 'message': 'Activation successful.', 'expiry_date': lic.expiry_date.isoformat() + 'Z'})

@app.route('/api/validate', methods=['POST'])
def validate_license():
    data = request.json
    lic = License.query.filter_by(license_key=data.get('license_key'), machine_id=data.get('machine_id')).first()
    if not lic: return jsonify({'status': 'invalid', 'message': 'License not found for this machine.'})
    if datetime.utcnow() > lic.expiry_date: return jsonify({'status': 'expired', 'message': 'Your license has expired.'})
    if not lic.is_active: return jsonify({'status': 'banned', 'message': 'Your license has been suspended.'})
    return jsonify({'status': 'valid', 'expiry_date': lic.expiry_date.isoformat() + 'Z'})

# --- Admin-facing APIs ---
@app.before_request
def check_admin_key():
    if request.path.startswith('/api/admin'):
        if request.headers.get('X-Admin-API-Key') != ADMIN_API_KEY:
            return jsonify({'error': 'Unauthorized'}), 401

def get_timedelta(data):
    """Calculates timedelta from a dictionary of time units."""
    return timedelta(
        days=int(data.get('days', 0)),
        hours=int(data.get('hours', 0)),
        minutes=int(data.get('minutes', 0))
    ) + relativedelta(
        months=int(data.get('months', 0)),
        years=int(data.get('years', 0))
    )


@app.route('/api/admin/generate', methods=['POST'])
def generate_license():
    data = request.json
    customer_name = data['customer_name'].upper()
    
    # Calculate expiry date from multiple time units
    expiry_date = datetime.utcnow() + get_timedelta(data)
    
    while True:
        unique_part = hashlib.sha256(f"{customer_name}{datetime.utcnow().isoformat()}{SECRET_KEY}".encode()).hexdigest()
        short_hash = unique_part[:16].upper()
        license_key = f"{customer_name[:5]}-{short_hash}"
        if not License.query.filter_by(license_key=license_key).first(): break

    new_license = License(license_key=license_key, customer_name=customer_name, expiry_date=expiry_date, notes=data.get('notes', ''))
    db.session.add(new_license)
    db.session.commit()
    return jsonify(new_license.to_dict()), 201

@app.route('/api/admin/update', methods=['POST'])
def admin_update():
    data = request.json
    lic = License.query.filter_by(license_key=data.get('license_key')).first()
    if not lic: return jsonify({'error': 'License not found'}), 404

    if 'add_time_value' in data and 'add_time_unit' in data:
        lic.expiry_date += get_timedelta(int(data['add_time_value']), data['add_time_unit'])
    if 'set_active' in data: lic.is_active = data['set_active']
    if 'notes' in data: lic.notes = data['notes']
    
    db.session.commit()
    return jsonify({'status': 'success', 'license': lic.to_dict()})

@app.route('/api/admin/stats', methods=['GET'])
def get_stats():
    now = datetime.utcnow()
    total = License.query.count()
    active = License.query.filter(License.is_active == True, License.expiry_date > now, License.machine_id != None).count()
    expiring_soon = License.query.filter(License.is_active == True, License.expiry_date > now, License.expiry_date < now + timedelta(days=7)).count()
    return jsonify({'total_licenses': total, 'active_now': active, 'expiring_soon_7d': expiring_soon})

@app.route('/api/admin/licenses', methods=['GET'])
def get_all_licenses():
    licenses = License.query.order_by(License.created_at.desc()).all()
    return jsonify([lic.to_dict() for lic in licenses])

@app.route('/api/admin/delete', methods=['POST'])
def admin_delete():
    data = request.json
    lic = License.query.filter_by(license_key=data.get('license_key')).first()
    if not lic: return jsonify({'error': 'License not found'}), 404
    db.session.delete(lic)
    db.session.commit()
    return jsonify({'status': 'success', 'message': f'License {lic.license_key} deleted.'})

if __name__ == '__main__':
    try:
        from dateutil.relativedelta import relativedelta
    except ImportError:
        print("ERROR: Missing 'python-dateutil' library.")
        print("Please install it using: pip install python-dateutil")
        exit()

    with app.app_context():
        db.create_all()
    if __name__ == '__main__': app.run(debug=True)