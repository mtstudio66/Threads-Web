from flask import Flask, request, jsonify
from flask_jwt_extended import JWTManager, create_access_token, jwt_required, get_jwt_identity

app = Flask(__name__)
app.config['JWT_SECRET_KEY'] = 'your_jwt_secret_key'  # Change this to your secret key
jwt = JWTManager(app)

# Mock user database
users = {'admin': 'adminpassword', 'user': 'userpassword'}  # Change passwords for production

# Admin authentication route
@app.route('/admin/login', methods=['POST'])
def admin_login():
    username = request.json.get('username')
    password = request.json.get('password')

    if username == 'admin' and users.get(username) == password:
        access_token = create_access_token(identity=username)
        return jsonify(access_token=access_token), 200
    return jsonify(message='Invalid username or password'), 401

# User authentication route
@app.route('/user/login', methods=['POST'])
def user_login():
    username = request.json.get('username')
    password = request.json.get('password')

    if users.get(username) == password:
        access_token = create_access_token(identity=username)
        return jsonify(access_token=access_token), 200
    return jsonify(message='Invalid username or password'), 401

# Protected route for admin
@app.route('/admin/protected', methods=['GET'])
@jwt_required()
def protected_admin():
    current_user = get_jwt_identity()
    if current_user != 'admin':
        return jsonify(message='Unauthorized access'), 403
    return jsonify(message='Welcome, admin!')

# Protected route for user
@app.route('/user/protected', methods=['GET'])
@jwt_required()
def protected_user():
    current_user = get_jwt_identity()
    return jsonify(message=f'Welcome, {current_user}!')

if __name__ == '__main__':
    app.run(debug=True)